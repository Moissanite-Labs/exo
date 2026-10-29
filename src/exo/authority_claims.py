"""Bounded candidate signals for review; never an authority or permission check.

This proposed detector is intentionally not wired into ingress. A match is a
possible unquoted instruction-override block, not proof of malicious intent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_OVERRIDE = re.compile(r"(?:\b(?:ignore|disregard|invalidate)\b.{0,50}\b(?:previous|preceding|earlier)\b.{0,30}\binstructions\b|\b(?:previous|preceding|earlier) instructions (?:are|were) (?:invalid|void))", re.I)
_REPLACE = re.compile(r"\b(?:you must|must now|now follow|obey this)\b", re.I)
_AUTHORITY = re.compile(r"\b(?:speak|speaking|acting) (?:for|on behalf of) the principal\b", re.I)
_QUOTE = re.compile(r"^\s*(?:>|[\"'“‘])")


@dataclass(frozen=True)
class ClaimCandidate:
    author: str
    kind: str = "instruction_override"
    # No user body is copied into findings/telemetry by this building block.


def principal_claim_candidate(text: str, *, author: str) -> ClaimCandidate | None:
    """Require a short multi-line unquoted override plus replacement direction.

    Refuse over-limit input instead of scanning a prefix and silently treating it
    as complete. Code fences, quote lines and single-line discussion are excluded.
    This is a reviewable initial heuristic, not a universal injection detector.
    """
    if len(text) > 65536:
        return None
    lines = []
    fence = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None or _QUOTE.match(line):
            continue
        if stripped:
            lines.append(stripped)
    if len(lines) < 2:
        return None
    offset = 1 if _AUTHORITY.search(lines[0]) else 0
    if len(lines) <= offset + 1 or not _OVERRIDE.search(lines[offset]):
        return None
    if not any(_REPLACE.search(line) for line in lines[offset + 1:]):
        return None
    return ClaimCandidate(author=author)
