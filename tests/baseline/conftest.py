"""Fixture stores for the house taste tests. Every store here is made up; no test reads a real store."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from exo import crypto, taste
from exo.consolidate import Proposal
from exo.ledger import Ledger
from exo.store import PrincipalStore


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


NOW = datetime.now(timezone.utc)


def write_node(store: PrincipalStore, nid: str, type_: str, summary: str, *, confidence="high", stability="durable",
               tags=(), links=(), extra="") -> None:
    domain = nid.split("/", 1)[0]
    link_lines = "".join(f"  - {{to: {t}, rel: see-also}}\n" for t in links)
    text = (f"---\nid: {nid}\ntitle: {nid.rsplit('/', 1)[-1].replace('-', ' ')}\ntype: {type_}\ndomain: {domain}\n"
            f"summary: {summary}\nload_when: always\nconfidence: {confidence}\nstability: {stability}\n"
            f"valid_from: 2026-01-01\nsources:\n  - {{kind: principal-said, ref: \"event:x\"}}\n"
            f"links:\n{link_lines}tags: [{', '.join(tags)}]\n{extra}---\n\nBody.\n")
    p = store.nodes_dir / f"{nid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def add_belief(led: Ledger, pid: str, claim: str, node_id: str, *, kind="correction", type_="preference",
               ts: str | None = None, strength=0.6, overrides=None, scope=None, author="principal") -> None:
    ts = ts or iso(NOW)
    ev = led.append("utterance", {"text": claim}, author=author)
    p = Proposal(id=pid, kind=kind, action="update", node_id=node_id, claim=claim, wants=claim, avoid=None,
                 because="", implication="", domain=node_id.split("/", 1)[0], type=type_, title=claim[:40],
                 confidence="high", weight=strength, sig=sorted(taste.signature(claim, None)),
                 evidence=[{"id": ev["id"], "ts": ts, "strength": strength}], pair={}, guarded=False,
                 reflector="rule", created=ts)
    body = p.body()
    if overrides:
        body["overrides"] = overrides
    if scope:
        body["scope"] = scope
    led.append("belief_proposed", body, author="exo-consolidate")
    led.append("belief_accepted", {"proposal_id": pid, "node_id": node_id}, author="principal")


def note(led: Ledger, type_: str, author="principal", **kw) -> None:
    led.append("note", {"type": type_, **kw}, author=author)


def open_ledger(store: PrincipalStore) -> Ledger:
    return Ledger(store.ledger_path, crypto.Signer("principal", crypto.keygen()))


def build_principal_store(root: Path) -> PrincipalStore:
    """The made-up principal: shippable method and value nodes, plus everything that must never ship."""
    s = PrincipalStore.create(root)
    write_node(s, "mind/short-paragraphs", "preference",
               "The principal wants answers as short paragraphs, not bullet lists.")
    write_node(s, "work/verify-before-done", "rule",
               "Verify a fix by running the tests before calling it done.", stability="core")
    write_node(s, "work/result-first", "procedure", "Lead the report with the result, then the evidence.")
    write_node(s, "mind/bad-news-first", "value",
               "Lead with bad news; do not bury it, because years at a firm that hid bad numbers taught it.")
    write_node(s, "mind/evidence-over-authority", "preference",
               "Weigh evidence above who said it (for example a famous expert's word).", tags=("worldview",))
    write_node(s, "mind/contested-reasoning", "value",
               "On contested political or religious questions, show the strongest case for each side and leave "
               "the conclusion to the principal.", tags=("contested",))
    # never shipped
    write_node(s, "person/sam-rivera", "entity", "Sam Rivera, a client contact at acme-corp.example.")
    write_node(s, "work/merge-guard", "rule", "Never merge or push to main without approval.")
    write_node(s, "work/client-acme", "preference", "Send the weekly report on Fridays.", tags=("client",))
    write_node(s, "mind/stubborn", "trait", "Holds a view for a long time once formed.")
    write_node(s, "mind/low-confidence", "preference", "Prefer tables for comparisons.", confidence="low")
    write_node(s, "systems/tied-to-person", "preference", "Use the shared drive for drafts.",
               links=("person/sam-rivera",))
    write_node(s, "mind/faded", "preference", "Use headings in long answers.")
    s.reload()
    with open_ledger(s) as led:
        note(led, "session_start")
        add_belief(led, "p-short", "The user wants answers as short paragraphs, not bullet lists.",
                   "mind/short-paragraphs", strength=0.9)
        add_belief(led, "p-faded", "Use headings in long answers.", "mind/faded",
                   ts=iso(NOW - timedelta(days=200)), strength=0.6)
    return s


@pytest.fixture
def principal_store(tmp_path):
    return build_principal_store(tmp_path / "principal")
