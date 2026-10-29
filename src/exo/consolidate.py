"""Consolidation: ledger events in, reviewed belief proposals out (principles 5, 6, 8, 9).

    res = consolidate_run(store, ledger)          # reads events after the watermark
    proposals_list(store, ledger)                 # open, held, accepted, rejected
    proposal_accept(store, ledger, pid)           # node edit + belief_accepted
    proposal_reject(store, ledger, pid, "why")    # note belief_rejected

Input kinds: `verdict`, `ruling`, `utterance`, `answer`, and `note` of type `consolidate_pending`.
Only events whose author is the principal (`exo.provenance.principal_names`: `principal`, the store's id, and
principal ids pinned in the key registry) are read as the principal's voice (D-040). A ruling the agent wrote
with `exo_note`, or one a peer's text produced, is skipped and counted in `not_principal`.

- Each verdict (edit, reject, should_have_been) and each ruling becomes a **belief proposal** in
  positive local form, "The principal wants Y, not X, because Z", with evidence refs, domain, type,
  confidence and an implication beyond the instance. It also carries a preference pair
  (context, rejected, chosen, evidence), appended to `training/preference-pairs.jsonl` on accept.
- **Rule of three:** an instruction the principal gives in 3+ utterances with no encoded belief
  gets a proposal to encode it. Live turns carry role `user` and rows from `exo ingest` role `principal`;
  both count. Summaries (`secondary`) are not anyone's own words and do not. Nor is superseded text: the older
  versions of an edited ChatGPT prompt (`branch: alt`) and Hermes rows marked `inactive`. An imported row
  counts as said at its source time (`source_ts`), not when it was imported (`said_at`).
  A rejected belief that agrees still counts as encoding the instruction.
- **Taste:** a new correction or ruling that agrees with an active belief (proposed, held, accepted) reinforces
  it (a `note` of type `belief_reinforced`); weight decays by half-life unless reinforced (`exo.taste`). Only
  active beliefs are matched, so a rejected one is never reinforced and never hides an active one. One that
  agrees only with a rejected belief is a new proposal. A reviewer taste or fact lesson (`propose_typed`) whose
  evidence the rejected beliefs it agrees with already hold in full, together, is skipped: no proposal, question
  or reinforcement (#861). A skill lesson is not checked and is proposed as before.
- **Conflict:** an event that opposes an active belief goes through the store's conflict rule.
  The default is newer wins unless the older belief guards something irreversible or public; then
  a numbered `question` event is raised and the new proposal is held until an `answer` arrives.
  Nothing is overwritten silently: "newer wins" still lands as a reviewed supersede proposal.

Outputs: `belief_proposed` events, and draft files in `<store>/proposals/`: `<pid>.md` (the node
as it would be written), `<pid>.diff` (when it edits an existing node), `<pid>.json` (the full
proposal). Decided drafts move to `proposals/archive/`. `proposals/state.json` is a rebuildable
cache of instruction clusters for the rule of three. A rebuild reads only principal-authored utterances (D-040).

Watermark: each run that reads events ends with a `note` of type `consolidate_run` whose `upto`
is the last sequence number read. Proposal ids are hashes of their evidence, so a lost watermark
cannot duplicate proposals either.

Policy (optional `<store>/policies/consolidate.yaml`, defaults shown):

    conflict_rule: newer_wins_unless_guarded   # or newer_wins, ask
    guard_words: [publish, public, post, ...]  # replaces exo.taste.GUARD_WORDS
    half_life_days: {preference: 30, rule: 0}  # 0 or null = no decay
                                               # add domain: {mind: 14} for a half-life per domain
    similarity: 0.6
    rule_of_three: 3
    target_overlap: 0.6                        # share of a proposal's tokens a node must cover
    domains: {keyword: domain}                 # extra keyword-to-domain hints
    stale_below: 0.2                           # report taste beliefs that decayed below this

Reflection is a seam. `RuleReflector` (default) is deterministic templates over the capture
heuristics. `ModelReflector(complete)` wraps any `complete(prompt) -> str` model call, validates
the JSON it returns and falls back to the rules on any failure. Nothing here calls an API.

CLI hook: `exo.cli` lists both commands in its `LAZY` table. It imports this module, calls
`register(sp, common)` and runs `cmd_consolidate` or `cmd_proposals` only when one of them runs.

    exo consolidate [--as SEAT] [--dry-run]
    exo proposals [list|show|accept|reject] [ID] [--reason R] [--all]
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import provenance, taste
from .capture import (CORRECTION, IMPERATIVE_RE, RULING, caps_emphasis, classify_sentence, clip,
                      is_imperative, is_trivial, normalize, sentences)
from .ledger import Ledger
from .store import LEAKS, PrincipalStore
from .taste import Belief, Evidence

INPUT_KINDS = ("verdict", "ruling", "utterance", "answer", "note")
STATE_KINDS = ("belief_proposed", "belief_accepted", "note", "question", "answer")
DEFAULT_AUTHOR = "exo-consolidate"
PAIRS_FILE = "training/preference-pairs.jsonl"

DEFAULT_POLICY: dict[str, Any] = {
    "conflict_rule": "newer_wins_unless_guarded",
    "guard_words": list(taste.GUARD_WORDS),
    "half_life_days": dict(taste.DEFAULT_HALF_LIFE),
    "similarity": taste.SIMILARITY,
    "rule_of_three": 3,
    "target_overlap": 0.6,
    "domains": {},
    "stale_below": 0.2,
}

STRENGTH = {"should_have_been": 0.8, "reject": 0.6, "edit": 0.6, "ruling": 0.9, "instruction": 0.4}

DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "relationship": ("comment", "post", "reply", "email", "message", "public", "external", "report", "brief",
                     "tag", "sign", "approval", "approve", "ask", "review", "link", "url", "notify", "tweet"),
    "mind": ("tone", "style", "wordy", "short", "shorter", "long", "verbose", "voice", "dash", "format",
             "table", "bullet", "prose", "concise", "language", "sentence", "word", "emoji"),
    "systems": ("model", "provider", "server", "machine", "config", "deploy", "key", "cron", "profile",
                "install", "gateway", "token", "budget", "paid", "api"),
    "work": ("code", "test", "pr", "merge", "branch", "repo", "commit", "issue", "project", "ship", "bug",
             "feature", "release", "orchestrat", "delegat", "task"),
}

IMPLICATIONS: tuple[tuple[re.Pattern, str], ...] = tuple((re.compile(p, re.I), s) for p, s in (
    (r"\b(too (long|wordy|verbose)|shorter|concise|brief|wordy|verbose)\b",
     "Default to the shorter form in all similar output, not only this reply."),
    (r"\b(comment|post|tweet|email|external|public|github|repo)\w*\b",
     "Applies to all public and external writing, not only this post."),
    (r"\b(ask|approv|confirm|permission|merge|close|delete)\w*\b",
     "Moves the approval line: apply it to the whole class of actions, then report."),
    (r"\b(again|already said|told you|as i said|i said)\b",
     "This was a repeat. It should already be known; encode it so it does not recur."),
    (r"\b(model|provider|paid|budget|spend|cost)\w*\b",
     "Treat model and spend choices as the principal's call across all lanes."),
))


class ConsolidateError(ValueError):
    pass


# --- policy -------------------------------------------------------------------

def load_policy(store: PrincipalStore) -> dict:
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    path = store.root / "policies" / "consolidate.yaml"
    if path.is_file():
        from .gate import parse_yaml
        raw = parse_yaml(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ConsolidateError(f"{path}: expected a mapping")
        for k, v in raw.items():
            if k == "half_life_days" and isinstance(v, Mapping):
                for t, d in v.items():
                    if t == "domain" and isinstance(d, Mapping):   # domain: {mind: 14} sets a half-life per domain
                        pol[k].update({f"domain:{n}": (float(x) if x else None) for n, x in d.items()})
                    else:
                        pol[k][t] = float(d) if d else None
            elif k == "domains" and isinstance(v, Mapping):
                pol[k].update({str(a): str(b) for a, b in v.items()})
            elif k in pol:
                pol[k] = v
    if pol["conflict_rule"] not in taste.CONFLICT_RULES:
        raise ConsolidateError(f"conflict_rule must be one of {taste.CONFLICT_RULES}")
    return pol


# --- observations -------------------------------------------------------------

@dataclass
class Observation:
    """One principal signal read from the ledger, parsed into want / avoid / reason."""
    event_id: str
    seq: int
    ts: str
    kind: str                    # correction | ruling | instruction
    source: str                  # verdict:edit, ruling, utterance, ...
    text: str                    # the principal's own words
    want: str | None = None
    avoid: str | None = None
    reason: str | None = None
    rejected: str | None = None  # the agent output that was judged (verdicts)
    cues: tuple[str, ...] = ()
    strength: float = 0.5
    session: str | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def sig(self) -> frozenset[str]:
        s = taste.signature(self.want, self.avoid)
        return s or taste.signature(self.text, None)


LEAD_RE = re.compile(
    r"^\W*(?:(?:no|nope|nah|wrong|ok|okay|please|again|also|and|so|hey|look|listen|actually|"
    r"(?:important\s+)?corrections?|from now on|going forward|"
    r"in future|in the future|henceforth|i (?:already )?(?:said|told you)|as i said|you should|you must|"
    r"agents? should|we should|make sure (?:to|you)?|remember to|always)\b[\s,.:;!-]*)+",
    re.I)
REASON_RE = re.compile(r"\s*[,;(-]?\s*\b(?:because|since|so that|as that|as it|as this|given that)\b\s*", re.I)
NEG_LEAD_RE = re.compile(r"^(?:(?:you|we|agents?)\s+)?(?:don'?t|do not|dont|never|stop|avoid|no more|quit|no)\s+(?:ever\s+)?(?:again\s+)?",
                         re.I)


def _tidy(s: str | None) -> str | None:
    if s is None:
        return None
    s = normalize(s).strip(" ,.;:!?\"'()-")
    if sum(c.isupper() for c in s) > 0.6 * max(1, sum(c.isalpha() for c in s)):
        s = s.lower()   # shouting is emphasis, not content
    s = re.sub(r"\s+again$", "", s, flags=re.I)
    if not s:
        return None
    if len(s) > 1 and s[0].isupper() and not s[:2].isupper():
        s = s[0].lower() + s[1:]
    return s


def split_reason(text: str) -> tuple[str, str | None]:
    m = REASON_RE.search(text)
    if not m or m.start() == 0:
        return text, None
    return text[:m.start()], _tidy(text[m.end():])


TOO_RE = re.compile(r"^\W*(?:(?:no|wrong)\W+)?(?:way |much |far )?too (long|wordy|verbose|short|formal|casual|much|many)"
                    r"\W*$", re.I)
TOO_WANT = {"long": "shorter output", "wordy": "shorter output", "verbose": "shorter output",
            "short": "more detail", "formal": "a plainer register", "casual": "a more formal register",
            "much": "less of it", "many": "fewer items"}
COMPLAINT_RE = re.compile(r"^\W*(no|nope|wrong|again|not that|ugh|why)\W*$", re.I)


def parse_instruction(text: str) -> tuple[str | None, str | None, str | None]:
    """(want, avoid, reason) from an instruction or correction. Each sentence is parsed; the first
    want, avoid and reason found win, so "Don't X. Y, not Z." gives want Y, avoid X."""
    parts = [x for x in re.split(r"(?<=[.!?])\s+|\n+", normalize(text)) if x.strip()]
    want = avoid = reason = too = None
    for part in parts:
        m = TOO_RE.match(part)
        if m:
            too = too or TOO_WANT[m.group(1).lower()]
            continue
        if COMPLAINT_RE.match(part) or len(re.findall(r"[A-Za-z']+", part)) < 2:
            continue
        w, a, r = _parse_one(part)
        want, avoid, reason = want or w, avoid or a, reason or r
    return want or too, avoid, reason


def _parse_one(text: str) -> tuple[str | None, str | None, str | None]:
    s = normalize(text)
    s, reason = split_reason(s)
    s = LEAD_RE.sub("", s).strip()
    patterns = (
        r"^(?:not|don'?t|do not|dont|never)\s+(?P<x>.+?),?\s+(?:but|instead,?)\s+(?P<y>.+)$",
        r"^not\s+(?P<x>[^,]+?),\s*(?P<y>.+)$",
        r"^(?:instead of|rather than)\s+(?P<x>.+?),\s*(?P<y>.+)$",
        r"^(?P<y>.+?),?\s+(?:not|rather than|instead of)\s+(?P<x>.+)$",
    )
    if not NEG_LEAD_RE.match(s) or re.search(r"\b(but|instead)\b", s, re.I):
        for p in patterns:
            m = re.match(p, s, re.I)
            if m:
                y, x = _tidy(m.group("y")), _tidy(m.group("x"))
                bad = re.match(r"^(that'?s|this is|it'?s|it is|what)\b", y or "", re.I)
                if y and x and not bad and len(taste.content_tokens(y)) >= 1:
                    return _predicate(y), x, reason
    m = NEG_LEAD_RE.match(s)
    if m:
        return None, _tidy(s[m.end():]), reason
    m = re.match(r"^(?:you'?re|you are|you keep|you always|you just)\s+(?P<x>.+)$", s, re.I)
    if m:   # "you're X" / "you keep X" describes the unwanted behaviour
        return None, _tidy(m.group("x")), reason
    m = re.match(r"^(?:why )?(?:did|would|are|were) you (?:just )?(?P<x>.+?)\??$", s, re.I)
    if m:   # "why did you X?" judges X: the principal wants X avoided
        return None, _tidy(m.group("x")), reason
    if s.endswith("?"):
        return f'the question "{_tidy(s)}?" answered before they have to ask it', None, reason
    if re.match(r"^(that'?s|this is|it'?s) not (what|it|right)\b", s, re.I):
        return None, None, reason
    return _predicate(_tidy(s)), None, reason


def _predicate(s: str | None) -> str | None:
    """"language should be plain" -> "language that is plain"."""
    if not s:
        return s
    m = re.match(r"^(?P<subj>[\w' -]{1,40}?) (?:should|must|needs to|has to) be (?P<pred>.+)$", s, re.I)
    return f"{m.group('subj')} that is {m.group('pred')}" if m else s


def _event_text(ev: Mapping | None) -> str:
    if not ev:
        return ""
    b = ev.get("body") or {}
    for k in ("text", "description", "content", "summary"):
        if isinstance(b.get(k), str) and b[k].strip():
            return b[k]
    return ""


def imported(ev: Mapping) -> bool:
    """True for a row `exo ingest` wrote: its body names the adapter it came from (`src.adapter`)."""
    src = (ev.get("body") or {}).get("src")
    return isinstance(src, Mapping) and bool(src.get("adapter"))


def said_at(ev: Mapping) -> str:
    """When the words were said: an imported row's own time (`source_ts`), else when the ledger got them. A source
    time after the ledger time, or one with no time zone, is not believed."""
    ts, src = ev["ts"], (ev.get("body") or {}).get("source_ts")
    if isinstance(src, str):
        try:
            when = taste.parse_ts(src)
            if when.tzinfo is not None and when < taste.parse_ts(ts):
                return src
        except (TypeError, ValueError):
            pass
    return ts


def observe_event(ev: Mapping, ledger: Ledger) -> Observation | None:
    """Observation for a verdict or ruling event, else None."""
    b = ev.get("body") or {}
    if ev["kind"] == "verdict":
        verdict = b.get("verdict")
        if verdict == "accept":
            return None
        said = next((b[k] for k in ("wanted", "chosen", "text", "edited") if isinstance(b.get(k), str)
                     and b[k].strip()), "")
        if not said:
            return None
        if b.get("wanted") or b.get("chosen"):
            want, avoid, reason = _tidy(said), None, None
            w2, a2, r2 = parse_instruction(said)
            if a2 and not w2:
                want, avoid = None, a2
            elif w2 and a2:
                want, avoid = w2, a2
            reason = r2
        else:
            want, avoid, reason = parse_instruction(said)
        reason = _tidy(b.get("reason")) or reason
        target = ledger.get(b.get("target")) if b.get("target") else None
        cues = tuple(b.get("cues") or ())
        strength = STRENGTH.get(verdict, 0.6) + (0.1 if ("caps" in cues or caps_emphasis(said)) else 0.0)
        return Observation(ev["id"], ev["seq"], ev["ts"], "correction", f"verdict:{verdict}", clip(said),
                           want, avoid, reason, clip(_event_text(target)) or None, cues, min(strength, 1.0),
                           ev.get("session"), [ev["id"]])
    if ev["kind"] == "ruling":
        said = _event_text(ev)
        if not said.strip():
            return None
        want, avoid, reason = parse_instruction(said)
        cues = tuple(b.get("cues") or ())
        strength = STRENGTH["ruling"] + (0.05 if "numbered" in cues else 0) + \
            (0.05 if ("caps" in cues or caps_emphasis(said)) else 0)
        return Observation(ev["id"], ev["seq"], ev["ts"], "ruling", "ruling", clip(said), want, avoid,
                           _tidy(b.get("reason")) or reason, None, cues, min(strength, 1.0),
                           ev.get("session"), [ev["id"]])
    return None


def instructions_in(ev: Mapping) -> list[Observation]:
    """Instruction-like sentences in a principal utterance (for the rule of three). Role `user` is a live turn,
    `principal` an `exo ingest` row, dated by its source time. The caller checks the author (D-040)."""
    b = ev.get("body") or {}
    if b.get("role", "user") not in ("user", "principal") or b.get("secondary"):
        return []
    if b.get("branch") == "alt" or b.get("inactive"):
        return []  # superseded: an edited ChatGPT prompt's older versions, a Hermes row no longer active
    text = _event_text(ev)
    if is_trivial(text):
        return []
    out = []
    for s in sentences(text):
        kind, score, cues = classify_sentence(s, after_agent=True)
        if kind not in (RULING, CORRECTION) and not is_imperative(s):
            continue
        want, avoid, reason = parse_instruction(s)
        o = Observation(ev["id"], ev["seq"], said_at(ev), "instruction", "utterance", clip(s), want, avoid, reason,
                        None, tuple(cues), STRENGTH["instruction"], ev.get("session"), [ev["id"]])
        if len(o.sig) >= 2:
            out.append(o)
    return out


# --- reflection ---------------------------------------------------------------

@dataclass
class Reflection:
    claim: str                   # "The principal wants Y, not X, because Z."
    wants: str
    avoid: str | None
    because: str
    implication: str
    domain: str
    type: str                    # preference | rule
    title: str
    reflector: str = "rules"


@dataclass
class ReflectContext:
    domains: Sequence[str]
    policy: Mapping[str, Any]
    date: str


@runtime_checkable
class Reflector(Protocol):
    name: str

    def reflect(self, obs: Observation, ctx: ReflectContext) -> Reflection: ...


EXTRA_VERBS = frozenset("""sign tag mention mention reply change replace touch edit rewrite summarize explain
apologize thank greet use add post close merge push delete ask wait report send open run start stop
make write put give show call name leave set pin move commit file log note remember skip batch route
answer copy archive fix test ship pay buy spend publish deploy repeat return loop guess assume claim
over-explain hedge cc include attach print""".split())


def _verb_led(phrase: str | None) -> bool:
    if not phrase:
        return False
    w = phrase.split(" ", 1)[0].lower()
    return w.isalpha() and (w in EXTRA_VERBS or bool(IMPERATIVE_RE.match(w))) and w not in ("no", "only")


def _infinitive(phrase: str) -> str:
    """"asking me first" -> "the agent to ask me first" when the stem is a known verb."""
    w, _, rest = phrase.partition(" ")
    if w.lower().endswith("ing") and len(w) > 5:
        for base in (w[:-3], w[:-3] + "e", w[:-4]):
            if _verb_led(base):
                return f"the agent to {base} {rest}".rstrip()
    return phrase


def gerund(phrase: str) -> str:
    words = phrase.split(" ", 1)
    w, rest = words[0], (" " + words[1] if len(words) > 1 else "")
    low = w.lower()
    if not (low in EXTRA_VERBS or IMPERATIVE_RE.match(w)) or low.endswith("ing") or not low.isalpha():
        return phrase
    if low.endswith("ie"):
        g = w[:-2] + "ying"
    elif low.endswith("e") and not low.endswith(("ee", "ye", "oe")) and len(low) > 2:
        g = w[:-1] + "ing"
    elif (len(low) <= 4 and len(low) >= 3 and low[-1] not in "aeiouwxy" and low[-2] in "aeiou"
          and low[-3] not in "aeiou"):
        g = w + w[-1] + "ing"
    else:
        g = w + "ing"
    return g + rest


def guess_domain(text: str, domains: Sequence[str], extra: Mapping[str, str] | None = None) -> str | None:
    low = text.lower()
    for kw, dom in (extra or {}).items():
        if re.search(r"\b" + re.escape(kw.lower()), low) and dom in domains:
            return dom
    best, score = None, 0
    for dom, kws in DOMAIN_HINTS.items():
        if dom not in domains:
            continue
        n = sum(1 for k in kws if re.search(r"\b" + re.escape(k), low))
        if n > score:
            best, score = dom, n
    return best


def default_domain(domains: Sequence[str]) -> str:
    return "mind" if "mind" in domains or not domains else sorted(domains)[0]


def implication_for(obs: Observation, domain: str) -> str:
    hay = " ".join(filter(None, (obs.text, obs.want, obs.avoid, " ".join(obs.cues))))
    found = [s for p, s in IMPLICATIONS if p.search(hay)][:2]
    if obs.kind == "instruction":
        found.insert(0, f"Repeated {len(obs.evidence)} times. Encode it so the principal does not repeat it.")
    if not found:
        found = [f"Applies beyond this instance: check similar {domain} work against it before showing it."]
    return " ".join(found)


class RuleReflector:
    """Deterministic templates over the capture heuristics. No model, no I/O."""
    name = "rules"

    def reflect(self, obs: Observation, ctx: ReflectContext) -> Reflection:
        rej = clip(obs.rejected or "", 90).strip('"\' ')
        rejected = f'output like "{rej}"' if rej else None
        if obs.want:
            want = f"the agent to {obs.want}" if _verb_led(obs.want) else obs.want
            if not obs.avoid:
                avoid = rejected
            elif _verb_led(obs.avoid):
                avoid = f"to {obs.avoid}" if _verb_led(obs.want) else gerund(obs.avoid)
            else:
                avoid = obs.avoid
        elif obs.avoid and (m := re.match(r"^(?P<x>.+?) without (?P<y>.+)$", obs.avoid)):
            want, avoid = f"{_infinitive(m.group('y'))} before {gerund(m.group('x'))}", rejected
        elif obs.avoid:
            want, avoid = f"work done without {gerund(obs.avoid)}", rejected
        else:
            want, avoid = f'what they asked for ("{clip(obs.text, 120)}")', rejected
        date = obs.ts[:10]
        because = obs.reason or (f"they said so on {date} (no reason given)" if obs.kind != "instruction"
                                 else f"they asked for it {len(obs.evidence)} times (no reason given)")
        claim = f"The principal wants {want}"
        if avoid:
            claim += f", not {avoid}"
        claim += f", because {because}."
        extra = ctx.policy.get("domains")
        domain = guess_domain(obs.text, ctx.domains, extra) or guess_domain(obs.rejected or "", ctx.domains, extra) \
            or default_domain(ctx.domains)
        typ = "preference" if obs.kind == "correction" else "rule"
        short = want[len("the agent to "):] if want.startswith("the agent to ") else want
        title = clip(("Wants " + short) if not want.startswith("what they") else "Wants " + clip(obs.text, 50), 70)
        return Reflection(claim, want, avoid, because, implication_for(obs, domain), domain, typ, title, self.name)


MODEL_PROMPT = """You turn one correction from a principal into a belief about what they want.
Write it in positive local form: "The principal wants Y, not X, because Z." Never store the bad
example as the claim. Then say what it implies beyond this one instance.

