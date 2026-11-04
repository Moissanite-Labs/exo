"""An ssh forward whose socket is a secret store is a credentials part, so the line asks the principal (#1096).

A forward's socket is a file ssh makes (`-L`, `-R`, `-D` and their `-o` settings), and with
`StreamLocalBindUnlink=yes` it first removes what is at that path. The secret check cut the line's words only at
whitespace, so `~/.exo/keys.env:localhost:5432` met no secret name and `ssh -L` on the keys file was a read that the
default policy allowed. `ssh -S` on the same path was credentials. Each forward field the store check reads (#484)
now meets the secret test, and a match is a credentials part beside the line, which keeps its own reading (#311).
Each line here is only classified and decided. No command is executed.
"""
from __future__ import annotations

import pytest

from exo import gate

DENY_STORE = {"gates": {"deny": ["store_write"]}}
DENY_SECRETS = {"gates": {"deny": ["credentials"]}}

SECRET_FORWARDS = [
    # the rows of #1096
    "ssh -L ~/.exo/keys.env:localhost:5432 host ls",
    "ssh -R ~/.exo/keys.env:localhost:22 host ls",
    "ssh -o StreamLocalBindUnlink=yes -L ~/.exo/keys.env:localhost:5432 host ls",
    "ssh -L ~/.config/exo/keys.env:localhost:5432 host ls",
    # each form's socket field: a local socket to a remote socket, a forward's brackets, an attached value
    "ssh -L ~/.exo/keys.env:/tmp/remote.sock host ls",
    "ssh -L '[~/.exo/keys.env]:localhost:5432' host ls",
    "ssh -L~/.exo/keys.env:localhost:5432 host ls",
    "ssh -D '[~/.exo/keys.env]' host ls",
    # the settings, with a space and with `=`
    "ssh -o 'LocalForward [~/.exo/keys.env] localhost:5432' host ls",
    "ssh -o 'LocalForward=[~/.exo/keys.env] localhost:5432' host ls",
    "ssh -o LocalForward='[~/.exo/keys.env] localhost:5432' host ls",
    "ssh -oLocalForward='[~/.exo/keys.env] localhost:5432' host ls",
    "ssh -o 'RemoteForward [~/.exo/keys.env] localhost:22' host ls",
    "ssh -o 'RemoteForward=[~/.exo/keys.env] localhost:22' host ls",
    "ssh -o 'RemoteForward ~/.exo/keys.env:/tmp/remote.sock' host ls",
    "ssh -o 'DynamicForward [~/.exo/keys.env]' host ls",
    "ssh -o DynamicForward='[~/.exo/keys.env]' host ls",
    # scp and sftp hand their -o settings to ssh
    "scp -o 'LocalForward [~/.exo/keys.env] localhost:5432' /tmp/a host:b",
    "scp -o 'LocalForward=[~/.exo/keys.env] localhost:5432' /tmp/a host:b",
    "sftp -o 'LocalForward [~/.exo/keys.env] localhost:5432' host",
    "sftp -o 'LocalForward=[~/.exo/keys.env] localhost:5432' host",
    # other secret names and spellings
    "ssh -L ~/.env:localhost:5432 host ls",
    "ssh -L ~/.config/gh/hosts.yml:localhost:5432 host ls",
    "ssh -L /srv/tls/server.pem:localhost:5432 host ls",
    "ssh -L ~/.config/exo/key*.env:localhost:5432 host ls",
    "ssh -L $HOME/.exo/keys.env:localhost:5432 host ls",
    "ssh -L \"$HOME/.exo/keys.env:localhost:5432\" host ls",
    "ssh -L %d/.exo/keys.env:localhost:5432 host ls",
    "ssh -L ~/.exo/keys.e$(:)nv:localhost:5432 host ls",
    "ssh -L ~/.exo/keys.{env,x}:localhost:5432 host ls",
    "ssh -L ~/.exo/keys.env{:a,:b}:5432 host ls",          # bash makes `-L ~/.exo/keys.env:a:5432` of it
    # wrappers and nested lines
    "sudo ssh -L ~/.exo/keys.env:localhost:5432 host ls",
    "env A=1 ssh -L ~/.exo/keys.env:localhost:5432 host ls",
    "x=$(ssh -L ~/.exo/keys.env:localhost:5432 host ls)",
    "ssh a 'ssh -L ~/.exo/keys.env:localhost:5432 b ls'",
    "mosh --ssh='ssh -L ~/.exo/keys.env:localhost:5432' host ls",
    "echo host | xargs ssh -L ~/.exo/keys.env:localhost:5432",
    "ssh -L ~/.exo/keys.env:localhost:5432 -L ~/.config/gh/hosts.yml:localhost:1 host ls",
]

