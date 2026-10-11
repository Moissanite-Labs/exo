"""Agent tools (`exo-bus` toolset): bus_post, bus_inbox, bus_thread, bus_verify.

Handlers take the Hermes tool args dict and return a JSON string. They stay hidden (check_fn) until
the profile sets EXO_BUS_URL and EXO_BUS_SEAT.
"""
from __future__ import annotations

import json
from typing import Any

from exo.bus import BusError

from .settings import Cursor, configured, make_client

TOOLSET = "exo-bus"


def _ok(**data) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _listify(value) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in (value or [])]


def _lines(cli, envs) -> list[dict]:
    reg = cli.registry() if any(e.get("enc") for e in envs) else {}
    relay_pub = cli.head().get("relay_pubkey") if reg else None
    return [{"seq": e["seq"], "id": e["id"], "from": e["from"], "to": e["to"], "kind": e["kind"],
             "thread": e["thread"], "ts": e["ts"], "text": cli.text(e, reg, relay_pub)} for e in envs]


def bus_post(args: dict, **_: Any) -> str:
    """Post a signed message. `to` is a seat, team:<name>, group, or a list. `sym2p` sends a SYM-2P line."""
    to = _listify(args.get("to"))
    text = str(args.get("text") or args.get("body") or args.get("message") or "")
    if not to or not text:
        return _err("bus_post needs 'to' and 'text'")
    try:
        cli = make_client()
        kw = {"kind": args.get("kind") or "msg", "thread": args.get("thread") or None,
              "enc_to": args.get("enc_to") or None}
        res = cli.post_sym2p(to, text, **kw) if args.get("sym2p") else cli.post(to, text, **kw)
        return _ok(**res)
    except Exception as e:  # BusError, sym2p validation errors
        return _err(str(e))


def bus_inbox(args: dict | None = None, **_: Any) -> str:
    """Messages to this seat, its team or the group since the stored cursor (or `since`)."""
    args = args or {}
    try:
        cli = make_client()
        cursor = Cursor(cli.seat)
        since = int(args["since"]) if args.get("since") is not None else cursor.get()
        envs = cli.inbox(since, int(args.get("limit") or 50))
        if envs and args.get("mark_read", True) and args.get("since") is None:
            cursor.set(envs[-1]["seq"])
        return _ok(seat=cli.seat, since=since, messages=_lines(cli, envs))
    except BusError as e:
        return _err(str(e))


def bus_thread(args: dict, **_: Any) -> str:
    thread = str(args.get("thread") or "").strip()
    if not thread:
        return _err("bus_thread needs 'thread'")
    try:
        cli = make_client()
        return _ok(thread=thread, messages=_lines(cli, cli.thread(thread)))
    except BusError as e:
        return _err(str(e))


def bus_verify(args: dict | None = None, **_: Any) -> str:
    try:
        return _ok(**make_client().verify())
    except BusError as e:
        return _err(str(e))


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


TOOLS = {
    "bus_post": (bus_post, "Post a signed message on the Exo Bus to another agent seat, a team or the group.",
                 {"to": {"type": "array", "items": {"type": "string"},
                         "description": "Recipients: seat ids, team:<name>, or group."},
                  "text": _str("Message text, or a SYM-2P line when sym2p is true."),
                  "thread": _str("Optional thread id to reply in."),
                  "kind": {"type": "string", "enum": ["msg", "ack", "claim", "notice", "directive", "report", "file"],
                           "description": "Message kind. Default msg."},
                  "enc_to": _str("Optional seat to seal the body to (only that seat can read it)."),
                  "sym2p": {"type": "boolean", "description": "Send text as a structured SYM-2P body."}},
                 ["to", "text"]),
    "bus_inbox": (bus_inbox, "Read new Exo Bus messages addressed to me, my team or the group. Advances a read "
                             "cursor unless 'since' is given. SYM-2P bodies come back as plain English.",
                  {"since": {"type": "integer", "description": "Optional: read after this seq without moving the cursor."},
                   "limit": {"type": "integer", "description": "Max messages (default 50)."},
                   "mark_read": {"type": "boolean", "description": "Advance the cursor (default true)."}}, []),
    "bus_thread": (bus_thread, "Read one Exo Bus thread in order.", {"thread": _str("Thread id.")}, ["thread"]),
    "bus_verify": (bus_verify, "Download the bus log and check every id, signature and chain link.", {}, []),
}


def register_tools(ctx) -> None:
    for name, (handler, description, properties, required) in TOOLS.items():
        parameters: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
        ctx.register_tool(name=name, toolset=TOOLSET, handler=handler, description=description,
                          schema={"name": name, "description": description, "parameters": parameters},
                          emoji="\U0001f4ec", check_fn=configured)

