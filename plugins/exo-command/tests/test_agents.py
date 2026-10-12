from exo.ingest import hermes_dashboard as hd
from exo.ingest.hermes_dashboard import discover_profiles


def test_live_subagent_card(world, now):
    d = hd.live_agents(discover_profiles(world["home"]), live_window=900, now=now)
    card = [a for a in d["active"] if a["session_id"] == "a3"][0]
    assert card["kind"] == "subagent" and card["status"] == "running"
    assert card["profile"] == "alpha" and card["model"] == "model-y"
    assert card["tool"].startswith("terminal")
    assert "12 tests pass" in card["output"]
    assert card["thinking"] == "checking output"
    assert card["goal"].startswith("Run the parser")
    # ended or stale sessions are not live
    assert not any(a["session_id"] in ("a1", "r1", "r2") for a in d["active"])


def test_past_subagents_and_status(world, now):
    d = hd.live_agents(discover_profiles(world["home"]), now=now)
    past = {a["session_id"]: a for a in d["past"]}
    assert past["r2"]["status"] == "errored"
    assert "r1" not in past  # top-level sessions are not subagents


def test_delegations(world, now):
    d = hd.live_agents(discover_profiles(world["home"]), now=now)
    active = [x for x in d["delegations"] if x["active"]]
    assert [x["delegation_id"] for x in active] == ["deleg_1"]
    assert active[0]["workers"] == 2
    assert d["counts"]["delegations_active"] == 1


def test_profile_filter(world, now):
    d = hd.live_agents(discover_profiles(world["home"], only=["beta"]), now=now)
    assert {s["profile"] for s in d["sources"]} == {"beta"}
