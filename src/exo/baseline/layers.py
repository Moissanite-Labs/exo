"""Taste layers and per-item lookup (D-044 wave 1, docs/TASTE-BASELINE.md section 2).

Three layers: the **house** pack (shipped, read-only, versioned), the **user** layer (beliefs learned from
the user's own signals, and their rulings) and **pins** (ledger notes from the user). For each house item
the first hit wins:

1. a ruling by the user that matches the item;
2. a pin, on the house item or on the learned item that matches it;
3. an active learned item that overrides it (`overrides: <id>`, or `exo.taste.relation` says the
   signatures match), including a scoped one whose scope matches the current work;
4. the house item itself, unless the user turned it off.

Learned items that match no house item are listed too. Every entry carries its source tag:
`[house 1.3]`, `[learned 0.72, 5 weeks]`, `[you said, 2026-10-14]`, `[pinned]`.

House precedence falls slowly from install day and never reaches zero:
`P_base(t) = max(0.3, 0.7 * 2^(-t / 365))`. Inside the getting-to-know window (30 days and 20 sessions, at
most 90 days) a house value item starts at 0.5 instead. The install day is the store ledger's first event.

The house layer is off when the pack is not final (exo.baseline.pack), when `taste: house: off` is set in
`policies/consolidate.yaml` (start blank), or when the store is the pack's own source (the principal's
install: the manifest's `source_id` matches), so nothing counts twice.

Wave 1 reads pins and offs (`note` events of type `taste_pin`, `taste_unpin`, `taste_off`, `taste_on`
written by the principal); the commands that write them come with `/taste` in wave 3.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import taste
from ..store import PrincipalStore
from .pack import HouseItem, HousePack, load_pack, sha256

DEFAULTS = {
    "house": True,
    "baseline_start": 0.7,
    "baseline_half_life": 365.0,
    "baseline_floor": 0.3,
    "value_window_start": 0.5,
    "value_window_days": 30,
    "value_window_sessions": 20,
    "value_window_max_days": 90,
}
PIN_TYPES = ("taste_pin", "taste_unpin", "taste_off", "taste_on")


def taste_policy(store: PrincipalStore | None) -> dict:
    """The `taste:` table of `policies/consolidate.yaml` over the defaults."""
    pol = dict(DEFAULTS)
    path = store.root / "policies" / "consolidate.yaml" if store is not None else None
    if path is not None and path.is_file():
        from ..gate import parse_yaml
        try:
            raw = (parse_yaml(path.read_text(encoding="utf-8")) or {}).get("taste")
        except Exception:
            raw = None
        if isinstance(raw, Mapping):
            for k, v in raw.items():
                if k == "house":
                    pol["house"] = v not in (False, "off", "no", "false", 0)
                elif k in DEFAULTS and isinstance(v, (int, float)):
                    pol[k] = v
                elif k not in DEFAULTS:
                    pol[k] = v
    return pol


def precedence(days: float, start: float = 0.7, half_life: float = 365.0, floor: float = 0.3) -> float:
    """P_base(t) = max(floor, start * 2^(-t / half_life)); t in days since install."""
    return max(floor, start * math.pow(2.0, -max(days, 0.0) / half_life))


def house_weight(item: HouseItem, days: float, in_window: bool, pol: Mapping) -> float:
    p = precedence(days, pol["baseline_start"], pol["baseline_half_life"], pol["baseline_floor"])
    if item.cls == "value" and in_window:
        return min(p, pol["value_window_start"])
    return p


def in_window(days: float, sessions: int, pol: Mapping) -> bool:
    if days >= pol["value_window_max_days"]:
        return False
    return days < pol["value_window_days"] or sessions < pol["value_window_sessions"]


# --- the user layer -----------------------------------------------------------

@dataclass
class UserItem:
    id: str
    claim: str
    sig: frozenset[str]
    weight: float
    source: str                   # ruling | learned
    since: str
    last: str
    type: str = ""
    overrides: str | None = None
    scope: str | None = None

    @property
    def weeks(self) -> int:
        return max(1, round(taste.age_days(self.since, self.last) / 7))


@dataclass
class UserLayer:
    items: list[UserItem] = field(default_factory=list)
    pins: set[str] = field(default_factory=set)
    offs: set[str] = field(default_factory=set)
    installed: str | None = None   # ts of the ledger's first event
    sessions: int = 0
    source_id: str = ""


def _now(now: str | datetime | None) -> str:
    if now is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return now if isinstance(now, str) else now.isoformat()


def user_layer(store: PrincipalStore, now: str | datetime | None = None) -> UserLayer:
    """Accepted beliefs with their current weight, the principal's pins and offs, and the install facts.
    Read-only: the ledger is opened without a signer and nothing is appended."""
    layer = UserLayer()
    if not store.ledger_path.is_file():
        return layer
    from .. import provenance
    from ..consolidate import load_policy, load_state
    from ..ledger import Ledger
    now_s = _now(now)
    half_lives = load_policy(store)["half_life_days"]
    principals = provenance.principal_names(store)
    with Ledger(store.ledger_path) as led:
        st = load_state(led)
        extra: dict[str, dict] = {}
        for ev in led.iter(kind=("belief_proposed", "note")):
            b = ev["body"] or {}
            if ev["kind"] == "belief_proposed" and b.get("proposal_id"):
                extra[b["proposal_id"]] = {"overrides": b.get("overrides"), "scope": b.get("scope")}
            elif b.get("type") in PIN_TYPES and b.get("item") and ev.get("author") in principals:
                iid = str(b["item"])
                if b["type"] == "taste_pin":
                    layer.pins.add(iid)
                elif b["type"] == "taste_unpin":
                    layer.pins.discard(iid)
                elif b["type"] == "taste_off":
                    layer.offs.add(iid)
                else:
                    layer.offs.discard(iid)
        first = led.db.execute("SELECT id, ts FROM events ORDER BY seq LIMIT 1").fetchone()
        if first:
            layer.installed = first[1]
            layer.source_id = sha256(("exo-house-source\n" + first[0]).encode())
        layer.sessions = led.db.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'note' AND json_extract(body, '$.type') = 'session_start'"
        ).fetchone()[0]
    for pid, b in sorted(st.beliefs.items()):
        if b.status != "accepted":
            continue
        p = st.proposals.get(pid)
        ex = extra.get(pid, {})
        layer.items.append(UserItem(pid, b.claim, b.sig, b.weight(now_s, half_lives),
                                    "ruling" if p is not None and p.kind == "ruling" else "learned",
                                    min((e.ts for e in b.evidence), default=b.created), b.last_seen, b.type,
                                    ex.get("overrides"), ex.get("scope")))
    return layer


# --- resolution ---------------------------------------------------------------

@dataclass
class Entry:
    id: str
    claim: str
    source: str                   # house | learned | ruling | pinned
    tag: str
    weight: float
    cls: str = "method"
    house_id: str | None = None


def _learned_tag(u: UserItem) -> str:
    if u.source == "ruling":
        return f"[you said, {u.since[:10]}]"
    weeks = f"{u.weeks} week" + ("s" if u.weeks != 1 else "")
    return f"[learned {u.weight:.2f}, {weeks}" + (f", {u.scope}" if u.scope else "") + "]"


def _matches(u: UserItem, h: HouseItem) -> bool:
    return u.overrides == h.id or (u.overrides is None and taste.relation(u.sig, h.sig) is not None)


def resolve(house: Sequence[HouseItem], user: Sequence[UserItem], *, pins: set[str] = frozenset(),
            offs: set[str] = frozenset(), weight_of=None, version: str = "", start: float = 0.7,
            scope: str | None = None) -> list[Entry]:
    """Per-item lookup: ruling, pin, learned (or scoped), house. `house` is empty when the layer is off.
    `weight_of(item)` gives a house item's precedence. Returns entries by weight, highest first."""
    weight_of = weight_of or (lambda h: start)
    active = [u for u in user if u.scope is None or u.scope == scope]
    used: set[str] = set()
    out: list[Entry] = []

    def from_user(u: UserItem, h: HouseItem | None) -> Entry:
        used.add(u.id)
        pinned = u.id in pins
        cls = "value" if u.type == "value" else (h.cls if h else "method")
        return Entry(u.id, u.claim, "pinned" if pinned else u.source, "[pinned]" if pinned else _learned_tag(u),
                     u.weight, cls, h.id if h else None)

    for h in house:
        matches = sorted((u for u in active if _matches(u, h)), key=lambda u: (-u.weight, u.id))
        ruling = next((u for u in matches if u.source == "ruling"), None)
        pinned_user = next((u for u in matches if u.id in pins), None)
        if ruling is not None:
            winner = ruling
        elif h.id in pins:
            winner = None
        else:
            winner = pinned_user or (matches[0] if matches else None)
        if winner is not None:
            if winner.id not in used:          # one learned item that covers two house items is listed once
                out.append(from_user(winner, h))
        elif h.id in pins:
            out.append(Entry(h.id, h.claim, "pinned", "[pinned]", start, h.cls, h.id))
        elif h.id not in offs:
            out.append(Entry(h.id, h.claim, "house", f"[house {version}]", weight_of(h), h.cls, h.id))
        used.update(u.id for u in matches)
    for u in active:
        if u.id not in used:
            out.append(from_user(u, None))
    out.sort(key=lambda e: (-e.weight, e.id))
    return out


