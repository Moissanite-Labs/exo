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
# runs as on main, and an ask offers "always". A copy is the post itself, not another part of the line (#343).
OTHER_READINGS = [
    "gh pr comment 5 -R github.com/myorg/app --body-file {work}/clean.md",
    "gh pr comment https://github.com/pub/repo/pull/5 -R myorg/app --body-file {work}/clean.md",
    "gh pr -R myorg/app comment 5 --body-file {work}/clean.md",
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


# #343: the hook reads a body file before the line runs. Another part of the line may change the file before gh reads
# it (a writer before the post, one beside it in a pipe or in the background, a redirection onto it), or run gh where
# the path names another file (ssh). So on a line where the gate finds any other part the file stays unread: the post
# asks once, and the card says why in place of the hint to pass an absolute path.
P = "gh pr comment 5 -R myorg/app --body-file {work}/clean.md"
COMPOUND = [
    # the issue's rows
    "cp {work}/notes.md {work}/clean.md && " + P,
    "mv {work}/notes.md {work}/clean.md && " + P,
    "cat {work}/notes.md > {work}/clean.md; " + P,
    "echo Zork >> {work}/clean.md && " + P,
    "ln -sf {work}/notes.md {work}/clean.md && " + P,
    "echo Zork | tee {work}/clean.md && " + P,
    # any other part, writer or not
    "true && " + P,
    "cd /tmp && " + P,
    P + " || true",
    # a part after the post that runs beside it: a pipe, the background
    P + " | tee {work}/clean.md",
    P + " & cp {work}/notes.md {work}/clean.md",
    # a group or a subshell with another command
    "{{ cp {work}/notes.md {work}/clean.md; " + P + "; }}",
    "(cp {work}/notes.md {work}/clean.md; " + P + ")",
    # a redirection, an assignment, a wrapper the gate does not see through, a shell, an ssh hop
    P + " 2> {work}/clean.md",
    P + " > {work}/out.txt",
    "GH_EDITOR=x " + P,
    "setsid " + P,
    "taskset 1 " + P,
    "bash -c '" + P + "'",
    "ssh host " + P,
    "python3 -c \"import shutil, subprocess; shutil.copy('{work}/notes.md', '{work}/clean.md'); "
    "subprocess.run(['gh', 'pr', 'comment', '5', '-R', 'myorg/app', '--body-file', '{work}/clean.md'])\"",
    # #399's other readings: a copy on another target, the line as read before
    "cp {work}/notes.md {work}/clean.md && gh pr comment 5 -R github.com/myorg/app --body-file {work}/clean.md",
    "cp {work}/notes.md {work}/clean.md && gh pr comment https://github.com/pub/repo/pull/5 -R myorg/app "
    "--body-file {work}/clean.md",
    "cp {work}/notes.md {work}/clean.md && gh pr -R myorg/app comment 5 --body-file {work}/clean.md",
    "gh pr -R myorg/app view 5 && " + P,
]


@pytest.mark.parametrize("line", COMPOUND)
def test_a_body_file_on_a_compound_line_asks_once(store, work, line):
    _, ctx = make_gate(store)
    r = call(ctx, line.format(work=work))
    assert r["action"] == "approve" and r.get("once") is True, r
    assert gate.UNSEEN_ON_LINE in r["message"] and gate.UNSEEN_BODY not in r["message"], r


def test_code_that_posts_a_body_file_asks_once(store, work):
    # The script around the call is another part: it can rewrite the file first (#343). On main the hook read the
    # file, still clean, and the ask offered "always".
    code = (f"import shutil, subprocess\nshutil.copy('{work}/notes.md', '{work}/clean.md')\n"
            f"subprocess.run(['gh', 'pr', 'comment', '5', '-R', 'myorg/app', '--body-file', '{work}/clean.md'])")
    _, ctx = make_gate(store)
    [cb] = ctx.hooks["pre_tool_call"]
    r = asyncio.run(cb(tool_name="execute_code", args={"code": code}, session_id="s1", tool_call_id="c1",
                       turn_id="t1"))
    assert r["action"] == "approve" and r.get("once") is True, r
    assert gate.UNSEEN_ON_LINE in r["message"] and gate.UNSEEN_BODY not in r["message"], r


def _has_folder_read(c):
    return any(p.category == "credentials" for x in gate.readings(c) for p in gate._each_part(x))


# The readings and parts main added since keep the rule (#343): a store read from the tool's own folder (#311), with
# the call as read before #311 (`also`) beside it; a sudo write that took the lead from a read (#454, `displaced`); an
# ssh that runs the line it is fed (#316). On main the hook read the file in all but the second, and ran both sudo rows.
NEWER_READINGS = [
    ("terminal", {"command": "cp config {work}/clean.md && " + P, "workdir": "~/.kube"}, _has_folder_read),
    ("bash", {"command": "cat ../.kube/config > {work}/clean.md; " + P, "cwd": "~/.kube"}, lambda c: c.also),
    ("terminal", {"command": "echo $(sudo cat {work}/notes.md > {work}/clean.md) && " + P},
     lambda c: any(p.displaced for p in gate._each_part(c))),
    ("terminal", {"command": "x=$(sudo gh pr view 1 -R o/r > /tmp/f); " + P},
     lambda c: any(p.displaced for p in gate._each_part(c))),
    ("terminal", {"command": "echo '" + P + "' | ssh prod-db"},
     lambda c: any("runs its stdin" in p.summary for p in gate._each_part(c))),
    ("terminal", {"command": "echo '" + P + "' | ssh prod-db ssh inner"},
     lambda c: any("passes its stdin" in p.summary for p in gate._each_part(c))),
]


@pytest.mark.parametrize("tool, args, kind", NEWER_READINGS)
def test_a_body_file_stays_unread_under_each_newer_reading(store, work, tmp_path, monkeypatch, tool, args, kind):
    home = tmp_path / "home"
    (home / ".kube").mkdir(parents=True)
    (home / ".kube" / "config").write_text("apiVersion: v1\n")
    monkeypatch.setenv("HOME", str(home))
    args = {k: v.format(work=work) for k, v in args.items()}
    assert kind(gate.classify({"tool": tool, "args": args})), args
    _, ctx = make_gate(store)
    [cb] = ctx.hooks["pre_tool_call"]
    r = asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1"))
    assert r["action"] == "approve" and r.get("once") is True, r
    assert gate.UNSEEN_ON_LINE in r["message"] and gate.UNSEEN_BODY not in r["message"], r


# D-074 (#125): a line where the home's config.json counts is also read as before D-074 (`ordinary`), and the hook
# reads that reading too (gate.readings): the body file stays unread, the post asks once and only the principal answers,
# as on main for the same line with another store write, and a required executor refuses the line.
CONFIG_WRITES = ["cp /tmp/x ~/.exo/config.json && " + P, "cp /tmp/x ~/.exo/policies/gates.yaml && " + P]


@pytest.mark.parametrize("line", CONFIG_WRITES)
def test_a_body_file_beside_a_config_json_write_stays_unread(store, work, tmp_path, monkeypatch, line):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EXO_HOME", raising=False)
    line = line.format(work=work)
    c = gate.classify({"tool": "terminal", "args": {"command": line}})
    assert c.category == "store_write", c
    if "config.json" in line:
        assert c.ordinary is not None and c.ordinary in gate.readings(c), c
        assert c.ordinary.category == "github_post" and c.ordinary.text_file, c.ordinary   # main's reading
        assert {id(p) for p in hook._every_part(c.ordinary)} <= {id(p) for p in hook._every_part(c)}
    g, ctx = make_gate(store)
    r = call(ctx, line)
    assert r["action"] == "approve" and r.get("once") is True and r["approvers"] == ["principal"], r
    assert gate.UNSEEN_ON_LINE in r["message"] and gate.UNSEEN_BODY not in r["message"], r
    g.required_getter = lambda: True
    r = call(ctx, line)
    assert r["action"] == "block" and "required executor" in r["message"], r


def test_the_card_says_the_file_was_not_read(store, work):
    # The file is not read, so the card names no word it holds now: gh may post another text. It says the file was
    # not read and why. Alone on its line, the same post is read and checked. A second post's file is left unread for
    # the same reason, so its note says so too, named after it; a relative path keeps the plain note and its hint.
    _, ctx = make_gate(store)
    r = call(ctx, f"true && gh pr comment 5 {R} --body-file {work}/notes.md")
    assert gate.UNSEEN_ON_LINE in r["message"] and "forbidden word" not in r["message"], r
    r = call(ctx, f"gh pr comment 5 {R} --body-file {work}/notes.md")
    assert "forbidden word: Zork" in r["message"] and gate.UNSEEN_ON_LINE not in r["message"], r
    r = call(ctx, f"gh pr comment 5 {R} --body-file {work}/notes.md && gh pr comment 6 {R} --body-file {work}/clean.md")
    assert r.get("once") is True and gate.UNSEEN_ON_LINE in r["message"], r
    assert f"GitHub PR comment on myorg/app#6: {gate.UNSEEN_ON_LINE}" in r["message"], r
    assert gate.UNSEEN_BODY not in r["message"], r
    r = call(ctx, f"gh pr comment 5 {R} --body-file {work}/notes.md && gh pr comment 6 {R} --body-file clean.md")
    assert f"GitHub PR comment on myorg/app#6: {gate.UNSEEN_BODY}" in r["message"], r


@pytest.mark.parametrize("line", [
    "cd /tmp && gh pr create -R myorg/app --title \"$T\" --body-file {work}/clean.md",
    "cd /tmp && gh pr comment 5 -R myorg/app --body-file {work}/clean.md --body-file {work}/clean.md",
    # a writer in a ${..} word on the post's own command, which bash runs before gh (#143, #158): the word is
    # another part, and a dynamic word on the post
    P + " ${{x:-$(cp {work}/notes.md {work}/clean.md)}}",
    # the same post and word in an `env -S` text (#591) and a sudo -s or -i shell's stdin (#491), which main
    # f28934a3 reads beside this PR's two readings (merge of f28934a3): both notes stay on the card
    "env -S '" + P + " ${{x:-$(cp {work}/notes.md {work}/clean.md)}}'",
    "env --split='" + P + " ${{x:-$(cp {work}/notes.md {work}/clean.md)}}'",
    "sudo -s <<< '" + P + " ${{x:-$(cp {work}/notes.md {work}/clean.md)}}'",
    "sudo -i <<'END'\n" + P + " ${{x:-$(cp {work}/notes.md {work}/clean.md)}}\nEND",
])
def test_a_post_also_unseen_otherwise_keeps_the_plain_note(store, work, line):
    # The file is left unread for the line, and the post is also unseen for a reason it keeps alone on its line (a
    # title from a variable, a repeated option). The card gives both notes, so it does not ask for the post alone and
    # then for the text inline (#343).
    _, ctx = make_gate(store)
    r = call(ctx, line.format(work=work))
    assert r.get("once") is True and gate.UNSEEN_ON_LINE in r["message"] and gate.UNSEEN_BODY in r["message"], r


@pytest.mark.parametrize("line", [
    "nice -n 5 " + P, "exec " + P, "sudo " + P, "env -u X " + P, "timeout 60 " + P, "command " + P, "nohup " + P,
    P + " 2>/dev/null", P + " >/dev/null 2>&1", "(" + P + ")", P + " &",
    "NO_COLOR=1 " + P, "env NO_COLOR=1 " + P,      # a variable that does not change what runs
])
def test_a_post_alone_under_a_wrapper_the_gate_sees_through_is_read(store, work, line):
    # These add no part, so the post is alone on its line and its file is read and checked (#343).
    _, ctx = make_gate(store)
    assert call(ctx, line.format(work=work)) is None
    r = call(ctx, line.replace("clean.md", "notes.md").format(work=work))
    assert "forbidden word: Zork" in r["message"] and gate.UNSEEN_BODY not in r["message"], r
    assert gate.UNSEEN_ON_LINE not in r["message"] and not r.get("once"), r


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


@pytest.mark.parametrize("line", ["sudo gh pr view 1 -R o/r -F {work}/clean.md > /tmp/f",
                                  "ssh h 'sudo gh pr view 1 -R o/r -F {work}/clean.md > /tmp/f'",
                                  'sudo gh pr "$V" 5 -F {work}/clean.md > /tmp/f'])
def test_a_required_executor_refuses_the_body_file_of_a_displaced_part(store, work, line):
    # #454: a sudo write leads the line in place of the gh command, which keeps the body file. Main refused the line.
    # With #399 only the line as read before may be a sudo write line, as the last one is.
    g, ctx = make_gate(store)
    g.required_getter = lambda: True
    r = call(ctx, line.format(work=work))
    assert r["action"] == "block" and "required executor" in r["message"], r


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


@pytest.mark.parametrize("line, notes", [
    (f"gh pr comment 6 {R} --body-file {{work}}/a\0b", [gate.UNSEEN_BODY]),
    # the held clean file's post gets UNSEEN_ON_LINE; the NUL path is never held, so its post keeps UNSEEN_BODY (#593)
    (P + f" && gh pr comment 6 {R} --body-file {{work}}/a\0b", [gate.UNSEEN_ON_LINE, gate.UNSEEN_BODY]),
    (f"gh pr comment 6 {R} --body-file {{work}}/a\0b; rm -rf {{work}}/x", [gate.UNSEEN_BODY]),   # no file to hold
    (f"rm -rf {{work}}/x; gh pr comment 6 {R} --body-file {{work}}/a\0b", [gate.UNSEEN_BODY]),
])
def test_a_nul_in_a_path_leaves_the_body_unseen_and_keeps_once(store, work, line, notes):
    # realpath and lstat raise ValueError on an embedded NUL. The hook used to fall into its error card there, which
    # dropped "once" and offered "always" on plugin_rule:exo-gate:error:terminal (abu-aska, #545).
    _, ctx = make_gate(store)
    r = call(ctx, line.format(work=work))
    assert r["action"] == "approve" and r.get("once") is True and all(n in r["message"] for n in notes), r
    assert "could not classify" not in r["message"], r


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
