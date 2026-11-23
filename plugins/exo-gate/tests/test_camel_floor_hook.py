"""Review 5662071801 of #602: two hook checks past `decide` read only the category a call leads with.

#515 (#602) reads a camelCase tool name by its hump words, so `mcp__fs__searchReplace` leads with `read` and
`mcp__slack__postMessage` with `message_send`; what main read before (`store_write` or `credentials` beside a store
path or a secret, `unknown` for a name it did not know) is the call's ``floor`` reading. `decide` keeps the strictest
reading, but the hook kept the D-065 bypass floor only when the lead category was a floor one, and asked a tool the
registry calls high risk only when the lead category was `unknown`. So in bypass a camelCase tool ran a seat or policy
file write and a secret read, and a high-risk camelCase MCP tool ran in every mode. Both checks now count each
reading (`gate.no_grant`, `gate.readings`). A post whose only floor is in a reading is still held for its posting
fixes, and a call with no floor reading is still auto-approved, camelCase or not.

Review 5675164403 of #703: the floor reads each part too, so a snake shell call whose own folder argument is a secret
folder (#311) also asks once in bypass. Its line keeps its own category (`read` for `cat` and `ls`), and the store
read is a part, so bypass ran it before #703. Its plain spelling (`cat ~/.kube/config`) asked already. Temp stores,
a temp HOME and a mock context; nothing is executed.
"""
import os

import pytest

from exo import crypto
from exo_gate import hook
from exo_gate import permissions as perm
from test_bypass import rows
from test_hook import FOOT, call, make_gate

SEAT = "~/.exo/seats/principal.toml"
GATES = "~/.exo/policies/gates.yaml"
HI = f"hi\n\n{FOOT}"   # the example store's posting rules want the footer
# The review's rows: each asked in bypass before #602, and ran with it. Since #659 (#651) the three replace rows
# lead with `store_write` on main e0968a2b and ask there too; they stay here as guards.
CAMEL_FLOOR = [
    ("mcp__fs__searchReplace", {"path": SEAT, "search": "ask", "replace": "bypass"}, "store_write"),
    ("findAndReplace", {"path": SEAT, "search": "ask", "replace": "bypass"}, "store_write"),
    ("mcp__fs__searchReplace", {"path": GATES, "search": "deny", "replace": "allow"}, "store_write"),
    ("readFile", {"path": GATES}, "store_write"),
    ("readFile", {"path": SEAT}, "store_write"),
    ("getOrCreateFile", {"path": GATES}, "store_write"),
    ("mcp__fs__readFile", {"path": "~/.exo/config.json"}, "store_write"),
    ("mergeGithub", {"path": "~/.ssh/id_rsa"}, "credentials"),
    ("mcp__github__mergePullRequest", {"path": "~/.ssh/id_rsa"}, "credentials"),
    ("mcp__slack__postMessage", {"file": "~/.aws/credentials", "text": HI, "channel": "#general"}, "credentials"),
]
# Review 5675164403 of #703: more spellings of a camelCase read of a policy file. A capital first hump, a dotted or
# dashed server prefix, an MCP name with its hash tail, and a relative path read from the session folder ~/.exo
# (the `cwd` the core passes, last in each row). Each ran in bypass before #703.
CAMEL_SPELLINGS = [
    ("mcp__fs__ReadFile", {"path": GATES}, ""),
    ("fs.readFile", {"path": GATES}, ""),
    ("exo-fs.readFile", {"path": GATES}, ""),
    ("mcp__my_server__readFile_1a2b3c4d", {"path": GATES}, ""),
    ("mcp__fs__readFile", {"path": "policies/gates.yaml"}, "~/.exo"),
]
# Review 5675164403 of #703: a snake shell call whose own folder argument is a secret folder (#311). The store read
# is a part of the line, not its lead category, so it ran in bypass before #703. The gate reads `cwd` and `workdir`
# alike for every shell tool (review 5681915526 asked for the other spellings), so the last four rows swap them.
SECRET_FOLDER = [
    ("terminal", {"command": "cat config", "workdir": "~/.kube"}),
    ("bash", {"command": "cat config", "workdir": "~/.kube"}),
    ("exec_command", {"cmd": "cat config", "workdir": "~/.kube"}),
    ("bash", {"command": "ls", "cwd": "~/.exo/keys"}),
    ("bash", {"command": "ls", "cwd": "~/.aws"}),
    ("bash", {"command": "ls", "cwd": "~/.claude"}),
    ("bash", {"command": "cat config", "cwd": "~/.kube"}),
    ("terminal", {"command": "ls", "cwd": "~/.aws"}),
    ("terminal", {"command": "ls", "workdir": "~/.exo/keys"}),
    ("exec_command", {"cmd": "ls", "workdir": "~/.claude"}),
]
# Controls that lead with their floor category: they asked in bypass on main too.
LEAD_FLOOR = [
    ("write_file", {"path": SEAT, "content": "x"}),
    ("mcp__fs__writeFile", {"path": SEAT, "content": "x"}),
    ("terminal", {"command": "cat ~/.exo/keys/exo-gate.key"}),
    ("terminal", {"command": "cat ~/.kube/config"}),   # the first SECRET_FOLDER row, spelled with its folder
]
# Controls with no floor reading: bypass still auto-approves them, camelCase or not, and in an ordinary folder.
NO_FLOOR = [
    ("mcp__slack__postMessage", {"channel": "#general", "text": HI}),
    ("mcp__slack__post_message", {"channel": "#general", "text": HI}),
    ("terminal", {"command": "rm -rf build"}),
    ("terminal", {"command": "rm -rf build", "workdir": "~/projects/app"}),
    ("bash", {"command": "rm -rf build", "cwd": "~/projects/app"}),
]
# High-risk camelCase MCP tools: `unknown` before #602, now only in their floor reading.
CAMEL_HIGH = [
    ("mcp__fs__writeFile", {"path": "/var/tmp/x", "content": "y"}),
    ("mcp__fs__readFile", {"path": "/var/tmp/x"}),
    ("mcp__fs__uploadFile", {"path": "/var/tmp/x"}),
    ("mcp__fs__moveFile", {"src": "/var/tmp/x", "dst": "/var/tmp/y"}),
    ("mcp__gh__listIssues", {}),
]
SNAKE_KNOWN = [
    ("mcp__fs__write_file", {"path": "/var/tmp/x", "content": "y"}),
    ("mcp__fs__read_file", {"path": "/var/tmp/x"}),
    ("mcp__gh__list_issues", {}),
]


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXO_HOME", raising=False)  # ~/.exo is the Exo home, as on a default install