Principal's words: {text}
Agent output that was judged (may be empty): {rejected}
Stated reason (may be empty): {reason}
Allowed domains: {domains}

Reply with one JSON object and nothing else:
{{"wants": "...", "avoid": "... or null", "because": "...", "implication": "...",
 "domain": "one of the allowed domains", "type": "preference or rule", "title": "short title"}}"""


_JSON_STARTS = 64   # brace positions tried before the reply counts as having no object


def _decode_model_json(raw: str) -> Any:
    """Decode the first JSON object in model output, skipping the prose around it.

    The old greedy match ran to the last brace in the text, so a trailing
    brace in prose after the object dropped valid output. Decoding is tried
    from each `{` in turn, so a bracketed note, an array or an object wrapped
    in an array before or around the answer does not hide it either. The first
    start that decodes to an object wins.
    """
    decoder, start = json.JSONDecoder(), raw.find("{")
    for _ in range(_JSON_STARTS):
        if start < 0:
            break
        try:
            value = decoder.raw_decode(raw, start)[0]
        except ValueError:
            pass
        else:
            if isinstance(value, dict):
                return value
        start = raw.find("{", start + 1)
    raise ValueError("no JSON object in the model output")


class ModelReflector:
    """Adapter seam for a model call. `complete(prompt) -> str` is any function the host supplies
    (Hermes auxiliary model, a local model, a test double). Bad output falls back to the rules."""
    name = "model"

    def __init__(self, complete: Callable[[str], str], fallback: Reflector | None = None):
        self.complete = complete
        self.fallback = fallback or RuleReflector()

    def reflect(self, obs: Observation, ctx: ReflectContext) -> Reflection:
        base = self.fallback.reflect(obs, ctx)
        try:
            raw = self.complete(MODEL_PROMPT.format(text=obs.text, rejected=obs.rejected or "",
                                                    reason=obs.reason or "", domains=", ".join(ctx.domains)))
            data = _decode_model_json(raw)
            wants = _tidy(str(data["wants"]))
            if not wants or re.match(r"^(not|no|don'?t|never)\b", wants, re.I):
                raise ValueError("wants must be positive")
            avoid = _tidy(str(data["avoid"])) if data.get("avoid") not in (None, "", "null") else base.avoid
            because = _tidy(str(data.get("because") or "")) or base.because
            domain = data.get("domain") if data.get("domain") in ctx.domains else base.domain
            typ = data.get("type") if data.get("type") in ("preference", "rule") else base.type
            claim = f"The principal wants {wants}" + (f", not {avoid}" if avoid else "") + f", because {because}."
            return Reflection(claim, wants, avoid, because, str(data.get("implication") or base.implication),
                              domain, typ, clip(str(data.get("title") or base.title), 70), self.name)
        except Exception:
            return base


# --- proposals ----------------------------------------------------------------

@dataclass
class Proposal:
    id: str
    kind: str                    # correction | ruling | rule_of_three
    action: str                  # create | update
    node_id: str
    claim: str
    wants: str
    avoid: str | None
    because: str
    implication: str
    domain: str
    type: str
    title: str
    confidence: str
    weight: float
    sig: list[str]
    evidence: list[dict]         # [{id, ts, strength}]
    pair: dict
    guarded: bool
    reflector: str
    created: str
    status: str = "proposed"     # proposed | held
    conflict: dict | None = None
    supersedes: str | None = None
    event_id: str | None = None
    quotes: list[dict] = field(default_factory=list)   # [{text, ts, id}] principal's words
    ptype: str = "taste"         # taste | skill | fact | question (LEARNING-LOOP 5.5)
    critic_note: str = ""        # the critic's objection, if it had one
    target: str | None = None    # skill name for a skill patch
    patch: str = ""              # skill patch text, or the question text
    half_life: float | None = None

    def body(self) -> dict:
        d = asdict(self)
        d.pop("event_id")
        d["proposal_id"] = d.pop("id")
        return d

    @classmethod
    def from_event(cls, ev: Mapping) -> "Proposal":
        b = dict(ev["body"])
        b["id"] = b.pop("proposal_id")
        known = {f for f in cls.__dataclass_fields__}
        p = cls(**{k: v for k, v in b.items() if k in known})
        p.event_id = ev["id"]
        return p


def proposal_id(kind: str, evidence_ids: Iterable[str]) -> str:
    return "p-" + hashlib.sha256((kind + "\n" + "\n".join(sorted(evidence_ids))).encode()).hexdigest()[:12]


def _slug(tokens: Iterable[str], fallback: str) -> str:
    words = [t for t in tokens if re.match(r"^[a-z0-9]+$", t)][:6]
    s = "-".join(words) or fallback
    return re.sub(r"-+", "-", s)[:60].strip("-") or fallback


def _redact(text: str) -> str:
    for p in LEAKS:
        text = p.sub("[redacted]", text)
    return text


def _flow(v: Any) -> str:
    s = _redact(normalize(str(v))).replace('"', "'")
    return f'"{s}"'


def _scalar(v: str) -> str:
    s = _redact(normalize(v)).replace('"', "'")
    if s.startswith(("[", "{", "-", "#")):
        s = "'" + s
    return s


def _leak_safe(prefix: str, ident: str, lengths: Sequence[int]) -> str:
    """`prefix + ident`, cut to the longest listed length that the lint's LEAKS patterns accept.
    Random hex ids sometimes contain a long digit run that reads as a phone number."""
    for n in lengths:
        ref = f"{prefix}{ident[:n]}"
        if not any(p.search(ref) for p in LEAKS):
            return ref
    return f"{prefix}{ident[:8]}"


def event_ref(eid: str) -> str:
    """`event:<id>`, shortened only if the hex happens to look like a phone number to the lint."""
    return _leak_safe("event:", eid, (64, 16, 12, 8))


def proposal_ref(pid: str) -> str:
    """A proposal id for node text. Ids are `p-` + 12 hex of a hash, so a run of 11+ digits is
    possible (about 1 in 300); cut it short. `proposal_accept` resolves unique prefixes."""
    return _leak_safe("p-", pid[2:] if pid.startswith("p-") else pid, (12, 10, 8))


def render_new_node(p: Proposal, links: Sequence[tuple[str, str]] = ()) -> str:
    lines = [
        "---",
        f"id: {p.node_id}",
        f"title: {_scalar(p.title)}",
        f"type: {p.type}",
        f"domain: {p.domain}",
        f"summary: {_scalar(clip(p.claim, 190))}",
        f"load_when: {_scalar(clip('Doing work this touches: ' + p.wants, 160))}",
        f"confidence: {p.confidence}",
        f"stability: {'durable' if p.type == 'rule' else 'volatile'}",
        f"valid_from: {min(e['ts'] for e in p.evidence)[:10]}",
        "sources:",
    ]
    lines += _source_lines(p)
    if links:
        lines.append("links:")
        lines += [f"  - {{to: {t}, rel: {r}}}" for t, r in links]
    else:
        lines.append("links: []")
    lines += [f"tags: [exo-consolidate, {p.type}, {p.kind.replace('_', '-')}]", "---", ""]
    return "\n".join(lines) + _render_body(p) + "\n"


def _source_lines(p: Proposal) -> list[str]:
    note = {"correction": "correction", "ruling": "ruling", "rule_of_three": "repeated instruction"}[p.kind]
    return [f"  - {{kind: principal-said, ref: {_flow(event_ref(e['id']))}, note: {_flow(note + ' ' + e['ts'][:10])}}}"
            for e in p.evidence]


def _render_body(p: Proposal, heading: str | None = None) -> str:
    out = []
    if heading:
        out.append(f"### {heading}\n")
    out.append(f"**Claim.** {_redact(p.claim)}\n")
    out.append(f"**Implication.** {_redact(p.implication)}\n")
    quotes = "; ".join(f'"{_redact(clip(q["text"], 160))}" ({q["ts"][:10]}, {event_ref(q["id"])})'
                       for q in p.quotes[:5])
    if quotes:
        out.append(f"**Evidence.** The principal said: {quotes}.\n")
    out.append("**How to apply.** Follow the claim in this kind of work and in nearby cases. "
               "If a case is unclear and hard to undo, ask one short numbered question.")
    if p.supersedes:
        out.append(f"\nSupersedes the earlier belief {proposal_ref(p.supersedes)}.")
    return "\n".join(out)


def render_update(p: Proposal, old_text: str) -> str:
    """Existing node text with this proposal's sources added and a dated section appended."""
    lines = old_text.rstrip("\n").split("\n")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ConsolidateError(f"{p.node_id}: frontmatter not closed")
    fm, body = lines[1:end], lines[end + 1:]
    new_src = _source_lines(p)
    if any(l.startswith("sources:") for l in fm):
        i = next(k for k, l in enumerate(fm) if l.startswith("sources:"))
        if fm[i].strip() in ("sources: []", "sources:[]"):
            fm[i] = "sources:"
        j = i + 1
        while j < len(fm) and (fm[j].startswith((" ", "\t")) or not fm[j].strip()):
            j += 1
        fm[j:j] = new_src
    else:
        fm += ["sources:"] + new_src
    section = _render_body(p, f"{p.title} ({p.created[:10]}, {proposal_ref(p.id)})")
    text = "\n".join(["---", *fm, "---", *body]).rstrip("\n") + "\n\n" + section + "\n"
    return text


