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
the view shows verbatim because a later leaf landed first, unless it holds the turn's first messages. The overflow
check measures pending spans as the view renders them, cut short, so that tree also waits when ``cut_pending`` would
leave a span out: a leaf this call scheduled gets its chance to run first, and a fast summarizer, or a span too
small to summarize, leaves no cut. Either wait of that tree is for its own jobs only, leaves and merges, not the
agent tree's.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

OVERFLOW_SETTLE = 5.0  # seconds a turn waits for summaries when the verbatim view overflows the budget
SUMMARIZING_MARKER = "[earlier messages are being summarized]"  # stands where cut_pending left a span out (issue #244)


def _cost(m: Any) -> int:
    """``estimate_tokens``, with tool-call arguments in full: ``render_message`` cuts them at 400 characters."""
    from exo.core.context import estimate_tokens  # core imports this module

    return estimate_tokens(m) + sum(max(0, len(json.dumps(c.arguments, ensure_ascii=False)) - 400)
                                    for c in m.tool_calls) // 4


def _turn_start(ctx: Any, session: str, since: int, tail: list, turn_id: str) -> int:
    """The seq of the turn's first message, found as ``cut_pending`` finds it in ``tail``, but from ``since`` on: the
    stored messages up to ``tail``, then its own. 0 when none of them can start it, so the turn started before."""
    first = next((m.meta["seq"] for m in tail if "seq" in m.meta), None)
    msgs = ctx._read(session, since, None if first is None else first - 1) + [m for m in tail if "seq" in m.meta]
    return next((m.meta["seq"] for m in msgs if turn_id and m.meta.get("turn_id") == turn_id),
                max((m.meta["seq"] for m in msgs if m.role == "user" and not m.meta.get("note")), default=0))


def cut_pending(ctx: Any, session: str, tail: list, turn_id: str = "") -> list:
    """``tail`` with the spans of a private thread's pending leaves left out, oldest first and a whole leaf at a time,
    until what comes before the turn fits ``view_budget`` (issue #244). Each gap gets ``SUMMARIZING_MARKER``.

    Only an AgentContext's private thread has a tree keyed by its session id, so any other thread passes through.
    The turn is kept whole: from its first message in ``tail`` (its user message, unless a summary holds that) to
    the end. So is every span that is not pending: a skipped or failed span stays verbatim, and the last
    ``tail_messages`` are in no leaf yet. Without a ``turn_id`` the turn starts at the last user message that is not
    a note, and before ``tail`` when it holds none. A tool call and its results are kept or left out together. The
    cut moves a whole leaf at a time, so it stays put until it must grow and calls share their cached prefix. When a
    later leaf landed first, the private view shows the pending ones before it verbatim. Those are the oldest, so
    they go first, each to the marker under its heading, up to the one that holds the turn's first messages: a turn
    can start inside the view."""
    from exo.core.context import TreeContext, estimate_tokens  # core imports this module
    from exo.core.types import ContentPart, Message

    if not TreeContext.pending(ctx, session):
        return tail
    view = tail[0].text() if tail and "seq" not in tail[0].meta and tail[0].meta.get("note") == "private" else ""
    turn = next((i for i, m in enumerate(tail) if turn_id and m.meta.get("turn_id") == turn_id),
                max((i for i, m in enumerate(tail) if m.role == "user" and not m.meta.get("note")),
                    default=int(bool(view))))  # no question in the tail: the turn began before it
    cost = {m.meta["seq"]: _cost(m) for m in tail[:turn] if "seq" in m.meta}  # the private view has no seq
    used = sum(cost.values()) + sum(_cost(m) for m in tail[:turn] if "seq" not in m.meta)
    if used <= ctx.view_budget:  # room to spare: no need to read the leaves
        return tail
    cut: set[int] = set()
    start = None  # the turn's first seq, read once the view cut needs it
    for leaf in sorted((n for n in ctx.store.summaries(session) if n.level == 0 and n.status == "pending"),
                       key=lambda n: n.start):
        if used <= ctx.view_budget:
            break
        span = [s for s in range(leaf.start, leaf.end + 1) if s in cost]
        part = "" if span or not view else ctx._view_parts([leaf])[0]
        if part and part in view:  # a later leaf landed first, so the private view shows this one verbatim
            start = _turn_start(ctx, session, leaf.start - 1, tail, turn_id) if start is None else start
            if leaf.end >= start:  # it holds the turn's first messages, and every leaf after it is the turn's
                break
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


