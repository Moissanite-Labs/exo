"""FastAPI relay: HTTP API, websocket stream, read-only web view (bodies off by default), optional MCP mount.

Needs the `bus` extra (`pip install 'exo[bus]'`). Wire compatible with the Divan Bus v0.1 relay:
same envelope, endpoints, status codes, X-Divan-* read headers and SQLite schema, so an existing
relay database and existing seats work unchanged.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from nacl.signing import SigningKey

from . import wire
from .client import render_body
from .config import RelayConfig
from .registry import Registry
from .store import PrevMismatch, Store

log = logging.getLogger("exo.bus.relay")
TS_SKEW_MINUTES = 10
MAX_SEQ = 2**63 - 1  # SQLite INTEGER, including the pagination and verification query bindings.


class Relay:
    """Everything the endpoints share: config, store, registry, replay cache, live subscribers."""

    def __init__(self, config: RelayConfig):
        self.config = config
        os.makedirs(os.path.dirname(os.path.abspath(config.db)), exist_ok=True)
        self.store = Store(config.db)
        self.registry = Registry(config.registry, config.leader_pubkey or None)
        self.replay = wire.ReplayCache()
        self.keys_dir = str(config.keys_dir) if config.keys_dir else None
        self.relay_key = wire.load_signing_key(config.relay_key) if config.relay_key else None
        self.relay_pubkey = wire.pubkey_b64(self.relay_key) if self.relay_key else None
        self.subscribers: dict[str, set[asyncio.Queue]] = {}

    # --- auth -----------------------------------------------------------------
    def signer_keys(self, seat: str, env: dict | None = None) -> list[str]:
        """Keys a message from `seat` may be signed with: the seat's own, and the relay's only on an envelope the
        relay marked as composed for that seat (an MCP post, D-040). The relay key never signs as a seat."""
        relay = self.relay_pubkey if env is not None and wire.relay_composed(env) else None
        return [k for k in (self.registry.pubkey(seat), relay) if k]

    def authed_seat(self, headers, method: str, path: str) -> str:
        try:
            return wire.verify_request(headers, method, path, self.registry.pubkey, self.replay)
        except ValueError as e:
            raise HTTPException(401, str(e)) from None

    # --- accepting envelopes --------------------------------------------------
    def check_envelope(self, env: dict) -> dict:
        if not isinstance(env, dict) or set(env) != wire.ENVELOPE_KEYS or type(env["v"]) is not int or env["v"] != 1:
            raise HTTPException(422, "envelope must have exactly the v1 fields")
        # The log stores v as 1 and these fields as TEXT: any other type would be signed in one form and stored in
        # another (True or 1.0 for v, 7 for "7"), and /v1/verify would fail at that row for good.
        texts = ("id", "ts", "from", "kind", "body", "prev", "sig")
        if not all(isinstance(env[k], str) for k in texts) or not isinstance(env["thread"], (str, type(None))):
            raise HTTPException(422, "envelope fields have the wrong types")
        if self.registry.seat(env["from"]) is None:
            raise HTTPException(403, f"unknown seat {env['from']!r}")
        if not any(wire.verify_doc(env, k) for k in self.signer_keys(env["from"], env)):
            raise HTTPException(401, "envelope signature does not verify")
        if wire.envelope_id(env) != env["id"]:
            raise HTTPException(422, "id does not match envelope content")
        if not wire.ts_within(env["ts"], TS_SKEW_MINUTES):
            raise HTTPException(422, "ts outside the 10-minute window")
        if env["kind"] not in wire.KINDS:
            raise HTTPException(422, f"unknown kind {env['kind']!r}")
        if not isinstance(env["to"], list) or not env["to"] or not all(
                isinstance(t, str) and self.registry.valid_recipient(t) for t in env["to"]):
            raise HTTPException(422, "to must list known seats, team:<team>, or group")
        if not isinstance(env["attach"], list):
            raise HTTPException(422, "attach must be a list")
        if env["enc"] is not None and (not isinstance(env["enc"], dict) or env["enc"].get("alg") != wire.ENC_ALG
                                       or self.registry.seat(env["enc"].get("to", "")) is None):
            raise HTTPException(422, "enc must name a known seat and the supported alg")
        return env

    async def accept(self, env: dict) -> dict:
        env = self.check_envelope(env)
        try:
            seq, head = self.store.append(env)
        except PrevMismatch as e:
            raise HTTPException(409, {"head": e.head, "seq": e.seq}) from None
        self.publish({**env, "seq": seq, "chain": head})
        return {"seq": seq, "id": env["id"], "head": head}

    def publish(self, env: dict) -> None:
        for seat, queues in self.subscribers.items():
            if set(env["to"]) & set(self.registry.audiences(seat)):
                for q in queues:
                    q.put_nowait(env)

    def seat_key(self, seat: str) -> SigningKey | None:
        """The key used for an MCP caller: the seat's own key file if the relay holds it, else the relay key."""
        if self.keys_dir:
            path = os.path.join(self.keys_dir, f"{seat}.key")
            if os.path.exists(path):
                return wire.load_signing_key(path)
        return self.relay_key

    async def compose_and_post(self, seat: str, to, body: str, kind="msg", thread=None, enc_to=None) -> dict:
        key = self.seat_key(seat)
        if key is None:
            raise HTTPException(503, "relay has no signing key; post a signed envelope over HTTP instead")
        enc = None
        if enc_to:
            pub = self.registry.pubkey(enc_to)
            if not pub:
                raise HTTPException(422, f"unknown seat {enc_to!r}")
            body, enc = wire.encrypt_body(body, key, enc_to, pub)
        attach = [wire.relay_mark(seat)] if key is self.relay_key else []  # D-040: say who really signed
        for _ in range(5):
            env = wire.make_envelope(seat, to, body, self.store.head()[1], kind, thread, enc, attach)
            try:
                return await self.accept(wire.finish_envelope(env, key))
            except HTTPException as e:
                if e.status_code != 409:
                    raise
        raise HTTPException(409, "could not win the head after 5 tries")


