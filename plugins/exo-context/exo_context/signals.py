"""Principal signals for exo-context: rulings and corrections as ``exo.handoff`` finds them (classified by
``exo.capture``), merged with the ones carried in earlier pinned blocks."""
from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from exo import handoff as hf


def classified(messages: list[dict[str, Any]]) -> tuple[list[hf.Signal], list[hf.Signal]]:
    """(rulings, corrections) from principal messages, verbatim, in order. ``exo.handoff`` classifies
    with ``exo.capture`` and keeps a short message whole, a long one as the classified sentence. A user message
    whose ``author`` or ``principal`` flag says it is not the principal's is skipped (D-040)."""
    return hf.extract_rulings_and_corrections([m for m in messages if isinstance(m, dict)])


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
