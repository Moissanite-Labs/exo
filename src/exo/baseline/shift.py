"""The sustained-change rule and the value step back (D-044 wave 2, docs/TASTE-BASELINE.md sections 3 and 4).

Pure functions and a replay over plain data. No I/O, no ledger, no clock: callers pass the signals, the
sessions, the user's acts and the time. `exo.baseline.adapt` reads those from the ledger and writes the
outcomes back.

Method items (section 3):

- Signals about one item in one session or one calendar day join into one **episode** (`episodes`). Its
  verdict is contradict, agree or void by majority; its delta is the mean delta of its contradicting
  signals; its strength is that of its strongest contradicting signal.
- A calendar **week** (Monday to Sunday, user's time zone) is contradicting, agreeing or neutral by a
  majority of its episodes. A week with no session and no episode is skipped.
- The **persistence score** S adds one per contradicting week and takes one per agreeing week, never below 0.
  At 0 the record is cleared.
- An item **arms** at a week close when S >= 3 and the net contradicting weeks inside the last 8 are >= 3.
  It **disarms** with no residue when S <= 1 or after 4 active weeks with no contradicting episode.
- While armed it **fades**: `W = W_innate * 2^(-n * d_mean / 4)`, n = S - 3.
- The **challenger** is the noisy-or of the record's contradicting episodes, 30-day half-life.
- It is **ready to swap** when `W < 0.3`, `W_c >= W + 0.15` and `W_c >= 0.45`. Silent mode swaps at the
  close; ask mode makes one proposal and keeps the weight it had at arming until a yes.
- After a swap, an undo or a no: 90 days quiet. A second swap inside 365 days stops automatic changes and
  raises one question. Rulings never fade (arming raises one question); guarded items raise a question
  instead of arming; pinned items never arm.
- When at least 80% (and at least 3) of the record's contradicting episodes share one project or task kind,
  or the model labels them so, the record is a **scoped refinement**: S counts only weeks with work in that
  scope, the house item never fades, and arming makes a learned item limited to the scope.
- Episodes labelled `period` (a deadline week) are void: no item is made.

Value items (section 4): step back at once (never needs a yes) on one clear signal inside the getting-to-know
window, on one strong signal or a stated opposing view after it, or on two contradicting weeks within 8.
Adoption needs a stated view and S >= 4 within 12 weeks; a restore needs 2 net agreeing weeks within 8.
Adoption and restore follow the mode.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from datetime import date, datetime, timedelta, timezone, tzinfo

from .. import taste

DEFAULTS: dict = {
    "mode": "",                       # ask | silent; empty means the install default (adapt.default_mode)
    "arm_weeks": 3,
    "arm_window_weeks": 8,
    "disarm_at": 1,
    "disarm_quiet_weeks": 4,
    "fade_half_life_weeks": 4.0,
    "swap_below": 0.3,
    "replace_margin": 0.15,
    "replace_min": 0.45,
    "scope_share": 0.8,
    "scope_min_episodes": 3,
    "flip_cooldown": 90,              # days quiet after a swap, an undo or a no
    "max_flips": 2,                   # swaps of one item inside flip_window days before Exo asks instead
    "flip_window": 365,
    "ask_quiet_after": 3,             # unanswered proposals in a row before the brief stops listing them
    "challenger_half_life": 30.0,
    "value_window_step_delta": 0.5,
    "value_window_step_confidence": 0.75,
    "value_step_delta": 0.75,
    "value_step_confidence": 0.8,
    "value_stated_delta": 0.5,
    "value_step_weeks": 2,
    "value_step_window_weeks": 8,
    "value_contra_delta": 0.5,        # smaller value deltas are refinements and do not count
    "value_adopt_weeks": 4,
    "value_adopt_window_weeks": 12,
    "value_restore_weeks": 2,
    "value_restore_window_weeks": 8,
    "min_confidence": 0.6,
    "daily_cap": 50,
    "timezone": "",                   # IANA name; empty means the machine's local zone
}
RELATIONS = ("contradict", "refine", "agree", "unrelated")
SCOPES = ("general", "project", "task", "period")
VOICES = ("own", "quoting", "asking", "role-playing")
TASK_KINDS = ("code", "writing", "research", "planning", "chat", "operations")
DELTAS = (0.0, 0.25, 0.5, 0.75, 1.0)
INFERRED, EDIT, SHOULD = 0.3, 0.6, 0.8      # episode strengths (section 3, step 3)


# --- signals and episodes -------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    """One thing the user did or said that bears on one item, already labelled (section 7)."""
    item: str
    ts: str
    relation: str                     # contradict | refine | agree | unrelated
    delta: float = 0.0
    strength: float = INFERRED
    session: str | None = None
    scope: str = "general"            # general | project | task | period
    project: str | None = None
    task: str | None = None
    voice: str | None = None          # own | quoting | asking | role-playing; None for an action
    confidence: float | None = None   # None: no decision model (the signature fallback)
    stated: bool = False              # the user's own words stating a view
    claim: str = ""                   # the user's version of the item
    evidence: str = ""


def counts(s: Signal, min_confidence: float = 0.6) -> bool:
    """Does this signal count at all? Period-bound, quoted, asked or role-played signals and low-confidence
    labels are void (sections 3, 5 and 7)."""
    if s.relation not in ("contradict", "agree") or s.scope == "period":
        return False
    if s.voice not in (None, "own"):
        return False
    return s.confidence is None or s.confidence >= min_confidence


@dataclass
class Episode:
    item: str
    day: date
    verdict: str                      # contradict | agree | void
    delta: float
    strength: float
    signals: tuple[Signal, ...]
    project: str | None = None
    task: str | None = None
    scoped: str | None = None         # the model's scope label when the contradicting signals share one
    stated: bool = False
    claim: str = ""

    @property
    def evidence(self) -> list[str]:
        return [s.evidence for s in self.signals if s.evidence]


def to_tz(ts: str | datetime, tz: tzinfo) -> datetime:
    return taste.parse_ts(ts).astimezone(tz)


def local_day(ts: str | datetime, tz: tzinfo) -> date:
    return to_tz(ts, tz).date()


def episodes(signals: Iterable[Signal], tz: tzinfo = timezone.utc, *, contra_delta: float = 0.0,
             min_confidence: float = 0.6) -> list[Episode]:
    """Join one item's signals that share a session or a calendar day into episodes, oldest first.
    `contra_delta`: a contradicting signal below this delta is a refinement and does not count (values)."""
    groups: list[tuple[set, list[Signal]]] = []
    for s in sorted((s for s in signals if counts(s, min_confidence)), key=lambda s: (s.item, s.ts)):
        if s.relation == "contradict" and s.delta < contra_delta:
            continue
        keys = {("d", s.item, local_day(s.ts, tz))} | ({("s", s.item, s.session)} if s.session else set())
        hit = [g for g in groups if g[0] & keys]
        merged_keys, merged = set(keys), [s]
        for g in hit:
            merged_keys |= g[0]
            merged += g[1]
            groups.remove(g)
        groups.append((merged_keys, merged))
    out = []
    for _, sigs in groups:
        sigs.sort(key=lambda s: s.ts)
        con = [s for s in sigs if s.relation == "contradict"]
        agr = [s for s in sigs if s.relation == "agree"]
        verdict = "contradict" if len(con) > len(agr) else "agree" if len(agr) > len(con) else "void"
        proj = Counter(s.project for s in sigs if s.project).most_common(1)
        task = Counter(s.task for s in sigs if s.task).most_common(1)
        labels = {s.scope for s in con}
        out.append(Episode(
            sigs[0].item, local_day(sigs[0].ts, tz), verdict,
            sum(s.delta for s in con) / len(con) if con else 0.0,
            max((s.strength for s in con), default=0.0), tuple(sigs),
            proj[0][0] if proj else None, task[0][0] if task else None,
            next(iter(labels)) if len(labels) == 1 and labels & {"project", "task"} else None,
            any(s.stated and s.voice == "own" for s in con),
            next((s.claim for s in reversed(con) if s.claim), "")))
    out.sort(key=lambda e: (e.day, e.signals[0].ts))
    return out


# --- weeks ----------------------------------------------------------------------

def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_index(d: date, origin: date) -> int:
    """Calendar weeks since the install week (Monday to Sunday); the install week is 0."""
    return (d - monday(origin)).days // 7


def week_close(w: int, origin: date) -> date:
    """The last day (Sunday) of week w. Values are taken on that day (the doc's day 7k - 1)."""
    return monday(origin) + timedelta(days=7 * w + 6)


def week_verdict(eps: Sequence[Episode]) -> int:
    """+1 contradicting, -1 agreeing, 0 neutral."""
    c = sum(1 for e in eps if e.verdict == "contradict")
    a = sum(1 for e in eps if e.verdict == "agree")
    return 1 if c > a else -1 if a > c else 0


def weekly_record(eps: Iterable[Episode], origin: date, active: Iterable[int] = ()) -> dict[int, int]:
    """{week: +1 | 0 | -1} for every week with a session or an episode. Weeks missing here are skipped."""
    by: dict[int, list[Episode]] = {}
    for e in eps:
        by.setdefault(week_index(e.day, origin), []).append(e)
    weeks = set(by) | set(active)
    return {w: week_verdict(by.get(w, ())) for w in sorted(weeks)}


def persistence(verdicts: Iterable[int], start: int = 0) -> int:
    """S: +1 per contradicting week, -1 per agreeing week, never below 0."""
    s = start
    for v in verdicts:
        s = max(0, s + v)
    return s


def net_in_window(record: Mapping[int, int], w: int, window: int, sign: int = 1) -> int:
    """Net weeks going `sign`'s way among weeks w - window + 1 .. w."""
    return sum(v * sign for k, v in record.items() if w - window < k <= w)


def precedence(days: float, start: float = 0.7, half_life: float = 365.0, floor: float = 0.3) -> float:
    """P_base(t) = max(floor, start * 2^(-t / half_life)); t in days since install."""
    return max(floor, start * math.pow(2.0, -max(days, 0.0) / half_life))


def effective_weight(w_innate: float, n: int, d_mean: float, h_w: float = 4.0) -> float:
    """W_item = W_innate * 2^(-n * d_mean / H_w); n is net contradicting weeks past arming."""
    return w_innate * math.pow(2.0, -max(n, 0) * d_mean / h_w)


def challenger_weight(eps: Iterable[Episode], on: date, half_life: float = 30.0) -> float:
    """Noisy-or of the contradicting episodes' strengths, each halved every `half_life` days."""
    return taste.combine(((e.strength, (on - e.day).days) for e in eps if e.verdict == "contradict"), half_life)


def ready_to_swap(w_item: float, w_challenger: float, pol: Mapping = DEFAULTS) -> bool:
    return (w_item < pol["swap_below"] and w_challenger >= w_item + pol["replace_margin"]
            and w_challenger >= pol["replace_min"])


def scope_of(eps: Sequence[Episode], pol: Mapping = DEFAULTS) -> str | None:
    """'project:<name>' or 'task:<kind>' when the contradicting episodes are bound to one place or kind of
    task (>= scope_share of them, and at least scope_min_episodes), else None."""
    con = [e for e in eps if e.verdict == "contradict"]
    if len(con) < pol["scope_min_episodes"]:
        return None
    for key in ("project", "task"):
        tally = Counter(getattr(e, key) for e in con if getattr(e, key))
        if tally:
            name, n = tally.most_common(1)[0]
            if n >= pol["scope_share"] * len(con) and n >= pol["scope_min_episodes"]:
                return f"{key}:{name}"
    labelled = [e for e in con if e.scoped]
    if len(labelled) >= pol["scope_share"] * len(con) and len(labelled) >= pol["scope_min_episodes"]:
        tag = Counter(f"{e.scoped}:{getattr(e, e.scoped or 'project') or f'this {e.scoped}'}"
                      for e in labelled).most_common(1)
        return tag[0][0]
    return None


def in_scope(e: Episode, scope: str) -> bool:
    key, _, name = scope.partition(":")
    return (getattr(e, key, None) or f"this {key}") == name


# --- the replay -------------------------------------------------------------------

@dataclass(frozen=True)
class Act:
    """A user act read from the ledger: pin, unpin, off, on, yes, no, later, undo, restore, reset."""
    ts: str
    verb: str
    item: str = ""
    data: str = ""


@dataclass
class Outcome:
    """Something the rule did. adapt.close_weeks writes each one once."""
    kind: str        # swapped | scoped | proposed | stepped_back | adopted | restored | question
    item: str
    at: str          # ISO time it happened (a week close, a signal or an answer)
    week: int
    data: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.item}:{self.at}"


