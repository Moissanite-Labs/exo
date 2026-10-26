"""The sustained-change rule on one store: read the ledger, replay, write what changed (D-044 wave 2).

    state = adapt.load(store)                     # read-only: every tracked item's state now
    adapt.close_weeks(store, ledger)              # write the outcomes the replay found that are not yet written

Inputs, all from the store's ledger:

- `note` type `taste_signal`: a labelled signal (exo.baseline.classify). It counts only when the event it
  cites (`evidence`) exists and was written by the principal (D-040). The note itself is the learner's.
- `note` type `session_start`, and the principal's own utterances and verdicts: which weeks were active.
- The principal's acts, `note` events written by the principal only (D-052): `taste_mode`, `taste_pin`,
  `taste_unpin`, `taste_off`, `taste_on`, `taste_answer` (yes, no, later), `taste_undo`, `taste_restore`,
  `taste_reset`.

Outputs (each written once, keyed by `key`): `taste_replaced` with the challenger as an accepted belief that
`overrides` the item, `taste_scoped` (the same, limited to a scope), `value_stepped_back`, `value_adopted`
(an accepted value belief), `taste_restored`, `taste_proposed` (ask mode), `taste_question` (a question in the
queue), and `taste_ask_quiet` (proposals stop reaching the brief after `ask_quiet_after` unanswered in a row).

Mode: the newest `taste_mode` note wins; else `taste: mode` in `policies/consolidate.yaml`; else ask. New
stores get `mode: silent` from the template, so a store without the key is an older one, which is the
principal's own (D-072).
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path

from .. import taste
from ..store import PrincipalStore
from . import shift
from .layers import UserLayer, house_status, house_weight, in_window, taste_policy, user_layer
from .pack import HousePack, load_pack

ACT_TYPES = {"taste_pin": "pin", "taste_unpin": "unpin", "taste_answer": "", "taste_undo": "undo",
             "taste_restore": "restore", "taste_reset": "reset"}
OUTCOME_NOTE = {"swapped": "taste_replaced", "scoped": "taste_scoped", "stepped_back": "value_stepped_back",
                "adopted": "value_adopted", "restored": "taste_restored", "proposed": "taste_proposed",
                "question": "taste_question"}
NOTE_TYPES = frozenset(OUTCOME_NOTE.values()) | {"taste_ask_quiet"}
AGENT = "exo-learn"


def zone(pol: Mapping) -> tzinfo:
    name = str(pol.get("timezone") or "")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _now(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return taste.parse_ts(now)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Item:
    subject: shift.Subject
    track: shift.Track
    house: bool
    origin: date


@dataclass
class State:
    """Everything /taste and the prompt need: the mode, every tracked item, and what is already written."""
    mode: str
    mode_source: str
    items: dict[str, Item] = field(default_factory=dict)
    outcomes: list[shift.Outcome] = field(default_factory=list)
    written: dict[str, dict] = field(default_factory=dict)       # key -> note body
    acts: list[shift.Act] = field(default_factory=list)
    pack: HousePack | None = None
    house_on: bool = False
    reason: str = ""
    layer: UserLayer | None = None
    pol: dict = field(default_factory=dict)
    tz: tzinfo = timezone.utc
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def track(self, iid: str) -> shift.Track | None:
        it = self.items.get(iid)
        return it.track if it else None

    @property
    def stepped_back(self) -> set[str]:
        return {i for i, it in self.items.items() if it.track.status == "stepped_back"}

    def weights(self) -> dict[str, float]:
        """Prompt weights for armed items (faded in silent mode, held at arming in ask mode)."""
        return {i: it.track.prompt_weight for i, it in self.items.items() if it.track.status == "armed"}


def default_mode(pol: Mapping, pack: HousePack, layer: UserLayer) -> tuple[str, str]:
    if pol.get("mode") in ("ask", "silent"):
        return pol["mode"], "policies/consolidate.yaml"
    if pack.source_id and pack.source_id == layer.source_id:
        return "ask", "the principal's install"
    return "ask", "default for a store without taste: mode"


def _principals(store: PrincipalStore) -> set[str]:
    from .. import provenance
    return set(provenance.principal_names(store))


def read_ledger(store: PrincipalStore, principals: set[str], now: datetime | None = None) -> dict:
    """Signals (verified), active days, sessions per day, acts, mode notes and written outcomes."""
    out: dict = {"signals": [], "days": [], "sessions": [], "acts": [], "modes": [], "written": {}, "beliefs": {}}
    if not store.ledger_path.is_file():
        return out
    from ..consolidate import load_state
    from ..ledger import Ledger
    with Ledger(store.ledger_path) as led:
        st = load_state(led)
        out["beliefs"] = {pid: (b, st.proposals.get(pid)) for pid, b in st.beliefs.items()}
        seen: set[tuple[str, str]] = set()
        for ev in led.iter(kind=("note", "utterance", "verdict")):
            if now is not None and taste.parse_ts(ev["ts"]) > now:
                continue
            b = ev["body"] or {}
            if ev["kind"] != "note":
                if ev.get("author") in principals:
                    out["days"].append(ev["ts"])
                continue
            t = b.get("type")
            if t == "session_start":
                out["days"].append(ev["ts"])
                out["sessions"].append(ev["ts"])
            elif t == "taste_signal" and b.get("item") and b.get("relation") in ("contradict", "agree"):
                if ev.get("author") != AGENT:
                    continue
                src = led.get(str(b.get("evidence") or ""))
                if (src is None or src.get("author") not in principals
                        or src.get("kind") not in ("utterance", "verdict")
                        or src.get("kind") == "utterance" and (src.get("body") or {}).get("role", "user") != "user"):      # D-040: only the user's own
                    continue
                if (b["item"], src["id"]) in seen:
                    continue
                seen.add((b["item"], src["id"]))
                out["signals"].append(_signal(b, src))
            elif t in NOTE_TYPES and b.get("key") and ev.get("author") == AGENT:
                out["written"][b["key"]] = {**b, "ts": ev["ts"]}
            elif ev.get("author") not in principals:
                continue                                                   # D-052: acts are the principal's
            elif t == "taste_mode" and b.get("mode") in ("ask", "silent"):
                out["modes"].append((ev["ts"], b["mode"]))
            elif t in ACT_TYPES and (b.get("item") or t == "taste_reset"):
                verb = ACT_TYPES[t] or str(b.get("answer") or "")
                if verb in ("pin", "unpin", "yes", "no", "later", "undo", "restore", "reset"):
                    out["acts"].append(shift.Act(ev["ts"], verb, str(b.get("item") or "*"),
                                                 str(b.get("proposal") or "")))
    return out


def _signal(b: Mapping, src: Mapping) -> shift.Signal:
    def num(k, d=None):
        try:
            return float(b[k]) if b.get(k) is not None else d
        except (TypeError, ValueError):
            return d
    return shift.Signal(str(b["item"]), str(src["ts"]), str(b["relation"]), num("delta", 0.0) or 0.0,
                        num("strength", shift.INFERRED) or shift.INFERRED, src.get("session") or b.get("session"),
                        str(b.get("scope") or "general"), b.get("project"), b.get("task"), b.get("voice"),
                        num("confidence"), bool(b.get("stated")), str(b.get("claim") or ""), src["id"])


def load(store: PrincipalStore, *, now: str | datetime | None = None, pack: HousePack | None = None) -> State:
    """Replay every tracked item up to `now`. Read-only."""
    now_dt = _now(now)
    pack = pack if pack is not None else load_pack()
    pol = taste_policy(store)
    tz = zone(pol)
    layer = user_layer(store, _iso(now_dt))
    on, reason = house_status(pack, layer, pol)
    principals = _principals(store)
    data = read_ledger(store, principals, now_dt)
    mode0, src = default_mode(pol, pack, layer)
    modes = sorted(data["modes"])
    mode = modes[-1][1] if modes else mode0
    st = State(mode, "/taste mode" if modes else src, pack=pack, house_on=on, reason=reason, layer=layer, pol=pol,
               tz=tz, now=now_dt, written=data["written"], acts=data["acts"])

    def mode_at(ts: str) -> str:
        m = mode0
        for t, x in modes:
            if t <= ts:
                m = x
        return m

    installed = taste.parse_ts(layer.installed) if layer.installed else now_dt
    inst_day = installed.astimezone(tz).date()
    days = sorted(shift.local_day(d, tz) for d in data["days"])
    starts = sorted(data["sessions"])

    def window_at(ts: str) -> bool:
        n = sum(1 for d in starts if d <= ts)
        return in_window(taste.age_days(installed, ts), n, pol)

    today = now_dt.astimezone(tz).date()
    subjects: list[tuple[shift.Subject, bool, date]] = []
    if on:
        def house_innate(h):
            def f(d: date) -> float:
                age = (d - inst_day).days
                ts = _iso(datetime(d.year, d.month, d.day, 12, tzinfo=tz))
                return house_weight(h, age, h.cls == "value" and window_at(ts), pol)
            return f
        for h in pack.items:
            subjects.append((shift.Subject(h.id, h.claim, h.cls, "house", False, layer.installed or "",
                                           house_innate(h)), True, inst_day))
    from ..consolidate import load_policy
    consolidate_policy = load_policy(store)
    half_lives = consolidate_policy["half_life_days"]
    pol["guard_words"] = consolidate_policy["guard_words"]
    for u in layer.items:
        b, p = data["beliefs"].get(u.id, (None, None))
        if b is None:
            continue
        created = taste.parse_ts(b.created).astimezone(tz).date()

        def learned_innate(d: date, b=b) -> float:
            return b.weight(_iso(datetime(d.year, d.month, d.day, 12, tzinfo=tz)), half_lives)
        subjects.append((shift.Subject(u.id, u.claim, "value" if u.type == "value" else "method", u.source,
                                       bool(b.guarded), b.created, learned_innate), False, created))
    for sub, house, origin in subjects:
        active = {shift.week_index(d, origin) for d in days if d >= origin}
        t = shift.replay(sub, data["signals"], origin=origin, today=today, tz=tz, active_weeks=active,
                         acts=data["acts"], mode_at=mode_at, window_at=window_at, pol=pol)
        st.items[sub.id] = Item(sub, t, house, origin)
        st.outcomes += t.outcomes
    st.outcomes.sort(key=lambda o: o.at)
    return st


# --- writing ---------------------------------------------------------------------

def _pid(key: str) -> str:
    return "p-taste-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def _belief(ledger, st: State, o: shift.Outcome, author: str) -> str:
    """The challenger, or the adopted view, as an accepted learned belief that overrides the item."""
    from ..consolidate import Proposal, load_state
    it = st.items[o.item]
    claim = (o.data.get("claim") or "").strip() or f"The user's own way, not: {it.subject.claim}"
    pid = _pid(o.key)
    ev = o.data.get("evidence") or [{"id": f"outcome:{o.key}", "ts": o.at, "strength": shift.INFERRED}]
    typ = "value" if it.subject.cls == "value" else "preference"
    w = taste.combine(((e["strength"], taste.age_days(e["ts"], o.at)) for e in ev), 30.0)
    domain = "mind"
    p = Proposal(pid, "taste_shift", "create", f"taste:{o.item}", claim, claim, None, "", "", domain, typ,
                 claim[:60], taste.label(w), round(w, 4), sorted(taste.signature(claim, None)), ev, {}, False,
                 "taste-shift", o.at)
    body = p.body()
    body["overrides"] = o.item
    if o.data.get("scope"):
        body["scope"] = o.data["scope"]
    existing = load_state(ledger)
    if pid not in existing.proposals:
        ledger.append("belief_proposed", body, author=author)
    if pid not in existing.beliefs or existing.beliefs[pid].status != "accepted":
        ledger.append("belief_accepted", {"proposal_id": pid, "node_id": None, "rule": "taste_shift"}, author=author)
    return pid


def close_weeks(store: PrincipalStore, ledger, *, now: str | datetime | None = None, pack: HousePack | None = None,
                author: str | None = None) -> list[dict]:
    """Write each outcome the replay found that is not written yet. Returns the new notes' bodies."""
    st = load(store, now=now, pack=pack)
    author = author or AGENT
    if author != AGENT:
        raise ValueError("taste outcomes must use the learner author")
    made: list[dict] = []
    for o in st.outcomes:
        if o.key in st.written:
            continue
        body: dict = {"type": OUTCOME_NOTE[o.kind], "key": o.key, "item": o.item, "at": o.at, "week": o.week + 1,
                      "claim": st.items[o.item].subject.claim}
        if o.kind in ("swapped", "scoped", "adopted"):
            body["proposal_id"] = _pid(o.key)
            body["yours"] = o.data.get("claim") or ""
            for k in ("scope", "challenger", "weight", "weeks"):
                if o.data.get(k) is not None:
                    body[k] = o.data[k]
            _belief(ledger, st, o, author)
        elif o.kind == "stepped_back":
            body["why"] = o.data.get("why", "")
        elif o.kind == "proposed":
            body.update(change=o.data.get("change"), yours=o.data.get("claim") or "", scope=o.data.get("scope"),
                        brief=not _ask_quiet(st))
        elif o.kind == "question":
            body.update(why=o.data.get("why"), text=o.data.get("text"))
            _question(store, ledger, body)
        ledger.append("note", body, author=author)
        st.written[o.key] = body
        made.append(body)
        if o.kind == "proposed" and not body["brief"] and not _quiet_noticed(st):
            q = {"type": "taste_ask_quiet", "key": f"ask_quiet:{o.at}", "at": o.at,
                 "text": f"{st.pol['ask_quiet_after']} taste proposals in a row went unanswered, so new ones now "
                         "wait in /taste and no longer appear in the brief."}
            ledger.append("note", q, author=author)
            st.written[q["key"]] = q
            made.append(q)
    return made


