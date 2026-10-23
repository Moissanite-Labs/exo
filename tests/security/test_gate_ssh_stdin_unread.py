"""ssh stdin the gate cannot read the text of still runs on the remote host (#316).

Three spellings gave an ssh with no remote command a stdin the gate did not see, so the line read as
plain `ssh` and was allowed: a compound redirected from a file (the closer's `<` feeds nothing the gate
can read), an output process substitution (the commands in a >( ) are fed what the outer command
writes), and a remote ssh hop (`ssh a ssh b` passes its stdin to `ssh b`, whose login shell runs it).
Each now asks like `ssh host < file` does, and a known text fed through a hop is read as shell. A hop
is found anywhere in the remote command and through more than one hop, a $( ) inherits the stdin of
the command it is in, a command in a >( ) body that sets its own stdin is not fed, and the command a
wrapper runs reads the wrapper's stdin. The part that runs the stdin sits beside ssh's own part, which
keeps the reading it had before, so a wrapper around ssh still marks it and a reserved entry still
finds it. The command strings are data for the classifier; nothing here runs them.
"""
import pytest

from exo import gate

DENY = {"gates": {"deny": ["delete", "force_push", "github_post"]}}
DELETE_DENY_FORMS = [
    DENY,
    {"gates": {"categories": {"delete": "deny"}}},
    {"gates": {"rules": [{"match": {"category": "delete"}, "decision": "deny"}]}},
]
HOST_DENY = {"gates": {"deny": ["target:prod-db"]}}
INNER_DENY = {"gates": {"deny": ["target:inner"]}}


@pytest.mark.parametrize("command", [
    # a compound whose closer is redirected from a file: the inner ssh is fed text nobody read
    "{ ssh prod-db; } < cmds.sh",
    "(ssh prod-db) < cmds.sh",
    "while read h; do ssh prod-db; done < hosts.txt",
    "cat cmds.sh | { cd /tmp; ssh prod-db; }",       # a pipe in whose text is not known, same mark
    "cat cmds.sh | { echo hi; ssh prod-db; }",        # not only the compound's first command
    # an output process substitution: >( ) is fed what the outer command writes
    "echo 'rm -rf /data' > >(ssh prod-db)",
    "echo 'rm -rf /data' | tee >(ssh prod-db)",
    "exec 3> >(ssh prod-db); echo 'rm -rf /data' >&3",
    "diff <(cat cmds.sh) >(ssh prod-db)",
])
def test_an_unreadable_stdin_still_runs_on_the_remote_host(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    "echo 'rm -rf /data' | ssh prod-db ssh inner",
    "ssh prod-db ssh inner <<'EOF'\nrm -rf /data\nEOF",
    "echo 'rm -rf /data' | ssh prod-db /usr/bin/ssh inner",
    "echo 'rm -rf /data' | ssh prod-db ssh -T inner",
])
def test_a_remote_hop_runs_the_stdin_on_the_inner_host(command):
    c = gate.classify_shell(command)
    assert c.category == "delete", c
    assert c.exposure == "internal" and "remote host" in c.signals, c
    assert any(p.summary.startswith("ssh prod-db passes its stdin to an ssh") for p in gate._each_part(c)), c
    assert any(p.target == "inner" for p in gate._each_part(c)), c
    for policy in DELETE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy
    assert gate.decide(c, INNER_DENY).outcome == "deny"
    assert gate.decide(c, HOST_DENY).outcome == "deny"
    assert gate.decide(c).outcome == "ask"


def test_a_hop_with_an_unread_stdin_asks():
    c = gate.classify_shell("cat cmds.sh | ssh prod-db ssh inner")
    assert c.category == "opaque_exec", c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, INNER_DENY).outcome == "deny"


@pytest.mark.parametrize("command, category", [
    # the outer ssh does not forward its stdin (-n, -N), or backgrounds with a command (-f)
    ("echo 'rm -rf /data' | ssh -n prod-db ssh inner", "shell"),
    ("echo 'rm -rf /data' | ssh -N prod-db ssh inner", "shell"),
    # the inner ssh does not read its stdin, or has a remote command of its own
    ("echo 'rm -rf /data' | ssh prod-db ssh -n inner", "shell"),
    ("echo 'rm -rf /data' | ssh prod-db ssh inner ls", "read"),
    ("echo 'rm -rf /data' | ssh prod-db ssh inner cat", "read"),
    # no stdin on the line at all
    ("ssh prod-db ssh inner", "shell"),
    # a compound fed nothing, or fed only /dev/null
    ("{ ssh prod-db; } < /dev/null", "shell"),
    # an input process substitution is not fed: <( ) commands keep their own stdin
    ("diff <(ssh prod-db) /etc/hosts", "shell"),
    ("cat <(ssh prod-db)", "shell"),
])
def test_ssh_that_still_does_not_run_a_stdin_keeps_its_reading(command, category):
    c = gate.classify_shell(command)
    assert c.category == category, c
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c, DENY).outcome == "allow"