def node_text_for(store: PrincipalStore, p: Proposal) -> tuple[str, str | None]:
    """(new text, old text or None) for writing `p` into the store now."""
    path = store.nodes_dir / f"{p.node_id}.md"
    if path.is_file():
        old = path.read_text(encoding="utf-8")
        return render_update(p, old), old
    return render_new_node(p, _links_for(store, p)), None


def _links_for(store: PrincipalStore, p: Proposal) -> list[tuple[str, str]]:
    links = []
    hits = [i for i, _ in store.find(" ".join(t[1:] for t in p.sig), n=3) if i != p.node_id]
    if hits:
        links.append((hits[0], "see-also"))
    return links


def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/nodes/{name}.md", f"b/nodes/{name}.md"))


# --- state rebuilt from the ledger --------------------------------------------

@dataclass
class State:
    watermark: int = 0
    beliefs: dict[str, Belief] = field(default_factory=dict)          # by proposal id
    proposals: dict[str, Proposal] = field(default_factory=dict)
    decided: dict[str, dict] = field(default_factory=dict)            # pid -> {status, event, reason}
    questions: dict[str, dict] = field(default_factory=dict)          # question event id -> body
    answered: set[str] = field(default_factory=set)
    resolved: set[str] = field(default_factory=set)                   # questions already acted on
    seen_evidence: set[str] = field(default_factory=set)


