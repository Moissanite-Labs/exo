"""#842: CHANGELOG.md merges with git's union driver, so two branches that each add a bullet do not conflict.
DECISIONS.md and docs/DOCS-MAP.md keep the normal driver: a clash there must still conflict (#648)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git is needed")


def _drivers() -> dict:
    out = {}
    for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        words = line.split()
        if words and not words[0].startswith("#"):
            out[words[0]] = words[1:]
    return out


def test_only_the_changelog_takes_the_union_driver():
    drivers = _drivers()
    assert drivers.get("CHANGELOG.md") == ["merge=union"]
    union = [path for path, attrs in drivers.items() if "merge=union" in attrs]
    assert union == ["CHANGELOG.md"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=repo, capture_output=True, text=True)


def _two_branches(tmp_path: Path, name: str, base: str, ours: str, theirs: str) -> Path:
    """A repo on main with this checkout's .gitattributes, and a branch `pr` that changes `name` from `base` to
    `theirs` while main changes it to `ours`."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    assert _git(repo, "init", "-q").returncode == 0
    shutil.copy(ROOT / ".gitattributes", repo / ".gitattributes")
    (repo / name).write_text(base, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / name).write_text(theirs, encoding="utf-8")
    _git(repo, "commit", "-qam", "pr")
    _git(repo, "checkout", "-q", "main")
    (repo / name).write_text(ours, encoding="utf-8")
    _git(repo, "commit", "-qam", "main")
    return repo


HEAD = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n"


def test_two_changelog_bullets_in_the_same_place_both_land(tmp_path):
    old = "- An older fix.\n"
    repo = _two_branches(tmp_path, "CHANGELOG.md", HEAD + old, HEAD + "- Main's fix.\n" + old,
                         HEAD + "- The branch's fix.\n" + old)
    merged = _git(repo, "merge", "--no-edit", "pr")
    assert merged.returncode == 0, merged.stdout + merged.stderr
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text == HEAD + "- Main's fix.\n- The branch's fix.\n" + old
    assert "<<<<<<<" not in text


def _repeats(text: str) -> list:
    """What a fused union merge leaves behind: a bullet that a version section holds twice, or a `###` heading
    with no bullet under it."""
    found, seen, lines = [], set(), text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            seen = set()
        elif line.startswith("- "):
            if line in seen:
                found.append(line)
            seen.add(line)
        elif line.startswith("### "):
            rest = [x for x in lines[i + 1:] if x.strip()]
            if not rest or rest[0].startswith("#"):
                found.append(line)
    return found


def test_the_changelog_holds_no_repeated_bullet_or_empty_section():
    """A union merge that fused two nearby changes copies the lines between them; this makes that visible."""
    assert _repeats((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) == []


def test_changes_beside_two_adjacent_headings_are_fused_and_the_repeat_is_caught(tmp_path):
    """Union does not keep an entry under its heading. Each side adds under `### Fixed` and under `### Added`, a few
    lines apart. Git takes the two changes as one hunk, and union keeps both sides' copy of it, with the lines
    between. Every new bullet survives, but `- Existing.` repeats, so the test above fails on the merged file."""
    base = "## [Unreleased]\n\n### Fixed\n- Existing.\n\n### Added\n- Older.\n"
    ours = "## [Unreleased]\n\n### Fixed\n- Main fix.\n- Existing.\n\n### Added\n- Main addition.\n- Older.\n"
    theirs = "## [Unreleased]\n\n### Fixed\n- Branch fix.\n- Existing.\n\n### Added\n- Branch addition.\n- Older.\n"
    repo = _two_branches(tmp_path, "CHANGELOG.md", base, ours, theirs)
    merged = _git(repo, "merge", "--no-edit", "pr")
    assert merged.returncode == 0, merged.stdout + merged.stderr
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    for bullet in ("- Main fix.", "- Branch fix.", "- Main addition.", "- Branch addition."):
        assert bullet in text.splitlines()
    assert "- Existing." in _repeats(text)


@pytest.mark.parametrize("name", ["DECISIONS.md", "docs/DOCS-MAP.md"])
def test_a_clash_in_decisions_or_the_map_still_conflicts(tmp_path, name):
    base = "# Top\n\n## D-093 (2026-10-30) Last\n"
    repo = _two_branches(tmp_path, name, base, base + "\n## D-094 (2026-10-29) Main's choice\n",
                         base + "\n## D-094 (2026-10-29) The branch's choice\n")
    merged = _git(repo, "merge", "--no-edit", "pr")
    assert merged.returncode != 0
    assert "CONFLICT" in merged.stdout + merged.stderr
