"""Read-only projection of explicit auxiliary request observations (#878).

Not wired into request capture or session totals. Missing observations cannot be
reconstructed. The caller supplies one immutable ID per actual request attempt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from exo.core.types import Usage


@dataclass(frozen=True)
class AuxiliaryRequest:
    request_id: str
    purpose: str
    session: str | None
    route: str | None
    usage: Usage | None
    failed: bool = False


@dataclass
class AuxiliaryTotal:
    requests: int = 0
    failed_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    unknown_usage: int = 0
    unknown_prices: int = 0
    known_cost_usd: float = 0.0

    @property
    def cost_usd(self) -> float | None:
        return None if self.unknown_usage or self.unknown_prices else self.known_cost_usd


def aggregate_auxiliary(records: Iterable[AuxiliaryRequest], *, max_records: int = 10000
                        ) -> dict[tuple[str | None, str], AuxiliaryTotal]:
    """Aggregate bounded observations; unknown totals stay unknown, never zero.

    Identical duplicate IDs count once, conflicting duplicates fail visibly.
    Failed attempts with reported usage are billed too; sessionless work is kept
    under None. Main-loop calls are refused to prevent mixing/double-counting.
    """
    if type(max_records) is not int or not 1 <= max_records <= 100000:
        raise ValueError('invalid record limit')
    seen: dict[str, AuxiliaryRequest] = {}
    totals: dict[tuple[str | None, str], AuxiliaryTotal] = {}
    token_fields = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')
    for index, record in enumerate(records):
        if index >= max_records:
            raise ValueError('auxiliary record limit exceeded')
        if not record.request_id or record.purpose not in ('summary', 'aux', 'decision', 'eval', 'title', 'memory'):
            raise ValueError('explicit auxiliary request ID and purpose required')
        if record.request_id in seen:
            if seen[record.request_id] != record:
                raise ValueError('conflicting duplicate auxiliary request')
            continue
        seen[record.request_id] = record
        total = totals.setdefault((record.session, record.purpose), AuxiliaryTotal())
        total.requests += 1
        total.failed_requests += record.failed
        usage = record.usage
        if usage is None:
            total.unknown_usage += 1
            continue
        for field in token_fields:
            value = getattr(usage, field)
            if type(value) is not int or value < 0:
                raise ValueError('invalid auxiliary token count')
            setattr(total, field, getattr(total, field) + value)
        if usage.cost_usd is None:
            total.unknown_prices += 1
        elif isinstance(usage.cost_usd, bool) or not math.isfinite(usage.cost_usd) or usage.cost_usd < 0:
            raise ValueError('invalid auxiliary cost')
        else:
            total.known_cost_usd += usage.cost_usd
    return totals
