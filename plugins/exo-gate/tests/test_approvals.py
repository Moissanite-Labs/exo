"""D-033: approvals bound to the exact text, who may approve, standing grants, outbound scrub."""
import asyncio
import dataclasses
import json
import os

import pytest

from exo import crypto, gate
from exo.ledger import Ledger
from exo_gate import hook

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


def make_gate(store, key, clock=None):
    ctx = MockCtx({"principal_store": str(store), "key_path": str(key), "decision_model": False})
    g = hook.register(ctx)
    if clock is not None:
        g.grants.clock = clock
    return g, ctx


def call(ctx, tool, args):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1"))


def answer(ctx, verdict, choice, by="principal:tui"):
    [cb] = ctx.hooks["post_approval_response"]
    cb(pattern_key="plugin_rule:" + verdict["rule_key"], choice=choice, decided_by=by)


def events(store):
    with Ledger(store / "ledger.sqlite") as led:
        return [e["body"] for e in led.iter(kind="action")]


def post(text):
    return ("terminal", {"command": f"gh pr comment 12 -R other/repo --body '{text}\n{FOOT}'"})


# --- binding --------------------------------------------------------------------------------

def test_binding_changes_with_text_and_target():
    a = gate.classify(("send_message", {"target": "#x", "message": "hello"}))
    b = gate.classify(("send_message", {"target": "#x", "message": "hello!"}))
    c = gate.classify(("send_message", {"target": "#y", "message": "hello"}))
    ha = gate.approval_binding(a, {"target": "#x", "message": "hello"})
    assert ha == gate.approval_binding(a, {"target": "#x", "message": "hello"})
    assert ha != gate.approval_binding(b, {"target": "#x", "message": "hello!"})
    assert ha != gate.approval_binding(c, {"target": "#y", "message": "hello"})
    assert gate.is_outward(a) and not gate.is_outward(gate.classify(("write_file", {"path": "/tmp/x"})))


def test_ask_carries_binding_and_default_approver(keyed_store):
    _, ctx = make_gate(*keyed_store)
    r = call(ctx, *post("Suggest a fix."))
    assert r["action"] == "approve" and len(r["binding"]) == 64 and r["persist"] is True
    assert r["approvers"] == ["principal"]
    assert r["binding"][:16] in r["rule_key"]


