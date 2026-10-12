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

from exo.protocol import schema as s

TOKEN_ENV = "EXOD_TOKEN"


def run_dir() -> Path:
    return Path(os.environ.get("EXO_HOME") or Path.home() / ".exo").expanduser() / "run"


def default_socket() -> Path:
    return run_dir() / "exod.sock"


def default_token_file() -> Path:
    return run_dir() / "exod.token"


def register(sp: Any, common: Any) -> None:
    p = sp.add_parser("serve", parents=[common], help="run exod, the core protocol server")
    p.add_argument("--socket", help="Unix socket path (default: $EXO_HOME/run/exod.sock)")
    p.add_argument("--tcp", metavar="HOST:PORT", help="also listen on TCP (bearer token required)")
    p.add_argument("--token-file", help="token for TCP (default: $EXO_HOME/run/exod.token, created 0600)")
    p.add_argument("--stdio", action="store_true", help="JSON lines on stdin/stdout instead of HTTP")
    p.add_argument("--engine", default=os.environ.get("EXO_ENGINE", "exo.core.engine:create"),
                   help="module:factory returning an Engine (default the real core), or echo")
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
    from exo.server.app import load_engine, serve

    engine = load_engine(a.engine)
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
    socket = Path(a.socket).expanduser() if a.socket else default_socket()
    async def main() -> None:
        task = asyncio.create_task(serve(engine, socket=socket, tcp=tcp, token=token,
                                         ready=lambda w: print(f"exod: listening on {w}", file=sys.stderr)))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
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
    hello = await client.call(s.Hello(client="exo-chat"))
    if resume and not session:
        rows = (await client.call(s.SessionList(limit=1)))["sessions"]
        session = rows[0]["id"] if rows else None
    if session:
        got = await client.call(s.SessionResume(session=session))
        write(f"resumed {session} ({got['session'].get('message_count', 0)} messages)\n")
    else:
        session = (await client.call(s.SessionCreate(title=title)))["session"]["id"]
        write(f"session {session}\n")

    turn_done = asyncio.Event()
    approvals: asyncio.Queue[s.ApprovalRequested] = asyncio.Queue()

    async def follow() -> None:
        async for ev in client.events(since=hello["seq"], session=session, reconnect=True):
            if ev.session not in (None, session):
                continue
            render(ev, write)
            if isinstance(ev.body, s.ApprovalRequested):
                approvals.put_nowait(ev.body)
            elif isinstance(ev.body, s.TurnCompleted):
                turn_done.set()

    feed = asyncio.create_task(follow())
    try:
        while (line := await read_line("> ")) is not None:
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line.startswith("/"):
                name, _, args = line[1:].partition(" ")
                resp = await client.send(s.CommandRun(name=name, args=args, session=session))
                write(f"{resp.result}\n" if resp.ok else f"error: {resp.error.code}: {resp.error.message}\n")
                continue
            turn_done.clear()
            resp = await client.send(s.TurnSend(session=session, text=line))
            if not resp.ok:
                write(f"error: {resp.error.code}: {resp.error.message}\n")
                continue
            if not await _wait_turn(client, turn_done, approvals, read_line):
                break
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
        req = asker.result()
        answer = await read_line(f"{'/'.join(req.options)}> ")
        if answer is None:
            return False
        decision = {"a": "allow", "y": "allow", "d": "deny", "n": "deny"}.get(answer.strip()[:1], answer.strip())
        if decision not in ("allow", "deny", "allow_always"):
            decision = "deny"
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
        async with Client(socket=socket, url=a.url, token=token) as client:
            return await chat(client, read_line, write, a.session, a.resume, a.title)

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # connection refused and friends: one line, not a trace
        print(f"exo chat: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
