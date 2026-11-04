"""SSH_SK_PROVIDER picks the library ssh loads on this machine, so setting it asks as LD_PRELOAD does (#1134).

ssh takes its FIDO security key library from SecurityKeyProvider, whose default is $SSH_SK_PROVIDER, and
ssh-sk-helper loads that library here when a key is used. `ssh -o SecurityKeyProvider=/tmp/evil.so host ls` was
already opaque_exec (#232, #1083), but the variable was not read at all, so `SSH_SK_PROVIDER=/tmp/evil.so ssh host
ls` was a read that every policy allowed, also through env, export or git, which runs ssh. The variable is now one of
the loader variables (_EXEC_ENV_OPAQUE), read wherever the line sets it. Its opaque_exec part goes beside the line, as
#1083's part for an ssh setting that runs a command here does, so the line keeps main's category, target and reserved
hits. `internal`, ssh's built-in support, loads no file and stays a read, as it is for the -o setting. Each line here
is only classified and decided. No command is run.
"""
from __future__ import annotations

import pytest

from exo import gate

# The outcome under each of the issue's policies, in this order.
POLICIES = {
    "default": {},
    "deny local_write": {"gates": {"deny": ["local_write"]}},
    "opaque_exec allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
    "deny opaque_exec": {"gates": {"deny": ["opaque_exec"]}},
}
LIBRARY = ("ask", "ask", "allow", "deny")
READ = ("allow", "allow", "allow", "allow")
PART = "SSH_SK_PROVIDER changes which libraries are loaded"


def rule(command: str, decision: str = "allow") -> dict:
    return {"match": {"command": command}, "decision": decision, "reason": f"{decision} {command}"}


# The issue's rows that main read as reads, allowed under every policy. They stay reads, with the part beside.
ISSUE = [
    "SSH_SK_PROVIDER=/tmp/evil.so ssh host ls",
    "env SSH_SK_PROVIDER=/tmp/evil.so ssh host ls",
    "export SSH_SK_PROVIDER=/tmp/evil.so; ssh host ls",
    "SSH_SK_PROVIDER=/tmp/evil.so git fetch",
]

# Each way a line sets the variable, as for LD_PRELOAD: (line, main's category, outcomes). Main allowed or logged
# each.
FORMS = [
    ("declare -x SSH_SK_PROVIDER=/tmp/evil.so; ssh host ls", "read", LIBRARY),
    ("typeset -x SSH_SK_PROVIDER=/tmp/evil.so; ssh host ls", "read", LIBRARY),
    ("readonly SSH_SK_PROVIDER=/tmp/evil.so; ssh host ls", "read", LIBRARY),
    # set bare earlier on the line: an exported variable keeps its new value
    ("SSH_SK_PROVIDER=/tmp/evil.so; ssh host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER=/tmp/evil.so; export SSH_SK_PROVIDER; ssh host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER=internal; SSH_SK_PROVIDER+=/tmp/evil.so; ssh host ls", "read", LIBRARY),
    ("read SSH_SK_PROVIDER; ssh host ls", "read", LIBRARY),
    ("printf -v SSH_SK_PROVIDER /tmp/evil.so; ssh host ls", "read", LIBRARY),
    (": ${SSH_SK_PROVIDER:=/tmp/evil.so}; ssh host ls", "read", LIBRARY),
    ("for SSH_SK_PROVIDER in /tmp/evil.so; do ssh host ls; done", "read", LIBRARY),
    # through a wrapper, quoted whole after env, or after an internal value the later one replaces
    ("sudo SSH_SK_PROVIDER=/tmp/evil.so ssh host ls", "read", ("ask", "ask", "log", "deny")),
    ("sudo env SSH_SK_PROVIDER=/tmp/evil.so ssh host ls", "read", ("ask", "ask", "log", "deny")),
    ("timeout 5 env SSH_SK_PROVIDER=/tmp/evil.so ssh host ls", "read", LIBRARY),
    ("env 'SSH_SK_PROVIDER=/tmp/evil.so' ssh host ls", "read", LIBRARY),
    ("env SSH_SK_PROVIDER=internal SSH_SK_PROVIDER=/tmp/evil.so ssh host ls", "read", LIBRARY),
    # in a subshell, a substitution, a pipe, a group or a body, and as a hop's remote line
    ("(SSH_SK_PROVIDER=/tmp/evil.so ssh host ls)", "read", LIBRARY),
    ("echo $(SSH_SK_PROVIDER=/tmp/evil.so ssh host ls)", "read", LIBRARY),
    ("echo x | SSH_SK_PROVIDER=/tmp/evil.so ssh host ls", "read", LIBRARY),
    ("{ SSH_SK_PROVIDER=/tmp/evil.so ssh host ls; }", "read", LIBRARY),
    ("if true; then SSH_SK_PROVIDER=/tmp/evil.so ssh host ls; fi", "read", LIBRARY),
    ("ssh a 'SSH_SK_PROVIDER=/tmp/evil.so ssh b ls'", "read", LIBRARY),
    # programs that run ssh, and lines main already asked on, which `deny: [opaque_exec]` now denies
    ("SSH_SK_PROVIDER=/tmp/evil.so scp /tmp/a host:b", "shell", LIBRARY),
    ("SSH_SK_PROVIDER=/tmp/evil.so rsync -a /tmp/a host:b", "shell", LIBRARY),
    ("SSH_SK_PROVIDER=/tmp/evil.so git push origin main", "vcs_push", ("ask", "ask", "log", "deny")),
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh-add -K", "credentials", ("ask", "ask", "ask", "deny")),
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh-keygen -t ed25519-sk -f /tmp/k", "credentials", ("ask", "ask", "ask", "deny")),
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh host 'rm -rf /srv/data'", "delete", ("ask", "ask", "ask", "deny")),
    # a value other than `internal` asks: a name the loader looks up, a variable, `none`, an empty value
    ("SSH_SK_PROVIDER=libsk.so ssh host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER=$X ssh host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER=none ssh host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER= ssh host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER=internal* ssh host ls", "read", LIBRARY),
    # the -o setting wins over the variable, but the gate does not read which one ssh takes
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh -o SecurityKeyProvider=internal host ls", "read", LIBRARY),
]

