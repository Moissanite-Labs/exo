# Lifted from NousResearch/hermes-agent tools/delegate_tool_progress.py, tools/delegate_tool_toolsets.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""Delegation: a subagent is a child session on the same engine with a narrower toolset.

The ``delegate`` tool turns each requested task into a typed ``Brief`` (``exo.wire``), runs a
child Loop on the engine's store, hub, router and tool runner, and returns one ``Receipt`` per
child. The harness fills every receipt field it can measure (status, usage, time, tool calls,
files); the child only writes the summary. Briefs and receipts are stored as SYM-2P lines in
the ``tasks`` table and sent to clients in ``task.updated`` events, with English in ``detail``.
Models only ever read the English form (docs/AGENT-WIRE.md section 3).

Limits: depth (flat by default), children per call, concurrent children per depth level,
tool rounds, tokens and wall time per child. Cancelling the parent's tool call interrupts
every child, which cancels their own children in turn. A background task is the one exception:
its parent's call has already returned, so only an explicit stop (``subagent stop <task_id>``),
the child's own limits or engine shutdown stops it.

A call may run its tasks in the background instead (opt-in, ``background: true``): it returns each task id at once,
the run stays tracked in the same ``tasks`` table and ``Delegator`` dicts, and its receipt reaches the parent on the
existing ingress notice path. The default call still waits for every child.

Only the child prompt wording and the blocked-tool list are lifted from Hermes; the rest is new.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass, fields, replace
from typing import Any, List

from exo.core.context import DefaultCompressor, TreeContext
from exo.core.ingress import Ingress, IngressPath
from exo.core.loop import Loop
from exo.core.models.router import TIERS
from exo.core.peer_target import EngineTarget
from exo.core.sessions import new_id
from exo.core.tools.registry import ToolContext, ToolError
from exo.core.types import ToolCall, ToolResult, ToolSpec, Usage
from exo.protocol import schema as s
from exo.wire import Brief, Receipt, encode, render, sign
from exo.wire.grammar import SYM
from exo.wire.packets import decode_any

ROLES = ("builder", "scout", "verifier", "critic")
READ_ONLY = {"scout", "critic"}  # low-risk tools only
ROLE_TIERS = {"scout": "fast", "verifier": "standard", "builder": "standard", "critic": "deep"}
# Hermes DELEGATE_BLOCKED_TOOLS, renamed for Exo: no recursion (unless depth allows), no user
# interaction (no ui panels), no shared memory writes, no outward messages, no scheduling in the parent's name.
BLOCKED_TOOLS = frozenset({"delegate", "clarify", "ask", "memory", "send_message", "bus_post", "cronjob_manage", "ui",
                           "exo_harness_report", "exo_trace", "exo_end_turn", "peers", "peer_send",
                           "jobs", "job_output", "job_kill"})  # jobs belong to the main agent's threads (D-032)  # peers are the main agent's (D-026); self-observation is the main agent's job (D-025)
BLOCKED_TOOLSETS = frozenset({"memory", "bus", "delegation", "kanban", "skills"})  # children get no skills index
WRITE_TOOLS = {"write_file", "edit_file"}
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)

CHILD_PROMPT = (
    "You are a focused subagent working on a specific delegated task.\n\n"
    "Complete this task using the tools available to you. When finished, provide a clear, concise summary of:\n"
    "- What you did\n- What you found or accomplished\n- Any files you created or modified\n- Any issues encountered\n\n"
    "Keep your final summary tight: lead with outcomes, prefer bullet points over paragraphs, and don't replay your "
    "whole process. Your response is returned to the parent agent as a summary, and overlong summaries crowd out the "
    "parent's context window.")
CODE_FIND_NOTE = ("\n\nScouts and verifiers: use `code_find` to learn unfamiliar code or answer a behavior question across "
                  "files; use grep or read_file when you already know the symbol or path.")
ORCHESTRATOR_NOTE = (
    "\n\nYou may use the `delegate` tool to run independent subtasks in parallel. Do not re-delegate your whole "
    "task to one worker. Coordinate your workers' results; you are responsible for the final summary.")

TABLE = ("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, parent TEXT, child TEXT, depth INTEGER NOT NULL, "
         "title TEXT NOT NULL, status TEXT NOT NULL, brief TEXT NOT NULL, receipt TEXT, sig TEXT, "
         "created TEXT NOT NULL, updated TEXT NOT NULL)")