@dataclass
class Subject:
    """An item the rule runs on."""
    id: str
    claim: str
    cls: str = "method"               # method | value
    source: str = "house"             # house | learned | ruling
    guarded: bool = False
    since: str = ""                   # when tracking starts (install day for a house item)
    innate: Callable[[date], float] = lambda d: 0.7




CHANGE = {"swap": "swapped", "scope": "scoped", "adopt": "adopted", "restore": "restored"}


@dataclass
class Track:
    """An item's state after the replay, for /taste and the prompt."""
    id: str
    cls: str
    status: str = "active"            # method: active | armed | swapped; value: active | stepped_back | adopted
    s: int = 0
    armed_week: int | None = None
    n: int = 0
    d_mean: float = 0.0
    weight: float = 0.0               # the item's weight at the last close (faded while armed)
    prompt_weight: float = 0.0        # what the prompt uses (ask mode keeps the weight from arming)
    arm_weight: float = 0.0
    challenger: float = 0.0
    claim: str = ""                   # the user's version, latest
    scope: str | None = None
    proposal: dict | None = None      # {kind, at, week} while one waits for an answer
    quiet_until: date | None = None
    record: list[Episode] = field(default_factory=list)
    weeks: dict[int, int] = field(default_factory=dict)      # the record's weeks (cleared at S = 0)
    history: dict[int, int] = field(default_factory=dict)    # every week's verdict since the last change
    last_week: int = -1
    swaps: list[date] = field(default_factory=list)
    stopped: bool = False             # max_flips reached: no more automatic changes
    stated: bool = False
    pinned: bool = False
    idle: int = 0                     # active weeks with no contradicting episode while armed
    stepped_week: int = -1            # the week a value stepped back; a restore counts the weeks after it
    done_scopes: set[str] = field(default_factory=set)
    outcomes: list[Outcome] = field(default_factory=list)

    def clear(self, history: bool = False) -> None:
        """Disarm or reset: no residue (section 3, step 2). The ledger keeps what happened."""
        self.s, self.armed_week, self.n, self.d_mean, self.record, self.weeks = 0, None, 0, 0.0, [], {}
        self.challenger, self.stated, self.scope, self.idle, self.proposal = 0.0, False, None, 0, None
        if history:
            self.history = {}
        if self.status == "armed":
            self.status = "active"

    @property
    def pending(self) -> bool:
        return self.proposal is not None

    def contra_weeks(self, last: int = 5) -> int:
        """Contradicting weeks among the last `last` calendar weeks up to the last close."""
        return sum(1 for k, v in self.history.items() if self.last_week - last < k <= self.last_week and v > 0)


