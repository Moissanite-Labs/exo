"""The chat web API: plain routes over protocol commands. No HTTP in here, so tests call it directly.

``dispatch(centre, method, path, query, body)`` returns ``(status, data)`` or ``None`` when the path is
not one of these routes. Every write validates its input, then sends one protocol command through the
centre's source. A core that does not handle a command yet answers 501.

  GET  /api/chat/sessions                  session.list
  POST /api/chat/sessions                  session.create        {title, seat, model, cwd}
  GET  /api/chat/sessions/<id>             session.get           the transcript so far, and the ids of queued steers
  POST /api/chat/sessions/<id>/send        turn.send             {text, mode}
  POST /api/chat/sessions/<id>/interrupt   turn.interrupt
  POST /api/chat/sessions/<id>/unsteer     command.run unsteer    {message_id}; pulls a queued steer back (D-045)
  POST /api/chat/sessions/<id>/rename      command.run rename     {title}; principal only (D-069)
  POST /api/chat/sessions/<id>/command     command.run           {name, args}; read-only names only
  POST /api/chat/sessions/<id>/model       session.get, then models.set {model}; the shared main route, principal only
  GET  /api/chat/models                    models.list           the /model picker's choices
  GET  /api/chat/panels                    open ui.panel panels, folded from the event stream
  POST /api/feedback                       feedback.send         {session, verdict, message_id, text}
  POST /api/ui/respond                     ui.respond            {panel_id, action, value}
  GET  /api/complete/commands             the slash commands this page runs, with argument shapes and choices, the
                                           core's commands.list names and the terminal client's own (client); of
                                           those, the names the page does not run (/grants, /help) are held, not sent;
                                           core is null while the core cannot answer, [] from an older core without
                                           commands.list
  GET  /api/complete/files?q=&session=     files under the session's folder for @ (D-037); the server guards it
  GET  /api/permissions                    command.run permissions json: the mode and its counts (D-065)
  POST /api/permissions                    command.run permissions {mode: bypass|ask}; the core checks the caller
  GET  /api/tasks                          tasks.list
  GET  /api/tasks/<task>                   command.run subagent open   the child's task row and session tail (#97)
  POST /api/tasks/<task>/steer             command.run subagent send   {text}; the core allows the principal only
  POST /api/tasks/<task>/stop              command.run subagent stop
  GET  /api/schedule                       schedule.edit list
  POST /api/schedule                       schedule.edit         {action, job_id, when, prompt, title, ...}
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, Optional, Tuple

from exo import complete as cm
from exo.protocol import schema as s

from .source import TOKEN_REFUSED, SourceError, core_message

MAX_TEXT = 50_000
MODES = ("send", "queue", "steer")
VERDICTS = ("accept", "edit", "reject", "should_have_been")
READ_COMMANDS = ("context", "findings", "trace")   # read-only reports the core answers itself
PERMISSION_MODES = ("bypass", "ask")
PANEL_ACTIONS = ("opened", "dismissed", "acted", "ignored")
SCHEDULE_ACTIONS = ("list", "add", "remove", "pause", "resume", "run_now")
TIERS = ("fast", "standard", "deep")
TASK_REF = re.compile(r"[A-Za-z0-9_.:-]{1,80}")

Reply = Tuple[int, Dict[str, Any]]


class BadRequest(Exception):
    pass


class Forbidden(Exception):
    pass


def _str(body: Dict[str, Any], key: str, required: bool = False, limit: int = 2000) -> str:
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise BadRequest("{} is required".format(key))
        return ""
    if not isinstance(v, str):
        raise BadRequest("{} must be text".format(key))
    if len(v) > limit:
        raise BadRequest("{} is too long".format(key))
    return v


def _opt(body: Dict[str, Any], key: str) -> Optional[str]:
    return _str(body, key) or None


def _choice(body: Dict[str, Any], key: str, allowed: tuple, default: Optional[str] = None) -> str:
    v = body.get(key, default)
    if v not in allowed:
        raise BadRequest("{} must be one of {}".format(key, ", ".join(allowed)))
    return v


def error_reply(exc: Exception) -> Reply:
    if isinstance(exc, BadRequest):
        return 400, {"error": str(exc)}
    if isinstance(exc, Forbidden):
        return 403, {"error": str(exc), "code": "forbidden"}
    if isinstance(exc, SourceError):
        status = (404 if exc.code == "not_found" else 403 if exc.code == "forbidden" else 409 if exc.code == "conflict"
                  else 400 if exc.code in ("bad_model", "bad_request")
                  else 501 if exc.code in ("unsupported", "unknown_command") else 502)
        # #856: a refused bearer token gets the token-file sentence, not the core's raw 401 body
        return status, {"error": core_message(exc) if exc.code in TOKEN_REFUSED else exc.message, "code": exc.code}
    return 502, {"error": core_message(exc)}


async def dispatch(centre, method: str, path: str, q: Dict[str, Any], body: Dict[str, Any]) -> Optional[Reply]:
    parts = [p for p in path.split("/") if p]            # ["api", ...]
    if len(parts) < 2 or parts[0] != "api":
        return None
    handler = _route(method, parts[1:], "/".join(parts[1:]))
    if handler is None:
        return None
    if handler is False:
        return 405, {"error": "method not allowed"}
    try:
        return 200, await handler(centre, parts[1:], q, body)
    except Exception as exc:
        return error_reply(exc)


def _route(method: str, p: list, key: str):
    get, post = method == "GET", method == "POST"
    table = {
        "chat/sessions": (_sessions, _create),
        "chat/models": (_models, None),
        "chat/panels": (_panels, None),
        "feedback": (None, _feedback),
        "ui/respond": (None, _ui_respond),
        "complete/commands": (_complete_commands, None),
        "complete/files": (_complete_files, None),
        "permissions": (_permissions, _permissions_set),
        "tasks": (_tasks, None),
        "schedule": (_schedule_list, _schedule_edit),
    }
    if key in table:
        g, po = table[key]
        return (g if get else po if post else None) or False
    if len(p) == 3 and p[:2] == ["chat", "sessions"]:
        return _transcript if get else False
    if len(p) == 2 and p[0] == "tasks":
        return _task_open if get else False
    if len(p) == 3 and p[0] == "tasks" and p[2] in ("steer", "stop"):
        return ({"steer": _task_steer, "stop": _task_stop}[p[2]]) if post else False
    if len(p) == 4 and p[:2] == ["chat", "sessions"] and p[3] in ("send", "interrupt", "command", "unsteer", "rename", "model"):
        return ({"send": _send, "interrupt": _interrupt, "command": _command, "unsteer": _unsteer, "rename": _rename, "model": _model}[p[3]]) if post else False
    return None


async def _sessions(c, p, q, body):
    try:
        limit = min(int(q["limit"][0]), 500) if q.get("limit") else 100
    except ValueError:
        raise BadRequest("limit must be a number")
    if limit < 1:   # SQLite reads LIMIT -1 as no limit at all (exo-desktop #55)
        raise BadRequest("limit must be at least 1")
    res = await c.source.call(s.SessionList(limit=limit))
    return {"sessions": res.get("sessions") or []}


async def _create(c, p, q, body):
    res = await c.source.call(s.SessionCreate(title=_str(body, "title", limit=200), seat=_opt(body, "seat"),
                                              model=_opt(body, "model"), cwd=_opt(body, "cwd"),
                                              kind=_choice(body, "kind", ("", "thread", "private"), "")))
    return {"session": res.get("session")}


async def _transcript(c, p, q, body):
    since = c.state.seq  # before the RPC: replay events arriving while the snapshot is read
    res = await c.source.call(s.SessionGet(session=p[2]))
    return {"session": res.get("session"), "messages": res.get("messages") or [], "steers": res.get("steers") or [],
            "seq": since}


async def _send(c, p, q, body):
    text = _str(body, "text", required=True, limit=MAX_TEXT)
    mode = _choice(body, "mode", MODES, "send")
    return await c.source.call(s.TurnSend(session=p[2], text=text, mode=mode))


async def _models(c, p, q, body):
    return await c.source.call(s.ModelsList())


async def _model(c, p, q, body):
    # The core validates the route and enforces principal authority, just as for the TUI.
    model = _str(body, "model", required=True, limit=300)
    # Validate the target before models.set changes the shared router (the core checks it afterwards).
    await c.source.call(s.SessionGet(session=p[2]))
    return await c.source.call(s.ModelsSet(model=model, session=p[2]))


async def _command(c, p, q, body):
    name = _choice(body, "name", READ_COMMANDS)
    return await c.source.call(s.CommandRun(name=name, args=_str(body, "args", limit=40), session=p[2]))


async def _unsteer(c, p, q, body):
    res = await c.source.call(s.CommandRun(name="unsteer", args=_str(body, "message_id", limit=80), session=p[2]))
    return res.get("output") or {"cancelled": []}


async def _rename(c, p, q, body):
    title = _str(body, "title", required=True, limit=200)
    res = await c.source.call(s.CommandRun(name="rename", args=title, session=p[2]))
    return res.get("output") or {}


async def _interrupt(c, p, q, body):
    return await c.source.call(s.TurnInterrupt(session=p[2]))


async def _panels(c, p, q, body):
    return {"panels": sorted(c.panels.values(), key=lambda x: x["seq"]), "seq": c.state.seq}


async def _feedback(c, p, q, body):
    verdict = _choice(body, "verdict", VERDICTS)
    text = _str(body, "text", limit=MAX_TEXT)
    if verdict in ("edit", "should_have_been") and not text.strip():
        raise BadRequest("text is required for {}".format(verdict))
    return await c.source.call(s.FeedbackSend(session=_str(body, "session", required=True),
                                              verdict=verdict, message_id=_opt(body, "message_id"), text=text))


async def _ui_respond(c, p, q, body):
    value = body.get("value") or {}
    if not isinstance(value, dict):
        raise BadRequest("value must be an object")
    action = _choice(body, "action", PANEL_ACTIONS)
    pid = _str(body, "panel_id", required=True)
    res = await c.source.call(s.UiRespond(panel_id=pid, action=action, value=value))
    if action == "dismissed":
        c.panels.pop(pid, None)
    return res


async def _permissions(c, p, q, body):
    """The permission mode for the banner. A core without the gate (or an older one) reads as ask."""
    try:
        out = (await c.source.call(s.CommandRun(name="permissions", args="json"))).get("output")
    except SourceError as exc:
        if exc.code not in ("not_found", "unsupported", "unknown_command"):
            raise
        out = None
    return out if isinstance(out, dict) else {"mode": "ask", "available": False, "auto_approved": 0}


async def _permissions_set(c, p, q, body):
    """Switch the mode. Only the principal may turn bypass on; the core decides that from the connection."""
    mode = _choice(body, "mode", PERMISSION_MODES)
    try:
        res = await c.source.call(s.CommandRun(name="permissions", args=mode))
    except SourceError as exc:
        if exc.code == "forbidden":
            raise Forbidden(exc.message) from None
        raise
    return {"output": res.get("output"), "permissions": await _permissions(c, p, q, {})}


async def _tasks(c, p, q, body):
    res = await c.source.call(s.TasksList(session=(q.get("session") or [None])[0]))
    return {"tasks": res.get("tasks") or []}


def _task_ref(p) -> str:
    if not TASK_REF.fullmatch(p[1]):
        raise BadRequest("not a task id")
    return p[1]


async def _subagent(c, args: str):
    return (await c.source.call(s.CommandRun(name="subagent", args=args))).get("output") or {}


async def _task_open(c, p, q, body):
    return await _subagent(c, "open " + _task_ref(p))


async def _task_steer(c, p, q, body):
    return await _subagent(c, "send {} {}".format(_task_ref(p), _str(body, "text", required=True, limit=MAX_TEXT)))


async def _task_stop(c, p, q, body):
    return await _subagent(c, "stop " + _task_ref(p))


async def _schedule_list(c, p, q, body):
    res = await c.source.call(s.ScheduleEdit(action="list"))
    return {"jobs": res.get("jobs") or []}


async def _schedule_edit(c, p, q, body):
    action = _choice(body, "action", SCHEDULE_ACTIONS)
    job = _opt(body, "job_id")
    if action == "add":
        _str(body, "when", required=True, limit=100)
        _str(body, "prompt", required=True, limit=MAX_TEXT)
    elif action != "list" and not job:
        raise BadRequest("job_id is required for {}".format(action))
    return await c.source.call(s.ScheduleEdit(
        action=action, job_id=job, when=_str(body, "when", limit=100), prompt=_str(body, "prompt", limit=MAX_TEXT),
        title=_str(body, "title", limit=200), seat=_opt(body, "seat"), tier=_choice(body, "tier", TIERS, "fast"),
        deliver=_str(body, "deliver", limit=100) or "notice"))


# ---------------------------------------------------------------- completion (D-037)
WEB_COMMANDS = {"context": "what the next model call carries, part by part",
                "model": "pick or set the shared main model; default <ref> saves the default",
                "default": "pick or set the default model for new threads",
                "findings": "open harness findings", "trace": "how the harness performed this session",
                "new": "start a new thread", "rename": "give this thread a new title",
                "permissions": "ask before risky calls, or bypass: auto-approve everything except changes to "
                               "Exo's own records and reading secrets",
                "layout": "inverse puts the box on top with the newest message under it; classic is the usual order"}
# The names of the shared table that core plugins serve through commands.list, not the terminal client: credited to
# the core when listed, and unknown (so sent, as in the TUI) when their plugin is not loaded.
PLUGIN_COMMANDS = {"trace", "findings", "grants", "exo", "orchestra", "peer-release", "peer-deny", "exo-command"}
_INDEXES: Dict[tuple, cm.FileIndex] = {}


async def _complete_commands(c, p, q, body):
    """Static: the commands the web composer runs itself. Also says whether the guidance line is on, which names the
    core's commands.list has, and the terminal client's own commands (``client``: /help, /quit, ...), so typo help
    covers them and the composer holds those it does not run instead of sending them (D-037)."""
    rows = []
    for name, desc in WEB_COMMANDS.items():
        slot = cm.plan(name, [])
        rows.append({"name": name, "description": desc, "args": cm.ARG_HINTS.get(name, ""),
                     "choices": [{"value": v, "meta": m} for v, m in (slot.choices if slot else ())]})
    guidance: bool | None = True
    try:
        got = await c.source.call(s.ConfigGet(key="guidance.enabled"))
        guidance = got.get("value") is not False
    except SourceError as exc:
        # not_found: the key is unset, so the default (on) holds; unknown_command/unsupported: an older core.
        if exc.code not in ("unknown_command", "unsupported", "not_found"):
            guidance = None   # not known yet (a core down or restarting): the page asks again
    except Exception:
        guidance = None
    # null when commands.list fails for now (a core down or restarting): the page asks again for as long as it takes.
    # An older core that has no commands.list says so, and its [] ends the asking.
    core = None
    try:
        listed = await c.source.call(s.CommandsList())
        core = [str(r["name"]) for r in listed.get("commands") or [] if isinstance(r, dict) and r.get("name")]
    except SourceError as exc:
        if exc.code in ("unknown_command", "unsupported"):
            core = []
    except Exception:
        pass
    client = [n for n in cm.ARG_HINTS if n not in WEB_COMMANDS and n not in PLUGIN_COMMANDS]
    return {"commands": rows, "guidance": guidance, "core": core, "client": client}


async def _complete_files(c, p, q, body):
    """Read-only file names for ``@``: only under the session's folder, secrets left out, nothing is read."""
    sid = (q.get("session") or [""])[0]
    if not sid or len(sid) > 120:
        return {"files": []}
    res = await c.source.call(s.SessionGet(session=sid))
    cwd = ((res.get("session") or {}).get("cwd")) or ""
    if not cwd or not os.path.isdir(cwd):
        return {"files": []}
    query = (q.get("q") or [""])[0][:200]
    required = bool(getattr(c.source, "executor_required", True))
    key = (cwd, required)
    ix = _INDEXES.get(key)
    if ix is None:
        while len(_INDEXES) >= 4:
            _INDEXES.pop(next(iter(_INDEXES)))
        ix = _INDEXES[key] = cm.FileIndex(cwd, executor_required=required)
    items = await asyncio.to_thread(ix.query, query, 30)
    return {"files": [{"value": i.value, "label": i.label, "meta": i.meta} for i in items]}
