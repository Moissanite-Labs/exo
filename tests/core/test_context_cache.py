"""Eviction bounds memory without truncating context or changing skipped-parent rendering."""
from types import SimpleNamespace

from exo.core.context import TreeContext
from exo.core.text_cache import TextCache


def test_lru_hit_and_replacement_accounting():
    cache = TextCache(2, 8)
    cache.put("a", "aaa")
    cache.put("b", "bbb")
    assert cache.get("a") == "aaa"
    cache.put("c", "cc")
    assert cache.get("b") is None
    assert cache.characters == 5
    cache.put("a", "a")
    assert cache.characters == 3
    cache.put("a", "x" * 9)
    assert cache.get("a") is None and cache.characters == 2


def test_character_budget_and_empty_strings():
    cache = TextCache(10, 5)
    for i in range(1000):
        cache.put(str(i), "xy")
        assert cache.characters <= 5 and len(cache.values) <= 2
    cache.put("empty", "")
    assert cache.get("empty") == ""
    cache = TextCache(0, 5)
    cache.put("a", "a")
    assert cache.characters == 0 and cache.get("a") is None


def test_tree_rebuilds_evicted_text_without_truncation(monkeypatch):
    tree = TreeContext(None, None)
    tree._verbatim_cache = TextCache(2, 8)
    reads = []
    monkeypatch.setattr(tree, "_verbatim", lambda node: (reads.append(node.id), node.text)[1])
    nodes = [SimpleNamespace(id=str(i), level=0, status="skipped", text="text") for i in range(5)]
    assert [tree._body(n) for n in nodes] == ["text"] * 5
    assert tree._body(nodes[-1]) == "text" and len(reads) == 5
    assert tree._body(nodes[0]) == "text" and len(reads) == 6
    huge = SimpleNamespace(id="huge", level=0, status="skipped", text="x" * 100)
    assert tree._body(huge) == "x" * 100
    assert tree._verbatim_cache.get("huge") is None


def test_recursive_parent_eviction_keeps_exact_body(monkeypatch):
    tree = TreeContext(None, None)
    tree._verbatim_cache = TextCache(1, 4)
    leaves = [SimpleNamespace(id=str(i), level=0, status="skipped", text=x) for i, x in enumerate(["left", "right"])]
    parent = SimpleNamespace(id="p", level=1, status="skipped")
    monkeypatch.setattr(tree, "_children", lambda node: leaves)
    monkeypatch.setattr(tree, "_verbatim", lambda node: node.text)
    assert tree._body(parent) == "left\n\nright"
    assert tree._body(parent) == "left\n\nright"
    assert tree._verbatim_cache.characters <= 4
