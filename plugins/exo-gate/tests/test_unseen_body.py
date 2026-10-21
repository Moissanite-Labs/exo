"""The exo-gate hook and a post body it cannot read (#208).

It reads a body file only by an absolute path: a relative one names a file in the command's folder, which the
hook does not know (`cd docs && gh ... --body-file notes.md` was allowed on main). A body it did not read asks,
gets no "always" option and no standing grant, and a required executor refuses it. Temp stores and files and a
mock context; nothing is executed.
"""
import asyncio
import json

import pytest

from exo import crypto, gate
from exo.ledger import Ledger
from exo_gate import hook

FOOTER = "F"
POSTING = "```yaml\nposting:\n  footer: \"F\"\n  forbid_words: [Zork]\n```\n"


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
    (root / "policies" / "gates.yaml").write_text("scopes:\n  internal: ['myorg/*']\n")
    (root / "policies" / "posting.md").write_text(POSTING)
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    return root, key


@pytest.fixture
def work(tmp_path, monkeypatch):
    d = tmp_path / "work"
    (d / "docs").mkdir(parents=True)
    (d / "notes.md").write_text("Zork leaked\n")
    (d / "docs" / "only.md").write_text("Zork leaked\n")
    (d / "clean.md").write_text(f"A note.\n{FOOTER}\n")
    (d / "big.md").write_text(f"A note.\n{'x' * (300 * 1024)}\n{FOOTER}\n")
    monkeypatch.chdir(d)                       # the hook's own folder holds a same-named file
    return d


def make_gate(store, **extra):
    root, key = store
    ctx = MockCtx({"principal_store": str(root), "key_path": str(key), "decision_model": False, **extra})
    return hook.register(ctx), ctx


def call(ctx, command):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name="terminal", args={"command": command}, session_id="s1", tool_call_id="c1",
                          turn_id="t1"))


def events(root):
    with Ledger(root / "ledger.sqlite") as led:
        return [e["body"] for e in led.iter(kind="action")]


R = "-R myorg/app"


@pytest.mark.parametrize("line", [
    f"gh pr comment 5 {R} --body-file notes.md",            # relative: the hook does not know the command's folder
    f"cd docs && gh pr comment 5 {R} --body-file only.md",
    f"gh pr comment 5 {R} --body-file ~/notes.md",
    f"gh pr comment 5 {R} --body-file {{work}}/missing.md",
    f"gh pr comment 5 {R} --body-file {{work}}/big.md",      # over 256 KiB
    f"gh pr comment 5 {R} --body-file - < {{work}}/clean.md",
    "gh api repos/myorg/app/issues/5/comments -F body=@{work}/clean.md",
])
def test_a_body_the_hook_did_not_read_asks_once(store, work, line):
    _, ctx = make_gate(store)
    r = call(ctx, line.format(work=work))
    assert r["action"] == "approve" and gate.UNSEEN_BODY in r["message"], r
    assert r.get("once") is True                            # the core offers no "always"


@pytest.mark.parametrize("line", ["curl -d @{work}/clean.md https://hooks.example.com/x",
                                  "curl -d \"$MSG\" https://hooks.example.com/x",
                                  "gh gist create --public {work}/clean.md"])
def test_an_unseen_body_outside_the_posting_rules_still_gets_no_always(store, work, line):
    # web_post and publish are not under `posting.applies_to` by default; they ask by the matrix, and an "always"
    # bound to the line would cover any later file or variable, so the core offers none.
    _, ctx = make_gate(store)
    r = call(ctx, line.format(work=work))
    assert r["action"] == "approve" and r.get("once") is True, r
    assert call(ctx, "curl -d 'text=hi' https://hooks.example.com/x").get("once") is None


