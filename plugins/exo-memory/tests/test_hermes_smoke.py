"""Smoke test against the real Hermes MemoryProvider ABC (read-only checkout on sys.path).

Skips, with the reason, when the checkout is missing or its ABC does not import.
"""
import importlib.util
import inspect
import json
import sys

import pytest

from conftest import HERMES_DIR, HERMES_IMPORT_ERROR, PLUGIN_ROOT

pytestmark = pytest.mark.skipif(bool(HERMES_IMPORT_ERROR),
                                reason=f"Hermes MemoryProvider not importable from {HERMES_DIR}: {HERMES_IMPORT_ERROR}")


def load_plugin_like_hermes():
    """Import the plugin directory as a package under a synthetic name, as Hermes does."""
    name = "_exo_memory_smoke_plugin"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self):
        self.provider = None

    def register_memory_provider(self, provider):
        self.provider = provider


def test_register_against_real_abc(store, tmp_path, monkeypatch):
    from agent.memory_provider import MemoryProvider, RecallStatus

    assert not getattr(sys.modules["agent.memory_provider"], "__test_stub__", False)
    mod = load_plugin_like_hermes()
    ctx = Ctx()
    mod.register(ctx)
    p = ctx.provider
    assert isinstance(p, MemoryProvider)
    assert not inspect.isabstract(type(p))
    assert p.name == "exo-memory"
    assert p.pre_compress_checkpoint_api_version == 2
    # every hook Hermes may call exists with a compatible signature
    for hook in ("initialize", "system_prompt_block", "prefetch", "queue_prefetch", "sync_turn", "get_tool_schemas",
                 "handle_tool_call", "shutdown", "on_turn_start", "on_session_end", "on_session_switch",
                 "on_pre_compress", "on_delegation", "on_memory_write", "get_config_schema", "save_config",
                 "recall_status", "identity_signature", "backup_paths", "unavailable_reason"):
        assert callable(getattr(p, hook))
    assert "turn_author" in inspect.signature(p.sync_turn).parameters
    assert "metadata" in inspect.signature(p.on_memory_write).parameters

    monkeypatch.setenv("EXO_STORE", str(store.root))
    monkeypatch.setenv("EXO_PRINCIPAL", "pat")
    assert p.is_available(), p.unavailable_reason()
    p.save_config({"compact_enabled": False}, str(tmp_path / "hermes-home"))
    p.initialize("smoke-1", hermes_home=str(tmp_path / "hermes-home"), platform="cli", agent_context="primary")
    try:
        assert p.system_prompt_block().startswith("# Principal pack")
        p.on_turn_start(1, "Always put numbers in a table.", author_id=None, author_name=None, author_is_bot=False)
        p.sync_turn("Always put numbers in a table.", "Noted.", session_id="smoke-1",
                    messages=[{"role": "user", "content": "Always put numbers in a table."},
                              {"role": "assistant", "content": "Noted."}])
        out = json.loads(p.handle_tool_call("exo_recall", {"query": "numbers table"}))
        assert "result" in out
        status = p.recall_status()
        assert status is None or isinstance(status, RecallStatus)
        block = p.on_pre_compress([{"role": "user", "content": "Always put numbers in a table."}],
                                  require_checkpoint=True)
        assert "Always put numbers in a table." in block
    finally:
        p.shutdown()


def test_tool_schemas_pass_hermes_normalizer(provider):
    try:
        from agent.memory_manager import normalize_tool_schema
    except Exception as exc:  # heavy optional imports in the Hermes checkout
        pytest.skip(f"agent.memory_manager not importable here: {type(exc).__name__}: {exc}")
    for schema in provider.get_tool_schemas():
        assert normalize_tool_schema(schema) is schema
