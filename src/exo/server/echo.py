"""EchoEngine: an in-memory Engine for tests and for trying clients without a model.

A turn echoes the user's text back word by word. Text starting with
``approve:`` first asks for approval, so clients can exercise that path.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from exo.core.types import Message, ToolCall, Usage, to_dict
from exo.protocol import schema as s
from exo.server.engine import CommandFailed, Hub


class EchoEngine:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.sessions: dict[str, s.SessionInfo] = {}
        self.messages: dict[str, list[Message]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._running: dict[str, asyncio.Task] = {}  # session -> turn task holding its lock
        self._approvals: dict[str, tuple[asyncio.Future, str]] = {}

    # ------------------------------------------------------------ dispatch

    async def handle(self, command: s.Command, hub: Hub) -> dict[str, Any]:
        method = getattr(self, "_" + command.type.replace(".", "_"), None)
        if method is None:
            raise CommandFailed("unsupported", f"{command.type} is not supported by the echo engine")
        return await method(command.body, hub)

    def _session(self, sid: str) -> s.SessionInfo:
        if sid not in self.sessions:
            raise CommandFailed("not_found", f"no session {sid}")
        return self.sessions[sid]

    def _new(self, hub: Hub, title: str, parent: str | None = None, **kw: Any) -> s.SessionInfo:
        ts = s.now_ts()
        info = s.SessionInfo(id=s.new_id("s"), title=title, parent=parent, created=ts, updated=ts, **kw)
        self.sessions[info.id] = info
        self.messages[info.id] = []
        self._locks[info.id] = asyncio.Lock()
        hub.publish(s.SessionCreated(session=info), info.id)
        return info

    # ------------------------------------------------------------ sessions

    async def _session_create(self, b: s.SessionCreate, hub: Hub) -> dict[str, Any]:
        info = self._new(hub, b.title, seat=b.seat, model=b.model or "echo", cwd=b.cwd)
        return {"session": to_dict(info)}

    async def _session_list(self, b: s.SessionList, hub: Hub) -> dict[str, Any]:
        rows = [i for i in self.sessions.values() if b.seat is None or i.seat == b.seat]
        rows.sort(key=lambda i: i.updated, reverse=True)
        out = []
        for i in rows[: b.limit]:
            task = self._running.get(i.id)
            i.status = "live" if task is not None and not task.done() else "idle"
            out.append(to_dict(i))
        return {"sessions": out}

    async def _approvals_list(self, b: s.ApprovalsList, hub: Hub) -> dict[str, Any]:
        return {"approvals": [{"request_id": rid, "session": sid} for rid, (fut, sid) in self._approvals.items()
                              if not fut.done() and (not b.session or sid == b.session)]}

    async def _session_get(self, b: s.SessionGet, hub: Hub) -> dict[str, Any]:
        info = self._session(b.session)
        return {"session": to_dict(info), "messages": to_dict(self.messages[b.session])}

    async def _session_resume(self, b: s.SessionResume, hub: Hub) -> dict[str, Any]:
        return await self._session_get(s.SessionGet(session=b.session), hub)

    async def _session_fork(self, b: s.SessionFork, hub: Hub) -> dict[str, Any]:
        src = self._session(b.session)
        msgs = list(self.messages[b.session])
        if b.at_message is not None:
            ids = [m.id for m in msgs]
            if b.at_message not in ids:
                raise CommandFailed("not_found", f"no message {b.at_message}")
            msgs = msgs[: ids.index(b.at_message) + 1]
        info = self._new(hub, b.title or src.title, parent=src.id, model=src.model, seat=src.seat)
        self.messages[info.id] = msgs
        info.message_count = len(msgs)
        return {"session": to_dict(info)}

    async def _session_undo(self, b: s.SessionUndo, hub: Hub) -> dict[str, Any]:
        info = self._session(b.session)
        msgs = self.messages[b.session]
        for _ in range(b.turns):
            while msgs and msgs[-1].role != "user":
                msgs.pop()
            if msgs:
                msgs.pop()
        self._touch(hub, info)
        return {"session": to_dict(info)}

    def _touch(self, hub: Hub, info: s.SessionInfo) -> None:
        info.updated = s.now_ts()
        info.message_count = len(self.messages[info.id])
        hub.publish(s.SessionUpdated(session=dataclasses.replace(info)), info.id)

    # ------------------------------------------------------------ turns

    async def _turn_send(self, b: s.TurnSend, hub: Hub) -> dict[str, Any]:
        self._session(b.session)
        text = b.text or "".join(p.text for p in b.parts if p.type == "text")
        turn_id = s.new_id("t")
        asyncio.create_task(self._run_turn(hub, b.session, turn_id, text))
        return {"turn_id": turn_id}

    async def _run_turn(self, hub: Hub, sid: str, turn_id: str, text: str) -> None:
        async with self._locks[sid]:  # one turn at a time per session; queue waits here
            self._running[sid] = asyncio.current_task()  # type: ignore[assignment]
            info = self.sessions[sid]
            user = Message.user(text)
            user.id = s.new_id("m")
            self.messages[sid].append(user)
            hub.publish(s.TurnStarted(turn_id=turn_id, model="echo"), sid)
            stop = "end"
            try:
                if text.startswith("approve:"):
                    allowed = await self._ask(hub, sid, text)
                    text = text if allowed else "denied"
                reply = s.new_id("m")
                words = text.split(" ")
                for i, word in enumerate(words):
                    hub.publish(s.MessageDelta(message_id=reply, text=word if i == 0 else " " + word), sid)
                    await asyncio.sleep(self.delay)
                msg = Message.assistant(text)
                msg.id = reply
                self.messages[sid].append(msg)
                hub.publish(s.MessageCompleted(message=msg), sid)
            except asyncio.CancelledError:
                stop = "interrupted"
            finally:
                self._running.pop(sid, None)
            usage = Usage(input_tokens=len(text.split()), output_tokens=len(text.split()))
            hub.publish(s.TurnCompleted(turn_id=turn_id, stop_reason=stop, usage=usage), sid)
            self._touch(hub, info)

    async def _ask(self, hub: Hub, sid: str, text: str) -> bool:
        rid = s.new_id("a")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approvals[rid] = (fut, sid)
        call = ToolCall(id=s.new_id("call"), name="echo", arguments={"text": text})
        hub.publish(s.ApprovalRequested(request_id=rid, summary=f"echo {text!r}", call=call), sid)
        try:
            decision = await fut
        finally:
            self._approvals.pop(rid, None)
        return decision != "deny"

    async def _turn_interrupt(self, b: s.TurnInterrupt, hub: Hub) -> dict[str, Any]:
        self._session(b.session)
        task = self._running.get(b.session)
        if task is not None and not task.done():
            task.cancel()
        return {"interrupted": int(task is not None)}

    async def _approval_respond(self, b: s.ApprovalRespond, hub: Hub) -> dict[str, Any]:
        fut, sid = self._approvals.get(b.request_id, (None, None))
        if fut is None or fut.done():
            raise CommandFailed("not_found", f"no open approval {b.request_id}")
        fut.set_result(b.decision)
        hub.publish(s.ApprovalResolved(request_id=b.request_id, decision=b.decision, by="client"), sid)
        return {}

    # ------------------------------------------------------------ catalogs

    async def _models_list(self, b: s.ModelsList, hub: Hub) -> dict[str, Any]:
        return {"models": [{"id": "echo", "roles": ["main"]}]}

    async def _commands_list(self, b: s.CommandsList, hub: Hub) -> dict[str, Any]:
        return {"commands": []}

    async def _tools_list(self, b: s.ToolsList, hub: Hub) -> dict[str, Any]:
        return {"tools": []}
