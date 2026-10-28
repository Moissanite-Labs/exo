"""Catalog prices: which published rate is in force right now, and how old the number is.

Rates are USD per million tokens and live in ``catalog.toml`` under ``[models."provider:model"]``
(see docs/PROVIDERS.md, "Prices"). A catalog entry may carry two keys besides the rates:

``source``    the page the number was read from, so a reviewer can check it.
``verified``  the UTC date (``YYYY-MM-DD``) it was read. A price is a claim about a vendor page, and
              vendor pages move; the date is how a stale claim is found without guessing.

A vendor that charges by clock time publishes more than one rate: DeepSeek bills peak and off-peak,
and off-peak is most of the week. Such an entry lists the windows in ``[[models."p:m".window]]``::

    [models."deepseek:deepseek-flash"]
    input = 0.30            # peak, the default published rate
    output = 1.20
    cache_read = 0.006
    source = "https://api-docs.deepseek.com/quick_start/pricing/"
    verified = "2026-10-28"

    [[models."deepseek:deepseek-flash".window]]
    days = ["mon", "tue", "wed", "thu", "fri"]
    hours = ["01:00-04:00", "06:00-10:00"]
    input = 0.15            # off-peak: half of peak
    output = 0.60
    cache_read = 0.003

The entry's own rates are the default, and a window replaces the rates it names only while it
matches. Two rules make an error cheap, because a wrong price is a wrong spend report:

- the default rates are the vendor's standard (highest) published rate, and windows carry the
  discount, so a window that never matches over-reports and never under-reports;
- a window this module cannot read is skipped, and ``problems()`` says so, rather than crashing a
  call or being silently accepted.

Weeks are UTC, matching both vendors that bill by clock time. Days are ``mon``..``sun``; ``hours``
are UTC ``HH:MM-HH:MM`` half-open spans, and a span may wrap midnight (``22:00-02:00``). Neither key
is required: no ``days`` means every day, no ``hours`` means all day. Chinese public holidays are
not modelled; a weekday holiday inside a peak window bills at the peak rate (over-report, the safe
direction).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RATE_KEYS = ("input", "output", "cache_read", "cache_write")
STALE_PRICE_DAYS = 90  # a price older than this is worth a look, not necessarily wrong


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _at(at: float | dt.datetime | None) -> dt.datetime:
    """The moment to price at: now, a POSIX timestamp, or an aware/naive datetime read as UTC."""
    if at is None:
        return utc_now()
    if isinstance(at, dt.datetime):
        return at.replace(tzinfo=dt.timezone.utc) if at.tzinfo is None else at.astimezone(dt.timezone.utc)
    return dt.datetime.fromtimestamp(at, dt.timezone.utc)


def _minutes(span: Any, ref: str) -> tuple[int, int]:
    if not isinstance(span, str) or span.count("-") != 1:
        raise ValueError(f'{ref}: hours must be "HH:MM-HH:MM", got {span!r}')
    start, end = (part.strip() for part in span.split("-"))
    out = []
    for part in (start, end):
        hours, _, minutes = part.partition(":")
        if not hours.isdigit() or not minutes.isdigit() or int(hours) > 24 or int(minutes) > 59:
            raise ValueError(f'{ref}: hours must be "HH:MM-HH:MM", got {span!r}')
        out.append(int(hours) * 60 + int(minutes))
    return out[0], out[1]


def windows(info: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The entry's windows, whether written as one ``[...window]`` table or as ``[[...window]]``."""
    raw = info.get("window")
    if raw is None:
        return []
    return [raw] if isinstance(raw, Mapping) else [w for w in raw if isinstance(w, Mapping)]


