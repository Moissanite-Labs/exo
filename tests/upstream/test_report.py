import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from exo import upstream as up

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, **ENV}, check=True)
    return r.stdout.strip()


def commit(repo: Path, msg: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


MEM = "class MemoryProvider:\n    def prefetch(self):\n        return ''\n\n    def other(self):\n        return 1\n"
ENG = "class ContextEngine:\n    def compress(self):\n        return []\n"


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "hermes"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "init", {"README.md": "x\n"})
    git(repo, "tag", "v0.9.0")
    base = commit(repo, "feat: slots", {"agent/memory_provider.py": MEM, "agent/context_engine.py": ENG})
    commit(repo, "refactor: tidy other", {"agent/memory_provider.py": MEM.replace("return 1", "return 2")})
    commit(repo, "fix(memory): prefetch returns cached text",
           {"agent/memory_provider.py": MEM.replace("return 2", "return 2").replace("return ''", "return 'c'")
            .replace("return 1", "return 2")})
    commit(repo, "docs: typo", {"agent/memory_provider.py": MEM.replace("return ''", "return 'c'")
                                .replace("return 1", "return 2") + "# note\n"})
    commit(repo, "feat(tools): unrelated", {"tools/x.py": "y = 1\n"})
    (repo / "agent/context").mkdir()
    git(repo, "mv", "agent/context_engine.py", "agent/context/engine.py")
    git(repo, "commit", "-q", "-m", "refactor: move context engine")
    commit(repo, "fix(security): compress drops secrets", {"agent/context/engine.py": ENG.replace("[]", "[None]")})
    git(repo, "tag", "v1.0.0")
    commit(repo, "feat: after the release", {"agent/memory_provider.py": MEM + "X = 1\n"})

    root = tmp_path / "exo"
    (root / "src/exo/core").mkdir(parents=True)
    (root / "src/exo/core/slots.py").write_text("# Lifted from hermes-agent\n" + MEM + ENG)
    (root / up.PROVENANCE).write_text(f'''[upstream.hermes]
repo = "https://example.invalid/hermes"

[[module]]
exo = "src/exo/core/slots.py"
mode = "lift"
upstream = ["agent/memory_provider.py", "agent/context_engine.py"]
commit = "{base}"

[[watch]]
upstream = ["tools/**"]
why = "tools"
commit = "{base}"
''')
    return repo, root, base


def test_report_ranks_and_filters(fixture):
    repo, root, base = fixture
    rep = up.build_report(up.load(root / up.PROVENANCE), repo, root)
    assert rep.target == git(repo, "rev-parse", "v1.0.0")  # newest stable tag containing the baseline
    mod, watch = sorted(rep.modules, key=lambda m: m.exo == "(watch)")
    subjects = [c.subject for c in mod.commits]
    assert subjects[0] == "fix(security): compress drops secrets"
    assert subjects[1] == "fix(memory): prefetch returns cached text"
    assert "feat: after the release" not in subjects and "feat(tools): unrelated" not in subjects
    assert mod.noise == 1  # the docs commit
    assert any("renamed to agent/context/engine.py" in n for n in mod.notes)
    fix = mod.commits[1]
    assert fix.kind == "fix" and "prefetch" in fix.touches
    assert [c.subject for c in watch.commits] == ["feat(tools): unrelated"]
    text = up.render_markdown(rep)
    assert "https://example.invalid/hermes/commit/" in text and "touches" in text


def test_report_to_head_and_missing_commit(fixture):
    repo, root, base = fixture
    prov = up.load(root / up.PROVENANCE)
    rep = up.build_report(prov, repo, root, to="main")
    mod = next(m for m in rep.modules if m.exo != "(watch)")
    assert "feat: after the release" in [c.subject for c in mod.commits]
    prov.modules[0].commit = "1234567"
    rep = up.build_report(prov, repo, root, to="main")
    assert any("not found upstream" in n for n in rep.modules[0].notes + rep.modules[1].notes)


def test_mark_checks_ancestry(fixture):
    repo, root, base = fixture
    head = git(repo, "rev-parse", "main")
    first = git(repo, "rev-list", "--max-parents=0", "main")
    with pytest.raises(up.ProvenanceError, match="does not descend"):
        up.mark(root, "src/exo/core/slots.py", first, repo=repo)
    rec = up.mark(root, "src/exo/core/slots.py", head[:10], repo=repo)
    assert rec["to"] == head
    rep = up.build_report(up.load(root / up.PROVENANCE), repo, root, to="main")
    assert next(m for m in rep.modules if m.exo != "(watch)").commits == []


def test_cli(fixture, capsys):
    repo, root, base = fixture
    ap = argparse.ArgumentParser()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root")
    up.register(ap.add_subparsers(dest="cmd"), common)

    def run(*argv):
        a = ap.parse_args(list(argv))
        return a.func(a)

    assert run("upstream", "check", "--provenance-root", str(root)) == 0
    assert run("upstream", "report", "--provenance-root", str(root), "--repo", str(repo), "--json") == 0
    data = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert data["source"] == "hermes" and data["modules"]
    assert run("upstream", "report", "--provenance-root", str(root), "--repo", str(repo)) == 0
    written = Path(capsys.readouterr().out.strip())
    assert written.parent == root / "upstream" and written.read_text().startswith("# Upstream report")
    assert run("upstream", "mark", "src/exo/core/slots.py", "--commit", "bad!",
               "--provenance-root", str(root)) == 2
    assert run("upstream", "mark", "src/exo/core/slots.py", "--commit", git(repo, "rev-parse", "main"),
               "--rejected", "not needed", "--repo", str(repo), "--provenance-root", str(root)) == 0
    assert "refused: not needed" in (root / up.PROVENANCE).read_text()


def test_mirror_clone_and_fetch(fixture, tmp_path):
    repo, root, base = fixture
    m = up.ensure_mirror(str(repo), tmp_path / "mirror" / "hermes.git")
    assert (m / "HEAD").is_file()
    commit(repo, "fix: later", {"agent/memory_provider.py": MEM + "Y = 2\n"})
    up.ensure_mirror(str(repo), m)
    assert git(m, "rev-parse", "main") == git(repo, "rev-parse", "main")
