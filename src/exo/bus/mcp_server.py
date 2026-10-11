"""MCP tools over Streamable HTTP, mounted at /mcp behind the same signed-header auth. Needs `mcp` v2."""
from __future__ import annotations

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

# Seats may reach the relay through tunnels or VPNs with arbitrary Host headers; signatures are the access control.
NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)


class AuthGate:
    """ASGI wrapper: every HTTP request to /mcp must carry valid, fresh X-Divan-* headers."""

    def __init__(self, app, relay):
        self.app, self.relay = app, relay

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            try:
                self.relay.authed_seat(Headers(scope=scope), scope["method"], scope["path"])
            except HTTPException as e:
                await JSONResponse({"error": e.detail}, status_code=e.status_code)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build(relay) -> MCPServer:
    mcp = MCPServer("exo-bus", instructions="Signed, hash-chained message bus between agent seats.")

    def seat_of(ctx: Context) -> str:
        return ctx.headers["x-divan-seat"]  # verified by AuthGate before the tool runs

    @mcp.tool()
    def whoami(ctx: Context) -> dict:
        """The seat this session is authenticated as, its team, and the audiences that reach it."""
        seat = seat_of(ctx)
        return {"seat": seat, **relay.registry.seat(seat), "audiences": relay.registry.audiences(seat)}

    @mcp.tool()
    async def post_message(ctx: Context, to: list[str] | str, body: str, kind: str = "msg",
                           thread: str | None = None, enc_to: str | None = None) -> dict:
        """Post a message. `to` holds seat ids, team:<name>, or group. enc_to encrypts the body to one seat."""
        return await guarded(relay.compose_and_post(seat_of(ctx), to, body, kind, thread, enc_to))

    @mcp.tool()
    def read_inbox(ctx: Context, since: int = 0, limit: int = 50) -> list[dict]:
        """Messages addressed to me, my team, or the group, with seq greater than `since`."""
        return relay.store.inbox(relay.registry.audiences(seat_of(ctx)), since, limit)

    @mcp.tool()
    def read_thread(thread: str) -> list[dict]:
        """All messages in a thread (the thread id is usually the first message's id)."""
        return relay.store.thread(thread)

    @mcp.tool()
    def search(query: str, limit: int = 50) -> list[dict]:
        """Substring search over plaintext bodies, thread ids, and senders. Newest first."""
        return relay.store.search(query, limit)

    @mcp.tool()
    def head() -> dict:
        """Current log length and chain head."""
        seq, h = relay.store.head()
        return {"seq": seq, "head": h}

    @mcp.tool()
    def verify_ledger(from_seq: int = 1, to_seq: int | None = None) -> dict:
        """Recompute ids, signatures and chain links over a segment of the log."""
        return relay.store.verify(from_seq, to_seq, relay.signer_keys)

    @mcp.tool()
    def list_seats() -> list[dict]:
        """The registry: every seat, its team, platform and role."""
        return [{k: s[k] for k in ("seat", "team", "platform", "role", "since")} for s in relay.registry.seats]

    @mcp.tool()
    async def post_claim(ctx: Context, task_id: str, note: str = "") -> dict:
        """Claim a task for myself: a kind=claim message to the group in thread task_id."""
        return await guarded(relay.compose_and_post(seat_of(ctx), "group", note, "claim", task_id))

    @mcp.tool()
    def list_claims(open: bool = True) -> list[dict]:
        """Claims; open ones are those without a later report from the claimant in the same thread."""
        return relay.store.claims(open)

    return mcp


async def guarded(coro):
    try:
        return await coro
    except HTTPException as e:
        raise ValueError(f"{e.status_code}: {e.detail}") from None
