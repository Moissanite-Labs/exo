"""n=1 evaluation (principle 12): eval Q&A, held-out choice prediction, longitudinal metrics.

Everything model-specific is a parameter. A `Responder` is any callable `str -> str` (a
model, a prompt chain, a retrieval baseline). A `Grader` is a callable
`(item, answer) -> float` in 0..1. Nothing here calls an API.

Inputs (JSONL, in the store's `training/` by default):

    eval-qa.jsonl           {"id", "question", "gold", "node"?, "domain"?}
    preference-pairs.jsonl  {"id", "context", "chosen", "rejected", "node"?, "domain"?}

The domain of an item is `domain`, else the part of `node` before "/", else "general".

Longitudinal metrics read the ledger by ISO week:
    accepted        verdicts of `accept`
    corrections     verdicts of `edit`, `reject`, `should_have_been`
    cpa             corrections per accepted output (corrections / max(accepted, 1))
    verdict mix     share of each verdict
    questions       ledger `question` events plus queue `asked` events without a ledger id
    answers         ledger `answer` events

Reports go to `<store>/reports/eval-YYYY-MM-DD.json` and `.html`.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .ask import QUEUE_REL, parse_ts

Responder = Callable[[str], str]
Grader = Callable[[Mapping, str], float]
CORRECTIONS = ("edit", "reject", "should_have_been")
VERDICT_ORDER = ("accept", "edit", "reject", "should_have_been")
STOP = set("""a an and are as at be been but by can could do does for from had has have he her
him his i if in into is it its may me my no not of on or our she should so than that the their
them then there these they this to was we were what when which who will with would you your""".split())


# --- io -----------------------------------------------------------------------------------------

def load_jsonl(path: str | os.PathLike) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            out.append(v)
    return out


def domain_of(item: Mapping) -> str:
    if item.get("domain"):
        return str(item["domain"])
    node = item.get("node")
    if node:
        return str(node).split("/", 1)[0]
    return "general"


# --- graders ------------------------------------------------------------------------------------

def content_tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[\w']+", str(s).lower()) if w not in STOP and len(w) > 1]


def normalize(s: str) -> str:
    return " ".join(re.findall(r"[\w']+", str(s).lower()))


def exact_grader(item: Mapping, answer: str) -> float:
    return 1.0 if normalize(answer) == normalize(item.get("gold", "")) else 0.0


def keyword_grader(item: Mapping, answer: str) -> float:
    """Exact match scores 1. Else recall of the gold answer's content words in the answer."""
    if exact_grader(item, answer):
        return 1.0
    gold = set(content_tokens(item.get("gold", "")))
    if not gold:
        return 0.0
    got = set(content_tokens(answer))
    return len(gold & got) / len(gold)


class LLMGrader:
    """Grader seam for a model judge. `judge` is a callable `prompt -> text` supplied by the
    caller; the text should contain a score between 0 and 1 (or 0 to 10, or CORRECT/INCORRECT)."""

    PROMPT = ("You grade whether an answer matches a reference about one person.\n"
              "Question: {question}\nReference: {gold}\nAnswer: {answer}\n"
              "Judge meaning, not wording. Reply with one number from 0 to 1.")

    def __init__(self, judge: Callable[[str], str], prompt: str | None = None):
        self.judge = judge
        self.prompt = prompt or self.PROMPT

    def __call__(self, item: Mapping, answer: str) -> float:
        out = str(self.judge(self.prompt.format(question=item.get("question", ""),
                                                gold=item.get("gold", ""), answer=answer)))
        return parse_score(out)


def parse_score(text: str) -> float:
    t = text.strip()
    if re.search(r"\bincorrect\b|\bwrong\b", t, re.I):
        return 0.0
    if re.search(r"\bcorrect\b", t, re.I) and not re.search(r"\d", t):
        return 1.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*(\d+))?", t)
    if not m:
        return 0.0
    x = float(m.group(1))
    if m.group(2):
        x /= float(m.group(2)) or 1.0
    elif x > 1.0:
        x /= 10.0 if x <= 10 else 100.0
    return max(0.0, min(1.0, x))


# --- eval Q&A -----------------------------------------------------------------------------------

def _acc(rows: list[dict], key: str = "correct") -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["domain"]].append(r)
    dom = {d: {"n": len(rs), "correct": sum(1 for r in rs if r[key]),
               "accuracy": round(sum(1 for r in rs if r[key]) / len(rs), 4)} for d, rs in sorted(by.items())}
    n = len(rows)
    c = sum(1 for r in rows if r[key])
    return {"n": n, "correct": c, "accuracy": round(c / n, 4) if n else None, "by_domain": dom}


