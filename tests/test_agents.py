import base64
import json
import os
from pathlib import Path

import pytest

from kiro_headless_service.application.contracts import JobRequest
from kiro_headless_service.domain.model import ServiceError
from kiro_headless_service.infrastructure.agents import AgentCatalog, materialize, parse_agent
from kiro_headless_service.infrastructure.config import Policy, Settings
from kiro_headless_service.infrastructure.runtime import KiroRuntime


def catalog(root, **limits):
    return AgentCatalog(
        str(root),
        limits.get("files", 128),
        limits.get("file_bytes", 262144),
        limits.get("total", 2097152),
    )


def test_native_json_markdown_prompt_resources_and_immutable_bundle(tmp_path):
    root = tmp_path / "kiro"
    (root / "agents").mkdir(parents=True)
    (root / "resources").mkdir()
    (root / "agents/json-agent.json").write_text(
        json.dumps(
            {
                "name": "json-agent",
                "prompt": "file://../resources/prompt.md",
                "tools": ["read"],
                "excludedTools": ["read_file"],
                "model": "operator-selected-model",
                "permissions": {
                    "rules": [{"capability": "fs_read", "match": ["docs/**"], "effect": "allow"}]
                },
                "resources": ["file://resources/guide.md"],
            }
        )
    )
    (root / "agents/markdown-agent.md").write_text(
        "---\nname: markdown-agent\ndescription: Native Markdown\ntools: []\n---\n"
        "Native instructions."
    )
    (root / "resources/prompt.md").write_text("Original prompt")
    (root / "resources/guide.md").write_text("Original guide")
    (root / "resources/private.md").write_text("Not referenced by this agent")
    library = catalog(root)
    bundle = library.snapshot("json-agent")
    assert bundle["config"]["prompt"] == "Original prompt"
    assert bundle["config"]["model"] == "operator-selected-model"
    assert library.agents["markdown-agent"]["prompt"] == "Native instructions."
    assert set(bundle["files"]) == {"resources/guide.md"}
    (root / "resources/guide.md").write_text("Edited after acceptance")
    assert base64.b64decode(bundle["files"]["resources/guide.md"]) == b"Original guide"
    assert catalog(root).digest != library.digest
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    effective = materialize(bundle, workspace)
    assert effective["permissions"] == bundle["config"]["permissions"]
    assert Path(effective["resources"][0][7:]).read_text() == "Original guide"
    assert not (workspace / ".kiro/agents/markdown-agent.md").exists()


@pytest.mark.parametrize(
    "data",
    [
        {"allowedTools": []},
        {"toolsSettings": {}},
        {"tools": ["*"]},
        {"includeMcpJson": True},
        {"includePowers": True},
        {"hooks": [{"command": "something"}]},
        {"tools": ["@builtin"]},
        {"tools": ["subagent/private"]},
        {"permissions": {"rules": [{"capability": "shell", "effect": "invalid"}]}},
        {"resources": [{"source": "file://resources/index", "type": "knowledgeBase"}]},
    ],
)
def test_unsupported_or_unbounded_native_capabilities_fail(data):
    with pytest.raises(ValueError):
        parse_agent(json.dumps(data).encode(), ".json")


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter",
        "---\nname: a",
        "---\nname: a\nname: b\n---\nx",
        "---\ntools: &a [read]\nexcludedTools: *a\n---\nx",
        "---\nname: [broken\n---\nx",
        "---\n? [not, a, string]\n: x\n---\nx",
        "---\nmodel: 2026-11-03\n---\nx",
    ],
)
def test_markdown_validation_is_safe(text):
    with pytest.raises(ValueError):
        parse_agent(text.encode(), ".md")


@pytest.mark.parametrize(
    "reference", ["file:///etc/passwd", "file://../../outside", "https://example.com/config"]
)
def test_external_resource_escape_is_rejected(tmp_path, reference):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents/agent.json").write_text(json.dumps({"resources": [reference]}))
    with pytest.raises(ValueError):
        catalog(tmp_path)


def test_symlink_hardlink_and_size_limits(tmp_path):
    root = tmp_path / "kiro"
    (root / "resources").mkdir(parents=True)
    source = tmp_path / "outside"
    source.write_text("private")
    target = root / "resources/data"
    target.symlink_to(source)
    with pytest.raises((OSError, ValueError)):
        catalog(root)
    target.unlink()
    os.link(source, target)
    with pytest.raises(ValueError, match="regular"):
        catalog(root)
    target.unlink()
    target.write_text("x" * 40)
    with pytest.raises(ValueError, match="limit"):
        catalog(root, file_bytes=20)
    with pytest.raises(ValueError, match="limit"):
        catalog(root, total=20)


