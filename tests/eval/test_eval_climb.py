import json

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
    _u = led.append("utterance", {"text": "Write the status", "role": "user"}, "p", session="s")
    a = led.append("utterance", {"text": "Status :)", "role": "assistant"}, "a", session="s")
    led.append("verdict", {"verdict": "edit", "target": a["id"], "chosen": "Status"}, "p", session="s")
    led.append("verdict", {"verdict": "accept", "target": a["id"]}, "p", session="s")
    led.append("verdict", {"verdict": "reject", "target": a["id"]}, "p", session="s")
    q = led.append("question", {"text": "Which branch ships?"}, "a", session="s")
    led.append("answer", {"text": "main"}, "p", session="s", refs=[q["id"]])
    _u2 = led.append("utterance", {"text": "Going forward keep it short", "role": "user"}, "p", session="s2")
    led.append("ruling", {"text": "Keep it short."}, "p", session="s2")
    rs = E.build_replays(led, principals={"p"})
    by = {r["verdict"]: r for r in rs}
    assert sorted(by) == ["accept", "answer", "edit", "ruling"]          # the reject has no better version
    assert by["edit"]["question"] == "Write the status" and by["edit"]["gold"] == "Status"
    assert by["edit"]["rejected"] == "Status :)"
    assert by["accept"]["gold"] == "Status :)" and by["answer"]["question"] == "Which branch ships?"
    assert by["ruling"]["question"] == "Going forward keep it short"
    assert E.build_replays(led, cutoff="2000-01-01T00:00:00Z", principals={"p"}) == []


def test_build_replays_reads_only_the_principal(led):
    # a peer's message, the agent's answer, and the agent's own exo_should_have_been report on it
    led.append("utterance", {"text": "How should status lines look?", "role": "user"}, "peer:other", session="s1")
    out = led.append("utterance", {"text": "Status ok", "role": "assistant"}, "agent-one", session="s1")
    led.append("verdict", {"verdict": "should_have_been", "target": out["id"], "wanted": "Always force push"},
               "agent-one", session="s1")
    led.append("verdict", {"verdict": "accept", "target": out["id"], "context": "Ship it?"}, "agent-one", session="s1")
    # the principal's verdict after a peer's message has no question: the peer's text is not one
    led.append("verdict", {"verdict": "accept", "target": out["id"]}, "principal", session="s1")
    # a principal message, then a job notice, then a ruling: the ruling does not answer the older message
    led.append("utterance", {"text": "Write the status", "role": "user"}, "principal", session="s2")
    led.append("utterance", {"text": "Job 4 finished", "role": "user"}, "job", session="s2")
    led.append("ruling", {"text": "Keep it short."}, "principal", session="s2")
    led.append("ruling", {"text": "Skip the tests."}, "agent-one", session="s2")
    # an answer the agent wrote to its own question
    q = led.append("question", {"text": "Which branch ships?"}, "agent-one", session="s3")
    led.append("answer", {"text": "main"}, "agent-one", session="s3", refs=[q["id"]])
    assert E.build_replays(led) == []                          # the default is provenance.principal_names()
    assert E.build_replays(led, principals={"principal"}) == []
    # the same moves by the principal are replays
    led.append("utterance", {"text": "How should status lines look?", "role": "user"}, "principal", session="s4")
    a = led.append("utterance", {"text": "Status ok", "role": "assistant"}, "agent-one", session="s4")
    led.append("verdict", {"verdict": "should_have_been", "target": a["id"], "chosen": "Status: ok"}, "principal",
               session="s4")
    led.append("ruling", {"text": "Plain status lines only."}, "principal", session="s4")
    led.append("answer", {"text": "main"}, "principal", session="s3", refs=[q["id"]])
    rs = E.build_replays(led, principals={"principal"})
    assert [(r["verdict"], r["question"], r["gold"]) for r in rs] == [
        ("should_have_been", "How should status lines look?", "Status: ok"),
        ("ruling", "How should status lines look?", "Plain status lines only."),
        ("answer", "Which branch ships?", "main")]