STORE_DENY_FORMS = [
    {"gates": {"deny": ["store_write"]}},
    {"gates": {"categories": {"store_write": "deny"}}},
]
POST_DENY = {"gates": {"deny": ["github_post"]}}


@pytest.mark.parametrize("command", [
    "declare -a 'a=(>(rm -rf /data))'",
    "local -a 'b=(>(rm -rf /data))'",
    "declare -ai 'b=([>(rm -rf /data)]=1)'",   # a >( ) held in an element's subscript
])
def test_a_process_substitution_in_a_declare_value_is_classified(command):
    """A >( ) declare runs as a value is read is a command of the line, as a $( ) there is: keeping
    >( ) bodies apart for the fed-stdin reading must not drop them from the value's re-read."""
    c = gate.classify_shell(command)
    assert c.category == "delete", c
    assert gate.decide(c).outcome == "ask"
    for policy in DELETE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy


def test_a_process_substitution_in_a_declare_value_can_write_the_store(monkeypatch, tmp_path):
    store = tmp_path / "exo-home"
    monkeypatch.setenv("EXO_HOME", str(store))
    c = gate.classify_shell(f"declare -a 'a=(>(rm -rf {store}))'")
    assert c.category == "store_write", c
    assert gate.decide(c).outcome == "ask"
    for policy in STORE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy


def test_a_post_in_a_declare_value_is_classified():
    c = gate.classify_shell("declare -a 'a=(>(gh pr comment 3 --body hi))'")
    assert c.category == "github_post", c
    assert gate.decide(c, POST_DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    "declare -a 'a=(rm -rf /data)'",   # element text, not a substitution: nothing runs
    "declare -a 'a=(echo hi)'",
])
def test_a_declare_value_without_a_substitution_stays_a_read(command):
    c = gate.classify_shell(command)
    assert c.category == "read", c
    assert gate.decide(c, DENY).outcome == "allow"


# --- the reworked reading (#316 review): a hop anywhere in the remote command, $( ) bodies, own stdin ---

@pytest.mark.parametrize("command", [
    # the hop is not the remote command's first command, or sits behind a wrapper or a compound
    "echo 'rm -rf /data' | ssh prod-db 'cd /; ssh inner'",
    "echo 'rm -rf /data' | ssh prod-db 'true; ssh inner'",
    "echo 'rm -rf /data' | ssh prod-db 'ssh -n other; ssh inner'",
    "echo 'rm -rf /data' | ssh prod-db sudo ssh inner",
    "echo 'rm -rf /data' | ssh prod-db timeout 5 ssh inner",
    "echo 'rm -rf /data' | ssh prod-db '{ ssh inner; }'",
    "echo 'rm -rf /data' | ssh prod-db 'echo $(ssh inner)'",
    # more than one hop: each passes the stdin on to the next
    "echo 'rm -rf /data' | ssh prod-db ssh jump ssh inner",
    "ssh prod-db ssh jump ssh inner <<'EOF'\nrm -rf /data\nEOF",
])
def test_a_hop_anywhere_in_the_remote_command_runs_the_known_text(command):
    c = gate.classify_shell(command)
    assert c.category == "delete", c
    assert c.exposure == "internal" and "remote host" in c.signals, c
    assert any("ssh that runs it" in p.summary for p in gate._each_part(c)), c
    for policy in DELETE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy
    assert gate.decide(c, HOST_DENY).outcome == "deny"
    assert gate.decide(c, INNER_DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    "ssh prod-db 'cd /; ssh inner' < cmds.sh",
    "ssh prod-db ssh jump ssh inner < cmds.sh",
    "{ ssh prod-db 'true; ssh inner'; } < cmds.sh",
    "echo hi > >(ssh prod-db ssh inner)",
    "echo 'rm -rf /data' | ssh prod-db 'while read h; do ssh $h; done'",
])
def test_a_hop_with_a_stdin_the_gate_cannot_read_asks(command):
    c = gate.classify_shell(command)
    assert c.category in ("opaque_exec", "delete"), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


