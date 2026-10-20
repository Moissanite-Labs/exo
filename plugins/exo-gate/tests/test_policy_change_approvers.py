"""A changed approvals.yaml narrows who may answer and how long "always" lasts at once (#211, D-038).

The plugin pins the policy it loaded first and decides each call under the pinned and the changed policy, the
stricter outcome winning. The approvers sent with an ask and the length of a standing grant read only the pinned
approvals.yaml, so a seat the principal had just removed could still answer, and a grant outlived a 0. Temp
stores and a mock context; nothing is executed.
"""
import asyncio
import json
import os

import pytest

from exo import crypto, gate, principal
from exo_gate import hook

WIDE = """
approvers:
  github_post: [principal, "seat:saga-divan", "seat:builder"]
always:
  days: 30
  outward_days: 7
"""
NARROW = """
approvers:
  github_post: [principal]
always:
  days: 0
  outward_days: 0
"""


class MockCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "gates.yaml").write_text("matrix: {}\n")
    (root / "policies" / "approvals.yaml").write_text(WIDE)
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    return root, key


def make_gate(store):
    root, key = store
    ctx = MockCtx({"principal_store": str(root), "key_path": str(key), "decision_model": False})
    g = hook.register(ctx)
    g.policies()                     # pin the policy now, as the first call of a session would
    return g, ctx


def post(ctx, text="Suggest a fix."):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name="terminal", args={"command": f"gh pr comment 5 -R other/app --body '{text}'"},
                          session_id="s1", tool_call_id="c1", turn_id="t1"))


def answer(ctx, verdict, choice, by="principal:tui"):
    [cb] = ctx.hooks["post_approval_response"]
    cb(pattern_key="plugin_rule:" + verdict["rule_key"], approval_key=verdict["approval_key"], choice=choice,
       decided_by=by, session_id="s1")


def rewrite(root, text):
    p = root / "policies" / "approvals.yaml"
    p.write_text(text)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def grant_days(root):
    p = root / "state" / "approval-grants.json"
    rows = json.loads(p.read_text()) if p.exists() else []
    return [round((g["expires"] - g["created"]) / 86400, 3) for g in rows]


# --- strictest_approvers ----------------------------------------------------------------------

def table(text):
    return gate.normalize_approvals(gate.parse_yaml(text))


def test_strictest_approvers_is_the_ordered_intersection():
    wide, narrow = table(WIDE), table(NARROW)
    assert gate.strictest_approvers("github_post", wide) == ["principal", "seat:saga-divan", "seat:builder"]
    assert gate.strictest_approvers("github_post", wide, None) == gate.approvers_for("github_post", wide)
    assert gate.strictest_approvers("github_post", wide, narrow) == ["principal"]
    assert gate.strictest_approvers("github_post", narrow, wide) == ["principal"]     # never wider than pinned
    other = table('approvers:\n  github_post: ["seat:builder", principal]\n')
    assert gate.strictest_approvers("github_post", wide, other) == ["principal", "seat:builder"]


def test_strictest_approvers_expands_roles_and_keeps_the_principal():
    roles = table('approvers:\n  github_post: ["role:r"]\nroles:\n  r: ["seat:builder"]\n')
    assert gate.strictest_approvers("github_post", table(WIDE), roles) == ["principal", "seat:builder"]
    assert gate.strictest_approvers("github_post", table(WIDE), {}) == ["principal"]   # defaults: principal only
    assert gate.strictest_approvers("store_write", table(WIDE), table(WIDE)) == ["principal"]


# --- the hook -----------------------------------------------------------------------------------

def test_a_tightened_approvals_file_narrows_the_next_ask_at_once(store):
    root, _ = store
    _, ctx = make_gate(store)
    assert post(ctx)["approvers"] == ["principal", "seat:saga-divan", "seat:builder"]
    rewrite(root, NARROW)
    r = post(ctx)
    assert r["approvers"] == ["principal"], r
    assert not gate.approver_allowed("seat:saga-divan", r["approvers"])
    assert gate.approver_allowed("principal:tui", r["approvers"])


def test_a_wider_approvals_file_waits_for_gate_accept(store):
    root, _ = store
    rewrite(root, 'approvers:\n  github_post: [principal, "seat:saga-divan"]\n')
    g, ctx = make_gate(store)                                   # pinned: principal and saga-divan
    rewrite(root, WIDE)                                         # adds builder
    assert post(ctx)["approvers"] == ["principal", "seat:saga-divan"]
    token = principal.CALLER.set(principal.Caller("principal:tui"))
    try:
        assert hook.gate_command(g, "accept").startswith("accepted")
    finally:
        principal.CALLER.reset(token)
    assert post(ctx)["approvers"] == ["principal", "seat:saga-divan", "seat:builder"]


def test_an_unreadable_changed_file_leaves_only_the_principal(store):
    root, _ = store
    _, ctx = make_gate(store)
    rewrite(root, "approvers:\n  github_post: [anyone]\n")     # a PolicyError: the defaults stand in for it
    assert post(ctx)["approvers"] == ["principal"]


def test_always_days_zero_in_the_changed_file_writes_no_grant(store):
    root, _ = store
    _, ctx = make_gate(store)
    rewrite(root, NARROW)
    r = post(ctx)
    answer(ctx, r, "always")
    assert grant_days(root) == []
    assert post(ctx)["action"] == "approve"


def test_a_grant_lasts_the_shorter_of_pinned_and_changed(store):
    root, _ = store
    _, ctx = make_gate(store)
    rewrite(root, WIDE.replace("outward_days: 7", "outward_days: 2"))
    answer(ctx, post(ctx), "always")
    assert grant_days(root) == [2.0]
    rewrite(root, WIDE.replace("outward_days: 7", "outward_days: 30"))   # longer: waits for /gate accept
    answer(ctx, post(ctx, "Other text."), "always")
    assert grant_days(root) == [2.0, 7.0]


def test_unchanged_policy_keeps_the_pinned_approvers_and_days(store):
    root, _ = store
    _, ctx = make_gate(store)
    r = post(ctx)
    assert r["approvers"] == ["principal", "seat:saga-divan", "seat:builder"]
    answer(ctx, r, "always")
    assert grant_days(root) == [7.0]
