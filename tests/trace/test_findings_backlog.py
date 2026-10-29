import json

import pytest

from exo.core.findings import Findings
from exo.core.sessions import SessionStore

NOW = "2026-10-28T12:00:00Z"


@pytest.fixture
def findings():
    store = SessionStore()
    try:
        yield Findings(store, version="fixture")
    finally:
        store.close()


def add(findings, signature, *, status="open", severity="bug", first="2026-10-27T12:00:00Z"):
    row = findings.file(signature, signature, severity=severity)
    findings.store._q("UPDATE findings SET status=?,first_seen=? WHERE id=?", (status, first, row["id"]))
    return row["id"]


def test_counts_and_bounded_oldest_match_ledger_without_mutation(findings):
    oldest = add(findings, "old", severity="blocker", first="2026-09-01T00:00:00Z")
    add(findings, "reopened", status="reopened", first="2026-10-26T12:00:00Z")
    add(findings, "fixed", status="fixed", severity="papercut", first="2026-10-18T12:00:00Z")
    add(findings, "verified", status="verified", severity="idea", first="2020-01-01T00:00:00Z")
    before = findings.list()
    report = findings.backlog(1, now=NOW)
    assert report["counts"] == {"open": 1, "triaged": 0, "fixed": 1, "verified": 1, "closed": 0, "reopened": 1, "total": 4}
    assert report["pending"] == 3 and report["shown"] == 1
    assert report["oldest"][0]["id"] == oldest
    assert report["by_severity"] == {"blocker": 1, "bug": 1, "papercut": 1, "idea": 0}
    assert report["by_age"] == {"under_1_day": 0, "1_to_7_days": 1, "7_to_30_days": 1,
                                "30_days_or_more": 1, "unknown": 0}
    assert sum(report["by_age"].values()) == report["pending"]
    assert findings.list() == before
    assert findings.store._q("SELECT COUNT(*) FROM finding_views")[0][0] == 0


def test_age_boundaries_timezones_future_and_bad_historical_timestamp(findings):
    for name, first in [("today", "2026-10-28T11:00:00Z"), ("future", "2027-01-01T00:00:00Z"),
                        ("day", "2026-10-27T07:00:00-05:00"), ("week", "2026-10-21T12:00:00Z"),
                        ("month", "2026-09-28T12:00:00Z"), ("bad", "not a date")]:
        add(findings, name, first=first)
    report = findings.backlog(now=NOW)
    assert report["by_age"] == {"under_1_day": 2, "1_to_7_days": 1, "7_to_30_days": 1,
                                "30_days_or_more": 1, "unknown": 1}
    rows = {row["title"]: row for row in report["oldest"]}
    assert rows["future"]["age_days"] == 0 and rows["bad"]["age_days"] is None
    assert rows["day"]["age_days"] == 1
    assert report["oldest"][-1]["title"] == "bad"


def test_zero_limit_keeps_aggregate_and_ties_sort_stably(findings):
    ids = [add(findings, str(i)) for i in range(8)]
    assert [row["id"] for row in findings.backlog(3, now=NOW)["oldest"]] == sorted(ids)[:3]
    report = findings.backlog(0, now=NOW)
    assert report["oldest"] == [] and report["pending"] == 8


def test_empty_backlog_is_complete(findings):
    report = findings.backlog(now=NOW)
    assert report["pending"] == report["counts"]["total"] == report["shown"] == 0
    assert sum(report["by_age"].values()) == sum(report["by_severity"].values()) == 0


@pytest.mark.parametrize("limit", [-1, 101, True, 1.5, "1"])
def test_bad_limit_refused(findings, limit):
    with pytest.raises(ValueError, match="limit"):
        findings.backlog(limit, now=NOW)


def test_bad_report_time_refused(findings):
    with pytest.raises(ValueError, match="timestamp"):
        findings.backlog(now="not a date")


def test_cli_backlog_text_json_and_invalid_filters(tmp_path, monkeypatch, capsys):
    from exo import cli
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    SessionStore(tmp_path / "home" / "sessions.sqlite").close()
    assert cli.main(["findings", "add", "Fixture backlog item", "--severity", "bug"]) == 0
    capsys.readouterr()
    assert cli.main(["findings", "backlog", "--json", "--limit", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pending"] == 1 and report["oldest"] == []
    assert cli.main(["findings", "backlog"]) == 0
    text = capsys.readouterr().out
    assert "1 unresolved" in text and "Fixture backlog item" in text
    with pytest.raises(SystemExit, match="limit"):
        cli.main(["findings", "backlog", "--limit", "-1"])
    with pytest.raises(SystemExit, match="--status"):
        cli.main(["findings", "backlog", "--status", "open"])


def test_backlog_reads_while_another_process_holds_the_write_lock(tmp_path):
    """The backlog is a read: it must not wait for, or block, a writer on the same file (#797 review)."""
    import sqlite3

    store = SessionStore(tmp_path / "sessions.sqlite")
    findings = Findings(store, version="fixture")
    add(findings, "pending")
    writer = sqlite3.connect(tmp_path / "sessions.sqlite", timeout=0, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE findings SET title='renamed'")
        # With the write lock this raised "database is locked" once the busy timeout ran out.
        report = findings.backlog(5, now=NOW)
        assert writer.in_transaction  # it returned while the writer still held its lock
        assert report["pending"] == 1 and report["oldest"][0]["title"] == "pending"  # the snapshot before the write
        writer.execute("COMMIT")  # timeout=0: this fails at once if the read had left a lock behind
        assert not store._db.in_transaction
        assert findings.backlog(5, now=NOW)["oldest"][0]["title"] == "renamed"  # the next read sees the commit
    finally:
        writer.close()
        store.close()
