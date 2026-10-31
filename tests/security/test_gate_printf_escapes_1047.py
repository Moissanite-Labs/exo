"""#1047: the text `printf` or `echo -e` pipes into a shell is also read as bash's builtin writes it.

On main d995d667 the gate read that text as written. It did not expand the escapes printf and `echo -e` expand,
nor fill a printf format. So `printf 'cp /tmp/x ~/.exo/config.json\n' | bash`, the same line piped to `sudo -s`,
and the `echo -e` and `\x0a` forms were only `opaque_exec`: the target read as a file named `config.json\n`.
`printf 'r\x6d -rf /srv/data' | bash` hid its delete. Under `deny: [store_write]` and `deny: [delete]` those lines
asked, and a policy that allows `opaque_exec` let them run. A shell now also reads what the command writes, with
its escapes expanded and its `%s`, `%b` and `%%` filled, beside the text as written. It reads it in a second reading
of the call, with budgets and memos of its own, beside main's reading, and decide keeps the strictest. A text the
gate cannot be sure of (an expansion, another conversion, an option) is read as before, and so is `echo` without
`-e`, which writes its words as they are in bash. Classification and decision only: nothing here runs the lines.
"""
import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


# The policies of the issue's table, in its order.
POLICIES = [normalize_policies({}), normalize_policies({"gates": {"deny": ["store_write"]}}),
            normalize_policies({"gates": {"deny": ["delete"]}}),
            normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}})]
# (category, default, deny: [store_write], deny: [delete], opaque_exec: allow)
STORE = ("store_write", "ask", "deny", "ask", "ask")
DELETE = ("delete", "ask", "ask", "deny", "ask")
OPAQUE = ("opaque_exec", "ask", "ask", "ask", "allow")
FORCE = ("force_push", "ask", "ask", "ask", "ask")
CP = "cp /tmp/x ~/.exo/config.json"
RM = "rm -rf /srv/data"
U = "\\" + "u"          # a backslash and u, spelt apart so no tool reads it as a character


def _decided(line):
    c = sh(line)
    return (c.category, *(decide(c, p).outcome for p in POLICIES))


# The issue's rows. On main the second to sixth were OPAQUE.
ISSUE_ROWS = [
    (f"printf '{CP}' | bash", STORE),
    (rf"printf '{CP}\n' | bash", STORE),
    (rf"printf '{CP}\n' | sudo -s", STORE),
    (rf"echo -e '{CP}\n' | bash", STORE),
    (rf"printf '{CP}\x0a' | bash", STORE),
    (r"printf 'r\x6d -rf /srv/data' | bash", DELETE),
    (rf"printf '{RM}\n' | bash", DELETE),
    (rf"printf '%s\n' '{CP}' | bash", STORE),
]


@pytest.mark.parametrize("line, want", ISSUE_ROWS)
def test_issue_rows(line, want):
    assert _decided(line) == want


# More spellings of the same text, other shells that run their stdin, and compounds fed the pipe. On main each was
# OPAQUE.
EXPANDED = [
    *[(rf"{p} '{CP}\n' | {s}", STORE) for p in ("printf", "printf --", "echo -e", "echo -ne", "echo -en",
                                                  "echo -e -n", "echo -Ee")
      for s in ("bash", "sh", "sudo -i", "{ bash; }", "( bash )", "setsid bash", "ssh host")],
    (rf'printf "{CP}\n" | bash', STORE),
    (rf"printf '{CP}\012' | bash", STORE),
    (rf"echo -e '{CP}\0012' | bash", STORE),
    (rf"printf '{CP}\r\n' | bash", STORE),
    (r"printf 'cp /tmp/x ~/.exo/con\146ig.json\n' | bash", STORE),
    (r"printf '\x63p /tmp/x ~/.exo/config.json' | bash", STORE),
    (r"printf 'echo hi \x3e ~/.exo/config.json' | bash", STORE),
    (r"printf 'cd ~/.exo\ncp /tmp/x config.json' | bash", STORE),
    (r"printf 'cp /tmp/x ~/.exo/config.%s\n' json | bash", STORE),
    (r"printf 'cp /tmp/x ~/.exo/%s\n' config.json policies/x | bash", STORE),
    (rf"printf '%b\n' '{CP}\c' | bash", STORE),
    (r"printf 'r\155 -rf /srv/data' | bash", DELETE),
    (r"printf 'r\0m -rf /srv/data' | bash", DELETE),
    (r"echo -e 'r\x6d -rf /srv/data' | bash", DELETE),
    (r"echo -e 'r\0155 -rf /srv/data' | bash", DELETE),
    (f"printf 'r{U}006d -rf /srv/data' | bash", DELETE),
    (r"printf 'rm\x20-rf\x20/srv/data' | bash", DELETE),
    (r"printf 'echo hi\nrm -rf /srv/data' | bash", DELETE),
    (f"printf '%s' '{RM}' | bash", DELETE),
    (r"printf '%s %s\n' rm '-rf /srv/data' | bash", DELETE),
    (r"printf '%b\n' 'r\x6d -rf /srv/data' | bash", DELETE),
    (r"""printf 'printf "rm -rf /srv/data\\n" | bash\n' | bash""", DELETE),
]


