"""The swarm web API: eight fixed routes over the core's ``command.run`` name ``group``. ``api.py`` routes ``/api/swarm*`` here.

``dispatch(centre, method, path, q, body)`` follows the ``api.dispatch`` convention: it returns ``(status, data)``,
or ``None`` when the path is not under ``/api/swarm`` (so the caller can fall through). Every route validates its
input first, then sends exactly one ``CommandRun(name="group", args="<op> <json>")`` through ``centre.source``.
The source is the only authority: this module never takes a caller, owner, host, socket or session from the
request, never builds a command name or operation from input, and adds no permission check or grant. The core
(``exo.core.group_commands``) requires the principal and scopes every group to its own seat.

  GET  /api/swarm/groups                     group list
  GET  /api/swarm/groups/<id>                group inspect
  GET  /api/swarm/groups/<id>/messages       group observe  ?member=&after=&limit=
  POST /api/swarm/groups/<id>/send           group send     {text, audience?}
  GET  /api/swarm/groups/<id>/status         group status
  POST /api/swarm/groups/<id>/pause          group pause    {member?}
  POST /api/swarm/groups/<id>/resume         group resume   {member?}
  POST /api/swarm/groups/<id>/stop           group stop     {member?}

Unknown paths under /api/swarm answer 404, wrong methods 405, and unknown query or body keys 400.

Send means PERSISTED ONLY. The core stores the message and, if a running conversation of this seat is attached,
asks it to wake. The reply never says the message was processed, read or that a member was woken, and this module
does not look at group status before or after sending (that would race and could not prove delivery). A send that
fails after the request left (transport error, bad reply) says the message MAY have been saved.

pause, resume and stop are fixed operations; ``member`` is the only option (absent means the whole group, sent as no
member at all). A 200 means the core ACCEPTED the request. It does not mean the members have paused, resumed or
stopped: execution settles later, so read ``status`` for the real state. If the request left and the outcome is not
known (transport error, malformed or mismatched reply) the answer is 502 with ``outcome: "unknown"``. This module
never retries: the caller must read ``status`` first. A refusal the core states itself (403, 400, ...) is definite.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from exo.protocol import schema as s

from .source import SourceError

Reply = Tuple[int, Dict[str, Any]]

MAX_TEXT = 50_000
MAX_AUDIENCE = 64
MAX_LIMIT = 1000                       # the core's observe ceiling
MAX_AFTER = 2 ** 63 - 1
GROUP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")        # core refuses ids over 128 characters
PROFILE = re.compile(r"[A-Za-z][A-Za-z0-9_.@-]{0,63}")            # the core's profile id shape
DIGITS = re.compile(r"[0-9]{1,19}")
MODES = ("socratic", "conductor")
SENDER_KINDS = ("human", "member")

PERSISTED_ONLY = ("Saved to the group log only. This does not mean the message was read or processed, "
                  "or that any member was woken.")

# Static messages: nothing from the core, the transport or an exception reaches a caller.
BAD_REPLY = "The swarm core returned an unexpected reply."
UNAVAILABLE = "The swarm core could not be reached or failed."
SEND_UNKNOWN = " The message may or may not have been saved; check the group log before sending again."
CONTROL_UNKNOWN = (" The request may or may not have been applied; read the group status before trying again. "
                   "It is not retried automatically.")
ACCEPTED_ONLY = ("The request was accepted. This does not mean members have already changed state; "
                 "read the group status for the settled state.")
STATES = ("idle", "queued", "running", "pausing", "paused", "completed", "stopped", "failed", "unknown")
ACTIONS = {"pause": "paused", "resume": "resumed", "stop": "stopped"}   # op -> the core's confirmation flag
REFUSALS = {
    "forbidden": (403, "Only the principal may use swarm groups."),
    "bad_request": (400, "The swarm core rejected the request (unknown group, invalid audience or invalid fields)."),
    "not_found": (501, "Swarm group commands are not available on this core."),
    "unsupported": (501, "Swarm group commands are not available on this core."),
    "unknown_command": (501, "Swarm group commands are not available on this core."),
    "conflict": (409, "The swarm core reports a conflict."),
}


class BadRequest(Exception):
    pass


class BadReply(Exception):
    pass


# ------------------------------------------------------------------ input
def _group_id(raw: str) -> str:
    if not GROUP_ID.fullmatch(raw):
        raise BadRequest("not a group id")
    return raw


def _profile(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not PROFILE.fullmatch(raw):
        raise BadRequest("{} must be a profile id".format(name))
    return raw


def _keys(data: Dict[str, Any], allowed: Tuple[str, ...], where: str) -> None:
    extra = sorted(k for k in data if k not in allowed)
    if extra:
        raise BadRequest("unknown {} field: {}".format(where, ", ".join(str(k)[:40] for k in extra[:5])))


def _query(q: Any, allowed: Tuple[str, ...]) -> Dict[str, str]:
    if q is None:
        q = {}
    if not isinstance(q, dict):
        raise BadRequest("bad query")
    _keys(q, allowed, "query")
    out: Dict[str, str] = {}
    for k, v in q.items():
        vals = v if isinstance(v, list) else [v]
        if len(vals) != 1 or not isinstance(vals[0], str):
            raise BadRequest("{} must be given once".format(k))
        out[k] = vals[0]
    return out


def _body(body: Any, allowed: Tuple[str, ...]) -> Dict[str, Any]:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise BadRequest("body must be an object")
    _keys(body, allowed, "body")
    return body


def _int(raw: str, name: str, lo: int, hi: int) -> int:
    if not DIGITS.fullmatch(raw) or not lo <= int(raw) <= hi:
        raise BadRequest("{} must be an integer between {} and {}".format(name, lo, hi))
    return int(raw)


def _text(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise BadRequest("text is required")
    if len(raw) > MAX_TEXT or "\x00" in raw:
        raise BadRequest("text is too long or has an invalid character")
    return raw


def _audience(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_AUDIENCE:
        raise BadRequest("audience must be a list of 1 to {} profile ids".format(MAX_AUDIENCE))
    out = [_profile(p, "audience") for p in raw]
    if len(set(out)) != len(out):
        raise BadRequest("audience has a repeated profile")
    return out


# ------------------------------------------------------------------ core call
def _args(op: str, payload: Optional[Dict[str, Any]] = None) -> str:
    return op if payload is None else "{} {}".format(op, json.dumps(payload))


async def _call(centre, op: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    res = await centre.source.call(s.CommandRun(name="group", args=_args(op, payload)))
    out = res.get("output") if isinstance(res, dict) else None
    if not isinstance(out, dict):
        raise BadReply()
    return out


# ------------------------------------------------------------------ reply validation
def _str(d: Dict[str, Any], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise BadReply()
    return v


def _int_field(d: Dict[str, Any], key: str, lo: int) -> int:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= MAX_AFTER:
        raise BadReply()
    return v


def _group(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise BadReply()
    conductor, members = raw.get("conductor"), raw.get("members")
    if raw.get("mode") not in MODES or not isinstance(members, list) or not members:
        raise BadReply()
    if conductor is not None and not isinstance(conductor, str):
        raise BadReply()
    rows = []
    for m in members:
        if not isinstance(m, dict):
            raise BadReply()
        rows.append({"profile": _str(m, "profile"), "session": _str(m, "session")})
    gid = _str(raw, "id")
    if not GROUP_ID.fullmatch(gid):
        raise BadReply()
    return {"id": gid, "owner": _str(raw, "owner"), "mode": raw["mode"], "conductor": conductor,
            "members": rows, "created": _str(raw, "created")}


def _message(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("sender_kind") not in SENDER_KINDS:
        raise BadReply()
    aud = raw.get("audience")
    if not isinstance(aud, list) or not aud or any(not isinstance(a, str) or not a for a in aud):
        raise BadReply()
    text = raw.get("text")
    if not isinstance(text, str):
        raise BadReply()
    return {"seq": _int_field(raw, "seq", 1), "id": _str(raw, "id"), "sender": _str(raw, "sender"),
            "sender_kind": raw["sender_kind"], "audience": list(aud), "text": text, "created": _str(raw, "created")}


def _bool_field(d: Dict[str, Any], key: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise BadReply()
    return v


def _state(raw: Any) -> str:
    if not isinstance(raw, str) or raw not in STATES:
        raise BadReply()
    return raw


def _status(raw: Any, gid: str) -> Dict[str, Any]:
    if not isinstance(raw, dict) or _str(raw, "id") != gid:
        raise BadReply()
    members = raw.get("members")
    if not isinstance(members, list) or not members:
        raise BadReply()
    rows = []
    for m in members:
        if not isinstance(m, dict):
            raise BadReply()
        prof = _str(m, "profile")
        if not PROFILE.fullmatch(prof):
            raise BadReply()
        rows.append({"profile": prof, "state": _state(m.get("state"))})
    if len({r["profile"] for r in rows}) != len(rows):
        raise BadReply()
    rec = raw.get("receipt")
    receipt = None
    if rec is not None:
        if not isinstance(rec, dict) or not isinstance(rec.get("members"), dict):
            raise BadReply()
        finished = rec.get("finished")
        if finished is not None and (not isinstance(finished, str) or not finished):
            raise BadReply()
        rmembers = {}
        for k, v in rec["members"].items():
            if not isinstance(k, str) or not PROFILE.fullmatch(k):
                raise BadReply()
            rmembers[k] = _state(v)
        receipt = {"id": _str(rec, "id"), "state": _str(rec, "state"), "created": _str(rec, "created"),
                   "finished": finished, "members": rmembers}
    return {"id": gid, "owner": _str(raw, "owner"), "members": rows,
            "local_runtime": _bool_field(raw, "local_runtime"),
            "reconciliation_required": _bool_field(raw, "reconciliation_required"), "receipt": receipt}


# ------------------------------------------------------------------ routes
async def _list(c, gid, q, body):
    _query(q, ()), _body(body, ())
    out = await _call(c, "list")
    rows = out.get("groups")
    if not isinstance(rows, list):
        raise BadReply()
    return {"groups": [_group(r) for r in rows]}


async def _inspect(c, gid, q, body):
    gid = _group_id(gid)
    _query(q, ()), _body(body, ())
    group = _group(await _call(c, "inspect", {"id": gid}))
    if group["id"] != gid:
        raise BadReply()
    return group


async def _observe(c, gid, q, body):
    gid = _group_id(gid)
    _body(body, ())
    qs = _query(q, ("member", "after", "limit"))
    payload: Dict[str, Any] = {"id": gid}
    if "member" in qs:
        payload["member"] = _profile(qs["member"], "member")
    after = 0
    if "after" in qs:
        after = payload["after"] = _int(qs["after"], "after", 0, MAX_AFTER)
    limit = 100                          # the core's default page
    if "limit" in qs:
        limit = payload["limit"] = _int(qs["limit"], "limit", 1, MAX_LIMIT)
    out = await _call(c, "observe", payload)
    rows = out.get("messages")
    if not isinstance(rows, list) or len(rows) > limit:
        raise BadReply()
    msgs = [_message(r) for r in rows]
    last = after
    for m in msgs:
        if m["seq"] <= last:
            raise BadReply()
        last = m["seq"]
    nxt = _int_field(out, "next_after", 0)
    if nxt != (msgs[-1]["seq"] if msgs else 0):
        raise BadReply()
    return {"messages": msgs, "next_after": nxt}


async def _send(c, gid, q, body):
    gid = _group_id(gid)
    _query(q, ())
    data = _body(body, ("text", "audience"))
    text = _text(data.get("text"))
    payload: Dict[str, Any] = {"id": gid, "text": text}
    audience = None
    if "audience" in data and data["audience"] is not None:
        audience = payload["audience"] = _audience(data["audience"])
    try:
        out = await _call(c, "send", payload)
        msg = _message(out)
        if msg["sender_kind"] != "human" or msg["text"] != text or (audience is not None and msg["audience"] != audience):
            raise BadReply()
    except Exception as exc:   # the request left: any failure from here says the message may be saved
        raise _SendFailed(exc) from None
    return {"message": msg, "persisted": True, "processed": "unknown", "woken": "unknown", "note": PERSISTED_ONLY}


async def _group_status(c, gid, q, body):
    gid = _group_id(gid)
    _query(q, ()), _body(body, ())
    return _status(await _call(c, "status", {"id": gid}), gid)


def _control(op: str):
    flag = ACTIONS[op]

    async def handler(c, gid, q, body):
        gid = _group_id(gid)
        _query(q, ())
        data = _body(body, ("member",))
        payload: Dict[str, Any] = {"id": gid}
        member = None
        if data.get("member") is not None:          # absent or null: the whole group, no member key at all
            member = payload["member"] = _profile(data["member"], "member")
        try:
            out = await _call(c, op, payload)
            if out.get("id") != gid or out.get(flag) is not True or out.get("member") != member:
                raise BadReply()
        except Exception as exc:   # the request left: any failure from here leaves the outcome unknown
            raise _ControlFailed(exc) from None
        return {"id": gid, "action": op, "member": member, "accepted": True, "settled": "unknown",
                "note": ACCEPTED_ONLY}
    return handler


class _SendFailed(Exception):
    def __init__(self, cause: BaseException):
        super().__init__()
        self.cause = cause


class _ControlFailed(_SendFailed):
    pass


ROUTES = {
    ("GET", ()): _list,
    ("GET", ("<id>",)): _inspect,
    ("GET", ("<id>", "messages")): _observe,
    ("POST", ("<id>", "send")): _send,
    ("GET", ("<id>", "status")): _group_status,
    ("POST", ("<id>", "pause")): _control("pause"),
    ("POST", ("<id>", "resume")): _control("resume"),
    ("POST", ("<id>", "stop")): _control("stop"),
}


def _route(method: str, rest: List[str]):
    """The handler, ``False`` for a known path with another method, or ``None`` for an unknown path."""
    if not rest or rest[0] != "groups":
        return None
    shape = tuple("<id>" if i == 0 else p for i, p in enumerate(rest[1:]))
    if shape not in {k[1] for k in ROUTES}:
        return None
    return ROUTES.get((method, shape), False)


def error_reply(exc: BaseException, sending: bool = False, controlling: bool = False) -> Reply:
    """A static, sanitized reply. Codes come from a fixed set; messages never carry core or transport text."""
    if isinstance(exc, BadRequest):
        return 400, {"error": str(exc), "code": "bad_request"}
    if isinstance(exc, SourceError) and exc.code in REFUSALS:
        status, msg = REFUSALS[exc.code]
        return status, {"error": msg, "code": exc.code}
    reply: Dict[str, Any] = {"error": (BAD_REPLY if isinstance(exc, BadReply) else UNAVAILABLE) + (SEND_UNKNOWN if sending else ""),
                             "code": "bad_reply" if isinstance(exc, BadReply) else "unavailable"}
    if sending:
        reply["persisted"] = "unknown"
    if controlling:
        reply["error"] += CONTROL_UNKNOWN
        reply["outcome"] = "unknown"
    return 502, reply


async def dispatch(centre, method: str, path: str, q: Dict[str, Any], body: Dict[str, Any]) -> Optional[Reply]:
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "api" or parts[1] != "swarm":
        return None
    handler = _route(method, parts[2:])
    if handler is None:
        return 404, {"error": "not found", "code": "not_found"}
    if handler is False:
        return 405, {"error": "method not allowed", "code": "method_not_allowed"}
    gid = parts[3] if len(parts) > 3 else ""
    try:
        return 200, await handler(centre, gid, q, body)
    except _SendFailed as exc:
        cause = exc.cause
        if isinstance(cause, SourceError) and cause.code in REFUSALS:
            return error_reply(cause)
        control = isinstance(exc, _ControlFailed)
        return error_reply(cause if isinstance(cause, BadReply) else RuntimeError(), sending=not control, controlling=control)
    except Exception as exc:
        return error_reply(exc)