def test_post_edited_after_approval_is_held_again(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    first = call(ctx, *post("Suggest a fix."))
    answer(ctx, first, "always")
    assert call(ctx, *post("Suggest a fix.")) is None          # the exact approved text runs
    again = call(ctx, *post("Suggest a fix, now."))           # one edit: a new ask
    assert again["action"] == "approve" and again["binding"] != first["binding"]
    once = call(ctx, *post("Other text."))
    answer(ctx, once, "once")
    assert call(ctx, *post("Other text."))["action"] == "approve"   # once is once


@pytest.mark.parametrize("failure", ["audit", "persistence"])
def test_required_grant_failure_leaves_no_authority_after_restart(keyed_store, monkeypatch, failure):
    store, key = keyed_store
    g, ctx = make_gate(store, key)
    g.required_getter = lambda: True
    verdict = call(ctx, *post("Suggest a fix."))
    if failure == "audit":
        append = g.sink.append
        def fail_grant(body, **kw):
            return None if body.get("phase") == "grant" else append(body, **kw)
        monkeypatch.setattr(g.sink, "append", fail_grant)
    else:
        def fail_write(rows):
            raise OSError("grant persistence unavailable")
        monkeypatch.setattr(g.grants, "_write", fail_write)
    with pytest.raises(hook.GateAuditError):
        g.post_approval_response(pattern_key="plugin_rule:" + verdict["rule_key"],
                                 approval_key=verdict["approval_key"], choice="always", decided_by="principal:tui")
    assert g.grants.list() == []
    restarted = hook.GrantStore(g.grants.path)
    assert restarted.list() == []
    assert call(ctx, *post("Suggest a fix."))["action"] == "approve"
    bodies = events(store)
    assert any(body["phase"] == "approval" and body["approved"] for body in bodies)
    grants = [body for body in bodies if body["phase"] == "grant"]
    if failure == "audit":
        assert grants == []
    else:
        assert len(grants) == 1 and grants[0]["state"] == "authorized" and grants[0]["activation"] == "pending"


def test_required_audited_grant_keeps_scope_and_exact_binding(keyed_store):
    g, ctx = make_gate(*keyed_store)
    g.required_getter = lambda: True
    verdict = call(ctx, *post("Suggest a fix."))
    g.post_approval_response(pattern_key="plugin_rule:" + verdict["rule_key"], approval_key=verdict["approval_key"],
                             choice="always", decided_by="principal:tui")
    assert len(g.grants.list()) == 1
    assert call(ctx, *post("Suggest a fix.")) is None
    assert call(ctx, *post("Changed text."))["action"] == "approve"


def test_required_approval_needs_exact_single_call_token(keyed_store):
    g, ctx = make_gate(*keyed_store)
    g.required_getter = lambda: True
    first = call(ctx, *post("Suggest a fix."))
    second = call(ctx, *post("Suggest a fix."))
    assert first["rule_key"] == second["rule_key"] and first["approval_key"] != second["approval_key"]
    with pytest.raises(hook.GateAuditError):
        answer(ctx, first, "once")
    assert len(g._pending) == 2
    for verdict in (second, first):
        g.post_approval_response(pattern_key="plugin_rule:" + verdict["rule_key"],
                                 approval_key=verdict["approval_key"], choice="deny", decided_by="principal:tui")
    assert not g._pending


# --- who may approve ------------------------------------------------------------------------

APPROVALS = """
approvers:
  default: [principal]
  categories:
    github_post: ["role:public-reviewer"]
    social_post: [principal, "seat:saga-divan"]
roles:
  public-reviewer: ["seat:saga-divan"]
always:
  days: 10
  outward_days: 2
"""


def test_approver_table_expands_roles_and_keeps_principal():
    a = gate.normalize_approvals(gate.parse_yaml(APPROVALS))
    assert gate.approvers_for("github_post", a) == ["principal", "seat:saga-divan"]
    assert gate.approvers_for("social_post", a) == ["principal", "seat:saga-divan"]
    assert gate.approvers_for("email_send", a) == ["principal"]
    assert gate.approvers_for("email_send", None) == ["principal"]
    assert a["always_days"] == 10 and a["outward_always_days"] == 2


@pytest.mark.parametrize("bad", ["approvers: {default: [anyone]}", "approvers: {github_post: ['role:nobody']}",
                                 "roles: {r: ['role:x']}"])
def test_bad_approver_table_is_an_error(bad):
    with pytest.raises(gate.PolicyError):
        gate.normalize_approvals(gate.parse_yaml(bad))


def test_identity_and_allowed():
    allowed = ["principal", "seat:saga-divan"]
    assert gate.approver_identity("principal:tui") == "principal" and gate.approver_identity("principal") == "principal"
    # D-039: an empty or made-up client is nobody; the core stamps the principal from the connection
    assert gate.approver_identity("") == "unknown" and gate.approver_identity("tui") == "unknown"
    assert not gate.approver_allowed("", []) and not gate.approver_allowed("tui", ["principal"])
    assert gate.approver_identity("seat:Saga-Divan") == "seat:saga-divan"
    assert gate.approver_identity("peer:x") == "peer"
    assert gate.approver_allowed("seat:saga-divan", allowed)
    assert not gate.approver_allowed("seat:builder", allowed)
    assert not gate.approver_allowed("seat:builder", ["principal"])
    assert not gate.approver_allowed("peer:saga-divan", allowed)
    assert gate.approver_allowed("principal:web", [])  # no table: the principal or a seat


def test_store_approvals_reach_the_ask(keyed_store):
    store, key = keyed_store
    (store / "policies" / "approvals.yaml").write_text(APPROVALS)
    _, ctx = make_gate(store, key)
    r = call(ctx, *post("Suggest a fix."))
    assert r["approvers"] == ["principal", "seat:saga-divan"]
    assert events(store)[-1]["approvers"] == ["principal", "seat:saga-divan"]


# #399: a line whose gh subcommand moved past an option, or stands behind an expansion, is also read as before (its
# floor). Who may answer is who may answer both readings, so a seat never answers a line only the principal answered.
SEATS = """
approvers:
  default: [principal]
  categories:
    github_post: [principal, "seat:saga-divan"]
    opaque_exec: [principal, "seat:saga-divan"]
"""
ASK_ALL = "categories: {read: ask, github_post: ask, vcs_push: ask, opaque_exec: ask}\n"


@pytest.mark.parametrize("line", ["gh pr -b x comment 5 -R myorg/app", 'gh pr "$V" 5 -R myorg/app',
                                  "git push origin f && gh issue -R myorg/app edit 5 -b x"])
def test_who_may_answer_a_line_read_two_ways_may_answer_both_readings(tmp_path, line):
    store = tmp_path / "st9"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(ASK_ALL)
    (store / "policies" / "approvals.yaml").write_text(SEATS)
    key = tmp_path / "k9.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    c = gate.classify(("terminal", {"command": line}))
    assert c.floor is not None and c.category in ("github_post", "opaque_exec"), c
    assert gate.approvers_for(c.floor.category, gate.normalize_approvals(gate.parse_yaml(SEATS))) == ["principal"]
    r = call(ctx, "terminal", {"command": line})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r
    assert events(store)[-1]["approvers"] == ["principal"]
    # The line read one way keeps its table.
    r = call(ctx, "terminal", {"command": "gh pr comment 5 -R myorg/app -b x"})
    assert r["approvers"] == ["principal", "seat:saga-divan"], r


# #143: a line read two ways (bash's reading of a ${..} word and the reading before, at the end of its `also` chain)
# binds an approval to its exact text, as a line with a floor does: a grant keyed on its first reading (read) must
# not cover a later line whose other reading is a push. The lines are data for the gate; none is run.
TWO_WAYS_FIRST = "echo \"${x:-'}\" ; rm -rf ./work ; echo \"'}\""
TWO_WAYS_LATER = "x=$(echo ${x:-<(} )\ngit push --force origin main\n})"


def _read_grant_on_any_target(g):
    """A standing read grant whose target glob is `*` (GrantStore.add escapes the target it is given)."""
    g.grants.persist({**g.grants.prepare("read", "", "", 30, "principal:tui"), "target": "*"})


def test_an_always_on_a_line_read_two_ways_covers_no_other_line(tmp_path):
    store = tmp_path / "st143"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("version: 1\n")
    key = tmp_path / "k143.key"
    crypto.save_key(crypto.keygen(), key)
    g, ctx = make_gate(store, key)
    for line, other in ((TWO_WAYS_FIRST, "delete"), (TWO_WAYS_LATER, "force_push")):
        c = gate.classify(("terminal", {"command": line}))
        assert [r.category for r in gate.readings(c)] == ["read", other] and gate.binds_exact(c), line
    r = call(ctx, "terminal", {"command": TWO_WAYS_FIRST})
    assert r["action"] == "approve" and r["binding"][:16] in r["rule_key"], r
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": TWO_WAYS_FIRST}) is None          # the exact approved line runs
    assert call(ctx, "terminal", {"command": TWO_WAYS_LATER})["action"] == "approve"
    _read_grant_on_any_target(g)
    assert call(ctx, "terminal", {"command": TWO_WAYS_LATER})["action"] == "approve"


# Its other reading (the one before #143) runs a value, which is neither outward nor irreversible: only binds_exact
# reading every reading keeps a grant keyed on the first reading (read) from covering it.
TWO_WAYS_EVAL = "echo \"${x:-'}\" ; eval \"$y\" ; echo \"'}\""


def test_a_line_whose_other_reading_runs_a_value_binds_to_its_text(tmp_path):
    store = tmp_path / "st143e"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("version: 1\n")
    key = tmp_path / "k143e.key"
    crypto.save_key(crypto.keygen(), key)
    g, ctx = make_gate(store, key)
    c = gate.classify(("terminal", {"command": TWO_WAYS_EVAL}))
    assert [r.category for r in gate.readings(c)] == ["read", "opaque_exec"]
    assert not gate.is_outward(c) and gate.binds_exact(c)
    _read_grant_on_any_target(g)
    assert g.grants.match("read", "terminal", "", False) is not None               # the grant covers read lines
    r = call(ctx, "terminal", {"command": TWO_WAYS_EVAL})
    assert r is not None and r["action"] == "approve", r


# Merge of main f28934a3 (review 5678114763): a ${..} word in an `env -S` text (#591's split readings) or in the stdin
# of a sudo -s or -i shell (#491). The store write in the word is read in each reading, so only the principal
# answers the card and a standing read grant does not let the line run. Main f28934a3 reads none of the word: the
# line is an opaque_exec ask, which the seat's table lets it answer.
@pytest.mark.parametrize("line", [
    "env -S 'echo ${{v:-$(cp /var/tmp/x {store}/policies/gates.yaml)}}'",
    "env --split='echo ${{v:-`cp /var/tmp/x {store}/policies/gates.yaml`}}'",
    "sudo -s <<< 'echo ${{v:-$(cp /var/tmp/x {store}/policies/gates.yaml)}}'",
    "sudo -i <<'END'\necho ${{x:-'}}'}} ; cp /var/tmp/x {store}/policies/gates.yaml\nEND",
])
def test_a_store_write_in_a_word_in_env_s_or_sudo_stdin_is_answered_by_the_principal_only(tmp_path, line):
    store = tmp_path / "st158"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(ASK_ALL)
    (store / "policies" / "approvals.yaml").write_text(SEATS)
    key = tmp_path / "k158.key"
    crypto.save_key(crypto.keygen(), key)
    g, ctx = make_gate(store, key)
    line = line.format(store=store)
    c = gate.classify(("terminal", {"command": line}))
    assert gate.no_grant(c) and gate.binds_exact(c)
    assert "store_write" in {p.category for r in gate.readings(c) for p in [r, *gate._each_part(r)]}
    r = call(ctx, "terminal", {"command": line})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r
    _read_grant_on_any_target(g)
    assert call(ctx, "terminal", {"command": line})["action"] == "approve"


@pytest.fixture
def floor_is(monkeypatch):
    """Give a line a floor read from another line: the hook's checks past `decide` must read it as well."""
    real = gate.classify

    def set_floor(line, before):
        def classify(action, *a, **k):
            c = real(action, *a, **k)
            args = action[1] if isinstance(action, tuple) else action.get("args") or {}
            if args.get("command") == line:
                c.floor = real({"tool": "terminal", "args": {"command": before}})
            return c
        monkeypatch.setattr(gate, "classify", classify)
    return set_floor


def test_a_line_read_before_as_credentials_is_answered_once_and_kept_in_observe_mode(tmp_path, floor_is):
    store = tmp_path / "st10"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(ASK_ALL)
    key = tmp_path / "k10.key"
    crypto.save_key(crypto.keygen(), key)
    line = "gh pr view 5 -R myorg/app"
    floor_is(line, "gh secret set X -b v")
    g, ctx = make_gate(store, key)
    c = gate.classify(("terminal", {"command": line}))
    assert c.category == "read" and c.floor.category == "credentials", c
    binding = gate.approval_binding(c, {"command": line})
    g.grants.add("read", "myorg/app#5", binding, 30, "principal:tui")     # a grant on this exact line runs no part
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve" and r.get("once") is True, r         # no grant runs it, no "always"
    answer(ctx, r, "always")
    assert [x["rule"] for x in g.grants.list()] == ["read"]                   # no new grant was kept
    # The grant lookup reads the floor too, also where "once" would not stop it first.
    d = gate.decide(c, g.policies())
    assert g.grants.match("read", "myorg/app#5", binding, True) and g._grant_for(c, d, binding, True) is None
    g.settings = dataclasses.replace(g.settings, mode="observe")
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve", r                                  # NO_GRANT holds in observe mode too


def test_a_line_read_before_with_an_unseen_body_is_answered_once(tmp_path, floor_is):
    store = tmp_path / "st11"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(ASK_ALL)
    key = tmp_path / "k11.key"
    crypto.save_key(crypto.keygen(), key)
    line = "gh pr comment 5 -R myorg/app -b x"
    floor_is(line, 'gh pr comment 5 -R myorg/app -b "$X"')
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve" and r.get("once") is True, r


# #471: a line with a quoted prefix assignment is also read as before, each such word the program (``before``), and
# that reading may have a floor of its own (#399). The hook's checks past `decide` read both, as for the floor.
@pytest.fixture
def before_is(monkeypatch):
    """Give a line a reading as before #471 read from another line, with that line's floor."""
    real = gate.classify

    def set_before(line, before, **more):
        def classify(action, *a, **k):
            c = real(action, *a, **k)
            args = action[1] if isinstance(action, tuple) else action.get("args") or {}
            if args.get("command") == line:
                c.before = real({"tool": "terminal", "args": {"command": before, **more}})
            return c
        monkeypatch.setattr(gate, "classify", classify)
    return set_before


def _seat_gate(tmp_path, name):
    store = tmp_path / name
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(ASK_ALL)
    (store / "policies" / "approvals.yaml").write_text(SEATS)
    key = tmp_path / f"{name}.key"
    crypto.save_key(crypto.keygen(), key)
    return store, key


@pytest.mark.parametrize("before", ["gh secret set X -b v", "gh -R myorg/app secret set X -b v"])
def test_a_line_read_as_before_471_as_credentials_is_answered_once(tmp_path, before_is, before):
    store, key = _seat_gate(tmp_path, "st12")
    line = "gh pr view 5 -R myorg/app"
    before_is(line, before)
    g, ctx = make_gate(store, key)
    c = gate.classify(("terminal", {"command": line}))
    assert c.category == "read" and "credentials" in (c.before.category, getattr(c.before.floor, "category", "")), c
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve" and r.get("once") is True and r["approvers"] == ["principal"], r
    answer(ctx, r, "always")
    assert g.grants.list() == []                                              # no grant was kept
    assert gate.binds_exact(c, gate.decide(c, g.policies()))


def test_a_line_read_as_before_471_with_an_unseen_body_is_answered_once(tmp_path, before_is):
    store, key = _seat_gate(tmp_path, "st13")
    line = "gh pr comment 5 -R myorg/app -b x"
    before_is(line, 'gh pr comment 5 -R myorg/app -b "$X"')
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve" and r.get("once") is True, r


def test_who_may_answer_a_line_read_as_before_471_may_answer_that_reading(tmp_path, before_is):
    store, key = _seat_gate(tmp_path, "st14")
    line = "gh pr comment 5 -R myorg/app -b x"
    before_is(line, "gh pr -b x comment 5 -R myorg/app")             # read as a read, with a post for its floor
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": line})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r


DELEGATED = 'approvers:\n  default: [principal, "seat:saga-divan"]\n  categories:\n    credentials: [principal]\n'


@pytest.mark.parametrize("before, more", [
    ("env; echo x > ~/.exo/policies/gates.yaml", {}),            # #447: a store write part beside a secret read
    ("cp /tmp/evil.yaml ~/.exo/policies/default.yaml", {}),
    ("cat config", {"workdir": "~/.kube"}),                       # #311: a store read from the call's own folder
])
def test_a_store_part_only_in_the_line_read_as_before_471_keeps_the_principal_once(tmp_path, before_is, before, more):
    # gate.readings lists the line read as before #471 after the line's own readings, so call_approvers and no_grant
    # (#447, #311) read its parts too: a seat every other category is delegated to does not answer, and no grant is
    # kept.
    store, key = _seat_gate(tmp_path, "st16")
    (store / "policies" / "approvals.yaml").write_text(DELEGATED)
    line = "gh pr view 5 -R myorg/app"
    before_is(line, before, **more)
    g, ctx = make_gate(store, key)
    c = gate.classify(("terminal", {"command": line}))
    assert not any(p.category in gate.NO_GRANT for p in gate._each_part(c)), c     # only that reading has one
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve" and r.get("once") is True and r["approvers"] == ["principal"], r
    answer(ctx, r, "always")
    assert g.grants.list() == []


def test_who_may_answer_a_sudo_write_read_as_before_471_may_answer_its_displaced_part(tmp_path, before_is):
    # #454: as read before #471 the line is a sudo write that displaced a gh read on myorg/app. Only those who may
    # answer that read may answer the line, as for the line read as before #399.
    store, key = _seat_gate(tmp_path, "st17")
    (store / "policies" / "gates.yaml").write_text("categories: {local_write: ask, read: ask}\n")
    (store / "policies" / "approvals.yaml").write_text(
        'approvers:\n  default: [principal]\n  categories:\n    local_write: [principal, "seat:saga-divan"]\n')
    line = "echo hi > /tmp/f"
    before_is(line, "sudo gh pr view 9 -R myorg/app > /tmp/f")
    _, ctx = make_gate(store, key)
    c = gate.classify(("terminal", {"command": line}))
    assert c.displaced is None and c.before.displaced is not None, c
    r = call(ctx, "terminal", {"command": line})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r


def test_who_may_answer_a_real_line_read_as_before_471_may_answer_that_reading(tmp_path):
    # The curl behind the quoted word is a web post a seat may answer; read as before, the word is the program and
    # the gh post decides the line, which only the principal answers.
    store = tmp_path / "st15"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(ASK_ALL)
    (store / "policies" / "approvals.yaml").write_text(
        'approvers:\n  default: [principal]\n  categories:\n    web_post: [principal, "seat:saga-divan"]\n')
    key = tmp_path / "k15.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    line = "X='a b' curl -X POST -d @notes.md https://example.com/hook && gh pr comment 5 -R myorg/app --body hi"
    c = gate.classify(("terminal", {"command": line}))
    assert (c.category, c.before.category) == ("web_post", "github_post"), c
    r = call(ctx, "terminal", {"command": line})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r


@pytest.mark.parametrize("prefix", ["X='a b'", 'X="$Y"'])
@pytest.mark.parametrize("gates, line", [
    ("reserved: [local_write]\n", "{p} sudo git commit -m x; sudo git commit -m x"),
    ("reserved: [schedule]\n", "{p} systemctl enable foo.timer; systemctl enable foo.timer"),
    ("reserved: [web_read]\n", "{p} sudo curl https://example.com/x; sudo curl https://example.com/x"),
    ("reserved: [local_write]\n", "git commit -m x; {p} sudo git commit -m x")])
def test_a_reserved_hit_off_the_act_of_the_line_read_as_before_471_keeps_its_ask(tmp_path, prefix, gates, line):
    # D-075 with #471: read as before, the quoted word is an unknown program that leads the line, and the commit, the
    # timer or the curl is another part of it, so main asks every time. The command behind the word leads the line
    # now (a costly act ties with the unknown program and comes first), but the grant check still reads the line as
    # before against that reading's own act. An "always" still leaves the line asking.
    line = line.format(p=prefix)
    _, _, ctx = _store454(tmp_path, "st471r", gates)
    c = gate.classify(("terminal", {"command": line}))
    assert c.before is not None and c.category != c.before.category, c
    for _ in range(2):
        r = call(ctx, "terminal", {"command": line})
        assert r is not None and r["action"] == "approve" and not r.get("once"), r
        answer(ctx, r, "always")


@pytest.mark.parametrize("gates, line", [
    ("reserved: [local_write]\n", "X=ab sudo git commit -m x; sudo git commit -m x"),
    ("reserved: [vcs_push]\n", "X='a b' git push origin main")])
def test_a_reserved_hit_on_the_act_of_each_reading_471_takes_the_grant_as_on_main(tmp_path, gates, line):
    # An unquoted prefix is read one way, with the commit leading it. Read as before, `X='a b' git push origin main` is
    # an unknown program with no reserved hit, and the push it reads now is the act of the line. Main takes the grant
    # on both, and so does the hook.
    _, _, ctx = _store454(tmp_path, "st471g", gates)
    r = call(ctx, "terminal", {"command": line})
    assert r is not None and r["action"] == "approve", r
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": line}) is None


def test_a_pending_reserved_entry_holds_off_a_grant_on_the_line_read_as_before_471(tmp_path):
    # D-038: a reserved entry not yet accepted reaches the check on the line read as before too.
    line = "X='a b' sudo git commit -m x; sudo git commit -m x"
    store, g, ctx = _store454(tmp_path, "st471p", "categories: {opaque_exec: ask}\n")
    r = call(ctx, "terminal", {"command": line})
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": line}) is None
    p = store / "policies" / "gates.yaml"
    p.write_text("categories: {opaque_exec: ask}\nreserved: [local_write]\n")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10_000_000))
    assert g.changed_policies() is not None
    r = call(ctx, "terminal", {"command": line})
    assert r is not None and r["action"] == "approve" and "policy files changed this session" in r["message"], r


