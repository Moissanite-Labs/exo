# Lifted from NousResearch/hermes-agent tools/fuzzy_match.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""Fuzzy find-and-replace for model-written edits.

An ordered chain of increasingly permissive strategies lets whitespace,
indentation, escaping and Unicode drift in tool-call arguments still land on
the intended region. Exo change: the two similarity strategies need two or more
non-blank lines, so a one-line pattern whose text differs is refused. Usage::

    new_content, count, strategy, error = fuzzy_find_and_replace(
        content, old_string, new_string, replace_all=False)

Exo trims: metrics hooks dropped, docstrings shortened. Two fixes: the whitespace-normalized span
mapping no longer starts a match inside the blank run before it, and a dedented pattern matched after a
line's indentation is re-anchored on that indentation before the reindent (see _apply_replacements).
"""

from __future__ import annotations

import bisect
import re
from difflib import SequenceMatcher
from typing import Callable

Span = tuple[int, int]

UNICODE_MAP = {
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
    "\u2014": "--", "\u2013": "-", "\u2026": "...", "\u00a0": " ", "\u2212": "-",
    "\u2000": " ", "\u2001": " ", "\u2002": " ", "\u2003": " ",
    "\u2004": " ", "\u2005": " ", "\u2006": " ", "\u2007": " ",
    "\u2008": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ",
    "\u205f": " ", "\u3000": " "}


def _unicode_normalize(text: str) -> str:
    for char, repl in UNICODE_MAP.items():
        text = text.replace(char, repl)
    return text


# ---------------------------------------------------------------- positions

def _window_spans(content: str, lines: list[str], n: int,
                  accept: Callable[[int], bool]) -> list[Span]:
    def span(i: int) -> Span:
        start = sum(len(line) + 1 for line in lines[:i])
        end = sum(len(line) + 1 for line in lines[:i + n]) - 1
        return start, min(len(content), end)
    return [span(i) for i in range(len(lines) - n + 1) if accept(i)]


def _match_transformed_lines(content: str, pattern: str,
                             transform: Callable[[list[str]], list[str]]) -> list[Span]:
    lines = content.split("\n")
    pat = transform(pattern.split("\n"))
    n = len(pat)
    return _window_spans(content, lines, n, lambda i: transform(lines[i:i + n]) == pat)


def _build_orig_to_norm_map(original: str) -> list[int]:
    """Original index to index in the Unicode-normalized string (plus end sentinel)."""
    result: list[int] = []
    pos = 0
    for char in original:
        result.append(pos)
        repl = UNICODE_MAP.get(char)
        pos += len(repl) if repl is not None else 1
    result.append(pos)
    return result


def _norm_end_to_orig(orig_to_norm: list[int], orig_start: int, norm_end: int) -> int:
    orig_len = len(orig_to_norm) - 1
    orig_end = orig_start
    while orig_end < orig_len and orig_to_norm[orig_end] < norm_end:
        orig_end += 1
    return orig_end


def _map_positions_norm_to_orig(orig_to_norm: list[int], norm_matches: list[Span]) -> list[Span]:
    starts: dict[int, int] = {}
    for orig_pos, norm_pos in enumerate(orig_to_norm[:-1]):
        starts.setdefault(norm_pos, orig_pos)
    return [(starts[ns], _norm_end_to_orig(orig_to_norm, starts[ns], ne)) for ns, ne in norm_matches if ns in starts]


def _map_normalized_positions(original: str, normalized: str, matches: list[Span]) -> list[Span]:
    """Best-effort span mapping for ``[ \\t]+`` to ``' '`` whitespace collapsing."""
    orig_to_norm: list[int] = []
    oi = ni = 0
    while oi < len(original) and ni < len(normalized):
        if original[oi] in " \t":
            # Every blank of a run maps to the one space it collapsed to; the run's last blank moves on.
            # (A blank must take this branch: matching it as an ordinary character consumed the normalized
            # space on the run's first blank and mapped the rest of the run onto the next word, so a span
            # could start inside the indentation and the reindented replacement shifted every line.)
            orig_to_norm.append(ni)
            oi += 1
            if oi < len(original) and original[oi] not in " \t":
                ni += 1
        elif original[oi] == normalized[ni]:
            orig_to_norm.append(ni)
            oi += 1
            ni += 1
        else:
            orig_to_norm.append(ni)
            oi += 1
    orig_to_norm.extend([len(normalized)] * (len(original) - oi))
    to_start: dict[int, int] = {}
    to_end: dict[int, int] = {}
    for orig_pos, norm_pos in enumerate(orig_to_norm):
        to_start.setdefault(norm_pos, orig_pos)
        to_end[norm_pos] = orig_pos
    out: list[Span] = []
    for ns, ne in matches:
        start = to_start[ns] if ns in to_start else min(i for i, n in enumerate(orig_to_norm) if n >= ns)
        end = to_end[ne - 1] + 1 if ne - 1 in to_end else start + (ne - ns)
        # Absorb trailing collapsed whitespace only when the match itself ended in a space.
        if ne < len(normalized) and normalized[ne - 1] == " ":
            while end < len(original) and original[end] in " \t":
                end += 1
        out.append((start, min(end, len(original))))
    return out


# ---------------------------------------------------------------- strategies

def _strategy_exact(content: str, pattern: str) -> list[Span]:
    return [m.span() for m in re.finditer(re.escape(pattern), content)]


def _strategy_line_trimmed(content: str, pattern: str) -> list[Span]:
    return _match_transformed_lines(content, pattern, lambda ls: [x.strip() for x in ls])


def _strategy_whitespace_normalized(content: str, pattern: str) -> list[Span]:
    def norm(s: str) -> str:
        return re.sub(r"[ \t]+", " ", s)
    normalized = norm(content)
    found = _strategy_exact(normalized, norm(pattern))
    return _map_normalized_positions(content, normalized, found) if found else []


def _strategy_escape_normalized(content: str, pattern: str) -> list[Span]:
    unescaped = pattern.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
    return [] if unescaped == pattern else _strategy_exact(content, unescaped)


def _strategy_unicode_normalized(content: str, pattern: str) -> list[Span]:
    norm_pattern, norm_content = _unicode_normalize(pattern), _unicode_normalize(content)
    if norm_content == content and norm_pattern == pattern:
        return []
    found = (_strategy_exact(norm_content, norm_pattern)
             or _strategy_line_trimmed(norm_content, norm_pattern))
    return _map_positions_norm_to_orig(_build_orig_to_norm_map(content), found) if found else []


def _strategy_block_anchor(content: str, pattern: str) -> list[Span]:
    """Anchor on first and last lines, similarity-score the middle. Needs two non-blank lines."""
    pat_lines = _unicode_normalize(pattern).split("\n")
    if len([p for p in pat_lines if p.strip()]) < 2:  # blank lines are no anchor for one line
        return []
    first, last, n = pat_lines[0].strip(), pat_lines[-1].strip(), len(pat_lines)
    norm_lines = _unicode_normalize(content).split("\n")
    candidates = {i for i in range(len(norm_lines) - n + 1)
                  if norm_lines[i].strip() == first and norm_lines[i + n - 1].strip() == last}
    threshold = 0.50 if len(candidates) == 1 else 0.70
    pat_middle = "\n".join(pat_lines[1:-1])

    def similar(i: int) -> bool:
        if i not in candidates:
            return False
        if n <= 2:
            return True
        middle = "\n".join(norm_lines[i + 1:i + n - 1])
        return SequenceMatcher(None, middle, pat_middle).ratio() >= threshold

    return _window_spans(content, content.split("\n"), n, similar)


def _strategy_context_aware(content: str, pattern: str) -> list[Span]:
    """Last resort for two or more non-blank lines (one has no context): each non-blank line >= 0.80."""
    pat_lines, lines = pattern.split("\n"), content.split("\n")
    n = len(pat_lines)
    if n > len(lines) or len([p for p in pat_lines if p.strip()]) < 2:
        return []

    def sim(a: str, b: str) -> float:
        return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()

    def accept(i: int) -> bool:
        block = lines[i:i + n]
        if sim(pat_lines[0].strip(), block[0].strip()) < 0.80:
            return False
        if sim(pat_lines[-1].strip(), block[-1].strip()) < 0.80:
            return False
        return all(not p.strip() or sim(p.strip(), c.strip()) >= 0.80
                   for p, c in zip(pat_lines, block))

    return _window_spans(content, lines, n, accept)


STRATEGIES: list[tuple[str, Callable[[str, str], list[Span]]]] = [
    ("exact", _strategy_exact),
    ("line_trimmed", _strategy_line_trimmed),
    ("whitespace_normalized", _strategy_whitespace_normalized),
    ("escape_normalized", _strategy_escape_normalized),
    ("unicode_normalized", _strategy_unicode_normalized),
    ("block_anchor", _strategy_block_anchor),
    ("context_aware", _strategy_context_aware)]

# Approximate matches: fine for one unique replacement, never under replace_all.
SIMILARITY_STRATEGIES = frozenset({"block_anchor", "context_aware"})


# ---------------------------------------------------------------- orchestrator

def is_already_applied(content: str, old_string: str, new_string: str) -> bool:
    """True when a re-sent edit is already present (new_string >= 8 chars, exact; old gone)."""
    if not new_string or len(new_string.strip()) < 8 or new_string not in content:
        return False
    return old_string == new_string or old_string not in content


def _matched_regions(content: str, matches: list[Span]) -> str:
    return "".join(content[s:e] for s, e in matches)


def _format_match_locations(content: str, matches: list[Span], cap: int = 5) -> str:
    rows = []
    for start, _end in matches[:cap]:
        line_no = content.count("\n", 0, start) + 1
        ls = content.rfind("\n", 0, start) + 1
        le = content.find("\n", ls)
        snippet = content[ls:le if le != -1 else len(content)].strip()
        rows.append(f"  L{line_no}: {snippet[:77] + '...' if len(snippet) > 80 else snippet}")
    if len(matches) > cap:
        rows.append(f"  ... and {len(matches) - cap} more")
    return "\n".join(rows)


def _same_line(a: str, b: str) -> bool:
    """Equal up to indentation, runs of blanks, Unicode punctuation and literal \\t escapes."""
    def norm(s: str) -> str:
        return re.sub(r"[ \t]+", " ", _unicode_normalize(s)).strip()
    return norm(a) == norm(b) or norm(a.replace("\\t", "\t")) == norm(b)


def _first_line_mismatch(region: str, pattern: str, exact_lines: frozenset[int]) -> str | None:
    """Describe the first pattern line in ``exact_lines`` that the matched region's line differs from."""
    for i, (want, got) in enumerate(zip(pattern.split("\n"), region.split("\n"))):
        if i in exact_lines and not _same_line(want, got):
            return f"'-' line {want.strip()!r} does not match the file's line {got.strip()!r}"
    return None