@pytest.fixture
def keyed_store(example_store, tmp_path):
    key = tmp_path / "keys" / "exo-gate.key"
    crypto.save_key(crypto.keygen(), key)
    return example_store, key


@pytest.fixture
def gate_in(keyed_store, request):
    """``gate_in(mode, bypass)``: a gate in enforce or observe mode, with bypass switched on by the principal."""
    from exo import principal

    def make(mode, bypass):
        store, key = keyed_store
        g, ctx = make_gate(store, key, decision_model=False, mode=mode)
        if bypass:
            token = principal.CALLER.set(principal.Caller("principal:tui"))
            try:
                hook.permissions_command(g, "bypass")
            finally:
                principal.CALLER.reset(token)
            assert g.bypass_on
        return g, ctx, store
    return make


@pytest.mark.parametrize("mode", ["enforce", "observe"])
@pytest.mark.parametrize("tool,args,floor", CAMEL_FLOOR)
def test_bypass_keeps_the_floor_a_camelcase_tool_has_in_its_floor_reading(gate_in, mode, tool, args, floor):
    g, ctx, store = gate_in(mode, True)
    r = call(ctx, tool, args)
    assert r is not None and r["action"] == "approve" and r.get("once") is True, (tool, r)
    [row] = rows(store)
    assert row["outcome"] == "ask" and "decided_by" not in row and row["bypass"] == {"floor": floor}
    assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1


@pytest.mark.parametrize("mode", ["enforce", "observe"])
@pytest.mark.parametrize("tool,args,floor", CAMEL_FLOOR)
def test_without_bypass_the_same_calls_ask(gate_in, mode, tool, args, floor):
    _, ctx, _ = gate_in(mode, False)
    assert call(ctx, tool, args)["action"] == "approve"


def _session(folder):
    """The `cwd` keyword the core passes with a call: the session's folder, or none."""
    return {"cwd": os.path.expanduser(folder)} if folder else {}


@pytest.mark.parametrize("mode", ["enforce", "observe"])
@pytest.mark.parametrize("tool,args,folder", CAMEL_SPELLINGS)
def test_bypass_keeps_the_floor_of_each_spelling_of_a_camelcase_read(gate_in, mode, tool, args, folder):
    g, ctx, store = gate_in(mode, True)
    r = call(ctx, tool, args, **_session(folder))
    assert r is not None and r["action"] == "approve" and r.get("once") is True, (tool, r)
    [row] = rows(store)
    assert row["outcome"] == "ask" and "decided_by" not in row and row["bypass"] == {"floor": "store_write"}
    assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1


@pytest.mark.parametrize("mode", ["enforce", "observe"])
@pytest.mark.parametrize("tool,args", SECRET_FOLDER)
def test_bypass_keeps_the_floor_of_a_shell_call_whose_folder_argument_is_a_secret_folder(gate_in, mode, tool, args):
    g, ctx, store = gate_in(mode, True)
    r = call(ctx, tool, args)
    assert r is not None and r["action"] == "approve" and r.get("once") is True, (tool, r)
    [row] = rows(store)
    assert row["classification"]["category"] == "read"   # the line's own category: the store read is a part
    assert row["outcome"] == "ask" and "decided_by" not in row and row["bypass"] == {"floor": "credentials"}
    assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1


