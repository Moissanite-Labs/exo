import json
import os
import stat
import weakref

import pytest

from exo import privacy
from exo import store as store_mod
from exo.retrieve import assemble
from exo.store import PrincipalStore, parse_text, principal_root, template_dir

DENY = privacy.deny_regex()  # from an untracked file; None when absent


def test_template_lints_clean(template_store):
    report = template_store.lint()
    assert report.errors == []
    assert report.warnings == []
    assert report.nodes == 5
    assert all(n["confidence"] == "low" for n in template_store.nodes.values())


def test_template_is_generic():
    """D-004: the shipped template names no real person, account or path."""
    for d, _, files in os.walk(template_dir()):
        for f in files:
            text = open(os.path.join(d, f), encoding="utf-8").read()
            assert not privacy.generic_findings(text), f"{f} has a home path or email"
            if DENY:
                assert not DENY.search(text), f"{f} mentions principal data"


def test_create_layout(template_store):
    root = template_store.root
    for rel in ("INDEX.md", "PRINCIPAL.md", "policies/gates.yaml", "policies/posting.md", "policies/budget.yaml",
                "policies/interruptions.yaml", "method/SCHEMA.md", "method/PRIVACY.md", ".gitignore"):
        assert (root / rel).is_file(), rel
    assert stat.S_IMODE(os.stat(root / "corpus" / "private").st_mode) == 0o700
    assert "corpus/private/" in (root / ".gitignore").read_text()
    assert "`person/identity`" in (root / "INDEX.md").read_text()
    with pytest.raises(FileExistsError):
        PrincipalStore.create(root)


def test_default_root(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "h"))
    assert principal_root("someone") == tmp_path / "h" / "principals" / "someone"
    monkeypatch.delenv("EXO_HOME")
    assert str(principal_root("x")).endswith(os.path.join(".exo", "principals", "x"))
    with pytest.raises(ValueError):
        principal_root("../escape")


def test_parser_handles_flow_and_block_lists():
    text = """---
id: a/b
title: "T: with colon"
links:
  - {to: c/d, rel: "see-also"}
sources:
  - kind: observed
    ref: file:x
tags: [one, two]
valid_until:
---
body here
"""
    n = parse_text(text)
    assert n["title"] == "T: with colon"
    assert n["links"] == [{"to": "c/d", "rel": "see-also"}]
    assert n["sources"] == [{"kind": "observed", "ref": "file:x"}]
    assert n["tags"] == ["one", "two"]
    assert n["valid_until"] is None
    assert n["_body"] == "body here"


def test_find_get_links(template_store):
    top = template_store.find("report decisions format", n=1)
    assert top[0][0] == "relationship/reporting"
    assert template_store.get("person/identity")["type"] == "fact"
    links = template_store.links("person/identity")
    assert ("see-also", "work/projects") in links["out"]
    assert ("applies-to", "systems/environment") in links["in"]


@pytest.mark.parametrize("budget", [40, 100, 250, 1500])
def test_ctx_budget_respected(template_store, budget):
    ctx = template_store.ctx("principal example node", budget=budget)
    assert len(ctx.text) <= budget * 4
    assert set(ctx.full) <= set(ctx.ids)


def test_ctx_prefers_full_bodies_with_room(template_store):
    ctx = template_store.ctx("projects status", budget=3000)
    assert ctx.ids[0] == "work/projects" and "work/projects" in ctx.full


def test_assemble_returns_ids(template_store):
    out = assemble(template_store, "machines accounts tools", budget=400)
    assert out.node_ids and out.node_ids[0] == "systems/environment"
    assert len(out.text) <= 400 * 4


def test_lint_catches_problems(template_store):
    root = template_store.root
    bad = (root / "nodes/work/projects.md").read_text()
    bad = bad.replace("type: entity", "type: gadget").replace("systems/environment", "systems/nowhere")
    bad += "\napi_key = abcdefghijklmnopqrstuvwxyz\n"
    (root / "nodes/work/projects.md").write_text(bad)
    (root / "nodes/work/lonely.md").write_text("---\nid: work/elsewhere\ntitle: x\n---\nbody\n")
    template_store.reload()
    report = template_store.lint()
    joined = "\n".join(report.errors)
    assert "bad type gadget" in joined
    assert "dangling link systems/nowhere" in joined
    assert "possible leak" in joined
    assert "id does not match path" in joined
    assert "work/elsewhere: missing summary" in joined
    assert any("orphan" in w for w in report.warnings)


def test_graph_and_index(template_store):
    g = template_store.graph()
    assert len(g["nodes"]) == 5 and len(g["edges"]) == 7
    saved = json.loads((template_store.root / "graph" / "graph.json").read_text())
    assert saved == g
    assert template_store.index() == 5


