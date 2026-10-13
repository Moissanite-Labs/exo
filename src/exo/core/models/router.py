"""Role routing: role to model, fallback, cooldown, lone-credential reprobe.

Ported (rewritten) from the ideas in Hermes agent/auxiliary_client.py,
agent/credential_pool*.py and agent/fallback_cooldown.py, including the
re-probe rule of hermes-agent PR #119652: a lone non-billing credential with a
provider-stated reset is re-probed after max(60 s, 2% of the open window), never
past the reset, and each failed probe re-stamps a shorter window. Credentials with
siblings, and billing failures, keep the provider reset.

Keys: environment, then the macOS Keychain (macOS only), then ~/.config/exo/keys.env (see keys.py).
"""

from __future__ import annotations

import os
import shutil
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.base import error_event
from exo.core.models.claude_cli import ClaudeCliProvider
from exo.core.models.muse_cli import MuseCliProvider
from exo.core.models.decision import DecisionProvider
from exo.core.models.keys import lookup_key
from exo.core.models.spend import SpendJournal, admitted_stream, configured
from exo.core.models.openai_chat import OpenAIChatProvider
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
TRANSPORTS = {"anthropic": AnthropicProvider, "openai": OpenAIChatProvider,
              "decision": DecisionProvider}


def load_catalog(path: Path | str = CATALOG_PATH) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


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


@dataclass
class Breaker:
    """Route circuit breaker: consecutive failures, times opened, reopen time, trial call in flight."""
    fails: int = 0
    level: int = 0
    until: float = 0.0
    trial: bool = False


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


