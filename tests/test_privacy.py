"""D-004 and D-042: tracked files carry no home paths, no stray email addresses and no private names."""
import subprocess
from pathlib import Path

import pytest

from exo import privacy

ROOT = Path(__file__).resolve().parents[1]
DENY = privacy.deny_regex()          # read now: the conftest hides the real HOME during tests
SELF = "tests/test_privacy.py"  # its teeth tests contain made-up paths and addresses
SKIP_DIRS = ("patches/",)
TEXT = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".txt", ".sh", ".html", ".css", ".js", ".ts", ".cfg"}


def _tracked():
    try:
        r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    for rel in r.stdout.splitlines():
        p = ROOT / rel
        if p.suffix in TEXT and not rel.startswith(SKIP_DIRS) and p.is_file():
            yield rel, p.read_text(encoding="utf-8", errors="replace")


def test_generic_checks_have_teeth():
    assert privacy.generic_findings("see /Users/jdoe/x") == ["home path: /Users/jdoe"]
    assert privacy.generic_findings("mail jdoe@corp.io now") == ["email address: jdoe@corp.io"]
    assert privacy.generic_findings("/Users/<name>/x and t@example.invalid") == []
    assert privacy.generic_findings("@pytest.mark.parametrize") == []


def test_tracked_files_have_no_home_paths_or_emails():
    hits = [f"{rel}: {f}" for rel, text in _tracked() if rel != SELF for f in privacy.generic_findings(text)]
    assert not hits, "\n".join(hits[:40])


def test_tracked_files_hold_no_denylisted_names():
    if DENY is None:
        pytest.skip("no privacy deny-list (set EXO_PRIVACY_DENYLIST or write ~/.config/exo/privacy-denylist.txt)")
    skip = {SELF}
    hits = [f"{rel}:{n}" for rel, text in _tracked() if rel not in skip
            for n, line in enumerate(text.splitlines(), 1) if DENY.search(line)]
    # Report counts only, never the matched text.
    assert not hits, f"{len(hits)} lines name a denylisted word; first: {hits[:10]}"