# Beside #1083's ssh settings, and in the command they run here: (line, main's category). Main had no such part, so
# the nested lines asked only for the setting, and the -J line was allowed.
WITH_SETTINGS = [
    ("ssh -o 'ProxyCommand=SSH_SK_PROVIDER=/tmp/evil.so ssh -W %h:%p jump' host ls", "read"),
    ("ssh -o PermitLocalCommand=yes -o 'LocalCommand=SSH_SK_PROVIDER=/tmp/evil.so ssh b ls' host ls", "read"),
    ("ssh -o 'KnownHostsCommand=env SSH_SK_PROVIDER=/tmp/evil.so ssh b cat' host ls", "read"),
    ("git -c core.sshCommand='SSH_SK_PROVIDER=/tmp/evil.so ssh' fetch", "opaque_exec"),
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh -o 'ProxyCommand=nc %h %p' host ls", "opaque_exec"),
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh -J jump host ls", "read"),
]

# The part goes beside the line, so these keep what main decided (main's gate_diff found each lower when the part led
# the line): a category rule on opaque_exec does not lift #1083's part, a reserved hit on that part stays one on
# another part than the line's own, and a reserved command entry still meets the line's own read.
ON_THE_SIDE = [
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh -o 'ProxyCommand=touch /tmp/x' host ls",
     {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "allow"}]}}, None),
    ("SSH_SK_PROVIDER=/tmp/evil.so ssh -o 'ProxyCommand=touch /tmp/x' host ls",
     {"gates": {"reserved": ["opaque_exec"]}}, ("opaque_exec", "")),
    ("SSH_SK_PROVIDER=$X ssh host ls", {"gates": {"reserved": ["command:SSH_SK_PROVIDER"]}}, ("read", "host")),
]

# Lines whose reading stays as main made it: the issue's controls, `internal` in any case and form, the -o
# setting's own `internal` and `none`, unsetting the variable, and lines that only name it.
KEPT = [
    ("ssh -o SecurityKeyProvider=/tmp/evil.so host ls", "opaque_exec", LIBRARY),
    ("LD_PRELOAD=/tmp/evil.so ssh host ls", "opaque_exec", LIBRARY),
    ("SSH_SK_PROVIDER=internal ssh host ls", "read", READ),
    ("SSH_SK_PROVIDER=INTERNAL ssh host ls", "read", READ),
    ("SSH_SK_PROVIDER=Internal git fetch", "read", READ),
    ("env SSH_SK_PROVIDER=internal ssh host ls", "read", READ),
    ("export SSH_SK_PROVIDER=internal; ssh host ls", "read", READ),
    ("declare -x SSH_SK_PROVIDER=internal; ssh host ls", "read", READ),
    ("ssh -o SecurityKeyProvider=internal host ls", "read", READ),
    ("ssh -o SecurityKeyProvider=none host ls", "read", READ),
    ("SSH_SK_PROVIDER=internal ssh -o SecurityKeyProvider=/tmp/evil.so host ls", "opaque_exec", LIBRARY),
    # #1083's setting still asks with `internal` in its command, and `internal` beside -J stays a read
    ("ssh -o 'ProxyCommand=SSH_SK_PROVIDER=internal ssh -W %h:%p jump' host ls", "read", LIBRARY),
    ("SSH_SK_PROVIDER=internal ssh -J jump host ls", "read", READ),
    ("env -u SSH_SK_PROVIDER ssh host ls", "read", READ),
    ("unset SSH_SK_PROVIDER; ssh host ls", "read", READ),
    ("echo $SSH_SK_PROVIDER", "read", READ),
    ("echo SSH_SK_PROVIDER=/tmp/evil.so", "read", READ),
    ("grep SSH_SK_PROVIDER=x file", "read", READ),
    ("SSH_SK_PROVIDERX=/tmp/evil.so ssh host ls", "read", READ),
    ("ssh host ls", "read", READ),
]