def fuzzy_find_and_replace(content: str, old_string: str, new_string: str,
                           replace_all: bool = False, exact_lines: frozenset[int] = frozenset(),
                           ) -> tuple[str, int, str | None, str | None]:
    """Returns ``(new_content, match_count, strategy, error)``; on failure ``(content, 0, None, error)``.

    ``exact_lines`` are 0-based line numbers of ``old_string`` (a patch hunk's ``-`` lines) that must
    match their file line up to whitespace, escaping or Unicode; the similarity strategies may place
    only the other lines.
    """
    if not old_string:
        return content, 0, None, (
            "old_string is empty, nothing to match. Set old_string to the exact existing text "
            "to replace (read the file first if unsure). To create or fully rewrite a file use "
            "write_file. Do not re-send this call unchanged.")
    if not old_string.strip():
        return content, 0, None, (
            "old_string is only whitespace. Provide non-blank text to match. "
            "Do not re-send this call unchanged.")
    if old_string == new_string:
        return content, 0, None, (
            "No edit was applied because old_string and new_string are identical. "
            "Provide the existing text in old_string and the changed text in new_string.")

    refused: str | None = None
    for name, strategy in STRATEGIES:
        matches = strategy(content, old_string)
        if matches and exact_lines and name in SIMILARITY_STRATEGIES:
            kept = []
            for span in matches:
                bad = _first_line_mismatch(content[span[0]:span[1]], old_string, exact_lines)
                if bad:
                    refused = refused or bad
                else:
                    kept.append(span)
            matches = kept
        if not matches:
            continue
        if len(matches) > 1 and not replace_all:
            return content, 0, None, (
                f"Found {len(matches)} matches for old_string. Provide more context to make it "
                f"unique, or use replace_all=true. Matches:\n{_format_match_locations(content, matches)}")
        if replace_all and len(matches) > 1 and name in SIMILARITY_STRATEGIES:
            return content, 0, None, (
                f"Found {len(matches)} approximate matches via the '{name}' strategy; replace_all "
                f"only applies to exact matches. Provide the precise text, whitespace included.")
        if name != "exact":
            drift = _detect_escape_drift(content, matches, old_string, new_string)
            if drift:
                return content, 0, None, drift
        effective = _maybe_unescape_new_string(new_string, content, matches)
        new_content = _apply_replacements(
            content, matches, effective,
            old_string=old_string if name != "exact" else None,
            unicode_plan=(_unicode_edit_plan(old_string, effective)
                          if name == "unicode_normalized" else None))
        return new_content, len(matches), name, None
    if refused:
        return content, 0, None, f"Refused a similar block: {refused}. Re-read the file and send its current text."
    return content, 0, None, "Could not find a match for old_string in the file"


