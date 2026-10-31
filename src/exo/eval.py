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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import provenance
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


_SCALE = re.compile(r"\b0(?:\.0+)?\s*(?:-|\u2013|\u2014|to|and)\s*(?:1|10|100)(?:\.0+)?\b", re.I)
_NUM = r"(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?"
_WRONG = re.compile(r"\b(?:incorrect|wrong)\b|\bnot\s+(?:correct|right)\b|n['\u2019]t\s+(?:correct|right)\b", re.I)
_NOT_WRONG = re.compile(r"\b(?:nothing|not|never|no)\s+(?:(?:is|was)\s+)?(?:wrong|incorrect)\b|"
                        r"n['\u2019]t\s+(?:wrong|incorrect)\b", re.I)


def _scaled(m: re.Match) -> float:
    x = float(m.group(1))
    if m.group(2):
        x /= float(m.group(2)) or 1.0
    elif x > 1.0:
        x /= 10.0 if x <= 10 else 100.0
    return max(0.0, min(1.0, x))


def parse_score(text: str) -> float:
    """A judge reply as a score from 0 to 1. A named scale ("0 to 1", "(0-10)") is only a label. A number that opens
    the reply is the score, as the prompt asks. Otherwise a reply that calls the answer wrong, incorrect or not
    correct is 0 ("nothing wrong" does not), a bare "correct" is 1, and the first number counts. A full score in a
    reply that also calls the answer wrong is 0. x/y is divided out, and a bare number above 1 reads as out of 10
    (above 10, out of 100). No number is 0."""
    t = _SCALE.sub(" ", text.strip())
    wrong = bool(_WRONG.search(_NOT_WRONG.sub(" ", t)))
    lead = re.match(r"[\W_]*" + _NUM, t)
    if lead:
        x = _scaled(lead)
        return 0.0 if wrong and x >= 1.0 else x
    if wrong:
        return 0.0
    if re.search(r"\bcorrect\b", t, re.I) and not re.search(r"\d", t):
        return 1.0
    m = re.search(_NUM, t)
    return _scaled(m) if m else 0.0


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


# --- hill-climbing pieces (LEARNING-LOOP section 6) -----------------------------------------------
# A Responder answers one prompt. A candidate is a Responder built from a configuration (taste rules,
# skills, settings); the same code serves any user, only the files in their store differ.

PASS_MARK = 0.5
EPS = 1e-9
HELD_FRACTION = 0.2
DEFAULT_GATE = {"held_tolerance": 0.0, "domain_drop": 0.1, "guarded_domains": (), "pass_mark": PASS_MARK}
Complete = Callable[[str], str]


def _round(x: float) -> float:
    return round(float(x), 4)


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


JUDGE_PROMPT = ("You grade an answer for one person against what they actually wanted.\n"
                "Rubric: {rubric}\nSituation: {question}\nWhat they wanted: {gold}\n{rejected}"
                "Answer to grade: {answer}\n"
                "Check stance and conclusion, not only voice. Reply with one number from 0 to 1.")


def judge_grader(complete: Complete, rubric: str = "", samples: int = 3, prompt: str = JUDGE_PROMPT) -> Grader:
    """A Grader that asks a model `samples` times and averages the scores (principle 12).

    `complete(prompt) -> text` is any model call; the judge role is `eval`, which defaults to the main route.
    A sample that raises is skipped; when every sample fails the item scores 0."""
    def grade(item: Mapping, answer: str) -> float:
        gold = item.get("gold") or item.get("chosen") or ""
        rej = item.get("rejected")
        text = prompt.format(rubric=rubric or "Match what the principal wanted.",
                             question=item.get("question") or item.get("context") or "", gold=gold,
                             rejected=f"What they rejected: {rej}\n" if rej else "", answer=answer)
        scores = []
        for _ in range(max(1, int(samples))):
            try:
                scores.append(parse_score(str(complete(text))))
            except Exception:
                continue
        return _mean(scores)
    grade.samples = max(1, int(samples))  # type: ignore[attr-defined]
    return grade