@pytest.mark.parametrize("command, category", [
    # the stdin the outer ssh passes on never reaches an ssh that runs it
    ("echo 'rm -rf /data' | ssh prod-db 'ssh inner < /dev/null'", "shell"),
    ("echo 'rm -rf /data' | ssh prod-db 'ssh -n inner; ls'", "shell"),
    ("echo 'rm -rf /data' | ssh -n prod-db 'cd /; ssh inner'", "shell"),
    ("echo 'rm -rf /data' | ssh -f prod-db 'true; ssh inner'", "shell"),
    ("echo 'rm -rf /data' | ssh prod-db ssh jump ssh -n inner", "shell"),
    ("echo 'rm -rf /data' | ssh prod-db ssh -n jump ssh inner", "shell"),
    ("ssh prod-db 'cd /; ssh inner'", "shell"),
    ("ssh prod-db ssh jump ssh inner", "shell"),
])
def test_a_hop_the_stdin_does_not_reach_keeps_its_reading(command, category):
    c = gate.classify_shell(command)
    assert c.category == category, c
    assert not any("runs" in p.summary and "stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c, DENY).outcome == "allow"


@pytest.mark.parametrize("command", [
    # the inner ssh reads a stdin of its own, not the one the outer ssh passes on: that text is not
    # read as run on a remote host
    "echo 'rm -rf /data' | ssh prod-db 'cat x | ssh inner'",
    "echo 'rm -rf /data' | ssh prod-db 'echo >(ssh inner)'",
    "echo 'rm -rf /data' | ssh prod-db 'ssh inner <<< ls'",
    "echo 'rm -rf /data' | ssh prod-db 'printf ls | ssh jump ssh inner'",   # jump is a hop, prod-db is not
    # a nested shell's line is not fed by the mark: what a $( ) in it inherits is the pipe's
    "echo 'rm -rf /data' | ssh prod-db \"bash -c 'cat x | echo \\$(ssh inner)'\"",
])
def test_an_inner_ssh_with_another_stdin_is_no_hop(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert not any(p.summary.startswith("ssh prod-db passes") for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, DENY).outcome == "ask"


@pytest.mark.parametrize("command", [
    # a $( ), ` ` or <( ) inherits the stdin of the command it is in
    "{ echo $(ssh prod-db); } < cmds.sh",
    "cat cmds.sh | echo $(ssh prod-db)",
    "cat cmds.sh | echo `ssh prod-db`",
    "cat cmds.sh | diff <(ssh prod-db) /etc/hosts",
    "echo hi > >(echo $(ssh prod-db))",
    "while read h; do echo $(ssh prod-db); done < hosts.txt",
    "{ echo $(ssh prod-db); } <<< 'rm -rf /data'",
])
def test_a_substitution_inherits_a_stdin_the_gate_cannot_read(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    # a command's own `<` is set up after its words are expanded, and /dev/null feeds nothing
    "echo $(ssh prod-db) < cmds.sh",
    "{ echo $(ssh prod-db); } < /dev/null",
    "echo $(ssh prod-db ls) < cmds.sh",
    "cat cmds.sh | echo $(ssh -n prod-db)",
    # a command in a >( ) body that sets its own stdin, or tells ssh not to read it, is not fed
    "echo hi > >(ssh prod-db < /dev/null)",
    "echo hi > >(ssh -n prod-db)",
    "echo hi > >({ ssh prod-db; } < /dev/null)",
])
def test_a_command_with_no_unread_stdin_keeps_its_reading(command):
    c = gate.classify_shell(command)
    assert c.category in ("shell", "read"), c
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c, DENY).outcome == "allow"


def test_a_process_substitution_in_a_declare_value_is_fed():
    """declare runs a >( ) in a value as it reads the value, fed by whatever later writes to it."""
    c = gate.classify_shell("declare -a 'a=(>(ssh prod-db))'")
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"


def test_classify_shell_keeps_its_signature():
    """Code wraps classify_shell with a callable of (cmd, depth), as the plugin suite's read-once tests
    do: the fed mark travels beside the call, so such a wrapper still reads >( ) bodies and hops."""
    import inspect
    assert list(inspect.signature(gate.classify_shell).parameters) == ["cmd", "depth"]


def test_a_wrapper_of_cmd_and_depth_still_reads_the_fed_lines(monkeypatch):
    real, seen = gate.classify_shell, []

    def wrapper(cmd, depth=0):
        seen.append(cmd)
        return real(cmd, depth)
    monkeypatch.setattr(gate, "classify_shell", wrapper)
    for command in ("echo 'rm -rf /data' > >(ssh prod-db)", "echo 'rm -rf /data' | ssh prod-db 'cd /; ssh inner'"):
        c = wrapper(command)
        assert gate.decide(c).outcome == "ask", command
    assert "ssh prod-db" in seen and "cd /; ssh inner" in seen, seen