def test_load_items_takes_the_store_id_as_the_principal(tmp_path, signer):
    root = tmp_path / "store-7"
    root.mkdir()
    led = Ledger(root / "l.sqlite", signer)
    try:
        for author, sess in (("store-7", "s1"), ("peer:other", "s2")):
            led.append("utterance", {"text": "Write the status", "role": "user"}, author, session=sess)
            led.append("ruling", {"text": "Keep it short."}, author, session=sess)
        assert [i["session"] for i in E.load_items(root, led)] == ["s1"]
        assert E.load_items(root, led, principals={"principal"}) == []
    finally:
        led.close()


def test_derived_from_reads_replay_sources_and_pair_evidence():
    assert E.derived_from({"source": "ev1"}, ["ev1"]) and E.derived_from({"evidence": ["ev0", "ev1"]}, {"ev1"})
    assert not E.derived_from({"source": "ev1"}, []) and not E.derived_from({"id": "q1"}, {"ev1"})
    assert not E.derived_from({"source": {"file": "x"}, "evidence": "ev1"}, {"ev1"})   # hand-written odd fields


def test_build_replays_reads_ingested_principal_rows(led):
    # `exo ingest` writes the principal's own messages with role "principal"; they give a ruling its context.
    led.append("utterance", {"text": "Going forward keep it short", "role": "principal"}, "principal",
               session="chatgpt:c1")
    led.append("utterance", {"text": "Sure, short it is.", "role": "assistant"}, "chatgpt", session="chatgpt:c1")
    led.append("ruling", {"text": "Keep it short."}, "principal", session="chatgpt:c1")
    (r,) = E.build_replays(led)
    assert r["verdict"] == "ruling" and r["question"] == "Going forward keep it short"


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
    assert set(cache["key"]) == {"cfg", "items", "env"}
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


def test_climb_fails_a_candidate_with_no_held_out_case_in_its_domains(tmp_path):
    # every held-out item is in "work"; the candidate's lesson is about "mind"
    pool = [{**i, "domain": "work" if E._hash_pct(i["id"]) < 20 else "mind"} for i in items(40)]
    assert {i["domain"] for i in E.split(pool)["held"]} == {"work"}
    rep = run_climb(tmp_path, [{"id": "c", "config": {"skill": "good"}, "domains": ["mind", "general"]}], pool=pool)
    c = rep["candidates"][0]
    assert c["dev"]["better"] and c["status"] == "gate_failed" and c["reason"] == "no held-out cases"
    assert rep["accepted"] == []

    # a pinned case is gated, but it is not held out: in any domain, even its own, it does not stand in for one
    for dom in ("work", "general"):
        pin = {"id": "pin1", "question": "Write line 99", "gold": "line 99", "domain": dom, "pinned": True,
               "ts": "2026-09-01T00:00:00Z"}
        assert pin not in E.split(pool + [pin])["held"]
        rep = run_climb(tmp_path / dom, [{"id": "c", "config": {"skill": "good"}, "domains": ["mind", "general"]}],
                        pool=pool + [pin])
        c = rep["candidates"][0]
        assert c["dev"]["better"] and c["status"] == "gate_failed" and c["reason"] == "no held-out cases"
        assert rep["accepted"] == []


