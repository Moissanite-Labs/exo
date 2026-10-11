#!/usr/bin/env python3
"""visual-value-gate.py — conservative advisory gate: does a draft warrant a visual?

Analyzes a draft markdown file and decides whether a visual (table, diagram,
or image) is warranted by the content. CONSERVATIVE by design: it fires only
when the content genuinely benefits from a visual. False negatives (missing a
warranted visual) are acceptable; false positives are bugs.

Deterministic: regex/pattern-based only. No LLM calls, no network, no external
dependencies (stdlib only). Same output for the same input every time.

    visual-value-gate.py <draft.md>

Output format (stdout):
    VISUAL-VALUE-GATE: WARRANTED
      reason: <specific pattern detected>
      suggested_visual: table|mermaid|image
      confidence: high|medium|low
or:
    VISUAL-VALUE-GATE: NOT-WARRANTED
      reason: <why prose is sufficient>
      suggested_visual: none
      confidence: high|medium|low
or (draft already carries a visual — validate instead of suggesting):
    VISUAL-VALUE-GATE: VALIDATION
      reason: <visual already present>
      suggested_visual: none
      confidence: high

Exit codes:
    0  NOT-WARRANTED (prose is correct, no visual needed)
    1  WARRANTED (a visual should be considered but is not present)
    2  VALIDATION (a visual is present — run check-mermaid.py on it)
    3  usage error (wrong argv, missing/unreadable file)
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

WORD_FLOOR = 200  # prose words below this -> NOT-WARRANTED, always

# Paragraph-level comparison frames.
BEFORE_AFTER_RES = [
    re.compile(r"\bbefore\b", re.I),
    re.compile(r"\bafter\b", re.I),
]
OPTION_RE = re.compile(r"\b(option|alternative|approach)\s*[A-Ca-c1-3]?\b", re.I)
CRITERION_RE = re.compile(
    r"\b(vs\.?|compared?\b|comparison\b|trade-?offs?\b|pros?\b.{0,20}cons?\b"
    r"|cons?\b.{0,20}pros?\b|criteria\b|better\b|worse\b|risk\b|cost\b|latency\b"
    r"|reliability\b|setup\b)",
    re.I,
)
FLOW_KW_RE = re.compile(
    r"\b(flows?\b|pipeline\b|feeds?\s+into\b|passes?\s+to\b|sends?\s+to\b"
    r"|depends\s+on\b|upstream\b|downstream\b|sequence\b|transitions?\b"
    r"|state\s+change\b|emits?\b|triggers?\b)",
    re.I,
)
ARROW_RE = re.compile(r"-->|->|=>|\u2192|\|\|--")
ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+\S", re.M)
STEP_WORD_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:step\s*\d+|first|then|next|finally)\b", re.I | re.M
)
PIPE_LINE_RE = re.compile(r"^\s*[^|`\n]+\|[^|`\n]+$", re.M)
NUMBER_UNIT_RE = re.compile(
    r"\d+\s*(pass|fail|ms\b|s\b|%|tests?\b|rps\b|MB\b|GB\b)", re.I
)
TABLE_DELIM_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M
)
TABLE_HEADER_RE = re.compile(r"^\s*\|(.+\|)+\s*$", re.M)
MERMAID_RE = re.compile(r"^\s*```mermaid\s*$", re.M)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\b[^>]*>", re.I)
FENCE_OPEN_RE = re.compile(r"^```", re.M)
CHANGELOG_ITEM_RE = re.compile(
    r"^\s*(?:[-*]|\d+[.)])\s+(?:fix(?:ed|es)?|bump(?:ed|s)?|updat(?:e|ed|es)|"
    r"releas(?:e|ed)|version|patch(?:ed)?|upgrade[sd]?|deprecat|hotfix|"
    r"backport|dependenc|improv(?:e|ed)|refactor|remov(?:e|ed)|changelog)\b",
    re.I | re.M,
)
WORKFLOW_FRAME_RE = re.compile(
    r"\b(step\s*\d+|stage\s*\d+|phase\s*\d+|workflow|sequence|procedure)\b",
    re.I,
)
UNIT_RE = re.compile(r"\b(ms\b|s\b|%|pass\b|fail\b|rps\b|MB\b|GB\b|tests?\b)", re.I)


def strip_fences(text: str) -> tuple[str, int, int]:
    """Return (prose, total_fences, non_mermaid_fences)."""
    opens = FENCE_OPEN_RE.findall(text)
    total = len(opens) // 2
    mermaid = len(MERMAID_RE.findall(text))
    prose = re.sub(r"```.*?```", " ", text, flags=re.S)
    return prose, total, max(0, total - mermaid)


def prose_words(prose: str) -> int:
    no_tables = TABLE_DELIM_RE.sub(" ", prose)
    no_tables = re.sub(r"^\s*\|.*$", " ", no_tables, flags=re.M)
    no_tables = IMAGE_RE.sub(" ", no_tables)
    return len(re.findall(r"\b[\w']+\b", no_tables))


def has_table(text: str) -> bool:
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        if TABLE_HEADER_RE.match(line) and TABLE_DELIM_RE.match(lines[i + 1]):
            return True
    return False


def emit(verdict: str, reason: str, suggested: str, confidence: str) -> str:
    return (
        f"VISUAL-VALUE-GATE: {verdict}\n"
        f"  reason: {reason}\n"
        f"  suggested_visual: {suggested}\n"
        f"  confidence: {confidence}\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print("usage: visual-value-gate.py <draft.md>", file=sys.stderr)
        return 3
    src = Path(args[0])
    if not src.is_file():
        print(f"usage error: not a file: {args[0]}", file=sys.stderr)
        return 3
    try:
        text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"usage error: cannot read {args[0]}: {exc}", file=sys.stderr)
        return 3
    if not text.strip():
        print(f"usage error: empty file: {args[0]}", file=sys.stderr)
        return 3

    # 1. Visual already present -> VALIDATION (validate, don't suggest).
    present: list[str] = []
    if has_table(text):
        present.append("markdown table")
    if MERMAID_RE.search(text):
        present.append("mermaid fence")
    if IMAGE_RE.search(text):
        present.append("image")
    if present:
        sys.stdout.write(
            emit(
                "VALIDATION",
                f"draft already contains {', '.join(present)} — validate instead of suggesting",
                "none",
                "high",
            )
        )
        return 2

    prose, _, non_mermaid_fences = strip_fences(text)
    words = prose_words(prose)
    lines = prose.splitlines()

    # 2. Triggers (conservative: keyword AND count, never bare keyword).
    # A candidate is STRONG when the structure is unambiguous (shared units,
    # ordered items, metric pairs, arrows); weak signals alone never beat
    # the short-draft exemption below.
    candidate: tuple[str, str, str, bool] | None = None  # reason, visual, conf, strong

    # T1: 3+ key:value rows with digits inside a 6-line window -> table.
    # Bare "Label: value" rows count as well as bullets (test-result prose
    # is often unbulleted); URL lines are excluded.
    def _is_kv(ln: str) -> bool:
        if "://" in ln:
            return False
        return bool(
            re.match(r"^\s*(?:[-*]|\d+[.)])?\s*[^:]{1,40}:\s*\S", ln)
            and re.search(r"\d", ln)
        )

    kv_idx = [i for i, ln in enumerate(lines) if _is_kv(ln)]
    if len(kv_idx) >= 3 and any(
        kv_idx[j + 2] - kv_idx[j] <= 5 for j in range(len(kv_idx) - 2)
    ):
        # Require the SAME unit token on 2+ of the rows (same fields):
        # heterogeneous units (12ms vs 40% vs 3GB) must not fire.
        units = [UNIT_RE.search(lines[i]) for i in kv_idx]
        freq = Counter(m.group(0).lower() for m in units if m)
        shared = max(freq.values(), default=0) >= 2
        if shared:
            candidate = (
                f"3+ rows with same fields ({len(kv_idx)} key: value rows with digits, shared units)",
                "table",
                "high",
                True,
            )

    # T1b: 3+ pseudo-column pipe lines but no real table -> table.
    # Corroboration bar (conservative: pasted terminal checklists must not
    # fire): the lines must share a token of length 4+ across 2+ lines AND
    # 2+ of them must carry a digit. Otherwise no candidate at all.
    pipes = PIPE_LINE_RE.findall(prose)
    if candidate is None and len(pipes) >= 3:
        cell_tokens = [
            Counter(t for t in re.findall(r"[a-z0-9]{4,}", pl.lower()))
            for pl in pipes
        ]
        tok_freq: Counter[str] = Counter()
        for ct in cell_tokens:
            for tok in ct:
                tok_freq[tok] += 1
        with_digit = sum(1 for pl in pipes if re.search(r"\d", pl))
        if max(tok_freq.values(), default=0) >= 2 and with_digit >= 2:
            candidate = (
                f"{len(pipes)} pipe-separated lines without a table: improvised columns",
                "table",
                "medium",
                True,
            )

    # T2: 3+ ordered steps -> mermaid. Line-leading markers are strongest;
    # intrasentence step chains (Step 1 ... then ... next ... finally)
    # count when 3+ hits of 2+ distinct kinds appear.
    ordered = len(ORDERED_RE.findall(prose))
    step_words = len(STEP_WORD_RE.findall(prose))
    inline_hits = re.findall(
        r"\bstep\s*\d+|\bfirst\b|\bthen\b|\bnext\b|\bfinally\b|\bafter that\b",
        prose,
        re.I,
    )
    inline_kinds = {h.lower() for h in inline_hits}
    inline_kinds = {re.sub(r"\d+", "N", k) for k in inline_kinds}
    # Changelog exemption (brief NOT-WARRANTED clause): an ordered list of
    # release-note items (Fixed/Bumped/Updated/...) with no step/sequence
    # framing is a list of changes, not a multi-step process.
    changelog_items = len(CHANGELOG_ITEM_RE.findall(prose))
    changelog_shaped = (
        changelog_items >= 3
        and changelog_items >= ordered - 1
        and step_words == 0
        and not WORKFLOW_FRAME_RE.search(prose)
    )
    steps_hit = (
        (ordered >= 3 and not changelog_shaped)
        or step_words >= 3
        or (len(inline_hits) >= 3 and len(inline_kinds) >= 2)
    )
    if candidate is None and steps_hit:
        candidate = (
            f"multi-step process ({ordered} ordered items, {step_words} step words, "
            f"{len(inline_hits)} sequence words)",
            "mermaid",
            "high" if ordered >= 3 or step_words >= 3 else "medium",
            True,
        )

    # T3: before/after frame + 2+ numbered lines -> table.
    if candidate is None and BEFORE_AFTER_RES[0].search(prose) and BEFORE_AFTER_RES[1].search(prose):
        numbered = [ln for ln in lines if NUMBER_UNIT_RE.search(ln)]
        if len(numbered) >= 2:
            candidate = (
                "before/after states with 2+ metric lines: comparison table",
                "table",
                "high",
                True,
            )

    # T3b: decision matrix (2+ options + criterion) -> table.
    options = {m.group(0).lower() for m in OPTION_RE.finditer(prose)}
    if candidate is None and len(options) >= 2 and CRITERION_RE.search(prose):
        candidate = (
            f"decision matrix ({len(options)} distinct options with comparison criteria)",
            "table",
            "medium",
            True,
        )

    # T4: data flow (2+ flow keywords, or any arrow) -> mermaid.
    flow_hits = len(FLOW_KW_RE.findall(prose))
    arrows = len(ARROW_RE.findall(prose))
    if candidate is None and (flow_hits >= 2 or arrows >= 1):
        candidate = (
            f"data flow or state transitions ({flow_hits} flow keywords, {arrows} arrows)",
            "mermaid",
            "high" if arrows >= 2 or (arrows >= 1 and flow_hits >= 1) else "medium",
            arrows >= 2 or (arrows >= 1 and flow_hits >= 1),  # one bare arrow
            # in a short draft is usually inline notation, not a diagram plea
        )

    # T5: 2+ code blocks inside a comparison frame -> table.
    if candidate is None and non_mermaid_fences >= 2 and (
        CRITERION_RE.search(prose)
        or (BEFORE_AFTER_RES[0].search(prose) or BEFORE_AFTER_RES[1].search(prose))
        or re.search(r"\b(either|alternative|which .* better|option)\b", prose, re.I)
    ):
        candidate = (
            f"{non_mermaid_fences} code blocks presenting alternatives: comparison table",
            "table",
            "medium",
            True,
        )

    # 3. Short-draft exemption: under the floor, only a STRONG structural
    # signal warrants a visual; weak signals and pure prose stay prose.
    if candidate is not None and (words >= WORD_FLOOR or candidate[3]):
        reason, suggested, confidence, _ = candidate
        sys.stdout.write(emit("WARRANTED", reason, suggested, confidence))
        return 1
    if candidate is not None:
        sys.stdout.write(
            emit(
                "NOT-WARRANTED",
                f"weak signal only in short prose ({words} words < {WORD_FLOOR}): prose suffices",
                "none",
                "medium",
            )
        )
        return 0

    # 4. Default: prose suffices.
    sys.stdout.write(
        emit(
            "NOT-WARRANTED",
            "no comparison data, multi-step sequence, or flow structure: prose suffices",
            "none",
            "high",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