def test_the_fed_mark_names_one_line():
    """A line read as fed does not leave the mark for the next line read, nor for the $( ) beside it."""
    gate.classify_shell("echo hi > >(ssh prod-db)")
    assert gate._fed_stdin.get() is False
    c = gate.classify_shell("ssh prod-db")
    assert c.category == "shell" and gate.decide(c, DENY).outcome == "allow", c
    # the >( ) body is fed, and the $( ) after it is not marked by it
    c = gate.classify_shell("echo hi > >(ssh -n prod-db) $(ssh -T prod-db)")
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c


def test_options_after_the_host_keep_the_stdin_from_the_hop():
    """ssh reads options after the host too: `-n` there keeps it from passing its stdin on."""
    c = gate.classify_shell("echo 'rm -rf /data' | ssh prod-db -n 'cd /; ssh inner'")
    assert c.category != "delete", c
    assert not any("ssh that runs it" in p.summary for p in gate._each_part(c)), c


@pytest.mark.parametrize("command, rule", [
    # #394: a rule that names the ssh does not lower a line where a compound or a >( ) feeds it, as
    # for `{ bash; } < f`: the part that runs the stdin has no own text a rule can name
    ("{ ssh prod-db; } < cmds.sh", "ssh prod-db"),
    ("{ ssh prod-db ssh inner; } < cmds.sh", "ssh prod-db ssh inner"),
    ("echo hi > >(ssh prod-db)", "ssh prod-db"),
])
def test_a_rule_for_the_ssh_does_not_lower_a_fed_line(command, rule):
    c = gate.classify_shell(command)
    allow = {"gates": {"rules": [{"match": {"command": rule, "category": "opaque_exec"}, "decision": "allow"}]}}
    assert gate.decide(c, allow).outcome == "ask", c
    # the same rule allows the ssh where the line itself sets its stdin, as on main
    direct = gate.classify_shell(rule + " < cmds.sh")
    assert gate.decide(direct, allow).outcome == "allow", direct


def test_a_line_read_fed_and_unfed_is_read_both_ways():
    """The same text as a $( ) and as a >( ) body on one line: the fed reading is kept apart."""
    c = gate.classify_shell("echo $(ssh prod-db) > >(ssh prod-db)")
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    c = gate.classify_shell("echo >(ssh prod-db) $(ssh prod-db)")
    assert sum("runs its stdin" in p.summary for p in gate._each_part(c)) == 1, c


def test_a_stand_in_line_keeps_the_mark_for_the_rest_of_its_line():
    c = gate.classify_shell("echo hi > >(__exo_gate_nested_too_deep__\nssh prod-db)")
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c


def test_the_mark_is_spent_by_the_line_it_names():
    """A nested shell in a >( ) body reads its own line unmarked: `bash -c` asks there by itself."""
    c = gate.classify_shell("echo hi > >(bash -c 'ssh -T prod-db')")
    assert c.category == "opaque_exec" and gate.decide(c).outcome == "ask", c
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c


@pytest.mark.parametrize("command", [
    "echo hi > >(ssh prod-db)",
    "ssh prod-db ssh jump ssh inner < cmds.sh",
    "echo 'rm -rf /data' | ssh prod-db 'echo >(ssh inner)'",
    "declare -a 'a=(>(ssh prod-db))'",
    "cat cmds.sh | echo $(ssh prod-db)",
])
def test_the_hop_signal_stays_inside_the_line(command):
    """The ssh around a fed line takes the signal off, so a classification the hook records never carries it."""
    c = gate.classify_shell(command)
    assert gate._LINE_STDIN not in c.signals, c
    assert gate._LINE_STDIN not in gate.classify({"tool": "terminal", "args": {"command": command}}).signals


# --- lowered on purpose (D-038 amendment for #316): the new reading decides as the direct form does ---

OPAQUE_ALLOW_OR_LOG = [
    {"gates": {"categories": {"opaque_exec": "allow"}}},
    {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "log"}]}},
]


