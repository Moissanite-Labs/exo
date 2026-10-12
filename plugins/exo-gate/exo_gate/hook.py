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
``apre_tool_call`` asks the router's ``decision`` role ``ask_choice(router, state, question,
["allow", "log", "ask", "deny"])`` after the rules ran, takes the top option and keeps the stricter of the
two with ``tighten_only(rule_outcome, verdict)``. A model never loosens an outcome, is never asked about a
call the rules already deny, and a failure or timeout keeps the rule outcome. The core awaits async hooks,
so the gate registers ``apre_tool_call``; the sync ``pre_tool_call`` is the rules alone.

Settings resolve in this order, first hit wins: explicit overrides, Hermes plugin settings
(``plugins.entries.exo-gate.settings.<key>``), ``EXO_GATE_<KEY>`` env vars, defaults. Nothing
here names a principal: without a store the gate runs on the default policies and only logs.
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
        store = str(exo_home / "principals" / env["EXO_PRINCIPAL"])
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
    the ``binding`` hash of the exact text it covers. Credentials never get a grant. An outward
    grant without a binding never matches, so no outward category has an unscoped always."""

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
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def match(self, category: str, target: str, binding: str, outward: bool) -> Optional[dict]:
        if category == "credentials":
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

    def add(self, category: str, target: str, binding: str, days: float, by: str, tool: str = "") -> Optional[dict]:
        if not self.path or category == "credentials" or days <= 0:
            return None
        now = self.clock()
        g = {"id": "g_" + secrets.token_hex(6), "rule": category, "target": _glob.escape(target or ""),
             "binding": binding or "", "tool": tool, "by": by, "created": now, "expires": now + days * 86400}
        with self._lock:
            rows = [r for r in self._read() if float(r.get("expires") or 0) > now]  # drop expired ones
            rows.append(g)
            self._write(rows)
        return g


class LedgerSink:
    """Appends `action` events to the principal's ledger. A no-op (plain logging) when no store
    or no signing key is configured. Never raises into the tool call."""

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
            from exo.core.seats import ensure_key
            if s.key_path and not s.key_path.is_file():
                ensure_key(s.key_path)  # first use: generate the seat key (0600)
            from exo import crypto
            from exo.ledger import Ledger
            self._ledger = Ledger(s.principal_store / "ledger.sqlite", crypto.Signer.from_file(s.author, s.key_path))
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


class Gate:
    """Holds settings, cached policies and the ledger sink; exposes the two hook callbacks."""

    def __init__(self, settings: Settings, sink: LedgerSink | None = None,
                 risk_lookup: Callable[[str], Optional[str]] | None = None,
                 router_getter: Callable[[], Any] | None = None):
        self.settings = settings
        self.router_getter = router_getter
        self.risk_lookup = risk_lookup
        self.sink = sink if sink is not None else LedgerSink(settings)
        self._policies: Optional[dict] = None
        self._policy_stamp: tuple = ()
        self._policy_error = ""
        self._pending: Dict[str, tuple[Optional[str], Optional[str], dict]] = {}
        self._spent: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.grants = GrantStore(settings.grants_path)

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

    def policies(self) -> dict:
        stamp = self._stamp() + self._scrub_stamp()
        if self._policies is None or stamp != self._policy_stamp:
            try:
                self._policies = gate.load_policies(self.settings.principal_store)
                self._policy_error = ""
            except (gate.PolicyError, OSError) as exc:
                # Fall back to the defaults, which ask on anything irreversible or public.
                logger.warning("exo-gate: policy error, using defaults: %s", exc)
                self._policies = gate.normalize_policies({})
                self._policy_error = str(exc)
            self._policy_stamp = stamp
        return self._policies

    # --- hooks ------------------------------------------------------------------------

    def _load_body_file(self, c: gate.Classification) -> None:
        if not (c.text_file and self.settings.read_body_files and not c.text):
            return
        try:
            p = Path(c.text_file).expanduser()
            if p.is_file() and p.stat().st_size <= _MAX_BODY_FILE:
                c.text = p.read_text(encoding="utf-8", errors="replace")
                if not c.link:
                    c.link = str(p)
        except OSError:
            pass

    def _rule_key(self, tool: str, args: Mapping, c: gate.Classification, d: gate.Decision) -> str:
        # An "always" answer must not cover a different public or irreversible act, so those
        # keys are bound to the exact arguments. Others are bound to category and target.
        if c.category == "credentials" or gate.is_outward(c, d) or d.violations:
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
        """``(top option, {"route", "source"})`` from the decision role, or None (no router, failure).
        The route is the one that answered: the first route (Jev) or a fallback."""
        router = self.router_getter() if self.router_getter else None
        if router is None:
            return None
        try:
            from exo.core.models.decision import ask_choice_result
            state = {"tool": tool, "classification": c.as_dict(), "risk": risk, "rule_outcome": d.outcome,
                     "reason": d.reason}
            res = await asyncio.wait_for(
                ask_choice_result(router, state, "How strictly should this tool call be gated?",
                                  list(gate.OUTCOMES)), timeout=20)
        except Exception:
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
                             tool_call_id: str = "", turn_id: str = "", risk: Any = None,
                             **_: Any) -> Optional[dict]:
        """The registered hook: the rules, plus the decision model when ``decision_model`` is on."""
        args = args if isinstance(args, dict) else {}
        verdict = None
        risk_s = self._risk(tool_name, risk)
        if self.settings.decision_model:
            try:
                c, d = self._rules(tool_name, args, risk_s)
                if d.outcome != "deny":
                    verdict = await self._verdict(d, c, risk_s, tool_name)  # (option, via) or None
            except Exception:
                logger.exception("exo-gate: rules failed before the decision model on %s", tool_name)
        return self.pre_tool_call(tool_name, args, session_id, task_id, tool_call_id, turn_id, risk,
                                  _verdict=verdict)

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "", task_id: str = "",
                      tool_call_id: str = "", turn_id: str = "", risk: Any = None, _verdict: Any = None,
                      **_: Any) -> Optional[dict]:
        args = args if isinstance(args, dict) else {}
        try:
            return self._pre_tool_call(tool_name, args, session_id, tool_call_id, self._risk(tool_name, risk),
                                       _verdict)
        except Exception as exc:
            logger.exception("exo-gate: internal error on %s", tool_name)
            secret = False
            try:
                secret = bool(gate.args_sensitive(args))
            except Exception:
                secret = True
            if (tool_name in _SAFE_ON_ERROR or self.settings.mode == "observe") and not secret:
                return None
            return {"action": "approve", "message": f"exo-gate could not classify {tool_name} ({exc}). "
                                                    "Approve only if you expected this action. Default if no reply: skip.",
                    "rule_key": f"exo-gate:error:{tool_name}"}

    def _rules(self, tool: str, args: dict, risk: str) -> tuple[gate.Classification, gate.Decision]:
        """Classification and the rule outcome (policies, then the registry risk floor)."""
        c = gate.classify({"tool": tool, "args": args})
        self._load_body_file(c)
        today = _dt.date.today().isoformat()
        d = gate.decide(c, self.policies(), {"spent_today": self._spent.get(today, 0.0)})
        if self._policy_error:
            d.reason += f" (policy error, defaults used: {self._policy_error[:120]})"
        if risk == "high" and c.category == "unknown" and _RANK[d.outcome] < _RANK["ask"]:
            d.reason += f" (registry risk is high and {tool} is not a known tool)"
            d.outcome = "ask"
        return c, d

    def _pre_tool_call(self, tool: str, args: dict, session_id: str, tool_call_id: str,
                       risk: str = "", verdict: Any = None) -> Optional[dict]:
        verdict, via = verdict if isinstance(verdict, tuple) else (verdict, None)
        c, d = self._rules(tool, args, risk)
        pol = self.policies()
        today = _dt.date.today().isoformat()
        if d.outcome != "deny":
            self._tighten(d, verdict)
        outward = gate.is_outward(c, d)
        binding = gate.approval_binding(c, args) if outward else ""
        grant = self._grant_for(c, d, binding, outward) if d.outcome == "ask" else None
        if grant:
            d.outcome, d.rule = "allow", "grant"
            d.reason += f"; standing grant {grant['id']} (until {_iso(grant['expires'])})"
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
        result: Optional[dict] = None
        if d.outcome == "deny":
            result = {"action": "block", "message": f"exo-gate blocked {c.summary or tool}: {d.reason}."}
        elif d.outcome == "ask":
            msg = gate.summarize(c, d, default)
            if d.violations:
                msg += " Fix: " + "; ".join(d.violations) + "."
            key = self._rule_key(tool, args, c, d)
            approvers = gate.approvers_for(c.category, pol.get("approvals"))
            # persist: this gate keeps "always" grants itself (scoped, expiring, in the store), so the
            # core must not also keep an unscoped one in memory.
            result = {"action": "approve", "message": msg, "rule_key": key, "approvers": approvers,
                      "persist": True}
            if binding:
                result["binding"] = binding
            if c.category == "credentials":
                result["once"] = True      # the core never stores an "always" for this (no standing allow)
            body["rule_key"] = key
            body["approvers"] = approvers
        if self.settings.mode == "observe" and c.category != "credentials":  # credentials are enforced in every mode
            body["would"] = result["action"] if result else "run"
            result = None
        if d.outcome in ("allow", "log") and c.cost:
            with self._lock:
                self._spent[today] = self._spent.get(today, 0.0) + float(c.cost)
        eid = self.sink.append(body, session=session_id)
        if result and result["action"] == "approve":
            with self._lock:
                scope = {"rule": c.category, "target": c.target or tool, "binding": binding, "outward": outward,
                         "tool": tool}
                self._pending["plugin_rule:" + result["rule_key"]] = (eid, session_id or None, scope)
                if len(self._pending) > 500:
                    self._pending.pop(next(iter(self._pending)))
        return result

    def post_approval_response(self, pattern_key: str = "", choice: str = "", decided_by: str = "",
                               **_: Any) -> None:
        with self._lock:
            pending = self._pending.pop(pattern_key, None)
        if pending is None:
            return None
        eid, session, scope = pending
        body = {"gate": PLUGIN_ID, "phase": "approval", "choice": str(choice),
                "approved": str(choice) in ("once", "session", "always"), "decided_by": str(decided_by or ""),
                "rule_key": pattern_key.split(":", 1)[-1]}
        aid = self.sink.append(body, session=session, refs=(eid,) if eid else ())
        if str(choice) == "always":
            self._add_grant(scope, str(decided_by or ""), session, aid or eid)
        return None

    # --- standing grants (D-033) --------------------------------------------------------

    def _grant_for(self, c: gate.Classification, d: gate.Decision, binding: str, outward: bool) -> Optional[dict]:
        if c.category == "credentials" or (d.violations and not binding):
            return None
        try:
            return self.grants.match(c.category, c.target or c.tool, binding, outward)
        except Exception:
            logger.warning("exo-gate: grant lookup failed; asking instead", exc_info=True)
            return None

    def _add_grant(self, scope: Mapping, by: str, session: Optional[str], ref: Optional[str]) -> None:
        approvals = self.policies().get("approvals") or {}
        days = float(approvals.get("outward_always_days" if scope.get("outward") else "always_days") or 0)
        try:
            g = self.grants.add(str(scope.get("rule") or ""), str(scope.get("target") or ""),
                                str(scope.get("binding") or ""), days, by, str(scope.get("tool") or ""))
        except Exception:
            logger.warning("exo-gate: could not save the standing grant", exc_info=True)
            return
        if g is None:
            return
        body = {"gate": PLUGIN_ID, "phase": "grant", "grant": g["id"], "rule": g["rule"], "target": g["target"],
                "binding": g["binding"] or None, "by": by, "expires": _iso(g["expires"])}
        self.sink.append(body, session=session, refs=(ref,) if ref else ())


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
    get_config = getattr(ctx, "get_config", None)
    g = Gate(resolve_settings(get_config=get_config), risk_lookup=_risk_lookup_for(ctx),
             router_getter=lambda: getattr(ctx, "router", None))
    ctx.register_hook("pre_tool_call", g.apre_tool_call)
    ctx.register_hook("post_approval_response", g.post_approval_response)
    return g
