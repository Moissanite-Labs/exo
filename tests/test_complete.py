"""D-037: the completion engine. Ranking, argument slots, @ files, typo help, history ghost text, guidance rules."""
from __future__ import annotations

import os
import subprocess

import pytest
from exo import complete as cm

DESC = {"compact": "summarize memory", "context": "what the next call carries", "handoff": "write a handoff note",
        "model": "pick the model", "providers": "set up keys", "resume": "pick a thread", "tutorial": "tips on or off",
        "trace": "how the harness did", "grants": "standing grants", "default": "default model", "help": "list commands"}
CAT = cm.build_catalog(DESC)


def labels(sug):
    return [i.value for i in sug.items]


# ---------------------------------------------------------------- ranking
def test_score_orders_exact_prefix_word_contains_subsequence():
    s = cm.score
    assert s("model", "model") == 0
    assert s("mod", "model") < s("o", "foo-bar") < s("del", "model") < s("mdl", "model")
    assert s("b", "foo-bar") < s("a", "foo-bar")  # a word start beats the middle of a word
    assert s("cmp", "compact") is not None and s("zz", "compact") is None


def test_slash_alone_lists_every_command_in_table_order():
    sug = cm.suggest("/", CAT)
    assert sug.mode == "command" and labels(sug) == list(DESC)


def test_fuzzy_filter_prefers_prefix_then_subsequence():
    assert labels(cm.suggest("/co", CAT))[:2] == ["compact", "context"]
    assert labels(cm.suggest("/cmp", CAT)) == ["compact"]
    assert labels(cm.suggest("/hlp", CAT)) == ["help"]
    assert cm.suggest("/zzzz", CAT).items == []


def test_description_words_find_a_command_too():
    assert "grants" in labels(cm.suggest("/standing", CAT))


def test_complete_text_adds_space_only_when_something_can_follow():
    sug = cm.suggest("/han", CAT)
    assert cm.complete_text("/han", sug, sug.items[0]) == "/handoff "
    sug = cm.suggest("/tutorial o", CAT)
    off = next(i for i in sug.items if i.value == "off")
    assert cm.complete_text("/tutorial o", sug, off) == "/tutorial off"  # no second argument


# ---------------------------------------------------------------- arguments
def test_model_arguments_use_the_given_list_and_default_word():
    ctx = {"models": [cm.Item("anthropic:claude-sonnet-4-5"), cm.Item("openai:gpt-5")]}
    sug = cm.suggest("/model ", CAT, ctx)
    assert labels(sug) == ["default", "anthropic:claude-sonnet-4-5", "openai:gpt-5"]
    assert labels(cm.suggest("/model gpt", CAT, ctx)) == ["openai:gpt-5"]
    sug = cm.suggest("/model default ", CAT, ctx)
    assert labels(sug) == ["anthropic:claude-sonnet-4-5", "openai:gpt-5"]
    assert cm.suggest("/default ", CAT, ctx).items[0].value == "anthropic:claude-sonnet-4-5"


def test_data_that_is_missing_is_requested_not_invented():
    sug = cm.suggest("/model ", CAT, {})
    assert sug.need == "models" and labels(sug) == ["default"]
    assert cm.suggest("/grants revoke ", CAT, {}).need == "grants"
    assert cm.suggest("/handoff ", CAT, {}).need == "handoff_path"
    assert cm.suggest("/resume ", CAT, {}).need == "threads"


def test_static_choices():
    assert labels(cm.suggest("/tutorial ", CAT)) == ["on", "off", "reset"]
    assert labels(cm.suggest("/copy ", cm.build_catalog({"copy": ""}))) == ["last", "error", "all"]
    assert labels(cm.suggest("/grants ", CAT)) == ["list", "revoke"]
    assert labels(cm.suggest("/trace ", CAT)) == ["5", "10", "25"]
    assert labels(cm.suggest("/new ", cm.build_catalog({"new": ""}))) == ["private"]


def test_grant_ids_after_revoke_and_provider_names():
    ctx = {"grants": [cm.Item("g_1", "g_1", "shell on git"), cm.Item("g_2", "g_2", "write on docs")],
           "providers": [cm.Item("openai"), cm.Item("anthropic")]}
    assert labels(cm.suggest("/grants revoke ", CAT, ctx)) == ["g_1", "g_2"]
    assert labels(cm.suggest("/grants revoke g_2", CAT, ctx)) == ["g_2"]
    assert cm.suggest("/grants revoke g_2 ", CAT, ctx).items == []  # one id only
    assert labels(cm.suggest("/providers ", CAT, ctx)) == ["add", "openai", "anthropic"]
    assert labels(cm.suggest("/providers add op", CAT, ctx))[0] == "openai"