# Guard rows (review 5659099075): with main's joined reading (#584) and its reading as before D-074 (the home's
# config.json), the line read as before #471 keeps both, and so does the new reading. A config.json store write in
# any of them stays the principal's, once, and an "always" on a push read joined behind the quoted word covers that
# exact line only, as on main. With main 58503282 (review 5661669883): a store name split by quotes or a
# backslash-newline, or in another letter case (#601), behind a quoted prefix, also as the folder of a `cd` there; a
# camelCase tool that writes the home's config.json (#515, its floor); and a `$((` bash reads as a command
# substitution (#553).
@pytest.mark.parametrize("tool, line", [
    ("terminal", "X='a b' cp /tmp/x ~/.exo/con$\\\nfig.json"),
    ("terminal", "X='a b' cp /tmp/x \"$\\\n{HOME}/.exo/config.json\""),
    ("terminal", "git push origin feature && X=\"$Y\" cp /tmp/x ~/.exo/config.json"),
    ("terminal", "X='a b' cp /tmp/x ~/.E''XO/con\\\nfig.json"),
    ("terminal", "X='a b' cd ~/.e\\\nxo && cp /tmp/x config.json"),
    ("terminal", "git push origin feature && X=\"$Y\" cd ~/'.exo' && cp /tmp/x config.json"),
    ("writeFile", "X='a b' rm -rf /srv/data")])