# --- the house layer on one store ---------------------------------------------

@dataclass
class TasteView:
    house_on: bool
    reason: str
    pack: HousePack
    entries: list[Entry]
    days: float = 0.0
    window: bool = False


def house_status(pack: HousePack, layer: UserLayer, pol: Mapping) -> tuple[bool, str]:
    if not pack.final:
        return False, pack.problem
    if not pol.get("house", True):
        return False, "turned off in policies/consolidate.yaml (taste: house: off)"
    if pack.source_id and pack.source_id == layer.source_id:
        return False, "this store is the house taste's source (the principal's own install)"
    return True, f"house taste {pack.version}"


def view(store: PrincipalStore, *, pack: HousePack | None = None, now: str | datetime | None = None,
         scope: str | None = None) -> TasteView:
    pack = pack if pack is not None else load_pack()
    pol = taste_policy(store)
    layer = user_layer(store, now)
    on, reason = house_status(pack, layer, pol)
    days = taste.age_days(layer.installed, _now(now)) if layer.installed else 0.0
    window = in_window(days, layer.sessions, pol)
    entries = resolve(pack.items if on else (), layer.items, pins=layer.pins, offs=layer.offs,
                      weight_of=lambda h: house_weight(h, days, window, pol), version=pack.short_version,
                      start=pol["baseline_start"], scope=scope)
    return TasteView(on, reason, pack, entries, days, window)


