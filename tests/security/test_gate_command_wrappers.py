"""#160: the gate reads the command behind setsid, flock, watch, script -c and other wrappers.

On main 35b7de8 each wrapper was an unknown program or an opaque one, so the command it runs was never classified:
`setsid git push origin main` was `opaque_exec` and a deny on `vcs_push` only asked. Now the wrapper's options and
fixed operands are skipped as getopt reads them, and its command (an argv, or a shell command string) is
classified as a nested command beside an opaque part, as the command after `xargs` is. The line still asks as it
did. Until D-075 lands, a deny on what runs applies only when its category outranks opaque_exec (`vcs_push`,
`delete`); a category that ties with it or ranks below it (`schedule`; `web_read`, `local_write`) still asks.

Every line here is classification data only; nothing runs it.
"""
import shlex
import tracemalloc

import pytest

from exo import gate


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(cmd):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


def denies(category):
    return [{"gates": {"categories": {category: "deny"}}}, {"gates": {"deny": [category]}},
            {"gates": {"rules": [{"match": {"category": category}, "decision": "deny", "reason": "no"}]}}]


# The issue's table. Each was opaque_exec on main, so `deny: [vcs_push]` asked.
ISSUE_ROWS = [
    "watch git push origin main",
    "parallel git push ::: origin",
    "script -qc 'git push origin main' /dev/null",
    "tmux new-session -d 'git push origin main'",
    "screen -dm git push origin main",
    "setsid git push origin main",
    "flock /tmp/l git push origin main",
    "chroot / git push origin main",
    "unshare -r git push origin main",
    "taskset 1 git push origin main",
    "nsenter -t 1 git push origin main",
    "chronic git push origin main",
    "systemd-run --user git push origin main",
]

# Option forms: values taken separate, attached or inside a cluster, fixed operands, `--`, and the command
# string forms (flock -c, script -c, watch without -x, tmux shell-command, parallel template).
OPTION_ROWS = [
    "flock -w 5 /tmp/l git push origin main",
    "flock -nw5 /tmp/l git push origin main",
    "flock --timeout=5 /tmp/l git push origin main",
    "flock -c 'git push origin main' /tmp/l",
    "flock /tmp/l -c 'git push origin main'",
    "taskset -c 0-3 git push origin main",
    "systemd-run --user --scope -p MemoryMax=1G git push origin main",
    "systemd-run --unit=x -E A=1 --property CPUQuota=10% git push origin main",
    "watch -n 5 -d git push origin main",
    "watch -x git push origin main",
    "watch -xn 5 git push origin main",
    "watch 'git push origin main'",
    "nsenter -t 1 -m -u git push origin main",
    "nsenter -m/proc/1/ns/mnt git push origin main",
    "unshare -rn git push origin main",
    "unshare --map-user 0 git push origin main",
    "chroot --userspec=u:g / git push origin main",
    "chroot --userspec u:g / git push origin main",
    "script -q -c 'git push origin main'",
    "script --command 'git push origin main' /dev/null",
    "script /dev/null -c 'git push origin main'",
    "tmux new-session -d -s x 'git push origin main'",
    "tmux new -ds x git push origin main",
    "tmux splitp 'git push origin main'",           # tmux's default command-alias entries for split-window
    "tmux split-pane -h 'git push origin main'",
    "tmux -L sock new-window -t x 'git push origin main'",
    "tmux run-shell 'git push origin main'",
    "tmux -c 'git push origin main'",
    "tmux new-session -d 'sleep 3;' new-window 'git push origin main'",
    "tmux new-session -d ls\\; new-window 'git push origin main'",
    "screen -dmS x git push origin main",
    "screen -dm -Logfile /tmp/log git push origin main",
    "screen -S x -X screen git push origin main",
    "parallel git ::: push",
    "parallel -j4 git push ::: origin",
    "parallel 'git push {}' ::: origin",
    "parallel 'git {} origin main' ::: status log push",
    "chrt -f 10 git push origin main",
    "numactl --interleave=all git push origin main",
    "numactl -N 0 -m 0 git push origin main",
    "unbuffer git push origin main",
    "catchsegv git push origin main",
    "firejail --private git push origin main",
    "dbus-run-session -- git push origin main",
    "xvfb-run -a -s '-screen 0 1024x768x24' git push origin main",
    "/usr/bin/setsid git push origin main",
    "setsid -w sudo git push origin main",
    "timeout 5 setsid git push origin main",
    "xargs setsid git push origin main",
    "setsid bash -c 'git push origin main'",
    "echo 'git push origin main' | setsid bash",
]


