"""Unit and integration test suite for exo-habitat (pytest & unittest compatible)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

PLUGIN_FILE = Path(__file__).resolve().parent.parent / "__init__.py"
_SPEC = importlib.util.spec_from_file_location("exo_habitat_plugin", PLUGIN_FILE)
assert _SPEC is not None and _SPEC.loader is not None
exo_habitat = importlib.util.module_from_spec(_SPEC)
sys.modules["exo_habitat_plugin"] = exo_habitat
_SPEC.loader.exec_module(exo_habitat)


class TestExoHabitat(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_epistemic_hash_chain_and_tamper_detection(self) -> None:
        db = self.tmp_path / "habitat.sqlite"
        store = exo_habitat.EpistemicStore(db_path=db)
        c1 = store.record(
            "swarm-pr-978",
            "Moving group_commands.py out of src/exo/core keeps inward imports <= 39",
            status="verified",
            confidence=0.98,
            evidence_ref="tests/integration/test_wave1_acceptance.py#L190",
            falsifier="INWARD_IMPORTS_MAX exceeded",
            now=1000.0,
        )
        c2 = store.record(
            "swarm-pr-998",
            "Wrapping factory startup in try/finally releases member writer locks",
            status="hypothesis",
            confidence=0.85,
            now=1010.0,
        )
        self.assertEqual(c1.prev_hash, exo_habitat.EpistemicStore.GENESIS)
        self.assertEqual(c2.prev_hash, c1.entry_hash)
        self.assertEqual(store.verify_chain(), (True, None))

        raw = sqlite3.connect(str(db))
        raw.execute(
            "UPDATE epistemic_claims SET statement = 'tampered' WHERE claim_id = 1"
        )
        raw.commit()
        raw.close()

        ok, broken_id = store.verify_chain()
        self.assertFalse(ok)
        self.assertEqual(broken_id, 1)
        store.close()

    def test_epistemic_ttl_staleness_and_falsification(self) -> None:
        store = exo_habitat.EpistemicStore(db_path=self.tmp_path / "ttl.sqlite")
        claim = store.record(
            "pr-57",
            "PR #57 is mergeable onto main",
            status="verified",
            ttl_seconds=3600,
            now=1000.0,
        )
        self.assertFalse(claim.is_stale(now=2000.0))
        self.assertTrue(claim.is_stale(now=5000.0))
        self.assertEqual(len(store.stale_claims(now=5000.0)), 1)

        refuted = store.falsify(
            claim.claim_id, "conflicted after #1230 landed", now=5100.0
        )
        self.assertEqual(refuted.status, "falsified")
        self.assertFalse(refuted.is_stale(now=999999.0))
        self.assertEqual(store.verify_chain(), (True, None))
        store.close()

    def test_proprioception_detects_thrash_context_and_d083_headroom(self) -> None:
        mon = exo_habitat.ProprioceptionMonitor(
            history_window=6, thrash_repeat_threshold=3
        )
        for _ in range(3):
            mon.record_tool_call("bash", {"cmd": "pytest -q"}, ok=False)

        snap = mon.sense(
            tokens_used=110000,
            token_budget=128000,
            spend_usd=9.0,
            daily_budget_usd=10.0,
            inward_core_imports=40,
        )
        self.assertTrue(snap.thrash_detected)
        self.assertEqual(snap.failure_streak, 3)
        self.assertEqual(snap.core_import_headroom, -1)
        self.assertGreater(snap.core_lines, 0)
        self.assertIn("BREAK_THRASH_LOOP", snap.advisories)
        self.assertIn("COMPACT_OR_HANDOFF", snap.advisories)
        self.assertIn("CONSERVE_MODEL_SPEND", snap.advisories)
        self.assertIn("MOVE_NEW_MODULES_OUTSIDE_CORE_D083", snap.advisories)
        self.assertNotIn("core_lines_left=", snap.format_hud())
        self.assertIn("[HABITAT HUD]", snap.format_hud())

    def test_sense_live_metrics_match_core_line_budget_gate(self) -> None:
        from exo.budget_scorecard import INWARD_IMPORTS_MAX, core_budget_snapshot
        from tests.integration.test_wave1_acceptance import test_core_line_budget

        test_core_line_budget()
        budget = core_budget_snapshot()
        snap = exo_habitat.ProprioceptionMonitor().sense()
        self.assertEqual(snap.core_import_headroom, INWARD_IMPORTS_MAX - budget.inward_imports)
        self.assertEqual(snap.core_lines, budget.core_lines)
        self.assertNotIn("MOVE_NEW_MODULES_OUTSIDE_CORE_D083", snap.advisories)

    def test_contract_graph_waves_collision_detection_and_cycle_guard(self) -> None:
        dag = exo_habitat.ContractGraph()
        dag.add_node(
            exo_habitat.ContractNode(
                node_id="contract-stub",
                seat="architect",
                interface_contract="Freeze GroupHost protocol in src/exo/group_types.py",
                touches_paths=("src/exo/group_types.py",),
            )
        )
        dag.add_node(
            exo_habitat.ContractNode(
                node_id="worker-api",
                seat="coder-a",
                interface_contract="Implement /api/swarm endpoints",
                depends_on=("contract-stub",),
                touches_paths=(
                    "plugins/exo-command/exo_command/api.py",
                    "plugins/exo-command/exo_command/static/chat.js",
                ),
            )
        )
        dag.add_node(
            exo_habitat.ContractNode(
                node_id="worker-ui",
                seat="coder-b",
                interface_contract="Implement Flow mode pins in web UI",
                depends_on=("contract-stub",),
                touches_paths=("plugins/exo-command/exo_command/static/chat.js",),
            )
        )
        self.assertEqual(
            dag.topological_waves(), [["contract-stub"], ["worker-api", "worker-ui"]]
        )
        dag.mark_done("contract-stub")
        ready_2, cols_2 = dag.ready_wave()
        self.assertEqual([n.node_id for n in ready_2], ["worker-api", "worker-ui"])
        self.assertEqual(
            cols_2,
            [("plugins/exo-command/exo_command/static/chat.js", "worker-api", "worker-ui")],
        )

    def test_probe_arena_and_plugin_register_hook(self) -> None:
        registered: dict[str, object] = {}
        db_file = str(self.tmp_path / "ctx.sqlite")

        class DummyCtx:
            def get_config(self, key: str, default: object = None) -> object:
                return db_file if key == "db_path" else default

            def register_command(
                self,
                name: str,
                fn: object,
                description: str = "",
                args_hint: str = "",
                **kwargs: object,
            ) -> None:
                registered[name] = fn

        from exo.dispatcher import subcommands

        runtime = exo_habitat.register(DummyCtx())
        habitat_cmds = {s.name for s in subcommands() if getattr(s, "plugin", "") == "exo-habitat"}
        self.assertEqual(habitat_cmds, {"sense", "claim", "audit", "wave"})
        self.assertIn("exo", registered)

        arena = exo_habitat.ProbeArena(store=runtime.store)
        bad = exo_habitat.CandidateProbe("in-core", "in core", 20, 0, False, 0.1, 800)
        good = exo_habitat.CandidateProbe("out-core", "outside core", 20, 0, True, 0.1, 800)
        res = arena.evaluate("d083", [bad, good], now=2000.0)
        assert res.winner is not None
        self.assertEqual(res.winner.probe_id, "out-core")
        self.assertIn("chain=ok", runtime.cmd_audit("d083"))
        runtime.store.close()


if __name__ == "__main__":
    unittest.main()
