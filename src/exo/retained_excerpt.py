"""Opt-in building block for excerpts of already retained, sanitized tool text.

No runtime calls this module yet. Callers remain responsible for principal/session
access, redaction, original retention, call/result pairing and exact expansion.
"""
from __future__ import annotations

import re

from exo.core.types import ToolResult

_MAX_INPUT = 1_048_576
_IMPORTANT = re.compile(
    r"\b(error|fail(?:ed|ure)?|exception|traceback|fatal|warning|denied|exit)\b"
    r"|^\[(?:resource|source|untrusted|third.party|truncated|omitted|command )", re.I,
)
_MARKER = "\n[... excerpt omitted retained text; exact expansion is required ...]\n"


def excerpt(text: str, budget: int) -> str | None:
    """Return a bounded deterministic view, or None to preserve the full input.

    Distinct diagnostics/source labels are retained verbatim. A refusal is never
    permission to truncate the original. Bounds count Python characters, not JSON
    bytes or tokens; the eventual transport must apply its own encoded-size bound.
    """
    if type(budget) is not int or not 256 <= budget <= 16000:
        raise ValueError("budget must be an integer between 256 and 16000")
    if len(text) <= budget or len(text) > _MAX_INPUT:
        return None
    important = list(dict.fromkeys(line for line in text.splitlines() if _IMPORTANT.search(line)))
    marked = "\n".join(important)
    labels = "[diagnostic/source lines]\n" + marked + "\n" if marked else ""
    room = budget - len(_MARKER) - len(labels)
    if room < 64:
        return None
    head = room // 2
    return text[:head] + _MARKER + labels + text[-(room - head):]


def excerpt_result(result: ToolResult, budget: int) -> ToolResult | None:
    """Copy a text-only ToolResult view, preserving pairing and error status.

    Mixed image/document results are refused: flattening them loses source data.
    Original objects are never mutated. Trust/source labels remain in text.
    """
    if any(part.type != "text" for part in result.content):
        return None
    view = excerpt(result.text(), budget)
    if view is None:
        return None
    return ToolResult.of_text(result.call_id, view, result.is_error)
