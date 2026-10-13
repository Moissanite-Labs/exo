"""User signals in, labelled taste signals out (D-044, docs/TASTE-BASELINE.md sections 5 and 7).

    await classify.record_signals(store, ledger, labeller=None)    # the signature fallback
    await classify.record_signals(store, ledger, labeller=classify.decision_labeller(router))

Reads the principal's verdicts and utterances after its watermark and writes one `note` of type
`taste_signal` per (event, item) it labels. Only the principal's own words and actions are read: events by
the agent, a tool, a peer, the bus, a job or `exo_note` are skipped before any model is asked (D-040). In an
utterance, quoted blocks, code, attachments and pasted text are dropped first (`own_words`).

Candidates are found cheaply: the top 3 active items whose signature overlaps the behaviour by at least 0.3
(either way round, since a contradiction flips the signs).

The labeller is a hook. With none (the default when no `decision` route is configured) the fallback is
`exo.taste.relation`: a conflict is a contradiction with delta 0.5 and scope general, an agreement is an
agreement, and confidence is unknown, so a value never steps back on a single signal. A labeller only
classifies: its answer adds a signal to a record, and below `min_confidence` it counts as unrelated. It can
hold an item back (period, quoting, unrelated), and it can never swap, adopt, step back or pin anything: the
rule in `exo.baseline.shift` does that, from the ledger. At most `daily_cap` labeller calls a day; past the cap
the remaining events wait for the next day.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import taste
from ..store import PrincipalStore
from . import shift

CANDIDATE_OVERLAP = 0.3
MAX_CANDIDATES = 3
STRENGTH = {"edit": shift.EDIT, "reject": shift.EDIT, "should_have_been": shift.SHOULD, "accept": shift.EDIT}


@dataclass
class Label:
    relation: str                       # contradict | refine | agree | unrelated
    delta: float = 0.5
    scope: str = "general"
    voice: str | None = None
    confidence: float | None = None
    task: str | None = None
    claim: str = ""                     # the user's version, in neutral form
    route: str = "rules"


@dataclass
class Candidate:
    id: str
    claim: str
    sig: frozenset[str]
    cls: str = "method"


Labeller = Callable[[Candidate, str, str, str], "Label | None | Awaitable[Label | None]"]


# --- the user's own words ----------------------------------------------------------

FENCE_RE = re.compile(r"```.*?(```|$)", re.S)
QUOTE_LINE_RE = re.compile(r"^\s*>.*$", re.M)
QUOTED_RE = re.compile(r"[\"“][^\"”]{12,}[\"”]")
ATTACH_RE = re.compile(r"<(attachment|pasted|file|document)[^>]*>.*?(</\1>|$)", re.S | re.I)
PASTE_MIN = 600                          # a paragraph this long in a message is taken as pasted text


def own_words(text: str) -> str:
    """The user's own words in a message: no code, quoted lines, quoted spans, attachments or pasted
    articles (section 5)."""
    t = ATTACH_RE.sub(" ", text or "")
    t = FENCE_RE.sub(" ", t)
    t = QUOTE_LINE_RE.sub(" ", t)
    t = QUOTED_RE.sub(" ", t)
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) > 1:
        paras = [p for p in paras if len(p) < PASTE_MIN]
    return "\n\n".join(p.strip() for p in paras).strip()


def candidates(sig: frozenset[str], items: Sequence[Candidate], k: int = MAX_CANDIDATES,
               floor: float = CANDIDATE_OVERLAP) -> list[Candidate]:
    scored = []
    for it in items:
        sc = max(taste.similarity(sig, it.sig), taste.similarity(sig, taste.flip(it.sig)))
        if sc >= floor:
            scored.append((sc, it.id, it))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored[:k]]


def fallback(item: Candidate, behaviour_sig: frozenset[str]) -> Label | None:
    rel = taste.relation(behaviour_sig, item.sig)
    if rel == "conflict":
        return Label("contradict", 0.5, "general")
    if rel == "agree":
        return Label("agree", 0.0, "general")
    return None


# --- the decision model ---------------------------------------------------------------

Q_REL = ("How does the user's behaviour relate to the taste item? contradict: they want the opposite; refine: a "
         "narrower version; agree: the same; unrelated.")
Q_DELTA = "How far apart are the user's behaviour and the taste item?"
Q_SCOPE = ("Does the behaviour look general, bound to this project, to this kind of task, or to this period "
           "(a deadline, travel, a bad week)?")
Q_VOICE = ("In the user's words, are they stating their own view, quoting someone, asking about a view, or asking "
           "for a role to be played?")
Q_TASK = "What kind of task is this?"
Q_CLAIM = "Write the user's version of the taste item as one short neutral sentence starting 'The user wants'."


DELTA_LEVELS = ("none", "small", "medium", "large", "opposite")  # index i maps to shift.DELTAS[i]


def _expected_level(probabilities: dict[str, float]) -> float:
    """The probability-weighted level index (0 to 4). A generative route puts 1.0 on its one pick."""
    total = sum(p for k, p in probabilities.items() if k in DELTA_LEVELS)
    if total <= 0:
        return 0.0
    return sum(DELTA_LEVELS.index(k) * p for k, p in probabilities.items() if k in DELTA_LEVELS) / total


def decision_labeller(router=None) -> Labeller | None:
    """A labeller on the `decision` role (D-015), or None when no decision route is configured: then the
    signature fallback is used and the model is never asked."""
    if router is None:
        try:
            from ..models_cmd import build_router
            router = build_router()
        except Exception:
            return None
    if not (getattr(router, "roles", None) or {}).get("decision"):
        return None
    from ..core.models.decision import ask_choice_result

    async def label(item: Candidate, behaviour: str, reply: str, kind: str) -> Label | None:
        state = {"taste item": item.claim, "the user's own words or action": behaviour,
                 "the agent reply being corrected": reply or "(none)", "kind": kind}
        rel = await ask_choice_result(router, state, Q_REL, list(shift.RELATIONS))
        conf = rel.confidence if rel.confidence is not None else max(rel.probabilities.values(), default=0.0)
        if rel.choice in ("unrelated", "refine"):
            return Label(rel.choice, 0.0, confidence=conf, route=rel.route or "decision")
        delta = await ask_choice_result(router, state, Q_DELTA, list(DELTA_LEVELS))
        scope = await ask_choice_result(router, state, Q_SCOPE, ["general", "this project", "this kind of task",
                                                                 "this period"])
        task = await ask_choice_result(router, state, Q_TASK, list(shift.TASK_KINDS))
        voice = None
        if item.cls == "value" and kind == "utterance":
            voice = (await ask_choice_result(router, state, Q_VOICE, ["own view", "quoting", "asking",
                                                                      "role-playing"])).choice
            voice = "own" if voice == "own view" else voice
        d = shift.DELTAS[max(0, min(4, round(_expected_level(delta.probabilities))))]
        sc = {"this project": "project", "this kind of task": "task", "this period": "period"}.get(scope.choice,
                                                                                                 "general")
        return Label(rel.choice, d, sc, voice, conf, task.choice, "", rel.route or "decision")

    return label


# --- recording ---------------------------------------------------------------------------

def _watermark(led) -> int:
    upto = 0
    for ev in led.iter(kind="note"):
        b = ev["body"] or {}
        if b.get("type") == "taste_scan":
            upto = max(upto, int(b.get("upto") or 0))
    return upto


def _model_calls_today(led, day: str) -> int:
    return sum(1 for ev in led.iter(kind="note") if (ev["body"] or {}).get("type") == "taste_signal"
               and (ev["body"] or {}).get("route") not in (None, "rules") and ev["ts"][:10] == day)


def _behaviour(ev: Mapping, led) -> tuple[str, str, str, float]:
    """(the user's words or action, the agent reply it judged, kind, strength)."""
    b = ev.get("body") or {}
    if ev["kind"] == "verdict":
        said = next((b[k] for k in ("wanted", "chosen", "text", "edited") if isinstance(b.get(k), str)
                     and b[k].strip()), "")
        target = led.get(b.get("target")) if b.get("target") else None
        reply = str(((target or {}).get("body") or {}).get("text") or "")
        verdict = str(b.get("verdict") or "")
        if verdict == "accept":
            return reply, "", "accept", STRENGTH["accept"]
        return said, reply, f"verdict:{verdict}", STRENGTH.get(verdict, shift.EDIT)
    if ev["kind"] == "utterance" and b.get("role", "user") == "user":
        return own_words(str(b.get("text") or "")), "", "utterance", shift.EDIT
    return "", "", "", 0.0


def _items(store: PrincipalStore, now: str, pack=None) -> list[Candidate]:
    from .layers import view
    v = view(store, now=now, pack=pack)
    out = []
    by_id = {h.id: h for h in v.pack.items}
    learned = {u.id: u for u in (v.state.layer.items if v.state and v.state.layer else [])}
    for e in v.entries:
        sig = by_id[e.id].sig if e.id in by_id else (learned[e.id].sig if e.id in learned else frozenset())
        if e.source == "stepped_back" or sig:
            out.append(Candidate(e.id, e.claim, sig or taste.signature(e.claim, None), e.cls))
    return out


async def record_signals(store: PrincipalStore, ledger, *, labeller: Labeller | None = None,
                         now: str | datetime | None = None, author: str | None = None,
                         items: Sequence[Candidate] | None = None, pack=None) -> dict:
    """Label the principal's new verdicts and utterances against the active items; write `taste_signal` notes
    and move the `taste_scan` watermark. Returns counts."""
    from .. import provenance
    from .layers import taste_policy
    now_s = (now if isinstance(now, str) else (now or datetime.now(timezone.utc)).isoformat()).replace("+00:00", "Z")
    pol = taste_policy(store)
    principals = set(provenance.principal_names(store))
    items = list(items) if items is not None else _items(store, now_s, pack)
    author = author or getattr(ledger, "author", None) or "exo-learn"
    res = {"read": 0, "not_principal": 0, "signals": 0, "abstained": 0, "capped": False}
    mark = _watermark(ledger)
    done = {((ev["body"] or {}).get("item"), (ev["body"] or {}).get("evidence")) for ev in ledger.iter(kind="note")
            if (ev["body"] or {}).get("type") == "taste_signal"}
    calls = _model_calls_today(ledger, now_s[:10])
    upto = mark
    for ev in list(ledger.iter(kind=("verdict", "utterance"), since=mark)):
        if ev.get("author") not in principals:            # D-040: skipped before any model is asked
            res["not_principal"] += 1
            upto = ev["seq"]
            continue
        said, reply, kind, strength = _behaviour(ev, ledger)
        if not said.strip():
            upto = ev["seq"]
            continue
        res["read"] += 1
        sig = frozenset(taste.signature(said, None))
        if kind.startswith("verdict:") or kind == "utterance":
            from ..consolidate import parse_instruction
            want, avoid, _ = parse_instruction(said)
            sig = taste.signature(want, avoid) or sig
        cands = candidates(sig, items)
        if labeller is not None and calls + len(cands) > int(pol["daily_cap"]):
            res["capped"] = True
            break
        for c in cands:
            if (c.id, ev["id"]) in done:                    # a review cancelled mid-way: never label twice
                continue
            lab = None
            if labeller is not None:
                calls += 1
                try:
                    out = labeller(c, said, reply, kind)
                    lab = await out if inspect.isawaitable(out) else out
                except Exception:
                    lab = None
            else:
                lab = fallback(c, sig)
                if lab is not None and kind == "accept" and lab.relation != "agree":
                    lab = None                             # accepting an answer only ever agrees
            if lab is None:
                continue
            if lab.confidence is not None and lab.confidence < float(pol["min_confidence"]):
                res["abstained"] += 1
                lab = Label("unrelated", 0.0, confidence=lab.confidence, route=lab.route)
            stated = kind == "utterance" and lab.voice == "own" and lab.relation == "contradict"
            body = {"type": "taste_signal", "item": c.id, "relation": lab.relation, "delta": lab.delta,
                    "scope": lab.scope, "voice": lab.voice, "confidence": lab.confidence, "route": lab.route,
                    "evidence": ev["id"], "at": ev["ts"], "strength": shift.INFERRED if kind == "accept" else strength,
                    "kind": kind, "stated": stated, "claim": lab.claim or (said[:200] if lab.relation == "contradict"
                                                                          else ""),
                    "project": (ev.get("body") or {}).get("project"), "task": lab.task}
            ledger.append("note", body, author=author, session=ev.get("session"), refs=[ev["id"]])
            res["signals"] += 1
        upto = ev["seq"]
    if upto > mark:
        ledger.append("note", {"type": "taste_scan", "upto": upto, "read": res["read"]}, author=author)
    return res


def record_signals_sync(store: PrincipalStore, ledger, **kw) -> dict:
    return asyncio.run(record_signals(store, ledger, **kw))
