"""#447, D-038: a store write anywhere on a line keeps the answer with the principal, whatever category leads it.

`_combine` ranks credentials above store_write, so `env; echo x > ~/.exo/policies/gates.yaml` is credentials. The
hook read who may answer from that one category, so a seat that `approvals.yaml` delegates credentials to could
approve a write to the policy files. The outcome stayed ask, so only the approvers show it. The hook now reads every
part of the line (`gate.call_approvers`, `gate.no_grant`): principal only, never a standing grant, in every mode.
A line with no store write keeps its delegation. Temp stores and a mock context; nothing is executed.
"""
import asyncio
import json
import os

import pytest

from exo import crypto, gate
from exo_gate import hook

SEAT = "seat:builder"
# Three ways to delegate credentials to a seat: a category line, a role, and the default with no credentials line.
DELEGATIONS = {
    "category": 'approvers:\n  credentials: [principal, "seat:builder"]\n',
    "role": 'approvers:\n  credentials: ["role:ops"]\nroles:\n  ops: ["seat:builder"]\n',
    "default": 'approvers:\n  default: [principal, "seat:builder"]\n',
}

# The issue's rows and more of the same shape: a secret read beside a store write. The first is store_write and was
# principal-only already; the others are credentials, and the seat could answer them.
STORE_LINES = [
    "echo x > ~/.exo/policies/gates.yaml",
    "env; echo x > ~/.exo/policies/gates.yaml",
    "cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml",
    "env | tee ~/.exo/policies/approvals.yaml",
    "cat ~/.ssh/id_rsa > ~/.exo/policies/gates.yaml",
    "printenv && rm -rf ~/.exo/state",
]
# The issue's rows behind wrappers, in compounds, subshells, $( ), heredocs and cd chains, and with other spellings.
WRAPPED_LINES = [
    "sudo sh -c 'env; echo x > ~/.exo/policies/gates.yaml'",
    "sudo env; sudo cp evil.yaml ~/.exo/policies/default.yaml",
    "doas sh -c 'cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml'",
    "env FOO=1 bash -c 'env; echo x > ~/.exo/policies/gates.yaml'",
    "timeout 5 bash -c 'cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml'",
    "nice -n 5 sh -c 'env; echo x > ~/.exo/policies/gates.yaml'",
    "command env; command cp evil.yaml ~/.exo/policies/default.yaml",
    "builtin echo x > ~/.exo/policies/gates.yaml; env",
    "bash -c 'env; echo x > ~/.exo/policies/gates.yaml'",
    'sh -c "cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml"',
    "eval 'env; echo x > ~/.exo/policies/gates.yaml'",
    "echo go | xargs -I{} sh -c 'env; echo x > ~/.exo/policies/gates.yaml'",
    "cat ~/.kube/config | xargs -I{} cp {} ~/.exo/policies/default.yaml",
    "env && echo x > ~/.exo/policies/gates.yaml",
    "env || echo x > ~/.exo/policies/gates.yaml",
    "cp evil.yaml ~/.exo/policies/default.yaml; cat ~/.kube/config",
    "(env; echo x > ~/.exo/policies/gates.yaml)",
    "{ cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml; }",
    "echo \"$(env)\" > ~/.exo/policies/gates.yaml",
    "cp $(cat ~/.kube/config) ~/.exo/policies/default.yaml",
    "bash <<'EOF'\nenv\necho x > ~/.exo/policies/gates.yaml\nEOF",
    "cat > ~/.exo/policies/gates.yaml <<EOF\n$(cat ~/.kube/config)\nEOF",
    "cd $EXO_HOME/policies && env; echo x > gates.yaml",
    "cd /tmp; cd $EXO_HOME/policies; cat ~/.kube/config > default.yaml",
    "cd ~/.exo && env; echo x > policies/gates.yaml",
    'env; echo x > "$HOME/.exo/policies/gates.yaml"',
    "env; echo x > ~/'.exo'/policies/gates.yaml",
    "env; echo x > ~/.exo/polic{i,i}es/gates.yaml",
    "env; echo x > ~/.exo/update/allowed_signers",
]
# Lines with no store write keep the delegation (credentials) or the default (principal only).
SECRET_LINES = ["env", "cat ~/.kube/config", "printenv | grep TOKEN", "sudo env", "bash -c 'cat ~/.ssh/id_rsa'"]
# #169: a write that touches keys/ or the run folder is credentials, and a seat delegated credentials could answer it.
# Its store write part keeps it with the principal now. A read of a secret there alone keeps the delegation.
KEYS_AND_RUN_WRITES = ["rm -rf ~/.exo/keys", "rm -rf ~/.exo/run", "mkdir -p ~/.exo/run", "ln -sfn /tmp/x ~/.exo/run",
                       "tar czf - ~/.exo/keys", "cp ~/.exo/keys/seat.key /tmp/k"]
