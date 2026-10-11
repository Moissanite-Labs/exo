"""Smoke test against the real ContextEngine ABC from a Hermes checkout (read only)."""
import importlib.util
import sys

import pytest

from conftest import HERMES_DIR, PLUGIN_DIR

try:
    from agent.context_engine import ContextEngine as RealContextEngine
except Exception as e:  # pragma: no cover
    RealContextEngine = None
    REASON = f"Hermes ContextEngine not importable from {HERMES_DIR}: {e}"
else:
    REASON = ""


@pytest.mark.skipif(RealContextEngine is None, reason=REASON or "hermes missing")
def test_engine_implements_real_abc():
    from exo_context.engine import HAVE_HERMES, ExoContextEngine
    assert HAVE_HERMES
    assert issubclass(ExoContextEngine, RealContextEngine)
    eng = ExoContextEngine()
    assert eng.name == "exo-context"
    assert eng.get_status()["compression_count"] == 0
    assert eng.should_compress() is False


@pytest.mark.skipif(RealContextEngine is None, reason=REASON or "hermes missing")
def test_plugin_register_entry():
    spec = importlib.util.spec_from_file_location(
        "exo_context_plugin", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exo_context_plugin"] = mod
    spec.loader.exec_module(mod)

    class Ctx:
        engine, commands = None, {}

        def register_context_engine(self, e):
            self.engine = e

        def register_command(self, name, handler, description="", args_hint=""):
            self.commands[name] = handler

    ctx = Ctx()
    mod.register(ctx)
    assert isinstance(ctx.engine, RealContextEngine)
    assert ctx.commands["exo"]("help").startswith("Usage:")
