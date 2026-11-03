import json

import pytest
from pydantic import ValidationError

from kiro_headless_service.application.contracts import JobRequest, ResponseFormat
from kiro_headless_service.application.worker import validate_result
from kiro_headless_service.domain.model import RuntimeFailure, ServiceError, State, transition
from kiro_headless_service.infrastructure.config import Policy, Settings, load_settings


@pytest.mark.parametrize(
    "payload",
    [
        {"input": ""},
        {"input": "hi", "unknown": 1},
        {"input": "x" * 140000},
        {"input": "x", "mcp_servers": ["echo", "echo"]},
        {"input": "x", "limits": {"timeout_seconds": 0}},
        {"input": {"number": float("nan")}},
        {"input": "NUL\x00"},
    ],
)
def test_strict_bounded_request(payload):
    with pytest.raises(ValidationError):
        JobRequest.model_validate(payload)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.com/schema"},
        {"type": "invalid"},
        {"properties": {"a": {"$dynamicRef": "file:///etc/passwd"}}},
        {"type": "string", "pattern": "(a+)+$"},
    ],
)
def test_schema_no_external_resolution(schema):
    with pytest.raises(ValidationError):
        ResponseFormat(type="json_schema", schema=schema)


def test_policy_optional_agent_immutable_snapshot(settings):
    policy = Policy(settings)
    consumer = policy.authenticate("a-test-token-at-least-24-characters")
    request = JobRequest(input="hello").model_dump(mode="json", by_alias=True)
    snapshot = policy.snapshot(consumer, request)
    assert snapshot["tools"] == []
    assert snapshot["request"]["response_format"]["type"] == "text"
    assert "a-test-token" not in json.dumps(snapshot)
    settings.profiles["default"].instructions = "changed"
    assert "changed" not in snapshot["instructions"]
    assert request["response_format"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"agent": "other"},
        {"profile": "private"},
        {"mcp_servers": ["other"]},
        {"callback": "http://169.254.169.254/"},
        {"model": "unknown"},
    ],
)
def test_authorized_selection(settings, change):
    policy = Policy(settings)
    consumer = policy.authenticate("a-test-token-at-least-24-characters")
    with pytest.raises(ServiceError):
        policy.snapshot(
            consumer, JobRequest(input="hello", **change).model_dump(mode="json", by_alias=True)
        )


@pytest.mark.parametrize(
    "text",
    [
        "```json\n{}\n```",
        'prefix {"a": 1}',
        '{"x":NaN}',
        '{"x":1,"x":2}',
        '{"x":1e999}',
        '{"x":"\\u0000"}',
    ],
)
def test_invalid_business_json(settings, text):
    policy = Policy(settings)
    consumer = policy.authenticate("a-test-token-at-least-24-characters")
    snapshot = policy.snapshot(
        consumer,
        JobRequest(input="x", response_format={"type": "json"}).model_dump(
            mode="json", by_alias=True
        ),
    )
    with pytest.raises(RuntimeFailure, match="INVALID_RESULT"):
        validate_result(text, snapshot, {})


def test_settings_precedence_and_incompatibility(settings, monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(settings.model_dump_json(by_alias=True))
    monkeypatch.setenv("KHS_CONFIG", str(path))
    monkeypatch.setenv("KHS_MAX_RUNNING", "2")
    assert load_settings().max_running == 2
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "require_os_sandbox": True})
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "profiles": {"default": {"tools": ["*"]}}})


def test_terminal_transitions():
    transition("QUEUED", State.RUNNING)
    transition("RUNNING", State.SUCCEEDED)
    with pytest.raises(ValueError):
        transition("SUCCEEDED", State.RUNNING)


def test_ci_version_cannot_be_overridden(settings, monkeypatch, tmp_path):
    data = settings.model_dump(mode="json", by_alias=True)
    data["cli_version"] = "99.1.0"
    path = tmp_path / "service.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("KHS_CONFIG", str(path))
    monkeypatch.delenv("KHS_CLI_VERSION", raising=False)
    with pytest.raises(ValidationError):
        load_settings()
    del data["cli_version"]
    path.write_text(json.dumps(data))
    assert load_settings().cli_version == "2.37.1"
    assert load_settings().agent_engine == "v3"
    monkeypatch.setenv("KHS_CLI_VERSION", "99.2.0-rc.1")
    with pytest.raises(ValueError, match="image release"):
        load_settings()


@pytest.mark.parametrize(
    "version", ["latest", "", "2", "2.0", "1.0.0;echo x", "1.0.0-" + "a" * 128]
)
def test_cli_version_requires_explicit_release(settings, version):
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(mode="json", by_alias=True), "cli_version": version})
