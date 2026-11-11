"""Plugin entry and cognitive organs for exo-habitat on the Exo core.

Zero external dependencies (Python stdlib + sqlite3 only) and zero imports inside
`src/exo/core/` so D-083 inward-import (<= 39) and core line budgets stay untouched.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

_HERE = Path(__file__).resolve().parent
VALID_STATUSES = frozenset({"verified", "hypothesis", "falsified"})


def _ensure_exo_importable() -> None:
    try:
        import exo  # noqa: F401

        return
    except ImportError:
        pass
    for cand in (os.environ.get("EXO_SRC"), _HERE.parent.parent / "src"):
        if cand and (Path(cand) / "exo" / "__init__.py").is_file():
            sys.path.insert(0, str(cand))
            return


_ensure_exo_importable()


def _canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class EpistemicClaim:
    claim_id: int
    topic: str
    statement: str
    status: str
    confidence: float
    evidence_ref: str
    falsifier: str
    created_at: float
    ttl_seconds: int | None
    prev_hash: str
    entry_hash: str

    def is_stale(self, now: float | None = None) -> bool:
        if self.status == "falsified" or self.ttl_seconds is None:
            return False
        ref_now = time.time() if now is None else now
        return (ref_now - self.created_at) > self.ttl_seconds


class EpistemicStore:
    """Append-only, hash-chained ledger of verified facts, hypotheses, and dead ends."""

    GENESIS = "0" * 64

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            exo_home = Path(os.environ.get("EXO_HOME", Path.home() / ".exo"))
            default_db = exo_home / "habitat.sqlite"
            db_path = Path(os.environ.get("EXO_HABITAT_DB", default_db))
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS epistemic_claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                statement TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_ref TEXT NOT NULL,
                falsifier TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl_seconds INTEGER,
                prev_hash TEXT NOT NULL,
                entry_hash TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def head_hash(self) -> str:
        row = self._conn.execute(
            "SELECT entry_hash FROM epistemic_claims ORDER BY claim_id DESC LIMIT 1"
        ).fetchone()
        return str(row["entry_hash"]) if row else self.GENESIS

    def record(
        self,
        topic: str,
        statement: str,
        *,
        status: str = "hypothesis",
        confidence: float = 0.7,
        evidence_ref: str = "",
        falsifier: str = "",
        ttl_seconds: int | None = 86400,
        now: float | None = None,
    ) -> EpistemicClaim:
        clean_topic, clean_stmt = topic.strip(), statement.strip()
        if not clean_topic or not clean_stmt:
            raise ValueError("topic and statement must be non-empty")
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive when set")

        ts = round(time.time() if now is None else float(now), 6)
        conf = round(confidence, 6)
        ev, fal = evidence_ref.strip(), falsifier.strip()
        prev = self.head_hash()
        digest = _canonical_hash(
            {
                "topic": clean_topic,
                "statement": clean_stmt,
                "status": status,
                "confidence": conf,
                "evidence_ref": ev,
                "falsifier": fal,
                "created_at": ts,
                "ttl_seconds": ttl_seconds,
                "prev_hash": prev,
            }
        )
        cur = self._conn.execute(
            """
            INSERT INTO epistemic_claims (
                topic, statement, status, confidence, evidence_ref,
                falsifier, created_at, ttl_seconds, prev_hash, entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (clean_topic, clean_stmt, status, conf, ev, fal, ts, ttl_seconds, prev, digest),
        )
        self._conn.commit()
        return EpistemicClaim(
            claim_id=int(cur.lastrowid or 0),
            topic=clean_topic,
            statement=clean_stmt,
            status=status,
            confidence=conf,
            evidence_ref=ev,
            falsifier=fal,
            created_at=ts,
            ttl_seconds=ttl_seconds,
            prev_hash=prev,
            entry_hash=digest,
        )

    def falsify(
        self,
        claim_id: int,
        reason: str,
        *,
        evidence_ref: str = "",
        now: float | None = None,
    ) -> EpistemicClaim:
        orig = self._conn.execute(
            "SELECT * FROM epistemic_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if orig is None:
            raise KeyError(f"claim #{claim_id} not found")
        msg = f"[FALSIFIED #{claim_id}] {orig['statement']} — Reason: {reason.strip()}"
        return self.record(
            topic=str(orig["topic"]),
            statement=msg,
            status="falsified",
            confidence=1.0,
            evidence_ref=evidence_ref or str(orig["evidence_ref"]),
            falsifier=reason.strip(),
            ttl_seconds=None,
            now=now,
        )

    def list_claims(self, topic: str | None = None) -> list[EpistemicClaim]:
        if topic and topic.strip():
            rows = self._conn.execute(
                "SELECT * FROM epistemic_claims WHERE topic = ? ORDER BY claim_id ASC",
                (topic.strip(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM epistemic_claims ORDER BY claim_id ASC"
            ).fetchall()
        return [
            EpistemicClaim(
                claim_id=int(r["claim_id"]),
                topic=str(r["topic"]),
                statement=str(r["statement"]),
                status=str(r["status"]),
                confidence=float(r["confidence"]),
                evidence_ref=str(r["evidence_ref"]),
                falsifier=str(r["falsifier"]),
                created_at=float(r["created_at"]),
                ttl_seconds=int(r["ttl_seconds"]) if r["ttl_seconds"] is not None else None,
                prev_hash=str(r["prev_hash"]),
                entry_hash=str(r["entry_hash"]),
            )
            for r in rows
        ]

    def stale_claims(self, now: float | None = None) -> list[EpistemicClaim]:
        ref_now = time.time() if now is None else now
        return [c for c in self.list_claims() if c.is_stale(ref_now)]

    def verify_chain(self) -> tuple[bool, int | None]:
        prev = self.GENESIS
        for c in self.list_claims():
            if c.prev_hash != prev:
                return False, c.claim_id
            expected = _canonical_hash(
                {
                    "topic": c.topic,
                    "statement": c.statement,
                    "status": c.status,
                    "confidence": round(c.confidence, 6),
                    "evidence_ref": c.evidence_ref,
                    "falsifier": c.falsifier,
                    "created_at": round(c.created_at, 6),
                    "ttl_seconds": c.ttl_seconds,
                    "prev_hash": c.prev_hash,
                }
            )
            if c.entry_hash != expected:
                return False, c.claim_id
            prev = c.entry_hash
        return True, None


@dataclass
class ProprioceptionSnapshot:
    context_ratio: float
    budget_ratio: float
    failure_streak: int
    thrash_detected: bool
    core_import_headroom: int
    core_lines: int
    advisories: list[str]

    def format_hud(self) -> str:
        state = "NOMINAL" if not self.advisories else ", ".join(self.advisories)
        return (
            f"[HABITAT HUD] ctx={self.context_ratio * 100:.1f}% | "
            f"spend={self.budget_ratio * 100:.1f}% | "
            f"fail_streak={self.failure_streak} | "
            f"core_imports_left={self.core_import_headroom} | "
            f"core_lines={self.core_lines:,} | "
            f"signal={state}"
        )


class ProprioceptionMonitor:
    """Self-telemetry organ giving an agent awareness of its operational boundaries."""

    def __init__(self, history_window: int = 8, thrash_repeat_threshold: int = 3) -> None:
        self._recent_calls: deque[str] = deque(maxlen=history_window)
        self._failure_streak = 0
        self._thrash_threshold = thrash_repeat_threshold

    def record_tool_call(self, tool_name: str, args: dict[str, Any], *, ok: bool) -> None:
        self._recent_calls.append(f"{tool_name}:{_canonical_hash(args)[:12]}")
        self._failure_streak = 0 if ok else (self._failure_streak + 1)

    def sense(
        self,
        *,
        tokens_used: int = 0,
        token_budget: int = 128000,
        spend_usd: float = 0.0,
        daily_budget_usd: float = 10.0,
        inward_core_imports: int | None = None,
        core_lines: int | None = None,
    ) -> ProprioceptionSnapshot:
        from exo.budget_scorecard import INWARD_IMPORTS_MAX, core_budget_snapshot

        ctx_ratio = min(1.0, max(0.0, tokens_used / max(1, token_budget)))
        spend_ratio = (
            min(1.0, max(0.0, spend_usd / daily_budget_usd)) if daily_budget_usd > 0 else 0.0
        )
        counts: dict[str, int] = {}
        for sig in self._recent_calls:
            counts[sig] = counts.get(sig, 0) + 1
        thrash = any(c >= self._thrash_threshold for c in counts.values())

        budget = core_budget_snapshot()
        measured_imports = inward_core_imports if inward_core_imports is not None else budget.inward_imports
        measured_lines = core_lines if core_lines is not None else budget.core_lines
        import_headroom = INWARD_IMPORTS_MAX - measured_imports
        advisories: list[str] = []
        if thrash or self._failure_streak >= 3:
            advisories.append("BREAK_THRASH_LOOP")
        if ctx_ratio >= 0.80:
            advisories.append("COMPACT_OR_HANDOFF")
        if spend_ratio >= 0.85:
            advisories.append("CONSERVE_MODEL_SPEND")
        if import_headroom < 0:
            advisories.append("MOVE_NEW_MODULES_OUTSIDE_CORE_D083")

        return ProprioceptionSnapshot(
            context_ratio=round(ctx_ratio, 4),
            budget_ratio=round(spend_ratio, 4),
            failure_streak=self._failure_streak,
            thrash_detected=thrash,
            core_import_headroom=import_headroom,
            core_lines=measured_lines,
            advisories=advisories,
        )


@dataclass
class ContractNode:
    node_id: str
    seat: str
    interface_contract: str
    depends_on: tuple[str, ...] = ()
    touches_paths: tuple[str, ...] = ()
    status: str = "pending"


class ContractGraph:
    """Contract-first Swarm DAG planner preventing deep stacked-PR deadlocks."""

    def __init__(self) -> None:
        self._nodes: dict[str, ContractNode] = {}

    def add_node(self, node: ContractNode) -> None:
        if not node.node_id.strip() or not node.interface_contract.strip():
            raise ValueError("node_id and interface_contract are required")
        self._nodes[node.node_id] = node

    def mark_done(self, node_id: str) -> None:
        if node_id not in self._nodes:
            raise KeyError(f"unknown contract node: {node_id}")
        n = self._nodes[node_id]
        self._nodes[node_id] = ContractNode(
            node_id=n.node_id,
            seat=n.seat,
            interface_contract=n.interface_contract,
            depends_on=n.depends_on,
            touches_paths=n.touches_paths,
            status="done",
        )

    def topological_waves(self) -> list[list[str]]:
        for nid, node in self._nodes.items():
            for dep in node.depends_on:
                if dep not in self._nodes:
                    raise KeyError(f"node '{nid}' depends on unknown contract '{dep}'")

        in_degree = {nid: len(set(node.depends_on)) for nid, node in self._nodes.items()}
        dependents: dict[str, list[str]] = {nid: [] for nid in self._nodes}
        for nid, node in self._nodes.items():
            for dep in set(node.depends_on):
                dependents[dep].append(nid)

        ready = sorted([nid for nid, deg in in_degree.items() if deg == 0])
        waves: list[list[str]] = []
        visited = 0
        while ready:
            waves.append(ready)
            visited += len(ready)
            next_ready: list[str] = []
            for nid in ready:
                for child in dependents[nid]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        next_ready.append(child)
            ready = sorted(next_ready)

        if visited != len(self._nodes):
            raise ValueError("cyclic dependency detected in Swarm contract graph")
        return waves

    def ready_wave(self) -> tuple[list[ContractNode], list[tuple[str, str, str]]]:
        self.topological_waves()
        ready_nodes = sorted(
            [
                n
                for n in self._nodes.values()
                if n.status == "pending"
                and all(self._nodes[d].status == "done" for d in n.depends_on)
            ],
            key=lambda n: n.node_id,
        )
        return ready_nodes, self.detect_path_collisions(ready_nodes)

    @staticmethod
    def detect_path_collisions(nodes: list[ContractNode]) -> list[tuple[str, str, str]]:
        seen: dict[str, str] = {}
        collisions: list[tuple[str, str, str]] = []
        for node in nodes:
            for path in node.touches_paths:
                norm = path.strip().rstrip("/")
                if not norm:
                    continue
                if norm in seen and seen[norm] != node.node_id:
                    collisions.append((norm, seen[norm], node.node_id))
                else:
                    seen[norm] = node.node_id
        return collisions


@dataclass(frozen=True)
class CandidateProbe:
    probe_id: str
    hypothesis: str
    tests_passed: int
    tests_failed: int
    d083_compliant: bool
    cost_usd: float
    latency_ms: float
    lines_delta: int = 0

    @property
    def gate_ok(self) -> bool:
        return self.tests_passed > 0 and self.tests_failed == 0 and self.d083_compliant

    def utility_score(self) -> float:
        if not self.gate_ok:
            return -1e9
        return (
            (self.tests_passed * 10.0)
            - (self.cost_usd * 25.0)
            - (self.latency_ms / 1000.0)
            - (max(0, self.lines_delta) * 0.05)
        )


@dataclass
class ArenaDecision:
    winner: CandidateProbe | None
    rejected: list[tuple[CandidateProbe, str]] = field(default_factory=list)


class ProbeArena:
    """Scores counterfactual probes and logs rejected candidates as falsified lessons."""

    def __init__(self, store: EpistemicStore | None = None) -> None:
        self._store = store

    def evaluate(
        self, topic: str, probes: list[CandidateProbe], *, now: float | None = None
    ) -> ArenaDecision:
        if not probes:
            return ArenaDecision(winner=None, rejected=[])
        passing = sorted(
            [p for p in probes if p.gate_ok],
            key=lambda p: (p.utility_score(), -p.cost_usd, -p.latency_ms),
            reverse=True,
        )
        winner = passing[0] if passing else None
        rejected: list[tuple[CandidateProbe, str]] = []

        for probe in probes:
            if winner and probe.probe_id == winner.probe_id:
                if self._store is not None:
                    self._store.record(
                        topic=topic,
                        statement=f"Promoted probe '{probe.probe_id}': {probe.hypothesis}",
                        status="verified",
                        confidence=0.95,
                        evidence_ref=f"probe:{probe.probe_id}:tests={probe.tests_passed}",
                        falsifier="regression on held-out or D-083 suite",
                        now=now,
                    )
                continue
            if not probe.d083_compliant:
                reason = "violated D-083 core import/line budget"
            elif probe.tests_failed > 0 or probe.tests_passed == 0:
                reason = (
                    f"failed verification "
                    f"({probe.tests_failed} failed, {probe.tests_passed} passed)"
                )
            elif winner:
                reason = (
                    f"outscored by '{winner.probe_id}' "
                    f"({probe.utility_score():.2f} < {winner.utility_score():.2f})"
                )
            else:
                reason = "did not clear promotion gate"

            rejected.append((probe, reason))
            if self._store is not None:
                self._store.record(
                    topic=topic,
                    statement=f"Rejected probe '{probe.probe_id}' ({probe.hypothesis}): {reason}",
                    status="falsified",
                    confidence=1.0,
                    evidence_ref=f"probe:{probe.probe_id}",
                    falsifier=reason,
                    ttl_seconds=None,
                    now=now,
                )
        return ArenaDecision(winner=winner, rejected=rejected)


class HabitatRuntime:
    """Session companion binding EpistemicStore, ProprioceptionMonitor, and ContractGraph."""

    def __init__(self, db_path: str | None = None) -> None:
        self.store = EpistemicStore(db_path=db_path)
        self.monitor = ProprioceptionMonitor()
        self.contracts = ContractGraph()

    def cmd_sense(self, _rest: str = "") -> str:
        snap = self.monitor.sense()
        ok, broken = self.store.verify_chain()
        chain_str = "verified" if ok else f"BROKEN_AT_{broken}"
        return (
            f"{snap.format_hud()}\n"
            f"[EPISTEMIC LEDGER] chain={chain_str} | stale_claims={len(self.store.stale_claims())}"
        )

    def cmd_claim(self, rest: str) -> str:
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return "usage: /exo claim <topic> | <statement> [| <falsifier>]"
        falsifier = parts[2] if len(parts) >= 3 else ""
        c = self.store.record(parts[0], parts[1], falsifier=falsifier)
        return (
            f"recorded #{c.claim_id} [{c.status}] ({c.topic}): "
            f"{c.statement} [sha256:{c.entry_hash[:12]}]"
        )

    def cmd_audit(self, rest: str = "") -> str:
        claims = self.store.list_claims(topic=rest.strip() or None)
        ok, broken = self.store.verify_chain()
        header = f"epistemic audit ({len(claims)} claims, chain={'ok' if ok else f'#{broken}'})"
        if not claims:
            return header
        lines = [header]
        for c in claims[-15:]:
            tag = "STALE" if c.is_stale() else c.status.upper()
            lines.append(f"  #{c.claim_id} [{tag}] {c.topic}: {c.statement}")
        return "\n".join(lines)

    def cmd_wave(self, _rest: str = "") -> str:
        ready, collisions = self.contracts.ready_wave()
        if not ready:
            return "swarm contract wave: 0 pending unblocked nodes"
        lines = [f"swarm contract wave: {len(ready)} parallelizable node(s)"]
        for n in ready:
            lines.append(f"  - {n.node_id} (seat={n.seat}): {n.interface_contract}")
        for path, a, b in collisions:
            lines.append(f"  ! PATH COLLISION on '{path}' between '{a}' and '{b}'")
        return "\n".join(lines)


def register(ctx: Any) -> HabitatRuntime:
    get = getattr(ctx, "get_config", None)
    opt = (lambda k, d=None: get(k, d)) if get else (lambda k, d=None: d)
    runtime = HabitatRuntime(db_path=opt("db_path"))
    plugin_name = "exo-habitat"
    try:
        from exo.dispatcher import register_root, register_subcommand

        register_subcommand(
            "sense",
            lambda rest: runtime.cmd_sense(rest),
            "Show proprioceptive HUD, D-083 core headroom, and epistemic ledger health.",
            plugin=plugin_name,
        )
        register_subcommand(
            "claim",
            lambda rest: runtime.cmd_claim(rest),
            "Record a hash-chained epistemic claim or hypothesis with optional falsifier.",
            plugin=plugin_name,
            args_hint="<topic> | <statement> [| <falsifier>]",
        )
        register_subcommand(
            "audit",
            lambda rest: runtime.cmd_audit(rest),
            "Verify epistemic hash chain and list active, stale, and falsified claims.",
            plugin=plugin_name,
            args_hint="[topic]",
        )
        register_subcommand(
            "wave",
            lambda rest: runtime.cmd_wave(rest),
            "Compute the next conflict-free parallel Swarm contract wave.",
            plugin=plugin_name,
        )
        register_root(ctx)
    except ImportError:
        register_cmd = getattr(ctx, "register_command", None)
        if callable(register_cmd):
            register_cmd("sense", runtime.cmd_sense)
            register_cmd("claim", runtime.cmd_claim)
            register_cmd("audit", runtime.cmd_audit)
            register_cmd("wave", runtime.cmd_wave)
    return runtime