@pytest.mark.parametrize("line, want", EXPANDED)
def test_the_expanded_text_reads_as_the_plain_line(line, want):
    assert _decided(line) == want


# Lines that keep main's reading: echo without -e (bash writes its words as they are), -E last, options echo does
# not take, a format or word bash may change, a conversion other than %s, %b and %%, `printf -v`, a printf that is
# not the builtin, a remote `bash` behind ssh (main reads no pipe there either), readers that are not shells, and
# `\c`, where the text as written still holds the delete the shorter output drops. Each is as on main d995d667.
CONTROLS = [
    (rf"echo '{CP}\n' | bash", OPAQUE),
    (f"echo '{CP}' | bash", STORE),
    (rf"echo -E '{CP}\n' | bash", OPAQUE),
    (rf"echo -eE '{CP}\n' | bash", OPAQUE),
    (rf"echo -- -e '{RM}\n' | bash", OPAQUE),
    (rf"echo -x '{RM}\n' | bash", OPAQUE),
    (rf"echo a -e '{RM}\n' | bash", OPAQUE),
    (r"echo -e 'r\155 -rf /srv/data' | bash", OPAQUE),
    (f"printf \"$FMT\" '{RM}' | bash", OPAQUE),
    (r"""printf '%s\n' "$CMD" | bash""", OPAQUE),
    (r"printf '%s\n' ~/x | bash", OPAQUE),
    (r"printf '%s\n' *.sh | bash", OPAQUE),
    (r"printf '%s\n' {a,b} | bash", OPAQUE),
    (rf"printf '%d\n' '{RM}' | bash", OPAQUE),
    (rf"printf '%5s\n' '{RM}' | bash", OPAQUE),
    (rf"printf '%q\n' '{RM}' | bash", OPAQUE),
    (rf"printf -v x '{CP}\n' | bash", OPAQUE),
    (rf"command printf '{RM}\n' | bash", OPAQUE),
    (rf"/usr/bin/printf '{RM}\n' | bash", OPAQUE),
    (rf"printf '{CP}\n' | ssh host bash", OPAQUE),
    (r"""printf 'import os\nos.remove("x")\n' | python3""", OPAQUE),
    (r"printf 'a\nb\n' | grep a", ("read", "allow", "allow", "allow", "allow")),
    (r"printf 'SELECT 1;\n' | psql", ("read", "allow", "allow", "allow", "allow")),
    (rf"printf '%b' 'echo hi\c; {RM}' | bash", DELETE),
    (rf"echo -e 'echo hi\c; {RM}' | bash", DELETE),
    (rf"printf 'echo hi\c; {RM}' | bash", DELETE),
]


@pytest.mark.parametrize("line, want", CONTROLS)
def test_controls_keep_main_reading(line, want):
    assert _decided(line) == want


