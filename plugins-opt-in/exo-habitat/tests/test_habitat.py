"""Unit and integration test suite for exo-habitat (pytest & unittest compatible)."""
from __future__ import annotations

import importlib.util
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
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
        try:
            from exo import dispatcher

            dispatcher.reset()
        except ImportError:
            pass
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
        self.assertEqual(store.stale_claims(now=999999.0), [])
        with self.assertRaises(ValueError):
            store.falsify(claim.claim_id, "again", now=5200.0)
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

    def test_budget_scorecard_repo_root_is_checkout_not_site_packages(self) -> None:
        from exo.budget_scorecard import find_repo_root

        root = find_repo_root()
        self.assertTrue((root / "src" / "exo" / "core").is_dir())

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
        self.assertFalse(Path(db_file).exists())
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
        runtime.close()

    def test_contract_path_containment_collision(self) -> None:
        nodes = [
            exo_habitat.ContractNode(
                node_id="a",
                seat="s1",
                interface_contract="touch parent",
                touches_paths=("plugins/exo-command",),
            ),
            exo_habitat.ContractNode(
                node_id="b",
                seat="s2",
                interface_contract="touch child",
                touches_paths=("plugins/exo-command/exo_command/api.py",),
            ),
        ]
        cols = exo_habitat.ContractGraph.detect_path_collisions(nodes)
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0][1:], ("a", "b"))

    def test_probe_arena_duplicate_probe_id_raises(self) -> None:
        store = exo_habitat.EpistemicStore(db_path=self.tmp_path / "dup.sqlite")
        arena = exo_habitat.ProbeArena(store=store)
        p1 = exo_habitat.CandidateProbe("same", "h1", 1, 0, True, 0.1, 100)
        p2 = exo_habitat.CandidateProbe("same", "h2", 1, 0, True, 0.1, 100)
        with self.assertRaises(ValueError):
            arena.evaluate("t", [p1, p2])
        store.close()

    def test_probe_arena_failing_clone_not_verified(self) -> None:
        store = exo_habitat.EpistemicStore(db_path=self.tmp_path / "clone.sqlite")
        arena = exo_habitat.ProbeArena(store=store)
        bad = exo_habitat.CandidateProbe("winner", "bad", 0, 1, True, 0.1, 100)
        good = exo_habitat.CandidateProbe("winner", "good", 5, 0, True, 0.1, 100)
        with self.assertRaises(ValueError):
            arena.evaluate("t", [bad, good])
        store.close()

    def test_cmd_claim_escaped_pipe(self) -> None:
        db = self.tmp_path / "claim.sqlite"
        runtime = exo_habitat.HabitatRuntime(db_path=str(db))
        out = runtime.cmd_claim(r"topic | stmt with \| pipe | falsifier bit")
        self.assertIn("recorded #", out)
        claims = runtime.store.list_claims("topic")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].statement, "stmt with | pipe")
        self.assertEqual(claims[0].falsifier, "falsifier bit")
        runtime.close()

    def test_audit_superseded_tag_after_falsify(self) -> None:
        store = exo_habitat.EpistemicStore(db_path=self.tmp_path / "audit.sqlite")
        runtime = exo_habitat.HabitatRuntime(db_path=str(self.tmp_path / "audit.sqlite"))
        c = store.record("t", "original", status="verified", ttl_seconds=3600, now=1000.0)
        store.falsify(c.claim_id, "nope", now=2000.0)
        audit = runtime.cmd_audit("t")
        self.assertIn("[SUPERSEDED]", audit)
        runtime.close()

    def test_epistemic_concurrent_record_preserves_chain(self) -> None:
        db = self.tmp_path / "conc.sqlite"
        store = exo_habitat.EpistemicStore(db_path=db)
        barrier = threading.Barrier(8)

        def worker(i: int) -> None:
            barrier.wait()
            store.record(f"topic-{i}", f"stmt-{i}", now=1000.0 + i)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(8)))
        self.assertEqual(store.verify_chain(), (True, None))
        ids = {c.claim_id for c in store.list_claims()}
        self.assertEqual(len(ids), 8)
        store.close()

    def test_epistemic_concurrent_dual_connection_no_fork(self) -> None:
        db = self.tmp_path / "dual.sqlite"
        seed = exo_habitat.EpistemicStore(db_path=db)
        seed.record("seed", "genesis child", now=1.0)
        seed.close()
        s1 = exo_habitat.EpistemicStore(db_path=db)
        s2 = exo_habitat.EpistemicStore(db_path=db)
        barrier = threading.Barrier(2)

        def run_a() -> None:
            barrier.wait()
            for i in range(10):
                s1.record("a", f"a{i}", now=10.0 + i)

        def run_b() -> None:
            barrier.wait()
            for i in range(10):
                s2.record("b", f"b{i}", now=20.0 + i)

        t1 = threading.Thread(target=run_a)
        t2 = threading.Thread(target=run_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        checker = exo_habitat.EpistemicStore(db_path=db)
        self.assertEqual(checker.verify_chain(), (True, None))
        s1.close()
        s2.close()
        checker.close()

    def test_default_db_path_honors_exo_principal(self) -> None:
        home = self.tmp_path / "exo_home"
        home.mkdir()
        prev_home = os.environ.get("EXO_HOME")
        prev_principal = os.environ.get("EXO_PRINCIPAL")
        prev_db = os.environ.get("EXO_HABITAT_DB")
        try:
            os.environ["EXO_HOME"] = str(home)
            os.environ["EXO_PRINCIPAL"] = "agent-1"
            os.environ.pop("EXO_HABITAT_DB", None)
            store = exo_habitat.EpistemicStore()
            expected = home / "principals" / "agent-1" / "habitat.sqlite"
            self.assertEqual(store.db_path, expected)
            store.record("t", "s", now=1.0)
            self.assertTrue(expected.is_file())
            mode = stat.S_IMODE(expected.stat().st_mode)
            self.assertEqual(mode, 0o600)
            store.close()
        finally:
            if prev_home is None:
                os.environ.pop("EXO_HOME", None)
            else:
                os.environ["EXO_HOME"] = prev_home
            if prev_principal is None:
                os.environ.pop("EXO_PRINCIPAL", None)
            else:
                os.environ["EXO_PRINCIPAL"] = prev_principal
            if prev_db is None:
                os.environ.pop("EXO_HABITAT_DB", None)
            else:
                os.environ["EXO_HABITAT_DB"] = prev_db

    def test_post_tool_call_wires_monitor(self) -> None:
        runtime = exo_habitat.HabitatRuntime(db_path=str(self.tmp_path / "hook.sqlite"))
        runtime.on_post_tool_call("bash", {"cmd": "x"}, result="error: failed")
        self.assertEqual(runtime.monitor._failure_streak, 1)
        runtime.on_post_tool_call("bash", {"cmd": "y"}, result="ok")
        self.assertEqual(runtime.monitor._failure_streak, 0)
        runtime.close()

    def test_register_undo_closes_store(self) -> None:
        db_file = str(self.tmp_path / "undo.sqlite")
        undo: list[object] = []

        class Ctx:
            def get_config(self, key: str, default: object = None) -> object:
                return db_file if key == "db_path" else default

            def register_hook(self, name: str, cb: object) -> None:
                self.hook = (name, cb)

            def register_command(self, *args: object, **kwargs: object) -> None:
                pass

            _undo = undo

        runtime = exo_habitat.register(Ctx())
        _ = runtime.store
        self.assertTrue(Path(db_file).exists())
        for fn in undo:
            if callable(fn):
                fn()
        self.assertIsNone(runtime._store)


if __name__ == "__main__":
    unittest.main()
