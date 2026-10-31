"""#1047: the text `printf` or `echo -e` pipes into a shell is also read as bash's builtin writes it.

On main d995d667 the gate read that text as written. It did not expand the escapes printf and `echo -e` expand,
nor fill a printf format. So `printf 'cp /tmp/x ~/.exo/config.json\n' | bash`, the same line piped to `sudo -s`,
and the `echo -e` and `\x0a` forms were only `opaque_exec`: the target read as a file named `config.json\n`.
`printf 'r\x6d -rf /srv/data' | bash` hid its delete. Under `deny: [store_write]` and `deny: [delete]` those lines
asked, and a policy that allows `opaque_exec` let them run. A shell now also reads what the command writes, with
its escapes expanded and its `%s`, `%b` and `%%` filled, beside the text as written, so no decision is lower than
main's. A text the gate cannot be sure of (an expansion, another conversion, an option) is read as before, and so
is `echo` without `-e`, which writes its words as they are in bash. Classification and decision only: nothing here
runs the lines.
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
