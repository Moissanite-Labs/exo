"""Peers for Hermes (D-026): a Hermes session that other agent sessions, Exo or Hermes, can message.

At load, when enabled (``EXO_PEERS=1`` or ``peers.enabled`` in the plugin settings), this starts a daemon
thread that runs the same ``PeerNode`` exod runs: a unix socket speaking the ``peers`` protocol command and
a presence file in the shared peers directory (seat = the Hermes profile name, platform ``hermes``). Hooks
keep the session id and busy or idle state. Incoming messages go through the same ingress path (policy,
hold, deliver, receipt) with a Hermes target:

- running CLI turn: ``agent.steer(text)``. It lands after the current tool batch and never interrupts.
  ``ctx.inject_message`` is NOT used mid-turn in the CLI: it puts the text on the interrupt queue, which
  the CLI treats as an interrupt.
- idle: queued, and handed to the model as context on its next turn (``pre_llm_call``). A peer starts a
  turn only when wake is on (``ctx.inject_message``; in the gateway that also needs
  ``plugins.entries.exo-bus.allow_gateway_injection: true`` and, being a queued event, it becomes a new turn
  after the running one).
- held: shown to the human; ``/peer-release <id>`` or ``/peer-deny <id>`` answers. A held message never
  reaches the model.

Tools ``peers`` and ``peer_send``, commands ``/peers``, ``/peer-release``, ``/peer-deny``, and an etiquette
skill are registered with the plugin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from exo import crypto
from exo.core.ingress import STANDING_LINE, Held, Ingress, Receipt, Target, frame
from exo.core.peer_target import describe
from exo.core.peers import BusLink, Directory, PeerConfig, PeerNode, peers_dir
from exo.core.seats import ensure_key, key_path
from exo.protocol import schema as s
from exo.server.app import Dispatcher, HttpServer
from exo.server.engine import CommandFailed, Hub

logger = logging.getLogger(__name__)
TOOLSET = "exo-peers"
SKILL = Path(__file__).resolve().parents[1] / "skills" / "peers" / "SKILL.md"
LIVE_HOURS = 12


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def seat_name(home: str | os.PathLike | None = None) -> str:
    """The Hermes profile name (``<home>/profiles/<name>``), or ``hermes`` for the default home."""
    if os.environ.get("EXO_PEERS_SEAT"):
        return os.environ["EXO_PEERS_SEAT"]
    try:
        from hermes_constants import get_hermes_home
        home = home or get_hermes_home()
    except Exception:
        home = home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes"
    p = Path(home)
    return p.name if p.parent.name == "profiles" else "hermes"


class Host:
    """What the plugin needs from Hermes. A fake in tests; ``CtxHost`` in a real process."""

    def cli_agent(self) -> Any:  # the running CLI agent, or None (gateway, no CLI)
        return None

    def gateway_agent(self, session_id: str) -> Any:  # the gateway's running agent for a session, or None
        return None

    def inject(self, text: str, session_id: str) -> bool:  # start a turn with this text; False if the host refuses
        return False

    def show(self, text: str) -> None:  # tell the human
        sys.stderr.write(text + "\n")


class CtxHost(Host):
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.gateway: Any = None  # set by the pre_gateway_dispatch hook

    def cli_agent(self) -> Any:
        return getattr(getattr(self.ctx._manager, "_cli_ref", None), "agent", None)

    def _key(self, session_id: str) -> str | None:
        """The gateway's durable session key for an agent session id. Private gateway state: best effort."""
        gw = self.gateway
        try:
            for key, entry in list(gw.session_store._entries.items()):
                if getattr(entry, "session_id", None) == session_id:
                    return key
        except Exception:
            pass
        return None

    def gateway_agent(self, session_id: str) -> Any:
        key = self._key(session_id)
        try:
            st = self.gateway._peek_session_state(key) if key else None
            agent = st.turn.agent if st else None
            return agent if hasattr(agent, "steer") else None
        except Exception:
            return None

    def inject(self, text: str, session_id: str) -> bool:
        try:
            if getattr(self.ctx._manager, "_cli_ref", None) is not None:
                return bool(self.ctx.inject_message(text))
            key = self._key(session_id)
            return bool(key and self.ctx.inject_message(text, session_key=key))
        except Exception:
            logger.warning("exo-peers: inject_message failed", exc_info=True)
            return False

    def show(self, text: str) -> None:
        try:
            from cli import _cprint  # the CLI's own printer, safe beside prompt_toolkit
            _cprint(text)
        except Exception:
            sys.stderr.write(text + "\n")


