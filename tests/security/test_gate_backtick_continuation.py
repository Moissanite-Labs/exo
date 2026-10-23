"""A backslash before a newline inside backticks is dropped, as bash drops it (#469).

Bash takes a `\\<newline>` out of a backquoted body before it parses the body, so ``echo `r\\<newline>m -rf /data` ``
runs `rm -rf /data`. The lexer turned the pair into a plain newline, so the gate read `r` and `m -rf /data` as two
unknown programs: an `opaque_exec` ask, which `categories: {opaque_exec: allow}` allowed and `deny: [delete]` did
not deny. Each row was checked against bash 5.2.21 with harmless commands in place of the dangerous ones.
Classification and decision only; nothing is executed.
"""
import random

import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies

DENY_DELETE = normalize_policies({"gates": {"deny": ["delete"]}})
DENY_PUSH = normalize_policies({"gates": {"deny": ["force_push"]}})
OPAQUE_ALLOW = normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}})
POLICIES = (None, DENY_DELETE, DENY_PUSH, OPAQUE_ALLOW)
NL = "\\\n"   # a backslash and a newline


def read(line):
    """The line's category and its outcome under the default policy, `deny: [delete]`, `deny: [force_push]` and
    `categories: {opaque_exec: allow}`."""
    c = classify({"tool": "bash", "args": {"command": line}})
    return c.category, [decide(c, p).outcome for p in POLICIES]


DELETE = ("delete", ["ask", "deny", "ask", "ask"])
PUSH = ("force_push", ["ask", "ask", "deny", "ask"])
READ = ("read", ["allow"] * 4)

# On main each row was opaque_exec: ask, ask, ask and allow.
JOINED = [
    (f"echo `r{NL}m -rf /data`", DELETE),
    (f"echo `git pu{NL}sh --force origin main`", PUSH),
    (f"x=`r{NL}m -rf /data`", DELETE),
    (f'echo "`r{NL}m -rf /data`"', DELETE),
    (f"echo `g{NL}it push --force origin main`; ls", PUSH),
    (f"echo `r{NL}{NL}m -rf /data`", DELETE),
    (f"echo `echo \\`r\\\\{NL}m -rf /data\\``", DELETE),   # nested: \\ is \, then the inner body drops \<newline>
    (f"cat <(echo `r{NL}m -rf /data`)", DELETE),
    (f"echo $(echo `r{NL}m -rf /data`)", DELETE),
    (f"bash -c 'echo `r{NL}m -rf /data`'", DELETE),
]


@pytest.mark.parametrize("line,expected", JOINED)
def test_a_continuation_in_backticks_joins_the_words_around_it(line, expected):
    assert read(line) == expected, line


# The same lines read the same without backticks or in $( ), on main too.
CONTROLS = [
    ("echo `rm -rf /data`", DELETE),
    (f"echo $(r{NL}m -rf /data)", DELETE),
    (f"r{NL}m -rf /data", DELETE),
    ("echo `r\\\\\nm -rf /data`", DELETE),   # \\ is a backslash, so the body holds \<newline> and its parse joins it
    ("echo `ls`", READ),
]


@pytest.mark.parametrize("line,expected", CONTROLS)
def test_the_controls_keep_their_reading(line, expected):
    assert read(line) == expected, line


# bash drops the pair before it parses the body, so quoting inside the body does not keep it. `echo 'a\<newline>b'`
# in backticks prints `ab`, and a here-doc line `x\<newline>` then `E` reads as `xE`, so the delimiter comes later.
BODY_QUOTING = [
    (f"echo `echo a{NL}b`", READ),
    (f"echo `echo 'a{NL}b'`", READ),
    (f"echo `echo \"r{NL}m\" -rf /data`", READ),
    (f"echo `cat <<'E'\nx{NL}E\nrm -rf /data\nE`", READ),
    (f"echo `cat <<E\nx{NL}E\nrm -rf /data\nE`", READ),
    ("echo `cat <<'E'\nx\nE\nrm -rf /data\nE`", DELETE),   # no continuation: the body ends at the first E
]


@pytest.mark.parametrize("line,expected", BODY_QUOTING)
def test_the_pair_is_gone_before_the_body_is_parsed(line, expected):
    assert read(line) == expected, line


def test_the_nest_end_reads_a_backquoted_delimiter_as_the_lexer_does():
    """Past the $( ) cap, _nest_end reads a here-doc delimiter written in backticks as the lexer does, so both
    wait for the same delimiter line after a continuation in it."""
    rnd = random.Random(469)
    pieces = ["<<`e`", "`a\\\nb`", "<<`x\\\ny`", "\\\n", "\n", "xy", "`xy`", " ", "e", ";", "$(", ")", "\\`"]
    for _ in range(2000):
        text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 20)))
        lx = gate._Lexer(text, scan=False, nest=-10**6)
        lx.run(closer=True)
        pending: list = []
        end, _ = gate._nest_end(text, 0, pending)
        assert end == lx.i, text
        assert [h.delim for h in pending] == [h.delim for h in lx.pending], text
