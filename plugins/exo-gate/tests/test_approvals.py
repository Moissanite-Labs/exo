"""D-033: approvals bound to the exact text, who may approve, standing grants, outbound scrub."""
import asyncio
import json

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


def test_a_grant_on_a_sudo_write_is_keyed_on_the_written_path(tmp_path):
    # #454: `sudo echo hi > /tmp/f` is a costly local_write with the written path as target. Its grant covers that
    # path, as one for `echo hi > /tmp/f` would, and a grant for a costly read keyed on the tool no longer covers it.
    store = tmp_path / "st454"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("categories: {read: ask, local_write: ask}\n")
    key = tmp_path / "k454.key"
    crypto.save_key(crypto.keygen(), key)
    _, ctx = make_gate(store, key)
    r = call(ctx, "terminal", {"command": "sudo ls"})
    assert r["action"] == "approve"
    answer(ctx, r, "always")
    assert call(ctx, "terminal", {"command": "sudo ls -la"}) is None
    r = call(ctx, "terminal", {"command": "sudo echo hi > /tmp/f"})
    assert r["action"] == "approve", r
    answer(ctx, r, "always")
    grants = json.loads((store / "state" / "approval-grants.json").read_text())
    assert [(g["rule"], g["target"]) for g in grants] == [("read", "terminal"), ("local_write", "/tmp/f")]
    assert call(ctx, "terminal", {"command": "echo hi > /tmp/f"}) is None
    assert call(ctx, "terminal", {"command": "sudo echo hi > /tmp/g"})["action"] == "approve"


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
