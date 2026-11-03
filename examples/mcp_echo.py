#!/usr/bin/env python3
"""Minimal local MCP server: JSON-RPC over newline-delimited stdio, no dependencies."""

import json
import sys


def main() -> None:
    for raw in sys.stdin.buffer:
        if len(raw) > 65536:
            break
        try:
            request = json.loads(raw)
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "service-echo-test", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo a bounded message",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"message": {"type": "string", "maxLength": 1024}},
                                "required": ["message"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                }
            elif method == "tools/call" and request.get("params", {}).get("name") == "echo":
                message = request["params"].get("arguments", {}).get("message")
                if not isinstance(message, str) or len(message) > 1024:
                    raise ValueError("invalid arguments")
                result = {"content": [{"type": "text", "text": message}], "isError": False}
            elif method == "ping":
                result = {}
            else:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {"code": -32601, "message": "Method not found"},
                        }
                    ),
                    flush=True,
                )
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except (ValueError, KeyError, TypeError):
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32602, "message": "Invalid request"},
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
