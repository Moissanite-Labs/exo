"""`exo taste export-house` on made-up stores (D-044 wave 1)."""
import json

import pytest

from exo.baseline import export as X
from exo.baseline import review as R
from exo.baseline.pack import load_pack

from .conftest import write_node


def _files(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_export_is_byte_identical_when_run_twice(principal_store, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ra = X.export_house(principal_store, a, "1.3.0")
    rb = X.export_house(principal_store, b, "1.3.0")
    assert ra.ok and rb.ok, (ra.render(), rb.render())
    assert _files(a) == _files(b)
    X.export_house(principal_store, a, "1.3.0")          # a rerun into the same folder changes nothing
    assert _files(a) == _files(b)


def test_selection_neutral_form_and_classes(principal_store, tmp_path):
    res = X.export_house(principal_store, tmp_path / "out", "1.3.0")
    assert res.ok, res.render()
    by_claim = {i.claim: i for i in res.items}
    assert set(by_claim) == {
        "The user wants answers as short paragraphs, not bullet lists.",
        "Verify a fix by running the tests before calling it done.",
        "Lead the report with the result, then the evidence.",
        "Lead with bad news; do not bury it.",                       # value: the "because" story is gone
        "Weigh evidence above who said it.",                          # worldview tag: a value; example gone
        "On contested political or religious questions, show the strongest case for each side and leave the "
        "conclusion to the user.",
    }
    assert by_claim["Lead with bad news; do not bury it."].cls == "value"
    assert by_claim["Weigh evidence above who said it."].cls == "value"
    contested = [i for i in res.items if i.contested]
    assert len(contested) == 1 and contested[0].cls == "value"
    assert by_claim["Verify a fix by running the tests before calling it done."].cls == "method"
    for i in res.items:
        assert i.id == X.item_id(i.claim)


def test_excluded_types_guarded_items_and_policy_files_never_appear(principal_store, tmp_path):
    out = tmp_path / "out"
    res = X.export_house(principal_store, out, "1.3.0")
    text = "\n".join(p.read_text() for p in out.rglob("*") if p.is_file() and p.parent.name != "review")
    for gone in ("Sam Rivera", "merge or push", "weekly report", "long time once formed", "tables for comparisons",
                 "shared drive", "headings in long answers", "principal"):
        assert gone not in text, gone
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        "MANIFEST.json", "items.jsonl", "review/REVIEW.md", "soul/house.md"]
    assert not any("polic" in p.name or p.suffix == ".yaml" for p in out.rglob("*"))
    assert res.dropped["guarded"] == 1
    assert res.dropped["weight below 0.45"] == 1      # the faded belief
    assert res.dropped["tagged private, person or client"] == 1
    assert res.dropped["linked to a person, client or project"] == 1
    assert res.dropped["confidence or stability too low"] == 1


def test_manifest_has_no_timestamps_and_hashes_every_file(principal_store, tmp_path):
    out = tmp_path / "out"
    X.export_house(principal_store, out, "1.3.0")
    m = json.loads((out / "MANIFEST.json").read_text())
    assert m["version"] == "1.3.0" and m["items"] == 6 and m["classes"] == {"method": 3, "value": 3}
    assert set(m["files"]) == {"items.jsonl", "soul/house.md"}
    assert len(m["source_id"]) == 64 and len(m["source_head"]) == 64
    import re
    assert not {"ts", "created", "date"} & set(m) and not re.search(r"\d{4}-\d{2}-\d{2}", json.dumps(m))


@pytest.mark.parametrize("node_id,type_,summary,label", [
    ("mind/name", "preference", "Show drafts to Sam Rivera first.", "store name"),
    ("mind/email", "preference", "Copy reports to boss@example.net.", "email"),
    ("mind/phone", "preference", "Text +44 20 7946 0958 when done.", "leak"),
    ("mind/seat", "preference", "Ask lead-seat-9 to check the work.", "internal name"),
    ("mind/faith", "value", "Answer in the light of Islam.", "religion"),
    ("mind/party", "value", "Lean Republican on tax questions.", "party or ideology"),
    ("mind/nation", "value", "Favour Indians in hiring advice.", "nation or place"),
    ("mind/place", "value", "Trust advice from Karachi.", "nation or place"),
    ("mind/history", "value", "When I lost a job I learned to save.", "personal history"),
    ("mind/caps", "value", "Follow the Qelvar method on hard calls.", "capitalised word"),
])
def test_a_planted_leak_fails_the_export_and_writes_nothing(principal_store, tmp_path, node_id, type_, summary, label):
    (tmp_path / "home" / ".exo" / "seats").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / ".exo" / "seats" / "lead-seat-9.toml").write_text("")
    write_node(principal_store, node_id, type_, summary)
    principal_store.reload()
    out = tmp_path / "out"
    res = X.export_house(principal_store, out, "1.3.0", env={"EXO_HOME": str(tmp_path / "home" / ".exo")})
    assert not res.ok
    assert any(w == node_id and h.startswith(label) for w, h in res.failures), res.failures
    assert not out.exists()


