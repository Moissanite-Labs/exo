"""`exo taste export-house`: build the house taste pack from the principal's store (D-044 wave 1).

    res = export_house(store, out, "1.3.0")            # select, neutralise, check, write
    res = export_house(store, out, "1.3.0", check_only=True)

1. **Select.** Nodes of type preference, rule or procedure (method items) or value (value items; a node
   tagged `worldview` is a value item too) in the domains work, systems and mind, with confidence medium or
   high, stability core or durable, no `valid_until`, current weight at least 0.45. The weight is the
   noisy-or weight of the node's accepted beliefs at the ledger head's time (so a rerun on the same snapshot
   selects the same items); a node with no belief in the ledger takes its confidence label (high 0.9, medium
   0.6). Dropped: other types and domains, nodes tagged private, person or client, nodes linked to a
   person, relationship, entity, client or project node, and guarded items (`exo.taste.guards`).
2. **Neutralise.** "The principal" becomes "the user". Value items also lose any example, "for example"
   tail and "because" clause. Only the claim and an evidence count survive; the id is a hash of the
   normalised claim.
3. **Check.** `exo.baseline.privacy`: a fail anywhere means nothing is written. A hold keeps the item out
   and lists it in the review. An optional judge (the decision model) can only hold, never pass.
4. **Write.** MANIFEST.json, items.jsonl, soul/house.md and review/REVIEW.md. No policy files, no
   timestamps. The same store snapshot and script version give the same bytes.

Gate, approval, budget and posting defaults are product policy, not taste: nothing here reads them.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import taste
from ..store import PrincipalStore
from . import privacy
from .pack import FORMAT, PACK_NAME, HouseItem, dumps, item_line, sha256

SCRIPT_VERSION = "1"
METHOD_TYPES = ("preference", "rule", "procedure")
DOMAINS = ("work", "systems", "mind")
MIN_WEIGHT = 0.45
CONFIDENCE_WEIGHT = {"high": 0.9, "medium": 0.6}
EXCLUDED_TAGS = {"private", "person", "client"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

HOUSE_SOUL = """# House soul

