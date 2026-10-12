import sqlite3

from exo.ingest import hermes_dashboard as hd
from exo.ingest.hermes_dashboard import ProfileDb, discover_profiles


def test_cost_panel(world, now):
    d = hd.cost_per_session(discover_profiles(world["home"]), window_days=7, now=now)
    assert d["available"]
    top = d["top"][0]
    assert top["session_id"] == "b1" and top["cost_usd"] == 2.0 and top["estimated"] is False
    by_id = {s["session_id"]: s for s in d["top"]}
    assert by_id["a1"]["cost_usd"] == 0.40 and by_id["a1"]["estimated"] is True
    assert by_id["a2"]["cost_usd"] == 0.25      # actual beats estimated
    assert round(d["total_usd"], 2) == 2.65
    assert d["priced_sessions"] == 3
    models = {m["model"]: m for m in d["by_model"]}
    assert models["model-x"]["api_calls"] == 4
    assert {p["profile"] for p in d["by_profile"]} == {"alpha", "beta", "default"}


def test_unavailable_on_schema_without_costs(tmp_path, now):
    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL)")
    c.commit()
    c.close()
    d = hd.cost_per_session([ProfileDb("old", db)], now=now)
    assert d["available"] is False and d["top"] == []
