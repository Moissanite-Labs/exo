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


def read_as(tool, args, cwd, policy=POL):
    """(the categories of every reading of the call and of their parts, the outcome): on a merge with #452 a shell
    call's folder reading stands beside the line alone (gate.readings)."""
    c = gate.classify({"tool": tool, "args": args}, cwd=cwd)
    return {x.category for r in gate.readings(c) for x in [r, *gate._each_part(r)]}, gate.decide(c, policy).outcome


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
    cats, out = read_as("bash", args, str(tree))
    assert expected[0] in cats and out == expected[1]


@pytest.mark.parametrize("tool,key", [("terminal", "command"), ("exec_command", "cmd")])
@pytest.mark.parametrize("folder", ["~/.exo/state", "sdir", "sdir/../policies", "c2"])
@pytest.mark.parametrize("command", ["cp /tmp/g.json approval-grants.json", "echo x > default.yaml",
                                     "bash -c 'cp x gates.yaml'", "echo $(cp /tmp/g.json approval-grants.json)"])
def test_the_workdir_argument(tree, tool, key, folder, command):
    """#453's `workdir` rows: with #311 the line is read in the folder, and a relative write there is a store write."""
    args = {key: command, "workdir": folder}
    cats, out = read_as(tool, args, str(tree))
    assert "store_write" in cats and out == "ask"
    assert read_as(tool, args, str(tree), DENY_STORE)[1] == "deny"


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
    cats, out = read_as("bash", args, str(tree))
    assert "store_write" in cats and out == "ask"
    cats, out = read_as("bash", args, str(tree), DENY_STORE)
    assert "store_write" in cats and out == "deny"


DENY_EXO = {"gates": {"deny": ["target:*/.exo/*"]}}


@pytest.mark.parametrize("folder", ["~/.exo/policies", "~/.exo/plugins/foo", "~/.exo/run", "sdir/../plugins/foo"])
@pytest.mark.parametrize("command,target", [
    ("touch ~/.exo/state/x", "~/.exo/state/x"),
    ("touch y ~/.exo/state/x", "~/.exo/state/x"),
    ("touch ~/.exo/state/x > out.txt", "~/.exo/state/x"),
    ("rm -rf ~/.exo/state/x", "~/.exo/state/x"),
    ("git -C ~/.exo/state add x", "~/.exo/state"),
    ("git -C sub add ~/.exo/state/x", "~/.exo/state/x"),
    ("sudo touch ~/.exo/state/x", "~/.exo/state/x"),
    ("bash -c 'touch ~/.exo/state/x'", "~/.exo/state/x"),
    ("timeout 5 env A=1 touch ~/.exo/state/x", "~/.exo/state/x"),
    ("ssh host 'touch ~/.exo/state/x'", "~/.exo/state/x"),
    ("mosh host -- touch ~/.exo/state/x", "~/.exo/state/x"),
    ("echo $(touch ~/.exo/state/x)", "~/.exo/state/x"),
    ("bash <<'EOF'\ntouch ~/.exo/state/x\nEOF", "~/.exo/state/x"),
])
def test_in_a_store_folder_the_write_is_named_after_its_store_path(tree, folder, command, target):
    """A word that is a store path only because the folder is in the store never displaces one that is a store path
    by its own spelling, and the program word never names the write. So `deny: ["target:*/.exo/*"]` denies these, as
    on main and as in their `cd` forms."""
    for d in (".exo/plugins/foo", ".exo/run"):
        (tree.parent / d).mkdir(parents=True, exist_ok=True)
    c = gate.classify({"tool": "bash", "args": {"command": command, "cwd": folder}}, cwd=str(tree))
    assert (c.category, c.target) == ("store_write", target)
    assert gate.decide(c, DENY_EXO).outcome == "deny"
    cd = gate.classify({"tool": "bash", "args": {"command": f"cd {folder} && {command}"}}, cwd=str(tree))
    assert gate.decide(cd, DENY_EXO).outcome == "deny"


@pytest.mark.parametrize("folder", ["sdir/../policies", "sdir/../keys", "sdir/../run", "sdir/../plugins/foo", "pdir",
                                    "~/.exo/keys", "~/.exo/state", "~/.exo/plugins", "$HOME/.exo/plugins/foo"])
