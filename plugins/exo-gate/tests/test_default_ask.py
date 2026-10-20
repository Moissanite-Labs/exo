"""D-038: the gate is an allowlist. Indirection asks, the store is credentials, everyday work still runs.

The first table is the 2026-10-12 audit's gate table, re-run here against the classifier. The bypass
table adds the indirection forms the audit named. The everyday table is the cost check: plain developer
commands must stay `allow`, or the gate asks so often that people learn to click yes.
"""
import json
import shlex

import pytest

from gate_fixtures import TEMPLATE
from exo import gate
from exo.gate import classify, decide, load_policies

STORE_ME = "~/.exo/principals/me"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)
    return h


def sh(cmd):
    return ("bash", {"command": cmd})


def outcome(action, policies=None):
    return decide(classify(action), policies).outcome


GH = "gh pr comment 3 --body hi"

# (action, category or None for "any", outcome) -- the audit's table, with the outcomes it should have had.
AUDIT = [
    (sh(GH), "github_post", "ask"),
    (sh(f"bash <<'EOF'\n{GH}\nEOF"), None, "ask"),
    (sh("python3 -c \"import subprocess; subprocess.run(['gh','pr','comment','3','--body','hi'])\""), None, "ask"),
    (sh("G=gh; $G pr comment 3 --body hi"), "opaque_exec", "ask"),
    (sh(f"echo 'x' > {STORE_ME}/policies/gates.yaml"), "store_write", "ask"),
    (sh(f"sqlite3 {STORE_ME}/ledger.sqlite 'DROP TRIGGER events_no_delete'"), "store_write", "ask"),
    (("write_file", {"path": "policies/gates.yaml", "content": "x"}), "store_write", "ask"),
    (("write_file", {"path": "state/approval-grants.json", "content": "[]"}), "store_write", "ask"),
    (sh("cat ~/.exo/keys/seat.key"), "credentials", "ask"),
    (sh("rm -rf ~/projects/foo"), "delete", "ask"),
]


@pytest.mark.parametrize("action,category,want", AUDIT, ids=[str(a[1])[:60] for a, _, _ in AUDIT])
def test_audit_table(action, category, want):
    c = classify(action)
    if category:
        assert c.category == category, c
    assert decide(c).outcome == want, c
    assert decide(c, load_policies(TEMPLATE)).outcome == want, c


# A github_post deny in both forms the policy file offers (GATE.md "Known limits").
DENY_GH = {
    "categories": gate.normalize_policies({"gates": {"categories": {"github_post": "deny"}}}),
    "deny-list": gate.normalize_policies({"gates": {"deny": ["github_post"]}}),
}

# (wrapper, what the card says at the cap): the outermost wrapper, not the cap (D-038, amendment for #137).
WRAPPERS = [("bash -c", "bash runs shell commands"), ("sh -c", "sh runs shell commands"),
            ("eval", "eval runs text as a command")]


@pytest.mark.parametrize("policy", sorted(DENY_GH))
@pytest.mark.parametrize("wrapper,capped", WRAPPERS, ids=[w for w, _ in WRAPPERS])
@pytest.mark.parametrize("wrappers,category,want", [(4, "github_post", "deny"), (5, "opaque_exec", "ask")])
def test_shell_inspection_depth_limit(policy, wrapper, capped, wrappers, category, want):
    """Pin the documented boundary without executing the command or sending a post."""
    command = GH
    for _ in range(wrappers):
        command = f"{wrapper} " + shlex.quote(command)
    c = classify(sh(command))
    assert c.category == category, c
    assert "indirection" in c.signals
    assert c.summary == (capped if wrappers > gate._DEPTH else "GitHub PR comment on #3"), c.summary
    assert decide(c, DENY_GH[policy]).outcome == want


