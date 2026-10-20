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
RecursionError or by its outcome, except the two marked as guards for the per-call memo and the three arithmetic
nests of 500 levels, which main still reads (it raises RecursionError for one of 1,000).

Every command string here is data for exo.gate.classify/decide; none is run."""
import random
import time

import pytest

from exo import gate

FORCE = gate.normalize_policies({"gates": {"deny": ["force_push"]}})
DELETE = gate.normalize_policies({"gates": {"deny": ["delete"]}})
PUSH = "git push --force origin main"
BOUND = 4.0       # CPU seconds per line; each takes under 0.8 s here (main: 0.9 s at 20 levels, a minute at 200)
LEXERS = 1000     # lexers one line may build at any depth; about 200 here (main: 15,185 at 20 levels, 41,845 at 25)


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


# --- the end of a nest past the cap is where the lexer ends it ---------------------------------------------

_PIECES = ["$(", "<(", ">(", "(", ")", ")", ")", "'", '"', "`", "\\", "\n", "\n", "#", " ", " ", ";", "&", "|", "<<",
           "<<-", "EOF", "'EOF'", '"EOF"', 'E"O"F', "x", "1", "2>", "<", ">", "&>", "$((", "${", "}", "$'", '$"', "$x",
           "\t", "EOF)", "\\\n", "\\'", '\\"', "\\`", "$", "<<<", ">&", "3<", "\tEOF", "EOF ", "<<$(e)", "<<`e`",
           "<<$'E'", "\r"]


def test_the_nest_end_is_the_lexer_s():
    """On random text from what either reader treats specially, _nest_end ends where the lexer ends a $( ) and
    leaves the same here-docs waiting, with the same bodies read."""
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