def test_a_config_store_write_behind_a_quoted_assignment_keeps_the_principal_once(tmp_path, monkeypatch, tool, line):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home" / ".exo"))
    store, key = _seat_gate(tmp_path, "st471c")
    (store / "policies" / "approvals.yaml").write_text(DELEGATED)
    g, ctx = make_gate(store, key)
    args = {"command": line} if tool == "terminal" else {"path": "~/.exo/config.json", "content": line}
    r = call(ctx, tool, args)
    assert r and r["action"] == "approve" and r.get("once") is True and r["approvers"] == ["principal"], r
    answer(ctx, r, "always")
    assert g.grants.list() == []


@pytest.mark.parametrize("line", ["X='a b' echo \"$\\\n(git push --force origin main)\"",
                                  "X='a b' echo $((echo '((' ; git push --force origin main) )",
                                  "git push origin feature && X=a\\ b echo $((echo '((' ; git push -f origin main) )"])
def test_an_always_on_a_joined_push_behind_a_quoted_assignment_covers_that_line_only(tmp_path, line):
    store, key = _seat_gate(tmp_path, "st471j")
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": line})
    assert r and r["action"] == "approve" and r["approvers"] == ["principal"], r
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": line}) is None
    r = call(ctx, "terminal", {"command": line.replace("main", "release")})
    assert r and r["action"] == "approve", r