@pytest.fixture(autouse=True)
def isolated_gate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_PRINCIPAL_STORE", "EXO_KEYS_FILE", "EXO_VENV", "EXO_PLUGIN_DIRS", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)


def decide(command: str, policy: dict | None = None):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c, gate.decide(c, gate.normalize_policies(policy or {}))


def outcomes(command: str) -> tuple[str, ...]:
    return tuple(decide(command, p)[1].outcome for p in POLICIES.values())


def library_parts(c) -> list[str]:
    return [p.summary for p in gate._each_part(c) if p.category == "opaque_exec" and PART in p.summary]


@pytest.mark.parametrize("command", ISSUE)
def test_the_issue_rows_ask_as_the_setting_does(command):
    # main: read, allowed under all four
    c, _ = decide(command)
    assert c.category == "read", (command, c)
    assert library_parts(c) == [PART], (command, c)
    assert outcomes(command) == LIBRARY, command


@pytest.mark.parametrize("command,category,expected", FORMS)
def test_each_way_of_setting_it_asks(command, category, expected):
    c, _ = decide(command)
    assert library_parts(c), (command, c)
    assert c.category == category, (command, c)
    assert outcomes(command) == expected, command


@pytest.mark.parametrize("command", [*ISSUE, "SSH_SK_PROVIDER=/tmp/evil.so ssh-add -K"])
@pytest.mark.parametrize("policy,expected", [
    pytest.param({"gates": {"reserved": ["opaque_exec"]}}, "ask", id="reserved"),
    pytest.param({"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "deny"}]}}, "deny",
                 id="category rule"),
    pytest.param({"gates": {"categories": {"opaque_exec": "deny"}}}, "deny", id="category deny"),
    # a rule keyed on command does not lift the library part (#394, #458): on ssh, git, env and the variable
    pytest.param({"gates": {"rules": [rule("^ssh")]}}, "ask", id="rule ssh"),
    pytest.param({"gates": {"rules": [rule("^git fetch")]}}, "ask", id="rule git"),
    pytest.param({"gates": {"rules": [rule("^env")]}}, "ask", id="rule env"),
    pytest.param({"gates": {"rules": [rule("SSH_SK_PROVIDER")]}}, "ask", id="rule variable"),
    pytest.param({"gates": {"rules": [rule("^ssh")], "deny": ["opaque_exec"]}}, "deny", id="rule ssh and deny"),
])
def test_deny_reserved_and_rule_entries_meet_the_part(command, policy, expected):
    c, d = decide(command, policy)
    assert library_parts(c), (command, c)
    assert d.outcome == expected, (command, policy, d)
    if "reserved" in policy["gates"]:
        assert any(cat == "opaque_exec" for cat, _ in d.reserved), d


@pytest.mark.parametrize("command,category", WITH_SETTINGS)
def test_beside_and_inside_an_ssh_setting_that_runs_a_command(command, category):
    c, _ = decide(command)
    assert c.category == category, (command, c)
    assert PART in library_parts(c), (command, c)
    assert outcomes(command) == LIBRARY, command


@pytest.mark.parametrize("command,policy,hit", ON_THE_SIDE)
def test_the_part_on_the_side_keeps_what_main_decided(command, policy, hit):
    c, d = decide(command, policy)
    assert d.outcome == "ask", (command, policy, d)
    if hit is not None:
        assert hit in d.reserved, d
        # the hook keeps a standing grant off a call whose reserved hit is not the line's own act (D-075)
        assert any(h != (c.category, c.target) for h in d.reserved), (c.category, c.target, d.reserved)


@pytest.mark.parametrize("command,category,expected", KEPT)
def test_lines_main_read_otherwise_stay_so(command, category, expected):
    c, _ = decide(command)
    assert c.category == category, (command, c)
    assert not library_parts(c), (command, c)
    assert outcomes(command) == expected, command


def test_a_deny_rule_on_the_variable_still_denies():
    # main denied these already, by the rule on the whole line
    for command in (*ISSUE, "SSH_SK_PROVIDER=internal ssh host ls"):
        assert decide(command, {"gates": {"rules": [rule("SSH_SK_PROVIDER", "deny")]}})[1].outcome == "deny"