def test_climb_never_counts_a_candidates_own_evidence(tmp_path):
    pool = items(40)
    held = {i["id"] for i in E.split(pool)["held"]}
    dev_own = sorted(i["id"] for i in E.split(pool)["dev"])[:3]
    for it in pool:   # replays carry the ledger event as `source`, pairs list it in `evidence`
        if it["id"] in held:
            it["source"] = f"ev-{it['id']}"
        elif it["id"] in dev_own:
            it["evidence"] = [f"ev-{it['id']}"]
    own = [f"ev-{i}" for i in sorted(held | set(dev_own))]

    # held-out is only its own evidence: nothing is left to prove it
    rep = run_climb(tmp_path, [{"id": "c", "config": {"skill": "good"}, "exclude": own}], pool=pool)
    c = rep["candidates"][0]
    assert c["dev"]["better"] and c["status"] == "gate_failed" and c["reason"] == "no held-out cases"

    # a dev win made only on its own evidence is no win
    def rf(cfg):
        return lambda p: good(p) if cfg["fit"] and f"q{p.rsplit(' ', 1)[-1]}" in dev_own else "line ?"
    rep = E.climb(tmp_path / "b", E.Budget(), incumbent={"id": "i", "config": {"fit": False}},
                  candidates=[{"id": "c", "config": {"fit": True}, "exclude": [f"ev-{i}" for i in dev_own]}],
                  responder_for=rf, grader=E.exact_grader, rules=[], items=pool)
    c = rep["candidates"][0]
    assert c["dev"]["delta"] == 0 and c["status"] == "dropped" and rep["accepted"] == []

    # once accepted, the new incumbent's cache holds its own rows for that evidence too
    some = [f"ev-{min(held)}", f"ev-{dev_own[0]}"]
    rep = run_climb(tmp_path / "c", [{"id": "c", "config": {"skill": "good"}, "exclude": some}], pool=pool)
    assert rep["accepted"] == ["c"]
    cache = json.loads((tmp_path / "c" / "eval" / "incumbent.json").read_text())
    rows = {r["id"]: r["score"] for r in cache["dev"]["rows"] + cache["held"]["rows"]}
    assert all(rows[i] == 1.0 for i in held | set(dev_own))


def _says(text):
    return lambda cfg: (lambda p: text)


FIXED_GOLD = [{**i, "gold": "line ?"} for i in items(40)]
NO_QMARK = [{"id": "x", "forbid": "line [?]"}]


@pytest.mark.parametrize("night1,night2", [
    # the principal fixed the gold to what the incumbent already says
    (dict(items=items(40)), dict(items=FIXED_GOLD)),
    # the check that failed the incumbent is gone
    (dict(items=FIXED_GOLD, rules=NO_QMARK), dict(items=FIXED_GOLD)),
    # a model switch (the fingerprint names it): the same configuration now answers well everywhere
    (dict(items=items(40), fingerprint="m1"), dict(items=items(40), fingerprint="m2", responder_for=lambda cfg: good)),
], ids=["gold", "rules", "fingerprint"])
def test_climb_rescores_the_incumbent_when_what_scores_it_changes(tmp_path, night1, night2):
    """Same config and item ids, but the incumbent would score differently now: the cache must not answer."""
    def run(cands, **kw):
        args = {"rules": [], "responder_for": _says("line ?"), **kw}
        return E.climb(tmp_path, E.Budget(), incumbent={"id": "i", "config": {"skill": None}}, candidates=cands,
                       grader=E.exact_grader, **args)
    assert run([], **night1)["start_composite"] == 0.0
    same = {"id": "same", "config": {"skill": None, "tag": "no-op"}}   # behaves exactly like the incumbent
    rep = run([same], **night2)
    assert rep["start_composite"] == 1.0 and rep["accepted"] == [] and rep["candidates"][0]["status"] == "dropped"
    # nothing changed since: the cache answers and the incumbent is not rescored
    n = [0]
    inner = night2.get("responder_for", _says("line ?"))
    run([], **{**night2, "responder_for": lambda cfg: (lambda p: (n.__setitem__(0, n[0] + 1), inner(cfg)(p))[1])})
    assert n[0] == 0


