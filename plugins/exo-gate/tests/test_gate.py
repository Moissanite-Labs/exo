import pytest

from gate_fixtures import TEMPLATE
from exo import gate
from exo.gate import classify, decide, load_policies, parse_yaml

T = "terminal"


def sh(cmd):
    return (T, {"command": cmd})


# (action, category, reversibility, exposure)
CLASSIFY = [
    (sh("ls -la"), "read", "reversible", "private"),
    (sh("rm -rf build"), "delete", "irreversible", "private"),
    (sh("git push --force origin main"), "force_push", "irreversible", "internal"),
    (sh("git push origin +main"), "force_push", "irreversible", "internal"),
    (sh("git push origin feature"), "vcs_push", "costly", "internal"),
    (sh("git push origin --delete old"), "delete", "irreversible", "internal"),
    (sh("git reset --hard HEAD~1"), "history_rewrite", "irreversible", "private"),
    (sh("git checkout -- ."), "history_rewrite", "irreversible", "private"),
    (sh("git commit -m wip"), "local_write", "reversible", "private"),
    (sh("gh pr comment 12 -R o/r --body hi"), "github_post", "costly", "public"),
    (sh("gh pr review https://github.com/o/r/pull/5 --approve -b ok"), "github_post", "costly", "public"),
    (sh("gh issue create -R o/r -t T -b B"), "github_post", "costly", "public"),
    (sh("gh pr merge 3 -R o/r --squash"), "vcs_merge", "costly", "internal"),
    (sh("gh issue close 9 -R o/r"), "github_close", "costly", "public"),
    (sh("gh repo delete o/r --yes"), "delete", "irreversible", "public"),
    (sh("gh api repos/o/r/issues/1/comments -f body=hi"), "github_post", "costly", "public"),
    (sh("gh api repos/o/r/pulls"), "read", "reversible", "private"),
    (sh("gh release create v1.0 --notes x"), "publish", "irreversible", "public"),
    (sh("gh pr list"), "read", "reversible", "private"),
    (sh("gh auth refresh -s repo"), "credentials", "costly", "private"),
    (sh("curl https://example.com"), "web_read", "reversible", "private"),
    (sh("curl -X POST https://hooks.example.com/x -d '{}'"), "web_post", "irreversible", "public"),
    (sh("curl -d a=1 http://localhost:8080/api"), "local_write", "reversible", "private"),
    (sh("http POST api.example.com/items name=x"), "web_post", "irreversible", "public"),
    (sh("npm publish"), "publish", "irreversible", "public"),
    (sh("twine upload dist/*"), "publish", "irreversible", "public"),
    (sh("vercel --prod"), "deploy_production", "costly", "public"),
    (sh("vercel deploy"), "deploy_preview", "costly", "internal"),
    (sh("kubectl delete pod x"), "delete", "irreversible", "private"),
    (sh("cd app && git status; rm -rf dist"), "delete", "irreversible", "private"),
    (sh("ls\ngit push -f origin main"), "force_push", "irreversible", "internal"),
    (sh("bash -c 'git push -f origin dev'"), "force_push", "irreversible", "internal"),
    (sh("ssh box 'rm -rf /tmp/x'"), "delete", "irreversible", "internal"),
    (sh("sudo apt install jq"), "shell", "costly", "private"),
    (sh("psql -c 'DROP TABLE users'"), "delete", "irreversible", "private"),
    (sh("echo hi | mail -s test someone@example.com"), "email_send", "irreversible", "public"),
    (sh("stripe payment_intents create --amount 500"), "payment", "irreversible", "public"),
    (("send_message", {"target": "telegram:123", "message": "hi"}), "message_send", "irreversible", "public"),
    (("send_message", {"action": "list"}), "read", "reversible", "private"),
    (("react_to_message", {"emoji": "+1"}), "reaction", "reversible", "internal"),
    (("write_file", {"path": "src/a.py", "content": "x"}), "local_write", "reversible", "private"),
    (("write_file", {"path": "/home/u/.ssh/config", "content": "x"}), "credentials", "costly", "private"),
    (("patch", {"path": "app/.env", "new_string": "K=1"}), "credentials", "costly", "private"),
    (("read_file", {"path": "x"}), "read", "reversible", "private"),
    (("web_extract", {"urls": ["https://example.org/a"]}), "web_read", "reversible", "private"),
    (("browser_vault_fill", {}), "credentials", "costly", "private"),
    (("cronjob_manage", {"action": "create"}), "schedule", "costly", "private"),
    (("mcp_github_create_issue", {"repo": "o/r", "title": "t", "body": "b"}), "github_post", "costly", "public"),
    (("mcp_github_merge_pull_request", {"repo": "o/r"}), "vcs_merge", "costly", "internal"),
    (("mcp_stripe_create_payment", {"amount": "12.50"}), "payment", "irreversible", "public"),
    (("mcp_gmail_send_email", {"to": "x@example.com", "body": "hi"}), "email_send", "irreversible", "public"),
    (("mcp_gmail_create_draft", {"to": "x@example.com"}), "local_write", "reversible", "private"),
    (("mcp_notion_get_page", {"id": "1"}), "read", "reversible", "private"),
    (("mcp_x_post_tweet", {"text": "hi"}), "social_post", "irreversible", "public"),
    (("mcp_slack_send_message", {"channel": "#general", "text": "hi"}), "message_send", "irreversible", "public"),
    (("mcp_drive_delete_file", {"id": "1"}), "delete", "irreversible", "private"),
    (("mcp_linear_update_issue", {"id": "1"}), "remote_write", "costly", "internal"),
    (("some_brand_new_tool", {}), "unknown", "costly", "private"),
    (("execute_code", {"code": "import shutil; shutil.rmtree('x')"}), "delete", "irreversible", "private"),
    (("execute_code", {"code": "import requests\nrequests.post('https://x.io', json={})"}), "web_post",
     "irreversible", "public"),
    (("execute_code", {"code": "print(1)"}), "code", "reversible", "private"),
    ({"tool": "terminal", "args": '{"command": "git push --force-with-lease"}'}, "force_push", "irreversible",
     "internal"),
]


