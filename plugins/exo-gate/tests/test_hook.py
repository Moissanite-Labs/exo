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
    return asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1",
                          task_id="", **kw))


def test_register_wires_both_hooks(keyed_store):
    _, ctx = make_gate(*keyed_store)
    assert set(ctx.hooks) == {"pre_tool_call", "post_approval_response"}


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


def test_internal_error_fails_closed_except_reads(keyed_store, monkeypatch):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    monkeypatch.setattr(hook.gate, "classify", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert call(ctx, "read_file", {"path": "x"}) is None
    r = call(ctx, "terminal", {"command": "ls"})
    assert r["action"] == "approve" and "boom" in r["message"]


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


def test_internal_error_on_a_secret_read_is_not_a_free_pass(keyed_store, monkeypatch):
    store, key = keyed_store
    g, ctx = make_gate(store, key, decision_model=False)
    monkeypatch.setattr(g, "_rules", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert g.pre_tool_call("read_file", {"path": "~/.exo/keys/seat.key"})["action"] == "approve"
    assert g.pre_tool_call("read_file", {"path": "README.md"}) is None


@pytest.mark.parametrize("cat, tool, args, plain_args", [
    ("local_write", "write_file", {"path": "~/.exo/policies/gates.yaml", "content": "version: 1\n"},
     {"path": "notes.md", "content": "x"}),
    # #311: a read line whose store read from the shell call's own folder is a credentials part (abu-aska's repro
    # on the #452 + #467 merge: the fallback read the top category only, so observe mode ran the call)
    ("read", "bash", {"command": "cat config", "cwd": "~/.kube"}, {"command": "cat config", "cwd": "~/proj"}),
    ("read", "terminal", {"command": "cat config", "workdir": "~/.kube"}, {"command": "cat notes.md"}),
    ("force_push", "bash", {"command": "cat * && git push --force origin main", "cwd": "~/.claude/projects"},
     {"command": "git push --force origin main", "cwd": "~/proj"}),
])
def test_a_policy_that_breaks_decide_still_asks_the_principal_once_for_a_store_write(cat, tool, args, plain_args,
                                                                                     keyed_store, tmp_path,
                                                                                     monkeypatch):
    # #464: a regex that does not compile raises in decide, now also on a store write's own part. The error card
    # for a call the gate read as a store write or secret asks the principal alone, once, in every mode.
    store, key = keyed_store
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (store / "policies" / "gates.yaml").write_text(f"deny:\n  - category: {cat}\n    command: '^(rm|shred'\n")
    for extra in ({}, {"mode": "observe"}):
        g, _ = make_gate(store, key, decision_model=False, **extra)
        r = g.pre_tool_call(tool, args)
        assert r["action"] == "approve" and r["rule_key"] == f"exo-gate:error:{tool}", r
        assert (r["approvers"], r["once"]) == (["principal"], True), r
        plain = g.pre_tool_call(tool, plain_args)
        if extra:
            assert plain is None                      # observe mode, as before
        else:
            assert plain["action"] == "approve" and "once" not in plain and "approvers" not in plain


def test_a_policy_that_breaks_decide_still_asks_once_for_a_store_write_only_the_floor_reads(keyed_store, tmp_path,
                                                                                            monkeypatch):
    # #439 review: the fallback read only the top reading, so a gh download into the store, which the line as read
    # before #399 (its floor) reads as a store write, got the plain error card, and ran in observe mode.
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
            assert r and r["rule_key"] == "exo-gate:error:terminal", (line, extra, r)
            assert (r.get("approvers"), r.get("once")) == (["principal"], True), (line, extra, r)


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
