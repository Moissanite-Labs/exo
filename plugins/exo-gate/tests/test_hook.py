import asyncio
import importlib.util
import json
import sys

import pytest

from gate_fixtures import PLUGIN
from exo import crypto
from exo.ledger import Ledger
from exo_gate import hook
from exo_gate.hook import resolve_settings

FOOT = "> Posted with agent help, reviewed by a person."


class MockCtx:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


@pytest.fixture
def keyed_store(example_store, tmp_path):
    key = tmp_path / "keys" / "exo-gate.key"
    crypto.save_key(crypto.keygen(), key)
    return example_store, key


def make_gate(store, key=None, **extra):
    ctx = MockCtx({"principal_store": str(store) if store else "", "key_path": str(key) if key else "", **extra})
    g = hook.register(ctx)
    return g, ctx


def events(store):
    with Ledger(store / "ledger.sqlite") as led:
        return list(led.iter(kind="action"))


def call(ctx, tool, args, **kw):
    [cb] = ctx.hooks["pre_tool_call"]  # an async hook: the core awaits it
    kw.setdefault("author", "principal:tui")  # the core sends who started the turn (#598); pass author="" for none
    return asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1",
                          task_id="", **kw))


def test_register_wires_both_hooks(keyed_store):
    _, ctx = make_gate(*keyed_store)
    assert set(ctx.hooks) == {"pre_tool_call", "post_approval_response"}


def test_internal_error_on_read_tool_fails_closed(keyed_store):
    # An internal error in the gate's own classification (e.g. a malformed policy
    # regex) must not silently allow a read tool. It must fall through to an
    # approval card. Regression for aska-digital/exo-desktop issue 11 (D-084).
    store, key = keyed_store
    g, ctx = make_gate(store, key)

    def _boom(*a, **k):
        raise RuntimeError("policy regex broken")

    g._pre_tool_call = _boom
    res = call(ctx, "read_file", {"path": "x"})
    assert res is not None, "a read tool must not be silently allowed on a gate error"
    assert res["action"] == "approve"
    assert res.get("once") is True, "an error card must not become a standing grant"


def test_allow_returns_none_and_records(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    assert call(ctx, "read_file", {"path": "x"}) is None
    [ev] = events(store)
    assert ev["kind"] == "action" and ev["author"] == "exo-gate" and ev["session"] == "s1"
    assert ev["body"]["outcome"] == "allow" and ev["body"]["classification"]["category"] == "read"
    with Ledger(store / "ledger.sqlite") as led:
        assert led.verify()["ok"]


@pytest.mark.parametrize("secret", [False, True])
def test_codex_patch_move_to_is_gated_before_execution(keyed_store, tmp_path, secret):
    store, key = keyed_store
    destination = tmp_path / ".ssh" / "config" if secret else store / "policies" / "gates.yaml"
    before = destination.read_bytes() if destination.exists() else None
    _, ctx = make_gate(store, key)
    result = call(ctx, "apply_patch", {"patch": "*** Begin Patch\n*** Update File: src.txt\n"
                  f"*** Move to: {destination}\n@@\n-before\n+after\n*** End Patch\n"})
    assert result["action"] == "approve"
    [ev] = events(store)
    assert ev["body"]["outcome"] == "ask"
    assert ev["body"]["classification"]["category"] == ("credentials" if secret else "store_write")
    assert (destination.read_bytes() if destination.exists() else None) == before


@pytest.mark.parametrize("bare", ["*** Move to:", "*** Add File:", "*** Move File: a ->"])
@pytest.mark.parametrize("path", [".ssh/config", ".exo/keys/k"])
def test_a_bare_patch_header_does_not_hide_the_next_write(keyed_store, tmp_path, monkeypatch, bare, path):
    """#548 follow-up: the bare header no longer takes the next header line as its path. A relative path, since an
    absolute or home one still matched inside the swallowed header line."""
    store, key = keyed_store
    monkeypatch.chdir(tmp_path)
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"before\n")
    _, ctx = make_gate(store, key)
    result = call(ctx, "apply_patch", {"patch": f"*** Begin Patch\n{bare}\n*** Add File: {path}\n+x\n*** End Patch\n"})
    assert result["action"] == "approve"
    [ev] = events(store)
    assert ev["body"]["outcome"] == "ask" and ev["body"]["classification"]["category"] == "credentials"
    assert target.read_bytes() == b"before\n"


@pytest.mark.parametrize("bare", ["*** Move to:", "*** Add File:", "*** Move File: a ->"])
def test_a_bare_patch_header_does_not_hide_a_store_write(keyed_store, tmp_path, monkeypatch, bare):
    """#548 follow-up: a bare header before a store path asks as a store write through the hook, and the
    target file is not written."""
    store, key = keyed_store
    monkeypatch.chdir(tmp_path)
    path = ".exo/policies/default.yaml"
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"before\n")
    _, ctx = make_gate(store, key)
    result = call(ctx, "apply_patch", {"patch": f"*** Begin Patch\n{bare}\n*** Add File: {path}\n+x\n*** End Patch\n"})
    assert result["action"] == "approve"
    [ev] = events(store)
    assert ev["body"]["outcome"] == "ask" and ev["body"]["classification"]["category"] == "store_write"
    assert target.read_bytes() == b"before\n"


def test_ask_returns_approve_with_summary_link_default(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": f"gh pr comment 12 -R other/repo --body 'Suggest a fix.\n{FOOT}'"})
    assert r["action"] == "approve"
    assert "https://github.com/other/repo/issues/12" in r["message"]
    assert "Default if no reply: skip." in r["message"] and "\n" not in r["message"]
    assert r["rule_key"].startswith("exo-gate:github_post:")
    [ev] = events(store)
    assert ev["body"]["outcome"] == "ask" and ev["body"]["rule_key"] == r["rule_key"]
    assert "Suggest a fix" not in str(ev["body"])          # outbound text is not copied into the log


def test_posting_violations_reach_the_message(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "gh pr comment 3 -R acme-org/app --body 'Scout says hi \u2014 ok'"})
    assert r["action"] == "approve"
    assert "Fix:" in r["message"] and "em dash" in r["message"] and "missing footer" in r["message"]
    assert "forbidden word: scout" in r["message"]


def test_body_file_is_checked(keyed_store, tmp_path):
    store, key = keyed_store
    body = tmp_path / "body.md"
    body.write_text("Plain text without the footer.\n")
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": f"gh pr comment 3 -R acme-org/app --body-file {body}"})
    assert r["action"] == "approve" and "missing footer" in r["message"]
    body.write_text(f"Clean text.\n\n{FOOT}\n")
    assert call(ctx, "terminal", {"command": f"gh pr comment 3 -R acme-org/app --body-file {body}"}) is None