@pytest.mark.parametrize("command, direct, entry, outcome", [
    # the part the new reading displaces is sudo's, or one the matrix logs, or the opaque floor's ask
    ("{ sudo ssh prod-db; } < cmds.sh", "sudo ssh prod-db < cmds.sh", {"reserved": ["command:sudo"]}, "ask"),
    ("{ ssh prod-db; git push origin main; } < cmds.sh", "ssh prod-db < cmds.sh; git push origin main",
     {"deny": ["vcs_push"]}, "deny"),
    ("cat cmds.sh | ssh jump ssh prod-db; gh pr merge 3", "cat cmds.sh | ssh prod-db; gh pr merge 3",
     {"deny": ["vcs_merge"]}, "deny"),
    ("echo ls | ssh jump 'ssh prod-db; sudo ls'", "echo ls | ssh prod-db; sudo ls", {"reserved": ["target:jump"]},
     "ask"),
    ("{ sudo ssh prod-db; make test; } < cmds.sh", "sudo ssh prod-db < cmds.sh; make test",
     {"reserved": ["command:sudo"]}, "ask"),
    # a wrapper's own `<` now reaches the ssh it runs: decided as the same wrapper around a shell that reads it
    ("nsenter -t 1 ssh prod-db < cmds.sh", "nsenter -t 1 bash < cmds.sh", {"reserved": ["command:nsenter"]}, "ask"),
])
def test_a_line_lowered_on_purpose_is_decided_as_its_direct_form(command, direct, entry, outcome):
    """Under a policy that allows or logs opaque_exec, these lines decide lower than on main: the newly
    read ssh part is now the deciding part, as it is on main for the direct form. A deny or reserved
    entry for the displaced part still sees it, also beside the policy that allows opaque_exec."""
    c, d = gate.classify_shell(command), gate.classify_shell(direct)
    assert c.category == d.category == "opaque_exec", (c, d)
    for policy in OPAQUE_ALLOW_OR_LOG:
        assert gate.decide(c, policy).outcome == gate.decide(d, policy).outcome, policy
        assert gate.decide(c, {"gates": {**policy["gates"], **entry}}).outcome == outcome, (policy, entry)
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("hop, shell, rule", [
    ("ssh jump 'cd /; ssh prod-db' < cmds.sh", "ssh jump 'cd /; bash' < cmds.sh", "^ssh "),
    ("cat cmds.sh | ssh jump ssh prod-db", "cat cmds.sh | ssh jump bash", "^cat "),
    ("cat cmds.sh | ssh jump 'cd /; ssh prod-db'", "cat cmds.sh | ssh jump 'cd /; bash'", "^cat "),
])
def test_a_rule_for_where_the_stdin_comes_from_decides_a_hop_as_a_remote_shell(hop, shell, rule):
    """#394: the parts that run the stdin a hop passes on take the text that shows where it comes from, the
    outer ssh's own text or the pipeline into it, as a remote bash's part does, so a rule for that text
    decides both alike."""
    allow = {"gates": {"rules": [{"match": {"command": rule, "category": "opaque_exec"}, "decision": "allow"}]}}
    h, s = gate.classify_shell(hop), gate.classify_shell(shell)
    assert h.category == "opaque_exec" and gate.decide(h).outcome == "ask", h
    assert gate.decide(h, allow).outcome == gate.decide(s, allow).outcome == "allow", h
    assert gate.decide(h, {"gates": {"reserved": ["target:jump"]}}).outcome == "ask"


# --- a wrapper runs its command with the wrapper's stdin (_wrapped) ---

OPAQUE_ALLOW = {"gates": {"categories": {"opaque_exec": "allow"}}}


