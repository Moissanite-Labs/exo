import json
import re
import tomllib
from pathlib import Path

import pytest

from exo import upstream as up

REPO = Path(__file__).resolve().parents[2]

HEAD = '''[upstream.hermes]
repo = "https://example.invalid/hermes"
license = "MIT"
'''


def write_root(tmp_path: Path, body: str = "", files=("src/x.py",)) -> Path:
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Lifted from hermes-agent: a.py\n")
    (tmp_path / up.PROVENANCE).write_text("# keep this comment\n" + HEAD + body)
    return tmp_path


MOD_X = '''
[[module]]
exo = "src/x.py"
mode = "lift"
upstream = ["a.py"]
commit = "aaaaaaa"
reconciled = "2026-10-01"
changes = "trimmed"
'''


def test_repo_provenance_is_consistent():
    prov = up.load(REPO / up.PROVENANCE)
    assert "hermes" in prov.sources
    assert up.validate(prov, REPO) == []


def test_slots_entry_matches_its_header():
    prov = up.load(REPO / up.PROVENANCE)
    m = prov.module("src/exo/core/slots.py")
    assert m is not None and m.mode == "lift"
    assert m.upstream == ["agent/memory_provider.py", "agent/context_engine.py"]
    head = (REPO / m.exo).read_text().splitlines()[:5]
    assert up.HEADER_RE.search("\n".join(head))
    assert any(m.commit in line for line in head)


@pytest.mark.parametrize("bad, msg", [
    ('mode = "copy"', "mode"),
    ('commit = "not-a-sha"', "hex sha"),
    ('reconciled = "yesterday"', "YYYY-MM-DD"),
    ('colour = "red"', "unknown key"),
    ('upstream = []', "non-empty"),
])
def test_schema_rejects(bad, msg):
    key = bad.split(" ")[0]
    lines = [ln for ln in MOD_X.strip().splitlines() if not ln.startswith(key + " ")]
    data = tomllib.loads(HEAD + "\n".join(lines + [bad]) + "\n")
    with pytest.raises(up.ProvenanceError, match=msg):
        up.parse(data)


def test_schema_duplicates_and_sources():
    with pytest.raises(up.ProvenanceError, match="duplicate"):
        up.parse(tomllib.loads(HEAD + MOD_X + MOD_X))
    with pytest.raises(up.ProvenanceError, match="no \\[upstream"):
        up.parse(tomllib.loads(MOD_X))


def test_validate_finds_missing_and_unrecorded(tmp_path):
    root = write_root(tmp_path, MOD_X.replace("src/x.py", "src/gone.py"))
    probs = up.validate(up.load(root / up.PROVENANCE), root)
    assert any("gone.py: path does not exist" in p for p in probs)
    assert any(p.startswith("src/x.py: has a lifted header") for p in probs)
    (root / "PROVENANCE.w1.part.toml").write_text(MOD_X)
    probs = up.validate(up.load(root / up.PROVENANCE), root)
    assert not any(p.startswith("src/x.py") for p in probs)


def test_merge_parts(tmp_path):
    root = write_root(tmp_path, files=("src/x.py", "src/y.py"))
    (root / "PROVENANCE.a.part.toml").write_text(MOD_X)
    (root / "src/PROVENANCE.part.toml").write_text(
        MOD_X.replace("src/x.py", "src/y.py") + '\n[[watch]]\nupstream = ["plugins/**"]\nwhy = "new"\n')
    parts = up.merge_parts(root, remove=True)
    assert [p.relative_to(root).as_posix() for p in parts] == ["PROVENANCE.a.part.toml", "src/PROVENANCE.part.toml"]
    text = (root / up.PROVENANCE).read_text()
    assert text.startswith("# keep this comment")
    prov = up.load(root / up.PROVENANCE)
    assert [m.exo for m in prov.modules] == ["src/x.py", "src/y.py"] and prov.watch[0].why == "new"
    assert up.validate(prov, root) == [] and not list(root.glob(up.PART_GLOB))


def test_repo_parts_merge_cleanly(tmp_path):
    """Dry run of the lead's merge on a copy: the repo's pending parts fold in without conflict."""
    import shutil
    shutil.copy(REPO / up.PROVENANCE, tmp_path / up.PROVENANCE)
    parts = up.find_parts(REPO)
    for p in parts:
        dst = tmp_path / p.relative_to(REPO)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, dst)
    assert len(up.merge_parts(tmp_path)) == len(parts)
    prov = up.load(tmp_path / up.PROVENANCE)
    assert {m.exo for m in prov.modules} >= set(up.part_modules(REPO))


def test_merge_is_all_or_nothing(tmp_path):
    root = write_root(tmp_path, MOD_X)
    before = (root / up.PROVENANCE).read_text()
    (root / "PROVENANCE.a.part.toml").write_text(MOD_X.replace("src/x.py", "src/z.py"))
    (root / "PROVENANCE.b.part.toml").write_text(MOD_X)  # duplicate of the main file
    with pytest.raises(up.ProvenanceError, match="already recorded"):
        up.merge_parts(root)
    assert (root / up.PROVENANCE).read_text() == before


def test_mark_port_and_refusal(tmp_path):
    root = write_root(tmp_path, MOD_X + '\n[[watch]]\nupstream = ["b/**"]\n')
    rec = up.mark(root, "src/x.py", "bbbbbbb", today="2026-10-12")
    assert rec["from"] == "aaaaaaa" and rec["outcome"] == "ported"
    rec = up.mark(root, "src/x.py", "ccccccc", rejected="not needed", today="2026-10-13")
    text = (root / up.PROVENANCE).read_text()
    assert text.startswith("# keep this comment") and 'changes = "trimmed"' in text
    m = up.load(root / up.PROVENANCE).module("src/x.py")
    assert (m.commit, m.reconciled, m.outcome) == ("ccccccc", "2026-10-13", "refused: not needed")
    marks = [json.loads(ln) for ln in (root / "upstream/MARKS.jsonl").read_text().splitlines()]
    assert [r["to"] for r in marks] == ["bbbbbbb", "ccccccc"]
    with pytest.raises(up.ProvenanceError):
        up.mark(root, "src/nope.py", "ddddddd")
    with pytest.raises(up.ProvenanceError):
        up.mark(root, "src/x.py", "zzz")


def test_no_em_dashes_in_owned_files():
    owned = ["src/exo/core/plugins.py", "src/exo/core/slots.py", "src/exo/core/hooks.py", "src/exo/upstream.py"]
    for f in owned:
        assert not re.search(chr(0x2014), (REPO / f).read_text()), f
