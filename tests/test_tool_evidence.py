"""Retained output stays exact in storage; model views and expansion are bounded."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from exo.config import Config, ConfigError
from exo.core.context import TreeContext
from exo.core.loop import Loop
from exo.core.models.keys import redact_secrets
from exo.core.sessions import SessionStore
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.core.types import ContentPart, Message, ProviderEvent, ToolCall, ToolResult, ToolSpec
from exo.tool_evidence import Evidence, EvidenceContext, META, Options, TOOL, TRUST, _digest, attach


def reference(text):
    return re.search(r'te1:m_[A-Za-z0-9_-]+:\d+:[0-9a-f]{64}', text)[0]


@pytest.fixture
def evidence(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    sid = store.create_session(agent="default").id
    obj = Evidence(store, Options(enabled=True), "alice", "default")
    obj.begin(sid)
    yield obj, sid
    store.close()


def recorded(obj, sid, text, *, error=False, parts=None, tool="build"):
    call = ToolCall("call-1", tool, {})
    assistant = obj.store.append_message(sid, Message.assistant(tool_calls=[call]))
    result = ToolResult("call-1", parts if parts is not None else [ContentPart.of_text(text)], error)
    message = obj.store.append_message(sid, Message.tool([result]))
    return [assistant, message]


def source(kind):
    if kind == "json":
        return json.dumps({"records": [{"id": i, "name": f"example {i}"} for i in range(1000)]})
    if kind == "diff":
        return "diff --git a/example b/example\n" + "@@ -1 +1 @@\n-old\n+new\n" * 600
    if kind == "malformed":
        return "{bad-json\x00\ufffd🙂e\u0301\n" * 900
    return ("[resource /reports/build.log]\n" + "build step ok\n" * 1000
            + "error: first problem\nerror: first problem\nerror: different problem\n[exit 7]\n")


@pytest.mark.parametrize("kind", ["log", "json", "diff", "malformed"])
def test_excerpt_is_deterministic_retains_pairing_and_preserves_originals(evidence, kind):
    obj, sid = evidence
    messages = recorded(obj, sid, source(kind))
    before = deepcopy(messages)
    rendered = obj.render(sid, messages)
    assert obj.render(sid, messages) == rendered
    assert messages == before == obj.store.messages(sid)
    assert rendered[0] is messages[0]
    result = rendered[-1].tool_results[0]
    assert result.call_id == "call-1" and result.is_error is False
    assert len(result.text()) < len(source(kind)) / 2
    assert TRUST in result.text() and '"build"' in result.text()
    if kind == "log":
        assert all(line in result.text() for line in ("error: first problem", "error: different problem",
                                                    "[exit 7]", "[resource /reports/build.log]"))
    ref = reference(result.text())
    page, joined, offset = None, "", 0
    while page is None or page["next_offset"] is not None:
        page = obj.read(sid, ref, offset, 509)
        joined += page["text"]
        offset = page["next_offset"]
        assert page["trust"] == TRUST
    assert joined == source(kind)


@pytest.mark.parametrize("case", ["error", "many-errors", "image", "multi-text", "oversize", "expansion"])
def test_unsafe_or_unhelpful_candidates_keep_original(evidence, case):
    obj, sid = evidence
    text, parts = source("log"), None
    if case == "many-errors":
        text = "\n".join(f"error {i}: distinct problem details" for i in range(1000))
    elif case == "image":
        parts = [ContentPart.of_text(text), ContentPart(type="image", data="AA==", mime="image/png")]
    elif case == "multi-text":
        parts = [ContentPart.of_text(text), ContentPart.of_text("[resource other]")]
    elif case == "oversize":
        text = "x" * 100129
    messages = recorded(obj, sid, text, parts=parts, error=case == "error", tool=TOOL if case == "expansion" else "build")
    assert obj.render(sid, messages) == messages


def test_exact_pages_work_after_restart_without_an_archive(evidence, tmp_path):
    obj, sid = evidence
    text = source("json") + "🙂\n"
    ref = reference(obj.render(sid, recorded(obj, sid, text))[-1].tool_results[0].text())
    obj.store.close()
    reopened = SessionStore(tmp_path / "sessions.sqlite")
    try:
        reader = Evidence(reopened, obj.options, "alice", "default")
        page = reader.read(sid, ref, len(text) - 4)
        assert page["text"] == text[-4:] and page["next_offset"] is None
        assert reader.read(sid, ref, len(text))["text"] == ""
        assert set(p.name for p in tmp_path.iterdir()) <= {"sessions.sqlite", "sessions.sqlite-wal", "sessions.sqlite-shm"}
        assert len(reopened.get_meta(sid)[META]) == 3
    finally:
        reopened.close()


def test_session_private_fork_principal_and_agent_boundaries(evidence):
    obj, sid = evidence
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    other = obj.store.create_session(agent="default").id
    private = obj.store.create_session(agent="default", kind="private").id
    child = obj.store.create_session(agent="default", kind="subagent", parent=private).id
    fork = obj.store.fork(sid).id
    for target in (other, private, child, fork):
        obj.begin(target)
        with pytest.raises(ToolError, match="unavailable"):
            obj.read(target, ref)
    for principal, agent in (("bob", "default"), (None, "default"), ("alice", "another")):
        other_reader = Evidence(obj.store, obj.options, principal, agent)
        other_reader.begin(sid)
        with pytest.raises(ToolError, match="unavailable"):
            other_reader.read(sid, ref)
        assert obj.store.get_meta(sid)[META]["principal"] == "alice"
    private_ref = reference(obj.render(private, recorded(obj, private, source("log")))[-1].tool_results[0].text())
    assert obj.read(private, private_ref)["text"]
    for target in (sid, child):
        with pytest.raises(ToolError, match="unavailable"):
            obj.read(target, private_ref)


def test_binding_first_writer_wins_across_connections(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite"
    first, second = SessionStore(path), SessionStore(path)
    sid = first.create_session(agent="default").id
    first.set_meta(sid, {"unrelated": {"keep": True}})
    first.append_message(sid, Message.user("existing history"))
    alice = Evidence(first, Options(enabled=True), "alice", "default")
    bob = Evidence(second, alice.options, "bob", "default")
    snapshot, competing, release = threading.Event(), threading.Event(), threading.Event()
    last_seq, transaction = first.last_seq, second._tx

    def boundary(session):
        snapshot.set()
        assert release.wait(5)
        return last_seq(session)

    def competing_write(fn):
        competing.set()
        return transaction(fn)

    monkeypatch.setattr(first, "last_seq", boundary)
    monkeypatch.setattr(second, "_tx", competing_write)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(alice.begin, sid)
            assert snapshot.wait(5)
            follower = pool.submit(bob.begin, sid)
            try:
                assert competing.wait(5)
                assert not follower.done()
            finally:
                release.set()
            winner.result(timeout=5)
            follower.result(timeout=5)
        second.lock(sid, "finished")
        bob.begin(sid)
        assert first.get_meta(sid) == {
            "unrelated": {"keep": True}, "locked": "finished",
            META: {"principal": "alice", "agent": "default", "since": 1}}
        records = recorded(alice, sid, source("log"))
        ref = reference(alice.render(sid, records)[-1].tool_results[0].text())
        with pytest.raises(ToolError, match="unavailable"):
            bob.read(sid, ref)
    finally:
        release.set()
        first.close()
        second.close()


def test_metadata_initialization_preserves_updates_and_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite"
    first, second = SessionStore(path), SessionStore(path)
    sid = first.create_session(agent="default").id
    lookup = first.get_session

    def concurrent_metadata(session):
        info = lookup(session)
        second.lock(session, "finished")
        second.update_meta(session, lambda meta: meta.update(unrelated=["keep"]))
        return info

    monkeypatch.setattr(first, "get_session", concurrent_metadata)
    try:
        Evidence(first, Options(enabled=True), "alice", "default").begin(sid)
        assert first.get_meta(sid) == {
            "locked": "finished", "unrelated": ["keep"],
            META: {"principal": "alice", "agent": "default", "since": 0}}

        def failed(meta):
            meta["failed"] = True
            raise RuntimeError("initialization failed")

        with pytest.raises(RuntimeError, match="initialization failed"):
            first.update_meta(sid, failed)
        assert "failed" not in first.get_meta(sid)
        assert second.update_meta(sid, lambda meta: meta.setdefault("value", "returned")) == "returned"
        assert first.get_meta(sid)["value"] == "returned"
        assert second.update_meta(sid, {"value": "set"}) is None  # a dict of keys, main's form
        assert first.get_meta(sid)["value"] == "set" and first.get_meta(sid)["locked"] == "finished"
        with pytest.raises(KeyError):
            first.update_meta("missing-session", failed)
        assert first.update_meta("missing-session", {"value": "set"}) is None  # keys skip it, as on main
    finally:
        first.close()
        second.close()


def edit(changes, meta):
    """Apply an update_meta argument, a dict of keys or a callback, to meta."""
    return changes(meta) if callable(changes) else meta.update(changes)


@pytest.mark.parametrize("binding_first", [True, False])
def test_lock_and_binding_transactions_preserve_both_write_orders(tmp_path, monkeypatch, binding_first):
    path = tmp_path / "sessions.sqlite"
    first, second = SessionStore(path), SessionStore(path)
    sid = first.create_session(agent="default").id
    alice = Evidence(first, Options(enabled=True), "alice", "default")
    paused, following = (first, second) if binding_first else (second, first)
    paused_update, following_update = paused.update_meta, following.update_meta
    captured, attempted, release = threading.Event(), threading.Event(), threading.Event()

    def pause_after_snapshot(session, callback):
        def update(meta):
            captured.set()
            assert release.wait(5)
            return edit(callback, meta)
        return paused_update(session, update)

    def competing_update(session, callback):
        attempted.set()
        return following_update(session, callback)

    monkeypatch.setattr(paused, "update_meta", pause_after_snapshot)
    monkeypatch.setattr(following, "update_meta", competing_update)
    binding, lock = lambda: alice.begin(sid), lambda: second.lock(sid, "finished")
    lead, follow = (binding, lock) if binding_first else (lock, binding)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            leading = pool.submit(lead)
            assert captured.wait(5)
            following_task = pool.submit(follow)
            try:
                assert attempted.wait(5)
                assert not following_task.done()
            finally:
                release.set()
            leading.result(timeout=5)
            following_task.result(timeout=5)
        Evidence(second, alice.options, "bob", "default").begin(sid)
        assert first.get_meta(sid) == {
            "locked": "finished", META: {"principal": "alice", "agent": "default", "since": 0}}
    finally:
        release.set()
        first.close()
        second.close()


class Summaries:
    async def summarize(self, content, previous=None, **kwargs):
        return "S"


@pytest.mark.parametrize("context_first", [True, False])
@pytest.mark.parametrize("key", ["seen_pos", "compact_seq"])
def test_context_metadata_and_lock_transactions_preserve_both_write_orders(tmp_path, monkeypatch, key, context_first):
    """AgentContext writes seen_pos (sync_thread) and compact_seq (compact) with update_meta, so a lock is kept."""
    from exo.core.context import AgentContext
    path = tmp_path / "sessions.sqlite"
    first, second = SessionStore(path), SessionStore(path)
    context = AgentContext(first, Summaries(), leaf_size=2, tail_messages=4, min_compact_tokens=0)
    loop = Loop(first, Script(*[f"ok {i}" for i in range(6)]), context=context, system_prompt="SYS", retry_delay=0)

    async def talk():
        a = await loop.create_session(agent="razi")
        for i in range(6 if key == "compact_seq" else 1):
            await loop.send(a.id, f"fact number {i} " + "word " * 40)
        b = await loop.create_session(agent="razi")
        return a.id, b.id
    a, b = asyncio.run(talk())
    sid = b if key == "seen_pos" else a
    assert key not in first.get_meta(sid)
    write = (lambda: context.sync_thread(b, 0)) if key == "seen_pos" else (lambda: asyncio.run(context.compact(a, "SYS")))
    context_update, lock_update = first.update_meta, second.update_meta
    captured, attempted, release = threading.Event(), threading.Event(), threading.Event()
    pool, locking = ThreadPoolExecutor(max_workers=2), []

    def pause_after_snapshot(callback):
        def update(meta):
            captured.set()
            assert release.wait(5)
            return edit(callback, meta)
        return update

    def context_writes(session, callback):
        if context_first:
            return context_update(session, pause_after_snapshot(callback))
        locking.append(pool.submit(second.lock, session, "finished"))  # the lock takes its transaction first
        assert captured.wait(5)
        attempted.set()
        return context_update(session, callback)

    def lock_writes(session, callback):
        if not context_first:
            return lock_update(session, pause_after_snapshot(callback))
        attempted.set()
        return lock_update(session, callback)

    monkeypatch.setattr(first, "update_meta", context_writes)
    monkeypatch.setattr(second, "update_meta", lock_writes)
    try:
        writing = pool.submit(write)
        assert captured.wait(5)
        if context_first:
            locking.append(pool.submit(second.lock, sid, "finished"))
        assert attempted.wait(5)
        try:
            assert not (locking[0] if context_first else writing).done()  # it waits for the paused transaction
        finally:
            release.set()
        writing.result(timeout=10)
        locking[0].result(timeout=10)
        meta = first.get_meta(sid)
        assert meta["locked"] == "finished" and type(meta[key]) is int and meta[key] > 0
    finally:
        release.set()
        pool.shutdown(wait=True)
        first.close()
        second.close()


def test_pre_enable_records_and_fork_copies_are_not_claimed(evidence):
    obj, _ = evidence
    sid = obj.store.create_session(agent="default").id
    old = recorded(obj, sid, source("log"))
    obj.begin(sid)
    assert obj.render(sid, old) == old
    new = recorded(obj, sid, source("json"))
    assert reference(obj.render(sid, new)[-1].tool_results[0].text())
    fork = obj.store.fork(sid).id
    obj.begin(fork)
    copied = obj.store.messages(fork)
    assert obj.render(fork, copied) == copied


def test_undo_and_window_expiry_revoke_references(evidence):
    obj, sid = evidence
    obj.store.append_message(sid, Message.user("turn"))
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    assert obj.store.undo(sid) == 3
    with pytest.raises(ToolError, match="hidden"):
        obj.read(sid, ref)
    short = Evidence(obj.store, Options(enabled=True, retention_messages=10), "alice", "default")
    records = recorded(short, sid, source("log"))
    ref = reference(short.render(sid, records)[-1].tool_results[0].text())
    for _ in range(10):
        obj.store.append_message(sid, Message.user("later"))
    with pytest.raises(ToolError, match="expired"):
        short.read(sid, ref)
    assert short.render(sid, records) == records


@pytest.mark.parametrize("offset,limit", [(-1, 1), (True, 1), (10**9, 1), (0, 0), (0, True), (0, 12001)])
def test_page_limits_cannot_be_bypassed(evidence, offset, limit):
    obj, sid = evidence
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    with pytest.raises(ToolError):
        obj.read(sid, ref, offset, limit)


def test_invalid_missing_and_changed_references_do_not_fall_back(evidence):
    obj, sid = evidence
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    for bad in ("../sessions.sqlite", ref + "\n", ref[:-1] + ("a" if ref[-1] != "a" else "b"),
                ref.replace(":0:", ":999:"), "te1:m_missing:0:" + "0" * 64):
        with pytest.raises(ToolError, match="unavailable"):
            obj.read(sid, bad)
    with pytest.raises(ToolError, match="unavailable"):
        obj.read(None, ref)


@pytest.mark.parametrize("case", ["error", "short", "expansion", "no-call"])
def test_expansion_refuses_results_the_view_never_excerpts(evidence, case):
    """A digest made from text the caller already holds redeems only what render could have issued."""
    obj, sid = evidence
    text = "x" * 1000 if case == "short" else source("log")
    if case == "no-call":
        messages = [obj.store.append_message(sid, Message.tool([ToolResult.of_text("call-1", text)]))]
    else:
        messages = recorded(obj, sid, text, error=case == "error", tool=TOOL if case == "expansion" else "build")
    assert obj.render(sid, messages) == messages
    result = messages[-1].tool_results[0]
    forged = f"te1:{messages[-1].id}:0:{_digest(result, redact_secrets(text, keep_markers=True))}"
    with pytest.raises(ToolError, match="unavailable"):
        obj.read(sid, forged)


def test_known_secrets_redacted_before_preview_and_expansion(evidence, monkeypatch):
    obj, sid = evidence
    monkeypatch.setattr("exo.core.models.keys.known_secret_values", lambda *args: ["secret-value"])
    raw = "secret-value\n" + "ordinary\n" * 1600 + "secret-value"
    messages = recorded(obj, sid, raw)
    view = obj.render(sid, messages)[-1].tool_results[0].text()
    ref = reference(view)
    assert "secret-value" not in view and "[redacted]" in view
    assert "secret-value" not in obj.read(sid, ref)["text"]
    monkeypatch.setattr("exo.core.models.keys.known_secret_values", lambda *args: ["secret-value", "ordinary"])
    with pytest.raises(ToolError, match="redaction changed"):
        obj.read(sid, ref)


@pytest.mark.parametrize("settings", [{"enabled": 1}, {"enabled": "yes"}, {"extra": 1},
    {"page_chars": 0}, {"page_chars": 24001}, {"threshold_chars": 999}, {"preview_chars": True},
    {"threshold_chars": 2400, "preview_chars": 2400}, {"retention_messages": -1}, None, []])
def test_invalid_settings_fail_explicitly(settings):
    with pytest.raises(ConfigError):
        Options.parse(settings)


class Script:
    name = "fake"

    def __init__(self, *steps):
        self.steps, self.calls = list(steps), []

    async def stream(self, messages, tools, **kwargs):
        self.calls.append(deepcopy(messages))
        step = self.steps.pop(0)
        step = step(messages) if callable(step) else step
        msg = Message.assistant(tool_calls=[step]) if isinstance(step, ToolCall) else Message.assistant(step)
        yield ProviderEvent(kind="done", message=msg, stop_reason="tool_use" if msg.tool_calls else "end")


def test_live_loop_compacts_model_input_not_transcript_or_events_and_expands(evidence):
    obj, sid = evidence
    reg = Registry()
    obj.install(reg)
    original = source("log")
    reg.register(ToolSpec("build", "build"), lambda args, ctx: original)
    def expand(messages):
        result = messages[-1].tool_results[0]
        return ToolCall("expand", TOOL, {"reference": reference(result.text()), "offset": 100, "limit": 800})
    provider = Script(ToolCall("build-1", "build", {}), expand, "done")
    context = EvidenceContext(TreeContext(obj.store, None), obj)
    async def run_tool(call, ctx):
        return await reg.call(call, ToolContext(session=ctx["session"]))
    loop = Loop(obj.store, provider, context=context, tool_specs=reg.specs, run_tool=run_tool)
    result = asyncio.run(loop.send(sid, "build something"))
    assert result.stop_reason == "end"
    stored = [m for m in obj.store.messages(sid) if m.tool_results]
    assert stored[0].tool_results[0].text() == original
    assert len(provider.calls[1][-1].tool_results[0].text()) < len(original)
    page = json.loads(stored[1].tool_results[0].text())
    assert page["text"] == original[100:900]
    assert provider.calls[2][-1].tool_results[0].text() == stored[1].tool_results[0].text()
    completed = [e.body.result for e in obj.store.events_since() if e.type == "tool.completed"]
    assert completed[0].text() == original


def test_executor_can_refuse_retrieval_without_running_handler(evidence):
    obj, sid = evidence
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    class Broker:
        def __init__(self):
            self.calls = []
        async def execute(self, request, tool, run_trusted):
            self.calls.append(request)
            return ToolResult.of_text(request.call_id, "executor refuses evidence", True)
    broker = Broker()
    reg = Registry(executor=broker)
    obj.install(reg)
    obj.read = lambda *args, **kwargs: pytest.fail("refused handler ran")
    result = asyncio.run(reg.call(ToolCall("read", TOOL, {"reference": ref}), ToolContext(session=sid)))
    assert result.is_error and result.text() == "executor refuses evidence"
    assert broker.calls[0].session == sid and broker.calls[0].tool_name == TOOL


def test_cancelled_page_does_not_read(evidence):
    obj, sid = evidence
    registry = Registry()
    obj.install(registry)
    ctx = ToolContext(session=sid)
    ctx.cancel.set()
    obj.read = lambda *args, **kwargs: pytest.fail("cancelled read ran")
    result = asyncio.run(registry.call(ToolCall("read", TOOL, {"reference": "anything"}), ctx))
    assert result.is_error and "cancelled" in result.text()


def test_child_view_requires_retrieval_tool_in_child_allowlist(evidence):
    obj, _ = evidence
    wrapper = EvidenceContext(TreeContext(obj.store, None), obj)
    child_tree = TreeContext(obj.store, None)
    assert wrapper.for_child(child_tree, tools={"build"}) is child_tree
    child = wrapper.for_child(child_tree, tools={"build", TOOL})
    assert isinstance(child, EvidenceContext) and child.evidence is obj


def test_disabled_composition_imports_no_feature_module_or_state(tmp_path):
    home = tmp_path / "home"
    Config({"tool_output": {"enabled": False}, "core_plugins": [], "peers": {"enabled": False},
            "learn": {"enabled": False}, "models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}}, home).save()
    code = ("import asyncio, sys; from exo.compose import create; "
            "e=create(); assert 'exo.tool_evidence' not in sys.modules; "
            "assert 'tool_output' not in e.registry.names(); "
            "s=e.store.create_session(); assert e.store.get_meta(s.id)=={}; asyncio.run(e.close())")
    env = {**os.environ, "EXO_HOME": str(home), "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "EXO_PROFILE": "", "EXO_PLUGIN_DIRS": str(tmp_path / "absent"), "EXO_TOOL_EXECUTOR_REQUIRED": "0"}
    env.pop("EXO_PROFILE")
    env.pop("EXO_TOOL_EXECUTOR", None)
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not list(home.glob("*evidence*"))


def test_composition_enables_in_lean_and_rolls_back_invalid_config(tmp_path, monkeypatch):
    from exo import compose
    from exo.core import agentlog
    from test_harness_composition import configured
    cfg = configured(tmp_path, monkeypatch, tool_output={"enabled": True})
    engine = compose.create()
    try:
        assert isinstance(engine.loop.context, EvidenceContext)
        assert engine.registry.get(TOOL)
        assert engine.plugins.slots.memory is None
    finally:
        asyncio.run(engine.close())
    cfg.data["tool_output"] = {"enabled": True, "page_chars": 999999}
    cfg.save()
    with pytest.raises(ConfigError):
        compose.create()
    writer = agentlog.claim_writer(cfg.home, "default")
    agentlog.release_writer(writer)


def test_retrieval_tool_name_collision_fails_without_replacing_plugin(evidence):
    obj, _ = evidence
    registry = Registry()
    registry.register(ToolSpec(TOOL, "other plugin"), lambda *args: "other")
    with pytest.raises(ConfigError, match="conflicts"):
        obj.install(registry)
    assert registry.get(TOOL).spec.description == "other plugin"


def test_reused_call_ids_and_multiple_results_are_selected_by_message_and_index(evidence):
    obj, sid = evidence
    first = recorded(obj, sid, "FIRST\n" + source("log"))
    first_ref = reference(obj.render(sid, first)[-1].tool_results[0].text())
    second = recorded(obj, sid, "SECOND\n" + source("json"))
    second_ref = reference(obj.render(sid, second)[-1].tool_results[0].text())
    assert first[1].tool_results[0].call_id == second[1].tool_results[0].call_id
    assert obj.read(sid, first_ref)["text"].startswith("FIRST\n")
    assert obj.read(sid, second_ref)["text"].startswith("SECOND\n")
    assistant = obj.store.append_message(sid, Message.assistant(tool_calls=[ToolCall("same", "search", {})]))
    message = obj.store.append_message(sid, Message.tool([
        ToolResult.of_text("same", "INDEX ZERO\n" + source("log")),
        ToolResult.of_text("same", "INDEX ONE\n" + source("log"))]))
    views = obj.render(sid, [assistant, message])[-1].tool_results
    assert obj.read(sid, reference(views[0].text()))["text"].startswith("INDEX ZERO\n")
    assert obj.read(sid, reference(views[1].text()))["text"].startswith("INDEX ONE\n")


def test_real_engine_main_and_child_share_the_rendering_and_executor_path(tmp_path):
    from exo.application import CoreEngine
    from exo.core.plugins import Manifest, PluginManager
    from exo.wire import Brief
    seen = []
    class Broker:
        async def execute(self, request, tool, run_trusted):
            seen.append(request)
            return await run_trusted()
    plugins = PluginManager()
    plugins._run_register(Manifest(name="allow-test"), "dir",
                          lambda ctx: ctx.register_hook("pre_tool_call", lambda **kwargs: None))
    engine = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                        plugins=plugins, executor=Broker(), peers={"enabled": False})
    attach(engine, Options(enabled=True), "alice")
    original = source("log")
    engine.registry.register(ToolSpec("build", "build"), lambda args, ctx: original)
    def expand(messages):
        return ToolCall("expand", TOOL, {"reference": reference(messages[-1].tool_results[0].text()), "limit": 512})
    async def run():
        try:
            engine.loop.provider = Script(ToolCall("main-build", "build", {}), expand, "main done")
            main = await engine.loop.create_session(agent=engine.agent)
            result = await engine.loop.send(main.id, "build")
            assert result.stop_reason == "end"
            assert len(engine.loop.provider.calls[1][-1].tool_results[0].text()) < len(original)
            child = engine.agents._loop(Brief(id="brief", thread=main.id, goal="inspect", tools=["build", TOOL]),
                                       Script(ToolCall("child-build", "build", {}), expand, "child done"), "fake:x")
            assert isinstance(child.context, EvidenceContext)
            sub = await child.create_session(parent=main.id, agent=engine.agent, kind="subagent")
            result = await child.send(sub.id, "inspect")
            assert result.stop_reason == "end"
            assert len(child.provider.calls[1][-1].tool_results[0].text()) < len(original)
            narrow = engine.agents._loop(Brief(id="b2", thread=main.id, goal="inspect", tools=["build"]),
                                        Script("done"), "fake:x")
            assert not isinstance(narrow.context, EvidenceContext)
            assert [(r.tool_name, r.session) for r in seen] == [
                ("build", main.id), (TOOL, main.id), ("build", sub.id), (TOOL, sub.id)]
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["🙂" * 20000, "\x00" * 24000, "\\\"\n" * 8000])
@pytest.mark.parametrize("limit", [12000, 24000])
def test_serialized_expansion_pages_fit_registry_limit(evidence, text, limit):
    obj, sid = evidence
    obj.options = Options(enabled=True, page_chars=limit)
    ref = reference(obj.render(sid, recorded(obj, sid, text))[-1].tool_results[0].text())
    registry = Registry()
    obj.install(registry)
    async def expand_all():
        offset, chunks = 0, []
        while offset is not None:
            result = await registry.call(ToolCall("page", TOOL, {"reference": ref, "offset": offset, "limit": limit}),
                                         ToolContext(session=sid))
            assert not result.is_error
            page = json.loads(result.text())
            assert page["trust"] == TRUST and page["tool"] == "build"
            assert len(result.text()) < 100000
            chunks.append(page["text"])
            offset = page["next_offset"]
        return "".join(chunks)
    assert asyncio.run(expand_all()) == text


def test_transformed_or_hidden_context_messages_never_get_unrecoverable_references(evidence):
    obj, sid = evidence
    obj.store.append_message(sid, Message.user("turn"))
    records = recorded(obj, sid, source("log"))
    edited = deepcopy(records)
    edited[-1].tool_results[0].content[0].text += "modified by context plugin"
    assert obj.render(sid, edited) == edited
    renamed = deepcopy(records)
    renamed[0].tool_calls[0].name = "different-source"
    assert obj.render(sid, renamed) == renamed
    obj.store.undo(sid)
    assert obj.render(sid, records) == records


def test_invalid_enabled_limits_fail_before_plugin_or_store_startup(tmp_path, monkeypatch):
    from exo import compose
    from exo.core.plugins import PluginManager
    home = tmp_path / "home"
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.delenv("EXO_PROFILE", raising=False)
    Config({"tool_output": {"enabled": True, "retention_messages": -1}}, home).save()
    monkeypatch.setattr(PluginManager, "load_all", lambda *a, **kw: pytest.fail("plugin load before validation"))
    with pytest.raises(ConfigError, match="retention_messages"):
        compose.create()
    assert not (home / "sessions.sqlite").exists()


def test_real_gate_classifies_pages_as_reads_and_enforces_read_deny(tmp_path, monkeypatch):
    from exo import compose, gate
    from test_harness_composition import configured
    configured(tmp_path, monkeypatch, tool_output={"enabled": True})
    engine = compose.create()
    obj = engine.loop.context.evidence
    sid = engine.store.create_session(agent=engine.agent).id
    obj.begin(sid)
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    call = ToolCall("read", TOOL, {"reference": ref, "limit": 512})
    assert gate.classify((TOOL, call.arguments)).category == "read"
    async def run():
        try:
            allowed = await asyncio.wait_for(engine.run_tool(call, {"session": sid}), 3)
            assert not allowed.is_error
            assert json.loads(allowed.text())["text"] == source("log")[:512]
            folder = tmp_path / "principal" / "policies"
            folder.mkdir()
            (folder / "gates.yaml").write_text("deny: [read]\n")
            monkeypatch.setattr(obj, "read", lambda *args, **kw: pytest.fail("denied read ran"))
            refused = await asyncio.wait_for(engine.run_tool(call, {"session": sid}), 3)
            assert refused.is_error and "den" in refused.text().lower()
        finally:
            await engine.close()
    asyncio.run(run())


SHAPED = "te1:m_example:0:" + "0" * 64  # the shape render issues; no stored result has it


@pytest.mark.parametrize("args", [{}, {"reference": None}, {"reference": []}, {"reference": 7},
    {"reference": SHAPED, "offset": True}, {"reference": SHAPED, "offset": -1},
    {"reference": SHAPED, "offset": "0"}, {"reference": SHAPED, "limit": True},
    {"reference": SHAPED, "limit": 0}, {"reference": SHAPED, "limit": 24001},
    {"reference": SHAPED, "session": "other"},
    {"reference": "ref"}, {"reference": "../../.ssh/id_rsa"}, {"reference": "~/.aws/credentials"},
    {"reference": "/etc/shadow", "limit": 512}, {"reference": "$(rm -rf /)", "offset": 0},
    {"reference": SHAPED + "\n"}, {"reference": " " + SHAPED}, {"reference": SHAPED[:-1]},
    {"reference": SHAPED.upper()}, {"reference": SHAPED.replace(":0:", ":1000:")},
    {"reference": SHAPED.replace("m_example", "m_../x")}, {"reference": SHAPED.replace(":0:", ":00:")},
    {"reference": SHAPED.replace(":0:", ":٠:")}, {"reference": SHAPED.replace(":0:", ":０:")}])
def test_malformed_tool_output_arguments_keep_generic_gate_classification(args):
    from exo import gate
    assert gate.classify((TOOL, args)).category == "unknown"


def test_gate_reads_only_the_reference_shape_render_issues(evidence):
    """The gate imports no other Exo module, so it keeps its own copy of the pattern, and this test keeps them equal."""
    from exo import gate
    from exo.tool_evidence import _REF
    assert (gate._TOOL_OUTPUT_REF.pattern, gate._TOOL_OUTPUT_REF.flags) == (_REF.pattern, _REF.flags)
    obj, sid = evidence
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    assert gate.classify((TOOL, {"reference": ref})).category == "read"
    head, index, digest = ref.rsplit(":", 2)
    assert index == "0"
    aliases = [f"{head}:{same}:{digest}" for same in ("00", "٠", "０", "०")]  # int() reads each as 0
    for path in ("../../.ssh/id_rsa", "~/.aws/credentials", "/etc/shadow", "$(rm -rf /)", *aliases):
        assert gate.classify((TOOL, {"reference": path})).category == "unknown"
        with pytest.raises(ToolError, match="unavailable"):
            obj.read(sid, path)


def test_gate_page_limit_ceiling_is_the_top_of_the_page_chars_range():
    """The gate cannot see the configured page_chars, so it reads a page up to the top of the allowed range."""
    from exo import gate
    from exo.tool_evidence import PAGE_CHARS
    low, top = PAGE_CHARS
    assert Options.parse({"page_chars": low}).page_chars == low and Options.parse({"page_chars": top}).page_chars == top
    for bad in (low - 1, top + 1):
        with pytest.raises(ConfigError):
            Options.parse({"page_chars": bad})
    assert gate.classify((TOOL, {"reference": SHAPED, "limit": top})).category == "read"
    assert gate.classify((TOOL, {"reference": SHAPED, "limit": top + 1})).category == "unknown"


@pytest.mark.parametrize("category", ["credentials", "store_write"])
@pytest.mark.parametrize("key", ["path", "file_path"])
@pytest.mark.parametrize("reference_arg", [{}, {"reference": SHAPED, "limit": 512}])
def test_unsupported_tool_output_paths_preserve_generic_gate_floors(tmp_path, monkeypatch, category, key, reference_arg):
    from exo import gate
    home = tmp_path / "exo-home"
    (home / "state").mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(home))
    path = "~/.ssh/id_ed25519" if category == "credentials" else str(home / "state" / "custom.json")
    args = {**reference_arg, key: path}
    classified = gate.classify((TOOL, args))
    generic = gate.classify(("evidence_probe", args))
    assert classified.category == generic.category == category
    assert [part.category for part in classified.parts] == [part.category for part in generic.parts] == ["unknown"]
    policy = {"gates": {"categories": {category: "allow"}}}
    assert gate.decide(classified, policy).outcome == gate.decide(generic, policy).outcome == "ask"


def test_registered_expansion_rejects_unsupported_arguments_before_reading(evidence, monkeypatch):
    obj, sid = evidence
    ref = reference(obj.render(sid, recorded(obj, sid, source("log")))[-1].tool_results[0].text())
    registry = Registry()
    obj.install(registry)
    monkeypatch.setattr(obj, "read", lambda *args, **kwargs: pytest.fail("unsupported arguments reached expansion"))

    async def run():
        for key in ("path", "file_path", "files", "destination", "session"):
            result = await registry.call(ToolCall("invalid", TOOL, {"reference": ref, key: "~/.ssh/id_ed25519"}),
                                         ToolContext(session=sid))
            assert result.is_error and "unknown argument" in result.text()
    asyncio.run(run())


@pytest.mark.parametrize("count,size,compressed", [(65, 9000, 64), (12, 100000, 10)])
def test_build_work_limits_leave_later_results_intact(evidence, count, size, compressed):
    obj, sid = evidence
    calls = [ToolCall(f"call-{i}", "build", {}) for i in range(count)]
    assistant = obj.store.append_message(sid, Message.assistant(tool_calls=calls))
    original = "x" * size
    message = obj.store.append_message(sid, Message.tool([ToolResult.of_text(c.id, original) for c in calls]))
    results = obj.render(sid, [assistant, message])[-1].tool_results
    assert sum(result.text().startswith("[Retained tool output:") for result in results) == compressed
    assert all(result.text() == original for result in results[compressed:])
    assert all(result.text() == original for result in obj.store.messages(sid)[-1].tool_results)
