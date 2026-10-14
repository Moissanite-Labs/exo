"""How long a turn waits for memory upkeep (issue #99, D-068).

A turn is built from the summaries that are ready plus the verbatim tail; summary and merge work keeps running in
the background and the next turn picks it up. The one reason to wait is size: when the verbatim parts alone would
overflow the context's ``view_budget``, wait briefly (``overflow_settle``) for a summary that is nearly done.

Consistency does not depend on the wait. ``build`` reads the tree and the tail in one synchronous stretch, and a
summary node turns ``done`` (text and status together) in a single step, so a turn sees each span either as its
old verbatim or leaf summaries, or as the finished summary, never a half-applied one.
"""
from __future__ import annotations

from typing import Any

OVERFLOW_SETTLE = 5.0  # seconds a turn waits for summaries when the verbatim view overflows the budget


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
