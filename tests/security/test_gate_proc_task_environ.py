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
    "cat /proc/thread-self/task/1/environ",
    "cd /proc/self/task/1 && cat environ",
])
def test_a_task_environ_read_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat /proc/self/environ",               # the direct form already asked
])
def test_the_direct_environ_still_asks(line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat /proc/self/status",
    "cat /proc/self/task/1/status",
    "cat /proc/self/task/*/status",
    "cat /proc/self/task/1/cmdline",
    "ls /proc/self/task",
])
def test_other_task_files_stay_reads(line):
    assert outcome(line) == ("read", "allow")


def test_the_file_tools_see_a_task_environ_too():
    c = gate.classify({"tool": "read_file", "args": {"path": "/proc/self/task/1/environ"}})
    assert (c.category, gate.decide(c, POL).outcome) == ("credentials", "ask")
