"""Ingest adapters on synthetic fixtures only. No real data."""
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from exo import crypto
from exo.ingest import adapter, author_conf, ingest, redact
from exo.ingest.cli import cmd_ingest, register
from exo.ledger import Ledger

FIX = Path(__file__).parent / "fixtures"
ME = "principal-x"


@pytest.fixture
def led(tmp_path):
    lg = Ledger(tmp_path / "ledger.sqlite", crypto.Signer("ingest", crypto.keygen()))
    yield lg
    lg.close()


def utterances(lg):
    return [e for e in lg.iter(kind="utterance")]


# --- fixture builders ----------------------------------------------------------

def _w(fh, d):
    fh.write(json.dumps(d) + "\n")


def make_claude_code(dirpath: Path) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    f = dirpath / "sess-1.jsonl"
    base = {"sessionId": "sess-1", "isSidechain": False, "timestamp": "2026-01-01T10:00:00.000Z"}
    with f.open("w") as fh:
        _w(fh, {"type": "mode", "mode": "x"})
        _w(fh, {**base, "type": "user", "uuid": "u1", "origin": {"kind": "human"},
                "message": {"role": "user", "content": "please fix the build"}})
        _w(fh, {**base, "type": "assistant", "uuid": "a1",
                "message": {"model": "model-t", "content": [
                    {"type": "thinking", "thinking": "hidden"},
                    {"type": "text", "text": "Looking now."},
                    {"type": "tool_use", "name": "Bash", "input": {}}]}})
        _w(fh, {**base, "type": "user", "uuid": "t1", "toolUseResult": {"x": 1},
                "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
        _w(fh, {**base, "type": "attachment", "uuid": "q1", "attachment": {
            "type": "queued_command", "prompt": "also use token=abcdef1234567890xyz", "commandMode": "prompt",
            "origin": {"kind": "human"}, "timestamp": "2026-01-01T10:00:30.000Z"}})
        _w(fh, {**base, "type": "attachment", "uuid": "q2", "attachment": {
            "type": "queued_command", "prompt": "<task-notification>done</task-notification>",
            "commandMode": "task-notification", "origin": {"kind": "task-notification"}}})
        _w(fh, {**base, "type": "attachment", "uuid": "q3", "attachment": {
            "type": "queued_command", "prompt": "peer says hi", "commandMode": "prompt", "isMeta": True,
            "origin": {"kind": "peer", "from": "a1"}}})
        _w(fh, {**base, "type": "user", "uuid": "u2", "origin": {"kind": "task-notification"},
                "message": {"content": "<task-notification>x</task-notification>"}})
        _w(fh, {**base, "type": "user", "uuid": "u3", "message": {"content": "<command-name>/clear</command-name>"}})
        _w(fh, {**base, "type": "user", "uuid": "u4", "isMeta": True, "message": {"content": "meta"}})
        _w(fh, {**base, "type": "user", "uuid": "c1", "isCompactSummary": True,
                "message": {"content": "Summary of the earlier conversation."}})
        _w(fh, {**base, "type": "user", "uuid": "s1", "isSidechain": True, "message": {"content": "subagent brief"}})
    return f


def make_hermes(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, model TEXT,
      profile_name TEXT, started_at REAL NOT NULL);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
      content TEXT, tool_calls TEXT, timestamp REAL NOT NULL, message_uid TEXT,
      _compressed_summary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
    INSERT INTO sessions VALUES ('s-cli', 'cli', 'Chat', 'model-h', NULL, 1767261600.0);
    INSERT INTO sessions VALUES ('s-cron', 'cron', 'Job', 'model-h', NULL, 1767261600.0);
    INSERT INTO messages (session_id, role, content, timestamp, message_uid) VALUES
      ('s-cli', 'user', 'hey can u check the logs', 1767261601.0, 'uid1'),
      ('s-cli', 'assistant', 'Checked. All clear.', 1767261602.0, 'uid2'),
      ('s-cli', 'assistant', NULL, 1767261603.0, 'uid3'),
      ('s-cli', 'tool', 'tool output', 1767261604.0, 'uid4'),
      ('s-cli', 'user', 'You are a research agent. Produce a report on X.\n- a\n- b\n- c\n- d\n- e\n- f',
        1767261605.0, 'uid5'),
      ('s-cron', 'user', 'cron prompt', 1767261606.0, 'uid6');
    """)
    c.commit()
    c.close()
    return path


# --- redaction and helpers -------------------------------------------------------

def test_redact_secrets():
    text = ("keys sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA and ghp_" + "a" * 36 + " and AKIAABCDEFGHIJKLMNOP "
            "and Bearer abcdefghijklmnopqrstuvwxyz012345 and password: hunter2hunter2 "
            "and xoxb-1234567890-abcdef")
    out, n = redact(text)
    assert n == 6
    for leak in ("sk-ant-api03", "ghp_aaa", "AKIAABCD", "abcdefghijklmnop", "hunter2", "xoxb-"):
        assert leak not in out
    assert "password: [REDACTED:SECRET]" in out
    assert redact("nothing secret here, token budget is 1500") == ("nothing secret here, token budget is 1500", 0)


def test_redact_private_key_block():
    out, n = redact("a\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\nb")
    assert n == 1 and "abc" not in out and out.startswith("a\n") and out.endswith("\nb")


def test_author_conf():
    assert author_conf("hey can u check the logs") == "high"
    assert author_conf("You are a research agent. Produce a report.\n- a\n- b\n- c\n- d\n- e") == "low"


# --- claude code ---------------------------------------------------------------------

def test_claude_code(tmp_path, led):
    f = make_claude_code(tmp_path / "proj")
    stats = ingest("claude_code", tmp_path / "proj", led, ME)
    ev = utterances(led)
    texts = [e["body"]["text"] for e in ev]
    assert stats["added"] == 4 == len(ev)
    assert texts[0] == "please fix the build" and ev[0]["author"] == ME and ev[0]["body"]["role"] == "principal"
    assert texts[1] == "Looking now." and ev[1]["author"] == "claude-code" and ev[1]["body"]["model"] == "model-t"
    q = ev[2]["body"]
    assert q["queued"] is True and q["role"] == "principal" and q["redacted"] == 1
    assert "abcdef1234567890xyz" not in q["text"]
    s = ev[3]["body"]
    assert s["secondary"] is True and s["summary"] == "compact"
    # provenance points at the exact line
    src = ev[1]["body"]["src"]
    assert src["path"] == str(f.resolve()) and src["uuid"] == "a1" and src["block"] == 1
    line = f.read_text().splitlines()[src["line"] - 1]
    assert json.loads(line)["uuid"] == "a1"
    assert ev[0]["session"] == "claude_code:sess-1"
    assert ev[0]["body"]["source_ts"] == "2026-01-01T10:00:00.000000Z"
    # nothing skipped leaks in
    for bad in ("hidden", "tool_result", "task-notification", "peer says hi", "meta", "subagent brief", "/clear"):
        assert not any(bad in t for t in texts)


def test_claude_code_idempotent_and_watermark(tmp_path, led):
    f = make_claude_code(tmp_path / "proj")
    ingest("claude_code", tmp_path / "proj", led, ME)
    n = len(led)
    again = ingest("claude_code", tmp_path / "proj", led, ME)
    assert again["added"] == 0 and len(led) == n
    wm = [e for e in led.iter(kind="ingest")]
    assert len(wm) == 1 and wm[0]["body"]["watermark"] == {"sess-1.jsonl": 12}
    assert wm[0]["body"]["source"] == str((tmp_path / "proj").resolve())
    # appended lines are picked up from the watermark; a partial last line waits
    with f.open("a") as fh:
        _w(fh, {"type": "user", "uuid": "u9", "sessionId": "sess-1", "timestamp": "2026-01-02T00:00:00Z",
                "message": {"content": "next request"}})
        fh.write('{"type": "user", "uuid": "partial"')
    third = ingest("claude_code", tmp_path / "proj", led, ME)
    assert third["added"] == 1 and third["seen"] == 1
    assert list(led.iter(kind="ingest"))[-1]["body"]["watermark"] == {"sess-1.jsonl": 13}


def test_dedupe_without_watermark(tmp_path):
    """A second ledger seeded only with utterances (no watermark) still dedupes by src.key."""
    make_claude_code(tmp_path / "proj")
    signer = crypto.Signer("ingest", crypto.keygen())
    with Ledger(tmp_path / "l.sqlite", signer) as lg:
        ingest("claude_code", tmp_path / "proj", lg, ME)
        # same source via a different path spelling -> no watermark match, keys still dedupe
        link = tmp_path / "link"
        os.symlink(tmp_path / "proj" / "sess-1.jsonl", link)
        assert ingest("claude_code", link, lg, ME)["added"] == 0


def test_since_filters_and_keeps_watermark(tmp_path, led):
    make_claude_code(tmp_path / "proj")
    stats = ingest("claude_code", tmp_path / "proj", led, ME, since="2026-01-01T10:00:10Z")
    assert stats["added"] == 1 and stats["before_since"] == 3  # only the queued message is later
    assert list(led.iter(kind="ingest"))[-1]["body"]["watermark"] == {}
    full = ingest("claude_code", tmp_path / "proj", led, ME)
    assert full["added"] == 3


def test_ledger_verifies_after_ingest(tmp_path, led):
    make_claude_code(tmp_path / "proj")
    ingest("claude_code", tmp_path / "proj", led, ME)
    assert led.verify()["ok"]


# --- hermes ------------------------------------------------------------------------------

def test_hermes(tmp_path, led):
    db = make_hermes(tmp_path / "profiles" / "alpha" / "state.db")
    before = db.read_bytes()
    stats = ingest("hermes", db, led, ME)
    ev = utterances(led)
    assert stats["added"] == 3 == len(ev)
    u, a, brief = (e["body"] for e in ev)
    assert ev[0]["author"] == ME and u["author_conf"] == "high" and u["role"] == "principal"
    assert u["profile"] == "alpha" and u["source"] == "cli" and u["hermes_session"] == "s-cli"
    assert u["src"] == {"adapter": "hermes", "key": "alpha:s-cli:1", "path": str(db.resolve()), "rowid": 1,
                        "message_uid": "uid1"}
    assert ev[0]["session"] == "hermes:alpha:s-cli"
    assert ev[1]["author"] == "hermes:alpha" and a["model"] == "model-h"
    assert brief["author_conf"] == "low" and brief["role"] == "user" and ev[2]["author"] == "hermes:unverified-user"
    assert not any("cron" in e["body"]["text"] for e in ev)
    assert list(led.iter(kind="ingest"))[-1]["body"]["watermark"] == {"max_id": 6,
                                                                     "sources": sorted(adapter("hermes").INTERACTIVE)}
    assert ingest("hermes", db.parent, led, ME)["added"] == 0  # directory form, idempotent
    assert db.read_bytes() == before  # read-only


def test_hermes_watermark_picks_up_new_rows(tmp_path, led):
    db = make_hermes(tmp_path / "state.db")
    ingest("hermes", db, led, ME)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s-cli','user','ok thanks',1767261700)")
    c.commit()
    c.close()
    s = ingest("hermes", db, led, ME)
    assert s["added"] == 1 and s["seen"] == 1
    assert utterances(led)[-1]["body"]["profile"] == "default"


def test_hermes_watermark_is_scoped_to_its_sources(tmp_path, led):
    """exo-desktop #54: a run that reads more session sources than the last one did reads the older rows too."""
    hermes = adapter("hermes")
    db = make_hermes(tmp_path / "state.db")
    ingest("hermes", db, led, ME)                                  # interactive sources: the cron row is skipped
    assert not any(e["body"]["text"] == "cron prompt" for e in utterances(led))
    wider = hermes.INTERACTIVE + ("cron",)
    s = hermes.ingest(db, led, ME, sources=wider)
    assert s["added"] == 1 and utterances(led)[-1]["body"]["text"] == "cron prompt"
    assert list(led.iter(kind="ingest"))[-1]["body"]["watermark"] == {"max_id": 6, "sources": sorted(wider)}
    assert "seen" not in hermes.ingest(db, led, ME, sources=wider)  # the wider set's own watermark now holds
    assert "seen" not in ingest("hermes", db, led, ME)              # a narrower set is covered by it too


def test_hermes_watermark_without_sources_counts_as_interactive(tmp_path, led):
    hermes = adapter("hermes")
    db = make_hermes(tmp_path / "state.db")
    led.append("ingest", {"adapter": "hermes", "source": str(db.resolve()), "watermark": {"max_id": 6}}, "ingest")
    assert "seen" not in ingest("hermes", db, led, ME)             # an older watermark still holds for the default
    assert hermes.ingest(db, led, ME, sources=("cron",))["added"] == 1
    hermes.ingest(db, led, ME, sources=())                        # reads nothing, so it covers nothing either
    assert ingest("hermes", db, led, ME)["seen"] == 4


# --- exports -------------------------------------------------------------------------------

def test_claude_ai_export(tmp_path, led):
    src = FIX / "claude_ai_conversations.json"
    stats = ingest("claude_ai_export", src, led, ME)
    ev = utterances(led)
    assert stats["added"] == 3 and stats["conversations"] == 2
    assert ev[0]["body"]["text"] == "which tomatoes grow well in shade?" and ev[0]["author"] == ME
    assert ev[1]["body"]["text"].startswith("Cherry") and ev[1]["author"] == "claude.ai"
    third = ev[2]["body"]
    assert third["redacted"] == 1 and "FAKEFAKE" not in third["text"] and third["attachments"] == ["plot.txt"]
    assert "bed A" not in json.dumps(third)
    assert third["src"]["conversation"] == "conv-aaaa-0001" and third["src"]["message"] == "msg-0003"
    assert ev[0]["session"] == "claude_ai:conv-aaaa-0001" and ev[0]["body"]["title"] == "Garden planning"
    assert ingest("claude_ai_export", src, led, ME)["added"] == 0


def test_claude_ai_export_zip(tmp_path, led):
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(FIX / "claude_ai_conversations.json", "data-2025/conversations.json")
    stats = ingest("claude_ai", z, led, ME)
    assert stats["added"] == 3
    assert utterances(led)[0]["body"]["src"]["member"] == "data-2025/conversations.json"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["exo-home", "export.zip", "ledger.sqlite", "ledger.sqlite-shm",
                                                          "ledger.sqlite-wal", "ledger.sqlite.head"]  # nothing extracted
    # (exo-home holds the second head anchor, D-040)


def test_chatgpt_export(tmp_path, led):
    src = FIX / "chatgpt_conversations.json"
    stats = ingest("chatgpt_export", src, led, ME)
    ev = utterances(led)
    by_node = {e["body"]["src"]["node"]: e for e in ev}
    assert stats["added"] == 3 and set(by_node) == {"n1", "n1b", "n4"}
    assert by_node["n1"]["body"]["branch"] == "main" and by_node["n1b"]["body"]["branch"] == "alt"
    assert by_node["n4"]["body"]["text"] == "About 4 to 12 hours." and by_node["n4"]["author"] == "chatgpt"
    assert by_node["n4"]["body"]["model"] == "gpt-test"
    assert by_node["n1"]["body"]["source_ts"] == "2025-02-19T21:20:10.000000Z"
    assert ingest("chatgpt", src, led, ME)["added"] == 0


def test_grok_export(tmp_path, led):
    d = tmp_path / "grok" / "ttl" / "export_data" / "abc"
    d.mkdir(parents=True)
    shutil.copy(FIX / "grok_backend.json", d / "prod-grok-backend.json")
    stats = ingest("grok_export", tmp_path / "grok", led, ME)
    ev = utterances(led)
    assert stats["added"] == 2
    assert ev[0]["author"] == ME and ev[0]["body"]["text"] == "why is the sky dark at night"
    assert ev[1]["author"] == "grok" and ev[1]["body"]["model"] == "grok-test"
    assert "secret reasoning" not in json.dumps([e["body"] for e in ev])
    assert ev[0]["body"]["source_ts"] == "2025-05-01T12:00:00.000000Z"
    assert ev[1]["body"]["src"]["response"] == "r2" and ev[1]["session"] == "grok:gk-conv-1"
    assert list(led.iter(kind="ingest"))[-1]["body"]["watermark"] == {"max_ts": "2025-05-01T12:00:10.000000Z"}
    assert ingest("grok", tmp_path / "grok", led, ME)["added"] == 0


def test_unknown_kind(led, tmp_path):
    with pytest.raises(KeyError):
        adapter("nope")


# --- cli ----------------------------------------------------------------------------------

def test_cli_ingest(tmp_path, capsys, monkeypatch):
    import argparse
    keyp = crypto.save_key(crypto.keygen(), tmp_path / "exo-home" / "keys" / "ingest.key")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--principal")
    common.add_argument("--root")
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    register(sp, common)
    lp = tmp_path / "cli-ledger.sqlite"
    a = ap.parse_args(["ingest", "grok", str(FIX / "grok_backend.json"), "--ledger", str(lp), "--author", ME])
    assert cmd_ingest(a) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 2 and out["adapter"] == "grok_export"
    assert oct(lp.stat().st_mode & 0o777) == "0o600"
    with Ledger(lp) as lg:
        ing = list(lg.iter(kind="ingest"))
        assert ing[0]["author"] == "ingest" and lg.verify()["ok"]
    assert keyp.exists()
