"""Active learning: question queue, value of information, ask-or-act, interruption budget.

Principles 3, 4 and 13. The mechanism is generic. The questions, answers, thresholds and
budget live in the principal store.

Queue
-----
`<store>/queues/questions.jsonl`. One JSON object per line, append-only. Each line is an
event about one question; the state of a question is the fold of its events in file order.

    v     1
    op    proposed | asked | answered | dropped
    id    stable question id, e.g. "q-20261011-1a2b3c"
    ts    UTC ISO-8601

`proposed` fields: text, node (target belief node id), rationale, channel, domain, default,
impact, uncertainty, voi (score at proposal time), source.
`asked` fields: channel, ledger_id (the ledger `question` event, when one was written).
`answered` fields: text, ledger_id (the ledger `answer` event), proposal (what the belief hook
returned, if anything). `dropped` fields: reason.

Value of information
--------------------
    voi = impact x uncertainty x (1 + R)
    R   = min(recency_cap, sum over corrections c on the target node of 0.5 ** (age_days(c) / half_life_days))

`impact` (0..1) is the expected change in agent behaviour if the answer differs from the current
guess. `uncertainty` (0..1) is how unsure the store is about that answer now. A correction is a
ledger `verdict` of `edit`, `reject` or `should_have_been` whose body names the node in `node`,
`nodes` or `target_node`. Recent corrections on a node mean agents are getting it wrong right now,
so a question about it is worth more. Defaults: half_life_days 14, recency_cap 3, so voi is in
[0, 4]. `voi` is recomputed at read time from the live ledger; the stored value is a snapshot.

Ask or act
----------
`should_ask(action_context, predictions)` takes several predicted principal choices (from
different models or prompts). Disagreement is the normalized entropy of the pooled vote
distribution (0 = all agree, 1 = uniform). Above the domain threshold, ask; else act and log.

Interruption budget
-------------------
Policy keys in `policies/interruptions.yaml` (all optional):

    questions_per_day: 5            # the floor of the curve, or the flat cap without a curve
    window: {hours: 4, max: 2}      # at most `max` questions in any rolling `hours`
    curve: {start_per_day: 12, half_life_weeks: 2, start: 2026-10-11}
                                    # front-loaded falling cap:
                                    # cap = floor + (start_per_day - floor) * 0.5 ** (weeks / half_life_weeks)
    quiet_hours: ["23:00-08:00"]    # no questions inside these hours
    timezone: America/New_York      # principal's zone for days and quiet hours (default: local)
    ask_threshold: {default: 0.5, public: 0.2}
    min_predictions: 2              # fewer predictions than this always asks
    voi: {half_life_days: 14, recency_cap: 3}

Without `curve.start` the curve starts at the first queue event.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

QUEUE_REL = Path("queues") / "questions.jsonl"
OPS = ("proposed", "asked", "answered", "dropped")
CORRECTIONS = frozenset({"edit", "reject", "should_have_been"})
DEFAULTS: dict = {
    "questions_per_day": 5,
    "window": None,
    "curve": None,
    "quiet_hours": [],
    "timezone": None,
    "ask_threshold": {"default": 0.5},
    "min_predictions": 2,
    "voi": {"half_life_days": 14.0, "recency_cap": 3.0},
}


# --- time helpers -----------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime.combine(v, time(), timezone.utc)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return datetime.fromtimestamp(float(v), timezone.utc)
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _zone(name: str | None):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    except Exception:
        return None


def local(dt: datetime, tz_name: str | None) -> datetime:
    z = _zone(tz_name)
    return dt.astimezone(z) if z else dt.astimezone()


# --- policy -----------------------------------------------------------------------------------

def load_policy(root: str | os.PathLike | None) -> dict:
    """Interruption policy of a store merged over DEFAULTS. Missing file means defaults."""
    raw: dict = {}
    if root:
        p = Path(root) / "policies" / "interruptions.yaml"
        if p.is_file():
            from .gate import parse_yaml
            raw = parse_yaml(p.read_text(encoding="utf-8")) or {}
    return merge_policy(raw)


def merge_policy(raw: Mapping | None) -> dict:
    pol = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in (raw or {}).items():
        if isinstance(pol.get(k), dict) and isinstance(v, Mapping):
            pol[k] = {**pol[k], **v}
        else:
            pol[k] = v
    if not isinstance(pol.get("ask_threshold"), Mapping):
        pol["ask_threshold"] = {"default": float(pol["ask_threshold"])}
    return pol


def threshold_for(domain: str | None, policy: Mapping) -> float:
    th = policy.get("ask_threshold") or {}
    if domain and domain in th:
        return float(th[domain])
    if domain and "/" in domain and domain.split("/", 1)[0] in th:
        return float(th[domain.split("/", 1)[0]])
    return float(th.get("default", 0.5))


# --- ask or act -------------------------------------------------------------------------------

@dataclass
class AskDecision:
    ask: bool
    disagreement: float      # normalized entropy of the pooled distribution, 0..1
    entropy: float           # nats
    vote_split: float        # 1 - share of the top choice
    threshold: float
    domain: str
    choice: str | None       # top predicted choice (what to do when acting)
    distribution: dict[str, float]
    n: int
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _pool(predictions: Iterable[Any]) -> tuple[Counter, int]:
    """Each prediction is a choice string, a {"choice": c, "weight": w} mapping, or a
    {"probs": {choice: p}} mapping (a verbalized distribution). Returns pooled mass."""
    mass: Counter = Counter()
    n = 0
    for p in predictions:
        n += 1
        if isinstance(p, Mapping):
            w = float(p.get("weight", 1.0))
            probs = p.get("probs") or p.get("distribution")
            if isinstance(probs, Mapping) and probs:
                tot = sum(float(x) for x in probs.values()) or 1.0
                for c, x in probs.items():
                    mass[_norm_choice(c)] += w * float(x) / tot
            elif p.get("choice") is not None:
                mass[_norm_choice(p["choice"])] += w
        elif p is not None:
            mass[_norm_choice(p)] += 1.0
    return mass, n


def _norm_choice(c: Any) -> str:
    return re.sub(r"\s+", " ", str(c).strip().lower())


def disagreement(predictions: Iterable[Any]) -> tuple[float, float, float, dict[str, float], int]:
    """(normalized entropy, entropy in nats, vote split, distribution, n)."""
    mass, n = _pool(predictions)
    total = sum(mass.values())
    if total <= 0:
        return 1.0, 0.0, 1.0, {}, n
    dist = {c: m / total for c, m in mass.most_common()}
    h = -sum(p * math.log(p) for p in dist.values() if p > 0)
    k = max(len(dist), 2)
    return h / math.log(k), h, 1.0 - max(dist.values()), dist, n


def should_ask(action_context: Mapping | None, predictions: Iterable[Any],
               policy: Mapping | None = None, root: str | os.PathLike | None = None) -> AskDecision:
    """Ask the principal when predicted choices disagree above the domain threshold.

    `action_context` may carry `domain` (used for the threshold; default "default").
    `policy` is a merged interruption policy; if absent it is read from `root`.
    """
    ctx = dict(action_context or {})
    pol = merge_policy(policy) if policy is not None else load_policy(root)
    domain = str(ctx.get("domain") or "default")
    th = threshold_for(domain, pol)
    norm, h, split, dist, n = disagreement(list(predictions))
    top = next(iter(dist), None)
    if n < int(pol.get("min_predictions", 2)) and not any(v < 1.0 for v in dist.values()):
        return AskDecision(True, 1.0, h, split, th, domain, top, dist, n,
                           f"only {n} prediction(s); uncertainty cannot be measured")
    ask = norm > th
    reason = (f"disagreement {norm:.2f} > threshold {th:.2f}" if ask
              else f"disagreement {norm:.2f} <= threshold {th:.2f}; act and log")
    return AskDecision(ask, norm, h, split, th, domain, top, dist, n, reason)


# --- value of information ---------------------------------------------------------------------

def corrections_by_node(ledger, since: str | None = None) -> dict[str, list[datetime]]:
    """Timestamps of correcting verdicts, keyed by the node(s) their body names."""
    out: dict[str, list[datetime]] = {}
    if ledger is None:
        return out
    for ev in ledger.iter(kind="verdict", since=since):
        b = ev.get("body") or {}
        if b.get("verdict") not in CORRECTIONS:
            continue
        nodes = list(b.get("nodes") or [])
        for k in ("node", "target_node"):
            if b.get(k):
                nodes.append(b[k])
        ts = parse_ts(ev["ts"])
        for nd in nodes:
            out.setdefault(str(nd), []).append(ts)
    return out


def recency(node: str | None, corrections: Mapping[str, list[datetime]], now: datetime,
            half_life_days: float = 14.0, cap: float = 3.0) -> float:
    if not node:
        return 0.0
    r = 0.0
    for ts in corrections.get(node, ()):
        age = max(0.0, (now - ts).total_seconds() / 86400.0)
        r += 0.5 ** (age / half_life_days)
    return min(cap, r)


def voi_score(impact: float, uncertainty: float, recency_weight: float = 0.0) -> float:
    """voi = impact x uncertainty x (1 + R). See the module docstring."""
    clamp = lambda x: max(0.0, min(1.0, float(x)))  # noqa: E731
    return clamp(impact) * clamp(uncertainty) * (1.0 + max(0.0, float(recency_weight)))


# --- the queue --------------------------------------------------------------------------------

@dataclass
class Question:
    id: str
    text: str
    node: str | None = None
    rationale: str = ""
    channel: str = "brief"
    domain: str | None = None
    default: str | None = None
    impact: float = 0.5
    uncertainty: float = 0.5
    voi: float = 0.0
    source: str | None = None
    status: str = "proposed"
    proposed_at: str | None = None
    asked: list[str] = field(default_factory=list)
    question_ledger_id: str | None = None
    answer: str | None = None
    answer_ledger_id: str | None = None
    proposal: Any = None
    drop_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def new_id(prefix: str = "q") -> str:
    return f"{prefix}-{utcnow().strftime('%Y%m%d')}-{secrets.token_hex(3)}"


class QuestionQueue:
    """`QuestionQueue(store_root)`. Reads and appends `queues/questions.jsonl`."""

    def __init__(self, root: str | os.PathLike, policy: Mapping | None = None, ledger=None):
        self.root = Path(root)
        self.path = self.root / QUEUE_REL
        self.policy = merge_policy(policy) if policy is not None else load_policy(self.root)
        self.ledger = ledger

    # read -----------------------------------------------------------------

    def events(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict) and ev.get("op") in OPS and ev.get("id"):
                out.append(ev)
        return out

    def state(self) -> dict[str, Question]:
        qs: dict[str, Question] = {}
        for ev in self.events():
            qid, op = ev["id"], ev["op"]
            if op == "proposed":
                if qid in qs:
                    continue
                qs[qid] = Question(
                    id=qid, text=ev.get("text", ""), node=ev.get("node"), rationale=ev.get("rationale", ""),
                    channel=ev.get("channel", "brief"), domain=ev.get("domain"), default=ev.get("default"),
                    impact=float(ev.get("impact", 0.5)), uncertainty=float(ev.get("uncertainty", 0.5)),
                    voi=float(ev.get("voi", 0.0)), source=ev.get("source"), proposed_at=ev.get("ts"))
                continue
            q = qs.get(qid)
            if q is None:
                continue
            if op == "asked":
                q.asked.append(ev.get("ts"))
                q.question_ledger_id = ev.get("ledger_id") or q.question_ledger_id
                q.channel = ev.get("channel") or q.channel
                if q.status == "proposed":
                    q.status = "asked"
            elif op == "answered":
                q.status, q.answer = "answered", ev.get("text")
                q.answer_ledger_id, q.proposal = ev.get("ledger_id"), ev.get("proposal")
            elif op == "dropped":
                q.status, q.drop_reason = "dropped", ev.get("reason")
        return qs

    def get(self, qid: str) -> Question | None:
        return self.state().get(qid)

    def open(self, now: datetime | None = None) -> list[Question]:
        """Unanswered, undropped questions ranked by live voi (highest first)."""
        now = now or utcnow()
        vp = self.policy.get("voi") or {}
        corr = corrections_by_node(self.ledger)
        out = []
        for q in self.state().values():
            if q.status in ("answered", "dropped"):
                continue
            r = recency(q.node, corr, now, float(vp.get("half_life_days", 14)), float(vp.get("recency_cap", 3)))
            q.voi = voi_score(q.impact, q.uncertainty, r)
            out.append(q)
        out.sort(key=lambda q: (q.status == "asked", -q.voi, q.proposed_at or ""))
        return out

    # write ----------------------------------------------------------------

    def _append(self, ev: dict) -> dict:
        ev = {"v": 1, "ts": iso(utcnow()), **ev}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, sort_keys=True, ensure_ascii=False) + "\n")
        return ev

    def propose(self, text: str, node: str | None = None, rationale: str = "", *, impact: float = 0.5,
                uncertainty: float = 0.5, channel: str = "brief", domain: str | None = None,
                default: str | None = None, source: str | None = None, qid: str | None = None,
                now: datetime | None = None) -> Question:
        now = now or utcnow()
        vp = self.policy.get("voi") or {}
        r = recency(node, corrections_by_node(self.ledger), now,
                    float(vp.get("half_life_days", 14)), float(vp.get("recency_cap", 3)))
        qid = qid or new_id()
        if qid in self.state():
            raise ValueError(f"question {qid} already exists")
        dom = domain or (node.split("/", 1)[0] if node and "/" in node else None)
        ev = {"op": "proposed", "id": qid, "text": text, "node": node, "rationale": rationale,
              "channel": channel, "domain": dom, "default": default, "impact": float(impact),
              "uncertainty": float(uncertainty), "voi": round(voi_score(impact, uncertainty, r), 6),
              "source": source, "ts": iso(now)}
        self._append({k: v for k, v in ev.items() if v is not None})
        return self.state()[qid]

    def mark_asked(self, qids: Iterable[str], channel: str | None = None, *, author: str | None = None,
                   session: str | None = None, now: datetime | None = None) -> list[dict]:
        """Record that questions reached the principal. With a ledger, also write a `question`
        event per question so the answer can reference it."""
        st = self.state()
        out = []
        for qid in qids:
            q = st.get(qid)
            if q is None:
                raise KeyError(qid)
            ev: dict = {"op": "asked", "id": qid, "channel": channel or q.channel}
            if now is not None:
                ev["ts"] = iso(now)
            if self.ledger is not None and getattr(self.ledger, "signer", None) is not None:
                lev = self.ledger.append("question", {"question": qid, "text": q.text, "node": q.node,
                                                      "channel": ev["channel"]}, author, session)
                ev["ledger_id"] = lev["id"]
            out.append(self._append(ev))
        return out

    def answer(self, qid: str, text: str, *, author: str | None = None, session: str | None = None,
               propose_belief: Callable[..., Any] | None = None, now: datetime | None = None) -> dict:
        """Record the principal's answer. Writes a ledger `answer` event that refs the ledger
        `question` event (when there is one) and names the queue id in `body.question`. Then
        calls `propose_belief(answer_event=..., question=...)`, the exo.consolidate hook,
        and stores whatever id it returns."""
        q = self.get(qid)
        if q is None:
            raise KeyError(qid)
        lev = None
        if self.ledger is not None and getattr(self.ledger, "signer", None) is not None:
            refs = [q.question_ledger_id] if q.question_ledger_id and self.ledger.get(q.question_ledger_id) else []
            body = {"question": qid, "question_text": q.text, "text": text}
            if q.node:
                body["node"] = q.node
            lev = self.ledger.append("answer", body, author, session, refs=refs)
        proposal = None
        if propose_belief is not None:
            payload = lev or {"kind": "answer", "body": {"question": qid, "text": text, "node": q.node}}
            res = propose_belief(answer_event=payload, question=q.to_dict())
            proposal = res.get("id", res) if isinstance(res, Mapping) else res
            if not isinstance(proposal, (str, int, float, bool, type(None), list, dict)):
                proposal = str(proposal)
        ev: dict = {"op": "answered", "id": qid, "text": text}
        if lev:
            ev["ledger_id"] = lev["id"]
        if proposal is not None:
            ev["proposal"] = proposal
        if now is not None:
            ev["ts"] = iso(now)
        return self._append(ev)

    def drop(self, qid: str, reason: str = "") -> dict:
        if qid not in self.state():
            raise KeyError(qid)
        return self._append({"op": "dropped", "id": qid, "reason": reason})

    # budget ---------------------------------------------------------------

    def curve_start(self) -> datetime | None:
        c = self.policy.get("curve") or {}
        if isinstance(c, Mapping) and c.get("start"):
            return parse_ts(c["start"])
        evs = self.events()
        return parse_ts(evs[0].get("ts")) if evs else None

    def daily_cap(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        floor = int(self.policy.get("questions_per_day") or 0)
        c = self.policy.get("curve")
        if not isinstance(c, Mapping) or c.get("start_per_day") is None:
            return floor
        start = self.curve_start() or now
        weeks = max(0.0, (now - start).total_seconds() / (7 * 86400))
        hl = float(c.get("half_life_weeks", 2)) or 2.0
        top = float(c["start_per_day"])
        return int(round(floor + max(0.0, top - floor) * 0.5 ** (weeks / hl)))

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        now = local(now or utcnow(), self.policy.get("timezone"))
        t = now.hour * 60 + now.minute
        for span in self.policy.get("quiet_hours") or []:
            m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", str(span))
            if not m:
                continue
            a = int(m.group(1)) * 60 + int(m.group(2))
            b = int(m.group(3)) * 60 + int(m.group(4))
            if (a <= t < b) if a <= b else (t >= a or t < b):
                return True
        return False

    def asked_times(self) -> list[datetime]:
        return [t for t in (parse_ts(e.get("ts")) for e in self.events() if e["op"] == "asked") if t]

    def budget(self, now: datetime | None = None) -> dict:
        """Remaining questions now: min(daily cap - asked today, window max - asked in window),
        zero inside quiet hours."""
        now = now or utcnow()
        tz = self.policy.get("timezone")
        today = local(now, tz).date()
        asked = self.asked_times()
        asked_today = sum(1 for t in asked if local(t, tz).date() == today)
        cap = self.daily_cap(now)
        remaining = max(0, cap - asked_today)
        w = self.policy.get("window")
        in_window = None
        if isinstance(w, Mapping) and w.get("max") is not None:
            since = now - timedelta(hours=float(w.get("hours", 24)))
            in_window = sum(1 for t in asked if since < t <= now)
            remaining = min(remaining, max(0, int(w["max"]) - in_window))
        quiet = self.in_quiet_hours(now)
        if quiet:
            remaining = 0
        return {"daily_cap": cap, "asked_today": asked_today, "asked_in_window": in_window,
                "quiet": quiet, "remaining": remaining}

    def next_questions(self, n: int = 3, now: datetime | None = None, *, mark: bool = False,
                       channel: str | None = None, author: str | None = None,
                       include_asked: bool = False) -> list[Question]:
        """Top open questions by voi, never more than the budget allows. Questions already
        asked and unanswered are skipped unless `include_asked`. `mark=True` records them asked."""
        now = now or utcnow()
        room = min(int(n), self.budget(now)["remaining"])
        if room <= 0:
            return []
        qs = [q for q in self.open(now) if include_asked or q.status == "proposed"][:room]
        if mark and qs:
            self.mark_asked([q.id for q in qs], channel, author=author, now=now)
        return qs


# --- seeding from a Markdown list -------------------------------------------------------------

_MD_ITEM = re.compile(r"^\s*(\d+)\.\s+(.*?)(?:\s*(?:→|->)\s*`([^`]+)`)?\s*$")


def parse_markdown_questions(text: str) -> list[dict]:
    """Read a numbered Markdown list ("1. **Title.** question text → `node/id`", continuation
    lines indented). List order is taken as rank: impact falls from 1.0 toward 0.3."""
    items: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if re.match(r"^\s*\d+\.\s", line):
            if cur:
                items.append(cur)
            cur = {"raw": line.strip()}
        elif cur is not None and line.startswith((" ", "\t")) and line.strip():
            cur["raw"] += " " + line.strip()
        elif cur is not None and not line.strip():
            items.append(cur)
            cur = None
    if cur:
        items.append(cur)
    out = []
    for it in items:
        m = _MD_ITEM.match(it["raw"])
        if not m:
            continue
        body, node = m.group(2), m.group(3)
        title = None
        tm = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", body)
        if tm:
            title, body = tm.group(1).rstrip(".").strip(), tm.group(2)
        if body.startswith("~~"):
            continue  # struck through: already answered
        out.append({"num": int(m.group(1)), "title": title, "text": body.strip(), "node": node})
    k = len(out)
    for i, q in enumerate(out):
        q["impact"] = round(1.0 - 0.7 * (i / max(1, k - 1)), 3) if k > 1 else 1.0
    return out


def seed_from_markdown(queue: QuestionQueue, text: str, source: str | None = None,
                       uncertainty: float = 0.7) -> list[Question]:
    """Propose every open item of a Markdown question list not already queued (same text)."""
    have = {q.text for q in queue.state().values()}
    added = []
    for it in parse_markdown_questions(text):
        full = f"{it['title']}: {it['text']}" if it["title"] else it["text"]
        if full in have:
            continue
        added.append(queue.propose(full, it["node"], rationale=f"seeded item {it['num']}",
                                   impact=it["impact"], uncertainty=uncertainty, source=source))
    return added


# --- CLI hook ---------------------------------------------------------------------------------
# Wire into exo.cli: in build_parser() call `ask.register(sp, common)`, and expose
# `cmd_ask` in exo.cli's globals (e.g. `from .ask import cmd_ask`).

def register(sp, common) -> None:
    p = sp.add_parser("ask", parents=[common], help="question queue and interruption budget")
    ap = p.add_subparsers(dest="ask_cmd", required=True)
    q = ap.add_parser("propose", parents=[common])
    q.add_argument("text")
    q.add_argument("--node")
    q.add_argument("--rationale", default="")
    q.add_argument("--impact", type=float, default=0.5)
    q.add_argument("--uncertainty", type=float, default=0.5)
    q.add_argument("--channel", default="brief")
    q.add_argument("--default")
    q = ap.add_parser("next", parents=[common])
    q.add_argument("-n", type=int, default=3)
    q.add_argument("--mark", action="store_true", help="record them as asked")
    q.add_argument("--author", help="ledger author for `question` events (needs --key)")
    q.add_argument("--key", help="signing key path; enables ledger writes")
    q = ap.add_parser("answer", parents=[common])
    q.add_argument("id")
    q.add_argument("text")
    q.add_argument("--author", required=True)
    q.add_argument("--key", help="signing key path (default: $EXO_HOME/keys/<author>.key)")
    q = ap.add_parser("drop", parents=[common])
    q.add_argument("id")
    q.add_argument("--reason", default="")
    ap.add_parser("list", parents=[common])
    ap.add_parser("budget", parents=[common])
    q = ap.add_parser("seed", parents=[common])
    q.add_argument("path", help="numbered Markdown question list")
    q = ap.add_parser("decide", parents=[common], help="ask-or-act from predictions (JSON list on stdin)")
    q.add_argument("--domain", default="default")


def _open_ledger(store, author: str | None, key: str | None, required: bool = False):
    from . import crypto
    from .ledger import Ledger
    from .store import exo_home
    kp = Path(key) if key else (exo_home() / "keys" / f"{author}.key" if author else None)
    if kp is None or not kp.exists():
        if required:
            raise SystemExit(f"no key at {kp}; run: exo keygen {author}")
        return Ledger(store.ledger_path) if Path(store.ledger_path).exists() else None
    return Ledger(store.ledger_path, crypto.Signer.from_file(author or "agent", kp))


def cmd_ask(a, store) -> int:
    import sys
    c = a.ask_cmd
    led = None
    if c == "answer":
        led = _open_ledger(store, a.author, a.key, required=True)
    elif c == "next" and a.mark and a.key:
        led = _open_ledger(store, a.author, a.key)
    elif Path(store.ledger_path).exists():
        from .ledger import Ledger
        led = Ledger(store.ledger_path)
    qq = QuestionQueue(store.root, ledger=led)
    if c == "propose":
        q = qq.propose(a.text, a.node, a.rationale, impact=a.impact, uncertainty=a.uncertainty,
                       channel=a.channel, default=a.default)
        print(json.dumps(q.to_dict(), sort_keys=True))
    elif c == "next":
        for i, q in enumerate(qq.next_questions(a.n, mark=a.mark, author=a.author), 1):
            print(f"{i}. {q.text}" + (f" ({q.node})" if q.node else "") + f"  [{q.id} voi={q.voi:.2f}]")
    elif c == "answer":
        print(json.dumps(qq.answer(a.id, a.text, author=a.author), sort_keys=True))
    elif c == "drop":
        print(json.dumps(qq.drop(a.id, a.reason), sort_keys=True))
    elif c == "list":
        for q in qq.open():
            print(f"{q.voi:5.2f} {q.status:<8} {q.id} {q.text[:90]}")
    elif c == "budget":
        print(json.dumps(qq.budget(), sort_keys=True))
    elif c == "seed":
        added = seed_from_markdown(qq, Path(a.path).read_text(encoding="utf-8"), source=str(a.path))
        print(json.dumps({"added": len(added)}))
    elif c == "decide":
        preds = json.loads(sys.stdin.read() or "[]")
        print(json.dumps(should_ask({"domain": a.domain}, preds, root=store.root).to_dict(), sort_keys=True))
    return 0
