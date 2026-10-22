"""`hash -p PATH NAME` rebinds a program name to another file, so the call is opaque (#330).

`hash -p` makes the shell run PATH for every later NAME, so a file planted by an earlier local write
runs under a trusted program's name, and the gate used to classify the line as that program (`read`).
Plain `hash`, `hash -r` and `hash -d` only read or clear the table and stay `read`, as do a `-p`
after the first operand (bash stops reading options there) and a `-p` with no name after its path
(bash binds nothing). Classification and decision only; nothing is executed.
"""
import os
import re

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
    "hash -dp /tmp/x ls; ls",
    "hash -p/tmp/x ls; ls",                  # the path attached to -p
    "hash -l -p /tmp/x ls; ls",              # -p in a later option word
    "hash -d -p /tmp/x git; git log",
    "hash '-p' /tmp/x ls; ls",
    "hash -p $P ls; ls",                     # a path the gate cannot read
    "hash -p /tmp/x -- ls; ls",              # options go on after the path
    "hash -p /tmp/x ./ls git; git log",      # bash skips a name with a slash, not the names after it
    "hash $O ls; ls",                        # an option word the gate cannot read may be -p (exo-36, #409)
    'hash "$O" ls; ls',
    "hash -l $O ls; ls",                     # also after a word it reads: O='-p EVIL' runs EVIL for ls
    'hash -r "$O" ls; ls',
    "hash $O",
    'hash "$@"',
    "hash {-p,/tmp/x} ls; ls",
    "hash -[p] /tmp/x ls; ls",
    "hash -? /tmp/x ls; ls",                 # a glob: with a file named -p in the folder, bash binds
    "hash -* /tmp/x ls; ls",
    "BASH_CMDS[ls]=/tmp/x; ls",              # the table hash -p fills, set as an array element
    "BASH_CMDS=([ls]=/tmp/x); ls",
    "read 'BASH_CMDS[ls]' <<< /tmp/x; ls",
    "printf -v 'BASH_CMDS[ls]' /tmp/x; ls",
    "BASH_ALIASES[ls]=/tmp/x; ls",
]

