"""``exo models --prices``: the price table a reader can check (D-090).

The point of the command is that every number on screen says where it came from and how old it is, and
that a route with no price reads ``unknown`` rather than a confident ``0``. No network, no keys.
"""

from __future__ import annotations

import datetime as dt

import pytest

from exo.core.models.router import Router
from exo.models_cmd import render_prices

WED_PEAK = dt.datetime(2026, 10, 28, 1, 30, tzinfo=dt.timezone.utc)     # inside the weekday window
WED_GAP = dt.datetime(2026, 10, 28, 5, 0, tzinfo=dt.timezone.utc)       # between the two windows
TODAY = dt.date(2026, 10, 28)

DEEPSEEK = {
    "input": 0.30,
    "output": 1.20,
    "cache_read": 0.006,
    "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "verified": "2026-10-28",
    "window": [
        {"days": ["mon", "tue", "wed", "thu", "fri"], "hours": ["01:00-04:00", "06:00-10:00"],
         "input": 0.15, "output": 0.60, "cache_read": 0.003},
    ],
}


def router(models: dict) -> Router:
    return Router.from_config({"models": models}, env={})


def test_a_window_in_force_shows_its_discounted_rates_and_names_the_window():
    out = render_prices(router({"deepseek:deepseek-flash": DEEPSEEK}), today=TODAY, at=WED_PEAK)
    rate_line = next(line for line in out.splitlines() if "deepseek:deepseek-flash" in line)
    assert "input 0.15" in rate_line and "output 0.6" in rate_line
    assert "window in force" in out


def test_outside_the_windows_the_standard_rate_shows_and_is_called_peak():
    out = render_prices(router({"deepseek:deepseek-flash": DEEPSEEK}), today=TODAY, at=WED_GAP)
    rate_line = next(line for line in out.splitlines() if "deepseek:deepseek-flash" in line)
    assert "input 0.3" in rate_line and "output 1.2" in rate_line
    assert "peak rate" in out


def test_every_row_names_its_source_and_age():
    out = render_prices(router({"anthropic:claude-opus-5-5": {
        "input": 4.0, "output": 20.0,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
        "verified": "2026-10-28",
    }}, ), today=TODAY, at=WED_GAP)
    assert "https://platform.claude.com/docs/en/about-claude/pricing" in out
    assert "read 0d ago" in out


def test_a_route_without_a_price_reads_unknown_never_zero():
    out = render_prices(router({"p:m": {"context": 8192}}), today=TODAY, at=WED_GAP)
    line = next(line for line in out.splitlines() if "p:m" in line)
    assert "unknown" in line
    assert "never $0" in out
    assert "input" not in line and "output" not in line  # no invented rate on the unpriced row


def test_an_old_date_is_reported_as_stale_and_an_undated_entry_as_a_problem():
    stale = {"p:old": {"input": 1.0, "source": "https://example.invalid/prices", "verified": "2026-01-01"}}
    undated = {"p:undated": {"input": 1.0}}
    out = render_prices(router({**stale, **undated}), today=TODAY, at=WED_GAP)
    assert "not re-read in over 90 days (or undated):" in out
    assert "p:old (read 300d ago)" in out
    assert "p:undated (no date)" in out
    assert "problems:" in out
    assert any("p:undated" in line for line in out.splitlines() if "no source" in line or "undated" in line)


def test_an_unreadable_window_is_reported_rather_than_priced():
    broken = {**DEEPSEEK, "window": [{"hours": ["not-a-time"], "input": 0.15}]}
    out = render_prices(router({"deepseek:deepseek-flash": broken}), today=TODAY, at=WED_PEAK)
    assert "problems:" in out
    assert "window in force" not in out


@pytest.mark.parametrize("at", [None, WED_PEAK])
def test_the_table_states_the_unit_and_the_rule(at):
    out = render_prices(router({"p:m": {"input": 1.0, "output": 2.0,
                                        "source": "https://example.invalid/prices",
                                        "verified": "2026-10-28"}}), today=TODAY, at=at)
    assert "prices (USD per million tokens" in out
    assert "a window replaces the rates it names" in out