def load_state(ledger: Ledger) -> State:
    st = State()
    for ev in ledger.iter(kind=STATE_KINDS):
        b = ev["body"] or {}
        k = ev["kind"]
        if k == "belief_proposed" and b.get("proposal_id"):
            p = Proposal.from_event(ev)
            st.proposals[p.id] = p
            st.beliefs[p.id] = Belief(p.id, frozenset(p.sig), p.type, p.claim, p.created,
                                      [Evidence(**e) for e in p.evidence], p.status, p.node_id, p.guarded, ev["id"],
                                      domain=p.domain)
            st.seen_evidence.update(e["id"] for e in p.evidence)
        elif k == "belief_accepted" and b.get("proposal_id") in st.beliefs:
            pid = b["proposal_id"]
            st.beliefs[pid].status = "accepted"
            st.beliefs[pid].node_id = b.get("node_id")
            st.decided[pid] = {"status": "accepted", "event": ev["id"], "node_id": b.get("node_id")}
            if b.get("supersedes") in st.beliefs:
                st.beliefs[b["supersedes"]].status = "superseded"
                st.decided.setdefault(b["supersedes"], {"status": "superseded", "event": ev["id"]})
                st.decided[b["supersedes"]]["status"] = "superseded"
        elif k == "note":
            t = b.get("type")
            if t in ("belief_rejected", "belief_released") and b.get("question"):
                st.resolved.add(b["question"])
            if t == "consolidate_run":
                st.watermark = max(st.watermark, int(b.get("upto") or 0))
            elif t == "belief_rejected" and b.get("proposal_id") in st.beliefs:
                st.beliefs[b["proposal_id"]].status = "rejected"
                st.decided[b["proposal_id"]] = {"status": "rejected", "event": ev["id"], "reason": b.get("reason")}
            elif t == "belief_reinforced" and b.get("proposal_id") in st.beliefs:
                st.beliefs[b["proposal_id"]].evidence.append(
                    Evidence(b["evidence"], b["ts"], float(b.get("strength", 0.5))))
                st.seen_evidence.add(b["evidence"])
            elif t == "belief_released" and b.get("proposal_id") in st.beliefs:
                if st.beliefs[b["proposal_id"]].status == "held":
                    st.beliefs[b["proposal_id"]].status = "proposed"
        elif k == "question" and b.get("type") == "belief_conflict":
            st.questions[ev["id"]] = b
        elif k == "answer":
            st.answered.update(r for r in ev["refs"])
    return st


# --- the run ------------------------------------------------------------------

@dataclass
class RunResult:
    read: int = 0
    upto: int = 0
    proposals: list[str] = field(default_factory=list)
    reinforced: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    stale: list[tuple[str, float]] = field(default_factory=list)
    pending_sessions: int = 0
    not_principal: int = 0       # D-040: principal-voice events written by someone else, skipped
    pairs: list[dict] = field(default_factory=list)
    dry_run: bool = False

    def render(self) -> str:
        lines = [f"read {self.read} events up to seq {self.upto}"
                 + (" (dry run, nothing written)" if self.dry_run else "")]
        for label, items in (("proposed", self.proposals), ("reinforced", self.reinforced),
                             ("questions", self.questions), ("released", self.released),
                             ("rejected by answer", self.rejected)):
            if items:
                lines.append(f"{label}: {', '.join(items)}")
        for pid, w in self.stale:
            lines.append(f"stale: {pid} weight {w:.2f}")
        if self.not_principal:
            lines.append(f"skipped {self.not_principal} not written by the principal (agent, peer or tool)")
        return "\n".join(lines)