def test_deny_returns_block(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "git push --force origin main"})
    assert r["action"] == "block" and r["message"].startswith("exo-gate blocked")
    assert events(store)[-1]["body"]["outcome"] == "deny"


def test_log_outcome_runs(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "git push origin feature"}) is None
    assert events(store)[-1]["body"]["outcome"] == "log"


def test_approval_response_is_linked(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "rm -rf build"})
    [resp] = ctx.hooks["post_approval_response"]
    resp(command="", description="", pattern_key="plugin_rule:" + r["rule_key"], pattern_keys=(),
         session_key="k", surface="cli", choice="once", decided_by="principal")
    first, second = events(store)
    assert second["body"]["phase"] == "approval" and second["body"]["approved"] is True
    assert second["refs"] == [first["id"]]
    resp(pattern_key="plugin_rule:unknown", choice="deny")      # unknown keys are ignored
    assert len(events(store)) == 2


def test_rule_key_binds_public_acts_to_exact_args(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    a = call(ctx, "terminal", {"command": "gh pr comment 1 -R other/repo --body one"})["rule_key"]
    b = call(ctx, "terminal", {"command": "gh pr comment 1 -R other/repo --body two"})["rule_key"]
    assert a != b


def test_observe_mode_never_blocks(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key, mode="observe")
    assert call(ctx, "terminal", {"command": "git push --force origin main"}) is None
    assert events(store)[-1]["body"]["would"] == "block"


def test_no_store_still_gates_with_defaults():
    _, ctx = make_gate(None)
    assert call(ctx, "terminal", {"command": "rm -rf /tmp/x"})["action"] == "approve"
    assert call(ctx, "read_file", {"path": "x"}) is None


def test_store_without_key_generates_the_seat_key_and_ledgers(example_store, tmp_path, monkeypatch):
    import os
    import stat
    monkeypatch.setenv("EXO_SEAT", "scout")
    _, ctx = make_gate(example_store)
    assert call(ctx, "terminal", {"command": "ls"}) is None
    key = tmp_path / "exo-home" / "keys" / "scout.key"
    assert key.is_file() and stat.S_IMODE(os.stat(key).st_mode) == 0o600
    evs = events(example_store)
    assert evs and evs[-1]["body"]["tool"] == "terminal"
    with Ledger(example_store / "ledger.sqlite") as led:
        assert led.verify()["ok"]
    seed = key.read_text().strip()
    assert seed not in "".join(str(e) for e in evs)  # key material never lands in the ledger


def test_bad_policy_file_falls_back_to_defaults(keyed_store):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text("matrix:\n  costly: {public: maybe}\n")
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "gh pr merge 3 -R acme-org/app"})
    assert r is None or r["action"] == "approve"       # defaults: costly internal is log
    assert "policy error" in events(store)[-1]["body"]["decision"]["reason"]


@pytest.mark.parametrize("yaml, error", [
    ("escalate: [read]\n", "gates.escalate must be a mapping"),
    ("escalate: read\n", "gates.escalate must be a mapping"),
    ("escalate: {categories: read}\n", "gates.escalate.categories must be a list"),
])
def test_malformed_escalation_reports_the_policy_error_in_the_hook(keyed_store, yaml, error, caplog):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(yaml)
    _, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "ls"})["action"] == "approve"   # #557: read joins the escalation
    [ev] = events(store)
    assert "policy error" in ev["body"]["decision"]["reason"]
    assert error in caplog.text


# #557: a malformed escalate beside a tightening decides at least as strictly as both main before #549 (1d2f32ac,
# which ignored a non-mapping escalate) and main after it (edeb4f89, which used the defaults for the whole policy).
ESCALATE_SHAPES = ["escalate: [payment]\n", "escalate: []\n", "escalate: null\n",
                   "escalate:\n  # categories: [payment]\n  # third_party: true\n", "escalate: false\n",
                   "escalate: 0\n", "escalate: payment\n", "escalate: read\n", "escalate: {categories: payment}\n",
                   "escalate:\n  categories:\n"]
ESCALATE_TIGHTENINGS = [
    ("deny: [delete]\n", "rm -rf build", "deny"),
    ("reserved: [vcs_push]\n", "git push origin main", "ask"),
    ("categories: {read: deny}\n", "ls", "deny"),
    ("rules:\n  - match: {category: read}\n    decision: deny\n", "ls", "deny"),
    ("matrix:\n  reversible: {private: ask}\n", "touch x", "ask"),
]
PERMISSIVE = "matrix:\n" + "".join(f"  {r}: {{private: allow, internal: allow, public: allow}}\n"
                                   for r in ("reversible", "costly", "irreversible"))
APPROVERS_WITH_SCOUT = ("approvers:\n  default: [principal, seat:scout]\n  delete: [seat:scout]\n"
                        "always: {days: 90, outward_days: 60}\n")


def _decision(store):
    return [e for e in events(store) if e["body"].get("phase") == "decision"][-1]["body"]["decision"]


@pytest.mark.parametrize("shape", ESCALATE_SHAPES)
@pytest.mark.parametrize("tight, line, outcome", ESCALATE_TIGHTENINGS)
def test_malformed_escalation_keeps_the_principals_other_sections(keyed_store, shape, tight, line, outcome):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(tight + shape)
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": line})
    d = _decision(store)
    assert d["outcome"] == outcome
    if "reserved" in tight:
        assert d["reserved"] == [["vcs_push", "origin"]]


@pytest.mark.parametrize("shape", ESCALATE_SHAPES)
def test_malformed_escalation_says_the_stricter_applies(keyed_store, shape, caplog):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text("deny: [delete]\n" + shape)
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": "rm -rf build"})
    reason = _decision(store)["reason"]
    assert "policy error, stricter of your policy and the defaults applies: gates.escalate" in reason
    assert "defaults used" not in reason
    assert "stricter of the policy and the defaults applies: gates.escalate" in caplog.text


@pytest.mark.parametrize("shape", ESCALATE_SHAPES + ["escalate: {categories: [payment]}\n"])
@pytest.mark.parametrize("line, outcome", [("rm -rf build", "ask"), ("git push origin main", "log")])
def test_permissive_matrix_beside_malformed_escalation_keeps_the_defaults(keyed_store, shape, line, outcome):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(PERMISSIVE + shape)
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": line})
    valid = shape.startswith("escalate: {categories: [")
    assert _decision(store)["outcome"] == ("allow" if valid else outcome)   # a valid policy decides as before


@pytest.mark.parametrize("tight, line, outcome", ESCALATE_TIGHTENINGS)
def test_valid_escalation_beside_a_tightening_decides_as_before(keyed_store, tight, line, outcome):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(tight + "escalate: {categories: [payment]}\n")
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": line})
    d = _decision(store)
    assert d["outcome"] == outcome and "policy error" not in d["reason"]


