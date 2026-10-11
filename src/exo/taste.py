"""Taste beliefs: signatures, similarity, decay with reinforcement, and the conflict rule.

Pure functions and small dataclasses. No I/O, no ledger, no clock (callers pass `now`).
Rebuilt from the upstream taste-learning design (hermes-agent PR 126940, D-007) with two
changes: weight is a pure function of the evidence list, so it rebuilds from the ledger with no
sidecar state; and a conflict never overwrites, it goes through the principal's conflict rule.

Signature. An instruction is reduced to a set of signed content tokens: "+tok" for what the
principal wants, "-tok" for what they want avoided. "Use tables, not bullets" becomes
{+table, -bullet}. Two signatures agree when they overlap; they conflict when one overlaps the
other's flip ({+bullet, -table}).

Weight. Each piece of evidence has a strength in [0, 1] and an age. Strength decays by half-life,
and the decayed strengths combine by noisy-or: w = 1 - prod(1 - s_i * 2^(-age_i / half_life)).
Repeated evidence raises w toward 1; silence lets it fall toward 0. A half-life of None or 0
turns decay off (standing rulings).
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_HALF_LIFE = {"preference": 30.0, "rule": None}   # days; None = no decay
HIGH, MEDIUM = 0.75, 0.45                               # confidence label cut points
SIMILARITY = 0.6

# Words that carry polarity or filler, not topic.
FILLER = frozenset("""
the a an and or of to in on for is are be with as at by it this that from he him his she her their they
them its i me my we our you your please always never ever dont don't do does did not no nope just
really again also any every each all some should must shall will would can could may might need have
has had get got make sure very more less stop avoid instead rather than but so then there here when
whenever from now going forward future time one thing things way like want wants wanted agent agents
principal let us okay ok yes use using keep put give
""".split())

NEGATION_RE = re.compile(r"\b(don'?t|do not|dont|never|stop|avoid|no more|quit|not)\b", re.I)
GUARD_WORDS = (
    "publish", "public", "post", "posting", "comment", "tweet", "email", "send", "external", "github",
    "merge", "push", "force", "deploy", "production", "delete", "remove", "drop", "close", "pay",
    "payment", "spend", "money", "irreversible", "credential", "password", "account", "sign in", "login",
)


# --- tokens and signatures ----------------------------------------------------

def stem(w: str) -> str:
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def content_tokens(text: str | None) -> frozenset[str]:
    words = re.findall(r"[a-z0-9][a-z0-9']*", (text or "").lower())
    return frozenset(stem(w.strip("'")) for w in words if w not in FILLER and len(w) > 1)


def signature(want: str | None, avoid: str | None) -> frozenset[str]:
    return frozenset({"+" + t for t in content_tokens(want)} | {"-" + t for t in content_tokens(avoid)})


def flip(sig: Iterable[str]) -> frozenset[str]:
    return frozenset(("-" if t[0] == "+" else "+") + t[1:] for t in sig)


def similarity(a: Iterable[str], b: Iterable[str]) -> float:
    """Overlap coefficient when both sets have 2+ items (short instructions match longer ones),
    else Jaccard."""
    a, b = frozenset(a), frozenset(b)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if min(len(a), len(b)) >= 2:
        return inter / min(len(a), len(b))
    return inter / len(a | b)


def relation(a: Iterable[str], b: Iterable[str], threshold: float = SIMILARITY) -> str | None:
    """'agree', 'conflict' or None for two signatures."""
    a, b = frozenset(a), frozenset(b)
    same, opposed = similarity(a, b), similarity(a, flip(b))
    if opposed >= threshold and opposed > same:
        return "conflict"
    if same >= threshold:
        return "agree"
    return None


# --- decay --------------------------------------------------------------------

def decay_factor(age_days: float, half_life_days: float | None) -> float:
    """2^(-age / half_life). 1.0 when decay is off or age is not positive."""
    if not half_life_days or age_days <= 0:
        return 1.0
    return math.pow(2.0, -age_days / half_life_days)


def decayed(strength: float, age_days: float, half_life_days: float | None) -> float:
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be in [0, 1]")
    return strength * decay_factor(age_days, half_life_days)


def combine(evidence: Iterable[tuple[float, float]], half_life_days: float | None) -> float:
    """Noisy-or of decayed strengths. `evidence` is (strength, age_days) pairs."""
    keep = 1.0
    for strength, age in evidence:
        keep *= 1.0 - decayed(strength, max(age, 0.0), half_life_days)
    return max(0.0, min(1.0, 1.0 - keep))


def label(weight: float, high: float = HIGH, medium: float = MEDIUM) -> str:
    return "high" if weight >= high else "medium" if weight >= medium else "low"


def parse_ts(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def age_days(ts: str | datetime, now: str | datetime) -> float:
    return (parse_ts(now) - parse_ts(ts)).total_seconds() / 86400.0


# --- beliefs ------------------------------------------------------------------

@dataclass
class Evidence:
    id: str
    ts: str
    strength: float


@dataclass
class Belief:
    """A belief under consolidation: proposed, held on a conflict question, accepted, rejected
    or superseded. Rebuilt from ledger events by exo.consolidate."""
    id: str
    sig: frozenset[str]
    type: str
    claim: str
    created: str
    evidence: list[Evidence] = field(default_factory=list)
    status: str = "proposed"
    node_id: str | None = None
    guarded: bool = False
    event_id: str | None = None      # the belief_proposed event

    @property
    def active(self) -> bool:
        return self.status in ("proposed", "held", "accepted")

    @property
    def last_seen(self) -> str:
        return max((e.ts for e in self.evidence), default=self.created)

    def half_life(self, half_lives: Mapping[str, float | None] | None = None) -> float | None:
        hl = dict(DEFAULT_HALF_LIFE, **(half_lives or {}))
        return hl.get(self.type, hl.get("preference"))

    def weight(self, now: str | datetime, half_lives: Mapping[str, float | None] | None = None) -> float:
        hl = self.half_life(half_lives)
        return combine(((e.strength, age_days(e.ts, now)) for e in self.evidence), hl)


def best_match(sig: Iterable[str], beliefs: Sequence[Belief], threshold: float = SIMILARITY,
               include_inactive: bool = False) -> tuple[Belief | None, str | None]:
    """The belief most related to `sig`, and how ('agree' or 'conflict'). Active beliefs win ties."""
    sig = frozenset(sig)
    best: tuple[float, int, Belief, str] | None = None
    for b in beliefs:
        if b.status == "superseded" or (not include_inactive and not b.active):
            continue
        rel = relation(sig, b.sig, threshold)
        if rel is None:
            continue
        score = similarity(sig, b.sig if rel == "agree" else flip(b.sig))
        key = (score, int(b.active), b, rel)
        if best is None or key[:2] > best[:2]:
            best = key
    return (best[2], best[3]) if best else (None, None)


# --- conflict rule ------------------------------------------------------------

CONFLICT_RULES = ("newer_wins_unless_guarded", "newer_wins", "ask")


def guards(text: str, words: Iterable[str] = GUARD_WORDS) -> bool:
    """True when a belief's text protects something irreversible or public."""
    low = (text or "").lower()
    return any(re.search(r"\b" + re.escape(w.lower()), low) for w in words)


def resolve_conflict(older: Belief, rule: str = "newer_wins_unless_guarded") -> str:
    """'newer' (propose superseding the older belief, still reviewed) or 'ask' (raise a question
    and hold the newer proposal until the principal answers)."""
    if rule not in CONFLICT_RULES:
        raise ValueError(f"unknown conflict rule {rule!r}; expected one of {CONFLICT_RULES}")
    if rule == "ask":
        return "ask"
    if rule == "newer_wins":
        return "newer"
    return "ask" if older.guarded else "newer"