# Lines main already read as credentials: the -S value, the ssh settings whose path stands as a word of its own, a
# path at the end of a forward, and `cp` over the keys file. They must stay so.
SECRET_LINES = [
    "ssh -S ~/.exo/keys.env host ls",
    "ssh -o ControlPath=~/.exo/keys.env host ls",
    "ssh -o 'UserKnownHostsFile ~/.exo/keys.env' host ls",
    "cp /tmp/x ~/.exo/keys.env",
    "ssh -o 'LocalForward ~/.exo/keys.env localhost:5432' host ls",
    "ssh -o 'LocalForward=~/.exo/keys.env localhost:5432' host ls",
    "ssh -o 'RemoteForward ~/.exo/keys.env localhost:22' host ls",
    "ssh -o DynamicForward=~/.exo/keys.env host ls",
    "ssh -D ~/.exo/keys.env host ls",
    "ssh -R 8022:~/.exo/keys.env host ls",
    "ssh -L 5432:~/.exo/keys.env host ls",
    "ssh -L ~/.ssh/agent.sock:localhost:5432 host ls",
    "scp -o 'LocalForward ~/.exo/keys.env localhost:5432' /tmp/a host:b",
    "ssh -S ~/.netrc -L ~/.exo/keys.env:localhost:5432 host ls",
]

