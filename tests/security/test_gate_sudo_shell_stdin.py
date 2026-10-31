"""#491: `sudo -s` and `sudo -i` (--shell, --login) start the target user's shell, which runs its stdin.

With no command, the shell sudo starts reads its commands from stdin. On main 58503282 the gate read such a line as
`read` (sudo's options left no program, a bare assignment) and allowed it, even under `deny: [delete]`, while `sudo
bash` with the same stdin was read. The gate now reads a sudo shell with no command as it reads `bash` with no
operand: an opaque part that runs its stdin, and the parts of each text the line feeds it (a pipe, a here-string, a
heredoc). A stdin it cannot read (a file, a variable, a process substitution) asks. With a command, sudo runs
`$SHELL -c` on the command's words joined with spaces, its metacharacters but `$` escaped with a backslash, and
that text is read too where it may differ from the words. The line's own reading stays beside the new parts, so the
decision is never lower than main's. Classification and decision only; nothing here runs the lines.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

from exo import crypto, gate
from exo.gate import binds_exact, classify, decide, normalize_policies


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


DEFAULT = normalize_policies({})
DENY_DELETE = normalize_policies({"gates": {"deny": ["delete"]}})
DENY_STORE = normalize_policies({"gates": {"deny": ["store_write"]}})
RM = "'rm -rf /srv/data'"

# The issue's rows (main: `read`, allowed under the default policy and under `deny: [delete]`).
ISSUE_ROWS = [
    f"echo {RM} | sudo -s",
    f"sudo -s <<< {RM}",
    f"sudo -i <<< {RM}",
    f"sudo --login <<< {RM}",
]
# sudo's shell options and their spellings, after other options of sudo's and their values.
SPELLINGS = ["sudo -s", "sudo -i", "sudo --shell", "sudo --login", "sudo --shel", "sudo -u root -s", "sudo -E -s",
             "sudo -Es", "sudo -sE", "sudo -iu root", "sudo -u root -i", "sudo -uroot -s", "sudo --user=root -s",
             "sudo --user root -i", "sudo -s --", "sudo -n -s", "sudo -H -i", "sudo -g wheel -s", "sudo -D /srv -s",
             "sudo --preserve-env -s", "sudo --preserve-env=PATH -s", "sudo -s FOO=bar", "/usr/bin/sudo -s",
             "doas -s", "doas -u root -s"]
# Prefixes and compounds around it.
WRAPPED = [f"env sudo -s <<< {RM}", f"nice sudo -s <<< {RM}", f"timeout 5 sudo -s <<< {RM}",
           f"command sudo -s <<< {RM}", f"sudo -u bob sudo -s <<< {RM}", f"sudo -s sudo -i <<< {RM}",
           f"sudo -iu root sudo -s <<< {RM}", f"{{ sudo -s; }} <<< {RM}", f"setsid sudo -s <<< {RM}"]
FEEDS = ["echo {p} | {s}", "{s} <<< {p}", "{s} <<'EOF'\n{p}\nEOF", "{s} <<EOF\n{p}\nEOF", "cat <<'EOF' | {s}\n{p}\nEOF"]


def _fed(spelling, payload=RM):
    return [f.format(s=spelling, p=payload if "EOF" not in f else payload.strip("'")) for f in FEEDS]


FIX_ROWS = ISSUE_ROWS + [line for s in SPELLINGS for line in _fed(s)] + WRAPPED


@pytest.mark.parametrize("line", FIX_ROWS)
def test_text_fed_to_a_sudo_shell_is_read(line):
    c = sh(line)
    assert c.category == "delete", (line, c)
    assert any(p.category == "opaque_exec" and "runs its stdin" in p.summary for p in gate._each_part(c)), line
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == "deny", line


@pytest.mark.parametrize("line", [
    # an option word with an expansion or an unquoted glob may come to -s, -i, --shell or --login
    f"sudo -$(printf s) <<< {RM}", f"sudo -`printf s` <<< {RM}", f'X=s; sudo -"$X" <<< {RM}',
    f'X=i; echo {RM} | sudo -"$X"', f'sudo -u root -"$X" <<< {RM}', f'sudo -E"$X" <<< {RM}',
    f'sudo --"$L" <<< {RM}', f'sudo "-$X" <<< {RM}', f"sudo -? <<< {RM}", f"sudo -* <<< {RM}",
    f"touch ./-s; sudo -? <<< {RM}", f'doas -"$X" <<< {RM}',
])
def test_an_option_word_the_gate_cannot_read_may_start_the_shell(line):
    c = sh(line)
    assert c.category == "delete", (line, c)
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == "deny", line


@pytest.mark.parametrize("line", [
    # an option's value that may make several words: `-u {root,-s}` is `-u root -s`, and `$U` may be `root -s`
    f"sudo -u $U <<< {RM}", f"echo {RM} | sudo -u $U", f"sudo -u {{root,-s}} <<< {RM}",
    f"sudo -u {{root,-i}} <<< {RM}", f"echo {RM} | sudo -u $(id -un)", f"sudo -u ${{U}} <<< {RM}",
    f"sudo -g $G <<< {RM}", f"sudo -D $D <<< {RM}", f"sudo --user $U <<< {RM}", f"sudo -u * <<< {RM}",
    f"sudo -E -u $U <<< {RM}", f"sudo -u $U -E <<< {RM}", f"doas -u $U <<< {RM}", f"env sudo -u $U <<< {RM}",
    f"sudo -u bob sudo -u $U <<< {RM}",
    # an operand that may expand to a shell option
    f'sudo "$X" <<< {RM}', f"sudo $(printf -- -s) <<< {RM}", f"sudo [-]s <<< {RM}", f"sudo ?s <<< {RM}",
    f"sudo {{-s,}} <<< {RM}", f'sudo -u root "$X" <<< {RM}', f"sudo -u root {{-i,}} <<< {RM}",
    f'doas "$X" <<< {RM}',
    # an option word the gate cannot read may take the next word as its value (`-$X root` may be `-iu root`)
    f"sudo -$X root <<< {RM}", f'sudo -"$X" ls <<< {RM}',
    # bash keeps a word a brace set makes twice: `-p {-s,-s}` is `-p -s -s`, and the second -s starts the shell
    f"sudo -p {{-s,-s}} <<< {RM}", f"sudo -p {{,}}-s <<< {RM}", f"sudo -p -s{{,}} <<< {RM}",
    f"sudo -p {{-i,-i}} <<< {RM}", f"echo {RM} | sudo -p {{-s,-s}}",
    # a bracket glob at the start of a value may make several words too (`[!a]s` may be `bs -s`)
    f"sudo -u [!a]s <<< {RM}", f"sudo -u [ab] <<< {RM}", f"sudo -u [r]oot <<< {RM}",
    # `~-` and `~+` are $OLDPWD and $PWD, one word the line may have set to -s
    f"OLDPWD=-s; sudo ~- <<< {RM}", f"sudo ~+ <<< {RM}", f"sudo ~-1 <<< {RM}",
    # bash expands braces before tildes: `~-{,}` is `~- ~-`
    f"OLDPWD=-s; sudo ~-{{,}} <<< {RM}", f"sudo ~+{{,}} <<< {RM}", f"sudo ~-{{1,}} <<< {RM}",
])
def test_a_value_or_operand_the_gate_cannot_read_may_start_the_shell(line):
    c = sh(line)
    assert c.category == "delete", (line, c)
    assert any(p.category == "opaque_exec" and "runs its stdin" in p.summary for p in gate._each_part(c)), line
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == "deny", line


@pytest.mark.parametrize("line", ["sudo -u {root,-s} <<< 'cp /tmp/x ~/.exo/policies/x'",
                                  "sudo -u root -p {-s,-s} <<< 'cp /tmp/x ~/.exo/policies/x'"])
def test_a_store_write_behind_a_value_that_may_start_the_shell_is_denied(line):
    c = sh(line)
    assert c.category == "store_write", c
    assert decide(c, DENY_STORE).outcome == "deny" and gate.no_grant(c)


@pytest.mark.parametrize("line,category", [
    # a brace value's words end the options elsewhere: `-u {root,-p} echo rm` is `-u root -p echo rm`, so `echo` is
    # the prompt and rm runs as root
    ("sudo -u {root,-p} echo rm -rf /srv/data", "delete"),
    ("sudo -u {root,-p} echo git push --force origin main", "force_push"),
    # a word the gate cannot read, or a value that may split, may take the next word as its value
    ("sudo -E{,p} echo rm -rf /srv/data", "delete"), ('sudo -"$X" echo rm -rf /srv/data', "delete"),
    ("sudo -u $U echo -s rm -rf /srv/data", "delete"), ("sudo -$X root rm -rf /srv/data", "delete"),
    ('sudo "$X" root rm -rf /srv/data', "delete"), ('sudo -"$X" echo rm -rf "$D"', "delete"),
    # the words such a reading moves to are read as sudo runs them too, as bash expanded them: a sudo there with a
    # brace or glob value is read in turn (`-E{,p}` is `-E -Ep`, so each prompt takes `echo`)
    ("sudo -E{,p} echo sudo -E{,p} echo rm -rf /srv/data", "delete"),
    ("sudo -u $U echo sudo -E{,p} echo rm -rf /srv/data", "delete"),
    ('sudo -"$X" echo sudo -E{,p} echo rm -rf /srv/data', "delete"),
    ("sudo -u [!a]s echo sudo -u [!a]s echo rm -rf /srv/data", "delete"),
    ("sudo -E{,p} echo sudo -E{,p} echo git push --force origin main", "force_push"),
    # a sudo shell's command read again holds a later sudo's brace value as a literal: that sudo's moved reading,
    # of the words bash expanded, is read too
    ('sudo -s sudo -u "$U" -u {root,-p} echo rm -rf /srv/data', "delete"),
    ("sudo -s sudo -C $N -u {root,-p} echo rm -rf /srv/data", "delete"),
    ("sudo -s sudo -u '' -u {root,-p} echo rm -rf /srv/data", "delete"),
    ('sudo -s sudo -u "$U" -u {root,-sp} echo rm -rf /srv/data', "delete"),
    ('sudo -s sudo -u "$U" -u {root,-p} echo git push --force origin main', "force_push"),
])
def test_a_word_that_may_take_the_next_as_its_value_moves_the_command(line, category):
    c = sh(line)
    assert c.category == category, (line, c)
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, normalize_policies({"gates": {"deny": [category]}})).outcome == "deny", line


@pytest.mark.parametrize("head", ["sudo -u {root,-p} echo ", "sudo -E{,p} echo ", "sudo -u $U echo "])
@pytest.mark.parametrize("n", [5, 6, 12])
def test_a_reading_cut_short_by_the_depth_cap_asks(head, n):
    # Each such reading is read one level deeper. Past the cap the deepest command is not read, and the cap's ask
    # stays: it is not dropped as a command the reading cannot read.
    c = sh(head * n + "rm -rf /srv/data")
    assert any(p.category == "opaque_exec" and "too deep" in p.summary for p in gate._each_part(c)), (head, n)
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome in ("ask", "deny"), (head, n)
    assert decide(c, normalize_policies({"gates": {"deny": ["force_push"]}})).outcome == "ask"
    # up to the cap, the deepest command is read
    assert decide(sh(head * 4 + "rm -rf /srv/data"), DENY_DELETE).outcome == "deny", head


@pytest.mark.parametrize("head", ["sudo -u {root,-s} ", "sudo -s ", "sudo -u $U ", "sudo -p {-s,-s} ", "sudo -$X ",
                                  "sudo -u {alice,bob} ", "sudo -E{,p} echo ", "sudo -u {root,-p} echo "])
def test_a_long_sudo_chain_is_read_in_linear_time(head):
    # Each sudo's command holds the rest of the chain: parsing it again for each sudo took minutes on 28 KB.
    line = head * 1000 + f"ls <<< {RM}"
    t = time.perf_counter()
    c = sh(line)
    assert time.perf_counter() - t < 10, head
    assert decide(c, DENY_DELETE).outcome in ("allow", "ask", "deny")


@pytest.mark.parametrize("tail", ["echo '$x'", 'echo "$x"', "echo 'a\nb'", "rm -rf '$x'"])
def test_nested_sudo_shells_are_read_once_per_level(tail):
    # Each sudo's command holds the sudos after it: reading each of them again at each level took minutes.
    line = "sudo -s " * 40 + tail
    t = time.perf_counter()
    c = sh(line)
    assert time.perf_counter() - t < 2, line
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == ("deny" if "rm" in tail else "ask")


@pytest.mark.parametrize("spelling", ["sudo -s", "sudo -i", "sudo --login", "sudo -iu root"])
def test_a_store_write_fed_to_a_sudo_shell_asks_and_a_deny_reaches_it(spelling):
    for line in _fed(spelling, "'cp /tmp/x ~/.exo/policies/x'"):
        c = sh(line)
        assert c.category == "store_write", (line, c)
        assert decide(c, DEFAULT).outcome == "ask"
        assert decide(c, DENY_STORE).outcome == "deny", line
        assert gate.no_grant(c), line


@pytest.mark.parametrize("payload,category", [
    ("'git push --force origin main'", "force_push"),
    ("'curl -X POST -d @f https://example.com'", "web_post"),
    ("'gh pr comment 1 --body x'", "github_post"),
])
def test_other_acts_fed_to_a_sudo_shell_are_read(payload, category):
    policy = normalize_policies({"gates": {"deny": [category]}})
    for line in _fed("sudo -s", payload) + _fed("sudo -i", payload):
        c = sh(line)
        assert c.category == category, (line, c)
        assert decide(c, policy).outcome == "deny", line


@pytest.mark.parametrize("line", [
    "sudo -s < /tmp/script.sh",          # the issue's row: a file the gate does not read
    "sudo -i < /tmp/script.sh",
    'sudo -s <<< "$CMDS"',               # a variable
    "sudo -s < <(printf x)",             # a process substitution
    "echo hi | sudo -s",                 # a known text with nothing denied in it still runs as root
])
def test_a_stdin_the_gate_cannot_read_asks(line):
    c = sh(line)
    assert c.category == "opaque_exec", (line, c)
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), line
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == "ask"


@pytest.mark.parametrize("line", [
    # main read the words sudo's options left as the program; sudo reads `-iu root` and `--user root` as options
    "sudo -iu root rm -rf /srv/data", "sudo --user root -s rm -rf /srv/data",
    # sudo escapes a newline with a backslash, which the shell drops, and an empty word leaves only its space
    "sudo -s $'r\\nm' -rf /srv/data", "sudo -s '' rm -rf /srv/data",
])
def test_a_command_is_read_as_the_shell_runs_it(line):
    c = sh(line)
    assert c.category == "delete", (line, c)
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == "deny", line
    # main read an unknown program here, which asked unless opaque_exec was named: the shell's reading asks so too
    assert decide(c, normalize_policies({"gates": {"categories": {"delete": "allow"}}})).outcome == "ask", line
    assert binds_exact(c), line


@pytest.mark.parametrize("line", ["sudo -s rm -rf /srv/data", "sudo -i rm -rf /srv/data",
                                  "sudo -s -- rm -rf /srv/data", 'sudo -s rm -rf "$D"'])
def test_a_command_main_read_keeps_its_reading(line):
    c = sh(line)
    assert c.category == "delete", (line, c)
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(c, DENY_DELETE).outcome == "deny", line


@pytest.mark.parametrize("line", ['sudo -s rm -rf "$D"', "sudo -s ls $DIR", "sudo -i echo $(date)"])
def test_a_word_the_gate_cannot_read_asks(line):
    # The shell expands a `$` in what sudo passes it again, so the words the outer shell expanded are not known.
    c = sh(line)
    assert any(p.category == "opaque_exec" and "it expands again" in p.summary for p in gate._each_part(c)), line
    assert decide(c, DEFAULT).outcome == "ask"


@pytest.mark.parametrize("line", ["sudo -s echo '$X'", "sudo -i ls '$HOME/x'", "sudo -s '$CMD'"])
def test_a_dollar_the_shell_expands_again_asks(line):
    # sudo leaves `$` unescaped, so the shell expands a quoted `$X` the line passed as text.
    c = sh(line)
    assert any(p.category == "opaque_exec" and "reads other words" in p.summary for p in gate._each_part(c)), line
    assert decide(c, DEFAULT).outcome == "ask" and binds_exact(c)


@pytest.mark.parametrize("line", [f"sudo bash <<< {RM}", f"bash <<< {RM}", f"echo {RM} | bash",
                                  f"sudo -s bash <<< {RM}"])
def test_the_controls_are_unchanged(line):
    c = sh(line)
    assert c.category == "delete", (line, c)
    assert decide(c, DEFAULT).outcome == "ask" and decide(c, DENY_DELETE).outcome == "deny"


@pytest.mark.parametrize("line,category,outcome", [
    ("sudo -l", "read", "allow"), ("sudo -v", "read", "allow"), ("sudo -k", "read", "allow"),
    ("sudo ls", "read", "allow"), ("sudo -u bob ls", "read", "allow"), ('sudo -u "$U" ls', "read", "allow"),
    ("sudo -s ls", "read", "allow"), ("sudo -i ls", "read", "allow"), ("sudo -s -u bob ls", "read", "allow"),
    ("sudo -s echo hi > /tmp/f", "local_write", "allow"),        # #454: as on main
    (f"sudo -s cat <<< {RM}", "read", "allow"),                  # the command, not the shell, reads the stdin
    ("sudo -s gh pr comment 1 --body 'hi there'", "github_post", "ask"),
    # a value that may make several words, with a command and no stdin fed; an expansion that is the value
    ("sudo -u $U ls", "read", "allow"), ("sudo -u {alice,bob} ls", "read", "allow"),
    ('sudo -u"$U" -l', "read", "allow"), ('sudo -u"$U" -v', "read", "allow"), ('sudo --user="$U" -v', "read", "allow"),
    ('sudo -D"$D"', "read", "allow"), ("sudo -u\"$U\" echo '$HOME'", "read", "allow"),
    # a word that may take the next as its value adds only what the command it may move to is known to do
    ("sudo -u [r]oot ls", "read", "allow"), (f"sudo -u '[r]oot' <<< {RM}", "read", "allow"),
    ("sudo -u $U cat /etc/hosts", "read", "allow"), ("sudo -u $USER git status", "read", "allow"),
    ('sudo -"$X" echo hi', "read", "allow"), ('sudo -"$X" ls -l', "read", "allow"),
    ("sudo -E{,p} echo sudo -E{,p} echo hi", "read", "allow"), ("sudo -u $U echo sudo -u {alice,bob} ls", "read", "allow"),
    # a quoted brace does not expand, and a tilde word with another prefix is no $OLDPWD
    (f"sudo ~-'{{,}}' <<< {RM}", "opaque_exec", "ask"), (f"sudo ~-{{a,b}} <<< {RM}", "opaque_exec", "ask"),
])
def test_lines_with_no_shell_stdin_are_unchanged(line, category, outcome):
    c = sh(line)
    assert c.category == category, (line, c)
    assert decide(c, DEFAULT).outcome == outcome
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), line


@pytest.mark.parametrize("line", ["sudo -s", "sudo -i", "sudo --login", "sudo -s > /tmp/f", "sudo -u $U"])
def test_a_sudo_shell_with_no_feed_asks_like_bash(line):
    # Main allowed these (`read`; #454 made the last a local_write). The shell runs whatever stdin reaches it, which a
    # command around the line may give (`bash -c 'sudo -s' <<< ...`), so it asks as `bash` alone does.
    c = sh(line)
    assert c.category == "opaque_exec", (line, c)
    assert decide(c, DEFAULT).outcome == "ask"
    assert decide(sh("bash"), DEFAULT).outcome == "ask"


@pytest.mark.parametrize("line", [f"ssh host 'sudo -s <<< {RM}'".replace("'rm", "\"rm").replace("data'", "data\""),
                                  "ssh host 'sudo -s < /tmp/script.sh'", "ssh host 'sudo -s > /tmp/f'"])
def test_the_line_is_also_read_as_before(line):
    # Main read the hop's sudo line as a costly `read` on the host, which logs. A policy that allows the category the
    # shell's parts lead with does not lower it: the call keeps its reading from before #491 as its last floor.
    c = sh(line)
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    before = gate.readings(c)[-1]
    assert not any("runs its stdin" in p.summary for p in gate._each_part(before)), before
    assert before.category in ("read", "local_write"), before
    allow = normalize_policies({"gates": {"categories": {x: "allow" for x in ("delete", "opaque_exec")}}})
    assert decide(c, allow).outcome == "log", line
    assert binds_exact(c), line


def test_a_sudo_raise_stays():
    # sudo's own raise of the line's first part is kept, and the shell's part is costly.
    c = sh(f"sudo -s <<< {RM}")
    assert c.parts[0].reversibility == "costly" and "sudo" in c.parts[0].signals, c.parts[0]
    assert c.reversibility == "irreversible"


class _Ctx:
    def __init__(self, settings):
        self.settings, self.hooks = settings, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


def test_an_always_through_the_hook_binds_the_exact_line_as_for_bash(tmp_path, monkeypatch):
    # `echo 'rm -rf x' | bash` binds its approval to the exact line on main: an always covers that line only. The sudo
    # shell line does the same, and neither covers the other or a bare `rm -rf x`.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "plugins" / "exo-gate"))
    from exo_gate import hook

    store = tmp_path / "store"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "gates.yaml").write_text("categories: {delete: ask}\n")
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    ctx = _Ctx({"principal_store": str(store), "key_path": str(key), "decision_model": False})
    hook.register(ctx)
    [pre], [post] = ctx.hooks["pre_tool_call"], ctx.hooks["post_approval_response"]

    def call(line):
        return asyncio.run(pre(tool_name="terminal", args={"command": line}, session_id="s1", tool_call_id="c1",
                               turn_id="t1"))
    sudo_line, bash_line = "echo 'rm -rf x' | sudo -s", "echo 'rm -rf x' | bash"
    for line in (sudo_line, bash_line):
        assert binds_exact(sh(line)) is binds_exact(sh(bash_line)), line
    r = call(sudo_line)
    assert r["action"] == "approve" and r["rule_key"] == f"exo-gate:delete:{r['binding'][:16]}", r
    post(pattern_key="plugin_rule:" + r["rule_key"], choice="always", decided_by="principal:tui")
    [g] = json.loads((store / "state" / "approval-grants.json").read_text())
    assert g["binding"] == r["binding"], g
    assert call(sudo_line) is None
    for other in (bash_line, "rm -rf x", "echo 'rm -rf y' | sudo -s", "sudo -s <<< 'rm -rf x'"):
        assert call(other)["action"] == "approve", other