# --- standing grants ------------------------------------------------------------------------

def test_always_grant_survives_restart_and_expires(keyed_store):
    store, key = keyed_store
    (store / "policies" / "approvals.yaml").write_text(APPROVALS)
    now = [1_000_000.0]
    _, ctx = make_gate(store, key, clock=lambda: now[0])
    r = call(ctx, *post("Ship it."))
    answer(ctx, r, "always", by="principal:tui")
    grants = json.loads((store / "state" / "approval-grants.json").read_text())
    assert len(grants) == 1 and grants[0]["binding"] == r["binding"] and grants[0]["rule"] == "github_post"
    assert grants[0]["expires"] == now[0] + 2 * 86400 and grants[0]["by"] == "principal:tui"
    assert oct((store / "state" / "approval-grants.json").stat().st_mode & 0o777) == "0o600"
    row = [b for b in events(store) if b.get("phase") == "grant"][0]
    assert row["grant"] == grants[0]["id"] and row["binding"] == r["binding"] and row["expires"]

    _, ctx2 = make_gate(store, key, clock=lambda: now[0] + 3600)        # a restart: a new gate, same store
    assert call(ctx2, *post("Ship it.")) is None
    used = events(store)[-1]
    assert used["grant"] == grants[0]["id"] and used["outcome"] == "allow"

    _, ctx3 = make_gate(store, key, clock=lambda: now[0] + 3 * 86400)   # past the expiry
    assert call(ctx3, *post("Ship it."))["action"] == "approve"


def test_non_outward_grant_is_scoped_to_rule_and_target(tmp_path):
    store = tmp_path / "st2"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("categories: {remote_write: ask}\n")
    key = tmp_path / "k2.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    r = call(ctx, "mcp_notes_create_page", {"target": "notes:inbox", "title": "t"})
    assert r["action"] == "approve" and "binding" not in r
    answer(ctx, r, "always")
    [g] = json.loads((store / "state" / "approval-grants.json").read_text())
    assert g["rule"] == "remote_write" and g["target"] == "notes:inbox" and g["binding"] == ""
    assert g["expires"] - g["created"] == 30 * 86400
    _, ctx2 = make_gate(store, key)                                   # restart
    assert call(ctx2, "mcp_notes_create_page", {"target": "notes:inbox", "title": "other"}) is None
    assert call(ctx2, "mcp_notes_create_page", {"target": "notes:other", "title": "t"})["action"] == "approve"


def _store454(tmp_path, name, gates, approvals=""):
    store = tmp_path / name
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(gates)
    if approvals:
        (store / "policies" / "approvals.yaml").write_text(approvals)
    key = tmp_path / f"{name}.key"
    crypto.save_key(crypto.keygen(), key)
    return (store, *make_gate(store, key))


def test_a_grant_on_a_sudo_write_is_keyed_on_the_written_path(tmp_path):
    # #454: `sudo echo hi > /tmp/f` is a costly local_write with the written path as target. Where only the write's
    # category asks, its grant covers that path, as one for `echo hi > /tmp/f` would.
    store, _, ctx = _store454(tmp_path, "st454", "categories: {local_write: ask}\n")
    r = call(ctx, "terminal", {"command": "sudo echo hi > /tmp/f"})
    assert r["action"] == "approve" and "binding" not in r, r
    answer(ctx, r, "always")
    grants = json.loads((store / "state" / "approval-grants.json").read_text())
    assert [(g["rule"], g["target"], g.get("binding") or "") for g in grants] == [("local_write", "/tmp/f", "")]
    assert call(ctx, "terminal", {"command": "echo hi > /tmp/f"}) is None
    assert call(ctx, "terminal", {"command": "sudo chmod 777 /x > /tmp/f"}) is None
    assert call(ctx, "terminal", {"command": "sudo echo hi > /tmp/g"})["action"] == "approve"


def test_a_grant_on_a_sudo_write_does_not_answer_an_ask_on_its_displaced_part(tmp_path):
    # #454 re-check: where the line asks with the sudo command's own part leading (here its category), a grant keyed
    # on the written path would answer that ask for any other command writing there. The "always" covers the exact
    # line only, and a grant for a costly read keyed on the tool does not cover the line.
    store, _, ctx = _store454(tmp_path, "st454c", "categories: {read: ask, shell: ask}\n")
    r = call(ctx, "terminal", {"command": "sudo ls"})
    assert r["action"] == "approve"
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": "sudo ls -la"}) is None
    r = call(ctx, "terminal", {"command": "sudo echo hi > /tmp/f"})
    assert r["action"] == "approve" and r.get("binding"), r
    answer(ctx, r, "always")
    grants = json.loads((store / "state" / "approval-grants.json").read_text())
    assert [(g["rule"], g["target"], bool(g.get("binding"))) for g in grants] == [
        ("read", "terminal", False), ("local_write", "/tmp/f", True)]
    assert call(ctx, "terminal", {"command": "sudo echo hi > /tmp/f"}) is None
    r = call(ctx, "terminal", {"command": "sudo chmod 4755 /usr/bin/python3 > /tmp/f"})
    assert r["action"] == "approve" and "category shell" in r["message"], r


@pytest.mark.parametrize("gates", ["categories: {shell: ask}\n", "categories: {read: ask, shell: ask}\n",
                                   "escalate: {categories: [shell]}\n",
                                   "rules: [{match: {category: shell, reversibility: costly}, decision: ask}]\n"])
def test_only_who_may_answer_the_displaced_part_answers_its_ask(tmp_path, gates):
    # #454 re-check: the ask comes from the line read with chmod leading, and chmod names no target. A seat that may
    # answer only local_write must not answer it, as on main, where the line was chmod's.
    _, _, ctx = _store454(tmp_path, "st454d", gates, "approvers: {local_write: ['seat:helper']}\n")
    r = call(ctx, "terminal", {"command": "sudo chmod u+s /tmp/x > /tmp/log"})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r