def test_template_escalate_block_commented_out_keeps_the_other_sections(keyed_store):
    from gate_fixtures import TEMPLATE
    text = (TEMPLATE / "policies" / "gates.yaml").read_text()
    lines = text.splitlines(keepends=True)
    at = next(i for i, ln in enumerate(lines) if ln.startswith("escalate:")) + 1
    while lines[at].startswith("  "):
        lines[at] = "  # " + lines[at][2:]
        at += 1
    text = "".join(lines).replace("\ndeny: []", "\ndeny: [delete]")
    assert "escalate:\n  # cost_over_budget" in text and "deny: [delete]" in text
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(text)
    _, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "block"


def test_malformed_escalation_keeps_the_internal_names(keyed_store):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text("escalate: [payment]\n")
    (store / "policies" / "internal-names.txt").write_text("zebrafinch\n")
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": 'gh pr comment 5 -R acme-org/app -b "zebrafinch notes"'})
    assert any("zebrafinch" in v for v in _decision(store)["violations"])


# #557: when both policies ask, the defaults' violations still stand (the card's Fix line, the text-bound key), as
# they did when the defaults alone applied (edeb4f89).
@pytest.mark.parametrize("shape", ESCALATE_SHAPES)
@pytest.mark.parametrize("posting, line, hit", [
    ("applies_to: [web_post]\n", "gh pr comment 5 -R acme-org/app -F body.md", "cannot read the body"),
    ("scrub: {enabled: false}\n", 'gh pr comment 5 -R acme-org/app -b "see /home/alice/notes"', "home path"),
    ("scrub: {home_paths: false, internal_ids: false}\n",
     'gh issue comment 7 -R other-org/lib -b "job jb_0123456789abcdef failed"', "internal id"),
])
def test_malformed_escalation_keeps_the_defaults_violations(keyed_store, shape, posting, line, hit):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(shape)
    (store / "policies" / "posting.yaml").write_text(posting)
    _, ctx = make_gate(store, key)
    result = call(ctx, "terminal", {"command": line})
    d = _decision(store)
    assert d["outcome"] == "ask" and any(hit in v for v in d["violations"])
    assert hit in d["reason"]                       # at an equal outcome and exposure the defaults' reason stands
    assert hit in result["message"] and "Fix:" in result["message"]


# #557: the widest exposure of the two stands, so an approval binds to the exact line as the defaults bind it (here
# both ask: the principal's private scope as costly, the defaults' as public).
@pytest.mark.parametrize("shape", ESCALATE_SHAPES)
def test_malformed_escalation_keeps_the_defaults_binding(keyed_store, shape):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text("scopes:\n  private: [acme-org/*]\n"
                                                  "matrix:\n  costly: {private: ask}\n" + shape)
    _, ctx = make_gate(store, key)
    result = call(ctx, "terminal", {"command": "gh pr close 5 -R acme-org/app"})
    assert result["action"] == "approve" and result.get("binding")
    assert _decision(store)["exposure"] == "public"


def test_malformed_escalation_keeps_the_defaults_approvers_and_grant_days(keyed_store):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(PERMISSIVE + "escalate: [payment]\n")
    (store / "policies" / "approvals.yaml").write_text(APPROVERS_WITH_SCOUT)
    g, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "rm -rf build"})["approvers"] == ["principal"]
    assert (g._grant_days(False), g._grant_days(True)) == (30.0, 7.0)


def test_valid_escalation_keeps_the_principals_approvers(keyed_store):
    store, key = keyed_store
    (store / "policies" / "gates.yaml").write_text(PERMISSIVE.replace("irreversible: {private: allow",
                                                                      "irreversible: {private: ask")
                                                   + "escalate: {categories: [payment]}\n")
    (store / "policies" / "approvals.yaml").write_text(APPROVERS_WITH_SCOUT)
    g, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "rm -rf build"})["approvers"] == ["principal", "seat:scout"]
    assert (g._grant_days(False), g._grant_days(True)) == (90.0, 60.0)


def test_policy_changed_to_malformed_escalation_decides_beside_the_defaults_before_accept(keyed_store):
    store, key = keyed_store
    p = store / "policies" / "gates.yaml"
    p.write_text(PERMISSIVE + "escalate: {categories: [payment]}\n")
    (store / "policies" / "approvals.yaml").write_text(APPROVERS_WITH_SCOUT)
    _, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "rm -rf build"}) is None
    _bump(p, PERMISSIVE + "escalate: [payment]\n")
    r = call(ctx, "terminal", {"command": "rm -rf build"})   # as on edeb4f89, where the changed file was the defaults
    assert r["action"] == "approve" and r["approvers"] == ["principal"]


def test_policy_changed_to_malformed_escalation_tightens_and_accept_keeps_the_defaults(keyed_store):
    store, key = keyed_store
    p = store / "policies" / "gates.yaml"
    p.write_text("escalate: {categories: [payment]}\n")
    g, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "approve"
    _bump(p, "deny: [delete]\nescalate: [payment]\n")
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "block"
    row = [e["body"] for e in events(store) if e["body"].get("phase") == "policy_change"][-1]
    assert row["error"] == "gates.escalate must be a mapping"
    _bump(p, PERMISSIVE + "escalate: [payment]\n")
    from exo import principal
    token = principal.CALLER.set(principal.Caller("principal:tui"))
    try:
        assert g.accept_policies().startswith("accepted")
    finally:
        principal.CALLER.reset(token)
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "approve"   # the defaults still ask
    assert "stricter of your policy and the defaults" in _decision(store)["reason"]


# #557: a malformed pinned escalate changed to a valid policy that asks as the pinned one does keeps the changed
# policy's violation (the Fix line, the text-bound key), so a standing grant still asks, as on edeb4f89. Before the
# change the grant does not cover the line either: the defaults, in place of the pinned file there, do not ask for it.
@pytest.mark.parametrize("granted", [False, True])
def test_policy_changed_from_malformed_escalation_keeps_the_changed_violations(keyed_store, granted):
    from exo import gate
    store, key = keyed_store
    p, line = store / "policies" / "gates.yaml", "curl -X POST -d 'zebrafinch' http://localhost:8080/x"
    p.write_text("categories: {local_write: ask, web_post: ask}\nescalate: [payment]\n")
    g, ctx = make_gate(store, key)
    if granted:
        c = gate.classify({"tool": "terminal", "args": {"command": line}})
        g.grants.add(c.category, c.target, "", 30, "principal", "terminal")
    assert call(ctx, "terminal", {"command": line})["action"] == "approve"   # pins the policy
    assert "grant" not in [e["body"] for e in events(store)][-1]
    _bump(p, "categories: {local_write: ask, web_post: ask}\n")
    _bump(store / "policies" / "posting.yaml", "applies_to: [local_write, web_post]\nforbid_words: [zebrafinch]\n")
    result = call(ctx, "terminal", {"command": line})
    assert result["action"] == "approve" and "Fix: forbidden word: zebrafinch" in result["message"]
    d = _decision(store)
    assert d["violations"] == ["forbidden word: zebrafinch"] and "zebrafinch" in d["reason"]


