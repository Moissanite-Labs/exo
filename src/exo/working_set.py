"""Offline working-set prototype (#625), not connected to runtime recall.

Records carry trusted caller-supplied visibility metadata. This pure selector is
not an authorization boundary, prompt builder, or source of principal authority.
Token costs are byte/4 estimates including provenance, not model-token counts.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from itertools import islice
from typing import Iterable

MAX_CANDIDATES = 256
MAX_TEXT = 6000
MAX_ADJUSTMENTS = 128


def _ids(values: Iterable[str]) -> frozenset[str]:
    values = list(islice(iter(values), MAX_ADJUSTMENTS + 1))
    if len(values) > MAX_ADJUSTMENTS or any(not isinstance(v, str) or not 1 <= len(v) <= 160 for v in values):
        raise ValueError("adjustments require at most 128 nonempty source IDs of at most 160 characters")
    return frozenset(values)


@dataclass(frozen=True)
class Record:
    id: str
    session: str
    agent: str
    text: str
    author: str = "other"
    kind: str = "message"
    visibility: str = "unknown"
    thread_kind: str = "unknown"
    private_ancestry: bool = False
    correction: bool = False
    ordinal: int = 0

    def __post_init__(self):
        for value in (self.id, self.session, self.agent, self.author):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value):
                raise ValueError("source identities must be bounded canonical identifiers")
        if not isinstance(self.text, str) or not 1 <= len(self.text) <= MAX_TEXT:
            raise ValueError("source text must contain 1-6000 characters; it is never silently truncated")
        self.text.encode("utf-8")
        if self.kind not in ("message", "summary", "working_note", "imported_message"):
            raise ValueError("source kind is unknown")
        if self.visibility not in ("unknown", "shared", "private", "protected"):
            raise ValueError("source visibility is unknown")
        if self.thread_kind not in ("unknown", "thread", "private", "subagent", "scheduled"):
            raise ValueError("source thread kind is unknown")
        if type(self.private_ancestry) is not bool or type(self.correction) is not bool or type(self.ordinal) is not int:
            raise ValueError("source flags and ordinal have invalid types")

    @property
    def fingerprint(self) -> str:
        """Content hash for inspection; this is not an authenticated ledger proof."""
        return hashlib.sha256(self.text.encode()).hexdigest()


def estimate(record: Record) -> int:
    data = "\n".join((record.id, record.session, record.author, record.kind, record.text))
    return (len(data.encode()) + 3) // 4 + 12


@dataclass(frozen=True)
class Selection:
    session: str
    agent: str
    records: tuple[Record, ...]
    estimated_tokens: int
    input_truncated: bool
    over_budget: int

    def __post_init__(self):
        records = tuple(islice(iter(self.records), 33))
        if len(records) > 32 or any(not isinstance(r, Record) for r in records):
            raise ValueError("a selection holds at most 32 records")
        object.__setattr__(self, "records", records)


@dataclass(frozen=True)
class WorkingSet:
    session: str
    agent: str
    token_budget: int = 1200
    max_items: int = 12
    pinned: frozenset[str] = field(default_factory=frozenset)
    excluded: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value)
               for value in (self.session, self.agent)):
            raise ValueError("working set requires its conversation and agent")
        if type(self.token_budget) is not int or not 1 <= self.token_budget <= 16000:
            raise ValueError("token_budget must be an integer from 1 to 16000")
        if type(self.max_items) is not int or not 1 <= self.max_items <= 32:
            raise ValueError("max_items must be an integer from 1 to 32")
        object.__setattr__(self, "pinned", _ids(self.pinned))
        object.__setattr__(self, "excluded", _ids(self.excluded))

    def adjust(self, *, pin=(), unpin=(), exclude=(), restore=()) -> WorkingSet:
        """Return a reversible local revision; exclusions win over pins at selection."""
        return replace(self, pinned=(self.pinned | _ids(pin)) - _ids(unpin),
                       excluded=(self.excluded | _ids(exclude)) - _ids(restore))

    def select(self, query: str, records: Iterable[Record], *, visible_ids=(), previous: Selection | None = None) -> Selection:
        if not isinstance(query, str) or len(query) > 4096:
            raise ValueError("query must be text of at most 4096 characters")
        if previous is not None and (previous.session, previous.agent) != (self.session, self.agent):
            raise ValueError("a previous working set belongs to another conversation")
        visible = _ids(visible_ids)
        batch = list(islice(iter(records), MAX_CANDIDATES + 1))
        by_id, conflicts = {}, set()
        for record in batch[:MAX_CANDIDATES]:
            if not isinstance(record, Record):
                raise ValueError("candidates must be provenance-bearing Record objects")
            if record.id in by_id and by_id[record.id] != record:
                conflicts.add(record.id)
            by_id[record.id] = record
        terms = set(re.findall(r"\w+", query.casefold())[:64])
        retained = {r.id for r in previous.records} if previous else set()
        ranked = []
        for record in by_id.values():
            if (record.id in conflicts or record.id in visible or record.id in self.excluded
                    or record.agent != self.agent or record.visibility != "shared"
                    or record.thread_kind != "thread" or record.private_ancestry):
                continue
            score = len(terms & set(re.findall(r"\w+", record.text.casefold())))
            if not score and record.id not in self.pinned:
                continue
            priority = record.id in self.pinned or record.correction and record.author == "principal"
            ranked.append((not priority, -score, record.id not in retained, -record.ordinal, record.id, record))
        chosen, spent, over_budget = [], 0, 0
        for *_, record in sorted(ranked):
            if len(chosen) >= self.max_items:
                break
            cost = estimate(record)
            if spent + cost > self.token_budget:
                over_budget += 1
                continue  # a large entry must not prevent smaller relevant evidence from fitting
            chosen.append(record)
            spent += cost
        return Selection(self.session, self.agent, tuple(chosen), spent, len(batch) > MAX_CANDIDATES, over_budget)
