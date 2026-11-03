#!/usr/bin/env python3
"""Fetch the CI-owned CLI release and verify its pinned upstream checksum."""

import argparse
import hashlib
import json
import platform
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

INDEX_URL = "https://prod.download.cli.kiro.dev/stable/index.json"
TRIPLES = {"amd64": "x86_64-unknown-linux-gnu", "arm64": "aarch64-unknown-linux-gnu"}
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def validate_package(package):
    url = urlsplit(package["url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("archive URL must use HTTPS without embedded credentials")
    if not isinstance(package.get("sha256"), str) or not re.fullmatch(
        r"[a-f0-9]{64}", package["sha256"]
    ):
        raise ValueError("release metadata needs an exact SHA-256")
    if type(package.get("size")) is not int or package["size"] <= 0:
        raise ValueError("release metadata needs a positive archive size")
    return {key: package[key] for key in ("url", "sha256", "size")}


def resolve_package(version, arch, manifest=None):
    if not VERSION.fullmatch(version):
        raise ValueError("version must be an explicit release number, for example 2.37.1")
    if manifest:
        metadata = json.loads(manifest.read_text())
        if metadata.get("version") != version:
            raise ValueError("manifest version does not match the selected version")
        if arch not in metadata.get("packages", {}):
            raise ValueError("selected architecture is absent from the supplied manifest")
        return validate_package(metadata["packages"][arch])
    with urllib.request.urlopen(INDEX_URL, timeout=60) as response:
        raw = response.read(33554433)
        if len(raw) > 33554432:
            raise ValueError("official release index exceeds 32 MiB")
        index = json.loads(raw)
    releases = [item for item in index["versions"] if item.get("version") == version]
    if len(releases) != 1:
        raise ValueError(f"release {version} is not available uniquely in the official index")
    packages = [
        item
        for item in releases[0]["packages"]
        if item.get("targetTriple") == TRIPLES[arch]
        and item.get("fileType") == "tarGz"
        and item.get("variant") == "headless"
    ]
    if len(packages) != 1:
        raise ValueError(f"release {version} has no unique Linux {arch} headless tar.gz package")
    package = packages[0]
    return validate_package(
        {
            "url": urljoin(INDEX_URL, package["download"]),
            "sha256": package["sha256"],
            "size": package["size"],
        }
    )


def fetch(version, arch, output, archive=None, manifest=None):
    package = resolve_package(version, arch, manifest)
    with tempfile.TemporaryDirectory() as temporary:
        bundle_path = archive or Path(temporary) / "kiro.tar.gz"
        if not archive:
            with (
                urllib.request.urlopen(package["url"], timeout=60) as response,
                bundle_path.open("wb") as target,
            ):
                size = 0
                while chunk := response.read(1048576):
                    size += len(chunk)
                    if size > package["size"]:
                        raise ValueError("archive exceeds the selected release size")
                    target.write(chunk)
        with bundle_path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != package["sha256"] or bundle_path.stat().st_size != package["size"]:
            raise ValueError("selected release archive integrity check failed")
        staged = Path(temporary) / "bin"
        staged.mkdir()
        with tarfile.open(bundle_path, "r:gz") as bundle:
            for name in ["kiro-cli", "kiro-cli-chat", "kiro-cli-term"]:
                member = bundle.getmember("kirocli/bin/" + name)
                if not member.isfile() or member.size > 300000000:
                    raise ValueError("unexpected binary archive member")
                source = bundle.extractfile(member)
                assert source is not None
                with (staged / name).open("wb") as target:
                    while chunk := source.read(1048576):
                        target.write(chunk)
        # Validate/extract the entire archive before updating the build inputs.
        binaries = output / "bin"
        binaries.mkdir(parents=True, exist_ok=True)
        for source in staged.iterdir():
            destination = binaries / source.name
            with source.open("rb") as origin, destination.open("wb") as target:
                while chunk := origin.read(1048576):
                    target.write(chunk)
            destination.chmod(0o755)
        (output / "provenance.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "architecture": arch,
                    **package,
                },
                indent=2,
            )
            + "\n"
        )
    print(f"Verified Kiro {version} ({arch}) in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    parser.add_argument("--arch", choices=list(TRIPLES), default=arch)
    parser.add_argument("--output", type=Path, default=Path("vendor/kiro"))
    parser.add_argument("--archive", type=Path, help="Already downloaded archive to verify")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/kiro_headless_service/kiro-release.json",
        help="CI release manifest; override only when preparing a release or mirrored build",
    )
    args = parser.parse_args()
    try:
        if args.arch is None:
            raise ValueError("unsupported build architecture")
        metadata = json.loads(args.manifest.read_text())
        fetch(metadata["version"], args.arch, args.output, args.archive, args.manifest)
        provenance = args.output / "provenance.json"
        installed = json.loads(provenance.read_text())
        installed["engine"] = metadata["engine"]
        provenance.write_text(json.dumps(installed, indent=2) + "\n")
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        tarfile.TarError,
        urllib.error.URLError,
    ) as exc:
        parser.exit(1, f"Kiro installation failed: {exc}\n")


if __name__ == "__main__":
    main()
