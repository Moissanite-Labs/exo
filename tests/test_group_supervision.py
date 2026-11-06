"""Offline policy tests; these do not assert executor recovery is integrated."""
from dataclasses import FrozenInstanceError, replace

import pytest

from exo.core.group_supervision import (
    Action, Attempt, Effects, Failure, SupervisionPolicy, TaskIdentity, decide,
)


IDENTITY = TaskIdentity("plan-1", "task-1", "builder")
SAFE = Attempt(IDENTITY, 1, "failed", Failure.TRANSIENT, Effects.NOT_DISPATCHED)


def test_default_off():
    answer = decide(SAFE)
    assert answer.action is Action.FINAL
    assert answer.next_attempt is None


@pytest.mark.parametrize("budget", range(11))
def test_lifetime_budget_and_stable_identity(budget):
    policy = SupervisionPolicy(budget)
    attempt = SAFE
    for number in range(1, budget + 1):
        answer = decide(attempt, policy)
        assert answer.action is Action.RETRY
        assert answer.identity is IDENTITY
        assert answer.next_attempt == number + 1
        assert not answer.reconciliation_required
        attempt = replace(attempt, number=answer.next_attempt)
    assert decide(attempt, policy).action is Action.FINAL
    # A new policy instance cannot reset a persisted attempt count.
    assert decide(attempt, SupervisionPolicy(budget)).next_attempt is None


@pytest.mark.parametrize("failure", [Failure.CANCELLED, Failure.DENIED,
                                    Failure.SPEND_LIMIT, Failure.PERMANENT])
@pytest.mark.parametrize("effects", list(Effects))
def test_final_failures_never_retry(failure, effects):
    answer = decide(replace(SAFE, failure=failure, effects=effects), SupervisionPolicy(10))
    assert answer.action is Action.FINAL
    assert answer.next_attempt is None
    assert answer.reconciliation_required == (effects is not Effects.NOT_DISPATCHED)


@pytest.mark.parametrize("state", ["stopped", "cancelled"])
@pytest.mark.parametrize("effects", list(Effects))
def test_cancellation_wins(state, effects):
    answer = decide(replace(SAFE, state=state, effects=effects), SupervisionPolicy(10))
    assert answer.action is Action.FINAL
    assert answer.reason == "cancelled"
    assert answer.next_attempt is None


def test_stop_wins_over_retry_and_unknown_state():
    for attempt in [SAFE, replace(SAFE, state="unknown")]:
        assert decide(attempt, SupervisionPolicy(10), stop_requested=True).action is Action.FINAL


@pytest.mark.parametrize("effects", list(Effects))
def test_completed_is_never_replayed(effects):
    assert decide(replace(SAFE, state="completed", effects=effects),
                  SupervisionPolicy(10)).action is Action.FINAL


@pytest.mark.parametrize("effects", [Effects.UNKNOWN, Effects.EXTERNAL])
def test_effects_require_reconciliation_even_with_budget_disabled(effects):
    for budget in [0, 10]:
        answer = decide(replace(SAFE, effects=effects), SupervisionPolicy(budget))
        assert answer.action is Action.RECONCILE
        assert answer.reconciliation_required
        assert answer.next_attempt is None


@pytest.mark.parametrize("state", ["pending", "running", "future_state", "", "FAILED"])
def test_unsettled_or_unknown_states_fail_closed(state):
    assert decide(replace(SAFE, state=state), SupervisionPolicy(10)).action is Action.RECONCILE


def test_unknown_failure_and_missing_evidence_fail_closed():
    assert decide(replace(SAFE, failure=Failure.UNKNOWN),
                  SupervisionPolicy(10)).action is Action.RECONCILE
    assert decide(Attempt(IDENTITY, 1, "failed"),
                  SupervisionPolicy(10)).action is Action.RECONCILE


@pytest.mark.parametrize("value", [-1, 11, True, False, 1.0, "1", None])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        SupervisionPolicy(value)


@pytest.mark.parametrize("field,value", [("number", 0), ("number", True), ("number", 1.0),
                                         ("identity", None), ("failure", "transient"),
                                         ("effects", "not_dispatched"), ("state", None)])
def test_invalid_attempt(field, value):
    with pytest.raises(ValueError):
        replace(SAFE, **{field: value})


@pytest.mark.parametrize("value", ["", "  ", None, 1])
def test_invalid_identity(value):
    with pytest.raises(ValueError):
        TaskIdentity(value, "task", "profile")


def test_invalid_stop_flag_and_arguments():
    with pytest.raises(ValueError):
        decide(SAFE, stop_requested="false")
    with pytest.raises(ValueError):
        decide(SAFE, None)
    with pytest.raises(ValueError):
        decide(None)


def test_pure_repeatable_immutable_and_task_local():
    before = repr(SAFE)
    assert decide(SAFE, SupervisionPolicy(1)) == decide(SAFE, SupervisionPolicy(1))
    other = replace(SAFE, identity=TaskIdentity("plan-1", "sibling", "reviewer"))
    assert decide(other, SupervisionPolicy(1)).identity == other.identity
    assert repr(SAFE) == before
    with pytest.raises(FrozenInstanceError):
        SAFE.number = 2