def check(info: Mapping[str, Any], ref: str = "") -> list[str]:
    """What is wrong with this entry's windows or provenance, in words. Empty when nothing is."""
    ref = ref or "models entry"
    out: list[str] = []
    days, hours = set(WEEKDAYS), None
    for n, w in enumerate(windows(info)):
        where = f"{ref}: window {n + 1}"
        for key in w:
            if key not in ("days", "hours", "source", "verified") and key not in RATE_KEYS:
                out.append(f"{where}: unknown key {key!r}")
        if "days" in w:
            days = set(w["days"] or WEEKDAYS) if isinstance(w["days"], (list, tuple)) else {"?"}
            bad = days - set(WEEKDAYS)
            if bad or days == {"?"}:
                out.append(f"{where}: days must be a list of {', '.join(WEEKDAYS)}")
        hours = w.get("hours")
        if hours is not None and not isinstance(hours, (list, tuple)):
            out.append(f"{where}: hours must be a list of spans")
        elif hours:
            for span in hours:
                try:
                    _minutes(span, where)
                except ValueError as e:
                    out.append(str(e))
        for key in RATE_KEYS:
            if key in w and not isinstance(w[key], (int, float)):
                out.append(f"{where}: {key} must be a number, got {w[key]!r}")
        if "verified" in w:
            try:
                dt.date.fromisoformat(str(w["verified"]))
            except ValueError:
                out.append(f'{where}: verified must be "YYYY-MM-DD", got {w["verified"]!r}')
    if "verified" in info:
        try:
            dt.date.fromisoformat(str(info["verified"]))
        except ValueError:
            out.append(f'verified must be "YYYY-MM-DD", got {info["verified"]!r}')
    for key in RATE_KEYS:
        if key in info and not isinstance(info[key], (int, float)):
            out.append(f"{key} must be a number, got {info[key]!r}")
    if "input" in info and "source" not in info:
        out.append("a priced entry needs a source (the page the rates were read from)")
    if "input" in info and "verified" not in info:
        out.append("a priced entry needs a verified date (the day the rates were read)")
    return out


def matches(window: Mapping[str, Any], at: float | dt.datetime | None = None) -> bool:
    """Whether this window is in force at ``at`` (UTC). A window that cannot be read does not match."""
    moment = _at(at)
    try:
        days = window.get("days")
        if days is not None and WEEKDAYS[moment.weekday()] not in (days or WEEKDAYS):
            return False
        spans = window.get("hours")
        if not spans:
            return True
        now = moment.hour * 60 + moment.minute
        for span in spans:
            start, end = _minutes(span, "window")
            if start <= end:
                if start <= now < end:
                    return True
            elif now >= start or now < end:  # wraps midnight
                return True
        return False
    except (ValueError, TypeError, AttributeError):
        return False


def rates_at(info: Mapping[str, Any] | None, at: float | dt.datetime | None = None) -> dict[str, float] | None:
    """The rates in force at ``at``: the entry's own rates, replaced by the first matching window.
    None when the entry carries no price (a route with no price is reported as unknown, never as 0)."""
    if not info or "input" not in info:
        return None
    rates = {key: info[key] for key in RATE_KEYS if key in info}
    for window in windows(info):
        if matches(window, at):
            rates.update({key: window[key] for key in RATE_KEYS if key in window})
            break
    return rates


def in_force(info: Mapping[str, Any] | None, at: float | dt.datetime | None = None) -> Mapping[str, Any] | None:
    """The table whose ``source`` and ``verified`` describe the rates ``rates_at`` returns: the matching
    window when it carries its own provenance, else the entry itself."""
    if not info or "input" not in info:
        return None
    for window in windows(info):
        if matches(window, at) and ("source" in window or "verified" in window):
            return {**info, **{"source": window.get("source", info.get("source")),
                               "verified": window.get("verified", info.get("verified"))}}
    return info


def age_days(info: Mapping[str, Any] | None, today: dt.date | None = None) -> int | None:
    """Days since the rates were read, or None when the entry records no date."""
    if not info or "verified" not in info:
        return None
    today = today or utc_now().date()
    try:
        return max((today - dt.date.fromisoformat(str(info["verified"]))).days, 0)
    except ValueError:
        return None


def stale(catalog: Mapping[str, Any], today: dt.date | None = None,
          max_age_days: int = STALE_PRICE_DAYS) -> list[dict[str, Any]]:
    """Every priced route whose recorded date is older than ``max_age_days``, or has none, oldest first.
    This is the maintenance queue: each row is a page to re-read and a one-line PR or issue."""
    out = []
    for ref, info in (catalog.get("models") or {}).items():
        if not isinstance(info, Mapping) or "input" not in info:
            continue
        age = age_days(info, today)
        if age is None:
            out.append({"route": ref, "verified": None, "age_days": None, "source": info.get("source")})
        elif age > max_age_days:
            out.append({"route": ref, "verified": str(info.get("verified")), "age_days": age,
                        "source": info.get("source")})
    out.sort(key=lambda row: (row["age_days"] is not None, -(row["age_days"] or 0)))
    return out


def problems(catalog: Mapping[str, Any]) -> list[str]:
    """Every catalog problem this module can see: unreadable windows, missing provenance, no price."""
    out = []
    for ref, info in (catalog.get("models") or {}).items():
        if not isinstance(info, Mapping):
            out.append(f"{ref}: a model entry must be a table")
            continue
        out.extend(check(info, ref))
    return out