@pytest.mark.parametrize("line", ["cat *; touch ~/.exo/state/x", "cat ../keys/seat; touch ~/.exo/state/x",
                                  "cat config; touch ~/.exo/state/x"])
def test_the_lines_the_check_of_452_found_are_denied_as_on_main(tree, folder, line):
    """A check of #452 found these asked under a `target:` deny that denies them on main: the store write was named
    after `touch`. A key read beside it does not change that."""
    for d in (".exo/plugins/foo", ".exo/run"):
        (tree.parent / d).mkdir(parents=True, exist_ok=True)
    c = gate.classify({"tool": "bash", "args": {"command": line, "cwd": folder}}, cwd=str(tree))
    assert "~/.exo/state/x" in [p.target for p in [c, *gate._each_part(c)] if p.category == "store_write"]
    assert gate.decide(c, DENY_EXO).outcome == "deny"


@pytest.mark.parametrize("folder", ["~/.exo/plugins/foo", "~/.exo/run", "pdir"])
@pytest.mark.parametrize("command", ["make", "./build.sh"])
def test_the_program_word_never_names_a_store_write(tree, folder, command):
    """A command with no other word still runs in a store folder, so it asks as its `cd` form does. The card names
    the folder, not the program."""
    for d in (".exo/plugins/foo", ".exo/run"):
        (tree.parent / d).mkdir(parents=True, exist_ok=True)
    c = gate.classify({"tool": "bash", "args": {"command": command, "cwd": folder}}, cwd=str(tree))
    assert ("store_write", "a path inside the store (the tool's folder)") in [(r.category, r.target)
                                                                              for r in gate.readings(c)]
    assert gate.decide(c, POL).outcome == "ask"


@pytest.mark.parametrize("args,target", [
    # from a folder outside the store, a program word that leads in names the write, as its absolute spelling does
    ({"command": "../pdir/run.sh", "cwd": "notes"}, "../pdir/run.sh"),
    # a string in code is read as a command, so its path is the program word there (gate._classify_code)
    ({"command": "python3 -c \"open('pdir/default.yaml','w').write('x')\""}, "pdir/default.yaml"),
    ({"command": "node -e \"require('fs').writeFileSync('sdir/../policies/default.yaml','x')\""},
     "sdir/../policies/default.yaml"),
])
def test_a_program_word_that_leads_into_the_store_names_the_write(tree, args, target):
    """Controls: from a folder outside the store these were named so before, and still are."""
    c = gate.classify({"tool": "bash", "args": args}, cwd=str(tree))
    assert target in [p.target for r in gate.readings(c) for p in [r, *gate._each_part(r)]
                      if p.category == "store_write"]
    assert gate.decide(c, POL).outcome == "ask"
    assert gate.decide(c, DENY_STORE).outcome == "deny"


@pytest.fixture
def links(tree):
    """xl links to a folder elsewhere, and kl, pl and rl to ~/.ssh, the plugins folder and the run folder. The kernel
    reads `xl/../kl` through xl (~/elsewhere/kl); os.path.abspath and bash's `cd` fold it to kl."""
    home = tree.parent
    for d in ("elsewhere/d", ".ssh", ".exo/plugins/foo", ".exo/run"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / ".ssh/id_rsa").write_text("fake\n")
    for name, to in (("xl", "elsewhere/d"), ("kl", ".ssh"), ("pl", ".exo/plugins"), ("rl", ".exo/run")):
        (tree / name).symlink_to(home / to, target_is_directory=True)
    return tree


@pytest.mark.parametrize("folder", ["xl/../kl", "~/proj/xl/../kl", "notes/../xl/../kl", "xl/../rl"])
@pytest.mark.parametrize("command", ["tar czf /tmp/o.tgz .", "pip install --target . x", "sudo tar czf /tmp/o.tgz .",
                                     "bash -c 'tar czf /tmp/o.tgz .'"])