@pytest.mark.parametrize("line", ISSUE_ROWS + OPTION_ROWS)
def test_wrapped_push_is_vcs_push_and_denied(line):
    c = sh(line)
    assert c.category == "vcs_push", c.summary
    for pol in denies("vcs_push"):
        assert gate.decide(c, pol).outcome == "deny", pol
    # The wrapper keeps the indirection floor: the line asks under the default policy, as it did on main.
    assert "indirection" in c.signals
    assert gate.decide(c).outcome == "ask"


def test_function_keyword_without_parentheses():
    c = sh("function f { git push origin main; }; f")
    assert c.category == "vcs_push"


def test_wrapped_force_push_keeps_its_category():
    c = sh("setsid git push --force origin main")
    assert c.category == "force_push"
    for pol in denies("force_push"):
        assert gate.decide(c, pol).outcome == "deny", pol


@pytest.mark.parametrize("line,signal", [
    ("chroot / git push origin main", "chroot"),
    ("nsenter -t 1 -m -u git push origin main", "nsenter"),
    ("unshare -r git push origin main", "unshare"),
    ("systemd-run --user git push origin main", "systemd-run"),
])
def test_privileged_wrappers_carry_a_signal_like_sudo(line, signal):
    assert signal in sh(line).signals
    c = sh(line.split(" git ")[0] + " ls")
    assert signal in c.signals and c.reversibility == "costly"


def test_privileged_wrapper_bumps_a_reversible_inner_command():
    # `ssh host ls` alone is reversible and internal; behind chroot it counts as costly, as behind sudo, so it
    # outranks the opaque head (costly, private) and the line is costly and internal. Without the bump the head
    # would win and the line would read as private.
    assert (sh("ssh host ls").reversibility, sh("ssh host ls").exposure) == ("reversible", "internal")
    c = sh("chroot / ssh host ls")
    assert (c.reversibility, c.exposure) == ("costly", "internal")
    assert gate.decide(c).outcome == "ask"


# D-075 (#126): a deny entry matches any simple command on a line, not only the most severe one. A wrapped
# category that ties with opaque_exec needs it, as `coproc systemctl enable foo` does in test_coproc.py.
_D075 = gate.decide(gate.classify_shell("git push origin main && rm file"),
                    {"gates": {"deny": ["vcs_push"]}}).outcome == "deny"


@pytest.mark.xfail(not _D075, strict=True, reason="a tie with opaque_exec is denied only once D-075 checks every part")
def test_wrapped_tie_category_is_denied():
    c = sh("setsid systemctl enable foo")
    assert gate.decide(c, {"gates": {"deny": ["schedule"]}}).outcome == "deny"