class HermesTarget(Target):
    def __init__(self, seat: str, host: Host, wake: bool | None) -> None:
        self.seat = seat
        self.host = host
        self.wake_cfg = wake
        self.node: PeerNode | None = None
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.current = ""
        self.pending: dict[str, list[str]] = {}  # session -> framed messages for its next turn
        self.notes: list[str] = []  # receipts for messages this session sent
        self.log: deque[str] = deque(maxlen=50)

    # -- hook feed
    def touch(self, sid: str, state: str | None = None, platform: str = "") -> None:
        if not sid:
            return
        with self.lock:
            row = self.sessions.setdefault(sid, {"id": sid, "title": f"hermes {platform or 'session'}", "state": "idle",
                                                 "started": crypto.now_ts(), "cwd": os.getcwd(), "seen": 0.0})
            row["seen"] = time.time()
            if state:
                row["state"] = state
            self.current = sid

    def drop(self, sid: str) -> None:
        with self.lock:
            self.sessions.pop(sid, None)
            self.pending.pop(sid, None)

    def live(self) -> list[dict[str, Any]]:
        cutoff = time.time() - LIVE_HOURS * 3600
        with self.lock:
            rows = [{k: v for k, v in r.items() if k != "seen"} for r in self.sessions.values() if r["seen"] >= cutoff
                    or r["state"] == "running"]
        return sorted(rows, key=lambda r: r["state"] != "running")

    def take_context(self, sid: str) -> str:
        with self.lock:
            msgs, notes = self.pending.pop(sid, []), self.notes
            self.notes = []
        parts = list(msgs) + [f"[peer receipt] {n}" for n in notes]
        return "\n\n".join(parts)

    # -- Target
    def pick_session(self, ing: Ingress) -> str | None:
        with self.lock:
            if ing.session and ing.session in self.sessions:
                return ing.session
            return self.current if self.current in self.sessions else None

    def state(self, session: str) -> str:
        with self.lock:
            return self.sessions.get(session, {}).get("state", "idle")

    def can_wake(self, session: str) -> bool:
        if self.wake_cfg is not None:
            return bool(self.wake_cfg)
        return False  # a Hermes session someone is typing in does not wake on a peer message

    async def steer(self, session: str, ing: Ingress) -> bool:
        agent = self.host.cli_agent() or self.host.gateway_agent(session)
        if agent is None:
            return False
        try:
            return bool(agent.steer(frame(ing)))
        except Exception:
            logger.warning("exo-peers: agent.steer failed", exc_info=True)
            return False

    async def queue(self, session: str, ing: Ingress) -> None:
        with self.lock:
            self.pending.setdefault(session, []).append(frame(ing))

    async def wake(self, session: str, ing: Ingress) -> bool:
        return self.host.inject(frame(ing), session)

    async def on_held(self, held: Held) -> None:
        ing = held.ing
        text = ing.body.strip().replace("\n", " ")
        self.host.show(f"[peers] message from {ing.sender} ({ing.trust}) is held for you: \"{text[:300]}\". "
                       f"/peer-release {ing.id} delivers it, /peer-deny {ing.id} drops it. It expires on its own.")

    async def record(self, ing: Ingress, receipt: Receipt, notify: bool) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')} {ing.sender} -> {receipt.state}: {receipt.detail}")
        logger.info("exo-peers %s from %s: %s", receipt.state, ing.sender, receipt.detail)
        if notify and self.node is not None:
            await self.node.notify_sender(ing, receipt)

    async def notice(self, session: str | None, text: str) -> None:
        self.host.show(f"[peers] {text}")

    def add_receipt(self, session: str | None, body: Any) -> None:
        with self.lock:
            self.notes.append(f"your message {body.msg_id} to {body.peer} is now {body.state}"
                              + (f" ({body.detail})" if body.detail else ""))


class _Engine:
    """The one command a Hermes peer answers: ``peers``."""

    def __init__(self, rt: "Runtime") -> None:
        self.rt = rt

    async def handle(self, command: s.Command, hub: Hub) -> dict[str, Any]:
        b = command.body
        if not isinstance(b, s.Peers):
            raise CommandFailed("unsupported", f"{command.type} is not supported by a Hermes peer")
        node = self.rt.node
        try:
            if b.action == "list":
                return await node.list(b.session, b.local_only)
            if b.action == "send":
                return await node.send(b.to, b.text, b.session or self.rt.target.current, b.via)
            if b.action == "deliver":
                return await node.deliver(b.envelope)
            return await node.receipt(b.envelope)
        except ValueError as exc:
            raise CommandFailed("bad_request", str(exc)) from None


