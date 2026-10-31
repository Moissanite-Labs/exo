"""Role routing: role to model, fallback, cooldown, lone-credential reprobe.

Ported (rewritten) from the ideas in Hermes agent/auxiliary_client.py,
agent/credential_pool*.py and agent/fallback_cooldown.py, including the
re-probe rule of hermes-agent PR #119652: a lone non-billing credential with a
provider-stated reset is re-probed after max(60 s, 2% of the open window), never
past the reset, and each failed probe re-stamps a shorter window. Credentials with
siblings, and billing failures, keep the provider reset.

Keys: environment, then the macOS Keychain, Linux Secret Service when `secret-tool` is available, then
`~/.config/exo/keys.env` (mode 0600; override with `EXO_KEYS_FILE`; see keys.py).
"""

from __future__ import annotations

import os
import asyncio
import concurrent.futures
import shutil
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping
from urllib.parse import urlsplit

from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.base import error_event
from exo.core.models.claude_cli import ClaudeCliProvider
from exo.core.models.muse_cli import MuseCliProvider
from exo.core.models.decision import DecisionProvider
from exo.core.models.keys import lookup_key
from exo.model_spend import SpendJournal, configured
from exo.core.models.spend import admitted_stream
from exo.model_spend import POLICY_ENV, required as spend_required, resolve_spend_policy
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.models.urls import display_url
from exo.core.trace import scrub
from exo.core.types import Message, Provider, ProviderEvent, ToolSpec, Usage

CATALOG_PATH = Path(__file__).with_name("catalog.toml")
TIERS = ("fast", "standard", "deep")  # cheapest to most capable; the delegate tool picks one per child
ROLE_FALLBACK = {"decision": ["aux", "main"], "eval": ["main"], "aux": ["main"], "fast": ["standard", "main"],
                 "standard": ["main"], "deep": ["main"]}
SOLE_TTL = 60.0
AUTH_TTL = 300.0
LONG_TTL = 4 * 3600.0
REPROBE_FRACTION = 0.02
BREAKER_FAILURES = 2  # consecutive retryable failures of one route before it is skipped
BREAKER_COOLDOWN_S = 600.0  # first skip; doubles on each failed trial
BREAKER_MAX_S = 3600.0
NO_FALLBACK_STATUS = frozenset({400, 413, 422})  # the request itself is bad; another route will fail too
REFUSED_STATUS = frozenset({401, 402, 403})  # the provider refused the key, the login or the spend: waiting will not fix it
TRANSPORTS = {"anthropic": AnthropicProvider, "openai": OpenAIChatProvider,
              "decision": DecisionProvider}
CLI_TRANSPORTS: dict[str, Any] = {"claude-cli": ClaudeCliProvider, "muse-cli": MuseCliProvider}  # keyless: the CLI's own login


def load_catalog(path: Path | str = CATALOG_PATH) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def server_address(base_url: str | None) -> str:
    """A server's host and port for text people or the model read. Never the netloc: it keeps any user:password@
    in the URL. IPv6 keeps its brackets. Use the raw URL for the connection only."""
    try:
        u = urlsplit(base_url or "")
        host, port = u.hostname, u.port
    except ValueError:  # a bad IPv6 literal or port
        return "its configured address"
    if not host:
        return "its configured address"
    host = f"[{host}]" if ":" in host else host
    return f"{host}:{port}" if port is not None else host


@dataclass(frozen=True)
class Route:
    provider: str
    model: str

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, ref: str) -> Route:
        provider, sep, model = ref.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"route must be 'provider:model', got {ref!r}")
        return cls(provider, model)


@dataclass
class Cooldown:
    until: float = 0.0
    strikes: int = 0
    reset_at: float | None = None
    status: int | None = None
    error: str = ""  # the provider's text, so a later call that sends nothing can still say why


@dataclass
class Breaker:
    """Route circuit breaker: consecutive failures, times opened, reopen time, trial call in flight."""
    fails: int = 0
    level: int = 0
    until: float = 0.0
    trial: bool = False
    last_failed: float = 0.0


@dataclass
class Credential:
    id: str
    key: str | None = field(repr=False)
    provider: Provider = field(repr=False)


def cooldown_until(now: float, status: int | None, strikes: int, reset_at: float | None, sole: bool) -> float:
    """When a failed credential re-enters rotation (see module docstring)."""
    if status == 401:
        return now + AUTH_TTL
    if status == 402:
        return reset_at or now + LONG_TTL
    if status == 404:
        return now + LONG_TTL
    if sole:
        if reset_at is None:
            return now + SOLE_TTL
        return min(reset_at, now + max(SOLE_TTL, REPROBE_FRACTION * (reset_at - now)))
    return reset_at or now + min(SOLE_TTL * 2 ** (strikes - 1), LONG_TTL)


