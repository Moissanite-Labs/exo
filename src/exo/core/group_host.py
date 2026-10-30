"""Trusted-host injection layer for Swarm group lifecycle (docs/SWARM-HOST.md).

The host supplies the authenticated owner, an explicit authorized profile set
and an authorized engine factory at construction. Authorization is never
inferred from seat files, stored metadata, credentials or configuration, and
this module constructs no profiles, engines or CoreEngine instances itself.

``GroupHost`` is an internal composition seam, not an authentication boundary,
protocol command or agent tool. Host objects must never be exposed to models.
It delegates durable claims, pause/stop semantics and receipts to
``GroupRuntime``: one claim per group, no automatic retry, no stale-claim
takeover. A real factory integration (constructing authorized engines) is
deliberately out of scope for this slice.

An explicit opt-in (``conversation=ConversationOptions(...)`` on ``start``)
binds the reactive ``GroupConversationAdapter`` instead of the one-shot
``GroupEngineAdapter``: fixed authorized audiences are derived from the
stored group mode, explicit ``max_turns``/``idle_timeout`` bounds are
validated before the factory is invoked or any claim exists, and a host
``send`` wakes the running conversation after the message is persisted.
Without the opt-in, ``start`` keeps the exact one-shot default behavior.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Callable, Collection, Mapping

from exo.core.group_conversation import (DEFAULT_IDLE_TIMEOUT, DEFAULT_MAX_TURNS,
                                         ConversationWake, GroupConversationAdapter)
from exo.core.group_engine_adapter import GroupEngineAdapter
from exo.core.group_execution import GroupExecutions
from exo.core.group_messages import GroupMessage, GroupMessages
from exo.core.group_runner import MemberStatus
from exo.core.group_runtime import GroupRuntime
from exo.core.groups import GroupStore, SavedGroup

# Trusted host hook: returns one authorized, distinct engine per member profile.
# The factory decides binding; this layer only validates membership coverage.
EngineFactory = Callable[[SavedGroup], Mapping[str, object]]


class HostError(ValueError):
    """Host-level rejection: authorization, unknown group or lifecycle misuse."""


@dataclass(frozen=True)
class ConversationOptions:
    """Explicit opt-in bounds for a reactive conversation start.

    ``max_turns`` is the per-member budget of successful engine turns and
    ``idle_timeout`` bounds each no-input wait (a missed wake degrades to
    this poll, never a lost message). Both are validated before the engine
    factory is invoked or any durable claim exists. Instances are immutable
    and reusable; each conversation start owns its own ``ConversationWake``.
    """

    max_turns: int = DEFAULT_MAX_TURNS
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT


class GroupHost:
    """Owner-scoped controller for creating and operating saved groups.

    The owner is fixed at construction and verified against the stored group
    before every runtime operation. Runtimes are process-local; durable claims
    survive this object, so a crashed start still blocks automatic retry.
    """

    def __init__(self, groups: GroupStore, *, owner: str,
                 known_profiles: Collection[str], engine_factory: EngineFactory):
        if not isinstance(owner, str) or not owner.strip():
            raise HostError("owner is required")
        if isinstance(known_profiles, (str, bytes)):
            raise HostError("known_profiles must be a collection of profile IDs")
        profiles = frozenset(known_profiles)
        if not profiles:
            raise HostError("at least one authorized profile is required")
        if not callable(engine_factory):
            raise HostError("an authorized engine factory is required")
        self._groups = groups
        self._messages = GroupMessages(groups)
        self._owner = owner
        self._profiles = profiles
        self._factory = engine_factory
        self._runtimes: dict[str, GroupRuntime] = {}
        # Wakes for opt-in conversation runtimes, keyed the same way; a host
        # ``send`` after an accepted persist wakes the matching conversation.
        self._wakes: dict[str, ConversationWake] = {}
        # Closing begins the moment aclose() is entered: no new start/create
        # is accepted afterwards. A successful close flips ``_closed`` once;
        # a failed close retains runtimes and drops the in-flight cleanup
        # task so an explicit later ``aclose()`` starts a fresh attempt.
        self._closing = False
        self._closed = False
        # Shared in-flight cleanup task: concurrent ``aclose()`` callers all
        # shield-await this one task, so runtimes are settled and the factory
        # is closed at most once per close attempt.
        self._cleanup_task: asyncio.Task | None = None

    @property
    def owner(self) -> str:
        return self._owner

    def create(self, *, mode: str, members: Collection[str],
               conductor: str | None = None) -> SavedGroup:
        """Persist a group restricted to the injected authorized profile set."""
        self._ensure_accepting()
        try:
            return self._groups.create(owner=self._owner, mode=mode, members=members,
                                       known_profiles=self._profiles, conductor=conductor)
        except ValueError as e:
            raise HostError(str(e)) from e

    def _group(self, group_id: str) -> SavedGroup:
        """Ownership check precedes every runtime operation; never infer access."""
        if not isinstance(group_id, str) or not group_id.strip():
            raise HostError("group id must be a non-empty string")
        group = self._groups.get(group_id, owner=self._owner)
        if group is None:
            raise HostError("no such group")
        return group

    def _runtime(self, group_id: str) -> GroupRuntime:
        self._group(group_id)
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise HostError("group execution has not started in this process")
        return runtime

    @staticmethod
    def _audiences_for(group: SavedGroup) -> dict[str, list[str]]:
        """Fixed authorized audiences derived from the stored group mode.

        Conductor mode: every worker targets exactly the conductor, and the
        conductor targets its first peer in stored member order. Socratic
        mode: every member targets all other members. Audiences are never
        inferred from model output or message content.
        """
        profiles = [m.profile for m in group.members]
        if group.mode == "conductor":
            conductor = group.conductor
            first_peer = next(p for p in profiles if p != conductor)
            return {p: ([conductor] if p != conductor else [first_peer])
                    for p in profiles}
        return {p: [q for q in profiles if q != p] for p in profiles}

    def start(self, group_id: str, *,
              audiences: Mapping[str, list[str]] | None = None,
              conversation: ConversationOptions | None = None) -> tuple:
        """Bind authorized engines and claim one durable execution. No retry.

        Stored membership is re-validated against the injected profile set
        before the factory is invoked or any claim is made: authorization is
        never inferred from stored metadata. A factory failure or invalid
        audience leaves no runtime and no durable claim, so the caller may fix
        inputs and start once. A partial start failure after the claim still
        blocks retry by design (GroupExecutions).

        Default (no ``conversation``) is the one-shot ``GroupEngineAdapter``
        with caller-supplied ``audiences``. With the explicit
        ``conversation`` opt-in the fixed authorized audiences are derived
        from the stored group mode (caller ``audiences`` are then rejected),
        conversation bounds are validated before the factory is invoked or
        any claim exists, and the reactive ``GroupConversationAdapter`` is
        bound with a per-start ``ConversationWake``: accepted host ``send``s
        and member replies wake the running conversation, while a missed
        wake degrades to the bounded idle poll.
        """
        self._ensure_accepting()
        group = self._group(group_id)
        if group_id in self._runtimes:
            raise HostError("group execution already started in this process")
        unknown = [m.profile for m in group.members if m.profile not in self._profiles]
        if unknown:
            raise HostError("group members outside the injected profile set: "
                            + ", ".join(sorted(unknown)))
        if conversation is not None:
            # Validate bounds before the factory is invoked or any claim
            # exists: an invalid bound leaves no engines and no receipt.
            # These are the adapter's full preallocation bounds, mirrored
            # verbatim (the exact-type test also rejects bools, and the
            # ordered comparisons reject inf/nan) so an out-of-range or
            # non-finite value never reaches the factory or a claim.
            if (type(conversation.max_turns) is not int
                    or not 1 <= conversation.max_turns <= 10000):
                raise HostError(
                    "conversation max_turns must be an explicit positive finite budget")
            if (type(conversation.idle_timeout) not in (int, float)
                    or not 0 < conversation.idle_timeout <= 86400):
                raise HostError("conversation idle_timeout must bound the idle wait")
            if audiences is not None:
                raise HostError("audiences are derived from the stored group mode "
                                "for a conversation start; do not pass them")
            engines = dict(self._factory(group) or {})
            wake = ConversationWake()
            try:
                adapter = GroupConversationAdapter(
                    group, engines,
                    audiences=self._audiences_for(group),
                    wake=wake,
                    max_turns=conversation.max_turns,
                    idle_timeout=float(conversation.idle_timeout))
                runtime = GroupRuntime(self._messages, group.id, owner=self._owner,
                                       adapters=adapter.adapters())
                runtime.start()
            except ValueError as e:
                # Nothing started: close the unregistered wake so no waiter
                # lingers, and leave no runtime, no claim and no stored wake.
                wake.close()
                raise HostError(str(e)) from e
            # Same contract as start_conversation: set the wake once after a
            # successful start so initial mailbox input is picked up without
            # an idle-timeout wait. The wake is stored only now.
            wake.set()
            self._runtimes[group_id] = runtime
            self._wakes[group_id] = wake
            return runtime.status()
        if audiences is None:
            raise HostError("one-shot start requires explicit audiences")
        engines = dict(self._factory(group) or {})
        try:
            adapter = GroupEngineAdapter(group, engines, audiences=audiences)
            runtime = GroupRuntime(self._messages, group.id, owner=self._owner,
                                   adapters=adapter.adapters())
            runtime.start()
        except ValueError as e:
            raise HostError(str(e)) from e
        self._runtimes[group_id] = runtime
        return runtime.status()

    def status(self, group_id: str) -> dict:
        """Structured snapshot: member states, durable receipt, liveness flags.

        Returns a dict with ``members`` (live runner states for a group started
        in this process, otherwise the settled receipt's terminal states or
        ``"idle"`` when no claim exists), ``receipt`` (the durable
        ``ExecutionReceipt`` or ``None``) and ``reconciliation_required``. With
        no local runtime there is no claimed liveness: an active receipt means
        a prior process claimed execution and may still hold it, so
        ``reconciliation_required`` is set and no start may be attempted until
        the receipt is reconciled. The receipt is never a liveness probe.
        """
        group = self._group(group_id)
        runtime = self._runtimes.get(group_id)
        receipt = GroupExecutions(self._groups).get(group.id, owner=self._owner)
        if runtime is not None:
            members = runtime.status()
        elif receipt is not None and receipt.state == "settled":
            members = tuple(MemberStatus(m.profile, receipt.members[m.profile])
                            for m in group.members)
        elif receipt is not None:
            # Active claim, no local runtime: liveness is unknown, never "idle".
            members = tuple(MemberStatus(m.profile, "unknown") for m in group.members)
        else:
            members = tuple(MemberStatus(m.profile, "idle") for m in group.members)
        return {
            "group_id": group.id,
            "owner": self._owner,
            "members": members,
            "receipt": receipt,
            "local_runtime": runtime is not None,
            "reconciliation_required": runtime is None and receipt is not None
            and receipt.state == "active",
        }

    def pause(self, group_id: str, *, profile: str | None = None) -> None:
        try:
            self._runtime(group_id).pause(profile)
        except ValueError as e:
            raise HostError(str(e)) from e

    def resume(self, group_id: str, *, profile: str | None = None) -> None:
        try:
            self._runtime(group_id).resume(profile)
        except ValueError as e:
            raise HostError(str(e)) from e

    def stop(self, group_id: str, *, profile: str | None = None) -> None:
        try:
            self._runtime(group_id).stop(profile)
        except ValueError as e:
            raise HostError(str(e)) from e

    async def wait(self, group_id: str) -> tuple:
        try:
            return await self._runtime(group_id).wait()
        except ValueError as e:
            raise HostError(str(e)) from e

    def send(self, group_id: str, *, audience: list[str], text: str) -> GroupMessage:
        """Human message with an explicit audience; never a member tool.

        The message is persisted first; only after the send is accepted does
        an opt-in conversation running in this process get woken for its
        audience. The wake is immediate and in-memory only; persistence is
        the source of truth, so a member that misses it still picks the
        message up on its bounded idle poll.
        """
        group = self._group(group_id)
        try:
            message = self._messages.send_human(group.id, owner=self._owner,
                                                audience=audience, text=text)
        except ValueError as e:
            raise HostError(str(e)) from e
        wake = self._wakes.get(group.id)
        if wake is not None:
            wake.set()
        return message

    def _ensure_accepting(self) -> None:
        """Reject new work once closing has begun."""
        if self._closing or self._closed:
            raise HostError("host is closing or closed; no new create/start")

    async def aclose(self) -> None:
        """Stop all local runtimes, await their settlement, then close the factory.

        Closing begins the moment this is entered: ``create``/``start`` are
        rejected from then on. The first caller spawns one shared cleanup
        task; every concurrent caller shield-awaits that same task, so
        runtimes are settled and the factory's async cleanup is invoked at
        most once per close attempt. ``asyncio.shield`` keeps the shared
        cleanup running when an individual caller is cancelled, and a
        wrapper retrieves the task's exception when every waiter is gone, so
        no cleanup failure is ever lost. A caller's ``CancelledError`` says
        nothing about cleanup: poll ``closed`` and retry ``aclose()``
        explicitly to await or re-drive the shared cleanup.

        Fail-closed: a failed cleanup raises in every caller, runtimes are
        retained and ``closed`` stays False; the failed task is dropped once
        retrieved, so an explicit later ``aclose()`` starts a fresh attempt.
        A fully successful close flips ``closed`` once and repeat calls
        return immediately (idempotent).
        """
        if self._closed:
            return  # idempotent: a prior successful close already settled
        task = self._cleanup_task
        if task is None:
            # Closing begins now: reject new work synchronously at entry,
            # then spawn the single shared cleanup task.
            self._closing = True
            task = asyncio.create_task(self._cleanup())
            self._cleanup_task = task
        try:
            await asyncio.shield(task)
        except BaseException:
            if task.done():
                # Outcome observed: a failed attempt is dropped so an
                # explicit later aclose() starts a fresh cleanup. This also
                # covers the no-waiter case: the wrapper retrieves the
                # exception, sees task.done(), and clears the task here.
                self._cleanup_task = None
            raise

    async def _cleanup(self) -> None:
        """One shared close attempt: settle every runtime, then close the factory.

        Every local runtime is stopped and awaited (engines are never
        released while any runtime can still be running), and every runtime
        is attempted even when an earlier one fails. Only after all runtimes
        have settled is the engine factory's async cleanup (an
        ``aclose``/``close`` attribute, when present) awaited; a plain
        callable factory has no such attribute and is left untouched
        (backward-compatible).
        """
        errors: list[BaseException] = []
        # Stop and settle every runtime; never release engines beneath one that
        # is still running. All runtimes are attempted despite individual error.
        for group_id, runtime in list(self._runtimes.items()):
            try:
                runtime.stop()
                await runtime.wait()
            except BaseException as exc:  # noqa: BLE001 - every runtime still attempted
                errors.append(exc)
        if errors:
            # Fail closed: retain runtimes for retry; do not release engines.
            raise HostError(f"runtime settlement failed: {errors[0]}") from errors[0]

        # Engines may be released only now that no runtime can still be running.
        aclose = getattr(self._factory, "aclose", None) or getattr(self._factory, "close", None)
        if aclose is not None:
            try:
                result = aclose()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:  # noqa: BLE001 - factory close failure is retriable
                # Runtimes stay referenced; a retry re-settles (already
                # settled) runtimes and re-attempts the factory close.
                raise HostError(f"engine factory close failed: {exc}") from exc

        self._runtimes.clear()
        # Wakes are removed on close: closing each unregistered wake wakes any
        # member still waiting so it exits on its stop flag, never a hang.
        for wake in self._wakes.values():
            wake.close()
        self._wakes.clear()
        self._closed = True
        # Successful close: drop the finished shared task synchronously (no
        # loop tick needed) since ``closed`` now short-circuits new callers.
        self._cleanup_task = None
