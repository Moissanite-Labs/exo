import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from conftest import PLUGIN_DIR
from exo_command.config import load_settings
from exo_command.service import CommandCentre


def test_defaults_are_generic(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h" / "profiles" / "p1"))
    s = load_settings(use_hermes_config=False)
    assert s.principal_store is None and s.decisions_queue is None
    assert s.profiles == []


def test_env_and_relative_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    monkeypatch.setenv("EXO_PRINCIPAL", "p")
    monkeypatch.setenv("EXO_COMMAND_PROFILES", "alpha,beta")
    monkeypatch.setenv("EXO_COMMAND_LIVE_WINDOW_SECONDS", "60")
    s = load_settings(use_hermes_config=False)
    assert s.decisions_queue == tmp_path / "principals" / "p" / "queues" / "decisions.jsonl"
    assert s.profiles == ["alpha", "beta"] and s.live_window_seconds == 60
    s2 = load_settings({"decisions_queue": "/abs/q.jsonl"}, use_hermes_config=False)
    assert s2.decisions_queue == Path("/abs/q.jsonl")


def test_no_hardcoded_identity():
    pat = re.compile(r"kethuda|/Users/|hafiz|askaconsult|ahraz", re.I)
    hits = []
    for p in PLUGIN_DIR.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".js", ".json", ".yaml", ".css") and "tests" not in p.parts:
            if pat.search(p.read_text(errors="ignore")):
                hits.append(str(p.relative_to(PLUGIN_DIR)))
    assert hits == []


def test_profile_scope(world, settings, now):
    settings.profiles = ["beta"]
    snap = CommandCentre(settings).snapshot(now=now, only=["costs"])
    assert snap["profiles"] == ["beta"]
    assert {p["profile"] for p in snap["costs"]["by_profile"]} == {"beta"}


def test_section_isolation(world, settings, now):
    (world["home"] / "profiles" / "broken").mkdir()
    (world["home"] / "profiles" / "broken" / "state.db").write_bytes(b"not a database at all" * 100)
    snap = CommandCentre(settings).snapshot(now=now)
    assert snap["agents"]["ok"] and snap["projects"]["ok"]
    statuses = {s["profile"]: s["status"] for s in snap["agents"]["sources"]}
    assert statuses["broken"].startswith("error")
    assert statuses["alpha"] == "ok"


def test_summary_text(world, settings, now):
    text = CommandCentre(settings).summary(now=now)
    assert "Decisions waiting: 2 (1 overdue)." in text
    assert "Publish the release?" in text
    assert "Kanban: 4 open" in text


def _load_root_plugin():
    spec = importlib.util.spec_from_file_location(
        "exo_command_plugin", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exo_command_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_register_adds_slash_command(world, monkeypatch):
    mod = _load_root_plugin()

    class Ctx:
        def __init__(self):
            self.commands = {}

        def register_command(self, name, handler, description="", **kw):
            self.commands[name] = handler

    ctx = Ctx()
    mod.register(ctx)
    assert list(ctx.commands) == ["exo-command"]
    monkeypatch.setenv("EXO_COMMAND_HERMES_HOME", str(world["home"]))
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(world["store"]))
    out = ctx.commands["exo-command"]("")
    assert "Decisions waiting: 2" in out


def test_manifests():
    m = json.loads((PLUGIN_DIR / "dashboard" / "manifest.json").read_text())
    assert m["name"] == "exo-command" and m["api"] == "plugin_api.py"
    for key in ("entry", "css", "api"):
        assert (PLUGIN_DIR / "dashboard" / m[key]).is_file()
    yaml_text = (PLUGIN_DIR / "plugin.yaml").read_text()
    assert yaml_text.startswith("name: exo-command")
    for key in ("hermes_home", "profiles", "principal_store", "decisions_queue"):
        assert "\n  {}:".format(key) in yaml_text
    js = (PLUGIN_DIR / "dashboard" / "dist" / "index.js").read_text()
    assert 'register("exo-command"' in js
    assert "import " not in js and "require(" not in js  # no bundler, no deps


def test_dashboard_router(world, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("EXO_COMMAND_HERMES_HOME", str(world["home"]))
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(world["store"]))
    spec = importlib.util.spec_from_file_location("exo_cmd_api", PLUGIN_DIR / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/exo-command")
    client = TestClient(app)
    r = client.get("/api/plugins/exo-command/snapshot?fresh=true")
    assert r.status_code == 200
    body = r.json()
    assert body["decisions"]["counts"]["waiting"] == 2
    assert body["kanban"]["open"] == 4
    assert client.get("/api/plugins/exo-command/section/agents").json()["counts"]["active"] >= 1
    assert client.get("/api/plugins/exo-command/section/nope").status_code == 404
    assert client.post("/api/plugins/exo-command/snapshot").status_code == 405