def _iso(d: date, tz: tzinfo) -> str:
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(timezone.utc).isoformat() \
        .replace("+00:00", "Z")


def replay(subject: Subject, signals: Sequence[Signal], *, origin: date, today: date, tz: tzinfo = timezone.utc,
           active_weeks: Iterable[int] = (), scope_weeks: Mapping[str, Iterable[int]] | None = None,
           acts: Sequence[Act] = (), mode_at: Callable[[str], str] = lambda ts: "silent",
           window_at: Callable[[str], bool] = lambda ts: False, pol: Mapping = DEFAULTS) -> Track:
    """Run the rule on one item from `origin` (its install or creation day) to `today`. Week closes before
    `today` are evaluated; acts and value step backs apply at their own time, up to now."""
    pol = {**DEFAULTS, **pol}
    t = Track(subject.id, subject.cls)
    value = subject.cls == "value"
    mine = [s for s in signals if s.item == subject.id]
    eps_all = episodes(mine, tz, contra_delta=pol["value_contra_delta"] if value else 0.0,
                       min_confidence=pol["min_confidence"])
    active = set(active_weeks) | {week_index(e.day, origin) for e in eps_all}
    scope_weeks = {k: set(v) for k, v in (scope_weeks or {}).items()}
    sigs = sorted((s for s in mine if counts(s, pol["min_confidence"])), key=lambda s: s.ts)
    acts = sorted((a for a in acts if a.item in (subject.id, "*")), key=lambda a: a.ts)
    last_closed = week_index(today, origin) - 1
    t.weight = t.prompt_weight = subject.innate(origin)

    def emit(kind: str, at: str, week: int, **data) -> None:
        t.outcomes.append(Outcome(kind, subject.id, at, week, data))

    def quiet(on: date) -> bool:
        return t.quiet_until is not None and on < t.quiet_until

    def change(kind: str, at: str, on: date, week: int) -> None:
        """A swap, a scoped item, an adoption or a restore actually happening."""
        emit(CHANGE[kind], at, week, claim=t.claim, evidence=_evidence(t.record), scope=t.scope,
             challenger=round(t.challenger, 4), weight=round(t.weight, 4), weeks=len(t.history))
        if kind in ("swap", "adopt"):
            t.swaps = [d for d in t.swaps if (on - d).days < pol["flip_window"]] + [on]
            t.status = "swapped" if kind == "swap" else "adopted"
            t.quiet_until = on + timedelta(days=pol["flip_cooldown"])
            if len(t.swaps) >= pol["max_flips"]:
                t.stopped = True
                emit("question", at, week, why="flips",
                     text=f"Your way on '{subject.claim}' has changed {len(t.swaps)} times within a year. Keep the "
                          "house version, keep yours, or limit yours to some kinds of work?")
        elif kind == "restore":
            t.status = "active"
        elif kind == "scope" and t.scope:
            t.done_scopes.add(t.scope)
        t.clear(history=True)

    def propose_or_change(kind: str, at: str, on: date, week: int) -> None:
        if mode_at(at) != "ask":
            change(kind, at, on, week)
        elif t.proposal is None:
            t.proposal = {"kind": kind, "at": at, "week": week}
            emit("proposed", at, week, change=kind, claim=t.claim, scope=t.scope, challenger=round(t.challenger, 4))

    def apply_act(a: Act, week: int) -> None:
        on = local_day(a.ts, tz)
        if a.verb in ("pin", "unpin"):
            t.pinned = a.verb == "pin"
            if t.pinned and t.status == "armed":
                t.clear()
        elif a.verb == "yes" and t.proposal is not None:
            change(t.proposal["kind"], a.ts, on, week)
        elif a.verb == "no" and t.proposal is not None:
            t.clear()
            t.quiet_until = on + timedelta(days=pol["flip_cooldown"])
        elif a.verb == "undo" and t.status == "active" and t.done_scopes:
            t.done_scopes.clear()
            t.clear(history=True)
            t.quiet_until = on + timedelta(days=pol["flip_cooldown"])
        elif a.verb == "undo" and t.status in ("swapped", "adopted"):
            t.status = "stepped_back" if t.status == "adopted" else "active"
            t.clear(history=True)
            t.quiet_until = on + timedelta(days=pol["flip_cooldown"])
        elif a.verb == "restore":
            t.status = "active"
            t.clear(history=True)
            t.quiet_until = on + timedelta(days=pol["flip_cooldown"])
        elif a.verb == "reset":
            t.status = "active"
            t.clear(history=True)

    def single_step(s: Signal) -> str | None:
        """A value steps back on one signal (section 4)."""
        if s.relation != "contradict":
            return None
        own = s.stated and s.voice == "own"
        if subject.source == "house" and window_at(s.ts):
            if own:
                return "a stated view, inside the getting-to-know window"
            if s.confidence is not None and s.delta >= pol["value_window_step_delta"] \
                    and s.confidence >= pol["value_window_step_confidence"]:
                return "one clear signal, inside the getting-to-know window"
            return None
        if own and s.delta >= pol["value_stated_delta"]:
            return "a stated opposing view"
        if s.confidence is not None and s.delta >= pol["value_step_delta"] \
                and s.confidence >= pol["value_step_confidence"]:
            return "one strong signal"
        return None

    for w in range(0, max(last_closed, -1) + 2):
        close = week_close(w, origin)
        start = close - timedelta(days=6)
        # acts and value step backs inside this week, in time order (the open week: up to now)
        events: list[tuple[str, Any]] = [("a", a) for a in acts if start <= local_day(a.ts, tz) <= close]
        events += [("s", s) for s in sigs if start <= local_day(s.ts, tz) <= close]
        for kind_, x in sorted(events, key=lambda p: p[1].ts):
            if kind_ == "a":
                apply_act(x, w)
            elif value and t.status == "active" and not t.pinned and subject.source != "ruling":
                why = single_step(x)
                if why:
                    t.status, t.stepped_week = "stepped_back", w
                    emit("stepped_back", x.ts, w, why=why)
        if w > last_closed:
            break
        at = _iso(close, tz)
        t.last_week = w
        eps = [e for e in eps_all if week_index(e.day, origin) == w and not quiet(e.day)
               and not any(in_scope(e, sc) for sc in t.done_scopes)]
        if not (w in active or eps):
            if t.status == "armed":                    # a skipped week: no progress, no loss; only time passes
                t.challenger = challenger_weight(t.record, close, pol["challenger_half_life"])
                t.weight = effective_weight(subject.innate(close), t.n, t.d_mean, pol["fade_half_life_weeks"])
            continue
        if t.pinned or t.stopped or t.status in ("swapped", "adopted") or subject.source == "ruling" and value:
            continue
        t.record += [e for e in eps if e.verdict == "contradict"]
        v = week_verdict(eps)
        t.weeks[w] = t.history[w] = v
        t.s = max(0, t.s + v)
        if t.s == 0:
            t.clear()                                  # a mood or a swing: no residue
            if value and t.status == "stepped_back":
                _value_close(t, w, close, at, pol, emit, propose_or_change, quiet)
            continue
        if any(e.stated for e in eps if e.verdict == "contradict"):
            t.stated = True
        t.claim = next((e.claim for e in reversed(t.record) if e.claim), t.claim)
        t.d_mean = sum(e.delta for e in t.record) / len(t.record) if t.record else 0.0
        t.challenger = challenger_weight(t.record, close, pol["challenger_half_life"])
        if value:
            _value_close(t, w, close, at, pol, emit, propose_or_change, quiet)
            continue
        # a scoped refinement: a record bound to one place or kind of task; the house item never fades
        sc = scope_of(t.record, pol) if t.status == "active" else None
        if sc is not None:
            t.scope = sc
            begin = min(x.day for x in t.record)
            in_sc = [e for e in eps_all if in_scope(e, sc) and begin <= e.day and week_index(e.day, origin) <= w]
            sw = {k for k in scope_weeks.get(sc, set()) if k <= w} | {week_index(e.day, origin) for e in in_sc}
            rec = weekly_record(in_sc, origin, [k for k in sw if k >= week_index(begin, origin)])
            if persistence(rec.values()) >= pol["arm_weeks"] and not quiet(close) \
                    and net_in_window(rec, w, pol["arm_window_weeks"]) >= pol["arm_weeks"]:
                propose_or_change("scope", at, close, w)
            continue
        t.scope = None
        if t.status == "armed":
            t.idle = 0 if any(e.verdict == "contradict" for e in eps) else t.idle + 1
            if t.s <= pol["disarm_at"] or t.idle >= pol["disarm_quiet_weeks"]:
                t.clear()                              # disarm: no residue
                continue
        elif t.s >= pol["arm_weeks"] and net_in_window(t.weeks, w, pol["arm_window_weeks"]) >= pol["arm_weeks"] \
                and not quiet(close):
            if subject.source == "ruling" or subject.guarded:
                emit("question", at, w, why="ruling" if subject.source == "ruling" else "guarded",
                     text=(f"You said: {subject.claim} You have done otherwise in each of the last "
                           f"{pol['arm_weeks']} weeks. Keep the rule, change it, or limit it?")
                     if subject.source == "ruling" else
                     f"For {pol['arm_weeks']} weeks you have worked against '{subject.claim}', which guards "
                     "something public or irreversible. Change it?")
                t.clear()
                t.quiet_until = close + timedelta(days=pol["flip_cooldown"])
                continue
            t.status, t.armed_week, t.idle = "armed", w, 0
            t.arm_weight = subject.innate(close)
        if t.status == "armed":
            t.n = max(0, t.s - pol["arm_weeks"])
            t.weight = effective_weight(subject.innate(close), t.n, t.d_mean, pol["fade_half_life_weeks"])
            t.prompt_weight = t.arm_weight if mode_at(at) == "ask" else t.weight
            if ready_to_swap(t.weight, t.challenger, pol):
                propose_or_change("swap", at, close, w)
    if t.status != "armed":
        t.weight = t.prompt_weight = subject.innate(today)
    return t


