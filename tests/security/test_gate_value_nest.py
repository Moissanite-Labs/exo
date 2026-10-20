"""#334: a $( ) nest in a value #231 reads is read in bounded time, and a deny after it still fires.

#231 reads the values bash runs later as code: PROMPT_COMMAND, PS4, BROWSER, the pagers and askpass programs,
GIT_PROXY_COMMAND, `git -c core.sshCommand`, GIT_CONFIG_* and the rest of that family. Each $( ) in such a value was
listed at every level above it and classified there again, so a nest of n levels cost about n**4: 40 levels of `$(` in
PROMPT_COMMAND took 15 s on main 5f434b15, and 200 levels over a minute. From about 500 levels the lexer's recursion
raised RecursionError, the hook asked for the whole line, and the `git push --force` after the value was no longer
denied.

Now one classify call reads each line once per depth, and a lexer reads 16 levels of $( ), <( ) and $(( )) inside one
another. Past them it finds the end of the nest in one pass (gate._nest_end), and the nest stands as an unread part
that asks, also in an exported value. Each test here fails on main 5f434b15, by its time or lexer count, by
RecursionError or by its outcome, except the guards: the two for the per-call memo, the three arithmetic nests of 500
levels, which main still reads (it raises RecursionError for one of 1,000), and the three stand-ins written by hand.

Every command string here is data for exo.gate.classify/decide; none is run."""
import random
import time

import pytest

from exo import gate

FORCE = gate.normalize_policies({"gates": {"deny": ["force_push"]}})
DELETE = gate.normalize_policies({"gates": {"deny": ["delete"]}})
PUSH = "git push --force origin main"
BOUND = 4.0       # CPU seconds per line; each takes under 0.8 s here (main: 0.9 s at 20 levels, a minute at 200)
LEXERS = 1000     # lexers one line may build at any depth; at most 520 here (main: 15,185 at 20 levels, 41,845 at 25)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def classified(monkeypatch):
    """classify() in bounded CPU time (other suites on the machine do not count, #308) and with a bounded number
    of lexers, which grew as a power of the depth on main."""
    built = [0]
    init = gate._Lexer.__init__

    def counting(self, *a, **kw):
        built[0] += 1
        init(self, *a, **kw)

    monkeypatch.setattr(gate._Lexer, "__init__", counting)

    def run(cmd: str) -> gate.Classification:
        built[0] = 0
        t = time.process_time()
        c = gate.classify({"tool": "bash", "args": {"command": cmd}})
        took = time.process_time() - t
        assert took < BOUND, (took, cmd[:60])
        assert built[0] < LEXERS, (built[0], cmd[:60])
        return c
    return run


def _denied(c: gate.Classification) -> None:
    assert gate.decide(c, FORCE).outcome == "deny", c
    assert gate.decide(c).outcome == "ask", c


# --- the issue's table: `$(` repeated n times in PROMPT_COMMAND, then a force push -----------------------

@pytest.mark.parametrize("n", [20, 25, 30, 35, 40, 200, 500, 1000])
def test_the_issue_table(classified, n):
    _denied(classified(f"export PROMPT_COMMAND='{'$(' * n}'; {PUSH}"))


# --- each value #231 reads, with each kind of nest ---------------------------------------------------------

CARRIERS = {
    "PROMPT_COMMAND": "export PROMPT_COMMAND='{}'",
    "PS4": "export PS4='{}'",
    "BROWSER": "BROWSER='{}' xdg-open x",
    "PSQL_PAGER": "export PSQL_PAGER='{}'",
    "SUDO_ASKPASS": "export SUDO_ASKPASS='{}'",
    "GIT_PROXY_COMMAND": "export GIT_PROXY_COMMAND='{}'",
    "GIT_PAGER": "GIT_PAGER='{}' git log",
    "GIT_SSH_COMMAND": "export GIT_SSH_COMMAND='{}'",
    "git -c": "git -c core.sshCommand='{}' fetch",
    "git clone -c": "git clone -c core.sshCommand='{}' h:x/y",
    "git config": "git config core.pager '{}'",
    "GIT_CONFIG_PARAMETERS": "export GIT_CONFIG_PARAMETERS='\"core.pager\"=\"{}\"'",
    "GIT_CONFIG_KEY_0": "export GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0='{}'",
}
NESTS = {
    "open": lambda n: "$(" * n,
    "arith": lambda n: "$((" * n,
    "closed": lambda n: "$(echo " * n + "x" + ")" * n,
}


@pytest.mark.parametrize("nest", NESTS)
@pytest.mark.parametrize("carrier", CARRIERS)
def test_each_carrier_and_nest_at_1000_levels(classified, carrier, nest):
    _denied(classified(CARRIERS[carrier].format(NESTS[nest](1000)) + "; " + PUSH))


