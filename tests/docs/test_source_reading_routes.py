"""Keep the source reading routes connected to the implementation they describe."""
import re
from pathlib import Path

from exo.improvement.harness2 import UPSTREAM, UPSTREAM_COMMIT

ROOT = Path(__file__).resolve().parents[2]
PAGES = (ROOT / "docs/ENGINEERING-WALKTHROUGH.md", ROOT / "docs/SOURCES.md")


def test_reading_route_local_links_resolve():
    for page in PAGES:
        links = re.findall(r"\]\(([^)]+)\)", page.read_text())
        assert links, page
        for link in links:
            if "://" in link or link.startswith("#"):
                continue
            target = (page.parent / link.split("#", 1)[0]).resolve()
            assert target.is_relative_to(ROOT), (page, link)
            assert target.is_file(), (page, link)


def test_reading_routes_are_in_the_existing_index():
    index = (ROOT / "docs/DOCS-MAP.md").read_text()
    readme = (ROOT / "README.md").read_text()
    for page in PAGES:
        relative = page.relative_to(ROOT).as_posix()
        assert relative in index
        assert relative in readme


def test_harness_source_record_matches_the_adapter_pin():
    sources = (ROOT / "docs/SOURCES.md").read_text()
    assert f"{UPSTREAM}/tree/{UPSTREAM_COMMIT}" in sources
