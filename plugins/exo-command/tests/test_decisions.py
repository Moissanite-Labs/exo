import json

import pytest

from exo_command import decisions


def test_fold_and_order(world, now):
    d = decisions.read_queue(world["queue"], now=now)
    ids = [x["id"] for x in d["waiting"]]
    assert ids == ["dec-2", "dec-1"]          # overdue first
    assert d["counts"] == {"waiting": 2, "overdue": 1, "snoozed": 1}
    one = d["waiting"][1]
    assert one["context"] == "Changelog is ready."    # update merged
    assert one["options"][1] == {"key": "b", "label": "Hold"}
    assert one["reversible"] is False
    assert [x["id"] for x in d["snoozed"]] == ["dec-4"]
    assert d["recent"][0]["id"] == "dec-3" and d["recent"][0]["choice"] == "no"
    assert len(d["errors"]) == 2               # bad JSON and resolve-before-open


def test_snooze_expires(world, now):
    d = decisions.read_queue(world["queue"], now=now + 7200)
    assert "dec-4" in [x["id"] for x in d["waiting"]]


def test_not_configured_and_missing():
    assert decisions.read_queue(None)["configured"] is False
    from pathlib import Path
    d = decisions.read_queue(Path("/nonexistent/queue.jsonl"))
    assert d["configured"] and not d["exists"] and d["waiting"] == []


def test_validate():
    assert decisions.validate({"op": "open", "id": "x", "ts": 1}) == "open needs a title"
    assert decisions.validate({"op": "nope", "id": "x", "ts": 1})
    assert decisions.validate({"op": "open", "id": "x", "ts": "2026-10-11T10:00:00Z", "title": "t"}) is None
    assert decisions.validate({"op": "open", "id": "x", "ts": "yesterday", "title": "t"})


def test_append_event_roundtrip(tmp_path):
    q = tmp_path / "q" / "d.jsonl"
    decisions.append_event(q, {"op": "open", "id": "a", "title": "Ship it?"})
    decisions.append_event(q, {"op": "withdraw", "id": "a", "reason": "moot"})
    with pytest.raises(ValueError):
        decisions.append_event(q, {"op": "open", "id": "b"})
    lines = q.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["v"] == 1
    d = decisions.read_queue(q)
    assert d["waiting"] == [] and d["recent"][0]["status"] == "withdrawn"
    assert oct(q.stat().st_mode & 0o777) == "0o600"


def test_schema_file_matches_ops():
    from conftest import PLUGIN_DIR
    schema = json.loads((PLUGIN_DIR / "schemas" / "decision-event.schema.json").read_text())
    assert set(schema["properties"]["op"]["enum"]) == decisions.OPS
