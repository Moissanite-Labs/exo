"""#579: a prompt-string transformation is code the gate cannot see.

`${name@P}` expands the value as a prompt string, and with `promptvars` on, the default, that runs its command
substitutions and expands its parameters: `printf -v p '%s(%s)' '$' 'id'; echo ${p@P}` runs `id`. A value a
command the gate does not read as an assignment sets (`printf -v`, `read`) can also rebuild a form the gate
reads only as written, a `/` substitution of a store variable (#342): `cat ${p@P}` reads `$HOME/.kube/config`.
Every `${..@P}` on a line is `opaque_exec` and asks, also in a here-string, a here-document body or a
redirection target, unless the line assigns the variable a literal value with no `$`, backtick or backslash —
a prompt expansion of such a value is the value itself (a backslash is a prompt escape: `p='\\u'` is the
username). A prefix assignment sets the command's own environment, which bash expands the words before, so it
is no source, and an append onto a value the line does not know leaves it unknown. The other `@` operators
(`@Q`, `@E`, `@a`...) decode or spell the value and run nothing, so they keep their reading.

Every command string here is data for exo.gate.classify/decide; none is run.
"""
import pytest

from exo.gate import classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}}})
FORCE = normalize_policies({"gates": {"deny": ["force_push"]}})


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def _out(line, policy=None):
    return decide(classify(sh(line)), POLICY if policy is None else policy).outcome


ASK = [
    # the issue's rows
    "printf -v p '%s(%s)' '$' 'id'; echo ${p@P}",
    "printf -v p '%s' '$''{HOME/%//.kube}/config'; cat ${p@P}",
    "read -r p <<< '${HOME/%//.kube}/config'; cat ${p@P}",
    "p='$(id)'; echo ${p@P}",                       # the control row
    # a value the line does not set
    "echo ${p@P}",
    "echo ${ps@P}",
    "echo ${@@P}",                                  # the positional parameters
    "echo ${*@P}",
    'echo "${@@P}"',
    "echo ${1@P}",
    "set -- '$(git push --force origin main)'; echo ${@@P}",
    # a value the line sets, but not to a literal
    "p=$(id); echo ${p@P}",
    "p=`id`; echo ${p@P}",
    "p='$(id)'; cat ${p@P}",
    "export p=$(id); echo ${p@P}",
    "read -r p; echo ${p@P}",                       # printf -v is above
    "printf -v p '%s' x; echo ${p@P}",
    # a literal that a prompt expansion reads as escapes
    "p='\\u'; echo ${p@P}",                         # the username
    'p="$"; echo ${p@P}',                           # a `$` may open a substitution
    "p=x\\u; echo ${p@P}",
    # an append or a prefix, which are no literal the later words read
    "read -r p; p+=y; echo ${p@P}",
    "p=x; p+='$(id)'; echo ${p@P}",
    "p=x echo ${p@P}",
    "echo ${p@P}; p=x",                             # assigned after the use
    "p[0]=x; echo ${p@P}",                          # the array's other elements are not on the line
    "p=x; echo ${p[@]@P}",
    "p=x; echo ${p[1]@P}",
    # where the expansion stands
    'echo "x${p@P}y"',
    "cat <<< ${p@P}",
    "cat <<E\n${p@P}\nE",
    "cat < ${p@P}",
    "2> ${p@P}",
    "echo ${p@P} > /tmp/o",
    "for p in x; do echo ${p@P}; done",             # the for list is data the command runs over
    'echo "${a:-\'${b@P}\'}"',                      # one nested in a word bash expands
    'read -r p; cat "$CLAUDE_CONFIG_DIR/${p@P}"',   # an argument to a store path
]

ALLOW = [
    # the literal exception
    "p=x; echo ${p@P}",
    'p=x; echo "${p@P}"',
    "p=x; echo ${p@P} /tmp/y",
    "p=x; echo ${p@P}${p@P}",
    "p=x; p=y; echo ${p@P}",                        # the last literal wins
    "p=y; p=x; echo ${p@P}",
    "p=; echo ${p@P}",                              # an empty value expands to nothing
    "p=x && echo ${p@P}",
    "p=x; { echo ${p@P}; }",
    "p=x; (echo ${p@P})",                           # a subshell inherits the value
    "p=x; if true; then echo ${p@P}; fi",
    "export p=x; echo ${p@P}",                      # an exporter assigns one
    "declare p=x; echo ${p@P}",
    "readonly p=x; echo ${p@P}",
    "p=x; export p; echo ${p@P}",                   # export alone changes no value
    "p=x; unset p; echo ${p@P}",                    # after the unset the value is empty
    "p=x; p+=y; echo ${p@P}",                       # a literal appended to a literal is one
    "p[0]=x; echo ${p[0]@P}",                       # the element the line sets
    "f=/tmp/x; cat < ${f@P}",                       # a literal redirection target
    # the operators that decode or spell, and run nothing
    "echo ${p@Q}",
    "p=x; echo ${p@Q}",
    "p=x; echo ${p@E}",
    "p=x; echo ${p@a}",
    "p=x; echo ${p@K}",
    "p=x; echo ${p@L}",
    "p=x; echo ${p@U}",
    "p=x; echo ${p@#}",
    "p=x; echo ${p@t}",
    "p=x; echo ${p@k}",
    # the operator is read only where the parameter ends
    "echo ${x:-foo@P}",
    "echo ${@P}",
    'echo "${a%\'${b@P}\'}"',                       # a pattern is text bash does not expand
    # text bash keeps as written
    "echo '${p@P}'",
    "cat <<'E'\n${p@P}\nE",
]


@pytest.mark.parametrize("line", ASK)
def test_a_prompt_expansion_of_a_value_the_gate_does_not_see_asks(line):
    assert _out(line) == "ask"


@pytest.mark.parametrize("line", [
    "printf -v p '%s(%s)' '$' 'id'; echo ${p@P}",
    "printf -v p '%s' '$''{HOME/%//.kube}/config'; cat ${p@P}",
    "read -r p <<< '${HOME/%//.kube}/config'; cat ${p@P}",
    "p='$(id)'; echo ${p@P}",                       # the control row
])
def test_an_issue_row_is_opaque(line):
    c = classify(sh(line))
    assert c.category == "opaque_exec"
    assert decide(c, POLICY).outcome == "ask"


@pytest.mark.parametrize("line", ALLOW)
def test_a_known_or_inert_transformation_keeps_its_reading(line):
    assert _out(line) == "allow"


@pytest.mark.parametrize("line", [
    "set -- '$(git push --force origin main)'; echo ${@@P}",
    "set -- '$(git push --force origin main)'; echo ${*@P}",
    "set -- '$(git push --force origin main)'; echo \"${@@P}\"",
])
def test_a_prompt_expansion_of_a_value_the_gate_does_not_see_asks_under_a_deny(line):
    assert _out(line, FORCE) == "ask"


def test_the_part_carries_the_indirection_signal_and_names_the_parameter():
    c = classify(sh("echo ${p@P}"))
    assert "indirection" in c.signals
    assert "runs the prompt expansion of p" in c.summary
    c = classify(sh("echo ${@@P}"))
    assert "runs the prompt expansion of the positional parameters" in c.summary


def test_a_literal_assignment_spares_the_line_and_the_inert_operator_says_why():
    assert _out("p=x; echo ${p@P}") == "allow"
    c = classify(sh("p=x; echo ${p@P}"))
    assert c.category == "read"