def test_arg_shape_hint_shows_for_a_command_with_no_list():
    sug = cm.suggest("/fork ", cm.build_catalog({"fork": "fork it"}))
    assert sug.hint == "[title]"
    sug = cm.suggest("/handoff ", CAT, {"handoff_path": [cm.Item("/x/handoff.md", "/x/handoff.md", "default path")]})
    assert sug.hint == "[path]" and labels(sug) == ["/x/handoff.md"]
    sug = cm.suggest("/handoff d", CAT, {})
    assert sug.hint == ""  # typing: no hint over the text


def test_core_commands_get_their_args_from_commands_list():
    cat = cm.build_catalog({"help": ""}, [{"name": "zap", "description": "do a zap", "args": "<target>"}])
    zap = next(c for c in cat if c.value == "zap")
    assert zap.args == "<target>" and zap.meta == "do a zap"
    assert cm.suggest("/zap ", cat).hint == "<target>"


def test_every_known_command_has_an_arg_shape():
    from exo_tui.app import COMMANDS
    assert set(COMMANDS) <= set(cm.ARG_HINTS)


# ---------------------------------------------------------------- shell and plain text
def test_bang_shows_the_shell_hint_and_plain_text_nothing():
    sug = cm.suggest("!ls -la", CAT)
    assert sug.mode == "shell" and "shell" in sug.hint
    assert cm.suggest("hello there", CAT).mode == ""
    assert cm.suggest("/model x\ny", CAT).mode == ""  # multi-line text is a message, not a command


# ---------------------------------------------------------------- @ files
@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, body in {"README.md": "x", "src/app.py": "x", "src/util/helpers.py": "x", "docs/guide.md": "x",
                      "build/out.bin": "x", ".env": "SECRET=1", "keys/id_rsa": "k", ".gitignore": "build/\n"}.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return tmp_path


def test_at_lists_dirs_first_and_respects_gitignore(repo):
    ix = cm.FileIndex(repo)
    got = [i.value for i in ix.query("")]
    assert got[:2] == ["@docs/", "@src/"] and "@README.md" in got
    assert not any(g.startswith("@build") for g in got)  # .gitignore
    assert [i.value for i in ix.query("src/")] == ["@src/util/", "@src/app.py"]


def test_at_fuzzy_by_name_and_never_secrets(repo):
    ix = cm.FileIndex(repo)
    assert [i.value for i in ix.query("help")][0] == "@src/util/helpers.py"
    assert [i.value for i in ix.query("app")][0] == "@src/app.py"
    every = [i.value for q in ("", "env", "id_rsa", "keys/", ".") for i in ix.query(q)]
    assert "@.env" not in every and not any("id_rsa" in e for e in every)


def test_at_stays_inside_the_working_folder(repo):
    ix = cm.FileIndex(repo)
    for q in ("/etc/", "../", "~/", "src/../.."):
        assert ix.query(q) == []