def test_duplicate_agent_formats_and_missing_prompt(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents/agent.json").write_text('{"name":"agent"}')
    (tmp_path / "agents/agent.md").write_text("---\nname: agent\n---\nInstructions")
    with pytest.raises(ValueError, match="duplicate"):
        catalog(tmp_path)
    (tmp_path / "agents/agent.md").unlink()
    (tmp_path / "agents/agent.json").write_text('{"prompt":"file://missing.md"}')
    with pytest.raises(ValueError, match="missing"):
        catalog(tmp_path)


def test_native_permissions_cannot_enable_profile_denied_tools(settings):
    root = Path(settings.kiro_config_dir)
    (root / "agents/custom.json").write_text('{"tools":["shell"],"permissions":{"rules":[]}}')
    settings = Settings(**settings.model_dump(mode="json", by_alias=True))
    policy = Policy(settings)
    with pytest.raises(ServiceError, match="exceed profile"):
        policy.snapshot(
            policy.authenticate("a-test-token-at-least-24-characters"),
            JobRequest(input="x", agent="custom").model_dump(mode="json", by_alias=True),
        )


async def test_markdown_agent_executes_from_accepted_resource_snapshot(settings, fake_cli):
    root = Path(settings.kiro_config_dir)
    (root / "agents/custom.json").unlink()
    (root / "resources").mkdir()
    (root / "resources/guide.md").write_text("Accepted resource")
    (root / "agents/custom.md").write_text(
        "---\nname: custom\ntools: []\nresources: [file://resources/guide.md]\n---\n"
        "custom system instruction"
    )
    settings = Settings(**settings.model_dump(mode="json", by_alias=True))
    policy = Policy(settings)
    snapshot = policy.snapshot(
        policy.authenticate("a-test-token-at-least-24-characters"),
        JobRequest(input="scenario:agent", agent="custom").model_dump(mode="json", by_alias=True),
    )
    snapshot["kiro_key_ref"] = "env:TEST_KIRO_KEY"
    (root / "resources/guide.md").unlink()
    (root / "agents/custom.md").unlink()

    async def emit(kind, data):
        pass

    output = await KiroRuntime(str(fake_cli), settings.temp_dir).execute(
        snapshot, "scenario:agent", emit
    )
    assert output.text == "Agent reply"


async def test_native_agent_mcp_is_authorized_required_and_secret_free(
    settings, fake_cli, monkeypatch
):
    root = Path(settings.kiro_config_dir)
    echo = settings.mcp_servers["echo"]
    (root / "agents/custom.json").write_text(
        json.dumps(
            {
                "tools": ["@echo/echo"],
                "permissions": {"rules": []},
                "mcpServers": {
                    "echo": {
                        "command": echo.command,
                        "args": echo.args,
                        "env": {"ACCESS_TOKEN": "${TEST_MCP_TOKEN}"},
                    }
                },
            }
        )
    )
    data = settings.model_dump(mode="json", by_alias=True)
    data["mcp_servers"] = {}
    monkeypatch.setenv("TEST_MCP_TOKEN", "native-mcp-credential-value")
    settings = Settings(**data)
    policy = Policy(settings)
    snapshot = policy.snapshot(
        policy.authenticate("a-test-token-at-least-24-characters"),
        JobRequest(input="scenario:mcp", agent="custom").model_dump(mode="json", by_alias=True),
    )
    snapshot["kiro_key_ref"] = "env:TEST_KIRO_KEY"
    assert snapshot["request"]["mcp_servers"] == ["echo"]
    assert "native-mcp-credential-value" not in json.dumps(snapshot)

    async def emit(kind, data):
        pass

    output = await KiroRuntime(str(fake_cli), settings.temp_dir).execute(
        snapshot, "scenario:mcp", emit
    )
    assert output.text == "Runtime reply"
    settings.consumers["alice"].mcp.clear()
    policy = Policy(settings)
    with pytest.raises(ServiceError, match="MCP servers exceed"):
        policy.snapshot(
            policy.authenticate("a-test-token-at-least-24-characters"),
            JobRequest(input="x", agent="custom").model_dump(mode="json", by_alias=True),
        )
