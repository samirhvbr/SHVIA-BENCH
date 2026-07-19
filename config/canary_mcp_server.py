#!/usr/bin/env python3
"""Canary MCP server (stdio, JSON-RPC 2.0) — stdlib only.

Exposes exactly one uniquely-named tool: `shvia_bench_canary`. It does nothing
useful. Its whole purpose is the isolation canary (audit A5, spec §11):

  - It is registered ONLY where a NON-isolated Claude Code would pick it up.
  - The real run is launched with `--mcp-config config/mcp.empty.json
    --strict-mcp-config`, so if isolation holds, this tool must NOT appear.
  - If `shvia_bench_canary` shows up in the agent's tool list, an inherited MCP
    server leaked in and the run's isolation is INVALID.

Deliberately minimal: implements just `initialize`, `notifications/initialized`,
`tools/list` and `tools/call` — enough for an MCP client to register and list the
tool, which is all the canary needs. No external dependencies.
"""
import json
import sys

TOOL_NAME = "shvia_bench_canary"
PROTOCOL_VERSION = "2024-11-05"


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _result(req_id, result) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def handle(msg: dict) -> None:
    method = msg.get("method")
    req_id = msg.get("id")

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "shvia-bench-canary", "version": "0.1.0"},
        })
    elif method == "tools/list":
        _result(req_id, {"tools": [{
            "name": TOOL_NAME,
            "description": ("CANARY — if you can see this tool, the run's "
                           "isolation FAILED (an inherited MCP server leaked in)."),
            "inputSchema": {"type": "object", "properties": {}},
        }]})
    elif method == "tools/call":
        _result(req_id, {"content": [{
            "type": "text",
            "text": "canary reached — isolation would be INVALID if this ran",
        }]})
    elif method in ("notifications/initialized", "initialized"):
        pass  # notification, no response
    elif req_id is not None:
        # Unknown request → JSON-RPC "method not found"
        _send({"jsonrpc": "2.0", "id": req_id,
               "error": {"code": -32601, "message": f"method not found: {method}"}})
    # unknown notification (no id) → ignore


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()