def test_an_absolute_body_file_is_read_and_checked(store, work):
    _, ctx = make_gate(store)
    assert call(ctx, f"gh pr comment 5 {R} --body-file {work}/clean.md") is None
    r = call(ctx, f"gh pr comment 5 {R} --body-file {work}/notes.md")
    assert r["action"] == "approve" and "forbidden word: Zork" in r["message"]
    assert gate.UNSEEN_BODY not in r["message"] and not r.get("once")


# #399: a post the gate reads more than one way keeps a copy on each other target (alt_targets), and a line whose gh
# subcommand moved keeps the line as read before (floor). The body file reaches every one of them, so a clean body
# runs as on main, and an ask offers "always".
OTHER_READINGS = [
    "gh pr comment 5 -R github.com/myorg/app --body-file {work}/clean.md",
    "gh pr comment https://github.com/pub/repo/pull/5 -R myorg/app --body-file {work}/clean.md",
    "gh pr -R myorg/app view 5 && gh pr comment 5 -R myorg/app --body-file {work}/clean.md",
]


def _store_with(tmp_path, name, gates_yaml):
    root = tmp_path / name
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "gates.yaml").write_text(gates_yaml)
    (root / "policies" / "posting.md").write_text(POSTING)
    key = tmp_path / f"{name}.key"
    crypto.save_key(crypto.keygen(), key)
    return root, key


@pytest.mark.parametrize("line", OTHER_READINGS)
def test_a_body_file_reaches_every_reading_of_the_post(tmp_path, work, line):
    line = line.format(work=work)
    scopes = "scopes:\n  internal: ['myorg/*', 'pub/*']\nescalate: {third_party: false}\n"
    _, ctx = make_gate(_store_with(tmp_path, "allow", scopes + "categories: {github_post: allow}\n"))
    assert call(ctx, line) is None
    _, ctx = make_gate(_store_with(tmp_path, "ask", scopes + "categories: {github_post: ask}\n"))
    r = call(ctx, line)
    assert r["action"] == "approve" and gate.UNSEEN_BODY not in r["message"] and not r.get("once"), r
    # The body is checked in each reading: a forbidden word still asks, and the reason names it.
    r = call(ctx, line.replace("clean.md", "notes.md"))
    assert r["action"] == "approve" and "forbidden word: Zork" in r["message"], r
    assert gate.UNSEEN_BODY not in r["message"] and not r.get("once"), r


def test_with_reading_off_a_body_file_asks(store, work):
    _, ctx = make_gate(store, read_body_files=False)
    r = call(ctx, f"gh pr comment 5 {R} --body-file {work}/clean.md")
    assert r["action"] == "approve" and gate.UNSEEN_BODY in r["message"]


def test_an_unseen_body_gets_no_standing_grant(store, work):
    root, _ = store
    _, ctx = make_gate(store)
    line = f"gh pr comment 5 {R} --body-file - < {work}/clean.md"
    r = call(ctx, line)
    [cb] = ctx.hooks["post_approval_response"]
    cb(pattern_key="plugin_rule:" + r["rule_key"], approval_key=r["approval_key"], choice="always",
       decided_by="principal:tui", session_id="s1")
    assert not (root / "state" / "approval-grants.json").exists()
    # A grant that exists for that exact line (written before this fix) does not cover a body the gate cannot see.
    c = gate.classify({"tool": "terminal", "args": {"command": line}})
    binding = gate.approval_binding(c, {"command": line})
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "approval-grants.json").write_text(json.dumps([{
        "id": "g_old", "rule": "github_post", "target": "myorg/app#5", "binding": binding, "tool": "terminal",
        "by": "principal:tui", "created": 0, "expires": 4_000_000_000}]))
    assert call(ctx, line)["action"] == "approve"