@pytest.mark.parametrize("command", [
    # a run wrapper or a wrapper the gate reads as one: its command is a command of the compound too
    "{ uv run ssh prod-db; } < cmds.sh",
    "(poetry run ssh prod-db) < cmds.sh",
    "while read h; do flock /tmp/l ssh prod-db; done < hosts.txt",
    "{ setsid ssh prod-db; } < cmds.sh",
    "{ nsenter -t 1 ssh prod-db; } < cmds.sh",
    "cat cmds.sh | { uv run ssh prod-db; }",
])
def test_a_compound_feed_reaches_the_command_a_wrapper_runs(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


@pytest.mark.parametrize("command, direct", [
    # the wrapper sets its own stdin, or is piped into: its command does not read the stdin the line is fed
    ("echo hi > >(uv run ssh prod-db < /dev/null)", "uv run ssh prod-db < /dev/null"),
    ("echo hi > >(nsenter -t 1 ssh prod-db < /dev/null)", "nsenter -t 1 ssh prod-db < /dev/null"),
    ("echo hi > >(flock /tmp/l ssh prod-db < /dev/null)", "flock /tmp/l ssh prod-db < /dev/null"),
    ("echo 'rm -rf /data' | ssh jump 'uv run ssh prod-db < /dev/null'", "ssh jump 'uv run ssh prod-db < /dev/null'"),
    ("{ uv run ssh prod-db < /dev/null; } < cmds.sh", "uv run ssh prod-db < /dev/null"),
    ("echo hi > >(uv run uv run ssh prod-db < /dev/null)", "uv run uv run ssh prod-db < /dev/null"),
])
def test_a_wrapper_with_a_stdin_of_its_own_keeps_its_command_unfed(command, direct):
    """The fed mark names the line's stdin. A wrapper whose own `<` or pipe gives it another passes that one to
    its command, so the line decides as the wrapper's own line does, under a policy that allows opaque_exec too."""
    c, d = gate.classify_shell(command), gate.classify_shell(direct)
    assert not any("runs its stdin" in p.summary or "ssh that runs it" in p.summary for p in gate._each_part(c)), c
    for policy in (None, DENY, OPAQUE_ALLOW):
        assert gate.decide(c, policy).outcome == gate.decide(d, policy).outcome, policy


@pytest.mark.parametrize("command", [
    # the line's own parse read the same text unfed, as a $( ), a <( ) or backticks: declare still runs the >( )
    "echo $(ssh prod-db); declare -a 'a=(>(ssh prod-db))'",
    "echo <(ssh prod-db); declare -a 'a=(>(ssh prod-db))'",
    "echo `ssh prod-db`; typeset -a 'a=(>(ssh prod-db))'",
])
def test_a_declare_value_s_process_substitution_is_fed_beside_an_unfed_copy(command):
    c = gate.classify_shell(command)
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


def test_a_declare_value_s_process_substitution_the_line_read_fed_is_read_once():
    c = gate.classify_shell("echo >(ssh prod-db); declare -a 'a=(>(ssh prod-db))'")
    assert sum("runs its stdin" in p.summary for p in gate._each_part(c)) == 1, c
    assert gate.decide(c).outcome == "ask"


COSTLY_DENY = {"gates": {"rules": [{"match": {"category": ["local_write", "shell", "read"], "reversibility": "costly"},
                                    "decision": "deny", "reason": "run by sudo or a privileged wrapper"}]}}


@pytest.mark.parametrize("command", [
    # sudo and the privileged wrappers make the first part of their command costly: ssh's own part, as on main
    "{ sudo ssh prod-db; } < cmds.sh",
    "{ git push origin main; sudo ssh prod-db; } < cmds.sh",
    "echo hi > >(sudo ssh prod-db); gh pr comment 3 --body hi",
    "{ nsenter -t 1 ssh prod-db; } < cmds.sh",
    "echo 'rm -rf /data' | ssh prod-db sudo ssh inner",
    "ssh jump 'sudo ssh prod-db' < cmds.sh",
    "echo 'rm -rf /data' | sudo ssh jump ssh prod-db",
    # the direct form, which main asked about: ssh's own part was built into the part of its stdin
    "sudo ssh prod-db < cmds.sh",
    "cat cmds.sh | nsenter -t 1 ssh prod-db",
])
def test_a_rule_on_costly_parts_still_finds_the_part_sudo_runs(command):
    """ssh's own part stays a part of the line beside the one that runs its stdin, so a rule that denies a part
    sudo or a privileged wrapper runs denies the line, as it did on main where the gate did not read the stdin."""
    c = gate.classify_shell(command)
    assert gate.decide(c, COSTLY_DENY).outcome == "deny", c
    assert gate.decide(c, {"gates": {"categories": {"shell": "deny"}}}).outcome == "deny", c
    assert gate.decide(c).outcome == "ask", c


@pytest.mark.parametrize("command, hits", [
    # the hop's own part keeps the target and category main gave it, beside the part that runs the stdin
    ("echo ls | ssh jump 'ssh prod-db; sudo ls'", {("read", "jump"), ("shell", "jump")}),
    ("echo ls | ssh jump 'ssh prod-db; make test'", {("opaque_exec", "jump"), ("shell", "jump")}),
    ("echo 'rm -rf /data' | ssh jump xargs ssh prod-db", {("opaque_exec", "jump"), ("shell", "jump")}),
])
def test_a_hop_keeps_the_reserved_hits_of_its_own_part(command, hits):
    c = gate.classify_shell(command)
    d = gate.decide(c, {"gates": {"reserved": ["target:jump"]}})
    assert d.outcome == "ask" and hits <= set(d.reserved), d


@pytest.mark.parametrize("command, direct", [
    ("sudo ssh jump 'tee >(ssh prod-db)'", "sudo ssh jump 'ssh prod-db < cmds.sh'"),
    ("echo hi | sudo ssh jump 'cat x | echo $(ssh prod-db)'", "sudo ssh jump 'ssh prod-db < cmds.sh'"),
    ("nsenter -t 1 bash -c 'echo hi > >(ssh prod-db)'", "nsenter -t 1 bash -c 'ssh prod-db < cmds.sh'"),
])
def test_a_line_lowered_on_purpose_under_a_rule_on_costly_parts(command, direct):
    """D-038 amendment for #316, (b): sudo or a privileged wrapper makes costly only the deciding part of a nested
    line it runs. A >( ) or $( ) in that line now reads the ssh as fed, so the new part decides it and the line asks,
    as main decides the same line with a direct `<`. A category line that denies shell still sees the ssh part."""
    c, d = gate.classify_shell(command), gate.classify_shell(direct)
    assert gate.decide(c, COSTLY_DENY).outcome == gate.decide(d, COSTLY_DENY).outcome == "ask", (c, d)
    assert gate.decide(c, {"gates": {"categories": {"shell": "deny"}}}).outcome == "deny"


def test_the_direct_form_keeps_its_reserved_hits():
    """ssh's own part beside the part that runs the stdin keeps the target it had inside it (none): the hop part
    names the host, so each hit main listed is still listed."""
    c = gate.classify_shell("echo 'rm -rf /data' | ssh prod-db")
    d = gate.decide(c, {"gates": {"reserved": ["command:\\brm\\b"]}})
    assert set(d.reserved) == {("delete", "/data"), ("opaque_exec", ""), ("shell", ""), ("shell", "prod-db"),
                               ("read", "")}, d


@pytest.mark.parametrize("command", [
    'while read l; do ssh prod-db "echo $l"; done < f',
    'cat f | grep x | while read l; do ssh prod-db "echo $l"; done',
    'tee >(ssh prod-db "echo $l") < f',
])
def test_a_fed_ssh_s_own_part_keeps_the_host(command):
    """ssh's own part names the host as main named it where it read no stdin, so a reserved entry on the host still
    finds the remote command's category beside the new part."""
    d = gate.decide(gate.classify_shell(command), {"gates": {"reserved": ["target:prod-db"]}})
    assert d.outcome == "ask" and {("read", "prod-db"), ("opaque_exec", "prod-db")} <= set(d.reserved), d


@pytest.mark.parametrize("command, policy, outcome, hits", [
    # a known text #240 reads led the line while ssh's own part, an unknown remote program, was still private
    ("echo 'gh pr merge 1' | ssh $O jump", {"gates": {"categories": {"opaque_exec": "allow"}}}, "log", set()),
    # ssh's own part led, so it kept the host and the part that runs the stdin kept no target
    ("ssh -T $h 'cat | ssh inner' <&3", {"gates": {"reserved": ["opaque_exec", "shell"]}}, "ask",
     {("opaque_exec", ""), ("opaque_exec", "inner"), ("shell", "$h")}),
])
def test_the_direct_form_is_led_and_targeted_as_before(command, policy, outcome, hits):
    """Where the line gives ssh its stdin itself (#240), the part that led when it was built into ssh's own part
    leads beside it, and the target goes where it went."""
    d = gate.decide(gate.classify_shell(command), policy)
    assert d.outcome == outcome and hits <= set(d.reserved), d


def test_a_rule_for_the_outer_ssh_does_not_name_the_sudo_part_of_a_hop():
    """#394: the remote command's own part is a command of the line. sudo makes it costly, so alone it logs,
    and a rule written for the outer ssh does not lower the line past it (main logs it)."""
    c = gate.classify_shell("ssh jump 'sudo ssh prod-db' < cmds.sh")
    allow = {"gates": {"rules": [{"match": {"command": "^ssh ", "category": "opaque_exec"}, "decision": "allow"}]}}
    assert gate.decide(c, allow).outcome == "ask", c


# --- the wrapper's own stdin reaches the command it runs (_wrapped) ---

@pytest.mark.parametrize("command", [
    "uv run ssh prod-db < cmds.sh",
    "cat cmds.sh | uv run ssh prod-db",
    "poetry run ssh prod-db 0<&3",
    "pdm run ssh prod-db <> cmds.sh",
    "hatch run -- ssh prod-db < cmds.sh",
    "cat cmds.sh | pipenv run ssh prod-db",
    "uv run --with x ssh prod-db < cmds.sh",
])
def test_a_run_wrapper_s_stdin_reaches_the_ssh_it_runs(command):
    """main read `uv run ssh host < cmds.sh` as plain ssh: the wrapper's `<` and pipe did not reach its command."""
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    "uv run ssh prod-db < /dev/null",
    "uv run ssh -n prod-db < cmds.sh",
    "uv run ssh prod-db uptime < cmds.sh",
    "cat cmds.sh | uv run ssh prod-db uptime",
])
def test_a_run_wrapper_s_stdin_that_ssh_does_not_run_keeps_its_reading(command):
    c = gate.classify_shell(command)
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c, DENY).outcome == "allow"