_ITEM = {
    "goal": {"type": "string", "description": "The whole task in plain words. The child has no memory of this "
                                              "conversation, so include everything it needs."},
    "context": {"type": "string", "description": "Facts, file paths and constraints the child needs."},
    "role": {"type": "string", "enum": list(ROLES), "description": "scout and critic only read; builder and "
                                                                   "verifier may edit files and run commands."},
    "tools": {"type": "array", "items": {"type": "string"}, "description": "Optional: narrow the child's tools."},
    "accept": {"type": "string", "description": "Optional: how the child knows it is done."},
    "seat": {"type": "string", "description": "Optional: send the task to another seat over the bus instead of "
                                              "running it here. It is queued; the receipt arrives later as a bus "
                                              "message."},
    "tier": {"type": "string", "enum": list(TIERS),
             "description": "Optional model tier, a cost choice. fast: retrieval, search, reading and summarizing, "
                            "running known commands, simple specified edits. standard: implementing a clear spec, "
                            "tests, docs, contained reviews. deep: only when judgment errors compound "
                            "(architecture, open research synthesis, hard cross-file debugging). Prefer the cheaper "
                            "tier when unsure. Default by role: scout fast, builder and verifier standard, critic "
                            "deep. A child never runs above your own tier."},
    "background": {"type": "boolean",
                   "description": "Optional: start the task and return at once with its task id; its receipt arrives "
                                  "later as a notice in this session. Use it when you have other work to do meanwhile. "
                                  "Set it on every task in a call, or on none."},
}
SPEC = ToolSpec(
    name="delegate", toolset="delegation", risk="low",
    description=("Hand self-contained tasks to subagents. Each runs in its own session with a narrower toolset "
                 "and returns a receipt: status, cost, files changed and a short summary. Give one task with "
                 "`goal`, or several independent ones in `tasks` to run them in parallel. Use it for work that "
                 "would flood your context or that splits into independent parts, not for one or two tool calls. "
                 "Put each child on the cheapest model tier that can do the job (see `tier`); a child that fails "
                 "is retried once one tier up. By default the call waits for every task; with `background` it "
                 "returns the task ids at once and each receipt arrives later as a notice. Stop a background task "
                 "with `subagent stop <task_id>`."),
    parameters={"type": "object", "properties": {
        **_ITEM, "tasks": {"type": "array", "items": {"type": "object", "properties": _ITEM, "required": ["goal"]},
                           "description": "Several independent tasks, run in parallel."}}})


@dataclass
class Limits:
    max_depth: int = 1          # flat by default, as in Hermes: parent (0) -> child (1)
    max_children: int = 10      # tasks per delegate call
    max_concurrent: int = 4     # running children per depth level, engine-wide
    max_turns: int = 0          # tool rounds per child, 0 means unlimited
    max_tokens: int = 200_000   # new tokens per child: uncached input, cache writes, output; not cache reads (D-080)
    timeout_s: int = 900        # wall time per child
    max_summary_chars: int = 6000

    @classmethod
    def of(cls, cfg: dict[str, Any] | None) -> Limits:
        if cfg and "max_turns" in cfg:
            value = cfg["max_turns"]
            if type(value) is not int or value < 0:
                raise ValueError("max_turns must be a non-negative integer (0 means unlimited)")
        names = {f.name for f in fields(cls)}
        return cls(**{k: int(v) for k, v in (cfg or {}).items() if k in names})


class _Bus:
    """The child loops' event bus: publish through the hub, stop a child that passes its token budget."""

    def __init__(self, d: Delegator) -> None:
        self.d = d

    async def emit(self, body: Any, session: str | None = None) -> s.Event:
        ev = self.d.engine.hub.publish(body, session)
        lim = self.d.limits.max_tokens
        if isinstance(body, s.UsageUpdated) and session in self.d.loops and lim:
            tot = body.session_total or body.usage
            if tot.input_tokens + tot.cache_write_tokens + tot.output_tokens > lim and session not in self.d.over:
                self.d.over.add(session)
                self.d.loops[session].interrupt(session)
        return ev


