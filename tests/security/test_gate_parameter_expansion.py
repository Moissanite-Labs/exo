"""Parameter words expose executable substitutions without running any payload."""
import sys

import pytest

from exo import gate
from exo.gate import _parse, classify_shell, decide


def _parses(line: str, cap: int = 100) -> int:
    """How many sources classify_shell lexes for the line, counted rather than timed so load cannot change it.
    Fails as soon as the count passes cap, so a run that would not end in time fails quickly instead, or as soon
    as the text lexed passes the line itself plus its budget of nested text."""
    calls, real, left = [0], gate._parse, [(gate._SOURCE_BUDGET + 1) * len(line) + 4096]

    def counted(src):
        calls[0], left[0] = calls[0] + 1, left[0] - len(src)
        assert calls[0] <= cap, f"more than {cap} nested sources lexed"
        assert left[0] >= 0, "nested text past the line's budget lexed"
        return real(src)
    gate._parse = counted
    try:
        classify_shell(line)
    finally:
        gate._parse = real
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


@pytest.mark.parametrize("word", [
    "${X:-$(git push --force origin main)}",
    '"${X:-$\'$(git push --force origin main)\'}"',
    "${X:=$(git push --force origin main)}",
    "${X:+$(git push --force origin main)}",
    "${X?$(git push --force origin main)}",
    "${X/old/$(git push --force origin main)}",
    "${X:-`git push --force origin main`}",
    '${X:-"$(git push --force origin main)"}',
    "${X:-${Y:-$(git push --force origin main)}}",
    '${X:-$(echo ${Y:-$(git push --force origin main)})}',
    '"${X:-\'$(git push --force origin main)\'}"',
])
def test_executable_parameter_words_retain_static_denial(word):
    classification = classify_shell("echo " + word)
    assert classification.category == "force_push"
    assert decide(classification, {"gates": {"categories": {"force_push": "deny"}}}).outcome == "deny"


@pytest.mark.parametrize("word", [
    "${x:-'}'}", '${x:-"}"}', '${X:-$(printf "}")}',
    "${X:-${Y:-'}'}}", r"${X:-\}}",
])
def test_matching_brace_resumes_at_following_command(word):
    classification = classify_shell("echo " + word + " && rm -rf ./work")
    assert classification.category == "delete"
    assert decide(classification).outcome != "allow"


@pytest.mark.parametrize("word", [
    "${X}", "${X:-default}", "${X:-/tmp/path}", "${#X}", "${X%.*}",
    "${X:-'$(git push --force origin main)'}",
    "'${X:-$(git push --force origin main)}'",
    r"${X:-\$(git push --force origin main)}",
])
def test_literal_parameter_words_still_allow(word):
    assert decide(classify_shell("echo " + word)).outcome == "allow"


@pytest.mark.parametrize("word", ["${X", "${X:-'unclosed}", "${X:-$(echo hi}", "${X:-`echo hi}"])
def test_malformed_parameter_is_opaque(word):
    classification = classify_shell("echo " + word)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


def test_scanner_nesting_limit_fails_closed_without_recursion_error():
    word = "${X:-" * 2000 + "value" + "}" * 2000
    assert decide(classify_shell("echo " + word)).outcome == "ask"


def test_nested_command_source_is_captured_once():
    """The $( ) body is one source; the word closes past its first }, so the line as a flat match reads it is
    another (see test_word_closed_past_its_first_brace_is_also_read_flat)."""
    _, substitutions, _, opaque = _parse("echo ${X:-$(echo ${Y:-$(git push --force origin main)})}")
    assert substitutions == ["echo ${Y:-$(git push --force origin main)}", "echo _)}"]
    assert not opaque


@pytest.mark.parametrize("line", [
    'echo "${x:-\'"\'}" && rm -rf ./work }',
    'echo "${x#\'"\'}"\nrm -rf ./work\necho }',
    'echo "${x%\'"\'}"\nrm -rf ./work\necho }',
    'echo "${x/\'"\'/z}"\nrm -rf ./work\necho }',
    'echo "${x:-$\'"\'}"\nrm -rf ./work\necho }',
    'echo "${x:-\'}"\'}" && rm -rf ./work \'\'',
    'echo "$\'" ; rm -rf ./work # \'"',
])
def test_single_quotes_inside_double_quoted_parameter_pair_like_bash(line):
    classification = classify_shell(line)
    assert classification.category == "delete"
    assert decide(classification).outcome != "allow"


