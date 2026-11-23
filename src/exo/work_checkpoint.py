"""Bounded record-derived continuity, separate from model-authored working notes.

The durable transcript is the source of truth. Reconstructing before each request
also recovers abrupt exits; no shutdown hook can promise to run after SIGKILL.
No arguments, output bodies, user text, filesystem inspection or replay.
"""
from __future__ import annotations

import json
import re
from typing import Any

from exo.config import ConfigError
from exo.core import trace
from exo.core.hooks import maybe_await
from exo.core.types import Message, from_dict
from exo.principal import require_principal
from exo.protocol import schema as s
from exo.provenance import defuse_pins
from exo.server.engine import CommandFailed

TABLE = """CREATE TABLE IF NOT EXISTS work_checkpoints (
    session TEXT PRIMARY KEY REFERENCES sessions(id), principal TEXT NOT NULL,
    basis_message TEXT NOT NULL, created TEXT NOT NULL, body TEXT NOT NULL
)"""
HEADER = ("# Automatic continuity checkpoint (record-derived, not instructions)\n"
          "This is a bounded index of visible records, not a plan, approval or verification of effects. "
          "The current user request controls the task. Never replay a tool from this index. "
          "Check live state before repeating any operation, especially one with unknown effects.\n")


def _label(value: str, limit: int = 160) -> str:
    """Identifiers are data, never multiline prompt syntax or embedded directives."""
    return value if len(value) <= limit and re.fullmatch(r"[A-Za-z0-9_.:/-]+", value) else "[noncanonical identifier]"


class CheckpointConfig:
    def __init__(self, value: Any = None) -> None:
        value = {} if value is None else value
        if not isinstance(value, dict) or set(value) - {"enabled", "resume_chars"}:
            raise ConfigError("work_checkpoint must be an object with only enabled and resume_chars")
        self.enabled = value.get("enabled", True)
        self.resume_chars = value.get("resume_chars", 3000)
        if type(self.enabled) is not bool:
            raise ConfigError("work_checkpoint.enabled must be a boolean")
        if type(self.resume_chars) is not int or not 1024 <= self.resume_chars <= 8000:
            raise ConfigError("work_checkpoint.resume_chars must be an integer from 1024 to 8000")