def test_at_without_git_walks_and_skips_dotfiles(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.txt").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "m.js").write_text("x")
    got = [i.value for i in cm.FileIndex(tmp_path).query("")]
    assert got == ["@a/"]
    assert [i.value for i in cm.FileIndex(tmp_path).query("one")] == ["@a/one.txt"]


def test_at_token_inside_a_sentence_completes_in_place(repo):
    ix = cm.FileIndex(repo)
    text = "read @src/ap"
    sug = cm.suggest(text, CAT, files=ix)
    assert sug.mode == "file" and labels(sug) == ["@src/app.py"]
    assert cm.complete_text(text, sug, sug.items[0]) == "read @src/app.py "
    sug = cm.suggest("mail a@b.com", CAT, files=ix)
    assert sug.mode == ""  # an at sign inside a word is not a file


# ---------------------------------------------------------------- typo help
def test_closest_command():
    names = ["compact", "context", "model", "handoff", "providers"]
    assert cm.closest("comand", names) in ("compact", "context")
    assert cm.closest("modle", names) == "model"
    assert cm.closest("handof", names) == "handoff"
    assert cm.closest("prov", names) == "providers"
    assert cm.closest("model", names) is None  # already right
    assert cm.closest("qqqqq", names) is None


# ---------------------------------------------------------------- history
def test_history_recall_and_ghost(tmp_path):
    h = cm.History.for_seat(tmp_path, "atlas")
    for line in ("fix the failing test", "/model default openai:gpt-5", "fix the failing test"):
        h.add(line)
    assert h.entries == ["fix the failing test", "/model default openai:gpt-5", "fix the failing test"]
    assert h.prev("") == "fix the failing test" and h.prev("") == "/model default openai:gpt-5"
    assert h.next() == "fix the failing test" and h.next() == "" and not h.browsing
    assert h.ghost("fix t") == "he failing test"
    assert h.ghost("/model d") == "efault openai:gpt-5"
    assert h.ghost("zzz") == "" and h.ghost("") == ""


def test_history_is_per_seat_private_and_bounded(tmp_path):
    a, b = cm.History.for_seat(tmp_path, "atlas"), cm.History.for_seat(tmp_path, "other")
    a.add("hello")
    assert cm.History.for_seat(tmp_path, "atlas").entries == ["hello"] and b.entries == []
    assert cm.History.for_seat(tmp_path, "other").entries == []
    assert oct((tmp_path / "history" / "atlas.jsonl").stat().st_mode & 0o777) == "0o600"
    small = cm.History(tmp_path / "s.jsonl", limit=3)
    for i in range(6):
        small.add(f"line {i}")
    assert cm.History(tmp_path / "s.jsonl", limit=3).entries == ["line 3", "line 4", "line 5"]


def test_history_never_keeps_what_looks_like_a_key(tmp_path):
    h = cm.History.for_seat(tmp_path, "atlas")
    h.add("my OPENAI_API_KEY=abc")
    h.add("here: sk-abcdefghijklmnopqrstuvwx")
    h.add("password: hunter2")
    assert h.entries == []


def test_ghost_prefers_history_then_menu_then_the_arg_hint():
    h = cm.History()
    h.add("/handoff notes/today.md")
    sug = cm.suggest("/handoff ", CAT)
    assert cm.ghost("/handoff ", sug, h) == ("notes/today.md", True)
    assert cm.ghost("/handoff ", sug, None) == ("[path]", False)  # a hint is not acceptable text
    sug = cm.suggest("/comp", CAT)
    assert cm.ghost("/comp", sug, None) == ("act", True)
    assert cm.ghost("hello", cm.suggest("hello", CAT), None) == ("", False)


# ---------------------------------------------------------------- guidance
class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def guide(**kw):
    clock = Clock()
    return cm.Guide(clock=clock, **kw), clock


def test_each_rule_fires_on_its_state():
    g, _ = guide()
    assert g.pick(cm.State(first_run=True)).fill == "/intro"
    g, _ = guide()
    assert "/providers" in g.pick(cm.State(provider_error=True)).text
    g, _ = guide()
    tip = g.pick(cm.State(job_done="j_7"))
    assert "job_output" in tip.text and "j_7" in tip.fill
    g, _ = guide()
    assert g.pick(cm.State(findings=2)).fill == "/findings"
    g, _ = guide()
    assert g.pick(cm.State(long_turn=True)).fill == "/trace"
    g, _ = guide()
    assert g.pick(cm.State(turns=3)) is None and g.pick(cm.State(turns=12)).fill == "/context"


def test_guidance_is_rate_limited_quiet_while_busy_and_capped():
    g, clock = guide()
    st = cm.State(findings=1, turns=20)
    assert g.pick(cm.State(findings=1, busy=True)) is None
    first = g.pick(st)
    assert first.key == "findings" and g.pick(st) is None  # the gap
    clock.t += 30
    assert g.pick(st).key == "context"  # never the same tip twice in a row; the next rule speaks
    clock.t += 30
    assert g.pick(st).key == "findings"
    clock.t += 30
    assert g.pick(st).key == "context"
    clock.t += 30
    assert g.pick(st) is None  # twice per rule per session


def test_a_command_the_user_ran_retires_its_tip_and_off_means_off():
    g, clock = guide()
    g.used("findings")
    assert g.pick(cm.State(findings=1)) is None
    g.enabled = False
    clock.t += 99
    assert g.pick(cm.State(first_run=True)) is None


def test_the_tip_is_one_short_line():
    g = cm.Guide()
    for st in (cm.State(first_run=True), cm.State(provider_error=True), cm.State(job_done="j_1"),
               cm.State(findings=3), cm.State(long_turn=True), cm.State(turns=50)):
        for tip in g.rules(st):
            assert "\n" not in tip.text and len(tip.text) <= 72


def test_short_desc_cuts_at_a_clause_or_a_word():
    assert cm.short_desc("pick the model for this session; /model <ref> sets it") == "pick the model for this session"
    long = "summarize now what is eligible in this agent's memory then show tokens before and after it ran"
    out = cm.short_desc(long)
    assert out.endswith("\u2026") and len(out) <= 65 and " " not in out[-3:] and long.startswith(out[:-1])