@pytest.mark.parametrize("action,category,rev,exp", CLASSIFY, ids=[str(c[0])[:60] for c in CLASSIFY])
def test_classify_table(action, category, rev, exp):
    c = classify(action)
    assert (c.category, c.reversibility, c.exposure) == (category, rev, exp), c


def test_classify_extracts_targets_text_and_links():
    c = classify(sh("gh pr comment 12 -R o/r --body 'hello there' "))
    assert c.target == "o/r#12" and c.link == "https://github.com/o/r/issues/12" and c.text == "hello there"
    c = classify(sh("gh pr review https://github.com/o/r/pull/5 -b ok"))
    assert c.target == "o/r#5" and c.link == "https://github.com/o/r/pull/5"
    c = classify(sh("gh api repos/o/r/issues/1/comments -f body=hi"))
    assert c.target == "o/r" and c.text == "hi"
    c = classify(sh("curl -X POST https://hooks.example.com/x -d a=1"))
    assert c.target == "hooks.example.com" and c.third_party
    c = classify(("mcp_stripe_create_payment", {"amount": "12.50"}))
    assert c.cost == 12.5
    assert classify(sh("stripe payment_intents create --amount 500")).cost == 500
    assert classify(("mcp_stripe_create_payment", {})).cost is None
    c = classify(sh("gh pr create -R o/r -t 'Fix x' --body-file notes.md"))
    assert c.text_file == "notes.md" and c.title == "Fix x"


def test_classify_heredoc_bodies():
    cmd = "gh pr comment 3 -R o/r --body \"$(cat <<'EOF'\nline one\nline two\nEOF\n)\""
    c = classify(sh(cmd))
    assert c.category == "github_post" and c.text == "line one\nline two"
    cmd = "gh issue comment 4 -R o/r --body-file - <<EOF\nstdin body\nEOF"
    c = classify(sh(cmd))
    assert c.text == "stdin body" and c.text_file == ""


def test_classification_ledger_view_hides_text():
    c = classify(sh("gh pr comment 1 -R o/r --body secret-ish"))
    d = c.as_dict()
    assert "text" not in d and "command" not in d and d["text_chars"] == len("secret-ish")


# --- decide with the template policies ----------------------------------------------------