@pytest.mark.parametrize("line", [
    "echo \"${x#$'\\''}\" ; git push --force origin main",
    "echo \"${x%$'\\''}\" ; git push --force origin main",
    "echo \"${x/$'\\''/z}\" ; git push --force origin main",
    "echo \"${x^$'\\''}\" ; git push --force origin main",
    "echo ${x:-$'\\''} ; git push --force origin main",
])
def test_ansi_c_quote_inside_parameter_word_closes_where_bash_closes_it(line):
    """$'\\'' is one ANSI-C string to bash's parser, so the brace closes and the next command runs."""
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert decide(classification, {"gates": {"categories": {"force_push": "deny"}}}).outcome == "deny"


@pytest.mark.parametrize("line", [
    "echo 'unclosed ; rm -rf ./work",
    'echo "unclosed ; rm -rf ./work',
    'echo "${x:-\'}"; rm -rf ./work; echo \'}"\'',    # bash: unexpected EOF; dash pairs differently and runs rm
])
def test_unclosed_top_level_quote_is_opaque(line):
    classification = classify_shell(line)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("line", [
    "echo ${y:-<(git push --force origin main)}",
    "echo $((git push --force origin main) )",
    "echo $((true);(git push --force origin main))",
])
def test_command_substitutions_bash_runs_are_classified(line):
    assert classify_shell(line).category == "force_push"


@pytest.mark.parametrize("line", [
    "cat <<EOF\n" + "${X:-" * 33 + "1" + "}" * 33 + "\n$(true)\nEOF",
    "echo $(( " + "${X:-" * 33 + "1" + "}" * 33 + " + $(true) ))",
    # the body of a heredoc read inside $( ) starts at that depth, so 32 more levels pass the limit
    "echo $(cat <<EOF\n" + "${X:-" * 32 + "1" + "}" * 32 + "\nEOF\n)",
])
def test_nesting_limit_inside_heredoc_and_arithmetic_stays_opaque(line):
    """The payload is harmless, so only the over-deep word can make these ask."""
    classification = classify_shell(line)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("line", ["echo $(true", "echo `true", "cat <(true", "echo ${x:-$(true}"])
def test_substitution_left_open_is_opaque(line):
    classification = classify_shell(line)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


def test_locale_string_in_a_parameter_word_is_scanned_without_the_locale_signal():
    """A $".." inside ${...} is read by the word's scan: its $( ) is classified, and no translation signal is set."""
    assert classify_shell('echo ${x:-$"$(git push --force origin main)"}').category == "force_push"
    assert "locale translation" not in classify_shell('echo ${x:-$"hello"}').signals


@pytest.mark.parametrize("deep", [
    "${X:-" * 33 + "1" + "}" * 33,
    "${X:-" * 33 + "'}'\"}\"" + "}" * 33,
    "$(" + "${X:-" * 33 + "1" + "}" * 33 + ")",
    "<(" * 33 + "true" + ")" * 33,
    "$((" * 33 + "1" + "))" * 33,
    # bare brackets and $'..' inside the over-deep word are literal text for bash, not nesting
    "${X:-" * 33 + "(" + "}" * 33,
    "${X:-" * 33 + "{" + "}" * 33,
    "${X:-" * 33 + "$(echo {)" + "}" * 33,
    "${X:-" * 33 + "$'\\''" + "}" * 33,
    '"' + "${X:-" * 33 + "(" + "}" * 33 + '"',
    "$(" * 33 + "echo {" + ")" * 33,
    # a quote in a comment or a heredoc body inside the over-deep $( ) is text, as in bash
    "$(" * 33 + "echo # it's\n" + ")" * 33,
    "$(" * 33 + "cat <<E\nit's )\nE\n" + ")" * 33,
    "$(" * 33 + "cat <<'E'\nit's\nE)" + ")" * 32,
])
def test_nesting_limit_skips_only_the_deep_word_and_reads_the_rest(deep):
    """The over-deep word asks on its own; a command after it is still classified."""
    assert decide(classify_shell(f"echo {deep}")).outcome == "ask"
    classification = classify_shell(f"echo {deep} ; git push --force origin main")
    assert classification.category == "force_push"
    assert "indirection" in classification.signals
    lowered = {"gates": {"categories": {"force_push": "deny", "opaque_exec": "act"}}}
    assert decide(classification, lowered).outcome == "deny"