NODE = ("---\nid: work/zebra-deploys\ntitle: Zebra deploys\ntype: rule\ndomain: work\n"
        "summary: Zebra deploys go out on Fridays only.\nload_when: zebra deploy\nconfidence: high\n"
        "stability: durable\nvalid_from: 2026-01-01\nsources: [test]\nlinks: []\n---\n{body}\n")


def test_refresh_sees_nodes_another_store_wrote(template_store):
    """A long-lived store (exo-memory's, the learner's) reads nodes once. `refresh` picks up what another store
    object or process added, edited or removed since; `nodes` alone stays the cached read."""
    other = PrincipalStore(template_store.root)
    assert other.refresh() is True  # nothing read yet
    assert other.refresh() is False and template_store.refresh() is False  # nothing changed
    path = other.nodes_dir / "work" / "zebra-deploys.md"
    path.write_text(NODE.format(body="Zebra deploys go out on Fridays only."))
    assert "work/zebra-deploys" not in template_store.nodes  # cached
    assert template_store.refresh() is True and "work/zebra-deploys" in template_store.nodes
    assert template_store.find("zebra deploy")[0][0] == "work/zebra-deploys"
    assert template_store.refresh() is False
    path.write_text(NODE.format(body="Zebra deploys moved to Tuesdays, after the freeze."))
    assert template_store.refresh() is True
    assert "Tuesdays" in template_store.render("work/zebra-deploys", body_only=True)
    path.unlink()
    assert template_store.refresh() is True and "work/zebra-deploys" not in template_store.nodes
    template_store.reload()  # a direct reload also counts as the last read
    assert template_store.refresh() is False


QUERIES = ["report decisions format", "machines accounts tools", "projects status", "principal example node",
           "zebra deploy", "tuesdays freeze", "identity"]


def counting_tokens(monkeypatch) -> list[str]:
    """Record every text `find` tokenizes: the query, and each node it builds a table row for."""
    seen: list[str] = []
    real = store_mod.tokens
    monkeypatch.setattr(store_mod, "tokens", lambda s: seen.append(s) or real(s))
    return seen


def test_find_tokenizes_each_node_once_across_calls(template_store, monkeypatch):
    """#254: `find` built every node's BM25 counts again on each call. The tables are now kept per node set, so
    100 queries tokenize each node once and otherwise only the query."""
    store = PrincipalStore(template_store.root)
    seen = counting_tokens(monkeypatch)
    for k in range(100):
        store.find(QUERIES[k % len(QUERIES)])
    assert len(seen) == len(store.nodes) + 100
    store.ctx("projects status")
    assemble(store, "machines accounts tools", budget=400)  # ctx and the default ranker share the same tables
    assert len(seen) == len(store.nodes) + 102


def test_find_cached_ranking_matches_a_fresh_store(template_store):
    """Kept tables give the same scores and order as tables built from scratch, call after call."""
    for _ in range(3):
        for q in QUERIES:
            assert template_store.find(q) == PrincipalStore(template_store.root).find(q)
    assert template_store.find("report decisions format", n=1)[0][0] == "relationship/reporting"


def test_reload_frees_the_old_tables_at_once(template_store):
    """A reload drops the kept tables, so they are not held until the next `find` (#442 review). `find` checks the
    node set it reads, so a reload during a `find` is still safe."""
    template_store.find("projects status")
    counts = weakref.ref(template_store._bm25[2])  # how many nodes hold each word
    template_store.reload()
    assert counts() is None
    assert template_store.find("projects status") == PrincipalStore(template_store.root).find("projects status")


def test_find_follows_every_change_to_the_node_set(template_store, monkeypatch):
    """`find` ranks exactly the nodes `nodes` returns: a reload, or a refresh that saw a change, rebuilds the
    tables, and a refresh that saw nothing keeps them."""
    other = PrincipalStore(template_store.root)
    assert template_store.find("zebra deploy") == []
    path = other.nodes_dir / "work" / "zebra-deploys.md"
    path.write_text(NODE.format(body="Zebra deploys go out on Fridays only."))
    assert template_store.find("zebra deploy") == []  # not read yet, as with `nodes`
    assert template_store.refresh() is True
    assert template_store.find("zebra deploy")[0][0] == "work/zebra-deploys"
    seen = counting_tokens(monkeypatch)
    assert template_store.refresh() is False
    assert template_store.find("zebra deploy")[0][0] == "work/zebra-deploys" and len(seen) == 1  # the query only
    path.write_text(NODE.format(body="Zebra deploys moved to Tuesdays, after the freeze."))
    assert template_store.find("tuesdays freeze") == []
    template_store.reload()
    assert template_store.find("tuesdays freeze")[0][0] == "work/zebra-deploys"
    assert len(seen) == 3 + len(template_store.nodes)  # three queries, one rebuild
    for q in QUERIES:
        assert template_store.find(q) == PrincipalStore(template_store.root).find(q)
    path.unlink()
    assert template_store.refresh() is True and template_store.find("zebra deploy") == []
