"""Principal signal capture: pure heuristics over chat messages.

Finds three kinds of signal in OpenAI-style messages (``{"role", "content"}``):

    ruling      a standing instruction from the principal ("from now on ...", numbered rules,
                "always" / "never", caps for hard rules)
    correction  the principal correcting the agent's last output ("no, ...", "don't ...",
                "instead ...", "again", "I said ...", caps)
    decision    an open decision: a question the agent put to the principal, or something the
                principal parked ("decide later", "TBD")

Everything here is pure: no I/O, no ledger, no model calls. Plugins (exo-memory, exo-context)
call ``extract_signals`` and turn the result into ledger events with ``to_event``. Weights are
generic defaults; a principal's tuning belongs in their store, not here.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

RULING, CORRECTION, DECISION = "ruling", "correction", "decision"
MAX_TEXT = 500

# --- text helpers -------------------------------------------------------------


def content_text(content: Any) -> str:
    """Flatten OpenAI-style content (str, list of parts, None) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, Mapping):
                if part.get("type") in ("image", "image_url", "input_image"):
                    out.append("[image]")
                elif "text" in part:
                    out.append(str(part.get("text") or ""))
        return "".join(out)
    return str(content)


TRIVIAL_RE = re.compile(
    r"^(yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|yeah|nah|hi|hey|hello|yo|"
    r"continue|go ahead|do it|proceed|got it|cool|nice|great|done|next|lgtm|k)[\s\W]*$",
    re.IGNORECASE,
)


