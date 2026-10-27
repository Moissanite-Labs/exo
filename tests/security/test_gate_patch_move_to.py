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


BARE_HEADERS = ["*** Move to:", "*** Move to:   ", "***Move to:\t", "*** Add File:", "*** Delete File:",
                "*** Update File:", "*** Move File: a ->", "*** Move File: ->"]


@pytest.mark.parametrize("bare", BARE_HEADERS)
@pytest.mark.parametrize("path,category", [(".ssh/config", "credentials"), (".env", "credentials"),
                                           (".exo/keys/k", "credentials"), (".exo/policies/default.yaml", "store_write"),
                                           ("policies/gates.yaml", "store_write")])
def test_a_header_with_no_path_does_not_hide_the_next_header(bare, path, category, layout, monkeypatch):
    """A patch tool skips a header with no path and applies the next one. Read with `\\s`, the bare header took the
    next header line as its path, so that write was never read (#548 follow-up)."""
    monkeypatch.chdir(layout)
    patch = f"*** Begin Patch\n{bare}\n*** Add File: {path}\n+x\n*** End Patch\n"
    c = gate.classify({"tool": "apply_patch", "args": {"patch": patch}})
    assert (c.category, c.target, gate.decide(c).outcome) == (category, path, "ask")
    assert gate.decide(c, {"gates": {"deny": [category]}}).outcome == "deny"


@pytest.mark.parametrize("path,category", [(".ssh/config", "credentials"), (".env", "credentials"),
                                           (".exo/keys/k", "credentials"), (".exo/policies/default.yaml", "store_write"),
                                           ("policies/gates.yaml", "store_write")])
def test_a_header_with_no_path_does_not_hide_the_next_header_under_the_patch_tool(path, category, layout, monkeypatch):
    """The bare-header rows above under the `patch` tool name, which the classify branch reads with the same
    `_patch_paths` call as `apply_patch` (#548 follow-up)."""
    monkeypatch.chdir(layout)
    patch = f"*** Begin Patch\n*** Move to:\n*** Add File: {path}\n+x\n*** End Patch\n"
    c = gate.classify({"tool": "patch", "args": {"patch": patch}})
    assert (c.category, c.target, gate.decide(c).outcome) == (category, path, "ask")
    assert gate.decide(c, {"gates": {"deny": [category]}}).outcome == "deny"


@pytest.mark.parametrize("patch,category,target,outcome", [
    # a path the reading across a line found still counts
    ("*** Update File:\n.ssh/config\n@@\n-a\n+b\n", "credentials", ".ssh/config", "ask"),
    # an ordinary write keeps the target it had: the first path read as before
    ("*** Move File: a ->\nnotes.txt\n", "local_write", "a", "allow"),
    ("*** Move to:\n*** Add File: notes.txt\n+x\n", "local_write", "*** Add File: notes.txt", "allow"),
])
def test_the_reading_across_a_line_is_kept(patch, category, target, outcome):
    """The call's target stays the one main read, so an entry that matched it still does. The path read on its own
    line is a part (below)."""
    c = gate.classify({"tool": "apply_patch", "args": {"patch": patch}})
    assert (c.category, c.target, gate.decide(c).outcome) == (category, target, outcome)


@pytest.mark.parametrize("bare", BARE_HEADERS)
def test_a_bare_header_does_not_hide_a_local_write_from_its_target_entries(bare):
    """Each file a local write of more than one file names is a part, so an exact target entry reaches the path that
    only the reading on one line found (#559 review)."""
    patch = f"*** Begin Patch\n{bare}\n*** Add File: notes.txt\n+x\n*** End Patch\n"
    c = gate.classify({"tool": "apply_patch", "args": {"patch": patch}})
    assert c.category == "local_write" and "notes.txt" in [p.target for p in c.parts]
    assert gate.decide(c).outcome == "allow"
    assert gate.decide(c, {"gates": {"deny": ["target:notes.txt"]}}).outcome == "deny"
    d = gate.decide(c, {"gates": {"reserved": ["target:notes.txt"]}})
    assert d.outcome == "ask" and d.reserved


@pytest.mark.parametrize("patch", [
    "*** Update File: a.txt\n@@\n-a\n+b\n*** Add File: notes.txt\n+x\n",
    "*** Update File: a.txt\n*** Move to: notes.txt\n@@\n-a\n+b\n",
])
def test_each_file_of_a_local_write_meets_its_target_entries(patch):
    c = gate.classify({"tool": "apply_patch", "args": {"patch": patch}})
    assert (c.category, c.target) == ("local_write", "a.txt")
    assert gate.decide(c, {"gates": {"deny": ["target:notes.txt"]}}).outcome == "deny"
    assert gate.decide(c, {"gates": {"deny": ["target:a.txt"]}}).outcome == "deny"


def test_a_crlf_patch_reads_each_path_without_the_carriage_return(layout, monkeypatch):
    monkeypatch.chdir(layout)
    patch = "*** Begin Patch\r\n*** Move to:\r\n*** Add File: .ssh/config\r\n+x\r\n*** End Patch\r\n"
    c = gate.classify({"tool": "apply_patch", "args": {"patch": patch}})
    assert (c.category, c.target, gate.decide(c).outcome) == ("credentials", ".ssh/config", "ask")
    assert all(not p.target.endswith("\r") for p in c.parts)