def merge_provider(base: Any, spec: Any) -> Any:
    """A provider table over the entry it overrides (the catalog's, or config.json's under a seat): the keys it sets
    win and the rest stay. A table that sets ``base_url`` uses only the ``key_env`` and ``headers`` it sets, so a key
    or a header token never follows a new URL. A value that is not a table replaces the entry as given."""
    if not isinstance(spec, dict):
        return spec
    base = base if isinstance(base, dict) else {}
    if "base_url" in spec:
        base = {k: v for k, v in base.items() if k not in ("key_env", "headers")}
    return {**base, **spec}


class Router:
    """Resolves roles to routes and streams with fallback.

    ``roles`` override the catalog roles; each ``providers`` table merges key by key over the catalog entry of
    its name. ``instances`` maps a provider name to a ready Provider object (tests, plugins, the fake provider).
    """

    def __init__(self, roles: Mapping[str, list[str]] | None = None, providers: Mapping[str, dict] | None = None,
                 *, catalog: dict[str, Any] | None = None, instances: Mapping[str, Provider] | None = None,
                 env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.time,
                 client: Any = None, key_sources: bool | None = None,
                 breaker: Mapping[str, Any] | None = None, spend: SpendJournal | None = None,
                 spend_config: Mapping[str, Any] | None = None, principal: str | None = None) -> None:
        selection = resolve_spend_policy(spend_config if spend is None else {"principal": spend.principal},
                                         principal=principal)
        if selection.source is not None and spend is not None:
            from exo.model_spend import SpendDenied
            raise SpendDenied("an injected journal cannot replace protected model-spend policy")
        self.spend = spend if spend is not None else configured(selection.config, required=selection.required)
        self._spend_required, self._spend_source = selection.required, selection.source
        cat = load_catalog() if catalog is None else catalog
        self.providers: dict[str, dict] = dict(cat.get("providers", {}))
        for name, spec in (providers or {}).items():  # a partial table keeps the catalog transport, key and roles
            self.providers[name] = merge_provider(self.providers.get(name), spec)
        self.models: dict[str, dict] = dict(cat.get("models", {}))
        self.roles: dict[str, list[str]] = {**cat.get("roles", {}), **(roles or {})}
        self.instances = dict(instances or {})
        self.env = os.environ if env is None else env
        # Keychain and keys file are read only for the real environment, or when asked.
        self.key_sources = (env is None) if key_sources is None else key_sources
        self.clock = clock
        self.client = client
        self.cooldowns: dict[str, Cooldown] = {}
        self.breakers: dict[str, Breaker] = {}  # route ref -> state; in memory, per process
        self._forced_probes: set[str] = set()  # live last-resort probes, independent of the bounded trial lease
        b = breaker or {}
        self.breaker_failures = max(1, int(b.get("failures") or BREAKER_FAILURES))
        self.breaker_cooldown_s = float(b.get("cooldown_s") or BREAKER_COOLDOWN_S)
        self.breaker_max_s = max(self.breaker_cooldown_s, float(b.get("max_cooldown_s") or BREAKER_MAX_S))
        self._creds: dict[str, list[Credential]] = {}
        self._cred_lock = threading.Lock()
        self._cred_versions: dict[str, int] = {}
        self._cred_pending: dict[tuple[str, int], concurrent.futures.Future] = {}

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None, **kw: Any) -> Router:
        """Build from the ``[models]`` table of Exo config: ``roles``, ``providers``, ``breaker`` (``failures``,
        ``cooldown_s``, ``max_cooldown_s``: the route circuit breaker) and ``models`` (per "provider:model"
        overrides of the catalog: ``input``, ``output``, ``cache_read``, ``cache_write`` USD per million tokens,
        ``context``)."""
        config = config or {}
        kw.setdefault("spend_config", config.get("spend"))
        r = cls(roles=config.get("roles"), providers=config.get("providers"), breaker=config.get("breaker"), **kw)
        for ref, info in (config.get("models") or {}).items():  # prices and limits per "provider:model" (D-032)
            if isinstance(info, dict):
                r.models[ref] = {**r.models.get(ref, {}), **info}
        return r

    @property
    def spend_enforced(self) -> bool:
        return self.spend is not None or self._spend_required or spend_required()

    def check_spend_policy(self) -> None:
        from exo.model_spend import SpendDenied
        if POLICY_ENV in os.environ and os.environ[POLICY_ENV] != self._spend_source:
            raise SpendDenied("protected model-spend policy changed; restart the engine")
        if self.spend_enforced and self.spend is None:
            raise SpendDenied("required model-spend policy is missing; ask the principal to configure it")

    def spend_status(self) -> dict[str, Any] | None:
        try:
            self.check_spend_policy()
            if self.spend is None:
                return None
            return {**self.spend.status(), "policy_source": self._spend_source or "config"}
        except Exception:
            return {"state": "unavailable", "policy_source": self._spend_source or "config",
                    "stop_reason": "Model-spend accounting is unavailable; ask the principal to review."}

    # --- resolution ----------------------------------------------------------
    def chain(self, role: str = "main") -> list[Route]:
        """Routes for ``role`` then its fallback roles, without duplicates."""
        refs: list[str] = []
        for r in [role, *ROLE_FALLBACK.get(role, [] if role == "main" else ["main"])]:
            refs += [x for x in self.roles.get(r) or [] if x not in refs]
        return [rt for rt in map(Route.parse, refs) if self._allows(rt.provider, role)]

    def _allows(self, provider: str, role: str) -> bool:
        """A provider may be limited to some roles (``roles = ["decision"]`` in its table)."""
        allowed = (self.providers.get(provider) or {}).get("roles")
        return not allowed or role in allowed

    def pinned(self, ref: str, role: str = "delegate") -> Route:
        """The one route ``ref`` names, checked against the configured providers and the role's limits.
        Raises ``ValueError`` for a bad shape, an unknown provider, or a provider that may not serve ``role``."""
        if not ref or ref.startswith("-") or any(c.isspace() for c in ref):
            raise ValueError(f"bad model ref {ref!r}")
        route = Route.parse(ref)
        if route.provider not in self.providers and route.provider not in self.instances:
            raise ValueError(f"unknown provider {route.provider!r} in {ref!r}")
        if not self._allows(route.provider, role):
            raise ValueError(f"provider {route.provider!r} cannot serve role {role!r}")
        return route

    def _chain_for(self, role: str, ref: str | None = None) -> list[Route]:
        """The chain for ``role``, or only the route ``ref`` names: an exact-route call never falls back."""
        return self.chain(role) if ref is None else [self.pinned(ref, role)]

    def tier_route(self, tier: str) -> Route | None:
        """First route of ``tier`` (then its fallbacks) whose provider has credentials, else None."""
        return next((r for r in self.chain(tier) if self.credentials(r.provider)), None)

    def tier_of(self, ref: str) -> str | None:
        """The highest tier whose configured routes include ``ref``, or None for an unknown model."""
        return next((t for t in reversed(TIERS) if ref in (self.roles.get(t) or [])), None)

    def set_role(self, role: str, refs: list[str]) -> None:
        for ref in refs:
            if not ref or ref.startswith("-") or any(c.isspace() for c in ref):
                raise ValueError(f"bad model ref {ref!r}")
            route = Route.parse(ref)
            if route.provider not in self.providers and route.provider not in self.instances:
                raise ValueError(f"unknown provider {route.provider!r} in {ref!r}")
            if not self._allows(route.provider, role):
                raise ValueError(f"provider {route.provider!r} cannot serve role {role!r}")
        self.roles[role] = list(refs)

    def prefer(self, role: str, ref: str) -> None:
        """Put ``ref`` first for ``role`` and keep the old routes behind it as fallbacks."""
        self.set_role(role, [ref, *[r for r in self.roles.get(role, []) if r != ref]])

    def credentials(self, provider: str) -> list[Credential]:
        """Synchronous cached lookup. Coordinate workers without holding a lock across secret-store I/O.
        Reload advances a generation so an in-flight lookup cannot restore credentials that were invalidated."""
        while True:
            with self._cred_lock:
                if provider in self._creds:
                    return self._creds[provider]
                version = self._cred_versions.get(provider, 0)
                token = (provider, version)
                owner = token not in self._cred_pending
                pending = self._cred_pending.setdefault(token, concurrent.futures.Future())
            if not owner:
                pending.result()
                continue
            try:
                creds = self._load_credentials(provider)
            except BaseException as exc:
                with self._cred_lock:
                    self._cred_pending.pop(token, None)
                    pending.set_exception(exc)
                raise
            with self._cred_lock:
                if self._cred_versions.get(provider, 0) == version:
                    self._creds[provider] = creds
                self._cred_pending.pop(token, None)
                pending.set_result(None)
            # A reload during I/O discards this result and resolves the new generation.

    def _load_credentials(self, provider: str) -> list[Credential]:
        creds: list[Credential] = []
        spec = self.providers.get(provider)
        timeout = {"read_timeout": float(spec["read_timeout"])} if spec and spec.get("read_timeout") else {}
        if provider in self.instances:
            creds = [Credential(provider, None, self.instances[provider])]
        elif spec is not None and spec.get("transport") in CLI_TRANSPORTS:
            # No key: the CLI's own login is the credential (D-051 for muse). Available only if the binary is on PATH.
            cls = CLI_TRANSPORTS[spec["transport"]]
            exe = shutil.which(spec.get("command", cls.default_command), path=self.env.get("PATH", ""))
            if exe:
                creds = [Credential(provider, None, cls(exe, name=provider, env=self.env, **timeout))]
        elif spec is not None and spec.get("base_url") is not None:  # no base_url: no credential, the route is skipped
            transport = TRANSPORTS[spec.get("transport", "openai")]
            envs = spec.get("key_env") or []
            envs = [envs] if isinstance(envs, str) else list(envs)
            keys = [k for e in envs if (k := self._key(e))] if envs else [None]
            for i, key in enumerate(keys):
                cid = provider if len(keys) == 1 else f"{provider}#{i}"
                prov = transport(spec["base_url"], key, name=provider, client=self.client,
                                 headers=spec.get("headers"), session_header=spec.get("session_header"), **timeout)
                creds.append(Credential(cid, key, prov))
        return creds

    def reload_credentials(self, provider: str | None = None) -> list[str]:
        """Forget cached credentials (and their cooldowns) so the next call reads keys again: after a key was
        added or removed with `exo providers`, the TUI or the web client (D-034). Returns the providers reset."""
        with self._cred_lock:
            names = [provider] if provider else list(dict.fromkeys([*self._creds,
                                                                   *(n for n, _ in self._cred_pending)]))
            for n in names:
                self._cred_versions[n] = self._cred_versions.get(n, 0) + 1
                self._creds.pop(n, None)
        for n in names:
            for k in [k for k in self.cooldowns if k == n or k.startswith((n + "|", n + "#"))]:
                self.cooldowns.pop(k, None)
            for k in [k for k in self.breakers if k.startswith(n + ":")]:
                self.breakers.pop(k, None)
        return names

    def _key(self, name: str) -> str | None:
        return lookup_key(name, self.env, sources=self.key_sources)

    def _available(self, key: str, now: float) -> bool:
        cd = self.cooldowns.get(key)
        return cd is None or cd.until <= now

    async def _pick_async(self, route: Route, *, failed: set[str] | None = None,
                          attempted: set[tuple[str, str]] | None = None) -> tuple[Credential, bool] | None:
        creds = await asyncio.to_thread(self.credentials, route.provider)
        return self._choose(route, self.clock(), creds, failed=failed, attempted=attempted)

    def _choose(self, route: Route, now: float, creds: list[Credential], *, failed: set[str] | None = None,
                attempted: set[tuple[str, str]] | None = None) -> tuple[Credential, bool] | None:
        for c in creds:
            if (failed and c.id in failed) or (attempted and (c.id, route.model) in attempted):
                continue
            if self._available(c.id, now) and self._available(f"{c.id}|{route.model}", now):
                return c, len(creds) == 1
        return None

    # --- outcome bookkeeping ---------------------------------------------------
    def record_failure(self, cred_id: str, model: str, status: int | None, retry_after: float | None,
                       sole: bool, model_only: bool = False, error: str = "") -> Cooldown:
        now = self.clock()
        key = f"{cred_id}|{model}" if status == 404 or model_only else cred_id
        cd = self.cooldowns.get(key) or Cooldown()
        cd.strikes += 1
        cd.status, cd.error = status, error
        if retry_after:
            cd.reset_at = now + retry_after
        elif cd.reset_at is not None and cd.reset_at <= now:
            cd.reset_at = None
        cd.until = cooldown_until(now, status, cd.strikes, cd.reset_at, sole)
        self.cooldowns[key] = cd
        return cd

    def record_success(self, cred_id: str, model: str) -> None:
        self.cooldowns.pop(cred_id, None)
        self.cooldowns.pop(f"{cred_id}|{model}", None)

    # --- route circuit breaker -----------------------------------------------------
    def route_open(self, ref: str, now: float) -> bool:
        """True while the route's breaker is open. After the cool-down one caller gets the trial call (a 60 s lease)."""
        if ref in self._forced_probes:
            return True
        b = self.breakers.get(ref)
        if b is None or b.level == 0:
            return False
        if b.until > now:
            return True
        b.until, b.trial = now + 60.0, True
        return False

    def route_failed(self, ref: str, now: float) -> float | None:
        """Count a retryable failure; returns the cool-down in seconds if this one opens the breaker."""
        b = self.breakers.setdefault(ref, Breaker())
        b.last_failed = now
        b.fails = self.breaker_failures if b.trial else b.fails + 1  # a failed trial reopens at once
        if b.fails < self.breaker_failures:
            return None
        b.level, b.fails, b.trial = b.level + 1, 0, False
        cool = min(self.breaker_cooldown_s * 2 ** (b.level - 1), self.breaker_max_s)
        b.until = now + cool
        return cool

    def route_ok(self, ref: str) -> None:
        self.breakers.pop(ref, None)

    def price(self, route: Route, usage: Usage | None) -> Usage | None:
        """Fill ``cost_usd`` from the catalog price of the route that served the call (USD per million tokens).
        None stays None when the route has no price (never 0). ``input_tokens`` is the uncached input on every
        transport (D-080); cache reads and writes are charged at their own rates on top."""
        info = self.models.get(route.ref)
        if usage is None or usage.cost_usd is not None or not info or "input" not in info:
            return usage
        inp = info["input"]
        cost = (max(usage.input_tokens, 0) * inp + usage.output_tokens * info.get("output", 0)
                + usage.cache_read_tokens * info.get("cache_read", inp * 0.1)
                + usage.cache_write_tokens * info.get("cache_write", inp * 1.25)) / 1e6
        return Usage(**{**vars(usage), "cost_usd": round(cost, 8)})

    # --- streaming -----------------------------------------------------------
    async def stream(self, role: str, messages: list[Message], tools: list[ToolSpec],
                     *, _exact: str | None = None, **opts: Any) -> AsyncIterator[ProviderEvent]:
        """Stream from the first available route of ``role``, rotating sibling credentials
        then falling back to later routes until output has started. Ends with exactly one
        ``done`` or ``error``.

        ``_exact`` (private, D02 prerequisite): pin this call to the one route named
        ``"provider:model"``. No other configured route is tried, in this role or another. The
        route keeps the ordinary credential rotation, cooldowns, circuit breaker and spend guard.
        An invalid ref raises ``ValueError`` before anything is streamed."""
        if _exact is not None:
            self.pinned(_exact, role)  # raises before anything is streamed; also limits the chain below
        tried: list[str] = []
        attempts: list[dict[str, Any]] = []  # every try, for the model.call record (D-025)
        last: ProviderEvent | None = None
        failed_credentials: set[str] = set()
        attempted_credentials: set[tuple[str, str]] = set()
        chain = self._chain_for(role, _exact)
        # Breaker state is cheap; resolve credentials only when reaching a route, off the event loop.
        skip = {r.ref for r in chain if self.route_open(r.ref, self.clock())}
        pending_routes, i = chain, 0
        last_resort = False
        forced_pick = None
        resort_notice = None
        while True:
            if i >= len(pending_routes):
                candidate = None
                if skip and not last_resort and (last is None or last.retryable):
                    # One last chance on the oldest failure. Credential cooldowns and attempts within this call
                    # still apply: an open breaker does not make an auth/billing/rate-limited credential usable.
                    skipped = sorted(((r, self.breakers.get(r.ref)) for r in chain if r.ref in skip),
                                     key=lambda pair: pair[1].last_failed if pair[1] else float("inf"))
                    for route, state in skipped:
                        if state is None or route.ref in self._forced_probes:
                            continue
                        picked = await self._pick_async(route, failed=failed_credentials,
                                                        attempted=attempted_credentials)
                        now = self.clock()
                        # Another stream can recover/reload the route, or claim its trial, during lookup.
                        if self.breakers.get(route.ref) is not state or route.ref in self._forced_probes \
                                or (state.trial and state.until > now):
                            continue
                        if picked is not None:
                            state.trial, state.until = True, now + 60.0
                            self._forced_probes.add(route.ref)
                            forced_pick = picked
                            candidate = route
                            break
                if candidate is not None:
                    if last is not None:
                        why = (f"is over its provider spend limit ({(last.error or '')[:80]}); this turn's cost is a floor"
                               if last.status == 402 else f"failed ({(last.error or '')[:80]})")
                        resort_notice = ProviderEvent(kind="notice", text=f"{tried[-1]} {why}; "
                                                       f"trying {candidate.ref} as a last resort", attempts=attempts)
                    pending_routes = [candidate]
                    skip.clear()
                    last_resort = True
                    i = 0
                else:
                    break
            route = pending_routes[i]
            i += 1
            if route.ref in skip:
                continue
            while (picked := forced_pick or await self._pick_async(route, failed=failed_credentials,
                                                                  attempted=attempted_credentials)) is not None:
                selected = forced_pick is not None
                if last_resort and not selected and route.ref in self._forced_probes:
                    break  # a later sibling lookup yielded while another caller claimed a probe
                forced = last_resort
                forced_pick = None
                if forced:
                    self._forced_probes.add(route.ref)
                cred, sole = picked
                attempted_credentials.add((cred.id, route.model))
                tried.append(route.ref)
                failed: list[ProviderEvent] = []
                try:
                    if resort_notice is not None:
                        yield resort_notice
                        resort_notice = None
                    async for ev in self._attempt(route, cred, sole, messages, tools, opts, failed, attempts):
                        yield ev
                finally:
                    if forced:
                        self._forced_probes.discard(route.ref)
                if not failed:
                    return
                last = failed[0]
                # A missing/unavailable model can fall back to another model on the same valid credential.
                # Other failures exclude that credential for this call, even if its timer expires meanwhile.
                if last.status != 404 and last.status not in NO_FALLBACK_STATUS:
                    failed_credentials.add(cred.id)
                nxt = None
                for candidate in pending_routes[i - 1:]:
                    if candidate.ref not in skip and await self._pick_async(
                            candidate, failed=failed_credentials, attempted=attempted_credentials) is not None:
                        nxt = candidate
                        break
                if nxt is not None:  # tell the user why the wait ended and where the call goes next
                    why = (f"did not answer in {getattr(getattr(cred.provider, '_timeout', None), 'read', 0):.0f} s"
                           if (last.error or "").startswith("ReadTimeout") else f"failed ({(last.error or '')[:80]})")
                    if last.status == 402:
                        # The route stopped on the provider's own spend limit. Say it in those words once, and say the
                        # money figure will read low: the paid route did work nobody will bill this turn for.
                        yield ProviderEvent(kind="notice", attempts=attempts,
                                            text=f"{route.ref} is over its provider spend limit; trying {nxt.ref}"
                                                 " (this turn's cost is a floor)")
                    else:
                        yield ProviderEvent(kind="notice", text=f"{route.ref} {why}; trying {nxt.ref}", attempts=attempts)
                if attempts and (cs := attempts[-1].get("cooldown_s")):
                    yield ProviderEvent(kind="notice", text=f"{route.ref} failed repeatedly; skipping it for {cs / 60:.0f} min")
        if attempts and attempts[-1]["outcome"] == "fallback":
            attempts[-1]["outcome"] = "error"  # normal routes and the optional last resort are exhausted
        end = self._exhausted(role, tried, last, _chain=chain)
        end.attempts = attempts
        yield end

    async def _attempt(self, route: Route, cred: Credential, sole: bool, messages: list[Message],
                       tools: list[ToolSpec], opts: dict[str, Any], failed: list[ProviderEvent],
                       attempts: list[dict[str, Any]] | None = None,
                       model_only: bool = False) -> AsyncIterator[ProviderEvent]:
        """One call on one credential. A failure that may fall back is swallowed and
        appended to ``failed``; anything else (including a terminal error) is yielded.
        Each outcome is added to ``attempts`` (a try that fell back reads ``timeout`` or ``fallback``).
        ``model_only``: a failure cools this model on the credential, not the credential (a side call)."""
        try:
            self.check_spend_policy()
        except RuntimeError as exc:
            event = error_event(str(exc), None, retryable=False)
            event.attempts = [{"route": route.ref, "outcome": "spend_refused"}]
            yield event
            return
        if self.spend is not None:
            async for event in admitted_stream(self.spend, route.ref, cred.provider, messages, tools,
                                               {**opts, "model": route.model}):
                yield event
            return
        started, t0 = False, time.monotonic()
        attempts = [] if attempts is None else attempts
        cool: float | None = None  # set when this failure opens the route's breaker

        def note(outcome: str, err: str = "") -> None:
            attempts.append({"route": route.ref, "outcome": outcome, "error": scrub(err, 160),
                             "ms": int((time.monotonic() - t0) * 1000),
                             **({"cooldown_s": int(cool)} if cool else {})})

        async for ev in cred.provider.stream(messages, tools, **{**opts, "model": route.model}):
            if ev.kind == "error":
                retry_after = ev.retry_after
                # 400, 413 and 422 do not fall back, unless the transport marked the upstream as down (retryable).
                fallback = (ev.status not in NO_FALLBACK_STATUS or ev.retryable) if ev.status is not None else ev.retryable
                if fallback:
                    self.record_failure(cred.id, route.model, ev.status, retry_after, sole, error=ev.error or "",
                                        model_only=model_only or ev.status in NO_FALLBACK_STATUS)  # a 400: this model's
                    if ev.status == 429:
                        # A responding rate limiter is reachable. Retry-After belongs to the credential cooldown,
                        # not the outage breaker (including a trial lease left over from an earlier outage).
                        self.route_ok(route.ref)
                    elif ev.retryable:
                        cool = self.route_failed(route.ref, self.clock())
                if started or not fallback:
                    note("error", ev.error or "")
                    ev.route, ev.attempts = route.ref, attempts
                    yield ev
                else:
                    # "spend_limit" keeps the cause after the fallback, so the money figure can say it is a floor.
                    note("spend_limit" if ev.status == 402 else
                         "timeout" if (ev.error or "").startswith("ReadTimeout") or "timed out" in (ev.error or "")
                         else "fallback", ev.error or "")
                    failed.append(ev)
                return
            if ev.kind in ("text", "reasoning", "tool_call"):
                started = True
            elif ev.kind in ("usage", "done"):
                ev.usage = self.price(route, ev.usage)
                ev.route = route.ref  # the route that served this call, after any fallback
                if ev.kind == "done":
                    note("ok")
                    ev.attempts = attempts
                    self.record_success(cred.id, route.model)
                    self.route_ok(route.ref)
                    if ev.message is not None:
                        ev.message.meta.setdefault("model", route.ref)
            yield ev

    def _exhausted(self, role: str, tried: list[str], last: ProviderEvent | None,
                   *, _chain: list[Route] | None = None) -> ProviderEvent:
        """The closing event. ``_chain`` is the chain this call actually walked (exact route: one route), so the
        wait hint and the refusal speak only for that route."""
        chain = self.chain(role) if _chain is None else _chain
        now = self.clock()
        waits = [until - now for until, _, _ in self._chain_cooldowns(role, now, _chain=chain)]
        wait = min(waits) if waits else None
        if not chain:
            return error_event(f"no model configured for role {role!r}; use /providers and choose a default model",
                               None, retryable=False)
        if not tried and not waits:
            return error_event(f"no credentials for role {role!r}: set the provider key_env variables, "
                               "or use /providers to add a provider and choose its model",
                               None, retryable=False)
        if refused := self._refused(role, tried, last, now, _chain=chain):  # no retry now, say what to fix
            return error_event(refused[1], refused[0], retryable=False, retry_after=wait)
        why = f"; last error: {last.error}" if last is not None else ""
        hint = f"; next route reopens in {wait:.0f}s" if wait else ""
        return error_event(f"all routes for {role!r} failed or are cooling down{why}{hint}",
                           last.status if last is not None else 429, retryable=True, retry_after=wait)

    def _chain_cooldowns(self, role: str, now: float, *,
                         _chain: list[Route] | None = None) -> list[tuple[float, str, Cooldown]]:
        """Live cooldowns that bear on ``role``: a credential of a chain provider, or a credential|model cooldown on a
        model of the chain. A gate side call (D-081) cools a model this role never asks, and another provider's
        cooldown says nothing about this role's routes, so neither moves the wait hint or the refusal."""
        routes = {(r.provider, r.model) for r in (self.chain(role) if _chain is None else _chain)}
        out: list[tuple[float, str, Cooldown]] = []
        for key, cd in self.cooldowns.items():
            cred, scoped, model = key.partition("|")
            name = cred.partition("#")[0]
            if cd.until > now and ((name, model) in routes if scoped else any(name == n for n, _ in routes)):
                out.append((cd.until, name, cd))
        return out

    def _refused(self, role: str, tried: list[str], last: ProviderEvent | None,
                 now: float, *, _chain: list[Route] | None = None) -> tuple[int | None, str] | None:
        """Status and message when waiting cannot help: the provider refused the key or login (401, 403), or the key is
        over its spend limit (402) which a new key does not fix; on a later call too, while that credential, or a model
        of the chain on it, reopens first; or a local server is not running."""
        if last is None:  # nothing ran: the first of this chain's cooldowns to reopen holds the cause
            cooling = self._chain_cooldowns(role, now, _chain=_chain)
            _, name, cd = min(cooling, key=lambda c: c[0], default=(0.0, "", Cooldown()))
            status, err = cd.status, cd.error or f"HTTP {cd.status}"
        else:
            name, status, err = tried[-1].partition(":")[0], last.status, last.error or ""
        spec = self.providers.get(name) or {}
        if status == 402:  # the provider's own spend limit: a new key fixes nothing, raising the limit does
            return status, (f"{name} is over its provider spend limit ({err}): raise the limit or top up the account, "
                            "then test it in /providers")
        if status in REFUSED_STATUS:
            envs = spec.get("key_env") or []
            envs = [envs] if isinstance(envs, str) else envs
            return status, (f"{name} rejected the key ({err}): fix {' or '.join(envs)} or add a key in /providers"
                            if envs else f"{name} refused the login ({err}): log in again, then test it in /providers")
        if last is not None and spec.get("local") and err.startswith("ConnectError"):
            return None, (f"{name} is not running at {server_address(spec.get('base_url', ''))}: "
                          "start it, then test it in /providers")
        return None

    def provider(self, role: str = "main") -> RoleProvider:
        return RoleProvider(self, role)

    def provider_for(self, ref: str, role: str = "delegate") -> RoleProvider:
        """A Provider for the one route ``ref`` names (D02 prerequisite). Raises ``ValueError`` before any
        credential is read, for a bad shape, a provider that is not configured, or a provider whose ``roles``
        table may not serve ``role``. Uses this same Router (one cooldown, breaker and spend state) with
        ``role`` as the stream role, so it never falls back to another route."""
        self.pinned(ref, role)
        return RoleProvider(self, role, exact=ref)

    def status(self) -> list[dict[str, Any]]:
        """Routes per role with availability, for ``models.list``."""
        now, out = self.clock(), []
        for role in sorted(set(self.roles) | {"main", "aux", "decision", "eval", *TIERS}):
            for route in self.chain(role):
                creds = self.credentials(route.provider)
                out.append({"role": role, "route": route.ref, "configured": bool(creds),
                            "available": self._choose(route, now, creds) is not None,
                            **{k: v for k, v in self.models.get(route.ref, {}).items() if k == "context"}})
        return out

    def providers_status(self) -> list[dict[str, Any]]:
        """Each configured provider: display URL without userinfo and key presence (never the value)."""
        out = []
        for name, spec in sorted(self.providers.items()):
            envs = spec.get("key_env") or []
            envs = [envs] if isinstance(envs, str) else list(envs)
            found = [e for e in envs if self._key(e)]
            out.append({"provider": name, "base_url": display_url(spec.get("base_url", "")),
                        "transport": spec.get("transport", "openai"),
                        "local": bool(spec.get("local")), "key_env": envs,
                        "key": ("n/a" if not envs else "yes" if found else "no"),
                        "roles": list(spec.get("roles") or [])})
        return out

    def resolved_roles(self) -> dict[str, list[str]]:
        """Resolved route chain per role (with fallbacks), no secrets."""
        return {role: [r.ref for r in self.chain(role)]
                for role in sorted(set(self.roles) | {"main", "aux", "decision", "eval", *TIERS})}