class _Run:
    def __init__(self, store: PrincipalStore, ledger: Ledger, reflector: Reflector | None, now: str | None,
                 author: str | None, dry_run: bool):
        self.store, self.ledger = store, ledger
        self.reflector = reflector or RuleReflector()
        self.now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.author = author or (ledger.author or DEFAULT_AUTHOR)
        self.dry = dry_run
        self.policy = load_policy(store)
        self.state = load_state(ledger)
        self.res = RunResult(dry_run=dry_run)
        self.pdir = store.root / "proposals"
        doms = {p.name for p in store.nodes_dir.iterdir() if p.is_dir()} if store.nodes_dir.is_dir() else set()
        self.ctx = ReflectContext(sorted(doms | {"mind"}), self.policy, self.now[:10])
        self.thr = float(self.policy["similarity"])
        self.meta: dict[str, Any] = {}    # typed-proposal fields for the next propose() call
        self.principals = provenance.principal_names(store)  # D-040: only these authors speak for the principal

    # -- writes --------------------------------------------------------------
    def append(self, kind: str, body: dict, refs: Iterable[str] = (), session: str | None = None) -> dict | None:
        if self.dry:
            return None
        return self.ledger.append(kind, body, self.author, session=session, refs=refs)

    # -- belief bookkeeping --------------------------------------------------
    def beliefs(self) -> list[Belief]:
        return list(self.state.beliefs.values())

    def reinforce(self, b: Belief, obs: Observation) -> None:
        if obs.event_id in {e.id for e in b.evidence}:
            return
        ev = Evidence(obs.event_id, obs.ts, obs.strength)
        b.evidence.append(ev)
        self.state.seen_evidence.add(obs.event_id)
        self.append("note", {"type": "belief_reinforced", "proposal_id": b.id, "evidence": obs.event_id,
                             "ts": obs.ts, "strength": obs.strength, "text": obs.text},
                    refs=[r for r in (b.event_id, obs.event_id) if r])
        self.res.reinforced.append(b.id)

    def target_node(self, sig: frozenset[str], domain: str) -> str | None:
        toks = {t[1:] for t in sig}
        if not toks:
            return None
        for nid, _ in self.store.find(" ".join(sorted(toks)), n=3):
            n = self.store.nodes[nid]
            cover = taste.content_tokens(" ".join(str(n.get(k) or "") for k in ("title", "summary", "load_when")))
            if len(toks & cover) / len(toks) >= float(self.policy["target_overlap"]):
                return nid
        return None

    def new_node_id(self, domain: str, phrase: str, pid: str) -> str:
        toks = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in taste.FILLER and len(w) > 2]
        base = f"{domain}/{_slug(toks, pid)}"
        nid, n = base, 2
        taken = set(self.store.nodes) | {p.node_id for p in self.state.proposals.values()
                                         if self.state.beliefs[p.id].active and p.action == "create"}
        while nid in taken:
            nid, n = f"{base}-{n}", n + 1
        return nid

    def propose(self, obs: Observation, kind: str, *, supersedes: Belief | None = None,
                hold: dict | None = None) -> Proposal | None:
        salt = self.meta.get("salt")   # typed reviewer output: one event may teach several lessons
        pid = proposal_id(kind, [*obs.evidence, salt] if salt else obs.evidence)
        if pid in self.state.proposals:
            return None
        r = self.reflector.reflect(obs, self.ctx)
        sig = obs.sig
        evidence = [{"id": e, "ts": obs.ts if e == obs.event_id else self._ts(e), "strength": obs.strength}
                    for e in obs.evidence]
        hls = self.policy["half_life_days"]
        hl = hls.get(f"domain:{r.domain}", hls.get(r.type, hls.get("preference"))) if r.type != "rule" \
            else hls.get("rule")
        weight = taste.combine(((e["strength"], taste.age_days(e["ts"], self.now)) for e in evidence), hl)
        node_id = (supersedes.node_id if supersedes and supersedes.node_id else None) \
            or self.target_node(sig, r.domain) or self.new_node_id(r.domain, " ".join(filter(None, (obs.want, obs.avoid))) or obs.text, pid)
        action = "update" if (self.store.nodes_dir / f"{node_id}.md").is_file() else "create"
        chosen = r.claim
        pair = {"id": pid, "context": f"{r.domain}: {clip(obs.text, 200)}",
                "rejected": obs.rejected or (f"Doing {obs.avoid}" if obs.avoid else ""),
                "chosen": chosen, "evidence": [e["id"] for e in evidence], "node": node_id}
        meta, self.meta = self.meta, {}
        guarded = taste.guards(obs.text + " " + r.claim, self.policy["guard_words"]) or bool(meta.get("guarded"))
        p = Proposal(pid, kind, action, node_id, r.claim, r.wants, r.avoid, r.because, r.implication, r.domain,
                     r.type, r.title, taste.label(weight), round(weight, 4), sorted(sig), evidence, pair,
                     guarded, r.reflector, self.now,
                     "held" if hold else "proposed", hold, supersedes.id if supersedes else None,
                     quotes=[{"text": obs.text, "ts": obs.ts, "id": obs.event_id}] if kind != "rule_of_three"
                     else self._quotes(obs.evidence),
                     ptype=meta.get("ptype", "taste"), critic_note=meta.get("critic_note", ""),
                     half_life=hl)
        new_text, old_text = node_text_for(self.store, p)
        refs = list(dict.fromkeys(obs.evidence + ([supersedes.event_id] if supersedes and supersedes.event_id else [])
                                  + ([hold["question"]] if hold and hold.get("question") else [])))
        ev = self.append("belief_proposed", p.body(), refs=refs, session=obs.session)
        p.event_id = ev["id"] if ev else None
        if not self.dry:
            self.pdir.mkdir(exist_ok=True)
            (self.pdir / f"{pid}.md").write_text(new_text, encoding="utf-8")
            if old_text is not None:
                (self.pdir / f"{pid}.diff").write_text(_diff(old_text, new_text, node_id), encoding="utf-8")
            (self.pdir / f"{pid}.json").write_text(json.dumps({**p.body(), "event_id": p.event_id}, indent=1,
                                                              ensure_ascii=False), encoding="utf-8")
        self.state.proposals[pid] = p
        self.state.beliefs[pid] = Belief(pid, frozenset(sig), p.type, p.claim, p.created,
                                         [Evidence(**e) for e in evidence], p.status, node_id, p.guarded, p.event_id,
                                         domain=p.domain)
        self.state.seen_evidence.update(obs.evidence)
        self.res.proposals.append(pid)
        self.res.pairs.append(pair)
        return p

    def _ts(self, eid: str) -> str:
        ev = self.ledger.get(eid)
        return said_at(ev) if ev else self.now

    def _quotes(self, ids: Sequence[str]) -> list[dict]:
        out = []
        for i in ids:
            ev = self.ledger.get(i)
            if ev:
                out.append({"text": clip(_event_text(ev), 200), "ts": said_at(ev), "id": i})
        return out

    # -- handlers ------------------------------------------------------------
    def handle_signal(self, obs: Observation) -> None:
        if obs.event_id in self.state.seen_evidence and not self.meta:
            return
        kind = "correction" if obs.kind == "correction" else "ruling"
        # A lesson the principal rejected teaches nothing again from the same evidence: it is not proposed, does
        # not reinforce another belief and raises no conflict question (#861). The evidence may be split across
        # several rejected beliefs that agree with it (since #770 each agreeing ruling after a rejection is its own
        # proposal), so it is checked against their union. Only the typed taste and fact path gets here with such
        # evidence: any other signal already returned above, as its event is in seen_evidence. A skill lesson goes
        # to _propose_skill and is not checked.
        ids = set(obs.evidence)
        ruled: set[str] = set()
        for x in self.beliefs():
            got = {e.id for e in x.evidence}
            if x.status == "rejected" and ids & got and taste.relation(obs.sig, x.sig, self.thr) == "agree":
                ruled |= got
        if ids and ids <= ruled:
            return
        # Only active beliefs are matched: a rejected belief that scores higher must not hide an active one (#861).
        b, rel = taste.best_match(obs.sig, self.beliefs(), self.thr)
        if b is not None and rel == "agree":
            self.reinforce(b, obs)
            return
        if b is not None and rel == "conflict":
            self.conflict(b, obs, kind)
            return
        self.propose(obs, kind)

    def conflict(self, older: Belief, obs: Observation, kind: str) -> None:
        decision = taste.resolve_conflict(older, self.policy["conflict_rule"])
        if decision == "newer":
            self.propose(obs, kind, supersedes=older)
            return
        pid = proposal_id(kind, obs.evidence)
        if pid in self.state.proposals:
            return
        r = self.reflector.reflect(obs, self.ctx)
        text = (f"Two beliefs conflict. 1) Keep: {older.claim} 2) Replace with: {r.claim} "
                f"Default 1 (the older one guards something irreversible or public)."
                if older.guarded else
                f"Two beliefs conflict. 1) Keep: {older.claim} 2) Replace with: {r.claim} Default 2 (newer wins).")
        body = {"type": "belief_conflict", "text": text, "open": True, "older": older.id, "newer": pid,
                "options": ["keep older", "replace with newer"], "default": 1 if older.guarded else 2,
                "rule": self.policy["conflict_rule"]}
        q = self.append("question", body, refs=[r for r in (older.event_id, obs.event_id) if r], session=obs.session)
        qid = q["id"] if q else None
        if qid:
            self.state.questions[qid] = body
            self.res.questions.append(qid)
        self.propose(obs, kind, supersedes=older, hold={"with": older.id, "question": qid,
                                                        "rule": self.policy["conflict_rule"]})

    def handle_answer(self, ev: Mapping) -> None:
        qid = next((r for r in ev["refs"] if r in self.state.questions), None)
        if qid is None or qid in self.state.resolved:
            return
        q = self.state.questions[qid]
        b = ev["body"] or {}
        choice = b.get("choice")
        if choice is None:
            m = re.match(r"\D*([12])\b", str(b.get("text") or ""))
            choice = int(m.group(1)) if m else None
        if choice is None:
            return
        pid = q.get("newer")
        belief = self.state.beliefs.get(pid)
        if belief is None or belief.status != "held":
            return
        self.state.resolved.add(qid)
        if int(choice) == 2:
            belief.status = "proposed"
            self.append("note", {"type": "belief_released", "proposal_id": pid, "question": qid},
                        refs=[r for r in (belief.event_id, ev["id"]) if r])
            self.res.released.append(pid)
        else:
            belief.status = "rejected"
            self.append("note", {"type": "belief_rejected", "proposal_id": pid,
                                 "reason": "principal kept the older belief", "question": qid},
                        refs=[r for r in (belief.event_id, ev["id"]) if r])
            self.state.decided[pid] = {"status": "rejected", "reason": "principal kept the older belief"}
            self.res.rejected.append(pid)
            _archive(self.pdir, pid, self.dry)

    # -- rule of three -------------------------------------------------------
    def load_clusters(self) -> list[dict]:
        path = self.pdir / "state.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("upto", -1)) == self.state.watermark:
                return data.get("clusters", [])
        except (OSError, ValueError):
            pass
        clusters: list[dict] = []
        for ev in self.ledger.iter(kind="utterance"):
            if ev["seq"] > self.state.watermark:
                break
            if ev["author"] not in self.principals:
                continue  # D-040, as in run(): only the principal's own words fill a cluster
            for o in instructions_in(ev):
                self._add_to_cluster(clusters, o)
        return clusters

    def save_clusters(self, clusters: list[dict], upto: int) -> None:
        if self.dry:
            return
        self.pdir.mkdir(exist_ok=True)
        (self.pdir / "state.json").write_text(json.dumps({"upto": upto, "clusters": clusters}, indent=1,
                                                         ensure_ascii=False), encoding="utf-8")

    def _add_to_cluster(self, clusters: list[dict], o: Observation) -> dict:
        for c in clusters:
            if taste.relation(o.sig, c["sig"], self.thr) == "agree":
                if o.event_id not in [m["id"] for m in c["members"]]:
                    c["members"].append({"id": o.event_id, "ts": o.ts, "text": o.text})
                return c
        c = {"sig": sorted(o.sig), "text": o.text, "want": o.want, "avoid": o.avoid, "reason": o.reason,
             "members": [{"id": o.event_id, "ts": o.ts, "text": o.text}]}
        clusters.append(c)
        return c

    def encoded(self, sig: Iterable[str]) -> bool:
        b, rel = taste.best_match(sig, self.beliefs(), self.thr, include_inactive=True)
        if b is not None and rel == "agree":
            return True
        for n in self.store.nodes.values():
            cover = taste.content_tokens(" ".join(str(n.get(k) or "") for k in ("title", "summary")))
            toks = {t[1:] for t in sig}
            if toks and len(toks & cover) / len(toks) >= 0.8:
                return True
        return False

    def handle_instruction(self, clusters: list[dict], o: Observation) -> None:
        c = self._add_to_cluster(clusters, o)
        if len(c["members"]) < int(self.policy["rule_of_three"]) or c.get("proposed"):
            return
        if self.encoded(c["sig"]):
            c["proposed"] = "encoded"
            return
        ids = [m["id"] for m in c["members"]]
        last = c["members"][-1]
        rep = Observation(last["id"], o.seq, last["ts"], "instruction", "utterance", c["text"], c.get("want"),
                          c.get("avoid"), c.get("reason"), None, (), STRENGTH["instruction"], o.session, ids)
        rep_sig = frozenset(c["sig"])
        b, rel = taste.best_match(rep_sig, self.beliefs(), self.thr)
        if b is not None and rel == "conflict":
            self.conflict(b, rep, "rule_of_three")
            p = self.state.proposals.get(proposal_id("rule_of_three", ids))
        else:
            p = self.propose(rep, "rule_of_three")
        c["proposed"] = p.id if p else "skipped"

    # -- main ----------------------------------------------------------------
    def run(self) -> RunResult:
        clusters = self.load_clusters()
        upto = self.state.watermark
        for ev in self.ledger.iter(kind=INPUT_KINDS, since=self.state.watermark):
            upto = ev["seq"]
            k, b = ev["kind"], ev["body"] or {}
            if k == "note":
                if b.get("type") == "consolidate_pending":
                    self.res.pending_sessions += 1
                    self.res.read += 1
                continue
            if ev["author"] == self.author and k != "answer":
                continue
            if ev["author"] not in self.principals:
                # D-040: a ruling, verdict, answer or utterance the agent, a peer or a tool wrote is not the
                # principal's voice, whatever its text says. It is read (the watermark moves) but never proposed.
                self.res.read += 1
                self.res.not_principal += 1
                continue
            self.res.read += 1
            if k in ("verdict", "ruling"):
                obs = observe_event(ev, self.ledger)
                if obs is not None:
                    self.handle_signal(obs)
            elif k == "utterance":
                for o in instructions_in(ev):
                    self.handle_instruction(clusters, o)
            elif k == "answer":
                self.handle_answer(ev)
        # Only a run that read something moves the watermark, so idle runs append nothing.
        if self.res.read == 0:
            upto = self.state.watermark
        self.res.upto = upto
        if upto > self.state.watermark:
            self.append("note", {"type": "consolidate_run", "upto": upto, "read": self.res.read,
                                 "proposals": self.res.proposals, "questions": self.res.questions,
                                 "reflector": self.reflector.name})
        self.save_clusters(clusters, upto)
        stale = float(self.policy["stale_below"])
        for b in self.beliefs():
            if b.status == "accepted" and b.type == "preference":
                w = b.weight(self.now, self.policy["half_life_days"])
                if w < stale:
                    self.res.stale.append((b.id, round(w, 4)))
        return self.res