# ---------------------------------------------------------------- escape drift guards

def _detect_newline_literal_drift(content: str, matches: list[Span],
                                  old_string: str, new_string: str) -> str | None:
    """A literal ``\\n`` in the arguments standing in for a real line break in the file."""
    if "\\n" not in new_string:
        return None
    old_literals = old_string.count("\\n")
    for start, end in matches:
        region = content[start:end]
        if "\n" in region and old_literals > region.count("\\n"):
            return (
                "Escape-drift detected: old_string has more literal '\\\\n' sequences than the "
                "matched region, which has real line breaks there. The tool-call arguments were "
                "escaped one extra time. Re-read the file and pass real line breaks.")
    return None


def _detect_escape_drift(content: str, matches: list[Span],
                         old_string: str, new_string: str) -> str | None:
    has_quotes = "\\'" in new_string or '\\"' in new_string
    if not has_quotes and "\\" not in old_string:
        return None
    regions = _matched_regions(content, matches)
    if has_quotes:
        for suspect in ("\\'", '\\"'):
            if suspect in new_string and suspect in old_string and suspect not in regions:
                return (
                    f"Escape-drift detected: old_string and new_string contain {suspect!r} but "
                    f"the matched region does not. Re-read the file and pass the strings without "
                    f"backslash-escaping {suspect[1]!r}.")
    return (_detect_newline_literal_drift(content, matches, old_string, new_string)
            or _detect_backslash_doubling(regions, old_string, new_string))


