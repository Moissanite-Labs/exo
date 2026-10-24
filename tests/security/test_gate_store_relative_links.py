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
    (proj / "sdir").symlink_to(home / ".exo/state", target_is_directory=True)
    (proj / "c1").symlink_to("sdir", target_is_directory=True)      # a chain: c2 -> c1 -> sdir
    (proj / "c2").symlink_to("c1", target_is_directory=True)
    (home / "a/b/c").mkdir(parents=True)
    (proj / "deep").symlink_to(home / "a/b/c", target_is_directory=True)
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


DENY_STORE = {"gates": {"deny": ["store_write"]}}


def _patch(head):
    return {"patch": f"*** Begin Patch\n*** {head}\n+x\n*** End Patch"}


@pytest.mark.parametrize("tool,args", [
    # `..` after a link climbs from where the link leads, as the kernel reads it (abu-aska's review of #429):
    # sdir -> ~/.exo/state, so sdir/../policies is ~/.exo/policies. Folding the `..` first read ~/proj/policies.
    ("write_file", {"path": "pdir/../policies/default.yaml", "content": "x"}),
    ("write_file", {"path": "sdir/../policies/default.yaml", "content": "x"}),
    ("edit_file", {"path": "sdir/../policies/default.yaml", "patch": "x"}),
    ("apply_patch", _patch("Update File: sdir/../policies/default.yaml")),
    ("apply_patch", _patch("Add File: sdir/../policies/new.yaml")),
    ("bash", {"command": "cp x pdir/../policies/default.yaml"}),
    ("bash", {"command": "cp x sdir/../policies/default.yaml"}),
    ("bash", {"command": "echo x > sdir/../policies/default.yaml"}),
    ("bash", {"command": "tee sdir/../policies/default.yaml"}),
    ("bash", {"command": "mv x sdir/../policies/default.yaml"}),
    ("bash", {"command": "sed -i s/a/b/ sdir/../policies/default.yaml"}),
    ("bash", {"command": "sudo cp x sdir/../policies/default.yaml"}),
    ("bash", {"command": "bash -c 'cp x sdir/../policies/default.yaml'"}),
    ("bash", {"command": "echo $(cp x sdir/../policies/default.yaml)"}),
    # a link chain
    ("write_file", {"path": "c1/../policies/default.yaml", "content": "x"}),
    ("write_file", {"path": "c2/../policies/default.yaml", "content": "x"}),
    ("bash", {"command": "cp x c2/../policies/default.yaml"}),
    # deep -> ~/a/b/c, so the link lands in c and ../../../.exo from there is the store
    ("bash", {"command": "ln -s ../../../.exo deep"}),
])
def test_dotdot_after_a_link_is_read_through_the_link(tree, tool, args):
    assert outcome(tool, args, cwd=str(tree)) == ("store_write", "ask")
    assert outcome(tool, args, cwd=str(tree), policy=ALLOW_STORE) == ("store_write", "ask")
    assert outcome(tool, args, cwd=str(tree), policy=DENY_STORE) == ("store_write", "deny")


def test_absolute_dotdot_control(tree):
    assert outcome("write_file", {"path": str(tree / "sdir/../policies/default.yaml"), "content": "x"},
                   cwd=str(tree)) == ("store_write", "ask")


@pytest.mark.parametrize("args,expected", [
    # the bash tool's own `cwd` argument is joined, not folded: the shell runs in it through the link
    ({"command": "cp x default.yaml", "cwd": "pdir"}, ("store_write", "ask")),
    ({"command": "cp x default.yaml", "cwd": "sdir/../policies"}, ("store_write", "ask")),
    ({"command": "echo x > default.yaml", "cwd": "c2/../policies"}, ("store_write", "ask")),
    ({"command": "cp x policies/default.yaml", "cwd": "rel"}, ("store_write", "ask")),
    ({"command": "cd ../pdir && echo x > default.yaml", "cwd": "notes"}, ("store_write", "ask")),
    # #453's `cwd` rows: a relative write in a store folder the call runs in
    ({"command": "cp /tmp/g.json approval-grants.json", "cwd": "~/.exo/state"}, ("store_write", "ask")),
    ({"command": "echo x > gates.yaml", "cwd": "~/.exo/policies"}, ("store_write", "ask")),
    ({"command": "bash -c 'cp /tmp/g.json approval-grants.json'", "cwd": "~/.exo/state"}, ("store_write", "ask")),
    ({"command": "echo $(cp /tmp/g.json approval-grants.json)", "cwd": "~/.exo/state"}, ("store_write", "ask")),
    # plain folders keep their class
    ({"command": "cp x default.yaml", "cwd": "notes"}, ("shell", "allow")),
    ({"command": "cp x default.yaml", "cwd": "notes/.."}, ("shell", "allow")),
    ({"command": "cp x y", "cwd": "sdir/.."}, ("shell", "allow")),
])
def test_the_shell_cwd_argument(tree, args, expected):
    assert outcome("bash", args, cwd=str(tree)) == expected


