"""#1104: a pipe from `echo`, `printf` or `cat <<<` into a shell is read as the command it prints, also when the pipe
is the first command of a group, a body or a condition, or comes right after `!` or `time`.

On main 6ec085b6 `echo 'rm -rf /srv/data' | bash` was a delete, and so was the same pipe after `true;` in a group.
But `{ echo 'rm -rf /srv/data' | bash; }`, the pipe as the first command of a `then`, `else` or `do` body, of a
`while` or `if` condition, of a function or coproc body, or after `!` or `time`, was only `opaque_exec`. The gate
took the reserved words that open the group or body, which bash keeps out of the command, as the program that writes
to the pipe. So the printed command was not read: under `{opaque_exec: allow, delete: deny}` the wrapped line was
allowed while the plain one was denied. Each row here now decides as the same line with `true;` before the pipe.
Classification and decision only: nothing here runs the lines.
"""
import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


def _pol(**gates):
    return normalize_policies({"gates": gates})


# The policies of the issue: the default, opaque_exec allowed, that with delete denied, a store-write deny and a
# reserved delete entry.
POLICIES = [_pol(), _pol(categories={"opaque_exec": "allow"}),
            _pol(categories={"opaque_exec": "allow", "delete": "deny"}), _pol(deny=["store_write"]),
            _pol(reserved=["delete"])]


def _decided(line):
    """The line's category, then each policy's outcome and the categories its reserved entries matched."""
    c = sh(line)
    return (c.category, *((d.outcome, sorted({cat for cat, _ in d.reserved}))
                          for d in (decide(c, p) for p in POLICIES)))


A, D, R = ("ask", []), ("deny", []), ("ask", ["delete"])
DELETE = ("delete", A, A, D, A, R)
STORE = ("store_write", A, A, A, D, A)
OPAQUE = ("opaque_exec", A, ("allow", []), ("allow", []), A, A)

RM = "rm -rf /srv/data"
CP = "cp /tmp/evil ~/.exo/policies/gates.yaml"

# Where the pipe stands: the issue's table, then nested forms. `%s` is the pipe.
SHAPES = [
    "{ %s; }",
    "if true; then %s; fi",
    "for i in 1; do %s; done",
    "! %s",
    "time %s",
    "if false; then :; else %s; fi",
    "if false; then :; elif true; then %s; fi",
    "while true; do %s; done",
    "until false; do %s; done",
    "f() { %s; }",
    "function f { %s; }",
    "coproc W { %s; }",
    "coproc { %s; }",
    "! { %s; }",
    "time { %s; }",
    "if true; then { %s; }; fi",
    "time -p %s",
    "! time %s",
    "time ! %s",
    "if %s; then :; fi",
    "while %s; do :; done",
    # nested and neighbouring forms
    "time -p -- %s",
    "time -- %s",
    "until %s; do :; done",
    "if false; then :; elif %s; then :; fi",
    "select x in a; do %s; done",
    "for i in 1; { %s; }",
    "function f() { %s; }",
    "function f while %s; do :; done",
    "! coproc { %s; }",
    "coproc W while %s; do :; done",
    "coproc W { { %s; }; }",
    "{ { %s; }; }",
    "{ time ! %s; }",
    "if true; then ! time -p %s; fi",
    "while true; do { %s; }; done",
    "until false; do if true; then %s; fi; done",
    "case x in x) { %s; };; esac",
    "case x in (x) if true; then %s; fi;; esac",
    "function f { while true; do %s; done; }",
    "f() { if true; then time %s; fi; }",
    "x=$( { %s; } )",
    "bash -c '{ %s; }'",
]
# What writes to the pipe and what reads it. A compound that reads it is fed the text and still marked as fed a stdin
# the gate cannot read, as on main, so ssh in it still asks.
PIPES = [
    (f"echo '{RM}' | bash", DELETE),
    (f"printf '{RM}' | bash", DELETE),
    (f"echo '{RM}' | sh", DELETE),
    (f"cat <<< '{RM}' | bash", DELETE),
    (f"echo '{RM}' | sudo -s", DELETE),
    (f"echo '{RM}' | {{ bash; }}", DELETE),
    (f"echo '{RM}' | ( bash )", DELETE),
    (f"echo '{RM}' | while read l; do bash; done", DELETE),
    (f"echo '{RM}' | {{ ssh host; }}", DELETE),
    (f"echo '{RM}' | ssh host", DELETE),
    (f"echo '{CP}' | bash", STORE),
    (f"printf '{CP}' | bash", STORE),
]


