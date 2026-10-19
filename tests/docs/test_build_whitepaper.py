"""The whitepaper page takes its version and month from the paper's header line, and the committed page is current."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_whitepaper.py"
BODY = "## Part I. Overview\n\n### 1. The problem\n\nText.\n"


def _build(repo: Path, md: str) -> subprocess.CompletedProcess:
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "WHITEPAPER.md").write_text(md, encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(repo)], capture_output=True, text=True)


def _page(repo: Path) -> str:
    return (repo / "site" / "whitepaper" / "index.html").read_text(encoding="utf-8")


def test_version_and_month_come_from_the_header(tmp_path):
    md = "# Paper\n\nVersion 0.4, December 2026. Aska Digital.\n\nVersion 0.3 adds a thing.\n\n" + BODY
    done = _build(tmp_path, md)
    assert done.returncode == 0, done.stderr
    page = _page(tmp_path)
    assert "Whitepaper v0.4" in page
    assert "<dd>December 2026</dd>" in page
    assert "version 0.4, December 2026. Aska Digital." in page
    assert "October" not in page and "v0.3" not in page


def test_a_reworded_header_stops_the_build_instead_of_reading_later_prose(tmp_path):
    md = "# Paper\n\nVersion 0.4 (December 2026), Aska Digital.\n\nVersion 0.3, October 2026. adds a thing.\n\n" + BODY
    done = _build(tmp_path, md)
    assert done.returncode != 0
    assert "Version 0.3, October 2026. Aska Digital." in done.stderr  # names the expected form
    assert "Traceback" not in done.stderr
    assert not (tmp_path / "site").exists()


def test_a_missing_header_stops_the_build_with_a_message(tmp_path):
    done = _build(tmp_path, "# Paper\n\nNo version here.\n\n" + BODY)
    assert done.returncode != 0
    assert "the line after the title must read like" in done.stderr
    assert "Traceback" not in done.stderr


def test_committed_page_matches_the_paper(tmp_path):
    done = _build(tmp_path, (ROOT / "docs" / "WHITEPAPER.md").read_text(encoding="utf-8"))
    assert done.returncode == 0, done.stderr
    committed = (ROOT / "site" / "whitepaper" / "index.html").read_text(encoding="utf-8")
    assert _page(tmp_path) == committed, "run python3 scripts/build_whitepaper.py and commit site/whitepaper/index.html"