@pytest.mark.parametrize("folder", ["sdir/../keys", "c2/../keys", "kdir"])
def test_the_cwd_argument_through_a_link_on_the_read_side(tree, folder):
    """The secret-store checks read the same joined folder (#209), so the key read asks. Checked by its parts, so the
    test holds whether the read makes the call credentials or adds a credentials part beside the line (#311)."""
    (tree / "kdir").symlink_to(tree.parent / ".exo/keys", target_is_directory=True)
    c = gate.classify({"tool": "bash", "args": {"command": "cat seat", "cwd": folder}}, cwd=str(tree))
    assert any(p.category == "credentials" for p in [c, *gate._each_part(c)])
    assert gate.decide(c, POL).outcome == "ask"


@pytest.mark.parametrize("command", [
    # a relative `cd`, `pushd` or `git -C` target is read from the tool's folders too
    "cd pdir && cp x default.yaml",
    "cd pdir && echo x > default.yaml",
    "cd sdir/../policies && echo x > default.yaml",
    "(cd pdir && echo x > default.yaml)",
    "pushd pdir && echo x > default.yaml",
    "git -C pdir commit -am x",
    "git -C sdir/../policies add default.yaml",
])
def test_a_relative_cd_through_a_link(tree, command):
    assert outcome("bash", {"command": command}, cwd=str(tree)) == ("store_write", "ask")
    assert outcome("bash", {"command": command}, cwd=str(tree), policy=DENY_STORE) == ("store_write", "deny")


@pytest.mark.parametrize("command,expected", [
    ("cd notes && echo x > y.md", ("local_write", "allow")),
    ("cd .. && echo x > notes.txt", ("local_write", "allow")),
    ("cd pdir && cat default.yaml", ("read", "allow")),          # reading the policies does not ask
    ("cd notes && cp ../x y && cd .. && mv a b", ("read", "allow")),
    ("cd notes && cat ../pdir/default.yaml", ("read", "allow")),
    ("cd a && echo x > y; cd ../b && echo x > y; cd ../c && echo x > y", ("local_write", "allow")),
])
def test_plain_cd_rows(tree, command, expected):
    assert outcome("bash", {"command": command}, cwd=str(tree)) == expected


@pytest.mark.parametrize("command", [
    # a relative path or `cd` target after a `cd` that stays out of the store is read from where that `cd` leads too
    "cd notes && cp x ../pdir/default.yaml",
    "cd notes && echo x > ../pol.yaml",
    "cd notes && cp x ../sdir/../policies/default.yaml",
    "cd sub && cd ../pdir && echo x > default.yaml",
    "pushd notes && tee ../pdir/default.yaml < x",
    "(cd notes && mv x ../pdir/default.yaml)",
    "cd notes; sed -i s/a/b/ ../pdir/default.yaml",
    "cd notes && echo $(cp x ../pdir/default.yaml)",
    "cd notes && bash -c 'cp x ../pdir/default.yaml'",
    "cd && cp x proj/pdir/default.yaml",
    "cd ~/proj/notes && cp x ../pdir/default.yaml",
    # the same text met again after the `cd` is read again from there
    "eval 'cp x ../pdir/default.yaml'; cd notes && eval 'cp x ../pdir/default.yaml'",
])
def test_a_relative_path_after_a_cd_out_of_the_store(tree, command):
    assert outcome("bash", {"command": command}, cwd=str(tree)) == ("store_write", "ask")
    assert outcome("bash", {"command": command}, cwd=str(tree), policy=DENY_STORE) == ("store_write", "deny")


def test_the_issues_two_calls_behind_a_cd(tree):
    """#340's two calls, each behind a `cd` that stays out of the store: the link asks, and so does the write
    through it once it is there."""
    (tree / "a/b").mkdir(parents=True)
    assert outcome("bash", {"command": "cd a/b && ln -s ../../../.exo e"}, cwd=str(tree)) == ("store_write", "ask")
    (tree / "a/b/e").symlink_to("../../../.exo", target_is_directory=True)
    for command in ("cd a/b && cp x e/policies/default.yaml", "cd a && cd b && echo x > e/policies/default.yaml"):
        assert outcome("bash", {"command": command}, cwd=str(tree)) == ("store_write", "ask")


def test_past_the_folder_cap_a_relative_write_asks(tree):
    """A line that enters more folders than the gate follows (gate._ENTERED) fails closed."""
    line = " && ".join(f"cd d{i}" for i in range(12)) + " && echo x > y"
    assert outcome("bash", {"command": line}, cwd=str(tree)) == ("store_write", "ask")