def test_the_cwd_argument_is_read_folded_too(links, folder, command):
    """Main read the folder folded, so these asked. Read joined only, they were allowed. Now both readings count:
    `xl/../kl` is ~/.ssh and `xl/../rl` the run folder."""
    args = {"command": command, "cwd": folder}
    c = gate.classify({"tool": "bash", "args": args}, cwd=str(links))
    assert "credentials" in [p.category for p in [c, *gate._each_part(c)]]
    assert gate.decide(c, POL).outcome == "ask"
    assert gate.decide(c, {"gates": {"deny": ["credentials"]}}).outcome == "deny"


def test_a_reserved_entry_reaches_the_folded_folder(links):
    """The check's two reserved hits: main kept `credentials` reserved for this line, and so does the folded folder."""
    line = "tar czf /tmp/o.tgz . ; git push --force origin main"
    c = gate.classify({"tool": "bash", "args": {"command": line, "cwd": "xl/../kl"}}, cwd=str(links))
    d = gate.decide(c, {"gates": {"reserved": ["credentials"]}})
    assert ("credentials", ".") in d.reserved and d.outcome == "ask", d
    d = gate.decide(c, {"gates": {"reserved": ["store_write", "credentials", "force_push"]}})
    assert {("credentials", "."), ("force_push", "origin")} <= set(d.reserved), d


@pytest.mark.parametrize("args", [
    {"command": "echo x > default.yaml", "cwd": "xl/../pdir"},
    {"command": "cp /tmp/g.json approval-grants.json", "cwd": "xl/../sdir"},
    {"command": "cp /tmp/evil.py hook.py", "cwd": "xl/../pl/foo"},
    {"command": "echo x > foo", "cwd": "xl/../rl"},
    {"command": "cd xl/../pdir && echo x > default.yaml"},
    {"command": "cd xl/../pl/foo && cp /tmp/evil.py hook.py"},
    {"command": "cd sub && cd ../xl/../pdir && echo x > default.yaml"},
    {"command": "cd ~/proj/xl/../pdir && echo x > default.yaml"},
    # bash's `cd` enters notes here, so ../pdir is the policies folder (_cd_enter reads the folder folded too)
    {"command": "cd xl/../notes && cp x ../pdir/default.yaml"},
    {"command": "pushd xl/../notes && echo x > ../pdir/default.yaml"},
    # and rel (-> ../.exo) here, the Exo home itself, as `cd ~/.exo` does (_in_store_dir reads the target folded too)
    {"command": "cd xl/../rel && touch x"},
    {"command": "cd xl/../rel && cp /tmp/a /tmp/b"},
])
def test_a_dotdot_after_a_link_in_the_folder_is_read_folded_for_store_writes(links, args):
    cats, out = read_as("bash", args, str(links))
    assert "store_write" in cats and out == "ask"
    cats, out = read_as("bash", args, str(links), DENY_STORE)
    assert "store_write" in cats and out == "deny"


@pytest.mark.parametrize("command,expected", [
    ("cd xl/../notes && cp x y", ("read", "allow")),
    ("cd xl/../notes && echo x > y.md", ("local_write", "allow")),
])
def test_a_folded_cd_out_of_the_store_keeps_its_class(links, command, expected):
    assert outcome("bash", {"command": command}, cwd=str(links)) == expected


def test_a_brace_word_met_again_after_a_cd_is_read_again(tree):
    """The store checks keep their brace memo per folder entered, so the second `cp` is read from notes."""
    line = "cp y ../pdi{r,x}/default.yaml; cd notes; cp y ../pdi{r,x}/default.yaml"
    assert outcome("bash", {"command": line}, cwd=str(tree)) == ("store_write", "ask")


@pytest.fixture
def inner(tree):
    """notes/in/e links to the Exo home, so a write through e is a store write only from notes/in."""
    (tree / "notes/in").mkdir()
    (tree / "notes/in/e").symlink_to("../../../.exo", target_is_directory=True)
    return tree