@pytest.mark.parametrize("line", [
    "cat " + "<(" * 2000 + "true" + ")" * 2000,
    "echo " + "$((" * 2000 + "1" + "))" * 2000,
])
def test_process_substitution_and_arithmetic_nesting_limit_fails_closed_quickly(line):
    assert decide(classify_shell(line)).outcome == "ask"
    assert _parses(line) <= 40                     # one source per level the depth and scan limits allow


@pytest.mark.parametrize("line", [
    "echo $(a $(a $(a $(a $(git push --force origin main)))))",
    "echo " + "$(a " * 30 + "$(git push --force origin main)" + ")" * 30,
    "cat " + "<(a " * 30 + "<(git push --force origin main)" + ")" * 30,
    "echo $(a $(a $(a $(a " + "$(true) " * 40 + "$(git push --force origin main)))))",
])
def test_category_deny_reaches_deeply_nested_command_sources(line):
    """Beyond the classification depth each level is still read once, so a category deny holds."""
    assert _parses(line) <= 80
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert decide(classification, {"gates": {"categories": {"force_push": "deny"}}}).outcome == "deny"


def test_command_source_beyond_the_scan_limit_is_opaque():
    line = "echo " + "$(a " * 40 + "$(git push --force origin main)" + ")" * 40
    classification = classify_shell(line)
    assert classification.category == "opaque_exec"
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("word", [
    "${x@P}", '"${arr[0]@P}"', "${!x}", "${!x:-y}", "${!arr[1]}",
    # each positional parameter is prompt-expanded, or its value is used as a variable name (`a[$(..)]` runs)
    "${@@P}", "${*@P}", '"${@@P}"', "${!@}", "${!*}", "${!@:-y}",
    # an operator after the indirection does not make it a listing form: bash still reads the value as a name
    "${!@@P}", "${!*@P}", "${!@@Q}", "${!*@Q}", "${!x@P}", "${!x@Q}", "${!1@Q}",
    # nor does an empty operator after @: bash reads $@ as names (running `a[$(..)]`) before it fails
    "${!@@}",
])
def test_expansions_that_run_the_variables_value_ask(word):
    classification = classify_shell("echo " + word)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("word", [
    "${!x*}", "${!x@}", "${!arr[@]}", "${!arr[*]}", "${x@Q}", "${x@a}", "${#x}", "${@@Q}", "${#@}", "${@}",
])
def test_listing_and_inert_transformations_still_allow(word):
    assert decide(classify_shell("echo " + word)).outcome == "allow"


_FORCE_DENY = {"gates": {"categories": {"force_push": "deny"}}}


@pytest.mark.parametrize("line", [
    "echo \"${x#'${y'}\" ; git push --force origin main ; echo \"'}\"",
    "echo \"${x:-'${y'}\" ; git push --force origin main ; echo \"'}\"",
    "echo \"${x:-'$(y'}\" ; git push --force origin main ; echo \"')}\"",
    "echo \"${x:-'`y'}\" ; git push --force origin main ; echo \"'`}\"",
    "echo \"${x:-$'${y'}\" ; git push --force origin main ; echo \"'}\"",
])
def test_scan_inside_a_quoted_span_stops_at_its_closing_quote(line):
    """bash ends the '..' span at its quote; an expansion opened inside it cannot take the next command."""
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert decide(classification, _FORCE_DENY).outcome == "deny"


def test_brace_opened_in_a_quoted_span_does_not_take_a_following_delete():
    classification = classify_shell("echo \"${x#'${y'}\" ; rm -rf ./work ; echo \"'}'}\"")
    assert classification.category == "delete"
    assert decide(classification, {"gates": {"categories": {"delete": "deny"}}}).outcome == "deny"