# The first build read the output in the line's own reading, so it spent the call's budgets there. A budget that a
# later command on the line reads then ran out before that command, and the command was cut short: it decided lower
# than on main. These rows spend one budget in the output first and then run a strict command that reads it. The
# output is now read in a reading of the call of its own, with budgets and memos of its own, and main's reading stays
# beside it, so each row decides as on main d995d667.
N16, N24 = " ".join(map(str, range(1, 17))), " ".join(map(str, range(1, 25)))
SUDO_LATER = "sudo -u $U echo rm -rf /srv/data"
WORD_LATER = """bash -c 'echo "${a:-$(rm -rf /srv/data)}"'"""
BUDGET_ROWS = [
    # _SUDO_BRANCHES: 16 readings in a call of a sudo word that may take the next word as its value (`-u $U`)
    (rf"printf 'sudo -u \x24U echo %s\n' {N16} | bash; {SUDO_LATER}", DELETE),
    (rf"printf 'sudo -u $U echo %s\n' {N16} | sudo -s; {SUDO_LATER}", DELETE),
    ("echo -e '" + r"\n".join(rf"sudo -u \x24U echo {i}" for i in range(1, 17)) + f"' | bash; {SUDO_LATER}", DELETE),
    ("echo -ne '" + r"\n".join(rf"sudo -u $U echo {i}" for i in range(1, 17)) + f"' | sh; {SUDO_LATER}", DELETE),
    # _WORD_PARTS: 24 parts in a call that only bash's reading opens (a $( ) in a ${..} word)
    (rf"""printf 'echo "\x24{{a:-\x24(true)}}" %s\n' {N24} | bash; {WORD_LATER}""", DELETE),
    (rf"""printf 'echo "${{a:-$(true)}}" %s\n' {N24} | bash; eval 'echo "${{a:-$(rm -rf /srv/data)}}"'""", DELETE),
    (rf"""printf 'echo "${{a:-$(true)}}" %s\n' {N24} | bash; sh -c 'x="${{a:-$(git push --force origin main)}}"'""",
     FORCE),
    ("echo -e '" + r"\n".join(rf'echo "\x24{{a:-\x24(true)}}" {i}' for i in range(1, 25)) + f"' | bash; {WORD_LATER}",
     DELETE),
]


@pytest.mark.parametrize("line, want", BUDGET_ROWS)
def test_the_output_spends_no_budget_of_main_reading(line, want):
    assert _decided(line) == want


# _EXO_PARTS: the path parts one call may build for names read from a store folder. The output joins 1,600 names to
# a folder 62 levels down (spelt `\x2eexo`, so the text as written names no store) and spends them. The `rm` in the
# store after it then failed closed as a credentials part that asks, and a policy that denies deletes and allows the
# rest by rule asked where main denies.
DEEP, NAMES = "/".join(f"d{j}" for j in range(62)), " ".join(f"n{i}" for i in range(1600))
DENY_DELETE_ALLOW_REST = normalize_policies({"gates": {
    "categories": {"delete": "deny"}, "rules": [{"match": {"command": ".*"}, "decision": "allow", "reason": "rest"}]}})


@pytest.mark.parametrize("line", [rf"printf 'cd ~/\x2eexo/{DEEP} && cat {NAMES}\n' | bash; cd ~/.exo && rm -rf run keys",
                                  rf"echo -e 'cd ~/\x2eexo/{DEEP} && cat {NAMES}' | bash; cd ~/.exo && rm -rf run"])
def test_the_output_spends_no_store_path_budget_of_main_reading(line):
    assert decide(sh(line.rsplit("; ", 1)[1]), DENY_DELETE_ALLOW_REST).outcome == "deny"
    assert decide(sh(line), DENY_DELETE_ALLOW_REST).outcome == "deny"
    assert _decided(line)[1:] == ("ask", "deny", "deny", "ask")


# The other budgets fail closed: past the brace budget a word counts as a store or secret hit, and past #479's budget
# the second reading of a loop is one store write. The first build decided these as main too. They stay so.
FAIL_CLOSED_ROWS = [
    (r"printf 'cat /tmp/\x7b1..1000} %s\n' 1 2 3 4 5 | bash; cp /tmp/x ~/.exo/{config.json,policies/p.yaml}", STORE),
    (r"printf 'cat /tmp/\x7b1..1000} %s\n' 1 2 3 4 5 | bash; rm -rf /srv/{a,b}", DELETE),
    (rf"printf 'echo %s\n' {' '.join(map(str, range(70)))} | bash; for i in 1 2; do cp /tmp/x config.json; cd ~/.exo; done",
     ("credentials", "ask", "deny", "ask", "ask")),
]


@pytest.mark.parametrize("line, want", FAIL_CLOSED_ROWS)
def test_budgets_that_fail_closed_decide_as_on_main(line, want):
    assert _decided(line) == want


# decide lists twenty violations of other posts on the line and counts the rest. Posts in the output took that room
# before a later post on the line, so a violation main lists was left out of the list. Each reading lists its own
# now, and decide keeps every entry any of them lists, as on main d995d667.
POSTING = normalize_policies({"posting": {"footer": "-- sent by an agent", "forbid_words": ["secret"]}})
LATER_POST = "gh pr comment 99 --body secret"