@pytest.mark.parametrize("n", [500, 5000])
@pytest.mark.parametrize("nest", NESTS)
@pytest.mark.parametrize("carrier", ["PROMPT_COMMAND", "PS4", "git -c"])
def test_no_recursion_error_at_500_and_5000_levels(classified, carrier, nest, n):
    """Main raises RecursionError for each of these but the arithmetic nest of 500 levels, which it reads with one
    lexer per level. That case guards against a regression."""
    _denied(classified(CARRIERS[carrier].format(NESTS[nest](n)) + "; " + PUSH))


@pytest.mark.parametrize("line", [
    "echo " + "$(echo " * 5000 + "x" + ")" * 5000,
    "echo " + "$((" * 5000 + "1" + "))" * 5000,
    "cat " + "<(cat " * 5000 + "x" + ")" * 5000,
    'echo "' + '$(echo "' * 5000 + "x" + '")' * 5000 + '"',
    "echo " + "$(echo $((1 + " * 2500 + "1" + ")))" * 2500,
], ids=["closed", "arith", "procsub", "double-quoted", "mixed"])
def test_a_closed_nest_at_the_top_of_a_line(classified, line):
    _denied(classified(line + "; " + PUSH))


# --- the cap: 16 levels are read as before, the 17th is not and asks -----------------------------------------

def test_sixteen_levels_are_read_as_before(classified):
    """The innermost command of a 16-level nest is still read, so a deny on it fires as on main (where it cost 0.7 s
    and 9,784 lexers)."""
    c = classified("export GIT_PAGER='" + "$(echo " * 15 + "$(rm -rf /srv/data)" + ")" * 15 + "'")
    assert gate.decide(c, DELETE).outcome == "deny", c


def test_past_the_cap_an_exported_value_asks(classified):
    """export drops a value it cannot read as one that runs nothing. A nest past the cap is kept, so the line asks:
    main read all 17 levels and allowed it, as it would allow `rm -rf` at the 17th."""
    for value in ("$(echo " * 17 + "$(rm -rf /srv/data)" + ")" * 17, "$(" * 17):
        c = classified(f"export GIT_PAGER='{value}'")
        assert "unread nest" in c.signals and gate.decide(c).outcome == "ask", c
        assert gate.decide(c, DELETE).outcome != "allow", c


# --- here-documents, where #231's later rounds found an exponential end and a RecursionError ----------------

def _delimiters(m: int, k: int = 17) -> str:
    """`cat <<` whose delimiter word is a k-level nest holding the next one, m times."""
    d = "x"
    for _ in range(m):
        d = "cat <<" + "$(" * k + d + ")" * k
    return d


def _bodies(k: int) -> str:
    """k here-documents, each in a $( ) in the body of the one before."""
    h = "x"
    for j in range(k, 0, -1):
        h = f"$(cat <<E{j}\n{h}\nE{j}\n)"
    return h


@pytest.mark.parametrize("line", [
    "echo " + "$(" * 17 + _delimiters(6) + ")" * 17,
    "echo " + "$(" * 17 + _delimiters(14) + ")" * 17,
    "cat <<" + "$(" * 40 + ")" * 40 + "; " + PUSH + "\n" + "$(" * 40 + ")" * 40 + "\n" + ": done",
], ids=["past-the-cap-6", "past-the-cap-14", "at-the-top"])
def test_a_heredoc_delimiter_holding_a_deep_nest(classified, line):
    _denied(classified(line + "; " + PUSH))


@pytest.mark.parametrize("k", [40, 200, 1000])
def test_heredocs_nested_in_one_another_s_bodies(classified, k):
    _denied(classified("echo " + _bodies(k) + "; " + PUSH))
    _denied(classified("export PROMPT_COMMAND='" + _bodies(k) + "'; " + PUSH))


# --- the levels around an unread part pair here-docs as the line does ----------------------------------------
# A level's source holds a stand-in for the part it does not read. Before, a here-doc delimiter holding such a part
# no longer matched its end line there, and a body read inside the part left its here-doc waiting there. Read again,
# the level took the lines after it for a body, so a deny on a command at a shallow level became an ask. Main read
# these to the end and denied them, slowly.

def _delim_first(k: int, m: int) -> str:
    """At level k, a here-doc whose delimiter is an m-level nest and whose body holds an open one, then `rm`."""
    d = "$(" * m + "x" + ")" * m
    deep = "$(cat <<" + d + "\n" + "$(" * m + "\n" + d + "\necho x\n)"
    return "$(echo " * (k - 1) + "$(echo " + deep + "; rm -rf /srv/data)" + ")" * (k - 1)


