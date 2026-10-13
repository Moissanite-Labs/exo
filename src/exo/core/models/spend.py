"""Principal-owned admission for routed model spend, under explicitly pinned prices.

No network, tokenizer estimates, automatic initialization, reservation expiry, or
policy-override approval. The service account must protect policy and journal.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from types import MappingProxyType
from typing import Any

from exo.core.types import Usage

SCALE = 1_000_000_000  # integer nano-USD, round charges upwards

# Audited 2026-10-13, native endpoints only; never extended through user/catalog config.
# https://developers.openai.com/api/docs/models/gpt-4.1 (dated snapshot listed on page)
# https://platform.claude.com/docs/en/models/overview (exact API IDs listed on page)
# https://platform.claude.com/docs/en/build-with-claude/context-windows
# Values: full native context ceiling, native output ceiling. No approximate prompt estimates.
NATIVE_BOUNDS = MappingProxyType({
    ("openai", "https://api.openai.com/v1", "gpt-4.1-2025-04-14"): (1_047_576, 32_768),
    ("anthropic", "https://api.anthropic.com", "claude-opus-5-5"): (1_000_000, 128_000),
    ("anthropic", "https://api.anthropic.com", "claude-sonnet-5-5"): (1_000_000, 128_000),
    ("anthropic", "https://api.anthropic.com", "claude-haiku-5-5"): (1_000_000, 128_000),
})


class SpendDenied(RuntimeError):
    """Terminal local refusal. Only the principal can change the policy."""


def _money(value: Any, *, ceiling: bool = True) -> int:
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError("spend amounts must be finite and nonnegative")
    n = int((d * SCALE).to_integral_value(rounding=ROUND_CEILING if ceiling else ROUND_FLOOR))
    if n > 10**15:
        raise ValueError("spend amount exceeds supported range")
    return n


def _positive(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("token bounds must be positive integers")
    return value


@dataclass(frozen=True)
class Price:
    base_url: str
    model: str
    transport: str
    input_bound: int
    max_output: int
    rates: tuple[int, int, int, int]  # nano-USD per million tokens: input/output/cache-read/cache-write

    @classmethod
    def parse(cls, raw: dict) -> Price:
        if raw.get("contract") != "text-tokens-v1":
            raise ValueError("route requires an explicit text-tokens-v1 billing contract")
        if raw["transport"] not in ("anthropic", "openai"):
            raise ValueError("unsupported bounded model transport")
        if not raw["base_url"] or not raw["model"]:
            raise ValueError("billing contract must pin endpoint and wire model")
        return cls(raw["base_url"].rstrip("/"), raw["model"], raw["transport"],
                   _positive(raw["input_bound"]), _positive(raw["max_output"]),
                   tuple(_money(raw[k]) for k in ("input", "output", "cache_read", "cache_write")))

    def charge(self, counts: tuple[int, int, int, int]) -> int:
        return (sum(n * rate for n, rate in zip(counts, self.rates)) + 999_999) // 1_000_000

    def maximum(self, output: int) -> int:
        return (self.input_bound * max(self.rates[0], self.rates[2], self.rates[3])
                + output * self.rates[1] + 999_999) // 1_000_000

    def actual(self, usage: Usage, output: int, includes_cache: bool) -> int:
        counts = (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
                  usage.cache_write_tokens, usage.reasoning_tokens)
        if any(type(n) is not int or n < 0 for n in counts):
            raise SpendDenied("invalid provider token accounting")
        fresh = usage.input_tokens - (usage.cache_read_tokens if includes_cache else 0)
        if (fresh < 0 or fresh + usage.cache_read_tokens + usage.cache_write_tokens > self.input_bound
                or usage.output_tokens > output or usage.reasoning_tokens > usage.output_tokens):
            raise SpendDenied("provider exceeded the pinned token contract")
        # Contract includes all reasoning in output. Never trust provider-reported cost_usd.
        return self.charge((fresh, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens))


class SpendJournal:
    def __init__(self, config: dict, *, clock=time.time, _review: bool = False, _readonly: bool = False):
        self.path = Path(config["journal"]).absolute()
        self.principal = str(config["principal"])
        self.identity = str(uuid.UUID(config["journal_id"]))
        if not self.principal:
            raise ValueError("model spend needs a principal")
        self.daily, self.monthly = _money(config["daily_usd"], ceiling=False), _money(config["monthly_usd"], ceiling=False)
        self.prices = MappingProxyType({k: Price.parse(v) for k, v in config["routes"].items()})
        self.clock = clock
        with self._db(readonly=_readonly) as db:
            # A recorded stop must leave transcript/status access available after restart.
            # Admission and tool boundaries still call check(); opening is not resuming.
            self._identity(db, check_block=False)

    @staticmethod
    def provision(path: str | Path, principal: str) -> str:
        """Trusted operator only: exclusively create a new journal; returns policy UUID."""
        path = Path(path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        identity = str(uuid.uuid4())
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE identity (id TEXT NOT NULL, principal TEXT NOT NULL, blocked TEXT NOT NULL,
                    observed REAL NOT NULL);
                CREATE TABLE calls (id TEXT PRIMARY KEY, day TEXT NOT NULL, month TEXT NOT NULL,
                    route TEXT NOT NULL, session TEXT NOT NULL, reserved INTEGER NOT NULL,
                    actual INTEGER, pricing TEXT NOT NULL, completed_day TEXT, completed_month TEXT);
            ''')
            db.execute("INSERT INTO identity VALUES (?, ?, '', 0)", (identity, principal))
        return identity

    @contextmanager
    def _db(self, *, readonly=False):
        db = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=10)
        try:
            if not readonly:
                db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _identity(self, db, *, check_block=True):
        rows = db.execute("SELECT id, principal, blocked FROM identity").fetchall()
        if len(rows) != 1 or rows[0][:2] != (self.identity, self.principal):
            raise SpendDenied("model spend journal identity mismatch")
        if check_block and rows[0][2]:
            raise SpendDenied(rows[0][2])

    def _observe(self, db):
        timestamp = self.clock()
        previous = db.execute("SELECT observed FROM identity").fetchone()[0]
        if not math.isfinite(timestamp) or timestamp < previous:
            raise SpendDenied("clock moved backwards; ask the principal to restore trustworthy UTC time")
        db.execute("UPDATE identity SET observed=?", (timestamp,))
        return datetime.fromtimestamp(timestamp, timezone.utc)

    def check(self):
        with self._db() as db:
            self._identity(db)

    def status(self) -> dict:
        """Inspect one consistent snapshot without updating the clock or journal."""
        with self._db(readonly=True) as db:
            self._identity(db, check_block=False)
            reason = db.execute("SELECT blocked FROM identity").fetchone()[0]
            pending, count = db.execute("SELECT COALESCE(SUM(reserved), 0), COUNT(*) FROM calls WHERE actual IS NULL").fetchone()
            now = datetime.fromtimestamp(self.clock(), timezone.utc)
            charged = {}
            for column, period in (("day", now.strftime("%Y-%m-%d")), ("month", now.strftime("%Y-%m"))):
                charged[column] = db.execute(f"SELECT COALESCE(SUM(actual), 0) FROM calls "
                    f"WHERE actual IS NOT NULL AND ({column}=? OR completed_{column}=?)", (period, period)).fetchone()[0]
        money = lambda n: format(Decimal(n) / SCALE, ".9f")
        return {"state": "stopped" if reason else ("offline" if not self.prices else "ready"),
                "principal": self.principal, "daily_cap_usd": money(self.daily), "monthly_cap_usd": money(self.monthly),
                "day_charged_usd": money(charged["day"]), "month_charged_usd": money(charged["month"]),
                "pending_reserved_usd": money(pending), "pending_count": count, "stop_reason": reason}

    @classmethod
    def inspect(cls, config: dict) -> dict:
        """Public read-only inspection, including stopped journals; never provisions or resumes."""
        return cls(config, _readonly=True).status()

    def reserve(self, route: str, session: str, maximum: int, price: Price) -> str:
        with self._db() as db:
            self._identity(db)
            now = self._observe(db)
            day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
            for column, period, limit in (("day", day, self.daily), ("month", month, self.monthly)):
                # Uncertain spend survives every rollover, even a call straddling UTC midnight.
                used = db.execute(f"SELECT COALESCE(SUM(CASE WHEN actual IS NULL THEN reserved ELSE actual END), 0) "
                                  f"FROM calls WHERE actual IS NULL OR {column}=? OR completed_{column}=?",
                                  (period, period)).fetchone()[0]
                if used + maximum > limit:
                    raise SpendDenied(f"{column} model-spend cap cannot cover this request; "
                                      "ask the principal to review the budget and pending reservations")
            token = str(uuid.uuid4())
            db.execute("INSERT INTO calls (id, day, month, route, session, reserved, actual, pricing) "
                       "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                       (token, day, month, route, session, maximum, json.dumps(vars(price))))
        return token

    def settle(self, token: str, actual: int):
        with self._db() as db:
            # A global stop denies new work, but admitted calls still need accounting.
            # Settlement validates identity and leaves the blocked reason untouched.
            self._identity(db, check_block=False)
            row = db.execute("SELECT reserved, actual FROM calls WHERE id=?", (token,)).fetchone()
            if row is None or row[1] is not None or actual < 0 or actual > row[0]:
                raise SpendDenied("invalid model spend settlement")
            # Charge both admission and completion periods when calls straddle a boundary.
            # Carry unresolved reservations forever; successful settlement is charged at completion.
            now = self._observe(db)
            day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
            db.execute("UPDATE calls SET actual=?, completed_day=?, completed_month=? WHERE id=?",
                       (actual, day, month, token))

    @classmethod
    def operator_review(cls, config: dict, *, settlements: dict[str, Any] | None = None,
                        resume: bool = False):
        """Explicit trusted operator action with engines stopped; never exposed to model tools.

        Values are confirmed USD charges from billing evidence. Retains every original
        reservation and its pinned price, including charges beyond the old estimate.
        Resume requires all uncertain calls reconciled; it never changes configured caps.
        """
        journal = cls(config, _review=True)
        with journal._db() as db:
            journal._identity(db, check_block=False)
            now = journal._observe(db)
            for token, amount in (settlements or {}).items():
                row = db.execute("SELECT actual FROM calls WHERE id=?", (token,)).fetchone()
                if row is None or row[0] is not None:
                    raise SpendDenied("operator settlement must identify an unresolved reservation")
                db.execute("UPDATE calls SET actual=?, completed_day=?, completed_month=? WHERE id=?",
                           (_money(amount), now.strftime("%Y-%m-%d"), now.strftime("%Y-%m"), token))
            if resume:
                if db.execute("SELECT 1 FROM calls WHERE actual IS NULL LIMIT 1").fetchone():
                    raise SpendDenied("reconcile every uncertain reservation before resuming")
                db.execute("UPDATE identity SET blocked=''")

    def freeze(self, reason: str):
        with self._db() as db:
            self._identity(db, check_block=False)
            db.execute("UPDATE identity SET blocked=?", (reason,))


def configured(config: dict | None, *, required: bool = False) -> SpendJournal | None:
    if config is None:
        if required:
            raise SpendDenied("required model-spend policy is missing; ask the principal to configure it")
        return None
    return SpendJournal(config)


async def admitted_stream(journal: SpendJournal, route: str, provider: Any,
                          messages: list, tools: list, opts: dict):
    """Strict adapter boundary. Refusal never enters a provider or executes a tool."""
    from exo.core.models.anthropic import AnthropicProvider
    from exo.core.models.openai_chat import OpenAIChatProvider
    from exo.core.models.fake import RuleFake
    from exo.core.models.base import error_event

    token, settled, stream = None, False, None
    try:
        journal.check()
        if type(provider) is RuleFake:
            # Exact installed implementation only, never a provider label/local flag/subclass.
            stream = RuleFake.stream(provider, messages, tools, **opts)
            async for event in stream:
                event.route = route
                if event.usage is not None:
                    event.usage.cost_usd = 0.0
                yield event
            return
        price = journal.prices.get(route)
        if price is None:
            raise SpendDenied(f"no pinned model-spend contract for {route}")
        bounds = NATIVE_BOUNDS.get((price.transport, price.base_url, price.model))
        if bounds is None:
            raise SpendDenied("model/endpoint lacks an audited native token ceiling")
        if price.input_bound < bounds[0] or price.max_output > bounds[1]:
            raise SpendDenied("policy must reserve the full native context and obey its output ceiling")
        expected = AnthropicProvider if price.transport == "anthropic" else OpenAIChatProvider
        if type(provider) is not expected or provider.base_url != price.base_url:
            raise SpendDenied("model-spend contract does not cover this transport or endpoint")
        if provider.extra_headers or provider.session_header:
            raise SpendDenied("custom or beta headers are outside the audited native model contract")
        if any(part.type == "image" for message in messages
               for part in [*message.content, *(p for r in message.tool_results for p in r.content)]):
            raise SpendDenied("image billing is outside the pinned text model-spend contract")
        output = _positive(opts.get("max_tokens", price.max_output))
        if output > price.max_output:
            raise SpendDenied("requested output exceeds the pinned model-spend contract")
        body = provider.build_body(messages, tools, **{**opts, "max_tokens": output})
        limit = body.get("max_completion_tokens", body.get("max_tokens"))
        if body.get("model") != price.model or limit != output:
            raise SpendDenied("prepared request does not match the pinned model-spend contract")
        token = journal.reserve(route, str(opts.get("session_id") or ""), price.maximum(output), price)
        stream = provider.stream_prepared(body, session_id=opts.get("session_id"), require_usage=True)
        async for event in stream:
            event.route = route
            if event.kind == "error":
                raise SpendDenied("model call failed with uncertain spend; reservation retained")
            if event.kind == "done":
                if event.usage is None:
                    raise SpendDenied("missing final usage; reservation retained")
                actual = price.actual(event.usage, output, provider.input_includes_cache
                                      if hasattr(provider, "input_includes_cache") else False)
                journal.settle(token, actual)
                settled = True
                event.usage.cost_usd = actual / SCALE
                event.attempts = [{"route": route, "outcome": "ok"}]
                if event.message is not None:
                    event.message.meta.setdefault("model", route)
                yield event
                return
            # Do not expose untrusted cost estimates or incomplete token totals as settled spend.
            if event.kind != "usage":
                yield event
        raise SpendDenied("model stream ended without final usage; reservation retained")
    except Exception as exc:
        message = str(exc) if isinstance(exc, SpendDenied) else "model spend boundary failed closed"
        try:
            journal.freeze(message)
        except Exception:
            pass
        event = error_event(f"Model spend stopped: {message}. Ask the principal to review the policy/journal; "
                            "approval replies cannot raise the cap.", None, retryable=False)
        event.route = route
        event.attempts = [{"route": route, "outcome": "spend_stopped"}]
        yield event
    finally:
        if token is not None and not settled:
            # Cancellation, failed accounting, and unknown network outcomes require explicit reconciliation.
            try:
                journal.freeze("uncertain model spend; ask the principal to reconcile retained reservations")
            except Exception:
                pass  # original reservation is already durable; subsequent admission still checks the journal
        if stream is not None:
            await stream.aclose()
