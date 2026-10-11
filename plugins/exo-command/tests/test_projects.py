from exo_command import projects
from exo_command.ro import discover_profiles


def _board(world, now):
    profs = discover_profiles(world["home"])
    return projects.board(profs, generic_paths=["/home/someone"], now=now, walkup=False)


def _cards(b):
    return [c for lane in b["lanes"] for c in lane["cards"]]


def _find(b, member):
    for c in _cards(b):
        if any(s["ref"] == member for s in c["sessions"]):
            return c
    return None


def test_shared_git_repo_is_high_band_across_profiles(world, now):
    b = _board(world, now)
    c = _find(b, "alpha/a1")
    refs = {s["ref"] for s in c["sessions"]}
    assert {"alpha/a1", "alpha/a2", "beta/b1"} <= refs
    assert c["band"] == "high"
    assert "CS-1" in c["signals"]
    assert c["profiles"] == ["alpha", "beta"]


def test_subagent_child_folds_into_parent_and_cron_never_joins(world, now):
    c = _find(_board(world, now), "alpha/a1")
    refs = {s["ref"] for s in c["sessions"]}
    assert "alpha/a3" in refs       # NS-2 child folded via CS-4
    assert "alpha/a4" not in refs   # NS-1 cron never seeds or joins


def test_newest_session_awaiting_principal_lands_in_waiting_lane(world, now):
    c = _find(_board(world, now), "alpha/a1")
    assert c["lane"] == "waiting_on_you"
    assert c["lifecycle"] == "LS-4"


def test_workspace_cluster_is_medium_and_stalled(world, now):
    c = _find(_board(world, now), "alpha/a5")
    assert c["band"] == "medium"
    assert "CS-2" in c["signals"]
    assert c["lane"] == "paused"
    assert c["stall_age_days"] >= 19


def test_generic_path_never_clusters(world, now):
    b = _board(world, now)
    assert _find(b, "alpha/a7") is None
    assert _find(b, "alpha/a8") is None
    for c in _cards(b):
        assert not any(k.endswith(":/home/someone") for k in c["keys"])


def test_evidence_tiers(world, now):
    b = _board(world, now)
    assert _find(b, "alpha/a1")["evidence_tier"] == 1
    kinds = {e["kind"] for e in _find(b, "alpha/a1")["evidence"]}
    assert {"goal", "next_action"} & kinds
    assert _find(b, "alpha/a5")["evidence_tier"] == 0
    assert _find(b, "alpha/a5")["needs_review"] is True


def test_counts_and_noise(world, now):
    b = _board(world, now)
    assert b["counts"]["projects"] == 3
    folded = _find(b, "default/r1")   # parent plus its delegated child only
    assert folded["band"] == "low" and folded["signals"] == ["CS-4"]
    assert b["counts"]["noise"].get("NS-1") == 1
    assert [l["id"] for l in b["lanes"]][0] == "waiting_on_you"


def test_window_excludes_old_sessions(world, now):
    profs = discover_profiles(world["home"])
    b = projects.board(profs, window_days=10, now=now, walkup=False)
    assert _find(b, "alpha/a5") is None