def _body_in_nest(k: int, m: int, delim: str = "EOF", end: str = "EOF") -> str:
    """At level k, a here-doc whose body lies in an m-level nest opened on its own line, then `rm` on a new line."""
    inner = "$(cat <<" + delim + " " + "$(echo " * m + "x\nbody\n" + end + "\n" + ")" * m + "\nrm -rf /srv/data\n)"
    return "$(echo " * (k - 1) + inner + ")" * (k - 1)


VALUE = {"top": "echo {}", "dq": 'echo "{}"', "PROMPT_COMMAND": "export PROMPT_COMMAND='{}'",
         "GIT_PAGER": "GIT_PAGER='{}' git log"}


@pytest.mark.parametrize("k,m", [(2, 24), (8, 17), (15, 16)])
@pytest.mark.parametrize("carrier", VALUE)
def test_a_command_after_a_heredoc_whose_delimiter_crosses_the_cap(classified, carrier, k, m):
    c = classified(VALUE[carrier].format(_delim_first(k, m)))
    assert gate.decide(c, DELETE).outcome == "deny", c


@pytest.mark.parametrize("k,m,delim,end", [
    (3, 30, "EOF", "EOF"), (12, 12, "'EOF'", "EOF"), (8, 20, "-EOF", "\tEOF"),
    (6, 14, "$($($($($($($($($($($($(x))))))))))))", "$($($($($($($($($($($($(x))))))))))))"),
], ids=["plain", "quoted", "tab", "delimiter-past-the-cap-too"])
@pytest.mark.parametrize("carrier", ["top", "PROMPT_COMMAND"])
def test_a_command_after_a_heredoc_whose_body_lies_in_the_unread_part(classified, carrier, k, m, delim, end):
    if "'" in delim and "'" in VALUE[carrier]:
        carrier = "dq"
    c = classified(VALUE[carrier].format(_body_in_nest(k, m, delim, end)))
    assert gate.decide(c, DELETE).outcome == "deny", c


@pytest.mark.parametrize("opener,end", [('"{}"', "{}"), ('y"{}"', "y{}"), ("\\y{}", "y{}"), ('-"{}"', "\t{}")],
                         ids=["quoted", "part-quoted", "escaped", "tab-quoted"])
@pytest.mark.parametrize("carrier", ["top", "PROMPT_COMMAND", "GIT_PAGER"])
def test_a_command_after_a_quoted_heredoc_delimiter_that_crosses_the_cap(classified, carrier, opener, end):
    """A delimiter with quotes or a backslash in it has text that is not its source. The levels around still spell
    its end line the way they read the delimiter, so the two pair and the `rm` after the here-doc is read."""
    d = "$(" * 17 + "x" + ")" * 17
    inner = "$(cat <<" + opener.format(d) + "\nb\n" + end.format(d) + "\n); rm -rf /srv/data"
    c = classified(VALUE[carrier].format("$(echo $(echo " + inner + "))"))
    assert gate.decide(c, DELETE).outcome == "deny", c


STAND_IN = "__exo_gate_nested_too_deep__"     # gate._TOO_DEEP, spelt out so this module loads on main too


@pytest.mark.parametrize("line", [
    STAND_IN + "\nrm -rf /srv/data",
    "echo $(" + STAND_IN + "\nrm -rf /srv/data\n)",
    "export GIT_PAGER='$(" + STAND_IN + "\nrm -rf /srv/data\n)'",
])
def test_a_stand_in_written_on_the_line_hides_no_command(line):
    """A level's stand-in can carry the end lines of bodies read in the part, and those lines are read as any line.
    So the stand-in's name written on the line by hand hides nothing after it (guard: main reads these too)."""
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert gate.decide(c, DELETE).outcome == "deny", c


def test_a_heredoc_body_read_in_the_unread_part_is_still_read(classified):
    """A here-doc opened at the cap whose body lies in the part past it still gets its body, and a backquoted
    command in it is read. Main reads it too, in 8 s."""
    c = classified("echo " + "$(echo " * 15 + "$(cat <<EOF " + "$(echo " * 3 + "x\n`rm -rf /srv/data`\nEOF\n"
                   + ")" * 4 + ")" * 15)
    assert "unread nest" in c.signals and gate.decide(c, DELETE).outcome == "deny", c


def test_an_arithmetic_nest_counts_toward_the_cap(classified):
    """$(( )) levels count like $( ) levels: 16 are read as before, and a $( ) inside the 17th is not read and asks.
    Without the unread part in its place, that text would be left out and the line allowed."""
    c = classified("echo " + "$((1+" * 15 + "$(rm -rf /srv/data)" + "))" * 15)
    assert gate.decide(c, DELETE).outcome == "deny", c
    for line in ("echo " + "$((1+" * 17 + "$(rm -rf /srv/data)" + "))" * 17,
                 "export GIT_PAGER='" + "$((1+" * 17 + "`rm -rf /srv/data`" + "))" * 17 + "'"):
        c = classified(line)
        assert "unread nest" in c.signals and gate.decide(c).outcome == "ask", c
        assert gate.decide(c, DELETE).outcome != "allow", c


