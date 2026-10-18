"""Promotion policy (LEARNING-LOOP 5.6), revert (5.7) and the views rebuilt from the ledger (5.5).

`apply_policy` walks open proposals. Mandatory checks (rules 3, 2 and 5) precede the ruling shortcut:

1. For an ordinary unguarded, ungated lesson, a principal ruling or answer among the evidence promotes it now.
2. Guarded (irreversible, public, financial, third party): never auto-promote. It becomes a question once.
3. Conflict with an existing belief: stays held; consolidation already raised the question. Never silent.
4. Taste or fact, not guarded, weight high, evidence from at least `min_sessions` different sessions, no critic
   objection: auto-promote, logged as `learn_promotion` for the brief. Evidence that is all imported history
   (`exo ingest` rows) waits for the principal's review instead.
5. Skill patches and configured gated lessons must pass the eval gate, even with principal evidence.
   Without held-out cases they cannot pass. Configured non-skill lessons also need high weight and enough sessions.
6. Everything else stays a proposal. Medium weight is a hint; low waits. Open past `stale_days` with enough
   value of information, it becomes a question; otherwise it decays away.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from exo import provenance, taste
from exo.ask import QuestionQueue
from exo.consolidate import (ConsolidateError, Proposal, imported, load_policy, load_state, proposal_accept,
                             proposal_revert, _status)

from .learner import Learner


@dataclass
class Decision:
    id: str
    action: str     # promoted | auto_promoted | held | question | hint | wait | wait_eval | gate_failed | error
    rule: int       # the 5.6 rule that decided it
    reason: str = ""
    failures: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sessions_of(ledger: Any, evidence_ids: list[str]) -> set[str]:
    out = set()
    for i in evidence_ids:
        ev = ledger.get(i)
        if ev:
            out.add(ev.get("session") or f"none:{i}")   # an event with no session counts as its own
    return out


def imported_only(ledger: Any, evidence_ids: list[str]) -> bool:
    """True when every evidence event is a row `exo ingest` wrote: history, with nothing said live."""
    evs = [ledger.get(i) for i in evidence_ids]
    return bool(evs) and all(ev is not None and imported(ev) for ev in evs)


def principal_said(ledger: Any, p: Proposal, principals: Iterable[str] | None = None) -> bool:
    """Rule 1: a ruling or an answer among the evidence, written by the principal (D-040). The kind alone is not
    enough: the agent could write a `ruling` for itself, and a peer's text could be captured as one."""
    who = set(principals) if principals is not None else set(provenance.principal_names())
    return any((ev := ledger.get(e["id"]) or {}).get("kind") in ("ruling", "answer") and ev.get("author") in who
               for e in p.evidence)


def ask_once(lr: Learner, p: Proposal, why: str) -> str | None:
    """Put one question about a proposal in the queue (never twice). Returns the question id or None."""
    rt = lr.runtime
    q = QuestionQueue(rt.root, ledger=rt.ledger)
    src = f"learn:{p.id}"
    if any(x.source == src for x in q.state().values()):
        return None
    made = q.propose(f"Keep this lesson? {p.claim}", node=p.node_id if not p.node_id.startswith("skill:") else None,
                     rationale=why, impact=0.7 if p.guarded else 0.5, uncertainty=max(0.2, 1 - p.weight),
                     default="no", source=src, domain=p.domain)
    return made.id


def _log(rt: Any, p: Proposal, rule: int, auto: bool, accepted: dict) -> None:
    rt.ledger.append("note", {"type": "learn_promotion", "proposal_id": p.id, "rule": rule, "auto": auto,
                              "claim": p.claim, "ptype": p.ptype, "domain": p.domain, "node": p.node_id},
                     refs=[accepted["event"]])


def apply_policy(lr: Learner, *, allow_eval: bool = False, now: str | None = None) -> list[Decision]:
    """Apply promotion rules, with mandatory checks first. `allow_eval` lets rule 5 run the gate (the nightly loop);
    the fast loop passes False so it never runs an eval."""
    rt = lr.runtime
    if rt is None:
        return []
    cfg = lr.settings["promote"]
    now = now or _now()
    out: list[Decision] = []
    with rt.lock:
        st = load_state(rt.ledger)
        pol = load_policy(rt.store)
        for pid, p in list(st.proposals.items()):
            if _status(st, pid) not in ("proposed", "held") or p.ptype == "question":
                continue
            belief = st.beliefs[pid]
            w = belief.weight(now, pol["half_life_days"])
            held = _status(st, pid) == "held"
            gated = p.ptype in cfg["eval_gate_for"] or p.domain in cfg["high_impact_domains"]
            d = _decide(lr, p, w, held, gated, allow_eval, cfg, now)
            out.append(d)
        for d in out:
            if d.action in ("promoted", "auto_promoted"):
                p = st.proposals[d.id]
                try:
                    r = proposal_accept(rt.store, rt.ledger, d.id, author=rt.agent, now=now)
                    _log(rt, p, d.rule, d.action == "auto_promoted", r)
                except ConsolidateError as exc:
                    d.action, d.reason = "error", str(exc)
            elif d.action == "question":
                ask_once(lr, st.proposals[d.id], d.reason)
        if any(d.action in ("promoted", "auto_promoted") for d in out):
            export_taste(lr)
    return out


