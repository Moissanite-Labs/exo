"""A file tool's write through a linked folder is read where it lands (exo-desktop #52).

`write_file`, `edit_file` and `apply_patch` follow a link in the folders of the path they are given, as the kernel
does. The gate already matched the resolved path against the stores, but the call's target, summary and parts named
only the path as spelt. So `deny: ["target:*/shared/*"]` denied `write_file /abs/shared/plain.txt` and
`../shared/plain.txt`, yet allowed `o/plain.txt` with `o` a link to that folder, and the ask for
`s/authorized_keys` (`s` a link to `~/.ssh`) never named `~/.ssh`. Each path a link leads elsewhere now adds a
`local_write` part whose target is where the write lands, and the call's summary names that place.

Fake files in a temporary HOME. The gate rows classify only; the last rows run the tools to show the part's
target is the file they write.
"""
import os

import pytest

from exo import gate
from exo.core.tools.builtin import fs
from exo.core.tools.registry import ToolContext

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="POSIX symlinks")


def P(*body):
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A session folder `work` with a link `o` to a folder outside it, `s` to `~/.ssh`, and `n` to a folder below
    the outside one, so `n/../plain.txt` lands beside `o/plain.txt` while it reads as `work/plain.txt`."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text("old\n")
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "policies" / "gates.yaml").write_text("old\n")
    shared = tmp_path / "shared"
    (shared / "sub").mkdir(parents=True)
    (shared / "plain.txt").write_text("old\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "notes.txt").write_text("old\n")
    (work / "o").symlink_to(shared)
    (work / "s").symlink_to(home / ".ssh")
    (work / "p").symlink_to(home / ".exo" / "policies")
    (work / "n").symlink_to(shared / "sub")
    (tmp_path / "session").symlink_to(work)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "CLAUDE_CONFIG_DIR", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


WRITES = {
    "write_file": lambda p: ("write_file", {"path": p, "content": "new\n"}),
    "edit_file": lambda p: ("edit_file", {"path": p, "old_string": "old", "new_string": "new"}),
    "apply_patch update": lambda p: ("apply_patch", {"patch": P(f"*** Update File: {p}", "-old", "+new")}),
    "apply_patch add": lambda p: ("apply_patch", {"patch": P(f"*** Add File: {p}", "+new")}),
    "apply_patch move": lambda p: ("apply_patch", {"patch": P(f"*** Move File: notes.txt -> {p}")}),
}


def read(tree, how, path, cwd=None):
    tool, args = WRITES[how](path)
    return gate.classify({"tool": tool, "args": args}, cwd=str(cwd or tree / "work"))


def outcome(c, *deny):
    return gate.decide(c, gate.normalize_policies({"gates": {"deny": list(deny)}})).outcome


@pytest.mark.parametrize("how", WRITES)
@pytest.mark.parametrize("path", ["o/plain.txt", "n/../plain.txt"])
def test_a_target_entry_on_the_place_reaches_a_write_through_a_linked_folder(tree, how, path):
    """On main the linked spelling was an allowed local write while every plain spelling of the place was denied."""
    for plain in (str(tree / "shared" / "plain.txt"), "../shared/plain.txt"):
        assert outcome(read(tree, how, plain), "target:*/shared/*") == "deny", plain      # the control
    c = read(tree, how, path)
    assert c.category == "local_write"
    assert outcome(c) == "allow"                    # an ordinary write outside the session folder stays allowed
    assert outcome(c, "target:*/shared/*") == "deny"
    assert str(tree / "shared" / "plain.txt") in [p.target for p in c.parts]


@pytest.mark.parametrize("how", WRITES)
@pytest.mark.parametrize("path,place,category,entry", [
    ("s/authorized_keys", ".ssh/authorized_keys", "credentials", "target:*/.ssh/*"),
    ("p/gates.yaml", ".exo/policies/gates.yaml", "store_write", "target:*/.exo/policies/*")])
def test_a_store_write_through_a_linked_folder_names_the_store(tree, how, path, place, category, entry):
    """The store checks already resolved the link, so the write asks, and no grant covers it (as on main). A deny
    entry on the store's place now denies it too, and the question names where the write lands."""
    c = read(tree, how, path)
    assert c.category == category and outcome(c) == "ask" and gate.no_grant(c)
    assert outcome(c, entry) == "deny"
    assert str(tree / "home" / place) in gate.summarize(c, gate.decide(c, gate.DEFAULT_POLICIES))


def test_one_linked_file_in_a_patch_is_a_part_of_its_own(tree):
    c = read(tree, "apply_patch update", "notes.txt")
    assert outcome(c, "target:*/shared/*") == "allow"
    c = gate.classify({"tool": "apply_patch", "args": {"patch": P("*** Update File: notes.txt", "-old", "+new",
                                                                  "*** Add File: o/new.txt", "+x")}},
                      cwd=str(tree / "work"))
    assert outcome(c, "target:*/shared/*") == "deny"
    assert c.summary == "write notes.txt"           # the call's own path has no link: its summary is as before


def test_no_link_no_change(tree):
    """A path with no link in its folders reads as before: no part, the summary as spelt. A session folder reached
    through a link, a final link the tools refuse anyway, and a doubled slash are not a link in the path's folders."""
    (tree / "work" / "final.txt").symlink_to(tree / "shared" / "plain.txt")
    rows = [("notes.txt", tree / "work"), ("notes.txt", tree / "session"), ("final.txt", tree / "work"),
            ("/" + str(tree / "shared" / "plain.txt"), tree / "work"), ("../shared/plain.txt", tree / "work")]
    for path, cwd in rows:
        for how in ("write_file", "edit_file"):
            c = read(tree, how, path, cwd)
            assert (c.category, c.summary, c.parts) == ("local_write", f"write {path}", []), (path, cwd)


def test_a_system_link_in_an_absolute_path_is_read_where_it_lands_too(tree):
    """The cost. The gate cannot tell a planted link from a system one, such as macOS `/tmp`, a link to
    `/private/tmp`. An allow rule matches the call as spelt, and the part is decided on its own (#191). So a policy
    that denies local writes and allows the spelt folder now denies the write, and names the place, until it allows
    the place too. On main the write was allowed. The default policy still allows it."""
    (tree / "private" / "tmp").mkdir(parents=True)
    (tree / "tmp").symlink_to(tree / "private" / "tmp")
    c = read(tree, "write_file", str(tree / "tmp" / "x.txt"))
    spelt = {"match": {"target": f"{tree}/tmp/*"}, "decision": "allow"}
    place = {"match": {"target": f"{tree}/private/tmp/*"}, "decision": "allow"}

    def confined(*rules):
        return gate.normalize_policies({"gates": {"categories": {"local_write": "deny"}, "rules": list(rules)}})
    d = gate.decide(c, confined(spelt))
    assert d.outcome == "deny" and str(tree / "private" / "tmp" / "x.txt") in d.reason
    assert gate.decide(c, confined(spelt, place)).outcome == "allow"
    assert outcome(c) == "allow"


@pytest.mark.parametrize("how", ["write_file", "edit_file", "apply_patch update"])
def test_the_part_is_the_file_the_tool_writes(tree, how):
    """What the gate reads is what the tool writes: the part's target is the file that changes."""
    tool, args = WRITES[how]("o/plain.txt")
    c = gate.classify({"tool": tool, "args": args}, cwd=str(tree / "work"))
    landed = [p.target for p in c.parts if p.target != "o/plain.txt"]
    handler = {"write_file": fs.write_file, "edit_file": fs.edit_file, "apply_patch": fs.patch}[tool]
    handler(args, ToolContext(cwd=str(tree / "work")))
    assert landed == [str(tree / "shared" / "plain.txt")]
    assert open(landed[0]).read() == "new\n"
    assert not (tree / "work" / "plain.txt").exists()