# Forwards to a port, sockets outside the secret stores, and names that only start like one stay reads.
READS = [
    "ssh -L 5432:localhost:5432 host ls",
    "ssh -L 127.0.0.1:5432:localhost:5432 host ls",
    "ssh -L '[::1]:5432:localhost:5432' host ls",
    "ssh -R 8022:localhost:22 host ls",
    "ssh -D 1080 host ls",
    "ssh -D localhost:1080 host ls",
    "ssh -L /tmp/sock:localhost:5432 host ls",
    "ssh -R 8022:/tmp/sock host ls",
    "ssh -D /tmp/socks.sock host ls",
    "ssh -o 'LocalForward /tmp/sock localhost:5432' host ls",
    "ssh -L /tmp/keys.env.d/sock:localhost:5432 host ls",
    "ssh -L ~/.exo/keys.env.bak:localhost:5432 host ls",
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


def secrets(c) -> list[str]:
    return [p.target for p in gate._each_part(c) if p.category == "credentials"]


def assert_secret(command: str) -> None:
    c, d = decide(command)
    assert secrets(c), (command, c)
    assert d.outcome == "ask", (command, d)
    assert decide(command, DENY_SECRETS)[1].outcome == "deny", command
    assert decide(command, DENY_STORE)[1].outcome == "ask", command


@pytest.mark.parametrize("command", SECRET_FORWARDS)
def test_a_forward_on_a_secret_store_is_credentials(command):
    assert_secret(command)


@pytest.mark.parametrize("command", SECRET_FORWARDS[:4])
def test_the_line_keeps_its_own_reading(command, tmp_path):
    # The remote command only reads, so the line stays a read, with the keys file a credentials part beside it.
    c, _ = decide(command)
    assert c.category == "read", c
    keys = ".config/exo/keys.env" if "~/.config/" in command else ".exo/keys.env"
    assert secrets(c) == [str(tmp_path / "home" / keys)]


@pytest.mark.parametrize("command", SECRET_LINES)
def test_lines_main_read_as_credentials_stay_so(command):
    assert_secret(command)
    assert decide(command)[0].category == "credentials"


@pytest.mark.parametrize("command", READS)
def test_a_forward_off_the_secret_stores_stays_a_read(command):
    c, d = decide(command, DENY_SECRETS)
    assert c.category == "read", (command, c)
    assert d.outcome == "allow", (command, d)


def test_the_option_after_the_host_reads_no_lower():
    # ssh reads its options after the host too. Main read this line as opaque_exec, which asks. It stays so, and the
    # keys file is a credentials part beside it, which a credentials deny denies.
    command = "ssh host -L ~/.exo/keys.env:localhost:5432 ls"
    assert_secret(command)
    assert decide(command)[0].category == "opaque_exec"
    assert decide(command, {"gates": {"deny": ["opaque_exec"]}})[1].outcome == "deny"


def test_a_target_entry_on_the_keys_file_meets_the_part():
    command = "ssh -L ~/.exo/keys.env:localhost:5432 host ls"
    assert decide(command, {"gates": {"deny": ["target:*/.exo/keys.env"]}})[1].outcome == "deny"
    assert decide(command, {"gates": {"reserved": ["credentials"]}})[1].reserved


def test_a_store_write_on_the_line_keeps_its_lead_and_deny():
    command = "ssh -L ~/.exo/state/sock:localhost:5432 -L ~/.exo/keys.env:localhost:1 host ls"
    c, d = decide(command, DENY_STORE)
    assert c.category == "store_write" and d.outcome == "deny", c
    assert decide(command, DENY_SECRETS)[1].outcome == "deny"


def test_the_remote_command_keeps_its_targets_on_the_host():
    # Main decided the remote `rm` on the host too, so a reserved delete names the host. The part keeps that.
    command = "ssh -L ~/.exo/keys.env:localhost:5432 host 'rm -rf x'"
    c, d = decide(command, {"gates": {"reserved": ["delete"]}})
    assert c.category == "delete" and ("delete", "host") in d.reserved, d
    assert secrets(c)


@pytest.mark.parametrize("command", [
    "ssh -L {kf}:localhost:5432 host ls",
    "ssh -R 8022:{kf} host ls",                       # -R's local socket is the field after the colon
    "ssh -R /run/user/1/r.sock:{kf} host ls",
    "ssh -o 'LocalForward [{kf}] localhost:5432' host ls",
])
def test_a_keys_file_named_by_exo_keys_file(command, tmp_path, monkeypatch):
    # The keys file can sit anywhere under any name. The secret check finds it by its path, not its name.
    kf = tmp_path / "vault" / "exo-keys"
    monkeypatch.setenv("EXO_KEYS_FILE", str(kf))
    assert_secret(command.format(kf=kf))
    c, _ = decide(command.format(kf=tmp_path / "vault" / "other"))
    assert c.category == "read", c


# #1104 reads a pipe from `echo`, `printf` or `cat <<<` into a shell as the command it prints, also as the first
# command of a group, a body or a condition. #458 lets a rule keyed on `command` lower a line only through the
# command's own text, which leaves out a here-string or here-doc and gives the inner command of `xargs`, `find -exec`
# and `git bisect run` its own words. Through each of them the forward's part decides as its `-S` twin does: an allow
# rule beside a credentials category deny does not lift it.
COMPOSED = [
    "{ echo '%s' | bash; }",
    "if true; then printf '%s\\n' | bash; fi",
    "while cat <<< '%s' | bash; do :; done",
    "! echo -e '%s' | sh",
    "time echo '%s' | sudo -s",
    "{ cat <<EOF | bash; }\n%s\nEOF",
    "%s <<< hi",
    "%s <<EOF\nhi\nEOF",
    "echo hi | xargs %s",
    "find . -exec %s \\;",
    "git bisect run %s",
]
RULES = ["^ssh", "ssh -L", "keys.env", "hi", "^xargs", "^find", "^git", "^bash", ".*"]


@pytest.mark.parametrize("shape", COMPOSED)
def test_a_forward_read_through_a_pipe_or_a_runner_decides_as_its_s_twin(shape):
    line = shape % "ssh -L ~/.exo/keys.env:localhost:5432 host ls"
    twin = shape % "ssh -S ~/.exo/keys.env host ls"
    assert decide(line)[1].outcome == "ask", line
    for command in RULES:
        policy = {"gates": {"rules": [{"match": {"command": command}, "decision": "allow", "reason": command}],
                            "categories": {"credentials": "deny"}}}
        assert decide(line, policy)[1].outcome == decide(twin, policy)[1].outcome == "deny", (line, command)
