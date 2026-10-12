import asyncio

import pytest

from exo import dispatcher as d


@pytest.fixture(autouse=True)
def clean():
    d.reset()
    yield
    d.reset()


def test_routing_passes_rest_verbatim():
    seen = []
    d.register_subcommand("branch", lambda rest: seen.append(rest) or f"branched {rest}",
                          "Write a handoff.", plugin="exo-context", args_hint="<topic>")
    assert d.dispatch("branch  my topic here") == "branched my topic here"
    assert d.dispatch("BRANCH x") == "branched x"
    assert seen == ["my topic here", "x"]


def test_help_lists_subcommands_and_details():
    d.register_subcommand("resume", lambda r: r, "Load a handoff.\n\nLonger text.", plugin="exo-context",
                          args_hint="<topic>")
    d.register_subcommand("recall", lambda r: r, "Search memory.", plugin="exo-memory", aliases=["r"])
    top = d.dispatch("")
    assert top.startswith("Usage: /exo")
    assert "/exo resume <topic>" in top and "Load a handoff." in top and "(exo-memory)" in top
    assert "Longer text." not in top
    assert d.dispatch("help") == top
    detail = d.dispatch("help resume")
    assert "Longer text." in detail and "From: exo-context" in detail
    assert d.dispatch("resume --help") == detail
    assert d.dispatch("r hello") == "hello"
    assert "Aliases: r" in d.help_text("recall")


def test_unknown_suggests():
    d.register_subcommand("handoff", lambda r: "ok", plugin="exo-context")
    out = d.dispatch("handof")
    assert "Unknown subcommand" in out and '"handoff"' in out


def test_empty_registry_help():
    assert "no subcommands" in d.dispatch("help")


def test_conflicts_and_reload():
    d.register_subcommand("status", lambda r: "a", plugin="exo-command")
    d.register_subcommand("status", lambda r: "b", plugin="exo-command")   # reload: same plugin
    assert d.dispatch("status") == "b"
    with pytest.raises(ValueError):
        d.register_subcommand("status", lambda r: "c", plugin="exo-gate")
    d.register_subcommand("status", lambda r: "c", plugin="exo-gate", replace=True)
    assert d.dispatch("status") == "c"
    with pytest.raises(ValueError):
        d.register_subcommand("help", lambda r: "x")
    with pytest.raises(ValueError):
        d.register_subcommand("two words", lambda r: "x")
    assert d.unregister_plugin("exo-gate") == 1
    assert "Unknown" in d.dispatch("status")


def test_handler_errors_are_contained_and_async_passes_through():
    def boom(rest):
        raise RuntimeError("nope")

    async def later(rest):
        return f"async {rest}"

    d.register_subcommand("boom", boom, plugin="p")
    d.register_subcommand("later", later, plugin="p")
    assert "failed: RuntimeError: nope" in d.dispatch("boom")
    assert asyncio.run(d.dispatch("later now")) == "async now"


def test_register_root_once():
    class Ctx:
        def __init__(self):
            self.commands = {}

        def register_command(self, name, handler, description="", args_hint=""):
            self.commands[name] = handler

    a, b = Ctx(), Ctx()
    assert d.register_root(a) is True
    assert d.register_root(b) is False and "exo" not in b.commands
    d.register_subcommand("ping", lambda r: "pong", plugin="t")
    assert a.commands["exo"]("ping") == "pong"
    assert d.register_root(b, force=True) and b.commands["exo"]("help").startswith("Usage:")
