"""Pure, default-off retry advice for plan attempts; NOT integrated recovery.

No I/O, clocks, sleeps, grants, ledger, scheduler or exception classification.
Only a positively classified transient failure *before dispatch* can be retried.
The caller must supply trusted facts, not model text or a guessed error string.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Failure(Enum):
    UNKNOWN = "unknown"
    TRANSIENT = "transient"
    DENIED = "denied"
    SPEND_LIMIT = "spend_limit"
    CANCELLED = "cancelled"
    PERMANENT = "permanent"


class Effects(Enum):
    UNKNOWN = "unknown"
    NOT_DISPATCHED = "not_dispatched"
    EXTERNAL = "external"


class Action(Enum):
    FINAL = "final"
    RETRY = "retry"
    RECONCILE = "reconcile"


@dataclass(frozen=True)
class TaskIdentity:
    """Logical identity stays fixed; profile must not change between attempts."""

    plan_id: str
    task_id: str
    profile: str

    def __post_init__(self) -> None:
        for value in (self.plan_id, self.task_id, self.profile):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("task identity fields must be nonempty strings")


@dataclass(frozen=True)
class Attempt:
    """Snapshot of a TaskRunState plus separately evidenced failure/effect facts.

    ``number`` is the durable 1-based attempt count, never a process-local counter.
    Unknown state strings are retained so decide() can refuse new/future states.
    """

    identity: TaskIdentity
    number: int
    state: str
    failure: Failure = Failure.UNKNOWN
    effects: Effects = Effects.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.identity, TaskIdentity):
            raise ValueError("identity must be TaskIdentity")
        if type(self.number) is not int or self.number < 1:
            raise ValueError("attempt number must be a positive integer")
        if not isinstance(self.state, str):
            raise ValueError("state must be a string")
        if not isinstance(self.failure, Failure) or not isinstance(self.effects, Effects):
            raise ValueError("failure and effects must be explicit enum values")


@dataclass(frozen=True)
class SupervisionPolicy:
    """Lifetime retry ceiling per logical task; zero disables retries.

    Hard cap avoids accidental unbounded retry configurations. No time window
    resets the budget; attempt 1 plus max_retries is the total attempt ceiling.
    """

    max_retries: int = 0

    def __post_init__(self) -> None:
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 10:
            raise ValueError("max_retries must be an integer from 0 to 10")


@dataclass(frozen=True)
class Decision:
    action: Action
    identity: TaskIdentity
    reason: str
    next_attempt: int | None = None
    reconciliation_required: bool = False


def decide(attempt: Attempt, policy: SupervisionPolicy = SupervisionPolicy(), *,
           stop_requested: bool = False) -> Decision:
    """Advise only; RETRY is neither permission nor an atomic attempt reservation.

    Cancellation/denial/spend/permanent failures remain final even when effects
    need reconciliation. Completed work is never replayed. A pending/running row
    is not evidence of a safe failure, including after a process crash.
    """
    if not isinstance(attempt, Attempt) or not isinstance(policy, SupervisionPolicy):
        raise ValueError("expected Attempt and SupervisionPolicy")
    if type(stop_requested) is not bool:
        raise ValueError("stop_requested must be boolean")

    def result(action: Action, reason: str, *, reconcile: bool = False) -> Decision:
        return Decision(action, attempt.identity, reason,
                        attempt.number + 1 if action is Action.RETRY else None, reconcile)

    uncertain_effects = attempt.effects is not Effects.NOT_DISPATCHED
    if stop_requested or attempt.state in {"stopped", "cancelled"}:
        return result(Action.FINAL, "cancelled", reconcile=uncertain_effects)
    if attempt.failure in {Failure.CANCELLED, Failure.DENIED, Failure.SPEND_LIMIT,
                           Failure.PERMANENT}:
        return result(Action.FINAL, attempt.failure.value, reconcile=uncertain_effects)
    if attempt.state == "completed":
        return result(Action.FINAL, "completed")
    if attempt.state != "failed":
        return result(Action.RECONCILE, "not a settled failure", reconcile=True)
    if uncertain_effects:
        return result(Action.RECONCILE, "effects require reconciliation", reconcile=True)
    if attempt.failure is not Failure.TRANSIENT:
        return result(Action.RECONCILE, "failure requires classification", reconcile=True)
    if attempt.number > policy.max_retries:
        return result(Action.FINAL, "retry budget exhausted")
    return result(Action.RETRY, "transient pre-dispatch failure")