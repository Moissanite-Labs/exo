"""Principal signals for exo-context, classified by ``exo.capture``.

``exo.handoff.extract_signals`` still looks for ``capture.extract_rulings`` (a seam that does not
exist) and so always falls back to its local regexes. This module does the switch: ``exo.capture``
decides which principal messages hold a ruling or a correction, and the verbatim text kept is the
whole message when it is short (as handoff does) or the classified sentence when it is long.
Everything else (open decisions, processes, files, next steps) still comes from ``exo.handoff``.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from exo import capture
from exo import handoff as hf
from exo.ext.compat import is_compaction_summary_message


def _slice(messages: list[dict[str, Any]], sig: capture.Signal) -> str:
    text = hf.message_text(messages[sig.index]).strip() if 0 <= sig.index < len(messages) else ''
    return text if text and len(text) <= hf.MAX_VERBATIM else sig.text


def classified(messages: list[dict[str, Any]]) -> tuple[list[hf.Signal], list[hf.Signal]]:
    """(rulings, corrections) from principal messages, verbatim, in order."""
    found = capture.extract_signals(messages, skip=is_compaction_summary_message)
    rulings, corrections = [], []
    for sig in found:
        if sig.kind == capture.RULING:
            rulings.append(hf.Signal('ruling', _slice(messages, sig), sig.index))
        elif sig.kind == capture.CORRECTION:
            corrections.append(hf.Signal('correction', _slice(messages, sig), sig.index))
    return rulings, corrections


def merge(*groups: Iterable[hf.Signal]) -> list[hf.Signal]:
    """Concatenate and drop repeats (same text ignoring whitespace and case), first wins."""
    return hf._dedupe([s for g in groups for s in g])


def extract(messages: list[dict[str, Any]]) -> hf.Signals:
    """Drop-in for ``hf.extract_signals`` that classifies rulings and corrections with capture.
    Items carried in earlier pinned blocks stay."""
    base = hf.extract_signals(messages)
    rulings, corrections = classified([m for m in messages if isinstance(m, dict)])
    return dataclasses.replace(
        base,
        rulings=merge([s for s in base.rulings if s.carried], rulings),
        corrections=merge([s for s in base.corrections if s.carried], corrections))