class Runtime:
    """The listener thread: its own event loop, the socket server and the node."""

    def __init__(self, seat: str, host: Host, cfg: PeerConfig, directory: Directory, key_file: Path,
                 principal: str = "", bus: BusLink | None = None) -> None:
        self.seat, self.cfg, self.directory = seat, cfg, directory
        self.target = HermesTarget(seat, host, cfg.wake)
        self.key_file, self.principal, self.bus = key_file, principal, bus
        self.node: PeerNode | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: HttpServer | None = None
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._main, name="exo-peers", daemon=True)

    def start(self, timeout: float = 8.0) -> bool:
        self.thread.start()
        self.ready.wait(timeout)
        return self.node is not None and self.error is None

    def _main(self) -> None:
        loop = self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._boot())
        except BaseException as exc:  # never take Hermes down with a peers failure
            self.error = exc
            logger.warning("exo-peers: could not start: %s", exc)
            self.ready.set()
            return
        self.ready.set()
        loop.run_forever()

    async def _boot(self) -> None:
        sock = str(self.directory.path / f"{self.seat}-{os.getpid()}.sock")
        self.directory.path.mkdir(parents=True, exist_ok=True)
        self.server = HttpServer(Dispatcher(_Engine(self), Hub()))
        await self.server.start_unix(sock)
        signer = crypto.Signer.from_file(self.seat, ensure_key(self.key_file))
        self.node = PeerNode(seat=self.seat, signer=signer, target=self.target, sessions=self.target.live, socket=sock,
                             platform="hermes", principal=self.principal, cwd=os.getcwd(), config=self.cfg,
                             bus=self.bus, directory=self.directory, emit_out=self.target.add_receipt)
        self.target.node = self.node
        self.node.start()

    def call(self, coro: Any, timeout: float = 15.0) -> Any:
        if self.loop is None or self.node is None:
            coro.close()
            raise RuntimeError("peers are not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        if self.node is not None:
            self.node.stop()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)


# ---------------------------------------------------------------- tools, commands, hooks

_RUNTIMES: dict[str, Runtime] = {}  # one per seat per process: Hermes may load a plugin more than once


def _json(**d: Any) -> str:
    return json.dumps(d, ensure_ascii=False)


def make_tools(rt: Runtime) -> dict[str, tuple]:
    def peers(args: dict | None = None, **_: Any) -> str:
        try:
            res = rt.call(rt.node.list(rt.target.current))
        except Exception as exc:
            return _json(ok=False, error=str(exc))
        return _json(ok=True, me=res["me"], peers=res["peers"], held_for_you=res["held"])

    def peer_send(args: dict, **_: Any) -> str:
        try:
            r = rt.call(rt.node.send(str(args.get("to") or ""), str(args.get("text") or ""), rt.target.current, "agent"))
        except ValueError as exc:
            return _json(ok=False, error=str(exc))
        except Exception as exc:
            return _json(ok=False, error=f"peer send failed: {exc}")
        return _json(ok=r["state"] in ("delivered",), state=r["state"], message=describe(r), msg_id=r["msg_id"])

    return {
        "peers": (peers, "List the other live agent sessions you can message (Exo and Hermes, and bus seats), with "
                         "their state. Use a name with peer_send.", {}, []),
        "peer_send": (peer_send, "Send a short message to another live agent session by name. The receiving side's "
                                 "rules decide whether it is delivered, held for its principal, or refused. If the "
                                 "result says held, it was NOT delivered: do not say it was and do not resend. You "
                                 "cannot approve anything for a peer.",
                      {"to": {"type": "string", "description": "A name from `peers`."},
                       "text": {"type": "string", "description": "The message, in plain words."}}, ["to", "text"]),
    }


