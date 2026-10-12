"""The docs map must cover every decision and name only files that exist (docs/DEVELOPER-GUIDE.md, "The docs rule")."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "docs" / "DOCS-MAP.md"
DECISIONS = ROOT / "DECISIONS.md"

# A repo-relative path to a file that docs name: has a folder, or is one of the two top-level docs.
PATH_RE = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.(?:md|json|toml|png|svg|py)|DECISIONS\.md|README\.md)(?![\w-])")


def _map_text() -> str:
    return MAP.read_text(encoding="utf-8")


def test_every_decision_has_a_row():
    ids = re.findall(r"^## (D-\d{3})\b", DECISIONS.read_text(encoding="utf-8"), re.M)
    assert ids, "no decision headings found"
    rows = {m.group(1) for m in re.finditer(r"^\| (D-\d{3})\b", _map_text(), re.M)}
    missing = [d for d in ids if d not in rows]
    assert not missing, f"DECISIONS.md has decisions with no row in docs/DOCS-MAP.md: {missing}"


def test_every_named_file_exists():
    text = _map_text()
    named = set(PATH_RE.findall(text))
    named |= {m.group(1) for m in re.finditer(r"\]\(([^)#\s]+)\)", text) if "://" not in m.group(1)}
    assert named, "the map names no files"
    # Paths that live in another repository or are written as examples are not checked.
    missing = sorted(p for p in named if not (ROOT / p).exists())
    assert not missing, f"docs/DOCS-MAP.md names files that do not exist: {missing}"


def test_the_four_layers_exist():
    for name in ("USER-GUIDE.md", "DEVELOPER-GUIDE.md", "WHITEPAPER.md", "DOCS-MAP.md"):
        assert (ROOT / "docs" / name).is_file(), name
