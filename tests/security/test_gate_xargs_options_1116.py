"""#1116: the command after xargs's own options is read where xargs starts it.

On main (d4f3214d, and c05f0ca1) the gate skipped xargs's options one word at a time, and two words only after eight
short options written alone (`-I`, `-n`, `-P`, `-L`, `-s`, `-d`, `-E`, `-a`). So the value of any other option that
takes the next word was read as the program, and the command after it was not read. Under `{force_push: deny,
opaque_exec: allow}` with no rules, `xargs --max-args 1 git push --force origin main` was allowed while xargs runs the
push. The same held for GNU's other long options that take a value (also as an unambiguous prefix, `--max-a 1`), for
short options run together whose last letter takes the next word (`-rn 1`), and for BSD's `-J`, `-R` and `-S`. The
options are now read as getopt reads them for GNU and BSD xargs (`_xargs_start`). Where that start differs from main's,
the call is read again with each xargs's command read from it (`_xargs_reading`), beside main's reading, so no line
decides lower. The command read from that start has its own words as its own text (#458, `_run_own`). Each new row fails
on main c05f0ca1, but for the rows that keep main's reading. Classification and decision only: nothing here runs the
lines.
"""
from pathlib import Path

import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies

PUSH = "git push --force origin main"
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "principal-store" / "policies" / "gates.yaml"
OPAQUE_OK = {"opaque_exec": "allow"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def rule(command):
    return {"match": {"command": command}, "decision": "allow", "reason": f"allow {command}"}


def policies():
    """The issue's policy, then the default template, force_push ask, an allow rule keyed on xargs and one that names
    the push."""
    return [normalize_policies({"gates": {"categories": {"force_push": "deny", **OPAQUE_OK}}}),
            normalize_policies({"gates": gate.parse_yaml(TEMPLATE.read_text(encoding="utf-8"))}),
            normalize_policies({"gates": {"categories": {"force_push": "ask", **OPAQUE_OK}}}),
            normalize_policies({"gates": {"rules": [rule("^xargs")], "categories": {"force_push": "deny", **OPAQUE_OK}}}),
            normalize_policies({"gates": {"rules": [rule(f"^{PUSH}")], "categories": OPAQUE_OK}})]


def decided(line):
    c = classify({"tool": "bash", "args": {"command": line.replace("PUSH", PUSH)}})
    return (c.category, *((d.outcome, d.rule) for d in (decide(c, p) for p in policies())))


# The issue's table: each line was allowed under its policy on main d4f3214d, while xargs runs the push.
ISSUE_ROWS = ["--max-args 1", "--max-a 1", "--max-procs 4", "--max-chars 99", "--delimiter x", "--arg-file list",
              "--process-slot-var V", "-rn 1", "-0n 1", "-tP 4", "-J %", "-R 2 -I %", "-S 9 -I %"]
# More spellings of the same options, each allowed under the issue's policy on main d4f3214d too.
MORE_ROWS = ["--max-p 4", "--max-c 99", "--del x", "--arg list", "--proc V", "--process-slot-var=V --max-args 1",
             "-r --max-args 1", "--max-args 1 --", "-0rn 1", "-xs 99", "-rL 1", "-rI %", "-0d x", "-ra list",
             "-tE END", "-oJ %", "-rR 2 -I %", "-pS 9 -I %", "--null --max-procs 4", "-n 1 -rP 4"]


@pytest.mark.parametrize("options", ISSUE_ROWS + MORE_ROWS)
def test_the_command_after_the_options_is_read(options):
    line = f"xargs {options} PUSH"
    assert decided(line)[:2] == ("force_push", ("deny", "category"))      # main: opaque_exec, allow
    assert decided(line) == decided("xargs -n 1 PUSH")


def test_the_issues_pipe_row_is_read():
    line = r"printf 'a\n' | xargs --max-args 1 PUSH"
    assert decided(line)[:2] == ("force_push", ("deny", "category"))      # main: opaque_exec, allow
    assert decided(line) == decided(r"printf 'a\n' | xargs -n 1 PUSH")


# Another command after such options is read too: a delete and a post, under deny entries on them.
@pytest.mark.parametrize("options", ["--max-args 1", "-rn 1", "-J %"])
@pytest.mark.parametrize("command, category", [("rm -rf /srv/data", "delete"),
                                               ("gh pr comment 1 --body hi", "github_post")])
def test_other_commands_after_the_options_are_read(options, command, category):
    c = classify({"tool": "bash", "args": {"command": f"xargs {options} {command}"}})
    deny = normalize_policies({"gates": {"deny": [category], "categories": OPAQUE_OK}})
    assert (c.category, decide(c, deny).outcome) == (category, "deny")    # main: opaque_exec, allow


# Lines that keep main's reading, each beside a line this change reads (the third column, decided as the same line with
# `-n 1` in place of its options). Flags alone, `-I` with its value, GNU's `-i`, `-e` and `-l` with an attached value,
# GNU's long flags and the forms main read already decide as on main. A value option at the end of the line has no
# command to read, and xargs with no command runs echo: both stay xargs's opaque part alone. An unknown or ambiguous
# option makes xargs fail, so main's reading is the one kept. A word xargs takes as a value that main read as the
# program is still read there too (`-J rm echo hi`), and so is a word after `--` or a lone `-`, which xargs runs as the
# program.
KEPT = [
    ("xargs PUSH", "xargs -n 1 PUSH", "xargs --max-a 1 PUSH"),
    ("xargs -n1 PUSH", "xargs -n 1 PUSH", "xargs -rn 1 PUSH"),
    ("xargs --max-args=1 PUSH", "xargs -n 1 PUSH", "xargs --max-args 1 PUSH"),
    ("xargs --replace PUSH", "xargs -n 1 PUSH", "xargs --replace --arg-file list PUSH"),
    ("xargs -- PUSH", "xargs -n 1 PUSH", "xargs -J % -- PUSH"),
    *[(f"xargs -{f} PUSH", "xargs -n 1 PUSH", f"xargs -{f}{v} PUSH") for f, v in
      [("0", "n 1"), ("r", "P 4"), ("t", "s 99"), ("x", "L 1"), ("p", "R 2 -I %")]],
    ("xargs -I{} PUSH", "xargs -n 1 PUSH", "xargs -rI {} PUSH"),
    ("xargs -I {} PUSH", "xargs -n 1 PUSH", "xargs -tI {} PUSH"),
    ("xargs -i{} PUSH", "xargs -n 1 PUSH", "xargs -i{} -rn 1 PUSH"),
    ("xargs -i PUSH", "xargs -n 1 PUSH", "xargs -i -tP 4 PUSH"),
    ("xargs -eEND PUSH", "xargs -n 1 PUSH", "xargs -eEND -0n 1 PUSH"),
    ("xargs -l1 PUSH", "xargs -n 1 PUSH", "xargs -l1 --max-procs 4 PUSH"),
    ("xargs -i {} PUSH", "xargs {} PUSH", "xargs -i -0n 1 PUSH"),
    ("xargs --null PUSH", "xargs -n 1 PUSH", "xargs --null --max-args 1 PUSH"),
    ("xargs --verbose PUSH", "xargs -n 1 PUSH", "xargs --verbose -rn 1 PUSH"),
    ("xargs --no-run-if-empty PUSH", "xargs -n 1 PUSH", "xargs --no-run-if-empty --max-procs 4 PUSH"),
    ("xargs -n", "xargs", "xargs -n 1 -rP 4 PUSH"),
    ("xargs -rn", "xargs", "xargs -rn 1 PUSH"),
    ("xargs --max-args", "xargs", "xargs --max-args 1 PUSH"),
    ("xargs --max-a", "xargs", "xargs --max-a 1 PUSH"),
    ("xargs -J", "xargs", "xargs -J % PUSH"),
    ("xargs", "xargs", "xargs -tP 4 PUSH"),
    (r"printf 'a\n' | xargs", r"printf 'a\n' | xargs", r"printf 'a\n' | xargs -rn 1 PUSH"),
    ("xargs -q 1 PUSH", "xargs 1 PUSH", "xargs -rs 1 PUSH"),
    ("xargs --max 1 PUSH", "xargs 1 PUSH", "xargs --max-p 1 PUSH"),
    ("xargs --foo PUSH", "xargs PUSH", "xargs --proc V PUSH"),
    ("xargs -J rm echo hi", "xargs rm echo hi", "xargs -J % PUSH"),
    ("xargs -- -n 1 PUSH", "xargs -n 1 PUSH", "xargs -0J % PUSH"),
    ("xargs - PUSH", "xargs PUSH", "xargs -S 9 -I % PUSH"),
]


@pytest.mark.parametrize("line, same, twin", KEPT)
def test_lines_that_keep_mains_reading(line, same, twin):
    lead = twin.split("xargs")[0]
    assert (decided(line), decided(twin)) == (decided(same), decided(f"{lead}xargs -n 1 PUSH"))


# With #458: the command after the options has its own words as its own text, from each start. A rule written for the
# program that runs it, or one that names a here-string word or a here-doc body, names the xargs line but not the
# push, so it does not lower the push. On main c05f0ca1 these lines were allowed under each rule, as the push was not
# read. Read from the new start without `_run_own`, the push would carry the xargs line as its own text, and the rule
# for xargs would allow it.
OWN_ROWS = [*((f"echo x | xargs {o} PUSH", "xargs") for o in ("--max-args 1", "--max-a 1", "-rn 1", "-J %")),
            ("xargs --arg-file list PUSH", "^xargs"),
            ("xargs --max-args 1 PUSH <<< 'npm test'", "npm test"),
            ("xargs --max-args 1 PUSH <<EOF\nnpm test\nEOF", "npm test")]


@pytest.mark.parametrize("line, command", OWN_ROWS)
def test_a_rule_that_names_only_the_xargs_line_does_not_lower_the_command_it_runs(line, command):
    line = line.replace("PUSH", PUSH)
    c = classify({"tool": "bash", "args": {"command": line}})
    for cats in ({"force_push": "deny", **OPAQUE_OK}, {"force_push": "deny"}):
        d = decide(c, normalize_policies({"gates": {"rules": [rule(command)], "categories": cats}}))
        assert (d.outcome, d.rule) == ("deny", "category"), (cats, d)    # main: allow, then ask
    pushes = [p for p in gate._each_part(c) if p.category == "force_push"]
    xargs_line = line.split("| ")[-1].split("\n")[0]
    assert pushes and {(p.own, p.own.whole) for p in pushes} == {(PUSH, xargs_line)}, [p.own for p in pushes]


@pytest.mark.parametrize("line, command", OWN_ROWS)
def test_a_rule_that_names_the_command_xargs_runs_too_allows_it(line, command):
    c = classify({"tool": "bash", "args": {"command": line.replace("PUSH", PUSH)}})
    both = normalize_policies({"gates": {"rules": [rule(["xargs", f"^{PUSH}"])],
                                         "categories": {"force_push": "deny", **OPAQUE_OK}}})
    bare = normalize_policies({"gates": {"rules": [rule(["^echo", f"^{PUSH}$"])],
                                         "categories": {"force_push": "deny", **OPAQUE_OK}}})
    assert (decide(c, both).outcome, decide(c, bare).outcome) == ("allow", "deny")    # main: allow, allow


# Main's reading stays a reading of its own. Read as parts of one reading, the push after `--max-args rm` would take
# the lead from the delete main reads there (`rm` as the program), and `force_push: log` would lower the line from
# the delete's ask to log. Each reading is decided, and the stricter stands.
def test_mains_reading_is_a_reading_of_its_own():
    line = f"xargs --max-args rm {PUSH}"
    c = classify({"tool": "bash", "args": {"command": line}})
    assert {r.category for r in gate.readings(c)} == {"delete", "force_push"}          # main: delete
    log = normalize_policies({"gates": {"categories": {"force_push": "log", **OPAQUE_OK}}})
    assert decide(c, log).outcome == "ask"                       # main: ask. As parts of one reading: log
    assert decided(line)[1] == ("deny", "category")               # main: ask under the issue's policy
    assert decide(c, normalize_policies({"gates": {"deny": ["delete"]}})).outcome == "deny"     # main: deny


def test_a_line_without_such_options_is_read_once():
    for line in ("xargs -n 1 PUSH", "xargs PUSH", "xargs --max-args=1 PUSH", "xargs --max 1 PUSH", "xargs -rn"):
        c = classify({"tool": "bash", "args": {"command": line.replace("PUSH", PUSH)}})
        assert gate.readings(c) == [c], line


def test_where_getopt_starts_the_command():
    def start(line):
        return gate._xargs_start(line.split())
    assert start("xargs -n 1 echo hi") == 3
    assert start("xargs -rn 1 echo hi") == 3                    # main's reading starts at 2
    assert start("xargs --max-args 1 echo hi") == 3
    assert start("xargs --max-a=1 echo hi") == 2
    assert start("xargs -J % -R 2 -I % echo hi") == 7
    assert start("xargs -0 -i{} -e -l echo hi") == 5
    assert start("xargs -ie -n 1 echo hi") == 4                 # GNU's -i takes `e`, -n takes `1`
    assert start("xargs -in 1 echo hi") == 2                    # -i takes `n`, so `1` is the program
    assert start("xargs -- -n 1 echo hi") == 2                  # xargs runs `-n`, main read past it
    assert start("xargs - echo hi") == 1
    assert start("xargs --max 1 echo hi") is None               # ambiguous: xargs fails, main's reading stays alone
    assert start("xargs -q 1 echo hi") is None
    assert start("xargs -rn") is None
    assert start("xargs --max-args") is None
    assert start("xargs") == 1
