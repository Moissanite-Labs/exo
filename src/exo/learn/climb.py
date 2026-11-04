"""The slow loop: the nightly climb (LEARNING-LOOP 5.7, 6.3 to 6.7).

A configuration is the current taste rules plus the skill files. A candidate is that configuration with one change:
add a held lesson, or drop an auto-promoted one. A candidate replaces the incumbent only if it is better on dev by
the margin, not worse on held-out, and passes the gate. The night is capped: at most `climb.candidates` candidates
(default 10) and `climb.judge_samples` judge samples per item (default 3). The judge is the `eval` role, which
defaults to the main route (D-018). The responder under test answers on the `main` route.

Eval items come only from the principal's own events (D-040), and a lesson's own evidence is never its proof. Gated
lessons (rule 5) and lessons with a critic objection are not climb candidates: `apply_policy` decides them.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

from exo import eval as E
from exo import provenance, taste
from exo.consolidate import (ConsolidateError, Proposal, load_policy, load_state, proposal_accept,
                             skill_text_after, _status)

from .learner import Learner
from .promote import accepted_rules, apply_policy, export_taste, revert, _now

SYSTEM_HEAD = "You are an agent working for one principal. Follow what you know about them."


# --- configurations -----------------------------------------------------------

def current_config(lr: Learner) -> dict:
    """The incumbent: accepted rules (id, domain, text) and skill files by name."""
    rt = lr.runtime
    skills: dict[str, str] = {}
    sdir = rt.root / "skills"
    if sdir.is_dir():
        for f in sorted(sdir.glob("*/SKILL.md")):
            skills[f.parent.name] = f.read_text(encoding="utf-8")
    return {"rules": [{"id": pid, "domain": d, "text": t} for d, t, _, pid in accepted_rules(lr)],
            "skills": skills}


def config_id(cfg: dict) -> str:
    return "cfg-" + hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]


def render_system(cfg: dict) -> str:
    parts = [SYSTEM_HEAD]
    if cfg.get("rules"):
        parts.append("What the principal wants:\n" + "\n".join(f"- {r['text']}" for r in cfg["rules"]))
    for name, text in sorted((cfg.get("skills") or {}).items()):
        parts.append(f"Skill {name}:\n{text.strip()}")
    return "\n\n".join(parts)


def with_lesson(cfg: dict, p: Proposal) -> dict:
    """The configuration plus one lesson."""
    new = json.loads(json.dumps(cfg))
    if p.ptype == "skill":
        old = new["skills"].get(str(p.target), "")
        new["skills"][str(p.target)] = skill_text_after(old, str(p.target), p.patch, (p.created or "")[:10], p.id, p.claim)
    else:
        text = f"Wants {p.wants}" + (f", not {p.avoid}" if p.avoid else "")
        new["rules"].append({"id": p.id, "domain": p.domain, "text": text})
    return new


def without_lesson(cfg: dict, pid: str) -> dict:
    new = json.loads(json.dumps(cfg))
    new["rules"] = [r for r in new["rules"] if r["id"] != pid]
    return new


def responder_for(lr: Learner) -> Callable[[dict], E.Responder]:
    def build(cfg: dict) -> E.Responder:
        system = render_system(cfg)
        return lambda prompt: lr.complete_sync("main", prompt, system)
    return build


def _rubric(lr: Learner) -> str:
    rp = lr.runtime.root / "eval" / "rubric.md"
    return rp.read_text(encoding="utf-8") if rp.is_file() else ""


def make_grader(lr: Learner) -> E.Grader:
    return E.judge_grader(lambda prompt: lr.complete_sync("eval", prompt), _rubric(lr),
                          samples=int(lr.settings["climb"]["judge_samples"]))


def climb_fingerprint(lr: Learner) -> dict:
    """What changes scores besides the configuration and the items: the responder's and the judge's routes, the
    rubric and the judge samples. The climb rescores its cached incumbent when this changes."""
    router = getattr(lr.engine, "router", None)
    routes = {r: [x.ref for x in router.chain(r)] for r in ("main", "eval")} if router is not None else {}
    return {"routes": routes, "rubric": _rubric(lr), "judge_samples": int(lr.settings["climb"]["judge_samples"])}


def eval_items(lr: Learner) -> list[dict]:
    """The store's eval items; session replays come only from the principal's own events (D-040)."""
    rt = lr.runtime
    if rt is None:
        return []
    return E.load_items(rt.root, rt.ledger, principals=provenance.principal_names(rt.store))


def gate_policy(lr: Learner) -> dict:
    return {"guarded_domains": lr.settings["promote"]["guarded_domains"]}


# --- the gate for one proposal (rule 5) ---------------------------------------

def gate_proposal(lr: Learner, p: Proposal) -> dict:
    """Run the 6.5 gates for one lesson: held-out and pinned items, incumbent against incumbent plus the lesson.
    Items built from the lesson's own evidence do not count. With no held-out items nothing can be proved, so it
    fails and says why."""
    rt = lr.runtime
    items = eval_items(lr)
    parts = E.split(items)
    pinned = [i for i in items if i.get("pinned") and i not in parts["held"]]
    own = {e["id"] for e in p.evidence}
    held = [i for i in parts["held"] + pinned if not E.derived_from(i, own)]
    if not held:
        return {"pass": False, "reason": "no held-out cases to prove it", "failures": []}
    build = responder_for(lr)
    grader, rules = make_grader(lr), E.load_checks(rt.root)
    cfg = current_config(lr)
    cmp = E.compare(build(cfg), build(with_lesson(cfg, p)), held, grader=grader, rules=rules)
    g = E.gate(cmp, gate_policy(lr))
    if g["pass"] and cmp["delta"] <= 0:
        return {"pass": False, "reason": "no better on held-out cases", "failures": [], "compare": cmp}
    return {**g, "reason": "" if g["pass"] else "failed gates: " + ", ".join(sorted({f["gate"] for f in g["failures"]})),
            "compare": cmp}


# --- the night ----------------------------------------------------------------

def candidates_for(lr: Learner, limit: int) -> list[dict]:
    """Add candidates from held-back lessons (medium weight and up), then drop candidates from auto-promoted ones.
    Only ordinary lessons are added: not guarded, not gated (rule 5 keeps its critic, weight, session and held-out
    checks in `apply_policy`) and with no critic objection. No candidate scores on its lesson's own evidence: the
    replays and preference pairs built from it count neither for adding the lesson nor for dropping it."""
    rt = lr.runtime
    if rt is None:
        return []
    pol = lr.settings["promote"]
    st = load_state(rt.ledger)
    policy = load_policy(rt.store)
    cfg = current_config(lr)
    now = _now()
    promoted = {}
    for ev in rt.ledger.iter(kind="note"):
        b = ev["body"] or {}
        if b.get("type") == "learn_promotion":
            promoted[b["proposal_id"]] = bool(b.get("auto"))
    out: list[dict] = []
    for pid, p in st.proposals.items():
        if len(out) >= limit:
            break
        # "medium weight and up", by the noisy-or `weight` label, not the stale `confidence` field. A lesson that
        # rule 4 would hold back because its computed weight is low must not slip through as a climb candidate and
        # auto-promote on eval evidence alone (issue 13). (The session floor belongs to rule 4's auto-promotion; the
        # climb is the path for medium-weight lessons, so it must still see them.)
        b = st.beliefs.get(pid)
        weight = b.weight(now, policy["half_life_days"]) if b is not None else 0.0
        lab = taste.label(weight)
        if (_status(st, pid) != "proposed" or p.ptype not in ("taste", "fact") or p.guarded
                or lab == "low"):
            continue
        if p.critic_note or p.ptype in pol["eval_gate_for"] or p.domain in pol["high_impact_domains"]:
            continue
        out.append({"id": f"add:{pid}", "config": with_lesson(cfg, p), "domains": [p.domain, "general"],
                    "note": p.claim, "kind": "add", "proposal": pid, "exclude": [e["id"] for e in p.evidence]})
    for pid, auto in promoted.items():
        if len(out) >= limit:
            break
        b = st.beliefs.get(pid)
        if auto and b is not None and b.status == "accepted" and any(r["id"] == pid for r in cfg["rules"]):
            p = st.proposals[pid]
            out.append({"id": f"drop:{pid}", "config": without_lesson(cfg, pid), "domains": [p.domain, "general"],
                        "note": p.claim, "kind": "drop", "proposal": pid, "exclude": [e["id"] for e in p.evidence]})
    return out


def _sync_climb(lr: Learner, budget: E.Budget) -> dict:
    rt = lr.runtime
    cfg = current_config(lr)
    st = load_state(rt.ledger)
    cands = candidates_for(lr, budget.candidates)
    accepted_kinds: dict[str, dict] = {c["id"]: c for c in cands}
    report = E.climb(rt.store, budget, incumbent={"id": config_id(cfg), "config": cfg}, candidates=cands,
                     responder_for=responder_for(lr), grader=make_grader(lr), items=eval_items(lr),
                     ledger=rt.ledger, policy=gate_policy(lr), fingerprint=climb_fingerprint(lr))
    report["applied"] = []
    for cid in report["accepted"]:
        c = accepted_kinds[cid]
        try:
            if c["kind"] == "add":
                p = st.proposals[c["proposal"]]
                r = proposal_accept(rt.store, rt.ledger, c["proposal"], author=rt.agent)
                rt.ledger.append("note", {"type": "learn_promotion", "proposal_id": c["proposal"], "rule": 5,
                                          "auto": True, "claim": c["note"], "ptype": p.ptype, "domain": p.domain,
                                          "node": p.node_id, "via": "climb"}, refs=[r["event"]])
                status = "kept"
            else:
                revert(lr, c["proposal"], "eval: the candidate without this lesson scored better", follow_up=False)
                status = "reverted"
            report["applied"].append({"id": cid, "status": status})
        except ConsolidateError as exc:
            report["applied"].append({"id": cid, "status": "error", "reason": str(exc)})
    report["incumbent"] = config_id(current_config(lr))   # the id the next night will see
    return report


def check_regression(lr: Learner, budget: E.Budget) -> list[dict]:
    """If the latest score fell below the one before it (beyond noise), find the recent auto-promoted lesson whose
    removal scores best and revert it. Principal-promoted lessons get a question instead (5.7)."""
    rt = lr.runtime
    rows = [r for r in E.load_jsonl(rt.root / E.SCOREBOARD) if r.get("composite") is not None]
    if len(rows) < 2 or rows[-1]["composite"] >= rows[-2]["composite"] - budget.margin:
        return []
    since = str(rows[-2]["ts"]).rstrip("Z")   # scoreboard ts has whole seconds; ledger ts has microseconds
    recent = [ev["body"] for ev in rt.ledger.iter(kind="note", since=since)
              if (ev["body"] or {}).get("type") == "learn_promotion"]
    if not recent:
        return []
    parts = E.split(eval_items(lr))
    pool = parts["dev"] + parts["held"]
    if not pool:
        return []
    build, grader, rules = responder_for(lr), make_grader(lr), E.load_checks(rt.root)
    cfg = current_config(lr)
    now = E.score_items(pool, build(cfg), grader, rules=rules)["composite"]
    best = None
    for b in recent:
        pid = b["proposal_id"]
        if not any(r["id"] == pid for r in cfg["rules"]):
            continue
        without = E.score_items(pool, build(without_lesson(cfg, pid)), grader, rules=rules)["composite"]
        if without > now + budget.margin and (best is None or without > best[1]):
            best = (b, without)
    if best is None:
        return []
    b = best[0]
    if b.get("auto"):
        res = revert(lr, b["proposal_id"], "eval regression: scores are higher without this lesson", follow_up=False)
        return [{"proposal_id": b["proposal_id"], "action": "reverted", "event": res.get("event")}]
    from .promote import ask_once
    st = load_state(rt.ledger)
    ask_once(lr, st.proposals[b["proposal_id"]], "scores fell after this lesson was promoted")
    return [{"proposal_id": b["proposal_id"], "action": "question"}]


def _sync_nightly(lr: Learner, budget: E.Budget) -> dict:
    rt = lr.runtime
    out: dict[str, Any] = {"status": "done"}
    from exo.consolidate import consolidate_run
    with rt.lock:
        out["consolidated"] = consolidate_run(rt.store, rt.ledger, author=rt.agent).proposals
    out["promotions"] = [d.to_dict() for d in apply_policy(lr, allow_eval=True)]
    out["regressions"] = check_regression(lr, budget)
    out["climb"] = _sync_climb(lr, budget)
    clim = out["climb"]
    row = {"incumbent": clim["incumbent"], "composite": clim["composite"], "cost": clim["calls"],
           "parts": {}}
    E.scoreboard(rt.root, row)
    for a in clim["applied"]:
        E.scoreboard(rt.root, {"incumbent": clim["incumbent"], "composite": None,
                               "change": {"id": a["id"], "status": a["status"]}})
    for r in out["regressions"]:
        E.scoreboard(rt.root, {"incumbent": clim["incumbent"], "composite": None,
                               "change": {"id": r["proposal_id"], "status": "reverted" if r["action"] == "reverted" else "asked"}})
    export_taste(lr)
    out["scoreboard"] = E.scoreboard(rt.root)["line"]
    return out


async def nightly(lr: Learner, budget: E.Budget | None = None) -> dict:
    """The function the lead calls once a night. Offloads to a worker thread; returns a report dict."""
    if lr.runtime is None:
        return {"status": "off", "reason": lr.notices[0] if lr.notices else "learning is off"}
    c = lr.settings["climb"]
    budget = budget or E.Budget(candidates=int(c["candidates"]), judge_samples=int(c["judge_samples"]),
                                margin=float(c["margin"]))
    return await asyncio.to_thread(_sync_nightly, lr, budget)