class Router:
    """Resolves roles to routes and streams with fallback.

    ``roles`` and ``providers`` override the catalog tables. ``instances`` maps a
    provider name to a ready Provider object (tests, plugins, the fake provider).
    """

    def __init__(self, roles: Mapping[str, list[str]] | None = None, providers: Mapping[str, dict] | None = None,
                 *, catalog: dict[str, Any] | None = None, instances: Mapping[str, Provider] | None = None,
                 env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.time,
                 client: Any = None, key_sources: bool | None = None,
                 breaker: Mapping[str, Any] | None = None, spend: SpendJournal | None = None) -> None:
        self.spend = spend
        cat = load_catalog() if catalog is None else catalog
        self.providers: dict[str, dict] = {**cat.get("providers", {}), **(providers or {})}
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
        b = breaker or {}
        self.breaker_failures = max(1, int(b.get("failures") or BREAKER_FAILURES))
        self.breaker_cooldown_s = float(b.get("cooldown_s") or BREAKER_COOLDOWN_S)
        self.breaker_max_s = max(self.breaker_cooldown_s, float(b.get("max_cooldown_s") or BREAKER_MAX_S))
        self._creds: dict[str, list[Credential]] = {}

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None, **kw: Any) -> Router:
        """Build from the ``[models]`` table of Exo config: ``roles``, ``providers``, ``breaker`` (``failures``,
        ``cooldown_s``, ``max_cooldown_s``: the route circuit breaker) and ``models`` (per "provider:model"
        overrides of the catalog: ``input``, ``output``, ``cache_read``, ``cache_write`` USD per million tokens,
        ``context``)."""
        config = config or {}
        if "spend" not in kw:
            kw["spend"] = configured(config.get("spend"), required=os.environ.get("EXO_MODEL_SPEND_REQUIRED") == "1")
        r = cls(roles=config.get("roles"), providers=config.get("providers"), breaker=config.get("breaker"), **kw)
        for ref, info in (config.get("models") or {}).items():  # prices and limits per "provider:model" (D-032)
            if isinstance(info, dict):
                r.models[ref] = {**r.models.get(ref, {}), **info}
        return r

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
        if provider in self._creds:
            return self._creds[provider]
        creds: list[Credential] = []
        if provider in self.instances:
            creds = [Credential(provider, None, self.instances[provider])]
        elif (spec := self.providers.get(provider)) is not None and spec.get("transport") == "claude-cli":
            # No key: the CLI's own OAuth login is the credential. Available only if the binary is on PATH.
            exe = shutil.which(spec.get("command", "claude"), path=self.env.get("PATH", ""))
            if exe:
                prov = ClaudeCliProvider(exe, name=provider, env=self.env,
                                         **({"read_timeout": float(spec["read_timeout"])} if spec.get("read_timeout") else {}))
                creds = [Credential(provider, None, prov)]
        elif (spec := self.providers.get(provider)) is not None and spec.get("transport") == "muse-cli":
            # No key: the muse CLI's own login is the credential (D-051). Available only if the binary is on PATH.
            exe = shutil.which(spec.get("command", "muse"), path=self.env.get("PATH", ""))
            if exe:
                prov = MuseCliProvider(exe, name=provider, env=self.env,
                                       **({"read_timeout": float(spec["read_timeout"])} if spec.get("read_timeout") else {}))
                creds = [Credential(provider, None, prov)]
        elif (spec := self.providers.get(provider)) is not None:
            transport = TRANSPORTS[spec.get("transport", "openai")]
            envs = spec.get("key_env") or []
            envs = [envs] if isinstance(envs, str) else list(envs)
            keys = [k for e in envs if (k := self._key(e))] if envs else [None]
            for i, key in enumerate(keys):
                cid = provider if len(keys) == 1 else f"{provider}#{i}"
                prov = transport(spec["base_url"], key, name=provider, client=self.client,
                                 headers=spec.get("headers"), session_header=spec.get("session_header"),
                                 **({"read_timeout": float(spec["read_timeout"])} if spec.get("read_timeout") else {}))
                creds.append(Credential(cid, key, prov))
        self._creds[provider] = creds
        return creds

    def reload_credentials(self, provider: str | None = None) -> list[str]:
        """Forget cached credentials (and their cooldowns) so the next call reads keys again: after a key was
        added or removed with `exo providers`, the TUI or the web client (D-034). Returns the providers reset."""
        names = [provider] if provider else list(self._creds)
        for n in names:
            self._creds.pop(n, None)
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

    def _pick(self, route: Route, now: float) -> tuple[Credential, bool] | None:
        creds = self.credentials(route.provider)
        for c in creds:
            if self._available(c.id, now) and self._available(f"{c.id}|{route.model}", now):
                return c, len(creds) == 1
        return None

    # --- outcome bookkeeping ---------------------------------------------------
    def record_failure(self, cred_id: str, model: str, status: int | None, retry_after: float | None,
                       sole: bool, model_only: bool = False) -> Cooldown:
        now = self.clock()
        key = f"{cred_id}|{model}" if status == 404 or model_only else cred_id
        cd = self.cooldowns.get(key) or Cooldown()
        cd.strikes += 1
        cd.status = status
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
        b.fails = self.breaker_failures if b.trial else b.fails + 1  # a failed trial reopens at once
        if b.fails < self.breaker_failures:
            return None
        b.level, b.fails, b.trial = b.level + 1, 0, False
        cool = min(self.breaker_cooldown_s * 2 ** (b.level - 1), self.breaker_max_s)
        b.until = now + cool
        return cool

    def route_ok(self, ref: str) -> None:
        self.breakers.pop(ref, None)

    def price(self, route: Route, usage: Usage | None, input_includes_cache: bool = False) -> Usage | None:
        """Fill ``cost_usd`` from the catalog price of the route that served the call (USD per million tokens).
        None stays None when the route has no price (never 0). ``input_includes_cache``: the provider counts cached
        tokens inside ``input_tokens`` (OpenAI style), so they are charged at the cache-read rate only."""
        info = self.models.get(route.ref)
        if usage is None or usage.cost_usd is not None or not info or "input" not in info:
            return usage
        inp = info["input"]
        fresh = usage.input_tokens - (usage.cache_read_tokens if input_includes_cache else 0)
        cost = (max(fresh, 0) * inp + usage.output_tokens * info.get("output", 0)
                + usage.cache_read_tokens * info.get("cache_read", inp * 0.1)
                + usage.cache_write_tokens * info.get("cache_write", inp * 1.25)) / 1e6
        return Usage(**{**vars(usage), "cost_usd": round(cost, 8)})

    # --- streaming -----------------------------------------------------------
    async def stream(self, role: str, messages: list[Message], tools: list[ToolSpec],
                     **opts: Any) -> AsyncIterator[ProviderEvent]:
        """Stream from the first available route of ``role``, rotating sibling credentials
        then falling back to later routes until output has started. Ends with exactly one
        ``done`` or ``error``."""
        tried: list[str] = []
        attempts: list[dict[str, Any]] = []  # every try, for the model.call record (D-025)
        last: ProviderEvent | None = None
        chain = self.chain(role)
        usable = [r for r in chain if self._pick(r, self.clock()) is not None]
        skip = {r.ref for r in usable if self.route_open(r.ref, self.clock())}
        skip = set() if len(skip) == len(usable) else skip  # all cooling down: try them anyway
        for i, route in enumerate(chain):
            if route.ref in skip:
                continue
            while (picked := self._pick(route, self.clock())) is not None:
                cred, sole = picked
                tried.append(route.ref)
                failed: list[ProviderEvent] = []
                async for ev in self._attempt(route, cred, sole, messages, tools, opts, failed, attempts):
                    yield ev
                if not failed:
                    return
                last = failed[0]
                nxt = next((r for r in chain[i:] if r.ref not in skip and self._pick(r, self.clock())), None)
                if nxt is None:
                    if attempts[-1]["outcome"] == "fallback":
                        attempts[-1]["outcome"] = "error"  # nothing left to fall back to
                if nxt is not None:  # tell the user why the wait ended and where the call goes next
                    why = (f"did not answer in {getattr(getattr(cred.provider, '_timeout', None), 'read', 0):.0f} s"
                           if (last.error or "").startswith("ReadTimeout") else f"failed ({(last.error or '')[:80]})")
                    yield ProviderEvent(kind="notice", text=f"{route.ref} {why}; trying {nxt.ref}")
                if attempts and (cs := attempts[-1].get("cooldown_s")):
                    yield ProviderEvent(kind="notice", text=f"{route.ref} failed repeatedly; skipping it for {cs / 60:.0f} min")
        end = self._exhausted(role, tried, last)
        end.attempts = attempts
        yield end

    async def _attempt(self, route: Route, cred: Credential, sole: bool, messages: list[Message],
                       tools: list[ToolSpec], opts: dict[str, Any], failed: list[ProviderEvent],
                       attempts: list[dict[str, Any]] | None = None) -> AsyncIterator[ProviderEvent]:
        """One call on one credential. A failure that may fall back is swallowed and
        appended to ``failed``; anything else (including a terminal error) is yielded.
        Each outcome is added to ``attempts`` (a try that fell back reads ``timeout`` or ``fallback``)."""
        if self.spend is None and os.environ.get("EXO_MODEL_SPEND_REQUIRED") == "1":
            yield error_event("required model-spend policy is missing; ask the principal to configure it", None,
                              retryable=False)
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
                    self.record_failure(cred.id, route.model, ev.status, retry_after, sole,
                                        model_only=ev.status in NO_FALLBACK_STATUS)  # an upstream 400 is this model's
                    if ev.retryable:
                        cool = self.route_failed(route.ref, self.clock())
                if started or not fallback:
                    note("error", ev.error or "")
                    ev.route, ev.attempts = route.ref, attempts
                    yield ev
                else:
                    note("timeout" if (ev.error or "").startswith("ReadTimeout") or "timed out" in (ev.error or "")
                         else "fallback", ev.error or "")
                    failed.append(ev)
                return
            if ev.kind in ("text", "reasoning", "tool_call"):
                started = True
            elif ev.kind in ("usage", "done"):
                ev.usage = self.price(route, ev.usage, getattr(cred.provider, "input_includes_cache", False))
                ev.route = route.ref  # the route that served this call, after any fallback
                if ev.kind == "done":
                    note("ok")
                    ev.attempts = attempts
                    self.record_success(cred.id, route.model)
                    self.route_ok(route.ref)
                    if ev.message is not None:
                        ev.message.meta.setdefault("model", route.ref)
            yield ev

    def _exhausted(self, role: str, tried: list[str], last: ProviderEvent | None) -> ProviderEvent:
        now = self.clock()
        waits = [cd.until - now for cd in self.cooldowns.values() if cd.until > now]
        wait = min(waits) if waits else None
        if not self.chain(role):
            return error_event(f"no model configured for role {role!r}", None, retryable=False)
        if not tried and not waits:
            return error_event(f"no credentials for role {role!r}: set the provider key_env variables",
                               None, retryable=False)
        why = f"; last error: {last.error}" if last is not None else ""
        hint = f"; next route reopens in {wait:.0f}s" if wait else ""
        return error_event(f"all routes for {role!r} failed or are cooling down{why}{hint}",
                           last.status if last is not None else 429, retryable=True, retry_after=wait)

    def provider(self, role: str = "main") -> RoleProvider:
        return RoleProvider(self, role)

    def status(self) -> list[dict[str, Any]]:
        """Routes per role with availability, for ``models.list``."""
        now, out = self.clock(), []
        for role in sorted(set(self.roles) | {"main", "aux", "decision", "eval", *TIERS}):
            for route in self.chain(role):
                creds = self.credentials(route.provider)
                out.append({"role": role, "route": route.ref, "configured": bool(creds),
                            "available": self._pick(route, now) is not None,
                            **{k: v for k, v in self.models.get(route.ref, {}).items() if k == "context"}})
        return out


    def providers_status(self) -> list[dict[str, Any]]:
        """Each configured provider: base URL and whether a key was found (yes/no, never the value)."""
        out = []
        for name, spec in sorted(self.providers.items()):
            envs = spec.get("key_env") or []
            envs = [envs] if isinstance(envs, str) else list(envs)
            found = [e for e in envs if self._key(e)]
            out.append({"provider": name, "base_url": spec.get("base_url", ""), "transport": spec.get("transport", "openai"),
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

    def __init__(self, router: Router, role: str) -> None:
        self.router = router
        self.role = role
        self.name = f"router:{role}"

    def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        return self.router.stream(self.role, messages, tools, **opts)


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
    import asyncio
    import threading

    if "loop" not in _BG:
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, name="exo-aux-loop", daemon=True).start()
        _BG["loop"] = loop
    return asyncio.run_coroutine_threadsafe(aux(router, prompt, **kw), _BG["loop"]).result(timeout)