@pytest.mark.parametrize("line", [
    rf"printf 'gh pr comment %s --body hello\n' {N24} | bash; {LATER_POST}",
    "echo -e '" + r"\n".join(f"gh pr comment {i} --body hello" for i in range(1, 25)) + f"' | bash; {LATER_POST}",
])
def test_posts_in_the_output_leave_main_listed_violations_listed(line):
    main = decide(sh(LATER_POST), POSTING).violations
    got = decide(sh(line), POSTING).violations
    assert main == ["missing footer: -- sent by an agent", "forbidden word: secret"]
    assert "GitHub PR comment on #99: forbidden word: secret" in got
    assert "GitHub PR comment on #99: missing footer: -- sent by an agent" in got


@pytest.mark.parametrize("line", [rf"printf '{RM}\n' | gh pr comment 1 -F -",
                                  r"echo -e 'body\nmore' | gh pr comment 1 -F -"])
def test_a_gh_body_from_an_escape_stays_unseen(line):
    c = sh(line)
    assert c.category == "github_post"
    assert [decide(c, p).outcome for p in POLICIES] == ["ask"] * 4


# What bash 5.2's printf builtin writes for these words (checked against it), or None where the gate is not sure.
PRINTF = [
    (["a\\nb"], "a\nb"),
    (["\\101\\0101\\1012"], "A\x081A2"),
    (["\\x41\\x4g\\x|"], "A\x04g\\x|"),
    (["\\q\\e|\\E"], "\\q\x1b|\x1b"),
    (["\\\"|\\'|\\?"], "\"|'|?"),
    (["a\\cb"], "a\\cb"),
    (["%b|", "a\\cb", "zz"], "a"),
    (["x%s\\n", "a", "b", "c"], "xa\nxb\nxc\n"),
    (["%s-%s|", "a"], "a-|"),
    (["%s %b|", "a", "b\\cz", "c", "d"], "a b"),
    (["\\%s|", "a"], "\\a|"),
    (["%%\\n", "a"], "%\n"),
    (["--", "a\\tb"], "a\tb"),
    (["%b|", "\\101", "\\0101", "\\1012"], "A|A|A2|"),
    (["%b|", "\\'", "\\x41", "\\x"], "\\'|A|\\x|"),
    (["ab\\"], "ab\\"),
    (["\\777"], "\xff"),
    ([f"r{U}006d"], "rm"),
    ([f"{U}00e9|"], f"{U}00e9|"),
    (["-v", "x", "a"], None),
    (["-x"], None),
    (["%d", "1"], None),
    (["%5s", "a"], None),
    (["a%"], None),
    ([], None),
]


@pytest.mark.parametrize("args, want", PRINTF)
def test_printf_writes_what_bash_writes(args, want):
    assert gate._printf_out(args, 1 << 20) == want


def test_printf_output_past_the_room_is_not_read():
    assert gate._printf_out(["x%s"] + ["a"] * 10, 15) is None
    assert gate._printf_out(["x%s"] + ["a"] * 10, 20) == "xa" * 10


# What bash 5.2's `echo` writes, without its newline, once -e is on, and None without -e.
ECHO = [
    (["-e", "a\\nb"], "a\nb"),
    (["-ne", "a\\nb"], "a\nb"),
    (["-en", "a\\nb"], "a\nb"),
    (["-Ee", "a\\nb"], "a\nb"),
    (["-e", "-n", "a\\nb"], "a\nb"),
    (["-eE", "a\\nb"], None),
    (["a\\nb"], None),
    (["--", "-e", "a\\nb"], None),
    (["-x", "a\\nb"], None),
    (["-e", "--", "a\\nb"], "-- a\nb"),
    (["-e", "\\0101\\101\\x41"], "A\\101A"),
    (["-e", "\\'\\\"\\?"], "\\'\\\"\\?"),
    (["-e", "x\\cy", "z"], "x"),
    (["-e", "a\\n", "b\\t"], "a\n b\t"),
    (["-e", "\\x|\\xg"], "\\x|\\xg"),
    (["-e", "\\00101"], "\x081"),
]


@pytest.mark.parametrize("args, want", ECHO)
def test_echo_e_writes_what_bash_writes(args, want):
    assert gate._echo_out(args) == want