Speak plainly and briefly. Lead with the answer, then the reasons. Use short sentences and common words.
Match the user's register. No filler and no performed enthusiasm. When something is unclear, say so in one
line and go on.
"""

# A judge asks the decision model about one shippable line and returns a hold reason, or None.
Judge = Callable[[str, str], "str | None"]


class ExportError(ValueError):
    pass


@dataclass
class Candidate:
    node_id: str
    cls: str
    type: str
    domain: str
    claim: str
    evidence: int
    contested: bool

    def item(self) -> HouseItem:
        want, avoid, _ = _parse(self.claim)
        return HouseItem(item_id(self.claim), self.cls, self.type, self.domain, self.claim,
                         taste.signature(want, avoid), self.evidence, self.contested)


@dataclass
class ExportResult:
    version: str
    items: list[HouseItem] = field(default_factory=list)
    held: list[tuple[HouseItem, list[str]]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)    # (where, hit)
    dropped: dict[str, int] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    judged: bool = False
    written: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures

    def render(self) -> str:
        if not self.ok:
            lines = [f"export-house {self.version}: FAILED, nothing written ({len(self.failures)} hit(s))"]
            lines += [f"  {w}: {h}" for w, h in self.failures]
            return "\n".join(lines)
        n_val = sum(1 for i in self.items if i.cls == "value")
        head = "checked" if not self.written else "written"
        lines = [f"export-house {self.version}: {head}; {len(self.items)} item(s) "
                 f"({len(self.items) - n_val} method, {n_val} value), {len(self.held)} held"]
        lines += [f"  dropped {k}: {v}" for k, v in sorted(self.dropped.items())]
        lines += [f"  held {i.id}: {'; '.join(r)}" for i, r in self.held]
        if not self.judged:
            lines.append("  decision-model pass: not run (add --decision)")
        return "\n".join(lines)


def item_id(claim: str) -> str:
    return "h-" + sha256(" ".join(claim.lower().split()).encode())[:12]


def _parse(claim: str):
    from ..consolidate import parse_instruction
    want, avoid, _ = parse_instruction(claim)
    return (want or claim), avoid, None


def neutralise(text: str, cls: str) -> str:
    """Apply limited mechanical cleanup to a claim. Value examples and the clause after "because" are removed;
    first-person stories are not semantically rewritten and must be rephrased by the principal."""
    from ..consolidate import split_reason
    t = " ".join(str(text or "").split())
    t = re.sub(r"\b([Tt])he principal's\b", r"\1he user's", t)
    t = re.sub(r"\b([Tt])he principal\b", r"\1he user", t)
    t = re.sub(r"\bprincipal's\b", "user's", t)
    t = re.sub(r"\bprincipal\b", "user", t)
    if cls == "value":
        t = re.sub(r"\s*\((?:e\.g\.|eg|for example|for instance|such as|like)\b[^)]*\)", "", t, flags=re.I)
        t = re.sub(r",?\s+(?:for example|for instance|such as|e\.g\.)(?:\s|,).*$", "", t, flags=re.I)
        t = split_reason(t)[0]
    t = t.strip().rstrip(" ,;:-")
    if t and t[-1] not in ".!?":
        t += "."
    return t[:1].upper() + t[1:]


# --- source -------------------------------------------------------------------

def source_info(store: PrincipalStore) -> dict:
    """`{head, source_id, now}` from the store's ledger without writing to it. `source_id` is a hash of the
    first event's id: stable for the life of the store, and it reveals nothing. `now` is the head event's
    time, so weights are a function of the snapshot."""
    info = {"head": sha256(b""), "source_id": "", "now": None}
    if not store.ledger_path.is_file():
        return info
    from ..ledger import Ledger
    with Ledger(store.ledger_path) as led:
        head = led.head()
        info["head"] = sha256(head.chain.encode())
        row = led.db.execute("SELECT id FROM events ORDER BY seq LIMIT 1").fetchone()
        if row:
            info["source_id"] = sha256(("exo-house-source\n" + row[0]).encode())
        if head.id:
            ev = led.get(head.id)
            info["now"] = ev and ev.get("ts")
    return info


def _belief_weights(store: PrincipalStore, now: str | None) -> tuple[dict[str, float], dict[str, int], dict]:
    """Per node: the highest current weight of its accepted beliefs, and their evidence count."""
    from ..consolidate import load_policy, load_state
    pol = load_policy(store)
    weights: dict[str, float] = {}
    counts: dict[str, int] = {}
    if not store.ledger_path.is_file() or not now:
        return weights, counts, pol
    from ..ledger import Ledger
    with Ledger(store.ledger_path) as led:
        st = load_state(led)
    for b in st.beliefs.values():
        if b.status == "accepted" and b.node_id:
            w = b.weight(now, pol["half_life_days"])
            weights[b.node_id] = max(weights.get(b.node_id, 0.0), w)
            counts[b.node_id] = counts.get(b.node_id, 0) + len(b.evidence)
    return weights, counts, pol


def _identity_ids(nodes: Mapping[str, dict]) -> set[str]:
    return {nid for nid, n in nodes.items() if privacy._node_is_identity(n)}


def select(store: PrincipalStore, now: str | None) -> tuple[list[Candidate], dict[str, int]]:
    """Step 1 and 2: the shippable candidates in neutral form, and counts of what was dropped and why."""
    weights, counts, pol = _belief_weights(store, now)
    guard_words = pol.get("guard_words") or taste.GUARD_WORDS
    nodes = store.nodes
    identity = _identity_ids(nodes)
    dropped: dict[str, int] = {}
    out: list[Candidate] = []

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    for nid in sorted(nodes):
        n = nodes[nid]
        typ, dom = str(n.get("type") or ""), str(n.get("domain") or "")
        tags = {str(t).lower() for t in (n.get("tags") or [])}
        if typ not in METHOD_TYPES and typ != "value":
            drop("type not shipped")
            continue
        if dom not in DOMAINS:
            drop("domain not shipped")
            continue
        if tags & EXCLUDED_TAGS:
            drop("tagged private, person or client")
            continue
        links = [str(lk.get("to") or "") if isinstance(lk, Mapping) else str(lk) for lk in (n.get("links") or [])]
        if any(t in identity or t.split("/", 1)[0] in privacy.DENY_DOMAINS for t in links):
            drop("linked to a person, client or project")
            continue
        if n.get("valid_until"):
            drop("superseded")
            continue
        if str(n.get("confidence")) not in CONFIDENCE_WEIGHT or str(n.get("stability")) not in ("core", "durable"):
            drop("confidence or stability too low")
            continue
        weight = weights.get(nid, CONFIDENCE_WEIGHT[str(n.get("confidence"))])
        if weight < MIN_WEIGHT:
            drop("weight below 0.45")
            continue
        raw = str(n.get("summary") or "").strip()
        if not raw:
            drop("no claim")
            continue
        if taste.guards(raw, guard_words):
            drop("guarded")
            continue
        cls = "value" if typ == "value" or "worldview" in tags else "method"
        evidence = counts.get(nid) or len(n.get("sources") or [])
        out.append(Candidate(nid, cls, typ, dom, neutralise(raw, cls), evidence, "contested" in tags))
    return out, dropped


# --- review -------------------------------------------------------------------

def review_text(version: str, items: list[HouseItem], held: list[tuple[HouseItem, list[str]]],
                dropped: Mapping[str, int], judged: bool) -> str:
    method = [i for i in items if i.cls == "method" and not i.contested]
    value = [i for i in items if i.cls == "value" and not i.contested]
    contested = [i for i in items if i.contested]
    out = [f"# House taste {version}: review", "",
           "Publishing is public and cannot be undone. Read every item. Each value item and each contested item",
           "needs your tick before the release is final:", "",
           "    exo taste review <this folder> --tick <id> [<id> ...]",
           "    exo taste review <this folder> --final", "",
           f"Decision-model pass: {'run' if judged else 'not run (export with --decision to add it)'}.", ""]

    def page(title: str, rows: list[HouseItem], tick: bool) -> None:
        out.extend([f"## {title} ({len(rows)})", ""])
        out.extend(f"- {'[ ] ' if tick else ''}`{i.id}` ({i.domain}, {i.type}) {i.claim}" for i in rows)
        out.append("")

    page("Page 1: method items", method, False)
    page("Page 2: value items, tick each", value, True)
    page("Page 3: contested questions, reasoning style only, tick each", contested, True)
    out.extend([f"## Held, not in the pack ({len(held)})", ""])
    out.extend(f"- `{i.id}` {i.claim} -- {'; '.join(r)}" for i, r in held)
    out.extend(["", "## Dropped by rule", ""])
    out.extend(f"- {k}: {v}" for k, v in sorted(dropped.items()))
    return "\n".join(out).rstrip() + "\n"


def _skills() -> list[str]:
    import json
    p = Path(__file__).resolve().parents[3] / "skills" / "MANIFEST.json"
    try:
        return sorted(str(s["name"]) for s in json.loads(p.read_text(encoding="utf-8")).get("skills") or [])
    except (OSError, ValueError, KeyError, TypeError):
        return []


# --- export -------------------------------------------------------------------

def export_house(store: PrincipalStore, out: str | os.PathLike, version: str, *, check_only: bool = False,
                 soul: str | None = None, judge: Judge | None = None,
                 env: Mapping[str, str] | None = None) -> ExportResult:
    if not VERSION_RE.match(version or ""):
        raise ExportError(f"version must look like 1.3.0, not {version!r}")
    if not store.exists():
        raise ExportError(f"{store.root} is not a principal store")
    outp = Path(out).expanduser().resolve()
    if outp == store.root or store.root in outp.parents:
        raise ExportError("--out must be outside the source store")
    res = ExportResult(version, judged=judge is not None)
    src = source_info(store)
    cands, res.dropped = select(store, src["now"])
    checker = privacy.Checker.for_store(store, env)

    by_id: dict[str, HouseItem] = {}
    for c in cands:
        it = c.item()
        hits = checker.check(it.claim, value=it.cls == "value")
        if privacy.verdict(hits) == "fail":
            res.failures += [(c.node_id, str(h)) for h in hits if not h.hold]
            continue
        reasons = [str(h) for h in hits]
        if judge is not None and not reasons:
            why = judge(it.claim, it.cls)      # it can only hold, never pass
            if why:
                reasons.append(f"decision model: {why}")
        if reasons:
            res.held.append((it, reasons))
            continue
        if it.id in by_id:
            it = replace(by_id[it.id], evidence=by_id[it.id].evidence + it.evidence)
        by_id[it.id] = it
    soul_text = soul if soul is not None else HOUSE_SOUL
    res.failures += [("soul/house.md", str(h)) for h in checker.check(soul_text, value=True)]
    res.items = [by_id[k] for k in sorted(by_id)]
    res.held.sort(key=lambda x: x[0].id)
    if not res.ok:
        return res

    items_bytes = ("".join(item_line(i.to_json()) + "\n" for i in res.items)).encode()
    soul_bytes = soul_text.encode()
    manifest = {
        "format": FORMAT, "name": PACK_NAME, "version": version, "script_version": SCRIPT_VERSION,
        "items": len(res.items),
        "classes": {"method": sum(1 for i in res.items if i.cls == "method"),
                    "value": sum(1 for i in res.items if i.cls == "value")},
        "contested": sum(1 for i in res.items if i.contested),
        "files": {"items.jsonl": sha256(items_bytes), "soul/house.md": sha256(soul_bytes)},
        "source_head": src["head"], "source_id": src["source_id"], "skills": _skills(),
    }
    res.files = {"MANIFEST.json": dumps(manifest).encode(), "items.jsonl": items_bytes, "soul/house.md": soul_bytes,
                 "review/REVIEW.md": review_text(version, res.items, res.held, res.dropped, res.judged).encode()}
    if check_only:
        return res
    for rel, data in res.files.items():
        p = outp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
    res.written = True
    return res


# --- the decision-model judge ---------------------------------------------------

QUESTION = {
    "method": "Does this line name or point to a real person, company, place or account?",
    "value": "Does this line reveal or point to the holder's religion, politics, ethnicity, nationality, health, "
             "family, or a life event?",
}


def decision_judge(router=None, threshold: float = 0.5) -> Judge:
    """A judge on the `decision` role (D-015). It holds when the model says yes, and holds a value item when
    the model cannot be asked; it never passes anything the rules failed."""
    import asyncio

    from ..core.models.decision import DecisionError, ask_choice_result
    if router is None:
        from ..models_cmd import build_router
        router = build_router()

    def judge(text: str, cls: str) -> str | None:
        try:
            res = asyncio.run(ask_choice_result(router, text, QUESTION[cls], ["yes", "no"]))
        except (DecisionError, ValueError, OSError) as e:
            return f"not asked ({e})" if cls == "value" else None
        p = float(res.probabilities.get("yes", 0.0))
        return f"says it points to the holder (p={p:.2f})" if p >= threshold else None

    return judge
