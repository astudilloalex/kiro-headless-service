#!/usr/bin/env python3
"""Subprocess test double; never packaged as a provider CLI."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def emit(kind, data):
    print(json.dumps({"type": kind, "data": data}), flush=True)


if "--version" in sys.argv:
    print("kiro-cli 2.37.1")
    sys.exit(0)
if "--help" in sys.argv:
    print(
        "--agent-engine v3 --trust-workspace --output-format stream-json "
        "--no-interactive --require-mcp-startup"
    )
    sys.exit(0)

prompt = sys.stdin.read()
identifier = sys.argv[sys.argv.index("--agent") + 1]
agent = json.loads(Path(f".kiro/agents/{identifier}.json").read_text())
assert str(Path(sys.argv[0]).parent) in os.environ["PATH"].split(":")
assert sys.argv[sys.argv.index("--agent-engine") + 1] == "v3"
assert "--trust-workspace" in sys.argv
assert "allowedTools" not in agent
assert "permissions" in agent
assert not agent["includeMcpJson"] and not agent["includePowers"]
for resource in agent["resources"]:
    if isinstance(resource, str) and "*" not in resource:
        assert Path(resource.split("://", 1)[1]).exists()
assert "--no-interactive" in sys.argv
assert "--trust-all-tools" not in sys.argv
assert "KHS_DATABASE_URL" not in os.environ
assert Path(os.environ["HOME"]).is_relative_to(Path.cwd().parent)
settings = json.loads((Path(os.environ["KIRO_HOME"]) / "settings/cli.json").read_text())
assert settings["chat.enableKnowledge"] is False
assert settings["chat.disableInheritingDefaultResources"] is True

if "scenario:mcp" in prompt:
    assert "--require-mcp-startup" in sys.argv
    cfg = agent["mcpServers"]["echo"]
    try:
        mcp = subprocess.Popen(
            [cfg["command"], *cfg["args"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        wire = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"message": "MCP works"}},
            },
        ]
        out, _ = mcp.communicate("\n".join(json.dumps(r) for r in wire) + "\n", timeout=5)
        assert json.loads(out.splitlines()[-1])["result"]["content"][0]["text"] == "MCP works"
    except (OSError, AssertionError):
        sys.exit(3)

emit("runStarted", {"payloadSchema": "acp", "acpProtocolVersion": 1})
if "scenario:malformed" in prompt:
    print("not json", flush=True)
    sys.exit(0)
if "scenario:partial" in prompt:
    sys.exit(0)
if "scenario:oversized" in prompt:
    print("x" * 2000000, flush=True)
    sys.exit(0)
if "scenario:many" in prompt:
    for _ in range(3000):
        emit("metadata", {})
    sys.exit(0)
if "scenario:stderr" in prompt:
    sys.stderr.write("x" * 300000)
    sys.stderr.flush()
    sys.exit(1)
if "scenario:wait" in prompt:
    time.sleep(30)
if "scenario:child" in prompt:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path("artifacts/child.pid").write_text(str(child.pid))
    time.sleep(30)
if "scenario:refused" in prompt:
    emit("runError", {"stage": "prompt"})
    sys.exit(1)
if "scenario:auth" in prompt:
    print("Authentication failed: invalid API key", file=sys.stderr)
    sys.exit(1)

text = "Runtime reply"
if "scenario:agent" in prompt:
    assert "custom system instruction" in agent["prompt"]
    text = "Agent reply"
if "scenario:json" in prompt:
    text = '{"answer": 42}'
if "scenario:secret" in prompt:
    text = os.environ["KIRO_API_KEY"]
if "scenario:artifact" in prompt:
    Path("artifacts/result.txt").write_text("downloadable output")
if "scenario:symlink" in prompt:
    Path("artifacts/leak").symlink_to("/etc/passwd")
emit(
    "sessionUpdate",
    {
        "sessionId": "test",
        "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        },
    },
)
emit("metadata", {"turnDurationMs": 12, "meteringUsage": [{"value": 1, "unit": "credit"}]})
emit(
    "runFinished",
    {
        "sessionId": "test",
        "status": "success",
        "stopReason": "end_turn",
        "finalText": text,
        "finalTextTruncated": False,
    },
)