READ = [
    "hash",
    "hash -r",
    "hash -d ls",
    "hash -t ls",
    "hash -l",
    "hash -p /tmp/x",                        # no name after the path: bash binds nothing
    "hash -rp",                              # no path: bash stops with a usage error
    "hash -p /tmp/x ./ls",                   # the only name has a slash: bash skips it
    "hash -p '' ls; ls",                     # an empty path runs nothing
    "hash ls -p /tmp/x; ls",                 # an option after the first operand is a name, not an option
    "hash -- -p /tmp/x ls; ls",
    'hash "$cmd"',                           # one word with nothing after it binds nothing
    "hash ls $O",                            # words after the first operand are names
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


@pytest.mark.parametrize("line, card", [
    ("hash -p /tmp/x ls; ls", "hash -p makes ls run /tmp/x"),
    ("builtin hash -p ./tool git; git log", "hash -p makes git run ./tool"),
    ("hash -p/tmp/x ls; ls", "hash -p makes ls run /tmp/x"),
    ("hash -dp /tmp/x ls; ls", "hash -p makes ls run /tmp/x"),
    # the cards named `--`, `-l`, `-p` and `./ls` here (exo-36, #409)
    ("hash -p /tmp/x -- ls; ls", "hash -p makes ls run /tmp/x"),
    ("hash -p /tmp/x -l ls; ls", "hash -p makes ls run /tmp/x"),
    ("hash -p /a -p /b ls; ls", "hash -p makes ls run /b"),
    ("hash -p /tmp/x ./ls git; git log", "hash -p makes git run /tmp/x"),
    ("hash -p /tmp/x ls git; git log", "hash -p makes ls, git run /tmp/x"),
])
def test_the_card_names_the_rebound_program_and_its_file(line, card):
    c = classify(sh(line))
    assert c.summary == card, c
    assert c.target == card.rsplit(" ", 1)[-1], c


def test_a_target_entry_on_the_path_denies_the_rebind():
    pol = normalize_policies({"gates": {"deny": ["target:/tmp/*"]}})
    assert decide(classify(sh("hash -p /tmp/x ls; ls")), pol).outcome == "deny"


def test_an_unread_option_word_is_the_target():
    c = classify(sh("hash $O ls"))
    assert c.target == "$O" and "$O" in c.summary, c


# exo-36's review of #409: the hash part went through the store-path scan, so a descriptor or store path in it made
# a store_write part. That outranks a post on the line, and the matrix cell and the posting rules apply to the top
# part only, so a matrix deny on the post became an ask and its posting violations were dropped. `hash -p` only
# records the path, so it writes nothing to the store and the post decides the line, as on main.
# exo-36's re-review: the same held when ssh, mosh, an ssh ProxyCommand or `man -P` runs the rebind, and for a
# BASH_CMDS or BASH_ALIASES entry set by printf -v or declare. The part that records the path is marked, so the
# store check takes it as the read it was whatever runs it.
POSTS = [
    "hash -p /proc/self/fd/3 ls; gh pr comment 1 --body Zork",
    "hash -p /dev/fd/3 ls && gh pr comment 1 --body Zork",
    "hash -p /dev/stdin ls; gh pr comment 1 --body Zork",
    "hash -lp /proc/self/fd/3 ls; gh issue comment 5 --body Zork",
    "builtin hash -p /dev/fd/9 git; gh pr comment 1 --body Zork",
    "hash -p ~/.exo/state/x ls; gh pr comment 1 --body Zork",
    "ssh host 'hash -p /proc/self/fd/3 ls'; gh pr comment 1 --body Zork",
    "ssh host hash -p /dev/fd/3 ls; gh pr comment 1 --body Zork",
    "ssh -t host 'hash -p ~/.exo/state/x ls'; gh pr comment 1 --body Zork",
    "ssh host 'sudo hash -p /proc/self/fd/3 ls'; gh pr comment 1 --body Zork",
    "ssh a ssh b 'hash -p /proc/self/fd/3 ls'; gh pr comment 1 --body Zork",
    "ssh host 'hash -p /proc/self/fd/3 ls; sort x'; gh pr comment 1 --body Zork",
    "mosh host -- hash -p /proc/self/fd/3 ls; gh pr comment 1 --body Zork",
    "ssh -o ProxyCommand='hash -p /dev/fd/3 ls' host ls; gh pr comment 1 --body Zork",
    "man -P 'hash -p /proc/self/fd/3 ls' ls; gh pr comment 1 --body Zork",
    "ssh host 'BASH_CMDS[ls]=/proc/self/fd/3 ls'; gh pr comment 1 --body Zork",
    "printf -v 'BASH_CMDS[ls]' /proc/self/fd/3; gh pr comment 1 --body Zork",
    "printf -v 'BASH_ALIASES[ls]' /proc/self/fd/3; gh pr comment 1 --body Zork",
    "printf -v 'BASH_CMDS[ls]' ~/.exo/state/x; gh pr comment 1 --body Zork",
    "printf -v 'BASH_CMDS[ls]' /proc/self/fd/3 && ls; gh pr comment 1 --body Zork",
    "declare 'BASH_CMDS[ls]=/proc/self/fd/3'; gh pr comment 1 --body Zork",
    "printf -v 'a[BASH_CMDS=3]' /proc/self/fd/3; gh pr comment 1 --body Zork",    # set in a subscript
    "gh pr comment 1 --body Zork && ssh host 'hash -p /dev/fd/3 ls'",
]


def _walk(c):
    yield c
    for p in c.parts:
        yield from _walk(p)


@pytest.mark.parametrize("line", POSTS)
def test_a_post_beside_hash_p_keeps_its_matrix_deny(line):
    pol = normalize_policies({"gates": {"matrix": {"costly": {"public": "deny"}}}})
    c = classify(sh(line))
    assert c.category == "github_post", c
    assert any(p.category == "opaque_exec" and re.search(r"hash -p|BASH_CMDS|BASH_ALIASES", p.summary)
               for p in _walk(c)), c.parts
    assert not any(p.category == "store_write" for p in _walk(c)), c.parts
    assert decide(c, pol).outcome == "deny", decide(c, pol)


@pytest.mark.parametrize("line", POSTS)
def test_a_post_beside_hash_p_keeps_its_posting_violations(line):
    d = decide(classify(sh(line)), POLICY)
    assert d.outcome == "ask", d
    assert "forbidden word: Zork" in d.violations, d
    assert "missing footer: F" in d.violations, d


@pytest.mark.parametrize("line", [
    "hash -p /proc/self/fd/3 ls; ls",
    "hash -p ~/.exo/state/x ls; ls",
    "ssh host 'hash -p /proc/self/fd/3 ls'",
    "man -P 'hash -p /dev/fd/3 ls' ls",
    "printf -v 'BASH_CMDS[ls]' /proc/self/fd/3",
])
def test_hash_p_with_a_store_or_descriptor_path_is_no_store_write(line):
    c = classify(sh(line))
    assert c.category == "opaque_exec", c
    assert not any(p.category == "store_write" for p in c.parts), c.parts
    assert decide(c, POLICY).outcome == "ask"


def test_a_rebind_beside_a_force_push_keeps_the_push_deny():
    pol = normalize_policies({"gates": {"categories": {"force_push": "deny"}}})
    c = classify(sh("hash -p /proc/self/fd/3 git; git push --force origin main"))
    assert decide(c, pol).outcome == "deny"


# The first fix skipped the whole store check for any program named hash. A redirect after a cd into the store, a
# redirect beside a store path in hash's words and a program named hash run by path (no builtin) then lost the
# store write they make on main: `cd ~/.exo/state && hash > notes` was allowed. Only the part that records the path
# counts as the read it was, so these are store writes again.
@pytest.mark.parametrize("line", [
    "cd ~/.exo/state && hash > notes",
    "cd ~/.exo/state && hash -p /tmp/x ls > out",
    "hash -p ~/.exo/state/x ls > out",
    "hash ~/.exo/state/x > out",
    "./hash ~/.exo/state/x",
    "/tmp/evil/hash ~/.exo/state/notes.md",
    "cd ~/.exo/state && ./hash x",
    # the marked part counts as a read, nothing more: a redirect, or a part beside it that opens the path, still is one
    "printf -v 'BASH_CMDS[ls]' x > ~/.exo/state/y",
    "cd ~/.exo/state && printf -v 'BASH_CMDS[ls]' x > notes",
    "ssh host 'hash -p /x ls' > ~/.exo/state/y",
    "man -P 'hash -p /x ls' ls > ~/.exo/state/y",
    "ssh host 'hash -p /x ls; eval /proc/self/fd/3'",
    "ssh host 'hash -p /x ls; eval y' /proc/self/fd/3",
    "ssh host ./hash ~/.exo/state/x",
    # a part beside the rebind that is more than a read or a shell command keeps the check of the words: here
    # ssh's own log file (-E) in the store
    "ssh -E ~/.exo/state/log host 'hash -p /x ls; eval y'",
    "ssh -E ~/.exo/state/log host 'rm x; hash -p /x ls'",
    "ssh -E ~/.exo/state/log host 'hash -p /x ls; sudo sort x'",   # sudo made it more than the read hash was
    "ssh -E ~/.exo/state/log host './tool; hash -p /x ls'",        # the line's own part is ./tool's, not the rebind's
])
def test_a_store_write_beside_hash_is_still_one(line, monkeypatch):
    monkeypatch.setenv("EXO_HOME", os.path.expanduser("~/.exo"))     # so a cd into ~/.exo enters the store
    c = classify(sh(line))
    assert c.category == "store_write", c
    assert decide(c, normalize_policies({"gates": {"categories": {"store_write": "deny"}}})).outcome == "deny"
    assert decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}})).outcome == "ask"


def test_a_credentials_floor_is_not_taken_for_a_rebind():
    # An environment dump's credentials floor carries every signal on its line, the rebind's too. Only the
    # opaque part that records the path counts as a read, so the descriptor path in ssh's words stays a store write.
    c = classify(sh("ssh host 'env BASH_CMDS[ls]=/proc/self/fd/3'"))
    assert c.category == "credentials", c
    assert any(p.category == "store_write" for p in _walk(c)), c.parts
    assert decide(c, normalize_policies({"gates": {"categories": {"store_write": "deny"}}})).outcome == "deny"
