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


# --- the folder never takes the line's place (exo-36's review of #452) ---------------------------------------------

FORCE_IN_KEYS = [
    ("terminal", {"command": "git push --force origin main ; cat *", "workdir": "~/.exo/keys"}),
    ("exec_command", {"cmd": "git push --force origin main ; cat *", "workdir": "~/.exo/keys"}),
    ("bash", {"command": "git push --force origin main ; cat *", "workdir": "~/.exo/keys"}),
    ("bash", {"command": "git push --force origin main ; cat *", "cwd": "~/.exo/keys"}),
    ("terminal", {"command": "tar czf /tmp/o.tgz . ; git push --force origin main", "workdir": "~/.exo/run"}),
    ("exec_command", {"cmd": "cat * && git push --force origin main", "workdir": "~/.claude/projects"}),
]


def _seat_answers_credentials(tmp_path, home, monkeypatch, gates: str = ""):
    """Every relative word in ~/.exo/keys, ~/.exo/run and ~/.claude/projects is a store hit (#209). approvals.yaml
    delegates only credentials to a seat."""
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for d in (".exo/keys", ".exo/run", ".claude/projects/p"):
        (home / d).mkdir(parents=True, exist_ok=True)
    root = tmp_path / "store2"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "gates.yaml").write_text(gates)
    (root / "policies" / "approvals.yaml").write_text(
        "approvers:\n  default: [principal]\n  credentials: [principal, \"seat:builder\"]\n")
    key = tmp_path / "gate2.key"
    crypto.save_key(crypto.keygen(), key)
    return root, key


@pytest.mark.parametrize("tool, args", FORCE_IN_KEYS)
def test_a_force_push_from_a_store_folder_keeps_the_principals_answer(tmp_path, home, monkeypatch, tool, args):
    """A `workdir` made the whole call credentials, so a seat that answers credentials could answer the force push.
    Main did so with `cwd` too. The line keeps force_push, and the approvers are those who may answer both."""
    _, ctx = make_gate(_seat_answers_credentials(tmp_path, home, monkeypatch))
    r = call(ctx, tool, args)
    assert r["action"] == "approve" and r["once"] is True
    assert r["approvers"] == ["principal"]


@pytest.mark.parametrize("tool, args", FORCE_IN_KEYS)
def test_a_force_push_from_a_store_folder_keeps_its_matrix_deny(tmp_path, home, monkeypatch, tool, args):
    gates = "matrix:\n  irreversible: {public: deny, internal: deny}\n  costly: {public: deny}\n"
    _, ctx = make_gate(_seat_answers_credentials(tmp_path, home, monkeypatch, gates))
    assert call(ctx, tool, args)["action"] == "block"


# --- the folder's part weighs as the cd form's (#468) --------------------------------------------------------------

WEIGHED = [
    ("bash", {"command": "cat *; touch ~/.exo/state/x; git push --force origin main", "cwd": "~/.kube"}),
    ("terminal", {"command": "cat *; touch ~/.exo/state/x; rm -rf build", "workdir": "~/.ssh"}),
    ("exec_command", {"cmd": "cat *; echo $(rm -rf x)", "workdir": "~/.kube"}),
]


@pytest.mark.parametrize("gates", [
    "matrix:\n  irreversible: {private: deny}\n",
    "rules:\n  - match: {category: credentials, reversibility: irreversible}\n    decision: deny\n",
], ids=["matrix", "rule"])
@pytest.mark.parametrize("tool, args", WEIGHED)
def test_an_irreversible_deny_reaches_the_folders_part(tmp_path, home, monkeypatch, gates, tool, args):
    """The store read from the folder weighs as its `cd` form's does: the line's irreversible command is a
    credentials part beside it. So these are blocked as `cd ~/.kube && ...` is, where they only asked before."""
    _, ctx = make_gate(_seat_answers_credentials(tmp_path, home, monkeypatch, gates))
    assert call(ctx, tool, args)["action"] == "block"
    plain = {k: v for k, v in args.items() if k not in ("cwd", "workdir")}
    cd = {"command": f"cd {args.get('cwd') or args.get('workdir')} && {plain.get('command') or plain.get('cmd')}"}
    assert call(ctx, "bash", cd)["action"] == "block"


