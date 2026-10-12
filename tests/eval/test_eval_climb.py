import json
from datetime import datetime, timezone

import pytest

from exo import eval as E
from exo.ledger import Ledger


def items(n=30, domain="mind"):
    return [{"id": f"q{i}", "question": f"Write line {i}", "gold": f"line {i}", "domain": domain,
             "ts": f"2026-09-{(i % 28) + 1:02d}T00:00:00Z"} for i in range(n)]


def good(prompt):
    return "line " + prompt.rsplit(" ", 1)[-1]


def bad(prompt):
    return "nope"


# --- graders ---------------------------------------------------------------------------------------

def test_judge_grader_averages_samples_and_skips_failures():
    scores = iter(["1", "0", "boom"])

    def complete(prompt):
        s = next(scores)
        if s == "boom":
            raise RuntimeError("down")
        return s
    g = E.judge_grader(complete, rubric="r", samples=3)
    assert g({"question": "q", "gold": "g"}, "a") == 0.5 and g.samples == 3
    assert E.judge_grader(lambda p: 1 / 0, samples=2)({"gold": "g"}, "a") == 0.0


def test_judge_prompt_carries_rubric_gold_and_rejected():
    seen = []
    E.judge_grader(lambda p: seen.append(p) or "1", rubric="Be plain", samples=1)(
        {"question": "ask", "chosen": "good one", "rejected": "bad one"}, "my answer")
    p = seen[0]
    assert "Be plain" in p and "good one" in p and "bad one" in p and "my answer" in p


def test_rule_grader_forbid_require_and_domains():
    checks = [{"id": "no-dash", "forbid": "\u2014"}, {"id": "has-link", "require": "https?://", "domains": ["relationship"]}]
    g = E.rule_grader(checks)
    assert g({"domain": "mind"}, "plain text") == 1.0
    assert g({"domain": "mind"}, "a \u2014 b") == 0.0
    assert g({"domain": "relationship"}, "no link here") == 0.0
    assert g({"domain": "relationship"}, "see https://x.example") == 1.0
    assert E.rule_violations(checks, {"domain": "relationship"}, "\u2014") == ["no-dash", "has-link"]
    assert E.rule_violations([lambda it, ans: "ok" in ans], {}, "no") == ["<lambda>"]
    with pytest.raises(ValueError):
        E.rule_violations([{"id": "x"}], {}, "a")


def test_rules_run_first_and_the_judge_is_skipped_on_a_break():
    calls = []
    grader = E.judge_grader(lambda p: calls.append(p) or "1", samples=1)
    res = E.score_items(items(2), lambda p: "bad \u2014 text", grader, rules=[{"forbid": "\u2014"}])
    assert calls == [] and res["composite"] == 0.0 and not res["rows"][0]["rule_ok"]


# --- replays and split -----------------------------------------------------------------------------

@pytest.fixture
def led(tmp_path, signer):
    l = Ledger(tmp_path / "l.sqlite", signer)
    yield l
    l.close()


def test_build_replays_from_verdicts_answers_and_rulings(led):
    u = led.append("utterance", {"text": "Write the status", "role": "user"}, "p", session="s")
    a = led.append("utterance", {"text": "Status :)", "role": "assistant"}, "a", session="s")
    led.append("verdict", {"verdict": "edit", "target": a["id"], "chosen": "Status"}, "p", session="s")
    led.append("verdict", {"verdict": "accept", "target": a["id"]}, "p", session="s")
    led.append("verdict", {"verdict": "reject", "target": a["id"]}, "p", session="s")
    q = led.append("question", {"text": "Which branch ships?"}, "a", session="s")
    led.append("answer", {"text": "main"}, "p", session="s", refs=[q["id"]])
    u2 = led.append("utterance", {"text": "Going forward keep it short", "role": "user"}, "p", session="s2")
    led.append("ruling", {"text": "Keep it short."}, "p", session="s2")
    rs = E.build_replays(led)
    by = {r["verdict"]: r for r in rs}
    assert sorted(by) == ["accept", "answer", "edit", "ruling"]          # the reject has no better version
    assert by["edit"]["question"] == "Write the status" and by["edit"]["gold"] == "Status"
    assert by["edit"]["rejected"] == "Status :)"
    assert by["accept"]["gold"] == "Status :)" and by["answer"]["question"] == "Which branch ships?"
    assert by["ruling"]["question"] == "Going forward keep it short"
    assert E.build_replays(led, cutoff="2000-01-01T00:00:00Z") == []


