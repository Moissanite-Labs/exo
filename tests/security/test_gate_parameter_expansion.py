"""Parameter words expose executable substitutions without running any payload."""
import time

import pytest

from exo.gate import _parse, classify_shell, decide


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
    _, substitutions, _, opaque = _parse("echo ${X:-$(echo ${Y:-$(git push --force origin main)})}")
    assert substitutions == ["echo ${Y:-$(git push --force origin main)}"]
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


def test_nesting_limit_inside_heredoc_and_arithmetic_stays_opaque():
    deep = "${X:-" * 33 + "1" + "}" * 33
    for line in (f"cat <<EOF\n{deep}\n$(git push --force origin main)\nEOF",
                 f"echo $(( {deep} + $(git push --force origin main) ))"):
        assert decide(classify_shell(line)).outcome == "ask"


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
    started = time.perf_counter()
    assert decide(classify_shell(line)).outcome == "ask"
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("line", [
    "echo $(a $(a $(a $(a $(git push --force origin main)))))",
    "echo " + "$(a " * 30 + "$(git push --force origin main)" + ")" * 30,
    "cat " + "<(a " * 30 + "<(git push --force origin main)" + ")" * 30,
    "echo $(a $(a $(a $(a " + "$(true) " * 40 + "$(git push --force origin main)))))",
])
def test_category_deny_reaches_deeply_nested_command_sources(line):
    """Beyond the classification depth each level is still read once, so a category deny holds."""
    started = time.perf_counter()
    classification = classify_shell(line)
    assert time.perf_counter() - started < 1.0
    assert classification.category == "force_push"
    assert decide(classification, {"gates": {"categories": {"force_push": "deny"}}}).outcome == "deny"


def test_command_source_beyond_the_scan_limit_is_opaque():
    line = "echo " + "$(a " * 40 + "$(git push --force origin main)" + ")" * 40
    classification = classify_shell(line)
    assert classification.category == "opaque_exec"
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("word", ["${x@P}", '"${arr[0]@P}"', "${!x}", "${!x:-y}", "${!arr[1]}"])
def test_expansions_that_run_the_variables_value_ask(word):
    classification = classify_shell("echo " + word)
    assert "indirection" in classification.signals
    assert decide(classification).outcome == "ask"


@pytest.mark.parametrize("word", ["${!x*}", "${!x@}", "${!arr[@]}", "${!arr[*]}", "${x@Q}", "${x@a}", "${#x}"])
def test_listing_and_inert_transformations_still_allow(word):
    assert decide(classify_shell("echo " + word)).outcome == "allow"
