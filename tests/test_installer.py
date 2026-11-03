import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

import pytest

from kiro_headless_service.infrastructure import installer


@pytest.fixture
def release(tmp_path):
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("kiro-cli", "kiro-cli-chat", "kiro-cli-term"):
            data = ("test executable " + name).encode()
            member = tarfile.TarInfo("kirocli/bin/" + name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    raw = archive.read_bytes()
    package = {
        "targetTriple": "aarch64-unknown-linux-gnu",
        "fileType": "tarGz",
        "variant": "headless",
        "download": "99.1.0/kirocli-aarch64-linux.tar.gz",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }
    index = {
        "versions": [
            {
                "version": "99.1.0",
                "packages": [
                    {**package, "fileType": "zip"},
                    package,
                ],
            }
        ]
    }
    return archive, package, index


def test_installer_uses_ci_manifest_and_ignores_runtime_version(release, tmp_path, monkeypatch):
    archive, package, index = release
    calls = []

    def urlopen(url, **kwargs):
        calls.append(url)
        return io.BytesIO(
            json.dumps(index).encode() if url == installer.INDEX_URL else archive.read_bytes()
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    output = tmp_path / "install"
    manifest = tmp_path / "release.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "99.1.0",
                "engine": "v3",
                "packages": {
                    "arm64": {
                        **package,
                        "url": "https://mirror.example.com/release.tar.gz",
                    }
                },
            }
        )
    )
    args = [
        "fetch_kiro.py",
        "--arch",
        "arm64",
        "--output",
        str(output),
        "--manifest",
        str(manifest),
    ]
    monkeypatch.setenv("KHS_CLI_VERSION", "2.26.1")
    monkeypatch.setattr(sys, "argv", args)
    installer.main()
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["version"] == "99.1.0"
    assert provenance["engine"] == "v3"
    assert provenance["architecture"] == "arm64"
    assert provenance["sha256"] == package["sha256"]
    assert calls == ["https://mirror.example.com/release.tar.gz"]
    assert sorted(p.name for p in (output / "bin").iterdir()) == [
        "kiro-cli",
        "kiro-cli-chat",
        "kiro-cli-term",
    ]


def test_external_manifest_and_archive_work_offline(release, tmp_path, monkeypatch):
    archive, package, _ = release

    def no_network(*args, **kwargs):
        pytest.fail("offline install attempted a network request")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "99.1.0",
                "packages": {
                    "arm64": {
                        "url": "https://mirror.example.com/kiro.tar.gz",
                        **package,
                    }
                },
            }
        )
    )
    output = tmp_path / "install"
    installer.fetch("99.1.0", "arm64", output, archive, manifest)
    assert json.loads((output / "provenance.json").read_text())["version"] == "99.1.0"
    with pytest.raises(ValueError, match="manifest version"):
        installer.resolve_package("99.2.0", "arm64", manifest)


@pytest.mark.parametrize("version,arch", [("99.2.0", "arm64"), ("99.1.0", "amd64")])
def test_unavailable_release_or_architecture_fails(release, monkeypatch, version, arch):
    _, _, index = release
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(index).encode())
    )
    with pytest.raises(ValueError, match="release " + version):
        installer.resolve_package(version, arch)


def test_checksum_failure_preserves_previous_install(release, tmp_path):
    archive, package, _ = release
    output = tmp_path / "install"
    (output / "bin").mkdir(parents=True)
    old = output / "bin/kiro-cli"
    old.write_text("previous executable")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "99.1.0",
                "packages": {
                    "arm64": {
                        **package,
                        "url": "https://mirror.example.com/release.tar.gz",
                        "sha256": "0" * 64,
                    }
                },
            }
        )
    )
    with pytest.raises(ValueError, match="integrity check failed"):
        installer.fetch("99.1.0", "arm64", output, archive, manifest)
    assert old.read_text() == "previous executable"
    assert not (output / "provenance.json").exists()


def test_baseline_manifest_is_optional_and_usable():
    package = installer.resolve_package(
        "2.37.1", "amd64", Path("src/kiro_headless_service/kiro-release.json")
    )
    assert package["url"].endswith("/2.37.1/kirocli-x86_64-linux.tar.gz")
