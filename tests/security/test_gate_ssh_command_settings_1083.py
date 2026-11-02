"""ssh's command settings run on this machine, so they ask as `bash -c` does (#1083).

ssh runs ProxyCommand and LocalCommand with the user's shell and KnownHostsCommand as a program, all on this
machine. XAuthLocation names the program it runs for X11 forwarding. The gate read the first three as the nested
line they hold, so a delete, a post or a store path there led, but a known program such as `touch` was allowed,
while the same line after `bash -c` asks. XAuthLocation was not read at all. Each is now an opaque_exec part beside
the line, which keeps its own reading, and ssh's `%d` token reads as the home folder. Each line here is only
classified and decided. No command is executed.
"""
from __future__ import annotations

import pytest

from exo import gate

# The outcome under each policy, in this order.
POLICIES = {
    "default": {},
    "deny local_write": {"gates": {"deny": ["local_write"]}},
    "deny delete": {"gates": {"deny": ["delete"]}},
    "opaque_exec allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
}
DENY_STORE = {"gates": {"deny": ["store_write"]}}
ASKS = ("ask", "ask", "ask", "allow")
ALLOWS = ("allow", "allow", "allow", "allow")
DELETE = ("ask", "ask", "deny", "ask")

RUNS_HERE = [
    # the rows of #1083
    "ssh -o 'ProxyCommand=touch /tmp/x' host ls",
    "ssh -o PermitLocalCommand=yes -o 'LocalCommand=touch /tmp/x' host ls",
    "ssh -X -o XAuthLocation=/tmp/evil host ls",
    # each setting as ssh's config reader takes it: `=`, a space, attached to -o, any case
    "ssh -o 'ProxyCommand touch /tmp/x' host ls",
    "ssh -oProxyCommand='touch /tmp/x' host ls",
    "ssh -o 'proxycommand=touch /tmp/x' host ls",
    "ssh -o 'PROXYCOMMAND = touch /tmp/x' host ls",
    "ssh -o 'LocalCommand touch /tmp/x' host ls",
    "ssh -oLocalCommand='touch /tmp/x' -oPermitLocalCommand=yes host ls",
    "ssh -o 'KnownHostsCommand=touch /tmp/x' host ls",
    "ssh -o 'KnownHostsCommand /usr/bin/touch /tmp/x' host ls",
    "ssh -o 'ProxyCommand ssh -W %h:%p jump' host ls",      # the host and port tokens stay as written
    "ssh -Y -o 'XAuthLocation /tmp/evil' host ls",
    "ssh -oXAuthLocation=/tmp/evil host ls",
    "ssh -o xauthlocation=/tmp/evil host ls",               # a config file can turn X11 forwarding on
    "ssh -o \"XAuthLocation=$X\" host ls",
    # scp hands its -o settings to ssh
    "scp -o 'ProxyCommand=touch /tmp/x' /tmp/a host:b",
    "scp -oKnownHostsCommand='touch /tmp/x' /tmp/a host:b",
    "scp -o XAuthLocation=/tmp/evil /tmp/a host:b",
    # an ssh line run by another program or on a host
    "rsync -e \"ssh -o 'ProxyCommand=touch /tmp/x'\" /tmp/a host:b",
    "ssh a \"ssh -o 'ProxyCommand=touch /tmp/x' b ls\"",
]

# ssh fills in `%d` as the home folder before it runs LocalCommand or KnownHostsCommand. Main read `%d` as a folder
# name, so these were only deletes in the working folder. The store write is a part beside the delete.
HOME_TOKEN = [
    "ssh -o 'LocalCommand=rm -rf %d/.exo' host ls",
    "ssh -o 'LocalCommand=rm -rf %d' host ls",
    "ssh -o 'KnownHostsCommand=/bin/rm -rf %d/.exo' host ls",
]

