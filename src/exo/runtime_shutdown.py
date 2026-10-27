"""Quiesce application work around the engine's transport and job steps, before plugin resources and stores go.

``CoreRuntime.close`` runs in three parts. ``stop_admission`` refuses new turns, tools and job notices and cancels
owned work, without waiting. The engine then stops channels, peers, background jobs, the scheduler and MCP.
``drain`` waits, within one bound, for turns, actual synchronous tool workers and learning reviews.

Python cannot kill a worker thread. A drain that reaches its bound raises ``ShutdownTimeout``. By then jobs and
transports are stopped. The engine keeps only what a stuck worker can still touch: plugin resources, the session
store and the writer claim. A later close() completes once the worker returns, and the application's
``CoreEngine.close`` retries until it does. No detached cleanup watcher is created.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

SHUTDOWN_TIMEOUT = 5.0


class ShutdownTimeout(RuntimeError):
    """close() reached its bound with work still running. Jobs and transports are stopped; plugin resources, the
    session store and the writer claim are kept until close() is called again."""

    def __init__(self, waiting_for: str) -> None:
        super().__init__(f"engine shutdown timed out waiting for {waiting_for}; jobs and transports are stopped, "
                         "resources retained, retry close")


def _loops(engine: Any) -> list[Any]:
    scheduler = engine.scheduler
    return [engine.loop, *engine.agents.loops.values(), *(scheduler.loops.values() if scheduler is not None else ())]


def stop_admission(engine: Any) -> set[asyncio.Future]:
    """Refuse new work, keep job notices from starting turns, and cancel owned turns. Returns what ``drain`` awaits."""
    current = asyncio.current_task()
    if current in engine._tasks:
        raise RuntimeError("close must be called by the engine owner, outside a running turn or tool")
    engine._closing = True
    engine.jobs.target.closed = True  # a job that ends from here on is recorded for its thread and never wakes it
    scheduler = engine.scheduler
    scheduled = []
    if scheduler is not None:  # its loop must not claim a slot that comes due while transports stop
        scheduled = [task for task in [scheduler._main, *scheduler.running.values()] if task is not None]
    for loop in _loops(engine):
        loop.closing = True
        for session in tuple(loop._state):
            loop.interrupt(session)
    for context in engine.registry.workers.values():
        context.cancel.set()  # also a worker whose caller close does not own; none starts after this
    tasks = {task for task in [*engine._tasks, *scheduled] if task is not current and not task.done()}
    if engine._started is not None and not engine._started.done():
        tasks.add(engine._started)
    for task in tasks:
        task.cancel()
    return tasks


async def drain(engine: Any, tasks: set[asyncio.Future]) -> None:
    """Wait for cancelled turns, direct ``Loop.send`` callers, sync tool workers and learning reviews.

    Raises ``ShutdownTimeout`` when they are still running at ``SHUTDOWN_TIMEOUT``."""
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT

    def left() -> float:
        return max(0.0, deadline - time.monotonic())

    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=left())
        for task in done:
            if not task.cancelled():
                task.exception()
        if pending:
            raise ShutdownTimeout("turns")
    # Direct embedded Loop.send callers need not be in engine._tasks. Their
    # session state remains running until their final hooks and writes finish.
    while any(state.running for loop in _loops(engine) for state in loop._state.values()):
        if time.monotonic() >= deadline:
            raise ShutdownTimeout("turns")
        await asyncio.sleep(min(0.01, left()))
    workers = dict(engine.registry.workers)  # stop_admission set each one's cancel event
    if workers:
        _, pending = await asyncio.wait(workers, timeout=left())
        if pending:
            raise ShutdownTimeout("tool workers")
    learner = getattr(engine, "_learn", None)  # reviews read the session store and write the principal store
    if learner is not None:
        reviews = [task for task in learner.tasks.values() if not task.done()]
        for task in reviews:
            task.cancel()
        if reviews and (await asyncio.wait(reviews, timeout=left()))[1]:
            raise ShutdownTimeout("learning reviews")
        learner.close()
