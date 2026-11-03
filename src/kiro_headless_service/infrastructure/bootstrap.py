"""Acquire the pinned runtime on the deployer's machine, with a reusable offline cache."""

import fcntl
import hashlib
import json
import os
import platform
import tarfile
import tempfile
import time
from pathlib import Path

from kiro_headless_service.domain.model import RuntimeFailure
from kiro_headless_service.infrastructure.installer import fetch
from kiro_headless_service.infrastructure.release import RELEASE

DEFAULT_CACHE = "/tmp/kiro-service/cli"
MANIFEST = Path(__file__).resolve().parents[1] / "kiro-release.json"
BINARIES = ("kiro-cli", "kiro-cli-chat", "kiro-cli-term")


def architecture() -> str:
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if arch not in RELEASE["packages"]:
        raise RuntimeFailure("KIRO_ARCHITECTURE_UNSUPPORTED")
    assert arch is not None
    return arch


def cache_path(directory: str) -> Path:
    arch = architecture()
    digest = RELEASE["packages"][arch]["sha256"]
    return Path(directory) / f"{RELEASE['version']}-{arch}-{digest[:12]}"


def valid_cache(path: Path) -> bool:
    try:
        if path.is_symlink() or (path / "bin").is_symlink():
            return False
        provenance = path / "provenance.json"
        if provenance.is_symlink():
            return False
        data = json.loads(provenance.read_text())
        if not isinstance(data, dict):
            return False
        arch = architecture()
        package = RELEASE["packages"][arch]
        if any(
            data.get(k) != v
            for k, v in {
                "version": RELEASE["version"],
                "engine": RELEASE["engine"],
                "architecture": arch,
                **package,
            }.items()
        ):
            return False
        for name in BINARIES:
            binary = path / "bin" / name
            if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
                return False
            with binary.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != data["binaries"][name]:
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_cli(
    mode: str,
    directory: str,
    installed_path: str = "/opt/kiro/bin/kiro-cli",
    archive: Path | None = None,
) -> str:
    if mode == "installed":
        return installed_path
    if mode not in {"download", "offline"} or not Path(directory).is_absolute():
        raise ValueError("invalid CLI bootstrap mode/cache directory")
    destination = cache_path(directory)
    if destination.exists():
        if not valid_cache(destination):
            raise RuntimeFailure("KIRO_RUNTIME_CACHE_INVALID")
        return str(destination / "bin/kiro-cli")
    if mode == "offline" and archive is None:
        raise RuntimeFailure("KIRO_RUNTIME_CACHE_MISSING")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Concurrent workers share a cache. Publish only a completely verified directory.
    lock = os.open(root / ".install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 300
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeFailure("KIRO_INSTALL_LOCK_TIMEOUT") from None
                time.sleep(0.1)
        if not destination.exists():
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
                staged = Path(temporary) / "runtime"
                try:
                    fetch(RELEASE["version"], architecture(), staged, archive, MANIFEST)
                    if not valid_cache(staged):
                        raise RuntimeFailure("KIRO_RUNTIME_CACHE_INVALID")
                    staged.rename(destination)
                except RuntimeFailure:
                    raise
                except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
                    raise RuntimeFailure("KIRO_INSTALL_FAILED") from exc
        if not valid_cache(destination):
            raise RuntimeFailure("KIRO_RUNTIME_CACHE_INVALID")
    finally:
        os.close(lock)
    return str(destination / "bin/kiro-cli")
