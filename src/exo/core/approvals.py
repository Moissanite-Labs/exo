"""Open approval requests (``ApprovalDesk``) and signed approval answers from seats (D-033).

``ApprovalDesk`` holds the engine's open requests: it publishes ``approval.requested``, waits for
``approval.respond``, checks who may answer (D-033, D-039) and closes each card once (D-045).
A client that answers ``approval.respond`` as ``seat:<name>`` must sign the answer with that seat's
Ed25519 key. The signature covers the request id, the decision, the request's binding hash and the
seat name, so it cannot be replayed on another request, another decision or another text.

The public key comes from the bus registry (``$EXO_HOME/bus/registry.json`` or
``$EXO_BUS_REGISTRY``) when that document verifies, else from ``<keys dir>/<seat>.pub`` (the file
``exo.crypto.save_key`` writes beside the seat key).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import hashlib
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from exo import crypto
from exo.gate import approver_allowed, approver_identity
from exo.principal import current_caller, demoted, is_principal, principal_as, require_principal
from exo.protocol import schema as s
from exo.server.engine import CommandFailed

KIND = "exo.approval.v1"


def approval_payload(request_id: str, decision: str, binding: str, seat: str) -> bytes:
    return crypto.canonical({"kind": KIND, "request_id": request_id, "decision": decision,
                             "binding": binding or "", "seat": seat})


def sign_approval(key, request_id: str, decision: str, binding: str, seat: str) -> str:
    """Base64 signature for ``ApprovalRespond.sig``. ``key`` is a SigningKey or a key file path."""
    if isinstance(key, (str, os.PathLike)):
        key = crypto.load_key(key)
    return crypto.sign(approval_payload(request_id, decision, binding, seat), key)


def seat_pubkeys(seat: str, env: Mapping[str, str] | None = None) -> list[str]:
    """Trusted public keys for ``seat``: the verified bus registry first, then the keys folder."""
    env = os.environ if env is None else env
    exo_home = Path(env.get("EXO_HOME") or Path.home() / ".exo").expanduser()
    out: list[str] = []
    reg_path = Path(env.get("EXO_BUS_REGISTRY") or exo_home / "bus" / "registry.json").expanduser()
    if reg_path.is_file():
        try:
            from exo.bus.registry import check_registry
            reg = json.loads(reg_path.read_text("utf-8"))
            check_registry(reg, env.get("EXO_BUS_LEADER_PUBKEY") or None)
            out += [s["pubkey"] for s in reg["seats"] if s.get("seat") == seat and s.get("pubkey")]
        except Exception:
            pass  # an unverified registry is not a key source
    kd = env.get("EXO_BUS_KEYS_DIR") or env.get("DIVAN_KEYS_DIR")
    pub = (Path(kd).expanduser() if kd else exo_home / "keys") / f"{seat}.pub"
    if pub.is_file():
        try:
            out.append(crypto.load_pub(pub))
        except OSError:
            pass
    return list(dict.fromkeys(out))


def verify_approval(sig: str, request_id: str, decision: str, binding: str, seat: str,
                    env: Mapping[str, str] | None = None) -> bool:
    if not sig or not seat:
        return False
    data = approval_payload(request_id, decision, binding, seat)
    return any(crypto.verify(data, sig, pub) for pub in seat_pubkeys(seat, env))


@dataclass
class Pending:
    """One open request: the future its asker waits on, its session, what was shown, and who answered."""
    future: asyncio.Future
    session: str | None
    request: s.ApprovalRequested
    decider: str = "unknown"
    delegation_eligible: bool = False
    delegation_binding: str = ""
    delegation_context: dict | None = None


class ApprovalDesk:
    """The engine's open approval requests. ``publish(body, session)`` sends through the engine's current hub;
    ``waited(session, ms)`` adds approval wait time to the turn summary. ``always`` holds standing grants."""

    def __init__(self, publish: Callable[[Any, str | None], Any], waited: Callable[[str, int], None]) -> None:
        self.publish, self.waited = publish, waited
        self.open: dict[str, Pending] = {}
        self.always: set[str] = set()
        self.delegation_grants: dict[str, str] = {}
        self.delegation_audit = None
        self.delegation_validate = lambda context: False
        self.delegation_parent_allowed = lambda parent: False
        self.delegation_edges = lambda: []
        self.delegation_changed = asyncio.Event()
        self._delegation_lock = threading.RLock()
        self._announced: set[str] = set()  # ids whose ApprovalResolved went out (D-045)

    async def ask(self, session: str | None, summary: str, call: Any = None, risk: str = "medium",
                  decided_by: list[str] | None = None, options: list[str] | None = None,
                  opened: list[str] | None = None, approvers: list[str] | None = None, binding: str = "",
                  delegation_eligible: bool = False, delegation_context: dict | None = None) -> str:
        """Publish an approval request and wait for ``approval.respond``.

        ``decided_by`` (optional out list) receives the id of the client that answered. ``approvers`` limits who
        may answer (``principal``, ``seat:<name>``; empty = any local client that is not a peer). ``binding`` is the
        hash of the exact outbound text; an answer that names a different hash is refused (D-033)."""
        rid, t0 = s.new_id("a"), time.monotonic()
        req = s.ApprovalRequested(request_id=rid, summary=summary, risk=risk, call=call, approvers=list(approvers or []),
                                  binding=binding)
        if options:
            req.options = list(options)
        self.open[rid] = pending = Pending(asyncio.get_running_loop().create_future(), session, req)
        if delegation_eligible and call is not None and session:
            pending.delegation_eligible = True
            pending.delegation_binding = self._delegation_digest(pending)
            pending.delegation_context = delegation_context
        self.delegation_changed.set()
        if opened is not None:
            opened.append(rid)
        answered = False
        try:
            self.publish(req, session)
            decision = await pending.future
            answered = True
            if decided_by is not None:
                decided_by.append(pending.decider)
            return decision
        finally:
            self.delegation_changed.set()
            self.open.pop(rid, None)  # first: a failed publication below leaves no pending row (D-060)
            if session:  # the turn summary reports this apart from tool time
                self.waited(session, int((time.monotonic() - t0) * 1000))
            try:
                if not answered:  # the turn was stopped or the ask cancelled: close the card in every client (D-045)
                    self.announce_resolved(rid, "cancelled", "core", session)
            finally:
                self._announced.discard(rid)

    @staticmethod
    def _delegation_digest(pending: Pending) -> str:
        return hashlib.sha256(crypto.canonical({"session": pending.session, "call": s.to_dict(pending.request.call),
                                               "binding": pending.request.binding})).hexdigest()

    def _delegation_log(self, body: dict, parent: str, context: dict | None = None) -> None:
        try:
            eid = self.delegation_audit(body, parent, context) if self.delegation_audit else None
            if not isinstance(eid, str) or not eid:
                raise ValueError()
        except Exception:
            raise CommandFailed("forbidden", "Signed delegation approval audit unavailable") from None

    def delegation_command(self, parent: str, action: str) -> dict:
        require_principal("grant or revoke delegated approval authority")
        if not parent or action not in ("status", "grant", "revoke"):
            raise CommandFailed("bad_request", "Select a parent session and use status, grant or revoke")
        with self._delegation_lock:
            if action == "revoke":
                previous = self.delegation_grants.pop(parent, None)
                self.delegation_changed.set()
                self._delegation_log({"phase": "delegation_revoke", "parent": parent, "generation": previous}, parent)
            elif action == "grant":
                if not self.delegation_parent_allowed(parent):
                    raise CommandFailed("forbidden", "Grant applies to a main conversation, not a delegated worker")
                generation = secrets.token_hex(16)
                self._delegation_log({"phase": "delegation_grant", "parent": parent, "generation": generation}, parent)
                self.delegation_grants[parent] = generation
                self.delegation_changed.set()
            return {"supports_delegated_approval": True, "parent": parent, "enabled": parent in self.delegation_grants,
                    "generation": self.delegation_grants.get(parent), "ephemeral": True}

    def _delegation_chain(self, parent: str, child: str | None) -> list[str]:
        edges = {t["child_session"]: t for t in self.delegation_edges()
                 if t.get("child_session") and t.get("parent") and t.get("status") in ("queued", "running")}
        chain, visited = [], set()
        while child and child != parent and child in edges and child not in visited:
            visited.add(child)
            task = edges[child]
            chain.append(task["task_id"])
            child = task["parent"]
        return chain if child == parent and chain else []

    def _delegation_valid(self, pending: Pending) -> bool:
        try:
            return (pending.delegation_eligible and pending.delegation_binding == self._delegation_digest(pending)
                    and self.delegation_validate(pending.delegation_context) is True)
        except Exception:
            return False

    def delegated_listing(self, parent: str, generation: str) -> list[dict]:
        from exo.core.models import keys
        with self._delegation_lock:
            if not generation or self.delegation_grants.get(parent) != generation:
                raise CommandFailed("forbidden", "Delegated approval grant is absent or changed")
            try:
                values = list(keys.known_secret_values())
                def scrub(value):
                    if isinstance(value, str):
                        return keys.redact_secrets(value, values=values)
                    if isinstance(value, list):
                        return [scrub(item) for item in value]
                    if isinstance(value, dict):
                        out = {}
                        for key, item in value.items():
                            base = safe = scrub(str(key))
                            index = 2
                            while safe in out:
                                safe = f"{base} [field {index}]"
                                index += 1
                            out[safe] = scrub(item)
                        return out
                    return value
                return [{"request_id": rid, "binding": p.delegation_binding, "child": p.session,
                         "summary": "Review delegated tool call",
                         "call": json.dumps(scrub(s.to_dict(p.request.call)), sort_keys=True),
                         "tasks": self._delegation_chain(parent, p.session)}
                        for rid, p in self.open.items() if not p.future.done() and self._delegation_valid(p)
                        and self._delegation_chain(parent, p.session)]
            except Exception:
                raise CommandFailed("forbidden", "Delegated approval details unavailable") from None

    def respond_delegated(self, parent: str, generation: str, request_id: str, binding: str,
                          decision: str, actor: dict) -> None:
        with self._delegation_lock:
            if decision not in ("allow", "deny") or not actor.get("turn_id") or not actor.get("call_id"):
                raise CommandFailed("forbidden", "Delegated decisions require an active tool call")
            eligible = {r["request_id"]: r for r in self.delegated_listing(parent, generation)}
            pending = self.open.get(request_id)
            if request_id not in eligible or pending is None or binding != pending.delegation_binding or binding != self._delegation_digest(pending):
                raise CommandFailed("stale", "Delegated approval is no longer eligible")
            self._delegation_log({"phase": "delegation_decision", "state": "attempt", "parent": parent, "generation": generation,
                                  "request_id": request_id, "binding": binding, "child": pending.session,
                                  "tasks": eligible[request_id]["tasks"], "original_call_id": pending.request.call.id,
                                  "tool": pending.request.call.name, "decision": decision, "actor": actor}, parent, pending.delegation_context)
            # Synchronous audit and resolution share the event-loop/lock domain with revoke and human answers.
            if (self.delegation_grants.get(parent) != generation or pending.future.done()
                    or self.open.get(request_id) is not pending or self._delegation_chain(parent, pending.session) != eligible[request_id]["tasks"]
                    or binding != self._delegation_digest(pending)):
                raise CommandFailed("stale", "Delegated approval changed during audit")
            pending.decider = "delegate:" + parent
            pending.future.set_result(decision)
            self.announce_resolved(request_id, decision, pending.decider, pending.session)
            self.delegation_changed.set()

    def announce_resolved(self, rid: str, decision: str, by: str, session: str | None) -> None:
        """Tell every client an approval is closed, once per request."""
        if rid not in self._announced:
            self._announced.add(rid)
            self.publish(s.ApprovalResolved(request_id=rid, decision=decision, by=by), session)

    def listing(self, session: str | None = None) -> list[dict[str, Any]]:
        """What ``approvals.list`` shows: requests still waiting, optionally of one session."""
        from exo.core.types import to_dict
        return [{**to_dict(p.request), "session": p.session} for p in self.open.values()
                if not p.future.done() and not (session and p.session != session)]

    def respond(self, b: s.ApprovalRespond) -> None:
        with self._delegation_lock:
            self._respond(b)

    def _respond(self, b: s.ApprovalRespond) -> None:
        """D-039: who answered comes from the connection, never from the body. A principal connection answers as
        the principal (``client`` is only a display label). Any other connection answers only as a seat, with a
        D-033 signature, and never with ``allow_always``."""
        if (b.client or "").lower().startswith(("peer", "ingress")):  # D-026: input from outside is never consent
            raise CommandFailed("forbidden", "a peer cannot answer an approval")
        pending = self.open.get(b.request_id)
        if pending is None or pending.future.done():
            raise CommandFailed("not_found", f"no open approval {b.request_id}")
        approvers, binding = pending.request.approvers, pending.request.binding
        caller = current_caller()
        if is_principal(caller):
            ident = by = principal_as(caller.partition(":")[2] or b.client)
        elif (b.client or "").strip().lower().startswith("seat:"):
            ident = by = approver_identity(b.client)
        else:
            raise CommandFailed("forbidden", "this connection cannot answer approvals: answer in the Exo window or "
                                             "the web page, or sign as a seat" + demoted())
        if not approver_allowed(ident, approvers):  # D-033: who may answer, by category
            raise CommandFailed("forbidden", f"{approver_identity(ident)} may not answer this approval; "
                                             f"it needs {' or '.join(approvers)}")
        if b.binding and binding and b.binding != binding:
            raise CommandFailed("stale", "the text changed after it was shown; read the new request and answer that")
        if not is_principal(caller):  # D-033: a seat signs its answer; a seat never gives a standing grant
            if not verify_approval(b.sig, b.request_id, b.decision, binding, ident[5:]):
                raise CommandFailed("forbidden", f"{ident} must sign its answer with its seat key "
                                                 "(request id, decision, binding)")
            if b.decision == "allow_always":
                raise CommandFailed("forbidden", "only the principal can give a standing grant; "
                                                 "a seat approves one exact text with allow")
        pending.decider = by
        pending.future.set_result(b.decision)
        self.announce_resolved(b.request_id, b.decision, by, pending.session)