def _proposals(st: State) -> list[dict]:
    rows = [b for b in st.written.values() if b.get("type") == "taste_proposed"]
    return sorted(rows, key=lambda b: b.get("at") or "")


def answered(st: State, prop: Mapping) -> bool:
    """Has the principal answered this proposal (yes, no or later)?"""
    return any(a.verb in ("yes", "no", "later") and a.item == prop.get("item") and a.ts >= str(prop.get("at") or "")
               for a in st.acts)


def _ask_quiet(st: State) -> bool:
    """True once `ask_quiet_after` proposals in a row are unanswered (section 6)."""
    run = 0
    for p in _proposals(st):
        run = run + 1 if not answered(st, p) else 0
    return run >= int(st.pol["ask_quiet_after"])


def _quiet_noticed(st: State) -> bool:
    last_q = max((b.get("at") or "" for b in st.written.values() if b.get("type") == "taste_ask_quiet"), default="")
    last_answer = max((p.get("at") or "" for p in _proposals(st) if answered(st, p)), default="")
    return bool(last_q) and last_q >= last_answer


def _question(store: PrincipalStore, ledger, body: Mapping) -> None:
    from ..ask import QuestionQueue
    try:
        q = QuestionQueue(store.root, ledger=ledger)
        src = f"taste:{body['key']}"
        if not any(x.source == src for x in q.state().values()):
            q.propose(str(body.get("text") or ""), rationale="the sustained-change rule (D-044)", impact=0.5,
                      uncertainty=0.6, source=src, domain="mind")
    except Exception:
        pass


