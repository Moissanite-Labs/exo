"""How long a turn waits for memory upkeep (issue #99, D-068).

A turn is built from the summaries that are ready plus the verbatim tail; summary and merge work keeps running in
the background and the next turn picks it up. The one reason to wait is size: when the verbatim parts alone would
overflow the context's ``view_budget``, wait briefly (``overflow_settle``) for a summary that is nearly done.

Consistency does not depend on the wait. ``build`` reads the tree and the tail in one synchronous stretch, and a
summary node turns ``done`` (text and status together) in a single step, so a turn sees each span either as its
old verbatim or leaf summaries, or as the finished summary, never a half-applied one.

A private thread's own tree is the one case where the wait is not enough (issue #244). Its view ends at its last
summary, so every span after it, pending ones included, is in the tail, and a slow summarizer would leave those
calls unbounded. While its leaves are pending, ``cut_pending`` leaves out the oldest of their spans until what comes
before the turn fits ``view_budget``, and ``SUMMARIZING_MARKER`` stands in each gap. That includes a pending leaf
the view shows verbatim because a later leaf landed first. The overflow check measures pending spans as the view
renders them, cut short, so that tree also waits when ``cut_pending`` would leave a span out: a leaf this call
scheduled gets its chance to run first, and a fast summarizer, or a span too small to summarize, leaves no cut.
"""
from __future__ import annotations

import json
from typing import Any

OVERFLOW_SETTLE = 5.0  # seconds a turn waits for summaries when the verbatim view overflows the budget
SUMMARIZING_MARKER = "[earlier messages are being summarized]"  # stands where cut_pending left a span out (issue #244)


def _cost(m: Any) -> int:
    """``estimate_tokens``, with tool-call arguments in full: ``render_message`` cuts them at 400 characters."""
    from exo.core.context import estimate_tokens  # core imports this module

    return estimate_tokens(m) + sum(max(0, len(json.dumps(c.arguments, ensure_ascii=False)) - 400)
                                    for c in m.tool_calls) // 4


def cut_pending(ctx: Any, session: str, tail: list, turn_id: str = "") -> list:
    """``tail`` with the spans of a private thread's pending leaves left out, oldest first and a whole leaf at a time,
    until what comes before the turn fits ``view_budget`` (issue #244). Each gap gets ``SUMMARIZING_MARKER``.

    Only an AgentContext's private thread has a tree keyed by its session id, so any other thread passes through.
    The turn is kept whole: from its first message in ``tail`` (its user message, unless a summary holds that) to
    the end. So is every span that is not pending: a skipped or failed span stays verbatim, and the last
    ``tail_messages`` are in no leaf yet. Without a ``turn_id`` the turn starts at the last user message that is not
    a note. A tool call and its results are kept or left out together. The cut moves a whole leaf at a time, so it
    stays put until it must grow and calls share their cached prefix. When a later leaf landed first, the private
    view shows the pending ones before it verbatim. Those are the oldest, so they go first, each to the marker under
    its heading."""
    from exo.core.context import TreeContext, estimate_tokens  # core imports this module
    from exo.core.types import ContentPart, Message

    if not TreeContext.pending(ctx, session):
        return tail
    turn = next((i for i, m in enumerate(tail) if turn_id and m.meta.get("turn_id") == turn_id),
                max((i for i, m in enumerate(tail) if m.role == "user" and not m.meta.get("note")), default=len(tail)))
    cost = {m.meta["seq"]: _cost(m) for m in tail[:turn] if "seq" in m.meta}  # the private view has no seq
    used = sum(cost.values()) + sum(_cost(m) for m in tail[:turn] if "seq" not in m.meta)
    view = tail[0].text() if tail and "seq" not in tail[0].meta and tail[0].meta.get("note") == "private" else ""
    cut: set[int] = set()
    for leaf in sorted((n for n in ctx.store.summaries(session) if n.level == 0 and n.status == "pending"),
                       key=lambda n: n.start):
        if used <= ctx.view_budget:
            break
        span = [s for s in range(leaf.start, leaf.end + 1) if s in cost]
        part = "" if span or not view else ctx._view_parts([leaf])[0]
        if part and part in view:  # a later leaf landed first, so the private view shows this one verbatim
            short = f"## Messages {leaf.start}-{leaf.end}\n{SUMMARIZING_MARKER}"
            view, used = view.replace(part, short), used - estimate_tokens(part) + estimate_tokens(short)
        cut.update(span)
        used -= sum(cost[s] for s in span)
    if view and view != tail[0].text():
        tail = [Message("user", [ContentPart.of_text(view)], meta=dict(tail[0].meta)), *tail[1:]]
    if not cut:
        return tail
    split = True
    while split:  # a leaf edge can fall between a call and its results: keep both
        split = False
        for a, b in zip(tail, tail[1:]):
            if b.role == "tool" and (a.meta.get("seq") in cut) != (b.meta.get("seq") in cut):
                cut -= {a.meta.get("seq"), b.meta.get("seq")}
                split = True
    out, prev = [], None
    for m in tail:
        if m.meta.get("seq") not in cut:
            out.append(m)
        elif prev not in cut:
            out.append(Message("user", [ContentPart.of_text(SUMMARIZING_MARKER)], meta={"note": "private", "local": True}))
        prev = m.meta.get("seq")
    return out


def verbatim_overflows(ctx: Any, key: str) -> bool:
    """True when the view as it stands (finished summaries, unfinished spans verbatim, then the tail) is over budget."""
    from exo.core.context import estimate_tokens  # core imports this module

    top = ctx._top(key)
    frontier = top[-1].end if top else 0
    used = sum(estimate_tokens(p) for p in ctx._view_parts(top)) + sum(estimate_tokens(m) for m in ctx._read(key, frontier))
    return used > ctx.view_budget


def _cuts(ctx: Any, key: str) -> bool:
    """True when ``cut_pending`` would leave a span out of a private thread's next call (issue #244)."""
    own = getattr(ctx, "_own", None)  # an AgentContext: only a private thread has a tree keyed by its session id
    if own is None or key.startswith("agent:") or not own(key):
        return False
    tail = ctx._thread_tail(key, 0)  # a private thread's tail starts at its own tree's frontier
    return cut_pending(ctx, key, tail) is not tail  # it returns the tail itself when it leaves nothing out


async def settle_for_turn(ctx: Any, key: str) -> bool:
    """Wait only as long as a turn needs: not at all with room to spare, else ``overflow_settle`` seconds. A private
    thread's tree has no room to spare when ``cut_pending`` would leave a span out."""
    if not ctx.pending(key) or not (verbatim_overflows(ctx, key) or _cuts(ctx, key)):
        return True
    return await ctx.settle(key, min(ctx.settle_timeout, getattr(ctx, "overflow_settle", OVERFLOW_SETTLE)))
