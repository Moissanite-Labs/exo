"""Context assembly under a token budget.

v1 ranks nodes with BM25 (PrincipalStore.find) and packs them with PrincipalStore.ctx.
The seam for later work is the `Ranker` protocol: an embedding ranker, or one that expands
the top hits along graph edges, only has to return [(node_id, score)] best first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .store import PrincipalStore


class Ranker(Protocol):
    def rank(self, store: PrincipalStore, query: str) -> list[tuple[str, float]]: ...


class BM25Ranker:
    def rank(self, store: PrincipalStore, query: str) -> list[tuple[str, float]]:
        return store.find(query)


DEFAULT_RANKER: Ranker = BM25Ranker()


@dataclass
class Assembly:
    text: str
    node_ids: list[str]
    tokens: int


def assemble(store: PrincipalStore, query: str, budget: int = 1500, ranker: Ranker | None = None) -> Assembly:
    """Return context text for `query` under `budget` tokens, plus the node ids it used."""
    ranked = (ranker or DEFAULT_RANKER).rank(store, query)
    ctx = store.ctx(query, budget=budget, ranked=ranked)
    return Assembly(text=ctx.text, node_ids=ctx.ids, tokens=ctx.tokens)
