"""Immutable provider selection shipped in the service wheel and OCI image."""

import json
from importlib.resources import files
from typing import Any

RELEASE: dict[str, Any] = json.loads(
    files("kiro_headless_service").joinpath("kiro-release.json").read_text()
)
CLI_VERSION: str = RELEASE["version"]
AGENT_ENGINE: str = RELEASE["engine"]
