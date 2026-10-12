"""Role routing: role to model, fallback, cooldown, lone-credential reprobe.

Ported (rewritten) from the ideas in Hermes agent/auxiliary_client.py,
agent/credential_pool*.py and agent/fallback_cooldown.py, including the
re-probe rule of hermes-agent PR #119652: a lone non-billing credential with a
provider-stated reset is re-probed after max(60 s, 2% of the open window), never
past the reset, and each failed probe re-stamps a shorter window. Credentials with
siblings, and billing failures, keep the provider reset.

Keys come from environment variables only (``key_env`` in the catalog).
"""

from __future__ import annotations

import os
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.base import error_event
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.types import Message, Provider, ProviderEvent, ToolSpec, Usage

CATALOG_PATH = Path(__file__).with_name("catalog.toml")
ROLE_FALLBACK = {"decision": ["aux", "main"], "aux": ["main"]}
SOLE_TTL = 60.0
AUTH_TTL = 300.0
LONG_TTL = 4 * 3600.0
REPROBE_FRACTION = 0.02
NO_FALLBACK_STATUS = frozenset({400, 413, 422})  # the request itself is bad; another route will fail too
TRANSPORTS = {"anthropic": AnthropicProvider, "openai": OpenAIChatProvider}


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
class Credential:
    id: str
    key: str | None
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
                 client: Any = None) -> None:
        cat = load_catalog() if catalog is None else catalog
        self.providers: dict[str, dict] = {**cat.get("providers", {}), **(providers or {})}
        self.models: dict[str, dict] = dict(cat.get("models", {}))
        self.roles: dict[str, list[str]] = {**cat.get("roles", {}), **(roles or {})}
        self.instances = dict(instances or {})
        self.env = os.environ if env is None else env
        self.clock = clock
        self.client = client
        self.cooldowns: dict[str, Cooldown] = {}
        self._creds: dict[str, list[Credential]] = {}

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None, **kw: Any) -> Router:
        """Build from the ``[models]`` table of Exo config: ``roles``, ``providers``."""
        config = config or {}
        return cls(roles=config.get("roles"), providers=config.get("providers"), **kw)

    # --- resolution ----------------------------------------------------------
    def chain(self, role: str = "main") -> list[Route]:
        """Routes for ``role`` then its fallback roles, without duplicates."""
        refs: list[str] = []
        for r in [role, *ROLE_FALLBACK.get(role, [] if role == "main" else ["main"])]:
            refs += [x for x in self.roles.get(r) or [] if x not in refs]
        return [Route.parse(x) for x in refs]

    def set_role(self, role: str, refs: list[str]) -> None:
        for ref in refs:
            Route.parse(ref)
        self.roles[role] = list(refs)

    def credentials(self, provider: str) -> list[Credential]:
        if provider in self._creds:
            return self._creds[provider]
        creds: list[Credential] = []
        if provider in self.instances:
            creds = [Credential(provider, None, self.instances[provider])]
        elif (spec := self.providers.get(provider)) is not None:
            transport = TRANSPORTS[spec.get("transport", "openai")]
            envs = spec.get("key_env") or []
            envs = [envs] if isinstance(envs, str) else list(envs)
            keys = [self.env.get(e) for e in envs if self.env.get(e)] if envs else [None]
            for i, key in enumerate(keys):
                cid = provider if len(keys) == 1 else f"{provider}#{i}"
                prov = transport(spec["base_url"], key, name=provider, client=self.client,
                                 headers=spec.get("headers"))
                creds.append(Credential(cid, key, prov))
        self._creds[provider] = creds
        return creds

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
                       sole: bool) -> Cooldown:
        now = self.clock()
        key = f"{cred_id}|{model}" if status == 404 else cred_id
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

    def price(self, route: Route, usage: Usage | None) -> Usage | None:
        info = self.models.get(route.ref)
        if usage is None or usage.cost_usd is not None or not info or "input" not in info:
            return usage
        inp = info["input"]
        cost = (usage.input_tokens * inp + usage.output_tokens * info.get("output", 0)
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
        last: ProviderEvent | None = None
        for route in self.chain(role):
            while (picked := self._pick(route, self.clock())) is not None:
                cred, sole = picked
                tried.append(route.ref)
                failed: list[ProviderEvent] = []
                async for ev in self._attempt(route, cred, sole, messages, tools, opts, failed):
                    yield ev
                if not failed:
                    return
                last = failed[0]
        yield self._exhausted(role, tried, last)

    async def _attempt(self, route: Route, cred: Credential, sole: bool, messages: list[Message],
                       tools: list[ToolSpec], opts: dict[str, Any],
                       failed: list[ProviderEvent]) -> AsyncIterator[ProviderEvent]:
        """One call on one credential. A failure that may fall back is swallowed and
        appended to ``failed``; anything else (including a terminal error) is yielded."""
        started = False
        async for ev in cred.provider.stream(messages, tools, **{**opts, "model": route.model}):
            if ev.kind == "error":
                retry_after = ev.message.meta.get("retry_after") if ev.message else None
                fallback = (ev.status not in NO_FALLBACK_STATUS) if ev.status is not None else ev.retryable
                if fallback:
                    self.record_failure(cred.id, route.model, ev.status, retry_after, sole)
                if started or not fallback:
                    yield ev
                else:
                    failed.append(ev)
                return
            if ev.kind in ("text", "reasoning", "tool_call"):
                started = True
            elif ev.kind in ("usage", "done"):
                ev.usage = self.price(route, ev.usage)
                if ev.kind == "done":
                    self.record_success(cred.id, route.model)
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
        for role in sorted(set(self.roles) | {"main", "aux", "decision"}):
            for route in self.chain(role):
                creds = self.credentials(route.provider)
                out.append({"role": role, "route": route.ref, "configured": bool(creds),
                            "available": self._pick(route, now) is not None,
                            **{k: v for k, v in self.models.get(route.ref, {}).items() if k == "context"}})
        return out


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
              **opts: Any) -> str:
    """One-shot side call (titles, summaries, decisions): returns the text, raises ModelError.
    ``role="decision"`` for cheap structured choices."""
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
