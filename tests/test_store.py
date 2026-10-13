import json
import os
import stat

import pytest

from exo import privacy
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