def run_qa(items: Iterable[Mapping], responder: Responder, grader: Grader | None = None,
           pass_mark: float = 0.5, prompt: str = "{question}") -> dict:
    """Ask every item's question, grade the reply, report accuracy overall and per domain."""
    grader = grader or keyword_grader
    rows = []
    for it in items:
        q = it.get("question")
        if not q:
            continue
        try:
            ans = str(responder(prompt.format(question=q)))
            err = None
        except Exception as e:  # a failing responder scores zero, the run goes on
            ans, err = "", f"{type(e).__name__}: {e}"
        score = float(grader(it, ans)) if err is None else 0.0
        row = {"id": it.get("id"), "domain": domain_of(it), "score": round(score, 4),
               "correct": score >= pass_mark, "answer": ans[:500]}
        if err:
            row["error"] = err
        rows.append(row)
    return {**_acc(rows), "pass_mark": pass_mark, "items": rows}


# --- held-out choice prediction -----------------------------------------------------------------

CHOICE_PROMPT = ("Predict what the principal would choose.\nSituation: {context}\n"
                 "A: {a}\nB: {b}\nReply with A or B only.")


def parse_choice(text: str) -> str | None:
    t = str(text).strip().upper()
    m = re.match(r"^\W*(?:OPTION\s+)?([AB])\b", t)
    if m:
        return m.group(1)
    found = set(re.findall(r"\b([AB])\b", t))
    return found.pop() if len(found) == 1 else None


def run_choices(pairs: Iterable[Mapping], responder: Responder, seed: int = 0,
                prompt: str = CHOICE_PROMPT) -> dict:
    """For each pair, show chosen and rejected in random order and ask which the principal
    picks. Accuracy is the share where the responder picked `chosen`. Unparseable is wrong."""
    rng = random.Random(seed)
    rows = []
    for p in pairs:
        if not (p.get("chosen") and p.get("rejected")):
            continue
        flip = rng.random() < 0.5
        a, b = (p["rejected"], p["chosen"]) if flip else (p["chosen"], p["rejected"])
        right = "B" if flip else "A"
        try:
            out = str(responder(prompt.format(context=p.get("context", ""), a=a, b=b)))
        except Exception as e:
            out = f"error: {e}"
        pick = parse_choice(out)
        rows.append({"id": p.get("id"), "domain": domain_of(p), "chosen_was": right, "picked": pick,
                     "correct": pick == right})
    res = _acc(rows)
    res["unparsed"] = sum(1 for r in rows if r["picked"] is None)
    res["chance"] = 0.5
    res["seed"] = seed
    res["items"] = rows
    return res


# --- longitudinal metrics -----------------------------------------------------------------------

def iso_week(ts: Any) -> str | None:
    dt = parse_ts(ts)
    if dt is None:
        return None
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def longitudinal(ledger=None, queue_path: str | os.PathLike | None = None) -> dict:
    weeks: dict[str, dict] = defaultdict(lambda: {"verdicts": Counter(), "questions": 0, "answers": 0})
    ledger_qids: set[str] = set()
    if ledger is not None:
        for ev in ledger.iter(kind=("verdict", "question", "answer")):
            wk = iso_week(ev["ts"])
            if wk is None:
                continue
            b = ev.get("body") or {}
            if ev["kind"] == "verdict":
                weeks[wk]["verdicts"][b.get("verdict")] += 1
            elif ev["kind"] == "question":
                weeks[wk]["questions"] += 1
                ledger_qids.add(ev["id"])
            else:
                weeks[wk]["answers"] += 1
    if queue_path and Path(queue_path).is_file():
        for ev in load_jsonl(queue_path):
            if ev.get("op") == "asked" and ev.get("ledger_id") not in ledger_qids:
                wk = iso_week(ev.get("ts"))
                if wk:
                    weeks[wk]["questions"] += 1
    out = []
    for wk in sorted(weeks):
        v = weeks[wk]["verdicts"]
        acc = v.get("accept", 0)
        corr = sum(v.get(k, 0) for k in CORRECTIONS)
        tot = sum(v.values())
        out.append({"week": wk, "accepted": acc, "corrections": corr,
                    "cpa": round(corr / max(acc, 1), 4),
                    "verdicts": {k: v.get(k, 0) for k in VERDICT_ORDER},
                    "verdict_mix": {k: round(v.get(k, 0) / tot, 4) if tot else 0.0 for k in VERDICT_ORDER},
                    "questions": weeks[wk]["questions"], "answers": weeks[wk]["answers"]})
    trend = None
    if len(out) >= 2:
        trend = "falling" if out[-1]["cpa"] < out[0]["cpa"] else ("rising" if out[-1]["cpa"] > out[0]["cpa"] else "flat")
    return {"weeks": out, "cpa_trend": trend}


