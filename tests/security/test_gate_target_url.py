"""A gh target comes from operands only, never from an option value (#399).

`_gh_target` took the first word that starts with a GitHub URL, wherever it sat: a comment body
that opens with a link, or a `--duplicate-of` URL, replaced the `-R` repo. A public post whose
body starts with a link to a repo under `scopes.internal` was then read as that repo and logged
instead of asking, and the public-only checks were dropped with it (the scrub's public rules and
the third-party floor read the exposure). Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F"}})
PUB = "-R pub/repo"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


# The issue's rows: gh posts each of these to pub/repo, so each asks as a public post.
ASK = [
    rf"gh pr comment 5 {PUB} -b $'Note SECRETX\nF'",          # control
    rf"gh pr comment 5 {PUB} -b $'https://github.com/myorg/app/pull/9 Note SECRETX\nF'",
    rf"gh issue comment 5 {PUB} --body $'https://github.com/myorg/app/issues/1 SECRETX\nF'",
    rf"gh issue close 5 {PUB}",                               # control
    rf"gh issue close 5 {PUB} --duplicate-of https://github.com/myorg/app/issues/1",
]


@pytest.mark.parametrize("line", ASK)
def test_a_url_in_an_option_value_does_not_name_the_target(line):
    c = classify(sh(line))
    assert c.target == "pub/repo#5", c
    assert decide(c, POLICY).outcome == "ask", (line, decide(c, POLICY))


# Operands still name the target: a URL operand, with or without `-R`; `-R` wins over a URL
# operand that names another repo; attached and clustered values are skipped with their option.
TARGETS = [
    (r"gh pr comment https://github.com/pub/repo/pull/5 -b $'Note SECRETX\nF'", "pub/repo#5"),
    (r"gh pr comment https://github.com/myorg/app/pull/9 -b $'Note\nF'", "myorg/app#9"),
    (rf"gh pr comment https://github.com/pub/repo/pull/7 {PUB} -b $'Note\nF'", "pub/repo#7"),
    (rf"gh pr comment https://github.com/other/repo/pull/9 {PUB} -b $'Note\nF'", "pub/repo"),
    (rf"gh pr comment 5 {PUB} --body=https://github.com/myorg/app/pull/9", "pub/repo#5"),
    (rf"gh pr comment 5 {PUB} -bhttps://github.com/myorg/app/pull/9", "pub/repo#5"),
    (rf"gh pr comment 5 {PUB} --body https://github.com/myorg/app/pull/9", "pub/repo#5"),
    (rf"gh issue close 5 {PUB} --duplicate-of=https://github.com/myorg/app/issues/1", "pub/repo#5"),
    (rf"gh issue close 5 {PUB} --reason duplicate", "pub/repo#5"),
    (r"gh issue close -R pub/repo -r duplicate 5", "pub/repo#5"),
]


@pytest.mark.parametrize("line,target", TARGETS)
def test_the_target_comes_from_operands(line, target):
    assert classify(sh(line)).target == target, line


def test_the_body_keeps_its_url():
    c = classify(sh(rf"gh pr comment 5 {PUB} -b $'https://github.com/myorg/app/pull/9 Note SECRETX\nF'"))
    assert c.text.startswith("https://github.com/myorg/app/pull/9"), c
