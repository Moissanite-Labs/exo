"""The Hermes plugin contracts Exo depends on.

If one of these fails on a new tag, do not move the pin. Either adapt the Exo
plugin or raise the break upstream. See docs/PINNING.md.
"""

from __future__ import annotations

import inspect

import pytest

MEMORY_PROVIDER_METHODS = [
    "name", "is_available", "initialize", "system_prompt_block", "prefetch",
    "queue_prefetch", "sync_turn", "get_tool_schemas", "handle_tool_call", "shutdown",
    "on_turn_start", "on_session_end", "on_session_switch", "on_pre_compress",
    "on_delegation", "on_memory_write", "backup_paths",
]

CONTEXT_ENGINE_METHODS = [
    "name", "should_compress", "compress", "update_from_response", "select_context",
    "on_turn_complete",
]

PLUGIN_CONTEXT_REGISTRARS = [
    "register_memory_provider", "register_context_engine", "register_hook", "register_tool",
    "register_command", "register_platform", "register_system_prompt_section",
    "register_approval_transport", "register_middleware",
]

# Hook names as spelled in hermes_cli.plugins.VALID_HOOKS. Exo plugins use these:
# exo-gate (tool calls, approvals), exo-memory and the ledger (session lifecycle,
# LLM calls, subagents, skills), exo-router (auxiliary calls), exo-command (kanban).
EXO_HOOKS = [
    "pre_tool_call", "post_tool_call",
    "pre_llm_call", "post_llm_call",
    "pre_auxiliary_call", "post_auxiliary_call",
    "on_session_start", "on_session_end", "on_session_finalize", "on_session_reset",
    "subagent_start", "subagent_stop",
    "on_skill_lifecycle",
    "pre_approval_request", "post_approval_response",
    "pre_gateway_dispatch",
    "kanban_task_completed", "kanban_task_blocked",
]

# Middleware kinds Exo may register (hermes_cli.middleware.VALID_MIDDLEWARE).
EXO_MIDDLEWARE = ["tool_request", "llm_request"]


@pytest.fixture(scope="module")
def memory_provider_cls(hermes_import):
    return hermes_import("agent.memory_provider").MemoryProvider


@pytest.fixture(scope="module")
def context_engine_cls(hermes_import):
    return hermes_import("agent.context_engine").ContextEngine


@pytest.fixture(scope="module")
def plugins_mod(hermes_import):
    return hermes_import("hermes_cli.plugins")


@pytest.mark.parametrize("method", MEMORY_PROVIDER_METHODS)
def test_memory_provider_method(memory_provider_cls, method):
    assert hasattr(memory_provider_cls, method), f"MemoryProvider.{method} is gone"
    attr = inspect.getattr_static(memory_provider_cls, method)
    assert callable(attr) or isinstance(attr, property), f"MemoryProvider.{method} is not callable"


@pytest.mark.parametrize("method", CONTEXT_ENGINE_METHODS)
def test_context_engine_method(context_engine_cls, method):
    assert hasattr(context_engine_cls, method), f"ContextEngine.{method} is gone"
    attr = inspect.getattr_static(context_engine_cls, method)
    assert callable(attr) or isinstance(attr, property), f"ContextEngine.{method} is not callable"


def test_memory_provider_abstract_set_is_known(memory_provider_cls):
    """A new abstract method would break every Exo memory provider at instantiation."""
    allowed = set(MEMORY_PROVIDER_METHODS)
    extra = set(memory_provider_cls.__abstractmethods__) - allowed
    assert not extra, f"MemoryProvider gained abstract methods Exo does not implement: {sorted(extra)}"


def test_context_engine_abstract_set_is_known(context_engine_cls):
    allowed = set(CONTEXT_ENGINE_METHODS)
    extra = set(context_engine_cls.__abstractmethods__) - allowed
    assert not extra, f"ContextEngine gained abstract methods Exo does not implement: {sorted(extra)}"


@pytest.mark.parametrize("registrar", PLUGIN_CONTEXT_REGISTRARS)
def test_plugin_context_registrar(plugins_mod, registrar):
    ctx_cls = plugins_mod.PluginContext
    fn = getattr(ctx_cls, registrar, None)
    assert callable(fn), f"PluginContext.{registrar} is gone"


@pytest.mark.parametrize(
    "registrar,params",
    [
        ("register_hook", ["hook_name", "callback"]),
        ("register_middleware", ["kind", "callback"]),
        ("register_memory_provider", ["provider"]),
        ("register_context_engine", ["engine"]),
        ("register_approval_transport", ["name", "present_fn"]),
    ],
)
def test_plugin_context_signature(plugins_mod, registrar, params):
    sig = inspect.signature(getattr(plugins_mod.PluginContext, registrar))
    names = [p for p in sig.parameters if p != "self"]
    assert names[: len(params)] == params, f"{registrar}{sig} changed its leading parameters"


@pytest.mark.parametrize("hook", EXO_HOOKS)
def test_valid_hook(plugins_mod, hook):
    assert hook in plugins_mod.VALID_HOOKS, f"hook {hook!r} is no longer in VALID_HOOKS"


@pytest.mark.parametrize("kind", EXO_MIDDLEWARE)
def test_valid_middleware(hermes_import, kind):
    mw = hermes_import("hermes_cli.middleware")
    assert kind in mw.VALID_MIDDLEWARE, f"middleware kind {kind!r} is no longer valid"


def test_module_level_dispatch_helpers(plugins_mod):
    """Exo calls these from plugins and tests to fire or inspect hooks."""
    for name in ("invoke_hook", "has_hook", "get_plugin_manager"):
        assert callable(getattr(plugins_mod, name, None)), f"hermes_cli.plugins.{name} is gone"


def test_minimal_memory_provider_instantiates(memory_provider_cls):
    """A provider implementing only Exo's method set must be constructible."""

    class _Probe(memory_provider_cls):
        @property
        def name(self):
            return "exo-contract-probe"

        def is_available(self):
            return True

        def initialize(self, session_id, **kwargs):
            return None

        def get_tool_schemas(self):
            return []

    probe = _Probe()
    assert probe.name == "exo-contract-probe"
    assert probe.get_tool_schemas() == []
    assert isinstance(probe.backup_paths(), list)
