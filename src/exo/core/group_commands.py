"""Human group commands: the command.run name "group" family (docs/SWARM-COMMANDS.md).

These handlers are reached only through the trusted protocol dispatch. Every
subcommand, including read-only observation, calls
``principal.require_principal`` first: a local or channel caller cannot
list, inspect, send into or observe a group. The group owner is always this
engine's seat (``eng.agent``, D-028), never a caller-supplied name, so one seat
can never read or mutate another seat's groups.

The lifecycle operations (create/start/status/pause/resume/stop) act only
through a trusted ``GroupHost`` explicitly injected as ``eng._group_host``
by the wiring that owns this engine. This module never constructs the host,
engines or profiles and never reads configuration to build one. With no
injected host, or a host whose owner differs from ``eng.agent``, every
lifecycle operation fails closed (``forbidden``): the injected host must be
scoped to this seat or the command cannot use it.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from exo import principal
from exo.core.group_host import GroupHost, HostError
from exo.core.group_knowledge import GroupKnowledge
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


def _knowledge(eng: CoreRuntime) -> GroupKnowledge:
    """Owner knowledge store on this engine's own sessions database; created lazily, exposed only here."""
    knowledge = getattr(eng, "_group_knowledge", None)
    if knowledge is None:
        knowledge = eng._group_knowledge = GroupKnowledge(_groups(eng))
    return knowledge


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


def _host(eng: CoreRuntime) -> GroupHost:
    """The explicitly injected, owner-matched host; fail closed otherwise.

    The host is never constructed here. An injected host owned by another
    seat is rejected, not trusted: authorization must come from wiring that
    scoped the host to this engine's seat, not from an object that happens
    to be reachable.
    """
    host = getattr(eng, "_group_host", None)
    if host is None:
        raise CommandFailed("forbidden",
                            "no trusted group host is injected on this engine; "
                            "group lifecycle commands are unavailable")
    owner = getattr(host, "owner", None)
    if not isinstance(owner, str) or owner != eng.agent:
        raise CommandFailed("forbidden", "injected group host is not owned by this seat")
    return host