# Lines whose reading stays as main made it: the issue's own controls, `none`, ProxyJump, a remote command, a
# literal `%`, and lines main already asked on.
KEPT = [
    ("bash -c 'touch /tmp/x'", "opaque_exec", ASKS),
    ("ssh -o 'ProxyCommand=rm -rf /tmp/work' host ls", "delete", DELETE),
    ("ssh -o 'LocalCommand=rm -rf /tmp/work' host ls", "delete", DELETE),
    ("ssh -o ProxyCommand=none host ls", "read", ALLOWS),
    ("ssh -o 'proxycommand NONE' host ls", "read", ALLOWS),
    ("ssh -o LocalCommand=none -o KnownHostsCommand=none host ls", "read", ALLOWS),
    ("ssh -J jump host ls", "read", ALLOWS),
    ("ssh -o ProxyJump=jump host ls", "read", ALLOWS),
    ("scp -J jump /tmp/a host:b", "shell", ALLOWS),
    ("ssh -o 'RemoteCommand=touch /tmp/x' host", "shell", ALLOWS),
    ("ssh -o 'LocalCommand=rm -rf %%d/.exo' host ls", "delete", DELETE),
    ("ssh -o 'ProxyCommand=nc %h %p' host ls", "opaque_exec", ASKS),
    ("ssh host -o 'ProxyCommand=touch /tmp/x' ls", "opaque_exec", ASKS),
    ("sftp -o 'ProxyCommand=touch /tmp/x' host", "opaque_exec", ASKS),
]


@pytest.fixture(autouse=True)
def isolated_gate_home(tmp_path, monkeypatch):
    # The store sits at ~/.exo, so `%d/.exo` names it.
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


def runs_here(c) -> list[str]:
    return [p.summary for p in gate._each_part(c) if p.category == "opaque_exec" and p.summary.startswith("ssh ")
            and ("runs a command on this machine" in p.summary or "XAuthLocation runs the program" in p.summary)]


@pytest.mark.parametrize("command", RUNS_HERE)
def test_a_command_ssh_runs_here_asks_as_bash_c_does(command):
    c, _ = decide(command)
    assert runs_here(c), (command, c)
    assert outcomes(command) == ASKS, command
    assert decide(command, {"gates": {"deny": ["opaque_exec"]}})[1].outcome == "deny", command


@pytest.mark.parametrize("command", HOME_TOKEN)
def test_the_home_token_is_the_home_folder(command):
    c, d = decide(command)
    assert c.category == "delete", (command, c)
    assert "store_write" in {p.category for p in gate._each_part(c)}, (command, c)
    assert d.outcome == "ask", d
    assert decide(command, DENY_STORE)[1].outcome == "deny", command
    assert decide(command, {"gates": {"reserved": ["store_write"]}})[1].reserved, command
    assert outcomes(command) == DELETE, command


@pytest.mark.parametrize("command,category,expected", KEPT)
def test_lines_main_read_otherwise_stay_so(command, category, expected):
    assert decide(command)[0].category == category, command
    assert outcomes(command) == expected, command


def test_the_part_names_the_setting():
    c, _ = decide("ssh -o 'ProxyCommand=touch /tmp/x' host ls")
    assert runs_here(c) == ["ssh ProxyCommand runs a command on this machine"], c
    c, _ = decide("ssh -X -o XAuthLocation=/tmp/evil host ls")
    assert runs_here(c) == ["ssh XAuthLocation runs the program /tmp/evil"], c


def test_the_line_keeps_its_own_reading():
    # The part sits beside the line. The line keeps the category main gave it, and a remote `rm` still leads and is
    # decided on the host.
    assert decide("ssh -o 'ProxyCommand=touch /tmp/x' host ls")[0].category == "read"
    command = "ssh -o 'ProxyCommand=touch /tmp/x' host 'rm -rf y'"
    c, d = decide(command, {"gates": {"reserved": ["delete"]}})
    assert c.category == "delete" and ("delete", "host") in d.reserved, d
    assert runs_here(c), c
    assert decide(command, {"gates": {"deny": ["opaque_exec"]}})[1].outcome == "deny"


@pytest.mark.parametrize("command,policy,entries", [
    # main's scripts/gate_diff.py found these lost when the new part led: the read's other target on the hop's host,
    # and the remote delete on the host when a store write through `%d` led
    ("ssh a \"ssh -o 'ProxyCommand=touch /tmp/x' b ls\"", {"gates": {"reserved": ["read"]}}, [("read", "b"), ("read", "a")]),
    ("ssh -o 'ProxyCommand=rm -rf %d/.exo' host 'rm -rf y'", {"gates": {"reserved": ["target:host"]}},
     [("shell", "host"), ("delete", "host")]),
])
def test_reserved_entries_main_found_stay(command, policy, entries):
    d = decide(command, policy)[1]
    assert d.outcome == "ask", d
    assert all(e in d.reserved for e in entries), d
