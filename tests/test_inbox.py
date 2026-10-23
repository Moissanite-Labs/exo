"""The inbox observes existing state without booting, repairing or crossing agent/store scope."""
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from exo import inbox
from exo.ask import QuestionQueue
from exo.core.agents.delegate import TABLE
from exo.core.findings import Findings
from exo.core.sessions import SessionStore
from exo.protocol import schema as s


@pytest.fixture
def home(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    store.create_session(id="mine", title="My work", agent="default")
    store.create_session(id="theirs", title="Other agent", agent="other")
    store._q(TABLE)
    for ident, agent, state in (("active", "default", "running"), ("ended", "default", "failed"),
                                ("foreign", "other", "running")):
        session = "mine" if agent == "default" else "theirs"
        store._q("INSERT INTO tasks VALUES (?,?,NULL,0,?,?,?,NULL,NULL,'','2026-10-23')",
                 (ident, session, ident, state, "private task brief"))
        store._q("INSERT INTO jobs(id,agent,session,command,state,pid,started,log) VALUES (?,?,?,?,?,?,?,?)",
                 (ident, agent, session, "secret shell command", state, 999999999, 1, "private.log"))
    Findings(store, version="test").file("mine", "Check my work", session="mine")
    Findings(store, version="test").file("theirs", "Other finding", session="theirs")
    Findings(store, version="test").file("global", "Unowned finding")
    store.append_event(s.ApprovalRequested(request_id="pending", summary="Please review"), "mine")
    store.append_event(s.ApprovalRequested(request_id="resolved", summary="Already reviewed"), "mine")
    store.append_event(s.ApprovalResolved(request_id="resolved", decision="allow"), "mine")
    store.append_event(s.ApprovalRequested(request_id="foreign", summary="Other approval"), "theirs")
    store.close()
    db = sqlite3.connect(tmp_path / "schedule.sqlite")
    db.execute("CREATE TABLE jobs(id,title,seat,paused,last_run,next_run,last_status)")
    db.executemany("INSERT INTO jobs VALUES (?,?,?,0,NULL,1,NULL)",
                   [("mine", "My schedule", "default"), ("shared", "Shared schedule", None),
                    ("foreign", "Other schedule", "other")])
    db.commit()
    db.close()
    QuestionQueue(tmp_path / "principals" / "selected").propose("A question")
    QuestionQueue(tmp_path / "principals" / "other").propose("Other question")
    return tmp_path


def observe(home, **kwargs):
    return inbox.collect(home, "default", home / "principals" / "selected", **kwargs)


def test_snapshot_coverage_and_ownership(home):
    report = observe(home)
    sections = report["sections"]
    assert set(sections) == set(inbox.SECTIONS)
    assert [r["id"] for r in sections["sessions"]["items"]] == ["mine"]
    assert {r["id"] for r in sections["tasks"]["items"]} == {"active", "ended"}
    assert {r["id"] for r in sections["jobs"]["items"]} == {"active", "ended"}
    assert {r["id"] for r in sections["schedules"]["items"]} == {"mine", "shared"}
    assert [r["title"] for r in sections["questions"]["items"]] == ["A question"]
    assert [r["id"] for r in sections["approvals"]["items"]] == ["pending"]
    assert [r["title"] for r in sections["findings"]["items"]] == ["Check my work"]
    assert "private" not in json.dumps(report) and "secret shell" not in json.dumps(report)


def test_stale_active_records_and_missing_pid_never_prove_outcomes(home, monkeypatch):
    monkeypatch.setattr("os.kill", lambda *a: pytest.fail("must not probe PIDs"))
    report = observe(home)
    assert report["live_state"] == "unknown"
    for kind in ("tasks", "jobs"):
        rows = {r["id"]: r for r in report["sections"][kind]["items"]}
        assert rows["active"]["state"] == "unknown"
        assert rows["active"]["recorded_state"] == "running"
        assert rows["ended"]["state"] == "failed"
    assert report["sections"]["approvals"]["status"] == "unknown"
    assert report["sections"]["schedules"]["items"][0]["state"] == "unknown"


def test_read_only_existing_database_and_queue(home):
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    report = observe(home)
    assert report["sections"]["questions"]["status"] == "ok"
    assert all(p.read_bytes() == data for p, data in before.items())
    snapshot = inbox.Snapshot(home / "sessions.sqlite", time.monotonic() + 1)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            snapshot.create_session(title="must not write")
    finally:
        snapshot.close()


def test_absent_home_is_unknown_and_creates_nothing(tmp_path):
    missing = tmp_path / "absent"
    report = inbox.collect(missing, "default")
    assert all(p["status"] == "unknown" for p in report["sections"].values())
    assert not missing.exists()


def test_limit_and_incompatible_schema_isolated_by_section(home):
    assert observe(home, limit=1)["sections"]["tasks"]["truncated"]
    db = sqlite3.connect(home / "sessions.sqlite")
    db.execute("DROP TABLE tasks")
    db.commit()
    db.close()
    report = observe(home)
    assert report["sections"]["tasks"]["status"] == "unknown"
    assert report["sections"]["jobs"]["status"] == "ok"


@pytest.mark.parametrize("meta", ["[]", "null", "[" * 1100])
def test_malformed_session_metadata_isolated_from_other_sections(home, meta):
    db = sqlite3.connect(home / "sessions.sqlite")
    db.execute("UPDATE sessions SET meta=? WHERE id='mine'", (meta,))
    db.commit()
    db.close()
    report = observe(home)
    assert report["sections"]["sessions"]["status"] == "unknown"
    assert report["sections"]["jobs"]["status"] == "ok"


def test_oversized_source_fields_cannot_expand_report(home):
    db = sqlite3.connect(home / "sessions.sqlite")
    db.execute("UPDATE sessions SET updated=? WHERE id='mine'", ("X" * 1_000_000,))
    db.commit()
    db.close()
    report = observe(home)
    assert report["sections"]["sessions"]["status"] == "unknown"
    assert len(json.dumps(report)) < 10000
    row = inbox.item("task", "id", "x" * 1_000_000, "running", updated="x" * 1_000_000,
                     session="x" * 1_000_000)
    assert len(row["title"]) == len(row["session"]) == 160
    assert row["updated_at"] is None
    assert inbox.timestamp(float("inf")) is None
    assert inbox.short({"unexpected": ["x" * 1_000_000]}) == "[invalid value]"


@pytest.mark.parametrize("content", [b"{broken", b"x" * (inbox.QUESTION_BYTES + 1),
                                     b'{"op":"asked","id":"q"}\n' * (inbox.QUESTION_EVENTS + 1)])
def test_question_replay_refuses_partial_or_overbudget_history(home, content):
    path = home / "principals" / "selected" / "queues" / "questions.jsonl"
    path.write_bytes(content)
    report = observe(home)
    assert report["sections"]["questions"]["status"] == "unknown"
    assert report["sections"]["questions"]["items"] == []


def test_expired_query_budget_and_control_characters(home):
    with pytest.raises(inbox.BudgetExceeded):
        inbox.Snapshot(home / "sessions.sqlite", time.monotonic() - 1)
    assert "\x1b" not in inbox.short("title\x1b[2J\nforged line")


def test_live_wal_is_visible_without_using_immutable_mode(home):
    writer = SessionStore(home / "sessions.sqlite")
    writer.create_session(id="new", agent="default", title="Committed in WAL")
    try:
        assert "new" in {r["id"] for r in observe(home)["sections"]["sessions"]["items"]}
    finally:
        writer.close()


def test_question_answers_are_folded_and_nonregular_file_cannot_block(home):
    import os

    root = home / "principals" / "selected"
    queue = QuestionQueue(root)
    queue.drop(next(iter(queue.state())), "No longer needed")
    assert observe(home)["sections"]["questions"]["items"] == []
    queue.path.unlink()
    os.mkfifo(queue.path)
    assert observe(home)["sections"]["questions"]["status"] == "unknown"


def test_cli_json_never_imports_runtime_or_creates_home(tmp_path):
    import os

    env = dict(os.environ, EXO_HOME=str(tmp_path / "empty"), EXO_PRINCIPAL="selected", EXO_SEAT="chosen")
    code = ("import sys; from exo.cli import main; main(['inbox','--json']); "
            "assert 'exo.core.engine' not in sys.modules; assert 'exo.compose' not in sys.modules; "
            "assert 'exo.protocol.client' not in sys.modules")
    run = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, timeout=10)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["scope"]["agent"] == "chosen"
    assert Path(report["scope"]["question_store"]).name == "selected"
    assert not (tmp_path / "empty").exists()