def _load_mcp(relay: Relay):
    """The MCP server, if the `mcp` package (v2) is installed and enabled. Otherwise None."""
    if not relay.config.mcp:
        return None
    try:
        from . import mcp_server as bus_mcp
    except ImportError:
        log.info("mcp package not installed; /mcp not mounted")
        return None
    return bus_mcp


def create_app(config: RelayConfig | None = None) -> FastAPI:
    config = config or RelayConfig.from_env()
    relay = Relay(config)
    bus_mcp = _load_mcp(relay)
    mcp = mcp_app = None
    if bus_mcp:
        mcp = bus_mcp.build(relay)
        mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True,
                                          transport_security=bus_mcp.NO_HOST_CHECK)

    @asynccontextmanager
    async def lifespan(app):
        try:  # SIGHUP reloads the registry; only possible when the loop runs on the main thread
            asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, _reload, relay)
        except (ValueError, NotImplementedError, RuntimeError):
            pass
        if mcp is not None:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(title=config.title, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.relay = relay
    if mcp_app is not None:
        app.add_route("/mcp", bus_mcp.AuthGate(mcp_app, relay), methods=["GET", "POST", "DELETE"])

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc):
        body = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
        return JSONResponse(body, status_code=exc.status_code)

    def auth(request: Request) -> str:
        return relay.authed_seat(request.headers, request.method, request.url.path)

    @app.post("/v1/messages", status_code=201)
    async def post_message(request: Request):
        return await relay.accept(await request.json())

    @app.get("/v1/messages")
    async def get_messages(request: Request, since: int = Query(0, ge=0, le=MAX_SEQ), to: str | None = None,
                           thread: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        auth(request)
        return relay.store.messages(since, to, thread, limit)

    @app.get("/v1/inbox/{seat}")
    async def inbox(request: Request, seat: str, since: int = Query(0, ge=0, le=MAX_SEQ),
                    limit: int = Query(200, ge=1, le=1000)):
        if auth(request) != seat:
            raise HTTPException(403, "you can only read your own inbox")
        return relay.store.inbox(relay.registry.audiences(seat), since, limit)

    @app.get("/v1/head")
    async def head():
        seq, h = relay.store.head()
        return {"seq": seq, "head": h, "relay_pubkey": relay.relay_pubkey}

    @app.get("/v1/registry")
    async def registry():
        return relay.registry.document

    @app.get("/v1/verify")
    async def verify(from_seq: int = Query(1, alias="from", ge=1, le=MAX_SEQ),
                     to_seq: int | None = Query(None, alias="to", ge=0, le=MAX_SEQ)):
        return relay.store.verify(from_seq, to_seq, relay.signer_keys)

    @app.get("/v1/checkpoints")
    async def checkpoints():
        return relay.store.checkpoints()

    @app.websocket("/v1/stream")
    async def stream(ws: WebSocket, seat: str):
        try:
            who = relay.authed_seat(ws.headers, "GET", ws.url.path)
        except HTTPException as e:
            await ws.close(code=4401, reason=str(e.detail))
            return
        if who != seat:
            await ws.close(code=4403, reason="seat mismatch")
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue()
        relay.subscribers.setdefault(seat, set()).add(q)
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            relay.subscribers[seat].discard(q)

    @app.get("/", response_class=HTMLResponse)
    async def web_view():
        return render_view(relay)

    return app


def _reload(relay: Relay) -> None:
    try:
        relay.registry.reload()
    except Exception as e:  # keep the old registry on a bad file
        log.error("registry reload failed: %s", e)


def render_view(relay: Relay) -> str:
    """Read-only HTML of recent threads, served without auth. It shows seq, sender, recipients, kind, time and thread;
    plaintext bodies only when the operator sets web_bodies (EXO_BUS_WEB_BODIES=1), since the API gives them only to
    signed seats. Encrypted bodies always stay hidden; SYM-2P renders as plain English."""
    show = relay.config.web_bodies
    seq, head = relay.store.head()
    title = html.escape(relay.config.title)
    threads: dict[str, list[dict]] = {}
    for env in reversed(relay.store.recent(200)):
        threads.setdefault(env["thread"] or env["id"][:12], []).append(env)
    out = [f"<!doctype html><meta charset=utf-8><title>{title}</title><style>{CSS}</style>",
           f"<h1>{title}</h1><p class=meta>seq {seq} · head <code>{head[:16]}…</code> · "
           f"{len(relay.registry.seats)} seats · read-only{'' if show else ' · bodies hidden'}</p>"]
    for tid, msgs in reversed(list(threads.items())):
        out.append(f"<section><h2>thread <code>{html.escape(tid)}</code></h2>")
        for m in msgs:
            body = (f"<i>encrypted to {html.escape(m['enc']['to'])}</i>" if m["enc"]
                    else html.escape(render_body(m["body"])) if show else "<i>body hidden</i>")
            out.append(f"<article><div class=meta>#{m['seq']} <b>{html.escape(m['from'])}</b> → "
                       f"{html.escape(', '.join(m['to']))} · {m['kind']} · {m['ts'][:19]}Z</div>"
                       f"<pre>{body}</pre></article>")
        out.append("</section>")
    return "\n".join(out)


CSS = ("body{font:15px/1.4 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#222}"
       "section{border-top:1px solid #ddd;padding:.5rem 0}article{margin:.5rem 0 .5rem 1rem}"
       ".meta{color:#666;font-size:.85em}pre{white-space:pre-wrap;margin:.2rem 0;font-family:inherit}")


def serve(config: RelayConfig | None = None) -> None:
    import uvicorn
    config = config or RelayConfig.from_env()
    host, _, port = config.bind.rpartition(":")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(config), host=host or "127.0.0.1", port=int(port), log_level="info")