# #557: while a policy error stands, a standing grant applies only where the decision as on edeb4f89 (the defaults in
# place of the file with the error, then the decision model's verdict) asks and finds a grant too. Otherwise an
# unbound grant would run a call edeb4f89 logs (a restored reserved entry asks), and a grant bound to the exact line a
# call it asks for (a restored or honoured `read` binds the sudo write line, which edeb4f89 does not bind).
VALID_ESC, LW_ASK = "escalate: {categories: [payment]}\n", "categories: {local_write: ask}\n"
SUDO_LINES = ["sudo echo hi > /tmp/f", "sudo cat /etc/hosts > /tmp/f"]
GRANT_ASKS = [   # the first gates.yaml, the changed one (None: unchanged), the line, bound, the model's verdict
    ("reserved: [vcs_push]\nescalate: [payment]\n", None, "git push origin main", False, None),
    ("reserved: [vcs_push]\nescalate: [payment]\n", None, "ls; git push origin main", False, None),
    ('reserved: ["target:acme-org/*"]\nescalate: [payment]\n', None, "gh pr merge 3 -R acme-org/app", False, None),
    *[row for line in SUDO_LINES for row in (
        ("escalate: [read]\n", LW_ASK + VALID_ESC, line, True, None),
        (LW_ASK + VALID_ESC, "escalate: [read]\n", line, True, None),
        (LW_ASK + "escalate: [read]\n", LW_ASK + VALID_ESC, line, True, None),
        ("escalate: [read]\n", None, line, True, "ask"),
        ("categories: {read: ask}\nescalate: [payment]\n", None, line, True, "ask"),
        ("categories: {read: ask}\nescalate: [payment]\n", LW_ASK + VALID_ESC, line, True, None))],
]
GRANT_APPLIES = [   # edeb4f89 applies the grant too, so it still applies
    ("reserved: [vcs_push]\n" + VALID_ESC, None, "git push origin main", False, None),
    ("reserved: [vcs_push]\nescalate: [payment]\n", None, "git push origin main", False, "ask"),
    ("categories: {read: ask}\nescalate: [payment]\n", "categories: {read: ask, local_write: ask}\n" + VALID_ESC,
     SUDO_LINES[0], True, None),
    # an unbound and a bound grant for one line: the merged decision binds the line and finds the bound grant, the
    # decision as on edeb4f89 does not bind it and finds the unbound one, so the call runs as it does there
    *[("escalate: [read]\n", None, line, "both", "ask") for line in SUDO_LINES],
    ("categories: {read: ask}\nescalate: [payment]\n", None, SUDO_LINES[0], "both", "ask"),
]

# #586: with two valid files, a standing grant applies only where each side's own decision (the model's verdict in)
# asks and finds one too, so the merged reading never runs a call one side logs (an unbound grant on the changed
# file's reserved hit) or a side asks for without that binding (a bound grant on the line only the changed file
# binds, which the pinned policy asks for unbound).
PUSH, READ_ESC = "git push origin main", "escalate: {categories: [payment, read]}\n"
VALID_ASKS = [   # the first gates.yaml, the changed one, the line, bound, the model's verdict
    (VALID_ESC, "reserved: [vcs_push]\n" + VALID_ESC, PUSH, False, None),
    (VALID_ESC, "reserved: [vcs_push]\n" + VALID_ESC, "ls; " + PUSH, False, None),
    *[row for line in SUDO_LINES for row in (
        (LW_ASK + VALID_ESC, READ_ESC, line, True, None),
        (READ_ESC, LW_ASK + VALID_ESC, line, True, None),
        (LW_ASK + VALID_ESC, "categories: {read: ask, local_write: ask}\n" + VALID_ESC, line, True, None),
        (LW_ASK + VALID_ESC, READ_ESC, line, True, "ask"))],
]
VALID_APPLIES = [   # each side's own decision asks and finds a grant too, so the merged one runs the call
    (VALID_ESC, "reserved: [vcs_push]\n" + VALID_ESC, PUSH, False, "ask"),
    # an unbound and a bound grant for one line: the merged decision binds the line and finds the bound grant, the
    # side that asks unbound finds the unbound one, so the call runs as it does on main
    (LW_ASK + VALID_ESC, "categories: {read: ask, local_write: ask}\n" + VALID_ESC, SUDO_LINES[0], "both", None),
    (READ_ESC, LW_ASK + VALID_ESC, SUDO_LINES[0], "both", None),
]


def _granted(store, key, first, changed, line, bound, verdict):
    from exo import gate
    p, args = store / "policies" / "gates.yaml", {"command": line}
    p.write_text(first)
    g, _ = make_gate(store, key)
    c = gate.classify({"tool": "terminal", "args": args})
    for b in (False, True) if bound == "both" else (bound,):
        g.grants.add(c.category, c.target, gate.approval_binding(c, args) if b else "", 30, "principal", "terminal")
    g.policies()    # pins the first file
    if changed is not None:
        _bump(p, changed)
    result = g.pre_tool_call("terminal", args, session_id="s1", tool_call_id="c1", _verdict=verdict)
    return result, [e["body"] for e in events(store) if e["body"].get("phase") == "decision"][-1]


@pytest.mark.parametrize("first, changed, line, bound, verdict", GRANT_ASKS)
def test_malformed_escalation_grant_applies_only_where_the_defaults_find_it(keyed_store, first, changed, line, bound,
                                                                           verdict):
    result, body = _granted(*keyed_store, first, changed, line, bound, verdict)
    assert result["action"] == "approve" and body["outcome"] == "ask" and "grant" not in body


@pytest.mark.parametrize("first, changed, line, bound, verdict", GRANT_APPLIES)
def test_grant_the_defaults_apply_still_applies(keyed_store, first, changed, line, bound, verdict):
    result, body = _granted(*keyed_store, first, changed, line, bound, verdict)
    assert result is None and body["outcome"] == "allow" and body["grant"]


@pytest.mark.parametrize("first, changed, line, bound, verdict", VALID_ASKS)
def test_valid_files_grant_applies_only_where_each_side_finds_one(keyed_store, first, changed, line, bound, verdict):
    result, body = _granted(*keyed_store, first, changed, line, bound, verdict)
    assert result["action"] == "approve" and body["outcome"] == "ask" and "grant" not in body