def test_a_changed_policy_whose_displaced_reading_asks_binds_the_line_too(tmp_path):
    # #454 re-check: mid-session the stricter of the pinned and the changed policy applies (D-038). The pinned one
    # asks only on the write; the changed one asks on chmod too, so the approval is about chmod as well.
    store, g, ctx = _store454(tmp_path, "st454e", "categories: {local_write: ask}\n",
                              "approvers: {local_write: ['seat:helper']}\n")
    g.policies()
    line = {"command": "sudo chmod u+s /tmp/x > /tmp/log"}
    r = call(ctx, "terminal", line)
    assert r["action"] == "approve" and not r.get("binding") and r["approvers"] == ["principal", "seat:helper"], r
    p = store / "policies" / "gates.yaml"
    p.write_text("categories: {local_write: ask, shell: ask}\n")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10_000_000))
    r = call(ctx, "terminal", line)
    assert r["action"] == "approve" and r.get("binding") and r["approvers"] == ["principal"], r


@pytest.mark.parametrize("gates,line", [
    ("version: 1\n", 'sudo gh pr "$V" 5 > /tmp/f'), ("categories: {read: ask}\n", 'sudo gh pr "$V" 5 > /tmp/f; ls'),
    ("version: 1\n", 'sudo chmod u+s /tmp/x > /tmp/f; gh pr "$V" 5'),
    ("categories: {shell: ask}\n", 'sudo chmod u+s /tmp/x > /tmp/f; gh pr "$V" 5')])
def test_the_line_as_read_before_399_keeps_the_approvers_of_its_displaced_part(tmp_path, gates, line):
    # #454 with #399: only who may answer the line as read before #399 (floor) may answer. That reading is a sudo write
    # line here. On main it led with the gh read or chmod the write displaced, so a seat that may answer opaque_exec
    # and local_write, but not that part's category, does not answer, as on main.
    _, _, ctx = _store454(tmp_path, "st454f", gates, "approvers: {opaque_exec: ['seat:h'], local_write: ['seat:h']}\n")
    r = call(ctx, "terminal", {"command": line})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r


def test_an_ask_from_a_displaced_part_outlives_a_stricter_399_reading(tmp_path):
    # #454 with #399, review 5640972780: as read now, the line is a sudo write line whose displaced chmod names no
    # target, and {shell: ask} asks on chmod. As read before #399 (floor), gh api's first -X is DELETE, public, so that
    # reading is stricter and stands. The ask from chmod's reading must outlive the merge (_strictest): a seat that may
    # answer the write and the floor's category, but not shell, does not answer, as before #454, where chmod led.
    _, _, ctx = _store454(tmp_path, "st454h", "categories: {shell: ask}\n",
                          "approvers: {local_write: ['seat:h'], delete: ['seat:h']}\n")
    r = call(ctx, "terminal", {"command": "sudo chmod u+s /tmp/x > /tmp/f; gh api -X DELETE -X GET repos/o/r"})
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r
    r = call(ctx, "terminal", {"command": "sudo echo hi > /tmp/f; gh api -X DELETE -X GET repos/o/r"})
    assert r["action"] == "approve" and r["approvers"] == ["principal", "seat:h"], r


def test_a_grant_on_the_written_path_does_not_answer_a_rule_on_the_host_or_repo(tmp_path):
    # #454: a sudo write line is also decided with the sudo command's own part leading, so a rule on the repo or host
    # that part names asks. A grant keyed on the written path does not answer that ask, and a seat that may answer
    # only local_write may not either. An "always" there covers the exact line only. With no repo or host, the
    # approvers follow the line's category.
    store = tmp_path / "st454b"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(
        "categories: {local_write: ask}\nrules: [{match: {target: prod}, decision: ask, reason: host},"
        " {match: {target: 'o/r*'}, decision: ask, reason: repo}]\n")
    (store / "policies" / "approvals.yaml").write_text("approvers: {local_write: ['seat:helper']}\n")
    key = tmp_path / "k454b.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    for path in ("/srv/out", "/tmp/f"):
        r = call(ctx, "terminal", {"command": f"echo hi > {path}"})
        assert r["action"] == "approve" and r["approvers"] == ["principal", "seat:helper"], r
        answer(ctx, r, "always")
    for line in ("ssh prod 'sudo echo hi > /srv/out'", "sudo gh pr view 1 -R o/r > /tmp/f"):
        r = call(ctx, "terminal", {"command": line})
        assert r["action"] == "approve" and r["approvers"] == ["principal"], (line, r)
        answer(ctx, r, "always")
        assert call(ctx, "terminal", {"command": line}) is None, line
    assert call(ctx, "terminal", {"command": "sudo gh pr view 2 -R o/r > /tmp/f"})["action"] == "approve"
    assert call(ctx, "terminal", {"command": "echo hi > /srv/out"}) is None
    assert call(ctx, "terminal", {"command": "sudo echo hi > /tmp/f"}) is None
    r = call(ctx, "terminal", {"command": "sudo echo hi > /tmp/g"})
    assert r["action"] == "approve" and r["approvers"] == ["principal", "seat:helper"], r


@pytest.mark.parametrize("gates,line", [
    ("reserved: [local_write]\n", "sudo echo hi > /tmp/f"), ("reserved: [local_write]\n", "x=$(sudo ls > /tmp/f)"),
    ("reserved: [local_write]\n", "ssh prod 'sudo echo hi > /tmp/f'"),
    ("reserved: [{category: local_write, target: '/etc/*'}]\n", "sudo echo hi >> /etc/hosts"),
    ("categories: {local_write: ask}\nreserved: [local_write]\n", "sudo chmod 777 /x > /tmp/f")])
def test_a_reserved_hit_on_a_sudo_write_keeps_its_ask_from_a_grant(tmp_path, gates, line):
    # #454 with D-075: the write leads the line now, but the line is also the act of the part it displaced, which led
    # it on main. There the write was another part of the line, so its reserved hit kept the principal's ask from any
    # grant. An "always" still leaves the line asking.
    _, _, ctx = _store454(tmp_path, "st454g", gates)
    for _ in range(2):
        r = call(ctx, "terminal", {"command": line})
        assert r is not None and r["action"] == "approve" and "reserved for the principal" in r["message"], r
        answer(ctx, r, "always")