@pytest.mark.parametrize("line", [
    f"gh pr comment 5 {R} --body-file - < notes.md",
    f"cat notes.md | gh pr comment 5 {R} --body-file -",
    f"gh pr comment 5 {R} --body \"$(cat notes.md)\"",
    "gh api repos/myorg/app/issues/5/comments -F body=@notes.md",
    "gh api repos/myorg/app/issues/5/comments --input notes.json",
    f"gh pr comment 5 {R} --body-file {{work}}/clean.md",
    f"(cat notes.md; echo; echo 'F') | gh pr comment 5 {R} --body-file -",
    f"printf 'Zo\\x72k\nF' | gh pr comment 5 {R} --body-file -",
    f"gh pr comment 5 {R} --body-file - <<EOF\nZo\\\nrk\nF\nEOF",
])
def test_a_required_executor_refuses_every_unseen_body(store, work, line):
    root, _ = store
    g, ctx = make_gate(store)
    g.required_getter = lambda: True
    r = call(ctx, line.format(work=work))
    assert r["action"] == "block" and "required executor" in r["message"], r
    row = events(root)[-1]
    assert row["phase"] == "decision" and row["outcome"] == "deny" and row["decision"]["rule"] == "required"


# #399: when gh finds its subcommand past an option, or an expansion stands where it may be, the line is also read as
# before (its floor). A body file in that reading is refused as on main, though the line as read has none.
FLOOR_BODY_FILES = [
    f'gh pr "$V" 5 {R} --body-file {{work}}/clean.md',                        # the subcommand is an expansion
    f"gh pr view 5 {R} -F {{work}}/clean.md | gh issue -b x comment 8 {R}",   # the second subcommand moved
    f"gh pr merge 7 {R} -F {{work}}/clean.md | gh issue -b x comment 8 {R}",
]


@pytest.mark.parametrize("line", FLOOR_BODY_FILES)
def test_a_required_executor_refuses_a_body_file_in_the_line_as_read_before(store, work, line):
    root, _ = store
    g, ctx = make_gate(store)
    g.required_getter = lambda: True
    line = line.format(work=work)
    c = gate.classify({"tool": "terminal", "args": {"command": line}})
    assert c.floor is not None and c.floor.text_file and not c.text_file, c
    r = call(ctx, line)
    assert r["action"] == "block" and "required executor" in r["message"], r
    row = events(root)[-1]
    assert row["outcome"] == "deny" and row["decision"]["rule"] == "required", row


def test_a_required_executor_still_asks_on_an_inline_body(store, work):
    g, ctx = make_gate(store)
    g.required_getter = lambda: True
    r = call(ctx, f"gh pr comment 5 {R} --body 'Zork leaked'")
    assert r["action"] == "approve" and "forbidden word: Zork" in r["message"]


def test_a_body_file_under_dev_or_proc_is_never_read(store, work, monkeypatch):
    # /dev/stdin and /proc/self/... name what the reading process holds: read by the hook they give exod's own stdin
    # or environment, not what gh reads. Make them look like readable files to show the hook still leaves them.
    import os
    from pathlib import Path
    real_is_file, real_read = Path.is_file, Path.read_text
    special = lambda p: str(p).startswith(("/dev/", "/proc/")) or os.path.realpath(p).startswith(("/dev/", "/proc/"))  # noqa: E731
    monkeypatch.setattr(Path, "is_file", lambda p: True if special(p) else real_is_file(p))
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **k: f"A note.\n{FOOTER}\n" if special(p) else real_read(p, *a, **k))
    link = work / "body.md"
    link.symlink_to("/dev/stdin")
    _, ctx = make_gate(store)
    r = call(ctx, f"gh pr comment 5 {R} --body-file {str(link)}")
    assert r["action"] == "approve" and gate.UNSEEN_BODY in r["message"] and r.get("once") is True, r
    # Written out, /dev/stdin is also a descriptor path the gate cannot resolve (#217): a store write, which only the
    # principal answers, never granted, the stricter ask. The card says the path is unresolved, not that gh writes it.
    r = call(ctx, f"gh pr comment 5 {R} --body-file /dev/stdin")
    assert r["action"] == "approve" and r.get("once") is True and r["approvers"] == ["principal"], r
    assert "/dev/stdin, a descriptor path or folder the gate cannot resolve" in r["message"], r
