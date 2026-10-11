"""Builds a small fake Hermes home with the same table and column names the plugin reads."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

STATE_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, chat_type TEXT, display_name TEXT, model TEXT,
    parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, end_reason TEXT,
    message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT, git_branch TEXT, git_repo_root TEXT, billing_provider TEXT,
    estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
    title TEXT, title_source TEXT, last_activity_at REAL, last_activity_description TEXT,
    archived INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
    tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL, reasoning TEXT
);
CREATE TABLE session_model_usage (
    session_id TEXT NOT NULL, model TEXT NOT NULL, billing_provider TEXT NOT NULL DEFAULT '',
    billing_base_url TEXT NOT NULL DEFAULT '', billing_mode TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0, reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0, actual_cost_usd REAL NOT NULL DEFAULT 0,
    cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL,
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task)
);
CREATE TABLE async_delegations (
    delegation_id TEXT PRIMARY KEY, origin_session TEXT NOT NULL, parent_session_id TEXT,
    state TEXT NOT NULL, dispatched_at REAL NOT NULL, completed_at REAL, updated_at REAL NOT NULL,
    task_json TEXT
);
"""

KANBAN_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT, status TEXT NOT NULL,
    priority INTEGER DEFAULT 0, created_by TEXT, created_at INTEGER NOT NULL, started_at INTEGER,
    completed_at INTEGER, result TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_error TEXT, last_heartbeat_at INTEGER, session_id TEXT, block_kind TEXT
);
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, author TEXT NOT NULL,
    body TEXT NOT NULL, created_at INTEGER NOT NULL
);
"""

DAY = 86400.0


def _session(c, **kw):
    cols = ", ".join(kw)
    c.execute("INSERT INTO sessions ({}) VALUES ({})".format(cols, ",".join("?" * len(kw))), list(kw.values()))


def _msg(c, sid, role, content, ts, **kw):
    data = dict(session_id=sid, role=role, content=content, timestamp=ts, **kw)
    c.execute("INSERT INTO messages ({}) VALUES ({})".format(", ".join(data), ",".join("?" * len(data))),
              list(data.values()))


def _db(path: Path, schema: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.executescript(schema)
    return c


def build(root: Path, now: float, home_dir: str = "/home/someone") -> dict:
    """Return paths. ``home_dir`` stands in for a generic cwd that must never cluster."""
    home = root / "hermes"
    store = root / "store"

    # ---------------------------------------------------------------- profile alpha
    c = _db(home / "profiles" / "alpha" / "state.db", STATE_SCHEMA)
    repo = "/srv/work/repo-one"
    _session(c, id="a1", source="cli", title="repo one: add parser", git_repo_root=repo, cwd=repo,
             started_at=now - 3 * DAY, last_activity_at=now - 2 * DAY, message_count=12, tool_call_count=5,
             model="model-x", estimated_cost_usd=0.40, actual_cost_usd=0.0, cost_status="estimated",
             input_tokens=1000, output_tokens=200)
    _msg(c, "a1", "user", "Goal: build the parser for repo one", now - 3 * DAY)
    _msg(c, "a1", "assistant", "Next step: wire the parser into the CLI.", now - 2 * DAY)
    _session(c, id="a2", source="cli", title="repo one: tests", git_repo_root=repo, cwd=repo + "/tests",
             started_at=now - 2 * DAY, last_activity_at=now - DAY, message_count=8, tool_call_count=3,
             model="model-x", estimated_cost_usd=0.30, actual_cost_usd=0.25, cost_status="included")
    _msg(c, "a2", "user", "write tests for the parser", now - 2 * DAY)
    _msg(c, "a2", "assistant", "Tests written, all green.", now - DAY)
    # live subagent child of a1
    _session(c, id="a3", source="subagent", title=None, parent_session_id="a1", cwd=repo,
             started_at=now - 300, last_activity_at=now - 30, message_count=4, tool_call_count=2,
             model="model-y", last_activity_description="running tests")
    _msg(c, "a3", "user", "Run the parser test suite and report failures", now - 300)
    _msg(c, "a3", "assistant", "", now - 200, tool_calls=json.dumps([{"function": {"name": "terminal"}}]))
    _msg(c, "a3", "tool", "12 passed in 0.4s", now - 190, tool_name="terminal")
    _msg(c, "a3", "assistant", "All 12 tests pass. Coverage 91 percent.", now - 30, reasoning="checking output")
    # cron noise sharing the repo: must never seed or join
    _session(c, id="a4", source="cron", title="nightly sync", git_repo_root=repo,
             started_at=now - DAY, last_activity_at=now - DAY, message_count=2)
    # workspace cluster (CS-2), quiet for 20 days -> stalled
    _session(c, id="a5", source="cli", title="proj two api", cwd="/srv/work/proj-two/api",
             started_at=now - 25 * DAY, last_activity_at=now - 20 * DAY, message_count=10, tool_call_count=4)
    _msg(c, "a5", "assistant", "API scaffolded.", now - 20 * DAY)
    _session(c, id="a6", source="cli", title="proj two web", cwd="/srv/work/proj-two/web",
             started_at=now - 24 * DAY, last_activity_at=now - 21 * DAY, message_count=6, tool_call_count=1)
    # generic cwd: two sessions in the home dir must NOT cluster
    _session(c, id="a7", source="cli", title="misc question one", cwd=home_dir, git_repo_root=home_dir,
             started_at=now - 5 * DAY, last_activity_at=now - 5 * DAY, message_count=3)
    _session(c, id="a8", source="cli", title="misc question two", cwd=home_dir, git_repo_root=home_dir,
             started_at=now - 4 * DAY, last_activity_at=now - 4 * DAY, message_count=3)
    c.execute("INSERT INTO session_model_usage (session_id, model, api_call_count, input_tokens, output_tokens, "
              "estimated_cost_usd, actual_cost_usd, last_seen) VALUES ('a1','model-x',4,1000,200,0.40,0,?)", (now,))
    c.commit()
    c.close()

    # ---------------------------------------------------------------- profile beta
    c = _db(home / "profiles" / "beta" / "state.db", STATE_SCHEMA)
    _session(c, id="b1", source="discord", title="repo one release", git_repo_root=repo, cwd=repo,
             started_at=now - 3600, last_activity_at=now - 3000, message_count=6, tool_call_count=1,
             model="model-z", estimated_cost_usd=1.50, actual_cost_usd=2.00, cost_status="actual")
    _msg(c, "b1", "user", "prepare the release notes", now - 3600)
    _msg(c, "b1", "assistant", "Release notes drafted. Should I tag v1.0 now? Please confirm.", now - 3000)
    c.commit()
    c.close()

    # ---------------------------------------------------------------- root profile ("default")
    c = _db(home / "state.db", STATE_SCHEMA)
    _session(c, id="r1", source="cli", title="orchestrator", started_at=now - 7200, ended_at=now - 600,
             last_activity_at=now - 600, message_count=20, cwd="/srv/work/other")
    _session(c, id="r2", source="subagent", title="research worker", parent_session_id="r1",
             started_at=now - 7000, ended_at=now - 1200, end_reason="error: provider timeout",
             last_activity_at=now - 1200, message_count=5, model="model-y")
    _msg(c, "r2", "assistant", "Partial findings before the provider failed.", now - 1200)
    c.execute("INSERT INTO async_delegations VALUES ('deleg_1','r1','r1','running',?,NULL,?,?)",
              (now - 400, now - 100, json.dumps({"goal": "2 parallel workers", "goals": ["audit", "write"]})))
    c.execute("INSERT INTO async_delegations VALUES ('deleg_0','r1','r1','completed',?,?,?,?)",
              (now - 5000, now - 4000, now - 4000, json.dumps({"goal": "old fan-out"})))
    c.commit()
    c.close()

    # ---------------------------------------------------------------- kanban
    c = _db(home / "kanban.db", KANBAN_SCHEMA)
    rows = [("t1", "triage me", "triage", 0, None), ("t2", "build feature", "running", 2, None),
            ("t3", "needs creds", "blocked", 1, "missing token"), ("t4", "old", "archived", 0, None)]
    for i, (tid, title, status, prio, err) in enumerate(rows):
        c.execute("INSERT INTO tasks (id, title, status, priority, created_at, last_failure_error, assignee) "
                  "VALUES (?,?,?,?,?,?,?)", (tid, title, status, prio, int(now) - i * 100, err, "worker"))
    for i in range(25):
        c.execute("INSERT INTO tasks (id, title, status, created_at, completed_at) VALUES (?,?,?,?,?)",
                  ("d%02d" % i, "done %d" % i, "done", int(now) - 10000, int(now) - i))
    c.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES ('t3','w','stuck',?)", (int(now),))
    c.commit()
    c.close()
    bdir = home / "kanban" / "boards" / "site"
    c = _db(bdir / "kanban.db", KANBAN_SCHEMA)
    c.execute("INSERT INTO tasks (id, title, status, created_at) VALUES ('s1','ship site','review',?)", (int(now),))
    c.commit()
    c.close()
    (bdir / "board.json").write_text(json.dumps({"slug": "site", "name": "Website"}))

    # ---------------------------------------------------------------- principal store
    q = store / "queues" / "decisions.jsonl"
    q.parent.mkdir(parents=True)
    events = [
        {"v": 1, "op": "open", "id": "dec-1", "ts": now - 3600, "title": "Publish the release?",
         "options": [{"key": "a", "label": "Publish"}, {"key": "b", "label": "Hold"}], "default": "b",
         "reversible": False, "exposure": "public", "asked_by": {"agent": "release-bot", "profile": "beta"}},
        {"v": 1, "op": "open", "id": "dec-2", "ts": now - 7200, "title": "Pick a model for the eval",
         "deadline": now - 60, "priority": "low"},
        {"v": 1, "op": "open", "id": "dec-3", "ts": now - 100, "title": "Rename the repo?"},
        {"v": 1, "op": "resolve", "id": "dec-3", "ts": now - 50, "choice": "no", "by": "principal"},
        {"v": 1, "op": "open", "id": "dec-4", "ts": now - 100, "title": "Later question"},
        {"v": 1, "op": "snooze", "id": "dec-4", "ts": now - 90, "until": now + 3600},
        {"v": 1, "op": "update", "id": "dec-1", "ts": now - 30, "context": "Changelog is ready."},
    ]
    lines = [json.dumps(e) for e in events] + ["{not json", json.dumps({"op": "resolve", "id": "ghost", "ts": now})]
    q.write_text("\n".join(lines) + "\n")
    return {"home": home, "store": store, "queue": q}
