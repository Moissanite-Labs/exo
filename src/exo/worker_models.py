"""Human-owned worker routes, shared by the terminal and web clients.

An ordered list replaces a tier's own routes. Router fallback-role inheritance
still applies. No automatic model choice or spending authority is granted here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import TYPE_CHECKING, Any

from exo.config import Config, ConfigError
from exo.principal import require_principal
from exo.server.engine import CommandFailed

if TYPE_CHECKING:
    from exo.core.engine import CoreEngine

TIERS = ("fast", "standard", "deep")


def command(eng: CoreEngine, args: str) -> dict[str, Any]:
    require_principal("configure worker models")
    if not args.strip():
        return snapshot(eng)
    try:
        if len(args) > 12000:
            raise ValueError()
        body = json.loads(args)
        if not isinstance(body, dict) or set(body) != {"roles", "revision"}:
            raise ValueError()
    except (ValueError, TypeError):
        raise CommandFailed("bad_request", "Worker settings need roles and a revision from the settings panel.") from None
    return update(eng, body["roles"], body["revision"])


def snapshot(eng: CoreEngine) -> dict[str, Any]:
    require_principal("read worker model settings")
    cfg = Config.load(eng.home)
    roles = {t: list(eng.router.roles.get(t, [])) for t in TIERS}
    seat_roles = (eng.seat.models.get("roles") or {}) if eng.seat else {}
    blocked = [t for t in TIERS if t in seat_roles]
    reason = ("This profile defines worker routes. Edit its profile settings before saving global worker models."
              if blocked else "")
    if eng.router.roles.get("delegate"):
        reason = "The delegate role overrides worker tiers. Remove that override before editing worker models."
    state = {"live": eng.router.roles, "saved": cfg.get("models.roles"), "profile": seat_roles}
    revision = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    return {"roles": roles, "effective": {t: [r.ref for r in eng.router.chain(t)] for t in TIERS},
            "revision": revision, "editable": not reason, "reason": reason,
            "scope": "Connected core now; global defaults on future starts.",
            "note": "First route is preferred. Later routes and inherited tier/main routes are fallbacks. "
                    "Changes apply to subsequent model calls; requests already running continue."}


def update(eng: CoreEngine, roles: dict[str, list[str]], revision: str) -> dict[str, Any]:
    require_principal("change worker models")
    before = snapshot(eng)
    if revision != before["revision"]:
        raise CommandFailed("conflict", "Worker model settings changed. Reload before saving again.")
    if not before["editable"]:
        raise CommandFailed("bad_request", before["reason"])
    if not isinstance(roles, dict) or set(roles) != set(TIERS):
        raise CommandFailed("bad_request", "Supply fast, standard and deep route lists.")
    # Validate every tier without touching the live router. No await separates
    # validation, persistence and activation on the engine's event loop.
    trial = copy.copy(eng.router)
    trial.roles = copy.deepcopy(eng.router.roles)
    try:
        for tier, refs in roles.items():
            if (not isinstance(refs, list) or len(refs) > 16 or
                    any(not isinstance(r, str) or len(r) > 200 or ":" not in r for r in refs)):
                raise ValueError("Each tier needs up to 16 provider:model references.")
            if len(set(refs)) != len(refs):
                raise ValueError("A tier cannot contain duplicate models.")
            trial.set_role(tier, refs)
        cfg = Config.load(eng.home)
        for tier, refs in roles.items():
            cfg.set(f"models.roles.{tier}", refs)
        cfg.save()
    except (ValueError, ConfigError):
        raise CommandFailed("bad_model", "Invalid worker routes. Use provider:model references from providers "
                            "that can serve the selected tier. No settings were changed.") from None
    except OSError:
        raise CommandFailed("save_failed", "Worker model settings could not be saved. Live routes were kept.") from None
    for tier in TIERS:
        eng.router.set_role(tier, trial.roles[tier])
    return {**snapshot(eng), "applied": True, "saved": True}