# --- the brief -------------------------------------------------------------------

def brief_lines(store: PrincipalStore, since: str, *, now: str | datetime | None = None) -> list[str]:
    """Lines for the morning brief: swaps, scoped items, step backs, adoptions and restores written since
    `since`, a count of new proposals (unless they have gone quiet), and the one quiet notice."""
    path = Path(store.ledger_path)
    if not path.is_file():
        return []
    from ..ledger import Ledger
    rows = []
    with Ledger(path) as led:
        for ev in led.iter(kind="note"):
            b = ev["body"] or {}
            if b.get("type") in NOTE_TYPES and ev["ts"] >= since and ev.get("author") == AGENT:
                rows.append(b)
    out, new_props = [], 0
    for b in rows:
        t, item = b["type"], b.get("item", "")
        if t == "taste_replaced":
            out.append(f"Switched to your way: {b.get('yours') or 'your version'} (instead of '{b.get('claim')}'), "
                       f"after {b.get('weeks') or 'several'} weeks. Undo: /taste undo {item}")
        elif t == "taste_scoped":
            out.append(f"Your way in {b.get('scope')}: {b.get('yours')}. The house item still applies elsewhere. "
                       f"Undo: /taste undo {item}")
        elif t == "value_stepped_back":
            out.append(f"Stepped back from '{b.get('claim')}'; staying neutral on this until your view is clear. "
                       f"Restore: /taste restore {item}")
        elif t == "value_adopted":
            out.append(f"Took on your view: {b.get('yours')} Undo: /taste undo {item}")
        elif t == "taste_restored":
            out.append(f"Restored '{b.get('claim')}': you went back to it. Undo: /taste reset {item}")
        elif t == "taste_proposed" and b.get("brief", True):
            new_props += 1
        elif t == "taste_ask_quiet":
            out.append(str(b.get("text")))
    if new_props:
        out.append(f"{new_props} taste change{'s' if new_props != 1 else ''} waiting for your yes in /taste.")
    return out