def test_a_grant_never_covers_a_reserved_part_of_the_line(tmp_path):
    # D-075: the push grant covers the push. A reserved merge on the same line keeps the principal's ask.
    store = tmp_path / "st3"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("categories: {vcs_push: ask}\nreserved: [vcs_merge]\n")
    key = tmp_path / "k3.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "git push origin feature"})
    assert r["action"] == "approve"
    answer(ctx, r, "always")
    [g] = json.loads((store / "state" / "approval-grants.json").read_text())
    assert g["rule"] == "vcs_push" and g["target"] == "origin"
    assert call(ctx, "terminal", {"command": "git push origin feature"}) is None
    r = call(ctx, "terminal", {"command": "git push origin feature && gh pr merge 3 --squash"})
    assert r["action"] == "approve" and "reserved for the principal" in r["message"], r
    assert events(store)[-1]["decision"]["reserved"] == [["vcs_merge", "#3"]]


def test_a_grant_for_one_push_does_not_cover_a_second_push_with_other_credentials(tmp_path):
    # D-075: the ledger view redacts URL userinfo, but the grant check compares the hits as written, so a
    # second push to the same URL under other credentials is not taken for the push the grant was given for.
    store = tmp_path / "st5"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("categories: {vcs_push: ask}\nreserved: [vcs_push]\n")
    key = tmp_path / "k5.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    push_a = "git push https://alice:tokA@example.com/acme/app.git main"
    r = call(ctx, "terminal", {"command": push_a})
    assert r["action"] == "approve"
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": push_a}) is None
    r = call(ctx, "terminal", {"command": push_a + " && git push https://bob:tokB@example.com/acme/app.git main"})
    assert r["action"] == "approve" and "reserved for the principal" in r["message"], r
    row = json.dumps(events(store)[-1]["decision"])
    assert "tokA" not in row and "tokB" not in row and "https://***@example.com/acme/app.git" in row


MERGE_ELSEWHERE = "gh pr merge --auto https://github.com/pub/repo/pull/7 -R myorg/app"
CLOSE_ELSEWHERE = "gh issue close 5 -R myorg/app --duplicate-of https://github.com/myorg/secret/issues/1"


def test_a_grant_on_the_target_does_not_cover_a_gh_line_that_may_land_on_another(tmp_path):
    # #399: gh merges the URL operand's PR, not one on the -R repo, and the gate decides the line on every target
    # it cannot rule out. A grant on one of them must not run the line, and the "session" key must not cover it.
    store = tmp_path / "st6"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(
        "scopes: {internal: ['myorg/*']}\ncategories: {vcs_merge: ask, github_close: ask}\n")
    key = tmp_path / "k6.key"
    crypto.save_key(crypto.keygen(), key)
    g, ctx = make_gate(store, key)
    g.grants.add("vcs_merge", "myorg/app", "", 30, "principal:tui")      # what an "always" on a plain merge leaves
    g.grants.add("github_close", "myorg/app#5", "", 30, "principal:tui")
    assert call(ctx, "terminal", {"command": "gh pr merge -R myorg/app --squash"}) is None
    assert call(ctx, "terminal", {"command": "gh issue close 5 -R myorg/app"}) is None
    for line, category in ((MERGE_ELSEWHERE, "vcs_merge"), (CLOSE_ELSEWHERE, "github_close")):
        c = gate.classify(("terminal", {"command": line}))
        assert c.alt_targets, c
        r = call(ctx, "terminal", {"command": line})
        assert r and r["action"] == "approve", (line, r)
        assert r["rule_key"] == f"exo-gate:{category}:{r['binding'][:16]}", r      # bound to the exact line
        assert "grant" not in events(store)[-1], events(store)[-1]


def test_always_on_a_gh_line_with_other_targets_covers_that_line_only(tmp_path):
    store = tmp_path / "st7"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("scopes: {internal: ['myorg/*']}\ncategories: {vcs_merge: ask}\n")
    key = tmp_path / "k7.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": MERGE_ELSEWHERE})
    answer(ctx, r, "always")
    [g] = json.loads((store / "state" / "approval-grants.json").read_text())
    # gh merges the URL operand's PR, so that is the target, and the grant binds the exact line.
    assert g["rule"] == "vcs_merge" and g["target"] == "pub/repo#7" and g["binding"] == r["binding"], g
    assert call(ctx, "terminal", {"command": MERGE_ELSEWHERE}) is None
    assert call(ctx, "terminal", {"command": "gh pr merge -R myorg/app --squash"})["action"] == "approve"
    assert call(ctx, "terminal", {"command": MERGE_ELSEWHERE.replace("/7", "/8")})["action"] == "approve"


def test_a_grant_on_the_top_part_does_not_run_a_hop_a_rule_on_its_host_denies(tmp_path):
    # #399 round 4: `gh repo view --web myorg/app` may also read with no repo, and on the hop the host stands for
    # that empty target. The rule on the host's reads denies the line, as on main, whatever grant the merge has.
    store = tmp_path / "st8"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text(
        "categories: {vcs_merge: ask}\nrules:\n  - match: {category: read, target: buildhost}\n    decision: deny\n")
    key = tmp_path / "k8.key"
    crypto.save_key(crypto.keygen(), key)
    g, ctx = make_gate(store, key)
    g.grants.add("vcs_merge", "myorg/app#9", "", 30, "principal:tui")      # what an "always" on the merge leaves
    assert call(ctx, "terminal", {"command": "gh pr merge 9 -R myorg/app"}) is None
    for line in ("ssh buildhost gh repo view --web myorg/app",
                 "gh pr merge 9 -R myorg/app && ssh buildhost gh repo view --web myorg/app"):
        r = call(ctx, "terminal", {"command": line})
        assert r and r["action"] == "block", (line, r)


def test_a_reserved_entry_added_by_a_pending_policy_also_holds_off_a_grant(tmp_path):
    # D-038 + D-075: the reserved entry arrives after the grant and is not yet accepted. Both policies
    # ask about the line, so the pinned decision is kept; the pending policy's reserved hit must still
    # reach the grant check.
    store = tmp_path / "st4"
    (store / "policies").mkdir(parents=True)
    gates = store / "policies" / "gates.yaml"
    gates.write_text("categories: {vcs_push: ask}\n")
    key = tmp_path / "k4.key"
    crypto.save_key(crypto.keygen(), key)
    g, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "git push origin feature"})
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": "git push origin feature"}) is None
    gates.write_text("categories: {vcs_push: ask}\nreserved: [vcs_merge]\n")
    assert g.changed_policies() is not None
    r = call(ctx, "terminal", {"command": "git push origin feature && gh pr merge 3 --squash"})
    assert r["action"] == "approve" and "policy files changed this session" in r["message"], r
    assert events(store)[-1]["decision"]["reserved"] == [["vcs_merge", "#3"]]
    assert call(ctx, "terminal", {"command": "git push origin feature"}) is None


