"""#254 W2-4: per-turn session reads count and tail in SQL instead of decoding the whole session."""

from __future__ import annotations

import asyncio

import pytest

import exo.core.sessions as sessions
from exo import provenance
from exo.core.loop import Loop
from exo.core.sessions import SessionStore
from exo.core.types import ContentPart, Message, ProviderEvent, ToolCall, ToolResult, Usage


@pytest.fixture
def decoded(monkeypatch):
    """Counts the messages the store decodes from JSON."""
    seen: list[int] = []
    decode = sessions.from_dict

    def record(*args, **kwargs):
        seen.append(1)
        return decode(*args, **kwargs)

    monkeypatch.setattr(sessions, "from_dict", record)
    return seen


def note(text: str, value) -> Message:
    return Message("user", [ContentPart.of_text(text)], meta={"note": value})


def fill(store: SessionStore, sid: str, turns: int) -> None:
    """User turns with tool rounds, context notes of every falsy and truthy kind, and an undone tail."""
    values = [None, False, 0, 0.0, "", [], {}, "threads", "0", "[]", 1, True]
    for i in range(turns):
        ask = Message.user(f"ask {i}")
        ask.meta["author"] = "principal"
        store.append_message(sid, ask)
        call = ToolCall(id=f"c{i}", name="echo", arguments={"x": str(i)})
        store.append_message(sid, Message.assistant("", tool_calls=[call]))
        store.append_message(sid, Message.tool([ToolResult.of_text(call.id, f"out {i}")]))
        store.append_message(sid, Message.assistant(f"done {i}"))
        if i % 3 == 0:
            store.append_message(sid, note(f"note {i}", values[i // 3 % len(values)]))
    store.undo(sid, 2)


def old_turns(store: SessionStore, sid: str) -> int:
    return sum(1 for m in store.messages(sid) if m.role == "user" and not m.meta.get("note"))


def test_user_turns_counts_as_the_full_load_did_without_decoding(decoded):
    store = SessionStore()
    try:
        sid, other = store.create_session().id, store.create_session().id
        fill(store, sid, 50)
        fill(store, other, 3)
        expected = old_turns(store, sid)
        decoded.clear()
        assert expected > 48  # 48 user turns after the undo, plus the notes whose value is falsy
        assert store.user_turns(sid) == expected and decoded == []
        assert store.user_turns(other) == old_turns(store, other)
        assert store.user_turns("missing") == 0
    finally:
        store.close()


def test_last_messages_is_the_tail_of_the_full_load(decoded):
    store = SessionStore()
    try:
        sid = store.create_session().id
        fill(store, sid, 30)
        full = store.messages(sid)
        for n in (1, 7, 40, len(full), len(full) + 5):
            decoded.clear()
            assert store.last_messages(sid, n) == full[-n:] and len(decoded) == min(n, len(full))
        assert store.last_messages(sid, 0) == [] and store.last_messages("missing", 5) == []
    finally:
        store.close()


class Script:
    name = "fake"

    async def stream(self, messages, tools, **opts):
        msg = Message.assistant("ok")
        yield ProviderEvent(kind="text", text="ok")
        yield ProviderEvent(kind="usage", usage=Usage(input_tokens=1, output_tokens=1))
        yield ProviderEvent(kind="done", stop_reason="end", message=msg)


class SmallContext:
    """A prompt that does not read the session, so the turn's own reads are what the test counts."""

    async def build(self, session, system):
        return [Message.system(system or "sys"), Message.user("hi")]


class Memory:
    def __init__(self):
        self.turn_numbers: list[int] = []
        self.checkpoints: list[list] = []

    def system_prompt_block(self):
        return ""

    def prefetch(self, query, session_id=""):
        return ""

    def on_turn_start(self, turn_number, message, session_id="", **kw):
        self.turn_numbers.append(turn_number)

    def sync_turn(self, user, assistant, session_id="", **kw):
        pass

    def checkpoint(self, session_id="", messages=None, reason="turn"):
        self.checkpoints.append(messages)


def test_a_turn_counts_and_tails_without_decoding_the_session(tmp_path, decoded):
    store = SessionStore(tmp_path / "sessions.sqlite")
    mem = Memory()
    loop = Loop(store, Script(), memory=mem, context=SmallContext(), retry_delay=0)
    try:
        sid = store.create_session(agent="a").id
        fill(store, sid, 300)
        before = old_turns(store, sid)
        decoded.clear()
        asyncio.run(loop.send(sid, "one more"))
        reads = len(decoded)
        assert mem.turn_numbers == [before + 1]
        assert mem.checkpoints == [[provenance.flat(m) for m in store.messages(sid)[-40:]]]
        assert reads <= 45, reads  # the 40-message tail and the repair check, not the session's 1,300 messages
    finally:
        store.close()