TEMPLATE_DECIDE = [
    (sh("ls"), "allow"),
    (("write_file", {"path": "a.py"}), "allow"),
    (sh("git push origin feature"), "log"),
    (sh("vercel deploy"), "log"),
    (sh("git push -f origin main"), "ask"),
    (sh("rm -rf build"), "ask"),
    (sh("gh pr comment 1 -R o/r --body hi"), "ask"),
    (("send_message", {"target": "discord:#x", "message": "hi"}), "ask"),
    (("mcp_stripe_create_payment", {"amount": "1"}), "ask"),
    (sh("gh auth login"), "ask"),
    (sh("vercel --prod"), "ask"),
    (("some_brand_new_tool", {}), "allow"),
]


@pytest.mark.parametrize("action,outcome", TEMPLATE_DECIDE, ids=[str(c[0])[:60] for c in TEMPLATE_DECIDE])
def test_decide_template(action, outcome):
    pol = load_policies(TEMPLATE)
    assert decide(classify(action), pol).outcome == outcome


def test_decide_without_policies_uses_defaults():
    assert decide(classify(sh("rm x"))).outcome == "ask"
    assert decide(classify(sh("cat x")), None).outcome == "allow"


# --- decide with a sample principal's policies --------------------------------------------

FOOT = "> Posted with agent help, reviewed by a person."

EXAMPLE_DECIDE = [
    (sh("gh pr merge 3 -R acme-org/app --squash"), "allow", "category"),
    (sh(f"gh pr comment 3 -R acme-org/app --body 'Looks good.\n\n{FOOT}'"), "log", "matrix"),
    (sh("gh pr comment 3 -R acme-org/app --body 'Looks good.'"), "ask", "posting"),
    (sh(f"gh pr comment 3 -R acme-org/app --body 'Fine \u2014 merged.\n{FOOT}'"), "ask", "posting"),
    (sh(f"gh pr comment 3 -R acme-org/app --body 'Scout checked it.\n{FOOT}'"), "ask", "posting"),
    (sh(f"gh pr comment 3 -R acme-org/app --body 'thanks @sample-principal\n{FOOT}'"), "ask", "posting"),
    (sh(f"gh pr comment 3 -R other/repo --body 'Suggest a fix.\n{FOOT}'"), "ask", "escalate"),
    (sh("gh issue close 9 -R acme-org/app"), "ask", "reserved"),
    (sh("git push --force origin main"), "deny", "deny"),
    (sh("git push --force origin dev"), "ask", "reserved"),
    (("send_message", {"target": "telegram:own-home", "message": "done"}), "allow", "rule"),
    (("send_message", {"target": "telegram:someone", "message": "hi"}), "ask", "escalate"),
    (sh("curl -X POST https://api.example.com/x -d a=1"), "ask", "escalate"),
    (("mcp_stripe_create_payment", {"amount": "50"}), "ask", "reserved"),
    (sh("ls"), "allow", "matrix"),
]


@pytest.mark.parametrize("action,outcome,rule", EXAMPLE_DECIDE, ids=[str(c[0])[:70] for c in EXAMPLE_DECIDE])
def test_decide_example_principal(example_store, action, outcome, rule):
    pol = load_policies(example_store)
    d = decide(classify(action), pol)
    assert (d.outcome, d.rule) == (outcome, rule), d


def test_posting_violations_are_listed(example_store):
    pol = load_policies(example_store)
    d = decide(classify(sh("gh pr comment 3 -R acme-org/app --body 'Scout Prime says hi \u2014 ok'")), pol)
    assert d.outcome == "ask"
    assert any("missing footer" in v for v in d.violations)
    assert "forbidden: em dash" in d.violations
    assert "forbidden word: Scout Prime" in d.violations and "forbidden word: scout" in d.violations


def test_floor_cannot_be_lowered_by_rule(example_store):
    pol = load_policies(example_store)
    d = decide(classify(sh("curl -X POST https://api.example.com/x -d a=1")), pol)
    assert d.outcome == "ask"      # the rule says act; third party escalation and floor still ask
    pol["gates"]["escalate"]["third_party"] = False
    d = decide(classify(sh("curl -X POST https://api.example.com/x -d a=1")), pol)
    assert (d.outcome, d.rule) == ("ask", "floor")


def test_budget_escalation():
    pol = gate.normalize_policies({"budget": {"per_action_max": 10, "per_day_max": 20},
                                   "gates": {"categories": {"remote_write": "act"},
                                             "escalate": {"third_party": False, "categories": []}}})
    c = classify(("mcp_shop_update_cart", {}))
    c.cost = 5
    assert decide(c, pol).outcome == "allow"
    assert decide(c, pol, {"spent_today": 18}).outcome == "ask"
    c.cost = 50
    d = decide(c, pol)
    assert d.outcome == "ask" and "per-action" in d.reason