def _check_fn(check: Any) -> tuple[str, Callable[[Mapping, str], bool], tuple[str, ...]]:
    """(id, passes(item, answer), domains) for a check: a dict with `forbid` or `require` (a regex) or a callable."""
    if callable(check):
        return getattr(check, "__name__", "check"), lambda item, ans: bool(check(item, ans)), ()
    cid = str(check.get("id") or check.get("forbid") or check.get("require"))
    doms = tuple(check.get("domains") or ())
    if check.get("forbid"):
        rx = re.compile(check["forbid"], re.I if check.get("ignore_case", True) else 0)
        return cid, lambda item, ans: not rx.search(ans), doms
    if check.get("require"):
        rx = re.compile(check["require"], re.I if check.get("ignore_case", True) else 0)
        return cid, lambda item, ans: bool(rx.search(ans)), doms
    raise ValueError(f"check {cid!r} needs forbid or require")


def rule_violations(checks: Iterable[Any], item: Mapping, answer: str) -> list[str]:
    """Ids of the checks the answer breaks. A check with `domains` applies only to those domains."""
    out = []
    for c in checks:
        cid, passes, doms = _check_fn(c)
        if doms and domain_of(item) not in doms:
            continue
        if not passes(item, answer):
            out.append(cid)
    return out


def rule_grader(checks: Iterable[Any]) -> Grader:
    """A Grader from the store's checkable rules: 1.0 when every check passes, 0.0 on any break (a hard fail)."""
    checks = list(checks)
    return lambda item, answer: 0.0 if rule_violations(checks, item, answer) else 1.0


def load_checks(root: str | os.PathLike) -> list[dict]:
    """The store's checkable rules: `<store>/eval/rules.jsonl`, one {"id", "forbid"|"require", "domains"?} per line."""
    return load_jsonl(Path(root) / "eval" / "rules.jsonl")


def _item_ts(ev: Mapping) -> str:
    return str(ev.get("ts") or "")


def build_replays(ledger, cutoff: str | None = None, principals: Iterable[str] | None = None) -> list[dict]:
    """Session replay items from ledger verdict, answer and ruling events (6.1).

    Each item is the context the agent had, cut before the principal's move, and what the principal did:
      accept            gold is the accepted output
      edit, should_have_been   gold is the better version the principal gave
      answer            gold is the answer to the asked question
      ruling            gold is the rule, given the principal message that led to it
    Rejections carry no better version, so they are skipped. `cutoff` keeps events at or before that timestamp.
    Only the principal's own events count (D-040; `principals`, default `provenance.principal_names()`): a verdict
    the agent reported with exo_should_have_been, or a peer's message, is never a gold answer or a question."""
    who = set(principals) if principals is not None else set(provenance.principal_names())
    items: list[dict] = []
    last_user: dict[str, str] = {}
    for ev in ledger.iter():
        if cutoff and ev["ts"] > cutoff:
            break
        b = ev.get("body") or {}
        sess = ev.get("session") or ""
        text = next((b[k] for k in ("text", "content", "description", "summary") if isinstance(b.get(k), str)
                     and b[k].strip()), "")
        mine = ev.get("author") in who
        if ev["kind"] == "utterance" and b.get("role", "user") in ("user", "principal"):  # principal: exo ingest
            if mine:
                last_user[sess] = text
            else:   # a later verdict answers this message, not an older one of the principal's
                last_user.pop(sess, None)
            continue
        if not mine:
            continue
        base = {"ts": ev["ts"], "source": ev["id"], "session": sess, "kind": "replay",
                "domain": b.get("domain") or (b.get("node") or "general").split("/", 1)[0]}
        if ev["kind"] == "verdict":
            target = ledger.get(b.get("target")) if b.get("target") else None
            ttext = ""
            if target:
                tb = target.get("body") or {}
                ttext = next((tb[k] for k in ("text", "content") if isinstance(tb.get(k), str)), "")
            context = b.get("context") or last_user.get(sess, "")
            verdict = b.get("verdict")
            better = b.get("chosen") or b.get("wanted") or b.get("edit") or b.get("edited") or b.get("text") or ""
            if verdict == "accept":
                gold = ttext or b.get("answer") or ""
            elif verdict in ("edit", "should_have_been"):
                gold = better
            else:
                continue
            if context and gold:
                items.append({**base, "id": f"r-{ev['id'][:12]}", "question": context, "gold": gold,
                              "verdict": verdict, "rejected": (b.get("answer") or ttext) if verdict != "accept" else None})
        elif ev["kind"] == "answer":
            q = next((ledger.get(r) for r in ev.get("refs") or [] if (ledger.get(r) or {}).get("kind") == "question"),
                     None)
            qtext = ((q or {}).get("body") or {}).get("text", "")
            if qtext and text:
                items.append({**base, "id": f"r-{ev['id'][:12]}", "question": qtext, "gold": text, "verdict": "answer"})
        elif ev["kind"] == "ruling":
            context = last_user.get(sess, "")
            if context and text and context != text:
                items.append({**base, "id": f"r-{ev['id'][:12]}", "question": context, "gold": text,
                              "verdict": "ruling"})
    return items


