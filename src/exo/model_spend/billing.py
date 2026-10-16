"""Pinned native text-billing contracts and exact monetary/token arithmetic.

This public accounting interface has no dependency on core provider or loop types.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import MappingProxyType
from typing import Any, Protocol


class TokenUsage(Protocol):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int


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

# Audited 2026-10-14, re-checked 2026-10-16, native endpoints only.
# https://platform.claude.com/docs/en/about-claude/pricing ("Model pricing", "Long context pricing")
# Tiered long-prompt pricing for native routes, keyed like
# NATIVE_BOUNDS. Value: (prompt-token threshold, worst-case tier rates).
# Worst-case rates are nano-USD per million tokens, (input, output, cache_read,
# cache_write); a class set to None has no audited tier and is not checked.
# These are validation floors only, never charge rates: charge() uses the pin.
# The pin must cover the worst case because input_bound admits prompts above the
# threshold, so maximum() and charge() would otherwise under-price them.
# Routes with a single documented tier have no entry.
# Haiku 5.5 bills $0.10/$0.50 per MTok input/output up to 100K prompt tokens
# (cache reads and writes count toward the threshold; each request is priced on
# its own) and $0.50/$2.50 above it. Opus 5.5 and Sonnet 5.5 bill their full 1M
# context window at flat standard rates; gpt-4.1-2025-04-14 has a single tier in
# the audited snapshot.
# Above the threshold cache reads bill $0.05 and cache writes $0.625 (5-minute
# TTL) or $1.00 (1-hour TTL). The cache_write floor is the 5-minute rate because
# the Anthropic adapter only sends the default ephemeral cache_control; sending a
# 1-hour TTL would need the floor raised to 1_000_000_000.
NATIVE_TIERS = MappingProxyType({
    ("anthropic", "https://api.anthropic.com", "claude-haiku-5-5"):
        (100_000, (500_000_000, 2_500_000_000, 50_000_000, 625_000_000)),
})


class SpendDenied(RuntimeError):
    """Nonretryable request refusal; it does not itself stop other requests."""


class SpendStopped(SpendDenied):
    """An existing global admission stop requires principal review."""


def _money(value: Any, *, ceiling: bool = True) -> int:
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError("spend amounts must be finite and nonnegative")
    n = int((d * SCALE).to_integral_value(rounding=ROUND_CEILING if ceiling else ROUND_FLOOR))
    if n > 10**15:
        raise ValueError("spend amount exceeds supported range")
    return n


def positive_tokens(value: Any) -> int:
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
                   positive_tokens(raw["input_bound"]), positive_tokens(raw["max_output"]),
                   (_money(raw["input"]), _money(raw["output"]), _money(raw["cache_read"]), _money(raw["cache_write"])))

    def charge(self, counts: tuple[int, int, int, int]) -> int:
        return (sum(n * rate for n, rate in zip(counts, self.rates)) + 999_999) // 1_000_000

    def maximum(self, output: int) -> int:
        return (self.input_bound * max(self.rates[0], self.rates[2], self.rates[3])
                + output * self.rates[1] + 999_999) // 1_000_000

    def actual(self, usage: TokenUsage, output: int, includes_cache: bool) -> int:
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


_TIER_CLASS_NAMES = ("input", "output", "cache_read", "cache_write")


def check_tier_rates(price: Price) -> None:
    """Refuse a pin below the audited worst-case tier rate for a route.

    A flat per-class pin must not under-price any prompt size the provider bills
    at a higher tier. Raises ValueError naming the class and model. Routes with
    no entry in NATIVE_TIERS have a single audited tier and are not checked.
    """
    entry = NATIVE_TIERS.get((price.transport, price.base_url, price.model))
    if entry is None:
        return
    _, worst = entry
    for name, pinned, floor in zip(_TIER_CLASS_NAMES, price.rates, worst):
        if floor is not None and pinned < floor:
            raise ValueError(f"pinned {name} rate below audited tier rate for {price.model}")


def admit_native(price: Price) -> None:
    """Refuse a pin outside the audited native contract; raises SpendDenied.

    The route must have a checked-in native ceiling, reserve its full context,
    obey its output ceiling and pin no class below the audited worst-case tier.
    """
    bounds = NATIVE_BOUNDS.get((price.transport, price.base_url, price.model))
    if bounds is None:
        raise SpendDenied("model/endpoint lacks an audited native token ceiling")
    if price.input_bound < bounds[0] or price.max_output > bounds[1]:
        raise SpendDenied("policy must reserve the full native context and obey its output ceiling")
    try:
        check_tier_rates(price)
    except ValueError as exc:
        raise SpendDenied(str(exc)) from exc
