"""Acceptance: run the store against a real, populated principal store.

Set EXO_ACCEPTANCE_STORE to a store directory. The default is a store at ~/hafiz if one
exists. The store is copied to a temp dir first, without corpus/private, and never modified.
The expected ids below belong to that example store; adjust them for another one.
"""
import os
import shutil
from pathlib import Path

import pytest

from exo.retrieve import assemble
from exo.store import PrincipalStore

SRC = Path(os.environ.get("EXO_ACCEPTANCE_STORE") or Path.home() / "hafiz")

pytestmark = pytest.mark.skipif(not (SRC / "nodes").is_dir(), reason=f"no example store at {SRC}")

CASES = [
    ("external posting github comment", "relationship/external-posting", 2),
    ("design ui taste", "mind/design-taste", 1),
    ("decide whether to act or ask approval", "relationship/autonomy-and-approval", 2),
]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    dst = tmp_path_factory.mktemp("example") / "store"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".git", "private", "_drafts"))
    assert not (dst / "corpus" / "private").exists()
    return PrincipalStore(dst)


def test_lint_clean(store):
    assert store.lint().errors == []


@pytest.mark.parametrize("query,expected,top", CASES)
def test_find(store, query, expected, top):
    ids = [i for i, _ in store.find(query, n=top)]
    assert expected in ids


@pytest.mark.parametrize("query,expected,top", CASES)
def test_ctx(store, query, expected, top):
    ctx = store.ctx(query, budget=1500)
    assert expected in ctx.ids[:top]
    assert len(ctx.text) <= 1500 * 4
    out = assemble(store, query, budget=1500)
    assert out.node_ids == ctx.ids