def derived_from(item: Mapping, ids: Iterable[str]) -> bool:
    """True when an item was built from one of these ledger events: a replay's `source`, or a pair's `evidence`."""
    ids = set(ids)
    src, ev = item.get("source"), item.get("evidence")
    return bool(ids) and ((isinstance(src, str) and src in ids)
                          or (isinstance(ev, (list, tuple)) and any(isinstance(e, str) and e in ids for e in ev)))


def _hash_pct(key: str) -> int:
    import hashlib
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100


def split(items: Iterable[Mapping], by: str = "ts", held_fraction: float = HELD_FRACTION,
          train_fraction: float = 0.5) -> dict[str, list[dict]]:
    """Train, dev and held-out.

    Held-out is a fixed sample: an item is held out when a hash of its id falls in the first `held_fraction`
    of 100, so membership never changes as items arrive and incumbent scores stay cached. Tiny sets always
    hold out their lowest-hash item. The rest are ordered (`by="ts"`: oldest first; `by="id"`) and the older
    `train_fraction` is train (the reviewer may read it), the newer part is dev (the climb scores on it)."""
    items = [dict(i) for i in items]
    cut = int(round(held_fraction * 100))
    held = [i for i in items if _hash_pct(str(i.get("id"))) < cut]
    if not held and len(items) >= 3:
        held = [min(items, key=lambda i: _hash_pct(str(i.get("id"))))]
    held_ids = {id(i) for i in held}
    rest = [i for i in items if id(i) not in held_ids]
    rest.sort(key=(lambda i: (_item_ts(i), str(i.get("id")))) if by == "ts" else (lambda i: str(i.get("id"))))
    n_train = int(len(rest) * train_fraction)
    return {"train": rest[:n_train], "dev": rest[n_train:], "held": held}


def _choice_flip(item: Mapping, seed: int) -> bool:
    return _hash_pct(f"{seed}:{item.get('id')}") % 2 == 1