# Shapes where main found the store path in a word of a `coproc`, `function` or `time` it read as a program, or in a
# `bash -c` text, so a store-write pipe there decided as its twin on main already. Only their delete rows are kept.
STORE_SEEN = {"function f { %s; }", "coproc W { %s; }", "coproc { %s; }", "time { %s; }", "time ! %s",
              "function f while %s; do :; done", "! coproc { %s; }", "coproc W while %s; do :; done",
              "coproc W { { %s; }; }", "{ time ! %s; }", "bash -c '{ %s; }'"}


def _cases():
    for shape in SHAPES:
        for pipe, want in PIPES:
            if want == STORE and shape in STORE_SEEN:
                continue
            if shape.startswith("bash -c '") and "'" in pipe:
                pipe = pipe.replace("'", '"')
            yield shape, pipe, want


@pytest.mark.parametrize("shape, pipe, want", list(_cases()))
def test_the_first_pipe_reads_as_after_true(shape, pipe, want):
    assert _decided(shape % pipe) == _decided(shape % f"true; {pipe}") == want


# A heredoc into the shell, as the first command of a group or body.
HEREDOCS = [
    f"{{ cat <<EOF | bash; }}\n{RM}\nEOF",
    f"if true; then cat <<EOF | bash; fi\n{RM}\nEOF",
    f"while true; do cat <<EOF | bash; done\n{RM}\nEOF",
    f"! cat <<EOF | bash\n{RM}\nEOF",
    f"function f {{ cat <<EOF | bash; }}\n{RM}\nEOF",
]


@pytest.mark.parametrize("line", HEREDOCS)
def test_a_heredoc_pipe_first_in_a_group_reads_as_after_true(line):
    assert _decided(line) == _decided(line.replace("cat <<EOF", "true; cat <<EOF", 1)) == DELETE


# #1047's escapes (PR #1089): what printf or `echo -e` writes is read too, now also as the first command of a group or
# body, so the escaped form decides as its written-out twin. #1089's rows put a `:` before each such pipe for this
# gap. The coproc and function shapes are the ones its review named.
ESCAPED = [
    (r"printf '\x72m -rf /srv/data' | bash", f"printf '{RM}' | bash", DELETE),
    (r"printf 'r\x6d -rf /srv/data\n' | bash", f"printf '{RM}' | bash", DELETE),
    (r"echo -e 'r\x6d -rf /srv/data' | sh", f"echo '{RM}' | sh", DELETE),
    (r"printf '%s\n' 'rm -rf /srv/data' | bash", f"printf '{RM}' | bash", DELETE),
    (r"printf 'cp /tmp/evil ~/.exo/policies/gates.yam\x6c' | bash", f"printf '{CP}' | bash", STORE),
]
ESCAPED_SHAPES = [
    "{ %s; }", "if true; then %s; fi", "for i in 1; do %s; done", "! %s", "time %s", "while %s; do :; done",
    "! coproc { %s; }", "coproc W { %s; }", "function f { %s; }", "if true; then coproc { %s; }; fi",
    "if false; then :; else coproc { %s; }; fi", "{ coproc { %s; }; }", "! time coproc { %s; }",
    "coproc W { for i in 1; do %s; done; }", "function f { for i in 1; do %s; done; }",
    "{ coproc W { while :; do %s; done; }; }",
]


# As above, main found the store path in these shapes' words already, so only their delete rows are kept.
ESCAPED_STORE_SEEN = {"! coproc { %s; }", "coproc W { %s; }", "function f { %s; }", "if true; then coproc { %s; }; fi",
                      "if false; then :; else coproc { %s; }; fi", "{ coproc { %s; }; }", "! time coproc { %s; }"}


def _escaped_cases():
    for escaped, written, want in ESCAPED:
        for shape in ESCAPED_SHAPES:
            if not (want == STORE and shape in ESCAPED_STORE_SEEN):
                yield shape, escaped, written, want


@pytest.mark.parametrize("shape, escaped, written, want", list(_escaped_cases()))
def test_an_escaped_pipe_first_in_a_group_reads_as_written_out(shape, escaped, written, want):
    assert _decided(shape % escaped) == _decided(shape % written) == _decided(shape % f"true; {escaped}") == want


