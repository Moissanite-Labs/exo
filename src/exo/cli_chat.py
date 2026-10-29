"""`exo serve` (run exod) and `exo chat` (line client).

The lead wires these into cli.py with ``register(subparsers, common)``; each
parser sets ``func`` to ``cmd_serve`` or ``cmd_chat``, which take ``(args, store=None)``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import signal
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from exo.core.private import private_file
from exo.ensure_core import exod_socket, home_dir, pid_path, probe
from exo.protocol import schema as s

TOKEN_ENV = "EXOD_TOKEN"


def default_socket() -> Path:
    return exod_socket(home_dir())


def default_token_file() -> Path:
    return default_socket().with_name("exod.token")


def register(sp: Any, common: Any) -> None:
    p = sp.add_parser("serve", parents=[common], help="run exod, the core protocol server")
    p.add_argument("--socket", help="Unix socket path (default: $EXO_HOME/run/exod.sock)")
    p.add_argument("--tcp", metavar="HOST:PORT", help="also listen on TCP (bearer token required)")
    p.add_argument("--token-file", help="token for TCP (default: $EXO_HOME/run/exod.token, created 0600)")
    p.add_argument("--stdio", action="store_true", help="JSON lines on stdin/stdout instead of HTTP")
    p.add_argument("--engine", default=os.environ.get("EXO_ENGINE", "exo.compose:create"),
                   help="module:factory returning an Engine (default the real core), or echo")
    p.add_argument("--seat", help="seat name: loads $EXO_HOME/seats/<name>.toml (see docs/SEATS.md)")
    p.add_argument("--stop", action="store_true", help="stop the background exod that exo web started, then exit")
    p.set_defaults(func=cmd_serve)

    p = sp.add_parser("chat", parents=[common], help="chat with exod from the terminal")
    p.add_argument("--socket", help="Unix socket path (default: $EXO_HOME/run/exod.sock)")
    p.add_argument("--url", help="TCP server URL, e.g. http://127.0.0.1:7341")
    p.add_argument("--token-file", help=f"bearer token file for --url (or ${TOKEN_ENV})")
    p.add_argument("--session", help="session id to continue")
    p.add_argument("--resume", action="store_true", help="continue the most recent session")
    p.add_argument("--title", default="", help="title for a new session")
    p.set_defaults(func=cmd_chat)


# ---------------------------------------------------------------- serve


def _server_token(path: Path) -> str:
    if os.environ.get(TOKEN_ENV):
        return os.environ[TOKEN_ENV]
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token + "\n")
    return token


def cmd_serve(a: Any, store: Any = None) -> int:
    if getattr(a, "stop", False):
        from exo.ensure_core import stop_core
        print(stop_core())
        return 0
    from exo.core.tools.builtin.shell import raise_nofile_limit
    from exo.server.app import SocketBusy, load_engine, serve

    raise_nofile_limit()  # tools inherit this; macOS defaults to 256 open files
    if a.seat:
        os.environ["EXO_SEAT"] = a.seat
    socket = Path(a.socket).expanduser() if a.socket else default_socket()
    if not a.stdio and probe(socket) == "alive":  # never take a live core's socket or pid file
        print(f"exod: another core is listening on {socket}. Stop it first, or give this one its own --socket.",
              file=sys.stderr)
        return 1
    from exo.core.agentlog import WriterBusy
    try:
        engine = load_engine(a.engine)
    except WriterBusy as exc:  # one writer per agent (D-028)
        print(f"exod: {exc}", file=sys.stderr)
        return 1
    if a.stdio:
        asyncio.run(serve(engine, stdio=True))
        return 0
    tcp = token = None
    if a.tcp:
        host, _, port = a.tcp.rpartition(":")
        tcp = (host or "127.0.0.1", int(port))
        token_path = Path(a.token_file).expanduser() if a.token_file else default_token_file()
        token = _server_token(token_path)
        print(f"exod: TCP token in {token_path}", file=sys.stderr)
    pidfile = pid_path(socket.parent.parent) if socket == default_socket() else None

    def claimed() -> None:  # runs with the socket's start lock held: only the core that owns the socket records itself
        if pidfile is not None:
            private_file(pidfile).write_text(f"{os.getpid()}\n")

    async def main() -> None:
        task = asyncio.create_task(serve(engine, socket=socket, tcp=tcp, token=token, claimed=claimed,
                                         ready=lambda w: print(f"exod: listening on {w}", file=sys.stderr)))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(main())
    except SocketBusy as exc:  # a second core on the socket: the start lock, or a live server, names the first
        print(f"exod: {exc}", file=sys.stderr)
        return 1
    finally:
        if pidfile is not None and pidfile.is_file() and pidfile.read_text().strip() == str(os.getpid()):
            pidfile.unlink()  # only our own pid: a core that lost a start race leaves the winner's
    return 0


# ---------------------------------------------------------------- chat


def render(ev: s.Event, write: Callable[[str], None]) -> None:
    """Print one event as plain text. Unknown event types are ignored."""
    b = ev.body
    if isinstance(b, s.MessageDelta):
        write(b.text)
    elif isinstance(b, s.MessageCompleted):
        write("\n")
    elif isinstance(b, s.ToolStarted):
        write(f"[{b.call.name}] {b.call.arguments}\n")
    elif isinstance(b, s.ToolCompleted):
        write(f"[{'error' if b.result.is_error else 'done'}] {b.result.text()[:200]}\n")
    elif isinstance(b, s.ApprovalRequested):
        write(f"approve? {b.summary} ({b.risk}) [{'/'.join(b.options)}]\n")
    elif isinstance(b, s.QuestionAsked):
        write(f"question: {b.text}\n")
    elif isinstance(b, s.TaskUpdated) and b.status in ("running", "done", "failed", "cancelled"):
        write(f"[task {b.status}] {(b.detail or b.title).splitlines()[0]}\n")
    elif isinstance(b, s.Notice):
        write(f"{b.level}: {b.text}\n")
    elif isinstance(b, s.Error):
        write(f"error: {b.code}: {b.message}\n")
    elif isinstance(b, s.TurnCompleted) and b.stop_reason not in ("end", "tool_use"):
        write(f"({b.stop_reason})\n")


async def chat(client: Any, read_line: Callable[[str], Awaitable[str | None]],
               write: Callable[[str], None], session: str | None = None,
               resume: bool = False, title: str = "") -> int:
    """Line chat loop. ``read_line(prompt)`` returns None on EOF."""
    from exo.chat_queue import PromptQueue

    hello = await client.call(s.Hello(client="exo-chat"))
    if resume and not session:
        rows = (await client.call(s.SessionList(limit=1)))["sessions"]
        session = rows[0]["id"] if rows else None
    if session:
        got = await client.call(s.SessionResume(session=session))
        write(f"resumed {session} ({got['session'].get('message_count', 0)} messages)\n")
    else:
        got = await client.call(s.SessionCreate(title=title))
        session = got["session"]["id"]
        write(f"session {session}\n")

    queue, draining = PromptQueue(), False
    queue_allowed = got["session"].get("kind") in ("thread", "private")
    last_stop = ""
    turn_done = asyncio.Event()
    approvals: asyncio.Queue[s.ApprovalRequested] = asyncio.Queue()

    async def follow() -> None:
        nonlocal last_stop
        async for ev in client.events(since=hello["seq"], session=session, reconnect=True):
            if ev.session not in (None, session):
                continue
            render(ev, write)
            if isinstance(ev.body, s.ApprovalRequested):
                approvals.put_nowait(ev.body)
            elif isinstance(ev.body, s.TurnCompleted):
                last_stop = ev.body.stop_reason
                turn_done.set()

    feed = asyncio.create_task(follow())
    try:
        while True:
            queued = draining and bool(queue)
            if queued:
                line = queue.first()
            else:
                draining = False
                if (line := await read_line("> ")) is None:
                    break
            line = line.strip()
            if not line:
                continue
            if not queued and line in ("/quit", "/exit"):
                break
            if not queued and line.startswith("/"):
                name, _, args = line[1:].partition(" ")
                if name == "queue":
                    action, _, text = args.strip().partition(" ")
                    if action in ("", "list"):
                        write(queue.render() + "\n")
                    elif action == "clear" and not text:
                        queue.clear()
                        write("queue cleared\n")
                    elif not queue_allowed:
                        write("queue requires a normal conversation with thread-kind metadata\n")
                    elif action == "add":
                        try:
                            queue.add(text)
                            write(f"queued {len(queue)} prompt(s); /queue run starts them\n")
                        except ValueError as error:
                            write(f"error: {error}\n")
                    elif action == "run" and not text:
                        draining = bool(queue)
                        if not draining:
                            write("queue is empty\n")
                    else:
                        write("usage: /queue [list|add <prompt>|clear|run]\n")
                    continue
                resp = await client.send(s.CommandRun(name=name, args=args, session=session))
                write(f"{resp.result}\n" if resp.ok else f"error: {resp.error.code}: {resp.error.message}\n")
                continue
            turn_done.clear()
            last_stop = ""
            resp = await client.send(s.TurnSend(session=session, text=line, mode="queue" if queued else "send"))
            if not resp.ok:
                draining = False  # a rejected queued prompt stays first; never retry automatically
                write(f"error: {resp.error.code}: {resp.error.message}\n")
                continue
            if queued:
                queue.accepted()
                if resp.result.get("steered"):
                    draining = False
                    write("core reported steering instead of a new turn; queue paused, inspect the conversation\n")
            if not await _wait_turn(client, turn_done, approvals, read_line):
                break
            if draining and last_stop not in ("end", "tool_use"):
                draining = False
                write(f"queue paused after {last_stop or 'unknown completion'}; /queue lists remaining prompts\n")
    finally:
        feed.cancel()
    return 0


async def _wait_turn(client: Any, done: asyncio.Event, approvals: asyncio.Queue,
                     read_line: Callable[[str], Awaitable[str | None]]) -> bool:
    """Wait for the turn to finish, answering approval requests on the way."""
    while not done.is_set():
        waiter = asyncio.create_task(done.wait())
        asker = asyncio.create_task(approvals.get())
        finished, _ = await asyncio.wait({waiter, asker}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if asker not in finished:
            asker.cancel()
            continue
        req, decision = asker.result(), ""
        while not decision:  # a cancelled allow_always asks again
            if (answer := await read_line(f"{'/'.join(req.options)}> ")) is None:
                return False
            a = answer.strip().lower()
            a = "allow" if a in ("a", "y", "yes") else a  # shorthands; otherwise an option as shown, or deny
            decision = a if a in req.options else "deny"
            if decision == "allow_always":  # a standing grant takes a second deliberate act, as in the TUI
                if (confirm := await read_line("allow always? enter confirms, anything else cancels> ")) is None:
                    return False
                decision = "" if confirm.strip() else decision
        await client.send(s.ApprovalRespond(request_id=req.request_id, decision=decision))
    return True


def cmd_chat(a: Any, store: Any = None) -> int:
    from exo.protocol.client import Client

    token = os.environ.get(TOKEN_ENV)
    if a.url and not token:
        path = Path(a.token_file).expanduser() if a.token_file else default_token_file()
        token = path.read_text().strip() if path.exists() else None

    async def read_line(prompt: str) -> str | None:
        try:
            return await asyncio.to_thread(input, prompt)
        except EOFError:
            return None

    def write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    async def main() -> int:
        socket = None if a.url else str(Path(a.socket).expanduser() if a.socket else default_socket())
        async with Client(socket=socket, url=a.url, token=token, principal="chat") as client:
            return await chat(client, read_line, write, a.session, a.resume, a.title)

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # connection refused and friends: one line, not a trace
        print(f"exo chat: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
