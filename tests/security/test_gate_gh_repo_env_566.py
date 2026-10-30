"""gh reads GH_REPO as `-R` when the line gives none, and GH_CONFIG_DIR picks its config (#566).

`GH_REPO=pub/repo gh pr comment 5` posts on pub/repo#5, but the gate read the target as `#5`, a repo the line does
not name. It asked as public with a third party, yet a `target:` deny or rule on pub/* never saw the line. Each
value the line sets for GH_REPO, as a prefix assignment or earlier on the line, is now a reading too, read as `-R`
is, beside the line's own: the line's own reading still decides, so no line decides lower than before.
GH_CONFIG_DIR chooses gh's hosts, tokens and settings, its pager and editor commands among them, as XDG_CONFIG_HOME
does, so it now asks as the config variables do.

Every command string here is data for exo.gate.classify and decide. None is run.
"""
import pytest

from exo import gate

SCOPE = {"scopes": {"internal": ["myorg/*"]}}
INTERNAL = gate.normalize_policies({"gates": SCOPE})
DENY_PUB = gate.normalize_policies({"gates": {**SCOPE, "deny": ["target:pub/*"]}})
ALLOW_POST = gate.normalize_policies({"gates": {**SCOPE, "categories": {"github_post": "allow"}}})
ALLOW_POST_DENY_PUB = gate.normalize_policies({"gates": {**SCOPE, "categories": {"github_post": "allow"},
                                                         "deny": ["target:pub/*"]}})
RULE_PUB = gate.normalize_policies({"gates": {**SCOPE, "rules": [{"match": {"target": "pub/*"}, "decision": "deny"}]}})


def run(line, policy):
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    return c, gate.decide(c, policy)


# Each of these posts on pub/repo#5 (or creates an issue on pub/repo).
POSTS = [
    "GH_REPO=pub/repo gh pr comment 5 --body ok",
    "export GH_REPO=pub/repo; gh pr comment 5 --body ok",
    "export GH_REPO=pub/repo && gh issue comment 5 --body ok",
    "env GH_REPO=pub/repo gh pr comment 5 --body ok",
    "GH_REPO='pub/repo' gh pr comment 5 --body ok",
    "GH_REPO=https://github.com/pub/repo gh pr comment 5 --body ok",
    "GH_REPO=pub/repo gh pr close 5 --comment ok",
    "GH_REPO=pub/repo gh issue create -t t -b ok",
    "export GH_REPO=pub/repo; true; gh pr comment 5 --body ok",
]
ON_PUB = POSTS + ["GH_REPO=pub/repo gh pr merge 5 --squash"]


@pytest.mark.parametrize("line", ON_PUB)
@pytest.mark.parametrize("policy", [DENY_PUB, ALLOW_POST_DENY_PUB, RULE_PUB], ids=["deny", "allow-post+deny", "rule"])
def test_a_deny_on_the_gh_repo_value_reaches_the_line(line, policy):
    c, d = run(line, policy)
    assert d.outcome == "deny", (line, c.target, c.alt_targets, d)


@pytest.mark.parametrize("line", POSTS)
def test_the_gh_repo_value_is_another_target_and_the_line_still_asks(line):
    c, d = run(line, INTERNAL)
    assert any(t.startswith("pub/repo") for t in c.alt_targets), (line, c.target, c.alt_targets)
    assert (d.outcome, d.exposure, d.third_party) == ("ask", "public", True), (line, d)


def test_an_internal_gh_repo_value_lowers_nothing():
    # gh posts on myorg/app#5, internal, but the line's own reading (a repo it does not name) still decides
    c, d = run("GH_REPO=myorg/app gh pr comment 5 --body ok", INTERNAL)
    assert c.target == "#5" and c.alt_targets == ["myorg/app#5"], c
    assert (d.outcome, d.exposure, d.third_party) == ("ask", "public", True), d


@pytest.mark.parametrize("line", [
    "GH_REPO=pub/repo gh pr comment 5 -R myorg/app --body ok",
    "export GH_REPO=pub/repo; gh pr comment 5 --repo myorg/app --body ok",
    "GH_REPO=pub/repo gh pr comment 5 -Rmyorg/app --body ok",
])
def test_r_wins_over_gh_repo_as_in_gh(line):
    c, d = run(line, DENY_PUB)
    assert c.target == "myorg/app#5" and c.alt_targets == [], c
    assert d.outcome == "log", d


def test_a_gh_repo_value_on_another_instance_stays_as_written():
    pol = gate.normalize_policies({"gates": {**SCOPE, "deny": ["target:ghe.corp/*"]}})
    c, d = run("GH_REPO=ghe.corp/myorg/app gh pr comment 5 --body ok", pol)
    assert "ghe.corp/myorg/app#5" in c.alt_targets, c
    assert d.outcome == "deny", d


@pytest.mark.parametrize("line", [
    "GH_REPO=pub/repo gh api repos/{owner}/{repo}/issues/5/comments -f body=ok",
    "export GH_REPO=pub/repo; gh api -X POST repos/{owner}/{repo}/issues/5/comments -f body=ok",
])
def test_gh_api_fills_owner_and_repo_from_gh_repo(line):
    c, d = run(line, DENY_PUB)
    assert "pub/repo" in c.alt_targets, c
    assert d.outcome == "deny", d


@pytest.mark.parametrize("line", [
    "GH_REPO=$R gh pr comment 5 --body ok",
    "export GH_REPO=\"$R\"; gh pr comment 5 --body ok",
    "gh pr comment 5 --body GH_REPO=pub/repo",
    "gh api repos/myorg/app/issues/5/comments -f body=ok",
])
def test_no_gh_repo_value_the_gate_can_read_adds_nothing(line):
    c, d = run(line, DENY_PUB)
    assert not any(t.startswith("pub/repo") for t in c.alt_targets), c
    assert d.outcome != "deny", d


@pytest.mark.parametrize("line", [
    "GH_CONFIG_DIR=/abs/cfg gh pr comment 5 -R myorg/app --body ok",
    "export GH_CONFIG_DIR=/abs/cfg; gh pr comment 5 -R myorg/app --body ok",
    "env GH_CONFIG_DIR=/abs/cfg gh pr comment 5 -R myorg/app --body ok",
    "XDG_CONFIG_HOME=/abs/cfg gh pr comment 5 -R myorg/app --body ok",          # control: it asked before
])
@pytest.mark.parametrize("policy", [INTERNAL, ALLOW_POST], ids=["internal", "allow-post"])
def test_gh_config_dir_asks_as_a_config_folder(line, policy):
    _, d = run(line, policy)
    assert (d.outcome, d.rule) == ("ask", "opaque"), (line, d)
    assert "GH_CONFIG_DIR" in d.reason or "XDG_CONFIG_HOME" in d.reason or "cannot fully see" in d.reason, d


def test_gh_config_dir_is_named_in_its_part():
    c = gate.classify({"tool": "bash", "args": {"command": "GH_CONFIG_DIR=/abs/cfg gh pr view 5 -R myorg/app"}})
    parts = [p for p in gate._each_part(c) if p.category == "opaque_exec"]
    assert any("GH_CONFIG_DIR" in p.summary for p in parts), [p.summary for p in gate._each_part(c)]
