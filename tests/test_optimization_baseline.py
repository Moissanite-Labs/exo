"""Characterize the measurement harness rather than asserting timing thresholds."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "optimization_baseline.py"
spec = importlib.util.spec_from_file_location("optimization_baseline", SCRIPT)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def test_session_reference_tail_and_transaction_observations(tmp_path):
    report = baseline.session_measurements(tmp_path, 61, 83)
    assert report["tail_matches_reference"]
    assert report["full_messages"]["rows_materialized"] == 61
    assert report["bounded_tail"]["rows_materialized"] == 40
    assert report["fork"]["rows_copied"] == 61
    assert report["all_events"]["rows_materialized"] == 83
    assert report["event_page"]["rows_materialized"] == 40
    assert report["list_sessions"]["sql_kinds"]["SELECT"] == 1
    assert report["fork"]["sql_statements"] > 0


def test_bus_fixture_reads_production_methods_and_explains_plans(tmp_path):
    report = baseline.bus_measurements(tmp_path, 61)
    assert report["inbox"]["rows_returned"] == 40
    assert report["search"]["rows_returned"] == 40
    assert report["inbox"]["sql_kinds"]["SELECT"] == 1
    assert all(report["query_plans"].values())


def test_anchor_crash_boundaries_remain_verifiable_and_recover():
    for row in baseline.ledger_measurements():
        assert row["exit_code"] == 73
        assert row["before"]["ok"] and row["before"]["checked"] == 2
        assert row["before"]["anchored"]
        assert row["before"]["anchored_seq"] in (1, 2)
        assert row["after"] == {"ok": True, "checked": 3, "anchored": True, "anchored_seq": 3}


def test_public_name_guard_detects_deleted_module_and_name():
    def report(modules):
        return {"source": {"modules": {k: {"public_names": v} for k, v in modules.items()}}}
    before = report({"gate": ["classify", "decide"], "context": ["Tree"]})
    after = report({"gate": ["classify", "New"]})
    assert baseline.removed_names(before, after) == {"gate": ["decide"], "context": ["Tree"]}
    assert not baseline.removed_names(before, before)


def test_assets_count_excess_once_and_ignore_symlink_targets(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "site").mkdir()
    (tmp_path / "docs/media").mkdir(parents=True)
    for name in ("site/a.png", "site/b.png", "docs/media/c.png"):
        (tmp_path / name).write_bytes(b"synthetic-image")
    (tmp_path / "site/link.png").symlink_to(tmp_path / "site/a.png")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    row = baseline.asset_measurements(tmp_path)
    assert row["tracked_image_bytes"] == 3 * len(b"synthetic-image")
    assert row["duplicate_excess_bytes"] == 2 * len(b"synthetic-image")
    assert len(row["duplicate_groups"]) == 1


def test_source_snapshot_is_ast_only_and_captures_reexports(tmp_path):
    for name in (*baseline.MODULES, "src/exo/core/models/base.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from __future__ import annotations\nfrom imaginary import public as alias\n"
                        "LIMIT = 3\ndef run():\n    return x.summaries()\n")
    report = baseline.source_measurements(tmp_path)
    assert report["modules"][baseline.MODULES[0]]["public_names"] == ["LIMIT", "alias", "run"]
    assert report["context_summaries_call_sites"] == 1


@pytest.mark.parametrize("value", ["0", "100001", "-1"])
def test_fixture_bound_rejects_invalid_values(value):
    import sys
    result = subprocess.run([sys.executable, str(SCRIPT), "--events", value], capture_output=True, text=True)
    assert result.returncode == 2
    assert "events 1..100000" in result.stderr