@pytest.mark.parametrize("command", [
    # a `cd` that did not stay, then a relative `cd`: the shell is still in notes, so it enters notes/in
    "cd notes && (cd sub) && cd in && echo x > e/policies/default.yaml",
    "cd notes; (cd sub); cd in; cp x e/policies/default.yaml",
    "cd notes && cd missing; cd in && echo x > e/policies/default.yaml",
    "cd notes && { false && cd sub; }; cd in && echo x > e/policies/default.yaml",
    "cd notes && if false; then cd sub; fi; cd in && echo x > e/policies/default.yaml",
    "cd notes && pushd sub && popd && cd in && echo x > e/policies/default.yaml",
    "cd notes && pushd sub; popd; cd in && tee e/policies/default.yaml < x",
    "cd notes && (cd sub) && pushd in && mv x e/policies/default.yaml",
    "cd notes && (cd sub) && cd in && bash -c 'cp x e/policies/default.yaml'",
    "cd notes && (cd sub) && cd in && sudo cp x e/policies/default.yaml",
    # the control the shapes were measured against
    "cd notes && cd in && echo x > e/policies/default.yaml",
])
def test_a_cd_target_is_read_from_every_folder_entered_before_it(inner, command):
    assert outcome("bash", {"command": command}, cwd=str(inner)) == ("store_write", "ask")
    assert outcome("bash", {"command": command}, cwd=str(inner), policy=DENY_STORE) == ("store_write", "deny")


@pytest.mark.parametrize("command", [
    "cd notes && (cd sub) && cd in && echo x > y.md",
    "cd notes && pushd sub && popd && cd in && echo x > y.md",
    "cd notes && cd missing; cd in && echo x > y.md",
])
def test_a_cd_target_read_from_every_folder_keeps_a_plain_write(inner, command):
    assert outcome("bash", {"command": command}, cwd=str(inner)) == ("local_write", "allow")


@pytest.mark.parametrize("steps,expected", [(64, ("local_write", "allow")), (65, ("store_write", "ask"))])
def test_the_folder_cap_is_64(tree, steps, expected):
    """gate._ENTERED: 64 folders entered with `cd` are read, the 65th fails closed. An absolute `cd` enters one."""
    line = " && ".join(f"cd {tree}/n{i}" for i in range(steps)) + " && echo x > y"
    assert outcome("bash", {"command": line}, cwd=str(tree)) == expected


@pytest.mark.parametrize("steps,expected", [(6, ("local_write", "allow")), (7, ("store_write", "ask"))])
def test_each_relative_cd_is_joined_to_every_folder_before_it(tree, steps, expected):
    """So n relative `cd`s in a row enter 2**n - 1 folders: six enter 63, and a seventh passes the cap."""
    line = " && ".join(f"cd d{i}" for i in range(steps)) + " && echo x > y"
    assert outcome("bash", {"command": line}, cwd=str(tree)) == expected


KDIR = {"command": "cat kdir/seat"}


@pytest.mark.parametrize("command", [
    # a relative secret read after a `cd` is read from the folder it enters, as a store write's path is
    "cd notes && cat ../kdir/seat",
    "cd notes; head -c 9 ../kdir/seat",
    "pushd notes && grep -r token ../kdir",
    "cd notes && (cd sub) && cd in && cat ../../kdir/seat",
])
def test_a_relative_secret_read_after_a_cd(tree, command):
    (tree / "kdir").symlink_to(tree.parent / ".exo/keys", target_is_directory=True)
    assert outcome("bash", KDIR, cwd=str(tree)) == ("credentials", "ask")
    (tree / "notes/in").mkdir()
    c = gate.classify({"tool": "bash", "args": {"command": command}}, cwd=str(tree))
    assert c.category == "credentials" and gate.decide(c, POL).outcome == "ask", c
    assert gate.decide(c, {"gates": {"deny": ["credentials"]}}).outcome == "deny"


@pytest.mark.parametrize("command,folder", [("cd d0 && cat ../kdir/seat", "notes"),
                                            ("cd d0 && cd d1 && cd d2 && cd d3 && cat ../kdir/seat", "notes"),
                                            ("cd notes && cat ../kdir/seat", "sub")])
def test_a_secret_read_after_a_cd_keeps_the_folders_reserved_hit(tree, command, folder):
    """With a `cwd`, the line alone leaves a read after a `cd` to the folder's reading beside it, so the folder's part
    (#311), named by its full path, is still added. When the line alone read it too, that part and its reserved hit
    were lost (5 pairs in this round's gate_diff)."""
    (tree / "kdir").symlink_to(tree.parent / ".exo/keys", target_is_directory=True)
    (tree / "sub").mkdir()
    c = gate.classify({"tool": "bash", "args": {"command": command, "cwd": folder}}, cwd=str(tree))
    d = gate.decide(c, {"gates": {"reserved": ["credentials"]}})
    assert {"../kdir/seat", str(tree / "kdir/seat")} <= {t for k, t in d.reserved if k == "credentials"}, d.reserved
    assert d.outcome == "ask"


