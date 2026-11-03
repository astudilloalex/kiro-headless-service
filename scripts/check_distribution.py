"""Verify release metadata survives wheel and sdist packaging (no installed source needed)."""

import json
import sys
import tarfile
import zipfile
from pathlib import Path

output = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
expected = json.loads(Path("src/kiro_headless_service/kiro-release.json").read_text())
with zipfile.ZipFile(next(output.glob("*.whl"))) as wheel:
    assert json.loads(wheel.read("kiro_headless_service/kiro-release.json")) == expected
with tarfile.open(next(output.glob("*.tar.gz"))) as sdist:
    manifest = next(n for n in sdist.getnames() if n.endswith("/kiro-release.json"))
    source = sdist.extractfile(manifest)
    assert source is not None
    assert json.load(source) == expected
print("Packaged CLI release manifest verified in wheel and sdist")