def _cuts(ctx: Any, key: str, turn_id: str = "") -> bool:
    """True when ``cut_pending`` would leave a span out of a private thread's next call (issue #244). When it would
    not, that call's ``thread_tail`` takes the tail built here, so ``build`` reads the thread once."""
    own = getattr(ctx, "_own", None)  # an AgentContext: only a private thread has a tree keyed by its session id
    if own is None or key.startswith("agent:") or not own(key):
        return False
    tail = ctx._thread_tail(key, 0)  # a private thread's tail starts at its own tree's frontier
    if cut_pending(ctx, key, tail, turn_id) is not tail:  # it returns the tail itself when it leaves nothing out
        return True
    ctx._uncut = (key, ctx.store.last_seq(key), tail)
    asyncio.get_running_loop().call_soon(setattr, ctx, "_uncut", None)  # gone once anything else runs: a leaf may land
    return False


def thread_tail(ctx: Any, session: str, frontier: int, turn_id: str = "", last_turn: bool = False) -> list:
    """The thread's tail as its next call carries it: ``_thread_tail``, then ``cut_pending``. ``build`` takes the tail
    its wait check just built when nothing has run or been stored since. With ``last_turn`` (``/context``) the turn
    is the thread's last stored one: mid-turn, the running turn, as ``build`` finds it by its turn_id."""
    uncut, ctx._uncut = getattr(ctx, "_uncut", None), None
    if uncut and uncut[:2] == (session, ctx.store.last_seq(session)):
        return uncut[2]
    tail = ctx._thread_tail(session, frontier)
    if last_turn:
        turn_id = next((m.meta["turn_id"] for m in reversed(tail) if m.meta.get("turn_id")), "")
    return cut_pending(ctx, session, tail, turn_id)


async def settle_for_turn(ctx: Any, key: str, turn_id: str = "", *, owner: str = "") -> bool:
    """Wait only as long as a turn needs: not at all with room to spare, else ``overflow_settle`` seconds for the
    tree's own live jobs. A private thread's tree has no room to spare when ``cut_pending`` would leave a span out.
    Its turn waits for that tree's jobs only: the agent tree's jobs shrink neither its view nor the cut.
    An identified turn gets one timed opportunity per owner/tree; anonymous callers retain per-build waits."""
    from exo.core.context import TreeContext  # core imports this module

    own = TreeContext.pending  # AgentContext.pending adds the agent tree's jobs to a private thread's
    if not own(ctx, key) or not (verbatim_overflows(ctx, key) or _cuts(ctx, key, turn_id)):
        return True
    # Agent trees are shared: the owner separates two threads waiting during different turns.
    # Keep only the latest identity per owner/tree, not every historical turn ID.
    identity = (owner or key, key)
    if turn_id and ctx.settled_turns.get(identity) == turn_id:
        await asyncio.sleep(0)  # let already-ready upkeep publish, without another timed wait
        return not own(ctx, key, "live")
    if not own(ctx, key, "live"):
        return True
    if turn_id:
        ctx.settled_turns[identity] = turn_id
    timeout = min(ctx.settle_timeout, getattr(ctx, "overflow_settle", OVERFLOW_SETTLE))
    deadline = asyncio.get_running_loop().time() + timeout
    while pending := own(ctx, key, "live"):
        if (left := deadline - asyncio.get_running_loop().time()) <= 0:
            return False
        await asyncio.wait(pending, timeout=left)
    return True