def test_a_copy_and_a_read_of_one_path_after_a_cd_agree(tree):
    (tree / "kdir").symlink_to(tree.parent / ".exo/keys", target_is_directory=True)
    c = gate.classify({"tool": "bash", "args": {"command": "cd notes && cp ../kdir/seat /tmp/x"}}, cwd=str(tree))
    assert c.category == "store_write" and "credentials" in [p.category for p in gate._each_part(c)]
    assert gate.decide(c, {"gates": {"deny": ["credentials"]}}).outcome == "deny"


@pytest.mark.parametrize("command", [
    # #530: bash's `cd` drops xl before it reads `..`, so these enter ~/.ssh, as a `cwd` of xl/../kl does
    "cd xl/../kl && tar czf /tmp/o.tgz .",
    "pushd xl/../kl && tar czf /tmp/o.tgz .",
    "cd ~/proj/xl/../kl && tar czf /tmp/o.tgz .",
    "cd notes; cd ../kl && tar czf /tmp/o.tgz .",
])
def test_a_folded_cd_into_a_secret_store(links, command):
    assert outcome("bash", {"command": command}, cwd=str(links)) == ("credentials", "ask")


@pytest.mark.parametrize("command", [
    "cd xl/../notes && ls", "cd xl/../sub && tar czf /tmp/o.tgz .", "cd notes && cat ../notes/x",
    "cd ~ && du -sh .", "cd sub && ls -la . && cat README",
])
def test_a_read_after_a_cd_keeps_its_class(links, command):
    assert outcome("bash", {"command": command}, cwd=str(links)) == ("read", "allow")


def test_each_path_is_looked_up_once_a_call(tree, monkeypatch):
    """#340: a relative word is read from each folder a `cd` entered. Each path is looked up once a call, and the
    folders above it are shared (_resolve), so the look-ups grow with the words and folders, not with their depth."""
    deep = "/".join(f"level{i}" for i in range(10))
    (tree / deep).mkdir(parents=True)
    words = " ".join(f"w{i}" for i in range(100))
    line = f"cd {deep}/a && cd {deep}/b && cd {deep}/c && touch {words}; touch {words}"
    calls = {"lstat": 0, "realpath": 0}
    stores: list = []
    resolved: list = []
    lstat, realpath, store_abs, resolve = gate.os.lstat, gate.os.path.realpath, gate._store_abs_read, gate._resolve

    def count_lstat(*a, **kw):
        calls["lstat"] += 1
        return lstat(*a, **kw)

    def count_realpath(*a, **kw):
        calls["realpath"] += 1
        return realpath(*a, **kw)

    def count_stores(p, ancestors):
        stores.append((p, ancestors))
        return store_abs(p, ancestors)

    def count_resolved(p, memo):
        resolved.append(p)
        return resolve(p, memo)

    monkeypatch.setattr(gate.os, "lstat", count_lstat)
    monkeypatch.setattr(gate.os.path, "realpath", count_realpath)
    monkeypatch.setattr(gate, "_store_abs_read", count_stores)
    monkeypatch.setattr(gate, "_resolve", count_resolved)
    c = gate.classify({"tool": "bash", "args": {"command": line}}, cwd=str(tree))
    monkeypatch.undo()
    assert "store_write" not in [p.category for p in gate._each_part(c)] and gate.decide(c, POL).outcome == "allow"
    # each (folder, word) once, though the line names every word twice: _Scope.stores and _Scope.forms
    assert len(stores) == len(set(stores)) and len(resolved) == len(set(resolved))
    assert len({p for p, _ in stores if p.endswith("/w7")}) == 8      # the tool's folder and the 7 a `cd` entered
    # 100 words in 8 folders, each looked up with its folders shared, not walked from the root
    assert calls["lstat"] < 4000 and calls["realpath"] < 100, calls


