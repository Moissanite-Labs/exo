import json
from datetime import date

import pytest

from exo import eval as ev
from exo.ask import QuestionQueue
from exo.ledger import Ledger

QA = [
    {"id": "1", "question": "What does the name mean?", "gold": "Truth established", "node": "person/identity"},
    {"id": "2", "question": "Who runs the firm?", "gold": "Her father, a civil engineer", "node": "person/family"},
    {"id": "3", "question": "Approval needs?", "gold": "A URL and the decision needed", "node": "relationship/reporting"},
]
PAIRS = [{"id": f"p{i}", "context": f"case {i}", "chosen": f"good {i}", "rejected": f"bad {i}",
          "node": "relationship/x"} for i in range(20)]


def test_graders():
    item = {"gold": "Truth established, from Persian"}
    assert ev.exact_grader(item, "truth established from persian") == 1.0
    assert ev.keyword_grader(item, "It means truth, established.") == pytest.approx(2 / 3)
    assert ev.keyword_grader({"gold": ""}, "x") == 0.0
    judge = ev.LLMGrader(lambda p: "Score: 0.8" if "Reference: Truth" in p else "0")
    assert judge({"question": "q", "gold": "Truth"}, "a") == 0.8
    assert ev.parse_score("7/10") == 0.7 and ev.parse_score("8") == 0.8 and ev.parse_score("INCORRECT") == 0.0


def test_run_qa_reports_per_domain():
    answers = {"What does the name mean?": "truth established", "Who runs the firm?": "no idea",
               "Approval needs?": "a url plus the decision needed"}
    res = ev.run_qa(QA, lambda q: answers[q])
    assert res["n"] == 3 and res["correct"] == 2
    assert res["by_domain"]["person"] == {"n": 2, "correct": 1, "accuracy": 0.5}
    assert res["by_domain"]["relationship"]["accuracy"] == 1.0


def test_failing_responder_counts_wrong():
    def boom(q):
        raise RuntimeError("down")
    res = ev.run_qa(QA[:1], boom)
    assert res["correct"] == 0 and "RuntimeError" in res["items"][0]["error"]


def test_choice_prediction_randomizes_order():
    def oracle(prompt):
        a = prompt.split("\nA: ")[1].split("\n")[0]
        return "A" if a.startswith("good") else "B"
    res = ev.run_choices(PAIRS, oracle, seed=1)
    assert res["accuracy"] == 1.0
    sides = {r["chosen_was"] for r in res["items"]}
    assert sides == {"A", "B"}                        # both positions used
    always_a = ev.run_choices(PAIRS, lambda p: "A", seed=1)
    assert 0 < always_a["accuracy"] < 1               # position bias is not rewarded
    assert ev.run_choices(PAIRS[:2], lambda p: "maybe")["unparsed"] == 2
    assert ev.parse_choice("Option B, because") == "B"


def test_longitudinal_from_ledger(tmp_path, signer):
    led = Ledger(tmp_path / "ledger.sqlite", signer)
    out = led.append("artifact", {"text": "x"})
    for v in ("accept", "accept", "edit", "reject"):
        led.append("verdict", {"verdict": v, "target": out["id"]})
    q = led.append("question", {"question": "q1"})
    led.append("answer", {"question": "q1", "text": "y"}, refs=[q["id"]])
    res = ev.longitudinal(led)
    (w,) = res["weeks"]
    assert w["accepted"] == 2 and w["corrections"] == 2 and w["cpa"] == 1.0
    assert w["verdict_mix"]["accept"] == 0.5 and w["questions"] == 1 and w["answers"] == 1


def test_run_eval_writes_json_and_html(tmp_path, signer):
    root = tmp_path / "store"
    (root / "training").mkdir(parents=True)
    (root / "training" / "eval-qa.jsonl").write_text("\n".join(json.dumps(x) for x in QA) + "\nnot json\n")
    (root / "training" / "preference-pairs.jsonl").write_text("\n".join(json.dumps(x) for x in PAIRS[:4]))
    led = Ledger(root / "ledger.sqlite", signer)
    a = led.append("artifact", {"text": "x"})
    led.append("verdict", {"verdict": "accept", "target": a["id"]})
    qq = QuestionQueue(root, policy={})
    qq.propose("Q?", qid="q1")
    qq.mark_asked(["q1"])
    rep = ev.run_eval(root, lambda p: "A", ledger=led)
    assert rep["qa"]["n"] == 3 and rep["choices"]["n"] == 4
    assert rep["longitudinal"]["weeks"][0]["questions"] == 1
    paths = ev.write_report(root, rep, date(2026, 10, 11))
    assert paths["json"].name == "eval-2026-10-11.json"
    assert json.loads(paths["json"].read_text())["qa"]["n"] == 3
    page = paths["html"].read_text()
    assert "color-scheme:dark" in page and "Eval Q&amp;A by domain" in page and "<script" not in page


def test_empty_store_report(tmp_path):
    rep = ev.run_eval(tmp_path, lambda p: "")
    assert rep["qa"] is None and rep["choices"] is None and rep["longitudinal"]["weeks"] == []
    assert "No items." in ev.render_html(rep)


def test_retrieval_baseline_on_template(template_store):
    r = ev.retrieval_responder(template_store)
    assert isinstance(r("anything at all"), str)
    assert r("Situation: x\nA: one\nB: two\nReply") in ("A", "B")