@pytest.mark.parametrize("line", [
    "echo $(( '(' ) ; git push --force origin main ; echo ))",
    'echo $(( "(" ) ; git push --force origin main ; echo ))',
    "echo $(( \\( ) ; git push --force origin main ; echo ))",
    "echo $(( 1 ; git push --force origin main",
    # bash steps over nested $( ) (# comments in it included) and $'..' (with \' escaped) when it matches the
    # group, and reads it as $( ) when its parentheses do not balance with only quotes skipped (`))` in
    # backticks), or when its second read at expansion, which takes # comments and not ${ }, ends elsewhere
    'echo $(( "$(: "((" ; echo 1)" )) ; git push --force origin main # ))',
    'unset x; echo $(( "${x:+"(("}" 1 )) ; git push --force origin main # ))',
    "echo $(( $(echo 1 # ((\n) )) ; git push --force origin main # ))",
    "echo $(( $'\\'' ) ; git push --force origin main ; echo ' ))' ) ; echo \\'",
    "echo $(( `echo ))` ; git push --force origin main ; echo ))",
    "echo $(( `echo ((` && git push --force origin main && echo ))",
    "echo $(( ${x:-`echo ((`} | git push --force origin main | echo ))",
    "echo $(( x #(\n) ; git push --force origin main ; (echo) ))",
    "echo $(( x #`((`\n )) ; git push --force origin main # ))",
    # bash's balance check sees $'..' as a quoted string, \' escaped in it, so the ( in backticks is counted
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
    # a group that is not arithmetic ends where bash matches its parentheses again, where # starts a comment
    # only after a blank: a # right after ) is text, and the command after the word runs
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
    # only the gate's own $( ) read of the group, kept beside bash's, reaches the push here
    "echo ${x:-$(( #*#))\ngit push --force origin main\\",
    "echo $(( 1 #) )) | git push --force origin main",
])
def test_arithmetic_matching_skips_quoted_parentheses_and_never_closes_at_end(line):
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert decide(classification, _FORCE_DENY).outcome == "deny"


@pytest.mark.parametrize("line", ["echo $(( 1", "echo $(( (1 + 2)", "echo $(( 1 + (2 )"])
def test_unclosed_arithmetic_is_opaque(line):
    classification = classify_shell(line)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("line", [
    "echo $(( 1 + 2 ))", "echo $(( (1 + 2) * 3 ))", "echo $(( x > 1 ? 2 : 3 ))", "echo $(( 16#ff | 3 & 1 ))",
    "echo $(( $(wc -l < f) + 1 ))", "echo $(( `echo 1` + 2 ))", "echo $(( $'\\x31' + $(echo 2) ))",
])
def test_closed_arithmetic_still_allows(line):
    assert decide(classify_shell(line)).outcome == "allow"


@pytest.mark.parametrize("line", ["echo $(( $(echo 1 # c\n) + 1 ))", "echo $(( $(cat <<E\n1\nE\n) + 1 ))"])
def test_arithmetic_with_a_comment_in_a_nested_substitution_is_read_as_a_command_substitution(line):
    """Stricter than bash, which drops the comment, rewrites the heredoc and reads arithmetic: the gate does not
    model the text bash prints back, so it classifies the group's commands and the line asks. These rows pin that
    fail-closed choice; no line is known where bash runs a command that only this reading catches."""
    classification = classify_shell(line)
    assert classification.category == "opaque_exec"
    assert decide(classification).outcome == "ask"


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
    """A ${ } word that never closes asks (bash rejects the line), and the text after its first } is read as
    commands too, where a flat reading ends the word; the word goes on there, so a # right after the } is text."""
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert decide(classification, _FORCE_DENY).outcome == "deny"


@pytest.mark.parametrize("line", ["echo ${x:-<(echo hi)} ; echo ok", "echo ${x:-<(echo hi)}${y:-<(echo yo)}"])
def test_closed_process_substitution_in_a_parameter_word_still_allows(line):
    assert decide(classify_shell(line)).outcome == "allow"


@pytest.mark.parametrize("line, category", [
    # dash, the /bin/sh fallback, has no <( ): it ends the word at its first } and runs the next line
    ("x=$(echo ${x:-<(} )\ngit push --force origin main\n})", "force_push"),
    ("( echo ${x:-<(} )\ngit push --force origin main\n}", "force_push"),
    ("echo $(echo ${x:-<(} )\nrm -rf ./work\n})", "delete"),
    ("echo $(echo ${x#>(} )\ngit push --force origin main\n})", "force_push"),
    # dash takes a ' inside "${x:-..}" as text, so the word ends at its first } there too
    ("echo \"${x:-'}\" ; rm -rf ./work ; echo \"'}\"", "delete"),
    ("echo \"${x:+'}\" ; git push --force origin main ; echo \"'}\"", "force_push"),
    ("echo \"${x:-'}\"; rm -rf ./work; echo '}\"'", "delete"),
    # bash and dash run nothing here; the flat reading, which main had, runs the command and is kept
    ("echo ${x:-${x}&rm -rf ./work}", "delete"),
    ("echo ${x:-${x} | git push --force origin main\n@}@@}", "force_push"),
    ("echo ${x:-$(( } ; rm -rf ./work ))}", "delete"),
])
def test_word_closed_past_its_first_brace_is_also_read_flat(line, category):
    """A ${ } word that closes past its first } is also read as a flat match reads it, ended at that }: bash's
    reading and that one are both classified and the stricter holds."""
    classification = classify_shell(line)
    assert classification.category == category
    assert decide(classification, {"gates": {"categories": {category: "deny"}}}).outcome == "deny"