@pytest.mark.parametrize("path", [
    "pdir/default.yaml", "sdir/../policies/x", "c2/../keys", "deep/../../../.exo/state", "notes/../pdir/./x",
    "missing/../pdir/x", "loop/x", "rel/policies/../state/x", "notes//x/", "/", "x/..",
])
def test_resolve_is_realpath(tree, path):
    (tree / "loop").symlink_to("loop")
    memo: dict = {}
    for p in (str(tree / path), str(tree) + "//" + path):
        assert gate._resolve(p, memo) == gate.os.path.realpath(p), p


def test_resolve_reads_a_dotdot_after_a_link_loop(tree):
    """A loop cannot be entered, so `loop/../pdir` names nothing the kernel opens. _resolve reads the `..` after it as
    after any link it could not resolve, so the path still reaches pdir's store folder. Python 3.13's realpath does
    the same; 3.11's stops at the loop and folds the rest as written."""
    (tree / "loop").symlink_to("loop")
    assert gate._resolve(str(tree / "loop/../pdir"), {}) == gate.os.path.realpath(tree.parent / ".exo/policies")


def test_past_the_budget_every_relative_word_counts(tree, monkeypatch):
    """Each join to a folder a `cd` entered is charged to the #269 budget (gate._charge). Past it every relative word
    counts as the store, so the line asks (fails closed), and the work stops growing with the words."""
    line = " && ".join(f"cd d{i}" for i in range(6)) + " && touch " + " ".join(f"w{i}" for i in range(2000))
    looked: list = []
    store_abs = gate._store_abs_read
    monkeypatch.setattr(gate, "_store_abs_read", lambda p, ancestors: looked.append(p) or store_abs(p, ancestors))
    assert outcome("bash", {"command": line}, cwd=str(tree)) == ("store_write", "ask")
    assert not [p for p in looked if p.endswith("/w1999")]      # not looked up at all once the budget is spent
    monkeypatch.undo()
    line = " && ".join(f"cd d{i}" for i in range(6)) + " && touch " + " ".join(f"w{i}" for i in range(20))
    assert outcome("bash", {"command": line}, cwd=str(tree)) == ("read", "allow")


DENY_KEYS = {"gates": {"deny": ["target:*/.ssh/*", "target:*id_rsa*"]}}


@pytest.mark.parametrize("folder,command", [
    ("sdir/../keys", "cat ~/.ssh/id_rsa"),
    ("c2/../keys", "cat ~/.ssh/id_rsa"),
    ("c2/../keys", "cat ../kl/id_rsa"),
    ("sdir/../keys", "sudo cat ~/.ssh/id_rsa"),
    ("c2/../keys", "bash -c 'cat ~/.ssh/id_rsa'"),
    ("~/.exo/keys", "cat ~/.ssh/id_rsa"),
])
def test_a_key_the_line_names_in_a_keys_folder_keeps_its_deny(links, folder, command):
    """abu-aska's review of f7074f1e: read through the link, `sdir/../keys` is the keys folder, whose credentials part
    is named '' and led the line, so a `target:` deny on the key the line names only asked. The line is read alone
    too now (#311, gate.readings), so the key names its own part, and the deny stands, as on main."""
    c = gate.classify({"tool": "bash", "args": {"command": command, "cwd": folder}}, cwd=str(links))
    assert gate.decide(c, DENY_KEYS).outcome == "deny"
    assert gate.decide(c, POL).outcome == "ask"


@pytest.mark.parametrize("folder", ["sdir/../keys", "c2/../keys", "~/.exo/keys"])
def test_a_reserved_entry_keeps_the_key_a_store_write_names(tree, folder):
    """The same review: the `(credentials, .../.exo/keys/seat)` reserved hit was lost in those folders."""
    c = gate.classify({"tool": "bash", "args": {"command": "cp x ~/.exo/keys/seat", "cwd": folder}}, cwd=str(tree))
    d = gate.decide(c, {"gates": {"reserved": ["target:*/.exo/*"]}})
    assert any(k == "credentials" and t.endswith("/.exo/keys/seat") for k, t in d.reserved), d.reserved
    assert ("store_write", "~/.exo/keys/seat") in d.reserved and d.outcome == "ask"