def test_category_override_raises_and_unknown_rule_key_never_matches():
    pol = gate.normalize_policies({"gates": {"categories": {"vcs_push": "ask"},
                                             "rules": [{"match": {"categry": "read"}, "decision": "deny"}]}})
    assert decide(classify(sh("git push origin x")), pol).outcome == "ask"
    assert decide(classify(sh("ls")), pol).outcome == "allow"


def test_summary_line_has_link_and_default(example_store):
    pol = load_policies(example_store)
    c = classify(sh("gh pr comment 12 -R other/repo --body hi"))
    d = decide(c, pol)
    line = gate.summarize(c, d, "skip")
    assert "\n" not in line and "https://github.com/other/repo/issues/12" in line
    assert line.endswith("Default if no reply: skip.")


def test_bad_policy_values_raise():
    with pytest.raises(gate.PolicyError):
        gate.normalize_policies({"gates": {"matrix": {"costly": {"public": "maybe"}}}})
    with pytest.raises(gate.PolicyError):
        gate.normalize_policies({"gates": {"rules": [{"match": {"category": "x"}}]}})
    with pytest.raises(gate.PolicyError):
        gate.normalize_policies({"budget": {"per_action_max": "lots"}})


# --- YAML subset and policy files ---------------------------------------------------------

def test_parse_yaml_subset():
    text = """
# comment
a: 1
b: 2.5
c: "x # not a comment"   # a comment
d: 'it''s'
e: [one, "two, three", 3]
f: {k: v, n: 2}
g:
  - plain
  - {pattern: "\\u2014", label: dash}
  - key: v
    other: [1, 2]
h:
  nested:
    deeper: true
i: >
  folded
  text
j: |
  line1
  line2
k:
- same-indent list
l: null
m: ~
n: yes
"""
    v = parse_yaml(text)
    assert v["a"] == 1 and v["b"] == 2.5 and v["c"] == "x # not a comment" and v["d"] == "it's"
    assert v["e"] == ["one", "two, three", 3] and v["f"] == {"k": "v", "n": 2}
    assert v["g"] == ["plain", {"pattern": "\u2014", "label": "dash"}, {"key": "v", "other": [1, 2]}]
    assert v["h"] == {"nested": {"deeper": True}}
    assert v["i"] == "folded text\n" and v["j"] == "line1\nline2\n"
    assert v["k"] == ["same-indent list"] and v["l"] is None and v["m"] is None and v["n"] is True


@pytest.mark.parametrize("bad", ["a: 1\n   b: 2\n", "a: [1, 2\n", "just text\n", 'a: "open\n'])
def test_parse_yaml_rejects_malformed(bad):
    with pytest.raises(gate.PolicyError):
        parse_yaml(bad)


def test_template_policies_parse_and_stay_generic():
    pol = load_policies(TEMPLATE)
    assert pol["gates"]["matrix"]["costly"]["internal"] == "log"
    assert pol["gates"]["scopes"] == {"private": [], "internal": []}
    assert pol["gates"]["reserved"] == [] and pol["gates"]["deny"] == [] and pol["gates"]["rules"] == []
    assert pol["posting"]["footer"] == "" and pol["posting"]["forbid"] == []
    assert pol["budget"]["per_action_max"] == 0
    for name in ("gates.yaml", "budget.yaml", "interruptions.yaml"):
        assert isinstance(parse_yaml((TEMPLATE / "policies" / name).read_text()), dict)


def test_example_store_policies_load(example_store):
    pol = load_policies(example_store)
    assert pol["gates"]["scopes"]["internal"] == ["acme-org/*"]
    assert pol["gates"]["categories"] == {"vcs_merge": "allow"}
    assert pol["posting"]["footer"] == FOOT
    assert pol["posting"]["forbid"][0] == {"pattern": "\u2014", "label": "em dash"}
    assert set(pol["agents"]) == {"scout", "Scout Prime"}
    assert pol["budget"]["per_day_max"] == 20


def test_missing_store_gives_defaults(tmp_path):
    assert load_policies(tmp_path / "nope")["gates"]["matrix"]["irreversible"]["public"] == "ask"
    assert load_policies(None)["posting"]["footer"] == ""
