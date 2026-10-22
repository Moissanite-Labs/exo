"""#311: a shell call's own folder argument (`cwd`, or `workdir` for Hermes' terminal and Codex's exec_command) that
holds a secret store asks as credentials do, through the hook too.

The line keeps its own category, so the hook reads the store read from the line's parts: no standing grant covers
the call, observe mode still enforces it, and only those who may answer credentials answer it. Temp stores, a temp
HOME and a mock context; nothing is executed."""
import asyncio
import json

import pytest

from exo import crypto, gate
from exo_gate import hook


class MockCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".kube").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("EXO_KEYS_FILE", raising=False)
    return h


@pytest.fixture
def store(tmp_path):
    """A store whose policy asks for every read, so a grant for one could cover the call if nothing kept it off."""
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "gates.yaml").write_text("categories: {read: ask}\n")
    (root / "policies" / "approvals.yaml").write_text(
        "approvers:\n  default: [principal, \"seat:builder\"]\n  credentials: [principal]\n")
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    return root, key


def make_gate(store, **extra):
    root, key = store
    ctx = MockCtx({"principal_store": str(root), "key_path": str(key), "decision_model": False, **extra})
    return hook.register(ctx), ctx


def call(ctx, tool, args):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1"))


def answer(ctx, verdict, choice, by="principal:tui"):
    [cb] = ctx.hooks["post_approval_response"]
    cb(pattern_key="plugin_rule:" + verdict["rule_key"], approval_key=verdict["approval_key"], choice=choice,
       decided_by=by, session_id="s1")


STORE_READS = [
    ("bash", {"command": "cat config", "cwd": "~/.kube"}),
    ("terminal", {"command": "cat config", "workdir": "~/.kube"}),
    ("exec_command", {"cmd": "cat config", "workdir": "~/.kube"}),
]


@pytest.mark.parametrize("tool, args", STORE_READS)
def test_a_store_read_from_the_tool_folder_is_answered_once_by_the_credentials_approvers(store, tool, args):
    _, ctx = make_gate(store)
    r = call(ctx, tool, args)
    assert r["action"] == "approve" and r["once"] is True
    assert r["approvers"] == ["principal"]
    assert "credentials always ask the principal" in r["message"]
    plain = call(ctx, tool, {k: v for k, v in args.items() if k not in ("cwd", "workdir")})
    assert plain["action"] == "approve" and "once" not in plain               # read: ask, from the store's policy
    assert plain["approvers"] == ["principal", "seat:builder"]


def test_a_read_grant_does_not_cover_a_store_read_from_the_tool_folder(store):
    root, _ = store
    _, ctx = make_gate(store)
    r = call(ctx, "terminal", {"command": "cat notes.md"})
    answer(ctx, r, "always")
    [g] = json.loads((root / "state" / "approval-grants.json").read_text())
    assert (g["rule"], g["target"]) == ("read", "terminal")
    assert call(ctx, "terminal", {"command": "cat notes.md"}) is None           # the grant covers a plain read
    r = call(ctx, "terminal", {"command": "cat config", "workdir": "~/.kube"})
    assert r["action"] == "approve" and r["once"] is True                       # but not the store read
    answer(ctx, r, "always")
    assert len(json.loads((root / "state" / "approval-grants.json").read_text())) == 1


def test_observe_mode_still_enforces_a_store_read_from_the_tool_folder(store):
    _, ctx = make_gate(store, mode="observe")
    for tool, args in STORE_READS:
        assert call(ctx, tool, args)["action"] == "approve", (tool, args)
    assert call(ctx, "terminal", {"command": "cat notes.md"}) is None


def test_no_grant_and_call_approvers_read_the_parts():
    c = gate.classify({"tool": "bash", "args": {"command": "cat config", "cwd": "~/.kube"}})
    assert c.category == "read" and gate.no_grant(c) and gate.binds_exact(c)
    table = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:builder"],
                                                    "credentials": ["principal"]}})
    assert gate.call_approvers(c, table) == ["principal"]
    plain = gate.classify({"tool": "bash", "args": {"command": "cat config"}})
    assert not gate.no_grant(plain)
    assert gate.call_approvers(plain, table) == gate.strictest_approvers("read", table)


def test_a_secret_read_that_also_writes_the_store_is_answered_by_the_principal_alone(home):
    """call_approvers reads every NO_GRANT part, so a line that reads a secret and writes the store (credentials, with
    a store_write part) is the principal's alone, as a store write is (D-038), whatever credentials allows."""
    c = gate.classify({"tool": "bash", "args": {"command": "cat ~/.kube/config > ~/.exo/policies/gates.yaml"}})
    assert c.category == "credentials" and any(p.category == "store_write" for p in gate._each_part(c))
    table = gate.normalize_approvals({"approvers": {"credentials": ["principal", "seat:builder"]}})
    assert gate.strictest_approvers("credentials", table) == ["principal", "seat:builder"]
    assert gate.call_approvers(c, table) == ["principal"]