def score_items(items: Iterable[Mapping], responder: Responder, grader: Grader | None = None, *,
                rules: Iterable[Any] = (), pass_mark: float = PASS_MARK, seed: int = 0,
                weights: Mapping[str, float] | None = None) -> dict:
    """Score every item once. Checkable rules run first and a break is a hard fail for that item (score 0, the
    judge is not called). Pairs (`chosen` and `rejected`, no `gold`) score 1 when the responder picks the chosen
    one, order fixed per item and seed. Everything else is graded against its `gold`."""
    grader = grader or keyword_grader
    rules = list(rules)
    rows = []
    for it in items:
        kind = "choice" if (it.get("chosen") and it.get("rejected") and not it.get("gold")) else "qa"
        row = {"id": it.get("id"), "domain": domain_of(it), "kind": kind, "rule_ok": True,
               "pinned": bool(it.get("pinned"))}
        try:
            if kind == "choice":
                flip = _choice_flip(it, seed)
                a, b = (it["rejected"], it["chosen"]) if flip else (it["chosen"], it["rejected"])
                out = str(responder(CHOICE_PROMPT.format(context=it.get("context", ""), a=a, b=b)))
                right = "B" if flip else "A"
                row["score"] = 1.0 if parse_choice(out) == right else 0.0
            else:
                q = it.get("question") or it.get("context")
                if not q:
                    continue
                ans = str(responder(str(q)))
                bad = rule_violations(rules, it, ans) if rules else []
                if bad:
                    row["rule_ok"], row["broken"], row["score"] = False, bad, 0.0
                else:
                    row["score"] = float(grader(it, ans))
        except Exception as e:  # a failing responder scores zero, the run goes on
            row["score"], row["error"] = 0.0, f"{type(e).__name__}: {e}"
        row["score"] = _round(row["score"])
        rows.append(row)
    w = dict(weights or {})
    kinds = sorted({r["kind"] for r in rows})
    parts = {k: _round(_mean(r["score"] for r in rows if r["kind"] == k)) for k in kinds}
    tot = sum(w.get(k, 1.0) for k in kinds)
    composite = _round(sum(parts[k] * w.get(k, 1.0) for k in kinds) / tot) if tot else 0.0
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by[r["domain"]].append(r["score"])
    return {"n": len(rows), "composite": composite, "parts": parts, "pass_mark": pass_mark,
            "by_domain": {d: {"n": len(v), "score": _round(_mean(v))} for d, v in sorted(by.items())},
            "rows": rows}


def noise_band(composites: Iterable[float]) -> float:
    """Run-to-run noise: the spread of the incumbent's composite across repeated scorings."""
    xs = list(composites)
    return _round(max(xs) - min(xs)) if len(xs) >= 2 else 0.0


def compare(incumbent: Any, candidate: Any, items: Iterable[Mapping] = (), *, grader: Grader | None = None,
            rules: Iterable[Any] = (), noise: float = 0.0, margin: float = 0.01, seed: int = 0) -> dict:
    """Paired scores of an incumbent and a candidate on the same items.

    Each side is a Responder (scored here with `score_items`) or a score result from `score_items`.
    Returns the composite of each, the delta, the noise band, whether the candidate is better (delta beats
    both `margin` and `noise`), per-domain deltas, and per-item rows for the gate."""
    items = list(items)
    inc = incumbent if isinstance(incumbent, Mapping) else score_items(items, incumbent, grader, rules=rules, seed=seed)
    cand = candidate if isinstance(candidate, Mapping) else score_items(items, candidate, grader, rules=rules, seed=seed)
    irows = {r["id"]: r for r in inc["rows"]}
    crows = {r["id"]: r for r in cand["rows"]}
    shared = [i for i in irows if i in crows]
    rows = {i: {"domain": crows[i]["domain"], "inc": irows[i]["score"], "cand": crows[i]["score"],
                "inc_rule_ok": irows[i].get("rule_ok", True), "cand_rule_ok": crows[i].get("rule_ok", True),
                "pinned": bool(crows[i].get("pinned"))} for i in shared}
    ci, cc = _mean(rows[i]["inc"] for i in shared), _mean(rows[i]["cand"] for i in shared)
    by: dict[str, list[str]] = defaultdict(list)
    for i in shared:
        by[rows[i]["domain"]].append(i)
    by_domain = {d: {"n": len(ids), "inc": _round(_mean(rows[i]["inc"] for i in ids)),
                     "cand": _round(_mean(rows[i]["cand"] for i in ids)),
                     "delta": _round(_mean(rows[i]["cand"] - rows[i]["inc"] for i in ids))}
                 for d, ids in sorted(by.items())}
    delta = cc - ci
    return {"n": len(shared), "inc": _round(ci), "cand": _round(cc), "delta": _round(delta), "noise": _round(noise),
            "margin": margin, "better": delta > max(margin, noise) + EPS,
            "improved": [i for i in shared if rows[i]["cand"] > rows[i]["inc"] + EPS],
            "worse": [i for i in shared if rows[i]["cand"] < rows[i]["inc"] - EPS],
            "by_domain": by_domain, "items": rows}