def is_trivial(text: str | None) -> bool:
    """True for empty text, slash commands and bare acknowledgements."""
    s = (text or "").strip()
    return not s or s.startswith("/") or bool(TRIVIAL_RE.match(s))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clip(text: str, limit: int = MAX_TEXT) -> str:
    text = normalize(text)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def sentences(text: str) -> list[str]:
    """Split on newlines, then on sentence ends. Keeps numbered list items whole."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if NUMBERED_RE.match(line):
            out.append(line)
            continue
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", line) if s.strip()]
    return out


NUMBERED_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\(?(\d{1,2})[.):]|[Rr]uling\s*#?\s*(\d{1,3})\s*[:.)-])\s+(.+)$")


def numbered_items(text: str) -> list[str]:
    """Items of a numbered list ("1. x", "2) y", "(3) z", "Ruling 4: w")."""
    items = []
    for line in text.splitlines():
        m = NUMBERED_RE.match(line)
        if m:
            items.append(m.group(3).strip())
    return items


# Common acronyms are not emphasis.
ACRONYMS = frozenset("""
API APIS CI CD CLI CPU GPU CSS DNS HTML HTTP HTTPS ID IDS JSON LLM MCP OK PR PRS QA SDK SQL SSH TUI UI
URL URLS UX VM YAML AI ML OS PDF CSV IP TLS DB TODO FYI ASAP UTC GMT EOD AM PM US UK EU README
""".split())
DIRECTIVE_CAPS = frozenset("NEVER ALWAYS MUST NOT DONT DON'T STOP NO ONLY IMPORTANT NOW EVERY ANY".split())


def caps_emphasis(text: str) -> list[str]:
    """Shouted words: a run of two or more all-caps words, or one directive word in caps."""
    words = re.findall(r"[A-Za-z][A-Za-z']*", text)
    hits, run = [], []
    for w in words + [""]:
        up = len(w) >= 2 and w.isupper() and w.replace("'", "") not in ACRONYMS
        if up:
            run.append(w)
            continue
        if len(run) >= 2:
            hits.append(" ".join(run))
        elif len(run) == 1 and run[0] in DIRECTIVE_CAPS:
            hits.append(run[0])
        run = []
    return hits


# --- cue tables ---------------------------------------------------------------
# (name, pattern, weight). A sentence scores the sum of its matched weights.

CORRECTION_CUES: tuple[tuple[str, re.Pattern, float], ...] = tuple(
    (n, re.compile(p, re.IGNORECASE), w) for n, p, w in (
        ("no", r"^\W*(no|nope|nah|wrong)\b", 1.0),
        ("dont", r"\b(don'?t|do not|dont|didn'?t i)\b", 1.0),
        ("instead", r"\binstead\b", 1.0),
        ("again", r"\bagain\b", 0.5),
        ("stop", r"\bstop\b", 1.0),
        ("said", r"\b(i (already )?(said|told you|asked( for)?)|as i said)\b", 1.0),
        ("not-but", r"\bnot\b[^.?!]{1,60}\bbut\b", 0.75),
        ("should-have", r"\b(should(n'?t)? have|should'?ve|supposed to)\b", 1.0),
        ("why-did", r"\bwhy (did|would) you\b", 1.0),
        ("not-what", r"\b(that'?s|this is) not (what|it|right)\b", 1.0),
        ("undo", r"\b(undo|revert|roll ?back|redo)\b", 0.75),
        ("too", r"\btoo (long|short|verbose|much|many|formal|wordy)\b", 0.75),
        ("label", r"^\W*(important\s+)?corrections?\s*[:\-]", 1.5),
        ("actually", r"^\W*actually\b\s*[,:]?\s*\S", 1.0),
        ("not-x-y", r"^\W*not\s+(?!(?:sure|yet|now|bad|really|too|much|at all|only|just|quite|exactly)\b)"
                    r"[^,.;:!?]{1,60},\s*\S", 1.0),
    ))

# Shouted NOT is case sensitive: "use Y, NOT X" is a correction, "not" in running prose is not.
CORRECTION_CUES += (("caps-not", re.compile(r"\bNOT\b"), 1.0),)

RULING_CUES: tuple[tuple[str, re.Pattern, float], ...] = tuple(
    (n, re.compile(p, re.IGNORECASE), w) for n, p, w in (
        ("from-now-on", r"\b(from now on|going forward|henceforth|in future|in the future|from here on)\b", 1.5),
        ("always-never", r"\b(always|never)\b", 1.0),
        ("every-time", r"\b(every time|each time|whenever)\b", 0.75),
        ("rule", r"\b(rul(e|es|ing|ings)|policy|standing order|by default|default is)\b", 1.0),
        ("must", r"\b(must|must not|have to|need to|required)\b", 0.5),
        ("only", r"\bonly\b", 0.25),
    ))

STRONG_RULING = frozenset({"from-now-on", "always-never", "rule", "every-time"})

IMPERATIVE_RE = re.compile(
    r"^\W*(please\s+)?(do|don'?t|do not|never|always|use|keep|make|stop|start|avoid|ask|tell|write|"
    r"put|run|add|remove|drop|check|send|post|wait|let|give|show|report|prefer|treat|call|name|"
    r"leave|set|pin|move|merge|push|commit|file|log|note|remember|ensure|make sure|no|only|"
    r"skip|batch|default|route|reply|answer|cc|copy|delete|archive|open|close|fix|test|ship)\b",
    re.IGNORECASE,
)
MODAL_RE = re.compile(r"\b(you|agents?|we)\s+(should|must|shall|need to|have to|will|may not|can'?t|cannot)\b",
                      re.IGNORECASE)

DECISION_Q_RE = re.compile(
    r"\b(should i|shall i|do you want|would you like|want me to|which (one|option|of)|"
    r"approve|confirm|ok to|okay to|prefer|or should|go ahead|green ?light|sign off|your call|decide)\b",
    re.IGNORECASE,
)
DECISION_MARK_RE = re.compile(
    r"^\W*(decision( needed)?|open (question|decision)|needs? (your )?(approval|decision)|q\d+)\b",
    re.IGNORECASE,
)
PARKED_RE = re.compile(
    r"\b(decide later|tbd|to be decided|open question|park (this|that|it)|parked|undecided|"
    r"let me think|hold off|not yet decided|revisit)\b",
    re.IGNORECASE,
)

COMPACTION_MARKERS = ("[CONTEXT COMPACTION", "[Context summary", "<compaction-summary", "## Compaction summary",
                      "Summary of earlier conversation")


def _score(text: str, table) -> tuple[float, list[str]]:
    score, cues = 0.0, []
    for name, pat, w in table:
        if pat.search(text):
            score += w
            cues.append(name)
    return score, cues


def is_imperative(text: str) -> bool:
    return bool(IMPERATIVE_RE.match(text) or MODAL_RE.search(text))


# --- signals ------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    kind: str                      # ruling | correction | decision
    text: str
    index: int                     # position of the source message in the list
    role: str
    cues: tuple[str, ...] = field(default_factory=tuple)
    score: float = 0.0
    verdict: str | None = None     # corrections: "reject" or "edit"
    target_index: int | None = None  # corrections: the agent message being corrected

    @property
    def key(self) -> str:
        """Stable dedup key: kind plus normalized text."""
        return hashlib.sha256(f"{self.kind}\n{normalize(self.text).lower()}".encode()).hexdigest()[:24]


def classify_sentence(text: str, *, after_agent: bool) -> tuple[str | None, float, list[str]]:
    """Classify one principal sentence as ruling, correction or nothing."""
    caps = caps_emphasis(text)
    c_score, c_cues = _score(text, CORRECTION_CUES)
    r_score, r_cues = _score(text, RULING_CUES)
    if caps:
        c_score += 0.5
        r_score += 0.5
        c_cues.append("caps")
        r_cues.append("caps")
    imperative = is_imperative(text)
    strong = bool(STRONG_RULING & set(r_cues))
    # A general instruction beats an instance-level complaint: "don't ever X again" is a rule.
    if strong and (imperative or r_score >= 1.5):
        return RULING, r_score, r_cues + (["imperative"] if imperative else [])
    if (after_agent or "label" in c_cues) and c_score >= 1.0:
        return CORRECTION, c_score, c_cues
    if imperative and r_score >= 1.0:
        return RULING, r_score, r_cues + ["imperative"]
    return None, 0.0, []


def _verdict(cues: Sequence[str]) -> str:
    return "reject" if {"no", "stop", "undo", "not-what"} & set(cues) else "edit"


def extract_signals(messages: Sequence[Mapping[str, Any]], *, principal_roles: Iterable[str] = ("user",),
                    agent_roles: Iterable[str] = ("assistant",),
                    skip: Callable[[Mapping[str, Any]], bool] | None = None,
                    max_text: int = MAX_TEXT) -> list[Signal]:
    """Rulings, corrections and open decisions in `messages`, in order, deduplicated.

    `skip(message)` lets a host drop its own synthetic rows (compaction summaries and the like).
    """
    principal_roles, agent_roles = set(principal_roles), set(agent_roles)
    out: list[Signal] = []
    seen: set[str] = set()
    last_agent: int | None = None
    # Index of the next principal message after each position, to tell open questions from answered.
    next_principal = [None] * (len(messages) + 1)
    for i in range(len(messages) - 1, -1, -1):
        role = messages[i].get("role")
        next_principal[i] = i if role in principal_roles else next_principal[i + 1]

    def add(sig: Signal) -> None:
        if sig.key not in seen:
            seen.add(sig.key)
            out.append(sig)

    for i, msg in enumerate(messages):
        role = msg.get("role")
        if skip is not None and skip(msg):
            continue
        text = content_text(msg.get("content"))
        if not text.strip() or text.lstrip().startswith(COMPACTION_MARKERS):
            continue
        if role in agent_roles:
            answered = next_principal[i + 1] is not None
            if not answered:
                for s in sentences(text):
                    if DECISION_MARK_RE.match(s) or (s.rstrip().endswith("?") and DECISION_Q_RE.search(s)):
                        add(Signal(DECISION, clip(s, max_text), i, role, ("agent-question",), 1.0))
            last_agent = i
            continue
        if role not in principal_roles or is_trivial(text):
            continue
        items = numbered_items(text)
        list_is_rules = len(items) >= 2 and bool(re.search(r"\brul(e|es|ing|ings)\b|\bdecisions?\b", text, re.I))
        for item in items:
            kind, score, cues = classify_sentence(item, after_agent=last_agent is not None)
            if list_is_rules or kind == RULING or is_imperative(item):
                add(Signal(RULING, clip(item, max_text), i, role, tuple(["numbered", *cues]), score + 1.0))
        for s in sentences(text):
            if NUMBERED_RE.match(s):
                continue
            if PARKED_RE.search(s):
                add(Signal(DECISION, clip(s, max_text), i, role, ("parked",), 1.0))
                continue
            kind, score, cues = classify_sentence(s, after_agent=last_agent is not None)
            if kind == CORRECTION:
                add(Signal(CORRECTION, clip(s, max_text), i, role, tuple(cues), score, _verdict(cues), last_agent))
            elif kind == RULING:
                add(Signal(RULING, clip(s, max_text), i, role, tuple(cues), score))
    return out


# --- rendering and events -----------------------------------------------------

SECTION_TITLES = ((RULING, "Rulings"), (CORRECTION, "Corrections"), (DECISION, "Open decisions"))


def render_block(signals: Sequence[Signal], max_chars: int = 2000,
                 title: str = "Principal signals from the compressed span (keep verbatim)") -> str:
    """A short text block for a compressor to keep verbatim. Empty when there are no signals."""
    if not signals:
        return ""
    lines = [f"## {title}"]
    dropped = 0
    for kind, heading in SECTION_TITLES:
        group = [s for s in signals if s.kind == kind]
        if not group:
            continue
        head = f"{heading}:"
        if sum(len(x) + 1 for x in lines) + len(head) + 1 > max_chars:
            dropped += len(group)
            continue
        lines.append(head)
        for s in group:
            line = f"- {s.text}"
            if sum(len(x) + 1 for x in lines) + len(line) + 40 > max_chars:
                dropped += 1
                continue
            lines.append(line)
        if lines[-1] == head:
            lines.pop()
    if dropped:
        lines.append(f"(+{dropped} more in the ledger)")
    return "\n".join(lines)


def to_event(sig: Signal, source: str = "capture") -> tuple[str, dict]:
    """(ledger kind, body) for a signal. A correction's verdict still needs `target` (the id of
    the judged event) filled in by the caller before it can be appended."""
    body = {"text": sig.text, "cues": list(sig.cues), "role": sig.role, "msg_index": sig.index,
            "capture_key": sig.key, "source": source}
    if sig.kind == RULING:
        return "ruling", body
    if sig.kind == CORRECTION:
        return "verdict", {**body, "verdict": sig.verdict or "edit"}
    return "question", {**body, "open": True}
