"""Human group commands: the command.run name "group" family (docs/SWARM-COMMANDS.md).

These handlers are reached only through the trusted protocol dispatch. Every
subcommand, including read-only observation, calls
``principal.require_principal`` first: a local or channel caller cannot
list, inspect, send into or observe a group. The group owner is always this
engine's seat (``eng.agent``, D-028), never a caller-supplied name, so one seat
can never read or mutate another seat's groups.

This module adds no execution and no creation: there is no create/start/
pause/stop here because no authorized engine factory exists yet. Group
start/control stays unimplemented until a trusted host can supply per-profile
engines (docs/SWARM-ENGINE-ADAPTER.md).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from exo import principal
from exo.core.group_messages import GroupMessages
from exo.core.groups import GroupStore
from exo.server.engine import CommandFailed

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime

_LIMIT = 100  # matches the GroupMessages default page; observe/history allow up to 1000


def _groups(eng: CoreRuntime) -> GroupStore:
    """The store on this engine's own sessions database; created lazily, exposed only here."""
    store = getattr(eng, "_group_store", None)
    if store is None:
        store = eng._group_store = GroupStore(eng.store)
    return store


def _messages(eng: CoreRuntime) -> GroupMessages:
    msgs = getattr(eng, "_group_messages", None)
    if msgs is None:
        msgs = eng._group_messages = GroupMessages(_groups(eng))
    return msgs


def _args(raw: str) -> dict[str, Any]:
    """The single JSON-object argument of a group subcommand. Unknown keys are rejected."""
    raw = raw.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise CommandFailed("bad_request", "usage: /group <op> '<json object>'")
    if not isinstance(data, dict):
        raise CommandFailed("bad_request", "the group command takes one JSON object")
    return data


def _take(args: dict[str, Any], *names: str) -> list[Any]:
    unknown = set(args) - set(names)
    if unknown:
        raise CommandFailed("bad_request", f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = [n for n in names if n not in args]
    if missing:
        raise CommandFailed("bad_request", f"missing field(s): {', '.join(missing)}")
    return [args[n] for n in names]


def _bad(message: str) -> CommandFailed:
    return CommandFailed("bad_request", message)


def _group_id(args: dict[str, Any]) -> str:
    """A group id is a non-empty string, validated before any store call."""
    group_id = args["id"]
    if not isinstance(group_id, str) or not group_id.strip():
        raise _bad("id must be a non-empty string")
    return group_id


def _bounded(value: Any, name: str, lo: int, hi: int) -> int:
    """An integer field; bool is not accepted as an integer."""
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise _bad(f"{name} must be an integer between {lo} and {hi}")
    return value


def _describe(group) -> dict[str, Any]:
    return {"id": group.id, "owner": group.owner, "mode": group.mode, "conductor": group.conductor,
            "members": [{"profile": m.profile, "session": m.session} for m in group.members],
            "created": group.created}


def _show(messages: list) -> dict[str, Any]:
    return {"messages": [{"seq": m.seq, "id": m.id, "sender": m.sender, "sender_kind": m.sender_kind,
                          "audience": list(m.audience), "text": m.text, "created": m.created}
                         for m in messages],
            "next_after": messages[-1].seq if messages else 0}


def _op_list(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    _take(args)
    return {"groups": [_describe(g) for g in _groups(eng).listing(owner=eng.agent)]}


def _op_inspect(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    _take(args, "id")
    group = _groups(eng).get(_group_id(args), owner=eng.agent)
    if group is None:
        raise _bad("no such group")
    return _describe(group)


def _op_send(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"id", "text", "audience"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    if "id" not in args or "text" not in args:
        raise _bad("missing field(s): " + ", ".join(n for n in ("id", "text") if n not in args))
    group_id, text, audience = _group_id(args), args["text"], args.get("audience")
    group = _groups(eng).get(group_id, owner=eng.agent)
    if group is None:
        raise _bad("no such group")
    try:
        if audience is None:
            audience = [m.profile for m in group.members]
        msg = _messages(eng).send_human(group_id, owner=eng.agent, audience=audience, text=text)
    except ValueError as e:
        raise _bad(str(e))
    return _show([msg])["messages"][0]


def _op_observe(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"id", "member", "after", "limit"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    if "id" not in args:
        raise _bad("missing field(s): id")
    group_id = _group_id(args)
    if _groups(eng).get(group_id, owner=eng.agent) is None:
        raise _bad("no such group")
    member = args.get("member")
    if member is not None and (not isinstance(member, str) or not member.strip()):
        raise _bad("member must be a non-empty string")
    after = _bounded(args["after"], "after", 0, 2**63 - 1) if "after" in args else 0
    limit = _bounded(args["limit"], "limit", 1, 1000) if "limit" in args else _LIMIT
    try:
        if member is None:
            return _show(_messages(eng).observe(group_id, owner=eng.agent, after=after, limit=limit))
        return _show(_messages(eng).history(group_id, owner=eng.agent, member=member, after=after, limit=limit))
    except ValueError as e:
        raise _bad(str(e))


_OPS = {"list": _op_list, "inspect": _op_inspect,
        "send": _op_send, "observe": _op_observe}


async def command(eng: CoreRuntime, b) -> Any:
    """The command.run name "group". The subcommand name is ``b.args``'s first word; the rest is one JSON object."""
    principal.require_principal("manage swarm groups")
    op, _, rest = b.args.partition(" ")
    fn = _OPS.get(op.strip())
    if fn is None:
        raise CommandFailed("bad_request",
                            "usage: /group <list|inspect|send|observe> '<json>' "
                            "(create/start/control is not implemented)")
    return fn(eng, _args(rest))