def test_a_session_folder_in_the_store(tree):
    """USER-GUIDE §6: a relative name written by a tool whose working folder is policies/ is a store write."""
    home = tree.parent
    for name in ("gates.yaml", "default.yaml"):
        assert outcome("write_file", {"path": name, "content": "x"},
                       cwd=str(home / ".exo/policies")) == ("store_write", "ask")
    # known friction, as the absolute spelling: a word naming a protected entry asks from the Exo home
    for command in ("echo policies > notes.txt", "git add state", "cp state/x /tmp/y"):
        assert outcome("bash", {"command": command}, cwd=str(home / ".exo")) == ("store_write", "ask")
        assert outcome("bash", {"command": command.replace("state", "~/.exo/state").replace(
            "policies >", "~/.exo/policies >")}, cwd=str(tree)) == ("store_write", "ask")


@pytest.mark.parametrize("command,home", [("ln -s .. up", False), ("ln -s ../.exo sub/rel2", False),
                                          ("ln -s ../.exo rel2", True)])
def test_ln_friction_fails_closed(tree, command, home):
    """The raw `ln` target is read from the tool's folder too, and the last operand could be a folder, so these
    ask (GATE.md, known friction)."""
    assert outcome("bash", {"command": command}, cwd=str(tree.parent if home else tree)) == ("store_write", "ask")


def test_never_from_the_gates_own_folder(tree, monkeypatch):
    """The gate runs in a folder that holds pl -> ~/.exo/policies; with no session folder nothing is read from it."""
    here = tree.parent.parent / "gate-here"
    here.mkdir()
    (here / "pl").symlink_to(tree.parent / ".exo/policies", target_is_directory=True)
    monkeypatch.chdir(here)
    assert outcome("write_file", {"path": "pl/default.yaml", "content": "x"}, cwd=None) == ("local_write", "allow")
    assert outcome("bash", {"command": "cd pl && echo x > default.yaml"}, cwd=None) == ("local_write", "allow")
    assert outcome("write_file", {"path": "pl/default.yaml", "content": "x"},
                   cwd=str(here)) == ("store_write", "ask")


@pytest.mark.parametrize("command", [
    # wrappers, quoting and other spellings of a write through a link (gate_diff's focused corpus has more)
    "doas cp x sdir/../policies/default.yaml",
    "env A=1 cp x pdir/default.yaml",
    "timeout 5 cp x c2/../policies/default.yaml",
    "nice cp x pdir/default.yaml",
    "command cp x sdir/../policies/default.yaml",
    "echo x | xargs -I{} cp {} pdir/default.yaml",
    "find /tmp -name x -exec cp {} sdir/../policies/ \\;",
    "ssh host 'cp x pdir/default.yaml'",
    "cp x p'dir'/default.yaml",
    "cp x ./pdir/./default.yaml",
    "cp x notes/../pdir/default.yaml",
    "cat > sdir/../policies/default.yaml <<EOF\nx\nEOF",
    "exec 3> sdir/../policies/default.yaml",
    "x=$(cp x c2/../policies/default.yaml)",
])
def test_other_spellings_of_a_write_through_a_link(tree, command):
    assert outcome("bash", {"command": command}, cwd=str(tree)) == ("store_write", "ask")


@pytest.mark.parametrize("head", ["Delete File: sdir/../policies/default.yaml",
                                  "Move File: notes/x.md -> sdir/../policies/default.yaml",
                                  "Move File: pdir/default.yaml -> /tmp/x"])
def test_apply_patch_deletes_and_moves(tree, head):
    assert outcome("apply_patch", _patch(head), cwd=str(tree)) == ("store_write", "ask")


def test_from_home_a_wide_write_on_the_folder_asks_as_its_absolute_spelling(tree):
    """Known friction (GATE.md): HOME holds the store, so `mv x .` from HOME asks, as `mv x ~` does from anywhere."""
    home = str(tree.parent)
    for command in ("mv x .", "chmod -R u+w .", "ln -s /opt/x ."):
        assert outcome("bash", {"command": command}, cwd=home) == ("store_write", "ask")
        assert outcome("bash", {"command": command.replace(" .", " ~")}, cwd=str(tree)) == ("store_write", "ask")
    for command in ("mv a b", "cp -r . /tmp/x", "tar czf /tmp/b.tgz .", "echo hi > notes.txt"):
        assert outcome("bash", {"command": command}, cwd=home)[1] == "allow"
    # a line that enters HOME with `cd`, and `..` from a folder in HOME, read the same way
    for command in ("cd ~ && mv Downloads/x.txt .", "cd && chmod -R u+w .", "mv x .."):
        assert outcome("bash", {"command": command}, cwd=str(tree)) == ("store_write", "ask")


@pytest.mark.parametrize("command", ["echo x > foo", "cp x foo", "rm -rf build", "git push --force origin main"])
def test_a_cwd_in_the_run_folder_decides_as_the_absolute_spelling(tree, command):
    """A relative write from a shell `cwd` in the run folder is a store write, as `echo x > ~/.exo/run/foo` is."""
    (tree.parent / ".exo/run").mkdir()
    args = {"command": command, "cwd": "~/.exo/run"}
    assert outcome("bash", args, cwd=str(tree)) == ("store_write", "ask")
    assert outcome("bash", args, cwd=str(tree), policy=DENY_STORE) == ("store_write", "deny")