@pytest.mark.parametrize("line", [
    "echo ${a:-${b}}", 'echo "${a:-${b}}"', 'echo "$(echo ${a:-${b}})"', 'echo ${a:-${b}}"x"', "x=${a:-${b}} ls",
    "echo ${x:-<(echo })} ; echo ok", "echo \"${x:-'}'}\"", "cat <<EOF\n${a:-${b}}\nrm -rf ./work\nEOF",
])
def test_flat_reading_of_a_closed_word_adds_no_false_ask(line):
    """The flat reading keeps the rest of the line in its context (quotes, assignment, heredoc body)."""
    assert decide(classify_shell(line)).outcome == "allow"


@pytest.mark.parametrize("line", [
    'echo "$\\\n(git push --force origin main)"',
    "echo ${x:-$\\\n(git push --force origin main)}",
    "cat <<EOF\n$\\\n(git push --force origin main)\nEOF",
    'echo "${x:-$\\\n\\\n(git push --force origin main)}"',
])
def test_backslash_newline_after_a_dollar_is_dropped_as_bash_drops_it(line):
    """bash and dash drop a backslash-newline before they read what a $ starts, in double quotes, ${ } words and
    heredoc bodies too, so `$\\`, a newline and `(cmd)` run cmd."""
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert decide(classification, _FORCE_DENY).outcome == "deny"


def test_backslash_newline_inside_single_quotes_stays_text():
    assert decide(classify_shell("echo '$\\\n(git push --force origin main)'")).outcome == "allow"


@pytest.mark.parametrize("line", [
    "echo ${ git push --force origin main; }",
    "echo ${| git push --force origin main; }",
    "echo ${\ngit push --force origin main\n}",
    'echo "${ git push --force origin main; }"',
    "echo ${ echo }; git push --force origin main; }",
])
def test_bash_5_3_function_substitution_is_read_as_commands(line):
    """bash 5.3 runs ${ cmd; } and ${| cmd; } in the current shell (bash 5.2 reports a bad substitution), so the
    body is classified, and the line asks: the gate ends it at its first } outside quotes, where bash 5.3 ends it at
    a } in command position, and reads what follows as commands too."""
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert "indirection" in classification.signals
    assert decide(classification, _FORCE_DENY).outcome == "deny"


def test_hidden_push_beside_a_visible_delete_decides_as_the_line_written_out():
    """The push a word hid from main now outranks the delete beside it, as it does on the same line written out:
    one category decides a line, so deny: [delete] asks there. A deny on any part of a line is D-075 (#126)."""
    policy = {"gates": {"deny": ["delete"]}}
    hidden = classify_shell("echo ${X:-$(git push --force origin main)} && rm -rf ./work")
    shown = classify_shell("git push --force origin main && rm -rf ./work")
    assert hidden.category == shown.category == "force_push"
    assert decide(hidden, policy).outcome == decide(shown, policy).outcome == "ask"


@pytest.mark.parametrize("line", [
    'echo $(( 1 " ) ; git push --force origin main ; echo " ))',
    "echo $(( \\) ; git push --force origin main ; echo ))",
])
def test_text_bash_never_runs_as_a_command_stays_text(line):
    """bash 5.2 reads each as one arithmetic group (an error, nothing runs), and so does a flat count of its
    parentheses."""
    assert decide(classify_shell(line), _FORCE_DENY).outcome == "allow"


@pytest.mark.parametrize("line, outcome", [
    ("echo $(( ${x:-'))\ngit push --force origin main\n'} ))", "deny"),
    ("echo $(( ) #( )) ; git push --force origin main\n) ))", "deny"),
    ("cat <<EOF $(echo x\n)\ngit push --force origin main\nEOF", "ask"),
])
def test_line_read_two_ways_keeps_the_stricter_reading(line, outcome):
    """bash runs nothing here: it reads one arithmetic group, a comment in a command substitution, a heredoc body
    that starts after the outer line. A flat count of the group's parentheses, the gate's reading before it
    modelled bash's parser, ends the group early and runs the push, so that reading is classified too; a newline
    inside $( ) while the outer heredoc waits makes the line ask."""
    classification = classify_shell(line)
    assert classification.category == ("force_push" if outcome == "deny" else "opaque_exec")
    assert decide(classification, _FORCE_DENY).outcome == outcome


