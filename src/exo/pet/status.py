"""Verified, read-only pet projection. Only bounded summaries cross the command boundary."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from exo import provenance
from exo.keyring import KeyRegistry
from exo.ledger import Ledger, missing_ledger_problem
from exo.ledger_authority.factory import open_authority
from exo.ledger_authority.verify import capture_authority
from exo.learn.runtime import resolve_store
from exo.principal import require_principal
from exo.protocol import schema as s
from exo.server.engine import CommandFailed
from .scoring import LIMITATIONS, VERSION, score


def _blank(state: str, reason: str | None = None, source: str | None = None) -> dict[str, Any]:
    return {"schema_version": 1, "scoring_version": VERSION, "state": state,
            "verification": {"state": "unverified" if state == "unverified" else "unavailable", "source": source,
                             "checked": None, "anchored": None, "reason": reason},
            "personal": {"xp": None, "xp_half_units": None, "level": None, "earned_form": None},
            "form": "egg", "next_form": "cell", "next_form_xp": 260, "progress": None, "learned_count": None,
            "community": {"state": "pending", "hatch_at": None}, "stale": False, "limitations": list(LIMITATIONS)}


@dataclass(frozen=True)
class _Context:
    identity: str
    root: Path
    registry: KeyRegistry
    principals: frozenset[str]
    source: str
    authority: Any = None


class PetStatus:
    """One in-flight read per engine. Last-good values are memory-only and scoped to authenticated context."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self._task: asyncio.Task | None = None
        self._task_identity: str | None = None
        self._good: dict[str, dict[str, Any]] = {}

    def _context(self) -> _Context | dict[str, Any]:
        backend = os.environ.get("EXO_LEDGER_AUTHORITY", "")
        required = os.environ.get("EXO_LEDGER_AUTHORITY_REQUIRED") == "1"
        source = "authority" if backend or required else "legacy"
        try:
            home = Path(self.engine.home).resolve()
            root = resolve_store(self.engine)
            if root is None:
                return _blank("no_store", "no_principal_store", source)
            root = Path(root).resolve()
            registry = KeyRegistry.load(home, create=False)
            who = provenance.principal_names(SimpleNamespace(root=root), registry.principals, home=home)
            # Opening verification access must not provision a writer or a new trusted identity.
            authority = open_authority(root / "ledger.sqlite") if source == "authority" else None
            policy = {"home": str(home), "root": str(root), "keys": registry.keys, "principals": sorted(who),
                      "backend": backend, "required": required, "authority": authority.identity if authority else None}
            identity = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
            return _Context(identity, root, registry, who, source, authority)
        except Exception:
            # Without the current trust identity, even an old memory projection cannot be safely reused.
            return _blank("unavailable", "authority_unavailable" if source == "authority" else "configuration_unavailable", source)

    async def read(self) -> dict[str, Any]:
        for _ in range(2):
            context = await asyncio.to_thread(self._context)
            if isinstance(context, dict):
                return context
            if self._task is not None and not self._task.done() and self._task_identity != context.identity:
                return _blank("unavailable", "configuration_changed", context.source)
            if self._task is None or self._task.done():
                self._task_identity = context.identity
                self._task = asyncio.create_task(asyncio.to_thread(self._read, context))
            result = await asyncio.shield(self._task)
            current = await asyncio.to_thread(self._context)
            if isinstance(current, dict):
                return current
            if current.identity == context.identity:
                return copy.deepcopy(result)
        return _blank("unavailable", "configuration_changed")

    def _read(self, context: _Context) -> dict[str, Any]:
        def failure(state: str, reason: str, checked: int | None = None) -> dict[str, Any]:
            result = _blank(state, reason, context.source)
            if context.identity in self._good:
                previous = self._good[context.identity]
                for key in ("personal", "form", "next_form", "next_form_xp", "progress", "learned_count"):
                    result[key] = copy.deepcopy(previous[key])
                result["stale"] = True
            result["verification"]["checked"] = checked
            return result

        path = context.root / "ledger.sqlite"
        if not path.exists():
            try:
                broken = bool(missing_ledger_problem(path))
            except Exception:
                broken = True
            return failure("unverified" if broken else "unavailable", "ledger_missing")
        try:
            # External evidence precedes the SQLite snapshot; a concurrent append is not tampering.
            captured = capture_authority(context.authority) if context.source == "authority" else None
            with Ledger.read_only(path) as reader:
                if captured is not None:
                    verified = captured.verify(reader)
                    checked = verified.get("local_seq")
                    anchored = bool(verified.get("ok"))
                else:
                    verified = reader.verify(trusted=context.registry)
                    checked, anchored = verified.get("checked"), verified.get("anchored", False)
                if verified.get("state") == "unconfirmed_tail":
                    return failure("unavailable", "ledger_busy", checked)
                if not verified.get("ok") or not anchored:
                    return failure("unverified", "integrity_or_trust_failed" if verified.get("ok") is not True else "unanchored", checked)
                projection = score(captured.events(reader) if captured is not None else reader.iter(), context.principals)  # the exact SQLite snapshot verified above
            result = _blank("empty" if checked == 0 else "ready", source=context.source)
            result.update(projection)
            result["verification"] = {"state": "verified", "source": context.source, "checked": checked,
                                      "anchored": True, "reason": None}
            if context.source == "authority":
                result["verification"]["fresh"] = False  # authenticated prefix, not an independent fresh witness
            self._good.clear()  # bounded last-good memory; changing principal/store/trust never inherits old growth
            self._good[context.identity] = copy.deepcopy(result)
            return result
        except Exception:
            return failure("unverified", "ledger_unreadable")


async def command(engine: Any, request: s.CommandRun) -> dict[str, Any]:
    require_principal("read the local pet")
    if request.args.strip() not in ("", "status"):
        raise CommandFailed("bad_request", "usage: /pet [status]")
    service = getattr(engine, "pet_status", None)
    if service is None:
        service = engine.pet_status = PetStatus(engine)
    return await service.read()