def test_split_has_a_fixed_held_out_sample():
    pool = items(50)
    a = E.split(pool)
    assert len(a["train"]) + len(a["dev"]) + len(a["held"]) == 50
    assert 4 <= len(a["held"]) <= 16
    # adding items never moves an item between held-out and the rest
    b = E.split(pool + items(80)[50:])
    held_a = {i["id"] for i in a["held"]}
    assert held_a <= {i["id"] for i in b["held"]}
    # dev is newer than train
    assert max(i["ts"] for i in a["train"]) <= min(i["ts"] for i in a["dev"])
    tiny = E.split(items(3))
    assert len(tiny["held"]) == 1


# --- compare and gate ------------------------------------------------------------------------------

def test_compare_reports_deltas_noise_and_domains():
    pool = items(10) + [{**i, "id": "r" + i["id"], "domain": "work"} for i in items(10)]

    def half(prompt):          # right on even lines only
        return good(prompt) if int(prompt.rsplit(" ", 1)[-1]) % 2 == 0 else "no"
    cmp = E.compare(half, good, pool, noise=0.0, margin=0.01)
    assert cmp["inc"] == 0.5 and cmp["cand"] == 1.0 and cmp["delta"] == 0.5 and cmp["better"]
    assert cmp["by_domain"]["mind"]["delta"] == 0.5 and cmp["by_domain"]["work"]["n"] == 10
    assert len(cmp["improved"]) == 10 and cmp["worse"] == []
    assert not E.compare(half, good, pool, noise=0.6)["better"]               # inside the noise band
    assert not E.compare(good, good, pool)["better"]
    assert E.noise_band([0.70, 0.72, 0.71]) == 0.02 and E.noise_band([0.7]) == 0.0


def test_gate_failures():
    base = {"delta": 0.0, "by_domain": {}, "items": {}}
    assert E.gate(base)["pass"]
    assert E.gate({**base, "delta": -0.05})["failures"][0]["gate"] == "held_out_drop"
    rule = {**base, "items": {"a": {"domain": "mind", "inc": 1.0, "cand": 0.0, "inc_rule_ok": True,
                                    "cand_rule_ok": False, "pinned": False}}}
    assert [f["gate"] for f in E.gate(rule)["failures"]] == ["rule_regression"]
    drop = {**base, "items": {"a": {"domain": "public", "inc": 1.0, "cand": 0.9, "inc_rule_ok": True,
                                    "cand_rule_ok": True, "pinned": False}}}
    assert E.gate(drop)["pass"]
    assert [f["gate"] for f in E.gate(drop, {"guarded_domains": ["public"]})["failures"]] == ["guarded_domain"]
    pin = {**base, "items": {"p": {"domain": "mind", "inc": 0.9, "cand": 0.2, "inc_rule_ok": True,
                                   "cand_rule_ok": True, "pinned": True}}}
    assert E.gate(pin)["failures"][0]["gate"] == "pinned"
    dom = {**base, "by_domain": {"mind": {"n": 3, "inc": 0.9, "cand": 0.5, "delta": -0.4}}}
    assert E.gate(dom)["failures"][0]["gate"] == "domain_drop"