class RoleProvider:
    """A Router bound to one role; satisfies the ``Provider`` protocol for the loop."""

    def __init__(self, router: Router, role: str, *, exact: str | None = None) -> None:
        self.router = router
        self.role = role
        self.exact = exact
        self.name = f"router:{role}" if exact is None else f"router:{role}:{exact}"

    def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        return self.router.stream(self.role, messages, tools, _exact=self.exact, **opts)


class ModelError(RuntimeError):
    def __init__(self, event: ProviderEvent) -> None:
        super().__init__(event.error or "model error")
        self.event = event


async def aux(router: Router, prompt: str | list[Message], *, system: str | None = None, role: str = "aux",
              session_id: str | None = None, **opts: Any) -> str:
    """One-shot side call (titles, summaries, decisions): returns the text, raises ModelError.
    ``role="decision"`` for cheap structured choices. Pass ``session_id`` for providers that require a
    per-conversation header (their ``session_header``)."""
    if session_id:
        opts["session_id"] = session_id
    messages = [Message.user(prompt)] if isinstance(prompt, str) else list(prompt)
    if system:
        messages.insert(0, Message.system(system))
    text = []
    async for ev in router.stream(role, messages, [], **opts):
        if ev.kind == "text":
            text.append(ev.text)
        elif ev.kind == "error":
            raise ModelError(ev)
        elif ev.kind == "done" and ev.message is not None and not text:
            text.append(ev.message.text())
    return "".join(text)


_BG: dict[str, Any] = {}


def aux_sync(router: Router, prompt: str | list[Message], *, timeout: float | None = 120.0, **kw: Any) -> str:
    """Blocking ``aux`` for sync callers (plugin threads). Runs on one shared background event loop so the
    router's HTTP clients stay bound to a single loop. Never call it from that loop."""
    if "loop" not in _BG:
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, name="exo-aux-loop", daemon=True).start()
        _BG["loop"] = loop
    return asyncio.run_coroutine_threadsafe(aux(router, prompt, **kw), _BG["loop"]).result(timeout)
