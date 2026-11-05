"""Own a killable process for a registered grep/glob call (D-057 stage 0.5).

The registry's gate and executor run before this handler. The worker has no shell,
does not load plugins, and receives only search arguments and store-path settings.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from exo.core.tools.registry import ToolContext, ToolError

SEARCH_TIMEOUT = 30.0
_POLL = 0.02
_STORE_ENV = ("HOME", "EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "CLAUDE_CONFIG_DIR", "SYSTEMROOT")
_BOOT = "import sys; sys.path.insert(0, sys.argv[1]); from exo.search_worker import _main; _main()"


async def _finish(start: asyncio.Task, communication: asyncio.Task | None) -> None:
    """Reap even when cancellation arrived while Python was being started."""
    try:
        proc = await start
    except Exception:
        return
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await proc.wait()
    if communication is not None:
        await asyncio.gather(communication, return_exceptions=True)


async def run_search(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    """Run the existing search in a child, with a wall-clock limit and owned cleanup."""
    if name not in ("grep", "glob"):
        raise ToolError("Unknown search tool")
    if ctx.cancel.is_set():
        raise ToolError("Search interrupted")
    payload = json.dumps({"args": args, "cwd": ctx.cwd}).encode("utf-8")
    # -I ignores PYTHONPATH and user-site startup. Pin the same Exo code the parent
    # loaded, including in a source checkout, without importing from the search cwd.
    env = {k: os.environ[k] for k in _STORE_ENV if k in os.environ}
    deadline = asyncio.get_running_loop().time() + SEARCH_TIMEOUT
    start = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", _BOOT, str(Path(__file__).resolve().parents[1]), name,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env))
    communication = None
    try:
        proc = await asyncio.shield(start)
        communication = asyncio.create_task(proc.communicate(payload))
        while True:
            if ctx.cancel.is_set():
                raise ToolError("Search interrupted")
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                raise ToolError(f"Search stopped after {SEARCH_TIMEOUT:g} s; narrow the pattern")
            try:
                output, _ = await asyncio.wait_for(asyncio.shield(communication), min(_POLL, left))
                break
            except TimeoutError:
                continue
        if proc.returncode:
            raise ToolError(f"Search worker exited with status {proc.returncode}")
        result = json.loads(output)
        if result["error"]:
            raise ToolError(result["text"])
        return result["text"]
    finally:
        cleanup = asyncio.create_task(_finish(start, communication))
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError


def _main() -> None:
    """Private worker entry point. Only JSON crosses the process boundary."""
    from exo.core.tools.builtin.search import glob, grep
    from exo.core.types import ToolResult

    name = sys.argv[2]
    try:
        data = json.load(sys.stdin)
        text = {"glob": glob, "grep": grep}[name](data["args"], ToolContext(cwd=data["cwd"]))
        result = ToolResult.of_text("", text)
    except ToolError as exc:
        result = ToolResult.of_text("", str(exc), is_error=True)
    except Exception as exc:
        result = ToolResult.of_text("", f"{name} failed: {type(exc).__name__}: {exc}", is_error=True)
    sys.stdout.write(json.dumps({"text": result.text(), "error": result.is_error}))