@pytest.mark.parametrize("first, changed, line, bound, verdict", VALID_APPLIES)
def test_valid_files_grant_applies_where_each_side_finds_one(keyed_store, first, changed, line, bound, verdict):
    result, body = _granted(*keyed_store, first, changed, line, bound, verdict)
    assert result is None and body["outcome"] == "allow" and body["grant"]


def test_a_single_valid_file_still_decides_alone(keyed_store):
    # #586: with no change there is no merge and no check on the sides: the pinned decision stands as gate.decide
    # made it, grant and all
    from exo import gate
    store, key = keyed_store
    args = {"command": "sudo echo hi > /tmp/f"}
    (store / "policies" / "gates.yaml").write_text(LW_ASK + VALID_ESC)
    g, ctx = make_gate(store, key)
    c = gate.classify({"tool": "terminal", "args": args})
    g.grants.add(c.category, c.target, gate.approval_binding(c, args), 30, "principal", "terminal")
    g.policies()
    result = g.pre_tool_call("terminal", args, session_id="s1", tool_call_id="c1")
    d = _decision(store)
    assert result["action"] == "approve" and d["outcome"] == "ask"
    assert d == gate.decide(c, g.policies(), {"held_body_files": []}).as_dict()
    assert "policy files changed" not in d["reason"] and "policy error" not in d["reason"]


def test_two_valid_files_keep_the_changed_violation_at_an_equal_outcome(keyed_store):
    # #586: the old and new decisions merge as one stricter reading, so a posting finding the changed files met
    # stands on the card at an equal outcome, as the #557 rows keep it beside a policy error
    store, key = keyed_store
    p, line = store / "policies" / "gates.yaml", "curl -X POST -d 'zebrafinch' http://localhost:8080/x"
    p.write_text(LW_ASK + VALID_ESC)
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": line})
    assert "zebrafinch" not in _decision(store)["reason"]
    _bump(p, LW_ASK + VALID_ESC)
    _bump(store / "policies" / "posting.yaml", "applies_to: [local_write]\nforbid_words: [zebrafinch]\n")
    call(ctx, "terminal", {"command": line})
    d = _decision(store)
    assert d["violations"] == ["forbidden word: zebrafinch"] and "zebrafinch" in d["reason"]


def test_two_valid_files_take_the_widest_exposure_and_keep_a_reserved_part(keyed_store):
    # #586: the exposure is the widest of the two, as a line's readings merge, so an approval binds where either
    # decision would bind it; a reserved part either policy met keeps its ask from a standing grant (D-075)
    store, key = keyed_store
    p = store / "policies" / "gates.yaml"
    p.write_text('scopes: {internal: ["acme-org/*"]}\nreserved: [github_close]\n')
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": "gh pr close 5 -R acme-org/app"})
    assert _decision(store)["exposure"] == "internal"
    assert _decision(store)["reserved"] == [["github_close", "acme-org/app#5"]]
    _bump(p, "reserved: [github_close]\n")
    call(ctx, "terminal", {"command": "gh pr close 5 -R acme-org/app"})
    d = _decision(store)
    assert d["exposure"] == "public" and d["outcome"] == "ask"
    assert d["reserved"] == [["github_close", "acme-org/app#5"]]


def test_gate_status_shows_a_standing_policy_error(keyed_store):
    store, key = keyed_store
    p = store / "policies" / "gates.yaml"
    p.write_text("escalate: [payment]\n")
    g, _ = make_gate(store, key)
    assert hook.gate_command(g, "status") == ("policy as loaded at session start\npolicy error (the stricter of your "
                                              "policy and the defaults applies): gates.escalate must be a mapping")
    _bump(p, "matrix:\n  costly: {public: maybe}\n")
    status = hook.gate_command(g, "status").splitlines()
    assert status[0].startswith("policy files changed") and len(status) == 3
    assert status[2].startswith("policy error in the changed files (the defaults apply): ")


