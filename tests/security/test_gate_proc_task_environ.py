"""#313: a thread's /proc/<pid>/task/<tid>/environ holds the same environment as
/proc/<pid>/environ, so it is a secret store too.

Every check classifies; nothing is run."""
import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES


def outcome(command):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c.category, gate.decide(c, POL).outcome


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EXO_HOME", raising=False)


@pytest.mark.parametrize("line", [
    "cat /proc/self/task/1/environ",
    "cat /proc/1/task/1/environ",
    "cat /proc/self/task/*/environ",
    "cat /proc/*/task/*/environ",
    "cat /proc/self/task/*/[!x]nviron",
    "cat /proc/self/task/1/environ*",
    "cat /proc/self/task/[0-9]*/environ",
    "cat /proc/self/task/{1,2}/environ",
    "cat /proc/self/{task,net}/*/environ",
    "cat '/proc/self/task/1/environ'",
    'cat "/proc/self/task/"*/environ',
    "cat /proc/self/ta\\sk/1/environ",
    # A doubled slash was the #446 gap: where no link resolved it, the row was matched as written and read. Since
    # #446 the name is also matched with `//` collapsed, so these ask whether or not /proc/self is a link.
    "cat /proc/self/task//1/environ",
    "cat /proc/1/task//1/environ",
    "cat /proc/1//environ",
    "cd /proc/self/task/1 && cat environ",
])
def test_a_task_environ_read_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [         # bash expands each to every task/<tid>/environ
    "cat /proc/1/*/*/environ",
    "cat /proc/self/[t]ask/*/environ",
    "cat /proc/self/ta?k/*/environ",
    "cat /proc/*/*/*/environ",
    "cat /proc/self/t*/1/environ",
    "cat /proc/self/*/*/env*",
])
def test_a_glob_in_the_task_folder_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat /proc/self/environ",               # the direct form already asked
    "cat /proc/thread-self/environ",        # thread-self is already a task folder
])
def test_the_direct_environ_still_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat /proc/self/status",
    "cat /proc/self/task/1/status",
    "cat /proc/self/task/*/status",
    "cat /proc/self/task/1/cmdline",
    "ls /proc/self/task",
    "cat /proc/self/*/*/status",
    "cat /proc/self/*/*/cmdline",
    "cat /proc/self/net/*/environ",         # only task/ holds a thread's environ
    "cat /proc/self/*/*",                   # no thread's environ is two levels down
    "ls /proc/*/status",
])
def test_other_task_files_stay_reads(line):
    assert outcome(line) == ("read", "allow")


@pytest.mark.parametrize("tool", ["read_file", "write_file"])
def test_the_file_tools_see_a_task_environ_too(tool):
    c = gate.classify({"tool": tool, "args": {"path": "/proc/self/task/1/environ", "content": "x"}})
    assert (c.category, gate.decide(c, POL).outcome) == ("credentials", "ask")


def test_the_search_tools_leave_a_task_environ_out():     # #241: a walk of /proc/self skips it, as the direct one
    skip = gate.StoreSkip("/proc/self")
    assert skip("/proc/self/task/1/environ") and skip("/proc/self/environ")
    assert not skip("/proc/self/task/1/status")


# The D-038 amendment for #313: each line the change decides lower than before gets what the same line with
# /proc/self/environ already got. A file tool's category deny becomes the credentials ask, and on a compound line
# the credentials part's private exposure hides a matrix deny on another part.
@pytest.mark.parametrize("policy", [
    {"gates": {"categories": {"read": "deny"}}},
    {"gates": {"categories": {"read": "deny", "credentials": "allow"}}},
    {"gates": {"categories": {"local_write": "deny"}}},
    {"gates": {"deny": ["local_write"]}},
    {"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"}}}},
])
@pytest.mark.parametrize("action", [
    {"tool": "read_file", "args": {"path": "{p}"}},
    {"tool": "write_file", "args": {"path": "{p}", "content": "x"}},
    {"tool": "bash", "args": {"command": "cat {p}"}},
    {"tool": "bash", "args": {"command": "cat {p}; git push --force origin main"}},
])
def test_a_task_environ_decides_as_the_direct_one(action, policy):
    def decided(path):
        args = {k: v.replace("{p}", path) for k, v in action["args"].items()}
        return gate.decide(gate.classify({"tool": action["tool"], "args": args}), policy).outcome
    assert decided("/proc/self/task/1/environ") == decided("/proc/self/environ")


# The same amendment: beside a store write the line is credentials, as with the direct form. Since #447 the store
# write part keeps the answer with the principal alone (gate.call_approvers), for both forms.
def test_a_store_write_beside_a_task_environ_has_the_direct_ones_approvers():
    approvals = {"default": ["principal", "seat:builder"]}

    def who(path):
        c = gate.classify({"tool": "bash", "args": {"command": f"cat {path}; echo x > ~/.exo/policies/gates.yaml"}})
        return c.category, gate.call_approvers(c, approvals)
    assert who("/proc/self/task/1/environ") == who("/proc/self/environ") == ("credentials", ["principal"])
