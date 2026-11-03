"""No-inference verification of the CI-owned CLI release and V3 command surface."""

import argparse
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path


def check(cli: Path, metadata: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="khs-cli-check-") as home:
        env = {
            "PATH": str(cli.parent) + ":/usr/local/bin:/usr/bin:/bin",
            "HOME": home,
            "KIRO_HOME": home + "/.kiro",
            "KIRO_RUNTIME_DIR": home + "/runtime",
        }

        def invoke(args):
            process = subprocess.Popen(
                [str(cli), *args],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, _ = process.communicate(timeout=30)
                if process.returncode or len(stdout) > 65536:
                    raise RuntimeError("CLI verification command failed")
                return stdout.decode().strip()
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

        if invoke(["--version"]) != "kiro-cli " + metadata["version"]:
            raise RuntimeError("CLI does not match the release manifest")
        help_text = invoke(["chat", "--help"])
        for flag in (
            "--agent-engine",
            metadata["engine"],
            "--no-interactive",
            "--trust-workspace",
            "--output-format",
            "stream-json",
            "--require-mcp-startup",
        ):
            if flag not in help_text:
                raise RuntimeError("Required V3 command capability is unavailable")
    print(f"Verified Kiro {metadata['version']} / {metadata['engine']} without inference")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    check(args.cli, json.loads(args.manifest.read_text()))
