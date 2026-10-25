"""`/taste` (D-044 wave 3): what Exo's taste is now, what is shifting, and the user's controls.

    /taste                       mode, then house, learned, pending, overridden and stepped-back items
    /taste show <id>             one item: claim, source, weight, state
    /taste why <id>              the weeks and signals behind it
    /taste mode [ask|silent]     how inferred changes reach you
    /taste yes|no|later <id>     answer a proposal (ask mode)
    /taste pin|unpin <id>        freeze an item: no fading, no swap, no step back
    /taste off|on <id>           turn a house item off now (a ruling), or back on
    /taste undo <id>             reverse a swap, a scoped item or an adoption (then 90 days quiet)
    /taste restore <id>          bring back an overridden or stepped-back item (a ruling)
    /taste reset <id>            drop the learned version and return to the house item
    /taste reset all confirm     drop every learned taste item; rulings and ledger rows stay

It is the built-in `taste` of `command.run` (the protocol is capped at 30 commands). Reading is open to any
caller; every write is the principal's only (D-052) and lands as a `note` written by the principal, so the
replay (`exo.baseline.adapt`) can tell it from anything an agent wrote.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..store import PrincipalStore
from . import adapt, shift
from .layers import Entry, TasteView, view

USAGE = ("usage: /taste [list] | show <id> | why <id> | mode [ask|silent] | yes|no|later <id> | pin|unpin <id> | "
         "off|on <id> | undo <id> | restore <id> | reset <id> | reset all confirm")
WRITES = {"mode", "yes", "no", "later", "pin", "unpin", "off", "on", "undo", "restore", "reset"}
NOTE = {"pin": "taste_pin", "unpin": "taste_unpin", "off": "taste_off", "on": "taste_on"}


class TasteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _id(v: TasteView, ref: str) -> str:
    """An item id from a full id or a unique prefix."""
    ids = {e.id for e in v.entries} | {h.id for h in v.pack.items}
    if v.state is not None:
        ids |= set(v.state.items) | {u.id for u in (v.state.layer.items if v.state.layer else [])}
    if ref in ids:
        return ref
    hits = sorted(i for i in ids if i.startswith(ref))
    if len(hits) == 1:
        return hits[0]
    raise TasteError("not_found", f"no taste item {ref!r}" if not hits else f"{ref!r} matches {len(hits)} items")


def _claim(v: TasteView, iid: str) -> str:
    st = v.state
    if st is not None and iid in st.items:
        return st.items[iid].subject.claim
    return next((e.claim for e in v.entries if e.id == iid), iid)


# --- reading ---------------------------------------------------------------------------

def pending_line(v: TasteView, iid: str) -> str | None:
    """'You have done X in 3 of the last 5 weeks, against Y. ...' for an item with a live record."""
    st = v.state
    it = st.items.get(iid) if st is not None else None
    if it is None:
        return None
    t = it.track
    yours = t.claim or "it your own way"
    if t.proposal is not None:
        proposed_scope = t.proposal.get("snapshot", {}).get("scope")
        what = {"swap": "switch to your way", "scope": f"use your way in {proposed_scope or 'that work'}",
                "adopt": "take on your view", "restore": "bring back the house view"}[t.proposal["kind"]]
        yours = t.proposal.get("snapshot", {}).get("claim") or yours
        return (f"Waiting for you: {what}: {yours} (against '{it.subject.claim}'). "
                f"/taste yes {iid}, /taste no {iid} or /taste later {iid}")
    if t.s <= 0 or t.status in ("swapped", "adopted"):
        return None
    n = t.contra_weeks(5)
    head = f"You have done {yours} in {n} of the last 5 weeks, against '{it.subject.claim}'."
    if t.status == "armed":
        est = shift.estimate_weeks(t, it.subject.innate(st.now.astimezone(st.tz).date()), st.pol)
        tail = f" Fading since week {(t.armed_week or 0) + 1}"
        tail += f"; at this pace it switches in about {est} week{'s' if est != 1 else ''}." if est else "."
        return head + tail + (" (ask mode: it will ask first)" if st.mode == "ask" else "")
    if t.status == "stepped_back":
        return head + f" Stepped back; S = {t.s} of {st.pol['value_adopt_weeks']} to take on your view" + \
            ("." if t.stated else " (needs your view in your own words).")
    if t.scope:
        return head + f" Bound to {t.scope}: it would apply there only."
    return head + f" S = {t.s}; it arms at {st.pol['arm_weeks']}."


def _row(e: Entry) -> str:
    return f"  {e.weight:.2f}  {e.cls:<6} {e.claim} {e.tag}  {e.id}"


def render(v: TasteView) -> str:
    st = v.state
    mode = st.mode if st else "ask"
    lines = [f"mode: {mode} ({st.mode_source if st else 'default'}); /taste mode "
             f"{'silent' if mode == 'ask' else 'ask'} to change"]
    p = v.pack
    from .layers import render as render_layers
    lines.append(render_layers(v).splitlines()[0])
    house = [e for e in v.entries if e.source == "house"]
    learned = [e for e in v.entries if e.source in ("learned", "ruling", "pinned")]
    stepped = [e for e in v.entries if e.source == "stepped_back"]
    covered = {e.house_id for e in v.entries if e.house_id and e.house_id != e.id}
    over = [h for h in p.items if h.id in covered] if v.house_on else []
    shadowed = []
    if st is not None and st.layer is not None:
        listed = {e.id for e in v.entries}
        shadowed = [u for u in st.layer.items if u.id not in listed]
    pend = [ln for ln in (pending_line(v, i) for i in (st.items if st else {})) if ln]
    sections = [(f"House ({len(house)})", [_row(e) for e in house]),
                (f"Learned ({len(learned)})", [_row(e) for e in learned]),
                (f"Pending ({len(pend)})", [f"  {ln}" for ln in pend]),
                (f"Overridden ({len(over) + len(shadowed)})",
                 [f"  {h.claim} [house {p.short_version}]  {h.id}; /taste restore {h.id}" for h in over]
                 + [f"  {u.claim} [{u.source}]  {u.id}; /taste restore {u.id}" for u in shadowed]),
                (f"Stepped back ({len(stepped)})",
                 [f"  {e.claim} [stepped back]  {e.id}; neutral until your view is clear; /taste restore {e.id}"
                  for e in stepped])]
    for head, rows in sections:
        lines.append(head + ("" if rows else ": none"))
        lines += rows
    lines.append("controls: /taste show|why|pin|off|undo|restore|reset <id>")
    return "\n".join(lines)


def show(v: TasteView, iid: str) -> str:
    e = next((x for x in v.entries if x.id == iid), None)
    st = v.state
    it = st.items.get(iid) if st else None
    lines = [f"{iid}: {_claim(v, iid)}"]
    if e is not None:
        lines.append(f"source {e.tag}, class {e.cls}, weight {e.weight:.2f}")
    else:
        lines.append("not applied now (overridden, off or out of scope)")
    if it is not None:
        t = it.track
        lines.append(f"state: {t.status}" + (f", S = {t.s}" if t.s else "") + (f", armed since week "
                     f"{(t.armed_week or 0) + 1}, n = {t.n}" if t.status == "armed" else "")
                     + (f", your version {t.challenger:.2f}" if t.challenger else "")
                     + (", pinned" if t.pinned else "") + (", automatic changes stopped" if t.stopped else "")
                     + (f", quiet until {t.quiet_until.isoformat()}" if t.quiet_until and
                        t.quiet_until > st.now.astimezone(st.tz).date() else ""))
        ln = pending_line(v, iid)
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def why(v: TasteView, iid: str) -> str:
    st = v.state
    it = st.items.get(iid) if st else None
    if it is None:
        return f"{iid}: no record (the rule runs on house items and learned items, not on this one)"
    t = it.track
    lines = [f"{iid}: {it.subject.claim}", f"state {t.status}, S = {t.s}"]
    verdict = {1: "against", -1: "the house way", 0: "mixed"}
    for w, val in sorted(t.history.items()):
        lines.append(f"  week {w + 1} ({shift.week_close(w, it.origin).isoformat()}): {verdict[val]}")
    for ep in t.record[-10:]:
        lines.append(f"  {ep.day.isoformat()} episode: {ep.verdict}, delta {ep.delta:.2f}, "
                     f"{len(ep.signals)} signal(s)" + (f", {ep.project or ep.task}" if ep.project or ep.task else ""))
    for o in t.outcomes:
        lines.append(f"  {o.at[:10]} {o.kind}" + (f": {o.data.get('why')}" if o.data.get("why") else ""))
    if len(lines) == 2:
        lines.append("  no signals yet")
    return "\n".join(lines)


# --- writing ---------------------------------------------------------------------------

def _reject(ledger, pids, reason: str) -> None:
    for pid in pids:
        ledger.append("note", {"type": "belief_rejected", "proposal_id": pid, "reason": reason}, author="principal")


def _overriders(v: TasteView, iid: str) -> list[str]:
    st = v.state
    if st is None or st.layer is None:
        return []
    from .layers import _matches
    house = next((h for h in v.pack.items if h.id == iid), None)
    targets = {iid}
    found: set[str] = set()
    while True:
        more = {u.id for u in st.layer.items if u.source != "ruling" and u.id not in found
                and (u.overrides in targets or house is not None and _matches(u, house))}
        if not more:
            return sorted(found)
        found |= more
        targets |= more


def write(store: PrincipalStore, ledger, v: TasteView, verb: str, ref: str, now: str | datetime | None = None) -> str:
    """Apply one principal act. The caller has checked that the caller is the principal."""
    st = v.state
    if verb == "mode":
        if ref not in ("ask", "silent"):
            raise TasteError("bad_request", "usage: /taste mode ask|silent")
        ledger.append("note", {"type": "taste_mode", "mode": ref}, author="principal")
        return f"mode: {ref}" + (" (each inferred change waits for your yes)" if ref == "ask" else
                                 " (changes happen, then show in /taste and the brief; /taste undo reverses one)")
    if verb == "reset" and ref in ("all", "all confirm"):
        if ref != "all confirm":
            return "This drops every learned taste item; your rulings and the ledger rows stay. " \
                   "Type /taste reset all confirm"
        pids = [u.id for u in (st.layer.items if st and st.layer else []) if u.source != "ruling"]
        _reject(ledger, pids, "taste reset all")
        ledger.append("note", {"type": "taste_reset", "item": "*"}, author="principal")
        return f"reset: {len(pids)} learned item(s) dropped; house items apply again"
    iid = _id(v, ref)
    claim = _claim(v, iid)
    if verb in ("off", "on") and iid not in {h.id for h in v.pack.items}:
        raise TasteError("bad_request", "off and on apply to house items; use reset for a learned item")
    if verb in NOTE:
        ledger.append("note", {"type": NOTE[verb], "item": iid}, author="principal")
        return {"pin": "pinned", "unpin": "unpinned", "off": "turned off", "on": "turned on"}[verb] + f": {claim}"
    t = st.track(iid) if st else None
    if verb in ("yes", "no", "later"):
        if t is None or t.proposal is None:
            raise TasteError("conflict", f"nothing is waiting for an answer on {iid}")
        ledger.append("note", {"type": "taste_answer", "item": iid, "answer": verb,
                               "proposal": t.proposal.get("at")}, author="principal")
        return {"yes": "done: switched", "no": "kept; it stays quiet for 90 days",
                "later": "left waiting in /taste"}[verb] + f": {claim}"
    if verb == "undo":
        target = iid
        if t is None or t.status not in ("swapped", "adopted"):
            u = next((u for u in (st.layer.items if st and st.layer else []) if u.id == iid and u.overrides), None)
            target = u.overrides if u is not None else iid
            t = st.track(target) if st else None
        mine = _overriders(v, target)
        if (t is None or t.status not in ("swapped", "adopted")) and not mine:
            raise TasteError("conflict", f"{iid} has no swap or adoption to undo")
        _reject(ledger, mine, "taste undo")
        ledger.append("note", {"type": "taste_undo", "item": target}, author="principal")
        return f"undone: '{_claim(v, target)}' applies again; this item stays quiet for 90 days"
    if verb == "restore":
        _reject(ledger, _overriders(v, iid), "taste restore")
        ledger.append("note", {"type": "taste_restore", "item": iid}, author="principal")
        return f"restored: {claim}"
    if verb == "reset":
        u = next((u for u in (st.layer.items if st and st.layer else []) if u.id == iid), None)
        pids = [iid] if u is not None and u.source != "ruling" else _overriders(v, iid)
        _reject(ledger, pids, "taste reset")
        ledger.append("note", {"type": "taste_reset", "item": u.overrides if u and u.overrides else iid},
                      author="principal")
        return f"reset: {len(pids)} learned version(s) dropped" + (f"; '{_claim(v, u.overrides)}' applies again"
                                                                   if u and u.overrides else "")
    raise TasteError("bad_request", USAGE)


def run(store: PrincipalStore, args: str, *, principal: bool, open_ledger: Callable[[], object] | None = None,
        now: str | datetime | None = None, pack=None) -> dict:
    """One `/taste` call. `open_ledger()` gives a writable ledger (needed for writes and to close weeks)."""
    words = (args or "").split()
    verb = words[0].lower() if words else "list"
    ref = " ".join(words[1:])
    if verb in WRITES and not (verb == "mode" and not ref):
        if not principal:
            raise TasteError("forbidden", "only the principal can change taste (D-052)")
        if open_ledger is None:
            raise TasteError("unsupported", "no writable store: learning is off")
        led = open_ledger()
        if verb in ("undo", "restore", "reset", "yes", "no", "later", "pin", "unpin", "off", "on") and not ref:
            raise TasteError("bad_request", USAGE)
        out = write(store, led, view(store, now=now, pack=pack), verb, ref, now)
        adapt.close_weeks(store, led, now=now, pack=pack)
        return {"text": out, "mode": adapt.load(store, now=now, pack=pack).mode}
    if principal and open_ledger is not None:
        try:
            adapt.close_weeks(store, open_ledger(), now=now, pack=pack)   # catch up on week closes first
        except Exception:
            pass
    v = view(store, now=now, pack=pack)
    if verb in ("list", "ls", ""):
        return {"text": render(v), "mode": v.state.mode if v.state else "ask"}
    if verb == "mode":
        st = v.state
        return {"text": f"mode: {st.mode if st else 'ask'} ({st.mode_source if st else 'default'})",
                "mode": st.mode if st else "ask"}
    if verb in ("show", "why"):
        if not ref:
            raise TasteError("bad_request", USAGE)
        iid = _id(v, ref)
        return {"text": show(v, iid) if verb == "show" else why(v, iid)}
    raise TasteError("bad_request", USAGE)


def engine_command(eng, args: str, principal: bool) -> dict:
    """The `command.run taste` built-in: the learner's store and its writable ledger."""
    from ..learn.learner import get_learner
    lr = get_learner(eng)
    rt = lr.runtime
    if rt is None:
        raise TasteError("unsupported", "taste needs a principal store: " + (lr.notices[-1] if lr.notices else
                                                                            "learning is off"))
    with rt.lock:
        return run(rt.store, args, principal=principal, open_ledger=lambda: rt.ledger)


async def tick(store: PrincipalStore, ledger, *, labeller=None, now: str | None = None, pack=None) -> dict:
    """After a review: label the principal's new signals, then close any finished weeks."""
    from .classify import record_signals
    res = await record_signals(store, ledger, labeller=labeller, now=now, pack=pack)
    res["outcomes"] = [b.get("type") for b in adapt.close_weeks(store, ledger, now=now, pack=pack)]
    return res

