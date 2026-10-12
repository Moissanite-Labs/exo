"""Docs that quote protocol counts must match the schema, so they cannot go stale quietly."""
import re
from pathlib import Path

from exo.protocol import schema

ROOT = Path(__file__).resolve().parents[2]


def test_core_doc_counts_match_schema():
    text = (ROOT / "docs" / "CORE.md").read_text(encoding="utf-8")
    m = re.search(r"holds (\d+) commands .*? and (\d+) event types", text, re.S)
    assert m, "docs/CORE.md must state the command and event counts"
    assert (int(m.group(1)), int(m.group(2))) == (len(schema.COMMANDS), len(schema.EVENTS))


def test_schema_comments_match_schema():
    text = (ROOT / "src" / "exo" / "protocol" / "schema.py").read_text(encoding="utf-8")
    assert f"# {'-' * 64} commands ({len(schema.COMMANDS)}" in text
    assert f"# {'-' * 64} events ({len(schema.EVENTS)}" in text
    assert f"Today there are {len(schema.COMMANDS)} commands and {len(schema.EVENTS)} events" in text