def test_scoreboard_rows_html_and_weekly_line(tmp_path):
    E.scoreboard(tmp_path, {"incumbent": "cfg-a", "composite": 0.71, "parts": {"qa": 0.71}, "cost": 40})
    E.scoreboard(tmp_path, {"incumbent": "cfg-b", "composite": 0.74, "change": {"id": "add:p-1", "status": "kept"}})
    out = E.scoreboard(tmp_path, {"incumbent": "cfg-b", "composite": None, "change": {"id": "x", "status": "reverted"}})
    assert out["line"] == "score 0.71 to 0.74 this week, 1 changes kept, 1 reverted"
    assert len(out["rows"]) == 3 and "add:p-1" in out["html"].read_text()
    assert E.scoreboard(tmp_path / "empty")["line"] == "no eval runs this week"


# --- the climb -------------------------------------------------------------------------------------

def responder_for(cfg):
    """A configuration is a dict {"skill": "good" | "bad" | None, "calls": counter}."""
    def respond(prompt):
        cfg.get("calls", [0])[0] += 1
        return good(prompt) if cfg.get("skill") == "good" else "line ?"
    return respond


def run_climb(tmp_path, cands, *, inc=None, pool=None, budget=None, **kw):
    return E.climb(tmp_path, budget or E.Budget(), incumbent={"id": "inc", "config": inc or {"skill": None}},
                   candidates=cands, responder_for=responder_for, grader=E.exact_grader, rules=[],
                   items=pool or items(40), **kw)


def test_climb_accepts_a_better_candidate_and_caches_the_incumbent(tmp_path):
    rep = run_climb(tmp_path, [{"id": "c1", "config": {"skill": "good"}}])
    assert rep["accepted"] == ["c1"] and rep["incumbent"] == "c1"
    c = rep["candidates"][0]
    assert c["status"] == "accepted" and c["dev"]["delta"] > 0 and c["held"]["delta"] > 0
    cache = json.loads((tmp_path / "eval" / "incumbent.json").read_text())
    assert set(cache["key"]) == {"cfg", "items"}
    # the next night starts from the cached incumbent with no rescoring of it
    n = [0]
    rep2 = E.climb(tmp_path, E.Budget(), incumbent={"id": "c1", "config": {"skill": "good"}}, candidates=[],
                   responder_for=lambda cfg: (lambda p: (n.__setitem__(0, n[0] + 1) or good(p))), grader=E.exact_grader,
                   rules=[], items=items(40))
    assert n[0] == 0 and rep2["incumbent"] == "c1" and rep2["start_composite"] == 1.0


def test_climb_drops_worse_equal_and_within_noise_candidates(tmp_path):
    rep = run_climb(tmp_path, [{"id": "same", "config": {"skill": None}}], inc={"skill": "good"})
    assert rep["accepted"] == [] and rep["candidates"][0]["status"] == "dropped"
    rep = run_climb(tmp_path / "b", [{"id": "worse", "config": {"skill": None}}], inc={"skill": "good"})
    assert rep["candidates"][0]["reason"].startswith("not better")


def test_climb_margin_is_enforced(tmp_path):
    def rf(cfg):
        k = cfg["k"]
        return lambda p: good(p) if int(p.rsplit(" ", 1)[-1]) < k else "x"
    pool = items(40)
    kw = dict(incumbent={"id": "i", "config": {"k": 0}}, responder_for=rf, grader=E.exact_grader, rules=[], items=pool)
    tiny = E.climb(tmp_path, E.Budget(margin=0.5), candidates=[{"id": "c", "config": {"k": 3}}], **kw)
    assert tiny["accepted"] == []


def test_climb_rejects_a_dev_win_that_loses_on_held_out(tmp_path):
    pool = items(40)
    held_ids = {i["id"] for i in E.split(pool)["held"]}

    def rf(cfg):
        def respond(prompt):
            k = prompt.rsplit(" ", 1)[-1]
            on_held = f"q{k}" in held_ids
            if cfg["mode"] == "overfit":      # right everywhere except held-out items
                return "x" if on_held else good(prompt)
            return good(prompt) if on_held else "x"     # the incumbent is right only on held-out items
        return respond
    rep = E.climb(tmp_path, E.Budget(), incumbent={"id": "i", "config": {"mode": "none"}},
                  candidates=[{"id": "o", "config": {"mode": "overfit"}}], responder_for=rf, grader=E.exact_grader,
                  rules=[], items=pool)
    c = rep["candidates"][0]
    assert c["dev"]["better"] and c["status"] != "accepted" and rep["accepted"] == []


