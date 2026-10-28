"""Price windows and price provenance (D-090).

The rates a call is charged must be the rates published for that moment: a vendor that bills by
clock time (DeepSeek peak and off-peak) publishes two, and charging the peak rate all week
over-reports while charging the off-peak rate all week under-reports. These tests pin the window
reading in ``exo.core.models.prices`` and the charge the router makes from it. No network.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

import pytest

from exo.core.models import prices
from exo.core.models.router import CATALOG_PATH, Route, Router
from exo.core.types import Usage

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


def at(text: str) -> dt.datetime:
    """A UTC moment from "YYYY-MM-DD HH:MM"."""
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


# 2026-10-28 is a Wednesday, 2026-10-31 a Saturday.
WED_PEAK = at("2026-10-28 01:00")
WED_BEFORE_PEAK = at("2026-10-28 00:59")
WED_AFTER_PEAK = at("2026-10-28 04:00")
WED_GAP = at("2026-10-28 05:00")
WED_SECOND_PEAK = at("2026-10-28 09:59")
SAT_PEAK_HOURS = at("2026-10-31 09:59")


def test_off_peak_rates_apply_inside_a_weekday_window():
    assert prices.rates_at(DEEPSEEK, WED_PEAK)["input"] == 0.15
    assert prices.rates_at(DEEPSEEK, WED_SECOND_PEAK)["output"] == 0.60


@pytest.mark.parametrize("moment", [WED_BEFORE_PEAK, WED_AFTER_PEAK, WED_GAP, SAT_PEAK_HOURS])
def test_the_published_default_rates_apply_outside_the_windows(moment):
    rates = prices.rates_at(DEEPSEEK, moment)
    assert (rates["input"], rates["output"]) == (0.30, 1.20)


def test_a_window_replaces_only_the_rates_it_names():
    info = {**DEEPSEEK, "window": [{"hours": ["01:00-04:00"], "input": 0.15}]}
    rates = prices.rates_at(info, WED_PEAK)
    assert rates["input"] == 0.15
    assert (rates["output"], rates["cache_read"]) == (1.20, 0.006)


def test_a_window_without_days_or_hours_is_always_in_force():
    info = {**DEEPSEEK, "window": [{"input": 0.15}]}
    assert prices.rates_at(info, SAT_PEAK_HOURS)["input"] == 0.15


def test_a_window_may_wrap_midnight():
    info = {**DEEPSEEK, "window": [{"hours": ["22:00-02:00"], "input": 0.15}]}
    assert prices.rates_at(info, at("2026-10-28 23:30"))["input"] == 0.15
    assert prices.rates_at(info, at("2026-10-29 01:59"))["input"] == 0.15
    assert prices.rates_at(info, at("2026-10-28 02:00"))["input"] == 0.30
    assert prices.rates_at(info, at("2026-10-28 21:59"))["input"] == 0.30


def test_hours_are_half_open_so_a_rate_changes_once():
    assert prices.rates_at(DEEPSEEK, WED_BEFORE_PEAK)["input"] == 0.30
    assert prices.rates_at(DEEPSEEK, WED_PEAK)["input"] == 0.15
    assert prices.rates_at(DEEPSEEK, WED_AFTER_PEAK)["input"] == 0.30


def test_an_unreadable_window_is_skipped_and_named():
    info = {**DEEPSEEK, "window": [{"hours": ["01:00"], "input": 0.15}]}
    assert prices.rates_at(info, WED_PEAK)["input"] == 0.30  # default rate: over-reports, never under
    said = prices.check(info, "deepseek:deepseek-flash")
    assert any("HH:MM-HH:MM" in line for line in said)


def test_windows_accept_one_table_or_many():
    one = {**DEEPSEEK, "window": {"input": 0.15}}
    many = {**DEEPSEEK, "window": [{"input": 0.15}]}
    assert prices.rates_at(one, WED_PEAK) == prices.rates_at(many, WED_PEAK)


def test_a_route_with_no_price_stays_unpriced():
    assert prices.rates_at({}, WED_PEAK) is None
    assert prices.rates_at(None, WED_PEAK) is None
    assert prices.rates_at({"context": 1000}, WED_PEAK) is None


def test_price_charges_the_window_rate_in_force():
    router = Router(catalog={"models": {"test:peaky": DEEPSEEK}, "providers": {}, "roles": {}})
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    peak = router.price(Route("test", "peaky"), usage, at=WED_PEAK)
    off = router.price(Route("test", "peaky"), usage, at=WED_GAP)
    assert peak.cost_usd == pytest.approx(0.15 + 0.06 + 0.003)
    assert off.cost_usd == pytest.approx(0.30 + 0.12 + 0.006)
    assert usage.cost_usd is None  # the call's own usage is not mutated


def test_price_defaults_cache_rates_from_the_rate_in_force():
    info = {"input": 1.00, "output": 2.00, "window": [{"input": 0.50}]}
    router = Router(catalog={"models": {"test:peaky": info}, "providers": {}, "roles": {}})
    usage = Usage(input_tokens=1_000_000, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    priced = router.price(Route("test", "peaky"), usage, at=WED_GAP)
    assert priced.cost_usd == pytest.approx(0.50 + 0.05 + 0.625)


def test_a_provider_reported_cost_is_kept():
    router = Router(catalog={"models": {"test:peaky": DEEPSEEK}, "providers": {}, "roles": {}})
    usage = Usage(input_tokens=10, cost_usd=0.25)
    assert router.price(Route("test", "peaky"), usage, at=WED_PEAK).cost_usd == 0.25


def test_provenance_follows_the_rates_in_force():
    info = {**DEEPSEEK, "window": [{"hours": ["01:00-04:00"], "input": 0.15,
                                    "source": "https://example.test/off-peak", "verified": "2026-10-28"}]}
    in_peak = prices.in_force(info, WED_PEAK)
    off_peak = prices.in_force(info, WED_GAP)
    assert in_peak["source"] == "https://example.test/off-peak"
    assert off_peak["source"] == info["source"]  # the window names no source, so the entry's stands


def test_age_and_staleness_are_read_from_the_recorded_date():
    catalog = {"models": {
        "old:priced": {"input": 1.0, "source": "https://example.test", "verified": "2026-01-01"},
        "new:priced": {"input": 1.0, "source": "https://example.test", "verified": "2026-10-01"},
        "undated:priced": {"input": 1.0, "source": "https://example.test"},
        "unpriced": {"context": 1000},
    }}
    today = dt.date(2026, 10, 28)
    rows = prices.stale(catalog, today, max_age_days=90)
    assert [row["route"] for row in rows] == ["undated:priced", "old:priced"]
    assert rows[1]["age_days"] == 300
    assert prices.age_days({"verified": "2026-10-01"}, today) == 27


def test_check_names_missing_provenance_and_bad_rates():
    said = prices.check({"input": 1.0}, "test:bare")
    assert any("source" in line for line in said)
    assert any("verified" in line for line in said)
    assert any("number" in line for line in prices.check({"input": "free"}, "test:bare"))


def test_the_shipped_catalog_is_readable_and_dated():
    catalog = tomllib.loads(Path(CATALOG_PATH).read_text(encoding="utf-8"))
    assert prices.problems(catalog) == []
    today = prices.utc_now().date()
    for ref, info in catalog["models"].items():
        if "input" not in info:
            continue
        assert str(info["source"]).startswith("https://"), ref
        assert dt.date.fromisoformat(info["verified"]) <= today, ref


def test_the_shipped_catalog_has_no_stale_prices():
    catalog = tomllib.loads(Path(CATALOG_PATH).read_text(encoding="utf-8"))
    assert prices.stale(catalog) == []
