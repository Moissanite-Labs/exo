"""Diagnostic views over a running CoreEngine: /context, /compact, /trace, /findings, /handoff facts, memory status.

Moved out of ``exo.core.engine`` (docs/ENGINE-SPLIT.md, issue #46). Each function reads the engine's
public state and its stores; none changes how a turn runs. ``builtin_command`` answers the built-in
``command.run`` names and returns ``MISSING`` for anything else, so plugin commands still resolve after it.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from exo.core import jobs as jobs_mod
from exo.core import trace, workspace
from exo.core.context import AgentContext
from exo.core.hooks import maybe_await
from exo.core.findings import STATUSES
from exo.core.types import to_dict
from exo.principal import current_caller, demoted, is_principal
from exo.protocol import schema as s
from exo.server.engine import CommandFailed

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine

MISSING = object()


def _tok(n: int) -> str:
    return str(n)


def render_context(r: dict[str, Any], tree: AgentContext) -> str:
    t, tr, nodes = r["tokens"], r["tree"], r["nodes"]
    levels = ", ".join(f"L{n}" for n in tr["levels"]) or "none"
    note = f"{r['notes']['stored']} stored" + (", news from other threads waiting" if r["notes"]["unseen"] else "")
    lines = [f"Next call for thread {r['thread'][-8:]} of agent {r['agent']} (estimates, about 4 characters per token):",
             f"  system        {_tok(t['system']):>7}   prompt and memory block",
             f"  summary tree  {_tok(t['tree']):>7}   {tr['summaries']} summaries over {tr['nodes']} nodes, levels {levels}"
             + (f", log 1-{tr['covers'][1]}" if tr["covers"] else ""),
             f"  other threads {_tok(t['notes']):>7}   {note}",
             f"  recent tail   {_tok(t['tail']):>7}   {r['tail']['messages']} messages of this thread",
             f"  total         {_tok(t['total']):>7}",
             f"Tree nodes: {nodes['done']} done, {nodes['pending']} pending ({nodes['running']} running), "
             f"{nodes['skipped']} skipped (kept verbatim: too small or not shorter), {nodes['failed']} failed.",
             f"Log: {r['log_entries']} entries; summarized up to {r['frontier']}. /compact would fold "
             f"{r['eligible']['leaves']} leaf chunk(s) now ({r['eligible']['leaves_after_tail_wait']} are due anyway; "
             f"a chunk is {tree.leaf_size} entries, the tail wait is {tree.tail_messages}).",
             _capture_line(r["capture"])]
    return "\n".join(lines)


def _capture_line(c: dict[str, Any]) -> str:
    if not c["store"]:
        return (f"Principal messages not yet captured: {c['uncaptured']} (no principal store is attached, so nothing "
                "captures them; the log still holds them verbatim).")
    return (f"Principal messages not yet captured: {c['uncaptured']} (a leaf offers its principal messages to the "
            "principal store before it is summarized; /compact captures all of them first).")


def render_compact(res: dict[str, Any]) -> str:
    b, a, f = res["before"]["tokens"]["total"], res["after"]["tokens"]["total"], res["folded"]
    cap = res["captured"]
    lines = [f"Compacted: {len(f)} node(s) folded, {res['skipped']} left verbatim; context {b} to {a} tokens.",
             f"Principal capture: {cap['taken']} message(s) captured first; {cap['uncaptured']} still not captured"
             + ("." if cap["store"] else " (no principal store is attached).")]
    lines += [f"  L{n['level']} log {n['start']}-{n['end']}: {n['before_tokens']} to {n['after_tokens']} tokens" for n in f]
    if not f and not res["skipped"] and not res["failed"]:
        lines.append("  nothing eligible: every full chunk is already summarized, or too small to be worth it")
    lines += [f"  failed: {e}" for e in res["failed"]]
    if not res["complete"]:
        lines.append("  some summaries are still running; /context shows progress")
    return "\n".join(lines)


def trace_text(eng: CoreEngine, session: str | None, last: int = 10, calls: bool = False) -> str:
    """What ``exo_trace`` and ``/trace`` show: the model this session runs on, then the per-turn table."""
    info = eng.store.get_session(session) if session else None
    # the newest call that names its route first: a call cut short by a stop or an error names none
    rows = eng.store._q("SELECT body FROM events WHERE session=? AND type='model.call' ORDER BY "
                        "COALESCE(json_extract(body, '$.route'), '') = '', seq DESC LIMIT 1", (session,)) if session else []
    served = (json.loads(rows[0]["body"]).get("route") if rows else "") or "no model call recorded yet"
    head = [f"running on: {eng.loop.model or '-'} (session model {info.model if info else '-'}, last call served "
            f"by {served}); chain: {', '.join(r.ref for r in eng.router.chain('main')) or '-'}",
            f"harness {eng.findings.version}; {eng.findings.summary()}"]
    open_rows = [r for r in eng.findings.list() if r["status"] in ("open", "reopened")][:5]
    if open_rows:
        head.append("open findings: " + ", ".join(f"{r['id']} {r['title'][:60]}" for r in open_rows))
    if not session:
        return "\n".join(head)
    peers = trace.peer_text(eng.store, session)
    jobs = jobs_mod.job_text(eng.store, session)
    return "\n".join(head + ["", trace.table(trace.build(eng.store, session, last, eng.thresholds), calls=bool(rows))]
                     + (["", peers] if peers else []) + (["", jobs] if jobs else []))

def agent_ctx(eng: CoreEngine, session: str | None) -> AgentContext:
    tree = eng.context_tree
    if not session or eng.store.get_session(session) is None:
        raise CommandFailed("bad_request", "no thread to look at")
    if not isinstance(tree, AgentContext) or tree._agent_key(session) is None:
        raise CommandFailed("bad_request", "this thread has no shared memory (a subagent or plain session)")
    return tree

async def context_text(eng: CoreEngine, session: str | None) -> str:
    """``/context``: what the next model call for this thread carries. Read-only."""
    return (await eng.context_report(session))["text"]

async def context_report(eng: CoreEngine, session: str | None) -> dict[str, Any]:
    tree = agent_ctx(eng, session)
    assert session is not None  # agent_ctx has validated the thread
    r = tree.report(session, await eng.loop.base_system(session))
    info = eng._info(session)
    model = info.model or eng.loop.model or ""
    capacity = eng.router.models.get(model, {}).get("context")
    return {**r, "schema_version": 1, "model": model,
            "capacity": capacity if isinstance(capacity, int) and capacity > 0 else None,
            "text": render_context(r, tree)}

async def compact_text(eng: CoreEngine, session: str | None) -> str:
    """``/compact``: summarize now what is eligible for this agent. Runs here, in the process that holds the
    agent lock, so a joining window never writes the tree itself."""
    tree = agent_ctx(eng, session)
    assert session is not None  # agent_ctx has validated the thread
    st = eng.loop._state.get(session)
    if st is not None and st.running:
        raise CommandFailed("busy", "a turn is running in this thread; try again when it ends")
    res = await tree.compact(session, await eng.loop.base_system(session))
    return render_compact(res)

async def handoff_facts(eng: CoreEngine, session: str | None, path_arg: str = "") -> dict[str, Any]:
    """What the agent needs to write a handoff (D-032, ``/handoff``): the thread, git state of its cwd, open
    findings and tasks, recent summaries from the tree, the last turns of the trace, and one key per entry of
    ``eng.handoff_sources`` (``jobs`` today is empty; background jobs register themselves there).
    Reads only; the agent writes the file through its normal write tool and the gate."""
    info = eng._info(session) if session else None
    if info is None:
        raise CommandFailed("bad_request", "no thread to hand off")
    cwd = info.cwd or eng.cwd
    facts: dict[str, Any] = {
        "date": time.strftime("%Y-%m-%d"), "seat": eng.agent,
        "path": str(workspace.handoff_path(eng.agent, path_arg)),
        "thread": {"id": info.id, "title": info.title, "model": info.model, "kind": info.kind,
                   "cwd": cwd, "created": info.created,
                   "user_turns": sum(1 for m in eng.store.messages(info.id) if m.role == "user"
                                     and not m.meta.get("note"))},
        # Even read-only Git commands can execute workspace .git/config fsmonitor hooks.
        "git": ({"repo": None, "unavailable": "Git facts unavailable: required executor has no isolated Git facts operation"}
                if eng.executor_required else workspace.git_facts(cwd)),
        "findings": [{"id": r["id"], "severity": r["severity"], "title": r["title"][:100]}
                     for r in eng.findings.list("open")[:10]],
    }
    latest: dict[str, dict[str, Any]] = {}
    for e in eng.store.events_since(0, info.id, limit=1 << 30):
        if isinstance(e.body, s.TaskUpdated):
            latest[e.body.task_id] = {"id": e.body.task_id, "title": e.body.title[:100], "status": e.body.status}
    facts["tasks"] = [t for t in latest.values() if t["status"] in ("queued", "running")]
    try:
        tree = agent_ctx(eng, info.id)
        key = tree._agent_key(info.id)
        assert key is not None  # agent_ctx has checked it
        _, frontier, parts = tree._view(key)
        facts["decisions"] = [p[:1500] for p in parts[-2:]]
        facts["context"] = {"log_entries": tree._last(key), "summarized_to": frontier}
    except CommandFailed:
        facts["decisions"] = []
    facts["trace"] = eng.trace_text(info.id, 3)
    for key, fn in eng.handoff_sources.items():
        try:
            facts[key] = await maybe_await(fn(info.id))
        except Exception as exc:  # a broken source must not block the handoff
            trace.report(f"handoff source {key}", exc, info.id)
            facts[key] = []
    return facts

def memory_status(eng: CoreEngine) -> dict[str, str]:
    """The memory slot's state: ``on``; ``off`` until a setup step (``NotConfigured``, a quiet notice);
    ``failed`` (an error at start); ``none`` with no memory plugin (the agent log and summary tree still run)."""
    mem = eng.plugins.slots.memory
    if mem is None:
        return {"state": "none", "provider": "", "text": "no memory plugin: the agent log and summary tree only"}
    if eng.memory_off:
        return {"state": "off", "provider": mem.name, "text": eng.memory_off}
    if eng.memory_init_error:
        return {"state": "failed", "provider": mem.name, "text": eng.memory_init_error}
    return {"state": "on", "provider": mem.name, "text": ""}

async def builtin_command(eng: CoreEngine, b: s.CommandRun) -> Any:
    if b.name == "limits":
        if b.args.strip() not in ("", "json", "status"):
            if not is_principal(current_caller()):
                raise CommandFailed("forbidden", "only the principal can change core-wide run limits")
            try:
                eng.run_limits.update(b.args.strip())
            except (ValueError, TypeError):
                raise CommandFailed("bad_request", "usage: /limits rounds|worker-timeout default|off|nonnegative-integer [all], or /limits default") from None
        return eng.run_limits.status(eng.loop.max_iterations, eng.agents.limits.max_turns, eng.agents.limits.timeout_s)
    if b.name == "approval-authority":
        from exo.principal import require_principal
        require_principal("manage delegated approval authority")
        if not b.session or (info := eng.store.get_session(b.session)) is None or info.agent != eng.agent:
            raise CommandFailed("bad_request", "Select an existing parent session")
        return eng.approvals.delegation_command(b.session, b.args.strip() or "status")
    if b.name == "context":
        if b.args.strip() not in ("", "json"):
            raise CommandFailed("bad_request", "usage: /context [json]")
        return await eng.context_report(b.session) if b.args.strip() == "json" else await eng.context_text(b.session)
    if b.name == "compact":
        return await eng.compact_text(b.session)
    if b.name == "trace":
        return eng.trace_text(b.session, int(b.args) if b.args.strip().isdigit() else 10)
    if b.name == "findings":
        arg = b.args.strip()
        if arg == "count":  # "open" is what needs work (open + reopened); by_state reconciles every finding
            return {"open": eng.findings.open_count(), "by_state": eng.findings.counts()}
        return eng.findings.text(arg if arg in STATUSES else "all", viewer=b.session or "ui")
    if b.name == "unsteer":  # D-045: pull a queued steer message back; args is its id, empty means the newest
        if not b.session:
            raise CommandFailed("bad_request", "unsteer needs a session")
        gone = eng.loop.unsteer(b.session, b.args.strip(), principal=is_principal(current_caller()))
        for m in gone:
            eng.hub.publish(s.SteerCancelled(message_id=m.id or "", text=m.text()), b.session)
        return {"cancelled": [{"id": m.id, "text": m.text()} for m in gone]}
    if b.name == "subagent":  # #97: list, open, send to and stop a running child; exo.subagents holds the rules
        from exo import subagents
        return await subagents.command(eng, b)
    if b.name == "rename":  # D-069: the session title, principal only (D-052); announced as session.updated
        if not b.session:
            raise CommandFailed("bad_request", "rename needs a session")
        if not is_principal(current_caller()):
            raise CommandFailed("forbidden", "only the principal can rename a session" + demoted())
        title = " ".join(b.args.split())[:200]
        if not title:
            raise CommandFailed("bad_request", "usage: /rename <title>")
        eng._info(b.session)
        info = eng.store.update_session(b.session, title=title)
        eng.hub.publish(s.SessionUpdated(session=info), b.session)
        return {"session": to_dict(info)}
    if b.name == "chatgpt":
        if not is_principal(current_caller()):
            raise CommandFailed("forbidden", "ChatGPT connection changes require principal authority")
        try:
            from exo import chatgpt_auth
            request = json.loads(b.args or '{}')
            action = request.get("action", "status")
            auth = chatgpt_auth.service(eng.home, eng.agent)
            if action == "status":
                return auth.status()
            if action == "start":
                return await asyncio.to_thread(auth.start, request.get("profile"))
            if action == "cancel":
                return auth.cancel()
            if action == "disconnect":
                return await asyncio.to_thread(auth.disconnect, request.get("profile"))
            raise ValueError()
        except Exception:
            raise CommandFailed("bad_request", "ChatGPT connection action failed. Check the connection or try again.") from None
    if b.name == "providers":  # D-034: status, or "reload [name]" after a client stored a key. Never a key.
        word, _, rest = b.args.strip().partition(" ")
        if word == "reload":
            name = rest.strip() or None
            if name and name not in eng.router.providers:
                raise CommandFailed("not_found", f"unknown provider {name!r}")
            return {"reloaded": eng.router.reload_credentials(name)}
        from exo import providers_setup
        table = await asyncio.to_thread(providers_setup.rows, eng.router, eng.home,
                                       prober=providers_setup.probe)
        return {"providers": table, "main": await eng._main_status(table)}
    if b.name == "memory_status":  # what the memory plugin is doing, so a client's boot line tells the truth
        return memory_status(eng)
    if b.name == "handoff_facts":  # D-032: the facts /handoff gives the agent; the client sends the turn
        return await eng.handoff_facts(b.session, b.args)
    if b.name == "harness_error":  # a client (the TUI) reporting an exception it caught
        try:
            d = json.loads(b.args)
        except ValueError:
            d = {"where": "client", "message": b.args}
        trace.report(str(d.get("where") or "client"), str(d.get("message") or ""), b.session,
                     error_type=str(d.get("error_type") or ""), tb=str(d.get("traceback") or ""))
        return None
    return MISSING
