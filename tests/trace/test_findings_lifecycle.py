"""Owner, disposition and exactly-once local retest metadata; no external notification is sent."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from exo.core.findings import Findings
from exo.core.sessions import SessionStore
from exo.core.trace import Thresholds, ToolRow, TurnTrace


@pytest.fixture
def findings(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    yield Findings(store, version="abc1234")
    store.close()


def test_lifecycle_has_owner_timestamps_history_and_consistent_backlog(findings):
    f = findings
    row = f.file("fixture", "a finding", session="filing-session", severity="bug")
    fid = row["id"]
    assert row["status"] == "open" and row["reported_session"] == "filing-session"
    triaged = f.triage(fid, "gpt-forge")
    assert triaged["status"] == "triaged" and triaged["triaged_at"] and triaged["owner"] == "gpt-forge"
    assert f.open_count() == 1 and f.backlog()["pending"] == 1
    assert "owner `gpt-forge`" in f.export_md()
    assert f.backlog()["oldest"][0]["owner"] == "gpt-forge"
    fixed = f.fix(fid, "abc1234")
    assert fixed["status"] == "fixed" and fixed["fixed_at"] and fixed["owner"] == "gpt-forge"
    request = f.retests("filing-session")[0]
    assert request["finding"] == fid and request["requested_at"] and request["fix_marker"] == "abc1234"
    assert [h["after_state"] for h in reversed(f.history(fid))] == ["open", "triaged", "fixed"]
    closed = f.close(fid, "handled elsewhere")
    assert closed["status"] == "closed" and closed["closed_at"] and closed["close_reason"] == "handled elsewhere"
    assert f.backlog()["pending"] == 0 and f.retests() == []
    assert f.triage(fid, "new-owner")["status"] == "triaged"


def test_recurrence_preserves_first_filing_session_and_owner(findings):
    f = findings
    row = f.file("same", "first", session="first-session")
    f.triage(row["id"], "owner")
    again = f.file("same", "later", session="later-session")
    assert again["reported_session"] == "first-session" and again["session"] == "later-session"
    assert again["owner"] == "owner" and again["status"] == "triaged"
    f.fix(row["id"], "fix-one")
    assert f.retests("later-session") == []
    assert len(f.retests("first-session")) == 1


def test_fix_is_idempotent_and_ack_is_not_verification(findings):
    f = findings
    fid = f.file("x", "x")["id"]
    first = f.fix(fid, "fix-one")
    f.store._q("UPDATE findings SET quiet=? WHERE id=?", (json.dumps({"turns": 3}), fid))
    again = f.fix(fid, "fix-one")
    assert again["fixed_at"] == first["fixed_at"] and json.loads(again["quiet"])["turns"] == 3
    assert len(f.retests()) == 1
    request = f.retests()[0]
    acknowledged = f.ack_retest(request["id"])
    assert acknowledged["acknowledged_at"]
    assert f.ack_retest(request["id"])["acknowledged_at"] == acknowledged["acknowledged_at"]
    assert f.get(fid)["status"] == "fixed" and not f.retests()
    f.fix(fid, "fix-two")
    assert [r["fix_marker"] for r in f.retests()] == ["fix-two"]
    assert f.store._q("SELECT COUNT(*) FROM finding_retests")[0][0] == 2


def test_two_store_instances_queue_one_request_transactionally(tmp_path):
    path = tmp_path / "sessions.sqlite"
    one, two = SessionStore(path), SessionStore(path)
    try:
        f1, f2 = Findings(one), Findings(two)
        fid = f1.file("x", "x")["id"]
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda f: f.fix(fid, "same-fix"), (f1, f2)))
        assert len(f1.retests()) == 1
        assert [r["after_state"] for r in f1.history(fid)].count("fixed") == 1
    finally:
        one.close()
        two.close()


def test_failed_queue_write_rolls_back_fix_and_transition(findings):
    f = findings
    fid = f.file("x", "x")["id"]
    f.store._q("CREATE TRIGGER fail_request BEFORE INSERT ON finding_retests BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        f.fix(fid, "fix-one")
    assert f.get(fid)["status"] == "open" and f.retests() == []
    assert len(f.history(fid)) == 1


def test_reopened_fix_requires_a_new_marker_and_old_request_is_not_actionable(findings):
    f = findings
    fid = f.file("x", "x")["id"]
    f.fix(fid, "abc1234")
    assert f.file("x", "again")["status"] == "reopened"
    assert f.retests() == []
    with pytest.raises(ValueError, match="new fix"):
        f.fix(fid, "abc1234")
    f.fix(fid, "next-fix")
    assert len(f.retests()) == 1 and f.retests()[0]["fix_marker"] == "next-fix"


@pytest.mark.parametrize("owner", [None, "", "a b", "x" * 81, "line\nbreak"])
def test_invalid_owner_does_not_mutate(findings, owner):
    fid = findings.file("x", "x")["id"]
    with pytest.raises(ValueError):
        findings.triage(fid, owner)
    assert findings.get(fid)["status"] == "open"


def test_bounds_unknown_ids_and_required_close_reason(findings):
    fid = findings.file("x", "x")["id"]
    for action in (lambda: findings.close(fid, ""), lambda: findings.fix(fid, "  "),
                   lambda: findings.fix(fid, "line\nbreak"), lambda: findings.retests(limit=True),
                   lambda: findings.history(fid, limit=101)):
        with pytest.raises(ValueError):
            action()
    for action in (lambda: findings.triage("missing", "owner"), lambda: findings.close("missing", "reason"),
                   lambda: findings.ack_retest("missing")):
        with pytest.raises(KeyError):
            action()


def test_cli_triage_fix_queue_ack_close_and_history(tmp_path, monkeypatch, capsys):
    from exo import cli
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    SessionStore(tmp_path / "sessions.sqlite").close()
    assert cli.main(["findings", "add", "Fixture lifecycle", "--signature", "fixture"]) == 0
    fid = capsys.readouterr().out.split()[0]
    assert cli.main(["findings", "triage", fid, "--owner", "owner", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "triaged"
    assert cli.main(["findings", "fix", fid, "--in", "fix-one"]) == 0
    capsys.readouterr()
    assert cli.main(["findings", "retests", "--json"]) == 0
    request = json.loads(capsys.readouterr().out)[0]["id"]
    assert cli.main(["findings", "ack-retest", request]) == 0
    assert "verification remains separate" in capsys.readouterr().out
    assert cli.main(["findings", "close", fid, "--reason", "duplicate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "closed"
    assert cli.main(["findings", "history", fid, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["after_state"] == "closed"


def test_quiet_verification_keeps_existing_contract_and_records_transition(findings):
    f = findings
    f.th = Thresholds(verify_turns=1)
    fid = f.file("tool.error:bash:exit-1", "bash error")["id"]
    f.triage(fid, "owner")
    f.fix(fid, "abc1234")
    turn = TurnTrace(session="s", turn_id="t", complete=True, tools=[ToolRow("bash")])
    assert f.observe(turn, set()) == [fid]
    assert f.get(fid)["status"] == "verified" and f.get(fid)["owner"] == "owner"
    assert f.retests() == []
    assert f.history(fid)[0]["after_state"] == "verified" and f.history(fid)[0]["at"]
    assert f.fix(fid, "abc1234")["status"] == "verified"  # retrying a fix cannot demote verified


def test_new_fix_during_observation_is_not_verified_by_old_evidence(findings, monkeypatch):
    import exo.core.findings as module
    f = findings
    f.th = Thresholds(verify_turns=1)
    fid = f.file("tool.error:bash:exit-1", "bash error")["id"]
    f.fix(fid, "abc1234")
    def changed_fix(signature, turn):
        f.fix(fid, "new0002")
        return True
    monkeypatch.setattr(module, "eligible", changed_fix)
    assert f.observe(TurnTrace(session="s", turn_id="t"), set()) == []
    assert f.get(fid)["status"] == "fixed" and f.get(fid)["fixed_in"] == "new0002"
    assert [r["fix_marker"] for r in f.retests()] == ["new0002"]


def test_legacy_finding_upgrades_without_rewriting_its_evidence(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE findings(id TEXT PRIMARY KEY,signature TEXT UNIQUE,source TEXT,severity TEXT,title TEXT,"
                   "detail TEXT DEFAULT '',session TEXT,turn TEXT,model TEXT,version TEXT,first_seen TEXT,last_seen TEXT,"
                   "count INTEGER DEFAULT 1,status TEXT DEFAULT 'open',fixed_in TEXT,fixed_at TEXT,fixed_turns INTEGER,"
                   "sessions TEXT DEFAULT '[]',quiet TEXT)")
        db.execute("INSERT INTO findings(id,signature,source,severity,title,session,first_seen,last_seen) "
                   "VALUES('f_legacy','legacy','agent','bug','original','last-known-session','2026-10-01','2026-10-02')")
    store = SessionStore(path)
    try:
        f = Findings(store)
        row = f.get("f_legacy")
        assert row["title"] == "original" and row["owner"] is None and row["reported_session"] is None
        f.triage("f_legacy", "owner")
        f.fix("f_legacy", "legacy-fix")
        assert f.retests()[0]["target_session"] == "last-known-session"
    finally:
        store.close()


def test_closed_fixed_regression_reopens_but_closed_unfixed_disposition_stays_closed(findings):
    f = findings
    fixed = f.file("fixed", "fixed")["id"]
    f.fix(fixed, "abc1234")
    f.close(fixed, "resolved")
    assert f.file("fixed", "regressed")["status"] == "reopened"
    unfixed = f.file("unfixed", "unfixed")["id"]
    f.close(unfixed, "declined")
    assert f.file("unfixed", "again")["status"] == "closed"