def _backslash_runs(s: str) -> list[int]:
    return [len(run) for run in re.findall(r"\\+", s)]


def _detect_backslash_doubling(regions: str, old_string: str, new_string: str) -> str | None:
    """Every backslash run in old_string exactly twice the file's (double JSON escaping)."""
    old_runs, file_runs = _backslash_runs(old_string), _backslash_runs(regions)
    if (not old_runs or not file_runs or len(old_runs) != len(file_runs)
            or old_runs == file_runs
            or any(o != f * 2 for o, f in zip(old_runs, file_runs))
            or not (any(f >= 2 for f in file_runs) or len(file_runs) >= 2)
            or _backslash_runs(new_string) == file_runs):
        return None
    return (
        "Escape-drift detected: every backslash run in old_string is twice as long as in the "
        "file. The arguments were JSON-escaped one extra time; applying would double every "
        "backslash. Resend with backslash counts exactly as in the file.")


def _maybe_unescape_new_string(new_string: str, content: str, matches: list[Span]) -> str:
    """Literal ``\\t`` / ``\\r`` become control chars only if the region already has them."""
    if "\\t" not in new_string and "\\r" not in new_string:
        return new_string
    regions = _matched_regions(content, matches)
    for literal, control in (("\\t", "\t"), ("\\r", "\r")):
        if literal in new_string and control in regions:
            new_string = new_string.replace(literal, control)
    return new_string


# ---------------------------------------------------------------- replacement shaping

