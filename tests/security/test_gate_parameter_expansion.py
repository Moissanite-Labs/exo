"""Parameter words expose executable substitutions without running any payload."""
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
