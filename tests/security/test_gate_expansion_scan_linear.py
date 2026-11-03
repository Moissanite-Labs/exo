"""#231: the scan for variables set inside an expansion (`${PATH:=x}`, `${a[PATH=1]}`, `$((PATH=1))`) is linear.

classify runs inside the gate hook, on the core's event loop, so one long tool call must not stall it. On the
first #231 head the review's three shapes of 100 KB took from seconds to minutes: every unclosed `${NAME:=`
read to the end of the line again, every `${a[` looked for its `]` again, and a closed nest copied each value
again for each level. The brace, bracket and paren pairs are now each found in one pass. The `${a[` run also
reached fnmatch.translate, which is quadratic on a `[` that opens no set; since #307 the secret-store glob check
reads a word with more than 32 `[` as one that could match anything, without fnmatch.

Every command string here is data for exo.gate.classify/decide; none is run."""
import time

import pytest

from exo import gate

SIZE = 100_000
BOUND = 2.0   # CPU seconds, for the review's three shapes; each takes under half a second here
SLACK = 8.0   # for the longer checks below, where most of the time is main's lexer and per-word store check (#293)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _timed(cmd: str, bound: float = BOUND):
    t = time.process_time()                   # CPU time, so other suites on the machine do not count (#308)
    c = gate.classify({"tool": "bash", "args": {"command": cmd}})
    took = time.process_time() - t
    assert took < bound, (took, cmd[:40])
    return c


def _times(unit: str) -> int:
    return SIZE // len(unit)


def test_unclosed_param_assignments_are_linear():
    c = _timed("echo ${a:-" + "${PATH:=" * _times("${PATH:="))
    assert c.category == "opaque_exec" and "PATH" in c.summary, c   # each one sets PATH
    assert gate.decide(c).outcome == "ask", c


def test_unclosed_subscripts_are_linear():
    _timed("echo ${a[" + "${b[" * _times("${b["))


def test_closed_assignment_nest_is_linear():
    n = _times("${x:=}")
    c = _timed("echo " + "${x:=" * n + "}" * n)
    assert (c.category, gate.decide(c).outcome) == ("read", "allow"), c


@pytest.mark.parametrize("cmd", [
    "echo " + "${x:=" * _times("${x:=}") + "${PATH:=.}" + "}" * _times("${x:=}"),
    "echo " + "${a[" * _times("${a[]}") + "PATH=1" + "]}" * _times("${a[]}"),
    "echo $((" + "x+" * _times("x+") + "PATH=1))",
    # single-quoted text bash expands inside double quotes, then an unclosed run
    'echo "${a:-\'' + "${PATH:=" * _times("${PATH:=") + "'}\"",
], ids=["deep-param", "deep-subscript", "long-arith", "sq-in-dq"])
def test_a_listed_variable_deep_in_a_long_line_is_still_found(cmd):
    c = _timed(cmd, SLACK)
    assert "indirection" in c.signals and gate.decide(c).outcome == "ask", c
    # the card may name a stronger part (since #307 the `${a[` run reads as a glob that could match a secret store),
    # so the scan itself is asked what it found
    cmds, _, _ = gate._parse(cmd)
    assert any(name == "PATH" for name, _, _ in gate._hidden_sets(cmds[0], [])), cmd[:40]


def test_a_command_value_nest_reads_each_value_once():
    """Each nested value stands in its parent as `${_}`, so the 1,300 values are one command, read once. A quarter of
    SIZE: each space starts a word the per-word store check reads (#293)."""
    n = _times("${GIT_PAGER:=less }") // 4
    c = _timed("echo " + "${GIT_PAGER:=less " * n + "}" * n, SLACK)
    assert (c.category, gate.decide(c).outcome) == ("read", "allow"), c


_URLS = " ".join(f"https://ghe.corp/o/r/pull/{i}" for i in range(_times("https://ghe.corp/o/r/pull/1234 ")))


@pytest.mark.parametrize("cmd", [
    f"gh pr comment {_URLS} -b x",
    f"gh pr close {_URLS} --comment x",
    f"gh pr merge {_URLS} --subject x",
    f"true && gh pr comment {_URLS} -b x",
], ids=["comment", "close", "merge", "compound"])
def test_a_gh_line_of_many_url_operands_is_linear(cmd):
    """#550: each URL operand on another instance is another target, with a copy of the action on it. classify read a
    close's comment or a merge's subject once per copy, and decide read the line on each target with every copy: some
    3,000 targets took minutes. The line on each other target is now decided without the copies, as the first
    decision met each of them. The lexer and the per-word store check take most of classify's time (#293)."""
    c = _timed(cmd, SLACK)
    assert len(c.alt_targets) > 3000, len(c.alt_targets)
    t = time.process_time()
    d = gate.decide(c, gate.normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]},
                                                          "deny": ["target:ghe.corp/o/r#3000"]}}))
    took = time.process_time() - t
    assert took < BOUND, took
    assert d.outcome == "deny", d       # a deny on one far target still reaches the line


def test_a_long_line_of_gh_repo_values_and_gh_commands_is_linear():
    """#566: each gh command reads the GH_REPO values the line set before it, in a compound too, each one another
    target with a copy of the action. Only the last few count, so a line of many values and many gh commands stays
    linear, and a deny on a value deep in the line still reaches it."""
    unit = "{ export GH_REPO=o/r1234; gh pr comment 1234 -b x; }; "
    cmd = "".join(f"{{ export GH_REPO=o/r{i}; gh pr comment {i} -b x; }}; " for i in range(_times(unit)))
    c = _timed(cmd, SLACK)
    t = time.process_time()
    d = gate.decide(c, gate.normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]},
                                                          "deny": ["target:o/r1500#*"]}}))
    took = time.process_time() - t
    assert took < BOUND, took
    assert d.outcome == "deny", d


def test_many_prefix_gh_repo_values_before_many_url_operands_are_linear():
    """#566, review 5722148651: each GH_REPO value before gh is read on every operand. bash passes gh only the last,
    and the gate keeps the last few, so 4,687 values before 806 URL operands no longer take the product of the two.
    A deny on the last value still reaches the line."""
    values = SIZE * 3 // 4 // len("GH_REPO=a1234/r ")
    unit = "https://ghe.corp/o/r/pull/1234 "
    urls = " ".join(f"https://ghe.corp/o/r/pull/{i}" for i in range(SIZE // 4 // len(unit)))
    cmd = " ".join(f"GH_REPO=a{i}/r" for i in range(values)) + f" gh pr comment {urls} -b x"
    assert len(cmd) > SIZE * 0.95, len(cmd)
    c = _timed(cmd, SLACK)
    t = time.process_time()
    d = gate.decide(c, gate.normalize_policies({"gates": {"deny": [f"target:a{values - 1}/*"]}}))
    took = time.process_time() - t
    assert took < BOUND, took
    assert d.outcome == "deny", d