# A SECRET_FOLDER row names its folder in its own `cwd` or `workdir`, so it needs no session folder (""). Only the
# relative CAMEL_SPELLINGS row reads its path from the session folder.
@pytest.mark.parametrize("mode", ["enforce", "observe"])
@pytest.mark.parametrize("tool,args,folder", CAMEL_SPELLINGS + [(t, a, "") for t, a in SECRET_FOLDER])
def test_without_bypass_those_calls_ask_once(gate_in, mode, tool, args, folder):
    _, ctx, _ = gate_in(mode, False)
    r = call(ctx, tool, args, **_session(folder))
    assert r["action"] == "approve" and r.get("once") is True, (tool, r)


@pytest.mark.parametrize("mode", ["enforce", "observe"])
@pytest.mark.parametrize("tool,args", LEAD_FLOOR)
def test_a_call_that_leads_with_its_floor_still_asks_in_bypass(gate_in, mode, tool, args):
    g, ctx, store = gate_in(mode, True)
    assert call(ctx, tool, args)["action"] == "approve"
    assert rows(store)[0]["bypass"]["floor"] in perm.FLOOR and g.permissions.floor_asks == 1


@pytest.mark.parametrize("tool,args", NO_FLOOR)
def test_bypass_still_auto_approves_a_call_with_no_floor_reading(gate_in, tool, args):
    g, ctx, store = gate_in("enforce", False)
    assert call(ctx, tool, args)["action"] == "approve"
    g, ctx, store = gate_in("enforce", True)
    assert call(ctx, tool, args) is None
    row = rows(store)[-1]
    assert row["decided_by"] == "bypass" and row["bypass"]["would"] == "ask" and g.permissions.auto_approved == 1


def test_a_post_whose_secret_is_in_its_floor_reading_is_still_held_for_its_fixes(gate_in):
    """As on main: bypass holds a post that breaks the posting rules and leads with no floor category, so the agent
    rewrites it. The call never runs; once rewritten, it asks as the floor."""
    g, ctx, store = gate_in("enforce", True)
    args = {"repo": "o/r", "title": "t", "body": "see /home/alice/x", "file": "~/.ssh/id_rsa"}
    r = call(ctx, "mcp__github__createIssue", args)
    assert r["action"] == "block" and "Fix:" in r["message"]
    assert rows(store)[0]["decision"]["rule"] == "bypass_hold" and g.permissions.held == 1


@pytest.mark.parametrize("tool,args", CAMEL_HIGH)
def test_a_high_risk_camelcase_mcp_tool_asks_in_enforce(gate_in, tool, args):
    _, ctx, store = gate_in("enforce", False)
    r = call(ctx, tool, args, risk="high")
    assert r is not None and r["action"] == "approve", (tool, r)
    assert f"registry risk is high and {tool} is known only by its hump words" in rows(store)[0]["decision"]["reason"]


@pytest.mark.parametrize("tool,args", CAMEL_HIGH)
def test_a_high_risk_camelcase_mcp_tool_would_ask_in_observe(gate_in, tool, args):
    _, ctx, store = gate_in("observe", False)
    assert call(ctx, tool, args, risk="high") is None
    [row] = rows(store)
    assert row["outcome"] == "ask" and row["would"] == "approve"


@pytest.mark.parametrize("tool,args", CAMEL_HIGH)
def test_a_high_risk_camelcase_mcp_tool_is_a_recorded_bypass_ask(gate_in, tool, args):
    """In bypass the ask is lifted as any other is, and the ledger row says it would have asked."""
    g, ctx, store = gate_in("enforce", True)
    assert call(ctx, tool, args, risk="high") is None
    [row] = rows(store)
    assert row["decided_by"] == "bypass" and row["bypass"]["would"] == "ask" and g.permissions.auto_approved == 1


@pytest.mark.parametrize("tool,args", CAMEL_HIGH + SNAKE_KNOWN)
def test_a_low_risk_mcp_tool_keeps_its_rule_outcome(gate_in, tool, args):
    _, ctx, _ = gate_in("enforce", False)
    assert call(ctx, tool, args, risk="low") is None


@pytest.mark.parametrize("tool,args", SNAKE_KNOWN)
def test_a_high_risk_known_snake_tool_keeps_its_rule_outcome(gate_in, tool, args):
    _, ctx, _ = gate_in("enforce", False)
    assert call(ctx, tool, args, risk="high") is None


def test_a_high_risk_tool_the_gate_does_not_know_still_asks(gate_in):
    _, ctx, store = gate_in("enforce", False)
    assert call(ctx, "mcp__x__doThing", {}, risk="high")["action"] == "approve"
    assert call(ctx, "mcp__x__do_thing", {}, risk="high")["action"] == "approve"
    assert "registry risk is high and mcp__x__do_thing is not a known tool" in rows(store)[-1]["decision"]["reason"]
    assert call(ctx, "mcp__x__doThing", {}, risk="low") is None