def test_a_category_deny_reaches_every_part():
    """A compound line keeps its most severe part, which sets the card. The `deny:` list matches every part (D-075),
    and since #191 so does a `categories:` deny, so the inspected post is denied under either form instead of the
    line asking for the delete (GATE.md "Known limits")."""
    c = classify(sh(f"{GH} && rm -rf build"))
    assert c.category == "delete", c
    assert decide(c, DENY_GH["categories"]).outcome == "deny"
    assert decide(c, DENY_GH["deny-list"]).outcome == "deny"


# Indirection: none of these may run without the principal. Where the gate can see inside, the inner
# category shows through (a heredoc'd gh comment is still a github_post).
BYPASS = [
    f"bash -c '{GH}'",
    "sh -c 'ls'",
    f"cat <<'EOF' | bash\n{GH}\nEOF",
    f"echo '{GH}' | sh",
    f"bash <<< '{GH}'",
    "bash script.sh",
    "sh ./x.sh",
    "zsh -s < cmds.txt",
    "./deploy.sh",
    "python -c 'print(1)'",
    "python3 -c 'import os; os.system(\"gh pr comment 3\")'",
    "node -e \"require('child_process').execSync('gh pr comment 3')\"",
    "perl -e 'system(\"gh pr comment 3\")'",
    "ruby -e 'system(\"gh pr comment 3\")'",
    "osascript -e 'do shell script \"gh pr comment 3\"'",
    f"python3 - <<'EOF'\nimport os\nos.system('{GH}')\nEOF",
    "python script.py",
    "make deploy",
    "npm run deploy",
    "npm test",
    "pnpm run release",
    "yarn run ship",
    "npx some-tool",
    "uvx some-tool",
    "ls | xargs rm",
    "echo x | xargs sh -c 'gh pr comment 3'",
    "find . -name '*.tmp' -exec rm {} \\;",
    "find . -exec cat {} +",
    "`echo gh` pr comment 3 --body hi",
    "$(echo gh) pr comment 3 --body hi",
    "$G pr comment 3",
    "${G} pr comment 3",
    "\"$G\" pr comment 3",
    "f() { gh pr comment 3 --body hi; }; f",
    "function f { ls; }; f",
    "alias g=gh; g pr comment 3",
    "source ./env.sh",
    ". ./env.sh",
    "eval \"$CMD\"",
    f"eval {GH}",
    "trap 'gh pr comment 3' EXIT",
    "awk 'BEGIN{system(\"gh pr comment 3\")}'",
    "git -c alias.x='!gh pr comment 3' x",
    "git config alias.y '!sh -c x'",
    f"echo \"$({GH})\"",
    f"cat <<EOF\n$({GH})\nEOF",
    "sudo -u root rm -rf /srv/x",
    "uv run python -c 'print(1)'",
    "env -S 'gh pr comment 3'",
    "if true; then $CMD; fi",
    "for f in a b; do $f; done",
    "/tmp/tool --run",
]


@pytest.mark.parametrize("cmd", BYPASS)
def test_indirection_never_allows(cmd):
    c = classify(sh(cmd))
    assert decide(c).outcome in ("ask", "deny"), c
    assert decide(c, load_policies(TEMPLATE)).outcome in ("ask", "deny"), c


def test_inner_text_shows_through():
    assert classify(sh(f"bash -c '{GH}'")).category == "github_post"
    assert classify(sh(f"bash <<'EOF'\n{GH}\nEOF")).category == "github_post"
    assert classify(sh(f"echo \"$({GH})\"")).category == "github_post"
    assert classify(sh("python3 -c \"import subprocess; subprocess.run(['gh','pr','comment','3'])\"")).category \
        == "github_post"
    assert classify(sh("find . -exec rm {} \\;")).category == "delete"
    assert classify(sh("ls | xargs rm")).category == "delete"
    c = classify(sh("sh -c 'ls'"))
    assert c.category == "opaque_exec" or "indirection" in c.signals


