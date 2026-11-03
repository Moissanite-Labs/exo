"""#1126: BusyBox's shell and applets are read as the program each one names.

BusyBox is one binary that runs the applet its first operand names: `busybox sh`, `busybox rm -rf /srv/data`. Its
shells also go by `ash` and `hush`. On main a2f6ec3d the gate read `busybox sh` as busybox running a script named
`sh`, so it read neither the `-c` text, the stdin, a heredoc nor #1047's printed text. `ash` and `hush` were
programs the gate does not know, and every applet was only `opaque_exec`. Under `deny: [delete]` or
`deny: [store_write]` such a line asked where the same line with `sh` denies. Under `opaque_exec: allow` it ran
where that line asks. A store write piped into `busybox sh` was no `no_grant` call, so bypass ran it
(plugins/exo-gate/tests/test_bypass.py has those rows).

The gate now also reads such a call in a second reading, beside main's. There busybox with an applet runs that
applet, also by path or behind a prefix, and `ash` and `hush` are shells. `decide` keeps the strictest of the two,
and a store write or a secret leads, so each line reads as its twin with `sh` reads. busybox's own options and busybox with no applet keep main's reading alone. Where the second reading alone would
decide lower (`busybox ls` is `ls`, a read), main's `opaque_exec` still decides. Those rows are pinned here and
left for a follow-up. Classification and decision only: nothing here runs the lines.
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
# (default, deny: [store_write], deny: [delete], opaque_exec: allow, no_grant)
DELETE = ("ask", "ask", "deny", "ask", False)
STORE = ("ask", "deny", "ask", "ask", True)
FORCE = ("ask", "ask", "ask", "ask", False)
SECRET = ("ask", "ask", "ask", "ask", True)
OPAQUE = ("ask", "ask", "ask", "allow", False)
RM = "rm -rf /srv/data"
CP = "cp /tmp/x ~/.exo/config.json"
PUSH = "git push --force origin main"
HIDDEN = r"r\x6d -rf /srv/data"         # #1047: printf writes `rm -rf /srv/data`


def _decided(line):
    c = sh(line)
    return (*(decide(c, p).outcome for p in POLICIES), gate.no_grant(c))


def _reads_as(line, twin):
    """The line decides as its twin with `sh` does, and leads with the same category and summary."""
    c, t = sh(line), sh(twin)
    assert (c.category, c.summary) == (t.category, t.summary)
    assert _decided(line) == _decided(twin)


# The issue's repro and table: each line, the same line with `sh` (its twin), and what the twin decides. On main
# each line was OPAQUE.
ISSUE_ROWS = [
    (f"printf '{RM}' | busybox sh", f"printf '{RM}' | sh", DELETE),
    (f"printf '{RM}' | ash", f"printf '{RM}' | sh", DELETE),
    (f"echo '{CP}' | busybox sh", f"echo '{CP}' | sh", STORE),
    (f"echo '{CP}' | ash", f"echo '{CP}' | sh", STORE),
    (f"busybox sh -c '{PUSH}'", f"sh -c '{PUSH}'", FORCE),
    (f"busybox {RM}", RM, DELETE),
    # a pipe
    *[(f"printf '{RM}' | {s}", f"printf '{RM}' | sh", DELETE)
      for s in ("busybox ash", "busybox hush", "busybox bash", "busybox env sh", "/bin/busybox sh", "/bin/ash",
                "env ash", "hush")],
    # #1047's printed text
    *[(f"printf '{HIDDEN}' | {s}", f"printf '{HIDDEN}' | sh", DELETE)
      for s in ("busybox sh", "busybox ash", "ash", "/bin/ash")],
    # a heredoc
    *[(f"{s} <<EOF\n{RM}\nEOF", f"sh <<EOF\n{RM}\nEOF", DELETE) for s in ("busybox sh", "busybox ash", "ash")],
    # -c
    *[(f"{s} -c '{RM}'", f"sh -c '{RM}'", DELETE)
      for s in ("busybox sh", "busybox ash", "busybox bash", "busybox env sh", "ash", "/bin/ash", "hush")],
    (f"ash -c '{PUSH}'", f"sh -c '{PUSH}'", FORCE),
    (f"printf '{CP}\\n' | busybox sh", f"printf '{CP}\\n' | sh", STORE),
    (f"printf '{CP}\\n' | ash", f"printf '{CP}\\n' | sh", STORE),
]


@pytest.mark.parametrize("line, twin, want", ISSUE_ROWS)
def test_issue_rows_decide_as_the_same_line_with_sh(line, twin, want):
    assert _decided(twin) == want
    _reads_as(line, twin)


# More spellings: busybox by path and behind prefixes and wrappers, an applet behind busybox's own env, busybox run
# by busybox, the applet by path, the store write as a heredoc and here-string, options before the text, and
# _cd_walk's reading of a `cd` into a secret folder before the shell. On main each was OPAQUE but `busybox cp ..`
# into the store, which main's word scan already read as its twin.
MORE_ROWS = [
    *[(f"printf '{RM}' | {s}", f"printf '{RM}' | sh", DELETE)
      for s in ("/usr/bin/busybox sh", "sudo busybox sh", "nice busybox sh", "env busybox sh",
                "timeout 5 busybox sh", "command busybox sh", "busybox busybox sh", "busybox /bin/sh", "{ ash; }",
                "/bin/hush", "/usr/bin/ash", "busybox sh -s", "ash -s", "busybox sh -e", "ash -x")],
    *[(f"echo '{CP}' | {s}", f"echo '{CP}' | sh", STORE)
      for s in ("busybox ash", "busybox hush", "hush", "/bin/ash", "sudo busybox sh", "busybox env sh")],
    *[(f"{s} <<EOF\n{CP}\nEOF", f"sh <<EOF\n{CP}\nEOF", STORE) for s in ("busybox sh", "ash", "hush")],
    *[(f"{s} <<< '{RM}'", f"sh <<< '{RM}'", DELETE) for s in ("busybox sh", "ash", "busybox hush")],
    *[(f"{s} -c '{RM}'", f"sh -c '{RM}'", DELETE)
      for s in ("sudo busybox sh", "/usr/bin/busybox ash", "busybox hush", "ash -e", "setsid busybox sh")],
    *[(f"cd ~/.kube && {s} -c 'cat config'", "cd ~/.kube && sh -c 'cat config'", SECRET)
      for s in ("busybox sh", "busybox ash", "sudo busybox sh", "ash", "/bin/ash", "hush")],
    (f"sudo busybox {RM}", f"sudo {RM}", DELETE),
    (f"/bin/busybox {RM}", RM, DELETE),
    (f"/usr/bin/busybox {RM}", RM, DELETE),
    (f"env busybox {RM}", RM, DELETE),
    (f"busybox env {RM}", RM, DELETE),
    (f"busybox busybox {RM}", RM, DELETE),
    (f"busybox /bin/rm {RM[3:]}", RM, DELETE),
    (f"busybox {PUSH}", PUSH, FORCE),
    (f"busybox {CP}", CP, STORE),
    (f"find . -exec busybox {RM} \\;", f"find . -exec {RM} \\;", DELETE),
]


@pytest.mark.parametrize("line, twin, want", MORE_ROWS)
def test_more_spellings_decide_as_the_same_line_with_sh(line, twin, want):
    assert _decided(twin) == want
    _reads_as(line, twin)


# #1104 (main a2f6ec3d) reads an echo or printf pipe into a shell that starts a group, a then or do body, or follows
# `!` or `time`. With BusyBox's shell in that place both readings meet: the pipe's text is read, as with `sh`. On main
# each line here was OPAQUE.
LEAD_ROWS = [
    *[(f"{{ echo '{RM}' | {s}; }}", f"{{ echo '{RM}' | sh; }}", DELETE) for s in ("busybox sh", "ash", "busybox hush")],
    *[(f"if true; then printf '{RM}' | {s}; fi", f"if true; then printf '{RM}' | sh; fi", DELETE)
      for s in ("busybox ash", "/bin/ash")],
    *[(f"while true; do echo '{RM}' | {s}; done", f"while true; do echo '{RM}' | sh; done", DELETE)
      for s in ("sudo busybox sh", "hush")],
    *[(f"! echo '{RM}' | {s}", f"! echo '{RM}' | sh", DELETE) for s in ("busybox sh", "ash")],
    *[(f"time printf '{RM}' | {s}", f"time printf '{RM}' | sh", DELETE) for s in ("busybox ash", "hush")],
    *[(f"{{ echo '{CP}' | {s}; }}", f"{{ echo '{CP}' | sh; }}", STORE) for s in ("busybox sh", "ash")],
    *[(f"time printf '{CP}\\n' | {s}", f"time printf '{CP}\\n' | sh", STORE) for s in ("busybox sh", "/bin/ash")],
    *[(f"{{ printf '{HIDDEN}' | {s}; }}", f"{{ printf '{HIDDEN}' | sh; }}", DELETE) for s in ("busybox sh", "ash")],
    (f"time busybox {RM}", f"time {RM}", DELETE),
    (f"! ash -c '{RM}'", f"! sh -c '{RM}'", DELETE),
]


@pytest.mark.parametrize("line, twin, want", LEAD_ROWS)
def test_a_pipe_after_reserved_words_into_busybox_reads_as_with_sh(line, twin, want):
    assert _decided(twin) == want
    _reads_as(line, twin)


# busybox's own options and busybox with no applet run no applet, so the call keeps main's reading alone: no second
# reading, the same category, outcomes and summary as on main a2f6ec3d. A bare _PREFIXES entry for busybox read the
# first five as reads and allowed them (the issue's warning).
KEPT = [
    ("busybox --install", "opaque_exec", OPAQUE, "busybox runs shell commands"),
    ("busybox --install -s /usr/local/bin", "opaque_exec", OPAQUE, "busybox runs the script /usr/local/bin"),
    ("busybox --list", "opaque_exec", OPAQUE, "busybox runs shell commands"),
    ("busybox --list-full", "opaque_exec", OPAQUE, "busybox runs shell commands"),
    ("busybox", "opaque_exec", OPAQUE, "busybox runs shell commands"),
    (f"echo '{RM}' | busybox", "delete", DELETE, "rm /srv/data"),
    (f"busybox -c '{RM}'", "delete", DELETE, "rm /srv/data"),
    (f"busybox <<EOF\n{RM}\nEOF", "delete", DELETE, "rm /srv/data"),
    ("busybox --help", "read", ("allow",) * 4 + (False,), "busybox --help"),
    ("busybox --help rm", "read", ("allow",) * 4 + (False,), "busybox --help"),
    ("busybox - rm", "opaque_exec", OPAQUE, "busybox runs the script rm"),
    (f"busybox -s '{RM}'", "opaque_exec", OPAQUE, f"busybox runs the script {RM}"),
    ("busybox < x.sh", "opaque_exec", OPAQUE, "busybox runs shell commands"),
    (f"toybox sh -c '{RM}'", "opaque_exec", OPAQUE, "runs toybox, a program the gate does not know"),
]


@pytest.mark.parametrize("line, category, want, summary", KEPT)
def test_busybox_with_no_applet_keeps_mains_reading(line, category, want, summary):
    c = sh(line)
    assert len(gate.readings(c)) == 1
    assert (c.category, c.summary) == (category, summary)
    assert _decided(line) == want


# Left open: where the second reading reads a read (`busybox ls` is `ls`), main's `opaque_exec` still decides, so the
# line asks as on main and `opaque_exec: allow` allows it. The same act as `ls`, but taking it would lower the line
# against main, which needs its own decision. Each row has both readings.
LEFT_OPEN = ["busybox ls", "busybox cat README.md", "busybox echo hi", "busybox true", "busybox pwd",
             "busybox sh --version", "busybox ash --help", "ash --version", "/bin/ash --version", "hush --help"]


@pytest.mark.parametrize("line", LEFT_OPEN)
def test_an_applet_that_only_reads_still_asks_as_on_main(line):
    c = sh(line)
    assert {r.category for r in gate.readings(c)} == {"opaque_exec", "read"}
    assert c.category == "opaque_exec" and _decided(line) == OPAQUE


def _rule(command, decision="allow"):
    return {"match": {"command": command}, "decision": decision, "reason": f"{decision} {command}"}


# #458 and #1082: the applet's command has its own words as its own text, with busybox's text as its whole, as the
# command xargs runs has. So on a line of two commands a rule for `^busybox` names neither the delete nor the push
# busybox runs: each denies by its category line, as it does without busybox. On main a2f6ec3d the rule named the
# `opaque_exec` part, the only one, and `opaque_exec: allow` let the line run.
RULES = [
    ({"rules": [_rule("^busybox")], "categories": {"delete": "deny", "opaque_exec": "allow"}},
     f"busybox {RM}; ls", "deny"),
    ({"rules": [_rule("^busybox")], "categories": {"force_push": "deny", "opaque_exec": "allow"}},
     f"busybox {PUSH}; ls", "deny"),
    ({"rules": [_rule("^busybox")], "categories": {"force_push": "deny", "opaque_exec": "allow"}},
     f"busybox sh -c '{PUSH}'", "deny"),
    ({"rules": [_rule("^ash")], "categories": {"force_push": "deny", "opaque_exec": "allow"}},
     f"ash -c '{PUSH}'", "deny"),
    ({"rules": [_rule(RM, "deny")]}, f"busybox {RM}", "deny"),
]


@pytest.mark.parametrize("gates, line, want", RULES)
def test_a_rule_for_busybox_or_ash_lowers_no_command_they_run(gates, line, want):
    assert decide(sh(line), normalize_policies({"gates": gates})).outcome == want
