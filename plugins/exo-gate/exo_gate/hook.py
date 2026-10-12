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

Decision models (D-015): ``exo.core.models.decision.tighten_only`` is called when it exists, to
make an outcome stricter. The gate keeps the stricter of the two, so a model never loosens it.

Settings resolve in this order, first hit wins: explicit overrides, Hermes plugin settings
(``plugins.entries.exo-gate.settings.<key>``), ``EXO_GATE_<KEY>`` env vars, defaults. Nothing
here names a principal: without a store the gate runs on the default policies and only logs.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import threading
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
    key_path = get("key_path") or str(exo_home / "keys" / f"{author}.key")
    mode = str(get("mode")).strip().lower()
    return Settings(
        principal_store=Path(store).expanduser() if store else None,
        author=author,
        key_path=Path(key_path).expanduser(),
        mode=mode if mode in ("enforce", "observe") else "enforce",
        read_body_files=bool(get("read_body_files")),
    )


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
        if not s.key_path or not s.key_path.is_file():
            logger.warning("exo-gate: no signing key at %s; decisions are logged only", s.key_path)
            return None
        try:
            from exo import crypto
            from exo.ledger import Ledger
            self._ledger = Ledger(s.principal_store / "ledger.sqlite", crypto.Signer.from_file(s.author, s.key_path))
        except Exception as exc:  # pragma: no cover - depends on the host
            logger.warning("exo-gate: cannot open ledger: %s", exc)
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
                 risk_lookup: Callable[[str], Optional[str]] | None = None):
        self.settings = settings
        self.risk_lookup = risk_lookup
        self.sink = sink if sink is not None else LedgerSink(settings)
        self._policies: Optional[dict] = None
        self._policy_stamp: tuple = ()
        self._policy_error = ""
        self._pending: Dict[str, tuple[Optional[str], Optional[str]]] = {}
        self._spent: Dict[str, float] = {}
        self._lock = threading.Lock()

    # --- policies ---------------------------------------------------------------------

    def _stamp(self) -> tuple:
        root = self.settings.principal_store
        if not root:
            return ()
        paths = gate.policy_files(root) + [root / "agents"]
        return tuple((str(p), p.stat().st_mtime_ns if p.exists() else 0) for p in paths)

    def policies(self) -> dict:
        stamp = self._stamp()
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
        if c.reversibility == "irreversible" or d.exposure == "public" or d.violations:
            digest = hashlib.sha256(json.dumps({"t": tool, "a": args}, sort_keys=True, default=str)
                                    .encode("utf-8")).hexdigest()[:16]
            return f"exo-gate:{c.category}:{digest}"
        return f"exo-gate:{c.category}:{c.target or tool}"

    def _risk(self, tool: str, given: Any) -> str:
        if given in ("low", "medium", "high"):
            return str(given)
        try:
            return (self.risk_lookup(tool) if self.risk_lookup else None) or ""
        except Exception:
            return ""

    @staticmethod
    def _tighten(d: gate.Decision, c: gate.Classification, risk: str) -> None:
        """Seam for the decision model. May only raise the outcome, never lower it."""
        try:
            from exo.core.models import decision as dm
            fn = getattr(dm, "tighten_only", None)
        except ImportError:
            fn = None
        if fn is None:
            return
        try:
            out = fn(d.outcome, {"classification": c.as_dict(), "risk": risk, "reason": d.reason})
        except Exception:
            logger.warning("exo-gate: decision model failed; keeping the rule outcome", exc_info=True)
            return
        out = getattr(out, "outcome", out)
        if isinstance(out, str) and _RANK.get(out, -1) > _RANK[d.outcome]:
            d.reason += f" (decision model tightened {d.outcome} to {out})"
            d.outcome = out

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "", task_id: str = "",
                      tool_call_id: str = "", turn_id: str = "", risk: Any = None, **_: Any) -> Optional[dict]:
        args = args if isinstance(args, dict) else {}
        try:
            return self._pre_tool_call(tool_name, args, session_id, tool_call_id, self._risk(tool_name, risk))
        except Exception as exc:
            logger.exception("exo-gate: internal error on %s", tool_name)
            if tool_name in _SAFE_ON_ERROR or self.settings.mode == "observe":
                return None
            return {"action": "approve", "message": f"exo-gate could not classify {tool_name} ({exc}). "
                                                    "Approve only if you expected this action. Default if no reply: skip.",
                    "rule_key": f"exo-gate:error:{tool_name}"}

    def _pre_tool_call(self, tool: str, args: dict, session_id: str, tool_call_id: str,
                       risk: str = "") -> Optional[dict]:
        c = gate.classify({"tool": tool, "args": args})
        self._load_body_file(c)
        pol = self.policies()
        today = _dt.date.today().isoformat()
        d = gate.decide(c, pol, {"spent_today": self._spent.get(today, 0.0)})
        if self._policy_error:
            d.reason += f" (policy error, defaults used: {self._policy_error[:120]})"
        if risk == "high" and c.category == "unknown" and _RANK[d.outcome] < _RANK["ask"]:
            d.reason += f" (registry risk is high and {tool} is not a known tool)"
            d.outcome = "ask"
        self._tighten(d, c, risk)
        default = str(pol["gates"].get("ask_default") or "skip")
        body = {
            "gate": PLUGIN_ID, "phase": "decision", "tool": tool, "outcome": d.outcome,
            "classification": c.as_dict(), "decision": d.as_dict(), "mode": self.settings.mode, "risk": risk or None,
            "tool_call_id": tool_call_id or None,
            "args_sha256": hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        }
        result: Optional[dict] = None
        if d.outcome == "deny":
            result = {"action": "block", "message": f"exo-gate blocked {c.summary or tool}: {d.reason}."}
        elif d.outcome == "ask":
            msg = gate.summarize(c, d, default)
            if d.violations:
                msg += " Fix: " + "; ".join(d.violations) + "."
            key = self._rule_key(tool, args, c, d)
            result = {"action": "approve", "message": msg, "rule_key": key}
            body["rule_key"] = key
        if self.settings.mode == "observe":
            body["would"] = result["action"] if result else "run"
            result = None
        if d.outcome in ("allow", "log") and c.cost:
            with self._lock:
                self._spent[today] = self._spent.get(today, 0.0) + float(c.cost)
        eid = self.sink.append(body, session=session_id)
        if result and result["action"] == "approve":
            with self._lock:
                self._pending["plugin_rule:" + result["rule_key"]] = (eid, session_id or None)
                if len(self._pending) > 500:
                    self._pending.pop(next(iter(self._pending)))
        return result

    def post_approval_response(self, pattern_key: str = "", choice: str = "", decided_by: str = "",
                               **_: Any) -> None:
        with self._lock:
            pending = self._pending.pop(pattern_key, None)
        if pending is None:
            return None
        eid, session = pending
        body = {"gate": PLUGIN_ID, "phase": "approval", "choice": str(choice),
                "approved": str(choice) in ("once", "session", "always"), "decided_by": str(decided_by or ""),
                "rule_key": pattern_key.split(":", 1)[-1]}
        self.sink.append(body, session=session, refs=(eid,) if eid else ())
        return None


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
    g = Gate(resolve_settings(get_config=get_config), risk_lookup=_risk_lookup_for(ctx))
    ctx.register_hook("pre_tool_call", g.pre_tool_call)
    ctx.register_hook("post_approval_response", g.post_approval_response)
    return g