def taste_block(store: PrincipalStore, *, budget_tokens: int = 400, pack: HousePack | None = None,
                now: str | datetime | None = None) -> str:
    """The taste block for the system message: active items by weight, each with its source, cut to the
    budget (low-weight items drop out first; they stay in `exo taste`). Empty when there is nothing."""
    v = view(store, pack=pack, now=now)
    if not v.entries:
        return ""
    head = "# Taste (how this user likes work done; source in brackets)"
    lines, used, limit = [head], len(head), max(budget_tokens, 50) * 4
    for e in v.entries:
        line = f"- {e.claim} {e.tag}"
        if used + len(line) + 1 > limit:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def render(v: TasteView) -> str:
    """`exo taste`: the house layer's state, then every active item with its weight and source."""
    p = v.pack
    if v.house_on:
        n_val = sum(1 for i in p.items if i.cls == "value")
        head = (f"house: on, {v.reason} ({len(p.items)} items: {len(p.items) - n_val} method, {n_val} value); "
                f"day {int(v.days)}" + (", getting-to-know window" if v.window else ""))
    else:
        head = f"house: off ({v.reason})"
    lines = [head]
    if not v.entries:
        lines.append("(no taste items yet)")
    for e in v.entries:
        lines.append(f"{e.weight:.2f}  {e.cls:<6} {e.claim} {e.tag}  {e.id}")
    return "\n".join(lines)