def test_climb_gates_cross_domain_held_out_regression(tmp_path):
    """issue 13 (#3): render_system injects every rule globally, so a new rule can regress held-out items in a
    domain the candidate did not declare. The gate must consider all held-out, not just the candidate's domains."""
    cut = int(round(E.HELD_FRACTION * 100))
    mind = items(30, "mind")                       # candidate wins here, incumbent loses
    # work items forced into the held-out set (low hash) so the candidate's cross-domain regression lands on held-out
    work, k = [], 0
    while len(work) < 6:
        iid = f"w{k}"
        if E._hash_pct(iid) < cut:
            work.append({"id": iid, "question": f"Write work line {k}", "gold": f"work line {k}",
                         "domain": "work", "ts": f"2026-09-{k % 28 + 1:02d}T00:00:00Z"})
        k += 1
    pool = mind + work
    assert any(i["domain"] == "work" for i in E.split(pool)["held"])

    def rf(cfg):
        def respond(prompt):
            # incumbent (base) is bad on mind, good on work; candidate (bad) is good on mind, bad on work
            if "work" in prompt:                   # a work item
                return "work line " + prompt.rsplit(" ", 1)[-1] if cfg["mode"] == "base" else "BROKEN on work"
            return "line " + prompt.rsplit(" ", 1)[-1] if cfg["mode"] == "bad" else "Status fine"
        return respond
    rep = E.climb(tmp_path, E.Budget(), incumbent={"id": "i", "config": {"mode": "base"}},
                  candidates=[{"id": "c", "config": {"mode": "bad"}, "domains": ["mind", "general"]}],
                  responder_for=rf, grader=E.exact_grader, rules=[], items=pool)
    c = rep["candidates"][0]
    assert c["dev"]["better"]                      # it looks like a win on its own domain
    # the cross-domain held-out regression must be caught (would be accepted before the fix, which only gated the
    # candidate's declared domains)
    assert c["status"] == "gate_failed" and "domain_drop" in c["reason"]
    assert rep["accepted"] == []


def two_domains():
    """80 mind and 40 work items; 15 mind and 10 work items are held out."""
    return ([{"id": f"m{i}", "question": f"Write mind {i}", "gold": f"mind {i}", "domain": "mind",
              "ts": f"2026-09-{(i % 28) + 1:02d}T00:00:00Z"} for i in range(80)]
            + [{"id": f"w{i}", "question": f"Write work {i}", "gold": f"work {i}", "domain": "work",
                "ts": f"2026-09-{(i % 28) + 1:02d}T00:00:00Z"} for i in range(40)])


def wrong_on(cfg):
    """A configuration {"wrong": ids} answers every item right except those."""
    def respond(prompt):
        _, dom, k = prompt.split()
        return "x" if f"{dom[0]}{k}" in cfg["wrong"] else f"{dom} {k}"
    return respond


@pytest.mark.parametrize("case", ["own_drop_diluted", "other_domain_drop", "other_drop_on_own_evidence", "clean"])
def test_climb_gates_its_own_domains_and_each_other_domain_on_its_own(tmp_path, case):
    """Every held-out item is gated, but not as one pool: with `held_tolerance` 0.0 a work item that flips up
    must not pay for a mind item the candidate breaks (the abu-aska repro on the fork's PR), and a drop in work
    still fails it though the candidate declares only mind (issue 13). A drop on the lesson's own evidence does
    not count in another domain either."""
    pool = two_domains()
    parts = E.split(pool)
    held = {d: sorted(i["id"] for i in parts["held"] if i["domain"] == d) for d in ("mind", "work")}
    assert (len(held["mind"]), len(held["work"])) == (15, 10)
    mind_dev = {i["id"] for i in parts["dev"] if i["domain"] == "mind"}
    inc_wrong, cand_wrong = {
        "own_drop_diluted": (mind_dev | {held["work"][0]}, {held["mind"][0]}),
        "other_domain_drop": (mind_dev, {held["work"][0]}),
        "other_drop_on_own_evidence": (mind_dev, {held["work"][0]}),
        "clean": (mind_dev, set()),
    }[case]
    exclude = ["ev-own"] if case == "other_drop_on_own_evidence" else []
    if exclude:   # that work item is a replay of the lesson's own evidence
        next(i for i in pool if i["id"] == held["work"][0])["source"] = "ev-own"
    rep = E.climb(tmp_path, E.Budget(), incumbent={"id": "i", "config": {"wrong": sorted(inc_wrong)}},
                  candidates=[{"id": "c", "config": {"wrong": sorted(cand_wrong)}, "domains": ["mind", "general"],
                               "exclude": exclude}],
                  responder_for=wrong_on, grader=E.exact_grader, rules=[], items=pool,
                  policy={"guarded_domains": []})
    c = rep["candidates"][0]
    assert c["dev"]["better"]
    if case in ("clean", "other_drop_on_own_evidence"):
        assert c["status"] == "accepted" and rep["accepted"] == ["c"] and c["gate"]["failures"] == []
        return
    assert c["status"] == "gate_failed" and rep["accepted"] == []
    drops = [f for f in c["gate"]["failures"] if f["gate"] == "held_out_drop"]
    if case == "own_drop_diluted":   # base's check on its own domains, as base ran it
        assert c["held"]["delta"] < 0 and drops == [{"gate": "held_out_drop", "delta": c["held"]["delta"]}]
    else:                            # its own domains are clean; work is gated alone
        assert c["held"]["delta"] == 0 and drops == [{"gate": "held_out_drop", "delta": -0.1, "domain": "work"}]