def test_a_known_text_piped_into_a_run_wrapper_is_read():
    c = gate.classify_shell("echo 'rm -rf /data' | uv run ssh prod-db")
    assert c.category == "delete", c
    for policy in DELETE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy


# --- a -n, -N or -f before a word that may expand is sure (the everyday `while read h; do ssh -n "$h" ..` loop) ---

@pytest.mark.parametrize("command", [
    'while read h; do ssh -n "$h" uptime; done < hosts.txt',
    'ssh -n "$h" uptime < hosts.txt',
    'ssh -f "$h" uptime < cmds.sh',
    'ssh -N -L 5432:db:5432 $h < cmds.sh',
    'ssh -n -- "$h" < cmds.sh',
    'echo hi > >(ssh -n "$h")',
])
def test_a_flag_before_a_word_that_may_expand_is_sure(command):
    """ssh's -n, -N or -f before such a word is an option whatever the word becomes, so ssh does not read its stdin.
    main read the stdin of the direct form as run (#240), and the loop asked once a compound's feed was read."""
    c = gate.classify_shell(command)
    assert not any("runs its stdin" in p.summary or "ssh that runs it" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c, DENY).outcome == "allow"


@pytest.mark.parametrize("command", [
    "ssh $OPTS -n host < cmds.sh",
    'ssh -o "$X" -n host < cmds.sh',
    'ssh "$h" -n < cmds.sh',
    "echo 'rm -rf /data' | ssh $X -n prod-db ssh inner",
    'ssh -- -n "$h" < cmds.sh',
    'ssh -T -- -n "$h" < cmds.sh',
])
def test_a_flag_after_a_word_that_may_expand_is_not_sure(command):
    """The word may end the options or turn the flag into a word of the remote command. After a `--` the flag is no
    option at all: ssh reads `-n` as the host, and `"$h"` may be empty, so no remote command."""
    c = gate.classify_shell(command)
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("command", [
    'ssh -l -n "$h" < cmds.sh',
    'ssh -ln "$h" < cmds.sh',
    'ssh -o -n "$h" < cmds.sh',
    'ssh -i -n "$h" uptime < cmds.sh',
    '{ ssh -l -n "$h"; } < cmds.sh',
])
def test_a_flag_letter_that_is_an_option_s_value_is_not_sure(command):
    """`-n` after an option that takes a value is that value (a login name, a key file), so ssh still reads its
    stdin, and the word that may expand still leaves the rest uncountable."""
    c = gate.classify_shell(command)
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("command", [
    "ssh prod-db -n $CMD < cmds.sh",
    'ssh prod-db -N "$@" < cmds.sh',
    "ssh prod-db -T -f *.sh < cmds.sh",
    "echo 'rm -rf /data' | ssh prod-db -n $CMD",
])
def test_a_flag_after_the_host_before_a_word_that_may_expand_is_sure(command):
    """ssh reads options after the host too, so a -n there, before the word that may expand, still keeps ssh from
    reading its stdin. The line asks for its remote command, which the gate cannot count, not for the stdin."""
    c = gate.classify_shell(command)
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert c.category != "delete", c


