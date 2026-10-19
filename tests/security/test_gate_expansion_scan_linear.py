"""#231: the scan for variables set inside an expansion (`${PATH:=x}`, `${a[PATH=1]}`, `$((PATH=1))`) is linear.

classify runs inside the gate hook, on the core's event loop, so one long tool call must not stall it. On the
first #231 head the review's three shapes of 100 KB took from seconds to minutes: every unclosed `${NAME:=`
read to the end of the line again, every `${a[` looked for its `]` again, and a closed nest copied each value
again for each level. The brace, bracket and paren pairs are now each found in one pass. The `${a[` run also
reached fnmatch.translate, which is quadratic on a `[` that opens no set (on main too).

Every command string here is data for exo.gate.classify/decide; none is run."""
import time

import pytest

from exo import gate

SIZE = 100_000
BOUND = 2.0   # seconds, for the review's three shapes; each takes under half a second here
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
    t = time.perf_counter()
    c = gate.classify({"tool": "bash", "args": {"command": cmd}})
    took = time.perf_counter() - t
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
    assert c.category == "opaque_exec" and "PATH" in c.summary, c
    assert gate.decide(c).outcome == "ask", c


def test_a_command_value_nest_reads_each_value_once():
    """Each nested value stands in its parent as `${_}`, so the 1,300 values are one command, read once. A quarter of
    SIZE: each space starts a word the per-word store check reads (#293)."""
    n = _times("${GIT_PAGER:=less }") // 4
    c = _timed("echo " + "${GIT_PAGER:=less " * n + "}" * n, SLACK)
    assert (c.category, gate.decide(c).outcome) == ("read", "allow"), c
