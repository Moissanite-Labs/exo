"""Named delegation routes (D02): a child runs on one configured named route instead of a tier.

The parent may ask a child to run on one named model route, ``delegation.routes.routes.<name>``,
instead of a tier. This module parses and validates the ``delegation.routes`` configuration block,
and ``Delegator`` resolves named routes through it at runtime. A route pins the child's model
exactly: no tier chain, no fallback, and no escalation when the child fails.

Config under ``delegation.routes``::

    {"backend": "default"}                                  # tiers and the parent's own model, as today
    {"backend": "model_map", "routes": {"cheap": "fake:fast"}}  # named routes: explicit opt-in

``backend = "default"`` takes no routes: a route map beside it would be dead configuration, so it
is rejected instead. ``backend = "model_map"`` needs at least one route; a bare ``routes`` map
implies it. Route names may not collide with the reserved roles or the delegate tiers, and refs
must be ``"provider:model"`` strings.

Parsing is credential-free and network-free: this module answers the same on a machine with no
keys and no network. Shape is all it validates here; ``Delegator`` later checks a resolved ref
against the router's catalog (known provider, a provider that may serve the delegate role, a model
ranked on a tier at or below the parent's) before any task row exists. See
docs/SWARM-DELEGATION.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

BACKENDS = ("default", "model_map")  # how a child picks its model: tiers/own model, or a named route
TIERS = ("fast", "standard", "deep")  # mirrors exo.core.models.router.TIERS
RESERVED_NAMES = frozenset({"main", "aux", "delegate", "decision", "eval", *TIERS})
"""Names a route may not take: the router's own roles and the delegate tiers. A route called
``fast`` would be ambiguous with the tier of that name in the same tool call."""
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_./-]{0,31}$")  # a short symbol, safe in a SYM-2P atom and a tool call
_PROVIDER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+-]*$")


class RouteConfigError(ValueError):
    """A ``delegation.routes`` block that must be fixed before delegation can use it."""


@dataclass(frozen=True)
class RouteConfig:
    """A validated ``delegation.routes`` block. ``routes`` is immutable: a frozen copy serves every
    caller, so a later mutation of the config dict it came from cannot change what a child runs on."""

    backend: str = "default"
    routes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "routes", MappingProxyType(dict(self.routes)))

    @classmethod
    def of(cls, cfg: Mapping[str, Any] | None) -> RouteConfig:
        """Parse the block. Missing means the default backend, as delegation behaves today."""
        block = dict(cfg or {})
        _reject_unknown_keys(block)
        backend = block.get("backend", "model_map" if block.get("routes") else "default")
        if not isinstance(backend, str) or backend not in BACKENDS:
            raise RouteConfigError(f"delegation.routes.backend must be one of {', '.join(BACKENDS)}, "
                                   f"got {backend!r}")
        raw = block.get("routes")
        if backend == "default":
            if raw:
                raise RouteConfigError("delegation.routes.backend = 'default' takes no routes; "
                                       "set backend = 'model_map' to use a named route")
            return cls(backend="default")
        routes = _parse_routes(raw)
        return cls(backend="model_map", routes=routes)

    def resolve(self, name: str) -> str:
        """The ``"provider:model"`` ref for ``name``. Raises RouteConfigError when the backend serves
        only the default (tiers and the parent's own model) or when ``name`` is not configured."""
        if self.backend == "default":
            raise RouteConfigError("no named routes: delegation.routes.backend = 'default'")
        if name not in self.routes:
            raise RouteConfigError(f"unknown route {name!r}; configured: {', '.join(sorted(self.routes))}")
        return self.routes[name]

    def names(self) -> tuple[str, ...]:
        """Configured route names, sorted: what the parent may ask for, and what an error can list."""
        return tuple(sorted(self.routes))


def _reject_unknown_keys(block: Mapping[str, Any]) -> None:
    unknown = set(block) - {"backend", "routes"}
    if unknown:
        # profile_routes is the profile-scoped variant, not part of this slice: naming it explicitly
        # beats silently ignoring it and leaving a parent to wonder why its route never runs.
        raise RouteConfigError(f"unsupported key(s) in delegation.routes: {', '.join(sorted(map(str, unknown)))}; "
                               "only backend and routes are read")


def _parse_routes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping) or not raw:
        raise RouteConfigError("delegation.routes.backend = 'model_map' needs a non-empty routes map")
    routes: dict[str, str] = {}
    for name in raw:
        if not isinstance(name, str) or not _NAME.match(name):
            raise RouteConfigError(f"bad route name {name!r}: use letters, digits, '_', '.', '/' or '-', "
                                   "starting with a letter, at most 32 characters")
        if name in RESERVED_NAMES:
            raise RouteConfigError(f"route name {name!r} is reserved for a role or a tier")
        routes[name] = _parse_ref(name, raw[name])
    return routes


def _parse_ref(name: str, ref: Any) -> str:
    if not isinstance(ref, str) or not ref:
        raise RouteConfigError(f"route {name!r} needs a \"provider:model\" string, got {ref!r}")
    provider, sep, model = ref.partition(":")
    if not sep or not _PROVIDER.match(provider) or not _MODEL.match(model):
        raise RouteConfigError(f"route {name!r} must be \"provider:model\", got {ref!r}")
    return ref