def gate(result: Mapping, policy: Mapping | None = None) -> dict:
    """The regression gates of 6.5 on a `compare` result made on held-out and pinned items.

    Fails on: a held-out composite drop beyond `held_tolerance`; any item that passed its checkable rules
    before and breaks one now; any score drop at all in a `guarded_domains` domain; a domain drop beyond
    `domain_drop`; a pinned item (flagged `pinned`, or listed in `policy["pinned"]`) under `pass_mark`.
    Returns {"pass": bool, "failures": [{"gate", ...}]}; the failures name the items."""
    pol = {**DEFAULT_GATE, **(policy or {})}
    fails: list[dict] = []
    if result["delta"] < -float(pol["held_tolerance"]) - EPS:
        fails.append({"gate": "held_out_drop", "delta": result["delta"]})
    guarded = set(pol["guarded_domains"] or ())
    pinned = set(pol.get("pinned") or ())
    for iid, r in result["items"].items():
        if r["inc_rule_ok"] and not r["cand_rule_ok"]:
            fails.append({"gate": "rule_regression", "item": iid})
        if r["domain"] in guarded and r["cand"] < r["inc"] - EPS:
            fails.append({"gate": "guarded_domain", "item": iid, "domain": r["domain"]})
        if (r.get("pinned") or iid in pinned) and r["cand"] < float(pol["pass_mark"]):
            fails.append({"gate": "pinned", "item": iid})
    for d, v in result["by_domain"].items():
        if v["delta"] < -float(pol["domain_drop"]) - EPS:
            fails.append({"gate": "domain_drop", "domain": d, "delta": v["delta"]})
    return {"pass": not fails, "failures": fails}


# --- scoreboard ---------------------------------------------------------------------------------

SCOREBOARD = "reports/scoreboard.jsonl"


def scoreboard(root: str | os.PathLike, row: Mapping | None = None) -> dict:
    """Append `row` (if given) to `<root>/reports/scoreboard.jsonl` and render `reports/scoreboard.html`.

    A row: {"ts"?, "incumbent", "composite", "parts"?, "cost"?, "change"?: {"id", "proposal"?, "status"}}.
    Statuses `kept` and `reverted` feed the weekly line, e.g. "score 0.71 to 0.74 this week, 6 changes kept,
    2 reverted". Returns {"rows", "line", "html"}."""
    root = Path(root)
    path = root / SCOREBOARD
    path.parent.mkdir(parents=True, exist_ok=True)
    if row is not None:
        r = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **row}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    rows = load_jsonl(path)
    line = _scoreboard_line(rows)
    hp = path.with_suffix(".html")
    hp.write_text(_scoreboard_html(rows, line), encoding="utf-8")
    return {"rows": rows, "line": line, "html": hp}


