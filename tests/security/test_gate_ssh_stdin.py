"""Text fed to ssh's stdin, with no remote command, runs on the remote host (#240).

With no remote command, `ssh host` starts the login shell there, and that shell reads its commands from stdin.
The gate reads a piped, heredoc or here-string text as shell, as it reads `bash -s`, and the line asks. The parts
keep the remote-host exposure of D-075. The command strings are data for the classifier; nothing here runs them.
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

RM_FED = [
    # the issue's rows
    "echo 'rm -rf /data' | ssh prod-db",
    "ssh prod-db <<'EOF'\nrm -rf /data\nEOF",
    # pipe, heredoc and here-string spellings
    "printf 'rm -rf /data\\n' | ssh prod-db",
    "ssh prod-db <<EOF\nrm -rf /data\nEOF",
    "ssh prod-db <<-EOF\n\trm -rf /data\n\tEOF",
    "ssh prod-db <<< 'rm -rf /data'",
    "cat <<'EOF' | ssh prod-db\nrm -rf /data\nEOF",
    "echo 'rm -rf /data' |& ssh prod-db",
    # options before the host that leave stdin to the login shell
    "echo 'rm -rf /data' | ssh -T prod-db",
    "ssh -T prod-db <<'EOF'\nrm -rf /data\nEOF",
    "ssh -T prod-db <<< 'rm -rf /data'",
    "echo 'rm -rf /data' | ssh -tt prod-db",
    "echo 'rm -rf /data' | ssh -T -p 22 prod-db",
    "echo 'rm -rf /data' | ssh -Tp 22 prod-db",
    "echo 'rm -rf /data' | ssh -l admin prod-db",
    "echo 'rm -rf /data' | ssh -o BatchMode=yes prod-db",
    "echo 'rm -rf /data' | ssh admin@prod-db",
    "echo 'rm -rf /data' | ssh ssh://admin@prod-db:2222",
    # ssh reads options after the host too, up to the remote command; `--` and an empty word are no command
    "echo 'rm -rf /data' | ssh prod-db -T",
    "echo 'rm -rf /data' | ssh prod-db -p 22",
    "echo 'rm -rf /data' | ssh prod-db --",
    "echo 'rm -rf /data' | ssh prod-db ''",
    # stdin that reaches ssh through a compound, a prefix or a coprocess
    "{ ssh prod-db; } <<< 'rm -rf /data'",
    "echo 'rm -rf /data' | { ssh prod-db; }",
    "echo 'rm -rf /data' | sudo ssh prod-db",
    "echo 'rm -rf /data' | timeout 5 ssh prod-db",
    "coproc ssh prod-db <<'EOF'\nrm -rf /data\nEOF",
]


@pytest.mark.parametrize("command", RM_FED)
def test_text_fed_to_ssh_runs_on_the_remote_host(command):
    c = gate.classify_shell(command)
    assert c.category == "delete", c
    assert c.exposure == "internal" and "remote host" in c.signals, c
    assert any(p.target == "prod-db" for p in gate._each_part(c)), c
    for policy in DELETE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy
    assert gate.decide(c, HOST_DENY).outcome == "deny"
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("command, category", [
    ("ssh prod-db <<< 'git push --force origin main'", "force_push"),
    ("printf 'gh pr comment 3 --body hi\\n' | ssh prod-db", "github_post"),
    ("ssh -T prod-db <<'EOF'\ncd /srv && git push --force origin main\nEOF", "force_push"),
])
def test_other_categories_fed_to_ssh_are_denied(command, category):
    c = gate.classify_shell(command)
    assert c.category == category, c
    assert gate.decide(c, DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    "echo hi | ssh prod-db",                # a known text with nothing denied in it still runs remotely
    "ssh prod-db < script.sh",              # a file the gate does not read
    "generate | ssh prod-db",               # output the gate does not read
    "ssh prod-db <<< \"$CMDS\"",
])
def test_fed_ssh_asks_like_bash_s(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"


@pytest.mark.parametrize("command", [
    "echo 'rm -rf /data' | ssh $OPTS prod-db",   # may expand to no word: the host and command cannot be counted
    "echo 'rm -rf /data' | ssh prod-db $CMD",    # every word of the command may expand to none
    "echo 'rm -rf /data' | ssh \"$HOST\"",
])
def test_words_that_may_vanish_leave_the_stdin_read(command):
    c = gate.classify_shell(command)
    assert gate.decide(c, DENY).outcome == "deny", c


@pytest.mark.parametrize("command, category", [
    # -n reads stdin from /dev/null, -N runs no command, -f forks only with a command (and implies -n)
    ("echo 'rm -rf /data' | ssh -n prod-db", "shell"),
    ("echo 'rm -rf /data' | ssh -nT prod-db", "shell"),
    ("echo 'rm -rf /data' | ssh -N prod-db", "shell"),
    ("echo 'rm -rf /data' | ssh -f prod-db", "shell"),
    ("ssh -n prod-db <<< 'rm -rf /data'", "shell"),
    ("ssh prod-db < /dev/null", "shell"),
    ("ssh prod-db", "shell"),
    ("ssh -T prod-db", "shell"),
    # a remote command takes the stdin as its own input
    ("echo 'rm -rf /data' | ssh prod-db cat", "read"),
    ("echo 'rm -rf /data' | ssh -T prod-db cat", "read"),
])
def test_ssh_that_does_not_run_its_stdin_keeps_its_reading(command, category):
    c = gate.classify_shell(command)
    assert c.category == category, c
    assert not any("runs its stdin" in p.summary for p in gate._each_part(c)), c
    assert gate.decide(c, DENY).outcome == "allow"


def test_fed_ssh_past_the_depth_cap_asks():
    c = gate.classify_shell("echo 'rm -rf /data' | ssh prod-db", depth=gate._DEPTH)
    assert c.category == "opaque_exec", c
    assert gate.decide(c).outcome == "ask"


def test_fed_remote_shell_still_asks():
    # Known limit: text fed to a remote `bash` is not read yet; the line asks as before (and denies on the host)
    c = gate.classify_shell("printf 'gh pr comment 3 --body hi\\n' | ssh prod-db bash")
    assert gate.decide(c, DENY).outcome == "ask"
    assert gate.decide(c, HOST_DENY).outcome == "deny"
