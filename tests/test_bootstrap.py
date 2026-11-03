import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

import pytest

from kiro_headless_service.domain.model import RuntimeFailure
from kiro_headless_service.infrastructure import bootstrap


@pytest.fixture
def cache_release(tmp_path, monkeypatch):
    archive = tmp_path / "kiro.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in bootstrap.BINARIES:
            raw = ("binary " + name).encode()
            member = tarfile.TarInfo("kirocli/bin/" + name)
            member.size = len(raw)
            bundle.addfile(member, io.BytesIO(raw))
    metadata = {
        "version": "99.1.0",
        "engine": "v3",
        "packages": {
            "arm64": {
                "url": "https://example.com/kiro.tar.gz",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "size": archive.stat().st_size,
            }
        },
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(metadata))
    monkeypatch.setattr(bootstrap, "RELEASE", metadata)
    monkeypatch.setattr(bootstrap, "MANIFEST", manifest)
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "aarch64")
    return archive, str(tmp_path / "cache")


def test_archive_preparation_and_readonly_offline_reuse(cache_release, monkeypatch):
    archive, cache = cache_release

    def no_network(*args, **kwargs):
        pytest.fail("offline preparation attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    cli = bootstrap.prepare_cli("offline", cache, archive=archive)
    assert Path(cli).read_bytes() == b"binary kiro-cli"
    assert bootstrap.valid_cache(Path(cli).parents[1])
    archive.unlink()

    def no_write(*args, **kwargs):
        pytest.fail("cache reuse attempted to write to a read-only cache")

    monkeypatch.setattr(bootstrap.os, "open", no_write)
    monkeypatch.setattr(Path, "mkdir", no_write)
    assert bootstrap.prepare_cli("offline", cache) == cli
    assert bootstrap.prepare_cli("download", cache) == cli


def test_missing_offline_cache_never_downloads_or_creates_directory(cache_release, monkeypatch):
    _, cache = cache_release
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network access"))
    with pytest.raises(RuntimeFailure, match="KIRO_RUNTIME_CACHE_MISSING"):
        bootstrap.prepare_cli("offline", cache)
    assert not Path(cache).exists()


def test_online_download_is_verified_published_once_and_reused(cache_release, monkeypatch):
    archive, cache = cache_release
    calls = []

    def download(url, **kwargs):
        calls.append(url)
        return io.BytesIO(archive.read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", download)
    cli = bootstrap.prepare_cli("download", cache)
    assert bootstrap.prepare_cli("download", cache) == cli
    assert calls == ["https://example.com/kiro.tar.gz"]
    assert not list(Path(cache).glob(".install-*"))


@pytest.mark.parametrize("damage", ["binary", "metadata", "symlink"])
def test_corrupt_cache_fails_without_silent_redownload(cache_release, monkeypatch, damage):
    archive, cache = cache_release
    cli = Path(bootstrap.prepare_cli("offline", cache, archive=archive))
    if damage == "binary":
        cli.write_bytes(b"corrupted")
    elif damage == "metadata":
        (cli.parents[1] / "provenance.json").write_text("[]")
    else:
        other = cli.with_name("other")
        cli.rename(other)
        cli.symlink_to(other)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network access"))
    with pytest.raises(RuntimeFailure, match="KIRO_RUNTIME_CACHE_INVALID"):
        bootstrap.prepare_cli("download", cache)


def test_bad_download_leaves_no_published_runtime(cache_release, monkeypatch):
    _, cache = cache_release
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"invalid archive"))
    with pytest.raises(RuntimeFailure, match="KIRO_INSTALL_FAILED"):
        bootstrap.prepare_cli("download", cache)
    assert not bootstrap.cache_path(cache).exists()
    assert not list(Path(cache).glob(".install-*"))
