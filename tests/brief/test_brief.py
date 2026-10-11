import json
from datetime import datetime, timedelta, timezone

from exo import brief
from exo.ask import QuestionQueue, iso, utcnow
from exo.ledger import Ledger


def dec(root, *events):
    p = root / "queues" / "decisions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for e in events:
            f.write((e if isinstance(e, str) else json.dumps({"v": 1, "ts": "2026-10-10T10:00:00Z", **e})) + "\n")


def setup_store(root, signer):
    (root / "policies").mkdir(parents=True)
    led = Ledger(root / "ledger.sqlite", signer)
    a = led.append("artifact", {"project": "dashboard", "result": "Dashboard is live", "url": "https://x.test/d"})
    led.append("verdict", {"verdict": "accept", "target": a["id"]})
    led.append("action", {"project": "dashboard", "text": "refreshed data"})
    led.append("action", {"project": "ingest", "status": "failed", "text": "Hermes import failed on schema",
                          "path": "/tmp/log.txt"})
    dec(root,
        {"op": "open", "id": "d1", "title": "Merge the docs PR", "links": ["https://gh.test/pr/1"],
         "options": [{"key": "merge", "label": "Merge"}, {"key": "hold", "label": "Hold"}], "default": "merge"},
        {"op": "open", "id": "d2", "title": "Publish post?", "priority": "high", "reversible": False,
         "default": "hold"},
        {"op": "open", "id": "d3", "title": "Done one"},
        {"op": "resolve", "id": "d3", "choice": "x"},
        {"op": "open", "id": "d4", "title": "Snoozed", },
        {"op": "snooze", "id": "d4", "until": "2999-01-01T00:00:00Z"},
        "garbage")
    q = QuestionQueue(root, policy={})
    q.propose("Brief length?", "relationship/reporting", impact=0.9, uncertainty=0.9)
    q.propose("Low value?", impact=0.1, uncertainty=0.1)
    return led


def test_read_decisions_folds_and_orders(tmp_path):
    root = tmp_path
    (root / "queues").mkdir()
    (root / "queues" / "decisions.jsonl").write_text("not json\n")
    dec(root, {"op": "open", "id": "a", "title": "A", "priority": "low"},
        {"op": "open", "id": "b", "title": "B"},
        {"op": "update", "id": "b", "title": "B2", "status": "resolved"},
        {"op": "open", "id": "c", "title": "C", "deadline": "2000-01-01T00:00:00Z"})
    ds = brief.read_decisions(root / "queues" / "decisions.jsonl")
    assert [d["id"] for d in ds] == ["c", "b", "a"]
    assert ds[0]["overdue"] and ds[1]["title"] == "B2" and ds[1]["status"] == "open"


def test_brief_markdown_shape(tmp_path, signer):
    root = tmp_path / "s"
    led = setup_store(root, signer)
    b = brief.build_brief(root, ledger=led)
    md = b.to_markdown()
    assert md.startswith("# Morning brief")
    # result first, failures listed as blockers
    assert "**dashboard**: Dashboard is live. (1 action, 1 artifact, 1 accepted)" in md
    assert "**ingest**: Hermes import failed on schema `/tmp/log.txt`" in md
    # numbered decisions, irreversible high priority first, links and defaults
    assert "1. Publish post?. " not in md
    assert "1. Publish post? Default: hold." in md
    assert "2. Merge the docs PR [link](https://gh.test/pr/1). Default: Merge." in md
    assert "Done one" not in md and "Snoozed" not in md
    # questions ranked by voi, budget respected
    assert "1. Brief length? (`relationship/reporting`)" in md
    assert "Low value?" not in md                      # under min_voi
    assert md.index("**Blockers**") < md.index("**Decisions**") < md.index("**Questions**")
    html = b.to_html()
    assert '<a href="https://gh.test/pr/1">link</a>' in html and "<ol>" in html and "color-scheme:dark" in html


def test_policy_changes_format(tmp_path, signer):
    root = tmp_path / "s"
    led = setup_store(root, signer)
    (root / "policies" / "brief.yaml").write_text(
        "title: Daily note\norder: [decisions, status]\nmax_decisions: 1\nmax_questions: 0\n"
        "show_counts: false\ndecision_format: \"{n}) {title} -> {default}\"\nheadings:\n  decisions: Needs you\n")
    md = brief.build_brief(root, ledger=led).to_markdown()
    assert md.startswith("# Daily note")
    assert "**Needs you**\n1) Publish post? -> hold" in md
    assert "Merge the docs PR" not in md and "Questions" not in md and "Blockers" not in md
    assert "(1 action" not in md
    assert md.index("Needs you") < md.index("Status")


def test_questions_respect_interruption_budget(tmp_path, signer):
    root = tmp_path / "s"
    led = setup_store(root, signer)
    (root / "policies" / "interruptions.yaml").write_text("questions_per_day: 0\n")
    b = brief.build_brief(root, ledger=led)
    assert b.questions == [] and b.budget["remaining"] == 0


def test_mark_asked_and_window(tmp_path, signer):
    root = tmp_path / "s"
    led = setup_store(root, signer)
    (root / "policies" / "brief.yaml").write_text("mark_asked: true\nwindow_hours: 1\n")
    later = utcnow() + timedelta(hours=3)
    b = brief.build_brief(root, ledger=led, now=later)
    assert b.status == [] and b.blockers == []
    assert "Nothing new in the window." in b.to_markdown()
    assert QuestionQueue(root, policy={}).state()[b.questions[0]["id"]].status == "asked"


def test_empty_store_and_write(tmp_path):
    b = brief.build_brief(tmp_path)
    assert "Nothing new" in b.to_markdown()
    paths = brief.write_brief(tmp_path, b)
    assert paths["markdown"].exists() and paths["html"].read_text().startswith("<!doctype html>")