def test_a_seat_never_makes_a_standing_grant(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, *post("Ship it."))
    answer(ctx, r, "always", by="seat:saga-divan")
    assert not (store / "state" / "approval-grants.json").exists()
    assert call(ctx, *post("Ship it."))["action"] == "approve"


def test_grants_command_lists_and_revokes_with_a_ledger_row(keyed_store):
    store, key = keyed_store
    g, ctx = make_gate(store, key)
    assert hook.grants_command(g, "") == "no standing grants"
    r = call(ctx, *post("Ship it."))
    answer(ctx, r, "always")
    [row] = g.grants.list()
    out = hook.grants_command(g, "list")
    assert out.startswith(row["id"]) and "github_post on other/repo#12" in out and r["binding"][:12] in out
    assert hook.grants_command(g, f"revoke {row['id']}") == f"revoked {row['id']}"
    rev = [b for b in events(store) if b.get("phase") == "revoke"]
    assert rev and rev[0]["grant"] == row["id"]
    assert call(ctx, *post("Ship it."))["action"] == "approve"
    assert hook.grants_command(g, "revoke") == "usage: grants revoke <id>"


def test_credentials_never_get_a_grant(keyed_store, tmp_path):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, "read_file", {"path": str(tmp_path / ".exo" / "keys" / "x.key")})
    assert r["action"] == "approve" and r.get("once") is True
    answer(ctx, r, "always")
    assert not (store / "state" / "approval-grants.json").exists()
    gs = hook.GrantStore(store / "state" / "g.json")
    assert gs.add("credentials", "x", "", 30, "tui") is None


def test_outward_grant_without_binding_never_matches(tmp_path):
    gs = hook.GrantStore(tmp_path / "g.json")
    gs.add("social_post", "*", "", 30, "tui")
    assert gs.match("social_post", "anything", "abc", outward=True) is None
    gs.add("social_post", "x", "abc", 30, "tui")
    assert gs.match("social_post", "x", "abc", outward=True)["binding"] == "abc"
    assert gs.match("social_post", "x", "abd", outward=True) is None


def test_grants_write_does_not_follow_a_symlink_at_the_old_temporary_name(tmp_path):
    """The grants file was written as `<name>.tmp` with O_TRUNC and no O_EXCL: a symlink planted there sent the
    grants to any file the user can write. It goes through mkstemp now (#273)."""
    import os
    import stat
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch\n")
    path = tmp_path / "state" / "g.json"
    path.parent.mkdir()
    path.with_suffix(".tmp").symlink_to(victim)
    assert hook.GrantStore(path).add("social_post", "x", "abc", 30, "tui") is not None
    assert victim.read_text() == "do not touch\n"
    st = os.lstat(path)
    assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
    assert [r["binding"] for r in json.loads(path.read_text())] == ["abc"]


# --- outbound scrub -------------------------------------------------------------------------

@pytest.fixture
def seats(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    (home / "seats").mkdir(parents=True)
    (home / "seats" / "saga-divan.toml").write_text('name = "saga-divan"\n')
    (home / "seats" / "scout.toml").write_text('name = "scout"\n')
    (home / "bus").mkdir()
    (home / "bus" / "registry.json").write_text(json.dumps({"seats": [{"seat": "orda-lead"}]}))
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


def _scrub(**kw):
    return gate.normalize_scrub(kw, gate.internal_names(None))


def test_scrub_finds_seat_names_paths_models_and_ids(seats):
    sc = _scrub()
    assert gate.check_scrub("Reviewed by saga-divan.", sc) == ["internal name: saga-divan"]
    assert gate.check_scrub("cc saga divan", sc) == ["internal name: saga divan"]
    assert gate.check_scrub("relayed by orda-lead", sc) == ["internal name: orda-lead"]
    assert gate.check_scrub("see ~/Work/exo/notes", sc) == ["home path: ~/"]
    assert gate.check_scrub("at /Users/someone/x", sc) == ["home path: /Users/someone"]
    assert gate.check_scrub("drafted on claude-opus-5-5", sc) == ["model id: claude-opus-5-5"]
    assert gate.check_scrub("task t_0123456789abcdef failed", sc) == ["internal id: t_0123456789abcdef"]
    assert gate.check_scrub("A clean note about the fix.", sc) == []


def test_plain_word_seat_name_matches_only_as_a_token(seats):
    sc = _scrub()
    assert gate.check_scrub("Scouting the docs found two typos.", sc) == []
    assert gate.check_scrub("The scout said so.", sc) == ["internal name: scout"]
    assert gate.check_scrub("The scout said so.", _scrub(allow=["scout"])) == []


def test_scrub_user_list_and_switches(seats, tmp_path):
    store = tmp_path / "st"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "internal-names.txt").write_text("# names\nproject-x9\n")
    sc = gate.normalize_scrub({"patterns": [{"pattern": r"\blrn-\d+\b", "label": "lesson id"}]},
                              gate.internal_names(store))
    assert gate.check_scrub("per lrn-042 and project-x9", sc) == ["internal name: project-x9", "lesson id: lrn-042"]
    assert gate.check_scrub("saga-divan", sc, exposure="internal") == []   # public only by default
    assert gate.check_scrub("saga-divan", gate.normalize_scrub({"enabled": False}, ["saga-divan"])) == []
    assert gate.check_scrub("saga-divan", gate.normalize_scrub({"seat_names": False}, ["saga-divan"])) == []


def test_draft_with_seat_name_or_home_path_is_held(keyed_store, seats):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, *post("Fixed by saga-divan, see ~/Work/exo."))
    assert r["action"] == "approve"
    assert "internal name: saga-divan" in r["message"] and "home path: ~/" in r["message"]
    d = events(store)[-1]["decision"]
    assert "internal name: saga-divan" in d["violations"] and "home path: ~/" in d["violations"]


def test_clean_draft_passes_the_scrub(keyed_store, seats):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    r = call(ctx, *post("Suggest a fix for the parser."))
    assert r["action"] == "approve"                       # still a public post: the floor asks
    assert events(store)[-1]["decision"]["violations"] == []
    # private target: a message to the principal's own channel runs, with no scrub
    assert call(ctx, "send_message", {"target": "telegram:own-1", "message": "saga-divan done in ~/x"}) is None


def test_footer_exposures_limits_the_footer_to_public():
    pol = gate.normalize_policies({"posting": {"footer": "> Automated.", "footer_exposures": ["public"]}})
    c = gate.classify(("send_message", {"target": "#team", "message": "hi"}))
    c.exposure = "internal"
    c.third_party = False
    assert "missing footer: > Automated." not in gate.decide(c, pol).violations
    c.exposure = "public"
    assert "missing footer: > Automated." in gate.decide(c, pol).violations