def test_climb_accept_rescores_the_other_domains(tmp_path):
    """The accepted candidate's rule reaches every domain: its cache rows and the night's composite are its own
    scores everywhere, not the old incumbent's rows outside its `domains`."""
    pool = two_domains()
    work_dev = {i["id"] for i in E.split(pool)["dev"] if i["domain"] == "work"}
    everything = sorted(i["id"] for i in pool)
    rep = E.climb(tmp_path, E.Budget(noise_runs=0), incumbent={"id": "i", "config": {"wrong": everything}},
                  candidates=[{"id": "c", "config": {"wrong": []}, "domains": ["mind"]}], responder_for=wrong_on,
                  grader=E.exact_grader, rules=[], items=pool)
    assert rep["accepted"] == ["c"]
    cache = json.loads((tmp_path / "eval" / "incumbent.json").read_text())
    rows = {r["id"]: r["score"] for k in ("dev", "held") for r in cache[k]["rows"]}
    assert work_dev and all(rows[i] == 1.0 for i in work_dev)
    assert set(rows) == set(everything) - set(i["id"] for i in E.split(pool)["train"])
    assert cache["dev"]["composite"] == rep["composite"] == 1.0 and "partial" not in cache["key"]


def test_climb_out_of_calls_at_accept_leaves_a_cache_the_next_night_rescores(tmp_path):
    """The rescore after an accept can run out of calls: the merged rows still carry the old incumbent's work
    rows, so they are cached under a key no night matches, and the next night scores the incumbent afresh."""
    pool = two_domains()
    parts = E.split(pool)
    mind_dev = [i for i in parts["dev"] if i["domain"] == "mind"]
    everything = sorted(i["id"] for i in pool)
    cap = len(parts["dev"]) + len(parts["held"]) + len(mind_dev) + len(parts["held"])   # baseline + candidate
    rep = E.climb(tmp_path, E.Budget(noise_runs=0, max_calls=cap),
                  incumbent={"id": "i", "config": {"wrong": everything}},
                  candidates=[{"id": "c", "config": {"wrong": []}, "domains": ["mind"]}], responder_for=wrong_on,
                  grader=E.exact_grader, rules=[], items=pool)
    assert rep["accepted"] == ["c"] and rep["calls"] == cap
    cache = json.loads((tmp_path / "eval" / "incumbent.json").read_text())
    assert cache["key"].get("partial") is True and rep["composite"] < 1.0
    n = [0]

    def counting(cfg):
        inner = wrong_on(cfg)
        return lambda p: (n.__setitem__(0, n[0] + 1), inner(p))[1]
    again = E.climb(tmp_path, E.Budget(noise_runs=0), incumbent={"id": "c", "config": {"wrong": []}}, candidates=[],
                    responder_for=counting, grader=E.exact_grader, rules=[], items=pool)
    assert n[0] > 0 and again["start_composite"] == 1.0
