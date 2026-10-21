"""#340: a relative link to the Exo home is judged by where it leads, from the tool's folder.

Fake files only, in a temporary HOME. Every check classifies; nothing is run. The session
folder is ~/proj; the store-write floor is checked too.
"""
import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES
ALLOW_STORE = {"gates": {"categories": {"store_write": "allow"}}}


def outcome(tool, args, cwd, policy=POL):
    c = gate.classify({"tool": tool, "args": args}, cwd=cwd)
    return c.category, gate.decide(c, policy).outcome


@pytest.fixture
def tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (".exo/policies", ".exo/state", ".exo/keys", "proj", "proj/notes"):
        (home / d).mkdir(parents=True)
    for f in (".exo/policies/default.yaml", ".exo/state/approval-grants.json", ".exo/keys/seat"):
        (home / f).write_text("fake\n")
    proj = home / "proj"
    (proj / "pdir").symlink_to(home / ".exo/policies", target_is_directory=True)
    (proj / "pol.yaml").symlink_to(home / ".exo/policies/default.yaml")
    (proj / "rel").symlink_to("../.exo", target_is_directory=True)   # the link the ln rows make
    for name in ("EXO_HOME", "EXO_PRINCIPAL_STORE", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)          # the gate's own folder is not the tool's
    return proj


@pytest.mark.parametrize("tool,args", [
    # the issue's rows: allowed on main 5f434b15, store_write now
    ("bash", {"command": "ln -s ../.exo rel2"}),
    ("bash", {"command": "ln -s ../.exo/ rel2"}),
    ("write_file", {"path": "rel/policies/default.yaml", "content": "x"}),
    ("edit_file", {"path": "rel/policies/default.yaml", "patch": "x"}),
    ("write_file", {"path": "rel/policies/new.yaml", "content": "x"}),
    ("bash", {"command": "cp x rel/policies/default.yaml"}),
    ("bash", {"command": "echo x > rel/policies/default.yaml"}),
    ("write_file", {"path": "pdir/default.yaml", "content": "x"}),
    ("bash", {"command": "cp x pdir/default.yaml"}),
    ("bash", {"command": "cp x pol.yaml"}),
])
def test_a_relative_link_into_the_store_asks(tree, tool, args):
    assert outcome(tool, args, cwd=str(tree)) == ("store_write", "ask")


@pytest.mark.parametrize("tool,args,expected", [
    # controls: unchanged from the issue's table
    ("bash", {"command": "ln -s ~/.exo rel2"}, ("store_write", "ask")),
    ("bash", {"command": "ln -s ../.exo/policies rel2"}, ("store_write", "ask")),
    ("write_file", {"path": "rel/state/approval-grants.json", "content": "x"}, ("store_write", "ask")),
    ("bash", {"command": "cat rel/keys/seat"}, ("credentials", "ask")),
])
def test_controls(tree, tool, args, expected):
    assert outcome(tool, args, cwd=str(tree)) == expected


def test_absolute_alias_controls(tree):
    assert outcome("write_file", {"path": str(tree / "pdir/default.yaml"), "content": "x"},
                   cwd=str(tree)) == ("store_write", "ask")
    assert outcome("write_file", {"path": str(tree / "pol.yaml"), "content": "x"},
                   cwd=str(tree)) == ("store_write", "ask")


def test_store_write_floor_still_asks(tree):
    """store_write is a floor category: even allowed in policy, the write asks."""
    assert outcome("write_file", {"path": "rel/policies/default.yaml", "content": "x"},
                   cwd=str(tree), policy=ALLOW_STORE) == ("store_write", "ask")


@pytest.mark.parametrize("tool,args,expected", [
    # a plain project keeps its classes
    ("write_file", {"path": "notes/x.md", "content": "x"}, ("local_write", "allow")),
    ("bash", {"command": "ln -s ../shared lib"}, ("shell", "allow")),
    ("bash", {"command": "echo hi > out.txt"}, ("local_write", "allow")),
    ("write_file", {"path": "../notes.md", "content": "x"}, ("local_write", "allow")),
])
def test_plain_project_keeps_its_class(tree, tool, args, expected):
    assert outcome(tool, args, cwd=str(tree)) == expected


def test_distinctive_names_still_match_anywhere(tree):
    """The pre-existing name checks are untouched by the folder resolution."""
    assert outcome("write_file", {"path": "policies/gates.yaml", "content": "x"},
                   cwd=str(tree)) == ("store_write", "ask")