SHELLS_ALLOWED = {"gates": {"categories": {"opaque_exec": "allow", "shell": "allow", "read": "allow"}}}


@pytest.mark.parametrize("line", [
    "{ bash; cd notes; bash; } <<< 'cp x ../pdir/default.yaml'",
    "{ cd notes; bash; } <<< 'cp x ../pdir/default.yaml'",
    "{ sh; cd notes; sh; } <<EOF\ncp x ../pdir/default.yaml\nEOF",
])
def test_a_stdin_text_met_again_after_a_cd_is_read_again(tree, line):
    """abu-aska's nit: the stdin memo (gate._once) kept the first shell's reading, from the session folder, for the
    shell after the `cd`. It is keyed by the folders entered too now, as classify_shell's memo is."""
    c = gate.classify({"tool": "bash", "args": {"command": line}}, cwd=str(tree))
    assert "store_write" in [p.category for p in [c, *gate._each_part(c)]]
    assert gate.decide(c, SHELLS_ALLOWED).outcome == "ask"


@pytest.mark.parametrize("args,target", [
    ({"command": "rm -rf x", "cwd": "~/.exo/policies"}, "x"),
    ({"command": "tar czf /tmp/o.tgz .", "cwd": "xl/../pdir"}, "."),
    ({"command": "tar -czf /tmp/o.tgz .", "cwd": "~/.exo/policies"}, "."),
    ({"command": "git -C ../state add x", "cwd": "~/.exo/policies"}, "../state"),
    ({"command": "cp -r a b", "cwd": "~/.exo/policies"}, "a"),
    ({"command": "xargs -0 rm -f old", "cwd": "~/.exo/state"}, "old"),
    ({"command": "xargs -n 1 cp -t dest", "cwd": "~/.exo/state"}, "dest"),
])
def test_in_a_store_folder_an_option_never_names_the_write(links, args, target):
    """abu-aska's nit: in a store folder every relative word is a store path, and the first one but the program named
    the write, an option too (`-rf`, `czf`, `-C`, xargs' `-0`). Options, tar's bundled options and xargs' words up
    to the program it runs are skipped now."""
    c = gate.classify({"tool": "bash", "args": args}, cwd=str(links))
    assert target in [r.target for r in gate.readings(c) if r.category == "store_write"], gate.readings(c)
    assert gate.decide(c, POL).outcome == "ask"


@pytest.mark.parametrize("command", ["tee -- -p/default.yaml < x", "cp -t -p x", "xargs -a -p/l rm -f y"])
def test_a_skipped_word_still_counts_when_nothing_else_names_the_write(links, command):
    """Outside a store folder, an option or a word that xargs or tar skip names the write when no other word does, so
    skipping them for the name never drops a write: `-p` links to the policies folder."""
    (links / "-p").symlink_to(links.parent / ".exo/policies", target_is_directory=True)
    assert outcome("bash", {"command": command}, cwd=str(links)) == ("store_write", "ask")


AFTER_CD = {"gates": {"deny": ["target:a path inside the store*"]}}


@pytest.mark.parametrize("line,target", [
    ("cd ~/.exo/policies && cp y pdi{r,x}/default.yaml", "pdir/default.yaml"),
    ("cd ~/.exo/state && touch sdir/x", "sdir/x"),
    ("cd ~/.exo/policies && echo x > c2/y", "c2/y"),
    ("cd ~/.exo/policies; cp y pdir/default.yaml", "pdir/default.yaml"),
])
def test_after_a_cd_into_the_store_the_old_name_stays_a_target(tree, line, target):
    """Since #340 reads a relative word from the folders, the card names the write after it. Before, it was named
    "a path inside the store (after cd)": that name is kept as a second target, so a `target:` entry on it still
    matches, as on main (the reviews of f7074f1e counted 5 and 18 such pairs lowered)."""
    c = gate.classify({"tool": "bash", "args": {"command": line}}, cwd=str(tree))
    assert (c.category, c.target) == ("store_write", target)
    assert gate._AFTER_CD in [p.target for p in gate._each_part(c) if p.category == "store_write"]
    assert gate.decide(c, AFTER_CD).outcome == "deny"
    assert gate.decide(c, DENY_EXO).outcome == "ask"         # the word itself is not in ~/.exo