# An environment dump is credentials wherever a prefix or runner is stripped (#155); behind a wrapper too, so
# each line takes the bare command's category and a deny on credentials applies.
ENV_DUMP_ROWS = [
    ("setsid printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("setsid env", "env"),
    ("setsid -w env FOO=1", "env FOO=1"),
    ("taskset 1 env", "env"),
    ("taskset -c 0-3 printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("xvfb-run printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("xvfb-run -a env", "env"),
    ("flock /tmp/l printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("flock -w 5 /tmp/l -c 'printenv GITHUB_TOKEN'", "printenv GITHUB_TOKEN"),
    ("watch -x env", "env"),
    ("watch -n 5 printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("script -qc env /dev/null", "env"),
    ("chrt -f 10 env", "env"),
    ("numactl -N 0 env", "env"),
    ("chroot / env", "env"),
    ("unshare -r printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("nsenter -t 1 -m env", "env"),
    ("systemd-run --user env", "env"),
    ("chronic printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("unbuffer env", "env"),
    ("catchsegv env", "env"),
    ("firejail --private env", "env"),
    ("dbus-run-session -- env", "env"),
    ("screen -dm printenv GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("tmux new-session -d env", "env"),
    ("parallel printenv ::: GITHUB_TOKEN", "printenv GITHUB_TOKEN"),
    ("nice -n 5 setsid env", "nice -n 5 env"),
    ("ionice -c 3 taskset 1 env", "ionice -c 3 env"),
    ("timeout 5 setsid printenv GITHUB_TOKEN", "timeout 5 printenv GITHUB_TOKEN"),
    ("setsid sudo env", "sudo env"),
    ("setsid uv run printenv GITHUB_TOKEN", "uv run printenv GITHUB_TOKEN"),
    ("setsid setsid env", "env"),
]


@pytest.mark.parametrize("line,bare", ENV_DUMP_ROWS)
def test_wrapped_env_dump_is_credentials_like_the_bare_command(line, bare):
    c = sh(line)
    assert c.category == sh(bare).category == "credentials", c.summary
    for pol in denies("credentials"):
        assert gate.decide(c, pol).outcome == "deny", pol
    assert gate.decide(c).outcome == "ask"


def test_store_write_behind_a_wrapper_is_store_write():
    c = sh("setsid rm -rf ~/.exo")
    assert c.category == "store_write"
    assert gate.decide(c).outcome == "ask"


# Ordinary wrapped lines keep what main gave them: a wrapper that was unknown or opaque still asks, a prefix
# that was already read (timeout, nice, env, ionice, stdbuf) is unchanged.
@pytest.mark.parametrize("line,category,outcome", [
    ("setsid ls", "opaque_exec", "ask"),
    ("flock /tmp/l true", "opaque_exec", "ask"),
    ("watch -n 1 date", "opaque_exec", "ask"),
    ("taskset 1 ls", "opaque_exec", "ask"),
    ("chronic -e true", "opaque_exec", "ask"),
    ("nice -n 5 setsid ls", "opaque_exec", "ask"),
    ("setsid env ls", "opaque_exec", "ask"),
    ("setsid printenv HOME", "opaque_exec", "ask"),
    ("timeout 5 ls", "read", "allow"),
    ("nice -n 5 ls", "read", "allow"),
    ("env FOO=1 ls", "read", "allow"),
    ("ionice -c 3 ls", "read", "allow"),
    ("stdbuf -oL ls", "read", "allow"),
])
def test_ordinary_wrapped_lines_keep_their_class(line, category, outcome):
    c = sh(line)
    assert (c.category, gate.decide(c).outcome) == (category, outcome)


def test_interpreter_behind_taskset_keeps_what_main_gives_it():
    bare, wrapped = sh("python3 -c 'print(1)'"), sh("taskset -c 0 python3 -c 'print(1)'")
    assert wrapped.category == bare.category
    assert gate.decide(wrapped).outcome == gate.decide(bare).outcome == "ask"


# What the gate cannot find or read stays opaque_exec and asks: a program from a variable, keys typed into a
# pane, a screen command other than `screen`, parallel reading its commands from input, an interactive shell
# (script, chroot or nsenter without a command), a lock with no command, a tmux command list it does not read.
@pytest.mark.parametrize("line", [
    "setsid $CMD",
    "flock /tmp/l $CMD",
    "watch -x $CMD",
    "tmux send-keys -t x 'git push origin main' Enter",
    "tmux new-session -d -s x \\; send-keys 'git push origin main' Enter",
    # An argument ending in an escaped `\;` (as tmux sees it) does not end the command: new-session runs `ls;`.
    "tmux new-session -d 'ls\\;' new-window 'git push origin main'",
    "tmux new-session -d ls\\\\\\; new-window 'git push origin main'",
    "screen -S x -X stuff 'git push origin main'",
    "screen -r x",
    "parallel",
    "parallel ::: 'git push origin main'",
    "script -q /tmp/t",
    "chroot /",
    "nsenter -t 1 -a",
    "flock 9",
    "setsid",
])
def test_unreadable_wrapped_commands_stay_opaque(line):
    c = sh(line)
    assert c.category == "opaque_exec", c.summary
    assert gate.decide(c).outcome == "ask"
    for pol in denies("vcs_push"):
        assert gate.decide(c, pol).outcome == "ask", pol


def test_parallel_template_keeps_the_indirection_signal():
    c = sh("parallel 'git push {}' ::: origin")
    assert c.category == "vcs_push" and "indirection" in c.signals


@pytest.mark.parametrize("line", ISSUE_ROWS + OPTION_ROWS + [r for r, _ in ENV_DUMP_ROWS] +
                         ["setsid ls", "watch -n 1 date", "setsid $CMD"])
def test_no_wrapped_line_is_allowed_or_logged(line):
    # Fail closed: every wrapper line asked on main; reading the command must not lower that.
    assert gate.decide(sh(line)).outcome in ("ask", "deny")


# A deny on opaque_exec itself was a deny for every wrapper line on main. The wrapped push now takes the line's
# category, so that deny no longer reaches it; the indirection floor keeps the line at ask (#178's rule, carried
# here): naming opaque_exec at deny must not turn the floor off and let the push log. `bash -c` logged on main.
@pytest.mark.parametrize("line", ISSUE_ROWS + OPTION_ROWS + [r for r, _ in ENV_DUMP_ROWS] +
                         ["bash -c 'git push origin main'", "setsid rm -rf /tmp/x"])
def test_a_deny_on_opaque_exec_never_lets_a_wrapped_command_log(line):
    c = sh(line)
    for pol in denies("opaque_exec"):
        assert gate.decide(c, pol).outcome in ("ask", "deny"), pol


def test_lowering_opaque_exec_by_name_still_turns_the_floor_off():
    c = sh("setsid ls")
    assert gate.decide(c, {"gates": {"categories": {"opaque_exec": "log"}}}).outcome == "log"
    rule = {"match": {"category": "opaque_exec", "command": "^setsid "}, "decision": "allow", "reason": "ok"}
    assert gate.decide(c, {"gates": {"rules": [rule]}}).outcome == "allow"


def _nested_parallel(levels, inner="git push origin main"):
    args = " ".join(str(n) for n in range(16))
    for _ in range(levels):
        inner = f"parallel {shlex.quote(inner)} ::: {args}"
    return inner


def test_nested_parallel_templates_stay_bounded(monkeypatch):
    # Each argument is read on the line itself only; a template that is a parallel line is read alone, so
    # nesting cannot multiply the work (17 reads per level grew to minutes at four levels).
    calls = []
    real = gate.classify_shell
    monkeypatch.setattr(gate, "classify_shell", lambda cmd, depth=0: calls.append(depth) or real(cmd, depth))
    line = _nested_parallel(5)
    c = gate.classify_shell(line)
    assert len(calls) <= 40
    assert gate.decide(c).outcome == "ask"
    assert sh(_nested_parallel(4)).category == "vcs_push"


def test_a_wide_parallel_template_is_not_multiplied(monkeypatch):
    # 100 `{}` and 16 long arguments: substituting all of them is cut at four times the line's length.
    line = "parallel " + shlex.quote("echo {} " * 100) + " ::: " + " ".join(chr(97 + k) * 200 for k in range(16))
    calls = []
    real = gate.classify_shell
    monkeypatch.setattr(gate, "classify_shell", lambda cmd, depth=0: calls.append(len(cmd)) or real(cmd, depth))
    c = gate.classify_shell(line)
    assert sum(calls) <= 6 * len(line)
    assert gate.decide(c).outcome == "ask"


def test_many_parallel_commands_on_one_line_stay_linear(monkeypatch):
    # Each parallel command is cut at four times its own length, not the whole line's, so a line of many of
    # them stays within a fixed multiple of the line.
    one = "parallel " + shlex.quote("echo {} " * 20) + " ::: " + " ".join(chr(97 + k) * 40 for k in range(16))
    line = "; ".join([one] * 30)
    calls = []
    real = gate.classify_shell
    monkeypatch.setattr(gate, "classify_shell", lambda cmd, depth=0: calls.append(len(cmd)) or real(cmd, depth))
    c = gate.classify_shell(line)
    assert sum(calls) <= 6 * len(line)
    assert gate.decide(c).outcome == "ask"


def test_a_substituted_template_is_sized_before_it_is_built():
    # 300 `{}` and one 10,000-character argument would make a 3-million-character text; it is over the budget,
    # so it is never built (the peak stays near 0.2 MB).
    line = "parallel " + shlex.quote("echo {} " * 300) + " ::: " + "x" * 10000
    tracemalloc.start()
    try:
        c = gate.classify_shell(line)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000
    assert gate.decide(c).outcome == "ask"
