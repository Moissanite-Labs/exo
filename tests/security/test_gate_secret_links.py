"""#209: a harmlessly named link into a secret store is judged by where it leads, from the tool's folder too.

Fake files only, in a temporary HOME. Every check classifies; nothing is run."""
import asyncio
import os

import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES


def outcome(tool, args, cwd=None):
    c = gate.classify({"tool": tool, "args": args}, cwd=cwd) if cwd else gate.classify({"tool": tool, "args": args})
    return c.category, gate.decide(c, POL).outcome


def held(tool, args, cwd=None):
    """outcome() for a shell call with its own folder argument: the line keeps its category, and a store read from
    that folder is a credentials part beside it (#311), so either counts as credentials here."""
    c = gate.classify({"tool": tool, "args": args}, cwd=cwd) if cwd else gate.classify({"tool": tool, "args": args})
    secret = any(p.category == "credentials" for p in gate._each_part(c))
    return ("credentials" if secret else c.category), gate.decide(c, POL).outcome


@pytest.fixture
def tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (".ssh", ".aws", "proj", ".exo/keys"):
        (home / d).mkdir(parents=True)
    for f in (".ssh/id_rsa", ".ssh/config", ".ssh/id_rsa.pub", ".aws/credentials", "proj/.env",
              ".exo/keys/seat.key"):
        (home / f).write_text("fake\n")
    work = tmp_path / "work"
    (work / "notes").mkdir(parents=True)
    (work / "README.md").write_text("hi\n")
    (work / "notes" / "v1.md").write_text("hi\n")
    links = {"notes.txt": home / ".ssh/id_rsa", "aws.txt": home / ".aws/credentials", "app.cfg": home / "proj/.env",
             "cfg": home / ".ssh", "seat.txt": home / ".exo/keys/seat.key", "key.pub": home / ".ssh/id_rsa",
             "shared.txt": home / ".ssh/id_rsa.pub", "latest.md": work / "notes/v1.md"}
    for name, target in links.items():
        (work / name).symlink_to(target)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXO_HOME", raising=False)
    monkeypatch.delenv("EXO_KEYS_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.chdir(elsewhere)              # the gate's own folder is not the tool's
    return work


@pytest.mark.parametrize("name", ["notes.txt", "aws.txt", "app.cfg", "cfg/config", "seat.txt"])
def test_a_link_into_a_store_asks_in_every_form(tree, name):
    """The issue's 16 forms (and a link into Exo's keys): relative from the tool's folder, and absolute."""
    absolute = str(tree / name)
    assert outcome("read_file", {"path": name}, cwd=str(tree)) == ("credentials", "ask")
    assert outcome("read_file", {"path": absolute}) == ("credentials", "ask")
    assert outcome("bash", {"command": f"cat {name}"}, cwd=str(tree)) == ("credentials", "ask")
    assert outcome("bash", {"command": f"cat {absolute}"}) == ("credentials", "ask")


def test_the_gate_own_folder_still_counts(tree, monkeypatch):
    """The stricter of the two folders: with no tool folder given, the gate's own is used, as before."""
    monkeypatch.chdir(tree)
    assert outcome("bash", {"command": "cat notes.txt"}) == ("credentials", "ask")
    assert outcome("read_file", {"path": "notes.txt"}) == ("credentials", "ask")
    assert outcome("read_file", {"path": "notes.txt"}, cwd="/") == ("credentials", "ask")


def test_the_bash_tool_cwd_argument_is_read(tree):
    assert held("bash", {"command": "cat notes.txt", "cwd": str(tree)}) == ("credentials", "ask")
    assert held("bash", {"command": "cat notes.txt", "cwd": tree.name}, cwd=str(tree.parent)) == \
        ("credentials", "ask")


def test_a_cwd_argument_moves_only_a_shell(tree):
    """Only a shell runs in its own `cwd` argument. The file tools take unknown arguments and read from the session's
    folder, so a `cwd` on them changes nothing, and the session's folder is checked for every tool."""
    w = str(tree)
    assert outcome("read_file", {"path": "notes.txt", "cwd": "/"}, cwd=w) == ("credentials", "ask")
    assert outcome("write_file", {"path": "cfg/authorized_keys", "content": "x", "cwd": "/tmp"}, cwd=w) == \
        ("credentials", "ask")
    assert outcome("grep", {"pattern": "x", "path": "cfg", "cwd": "/tmp"}, cwd=w) == ("credentials", "ask")
    assert outcome("read_file", {"path": "README.md", "cwd": "/"}, cwd=w) == ("read", "allow")
    # A shell runs in its `cwd`, joined to the session's folder; the session's folder still counts too.
    assert held("bash", {"command": "cat notes.txt", "cwd": w}, cwd="/tmp") == ("credentials", "ask")
    assert held("bash", {"command": "cat work/notes.txt", "cwd": ".."}, cwd=w) == ("credentials", "ask")
    assert outcome("bash", {"command": "cat notes.txt", "cwd": "/tmp"}, cwd=w) == ("credentials", "ask")


def test_a_pub_name_does_not_hide_a_private_key(tree):
    assert outcome("read_file", {"path": "key.pub"}, cwd=str(tree)) == ("credentials", "ask")
    assert outcome("bash", {"command": f"cat {tree / 'key.pub'}"}) == ("credentials", "ask")
    # A public key stays shareable, by name or through a link that leads to one.
    assert outcome("read_file", {"path": "~/.ssh/id_rsa.pub"}) == ("read", "allow")
    assert outcome("read_file", {"path": "shared.txt"}, cwd=str(tree)) == ("read", "allow")


def test_ordinary_reads_stay_allowed(tree):
    for args in ({"path": "README.md"}, {"path": "latest.md"}, {"path": str(tree / "latest.md")}):
        assert outcome("read_file", args, cwd=str(tree)) == ("read", "allow")
    assert outcome("bash", {"command": "cat README.md latest.md notes/v1.md"}, cwd=str(tree)) == ("read", "allow")
    assert outcome("read_file", {"path": "~/.ssh/id_rsa"}) == ("credentials", "ask")     # the control


def test_a_folder_named_like_a_store_does_not_make_every_read_ask(tree, tmp_path):
    """A session in a repo's `.claude/worktrees/x` checkout: a link that stays below that folder is judged by its
    part there, so ordinary reads are not asked about; a link out of it into a store still asks."""
    wt = tmp_path / "repo" / ".claude" / "worktrees" / "x"
    (wt / "docs").mkdir(parents=True)
    (wt / "docs" / "guide.md").write_text("hi\n")
    (wt / "guide.md").symlink_to(wt / "docs" / "guide.md")
    (wt / "id.txt").symlink_to(tree / "notes.txt")
    assert outcome("bash", {"command": "cat guide.md docs/guide.md"}, cwd=str(wt)) == ("read", "allow")
    assert outcome("read_file", {"path": "guide.md"}, cwd=str(wt)) == ("read", "allow")
    assert outcome("read_file", {"path": "id.txt"}, cwd=str(wt)) == ("credentials", "ask")


def test_the_core_passes_the_session_folder_to_tool_hooks(tmp_path):
    """A hook that takes **kwargs gets `cwd`; an older one that declares neither `cwd` nor **kwargs still runs,
    since each callback gets only the keywords it declares."""
    from exo.core.engine import CoreEngine
    from exo.core.tools.registry import ToolContext
    from exo.core.types import ToolCall

    seen, older = [], []

    def old_hook(tool_name, args, session_id, task_id, tool_call_id, turn_id, risk):
        older.append(tool_name)

    models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"rules": []}}
    eng = CoreEngine(tmp_path / "home", models, cwd=str(tmp_path), env={})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: seen.append(kw), "probe")
    eng.plugins.hooks.register("pre_tool_call", old_hook, "older")
    eng.plugins.hooks.register("post_tool_call", old_hook, "older")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "a.txt").write_text("hi")

    async def go():
        sid = (await eng.loop.create_session(title="t")).id
        result = await eng.registry.call(ToolCall("c", "read_file", {"path": "a.txt"}),
                                         ToolContext(cwd=str(tmp_path / "work"), session=sid))
        await asyncio.wait_for(eng.close(), 5)
        return result
    result = asyncio.run(go())
    assert not result.is_error and "hi" in result.text()
    assert seen and seen[0]["cwd"] == str(tmp_path / "work")
    assert os.path.isabs(seen[0]["cwd"])
    assert older == ["read_file", "read_file"]          # before the call and after it


def test_hooks_get_only_the_keywords_they_declare():
    from exo.core.hooks import HookRegistry

    calls = []
    reg = HookRegistry()
    reg.register("post_tool_call", lambda tool_name: calls.append(tool_name))
    reg.register("post_tool_call", lambda **kw: calls.append(sorted(kw)))
    assert reg.fire("post_tool_call", tool_name="t", args={}, cwd="/w") == []
    asyncio.run(reg.afire("post_tool_call", tool_name="u", args={}, cwd="/w"))
    assert calls == ["t", ["args", "cwd", "tool_name"], "u", ["args", "cwd", "tool_name"]]
