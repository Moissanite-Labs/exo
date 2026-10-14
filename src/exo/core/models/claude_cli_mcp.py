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

    def error(code: int, message: str, request_id=None) -> None:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}), flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            error(-32700, "Parse error")
            continue
        if not isinstance(row, dict) or row.get("jsonrpc") != "2.0" or not isinstance(row.get("method"), str) \
                or ("id" in row and (isinstance(row["id"], bool) or not isinstance(row["id"], (str, int, float, type(None))))):
            error(-32600, "Invalid Request")
            continue
        if "params" in row and not isinstance(row["params"], (dict, list)):
            if "id" in row:
                error(-32602, "Invalid params", row["id"])
            continue
        method, result = row.get("method"), {}
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "exo-inert-inventory", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": manifest}
        elif method == "tools/call":
            result = {"isError": True, "content": [{"type": "text", "text": "Denied: native tools are inert; only Exo executes tools."}]}
        elif method != "ping":
            if "id" in row:
                error(-32601, "Method not found", row["id"])
            continue
        if "id" in row:
            print(json.dumps({"jsonrpc": "2.0", "id": row["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