# --- reports ------------------------------------------------------------------------------------

def run_eval(root: str | os.PathLike, responder: Responder, *, grader: Grader | None = None,
             ledger=None, qa_path: str | os.PathLike | None = None,
             pairs_path: str | os.PathLike | None = None, seed: int = 0,
             responder_name: str | None = None) -> dict:
    root = Path(root)
    qa = load_jsonl(qa_path or root / "training" / "eval-qa.jsonl")
    pairs = load_jsonl(pairs_path or root / "training" / "preference-pairs.jsonl")
    if ledger is None and (root / "ledger.sqlite").exists():
        from .ledger import Ledger
        ledger = Ledger(root / "ledger.sqlite")
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "store": root.name,
        "responder": responder_name or getattr(responder, "__name__", type(responder).__name__),
        "qa": run_qa(qa, responder, grader) if qa else None,
        "choices": run_choices(pairs, responder, seed=seed) if pairs else None,
        "longitudinal": longitudinal(ledger, root / QUEUE_REL),
    }


CSS = """
:root{color-scheme:dark}
body{margin:0;background:#0d1117;color:#e6edf3;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
main{max-width:920px;margin:0 auto;padding:40px 24px 64px}
h1{font-size:22px;margin:0 0 4px;font-weight:600}
h2{font-size:15px;margin:36px 0 10px;color:#9da7b3;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.meta{color:#8b949e;font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:20px}
.tile{background:#161b22;border:1px solid #2a313c;border-radius:10px;padding:14px 16px}
.tile .k{color:#8b949e;font-size:12px}.tile .v{font-size:26px;font-weight:600;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid #21262d;text-align:right}
th:first-child,td:first-child{text-align:left}
th{color:#8b949e;font-weight:500}
.bar{display:inline-block;height:8px;border-radius:4px;background:#3fb950;vertical-align:middle}
.track{display:inline-block;width:90px;height:8px;border-radius:4px;background:#21262d;margin-right:8px;vertical-align:middle}
.empty{color:#8b949e}
"""


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _bar(x: float | None) -> str:
    w = int(round(90 * (x or 0)))
    return f'<span class="track"><span class="bar" style="width:{w}px"></span></span>{_pct(x)}'


def _domain_table(res: Mapping | None) -> str:
    if not res:
        return '<p class="empty">No items.</p>'
    rows = "".join(f"<tr><td>{html.escape(d)}</td><td>{v['n']}</td><td>{v['correct']}</td><td>{_bar(v['accuracy'])}</td></tr>"
                   for d, v in res["by_domain"].items())
    return (f"<table><tr><th>Domain</th><th>Items</th><th>Correct</th><th>Accuracy</th></tr>{rows}"
            f"<tr><td><b>All</b></td><td>{res['n']}</td><td>{res['correct']}</td><td>{_bar(res['accuracy'])}</td></tr></table>")


