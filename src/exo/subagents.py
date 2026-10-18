"""Inspect, steer and stop a running subagent (issue #97). Kept outside the counted core on purpose.

The parent's ``delegate`` tool starts a child session and the ``Delegator`` holds its live loop. This module
reads that state and calls ``Loop.send(mode="steer")`` and ``Loop.interrupt`` on it. It is reached by the built-in
``command.run`` name ``subagent`` (no protocol command or event was added) and, for a parent session's own tool,
by calling ``steer`` and ``stop`` with ``by=<parent session id>``.

Who may act (D-052): the principal, or the parent session of that task. Nobody else, including local callers.
Whose words land (D-040): a principal caller typed it, so the message is the principal's; the parent session
speaks as ``delegate``. A finished child takes no new turns, so steering or stopping it fails with ``conflict``.
The note ("steered by principal") goes to the child's receipt through ``Delegator.marks``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from exo import provenance
from exo.principal import current_caller, demoted, ingress_author, is_principal
from exo.protocol import schema as s
from exo.server.engine import CommandFailed

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine

USAGE = "usage: subagent list | open <task> | send <task> <text> | stop <task>"


def find(eng: CoreEngine, ref: str) -> dict[str, Any]:
    """The task row for a task id, a unique id prefix or the child's session id."""
    ref, rows = ref.strip(), [t for t in eng.agents.list() if t["child_session"] or t["status"] in ("queued", "running")]
    if not ref:
        raise CommandFailed("bad_request", USAGE)
    hit = [t for t in rows if ref in (t["task_id"], t["child_session"])] or [
        t for t in rows if t["task_id"].startswith(ref)]
    if len(hit) != 1:
        raise CommandFailed("not_found" if not hit else "bad_request",
                            f"no subagent task {ref!r}" if not hit else f"{ref!r} matches {len(hit)} tasks; be longer")
    return hit[0]


def live(eng: CoreEngine, row: dict[str, Any]) -> bool:
    return row["status"] == "running" and row["child_session"] in eng.agents.loops


def _act(eng: CoreEngine, ref: str, by: str | None, verb: str) -> tuple[dict[str, Any], str, str]:
    """(row, author, who) once the caller may act on a running child; otherwise the right error."""
    row = find(eng, ref)
    if by is not None:
        if by != row["parent"]:
            raise CommandFailed("forbidden", f"only the parent session can {verb} its subagent")
        author, who = provenance.DELEGATE, "parent"
    elif is_principal(current_caller()):
        author, who = ingress_author(), "principal"
    else:
        raise CommandFailed("forbidden", f"only the principal or the parent session can {verb} a subagent"
                            + demoted())
    if not live(eng, row):
        raise CommandFailed("conflict", f"task {row['task_id']} is finished ({row['status']}); nothing to {verb}")
    return row, author, who


def _announce(eng: CoreEngine, row: dict[str, Any], what: str) -> None:
    eng.hub.publish(s.TaskUpdated(task_id=row["task_id"], title=row["title"], status=row["status"],
                                  child_session=row["child_session"], detail=what, packet=row["brief"]),
                    row["parent"])


async def steer(eng: CoreEngine, ref: str, text: str, by: str | None = None) -> dict[str, Any]:
    """Put ``text`` into a running child at its next tool round."""
    if not text.strip():
        raise CommandFailed("bad_request", "nothing to send; usage: subagent send <task> <text>")
    row, author, who = _act(eng, ref, by, "steer")
    child = row["child_session"]
    await eng.agents.loops[child].send(child, text.strip(), mode="steer", meta={"author": author})
    eng.agents.marks.setdefault(child, []).append(f"steered by {who}")
    _announce(eng, row, f"steered by {who}")
    return {"task_id": row["task_id"], "child_session": child, "steered": True, "author": author}


async def stop(eng: CoreEngine, ref: str, by: str | None = None) -> dict[str, Any]:
    """Interrupt a running child; its receipt comes back as cancelled and says who stopped it."""
    row, _, who = _act(eng, ref, by, "stop")
    child = row["child_session"]
    eng.agents.marks.setdefault(child, []).append(f"stopped by {who}")
    if not eng.agents.interrupt(child):
        eng.agents.marks.pop(child, None)
        raise CommandFailed("conflict", f"task {row['task_id']} is between turns; nothing to stop")
    _announce(eng, row, f"stopped by {who}")
    return {"task_id": row["task_id"], "child_session": child, "stopped": True}


def view(eng: CoreEngine, ref: str, tail: int = 20) -> dict[str, Any]:
    """Everything a client needs to open a child: its task row, its session and the tail of its conversation."""
    row = find(eng, ref)
    child = row["child_session"]
    info = eng.store.get_session(child) if child else None
    msgs = eng.store.messages(child) if child else []
    return {"task_id": row["task_id"], "child_session": child, "parent": row["parent"], "title": row["title"],
            "status": row["status"], "steerable": live(eng, row), "session": s.to_dict(info) if info else None,
            "messages": [{"role": m.role, "author": provenance.author_of(m), "text": m.text()[:2000]}
                         for m in msgs[-tail:]]}


def listing(eng: CoreEngine, session: str | None = None) -> dict[str, Any]:
    rows = [{k: t[k] for k in ("task_id", "parent", "child_session", "status", "title")} | {"live": live(eng, t)}
            for t in eng.agents.list(session)]
    return {"tasks": rows}


async def command(eng: CoreEngine, b: s.CommandRun) -> Any:
    """``command.run subagent <verb> ...``: the one built-in behind /tasks, /open, /steer and /stop <task>."""
    verb, _, rest = b.args.strip().partition(" ")
    ref, _, text = rest.strip().partition(" ")
    if verb == "list":
        return listing(eng, b.session)
    if verb == "open":
        return view(eng, ref)
    if verb == "send":
        return await steer(eng, ref, text)
    if verb == "stop":
        return await stop(eng, ref)
    raise CommandFailed("bad_request", USAGE)