def test_the_levels_around_an_unread_part_hold_a_stand_in():
    """Each level's source spells the part past the cap as a short stand-in, so classifying the levels never reads
    the deep text again (on main each source held all the levels below it)."""
    _, subs, _ = gate._parse("echo " + "$(echo " * 40 + "x" + ")" * 40)
    assert gate._TOO_DEEP == STAND_IN and STAND_IN in subs and max(map(len, subs)) < 200, max(map(len, subs))


def test_a_brace_past_the_cap_finds_its_end_without_a_lexer(monkeypatch):
    """_dollar_brace finds where a $( ) inside ${..} ends with a lexer that counts the levels around it toward the
    cap, and past the cap with _nest_end, which builds none. Either way it ends where the lexer ends it."""
    text = "${x:-" + "$(echo " * 40 + "x" + ")" * 40 + "}"
    nests: list[int] = []
    init = gate._Lexer.__init__

    def recording(self, *a, **kw):
        init(self, *a, **kw)
        nests.append(self.nest)

    monkeypatch.setattr(gate._Lexer, "__init__", recording)
    for nest in (0, gate._NEST - 3, gate._NEST, gate._NEST + 5):
        nests.clear()
        assert gate._dollar_brace(text, 0, False, nest)[1] == len(text) - 1, nest
        assert nests == ([nest + 1] if nest < gate._NEST else []), (nest, nests)


# --- the end of a nest past the cap is where the lexer ends it ---------------------------------------------

_PIECES = ["$(", "<(", ">(", "(", ")", ")", ")", "'", '"', "`", "\\", "\n", "\n", "#", " ", " ", ";", "&", "|", "<<",
           "<<-", "EOF", "'EOF'", '"EOF"', 'E"O"F', "x", "1", "2>", "<", ">", "&>", "$((", "${", "}", "$'", '$"', "$x",
           "\t", "EOF)", "\\\n", "\\'", '\\"', "\\`", "$", "<<<", ">&", "3<", "\tEOF", "EOF ", "<<$(e)", "<<`e`",
           "<<$'E'", "\r"]


def test_the_nest_end_is_the_lexer_s():
    """On random text from what either reader treats specially, _nest_end ends where the lexer ends a $( ) and
    leaves the same here-docs waiting, with the same bodies read. The pieces leave out a delimiter word holding
    `$(cat <<X ...)`, which the lexer reads as that here-doc's body: there the text a here-doc waits for can
    differ, while the end does not (docs/GATE.md)."""
    rnd = random.Random(334)
    for _ in range(3000):
        text = "".join(rnd.choice(_PIECES) for _ in range(rnd.randint(1, 60)))
        before = [gate._Heredoc("EOF", rnd.random() < 0.3, rnd.random() < 0.5)] if rnd.random() < 0.4 else []
        lx = gate._Lexer(text, scan=False, nest=-10**6)              # never cut
        lx.pending = [gate._Heredoc(h.delim, h.strip, h.quoted) for h in before]
        first = list(lx.pending)
        lx.run(closer=True)
        pending = [gate._Heredoc(h.delim, h.strip, h.quoted) for h in before]
        mine = list(pending)
        end, read = gate._nest_end(text, 0, pending)
        assert end == lx.i, text
        assert [(h.delim, h.strip, h.quoted, h.ambiguous) for h in pending] == \
            [(h.delim, h.strip, h.quoted, h.ambiguous) for h in lx.pending], text
        assert [(h.body, h.ambiguous) for h in mine] == [(h.body, h.ambiguous) for h in first], text
        assert read == [h for h in mine if all(h is not p for p in pending)], text


# --- guards for the per-call memo (these pass on main too) --------------------------------------------------

def test_a_part_marked_remote_stays_private_to_the_ssh_line():
    """The same text read as ssh's remote command and as a local one shares its parts in the memo. The ssh hop
    marks a copy, so the local `ls` keeps its own exposure."""
    c = gate.classify({"tool": "bash", "args": {"command": "ssh h 'rm -rf /tmp/x; ls'; bash -c 'rm -rf /tmp/x; ls'"}})
    exposures = sorted(p.exposure for p in gate._each_part(c) if p.summary == "ls")
    assert exposures == ["internal", "private"], exposures


def test_a_line_read_again_spends_the_budget_again():
    """Five copies of one $( ) spend the brace budget five times, as on main, so the line still counts as one that
    could name a secret store. A result read from the memo sets the budget to what reading it left."""
    line = "echo" + " $(echo {1..1000})" * 5
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert c.category == "credentials", c
    c = gate.classify({"tool": "bash", "args": {"command": "echo" + " $(echo {1..1000})" * 4}})
    assert c.category == "read", c
