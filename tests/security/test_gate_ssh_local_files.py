"""ssh's own options that name a file it makes on this machine are store writes whatever the remote line runs (#484).

ssh makes its control socket (`-S`, `-o ControlPath`), adds keys to its known-hosts file (`-o UserKnownHostsFile`)
and makes a forward's socket (`-L`, `-R`, `-D`, `-o LocalForward`, `-o RemoteForward`, `-o DynamicForward`) on this
machine. The store check read these words only when the remote line was more than reads, so
`ssh -S ~/.exo/state/ctl host ls` was allowed. scp and sftp hand their `-o` settings to ssh.
Each line here is only classified and decided. No command is executed.
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
    "ssh -S '${HOME}/.exo/state/ctl' host ls",              # ssh expands ${HOME} itself
    "ssh -o 'UserKnownHostsFile=${HOME}/.exo/state/kh' host ls",
    "ssh -S ~/.exo/sta{te,x}/ctl host ls",
    "ssh -S ~/.exo/sta$(:)te/ctl host ls",
    "ssh -S ~/.exo/policies/ctl host ls",
    "cd ~/.exo/state && ssh -S ctl host ls",
    "cd ~/.exo/state && ssh -o ControlPath=./ctl host ls",
    "cd ~/.exo/state && ssh -L ./sock:localhost:5432 host ls",
    # an ssh in the remote line
    "ssh a 'ssh -S ~/.exo/state/ctl b ls'",
    # ssh's own quotes in a setting and a forward's brackets and backslashes, which ssh takes out
    "ssh -o 'ControlPath ~/.exo/\"st\"ate/ctl' host ls",
    "ssh -o \"ControlPath=~/.exo/st'a'te/ctl\" host ls",
    "ssh -o 'UserKnownHostsFile /tmp/kh ~/.exo/\"state\"/kh' host ls",
    "ssh -o 'LocalForward ~/.exo/\"state\"/sock localhost:5432' host ls",
    "ssh -o 'LocalForward /x/.exo/st\\ate/sock localhost:5432' host ls",
    "ssh -L '/x/.exo/st\\ate/sock:localhost:5432' host ls",
    "ssh -R \"$HOME/.exo/st\\ate/sock:localhost:22\" host ls",
    "ssh -L '[/x/.exo/state/sock]:localhost:5432' host ls",
    "ssh -o ControlPath=%d/.exo/state/ctl host ls",
    # a dynamic forward on a socket path: ssh listens on that socket here, as for -L
    "ssh -D ~/.exo/state/sock host ls",
    "ssh -D~/.exo/state/sock host ls",
    "ssh -o 'DynamicForward ~/.exo/state/sock' host ls",
    "ssh -D '[~/.exo/state/sock]' host ls",
    "cd ~/.exo/state && ssh -D ./sock host ls",
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
    "cd ~/.exo/state && ssh -o \"ControlPath='/tmp/ctl'\" host ls",
    "cd ~/.exo/state && ssh -o ControlPath=%d/cm-%C host ls",
    "ssh -o \"ControlPath '/tmp/a b/ctl'\" host ls",
    "ssh -o 'UserKnownHostsFile \"/tmp/a b/kh\" /dev/null' host ls",
    "ssh -D 1080 host ls",
    "ssh -D localhost:1080 host ls",
    "ssh -D /tmp/socks.sock host ls",
    "cd ~/.exo/state && ssh -D 1080 host ls",
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


@pytest.mark.parametrize("command", [
    "ssh -o 'ControlPath ~/.exo/\"st\"ate/ctl' a 'bash -c x'",
    "ssh -o 'ControlPath ~/.exo/\"st\"ate/ctl' a 'ssh b true'",
    "ssh -L '/x/.exo/st\\ate/sock:localhost:5432' a 'bash -c x'",
])
def test_a_remote_line_that_is_more_than_reads_gets_the_same_reading(command):
    # The word scan for such a line does not take out ssh's own quotes, so ssh's options are read for it too.
    c, d = decide(command, REBIND_ALLOWED)
    assert c.category == "store_write" and d.outcome == "deny"


@pytest.mark.parametrize("value", [
    "-o \"ControlPath '{root}/state/ctl'\"",
    "-o 'ControlPath \"${{EXO_HOME}}/state/ctl\"'",
    "-o 'ControlPath %d/Library/Application\\ Support/Exo\\ Desktop/runtime/state/ctl'",
    "-o \"UserKnownHostsFile /dev/null '{root}/state/kh'\"",
    "-o \"LocalForward '{root}/state/sock' localhost:5432\"",
    "-L '[{root}/state/sock]:localhost:5432'",
    "-S '{root}/state/ctl'",
])
def test_a_store_folder_whose_name_holds_a_space(value, tmp_path, monkeypatch):
    # The desktop app's store is in "Application Support". A setting can only name a path with a space inside
    # ssh's own quotes, which the store check now takes out as ssh does.
    home = tmp_path / "home"
    root = home / "Library" / "Application Support" / "Exo Desktop" / "runtime"
    root.mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(root))
    command = "ssh " + value.format(root=root) + " host ls"
    c, d = decide(command, DENY_STORE)
    assert c.category == "store_write" and d.outcome == "deny", command
    if "{root}" in value:       # the same path outside the store stays a read
        c, d = decide(command.replace(str(root), "/tmp/Exo Desktop"), DENY_STORE)
        assert c.category == "read" and d.outcome == "allow", command


@pytest.mark.parametrize("command", [
    "scp -o 'UserKnownHostsFile ~/.exo/\"st\"ate/kh' host:/x /tmp/y",
    "scp -o \"UserKnownHostsFile=~/.exo/st'a'te/kh\" /tmp/y host:/x",
    "scp -Ao'UserKnownHostsFile ~/.exo/\"st\"ate/kh' host:/x /tmp/y",
    "sudo scp -o 'ControlPath ~/.exo/\"st\"ate/ctl' host:/x /tmp/y",
    "sftp -o 'UserKnownHostsFile ~/.exo/\"st\"ate/kh' host",
    "sftp -b /dev/null -o 'UserKnownHostsFile ~/.exo/\"st\"ate/kh' host",
])
def test_scp_and_sftp_settings_get_the_same_reading(command):
    # scp and sftp hand -o to the ssh they run. The plain spelling was already a store write. ssh's own quotes hid it.
    c, d = decide(command, DENY_STORE)
    assert c.category == "store_write" and d.outcome == "deny", c


@pytest.mark.parametrize("command", [
    "scp -o 'UserKnownHostsFile \"/tmp/a b/kh\"' host:/x /tmp/y",
    "scp -S /usr/bin/ssh host:/x /tmp/y",
    "scp -l 100 host:/x /tmp/y",
    "sftp -o 'UserKnownHostsFile \"/tmp/a b/kh\"' host",
    "sftp -R 64 host",
])
def test_scp_and_sftp_without_a_store_file_keep_their_reading(command):
    c, _ = decide(command, DENY_STORE)
    assert c.category != "store_write", c


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
