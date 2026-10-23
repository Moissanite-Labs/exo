"""Quiesce application work before releasing plugin resources and persistent stores.

Python cannot kill a worker thread. A shutdown that reaches its deadline leaves
the engine's resources and writer claim intact; the owner can retry close after
the handler finishes. No detached cleanup watcher is created.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

SHUTDOWN_TIMEOUT = 5.0


async def quiesce(engine: Any) -> None:
    """Stop admission, cancel owned turns, and await actual synchronous tool completion."""
    current = asyncio.current_task()
    if current in engine._tasks:
        raise RuntimeError("close must be called by the engine owner, outside a running turn or tool")
    engine._closing = True
    loops = [engine.loop, *engine.agents.loops.values()]
    scheduler = engine.scheduler
    scheduled = []
    if scheduler is not None:
        loops.extend(scheduler.loops.values())
        scheduled = [task for task in [scheduler._main, *scheduler.running.values()] if task is not None]
        scheduler._main = None  # intentional stop; its done callback must not report an unexpected exit
    for loop in loops:
        loop.closing = True
        for session in tuple(loop._state):
            loop.interrupt(session)
    tasks = {task for task in [*engine._tasks, *scheduled] if task is not current and not task.done()}
    if engine._started is not None and not engine._started.done():
        tasks.add(engine._started)
    for task in tasks:
        task.cancel()
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
        for task in done:
            if not task.cancelled():
                task.exception()
        if pending:
            raise RuntimeError("engine shutdown timed out waiting for turns; resources retained, retry close")
    # Direct embedded Loop.send callers need not be in engine._tasks. Their
    # session state remains running until their final hooks and writes finish.
    while any(state.running for loop in loops for state in loop._state.values()):
        if time.monotonic() >= deadline:
            raise RuntimeError("engine shutdown timed out waiting for turns; resources retained, retry close")
        await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
    workers = dict(engine.registry.workers)
    for context in workers.values():
        context.cancel.set()
    if workers:
        _, pending = await asyncio.wait(workers, timeout=max(0, deadline - time.monotonic()))
        if pending:
            raise RuntimeError("engine shutdown timed out waiting for tool workers; resources retained, retry close")