def test_policy_reload_on_change(keyed_store):
    store, key = keyed_store
    g, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "git push origin x"}) is None
    import os
    p = store / "policies" / "gates.yaml"
    p.write_text(p.read_text().replace("vcs_merge: act", "vcs_merge: act\n  vcs_push: deny"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert call(ctx, "terminal", {"command": "git push origin x"})["action"] == "block"


def _bump(p, text):
    import os
    p.write_text(text)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def test_policy_change_mid_session_only_tightens_until_accepted(keyed_store):
    """D-038: a policy file changed during a session can tighten at once; loosening waits for the principal."""
    store, key = keyed_store
    g, ctx = make_gate(store, key)
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "approve"
    p = store / "policies" / "gates.yaml"
    loose = p.read_text().replace("  - delete\n", "").replace("vcs_merge: act", "vcs_merge: act\n  delete: act")
    _bump(p, loose.replace("irreversible: {private: ask", "irreversible: {private: act"))
    r = call(ctx, "terminal", {"command": "rm -rf build"})
    assert r["action"] == "approve"                       # still the pinned (stricter) policy
    bodies = [e["body"] for e in events(store)]
    assert any(b.get("phase") == "policy_change" for b in bodies)
    assert "/gate accept" in bodies[-1]["decision"]["reason"]
    from exo import principal
    token = principal.CALLER.set(principal.Caller("principal:tui"))
    try:
        assert hook.gate_command(g, "accept").startswith("accepted")
    finally:
        principal.CALLER.reset(token)
    assert call(ctx, "terminal", {"command": "rm -rf build"}) is None
    assert any(e["body"].get("phase") == "policy_accept" for e in events(store))
    assert hook.gate_command(g, "status").startswith("policy")


def test_required_policy_accept_audit_failure_keeps_prior_active_policy(keyed_store, monkeypatch, request):
    store, key = keyed_store
    g, ctx = make_gate(store, key, decision_model=False)
    g.required_getter = lambda: True
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "approve"
    prior = g.policies()
    p = store / "policies" / "gates.yaml"
    loose = p.read_text().replace("  - delete\n", "").replace("vcs_merge: act", "vcs_merge: act\n  delete: act")
    _bump(p, loose.replace("irreversible: {private: ask", "irreversible: {private: act"))
    append = g.sink.append
    def fail_accept(body, **kw):
        return None if body.get("phase") == "policy_accept" else append(body, **kw)
    monkeypatch.setattr(g.sink, "append", fail_accept)
    from exo import principal
    token = principal.CALLER.set(principal.Caller("principal:tui"))
    request.addfinalizer(lambda: principal.CALLER.reset(token))
    with pytest.raises(hook.GateAuditError):
        g.accept_policies()
    assert g.policies() is prior
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "approve"
    assert not any(event["body"]["phase"] == "policy_accept" for event in events(store))
    monkeypatch.setattr(g.sink, "append", append)
    assert g.accept_policies().startswith("accepted")
    assert call(ctx, "terminal", {"command": "rm -rf build"}) is None


def test_internal_error_fails_closed_including_reads(keyed_store, monkeypatch):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    monkeypatch.setattr(hook.gate, "classify", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    r = call(ctx, "read_file", {"path": "x"})
    assert r["action"] == "approve" and "boom" in r["message"] and r.get("once") is True
    r = call(ctx, "terminal", {"command": "ls"})
    assert r["action"] == "approve" and "boom" in r["message"]


def _raise(*a, **k):
    raise RuntimeError("boom")


@pytest.mark.parametrize("decision_model", [False, True])
def test_internal_error_card_is_once_only_for_every_call(keyed_store, monkeypatch, decision_model):
    """D-084: in enforce mode a gate error asks for every call, reads, a search and non-dict arguments included, and
    the card offers no "always"."""
    store, key = keyed_store
    _, ctx = make_gate(store, key, decision_model=decision_model)
    monkeypatch.setattr(hook.gate, "classify", _raise)
    for tool, args in [("read_file", {"path": "README.md"}), ("search_files", {"pattern": "x", "path": "."}),
                       ("read_file", ["README.md"]), ("terminal", {"command": "ls"})]:
        r = call(ctx, tool, args)
        assert r["action"] == "approve" and r["once"] is True and r["rule_key"] == f"exo-gate:error:{tool}", r


@pytest.mark.parametrize("decision_model", [False, True])
def test_internal_error_in_observe_mode(keyed_store, monkeypatch, decision_model):
    """D-084: observe mode lets a call ahead on a gate error, as before, unless the gate reads it as a store write or
    a secret. Those ask the principal alone, once, and a secret check that raises counts as a secret."""
    store, key = keyed_store
    g, ctx = make_gate(store, key, mode="observe", decision_model=decision_model)
    monkeypatch.setattr(g, "_rules", _raise)
    assert call(ctx, "read_file", {"path": "README.md"}) is None
    assert call(ctx, "terminal", {"command": "ls"}) is None
    for tool, args in [("read_file", {"path": "~/.exo/keys/seat.key"}),
                       ("write_file", {"path": "~/.exo/policies/gates.yaml", "content": "x"})]:
        r = call(ctx, tool, args)
        assert r["action"] == "approve" and r["once"] is True and r["approvers"] == ["principal"], (tool, r)
    monkeypatch.setattr(hook.gate, "args_sensitive", _raise)
    r = call(ctx, "read_file", {"path": "README.md"})
    assert r["action"] == "approve" and r["once"] is True and r["approvers"] == ["principal"], r


def test_settings_resolution(tmp_path):
    env = {"EXO_HOME": str(tmp_path), "EXO_PRINCIPAL": "p1", "EXO_GATE_MODE": "observe"}
    s = resolve_settings(env=env)
    assert s.principal_store == tmp_path / "principals" / "p1" and s.mode == "observe"
    assert s.key_path == tmp_path / "keys" / "exo-gate.key"
    s = resolve_settings({"principal_store": str(tmp_path / "x"), "mode": "bogus"}, env=env)
    assert s.principal_store == tmp_path / "x" and s.mode == "enforce"
    s = resolve_settings(env={"EXO_HOME": str(tmp_path)})
    assert s.principal_store is None


def test_settings_find_a_store_made_with_root(tmp_path):
    """`exo init <id> --root DIR` records DIR in config `principals.<id>`: the gate reads that store's policies,
    ledger and grants, as memory and the store commands do. Not a principal id: no store, the default policies."""
    (tmp_path / "config.json").write_text(json.dumps({"principals": {"p1": str(tmp_path / "elsewhere")}}))
    s = resolve_settings(env={"EXO_HOME": str(tmp_path), "EXO_PRINCIPAL": "p1"})
    assert s.principal_store == tmp_path / "elsewhere"
    assert s.grants_path == tmp_path / "elsewhere" / "state" / "approval-grants.json"
    s = resolve_settings(env={"EXO_HOME": str(tmp_path), "EXO_PRINCIPAL": "../p1"})
    assert s.principal_store is None and s.grants_path == tmp_path / "state" / "gate-grants.json"
    # a recorded root that cannot be a store (unknown user, relative) is skipped: the default store, no crash
    (tmp_path / "config.json").write_text(json.dumps({"principals": {"p1": "~nosuchuser-exo-test/x", "p2": "rel/x"}}))
    for pid in ("p1", "p2"):
        s = resolve_settings(env={"EXO_HOME": str(tmp_path), "EXO_PRINCIPAL": pid})
        assert s.principal_store == tmp_path / "principals" / pid


def test_plugin_entry_point_imports(monkeypatch):
    spec = importlib.util.spec_from_file_location("exo_gate_plugin", PLUGIN / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "exo_gate_plugin", mod)
    spec.loader.exec_module(mod)
    ctx = MockCtx()
    mod.register(ctx)
    assert "pre_tool_call" in ctx.hooks


# --- decision model seam (D-015) ---------------------------------------------------------

def decision_router(top, calls=None):
    """A real Router whose decision role is a mocked decision API that answers with `top` as the top option."""
    import httpx
    from exo.core.models import Router

    def handler(req):
        if calls is not None:
            calls.append(req)
        probs = {o: 0.05 for o in ("allow", "log", "ask", "deny")}
        probs[top] = 0.85
        return httpx.Response(200, json={"model": "m", "usage": {}, "answers": {"q": {
            "type": "choice", "choice": top, "probabilities": probs, "confidence": 0.9}}})
    return Router(roles={"decision": ["jev:jev-latest"]}, env={},
                  client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                  providers={"jev": {"transport": "decision", "base_url": "https://x/v1", "roles": ["decision"]}})


def gated(store, key, router, enabled=True):
    ctx = MockCtx({"principal_store": str(store), "key_path": str(key),
                   "decision_model": enabled})
    ctx.router = router
    hook.register(ctx)
    return ctx


def test_decision_model_can_be_turned_off_and_is_then_never_asked(keyed_store):
    store, key = keyed_store
    calls = []
    ctx = gated(store, key, decision_router("deny", calls), enabled=False)
    assert call(ctx, "read_file", {"path": "x"}) is None
    assert calls == []


def test_model_deny_tightens_allow_to_deny(keyed_store):
    store, key = keyed_store
    calls = []
    ctx = gated(store, key, decision_router("deny", calls))
    r = call(ctx, "read_file", {"path": "x"})
    assert r["action"] == "block" and len(calls) == 1
    assert events(store)[-1]["body"]["outcome"] == "deny"


def test_model_allow_never_loosens_ask(keyed_store):
    store, key = keyed_store
    ctx = gated(store, key, decision_router("allow"))
    r = call(ctx, "terminal", {"command": f"gh pr comment 12 -R other/repo --body 'Suggest a fix.\n{FOOT}'"})
    assert r["action"] == "approve"
    assert events(store)[-1]["body"]["outcome"] == "ask"


def test_model_not_consulted_when_rules_deny(keyed_store):
    store, key = keyed_store
    calls = []
    ctx = gated(store, key, decision_router("allow", calls))
    r = call(ctx, "terminal", {"command": "git push --force origin main"})
    assert r["action"] == "block" and calls == []


def test_model_failure_keeps_the_rule_outcome(keyed_store):
    store, key = keyed_store
    ctx = gated(store, key, None)  # no router yet
    assert call(ctx, "read_file", {"path": "x"}) is None


def test_decision_model_is_on_by_default():
    assert hook.resolve_settings(env={}).decision_model is True


def test_decision_call_goes_to_jev_first_and_the_route_is_recorded(keyed_store):
    store, key = keyed_store
    calls = []
    ctx = gated(store, key, decision_router("ask", calls))
    r = call(ctx, "read_file", {"path": "x"})
    assert r["action"] == "approve" and len(calls) == 1
    assert str(calls[0].url).startswith("https://x/v1")  # the fake transport saw the jev call
    dm = events(store)[-1]["body"]["decision_model"]
    assert dm == {"verdict": "ask", "route": "jev:jev-latest", "source": "decision"}


def test_fallback_route_is_recorded_when_jev_fails(keyed_store):
    import httpx
    from exo.core.models import Router

    store, key = keyed_store

    def handler(req):
        return httpx.Response(500, json={"error": "down"})
    router = Router(roles={"decision": ["jev:jev-latest"]}, env={},
                    client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                    providers={"jev": {"transport": "decision", "base_url": "https://x/v1", "roles": ["decision"]}})
    ctx = gated(store, key, router)
    assert call(ctx, "read_file", {"path": "x"}) is None  # the model failed; the rule outcome stands
    assert "decision_model" not in events(store)[-1]["body"]


def test_malformed_model_answer_keeps_the_rule_outcome_and_the_main_role_working(keyed_store):
    from exo.core.models import FakeProvider, Router

    store, key = keyed_store
    chat = FakeProvider(["I would say ask, to be safe.", "main reply"], name="chat")
    router = Router(roles={"aux": ["chat:small"], "main": ["chat:m"]}, env={}, instances={"chat": chat},
                    catalog={"providers": {}})
    ctx = gated(store, key, router)  # the decision role falls back to aux on the main credential (never main, D-081)
    assert call(ctx, "read_file", {"path": "x"}) is None  # no JSON verdict: the rule outcome stands
    assert len(chat.calls) == 1 and "decision function" in chat.calls[0]["messages"][0].text()

    async def main_turn():
        return [e async for e in router.stream("main", [], [])][-1]
    assert router.cooldowns == {} and asyncio.run(main_turn()).kind == "done"


def test_credentials_always_ask_once_only_even_in_observe_mode(keyed_store):
    store, key = keyed_store
    for extra in ({}, {"mode": "observe"}):
        g, ctx = make_gate(store, key, decision_model=False, **extra)
        r = call(ctx, "read_file", {"path": "~/.config/exo/keys.env"})
        assert r["action"] == "approve" and r["once"] is True
        assert r["rule_key"].startswith("exo-gate:credentials:")
        r2 = call(ctx, "terminal", {"command": "security find-generic-password -s exo -w"})
        assert r2["action"] == "approve" and r2["once"] is True
    g, ctx = make_gate(store, key, decision_model=False)
    assert call(ctx, "read_file", {"path": "docs/keys.md"}) is None


REPLACE_WRITES = [
    # #651: the seat file, snake and camel spellings -- a replace that turns the seat's default into bypass
    ("mcp__fs__search_replace", {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}),
    ("mcp__fs__searchReplace", {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}),
    # a policy file
    ("search_replace", {"path": "~/.exo/policies/gates.yaml", "search": "ask", "replace": "bypass"}),
    ("find_and_replace_file", {"path": "~/.exo/policies/gates.yaml", "old_string": "ask", "new_string": "bypass"}),
    # config.json
    ("find_and_replace", {"path": "~/.exo/config.json", "old_string": '"ask"', "new_string": '"bypass"'}),
    ("findAndReplace", {"path": "~/.exo/config.json", "oldString": '"ask"', "newString": '"bypass"'}),
]


@pytest.mark.parametrize("tool,args", REPLACE_WRITES)
def test_a_read_named_tool_that_replaces_a_store_file_asks_in_enforce_and_observe(keyed_store, tool, args):
    """#651: the replace of the seat file, a policy file or config.json is a store_write the principal answers
    once, in enforce mode and in observe mode too, where `no_grant` keeps the ask from running the call."""
    store, key = keyed_store
    for extra in ({}, {"mode": "observe"}):
        _, ctx = make_gate(store, key, decision_model=False, **extra)
        r = call(ctx, tool, args)
        assert r["action"] == "approve" and r["once"] is True, (tool, extra, r)
        assert r["rule_key"].startswith("exo-gate:store_write:"), (tool, extra, r)
        assert (r["approvers"], r["once"]) == (["principal"], True), (tool, extra, r)


@pytest.mark.parametrize("tool,args", REPLACE_WRITES[:1] + [("read_file", {"path": "~/.exo/seats/principal.toml"})])
def test_the_replace_asks_where_a_plain_read_of_the_same_path_runs(keyed_store, tool, args):
    """#651's guard, through the hook: the seat file's replace asks where `read_file` of the same path runs with
    no ask, in both modes."""
    store, key = keyed_store
    for extra in ({}, {"mode": "observe"}):
        _, ctx = make_gate(store, key, decision_model=False, **extra)
        if tool == "read_file":
            assert call(ctx, tool, args) is None, (tool, extra)
        else:
            assert call(ctx, tool, args)["action"] == "approve", (tool, extra)


def test_internal_error_on_a_secret_read_is_not_a_free_pass(keyed_store, monkeypatch):
    store, key = keyed_store
    g, ctx = make_gate(store, key, decision_model=False)
    monkeypatch.setattr(g, "_rules", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    secret = g.pre_tool_call("read_file", {"path": "~/.exo/keys/seat.key"})
    assert secret["action"] == "approve" and secret.get("once") is True
    plain = g.pre_tool_call("read_file", {"path": "README.md"})
    assert plain["action"] == "approve" and plain.get("once") is True


@pytest.mark.parametrize("cat, tool, args, plain_args, key", [
    ("local_write", "write_file", {"path": "~/.exo/policies/gates.yaml", "content": "version: 1\n"},
     {"path": "notes.md", "content": "x"}, "store_write"),
    # #311: a read line whose store read from the shell call's own folder is a credentials part (abu-aska's repro
    # on the #452 + #467 merge: the fallback read the top category only, so observe mode ran the call)
    ("read", "bash", {"command": "cat config", "cwd": "~/.kube"}, {"command": "cat config", "cwd": "~/proj"}, "read"),
    ("read", "terminal", {"command": "cat config", "workdir": "~/.kube"}, {"command": "cat notes.md"}, "read"),
    ("force_push", "bash", {"command": "cat * && git push --force origin main", "cwd": "~/.claude/projects"},
     {"command": "git push --force origin main", "cwd": "~/proj"}, "force_push"),
])
def test_a_policy_pattern_that_does_not_compile_refuses_the_policy_and_the_defaults_still_ask_the_principal(
        cat, tool, args, plain_args, key, keyed_store, tmp_path, monkeypatch):
    # #466: the loader refuses a policy whose pattern does not compile, naming the file and the entry, and the hook
    # decides on the defaults. The floors stand without the policy, so a store write, a secret and the force push
    # still ask the principal alone, once, in every mode -- on a real rule key now, where #464's error card carried
    # no approver list and its "always" let a seat answer every later call the gate failed on.
    store, key_path = keyed_store
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (store / "policies" / "gates.yaml").write_text(f"deny:\n  - category: {cat}\n    command: '^(rm|shred'\n")
    for extra in ({}, {"mode": "observe"}):
        g, _ = make_gate(store, key_path, decision_model=False, **extra)
        r = g.pre_tool_call(tool, args)
        assert r["action"] == "approve" and r["rule_key"].startswith(f"exo-gate:{key}:"), r
        assert (r["approvers"], r["once"]) == (["principal"], True), r
        assert "policy error, defaults used" in r["message"], r
        plain = g.pre_tool_call(tool, plain_args)
        if extra:
            assert plain is None                      # observe mode, as before
        elif cat == "force_push":
            assert plain["action"] == "approve" and plain["rule_key"].startswith("exo-gate:force_push:"), plain
        else:
            assert plain is None                      # the defaults allow a plain read and a local write


def test_a_policy_pattern_that_does_not_compile_refuses_the_policy_and_the_store_write_still_asks_once(
        keyed_store, tmp_path, monkeypatch):
    # #466, on #439 review's rows: a gh download into the store, which the line as read before #399 (its floor)
    # reads as a store write. The refused policy's defaults still ask the principal alone, once, in every mode, on
    # a real store_write ask -- where the plain error card ran in observe mode.
    store, key = keyed_store
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (store / "policies" / "gates.yaml").write_text("deny:\n  - 'command:^(rm|shred'\n")
    lines = ("gh -R pub/x release download v1 -O ~/.exo/update/allowed_signers --clobber",
             "gh --repo pub/x release download v1 -D ~/.exo/state",
             "gh -R pub/x run download 5 -D ~/.exo/policies")
    for extra in ({}, {"mode": "observe"}):
        g, _ = make_gate(store, key, decision_model=False, **extra)
        for line in lines:
            r = g.pre_tool_call("terminal", {"command": line})
            assert r["rule_key"].startswith("exo-gate:store_write:"), (line, extra, r)
            assert (r.get("approvers"), r.get("once")) == (["principal"], True), (line, extra, r)
            assert "policy error, defaults used" in r["message"], (line, extra, r)


# --- settings precedence under Exo's plugin loader (issue #19) -----------------------------

def _loaded_settings(config=None):
    from exo.core.plugins import PluginManager
    pm = PluginManager(config={"exo-gate": config} if config else {})
    pm.load_all([PLUGIN.parent], enabled=["exo-gate"], entry_points=False)
    rec = pm.plugins["exo-gate"]
    assert not rec.error, rec.error
    [entry] = pm.hooks._hooks["pre_tool_call"]
    return entry.callback.__self__.settings


def test_env_beats_manifest_default_but_not_explicit_config(monkeypatch):
    monkeypatch.setenv("EXO_GATE_MODE", "observe")
    monkeypatch.setenv("EXO_GATE_DECISION_MODEL", "0")
    s = _loaded_settings()
    assert s.mode == "observe" and s.decision_model is False         # env over the manifest default
    s = _loaded_settings({"mode": "enforce", "decision_model": True})
    assert s.mode == "enforce" and s.decision_model is True          # explicit config over env
    monkeypatch.delenv("EXO_GATE_MODE")
    monkeypatch.delenv("EXO_GATE_DECISION_MODEL")
    s = _loaded_settings()
    assert s.mode == "enforce" and s.decision_model is True          # manifest default


def test_mode_is_read_once_at_load(keyed_store, monkeypatch):
    """A tool subprocess cannot reach exod's environment; even a change in this process after load is ignored."""
    store, key = keyed_store
    g, ctx = make_gate(store, key)
    monkeypatch.setenv("EXO_GATE_MODE", "observe")
    assert g.settings.mode == "enforce"
    assert call(ctx, "terminal", {"command": "rm -rf build"})["action"] == "approve"


def test_spend_denial_never_falls_back_to_allow(keyed_store, monkeypatch):
    from exo.model_spend import SpendDenied
    from exo.core.models import decision
    async def denied(*args, **kwargs):
        raise SpendDenied('principal model-spend cap reached')
    monkeypatch.setattr(decision, 'ask_choice_result', denied)
    store, key = keyed_store
    calls = []
    ctx = gated(store, key, decision_router('allow', calls))
    result = call(ctx, 'read_file', {'path': 'x'})
    assert result['action'] == 'block' and 'spend' in result['message'] and not calls


def test_relative_paths_are_read_from_the_session_folder(keyed_store, tmp_path, monkeypatch):
    """#209: the core passes the session's folder as ``cwd``; a harmless name there that links into ~/.ssh asks."""
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    home, work = tmp_path / "h", tmp_path / "w"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("fake\n")
    work.mkdir()
    (work / "notes.txt").symlink_to(home / ".ssh" / "id_rsa")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)                       # the gate's own folder holds no such link
    assert call(ctx, "read_file", {"path": "notes.txt"}) is None
    for tool, args in (("read_file", {"path": "notes.txt"}), ("terminal", {"command": "cat notes.txt"})):
        r = call(ctx, tool, args, cwd=str(work))
        assert r["action"] == "approve" and "notes.txt" in r["message"]
