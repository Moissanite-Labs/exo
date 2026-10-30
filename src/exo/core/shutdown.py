"""Quiesce application work around the engine's transport and job steps, before plugin resources and stores go.

``CoreRuntime.close`` runs in three parts. It refuses new work, and ``stop_admission`` keeps job notices from
starting turns and cancels owned work, without waiting. The engine then stops channels, peers, background jobs, the scheduler and MCP.
``drain`` waits, within one bound, for turns, actual synchronous tool workers and learning reviews. A second
Ctrl-C does not cut that wait short. The engine passes its own tasks in, and loops and the scheduler answer
through public methods, so this module reads no other module's private state.

Python cannot kill a worker thread. A drain that reaches its bound raises ``ShutdownTimeout``. By then jobs and
transports are stopped. The engine keeps only what a stuck worker can still touch: plugin resources, the session
store and the writer claim. A later close() completes once the worker returns, and the application's
``CoreEngine.close`` retries until it does. No detached cleanup watcher is created.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable, Sequence

SHUTDOWN_TIMEOUT = 5.0


class ShutdownTimeout(RuntimeError):
    """close() reached its bound with work still running. Jobs and transports are stopped; plugin resources, the
    session store and the writer claim are kept until close() is called again.

    ``errors`` are the close steps that failed before the bound. The message names the first and it is the cause,
    so a retry whose steps pass does not hide it."""

    def __init__(self, waiting_for: str, errors: Sequence[BaseException] = ()) -> None:
        self.errors = list(errors)
        failed = f"; a close step also failed: {type(errors[0]).__name__}: {errors[0]}" if errors else ""
        super().__init__(f"engine shutdown timed out waiting for {waiting_for}; jobs and transports are stopped, "
                         f"resources retained, retry close{failed}")
        self.__cause__ = errors[0] if errors else None


def _loops(engine: Any) -> list[Any]:
    scheduler = engine.scheduler
    return [engine.loop, *engine.agents.loops.values(), *(scheduler.loops.values() if scheduler is not None else ())]


def stop_admission(engine: Any, owned: Iterable[asyncio.Task], started: asyncio.Future | None) -> set[asyncio.Future]:
    """Keep job notices from starting turns and cancel owned turns. Returns what ``drain`` awaits.

    The engine has already checked that its owner calls close and set its closing flag, so it refuses new work.
    ``owned`` are its turn and tool tasks, ``started`` its start task."""
    current = asyncio.current_task()
    engine.jobs.target.closed = True  # a job that ends from here on is recorded for its thread and never wakes it
    scheduler = engine.scheduler
    scheduled = scheduler.tasks() if scheduler is not None else ()  # no slot is claimed while transports stop
    for loop in _loops(engine):
        loop.closing = True
        loop.interrupt_all()
    for context in engine.registry.workers.values():
        context.cancel.set()  # also a worker whose caller close does not own; none starts after this
    tasks = {task for task in [*owned, *scheduled] if task is not current and not task.done()}
    if started is not None and not started.done():
        tasks.add(started)
    for task in tasks:
        task.cancel()
    return tasks


async def drain(engine: Any, tasks: set[asyncio.Future], errors: list[BaseException], learner: Any = None) -> None:
    """Wait for cancelled turns, direct ``Loop.send`` callers, sync tool workers and learning reviews, then close the
    learner (the application's, which the engine passes in).

    ``errors`` holds the close steps that failed so far. A learner that fails to close is added to it, so close()
    still runs its final steps. Raises ``ShutdownTimeout``, carrying ``errors``, when work is still running at
    ``SHUTDOWN_TIMEOUT``.

    A hard stop of the caller here (a second Ctrl-C or SIGTERM cancels it) does not cut the wait short. It is added
    to ``errors``, and the wait goes on within the same bound. So close() still runs plugin cleanups once the work
    ends, and raises the cancel after them."""
    waiting = asyncio.ensure_future(_wait(engine, tasks, errors, learner))
    while not waiting.done():
        try:
            await asyncio.shield(waiting)
        except asyncio.CancelledError as exc:
            if waiting.cancelled():
                raise
            errors.append(exc)
    if (still := waiting.result()) is not None:
        raise ShutdownTimeout(still, errors)  # built here, so it also carries a cancel from the last pass


async def _wait(engine: Any, tasks: set[asyncio.Future], errors: list[BaseException], learner: Any) -> str | None:
    """``drain``'s wait. Returns what is still running at the bound, or None once all of it has ended."""
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT

    def left() -> float:
        return max(0.0, deadline - time.monotonic())

    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=left())
        for task in done:
            if not task.cancelled():
                task.exception()
        if pending:
            return "turns"
    # Direct embedded Loop.send callers need not be in engine._tasks. Their
    # session state remains running until their final hooks and writes finish.
    while any(loop.busy() for loop in _loops(engine)):
        if time.monotonic() >= deadline:
            return "turns"
        await asyncio.sleep(min(0.01, left()))
    workers = dict(engine.registry.workers)  # stop_admission set each one's cancel event
    if workers:
        _, pending = await asyncio.wait(workers, timeout=left())
        if pending:
            return "tool workers"
    if learner is not None:  # reviews read the session store and write the principal store
        reviews = [task for task in learner.tasks.values() if not task.done()]
        for task in reviews:
            task.cancel()
        if reviews and (await asyncio.wait(reviews, timeout=left()))[1]:
            return "learning reviews"
        try:
            learner.close()
        except BaseException as exc:  # noqa: BLE001 - close() raises it after its final steps
            errors.append(exc)
    return None