# --- a >( ) is one of the line's substitutions for the #465 store folder too ---

@pytest.fixture
def _store_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("_store_home")
@pytest.mark.parametrize("command, fed", [
    ("cd ~/.exo/policies && echo >(cp /tmp/evil gates.yaml)", False),
    ("cd ~/.exo/policies && tee >(ssh prod-db) < f", True),
    ("cd ~/.exo/policies && { echo $(ssh prod-db); } < f", True),
])
def test_a_fed_substitution_after_cd_into_the_store_runs_there(command, fed):
    """A >( ) or a $( ) after a `cd` into the store runs in the store (#465), also when it is read as fed: the line
    is a store write, and the ssh in it still runs the stdin it is fed."""
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    assert c.category == "store_write", c
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)) == fed, c
    assert gate.decide(c, gate.normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny"
    if fed:
        assert gate.decide(c, gate.normalize_policies({"gates": {"deny": ["opaque_exec"]}})).outcome == "deny"


@pytest.mark.usefixtures("_store_home")
def test_a_process_substitution_before_cd_into_the_store_does_not_run_there():
    c = gate.classify({"tool": "bash", "args": {"command": "echo >(cp /tmp/evil gates.yaml); cd ~/.exo/policies"}})
    assert c.category != "store_write", c
