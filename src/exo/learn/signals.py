"""Signals in: feedback verdicts, ui responses and chat corrections, all as ledger events (LEARNING-LOOP 5.3)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from exo import capture, provenance
from exo.consolidate import PAIRS_FILE
from exo.ledger import VERDICTS

from .learner import Learner, get_learner
from .runtime import PRINCIPAL, Runtime

MAX_ANSWER = 4000
CORRECTION_VERDICTS = ("edit", "reject", "should_have_been")
CAPTURED = ("ruling", "verdict", "question")  # the ledger kinds a captured signal is written as


def _h(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:24]


def _domain(rt: Runtime, text: str) -> str:
    from exo.consolidate import guess_domain
    nodes = rt.store.nodes_dir
    domains = sorted(p.name for p in nodes.iterdir() if p.is_dir()) if nodes.is_dir() else []
    return guess_domain(text, domains) or "general"


def ensure_utterance(rt: Runtime, session: str | None, role: str, text: str, message_id: str | None = None) -> str:
    """The id of the ledger utterance for `text` in `session`: the one exo-memory wrote, or a backfill."""
    want = capture.normalize(text)
    for ev in rt.ledger.iter(kind="utterance", session=session):
        b = ev["body"] or {}
        if b.get("role") == role and capture.normalize(str(b.get("text") or "")) == want:
            return ev["id"]
    body = {"text": text or "(empty)", "role": role, "source": "learn_backfill"}
    if message_id:
        body["message_id"] = message_id
    return rt.ledger.append("utterance", body, rt.ledger.author if role != "user" else PRINCIPAL,
                            session=session)["id"]


def _answer_and_context(lr: Learner, session: str, message_id: str | None) -> tuple[str, str, str | None]:
    """(answer text, the principal message before it, resolved message id) for a verdict."""
    msgs = lr.messages(session)
    idx = None
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.role == "assistant" and m.text().strip() and (message_id is None or m.id == message_id):
            idx = i
            break
    if idx is None:
        return "", "", message_id
    context = next((m.text() for m in reversed(msgs[:idx]) if m.role == "user" and m.text().strip()
                    and provenance.is_principal_author(provenance.author_of(m))), "")
    return msgs[idx].text(), context, msgs[idx].id


def append_pair(rt: Runtime, pair: Mapping[str, Any]) -> bool:
    """Add a preference pair to `training/preference-pairs.jsonl` unless its id is already there."""
    path = rt.root / PAIRS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and any(json.loads(l).get("id") == pair["id"] for l in path.read_text("utf-8").splitlines()
                              if l.strip().startswith("{")):
        return False
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(pair), ensure_ascii=False) + "\n")
    return True


def handle_feedback(engine: Any, body: Any) -> dict:
    """`feedback.send`: write a verdict event (accept, edit, reject, should_have_been) with the answer text and
    the edit. An edit or should_have_been with text also appends a preference pair (context, rejected, chosen).
    A non-accept verdict starts the fast loop. Returns {"recorded": False, "reason"} when learning is off."""
    lr = get_learner(engine)
    rt = lr.runtime
    if rt is None:
        return {"recorded": False, "reason": lr.notices[0] if lr.notices else "learning is off"}
    verdict = getattr(body, "verdict", None)
    if verdict not in VERDICTS:
        return {"recorded": False, "reason": f"unknown verdict {verdict!r}"}
    session = getattr(body, "session", None)
    if lr.private(session):  # D-028: nothing from a private thread (or its child) reaches the ledger, verdicts too
        return {"recorded": False, "reason": "this is a private thread, or runs under one: feedback on it is not "
                                             "remembered"}
    text = (getattr(body, "text", "") or "").strip()
    answer, context, mid = _answer_and_context(lr, session, getattr(body, "message_id", None))
    with rt.lock:
        target = ensure_utterance(rt, session, "assistant", answer or "(answer not in the transcript)", mid)
        ev_body: dict[str, Any] = {"verdict": verdict, "target": target, "source": "ui", "answer": answer[:MAX_ANSWER]}
        if mid:
            ev_body["message_id"] = mid
        if context:
            ev_body["context"] = context[:2000]
        if verdict in ("edit", "should_have_been") and text:
            ev_body["edit"] = text
            ev_body["chosen"] = text
        elif verdict == "reject" and text:
            ev_body["reason"] = text
        ev = rt.ledger.append("verdict", ev_body, PRINCIPAL, session=session)
        pair = False
        if verdict in ("edit", "should_have_been") and text and answer:
            pair = append_pair(rt, {"id": "pp-" + _h(ev["id"]), "context": context or "(no principal message)",
                                    "rejected": answer, "chosen": text, "evidence": [ev["id"]],
                                    "domain": _domain(rt, context + " " + text), "source": "feedback"})
    started = False
    if verdict != "accept":
        started = lr.trigger(session, "verdict") if hasattr(lr, "trigger") else False
    return {"recorded": True, "event": ev["id"], "verdict": verdict, "pair": pair, "review": started}


def _panel_sessions(engine: Any, panel_id: str) -> list[str]:
    """Every session with a `ui.panel` event for panel `panel_id` in sessions.sqlite: the one that opened it and any
    that updated it (`ui update` takes a known id from any thread). Empty if unknown."""
    store = getattr(engine, "store", None)
    if not panel_id or store is None:
        return []
    rows = store._q("SELECT DISTINCT session FROM events WHERE type='ui.panel' AND json_extract(body, '$.panel_id')=?",
                    (panel_id,))
    return [r["session"] for r in rows]


def handle_ui_respond(engine: Any, body: Any) -> dict:
    """`ui.respond`: log what the user did with a panel (opened, dismissed, acted, ignored) as a `note` event."""
    lr = get_learner(engine)
    rt = lr.runtime
    if rt is None:
        return {"recorded": False, "reason": lr.notices[0] if lr.notices else "learning is off"}
    shown = _panel_sessions(engine, getattr(body, "panel_id", ""))
    if any(lr.private(x) for x in shown):  # D-028: its value can hold a comment
        return {"recorded": False, "reason": "this panel is from a private thread: what you did with it is not "
                                             "remembered"}
    value = dict(getattr(body, "value", None) or {})
    if len(json.dumps(value, default=str)) > 4000:
        value = {"truncated": True}
    with rt.lock:
        ev = rt.ledger.append("note", {"type": "ui_signal", "source": "ui", "panel_id": getattr(body, "panel_id", ""),
                                       "action": getattr(body, "action", ""), "value": value}, PRINCIPAL)
    return {"recorded": True, "event": ev["id"]}


def known_keys(rt: Runtime) -> set:
    """The capture keys of every ruling, verdict and question in the ledger (#263).

    The set stays on `rt` with the last row read, and a call reads only the rows after it. The ledger is append-only
    and nothing takes a key back, so the set only grows. If the ledger no longer holds that row in its place (another
    ledger, or a file swapped or cut short), the set is built again from the first row. Call it under `rt.lock`."""
    seq, last, keys = rt.known
    if last is not None and ((ev := rt.ledger.get(last)) is None or ev["seq"] != seq):
        seq, last, keys = 0, None, set()
    for ev in rt.ledger.iter(kind=CAPTURED, since=seq):
        keys.add((ev["body"] or {}).get("capture_key"))
        seq, last = ev["seq"], ev["id"]
    rt.known = (seq, last, keys)
    return keys


def record_chat_signals(lr: Learner, session: str, messages: Sequence[Mapping[str, Any]] | None = None) -> list[dict]:
    """Capture rulings and corrections in the recent chat as ledger events, once each (by capture key).

    exo-memory may already have written them; its capture keys are the same, so nothing is doubled.
    Returns the events written, newest last. Each correction is a verdict on the agent message before it."""
    rt = lr.runtime
    if rt is None:
        return []
    if messages is None:
        messages = [provenance.flat(m) for m in lr.last_messages(session, 12)]
    msgs = provenance.principal_view(messages)  # D-040: a peer message or job notice is not the principal
    out = []
    with rt.lock:
        known = known_keys(rt)
        for sig in capture.extract_signals(msgs):
            if sig.key in known or sig.kind == capture.DECISION:
                continue
            kind, body = capture.to_event(sig, source="learn")
            if kind == "verdict":
                said = capture.content_text(msgs[sig.target_index].get("content")) if sig.target_index is not None else ""
                body["target"] = ensure_utterance(rt, session, "assistant", said or "(agent output not in the transcript)")
            # No known.add: extract_signals gives each key once, and the set holds only what known_keys read.
            out.append(rt.ledger.append(kind, body, PRINCIPAL, session=session))
    return out
