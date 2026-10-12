"""Inert MCP server for the claude-cli transport. Run by the ``claude`` CLI as a subprocess, stdlib only.

It lists Exo's tools (from a JSON manifest file) so the CLI lets the model call them, and refuses to
run any: the model's tool_use is read from the CLI's output and Exo runs the tool itself.
Same mechanism as the Hermes claude-subscription-directsdk plugin's inert_mcp.py.
"""
import json
import sys
from pathlib import Path


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for line in sys.stdin:
        row = json.loads(line)
        method, result = row.get("method"), {}
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "exo-inert-inventory", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": manifest}
        elif method == "tools/call":
            result = {"isError": True, "content": [{"type": "text", "text": "Denied: native tools are inert; only Exo executes tools."}]}
        if "id" in row:
            print(json.dumps({"jsonrpc": "2.0", "id": row["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
