"""exo-command dashboard backend, mounted by Hermes at ``/api/plugins/exo-command/``.

GET only. Every handler reads Hermes databases through ``exo_command.ro`` (``mode=ro`` URIs
plus ``query_only``) and the decisions queue as a plain file. There is no write route.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from exo_command.service import SECTIONS, default_centre  # noqa: E402

router = APIRouter()


@router.get("/health")
def health() -> dict:
    c = default_centre()
    return {"ok": True, "read_only": True, "profiles": [p.name for p in c.profiles()],
            "settings": c.settings.public()}


@router.get("/snapshot")
def snapshot(fresh: bool = Query(False), sections: Optional[str] = Query(None)) -> dict:
    only = [s.strip() for s in sections.split(",")] if sections else None
    return default_centre().snapshot(fresh=fresh, only=only)


@router.get("/section/{name}")
def section(name: str, fresh: bool = Query(False)) -> dict:
    if name not in SECTIONS:
        raise HTTPException(status_code=404, detail="unknown section {!r}".format(name))
    return default_centre().section(name, fresh=fresh)