def consolidate_run(store: PrincipalStore, ledger: Ledger, *, reflector: Reflector | None = None,
                    now: str | None = None, author: str | None = None, dry_run: bool = False) -> RunResult:
    """Read events after the watermark and write proposals, reinforcements and conflict questions."""
    return _Run(store, ledger, reflector, now, author, dry_run).run()


# --- review -------------------------------------------------------------------

@dataclass
class ProposalView:
    id: str
    status: str
    kind: str
    action: str
    node_id: str
    claim: str
    confidence: str
    weight: float
    evidence: list[str]
    question: str | None = None


def _status(st: State, pid: str) -> str:
    if pid in st.decided:
        return st.decided[pid]["status"]
    b = st.beliefs[pid]
    if b.status == "held":
        q = (st.proposals[pid].conflict or {}).get("question")
        return "held" if q and q not in st.answered else ("proposed" if q else "held")
    return b.status


def proposals_list(store: PrincipalStore, ledger: Ledger, *, include_decided: bool = False,
                   now: str | None = None) -> list[ProposalView]:
    st = load_state(ledger)
    pol = load_policy(store)
    now = now or datetime.now(timezone.utc).isoformat()
    out = []
    for pid, p in st.proposals.items():
        s = _status(st, pid)
        if not include_decided and s not in ("proposed", "held"):
            continue
        b = st.beliefs[pid]
        w = b.weight(now, pol["half_life_days"])
        out.append(ProposalView(pid, s, p.kind, p.action, p.node_id, p.claim, taste.label(w), round(w, 4),
                                [e.id for e in b.evidence], (p.conflict or {}).get("question")))
    return out


def _find(st: State, pid: str) -> Proposal:
    if pid in st.proposals:
        return st.proposals[pid]
    hits = [k for k in st.proposals if k.startswith(pid) or k == "p-" + pid]
    if len(hits) == 1:
        return st.proposals[hits[0]]
    raise ConsolidateError(f"no proposal {pid}" if not hits else f"ambiguous proposal id {pid}")


def _archive(pdir: Path, pid: str, dry: bool = False) -> None:
    if dry:
        return
    arch = pdir / "archive"
    for ext in (".md", ".diff", ".json"):
        f = pdir / f"{pid}{ext}"
        if f.exists():
            arch.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), arch / f.name)


def proposal_accept(store: PrincipalStore, ledger: Ledger, pid: str, *, author: str | None = None,
                    now: str | None = None) -> dict:
    """Write the proposal into its node (create or append), lint it, then log `belief_accepted`.
    The node file is restored if the edit would add lint errors."""
    st = load_state(ledger)
    p = _find(st, pid)
    status = _status(st, p.id)
    if status not in ("proposed", "held"):
        raise ConsolidateError(f"proposal {p.id} is already {status}")
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    p.created = p.created or now
    if p.ptype == "question":
        raise ConsolidateError(f"{p.id} is a question: answer it, there is nothing to accept")
    if p.ptype == "skill":
        return _accept_skill(store, ledger, st, p, status, author)
    path = store.nodes_dir / f"{p.node_id}.md"
    store.refresh()  # a long-lived store (the learner's) may predate nodes written since: lint and link against disk
    before = store.lint()
    new_text, old_text = node_text_for(store, p)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    store.reload()
    after = store.lint()
    added = [e for e in after.errors if e not in before.errors]
    if added or p.node_id not in store.nodes:
        if old_text is None:
            path.unlink()
        else:
            path.write_text(old_text, encoding="utf-8")
        store.reload()
        raise ConsolidateError(f"accepting {p.id} would break lint: {'; '.join(added) or 'node did not parse'}")
    store.index()
    body = {"proposal_id": p.id, "node_id": p.node_id, "action": "update" if old_text is not None else "create",
            "claim": p.claim, "type": p.type, "domain": p.domain, "confidence": p.confidence}
    if p.supersedes:
        body["supersedes"] = p.supersedes
    if status == "held":
        body["conflict_resolved_by"] = "direct accept"
    refs = [r for r in (st.beliefs[p.id].event_id,) if r]
    ev = ledger.append("belief_accepted", body, author or ledger.author or "principal",
                       refs=refs)
    pairs = store.root / PAIRS_FILE
    pairs.parent.mkdir(parents=True, exist_ok=True)
    with pairs.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**p.pair, "node": p.node_id, "accepted": ev["id"]}, ensure_ascii=False) + "\n")
    _archive(store.root / "proposals", p.id)
    _save_undo(store.root, p.id, path, old_text, new_text)
    return {"proposal_id": p.id, "node_id": p.node_id, "event": ev["id"], "action": body["action"]}


