"""Opt-in contract test: one tiny turn through the real ``claude`` CLI (issue #47).

Skipped unless EXO_TEST_REAL_CLAUDE=1 and a ``claude`` binary is on PATH. It spends a little money and needs a
logged-in CLI, so default runs and CI never run it. A CLI release that changes the stream-json envelope fails here.

    EXO_TEST_REAL_CLAUDE=1 .venv/bin/python -m pytest -q tests/core/models/test_claude_cli_real.py
"""
import asyncio
import os
import shutil

import pytest

from exo.core.models.claude_cli import ClaudeCliProvider
from exo.core.types import Message

pytestmark = [
    pytest.mark.real_claude,
    pytest.mark.skipif(os.environ.get("EXO_TEST_REAL_CLAUDE") != "1", reason="set EXO_TEST_REAL_CLAUDE=1 to run"),
    pytest.mark.skipif(shutil.which("claude") is None, reason="claude binary not on PATH"),
]


def test_real_claude_one_turn():
    async def go():
        provider = ClaudeCliProvider(model="haiku", read_timeout=120.0, timeout=180.0)
        return [e async for e in provider.stream([Message.user("Reply with the single word: ok")], [])]

    events = asyncio.run(go())
    assert events, "no events from the real CLI"
    assert events[-1].kind == "done", events[-1]
    assert any(e.kind == "text" and e.text for e in events)
