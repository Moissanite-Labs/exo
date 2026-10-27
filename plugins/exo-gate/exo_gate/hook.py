"""The exo-gate hook: classify, decide, record, then let the core run, ask or block.

Core contract (exo.core.engine, same names as Hermes): a ``pre_tool_call`` callback returns
``{"action": "block", "message": ...}`` to veto a call (the message becomes the tool result),
``{"action": "approve", "message": ..., "rule_key": ...}`` to ask the principal, or ``None`` to
let it run. Asking is the protocol event ``approval.requested``; any client answers with
``approval.respond``, and a call nobody answers never runs. ``post_approval_response`` reports
the choice (once, always, deny).

Risk: the core may pass ``risk`` (the tool registry's hint). When it does not, the gate looks the
tool up itself. A tool the classifier does not know and the registry calls high risk is an ask,
never an allow.

Decision model (D-015), on by default (``decision_model``, or ``EXO_GATE_DECISION_MODEL=0`` to turn it off): the async hook
``apre_tool_call`` asks the router's ``decision`` role ``ask_choice_result(router, state, question,
["allow", "log", "ask", "deny"], spare="main")`` after the rules ran, takes the top option and keeps the
stricter of the two with ``tighten_only(rule_outcome, verdict)``. A model never loosens an outcome, is never
asked about a call the rules already deny, and a failure or timeout keeps the rule outcome. ``spare="main"``
(D-081): the call uses the decision and aux routes only, never a main route, and a failure cools only the
model that failed, so a gate call never slows a tool call by a main-model turn or locks the main role out.
The core awaits async hooks, so the gate registers ``apre_tool_call``; the sync ``pre_tool_call`` is the
rules alone.

Settings resolve in this order, first hit wins: explicit overrides, a value set in config (Exo's
plugin config, or Hermes ``plugins.entries.exo-gate.settings.<key>``), ``EXO_GATE_<KEY>`` env vars,
defaults (the same values as the manifest). Under Exo the gate reads config through
``ctx.get_explicit_config`` so a manifest default never hides an env var (issue #19). Settings are
read once, when the plugin loads, from exod's own environment. A tool subprocess cannot change that
environment, so ``EXO_GATE_MODE=observe`` set inside a tool call has no effect on the running gate.
Nothing here names a principal: without a store the gate runs on the default policies and only logs.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import fnmatch
import glob as _glob
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from exo import gate
from exo.core.private import atomic_write
from exo.principal import current_caller, is_principal, require_principal
from exo.store import principal_root

from . import permissions as perm

logger = logging.getLogger("exo_gate")

PLUGIN_ID = "exo-gate"
DEFAULTS: Dict[str, Any] = {
    "principal_store": "",
    "author": "exo-gate",
    "key_path": "",
    "mode": "enforce",          # enforce | observe
    "read_body_files": True,
    "decision_model": True,     # ask the decision role to tighten (never loosen) rule outcomes
}
_MAX_BODY_FILE = 256 * 1024
# Tools that cannot change anything; on an internal error these still run.
_SAFE_ON_ERROR = {k for k, v in gate.TOOLS.items() if v == "read"}
_RANK = {o: i for i, o in enumerate(gate.OUTCOMES)}  # allow < log < ask < deny


def _every_part(c: gate.Classification) -> list:
    """Each reading of the line (``gate.readings``) and every part it was built from, nested ones included, once."""
    seen: set[int] = set()
    out, todo = [], list(gate.readings(c))
    while todo:
        p = todo.pop(0)
        if id(p) not in seen:
            seen.add(id(p))
            out.append(p)
            todo += p.parts
    return out


def _body_path(f: str) -> Optional[Path]:
    """``f`` when the hook may read it as a body file: an absolute path to a regular file of at most 256 KiB. /dev and
    /proc name what a process holds (/dev/stdin, /proc/self/fd/3, /proc/self/environ): read here they give exod's own,
    not what gh will read, so they stay unseen (#208)."""
    try:
        p = Path(f)
        if any(str(y).startswith(("/dev/", "/proc/")) or str(y) in ("/dev", "/proc")
               for y in (p, Path(os.path.realpath(p)))):
            return None
        return p if p.is_absolute() and p.is_file() and p.stat().st_size <= _MAX_BODY_FILE else None
    except (OSError, ValueError):                       # ValueError: a NUL in the path, which gh never opens
        return None


@dataclass
class Settings:
    principal_store: Optional[Path]
    author: str = "exo-gate"
    key_path: Optional[Path] = None
    mode: str = "enforce"
    read_body_files: bool = True
    decision_model: bool = True
    grants_path: Optional[Path] = None


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def resolve_settings(overrides: Mapping[str, Any] | None = None,
                     get_config: Callable[[str, Any], Any] | None = None,
                     env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    overrides = dict(overrides or {})

    def get(key: str) -> Any:
        if key in overrides and overrides[key] not in (None, ""):
            return overrides[key]
        if get_config is not None:
            try:
                v = get_config(key, None)
            except Exception:
                v = None
            if v not in (None, ""):
                return v
        v = env.get("EXO_GATE_" + key.upper())
        if v not in (None, ""):
            return _truthy(v) if isinstance(DEFAULTS[key], bool) else v
        return DEFAULTS[key]

    exo_home = Path(env.get("EXO_HOME") or Path.home() / ".exo").expanduser()
    store = get("principal_store") or env.get("EXO_PRINCIPAL_STORE") or ""
    if not store and env.get("EXO_PRINCIPAL"):
        try:  # config principals.<id> first: a store made with --root
            store = str(principal_root(env["EXO_PRINCIPAL"], exo_home))
        except ValueError:  # not a principal id: no store, so the default policies apply
            store = ""
    author = str(get("author"))
    seat = env.get("EXO_SEAT") or author  # the seat's own signing key, the same file the bus client uses
    key_path = get("key_path") or str(exo_home / "keys" / f"{seat}.key")
    mode = str(get("mode")).strip().lower()
    # Standing "always" grants are the principal's consent, so they live in the principal store
    # beside the policies and ledger. Without a store they live in exod's home.
    grants = (Path(store).expanduser() / "state" / "approval-grants.json" if store
              else exo_home / "state" / "gate-grants.json")
    return Settings(
        principal_store=Path(store).expanduser() if store else None,
        author=author,
        key_path=Path(key_path).expanduser(),
        mode=mode if mode in ("enforce", "observe") else "enforce",
        read_body_files=bool(get("read_body_files")),
        decision_model=_truthy(get("decision_model")),
        grants_path=grants,
    )


class GrantStore:
    """Standing "always" grants on disk (D-033). One JSON list, mode 0600, written atomically.

    A grant has a scope (``rule`` = category, ``target`` = glob), an expiry and, for an outward act,
    the ``binding`` hash of the exact text it covers. Credentials and store writes never get a grant
    (``gate.NO_GRANT``). An outward grant without a binding never matches, so no outward category has an
    unscoped always."""

    def __init__(self, path: Optional[Path], clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()

    def _read(self) -> list:
        if not self.path or not self.path.is_file():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("exo-gate: unreadable grants file %s; ignoring it", self.path)
            return []
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def _write(self, rows: list) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(self.path, json.dumps(rows, indent=1, sort_keys=True))

    def match(self, category: str, target: str, binding: str, outward: bool) -> Optional[dict]:
        if category in gate.NO_GRANT:
            return None
        now = self.clock()
        with self._lock:
            rows = self._read()
        for g in rows:
            if g.get("rule") != category or float(g.get("expires") or 0) <= now:
                continue
            if outward and not g.get("binding"):
                continue
            if g.get("binding") and g.get("binding") != binding:
                continue
            if not fnmatch.fnmatchcase((target or "").lower(), str(g.get("target") or "").lower()):
                continue
            return g
        return None

    def list(self) -> list:
        now = self.clock()
        with self._lock:
            return [g for g in self._read() if float(g.get("expires") or 0) > now]

    def revoke(self, gid: str) -> Optional[dict]:
        with self._lock:
            rows = self._read()
            hit = next((g for g in rows if g.get("id") == gid), None)
            if hit is not None:
                self._write([g for g in rows if g.get("id") != gid])
        return hit

    def add(self, category: str, target: str, binding: str, days: float, by: str, tool: str = "") -> Optional[dict]:
        g = self.prepare(category, target, binding, days, by, tool)
        if g is not None:
            self.persist(g)
        return g

    def prepare(self, category: str, target: str, binding: str, days: float, by: str, tool: str = "") -> Optional[dict]:
        """Build a grant candidate without activating or writing it."""
        if not self.path or category in gate.NO_GRANT or days <= 0:
            return None
        now = self.clock()
        return {"id": "g_" + secrets.token_hex(6), "rule": category, "target": _glob.escape(target or ""),
             "binding": binding or "", "tool": tool, "by": by, "created": now, "expires": now + days * 86400}

    def persist(self, grant: dict) -> None:
        """Activate one already-authorized candidate with the atomic file replacement."""
        now = self.clock()
        with self._lock:
            rows = [r for r in self._read() if float(r.get("expires") or 0) > now]  # drop expired ones
            rows.append(dict(grant))
            self._write(rows)


class LedgerSink:
    """Appends `action` events to the principal's ledger. A no-op (plain logging) when no store
    or no signing key is configured. The required gate checks the returned event id
    and refuses execution when opening or committing the audit fails."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._ledger = None
        self._tried = False
        self._lock = threading.Lock()

    def _open(self):
        if self._tried:
            return self._ledger
        self._tried = True
        s = self.settings
        if not s.principal_store or not s.principal_store.is_dir():
            logger.info("exo-gate: no principal store; decisions are logged only")
            return None
        try:
            from exo.ledger_authority.factory import open_writable_ledger
            self._ledger = open_writable_ledger(s.principal_store / "ledger.sqlite", s.author,
                                                s.key_path, writer="exo-gate", auto_keygen=True)
        except Exception as exc:  # pragma: no cover - depends on the host
            logger.warning("exo-gate: cannot open ledger or key at %s: %s", s.key_path, exc)
            self._ledger = None
        return self._ledger

    def append(self, body: Mapping, session: str | None = None, refs: tuple = ()) -> Optional[str]:
        logger.info("exo-gate %s", json.dumps(body, sort_keys=True, default=str)[:2000])
        with self._lock:
            led = self._open()
            if led is None:
                return None
            try:
                ev = led.append("action", dict(body), self.settings.author, session=session or None, refs=refs)
                return ev["id"]
            except Exception as exc:
                logger.warning("exo-gate: ledger append failed: %s", exc)
                return None


class GateAuditError(RuntimeError):
    """Required gate evidence was not committed; do not expose host error details."""


@dataclass
class PendingApproval:
    decision_id: Optional[str]
    session: Optional[str]
    scope: dict
    rule_key: str
    call_id: str


class Gate:
    """Holds settings, cached policies and the ledger sink; exposes the two hook callbacks."""

    def __init__(self, settings: Settings, sink: LedgerSink | None = None,
                 risk_lookup: Callable[[str], Optional[str]] | None = None,
                 router_getter: Callable[[], Any] | None = None,
                 required_getter: Callable[[], bool] | None = None):
        self.settings = settings
        self.router_getter = router_getter
        self.risk_lookup = risk_lookup
        self._required_at_load = (os.environ.get("EXO_TOOL_EXECUTOR_REQUIRED") == "1"
                                 or bool(required_getter and required_getter()))
        self.required_getter = required_getter
        self.sink = sink if sink is not None else LedgerSink(settings)
        self._policies: Optional[dict] = None      # pinned: the policy in force for this session
        self._policy_stamp: tuple = ()
        self._policy_error = ""
        self._floor: Optional[dict] = None         # #557: the defaults, decided beside a repaired escalate
        self._changed: Optional[dict] = None       # D-038: files changed since; waits for /gate accept
        self._changed_stamp: tuple = ()
        self._changed_error = ""
        self._changed_floor: Optional[dict] = None
        self._pending: Dict[str, PendingApproval] = {}
        self._spent: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.grants = GrantStore(settings.grants_path)
        self._permission_lock = threading.RLock()
        self.permissions = perm.Permissions()  # D-065: ask (default) or bypass, for this run of the core only

    @property
    def executor_required(self) -> bool:
        return self._required_at_load or bool(self.required_getter and self.required_getter())

    # --- permission mode (D-065) ---------------------------------------------------------

    @property
    def bypass_on(self) -> bool:
        """Bypass is in force: switched on and not under a required executor, whatever the order they came in."""
        return self.permissions.mode == perm.BYPASS and not self.executor_required

    def set_permissions(self, mode: str, via: str = "/permissions", session: str = "") -> None:
        """Serialize switches and full-call audits. Any caller may revoke full, even if audit storage fails."""
        if mode not in (perm.ASK, perm.BYPASS, perm.FULL):
            raise ValueError(mode)
        if mode in (perm.BYPASS, perm.FULL):
            by = require_principal("turn on permissions " + mode)
            if self.executor_required:
                from exo.server.engine import CommandFailed
                raise CommandFailed("forbidden", perm.FULL_REQUIRED_REFUSAL if mode == perm.FULL else perm.REQUIRED_REFUSAL)
            if mode == perm.FULL and session == "all":
                session = "*"
            if mode == perm.FULL and (not isinstance(session, str) or not session or len(session) > 256
                                      or any(c.isspace() for c in session)):
                raise ValueError("full requires one session ID")
        else:
            by = current_caller()
        with self._permission_lock:
            previous = self.permissions.mode
            if mode == previous and (mode != perm.FULL or session == self.permissions.full_session):
                return
            row = {"gate": PLUGIN_ID, "phase": "permissions", "mode": mode, "previous": previous,
                   "by": by, "via": via, "auto_approved": self.permissions.auto_approved}
            if previous == perm.FULL:
                row["previous_session"] = self.permissions.full_session
            if mode == perm.FULL:
                row.update(full_session=session, scope="core-all-sessions" if session == "*" else "session-all-turns")
            if mode == perm.ASK and previous == perm.FULL:
                self.permissions.set(mode, by, via, time.time())
                try:
                    self._append(row)
                except Exception:
                    logger.warning("exo-gate: full revoked but its audit could not be saved")
                return
            try:
                eid = self._append(row)
            except Exception:
                if mode == perm.FULL:
                    raise GateAuditError("Full permission mode audit could not be saved") from None
                raise
            if mode == perm.FULL and (not isinstance(eid, str) or not eid):
                raise GateAuditError("Full permission mode audit could not be saved")
            self.permissions.set(mode, by, via, time.time(), session)

    def _full(self, tool: str, args: dict, session: str, call_id: str, turn_id: str,
              author: str) -> tuple[bool, Optional[dict]]:
        """A full allow linearizes at its committed audit; revocation waits for that operation to finish."""
        with self._permission_lock:
            if self.permissions.mode != perm.FULL or not session or (self.permissions.full_session != "*"
                    and session != self.permissions.full_session):
                return False, None
            if self.executor_required:
                return True, {"action": "block", "message": "Full permissions are unavailable with a required executor"}
            try:
                call, digest = perm.full_recorded_call(tool, args)
                eid = self._append({"gate": PLUGIN_ID, "phase": "decision", "mode": perm.FULL,
                                    "outcome": "allow", "decided_by": perm.FULL, "call": call,
                                    "args_sha256": digest, "tool_call_id": call_id or None,
                                    "turn_id": turn_id or None, "turn_author": author,
                                    "full_session": self.permissions.full_session, "session_id": session,
                                    "scope": "core-all-sessions" if self.permissions.full_session == "*"
                                    else "session-all-turns"}, session=session)
                if not isinstance(eid, str) or not eid:
                    raise GateAuditError("Full permission audit could not be saved")
            except Exception:
                return True, {"action": "block", "message": "Full permission audit could not be saved; call blocked"}
            self.permissions.full_auto_approved += 1
            return True, None

    def start_from_seat(self) -> None:
        """The seat file's ``[permissions] mode = "bypass"``, applied once when the gate loads."""
        if self.executor_required:
            logger.warning("exo-gate: the seat asks for permissions bypass; %s", perm.REQUIRED_REFUSAL)
            return
        try:
            self._append({"gate": PLUGIN_ID, "phase": "permissions", "mode": perm.BYPASS, "previous": perm.ASK,
                          "by": "seat file", "via": "seat default"})
        except Exception:
            logger.warning("exo-gate: could not record the seat's bypass default; staying in ask", exc_info=True)
            return
        self.permissions.set(perm.BYPASS, "seat file", "seat default", time.time())

    def permissions_snapshot(self) -> dict:
        with self._permission_lock:
            snap = self.permissions.snapshot(available=not self.executor_required)
            if self.executor_required:
                snap["mode"] = perm.ASK
            return snap

    def _bypass(self, c: gate.Classification, d: gate.Decision, author: str = "") -> Optional[str]:
        """What bypass does with an ask in a turn the principal started: ``allow``, ``hold`` (a post that breaks
        the posting rules goes back to the agent with the fixes) or ``floor`` (store writes and secrets still ask).
        None when bypass is off, or the turn was started by anyone else (#598): a peer, bus wake, job notice or
        local caller keeps its card, as when bypass was off."""
        if d.outcome != "ask" or not self.bypass_on or not is_principal(author):
            return None
        if c.category in perm.FLOOR:
            return "floor"
        return "hold" if d.violations else "allow"

    def _append(self, body: Mapping, **kwargs: Any) -> Optional[str]:
        try:
            eid = self.sink.append(body, **kwargs)
        except Exception:
            if self.executor_required:
                raise GateAuditError("Required gate audit could not be saved") from None
            raise
        if self.executor_required and (not isinstance(eid, str) or not eid):
            raise GateAuditError("Required gate audit could not be saved")
        return eid

    # --- policies ---------------------------------------------------------------------

    def _stamp(self) -> tuple:
        root = self.settings.principal_store
        if not root:
            return ()
        paths = gate.policy_files(root) + [root / "agents"]
        return tuple((str(p), p.stat().st_mtime_ns if p.exists() else 0) for p in paths)

    def _scrub_stamp(self) -> tuple:
        """Seat files and the bus registry feed the scrub names; reload when they change."""
        paths = gate.scrub_sources(self.settings.principal_store)
        return tuple((str(p), p.stat().st_mtime_ns if p.exists() else 0) for p in paths)

    def _load(self) -> tuple[dict, Optional[dict], str]:
        """The policy, the defaults to decide beside it (or None) and the policy error."""
        errors: list[str] = []
        try:
            pol = gate.load_policies(self.settings.principal_store, errors)
        except (gate.PolicyError, OSError) as exc:
            # Fall back to the defaults, which ask on anything irreversible or public.
            logger.warning("exo-gate: policy error, using defaults: %s", exc)
            return gate.normalize_policies({}), None, str(exc)
        if not errors:
            return pol, None, ""
        # #557: a malformed escalate keeps the principal's other sections; the stricter of them and the defaults applies
        logger.warning("exo-gate: policy error, the stricter of the policy and the defaults applies: %s",
                       "; ".join(errors))
        return pol, gate.normalize_policies({}), "; ".join(errors)

    def policies(self) -> dict:
        """The pinned policy: what was on disk when this gate first looked (D-038). A file changed later is
        loaded by ``changed_policies`` and can only make a decision stricter until the principal accepts it."""
        if self._policies is None:
            self._policy_stamp = self._stamp() + self._scrub_stamp()
            self._policies, self._floor, self._policy_error = self._load()
        return self._policies

    def changed_policies(self) -> Optional[dict]:
        """The policy on disk when it differs from the pinned one, else None. The first sight of each
        change writes a ``policy_change`` row with the files' hashes."""
        self.policies()
        stamp = self._stamp() + self._scrub_stamp()
        if stamp == self._policy_stamp:
            self._changed, self._changed_floor, self._changed_stamp = None, None, ()
            return None
        if stamp != self._changed_stamp:
            self._changed, self._changed_floor, self._changed_error = self._load()
            self._changed_stamp = stamp
            self._append({"gate": PLUGIN_ID, "phase": "policy_change", "files": self._file_hashes(),
                              "error": self._changed_error or None})
        return self._changed

    def _file_hashes(self) -> dict:
        root = self.settings.principal_store
        out = {}
        for p in (gate.policy_files(root) if root else []):
            try:
                out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            except OSError:
                out[p.name] = None
        return out

    def accept_policies(self) -> str:
        """Adopt changed policy files only with live principal authority (``/gate accept``)."""
        by = require_principal("accept changed gate policies")
        candidate = self.changed_policies()
        if candidate is None:
            return "policy unchanged since the session started"
        stamp, floor, error = self._changed_stamp, self._changed_floor, self._changed_error
        self._append({"gate": PLUGIN_ID, "phase": "policy_accept", "files": self._file_hashes(), "by": by,
                      "policy_sha256": hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()})
        self._policies, self._floor, self._policy_error = candidate, floor, error
        self._policy_stamp, self._changed, self._changed_floor, self._changed_stamp = stamp, None, None, ()
        return "accepted the changed policy files"

    # --- hooks ------------------------------------------------------------------------

    def _load_body_file(self, c: gate.Classification) -> list[str]:
        """Read a post's body file into its text when the hook can be sure it reads the file the command will:
        an absolute path to a regular file of at most 256 KiB, on a line where the gate finds no other part. A
        relative or `~` path names a file in the command's folder or home, which the hook does not know. Anything not
        read stays unseen, so the post asks and gets no standing grant (#208). A required executor reads none:
        tool-selected host paths, symlinks included, are never read before the worker, and `_rules` refuses the call.
        The copies of the post on its other targets get the text too (`gate.fill_body`), and each other reading of the
        line (`gate.readings`: as read before #399 or #311) has its own body file read the same way, as the hook read
        it before.

        Another part in any reading (a command, a redirection to a file, an assignment that changes what runs, a
        wrapper the gate does not see through, an ssh hop, the code around a call) may change the file after this
        read and before gh reads it, or give gh another file by that path. So on such a line the file stays unread
        and the post asks once (#343). Any part counts, a store read from the tool's folder (#311) and a sudo write
        that displaced a part (#454) among them. A copy of the post on another target is the post itself, not another
        part. Returns the files left unread for that reason alone, of every post on the line, so the card can say why
        (`gate.UNSEEN_ON_LINE`): alone on its line, each would be read."""
        if not self.settings.read_body_files or self.executor_required:
            return []
        alone = all(p.alt_copy for x in gate.readings(c) for p in x.parts)
        held: list[str] = []
        for x in gate.readings(c) if alone else _every_part(c):
            if not x.text_file or x.text or (p := _body_path(x.text_file)) is None:
                continue
            if not alone:
                held.append(x.text_file)
                continue
            try:
                gate.fill_body(x, p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not x.link:
                x.link = str(p)
        return list(dict.fromkeys(held))

    def _rule_key(self, tool: str, args: Mapping, c: gate.Classification, d: gate.Decision) -> str:
        # An "always" answer must not cover a different public or irreversible act, or different code the
        # gate cannot see into, so those keys are bound to the exact arguments. Others: category and target.
        if gate.binds_exact(c, d) or d.violations:
            return f"exo-gate:{c.category}:{gate.approval_binding(c, args)[:16]}"  # D-033: text-bound
        return f"exo-gate:{c.category}:{c.target or tool}"

    def _risk(self, tool: str, given: Any) -> str:
        if given in ("low", "medium", "high"):
            return str(given)
        try:
            return (self.risk_lookup(tool) if self.risk_lookup else None) or ""
        except Exception:
            return ""

    async def _verdict(self, d: gate.Decision, c: gate.Classification, risk: str, tool: str) -> Optional[tuple]:
        """``(top option, {"route", "source"})`` from the decision role, or None (no router, no route, failure).
        The route is the one that answered: the first route (Jev) or a fallback (Laya, aux; never main, D-081)."""
        router = self.router_getter() if self.router_getter else None
        if router is None:
            return None
        try:
            from exo.core.models.decision import ask_choice_result
            state = {"tool": tool, "classification": c.as_dict(), "risk": risk, "rule_outcome": d.outcome,
                     "reason": d.reason}
            res = await asyncio.wait_for(
                ask_choice_result(router, state, "How strictly should this tool call be gated?",
                                  list(gate.OUTCOMES), spare="main"), timeout=20)
        except Exception as exc:
            from exo.core.models.decision import DecisionError
            from exo.model_spend import SpendDenied
            if isinstance(exc, SpendDenied):
                raise
            if isinstance(exc, DecisionError):  # no decision or aux route (often), or each one failed
                logger.info("exo-gate: no decision verdict (%s); keeping the rule outcome", exc)
            else:
                logger.warning("exo-gate: decision model failed; keeping the rule outcome", exc_info=True)
            return None
        probs = res.probabilities
        return (max(probs, key=probs.get), {"route": res.route, "source": res.source}) if probs else None

    @staticmethod
    def _tighten(d: gate.Decision, verdict: Optional[str]) -> None:
        """Keep the stricter of the rule outcome and the model's verdict (D-015)."""
        if not verdict:
            return
        from exo.core.models.decision import tighten_only
        out = tighten_only(d.outcome, verdict)
        if out != d.outcome:
            d.reason += f" (decision model tightened {d.outcome} to {out})"
            d.outcome = out

    async def apre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "", task_id: str = "",
                             tool_call_id: str = "", turn_id: str = "", author: str = "", risk: Any = None,
                             cwd: str = "", **_: Any) -> Optional[dict]:
        """The registered hook: the rules, plus the decision model when ``decision_model`` is on. ``cwd`` is the
        session's working folder, from which the tool reads relative paths (#209). ``author`` is who started the
        turn (D-040); bypass lowers only in the principal's (#598)."""
        args = args if isinstance(args, dict) else {}
        handled, result = self._full(tool_name, args, session_id, tool_call_id, turn_id, author)
        if handled:
            return result
        verdict = None
        risk_s = self._risk(tool_name, risk)
        if self.settings.decision_model:
            try:
                c, d = self._rules(tool_name, args, risk_s, cwd)
                if d.outcome != "deny":
                    verdict = await self._verdict(d, c, risk_s, tool_name)  # (option, via) or None
            except Exception as exc:
                from exo.model_spend import SpendDenied
                if isinstance(exc, SpendDenied):
                    return {"action": "block", "message": str(exc)}
                logger.exception("exo-gate: rules failed before the decision model on %s", tool_name)
                if self.executor_required:
                    return {"action": "block", "message": "Required gate checks failed"}
        return self.pre_tool_call(tool_name, args, session_id, task_id, tool_call_id, turn_id, risk,
                                  _verdict=verdict, cwd=cwd, author=author)

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "", task_id: str = "",
                      tool_call_id: str = "", turn_id: str = "", risk: Any = None, _verdict: Any = None,
                      cwd: str = "", author: str = "", **_: Any) -> Optional[dict]:
        args = args if isinstance(args, dict) else {}
        handled, result = self._full(tool_name, args, session_id, tool_call_id, turn_id, author)
        if handled:
            return result
        try:
            return self._pre_tool_call(tool_name, args, session_id, tool_call_id, self._risk(tool_name, risk),
                                       _verdict, cwd, author)
        except Exception as exc:
            logger.exception("exo-gate: internal error on %s", tool_name)
            if self.executor_required:
                return {"action": "block", "message": "Required gate checks or audit failed"}
            secret = store = False
            try:
                secret = bool(gate.args_sensitive(args))
            except Exception:
                secret = True
            try:        # the call read as a store write or secret, when only the decision failed (a bad policy regex)
                action = {"tool": tool_name, "args": args}
                c = gate.classify(action, cwd=cwd) if cwd else gate.classify(action)
                store = gate.no_grant(c)    # a part and each reading count too (#311, #399)
            except Exception:
                pass
            if (tool_name in _SAFE_ON_ERROR or self.settings.mode == "observe") and not (secret or store):
                return None
            card = {"action": "approve", "message": f"exo-gate could not classify {tool_name} ({exc}). "
                                                    "Approve only if you expected this action. Default if no reply: skip.",
                    "rule_key": f"exo-gate:error:{tool_name}"}
            if store:   # a store write or secret the gate read: the principal answers, once, in every mode (D-033)
                card.update(approvers=["principal"], once=True)
            return card

    def _rules(self, tool: str, args: dict, risk: str, cwd: str = "", c: Optional[gate.Classification] = None,
               floors: bool = True, only: Optional[bool] = None) -> tuple[gate.Classification, gate.Decision]:
        """Classification and the rule outcome (policies, then the registry risk floor). With ``floors`` off, the
        outcome as before #557: the defaults alone in place of a policy with a malformed escalate. With ``only``, one
        policy alone decides, the pinned one (False) or the changed one (True), so the #586 grant check can ask each
        side's own decision. A given ``c`` is a line already read here: its body files are not read again, and none
        counts as held (#343), since a held file leaves its post unseen and such a line never gets that far
        (``_pre_tool_call``)."""
        held: list[str] = []
        if c is None:
            action = {"tool": tool, "args": args}
            c = gate.classify(action, cwd=cwd) if cwd else gate.classify(action)
            held = self._load_body_file(c)
        today = _dt.date.today().isoformat()
        ctx = {"spent_today": self._spent.get(today, 0.0), "held_body_files": held}
        pol, changed = self.policies(), self.changed_policies()
        floor, changed_floor = self._floor, self._changed_floor
        if only is not None:
            pol, floor = (changed, self._changed_floor) if only else (pol, floor)
            changed, changed_floor = None, None
        if not floors:
            pol, floor = pol if floor is None else floor, None
            changed, changed_floor = changed if changed_floor is None else changed_floor, None
        d = _decide(c, pol, floor, ctx)
        if floor is not None:
            d.reason += f" (policy error, stricter of your policy and the defaults applies: {self._policy_error[:120]})"
        elif self._policy_error:
            d.reason += f" (policy error, defaults used: {self._policy_error[:120]})"
        if changed is not None:
            # D-038: a policy changed mid-session only tightens. Loosening waits for /gate accept. Old and new merge
            # as one stricter reading (#586), as they do beside a policy error (#557). The #557 grant check keeps the
            # decision as before #557: the higher outcome of the two sides, so its grant applies where that asks too.
            d2 = _decide(c, changed, changed_floor, ctx)
            if floors:
                d = _merge(d, d2)   # either may already be a merge; the other's violations stand at a tie
            else:
                if _RANK[d2.outcome] > _RANK[d.outcome]:
                    d = d2
                # A part reserved under either policy keeps its ask from a standing grant (D-075), and so does a sudo
                # write line whose displaced part asks under either (#454).
                d.reserved = list(dict.fromkeys(d.reserved + d2.reserved))
                d.displaced_asks = d.displaced_asks or d2.displaced_asks
            d.reason += " (policy files changed this session; the stricter of old and new applies until /gate accept)"
        if risk == "high" and c.category == "unknown" and _RANK[d.outcome] < _RANK["ask"]:
            d.reason += f" (registry risk is high and {tool} is not a known tool)"
            d.outcome = "ask"
        # #454: a sudo write line carries the body file of the part it displaced there (`sudo gh ... -F /tmp/b > f`)
        if self.executor_required and d.outcome != "deny" and any(
                x.text_file or (x.displaced is not None and x.displaced.text_file) or gate.body_unseen(x)
                for x in gate.readings(c)):
            # The gate cannot approve contents it has not seen (#208): a body file, stdin, @file, an expansion.
            # In any reading of the line (#399, #311): `gh pr "$V" 5 -F f` has its body file only as read before.
            d.outcome, d.rule = "deny", "required"
            d.reason = ("a required executor runs only a post whose body the gate read on the line; "
                        "pass the text inline")
        return c, d

    def _pre_tool_call(self, tool: str, args: dict, session_id: str, tool_call_id: str,
                       risk: str = "", verdict: Any = None, cwd: str = "", author: str = "") -> Optional[dict]:
        verdict, via = verdict if isinstance(verdict, tuple) else (verdict, None)
        c, d = self._rules(tool, args, risk, cwd)
        pol = self.policies()
        today = _dt.date.today().isoformat()
        if d.outcome != "deny":
            self._tighten(d, verdict)
        outward = gate.binds_exact(c, d)
        binding = gate.approval_binding(c, args) if outward else ""
        # No standing allow for credentials and store writes (a part of the line counts, #311), nor for a post whose
        # body the gate did not see: its binding hashes only the line, so a grant would cover any later body (#208).
        # Each reading counts (gate.readings: as read before #311 or #399).
        once = gate.no_grant(c) or gate.body_unseen(c)
        grant = self._grant_for(c, d, binding, outward) if d.outcome == "ask" and not once else None
        if grant and (self._floor is not None or self._changed_floor is not None):
            # #557: while a policy error stands, a grant applies only where the decision as before (the defaults in
            # place of the policy with the error, then the model's verdict) asks and finds a grant too, so it never
            # takes the call below that decision: an allow where it logs, or where it asks and no grant covers it.
            m = self._rules(tool, args, risk, cwd, c=c, floors=False)[1]
            if m.outcome != "deny":
                self._tighten(m, verdict)
            m_out = gate.binds_exact(c, m)
            m_grant = (self._grant_for(c, m, gate.approval_binding(c, args) if m_out else "", m_out)
                       if m.outcome == "ask" else None)
            if m_grant is None:
                grant = None
        elif grant and self._changed is not None and not self._policy_error and not self._changed_error:
            # #586: with two valid files, a grant applies only where each side's own decision (the model's verdict
            # in) asks and finds one too, so the merged reading never runs a call one side logs (an unbound grant on
            # the changed file's reserved hit) or a side asks for without that binding (a bound grant on the line
            # only the changed file binds).
            for alone in (False, True):
                s = self._rules(tool, args, risk, cwd, c=c, only=alone)[1]
                if s.outcome != "deny":
                    self._tighten(s, verdict)
                s_out = gate.binds_exact(c, s)
                s_grant = (self._grant_for(c, s, gate.approval_binding(c, args) if s_out else "", s_out)
                           if s.outcome == "ask" else None)
                if s_grant is None:
                    grant = None
                    break
        if grant:
            d.outcome, d.rule = "allow", "grant"
            d.reason += f"; standing grant {grant['id']} (until {_iso(grant['expires'])})"
        bypass = self._bypass(c, d, author)
        if bypass == "allow":
            asked = {"would": "ask", "rule": d.rule, "reason": d.reason}
            d.outcome, d.rule = "allow", "bypass"
            d.reason += "; auto-approved: permissions bypass"
        elif bypass == "hold":
            d.outcome, d.rule = "deny", "bypass_hold"
        default = str(pol["gates"].get("ask_default") or "skip")
        body = {
            "gate": PLUGIN_ID, "phase": "decision", "tool": tool, "outcome": d.outcome,
            "classification": c.as_dict(), "decision": d.as_dict(), "mode": self.settings.mode, "risk": risk or None,
            "tool_call_id": tool_call_id or None,
            "args_sha256": hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        }
        if verdict:
            body["decision_model"] = {"verdict": verdict, **(via or {})}
        if grant:
            body["grant"] = grant["id"]
        if binding:
            body["binding"] = binding
        if bypass in ("allow", "hold"):  # D-065: the exact call, so the audit can be read without the session
            body["decided_by"] = "bypass"
            body["call"] = perm.recorded_call(tool, args)
            body["turn_author"] = author  # the turn the bypass served (#598)
            if bypass == "allow":
                body["bypass"] = asked
        elif bypass == "floor":
            body["bypass"] = {"floor": c.category}
        result: Optional[dict] = None
        if d.outcome == "deny":
            result = {"action": "block", "message": f"exo-gate blocked {c.summary or tool}: {d.reason}."}
            if bypass == "hold":
                result["message"] = (f"exo-gate held {c.summary or tool}: it breaks the posting rules. Fix: "
                                     + "; ".join(d.violations) + ". Permissions bypass does not ask about this; "
                                     "change the text and call again.")
        elif d.outcome == "ask":
            msg = gate.summarize(c, d, default)
            if d.violations:
                msg += " Fix: " + "; ".join(d.violations) + "."
            key = self._rule_key(tool, args, c, d)
            # D-038 (#211): a changed approvals.yaml can narrow who answers at once; a wider one waits for /gate accept.
            changed = self.changed_policies()
            changed_approvals = changed.get("approvals") if changed is not None else None
            # Only who may answer each reading of the line (#399) and each NO_GRANT part (#447, gate.call_approvers).
            approvers = gate.call_approvers(c, pol.get("approvals"),
                                            changed.get("approvals") if changed is not None else None)
            for floor in (self._floor, self._changed_floor):   # #557: no wider than the defaults' (the principal)
                if floor is not None:
                    allowed = gate.call_approvers(c, floor.get("approvals"))
                    approvers = [a for a in approvers if a in allowed]
            for x in gate.readings(c):
                if x.displaced is not None and (x is not c or x.displaced.target or d.displaced_asks):
                    # #454: a sudo write line is also about the repo or host its displaced part names, or asks with
                    # that part leading, so only who may answer that part's category too may answer it. The line as
                    # read before #399 keeps its displaced part's approvers always, as #399 keeps that reading's:
                    # `sudo gh pr "$V" 5 > /tmp/f` is a sudo write line only as read before.
                    also = set(gate.strictest_approvers(x.displaced.category, pol.get("approvals"), changed_approvals))
                    approvers = [a for a in approvers if a in also]
            # persist: this gate keeps "always" grants itself (scoped, expiring, in the store), so the
            # core must not also keep an unscoped one in memory.
            approval_key = "a_" + secrets.token_hex(16)
            result = {"action": "approve", "message": msg, "rule_key": key, "approval_key": approval_key,
                      "approvers": approvers,
                      "persist": True}
            if binding:
                result["binding"] = binding
            if once:
                result["once"] = True      # the core never stores an "always" for this (no standing allow)
            body["rule_key"] = key
            body["approval_key"] = approval_key
            body["approvers"] = approvers
        # NO_GRANT is enforced in every mode; a required executor never runs an unanswered ask.
        if self.settings.mode == "observe" and not gate.no_grant(c) and not self.executor_required:
            body["would"] = result["action"] if result else "run"
            result = None
        if d.outcome in ("allow", "log") and c.cost:
            with self._lock:
                self._spent[today] = self._spent.get(today, 0.0) + float(c.cost)
        eid = self._append(body, session=session_id)
        if bypass:
            with self._lock:
                key = {"allow": "auto_approved", "hold": "held", "floor": "floor_asks"}[bypass]
                setattr(self.permissions, key, getattr(self.permissions, key) + 1)
        if result and result["action"] == "approve":
            with self._lock:
                scope = {"rule": c.category, "target": c.target or tool, "binding": binding, "outward": outward,
                         "tool": tool, "once": once}
                self._pending[result["approval_key"]] = PendingApproval(
                    eid, session_id or None, scope, "plugin_rule:" + result["rule_key"], tool_call_id)
                if len(self._pending) > 500:
                    self._pending.pop(next(iter(self._pending)))
        return result

    def post_approval_response(self, pattern_key: str = "", choice: str = "", decided_by: str = "",
                               approval_key: str = "", session_id: str = "",
                               **_: Any) -> None:
        with self._lock:
            if not approval_key and not self.executor_required:
                # Legacy hosts may omit correlation; never guess between identical pending calls.
                matches = [key for key, value in self._pending.items() if value.rule_key == pattern_key]
                if len(matches) == 1:
                    approval_key = matches[0]
            pending = self._pending.get(approval_key)
            if pending is not None and (pending.rule_key != pattern_key
                                       or (session_id and pending.session != session_id)):
                pending = None
            if pending is not None:
                self._pending.pop(approval_key)
        if pending is None:
            if self.executor_required and pattern_key.startswith("plugin_rule:exo-gate:"):
                raise GateAuditError("Required gate approval evidence is unavailable")
            return None
        eid, session, scope = pending.decision_id, pending.session, pending.scope
        body = {"gate": PLUGIN_ID, "phase": "approval", "choice": str(choice),
                "approved": str(choice) in ("once", "session", "always"), "decided_by": str(decided_by or ""),
                "approval_key": approval_key, "tool_call_id": pending.call_id or None,
                "rule_key": pattern_key.split(":", 1)[-1]}
        aid = self._append(body, session=session, refs=(eid,) if eid else ())
        if str(choice) == "always":
            self._add_grant(scope, str(decided_by or ""), session, aid or eid)
        return None

    # --- standing grants (D-033) --------------------------------------------------------

    def _grant_for(self, c: gate.Classification, d: gate.Decision, binding: str, outward: bool) -> Optional[dict]:
        if gate.no_grant(c) or (d.violations and not binding):
            return None
        # D-075: a grant covers the act it was given for. A reserved entry that matched another part of
        # the line keeps the principal's ask, so `git push origin f && gh pr merge 3` is not run on a push grant.
        # #454: a sudo write line is also the act of the part its write displaced, which led it before, so a hit on
        # the write keeps the ask too, as it did then (`sudo echo hi > /tmp/f` under `reserved: [local_write]`).
        if any(hit != (x.category, x.target) for x in (c, c.displaced) if x is not None for hit in d.reserved):
            return None
        try:
            return self.grants.match(c.category, c.target or c.tool, binding, outward)
        except Exception:
            logger.warning("exo-gate: grant lookup failed; asking instead", exc_info=True)
            return None

    def _grant_days(self, outward: bool) -> float:
        """How long an "always" lasts: the pinned approvals.yaml, or the shorter of pinned and changed while a
        change waits for /gate accept, so 0 in either means no grant (D-038, #211)."""
        key = "outward_always_days" if outward else "always_days"
        days = float((self.policies().get("approvals") or {}).get(key) or 0)
        changed = self.changed_policies()
        if changed is not None:
            days = min(days, float((changed.get("approvals") or {}).get(key) or 0))
        for floor in (self._floor, self._changed_floor):    # #557: no longer than the defaults' either
            if floor is not None:
                days = min(days, float((floor.get("approvals") or {}).get(key) or 0))
        return days

    def _add_grant(self, scope: Mapping, by: str, session: Optional[str], ref: Optional[str]) -> None:
        if gate.approver_identity(by) != "principal":  # standing grants are the principal's alone
            logger.warning("exo-gate: %s answered always; no standing grant kept", by)
            return
        if scope.get("once"):  # asked with no "always" option (NO_GRANT, or a body the gate did not see)
            logger.warning("exo-gate: always answered on a once-only ask; no standing grant kept")
            return
        days = self._grant_days(bool(scope.get("outward")))
        args = (str(scope.get("rule") or ""), str(scope.get("target") or ""),
                str(scope.get("binding") or ""), days, by, str(scope.get("tool") or ""))
        if self.executor_required:
            candidate = self.grants.prepare(*args)
            if candidate is None:
                return
            body = {"gate": PLUGIN_ID, "phase": "grant", "state": "authorized", "activation": "pending",
                    "grant": candidate["id"], "rule": candidate["rule"], "target": candidate["target"],
                    "binding": candidate["binding"] or None, "by": by, "expires": _iso(candidate["expires"])}
            self._append(body, session=session, refs=(ref,) if ref else ())
            try:
                self.grants.persist(candidate)
            except Exception:
                raise GateAuditError("Required standing grant could not be saved") from None
            return
        try:
            g = self.grants.add(*args)
        except Exception:
            logger.warning("exo-gate: could not save the standing grant", exc_info=True)
            return
        if g is None:
            return
        body = {"gate": PLUGIN_ID, "phase": "grant", "grant": g["id"], "rule": g["rule"], "target": g["target"],
                "binding": g["binding"] or None, "by": by, "expires": _iso(g["expires"])}
        self._append(body, session=session, refs=(ref,) if ref else ())


def _decide(c: gate.Classification, pol: dict, floor: Optional[dict], ctx: dict) -> gate.Decision:
    """The decision under ``pol``, or with ``floor`` (the defaults beside a repaired escalate, #557) the stricter of
    the two, merged as a line's readings merge: the widest exposure (so an approval binds as either would bind it),
    every violation and every reserved part. At an equal outcome and exposure the decision with a violation the other
    lacks stands, so its reason names it."""
    d = gate.decide(c, pol, ctx)
    return d if floor is None else _merge(d, gate.decide(c, floor, ctx))


def _merge(d: gate.Decision, d2: gate.Decision) -> gate.Decision:
    """The stricter of two decisions of one line (#557); at an equal outcome and exposure ``d2`` stands only when it
    has a violation ``d`` lacks."""
    return gate.stricter([d2, d] if set(d2.violations) - set(d.violations) else [d, d2])


def grants_command(g: "Gate", args: str = "") -> str:
    """``/grants`` lists live standing grants; ``/grants revoke <id>`` removes one (ledger row ``revoke``)."""
    words = (args or "").split()
    if words[:1] == ["revoke"]:
        if len(words) != 2:
            return "usage: grants revoke <id>"
        hit = g.grants.revoke(words[1])
        if hit is None:
            return f"no grant {words[1]}"
        g._append({"gate": PLUGIN_ID, "phase": "revoke", "grant": hit["id"], "rule": hit.get("rule"),
                       "target": hit.get("target"), "binding": hit.get("binding") or None})
        return f"revoked {hit['id']}"
    if words and words[0] != "list":
        return "usage: grants [list] | grants revoke <id>"
    rows = g.grants.list()
    if not rows:
        return "no standing grants"
    out = []
    for r in rows:
        scope = f"{r.get('rule')} on {r.get('target') or '*'}"
        if r.get("binding"):
            scope += f", text {str(r['binding'])[:12]}"
        out.append(f"{r['id']}  {scope}  by {r.get('by') or '?'}  until {_iso(r['expires'])}")
    return "\n".join(out)


def gate_command(g: "Gate", args: str = "") -> str:
    """``/gate`` shows whether the policy files changed this session; ``/gate accept`` adopts them (D-038)."""
    words = (args or "").split()
    if words[:1] == ["accept"]:
        return g.accept_policies()
    if words and words[0] != "status":
        return "usage: gate [status] | gate accept"
    changed = g.changed_policies()
    text = ("policy as loaded at session start" if changed is None else "policy files changed this session; the "
            "stricter of old and new applies. Run /gate accept to adopt them.")
    for where, error, floor in (("", g._policy_error, g._floor),     # #557: a standing policy error shows here too
                                (" in the changed files", changed is not None and g._changed_error, g._changed_floor)):
        if error:
            used = "the stricter of your policy and the defaults applies" if floor is not None else "the defaults apply"
            text += f"\npolicy error{where} ({used}): {error}"
    return text


def permissions_command(g: "Gate", args: str = "") -> Any:
    """``/permissions`` shows the mode and its counts; ``bypass`` and ``ask`` switch it; ``json`` is for clients."""
    words = (args or "").split()
    if words[:1] == ["json"]:
        return g.permissions_snapshot()
    if words[:1] == ["full"]:
        if len(words) != 2:
            return "usage: permissions full <session-id|all>"
        g.set_permissions(perm.FULL, session=words[1])
    elif words[:1] in (["bypass"], ["ask"]):
        if len(words) > 2 or words[1:] not in ([], ["launch"]):
            return "usage: permissions [status] | full <session-id|all> | bypass | ask"
        g.set_permissions(words[0], "launch flag" if words[1:] == ["launch"] else "/permissions")
    elif words and words[0] != "status":
        return "usage: permissions [status] | full <session-id|all> | bypass | ask"
    return perm.status_text(g.permissions, on=g.bypass_on, available=not g.executor_required)


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat(timespec="seconds")


def _risk_lookup_for(ctx) -> Callable[[str], Optional[str]]:
    """Registry risk by tool name: plugin tools from the manager, then the built-in registry."""
    builtin: dict = {}

    def lookup(name: str) -> Optional[str]:
        entry = (getattr(getattr(ctx, "manager", None), "tools", None) or {}).get(name)
        if entry is not None:
            return entry.spec.risk
        if "reg" not in builtin:
            try:
                from exo.core.tools.registry import default_registry
                builtin["reg"] = default_registry()
            except Exception:
                builtin["reg"] = None
        tool = builtin["reg"].get(name) if builtin["reg"] else None
        return tool.spec.risk if tool else None
    return lookup


def register(ctx) -> Gate:
    """Plugin entry point: build the gate from plugin settings and register the hooks."""
    # Exo's context has get_explicit_config (no manifest default), so EXO_GATE_* env vars sit between an
    # explicit setting and the default (issue #19). A host without it keeps its own get_config.
    get_config = getattr(ctx, "get_config", None)
    explicit = getattr(ctx, "get_explicit_config", None)
    if callable(explicit):
        get_config = lambda key, default=None: explicit(key)  # noqa: E731
    g = Gate(resolve_settings(get_config=get_config), risk_lookup=_risk_lookup_for(ctx),
             router_getter=lambda: getattr(ctx, "router", None),
             required_getter=lambda: bool(getattr(getattr(ctx, "manager", None), "executor_required", False)))
    if perm.seat_default() == perm.BYPASS:  # read once, at start; a seat file is a store path (D-065)
        g.start_from_seat()
    ctx.register_hook("pre_tool_call", g.apre_tool_call)
    ctx.register_hook("post_approval_response", g.post_approval_response)
    reg_cmd = getattr(ctx, "register_command", None)
    if reg_cmd is not None:  # reached through command.run: no new protocol command (D-033)
        try:
            reg_cmd("grants", lambda args="": grants_command(g, args), "List or revoke standing approval grants",
                    "[list] | revoke <id>")
            reg_cmd("gate", lambda args="": gate_command(g, args), "Show or accept policy files changed this session",
                    "[status] | accept")
            reg_cmd("permissions", lambda args="": permissions_command(g, args),
                    "Ask before risky calls, or bypass: " + perm.SUMMARY, "[status] | full <session-id|all> | bypass | ask")
        except Exception:
            logger.warning("exo-gate: could not register /grants or /gate", exc_info=True)
    return g