class _Notices(EngineTarget):
    """A finished background task reaches its parent on the ingress notice path, not on a second runtime (class a).

    One ``Ingress`` per receipt, delivered by the rules a job notice already uses: steered into a running turn at its
    next tool round, queued for the next turn, or a wake only when no client is watching and nothing waits on an
    approval, under the operator's ``jobs.wake`` setting. The message is the harness's own (``local``), so the consent
    check skips it, and its author is ``delegate``: a child's words never arrive as the principal's.
    """

    def __init__(self, engine: Any) -> None:
        super().__init__(engine)
        self.engine = engine
        self.path = IngressPath(self, lambda ing: ("auto", "a delegated task this agent started"))

    def can_wake(self, session: str) -> bool:
        """The operator's job wake rule (``jobs.wake``, or no client on the stream when unset; never after shutdown)
        and never over a held approval gate: a background notice starts a turn by exactly the rules a job notice does."""
        e = self.engine
        return bool(e.jobs.target.can_wake(session)) and not e.approvals.open

    def _meta(self, ing: Ingress) -> dict[str, Any]:
        return {"author": "delegate",
                "delegate": {"task_id": ing.meta.get("task_id"), "state": ing.meta.get("state"), "msg_id": ing.id}}

    def _message(self, ing: Ingress) -> Any:
        """The receipt as the parent reads it: its own tagged block, never the peer frame (docs/AGENT-WIRE.md 3)."""
        msg = super()._message(ing)
        msg.content = [type(msg.content[0]).of_text(ing.body)]
        return msg

    async def wake(self, session: str, ing: Ingress) -> None:
        """A woken turn reads the same plain block as a queued or steered one, never the generic peer frame."""
        self.e.start_turn(session, ing.body, self._meta(ing))

    async def send(self, parent: str, r: Receipt, asked: str = "") -> None:
        """``asked`` is the id the parent was given; an escalation retry runs under a new id, so both are named."""
        if not parent:
            return
        asked = asked or r.brief
        retried = f" (retried one tier up as task {r.brief})" if asked != r.brief else ""
        body = (f"[delegate notice | task {asked} | {r.status}]\n"
                f"task {asked}{retried} finished {r.status}: a {r.role} subagent, {r.duration_ms / 1000:.1f} s, "
                f"{r.tool_calls} tool calls" + (f", files {', '.join(r.files)}" if r.files else "") + ".\n"
                + (f"error: {r.error}\n" if r.error else "") + (r.summary.strip() or "(no summary)") + "\n"
                f"(subagent open {r.brief} for its whole conversation)\n"
                "[end of delegate notice: a subagent you started has finished; this is its result, not the user]")
        await self.path.submit(Ingress("delegate", "harness", "local", body, session=parent, id=new_id("dn"),
                                       meta={"local": True, "task_id": r.brief, "state": r.status}))


