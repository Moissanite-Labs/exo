import asyncio

import pytest

from exo.core.models.auxiliary_usage import AuxiliaryRequest, aggregate_auxiliary
from exo.core.models.fake import FakeProvider
from exo.core.types import Usage


def test_real_fake_usage_is_aggregated_once_per_request_and_separate_from_main():
    async def sample():
        events = [e async for e in FakeProvider(['summary']).stream([], [])]
        usage = next(e.usage for e in events if e.kind == 'usage')
        return AuxiliaryRequest('one', 'summary', 's_one', 'fake:x', usage)
    request = asyncio.run(sample())
    rows = aggregate_auxiliary([request, request])
    row = rows[('s_one', 'summary')]
    assert row.requests == 1 and row.output_tokens == len('summary') // 4
    assert row.cost_usd is None and row.unknown_prices == 1


def test_known_unpriced_failed_and_unowned_requests_preserve_unknowns():
    rows = aggregate_auxiliary([
        AuxiliaryRequest('a', 'summary', 's', 'fake:x', Usage(input_tokens=10, cost_usd=.2)),
        AuxiliaryRequest('b', 'summary', 's', 'fake:x', Usage(input_tokens=5)),
        AuxiliaryRequest('c', 'aux', None, None, None, failed=True),
    ])
    assert rows[('s', 'summary')].input_tokens == 15
    assert rows[('s', 'summary')].cost_usd is None
    assert rows[('s', 'summary')].known_cost_usd == .2
    assert rows[(None, 'aux')].unknown_usage == 1
    assert rows[(None, 'aux')].failed_requests == 1


def test_attempt_ids_keep_retries_and_costs_distinct():
    rows = aggregate_auxiliary([
        AuxiliaryRequest('attempt1', 'aux', 's', 'fake:x', Usage(cost_usd=.1), failed=True),
        AuxiliaryRequest('attempt2', 'aux', 's', 'fake:y', Usage(cost_usd=.2)),
    ])
    assert rows[('s', 'aux')].requests == 2
    assert rows[('s', 'aux')].cost_usd == pytest.approx(.3)


def test_conflicting_duplicate_main_invalid_usage_and_bounds_are_refused():
    one = AuxiliaryRequest('one', 'aux', 's', 'fake:x', Usage(input_tokens=1))
    with pytest.raises(ValueError, match='conflicting'):
        aggregate_auxiliary([one, AuxiliaryRequest('one', 'aux', 's', 'fake:x', Usage(input_tokens=2))])
    with pytest.raises(ValueError):
        aggregate_auxiliary([AuxiliaryRequest('main', 'main', 's', None, None)])
    with pytest.raises(ValueError):
        aggregate_auxiliary([AuxiliaryRequest('bad', 'aux', 's', None, Usage(cost_usd=float('nan')))])
    with pytest.raises(ValueError, match='limit'):
        aggregate_auxiliary([one, one], max_records=1)
