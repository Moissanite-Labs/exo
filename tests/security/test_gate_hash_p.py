"""`hash -p PATH NAME` rebinds a program name to another file, so the call is opaque (#330).

`hash -p` makes the shell run PATH for every later NAME, so a file planted by an earlier local write
runs under a trusted program's name, and the gate used to classify the line as that program (`read`).
Plain `hash`, `hash -r` and `hash -d` only read or clear the table and stay `read`, as do a `-p`
after the first operand (bash stops reading options there) and a `-p` with no name after its path
(bash binds nothing). Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F", "forbid_words": ["Zork"]}})


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


ASK = [
    "hash -p /tmp/x ls; ls",                 # the issue's repros
    "builtin hash -p ./tool git; git log",
    "command hash -p /tmp/x ls; ls",
    "hash -rp /tmp/x ls; ls",                # -p in a cluster of valueless flags
    "hash -lp /tmp/x ls; ls",
    "hash -p/tmp/x ls; ls",                  # the path attached to -p
    "hash -l -p /tmp/x ls; ls",              # -p in a later option word
    "hash -d -p /tmp/x git; git log",
    "hash -p $P ls; ls",                     # a path the gate cannot read
]

READ = [
    "hash",
    "hash -r",
    "hash -d ls",
    "hash -t ls",
    "hash -l",
    "hash -p /tmp/x",                        # no name after the path: bash binds nothing
    "hash ls -p /tmp/x; ls",                 # an option after the first operand is a name, not an option
    "hash -- -p /tmp/x ls; ls",
    "ls",
    "git log",
]


@pytest.mark.parametrize("line", ASK)
def test_hash_p_rebinding_a_name_asks(line):
    c = classify(sh(line))
    assert c.category == "opaque_exec", c
    assert decide(c, POLICY).outcome == "ask", (line, decide(c, POLICY))


@pytest.mark.parametrize("line", READ)
def test_hash_without_a_rebind_stays_read(line):
    c = classify(sh(line))
    assert c.category == "read", c
    assert decide(c, POLICY).outcome == "allow", (line, decide(c, POLICY))


def test_the_card_names_the_rebound_program_and_its_file():
    c = classify(sh("hash -p /tmp/x ls; ls"))
    assert c.summary == "hash -p makes ls run /tmp/x", c
    c = classify(sh("builtin hash -p ./tool git; git log"))
    assert c.summary == "hash -p makes git run ./tool", c
