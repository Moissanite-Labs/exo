"""Optional ASGI app, for people who would rather mount the command centre in their own server.

Needs ``fastapi`` (not a dependency of the plugin: ``server.py`` serves the same data with the
standard library). The routes match ``server.py``: ``/api/health``, ``/api/snapshot``,
``/api/section/<name>`` and ``POST /api/approvals/<id>``. A lifespan task keeps the state current.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from .service import SECTIONS, CommandCentre


def build_app(centre: Optional[CommandCentre] = None):
    from fastapi import FastAPI, HTTPException, Query

    c = centre or CommandCentre()

    @asynccontextmanager
    async def lifespan(app):
        if c.source is None:
            c.connect()
        await c.bootstrap()
        task = asyncio.create_task(c.follow())
        try:
            yield
        finally:
            task.cancel()
            await c.aclose()

    app = FastAPI(title="exo-command", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "connected": c.connected, "seq": c.state.seq, "settings": c.settings.public()}

    @app.get("/api/snapshot")
    def snapshot(fresh: bool = Query(False), sections: Optional[str] = Query(None)) -> dict:
        only = [x.strip() for x in sections.split(",")] if sections else None
        return c.snapshot(fresh=fresh, only=only)

    @app.get("/api/section/{name}")
    def section(name: str, fresh: bool = Query(False)) -> dict:
        if name not in SECTIONS:
            raise HTTPException(status_code=404, detail="unknown section {!r}".format(name))
        return c.section(name, fresh=fresh)

    @app.post("/api/approvals/{request_id}")
    async def approve(request_id: str, body: dict) -> dict:
        if body.get("decision") not in ("allow", "deny", "allow_always"):
            raise HTTPException(status_code=400, detail="bad decision")
        await c.respond_approval(request_id, body["decision"], str(body.get("note") or ""))
        return {"ok": True}

    return app