# Lines that keep main's reading, each beside a line this change reads. A text that reads as no more than opaque_exec
# stays so. A quoted `{` or `time`, and a prefix command (`command`, `nohup`), are commands bash runs, not reserved
# words: they are read as on main, as is a coproc of a simple command, whose output goes to the coprocess and not down
# the pipe. A remote `bash` behind ssh reads no pipe on main either, and a word bash expands is not read.
KEPT = [
    ("{ echo ls | bash; }", OPAQUE, f"{{ echo '{RM}' | bash; }}"),
    ("if true; then echo 'git status' | bash; fi", OPAQUE, f"if true; then echo '{RM}' | bash; fi"),
    (f"'{{' echo '{RM}' | bash", OPAQUE, f"{{ echo '{RM}' | bash; }}"),
    (f"\"time\" echo '{RM}' | bash", OPAQUE, f"time echo '{RM}' | bash"),
    (f"{{ command echo '{RM}' | bash; }}", OPAQUE, f"{{ echo '{RM}' | bash; }}"),
    (f"{{ nohup echo '{RM}' | bash; }}", OPAQUE, f"{{ echo '{RM}' | bash; }}"),
    (f"coproc echo '{RM}' | bash", OPAQUE, f"coproc {{ echo '{RM}' | bash; }}"),
    (f"{{ echo '{RM}' | ssh host bash; }}", OPAQUE, f"{{ echo '{RM}' | ssh host; }}"),
    (f"{{ echo $X '{RM}' | bash; }}", OPAQUE, f"{{ echo '{RM}' | bash; }}"),
]


@pytest.mark.parametrize("line, want, read", KEPT)
def test_lines_that_keep_mains_reading(line, want, read):
    assert (_decided(line), _decided(read)) == (want, DELETE)


# A post's body from the first command of a group is still not read (the stricter side, #208), also through a
# compound, while the same pipe into a shell is read.
@pytest.mark.parametrize("line", [f"{{ echo '{RM}' | gh pr comment 1 -F -; }}",
                                  f"if true; then echo '{RM}' | gh pr comment 1 -F -; fi",
                                  f"{{ echo '{RM}' | {{ gh pr comment 1 -F -; }}; }}"])
def test_a_post_body_from_the_first_command_stays_unseen(line):
    c = sh(line)
    assert c.category == "github_post"
    assert gate.body_unseen(c)
    assert _decided(line.replace("gh pr comment 1 -F -", "bash")) == DELETE


# Such a pipe's text is read in a reading of the call of its own (_lead_reading), with budgets of its own, beside
# main's reading. A text that uses up a cap there, here the parts in `${..}` words one call opens (_WORD_PARTS), leaves
# the parts after it as main's reading read them: the delete in a later `bash -c` or `eval` text is still denied.
FILLER = " ".join(["${a:-$(true)}"] * 30)


@pytest.mark.parametrize("later", [f"bash -c 'echo ${{x:-$({RM})}}'", f"eval 'echo ${{x:-$({RM})}}'"])
@pytest.mark.parametrize("shape", ["{ %s; }", "if true; then %s; fi", "for i in 1; do %s; done", "! %s", "time %s",
                                   "function f { %s; }"])
def test_a_text_that_uses_up_a_cap_leaves_the_parts_after_it_read(shape, later):
    assert _decided(shape % f"echo 'echo {FILLER}' | bash" + f"; {later}") == DELETE


def test_the_pipe_lead_skips_only_reserved_words():
    words = lambda s: gate._parse(s)[0][0].words  # noqa: E731
    lead = lambda s: [w.text for w in words(s)[gate._pipe_lead(words(s)):]]  # noqa: E731
    assert lead("{ echo x") == ["echo", "x"]
    assert lead("! time -p -- echo x") == ["echo", "x"]
    assert lead("time ! echo x") == ["echo", "x"]
    assert lead("coproc W { echo x") == ["echo", "x"]
    assert lead("coproc { echo x") == ["echo", "x"]
    assert lead("function f { echo x") == ["echo", "x"]
    assert lead("coproc echo x") == ["coproc", "echo", "x"]
    assert lead("coproc W echo x") == ["coproc", "W", "echo", "x"]
    assert lead("'{' echo x") == ["{", "echo", "x"]
    assert lead("time -o f echo x") == ["-o", "f", "echo", "x"]