def test_climb_gate_blocks_a_rule_regression_and_a_pinned_miss(tmp_path):
    pool = items(40)
    held = E.split(pool)["held"]
    victim = held[0]["id"]

    def rf(cfg):
        def respond(prompt):
            if cfg["mode"] == "base":
                return "line ?"
            return "BROKEN" if f"q{prompt.rsplit(' ', 1)[-1]}" == victim else good(prompt)
        return respond
    rules = [{"id": "no-broken", "forbid": "BROKEN"}]
    rep = E.climb(tmp_path, E.Budget(), incumbent={"id": "i", "config": {"mode": "base"}},
                  candidates=[{"id": "c", "config": {"mode": "cand"}}], responder_for=rf, grader=E.exact_grader,
                  rules=rules, items=pool)
    c = rep["candidates"][0]
    assert c["dev"]["better"] and c["held"]["delta"] > 0          # it wins on dev and on held-out overall
    assert c["status"] == "gate_failed" and "rule_regression" in c["reason"] and rep["accepted"] == []

    # a pinned item that stays wrong blocks it too
    pool2 = [dict(i) for i in items(40)]
    for it in pool2:
        if it["id"] == victim:
            it["pinned"] = True

    def rf2(cfg):
        return lambda p: "line ?" if cfg["mode"] == "base" or f"q{p.rsplit(' ', 1)[-1]}" == victim else good(p)
    rep = E.climb(tmp_path / "b", E.Budget(), incumbent={"id": "i", "config": {"mode": "base"}},
                  candidates=[{"id": "c", "config": {"mode": "cand"}}], responder_for=rf2, grader=E.exact_grader,
                  rules=[], items=pool2)
    assert rep["candidates"][0]["status"] == "gate_failed" and "pinned" in rep["candidates"][0]["reason"]


def test_climb_caps_candidates_and_calls(tmp_path):
    counter = [0]
    cands = [{"id": f"c{i}", "config": {"skill": None, "calls": counter}} for i in range(25)]
    rep = run_climb(tmp_path, cands, inc={"skill": None, "calls": counter})
    assert len(rep["candidates"]) == 10                       # default cap per night
    rep = run_climb(tmp_path / "b", cands, inc={"skill": None, "calls": counter}, budget=E.Budget(candidates=3))
    assert len(rep["candidates"]) == 3
    rep = run_climb(tmp_path / "c", cands, inc={"skill": None, "calls": counter}, budget=E.Budget(max_calls=50))
    assert rep["calls"] <= 50 and any(c["status"] == "stopped" for c in rep["candidates"])


def test_climb_only_rescores_the_candidates_domains(tmp_path):
    pool = items(20, "mind") + [{**i, "id": "w" + i["id"], "domain": "work"} for i in items(20)]
    seen = []

    def rf(cfg):
        def respond(prompt):
            seen.append(cfg["name"])
            return good(prompt)
        return respond
    E.climb(tmp_path, E.Budget(noise_runs=0), incumbent={"id": "i", "config": {"name": "inc"}},
            candidates=[{"id": "c", "config": {"name": "cand"}, "domains": ["mind"]}], responder_for=rf,
            grader=E.exact_grader, rules=[], items=pool)
    assert 0 < seen.count("cand") < seen.count("inc")        # the candidate saw only its domain
    assert seen.count("cand") <= 20


def test_climb_notes_the_decision_in_the_ledger(tmp_path, led):
    run_climb(tmp_path, [{"id": "c1", "config": {"skill": "good"}, "note": "plain"}], ledger=led)
    (ev,) = [e for e in led.iter(kind="note") if e["body"]["type"] == "learn_climb"]
    assert ev["body"]["candidate"] == "c1" and ev["body"]["dev"]["delta"] > 0 and "delta" in ev["body"]["held"]