def command_handlers(rt: Runtime) -> dict[str, Any]:
    def peers_cmd(raw: str = "") -> str:
        try:
            res = rt.call(rt.node.list(rt.target.current))
        except Exception as exc:
            return f"peers unavailable: {exc}"
        lines = [f"you are {res['me']}; {len(res['peers'])} peer(s)"]
        lines += [f"  {r['name']}  [{r['platform']}, {r['state']}]  {r.get('cwd') or ''}" for r in res["peers"]]
        lines += [f"  held for you: {h['msg_id']} from {h['from']} ({h['expires_in_s']}s left)" for h in res["held"]]
        lines += [f"  {x}" for x in list(rt.target.log)[-5:]]
        return "\n".join(lines)

    def answer(approved: bool) -> Any:
        def run(raw: str = "") -> str:
            mid = (raw or "").strip()
            if not mid:
                return "usage: /peer-release <id> or /peer-deny <id>; /peers lists what is held"
            ok = rt.call(_resolve(rt, mid, approved))
            return ("released" if approved else "denied") + f" {mid}" if ok else f"{mid} is not held (already closed?)"
        return run

    return {"peers": peers_cmd, "peer-release": answer(True), "peer-deny": answer(False)}


async def _resolve(rt: Runtime, mid: str, approved: bool) -> bool:
    return rt.node.path.resolve(mid, approved)


def make_hooks(rt: Runtime, host: Host) -> dict[str, Any]:
    t = rt.target

    def on_session_start(session_id: str = "", platform: str = "", **_: Any) -> None:
        t.touch(session_id, "idle", platform)

    def pre_llm_call(session_id: str = "", platform: str = "", is_first_turn: bool = False, **_: Any) -> dict | None:
        t.touch(session_id, "running", platform)
        parts = [STANDING_LINE] if is_first_turn else []
        if extra := t.take_context(session_id):
            parts.append(extra)
        return {"context": "\n\n".join(parts)} if parts else None

    def post_llm_call(session_id: str = "", **_: Any) -> None:
        t.touch(session_id, "idle")

    def on_session_end(session_id: str = "", **_: Any) -> None:
        t.touch(session_id, "idle")  # fires at the end of a turn, not only when the session closes

    def on_session_finalize(session_id: str = "", **_: Any) -> None:
        t.drop(session_id)

    def pre_gateway_dispatch(gateway: Any = None, **_: Any) -> None:
        if gateway is not None and isinstance(host, CtxHost):
            host.gateway = gateway
        return None

    return {"on_session_start": on_session_start, "pre_llm_call": pre_llm_call, "post_llm_call": post_llm_call,
            "on_session_end": on_session_end, "on_session_finalize": on_session_finalize,
            "pre_gateway_dispatch": pre_gateway_dispatch}


def enabled(ctx: Any) -> tuple[bool, dict]:
    cfg: dict = {}
    try:
        cfg = ctx.get_config("peers", {}) or {}
    except Exception:
        pass
    cfg = cfg if isinstance(cfg, dict) else {}
    on = os.environ.get("EXO_PEERS")
    return (_truthy(on) if on not in (None, "") else cfg.get("enabled") is True), cfg


def register_peers(ctx: Any, host: Host | None = None) -> Runtime | None:
    """Start peers for this Hermes profile when enabled. Returns the runtime (None when off or failed)."""
    on, raw = enabled(ctx)
    if not on:
        return None
    seat = seat_name()
    if seat in _RUNTIMES:
        rt = _RUNTIMES[seat]
    else:
        host = host or CtxHost(ctx)
        exo_home = Path(os.path.expanduser(os.environ.get("EXO_HOME", "~/.exo")))
        rt = Runtime(seat, host, PeerConfig.of(raw), Directory(peers_dir()), key_path(exo_home, seat),
                     principal=os.environ.get("EXO_PRINCIPAL", ""), bus=BusLink.from_env())
        if not rt.start():
            logger.warning("exo-peers: not running (%s)", rt.error)
            return None
        _RUNTIMES[seat] = rt
    for name, (handler, description, props, required) in make_tools(rt).items():
        params: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            params["required"] = required
        ctx.register_tool(name=name, toolset=TOOLSET, handler=handler, description=description,
                          schema={"name": name, "description": description, "parameters": params}, emoji="\U0001f4ac")
    for name, fn in make_hooks(rt, host or rt.target.host).items():
        ctx.register_hook(name, fn)
    descs = {"peers": "list live agent sessions you can message", "peer-release": "deliver a held peer message: <id>",
             "peer-deny": "drop a held peer message: <id>"}
    for name, fn in command_handlers(rt).items():
        ctx.register_command(name, fn, description=descs[name], args_hint="<id>" if name != "peers" else "")
    if SKILL.is_file() and hasattr(ctx, "register_skill"):
        try:
            ctx.register_skill("peers", SKILL, description="How to message other agent sessions and treat their messages")
        except Exception:
            logger.warning("exo-peers: could not register the skill", exc_info=True)
    return rt