KEYS_AND_RUN_READS = ["cat ~/.exo/keys/seat.key", "cat ~/.exo/run/exod.token"]


class MockCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


def make_store(tmp_path, approvals):
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "approvals.yaml").write_text(approvals)
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    return root, key


def make_gate(tmp_path, approvals=DELEGATIONS["category"], mode="enforce"):
    root, key = make_store(tmp_path, approvals)
    ctx = MockCtx({"principal_store": str(root), "key_path": str(key), "decision_model": False, "mode": mode})
    g = hook.register(ctx)
    g.policies()
    return g, ctx, root


def run(ctx, line):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name="terminal", args={"command": line}, session_id="s1", tool_call_id="c1",
                          turn_id="t1"))


def answer(ctx, verdict, choice, by="principal:tui"):
    [cb] = ctx.hooks["post_approval_response"]
    cb(pattern_key="plugin_rule:" + verdict["rule_key"], approval_key=verdict["approval_key"], choice=choice,
       decided_by=by, session_id="s1")


def classify(line):
    return gate.classify({"tool": "terminal", "args": {"command": line}})


@pytest.mark.parametrize("line", STORE_LINES + WRAPPED_LINES)
def test_a_store_write_anywhere_on_the_line_is_the_principals(line):
    c = classify(line)
    assert c.category in ("store_write", "credentials"), (line, c.category)
    assert any(p.category == "store_write" for p in gate._each_part(c)), line
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", SEAT]}})
    assert gate.call_approvers(c, approvals) == ["principal"], (line, c.category)
    assert gate.no_grant(c) and gate.binds_exact(c)


@pytest.mark.parametrize("line", SECRET_LINES + ["ls -la", "git push origin main"])
def test_a_line_with_no_store_write_keeps_its_own_approvers(line):
    c = classify(line)
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", SEAT]}})
    assert gate.call_approvers(c, approvals) == gate.strictest_approvers(c.category, approvals) == ["principal", SEAT]
    assert gate.no_grant(c) == (c.category in gate.NO_GRANT)


def test_the_issue_rows_stay_credentials():
    # The fix reads every part; it does not move the line's category (that is #437).
    assert classify("env; echo x > ~/.exo/policies/gates.yaml").category == "credentials"
    assert classify("cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml").category == "credentials"


def test_every_part_counts_whatever_category_leads():
    # No classification puts a store write or a secret under another category today; a part added by hand stands in.
    approvals = gate.normalize_approvals({"approvers": {"vcs_push": ["seat:a", SEAT], "credentials": [SEAT]}})
    push = classify("git push origin main")
    assert gate.call_approvers(push, approvals) == ["principal", "seat:a", SEAT] and not gate.no_grant(push)
    push.parts = [*push.parts, classify("env")]
    assert gate.call_approvers(push, approvals) == ["principal", SEAT] and gate.no_grant(push)
    assert gate.binds_exact(push)
    push.parts = [*push.parts, classify("echo x > ~/.exo/policies/gates.yaml")]
    assert gate.call_approvers(push, approvals) == ["principal"]