def _control(args: dict[str, Any]) -> tuple[str, str | None]:
    """Validated id/member for pause, resume and stop. member is optional."""
    unknown = set(args) - {"id", "member"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    group_id = _group_id(args)
    member = args.get("member")
    if member is not None and (not isinstance(member, str) or not member.strip()):
        raise _bad("member must be a non-empty string")
    return group_id, member


def _op_create(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"mode", "members", "conductor"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = [n for n in ("mode", "members") if n not in args]
    if missing:
        raise _bad("missing field(s): " + ", ".join(missing))
    mode, members, conductor = args["mode"], args["members"], args.get("conductor")
    if not isinstance(mode, str) or mode not in ("socratic", "conductor"):
        raise _bad('mode must be "socratic" or "conductor"')
    if (isinstance(members, (str, bytes)) or not isinstance(members, list) or not members
            or any(not isinstance(m, str) or not m.strip() for m in members)):
        raise _bad("members must be a non-empty list of profile strings")
    if conductor is not None and (not isinstance(conductor, str) or not conductor.strip()):
        raise _bad("conductor must be a non-empty string")
    try:
        group = _host(eng).create(mode=mode, members=members, conductor=conductor)
    except HostError as e:
        raise _bad(str(e))
    return _describe(group)


def _audiences(args: dict[str, Any]) -> dict[str, list[str]]:
    """The explicit profile->audience routing map; never inferred or defaulted."""
    raw = args["audiences"]
    if not isinstance(raw, dict) or not raw:
        raise _bad("audiences must be a non-empty object mapping each member profile to its audience")
    audiences: dict[str, list[str]] = {}
    for profile, audience in raw.items():
        if not isinstance(profile, str) or not profile.strip():
            raise _bad("audiences keys must be non-empty profile strings")
        if (isinstance(audience, (str, bytes)) or not isinstance(audience, list)
                or any(not isinstance(a, str) or not a.strip() for a in audience)):
            raise _bad(f"audiences[{profile!r}] must be a list of profile strings")
        audiences[profile] = audience
    return audiences


def _status_view(snap: dict[str, Any]) -> dict[str, Any]:
    receipt = snap.get("receipt")
    return {"id": snap["group_id"], "owner": snap["owner"],
            "members": [{"profile": m.profile, "state": m.state} for m in snap["members"]],
            "local_runtime": snap["local_runtime"],
            "reconciliation_required": snap["reconciliation_required"],
            "receipt": None if receipt is None else {
                "id": receipt.id, "state": receipt.state,
                "created": receipt.created, "finished": receipt.finished,
                "members": dict(receipt.members)}}


def _op_start(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    _take(args, "id", "audiences")
    group_id, audiences = _group_id(args), _audiences(args)
    try:
        statuses = _host(eng).start(group_id, audiences=audiences)
    except HostError as e:
        raise _bad(str(e))
    return {"id": group_id,
            "members": [{"profile": s.profile, "state": s.state} for s in statuses]}


def _op_status(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    _take(args, "id")
    try:
        return _status_view(_host(eng).status(_group_id(args)))
    except HostError as e:
        raise _bad(str(e))


def _op_pause(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    group_id, member = _control(args)
    try:
        _host(eng).pause(group_id, profile=member)
    except HostError as e:
        raise _bad(str(e))
    return {"id": group_id, "paused": True, "member": member}


def _op_resume(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    group_id, member = _control(args)
    try:
        _host(eng).resume(group_id, profile=member)
    except HostError as e:
        raise _bad(str(e))
    return {"id": group_id, "resumed": True, "member": member}


def _op_stop(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    group_id, member = _control(args)
    try:
        _host(eng).stop(group_id, profile=member)
    except HostError as e:
        raise _bad(str(e))
    return {"id": group_id, "stopped": True, "member": member}


# -- shared knowledge (docs/SWARM-KNOWLEDGE.md) ---------------------------
#
# Publishing is owner-only: the publisher is always this seat (``eng.agent``),
# never a caller-supplied name. Reading is member-scoped: ``member`` is
# explicit and the stored group is the only authority for who belongs to it,
# so an unknown profile or another seat's group fails closed. This is a human
# owner inspection of a member's view, never a member read: the response
# labels it, and nothing here claims to be the member.
_VIEW = "human_owner_view"


def _knowledge_item(item) -> dict[str, Any]:
    data = asdict(item)
    data["sources"] = list(item.sources)
    return data


def _string(args: dict[str, Any], name: str) -> str:
    """A required non-empty string field, validated before any store call."""
    value = args[name]
    if not isinstance(value, str) or not value.strip():
        raise _bad(f"{name} must be a non-empty string")
    return value


def _member(args: dict[str, Any]) -> str:
    """The explicit reader profile; membership is then checked against the stored group."""
    member = _string(args, "member")
    if member != member.strip():
        raise _bad("member must be a profile name")
    return member


def _op_knowledge_publish(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"id", "key", "text", "sources", "expected_revision"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = [n for n in ("id", "key", "text", "sources") if n not in args]
    if missing:
        raise _bad("missing field(s): " + ", ".join(missing))
    group_id, key, text = _group_id(args), _string(args, "key"), args["text"]
    sources = args["sources"]
    if not isinstance(text, str):
        raise _bad("text must be a string")
    if (
        isinstance(sources, (str, bytes))
        or not isinstance(sources, list)
        or any(not isinstance(s, str) for s in sources)
    ):
        raise _bad("sources must be a non-empty list of source reference strings")
    expected = args.get("expected_revision", 0)
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise _bad("expected_revision must be a nonnegative integer")
    try:
        item = _knowledge(eng).publish(
            group_id,
            owner=eng.agent,
            key=key,
            text=text,
            sources=sources,
            expected_revision=expected,
        )
    except ValueError as e:
        raise _bad(str(e))
    return {"id": group_id, "view": _VIEW, **_knowledge_item(item)}


def _op_knowledge_read(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"id", "member", "key"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = [n for n in ("id", "member", "key") if n not in args]
    if missing:
        raise _bad("missing field(s): " + ", ".join(missing))
    group_id, member, key = _group_id(args), _member(args), _string(args, "key")
    try:
        item = _knowledge(eng).read(group_id, owner=eng.agent, member=member, key=key)
    except ValueError as e:
        raise _bad(str(e))
    return {
        "id": group_id,
        "member": member,
        "key": key,
        "view": _VIEW,
        "item": None if item is None else _knowledge_item(item),
    }


def _op_knowledge_list(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"id", "member", "limit"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = [n for n in ("id", "member") if n not in args]
    if missing:
        raise _bad("missing field(s): " + ", ".join(missing))
    group_id, member = _group_id(args), _member(args)
    limit = _bounded(args["limit"], "limit", 1, 1000) if "limit" in args else _LIMIT
    try:
        keys = _knowledge(eng).keys(group_id, owner=eng.agent, member=member)
    except ValueError as e:
        raise _bad(str(e))
    return {
        "id": group_id,
        "member": member,
        "view": _VIEW,
        "keys": keys[:limit],
        "total": len(keys),
        "limit": limit,
    }


def _op_knowledge_history(eng: CoreRuntime, args: dict[str, Any]) -> dict[str, Any]:
    unknown = set(args) - {"id", "member", "key", "limit"}
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = [n for n in ("id", "member", "key") if n not in args]
    if missing:
        raise _bad("missing field(s): " + ", ".join(missing))
    group_id, member, key = _group_id(args), _member(args), _string(args, "key")
    limit = _bounded(args["limit"], "limit", 1, 1000) if "limit" in args else _LIMIT
    try:
        revisions = _knowledge(eng).history(
            group_id, owner=eng.agent, member=member, key=key
        )
    except ValueError as e:
        raise _bad(str(e))
    shown = revisions[-limit:]
    return {
        "id": group_id,
        "member": member,
        "key": key,
        "view": _VIEW,
        "revisions": [_knowledge_item(r) for r in shown],
        "total": len(revisions),
        "limit": limit,
    }


_OPS.update(
    {
        "knowledge_publish": _op_knowledge_publish,
        "knowledge_read": _op_knowledge_read,
        "knowledge_list": _op_knowledge_list,
        "knowledge_history": _op_knowledge_history,
    }
)


_OPS.update({"create": _op_create, "start": _op_start, "status": _op_status,
             "pause": _op_pause, "resume": _op_resume, "stop": _op_stop})


async def command(eng: CoreRuntime, b) -> Any:
    """The command.run name "group". The subcommand name is ``b.args``'s first word; the rest is one JSON object."""
    principal.require_principal("manage swarm groups")
    op, _, rest = b.args.partition(" ")
    fn = _OPS.get(op.strip())
    if fn is None:
        raise CommandFailed("bad_request",
                            "usage: /group <list|inspect|send|observe|create|start|"
                            "status|pause|resume|stop|"
                            "knowledge_publish|knowledge_read|knowledge_list|"
                            "knowledge_history> '<json>'")
    return fn(eng, _args(rest))