def _leading_ws(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def _first_meaningful_line(text: str) -> str | None:
    return next((line for line in text.split("\n") if line.strip()), None)


def _reindent_replacement(region: str, old_string: str, new_string: str) -> str:
    """Re-anchor new_string onto the file's base indent after a non-exact match."""
    if not new_string:
        return new_string
    old_first, file_first = _first_meaningful_line(old_string), _first_meaningful_line(region)
    if old_first is None or file_first is None:
        return new_string
    old_indent, file_indent = _leading_ws(old_first), _leading_ws(file_first)
    if old_indent == file_indent:
        return new_string
    out = []
    for line in new_string.split("\n"):
        if not line.strip():
            out.append(line)
        elif _leading_ws(line).startswith(old_indent):
            out.append(file_indent + line[len(old_indent):])
        else:
            out.append(file_indent + line.lstrip(" \t"))
    return "\n".join(out)


def _unicode_edit_plan(old_string: str, new_string: str) -> tuple[str, list]:
    norm_old = _unicode_normalize(old_string)
    return norm_old, SequenceMatcher(None, norm_old, new_string).get_opcodes()


def _preserve_unicode_in_replacement(region: str, new_string: str, plan: tuple[str, list]) -> str:
    """Apply only the old-to-new edits onto the region's original text, keeping its typography."""
    norm_old, opcodes = plan
    if norm_old != _unicode_normalize(region):
        return new_string
    o2n = _build_orig_to_norm_map(region)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            start = bisect.bisect_right(o2n, i1) - 1
            parts.append(region[start:_norm_end_to_orig(o2n, start, i2)])
        elif tag != "delete":
            parts.append(new_string[j1:j2])
    return "".join(parts)


def _dedented_form(region: str, old_string: str) -> bool:
    """True when old_string's first indented continuation line sits shallower than the file's (a tab
    counts to the next multiple of 8 columns, so spaces against a tab-indented file compare too): the
    block was dedented to column 0, so its lines need the file's indent back. A verbatim copy
    (continuation lines already at the file's depth) and a one-line old_string are not."""
    for old_line, file_line in zip(old_string.split("\n")[1:], region.split("\n")[1:]):
        if old_line.strip():
            return len(_leading_ws(old_line).expandtabs(8)) < len(_leading_ws(file_line).expandtabs(8))
    return False


def _apply_replacements(content: str, matches: list[Span], new_string: str,
                        old_string: str | None = None,
                        unicode_plan: tuple[str, list] | None = None) -> str:
    result = content
    ordered = sorted(matches)
    for i in reversed(range(len(ordered))):
        start, end = ordered[i]
        adjusted = new_string
        if old_string is not None:
            region = content[start:end]
            if unicode_plan is not None:
                adjusted = _preserve_unicode_in_replacement(region, adjusted, unicode_plan)
            line_start = content.rfind("\n", 0, start) + 1
            if i:
                line_start = max(line_start, ordered[i - 1][1])  # never back into the previous match
            at_eol = end == len(content) or content[end] in "\r\n" or content[end - 1] == "\n"
            if (line_start < start and not content[line_start:start].strip(" \t")
                    and _dedented_form(region, old_string) and (adjusted or at_eol)):
                # A dedented pattern matched after the line's indentation. Take that indentation into the
                # region, so the reindent below sees the file's base indent and the replacement's lines
                # keep their depth. A deletion that stops mid-line keeps the indent.
                start = line_start
            adjusted = _reindent_replacement(content[start:end], old_string, adjusted)
        result = result[:start] + adjusted + result[end:]
    return result


# ---------------------------------------------------------------- did-you-mean

def find_closest_lines(old_string: str, content: str, context: int = 2, limit: int = 3) -> str:
    """Numbered snippets of the lines most similar to old_string's anchor line, or ''."""
    old_lines, lines = old_string.splitlines(), content.splitlines()
    if not old_lines or not lines:
        return ""
    anchor = old_lines[0].strip() or next((x.strip() for x in old_lines if x.strip()), "")
    if not anchor:
        return ""
    scored = sorted(((SequenceMatcher(None, anchor, line.strip()).ratio(), i)
                     for i, line in enumerate(lines) if line.strip()), key=lambda x: -x[0])
    top = [s for s in scored if s[0] > 0.3][:limit]
    parts, seen = [], set()
    for _, idx in top:
        start, end = max(0, idx - context), min(len(lines), idx + len(old_lines) + context)
        if (start, end) not in seen:
            seen.add((start, end))
            parts.append("\n".join(f"{start + j + 1:4d}| {lines[start + j]}" for j in range(end - start)))
    return "\n---\n".join(parts)


def format_no_match_hint(error: str | None, old_string: str, content: str) -> str:
    if not error or not error.startswith("Could not find"):
        return ""
    hint = find_closest_lines(old_string, content)
    return "\n\nDid you mean one of these sections?\n" + hint if hint else ""