def proposal_reject(store: PrincipalStore, ledger: Ledger, pid: str, reason: str, *,
                    author: str | None = None) -> dict:
    """Record why a proposal was rejected. The node is not touched."""
    if not (reason or "").strip():
        raise ConsolidateError("a rejection needs a reason")
    st = load_state(ledger)
    p = _find(st, pid)
    status = _status(st, p.id)
    if status not in ("proposed", "held"):
        raise ConsolidateError(f"proposal {p.id} is already {status}")
    refs = [r for r in (st.beliefs[p.id].event_id,) if r]
    ev = ledger.append("note", {"type": "belief_rejected", "proposal_id": p.id, "reason": reason.strip()},
                       author or ledger.author or "principal", refs=refs)
    _archive(store.root / "proposals", p.id)
    return {"proposal_id": p.id, "event": ev["id"]}


# --- typed proposals (LEARNING-LOOP 5.5) ---------------------------------------

PTYPES = ("taste", "skill", "fact", "question")


class FixedReflector:
    """Returns the reflection a reviewer already wrote (a dict), so one proposal path serves rules,
    models and the fast loop. Missing fields come from the rule reflector."""
    name = "reviewer"

    def __init__(self, item: Mapping[str, Any], fallback: Reflector | None = None):
        self.item = item
        self.fallback = fallback or RuleReflector()

    def reflect(self, obs: Observation, ctx: ReflectContext) -> Reflection:
        base = self.fallback.reflect(obs, ctx)
        it = self.item
        wants = _tidy(str(it.get("wants") or "")) or base.wants
        avoid = _tidy(str(it["avoid"])) if it.get("avoid") not in (None, "", "null") else base.avoid
        because = _tidy(str(it.get("because") or "")) or base.because
        claim = str(it.get("claim") or "").strip()
        if not claim:
            claim = f"The principal wants {wants}" + (f", not {avoid}" if avoid else "") + f", because {because}."
        domain = it.get("domain") if it.get("domain") in ctx.domains else base.domain
        typ = "fact" if it.get("type") == "fact" else ("rule" if it.get("rule") else base.type)
        return Reflection(claim, wants, avoid, because, str(it.get("implication") or base.implication), domain, typ,
                          clip(str(it.get("title") or base.title), 70), self.name)


def _evidence_obs(ledger: Ledger, ids: Sequence[str], item: Mapping[str, Any], now: str) -> Observation | None:
    rows = [ev for ev in (ledger.get(i) for i in ids) if ev]
    if not rows:
        return None
    last = max(rows, key=lambda e: e["seq"])
    strength = max(STRENGTH.get((e["body"] or {}).get("verdict") or e["kind"], 0.6) for e in rows)
    quote = next((_event_text(e) for e in rows if _event_text(e)), "") or str(item.get("claim") or "")
    return Observation(last["id"], last["seq"], last["ts"], "correction", "review", clip(quote),
                       _tidy(str(item.get("wants") or "")) or None, _tidy(str(item.get("avoid") or "")) or None,
                       _tidy(str(item.get("because") or "")) or None, item.get("rejected"), (), min(strength, 1.0),
                       last.get("session"), [e["id"] for e in rows])


def _weight_of(run: "_Run", evidence: list[dict], domain: str, typ: str) -> float:
    hls = run.policy["half_life_days"]
    hl = hls.get("rule") if typ == "rule" else hls.get(f"domain:{domain}", hls.get("preference"))
    return taste.combine(((e["strength"], taste.age_days(e["ts"], run.now)) for e in evidence), hl)


def _propose_skill(run: "_Run", obs: Observation, item: Mapping[str, Any], critic: str) -> Proposal | None:
    name = re.sub(r"[^a-z0-9-]+", "-", str(item.get("skill") or item.get("target") or "").lower()).strip("-")
    patch = str(item.get("patch") or "").strip()
    if not name or not patch:
        return None
    pid = proposal_id("skill", [*obs.evidence, name, patch])
    if pid in run.state.proposals:
        return None
    path = run.store.root / "skills" / name / "SKILL.md"
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = skill_text_after(old, name, patch, run.now[:10], pid, str(item.get("claim") or ""))
    refl = FixedReflector(item).reflect(obs, run.ctx)
    evidence = [{"id": e, "ts": obs.ts if e == obs.event_id else run._ts(e), "strength": obs.strength}
                for e in obs.evidence]
    weight = _weight_of(run, evidence, refl.domain, refl.type)
    p = Proposal(pid, "correction", "update" if old else "create", f"skill:{name}", refl.claim, refl.wants,
                 refl.avoid, refl.because, refl.implication, refl.domain, "procedure", refl.title,
                 taste.label(weight), round(weight, 4), sorted(obs.sig), evidence,
                 {"id": pid, "context": clip(obs.text, 200), "rejected": obs.rejected or "", "chosen": patch,
                  "evidence": obs.evidence, "node": f"skill:{name}"},
                 bool(item.get("guarded")) or taste.guards(patch + " " + refl.claim, run.policy["guard_words"]),
                 refl.reflector, run.now, "proposed", None, None,
                 quotes=[{"text": obs.text, "ts": obs.ts, "id": obs.event_id}], ptype="skill",
                 critic_note=critic, target=name, patch=patch)
    ev = run.append("belief_proposed", p.body(), refs=obs.evidence, session=obs.session)
    p.event_id = ev["id"] if ev else None
    if not run.dry:
        run.pdir.mkdir(exist_ok=True)
        (run.pdir / f"{pid}.md").write_text(new, encoding="utf-8")
        (run.pdir / f"{pid}.diff").write_text(_diff(old, new, f"skills/{name}/SKILL").replace("nodes/", ""),
                                              encoding="utf-8")
        (run.pdir / f"{pid}.json").write_text(json.dumps({**p.body(), "event_id": p.event_id}, indent=1,
                                                         ensure_ascii=False), encoding="utf-8")
    run.state.proposals[pid] = p
    run.state.beliefs[pid] = Belief(pid, frozenset(p.sig), p.type, p.claim, p.created,
                                    [Evidence(**e) for e in evidence], p.status, p.node_id, p.guarded, p.event_id,
                                    domain=p.domain)
    run.state.seen_evidence.update(obs.evidence)
    run.res.proposals.append(pid)
    return p


def skill_text_after(old: str, name: str, patch: str, date: str, pid: str, claim: str = "") -> str:
    """A skill file with a dated lesson section added (or a new skill file when there was none)."""
    section = f"## Lesson {date} ({pid})\n\n{patch.strip()}\n"
    if not old.strip():
        desc = clip(claim or patch, 120).replace(":", " -")
        return f"---\nname: {name}\ndescription: {desc}\n---\n\n{section}"
    return old.rstrip("\n") + "\n\n" + section


def _propose_question(run: "_Run", obs: Observation | None, item: Mapping[str, Any], ids: Sequence[str]) -> Proposal | None:
    text = str(item.get("question") or item.get("claim") or "").strip()
    if not text:
        return None
    from .ask import QuestionQueue
    pid = proposal_id("question", list(ids) or [text])
    if pid in run.state.proposals:
        return None
    q = None if run.dry else QuestionQueue(run.store.root, ledger=run.ledger).propose(
        text, node=item.get("target"), rationale=str(item.get("implication") or ""),
        impact=float(item.get("impact", 0.6)), uncertainty=float(item.get("uncertainty", 0.6)),
        default=item.get("default"), source="learn", domain=item.get("domain"))
    domain = item.get("domain") if item.get("domain") in run.ctx.domains else default_domain(run.ctx.domains)
    p = Proposal(pid, "correction", "ask", f"question:{q.id if q else pid}", text, text, None, "it is unclear",
                 str(item.get("implication") or ""), domain, "question", clip(text, 70), "low", 0.0, [], [], {},
                 False, "reviewer", run.now, "held", {"queue": q.id if q else None}, None, ptype="question",
                 patch=text)
    ev = run.append("belief_proposed", p.body(), refs=list(ids))
    p.event_id = ev["id"] if ev else None
    if not run.dry:
        run.pdir.mkdir(exist_ok=True)
        (run.pdir / f"{pid}.json").write_text(json.dumps({**p.body(), "event_id": p.event_id}, indent=1,
                                                         ensure_ascii=False), encoding="utf-8")
    run.state.proposals[pid] = p
    run.state.beliefs[pid] = Belief(pid, frozenset(), "question", text, p.created, [], "held", p.node_id, False,
                                    p.event_id, domain=domain)
    run.res.proposals.append(pid)
    return p