@pytest.mark.parametrize("how", sorted(DELEGATIONS))
@pytest.mark.parametrize("line", STORE_LINES)
def test_a_seat_delegated_credentials_may_not_answer_a_store_write(tmp_path, how, line):
    _, ctx, _ = make_gate(tmp_path, DELEGATIONS[how])
    r = run(ctx, line)
    assert r["action"] == "approve" and r["approvers"] == ["principal"], (how, line, r)
    assert not gate.approver_allowed(SEAT, r["approvers"])
    assert gate.approver_allowed("principal:tui", r["approvers"])
    assert r.get("once") is True and r.get("binding")


@pytest.mark.parametrize("line", WRAPPED_LINES)
def test_wrappers_and_compounds_keep_the_store_write_with_the_principal(tmp_path, line):
    _, ctx, _ = make_gate(tmp_path)
    r = run(ctx, line)
    assert r["action"] == "approve" and r["approvers"] == ["principal"], (line, r)
    assert r.get("once") is True and r.get("binding")


@pytest.mark.parametrize("how", sorted(DELEGATIONS))
@pytest.mark.parametrize("line", SECRET_LINES)
def test_a_secret_read_alone_keeps_the_delegation(tmp_path, how, line):
    _, ctx, _ = make_gate(tmp_path, DELEGATIONS[how])
    r = run(ctx, line)
    assert classify(line).category == "credentials"
    assert r["action"] == "approve" and r["approvers"] == ["principal", SEAT], (how, line, r)
    assert gate.approver_allowed(SEAT, r["approvers"])
    assert r.get("once") is True


def test_with_no_delegation_both_stay_with_the_principal(tmp_path):
    _, ctx, _ = make_gate(tmp_path, "approvers: {}\n")
    for line in STORE_LINES + SECRET_LINES:
        assert run(ctx, line)["approvers"] == ["principal"], line


def test_writes_to_keys_or_the_run_folder_stay_with_the_principal(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", os.path.expanduser("~/.exo"))
    _, ctx, _ = make_gate(tmp_path)
    for line in KEYS_AND_RUN_WRITES:
        assert classify(line).category == "credentials", line
        assert run(ctx, line)["approvers"] == ["principal"], line
    for line in KEYS_AND_RUN_READS:
        assert classify(line).category == "credentials", line
        assert run(ctx, line)["approvers"] == ["principal", SEAT], line


@pytest.mark.parametrize("line", STORE_LINES + WRAPPED_LINES[:3])
def test_observe_mode_still_asks_the_principal(tmp_path, line):
    _, ctx, _ = make_gate(tmp_path, mode="observe")
    r = run(ctx, line)
    assert r is not None and r["action"] == "approve" and r["approvers"] == ["principal"], (line, r)
    assert run(ctx, "git push origin main") is None          # observe mode: an ordinary ask only logs


@pytest.mark.parametrize("line", STORE_LINES)
def test_always_from_the_principal_keeps_no_grant(tmp_path, line):
    g, ctx, root = make_gate(tmp_path)
    r = run(ctx, line)
    answer(ctx, r, "always")
    grants = root / "state" / "approval-grants.json"
    assert not grants.exists() or json.loads(grants.read_text()) == []
    assert g.grants.list() == []
    assert run(ctx, line)["action"] == "approve"              # asked again, every time


def test_a_wider_approvals_file_mid_session_does_not_reach_the_store_write(tmp_path):
    _, ctx, root = make_gate(tmp_path, "approvers: {}\n")
    p = root / "policies" / "approvals.yaml"
    p.write_text(DELEGATIONS["category"])
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert run(ctx, "env; echo x > ~/.exo/policies/gates.yaml")["approvers"] == ["principal"]
    assert run(ctx, "env")["approvers"] == ["principal"]      # wider waits for /gate accept (#211)


def test_a_narrower_approvals_file_mid_session_narrows_a_secret_read(tmp_path):
    _, ctx, root = make_gate(tmp_path)
    p = root / "policies" / "approvals.yaml"
    p.write_text("approvers: {}\n")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert run(ctx, "env")["approvers"] == ["principal"]      # narrower applies at once (#211)
    assert run(ctx, "env; echo x > ~/.exo/policies/gates.yaml")["approvers"] == ["principal"]