# --- with #399's readings (#439) -----------------------------------------------------------------------------------
# A gh line whose subcommand gh finds past an option is also read as before #399 (its floor), in the call's own folder
# too. The hook reads every reading (gate.readings): the floor, the reading from before #311 and both at once.

FLOOR_STORE_WRITES = [   # the line as read before #399 writes the store; the folder adds no part
    ("bash", {"command": "gh --repo pub/x release download v1 -D ~/.exo/state", "cwd": "~/proj"}),
    ("terminal", {"command": "gh -R pub/x run download 5 -D ~/.exo/policies", "workdir": "~/proj"}),
    ("exec_command", {"cmd": "gh -R pub/x release download v1 -O ~/.exo/update/allowed_signers --clobber",
                      "workdir": "~/proj"}),
]


@pytest.mark.parametrize("tool, args", FLOOR_STORE_WRITES + [
    ("terminal", {"command": "cat config ; gh -R pub/x run download 5 -D ~/.exo/policies", "workdir": "~/.kube"}),
])
def test_a_policy_pattern_that_does_not_compile_refuses_the_policy_and_the_floor_still_asks(tmp_path, home,
                                                                                            monkeypatch, tool, args):
    """#466: the loader refuses a policy whose pattern does not compile and the hook decides on the defaults.
    The floors stand without the policy, so the store write of each reading and the credentials part still ask
    the principal alone, once, in both modes -- on a real store_write ask, not the plain error card."""
    root, key = _seat_answers_credentials(tmp_path, home, monkeypatch, "deny:\n  - 'command:^(rm|shred'\n")
    for extra in ({}, {"mode": "observe"}):
        _, ctx = make_gate((root, key), **extra)
        r = call(ctx, tool, args)
        assert r["rule_key"].startswith("exo-gate:store_write:"), (args, extra, r)
        assert (r.get("approvers"), r.get("once")) == (["principal"], True), (args, extra, r)
        assert "policy error, defaults used" in r["message"], (args, extra, r)


@pytest.mark.parametrize("tool, args", [
    ("bash", {"command": "cat config && gh pr -R pub/repo comment 5 -b x", "cwd": "~/.kube"}),
    ("terminal", {"command": "cat config && gh pr -R pub/repo comment 5 -b x", "workdir": "~/.kube"}),
    ("terminal", {"command": "gh pr -R pub/repo comment 5 -b x", "workdir": "~/.kube"}),
])
def test_a_seat_that_answers_posts_and_credentials_does_not_answer_what_was_a_read(tmp_path, home, monkeypatch, tool,
                                                                                  args):
    root, key = _seat_answers_credentials(tmp_path, home, monkeypatch, "categories: {read: ask, github_post: ask}\n")
    (root / "policies" / "approvals.yaml").write_text(
        "approvers:\n  default: [principal]\n  github_post: [principal, \"seat:builder\"]\n"
        "  credentials: [principal, \"seat:builder\"]\n")
    _, ctx = make_gate((root, key))
    r = call(ctx, tool, args)
    assert r["action"] == "approve" and r["approvers"] == ["principal"], r


@pytest.mark.parametrize("tool, folder", [("bash", {"cwd": "~/.ssh"}), ("terminal", {"workdir": "~/.kube"})])
def test_a_required_executor_refuses_a_body_file_only_the_floor_has_in_a_folder(tmp_path, home, monkeypatch, store,
                                                                                tool, folder):
    body = tmp_path / "clean.md"
    body.write_text("hello\n")
    g, ctx = make_gate(store)
    g.required_getter = lambda: True
    args = {"command": f'gh pr "$V" 5 -R pub/repo -F {body}', **folder}
    c = gate.classify({"tool": tool, "args": args})
    assert c.floor is not None and c.floor.text_file and not c.text_file, c
    r = call(ctx, tool, args)
    assert r["action"] == "block" and "required executor" in r["message"], r