def _scoreboard_line(rows: list[dict], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    week = [r for r in rows if (dt := parse_ts(r.get("ts"))) is not None and (now - dt).days < 7]
    scored = [r for r in week if r.get("composite") is not None]
    kept = sum(1 for r in week if (r.get("change") or {}).get("status") == "kept")
    rev = sum(1 for r in week if (r.get("change") or {}).get("status") == "reverted")
    if not scored:
        return "no eval runs this week"
    return (f"score {scored[0]['composite']:.2f} to {scored[-1]['composite']:.2f} this week, "
            f"{kept} changes kept, {rev} reverted")


def _scoreboard_html(rows: list[dict], line: str) -> str:
    body = "".join(
        f"<tr><td>{html.escape(str(r.get('ts', ''))[:16])}</td><td>{html.escape(str(r.get('incumbent', '')))}</td>"
        f"<td>{'n/a' if r.get('composite') is None else format(r['composite'], '.3f')}</td>"
        f"<td>{html.escape(', '.join(f'{k} {v:.2f}' for k, v in (r.get('parts') or {}).items()))}</td>"
        f"<td>{html.escape(str((r.get('change') or {}).get('id', '')))} {html.escape(str((r.get('change') or {}).get('status', '')))}</td>"
        f"<td>{r.get('cost', '')}</td></tr>" for r in rows)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Scoreboard</title>'
            f"<style>{CSS}</style></head><body><main><h1>Scoreboard</h1><div class=\"meta\">{html.escape(line)}</div>"
            f"<table><tr><th>When</th><th>Incumbent</th><th>Composite</th><th>Parts</th><th>Change</th><th>Cost</th></tr>"
            f"{body}</table></main></body></html>")


# --- the nightly climb --------------------------------------------------------------------------

@dataclass
class Budget:
    """Caps for one night (6.7). `judge_samples` is for whoever builds the judge; `max_calls` stops the night
    when the responder has been called that many times (None = no cap)."""
    candidates: int = 10
    judge_samples: int = 3
    max_calls: int | None = None
    margin: float = 0.01
    noise_runs: int = 1          # extra scorings of the incumbent to measure noise


def load_items(root: str | os.PathLike, ledger=None, cutoff: str | None = None,
               principals: Iterable[str] | None = None) -> list[dict]:
    """Eval Q&A, preference pairs and session replays of a store, each with an id. Replays come only from the
    events of `principals` (default `provenance.principal_names` for the store at `root`)."""
    root = Path(root)
    items = [{**q, "id": q.get("id") or f"qa-{i}"} for i, q in enumerate(load_jsonl(root / "training" / "eval-qa.jsonl"))]
    items += [{**p, "id": p.get("id") or f"pair-{i}"} for i, p in
              enumerate(load_jsonl(root / "training" / "preference-pairs.jsonl")) if p.get("chosen") and p.get("rejected")]
    if ledger is not None:
        items += build_replays(ledger, cutoff, provenance.principal_names(extra=(root.name,))
                               if principals is None else principals)
    seen, out = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out


def _cfg_hash(cfg: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]