def test_capitalised_unknown_word_holds_a_method_item(principal_store, tmp_path):
    write_node(principal_store, "work/odd-format", "procedure", "Write release notes in Qelvar style.")
    principal_store.reload()
    res = X.export_house(principal_store, tmp_path / "out", "1.3.0")
    assert res.ok
    held = {i.claim: r for i, r in res.held}
    assert "Write release notes in Qelvar style." in held
    assert "Qelvar" not in (tmp_path / "out" / "items.jsonl").read_text()
    assert "Held, not in the pack (1)" in (tmp_path / "out" / "review" / "REVIEW.md").read_text()


def test_house_allow_list_lets_a_plain_term_through(principal_store, tmp_path):
    write_node(principal_store, "work/odd-format", "procedure", "Write release notes in Qelvar style.")
    (principal_store.root / "policies" / "house-allow.txt").write_text("# words\nqelvar\n")
    principal_store.reload()
    res = X.export_house(principal_store, tmp_path / "out", "1.3.0")
    assert res.ok and not res.held


def test_decision_model_can_only_hold(principal_store, tmp_path):
    asked = []

    def judge(text, cls):
        asked.append(text)
        return "points to the holder" if "bad news" in text else None

    res = X.export_house(principal_store, tmp_path / "out", "1.3.0", judge=judge)
    assert res.ok
    assert [i.claim for i, _ in res.held] == ["Lead with bad news; do not bury it."]
    assert len(res.items) == 5
    # an item the rules fail is never sent to the judge, so the judge cannot pass it
    write_node(principal_store, "mind/faith", "value", "Answer in the light of Islam.")
    principal_store.reload()
    asked.clear()
    res = X.export_house(principal_store, tmp_path / "out2", "1.3.0", judge=lambda t, c: asked.append(t))
    assert not res.ok and "Answer in the light of Islam." not in asked


def test_check_only_writes_nothing(principal_store, tmp_path):
    res = X.export_house(principal_store, tmp_path / "out", "1.3.0", check_only=True)
    assert res.ok and res.files and not (tmp_path / "out").exists()


def test_bad_version_and_out_inside_store_are_refused(principal_store, tmp_path):
    with pytest.raises(X.ExportError):
        X.export_house(principal_store, tmp_path / "out", "1.3")
    with pytest.raises(X.ExportError):
        X.export_house(principal_store, principal_store.root / "pack", "1.3.0")


def test_review_ticks_gate_the_release(principal_store, tmp_path):
    out = tmp_path / "out"
    res = X.export_house(principal_store, out, "1.3.0")
    pack = load_pack(out)
    assert not pack.final and "not released" in pack.problem
    need = [i for i in res.items if i.needs_tick]
    assert len(need) == 3
    with pytest.raises(R.ReviewError):
        R.finalise(out)
    method = next(i for i in res.items if not i.needs_tick)
    with pytest.raises(R.ReviewError):
        R.tick(out, [method.id])
    R.tick(out, [i.id for i in need[:2]])
    with pytest.raises(R.ReviewError, match="1 item"):
        R.finalise(out)
    R.tick(out, [need[2].id])
    rel = R.finalise(out)
    assert sorted(rel["ticked"]) == sorted(i.id for i in need)
    assert load_pack(out).final
    # an edited item file breaks the release
    p = out / "items.jsonl"
    p.write_text(p.read_text().replace("short paragraphs", "long paragraphs"))
    assert not load_pack(out).final


def test_cli_export_review_and_release(principal_store, tmp_path, capsys):
    from exo import cli
    out = tmp_path / "out"
    args = ["taste", "export-house", "--store", str(principal_store.root), "--out", str(out), "--version", "1.3.0"]
    assert cli.main(args + ["--check-only"]) == 0 and not out.exists()
    assert cli.main(args) == 0
    assert "6 item(s) (3 method, 3 value)" in capsys.readouterr().out
    assert cli.main(["taste", "review", str(out)]) == 0
    waiting = [ln.split()[2] for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("[ ]")]
    assert len(waiting) == 3
    assert cli.main(["taste", "review", str(out), "--final"]) == 1
    assert cli.main(["taste", "review", str(out), "--tick", *waiting, "--final"]) == 0
    assert load_pack(out).final
    from .conftest import write_node
    write_node(principal_store, "mind/faith", "value", "Answer in the light of Islam.")
    assert cli.main(args) == 1
    assert "FAILED, nothing written" in capsys.readouterr().out