def propose_typed(store: PrincipalStore, ledger: Ledger, items: Iterable[Mapping[str, Any]], *,
                  author: str | None = None, now: str | None = None) -> list[dict]:
    """Write reviewer output as typed proposals: `belief_proposed` events plus drafts in `<store>/proposals/`.

    Each item is a dict: `type` (taste, skill, fact, question), `claim`, optional `wants`, `avoid`, `because`,
    `implication`, `domain`, `evidence` (ledger event ids; at least one for all but questions), `guarded`,
    `critic_note`, and `skill` plus `patch` for a skill. Taste and fact go through the same conflict and
    reinforcement rules as consolidation. Returns one result per item: {"id", "type", "status"} where status
    is proposed, held, reinforced or skipped."""
    run = _Run(store, ledger, None, now, author, False)
    out: list[dict] = []
    for item in items:
        ptype = str(item.get("type") or "taste")
        ids = [i for i in (item.get("evidence") or []) if ledger.get(i)]
        res: dict[str, Any] = {"type": ptype, "id": None, "status": "skipped"}
        out.append(res)
        if ptype not in PTYPES or (ptype != "question" and not ids):
            continue
        before = len(run.res.proposals)
        if ptype == "question":
            p = _propose_question(run, None, item, ids)
        else:
            obs = _evidence_obs(ledger, ids, item, run.now)
            if obs is None:
                continue
            critic = str(item.get("critic_note") or "")
            if ptype == "skill":
                p = _propose_skill(run, obs, item, critic)
            else:
                run.reflector = FixedReflector(item)
                run.meta = {"ptype": ptype, "critic_note": critic, "guarded": bool(item.get("guarded")),
                            "salt": ptype + "|" + str(item.get("claim") or item.get("wants") or "")}
                reinforced = len(run.res.reinforced)
                run.handle_signal(obs)
                run.meta = {}
                if len(run.res.reinforced) > reinforced:
                    res["status"] = "reinforced"
                    res["id"] = run.res.reinforced[-1]
                    continue
                p = run.state.proposals.get(run.res.proposals[-1]) if len(run.res.proposals) > before else None
        if p is not None:
            res["id"], res["status"] = p.id, p.status
    return out


# --- accept a skill patch, undo records, revert --------------------------------

def _accept_skill(store: PrincipalStore, ledger: Ledger, st: State, p: Proposal, status: str,
                  author: str | None) -> dict:
    path = store.root / "skills" / str(p.target) / "SKILL.md"
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    new = skill_text_after(old or "", str(p.target), p.patch, (p.created or "")[:10], p.id, p.claim)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    body = {"proposal_id": p.id, "node_id": p.node_id, "action": "update" if old is not None else "create",
            "claim": p.claim, "type": p.type, "domain": p.domain, "confidence": p.confidence, "ptype": "skill"}
    if status == "held":
        body["conflict_resolved_by"] = "direct accept"
    refs = [r for r in (st.beliefs[p.id].event_id,) if r]
    ev = ledger.append("belief_accepted", body, author or ledger.author
                       or "principal", refs=refs)
    _archive(store.root / "proposals", p.id)
    _save_undo(store.root, p.id, path, old, new)
    commit = _git_commit(store.root, path, f"skill {p.target}: lesson {p.id}")
    return {"proposal_id": p.id, "node_id": p.node_id, "event": ev["id"], "action": body["action"],
            "commit": commit}


def _save_undo(root: Path, pid: str, path: Path, before: str | None, after: str) -> None:
    arch = root / "proposals" / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / f"{pid}.undo.json").write_text(
        json.dumps({"path": str(path.relative_to(root)), "before": before, "after": after}, ensure_ascii=False),
        encoding="utf-8")


def _git_commit(root: Path, path: Path, message: str) -> str | None:
    """One commit per promoted skill patch when the store is a git repo. Best effort; never raises."""
    import subprocess
    if not (root / ".git").exists():
        return None
    try:
        ident = ["-c", "user.name=exo-learn", "-c", "user.email=exo-learn@localhost", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "-C", str(root), "add", str(path)], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(root), *ident, "commit", "-m", message, "--", str(path)], check=True,
                       capture_output=True, timeout=30)
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True,
                             text=True, timeout=30)
        return out.stdout.strip()
    except Exception:
        return None


def _strip_section(text: str, pid: str) -> str:
    """Remove the dated section a proposal appended (a heading that names its id) from `text`."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("#") and f"{pid})" in l), None)
    if start is None:
        return text
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("#") and len(lines[j]) - len(lines[j].lstrip("#")) <= level:
            end = j
            break
    return "\n".join(lines[:start] + lines[end:]).rstrip("\n") + "\n"


def proposal_revert(store: PrincipalStore, ledger: Ledger, pid: str, reason: str, *, author: str | None = None,
                    now: str | None = None, follow_up: bool = True) -> dict:
    """Undo an accepted lesson by adding events, never by deleting any (LEARNING-LOOP 5.7).

    The node or skill file goes back to what it was (or loses the section this lesson added if the file
    changed since). A `belief_rejected` note with the reason follows. If the reason implies an opposite
    lesson, it is proposed as a new correction."""
    if not (reason or "").strip():
        raise ConsolidateError("a revert needs a reason")
    st = load_state(ledger)
    p = _find(st, pid)
    status = _status(st, p.id)
    if status != "accepted":
        raise ConsolidateError(f"proposal {p.id} is {status}; only an accepted lesson can be reverted")
    undo_path = store.root / "proposals" / "archive" / f"{p.id}.undo.json"
    file_state = "no undo record"
    if undo_path.is_file():
        undo = json.loads(undo_path.read_text(encoding="utf-8"))
        target = store.root / undo["path"]
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current == undo["after"]:
            if undo["before"] is None:
                target.unlink()
            else:
                target.write_text(undo["before"], encoding="utf-8")
            file_state = "restored"
        elif current is not None:
            target.write_text(_strip_section(current, p.id), encoding="utf-8")
            file_state = "section removed"
        if p.ptype == "skill":
            _git_commit(store.root, target, f"revert skill {p.target}: {p.id}")
        else:
            store.reload()
            store.index()
    refs = [r for r in (st.beliefs[p.id].event_id, st.decided.get(p.id, {}).get("event")) if r]
    ev = ledger.append("note", {"type": "belief_rejected", "proposal_id": p.id, "reason": reason.strip(),
                                "reverted": True, "file": file_state},
                       author or ledger.author or "principal", refs=refs)
    res = {"proposal_id": p.id, "event": ev["id"], "file": file_state, "follow_up": None}
    if follow_up:
        want, avoid, why = parse_instruction(reason)
        if want:
            obs = Observation(ev["id"], ev["seq"], ev["ts"], "correction", "revert", clip(reason), want, avoid, why,
                              None, (), STRENGTH["reject"], None, [ev["id"]])
            rel = taste.relation(obs.sig, p.sig, float(load_policy(store)["similarity"]))
            if rel != "agree":
                run = _Run(store, ledger, None, now, author, False)
                q = run.propose(obs, "correction")
                res["follow_up"] = q.id if q else None
    return res


# --- CLI ----------------------------------------------------------------------

def register(sp, common) -> None:
    """Add `exo consolidate` and `exo proposals` to exo.cli's subparsers."""
    p = sp.add_parser("consolidate", parents=[common], help="turn new ledger events into belief proposals")
    p.add_argument("--as", dest="seat", default=DEFAULT_AUTHOR, help=f"signing seat (default: {DEFAULT_AUTHOR})")
    p.add_argument("--key", help="signing key path (default: $EXO_HOME/keys/<seat>.key)")
    p.add_argument("--dry-run", action="store_true", help="report without writing")
    p = sp.add_parser("proposals", parents=[common], help="review belief proposals")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show", "accept", "reject", "revert"])
    p.add_argument("id", nargs="?")
    p.add_argument("--reason", help="why (required for reject and revert)")
    p.add_argument("--all", action="store_true", help="list decided proposals too")
    p.add_argument("--as", dest="seat", default="principal", help="signing seat for accept/reject")
    p.add_argument("--key", help="signing key path (default: $EXO_HOME/keys/<seat>.key)")


def _open_ledger(a, store: PrincipalStore, write: bool) -> Ledger:
    from .ledger_authority.factory import configured, open_writable_ledger
    from .store import exo_home
    if not write:
        return Ledger(store.ledger_path)
    kp = Path(a.key) if getattr(a, "key", None) else exo_home() / "keys" / f"{a.seat}.key"
    if not configured() and not kp.exists():
        sys.exit(f"no key at {kp}; run: exo keygen {a.seat}")
    return open_writable_ledger(store.ledger_path, a.seat, kp)


def cmd_consolidate(a, store: PrincipalStore) -> int:
    with _open_ledger(a, store, write=not a.dry_run) as led:
        print(consolidate_run(store, led, dry_run=a.dry_run).render())
    return 0


def cmd_proposals(a, store: PrincipalStore) -> int:
    write = a.action in ("accept", "reject", "revert")
    with _open_ledger(a, store, write) as led:
        try:
            if a.action == "list":
                for v in proposals_list(store, led, include_decided=a.all):
                    print(f"{v.id}  {v.status:<9} {v.action:<6} {v.node_id:<34} {v.confidence:<6} {v.claim[:90]}")
            elif not a.id:
                sys.exit(f"exo proposals {a.action} needs an id")
            elif a.action == "show":
                p = _find(load_state(led), a.id)
                print(json.dumps(p.body(), indent=1, ensure_ascii=False))
                diff = store.root / "proposals" / f"{p.id}.diff"
                if diff.exists():
                    print(diff.read_text(encoding="utf-8"))
            elif a.action == "accept":
                r = proposal_accept(store, led, a.id)
                print(f"accepted {r['proposal_id']}: {r['action']} nodes/{r['node_id']}.md")
            elif a.action == "revert":
                if not a.reason:
                    sys.exit("exo proposals revert needs --reason")
                r = proposal_revert(store, led, a.id, a.reason)
                print(f"reverted {r['proposal_id']} ({r['file']})")
            else:
                if not a.reason:
                    sys.exit("exo proposals reject needs --reason")
                r = proposal_reject(store, led, a.id, a.reason)
                print(f"rejected {r['proposal_id']}")
        except ConsolidateError as e:
            sys.exit(str(e))
    return 0