def _value_close(t: Track, w: int, close: date, at: str, pol: Mapping, emit, propose_or_change, quiet) -> None:
    """A value item at a week close: the two-week step back, then adoption or a restore."""
    if t.status == "active":
        hits = sum(1 for k, v in t.history.items() if w - pol["value_step_window_weeks"] < k <= w and v > 0)
        if hits >= pol["value_step_weeks"]:
            t.status, t.stepped_week = "stepped_back", w
            emit("stepped_back", at, w, why=f"{hits} contradicting weeks within {pol['value_step_window_weeks']}")
        return
    if t.status != "stepped_back" or t.proposal is not None or quiet(close):
        return
    if t.stated and t.s >= pol["value_adopt_weeks"] \
            and net_in_window(t.weeks, w, pol["value_adopt_window_weeks"]) >= pol["value_adopt_weeks"]:
        propose_or_change("adopt", at, close, w)
    elif net_in_window({k: v for k, v in t.history.items() if k > t.stepped_week}, w,
                       pol["value_restore_window_weeks"], sign=-1) >= pol["value_restore_weeks"]:
        propose_or_change("restore", at, close, w)


def _evidence(record: Sequence[Episode]) -> list[dict]:
    """One evidence row per contradicting episode, at the strength of its strongest signal."""
    out = []
    for e in record:
        con = [s for s in e.signals if s.relation == "contradict"]
        best = max(con, key=lambda s: s.strength) if con else e.signals[0]
        out.append({"id": best.evidence or f"episode:{e.item}:{e.day.isoformat()}", "ts": best.ts,
                    "strength": e.strength})
    return out


def estimate_weeks(t: Track, w_innate: float, pol: Mapping = DEFAULTS) -> int | None:
    """Further net weeks of change until the item fades below swap_below at the current d_mean."""
    pol = {**DEFAULTS, **pol}
    if t.d_mean <= 0 or w_innate <= 0:
        return None
    if w_innate < pol["swap_below"]:
        return 0
    need = math.floor(pol["fade_half_life_weeks"] * math.log2(w_innate / pol["swap_below"]) / t.d_mean) + 1
    return max(0, need - t.n)