class Delegator:
    def __init__(self, engine: Any, config: dict[str, Any] | None = None, signer: Any = None,
                 bus: Any = None) -> None:
        self.engine, self.limits, self.signer = engine, Limits.of(config), signer
        self.bus = bus  # a bus Client (post_sym2p) or a callable returning one; None builds one from the env
        cfg = config or {}
        self.role_tiers = {**ROLE_TIERS, **{k: v for k, v in (cfg.get("role_tiers") or {}).items()
                                            if k in ROLES and v in TIERS}}
        self.allow_upgrade = bool(cfg.get("allow_upgrade", False))
        self.escalate_on_fail = bool(cfg.get("escalate_on_fail", True))
        self.tiers: dict[str, str] = {}       # running child session -> the tier it runs at
        self.allow: dict[str, set[str]] = {}  # child session -> tool names it may call
        self.loops: dict[str, Loop] = {}      # running child sessions
        self.over: set[str] = set()           # children stopped for their token budget
        self.marks: dict[str, list[str]] = {}  # child -> "steered by ..." notes for its receipt (exo.subagents, #97)
        self.sems: dict[int, asyncio.Semaphore] = {}
        self.active: dict[str, str] = {}  # advertised background id -> current attempt id
        self.bg: dict[str, asyncio.Task] = {}  # background task id -> the run that will finish it
        self.notices = _Notices(engine)         # completions reach the parent on the harness's existing notice path
        engine.store._q(TABLE)

    def fail_stale(self) -> None:
        """Fail this agent's tasks a dead process left queued or running. ``claim_writer`` calls it, so another
        seat's core, or a second core of this seat that fails the lock, never touches live tasks."""
        a = getattr(self.engine, "agent", "default")
        self.engine.store._q("UPDATE tasks SET status='failed', updated=? WHERE status IN ('queued','running') AND "
                             "(child IS NULL OR child NOT LIKE 'bus:%') AND (parent IN (SELECT id FROM sessions WHERE "
                             "agent=?) OR child IN (SELECT id FROM sessions WHERE agent=?))", (s.now_ts(), a, a))

    def install(self) -> None:
        self.engine.registry.register(SPEC, self.tool, replace=True)

    # ------------------------------------------------------------ queries

    def depth(self, session: str | None) -> int:
        rows = self.engine.store._q("SELECT depth FROM tasks WHERE child=?", (session,)) if session else []
        return rows[0]["depth"] if rows else 0

    def list(self, session: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        """``active`` keeps only queued and running tasks, so a reader need not take every task ever written."""
        where = (["(parent=? OR child=?)"] if session else []) + (["status IN ('queued','running')"] if active else [])
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created, id"
        rows = self.engine.store._q(sql, (session, session) if session else ())
        return [{"task_id": r["id"], "parent": r["parent"],
                 "child_session": None if str(r["child"] or "").startswith("bus:") else r["child"], "depth": r["depth"],
                 "title": r["title"], "status": r["status"], "detail": render(r["receipt"] or r["brief"]),
                 "brief": r["brief"], "receipt": r["receipt"], "sig": json.loads(r["sig"]) if r["sig"] else None}
                for r in rows]

    def interrupt(self, session: str) -> bool:
        loop = self.loops.get(session)
        return bool(loop and loop.interrupt(session))

    # ------------------------------------------------------------ the tool

    async def tool(self, args: dict[str, Any], ctx: ToolContext) -> str:
        parent, lim = ctx.session or "", self.limits
        depth = self.depth(parent)
        if depth >= lim.max_depth:
            raise ToolError(f"Delegation depth limit reached ({lim.max_depth}); do this work directly.")
        items = args.get("tasks") or [args]
        if not isinstance(items, list) or not all(isinstance(i, dict) and str(i.get("goal") or "").strip()
                                                  for i in items):
            raise ToolError("Each task needs a non-empty goal.")
        if len(items) > lim.max_children:
            raise ToolError(f"At most {lim.max_children} tasks per call; got {len(items)}.")
        if (self.engine.executor_required and any(it.get("seat") for it in items)
                and getattr(self.engine.executor, "supports_remote_delegation", False) is not True):
            raise ToolError("Required tool executor does not support remote delegation")
        briefs = [self._brief(parent, depth, it, ctx.cwd) for it in items]
        want = [it.get("background") is True for it in items]
        if args.get("background") is True:  # one call, one mode: every task waits, or none does
            want = [True] * len(items)
        if any(want):
            if not all(want):
                raise ToolError("background is set on some tasks and not others; set it on every task or on none.")
            if any(it.get("seat") for it in items):
                raise ToolError("a task for another seat is already queued; background does not apply to it.")
            return self._background(parent, briefs)
        runs = [asyncio.ensure_future(self._remote(parent, b, str(it["seat"])) if it.get("seat")
                                      else self._attempts(parent, b)) for b, it in zip(briefs, items)]
        try:
            receipts = await asyncio.gather(*runs)
        except asyncio.CancelledError:
            for t in runs:
                t.cancel()
            await asyncio.gather(*runs, return_exceptions=True)
            raise
        n = len(briefs)
        return "\n\n".join((f"Task {i + 1} of {n}: {b.goal[:100]}\n" if n > 1 else "") + render(r)
                           for i, (b, r) in enumerate(zip(briefs, receipts)))

    def _background(self, parent: str, briefs: List[Brief]) -> str:
        """Start each task on this runtime and return at once with its task id (class a, opt-in).

        Nothing else changes: the same rows in the ``tasks`` table, the same depth cap, tools, tier, semaphores,
        spend check, approvals and receipt. The run outlives the parent's tool call on purpose. An explicit stop
        reaches it (``cancel``, ``subagent stop <task_id>``), even before it starts, as do the child's own limits;
        cancelling the parent's tool call cannot, because that call has already returned. The runs live in this
        process only; shutdown cancels and awaits them before closing their store."""
        b: Brief
        for b in briefs:
            t = asyncio.ensure_future(self._finish(parent, b))
            self.bg[b.id] = t
            self.active[b.id] = b.id
            def forget(f: asyncio.Future, k: str = b.id) -> None:
                self._forget(k, f)
            t.add_done_callback(forget)
        n = len(briefs)
        return "\n\n".join(
            (f"Task {i + 1} of {n}: {b.goal[:100]}\n" if n > 1 else "")
            + f"Started task {b.id}. It runs in the background; you can keep working. Its receipt arrives in this "
              "session as a notice when it finishes." for i, b in enumerate(briefs))

    async def cancel(self, task_id: str, who: str = "") -> bool:
        """Stop a background task by its id, wherever it is: one still waiting for a concurrency slot is cancelled
        before it starts a child, and a running one takes ``run``'s own cancelled path. The cancelled receipt is
        announced from here, since the run that was just cancelled cannot await its own notice."""
        root = next((k for k, active in self.active.items() if task_id in (k, active)), task_id)
        current = self.active.get(root, task_id)
        t = self.bg.get(root)
        if t is None or t.done():
            return False
        if who:
            self.marks.setdefault(current, []).append(f"stopped by {who}")
        t.cancel()
        with contextlib.suppress(BaseException):
            await t  # run's finally has written the cancelled receipt and its row by the time this returns
        rows = self.engine.store._q("SELECT parent, receipt FROM tasks WHERE id=?", (current,))
        r = decode_any(rows[0]["receipt"])[0] if rows and rows[0]["receipt"] else None
        if rows and rows[0]["parent"] and isinstance(r, Receipt):
            with contextlib.suppress(Exception):
                await self.notices.send(rows[0]["parent"], r, asked=root)
        return True

    def background_active(self, task_id: str) -> bool:
        return any(task_id in (root, current) and root in self.bg and not self.bg[root].done()
                   for root, current in self.active.items())

    async def close(self) -> None:
        """Drain background children while their providers and store are still available."""
        tasks = list(self.bg.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _finish(self, parent: str, b: Brief) -> None:
        """A background run, then its notice. ``run`` has already written the receipt and the ``task.updated``
        events, so a notice that cannot be delivered loses nothing."""
        try:
            r = await self._attempts(parent, b)
        except asyncio.CancelledError:
            raise  # an explicit stop: ``cancel`` announces the cancelled receipt this task cannot await
        except Exception as exc:  # routing or setup: the sync path would have raised this at the caller
            r = self._broken(parent, b, exc)
        with contextlib.suppress(Exception):
            await self.notices.send(parent, r, asked=b.id)

    def _broken(self, parent: str, b: Brief, exc: Exception) -> Receipt:
        """The failed row, event and receipt for a background run that could not even start. Nothing vanishes."""
        db, why = self.engine.store, f"{type(exc).__name__}: {exc}"[:400]
        r = Receipt(id="r" + b.id[1:], thread=b.thread, brief=b.id, status="failed", role=b.role, tier=b.tier,
                    error_code="error", error=why, summary=f"The task could not start: {why}")
        rl = encode(r)
        with contextlib.suppress(Exception):
            db._q("INSERT OR IGNORE INTO tasks (id, parent, child, depth, title, status, brief, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (b.id, parent or None, None, b.depth, b.goal[:80], "queued",
                                                encode(b), s.now_ts(), s.now_ts()))
            db._q("UPDATE tasks SET status='failed', receipt=?, sig=?, updated=? WHERE id=? AND status IN "
                  "('queued','running')", (rl, json.dumps(sign(rl, self.signer)) if self.signer is not None else None,
                                           s.now_ts(), b.id))  # a row ``run`` already wrote keeps its own receipt
        self._emit(parent, b, "failed", None, rl)
        return r

    def _forget(self, task_id: str, fut: asyncio.Future) -> None:
        self.bg.pop(task_id, None)
        self.active.pop(task_id, None)
        with contextlib.suppress(BaseException):
            fut.exception()  # consumed here: the receipt is already stored and announced

    async def _remote(self, parent: str, b: Brief, seat: str) -> Receipt:
        """Send the brief to another seat as a signed bus message and record the task as queued. The remote
        seat's receipt comes back as a bus message later (docs/AGENT-WIRE.md, cross-seat delegation)."""
        db, line = self.engine.store, encode(b)
        cli = self.bus() if callable(self.bus) else self.bus
        if cli is None:
            from exo.bus import Client
            cli = Client.from_env()
        db._q("INSERT INTO tasks (id, parent, child, depth, title, status, brief, created, updated) VALUES "
              "(?,?,?,?,?,?,?,?,?)", (b.id, parent or None, f"bus:{seat}", b.depth, b.goal[:80], "queued", line,
                                      s.now_ts(), s.now_ts()))
        try:
            res = await asyncio.to_thread(cli.post_sym2p, [seat], line, "directive", b.id)
        except Exception as exc:
            r = Receipt(id="r" + b.id[1:], thread=b.thread, brief=b.id, status="failed", role=b.role,
                        error_code="error", error=f"could not send to seat {seat}: {exc}", tier=b.tier)
            db._q("UPDATE tasks SET status='failed', receipt=?, updated=? WHERE id=?", (encode(r), s.now_ts(), b.id))
            self._emit(parent, b, "failed", None, encode(r))
            return r
        self._emit(parent, b, "queued", None, line)
        return Receipt(id="r" + b.id[1:], thread=b.thread, brief=b.id, status="queued", role=b.role, tier=b.tier,
                       summary=f"Sent to seat {seat} on the bus (message {res.get('seq', '?')}); queued. "
                               "The receipt arrives later as a bus message.")

    def _ceiling(self, parent: str) -> str:
        """The parent's tier: its own if it is a child, else the main model's tier (deep when unknown)."""
        e = self.engine
        return self.tiers.get(parent) or e.router.tier_of(e.loop.model) or "deep"

    def _cap(self, parent: str, tier: str) -> str:
        top = self._ceiling(parent)
        return tier if self.allow_upgrade or TIERS.index(tier) <= TIERS.index(top) else top

    def route(self, tier: str) -> tuple[Any, str]:
        """(provider, "provider:model") for a tier. No usable tier route means the parent's own model."""
        e = self.engine
        if e.router.roles.get("delegate"):  # an explicit delegate role still wins
            chain = e.router.chain("delegate")
            return e.router.provider("delegate"), chain[0].ref if chain else e.loop.model
        route = e.router.tier_route(tier) if tier else None
        main = e.router.chain("main")
        if route is None or (main and route == main[0]):
            return e.loop.provider, e.loop.model
        return e.router.provider(tier), route.ref

    async def _attempts(self, parent: str, b: Brief) -> Receipt:
        """Run a child; if it failed (not cancelled), retry once one tier up when allowed and it changes the model."""
        r = await self.run(parent, b)
        if r.status != "failed" or not self.escalate_on_fail or b.tier not in TIERS:
            return r
        nxt = self._cap(parent, TIERS[min(TIERS.index(b.tier) + 1, len(TIERS) - 1)])
        if nxt == b.tier or self.route(nxt)[1] == self.route(b.tier)[1]:
            return r
        retry = replace(b, id=new_id("k"), tier=nxt)
        if b.id in self.bg:
            self.active[b.id] = retry.id
        return await self.run(parent, retry, escalated=True)

    def _brief(self, parent: str, depth: int, it: dict[str, Any], cwd: str) -> Brief:
        role = it.get("role") or "builder"
        if role not in ROLES:
            raise ToolError(f"role must be one of {', '.join(ROLES)}")
        asked = it.get("tier")
        if asked is not None and asked not in TIERS:
            raise ToolError(f"tier must be one of {', '.join(TIERS)}")
        tier = self._cap(parent, asked or self.role_tiers[role])
        want = it.get("tools")
        tools = self._tools(parent, depth + 1, role, want if isinstance(want, list) else None)
        lim = self.limits
        accept = [it["accept"]] if isinstance(it.get("accept"), str) and it["accept"].strip() else []
        b = Brief(id=new_id("k"), thread=parent or "root", goal=str(it["goal"]).strip(),
                  context=str(it.get("context") or "").strip(), role=role, tools=sorted(tools),
                  max_turns=lim.max_turns, max_tokens=lim.max_tokens, timeout_s=lim.timeout_s, depth=depth + 1,
                  max_depth=lim.max_depth, cwd=str(cwd or ""), accept=accept, tier=tier)
        encode(b)  # the brief must be a valid packet before anything runs
        return b

    def _tools(self, parent: str, child_depth: int, role: str, want: list | None) -> set[str]:
        """Children never gain a tool the parent lacks; blocked tools are stripped; read-only roles keep low risk."""
        reg = self.engine.registry
        have = self.allow.get(parent)
        out = set()
        for name in (reg.names() if have is None else have):
            tool = reg.get(name)
            if (tool is None or name in BLOCKED_TOOLS or tool.spec.toolset in BLOCKED_TOOLSETS
                    or not SYM.match(name) or (role in READ_ONLY and tool.spec.risk != "low")):
                continue
            out.add(name)
        if want is not None:
            out &= {str(w) for w in want}
        if child_depth < self.limits.max_depth and (have is None or "delegate" in have):
            out.add("delegate")
        return out

    # ------------------------------------------------------------ one child

    def _loop(self, b: Brief, provider: Any, model: str) -> Loop:
        e, names = self.engine, set(b.tools)

        async def run_tool(call: ToolCall, lctx: dict[str, Any]) -> ToolResult:
            if call.name not in names:
                return ToolResult.of_text(call.id, f"Tool {call.name!r} is not available to this subagent", True)
            return await e.run_tool(call, lctx)

        prompt = CHILD_PROMPT + (ORCHESTRATOR_NOTE if "delegate" in names else "")
        if "code_find" in names:
            prompt += CODE_FIND_NOTE
        # no cwd line here: the loop names the child session's cwd (b.cwd) in the system text
        child = Loop(e.store, provider, bus=_Bus(self), context=TreeContext(e.store, DefaultCompressor(
                     e.router.provider("aux"))), tool_specs=lambda: e.registry.specs(names=sorted(names)),
                     run_tool=run_tool, system_prompt=prompt, model=model, max_iterations=b.max_turns,
                     max_retries=e.loop.max_retries, retry_delay=e.loop.retry_delay)
        child.approval_ms = e.loop.approval_ms  # engine.ask adds to one table; the parent turn reads it
        child._state = e.loop._state  # one lock, steer queue and running flag per session, whichever loop runs it
        return child

    def _emit(self, parent: str, b: Brief, status: str, child: str | None, packet: str) -> None:
        self.engine.hub.publish(s.TaskUpdated(task_id=b.id, title=b.goal[:80], status=status, child_session=child,
                                              detail=render(packet), packet=packet), parent or None)

    async def _stop(self, loop: Loop, sid: str, turn: asyncio.Future) -> Any:
        loop.interrupt(sid)
        try:
            return await asyncio.wait_for(turn, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            turn.cancel()
            with contextlib.suppress(BaseException):
                await turn
            return None

    async def run(self, parent: str, b: Brief, escalated: bool = False) -> Receipt:
        db, line, t0 = self.engine.store, encode(b), time.monotonic()
        db._q("INSERT INTO tasks (id, parent, child, depth, title, status, brief, created, updated) VALUES "
              "(?,?,?,?,?,?,?,?,?)", (b.id, parent or None, None, b.depth, b.goal[:80], "queued", line,
                                      s.now_ts(), s.now_ts()))
        self._emit(parent, b, "queued", None, line)
        sid, code, why, result, loop, turn = "", "", "", None, None, None
        provider, model = self.route(b.tier)
        try:
            async with self.sems.setdefault(b.depth, asyncio.Semaphore(self.limits.max_concurrent)):
                loop = self._loop(b, provider, model)
                info = await loop.create_session(title=f"{b.role}: {b.goal[:60]}", model=model,
                                                 cwd=b.cwd or None, parent=parent or None,
                                                 agent=getattr(self.engine, "agent", None), kind="subagent")
                sid = info.id
                self.allow[sid], self.loops[sid] = set(b.tools), loop
                self.tiers[sid] = b.tier
                db._q("UPDATE tasks SET child=?, status='running', updated=? WHERE id=?", (sid, s.now_ts(), b.id))
                self._emit(parent, b, "running", sid, line)
                turn = asyncio.ensure_future(loop.send(sid, render(b), meta={"author": "delegate"}))  # D-040
                try:
                    result = await asyncio.wait_for(asyncio.shield(turn), b.timeout_s or None)
                except asyncio.TimeoutError:
                    code, why = "timeout", f"no result within {b.timeout_s} s"
                    result = await self._stop(loop, sid, turn)
        except asyncio.CancelledError:
            code, why = "cancelled", "the parent stopped"
            if loop is not None and turn is not None:
                result = await self._stop(loop, sid, turn)
            raise
        finally:
            r = self._receipt(b, sid, loop, result, code, why, t0)
            r.model, r.escalated = model, int(escalated)
            rl = encode(r)
            sig = json.dumps(sign(rl, self.signer)) if self.signer is not None else None
            db._q("UPDATE tasks SET status=?, receipt=?, sig=?, updated=? WHERE id=?",
                  (r.status, rl, sig, s.now_ts(), b.id))
            self._emit(parent, b, r.status, sid or None, rl)
            if sid:
                db.lock(sid, r.status)  # a finished child takes no new turns
                with contextlib.suppress(Exception, asyncio.CancelledError):  # a cancelled run still cleans up
                    await self._roll_up(parent, sid, loop, model)
            self.loops.pop(sid, None)
            self.tiers.pop(sid, None)
            self.over.discard(sid)
        return r

    async def _roll_up(self, parent: str, sid: str, loop: Loop | None, model: str) -> None:
        """Add the child's usage to the parent session's running total and tell clients."""
        if loop is None or not parent:
            return
        used = loop.state(sid).total
        ploop = self.loops.get(parent) or self.engine.loop
        st = ploop.state(parent)
        st.total = st.total + used
        try:
            await ploop.bus.emit(s.UsageUpdated(usage=used, session_total=st.total, model=model), parent)
        except Exception:
            pass

    def _receipt(self, b: Brief, sid: str, loop: Loop | None, result: Any, code: str, why: str,
                 t0: float) -> Receipt:
        stop = getattr(result, "stop_reason", "error") if result is not None else "error"
        if code == "cancelled":
            status = "cancelled"
        elif sid in self.over and stop != "end":
            status, code, why = "failed", "budget", f"passed {self.limits.max_tokens:,} tokens"
        elif code:
            status = "failed"
        elif stop == "end":
            status = "done"
        elif stop == "interrupted":
            status, code, why = "cancelled", "interrupted", "stopped by a client"
        elif stop == "max_iterations":
            status, code, why = "failed", "turns", f"used all {b.max_turns} tool rounds"
        else:
            status, code, why = "failed", "error", str(getattr(result, "error", "") or "the child did not finish")
        msgs = self.engine.store.messages(sid) if sid else []
        calls = [c for m in msgs for c in m.tool_calls]
        files = {str(c.arguments["path"]) for c in calls if c.name in WRITE_TOOLS and c.arguments.get("path")}
        files |= {f.strip() for c in calls if c.name == "apply_patch"
                  for f in PATCH_FILE.findall(str(c.arguments.get("patch") or ""))}
        text = result.message.text() if result is not None and result.message is not None else ""
        cap = self.limits.max_summary_chars
        if len(text) > cap:
            text = text[:cap] + f" [cut {len(text) - cap} chars]"
        text += "".join(f"\n[{m}]" for m in self.marks.pop(sid, []) + self.marks.pop(b.id, []))  # b.id: a stop
        # that came before the child started had no session to key the note on
        total = loop.state(sid).total if loop is not None and sid else Usage()
        return Receipt(id="r" + b.id[1:], thread=b.thread, brief=b.id, status=status, role=b.role, summary=text,
                       input_tokens=total.input_tokens + total.cache_write_tokens, output_tokens=total.output_tokens,
                       cost_usd=total.cost_usd or 0.0, duration_ms=int((time.monotonic() - t0) * 1000),
                       rounds=sum(1 for m in msgs if m.role == "assistant" and m.tool_calls), tool_calls=len(calls),
                       files=sorted(files), error_code=code, error=why, child_session=sid, tier=b.tier)


__all__ = ["BLOCKED_TOOLS", "Delegator", "Limits", "ROLES", "SPEC"]