# The store is credentials: any write asks, every time, principal only.
STORE_WRITES = [
    sh(f"echo x > {STORE_ME}/policies/gates.yaml"),
    sh("echo x >> $EXO_HOME/policies/gates.yaml"),
    sh(f"echo x | tee {STORE_ME}/policies/gates.yaml"),
    sh(f"cp /tmp/g.yaml {STORE_ME}/policies/gates.yaml"),
    sh(f"mv /tmp/g {STORE_ME}/state/approval-grants.json"),
    sh(f"rm {STORE_ME}/state/approval-grants.json"),
    sh("rm -rf ~/.exo"),
    sh(f"sed -i s/ask/act/ {STORE_ME}/policies/gates.yaml"),
    sh(f"sqlite3 {STORE_ME}/ledger.sqlite 'DROP TRIGGER x'"),
    sh("sqlite3 ledger.sqlite 'delete from events'"),
    sh(f"cd {STORE_ME} && echo x > policies/gates.yaml"),
    sh(f"cd {STORE_ME} && rm -rf state"),
    sh("nc -U ~/.exo/run/exod.sock"),
    sh("curl --unix-socket ~/.exo/run/exod.sock http://x/approval"),
    sh("socat - UNIX-CONNECT:$EXO_HOME/run/agent-main.sock"),
    sh("echo 'name = \"x\"' > ~/.exo/seats/builder.toml"),
    sh(f"touch {STORE_ME}/policies/approvals.yaml"),
    sh(f"chmod 666 {STORE_ME}/policies/gates.yaml"),
    sh("exo log append --kind ruling x"),
    ("write_file", {"path": "policies/gates.yaml", "content": "x"}),
    ("write_file", {"path": "state/approval-grants.json", "content": "[]"}),
    ("write_file", {"path": f"{STORE_ME}/policies/approvals.yaml", "content": "x"}),
    ("edit_file", {"path": "$EXO_HOME/policies/gates.yaml", "old_string": "a", "new_string": "b"}),
    ("patch", {"path": f"{STORE_ME}/policies/gates.yaml", "new_string": "x"}),
    ("apply_patch", {"patch": f"*** Begin Patch\n*** Update File: {STORE_ME}/policies/gates.yaml\n@@\n-a\n+b\n"
                              "*** End Patch"}),
    ("write_file", {"path": "~/.exo/seats/builder/seat.toml", "content": "x"}),
    ("write_file", {"path": f"{STORE_ME}/ledger.sqlite", "content": "x"}),
]


@pytest.mark.parametrize("action", STORE_WRITES, ids=lambda a: str(a[1])[:70])
def test_store_writes_always_ask(action):
    c = classify(action)
    assert c.category in ("store_write", "credentials"), c
    for raw in ({}, {"gates": {"categories": {"store_write": "allow"}}},
                {"gates": {"rules": [{"match": {"category": "store_write"}, "decision": "act"}]}},
                {"gates": {"matrix": {"costly": {"private": "allow"}}, "scopes": {"private": ["*"]}}}):
        assert decide(c, gate.normalize_policies(raw)).outcome in ("ask", "deny"), (raw, c)


def test_principal_store_override_is_protected(tmp_path, monkeypatch):
    store = tmp_path / "elsewhere" / "store"
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(store))
    assert classify(("write_file", {"path": str(store / "policies" / "x.yaml")})).category == "store_write"
    assert classify(sh(f"rm -rf {store}/state")).category == "store_write"
    assert classify(("write_file", {"path": str(tmp_path / "elsewhere" / "notes.md")})).category == "local_write"


def test_explicit_store_root_is_protected(tmp_path, home):
    """A store made with `exo init --root` is recorded in config `principals.<id>` and is the live store for that
    id, outside the home: its policies, state, keys and ledger are protected like the home's stores."""
    store = tmp_path / "elsewhere" / "pat"
    (home / ".exo" / "config.json").write_text(json.dumps({"principal": "pat", "principals": {"pat": str(store)}}))
    assert classify(("write_file", {"path": str(store / "policies" / "x.yaml")})).category == "store_write"
    assert classify(sh(f"rm -rf {store}/state")).category == "store_write"
    assert classify(sh(f"cp /tmp/k {store}/keys/seat.pub")).category == "store_write"
    assert gate.is_store_path(str(store / "ledger.sqlite-wal"))
    assert classify(("write_file", {"path": str(store / "nodes" / "work" / "x.md")})).category == "local_write"
    assert classify(("write_file", {"path": str(tmp_path / "elsewhere" / "notes.md")})).category == "local_write"


