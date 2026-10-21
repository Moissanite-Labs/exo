"""How long a turn waits for memory upkeep (issue #99, D-068).

A turn is built from the summaries that are ready plus the verbatim tail; summary and merge work keeps running in
the background and the next turn picks it up. The one reason to wait is size: when the verbatim parts alone would
overflow the context's ``view_budget``, wait briefly (``overflow_settle``) for a summary that is nearly done.

Consistency does not depend on the wait. ``build`` reads the tree and the tail in one synchronous stretch, and a
summary node turns ``done`` (text and status together) in a single step, so a turn sees each span either as its
old verbatim or leaf summaries, or as the finished summary, never a half-applied one.

One view has no summary to wait for: a private thread whose own tree has no finished summary yet. Its tail is
the whole thread, so a slow summarizer would leave the first calls unbounded (issue #244). Until the first
summary lands, ``fit_tail`` cuts that tail to ``view_budget`` and the caller marks the cut with
``SUMMARIZING_MARKER``. Once a summary lands, the tree's view leads the tail as usual and nothing is cut.
"""
from __future__ import annotations

from typing import Any

OVERFLOW_SETTLE = 5.0  # seconds a turn waits for summaries when the verbatim view overflows the budget
SUMMARIZING_MARKER = "[earlier messages are being summarized]"  # leads a tail cut by fit_tail (issue #244)


def fit_tail(tail: list, budget: int) -> tuple[list, int]:
    """The newest messages of ``tail`` whose rendered size fits ``budget``, and how many older ones were left
    out. The last message is always kept, however large: it is the turn being answered."""
    from exo.core.context import estimate_tokens  # core imports this module

    kept: list = []
    used = 0
    for m in reversed(tail):
        cost = estimate_tokens(m)
        if kept and used + cost > budget:
            break
        kept.append(m)
        used += cost
    kept.reverse()
    return kept, len(tail) - len(kept)


def verbatim_overflows(ctx: Any, key: str) -> bool:
    """True when the view as it stands (finished summaries, unfinished spans verbatim, then the tail) is over budget."""
    from exo.core.context import estimate_tokens  # core imports this module

    top = ctx._top(key)
    frontier = top[-1].end if top else 0
    used = sum(estimate_tokens(p) for p in ctx._view_parts(top)) + sum(estimate_tokens(m) for m in ctx._read(key, frontier))
    return used > ctx.view_budget


async def settle_for_turn(ctx: Any, key: str) -> bool:
    """Wait only as long as a turn needs: not at all with room to spare, else ``overflow_settle`` seconds."""
    if not ctx.pending(key) or not verbatim_overflows(ctx, key):
        return True
    return await ctx.settle(key, min(ctx.settle_timeout, getattr(ctx, "overflow_settle", OVERFLOW_SETTLE)))