def render_html(report: Mapping) -> str:
    qa, ch, lg = report.get("qa"), report.get("choices"), report.get("longitudinal") or {}
    weeks = lg.get("weeks") or []
    last = weeks[-1] if weeks else None
    tiles = [("Eval Q&A accuracy", _pct(qa["accuracy"]) if qa else "n/a"),
             ("Choice prediction", _pct(ch["accuracy"]) if ch else "n/a"),
             ("Corrections per accepted", f"{last['cpa']:.2f}" if last else "n/a"),
             ("Questions this week", str(last["questions"]) if last else "0")]
    tile_html = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in tiles)
    if weeks:
        wrows = "".join(
            f"<tr><td>{w['week']}</td><td>{w['accepted']}</td><td>{w['corrections']}</td><td>{w['cpa']:.2f}</td>"
            + "".join(f"<td>{_pct(w['verdict_mix'][k])}</td>" for k in VERDICT_ORDER)
            + f"<td>{w['questions']}</td><td>{w['answers']}</td></tr>" for w in weeks)
        week_html = ("<table><tr><th>Week</th><th>Accepted</th><th>Corrections</th><th>Per accepted</th>"
                     + "".join(f"<th>{k.replace('_', ' ')}</th>" for k in VERDICT_ORDER)
                     + f"<th>Questions</th><th>Answers</th></tr>{wrows}</table>")
        if lg.get("cpa_trend"):
            week_html += f'<p class="meta">Corrections per accepted output: {lg["cpa_trend"]}.</p>'
    else:
        week_html = '<p class="empty">No verdicts or questions in the ledger yet.</p>'
    ch_note = (f'<p class="meta">Chance is 50%. Unparsed replies: {ch["unparsed"]}. Seed {ch["seed"]}.</p>'
               if ch else "")
    title = f"Eval report, {html.escape(str(report.get('store', '')))}"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
            f"<style>{CSS}</style></head><body><main><h1>{title}</h1>"
            f'<div class="meta">{html.escape(str(report.get("generated", "")))} · responder '
            f'{html.escape(str(report.get("responder", "")))}</div><div class="tiles">{tile_html}</div>'
            f"<h2>Eval Q&amp;A by domain</h2>{_domain_table(qa)}"
            f"<h2>Held-out choice prediction</h2>{_domain_table(ch)}{ch_note}"
            f"<h2>Week by week</h2>{week_html}</main></body></html>")


def write_report(root: str | os.PathLike, report: Mapping, day: date | None = None) -> dict[str, Path]:
    day = day or datetime.now(timezone.utc).date()
    out = Path(root) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"eval-{day.isoformat()}"
    jp, hp = stem.with_suffix(".json"), stem.with_suffix(".html")
    jp.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    hp.write_text(render_html(report), encoding="utf-8")
    return {"json": jp, "html": hp}


# --- baseline responder -------------------------------------------------------------------------

def retrieval_responder(store, budget: int = 400) -> Responder:
    """Answers from the store with no model: the top retrieved node bodies for a question.
    For an A/B choice prompt it picks the option whose words overlap the retrieved text more.
    A floor to beat, and the M1 check that eval Q&A is answerable from the store."""
    def respond(prompt: str) -> str:
        m = re.search(r"Situation: (.*)\nA: (.*)\nB: (.*)\n", prompt)
        if m:
            ctx = set(content_tokens(store.ctx(m.group(1), budget=budget * 2).text))
            sa = len(set(content_tokens(m.group(2))) & ctx)
            sb = len(set(content_tokens(m.group(3))) & ctx)
            return "A" if sa >= sb else "B"
        return store.ctx(prompt, budget=budget).text
    respond.__name__ = "retrieval"
    return respond


def load_responder(spec: str) -> Responder:
    """`module:function` returning a Responder, or the Responder itself."""
    import importlib
    mod, _, fn = spec.partition(":")
    obj = getattr(importlib.import_module(mod), fn or "responder")
    return obj


# --- CLI hook -----------------------------------------------------------------------------------
# Wire into exo.cli: in build_parser() call `eval.register(sp, common)`, and expose `cmd_eval`
# in exo.cli's globals (e.g. `from .eval import cmd_eval`).

def register(sp, common) -> None:
    p = sp.add_parser("eval", parents=[common], help="n=1 evaluation report")
    p.add_argument("--responder", help="module:function giving a str->str callable (default: retrieval baseline)")
    p.add_argument("--qa", help="eval Q&A JSONL (default: <store>/training/eval-qa.jsonl)")
    p.add_argument("--pairs", help="preference pairs JSONL (default: <store>/training/preference-pairs.jsonl)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--no-write", action="store_true", help="print JSON only")


def cmd_eval(a, store) -> int:
    if a.responder:
        responder = load_responder(a.responder)
        name = a.responder
    else:
        responder, name = retrieval_responder(store), "retrieval"
    rep = run_eval(store.root, responder, qa_path=a.qa, pairs_path=a.pairs, seed=a.seed, responder_name=name)
    if a.no_write:
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0
    paths = write_report(store.root, rep)
    summary = {"qa": rep["qa"] and rep["qa"]["accuracy"], "choices": rep["choices"] and rep["choices"]["accuracy"],
               "json": str(paths["json"]), "html": str(paths["html"])}
    print(json.dumps(summary, sort_keys=True))
    return 0
