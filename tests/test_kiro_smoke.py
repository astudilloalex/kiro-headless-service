import os
import shutil

import pytest

from kiro_headless_service.application.contracts import JobRequest
from kiro_headless_service.application.service import build_prompt
from kiro_headless_service.infrastructure.config import Policy
from kiro_headless_service.infrastructure.runtime import KiroRuntime

pytestmark = [
    pytest.mark.kiro,
    pytest.mark.skipif(
        os.environ.get("RUN_KIRO_SMOKE") != "1",
        reason="RUN_KIRO_SMOKE=1 explicitly enables provider inference",
    ),
]


async def test_real_provider_headless(settings):
    policy = Policy(settings)
    snapshot = policy.snapshot(
        policy.authenticate("a-test-token-at-least-24-characters"),
        JobRequest(input="Reply with the single word OK.").model_dump(mode="json", by_alias=True),
    )
    snapshot["engine"] = "kiro"

    async def emit(kind, data):
        pass

    output = await KiroRuntime(
        os.environ.get("KHS_CLI_PATH", shutil.which("kiro-cli") or "/opt/kiro/bin/kiro-cli"),
        settings.temp_dir,
    ).execute(snapshot, build_prompt(snapshot, []), emit)
    assert output.text.strip() == "OK"
