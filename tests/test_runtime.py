import json
import sys
from pathlib import Path

import pytest

from kiro_headless_service.application.contracts import JobRequest
from kiro_headless_service.domain.model import RuntimeFailure
from kiro_headless_service.infrastructure.config import Policy
from kiro_headless_service.infrastructure.process import supervise
from kiro_headless_service.infrastructure.runtime import KiroRuntime, KiroStream


def snapshot(settings, value="hello", **kwargs):
    policy = Policy(settings)
    who = policy.authenticate("a-test-token-at-least-24-characters")
    data = policy.snapshot(
        who, JobRequest(input=value, **kwargs).model_dump(mode="json", by_alias=True)
    )
    data["kiro_key_ref"] = "env:TEST_KIRO_KEY"
    return data


async def discard(kind, data):
    pass


@pytest.mark.parametrize(
    "value,kwargs,expected",
    [
        ("hello", {}, "Runtime reply"),
        ("scenario:agent", {"agent": "custom"}, "Agent reply"),
        ("scenario:json", {}, '{"answer": 42}'),
        ("scenario:mcp", {"mcp_servers": ["echo"]}, "Runtime reply"),
    ],
)
async def test_real_subprocess_adapter(settings, fake_cli, value, kwargs, expected):
    from kiro_headless_service.application.service import build_prompt

    snap = snapshot(settings, value, **kwargs)
    output = await KiroRuntime(str(fake_cli), settings.temp_dir).execute(
        snap, build_prompt(snap, []), discard
    )
    assert output.text == expected
    assert output.usage["metering"] == [{"value": 1, "unit": "credit"}]
    assert not list(Path(settings.temp_dir).glob("run-*"))


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("partial", "RUNTIME_PROTOCOL_INCOMPLETE"),
        ("malformed", "RUNTIME_PROTOCOL_MALFORMED"),
        ("oversized", "RUNTIME_OUTPUT_LIMIT"),
        ("many", "RUNTIME_EVENT_LIMIT"),
        ("stderr", "RUNTIME_STDERR_LIMIT"),
        ("refused", "RUNTIME_REJECTED"),
        ("auth", "KIRO_AUTHENTICATION_FAILED"),
        ("symlink", "ARTIFACT_UNSAFE_FILE"),
        ("secret", "SENSITIVE_OUTPUT_REJECTED"),
    ],
)
async def test_runtime_fail_closed(settings, fake_cli, scenario, code):
    with pytest.raises(RuntimeFailure, match=code):
        await KiroRuntime(str(fake_cli), settings.temp_dir).execute(
            snapshot(settings), "scenario:" + scenario, discard
        )


async def test_required_mcp_missing(settings, fake_cli):
    settings.mcp_servers["echo"].command = "/does/not/exist"
    snap = snapshot(settings, mcp_servers=["echo"])
    with pytest.raises(RuntimeFailure, match="MCP_STARTUP_FAILED"):
        await KiroRuntime(str(fake_cli), settings.temp_dir).execute(snap, "scenario:mcp", discard)


async def test_timeout_and_artifacts(settings, fake_cli):
    snap = snapshot(settings)
    output = await KiroRuntime(str(fake_cli), settings.temp_dir).execute(
        snap, "scenario:artifact", discard
    )
    assert output.artifacts[0].data == b"downloadable output"
    snap["timeout_seconds"] = 0.1
    with pytest.raises(RuntimeFailure, match="EXECUTION_TIMED_OUT"):
        await KiroRuntime(str(fake_cli), settings.temp_dir).execute(snap, "scenario:wait", discard)


async def test_missing_key_and_wrong_version(settings, fake_cli, monkeypatch):
    monkeypatch.delenv("TEST_KIRO_KEY")
    with pytest.raises(RuntimeFailure, match="KIRO_CREDENTIAL_MISSING"):
        await KiroRuntime(str(fake_cli), settings.temp_dir).check("2.37.1", "env:TEST_KIRO_KEY")
    monkeypatch.setenv("TEST_KIRO_KEY", "invalid-test-key")
    with pytest.raises(RuntimeFailure, match="KIRO_VERSION_MISMATCH"):
        await KiroRuntime(str(fake_cli), settings.temp_dir).check("0.0.0", "env:TEST_KIRO_KEY")


async def test_ci_release_is_enforced_in_snapshots(settings, fake_cli):
    snap = snapshot(settings)
    assert snap["cli_version"] == "2.37.1" and snap["agent_engine"] == "v3"
    output = await KiroRuntime(str(fake_cli), settings.temp_dir).execute(snap, "hello", discard)
    assert output.text == "Runtime reply"
    snap["cli_version"] = "2.26.1"
    with pytest.raises(RuntimeFailure, match="JOB_RELEASE_INCOMPATIBLE"):
        await KiroRuntime(str(fake_cli), settings.temp_dir).execute(snap, "hello", discard)


async def test_process_group_cleanup(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(30)"
    )

    async def line(raw):
        pass

    with pytest.raises(RuntimeFailure, match="EXECUTION_TIMED_OUT"):
        await supervise(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            stdin=b"",
            on_line=line,
            output_limit=4096,
            timeout=0.3,
        )
    pid = int(pidfile.read_text())
    # A child zombie has terminated; the OS's init/subreaper is responsible for adopting it.
    status = Path(f"/proc/{pid}/stat")
    assert not status.exists() or status.read_text().split()[2] == "Z"


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"type": "unknown", "data": {}},
        {"type": "runFinished", "data": {"status": "success"}},
    ],
)
async def test_malformed_envelopes(settings, record):
    with pytest.raises(RuntimeFailure):
        await KiroStream(snapshot(settings), discard).line(json.dumps(record).encode())