@pytest.mark.parametrize("line", [
    "echo $(cat <<EOF) ; echo hi\ngit push --force origin main\nEOF",
    "echo ${x:-$(cat <<EOF)} ; echo hi\ngit push --force origin main\nEOF",
    "cat <(cat <<EOF) ; echo hi\ngit push --force origin main\nEOF",
])
def test_heredoc_left_open_in_a_substitution_does_not_take_the_following_lines(line):
    classification = classify_shell(line)
    assert classification.category == "force_push"
    assert "indirection" in classification.signals
    assert decide(classification, _FORCE_DENY).outcome == "deny"


def test_outer_heredoc_body_starts_after_the_outer_newline():
    """A newline inside $( ) does not read the outer line's heredoc, as in bash."""
    line = "cat <<EOF $(echo x\n)\nbody\nEOF\ngit push --force origin main"
    assert decide(classify_shell(line), _FORCE_DENY).outcome == "deny"
    cmds, _, _, _ = _parse(line)
    assert [h.body for k in cmds for h in k.heredocs] == ["body"]


_UNITS = [
    ("echo \"${x:-", "'$(( 1 )) ${y}'", "}\""),
    ("echo \"${x:-", "'${y:-'", "}\""),
    ("echo ", "$(( '(' + 1 )) ", ""),
    ("echo ", "$(( (1) ", ""),
    ("echo ", "$(cat <<EOF) ", ""),
    ("echo ", '$(( "$(: "((" ; echo 1)" ', ""),
    ("echo ", "$(( $'\\'' `(` ", ""),
    ("echo ", "$(( $(: # (\n) ", ""),
    ("echo ", "${x:-<(})", ""),
    ("echo ", "$( ${x:-<(}) ", ""),
    ("echo ", "$(( )#( )) ", ""),
    ("echo ", "$(( ${x:-)) ", ""),
    ("echo ", "${x:-} ${y:-'", ""),
    ("echo ", "$(( |#( ))' ", ""),
    # words and groups left open: each reading holds the rest of the line, and both are read again per level
    ("echo ", "${x:-$(}", ""),
    ("echo ", "${x:-$((}", ""),
    ("echo ", '$((")', ""),
    ("echo ", '$(("))', ""),
    ("echo ", "${x:-<(}", ""),
    ("echo ", "${x:-${y:-$(}", ""),
    ("echo ", "$(( ${x:-", ""),
    # words closed past their first }, also read flat
    ("echo ", "${a:-${b}}", ""),
    ("echo ", "\"${x:-'}'}\"", ""),
    ("echo ", "${x:-<(echo })}", ""),
    ("echo ", "${x:-<(} )", ""),
    ("echo ", "\"${x:-'}\" ; echo \"'}\" ", ""),
]


@pytest.mark.parametrize("before, unit, after", _UNITS)
def test_bounded_scans_stay_linear(before, unit, after):
    """One parse does work in proportion to the line (4 times the steps for 4 times the units; 16 if quadratic)."""
    def steps(n: int) -> int:
        return _steps(lambda: _parse(before + unit * n + after))
    assert steps(160) < 6 * steps(40)


@pytest.mark.parametrize("before, unit, after", _UNITS)
def test_nested_sources_are_lexed_a_bounded_number_of_times(before, unit, after):
    """The readings of a word or group left open each hold the rest of the line and are read again at every level,
    so one source turns up on many paths. classify_shell reads each source once, at its shallowest depth: about
    two per level of the 37 the depth and scan limits allow, however long the chain (each source is at most the
    line), where reading every path costs 2 to the power of the levels."""
    assert _parses(before + unit * 60 + after) <= 80


def test_unclosed_arithmetic_chain_costs_what_a_command_substitution_chain_costs():
    """Group ends are cached per index: reading an unclosed $(( chain as nested $( ) must not rescan to the end
    of the line for every level and every parse of each nested source."""
    def steps(unit: str) -> int:
        return _steps(lambda: classify_shell("echo " + unit * 100))
    assert steps("$(( (1) ") < 4 * steps("$( (1) ")
