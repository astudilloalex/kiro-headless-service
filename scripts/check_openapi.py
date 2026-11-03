"""Ensure the committed JSON specification matches the actual API."""

import json
import os
from pathlib import Path

from kiro_headless_service.api.app import create_app
from kiro_headless_service.infrastructure.config import Settings

os.environ["OPENAPI_DUMMY_TOKEN"] = "spec-generation-token-not-a-real-secret"
settings = Settings(
    engine="simulated", consumers={"spec": {"token_ref": "env:OPENAPI_DUMMY_TOKEN"}}
)
actual = create_app(settings).openapi()
path = Path("openapi.json")
if os.environ.get("UPDATE_OPENAPI") == "1":
    path.write_text(json.dumps(actual, indent=2) + "\n")
else:
    assert json.loads(path.read_text()) == actual, (
        "Run UPDATE_OPENAPI=1 uv run python scripts/check_openapi.py"
    )
print("OpenAPI specification matches implementation")