STORE_READS = [
    sh(f"cat {STORE_ME}/policies/gates.yaml"),
    sh("ls ~/.exo/state"),
    sh(f"grep ask {STORE_ME}/policies/gates.yaml"),
    ("read_file", {"path": f"{STORE_ME}/policies/gates.yaml"}),
]


@pytest.mark.parametrize("action", STORE_READS, ids=lambda a: str(a[1])[:70])
def test_store_reads_still_run(action):
    c = classify(action)
    assert c.category == "read", c
    assert decide(c).outcome == "allow"


def test_store_write_is_principal_only_and_never_granted(tmp_path):
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:helper"],
                                                         "store_write": ["seat:helper"]}})
    assert gate.approvers_for("store_write", approvals) == ["principal"]
    from exo_gate.hook import GrantStore
    gs = GrantStore(tmp_path / "grants.json")
    assert gs.add("store_write", "policies/gates.yaml", "", 30, "principal") is None
    (tmp_path / "grants.json").write_text(json.dumps([{"id": "g_x", "rule": "store_write", "target": "*",
                                                       "binding": "", "expires": 9e12}]))
    assert gs.match("store_write", "policies/gates.yaml", "", False) is None


# Everyday developer work: all of these must stay allow with the default and template policies.
EVERYDAY = [
    "pytest",
    "pytest -q tests/test_x.py",
    "uv run pytest -q",
    "uv run pytest tests -x",
    "python -m pytest",
    "python3 -m pytest -q",
    ".venv/bin/pytest -q",
    "git status",
    "git diff",
    "git diff --stat HEAD~1",
    "git log --oneline -5",
    "git add -A",
    "git commit -m 'fix: x'",
    "git checkout -b feature/x",
    "git switch main",
    "git -C ../repo status",
    "git branch -a",
    "git stash",
    "git fetch origin",
    "ls",
    "ls -la src",
    "rg foo src",
    "rg -n 'def x' | head -20",
    "grep -rn TODO src",
    "cat README.md",
    "head -50 src/a.py",
    "tail -n 20 build.log",
    "wc -l src/*.py",
    "find . -name '*.py' | wc -l",
    "sed -n 1,40p src/a.py",
    "awk '{print $1}' data.txt",
    "jq .name package.json",
    "echo hello",
    "pwd",
    "which python",
    "mkdir -p build/out",
    "touch a.txt",
    "cp a.txt b.txt",
    "mv a.txt c.txt",
    "diff a.txt b.txt",
    "sort f.txt | uniq -c",
    "cd src && ls",
    "ruff check src",
    "uv sync",
    "uv pip install -e .",
    "pip install requests",
    "npm install",
    "npm ci",
    "python --version",
    "node --version",
    "cargo build",
    "cargo test",
    "go test ./...",
    "gh pr list",
    "gh pr view 3",
    "curl -s https://example.com",
    "docker ps",
    "du -sh .",
    "env FOO=1 ls",
    "export FOO=bar",
    "set -euo pipefail; ls",
    "git diff > /tmp/patch.diff",
    "echo note > notes.txt",
    "cat <<'EOF' > notes.md\nhello\nEOF",
    "python -m json.tool x.json",
    "ps aux | grep python",
    "echo $HOME",
    "if [ -f a.txt ]; then cat a.txt; fi",
    "for f in *.md; do wc -l \"$f\"; done",
]


@pytest.mark.parametrize("cmd", EVERYDAY)
def test_everyday_commands_stay_allow(cmd):
    c = classify(sh(cmd))
    assert decide(c).outcome == "allow", c
    assert decide(c, load_policies(TEMPLATE)).outcome == "allow", c