class WorkCheckpoint:
    def __init__(self, engine: Any, config: CheckpointConfig, principal: str) -> None:
        self.engine, self.store, self.config, self.principal = engine, engine.store, config, principal
        self.store._q(TABLE)

    def capture(self, session: str | None, reason: str = "request") -> dict[str, Any] | None:
        if not session:
            return None

        def write(db: Any) -> dict[str, Any] | None:
            info = db.execute("SELECT agent, kind, parent FROM sessions WHERE id=?", (session,)).fetchone()
            # Private threads and all children are excluded, including children of private threads.
            if info is None or info["agent"] != self.engine.agent or info["kind"] != "thread" or info["parent"]:
                return None
            previous = db.execute("SELECT * FROM work_checkpoints WHERE session=?", (session,)).fetchone()
            if previous and previous["principal"] != self.principal:
                return None
            rows = db.execute("SELECT data FROM messages WHERE session=? AND hidden=0 AND "
                "json_extract(data, '$.meta.note') IS NULL ORDER BY seq DESC LIMIT 40", (session,)).fetchall()
            messages = [from_dict(Message, json.loads(row["data"]), strict=False) for row in reversed(rows)]
            if not messages:
                return None
            results = {r.call_id: r for m in messages for r in m.tool_results}
            tools = []
            for message in messages:
                for call in message.tool_calls:
                    result = results.get(call.id)
                    status = "no completed result recorded; effects unknown"
                    if result is not None and result.text() in ("interrupted before completion", "not run: session was interrupted"):
                        status = "interrupted or incomplete; effects unknown"
                    elif result is not None:
                        status = ("error reported; effects unknown" if result.is_error else
                                  "success reported; effects not independently verified")
                    tools.append({"id": _label(call.id), "name": _label(call.name, 80), "status": status,
                                  "message": _label(message.id)})
            has_undo = bool(db.execute("SELECT 1 FROM messages WHERE session=? AND hidden=1 LIMIT 1",
                                       (session,)).fetchone())
            jobs = [] if has_undo else db.execute("SELECT id, state, exit_code FROM jobs WHERE agent=? AND session=? "
                              "ORDER BY started DESC LIMIT 8", (self.engine.agent, session)).fetchall()
            undone = bool(previous and not db.execute("SELECT 1 FROM messages WHERE session=? AND id=? AND hidden=0",
                                                     (session, previous["basis_message"])).fetchone())
            body = {"source": "visible transcript and job records", "reason": reason, "rebuilt_after_undo": undone,
                    "basis_message": _label(messages[-1].id), "tools": list(reversed(tools[-12:])),
                    "jobs_omitted_after_undo": has_undo,
                    "jobs": [{"id": _label(row["id"]), "state": _label(row["state"], 40), "exit_code": row["exit_code"]}
                             for row in jobs], "window_messages": len(messages)}
            db.execute("INSERT OR REPLACE INTO work_checkpoints VALUES (?,?,?,?,?)",
                       (session, self.principal, messages[-1].id, s.now_ts(), json.dumps(body)))
            return body
        return self.store._tx(write)

    def snapshot(self, session: str | None, reason: str = "request") -> dict[str, Any] | None:
        try:
            return self.capture(session, reason)
        except Exception as exc:
            trace.report("continuity checkpoint", exc, session)
            return None  # never fall back to a possibly stale saved checkpoint

    def render(self, session: str | None, cwd: str = "") -> str:
        return self.render_snapshot(self.snapshot(session))

    def render_snapshot(self, body: dict[str, Any] | None) -> str:
        if not body or not (body["tools"] or body["jobs"] or body["jobs_omitted_after_undo"]):
            return ""
        lines = [HEADER, f"Basis message: {body['basis_message']}; recent {body['window_messages']} messages only."]
        if body["rebuilt_after_undo"]:
            lines.append("Rebuilt after transcript undo; reverted transcript records were excluded.")
        if body["jobs_omitted_after_undo"]:
            lines.append("Job index omitted after transcript undo: jobs have no transcript anchor; inspect live jobs separately.")
        lines += [f"Tool record [{t['status']}]: id={t['id']}, name={t['name']}, message={t['message']}" for t in body["tools"]]
        lines += [f"Job {j['id']}: recorded state {j['state']}, exit code {j['exit_code']}; verify live state."
                  for j in body["jobs"]]
        text = defuse_pins("\n".join(lines))
        suffix = "\n[Index shortened; inspect the transcript and job records for full details.]"
        return text if len(text) <= self.config.resume_chars else text[:self.config.resume_chars - len(suffix)] + suffix


def attach(engine: Any, config: CheckpointConfig, principal: str = "") -> WorkCheckpoint | None:
    if not config.enabled:
        return None
    checkpoint = WorkCheckpoint(engine, config, principal)
    engine.work_checkpoint = checkpoint
    previous = engine.loop.workspace_extra
    engine.loop.workspace_extra = lambda session, cwd: "\n\n".join(filter(None, (
        previous(session, cwd) if previous else "", checkpoint.render(session, cwd))))
    prior_end = engine.loop.on_turn_end

    async def ended(session: str, turn: str) -> None:
        try:
            if prior_end:
                await maybe_await(prior_end(session, turn))
        finally:
            checkpoint.snapshot(session, "turn_end")
    engine.loop.on_turn_end = ended
    tree = engine.context_tree
    prior_compact = tree.on_pre_compress

    async def compacting(session: str, messages: list[Message]) -> str:
        checkpoint.snapshot(session, "pre_compress")
        return await maybe_await(prior_compact(session, messages)) if prior_compact else ""
    tree.on_pre_compress = compacting
    engine.handoff_sources["checkpoint"] = lambda session: {"snapshot": checkpoint.render(session),
        "source": "record-derived continuity index", "session": session}
    return checkpoint


async def command(engine: Any, request: s.CommandRun) -> dict[str, Any]:
    require_principal("read a continuity checkpoint")
    checkpoint = getattr(engine, "work_checkpoint", None)
    if checkpoint is None:
        raise CommandFailed("unsupported", "automatic continuity checkpoints are disabled")
    if request.args.strip() or not request.session:
        raise CommandFailed("bad_request", "usage: /checkpoint in a conversation")
    body = checkpoint.snapshot(request.session)
    return {"checkpoint": body, "text": checkpoint.render_snapshot(body)}