def climb(store, budget: Budget | None = None, *, incumbent: Mapping, candidates: Iterable[Mapping],
          responder_for: Callable[[Any], Responder], grader: Grader | None = None, rules: Iterable[Any] | None = None,
          items: Iterable[Mapping] | None = None, ledger=None, policy: Mapping | None = None,
          weights: Mapping[str, float] | None = None, seed: int = 0, fingerprint: Any = None) -> dict:
    """One night of greedy, conservative hill-climbing (6.4, 6.7).

    `incumbent` and each candidate are {"id", "config", "domains"?, "note"?}; `responder_for(config)` builds the
    Responder. At most `budget.candidates` are tried. A candidate replaces the incumbent only if it is better
    on dev by the margin and the incumbent's noise band, and passes `gate` on the held-out items of its `domains`
    plus the pinned items, and again on each other domain's held-out items alone. Dev is scored only in its
    `domains`; every held-out and pinned item is scored for each candidate, and an accepted one rescores every
    dev, held-out and pinned item (when the call budget runs out first, the merged rows are cached under a key
    the next night will not trust). Items built from the ledger events in a candidate's `exclude` (its lesson's
    own evidence) never count for or against it, and a candidate with no other held-out item in its domains
    cannot pass: pinned items are gated too, but they are not held out, so they never make up for it. The
    incumbent's scores are cached in `<store>/eval/incumbent.json`,
    keyed on its config, the items' contents, the rules, `weights`, `seed` and `fingerprint` (whatever else
    changes scores, such as the models and the rubric behind the responder and `grader`). The decision is a
    ledger note with both scores when a writable `ledger` is given. Returns the report: incumbent id, one record
    per candidate, accepted ids, calls."""
    budget = budget or Budget()
    root = Path(store) if isinstance(store, (str, os.PathLike)) else Path(store.root)
    rules = list(load_checks(root) if rules is None else rules)
    pool = list(items) if items is not None else load_items(root, ledger)
    parts = split(pool)
    dev, held = parts["dev"], parts["held"]
    pinned = [i for i in pool if i.get("pinned") and i not in held]
    calls = [0]
    cap = {"on": False, "hit": False}   # the call cap applies to candidates; the incumbent baseline is always scored

    def counted(cfg: Any) -> Responder:
        inner = responder_for(cfg)

        def respond(prompt: str) -> str:
            if cap["on"] and budget.max_calls is not None and calls[0] >= budget.max_calls:
                cap["hit"] = True
                raise RuntimeError("call budget spent")
            calls[0] += 1
            return inner(prompt)
        return respond

    def score(cfg: Any, rows: list[dict]) -> dict:
        res = score_items(rows, counted(cfg), grader, rules=rules, seed=seed, weights=weights)
        if cap["hit"]:   # score_items turns a raising responder into zero scores, so stop here instead
            raise RuntimeError("call budget spent")
        return res

    def every(cfg: Any) -> dict:   # every dev, held-out and pinned item, as the cache holds them
        return {"dev": score(cfg, dev), "held": score(cfg, held), "pinned": score(cfg, pinned) if pinned else {"rows": []}}

    inc = dict(incumbent)
    cache_path = root / "eval" / "incumbent.json"
    key = {"cfg": _cfg_hash(inc.get("config")), "items": _cfg_hash(sorted(pool, key=lambda i: str(i["id"]))),
           "env": _cfg_hash({"rules": rules, "weights": weights, "seed": seed, "noise_runs": budget.noise_runs,
                             "fingerprint": fingerprint})}
    cache: dict = {}
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if cache.get("key") != key:
        cache = {"key": key, **every(inc["config"])}
        runs = [cache["dev"]["composite"]] + [score(inc["config"], dev)["composite"] for _ in range(budget.noise_runs)]
        cache["noise"] = noise_band(runs)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    report: dict[str, Any] = {"incumbent": inc["id"], "start_composite": cache["dev"]["composite"],
                              "noise": cache["noise"], "candidates": [], "accepted": [],
                              "sizes": {k: len(v) for k, v in parts.items()}}
    cap["on"] = True
    for cand in list(candidates)[: max(0, budget.candidates)]:
        rec: dict[str, Any] = {"id": cand["id"], "status": "dropped"}
        report["candidates"].append(rec)
        try:
            doms, own = set(cand.get("domains") or ()), set(cand.get("exclude") or ())
            # Its own evidence is scored (the cache stays whole) but never compared: re-scoring it proves nothing.
            fair = {i["id"] for i in pool if not derived_from(i, own)}
            affected = [i for i in dev if not doms or domain_of(i) in doms]
            cand_dev = score(cand["config"], affected)
            dev_ids = {i["id"] for i in affected} & fair
            inc_dev = {**cache["dev"], "rows": [r for r in cache["dev"]["rows"] if r["id"] in dev_ids]}
            cmp_dev = compare(inc_dev, cand_dev, noise=cache["noise"], margin=budget.margin)
            rec["dev"] = {k: cmp_dev[k] for k in ("inc", "cand", "delta", "better")}
            if not cmp_dev["better"]:
                rec["reason"] = "not better on dev by the margin and noise"
                continue
            affected_held = [i for i in held if not doms or domain_of(i) in doms]
            # Pinned items are gated too, but they are not held out: alone they cannot show it is not worse.
            aff_ids = {i["id"] for i in affected_held}
            if not aff_ids & fair:
                rec["status"], rec["reason"] = "gate_failed", "no held-out cases"
                continue
            gate_items = affected_held + [i for i in pinned if i not in affected_held]
            other = [i for i in held if i["id"] not in aff_ids]
            cand_held = {"rows": score(cand["config"], gate_items)["rows"] + score(cand["config"], other)["rows"]}
            inc_rows = cache["held"]["rows"] + cache["pinned"]["rows"]

            def gated(ids: set) -> tuple[dict, dict]:
                keep = ids & fair
                cmp = compare({"rows": [r for r in inc_rows if r["id"] in keep]}, cand_held, margin=0.0)
                return cmp, gate(cmp, policy)
            cmp_held, g = gated({i["id"] for i in gate_items})
            # Every rule reaches every domain, so each other domain's held-out is gated on its own too (issue 13):
            # pooled with the candidate's own domains, a gain there would hide a drop here.
            for d in sorted({domain_of(i) for i in other}):
                g["failures"] += [{**f, "domain": d} for f in gated({i["id"] for i in other if domain_of(i) == d})[1]["failures"]]
            g["pass"] = not g["failures"]
            rec["held"] = {k: cmp_held[k] for k in ("inc", "cand", "delta")}
            rec["gate"] = g
            if not g["pass"]:
                rec["status"], rec["reason"] = "gate_failed", "; ".join(sorted({f["gate"] for f in g["failures"]}))
                continue
        except RuntimeError as e:
            rec["status"], rec["reason"] = "stopped", str(e)
            break
        rec["status"] = "accepted"
        report["accepted"].append(cand["id"])
        inc = dict(cand)
        # The accepted candidate becomes the incumbent. Re-score every dev/held/pinned item under the new
        # config so the cache is not stale for domains the candidate did not touch (issue 13): a global rule
        # (render_system injects every rule) can shift held-out items in any domain, and the next compare and
        # check_regression must see the new incumbent's scores everywhere. If the call budget is spent, fall
        # back to merging the already-scored rows, under a key the next night cannot match.
        newkey = {**key, "cfg": _cfg_hash(inc.get("config"))}
        try:
            cache = {"key": newkey, **every(inc["config"]), "noise": cache["noise"]}
        except RuntimeError:
            merged = {r["id"]: r for r in cache["dev"]["rows"]}
            merged.update({r["id"]: r for r in cand_dev["rows"]})
            dev_rows = list(merged.values())
            held_map = {r["id"]: r for r in cache["held"]["rows"]}
            pin_map = {r["id"]: r for r in cache["pinned"]["rows"]}
            for r in cand_held["rows"]:
                (pin_map if r["pinned"] and r["id"] not in held_map else held_map)[r["id"]] = r
            cache = {"key": {**newkey, "partial": True},
                     "dev": {"composite": _round(_mean(r["score"] for r in dev_rows)), "rows": dev_rows},
                     "held": {"composite": _round(_mean(r["score"] for r in held_map.values())), "rows": list(held_map.values())},
                     "pinned": {"rows": list(pin_map.values())}, "noise": cache["noise"]}
        key = newkey
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        cb = cand.get("on_accept")
        if callable(cb):
            cb(rec)
        if ledger is not None and getattr(ledger, "signer", None) is not None:
            ledger.append("note", {"type": "learn_climb", "candidate": cand["id"], "note": cand.get("note", ""),
                                   "dev": rec["dev"], "held": rec["held"]})
    report["incumbent"] = inc["id"]
    report["composite"] = cache["dev"]["composite"]
    report["calls"] = calls[0]
    return report


# --- CLI hook -----------------------------------------------------------------------------------
# `exo.cli` lists `eval` in its LAZY table. It imports this module, calls `register(sp, common)` and runs
# `cmd_eval` only when `exo eval` runs.

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
