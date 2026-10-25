"""Codex patch move destinations use the same path checks as other patch headers (#528).

These tests classify complete tool calls; they never apply the patch or write the named stores.
"""
import pytest

from exo import gate


@pytest.fixture(autouse=True)
def layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in (".exo/policies", ".exo/state", ".ssh", "project"):
        (home / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def move(destination, source="src.txt", tool="apply_patch"):
    return {"tool": tool, "args": {"patch": "*** Begin Patch\n"
            f"*** Update File: {source}\n*** Move to: {destination}\n@@\n-before\n+after\n*** End Patch\n"}}


@pytest.mark.parametrize("destination", [
    "~/.exo/policies/default.yaml", "$HOME/.exo/state/gate-grants.json",
    "$EXO_HOME/plugins/p/plugin.py", ".exo/policies/default.yaml", "policies/gates.yaml",
    "~/.exo/{policies,notes}/default.yaml", "~/.exo/polic{i..i}es/default.yaml",
])
@pytest.mark.parametrize("tool", ["apply_patch", "patch"])
def test_a_move_destination_in_the_store_asks(destination, tool):
    c = gate.classify(move(destination, tool=tool))
    assert (c.category, c.target, gate.decide(c).outcome) == ("store_write", destination, "ask")
    assert gate.decide(c, {"gates": {"deny": ["store_write"]}}).outcome == "deny"


@pytest.mark.parametrize("destination", ["~/.ssh/config", "$HOME/.ssh/id_rsa", ".env", "~/{.ssh,notes}/config"])
def test_a_move_destination_in_a_secret_store_asks(destination):
    c = gate.classify(move(destination))
    assert (c.category, c.target, gate.decide(c).outcome) == ("credentials", destination, "ask")
    assert gate.decide(c, {"gates": {"deny": ["credentials"]}}).outcome == "deny"


@pytest.mark.parametrize("destination,category", [(".exo/policies/default.yaml", "store_write"),
                                                 (".ssh/config", "credentials")])
def test_absolute_destinations_and_absolute_links_count(destination, category, layout):
    direct = layout / destination
    link = layout / "project" / "link"
    link.symlink_to(direct.parent)
    for name in (direct, link / direct.name):
        c = gate.classify(move(str(name)))
        assert (c.category, gate.decide(c).outcome) == (category, "ask")


@pytest.mark.parametrize("destination", ["out.txt", "docs/notes.md", "~/project/policies/example.yaml"])
def test_an_ordinary_move_stays_a_local_write(destination):
    c = gate.classify(move(destination))
    assert (c.category, c.target, gate.decide(c).outcome) == ("local_write", "src.txt", "allow")


@pytest.mark.parametrize("source,category", [("~/.exo/policies/gates.yaml", "store_write"),
                                            ("~/.ssh/config", "credentials")])
def test_a_protected_source_is_still_protected(source, category):
    c = gate.classify(move("out.txt", source))
    assert (c.category, c.target, gate.decide(c).outcome) == (category, source, "ask")


@pytest.mark.parametrize("destination", ["~/.exo/policies/gates.yaml", "~/.ssh/config"])
def test_a_protected_move_keeps_the_source_and_destination_write_policies(destination):
    c = gate.classify(move(destination))
    for target in ("src.txt", destination):
        policy = {"gates": {"deny": [f"target:{target}"]}}
        assert gate.decide(c, policy).outcome == "deny"
    assert gate.decide(c, {"gates": {"categories": {"local_write": "deny"}}}).outcome == "deny"


def test_headers_in_patch_content_are_not_move_headers():
    action = move("out.txt")
    action["args"]["patch"] = action["args"]["patch"].replace("+after", "+*** Move to: ~/.exo/policies/gates.yaml")
    assert gate.classify(action).category == "local_write"


def test_all_move_destinations_are_read():
    action = move("out.txt")
    action["args"]["patch"] = action["args"]["patch"].replace("*** End Patch", "*** Update File: other.txt\n"
        "*** Move to: ~/.exo/policies/gates.yaml\n@@\n-before\n+after\n*** End Patch")
    c = gate.classify(action)
    assert (c.category, c.target) == ("store_write", "~/.exo/policies/gates.yaml")


@pytest.mark.parametrize("destination", ["pdir/default.yaml", "sdir/../policies/default.yaml", "kdir/id_rsa"])
def test_relative_aliases_use_the_existing_patch_path_rules(destination, layout, monkeypatch):
    """#527 owns relative store-write resolution. Move-to must match Add File, including that existing limit."""
    work = layout / "project"
    (work / "pdir").symlink_to(layout / ".exo/policies")
    (work / "sdir").symlink_to(layout / ".exo/state")
    (work / "kdir").symlink_to(layout / ".ssh")
    monkeypatch.chdir(work)
    moved = gate.classify(move(destination))
    added = gate.classify({"tool": "apply_patch", "args": {"patch": f"*** Add File: {destination}\n+x\n"}})
    assert moved.category == added.category
    assert gate.decide(moved).outcome == gate.decide(added).outcome