def _decide(lr: Learner, p: Proposal, weight: float, held: bool, gated: bool, allow_eval: bool, cfg: dict,
            now: str) -> Decision:
    rt = lr.runtime
    # Rule 3 before rule 2 for a held proposal: the conflict already raised its question, so it only waits.
    if held:
        return Decision(p.id, "held", 3, "conflicts with an existing belief; waiting on the principal's answer")
    # Rule 2: guarded.
    if p.guarded:
        return Decision(p.id, "question", 2, "guarded: irreversible, public or financial")
    # Rule 5 is mandatory for skill patches and configured gated lessons, including principal evidence.
    lab = taste.label(weight)
    ledger, ids = rt.ledger, [e["id"] for e in p.evidence]
    n_sessions = len(sessions_of(ledger, ids))
    if p.ptype == "skill" or gated:
        if p.critic_note:
            return Decision(p.id, "wait", 5, f"critic objection: {p.critic_note}")
        if p.ptype != "skill" and not (lab == "high" and n_sessions >= int(cfg["min_sessions"])):
            return _rule6(lr, p, weight, lab, cfg, now)
        if not allow_eval:
            return Decision(p.id, "wait_eval", 5, "needs the eval gate; the nightly loop runs it")
        from .climb import gate_proposal
        g = gate_proposal(lr, p)
        if g.get("pass"):
            return Decision(p.id, "auto_promoted", 5, "passed the eval gate")
        return Decision(p.id, "gate_failed", 5, g.get("reason") or "failed the eval gate", g.get("failures", []))
    # Rule 1 only shortcuts ordinary lessons after the mandatory checks.
    if principal_said(rt.ledger, p, provenance.principal_names(rt.store)):
        return Decision(p.id, "promoted", 1, "the principal said it")
    # Rule 4. A first import is a batch the principal reviews (ARCHITECTURE section 5): history alone never applies it.
    if p.ptype in ("taste", "fact") and lab == "high" and n_sessions >= int(cfg["min_sessions"]) \
            and not p.critic_note:
        if imported_only(ledger, ids):
            return Decision(p.id, "wait", 4, "evidence is all imported history: waits for the principal's review")
        return Decision(p.id, "auto_promoted", 4, f"high weight, {n_sessions} sessions, no objection")
    return _rule6(lr, p, weight, lab, cfg, now)


def _rule6(lr: Learner, p: Proposal, weight: float, lab: str, cfg: dict, now: str) -> Decision:
    age = taste.age_days(p.created, now) if p.created else 0.0
    voi = 0.7 * (1.0 - weight)
    if age >= float(cfg["stale_days"]) and voi >= float(cfg["voi_floor"]) and lab != "high":
        return Decision(p.id, "question", 6, f"open {age:.0f} days with value of information {voi:.2f}")
    if lab == "medium":
        return Decision(p.id, "hint", 6, "medium weight: a hint, not enforced")
    return Decision(p.id, "wait", 6, f"{lab} weight; waiting for more evidence")


# --- revert (5.7) ---------------------------------------------------------------

def revert(lr: Learner, pid: str, reason: str, *, follow_up: bool = True) -> dict:
    """Revert an accepted lesson by adding events. The pack and taste.md render again without it."""
    rt = lr.runtime
    if rt is None:
        return {"reverted": False, "reason": "learning is off"}
    with rt.lock:
        res = proposal_revert(rt.store, rt.ledger, pid, reason, author=rt.agent, follow_up=follow_up)
        export_taste(lr)
    return {"reverted": True, **res}


# --- views ----------------------------------------------------------------------

def accepted_rules(lr: Learner, now: str | None = None) -> list[tuple[str, str, float, str]]:
    """(domain, rule text, weight, proposal id) of every accepted taste or fact lesson."""
    rt = lr.runtime
    st = load_state(rt.ledger)
    pol = load_policy(rt.store)
    now = now or _now()
    rows = []
    for pid, b in st.beliefs.items():
        p = st.proposals[pid]
        if b.status != "accepted" or p.ptype not in ("taste", "fact"):
            continue
        text = f"Wants {p.wants}" if p.wants else p.claim
        if p.avoid:
            text += f", not {p.avoid}"
        rows.append((p.domain, text, round(b.weight(now, pol["half_life_days"]), 4), pid))
    return rows


def export_taste(lr: Learner, path: str | Path | None = None) -> Path | None:
    """Write `<store>/taste.md` in Command Code's format (`- rule. confidence: 0.82`). Rebuildable from the ledger."""
    rt = lr.runtime
    if rt is None:
        return None
    rows = accepted_rules(lr)
    out = Path(path) if path else rt.root / "taste.md"
    out.write_text(taste.export_taste_md([(d, t, w) for d, t, w, _ in rows]), encoding="utf-8")
    return out


def learned_since(lr: Learner, since: str | None = None) -> list[str]:
    """Short lines for the morning brief: what was learned (and by which rule) since `since`."""
    rt = lr.runtime
    if rt is None:
        return []
    lines = []
    for ev in rt.ledger.iter(kind="note"):
        b = ev["body"] or {}
        if b.get("type") == "learn_promotion" and (since is None or ev["ts"] >= since):
            how = "auto-promoted" if b.get("auto") else "promoted"
            lines.append(f"Learned ({b.get('ptype')}, {how}): {b.get('claim')} [{b.get('proposal_id')}]")
    return lines
