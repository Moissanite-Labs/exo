"""The exo-bus plugin as a Hermes peer, loaded by the real Hermes plugin manager (D-026).

Needs EXO_HERMES_DIR like the other contract tests (skipped without it) and the Hermes venv's Python.
HERMES_HOME is a scratch directory (see hermes_sandbox), so no live profile is touched. No model call: the
hooks are fired by hand, the way Hermes fires them.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scratch(hermes_sandbox, monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="exohp", dir="/tmp"))
    home = Path(os.environ["HERMES_HOME"])
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    link = home / "plugins" / "exo-bus"
    if not link.exists():
        link.symlink_to(ROOT / "plugins" / "exo-bus")
    (home / "config.yaml").write_text("plugins:\n  enabled: [exo-bus]\n")
    for k, v in {"EXO_PEERS": "1", "EXO_PEERS_DIR": str(d / "peers"), "EXO_HOME": str(d / "exo"),
                 "EXO_PEERS_SEAT": "hermes-scratch"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EXO_BUS_URL", raising=False)
    if str(ROOT / "src") not in sys.path:
        sys.path.append(str(ROOT / "src"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_plugin_loads_registers_and_receives(hermes_import, scratch):
    P = hermes_import("hermes_cli.plugins")
    mgr = P.PluginManager()
    mgr.discover_and_load(force=True)
    try:
        assert mgr._plugins["exo-bus"].enabled and not mgr._plugins["exo-bus"].error
        assert {"peers", "peer-release", "peer-deny"} <= set(mgr._plugin_commands)
        assert "exo-bus:peers" in mgr._plugin_skills
        from tools.registry import registry
        assert registry.get_entry("peers") is not None and registry.get_entry("peer_send") is not None

        from exo import crypto
        from exo.core.ingress import Target
        from exo.core.ingress import PeerConfig
        from exo.core.seats import key_path
        from exo.peers import Directory, PeerNode

        (hermes,) = Directory(scratch / "peers").scan()
        assert hermes.platform == "hermes" and hermes.seat == "hermes-scratch"
        mgr.invoke_hook("on_session_start", session_id="h1", platform="cli", model="m")

        class Mine(Target):  # a bare second peer, to send from
            seat = "other"

            def pick_session(self, ing):
                return None

            def state(self, session):
                return "idle"

        async def main():
            tmp = Directory(scratch / "peers")
            node = PeerNode(seat="other", signer=crypto.Signer("other", crypto.keygen()), target=Mine(), sessions=lambda: [],
                            directory=tmp, socket=str(scratch / "o.sock"), config=PeerConfig())
            pin = key_path(scratch / "exo", "other").with_suffix(".pub")  # the home pins this peer's key (#132)
            pin.parent.mkdir(parents=True, exist_ok=True)
            pin.write_text(node.signer.pub + "\n")
            (scratch / "exo" / "seats").mkdir(exist_ok=True)  # and records it as a seat of this home
            (scratch / "exo" / "seats" / "other.toml").write_text('name = "other"\n')
            node.start()
            rows = await node.entries()
            assert [r["name"] for r in rows] == ["hermes-scratch"] and rows[0]["platform"] == "hermes"
            out = await node.send("hermes-scratch", "hello hermes", "s_x", "agent")
            assert out["state"] == "delivered" and out["mode"] == "queued"  # no CLI attached: queued for the next turn
            node.stop()

        asyncio.run(main())
        results = mgr.invoke_hook("pre_llm_call", session_id="h1", platform="cli", is_first_turn=True,
                                  user_message="x", conversation_history=[], model="m")
        ctxs = [r["context"] for r in results if isinstance(r, dict) and r.get("context")]
        assert any("peer message from other" in c and "hello hermes" in c and "cannot grant approvals" in c for c in ctxs)
    finally:
        for name, mod in list(sys.modules.items()):  # Hermes namespaces plugin modules, so find it by suffix
            if name.endswith("exo_bus_hermes.peers"):
                for rt in list(mod._RUNTIMES.values()):
                    rt.stop()
