"""ssh's own options that name a file it makes on this machine are store writes whatever the remote line runs (#484).

ssh makes its control socket (`-S`, `-o ControlPath`), adds keys to its known-hosts file (`-o UserKnownHostsFile`)
and makes a forward's socket (`-L`, `-R`, `-o LocalForward`, `-o RemoteForward`) on this machine. The store check
read these words only when the remote line was more than reads, so `ssh -S ~/.exo/state/ctl host ls` was allowed.
Each line here is classified and decided only; no command is executed.
"""
from __future__ import annotations

import pytest

from exo import gate

DENY_STORE = {"gates": {"deny": ["store_write"]}}
CATEGORY_DENY = {"gates": {"categories": {"store_write": "deny"}}}
REBIND_ALLOWED = {"gates": {"categories": {"opaque_exec": "allow", "store_write": "deny"}}}

STORE_WRITES = [
    # the rows of #484 and its comments
    "ssh -o UserKnownHostsFile=~/.exo/state/kh host ls",
    "ssh -S ~/.exo/state/ctl host ls",
    "ssh -L ~/.exo/state/sock:localhost:5432 host ls",
    "ssh -R ~/.exo/state/sock:localhost:22 host ls",
    "ssh -o ControlPath=~/.exo/state/ctl host ls",
    "ssh -S ~/.exo/state/ctl host 'cat x; sort x'",
    "ssh -S ~/.exo/state/ctl a 'ssh b true'",
    # attached, clustered and setting spellings
    "ssh -S~/.exo/state/ctl host ls",
    "ssh -vS ~/.exo/state/ctl host ls",
    "ssh -vS~/.exo/state/ctl host ls",
    "ssh -oControlPath=~/.exo/state/ctl host ls",
    "ssh -o controlpath=~/.exo/state/ctl host ls",
    "ssh -o 'ControlPath ~/.exo/state/cm-%r@%h:%p' host ls",
    "ssh -o 'UserKnownHostsFile ~/.exo/state/kh' host ls",
    "ssh -o 'UserKnownHostsFile=/tmp/kh ~/.exo/state/kh' host ls",
    "ssh -L~/.exo/state/sock:localhost:5432 host ls",
    "ssh -o LocalForward='~/.exo/state/sock localhost:5432' host ls",
    "ssh -o 'RemoteForward ~/.exo/state/sock localhost:22' host ls",
    # other option orders, the host's options, wrappers
    "ssh -p 22 -S ~/.exo/state/ctl host ls",
    "ssh -S ~/.exo/state/ctl -- host ls",
    "ssh host -o ControlPath=~/.exo/state/ctl ls",
    "/usr/bin/ssh -S ~/.exo/state/ctl host ls",
    "sudo ssh -S ~/.exo/state/ctl host ls",
    "env A=1 ssh -S ~/.exo/state/ctl host ls",
    "x=$(ssh -S ~/.exo/state/ctl host ls)",
    # store paths spelt through HOME, a brace set, an empty substitution or a cd into the store
    "ssh -S $HOME/.exo/state/ctl host ls",
    "ssh -S ~/.exo/sta{te,x}/ctl host ls",
    "ssh -S ~/.exo/sta$(:)te/ctl host ls",
    "ssh -S ~/.exo/policies/ctl host ls",
    "cd ~/.exo/state && ssh -S ctl host ls",
    "cd ~/.exo/state && ssh -o ControlPath=./ctl host ls",
    "cd ~/.exo/state && ssh -L ./sock:localhost:5432 host ls",
    # an ssh in the remote line
    "ssh a 'ssh -S ~/.exo/state/ctl b ls'",
]

READS = [
    "ssh host ls",
    "ssh -S /tmp/ctl host ls",
    "ssh -o ControlPath=/tmp/cm-%r@%h:%p host ls",
    "ssh -o ControlPath=none host ls",
    "ssh -o UserKnownHostsFile=/dev/null host ls",
    "ssh -o UserKnownHostsFile=/tmp/kh host ls",
    "ssh -o GlobalKnownHostsFile=~/.exo/state/kh host ls",
    "ssh -F ~/.exo/state/cfg host ls",
    "ssh -L 5432:localhost:5432 host ls",
    "ssh -R 8022:localhost:22 host ls",
    "ssh host ls -S ~/.exo/state/ctl",
    "ssh -p 22 host cat ~/.exo/state/x",
    "cd ~/.exo/state && ssh -S /tmp/ctl host ls",
    "cd ~/.exo/state && ssh -L 5432:localhost:5432 host ls",
    "cd ~/.exo/state && ssh -o ControlPath=none host ls",
]


@pytest.fixture(autouse=True)
def isolated_gate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_PRINCIPAL_STORE", "EXO_KEYS_FILE", "EXO_VENV", "EXO_PLUGIN_DIRS"):
        monkeypatch.delenv(name, raising=False)


def decide(command: str, policy: dict | None = None):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c, gate.decide(c, gate.normalize_policies(policy or {}))


@pytest.mark.parametrize("command", STORE_WRITES)
def test_ssh_local_file_in_the_store_is_a_store_write(command):
    c, d = decide(command, DENY_STORE)
    assert c.category == "store_write", c
    assert d.outcome == "deny", d
    assert decide(command, CATEGORY_DENY)[1].outcome == "deny"
    assert decide(command)[1].outcome == "ask"


@pytest.mark.parametrize("command", READS)
def test_ssh_without_a_store_file_stays_a_read(command):
    c, d = decide(command, DENY_STORE)
    assert c.category == "read", c
    assert d.outcome == "allow", d


def test_a_key_in_the_store_stays_credentials():
    c, d = decide("ssh -i ~/.exo/keys/seat host ls")
    assert c.category == "credentials" and d.outcome == "ask"


@pytest.mark.parametrize("remote", ["'hash -p /x ls'", "'ssh b true; hash -p /x ls'"])
def test_a_rebind_in_the_remote_line_does_not_hide_the_socket(remote):
    # The store check takes a hash -p rebind as a read (#330), so these lines asked as opaque_exec and an
    # opaque_exec allow let the socket through.
    c, d = decide(f"ssh -S ~/.exo/state/ctl a {remote}", REBIND_ALLOWED)
    assert c.category == "store_write" and d.outcome == "deny"


def test_the_remote_target_rule_is_kept():
    _, d = decide("ssh -S ~/.exo/state/ctl host ls", {"gates": {"deny": ["target:host"]}})
    assert d.outcome == "deny"


@pytest.mark.parametrize("command", [
    "mosh --ssh='ssh -S ~/.exo/state/ctl' host ls",
    "mosh --ssh='ssh -o ControlPath=~/.exo/state/ctl' host",
    "ssh -S ~/.exo/state/ctl host 'sort x'",
    "ssh -S ~/.exo/state/ctl host",
])
def test_lines_main_read_as_store_writes_stay_so(command):
    c, d = decide(command, DENY_STORE)
    assert c.category == "store_write" and d.outcome == "deny"
