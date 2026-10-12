import asyncio
import importlib.util
import sys

import pytest

from gate_fixtures import PLUGIN
from exo import crypto
from exo.ledger import Ledger
from exo_gate import hook
from exo_gate.hook import Gate, LedgerSink, resolve_settings

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


def test_store_without_key_logs_only(example_store):
    _, ctx = make_gate(example_store)
    assert call(ctx, "terminal", {"command": "ls"}) is None
    assert not (example_store / "ledger.sqlite").exists()


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
