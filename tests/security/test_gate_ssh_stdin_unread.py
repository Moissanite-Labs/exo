"""ssh stdin the gate cannot read the text of still runs on the remote host (#316).

Three spellings gave an ssh with no remote command a stdin the gate did not see, so the line read as
plain `ssh` and was allowed: a compound redirected from a file (the closer's `<` feeds nothing the gate
can read), an output process substitution (the commands in a >( ) are fed what the outer command
writes), and a remote ssh hop (`ssh a ssh b` passes its stdin to `ssh b`, whose login shell runs it).
Each now asks like `ssh host < file` does, and a known text fed through a hop is read as shell. The
command strings are data for the classifier; nothing here runs them.
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
    "generate | { ssh prod-db; }",                    # a pipe in whose text is not known, same mark
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
    assert any("runs its stdin" in p.summary and "inner" in p.summary for p in gate._each_part(c)), c
    for policy in DELETE_DENY_FORMS:
        assert gate.decide(c, policy).outcome == "deny", policy
    assert gate.decide(c, INNER_DENY).outcome == "deny"
    assert gate.decide(c, HOST_DENY).outcome == "deny"
    assert gate.decide(c).outcome == "ask"


def test_a_hop_with_an_unread_stdin_asks():
    c = gate.classify_shell("generate | ssh prod-db ssh inner")
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
