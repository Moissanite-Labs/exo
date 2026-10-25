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
    "hash -lp/tmp/x ls && ls",               # and to -p in a cluster (exo-36, #409: `elif j == 1` passed every test)
    "hash -rp/tmp/x ls; ls",
    "hash -dp/tmp/x ls; ls",
    "command hash -rp/tmp/x ls; ls",
    "builtin hash -dp./tool git; git log",
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
    # a name the gate cannot read may lose its slash: bash runs `{ls,/}` as `ls /`, skips `/` and binds ls (abu-aska,
    # #409)
    "hash -p /tmp/x {ls,/}; ls",
    "hash -p /tmp/x l{s,/}; ls",
    "hash -p /tmp/x -- {ls,/a}; ls",
    "N=ls/; hash -p /tmp/x ${N%/}; ls",
    'N=ls/; hash -p /tmp/x "${N%/}"; ls',
    "hash -p /tmp/x $(cat /tmp/names); ls",
    "hash -p /tmp/x `cat /tmp/names`; ls",
    "builtin hash -p /tmp/x {ls,/}; git log",
    "ssh host 'hash -p /tmp/x {ls,/}; ls'",
    # and a path word it cannot read may split into a path and a name: P='/tmp/x ls' runs `hash -p /tmp/x ls`
    "hash -p {/tmp/x,ls}",
    "hash -p $P; ls",
    "hash -p ${P}; ls",
    "hash -p `cat /tmp/p`; ls",
    "hash -p $(printf '/tmp/x ls'); ls",
    "hash -p *; ls",                         # in a folder that holds `a` and `ls`: hash -p a ls
    'hash -p "$@"; ls',
    "hash -p /a -p$P; ls",                   # so may an attached path, or an option word after the path
    "hash -p /a -l$O; ls",
    "hash [l]s",                             # a glob as an option word asks, as `hash l?` does
]

# A word that starts with `[` is a glob the lexer does not mark. With files named -p, as and ls in the folder, bash
# runs `hash [-]p /tmp/x ls` as `hash -p /tmp/x ls` and `hash -p [al]s` as `hash -p as ls` (abu-aska, #409).
BRACKET = [
    "hash [-]p /tmp/x ls; ls",
    "hash [-]p/tmp/x ls; ls",
    "hash [-]lp /tmp/x ls; ls",
    "hash -l [-]p /tmp/x ls; ls",
    "builtin hash [-]p /tmp/x git; git log",
    "command hash [-]p /tmp/x ls; ls",
    "sudo hash [-]p /tmp/x ls; ls",
    "ssh host 'hash [-]p /tmp/x ls; ls'",
    "hash -p [al]s; ls",
    "hash -p [a-z]; ls",
    "hash -p /a -p [al]s; ls",
    "hash \\\n[-]p /tmp/x ls; ls",           # also after a \<newline>, which bash drops first
    "hash -p \\\n[al]s; ls",
    "hash [\\\n-]p /tmp/x ls; ls",
]
ASK += BRACKET

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
    'hash -p "$P"; ls',                      # one double-quoted path word does not split
    "hash -p '/tmp/x ls'; ls",
    "hash -p /tmp/x\\ ls; ls",
    'hash -p /a "-l$O"; ls',
    "hash -p /tmp/x*; ls",                   # a glob keeps the slash it has
    "hash -p /tmp/x /usr/bin/l*; ls",
    'hash -p "[al]s"; ls',                   # a quoted or escaped `[` is no glob
    "hash -p '[al]s'; ls",
    "hash \\[-]p /tmp/x ls; ls",
    "hash -p \\[al]s; ls",
    "hash -p ./[al]s; ls",
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
    ("hash -lp/tmp/x ls && ls", "hash -p makes ls run /tmp/x"),
    ("builtin hash -dp./tool git; git log", "hash -p makes git run ./tool"),
    # the cards named `--`, `-l`, `-p` and `./ls` here (exo-36, #409)
    ("hash -p /tmp/x -- ls; ls", "hash -p makes ls run /tmp/x"),
    ("hash -p /tmp/x -l ls; ls", "hash -p makes ls run /tmp/x"),
    ("hash -p /a -p /b ls; ls", "hash -p makes ls run /b"),
    ("hash -p /tmp/x ./ls git; git log", "hash -p makes git run /tmp/x"),
    ("hash -p /tmp/x ls git; git log", "hash -p makes ls, git run /tmp/x"),
    ("hash -p /tmp/x {ls,/}; ls", "hash -p makes {ls,/} run /tmp/x"),
    ("hash -p /tmp/x ./ls ${N%/}; ls", "hash -p makes ${N%/} run /tmp/x"),
])
def test_the_card_names_the_rebound_program_and_its_file(line, card):
    c = classify(sh(line))
    assert c.summary == card, c
    assert c.target == card.rsplit(" ", 1)[-1], c


@pytest.mark.parametrize("line", BRACKET)
@pytest.mark.parametrize("gates", [{}, {"matrix": {"costly": {"public": "deny"}}},
                                   {"categories": {"store_write": "deny"}}, {"reserved": ["tool:terminal"]}])
def test_a_word_that_starts_with_a_bracket_may_rebind(line, gates):
    c = classify(sh(line))
    assert c.category == "opaque_exec", c
    assert decide(c, normalize_policies({"gates": gates})).outcome == "ask"


# On main a rebind-only line was `read`, so a policy that names `read` decided it. Now it is `opaque_exec`, which that
# policy does not name: a read deny asks, and a read reservation no longer refuses an opaque_exec allow. The D-038
# amendment records this as group (4) (abu-aska, #409). A read beside the rebind still denies the line.
@pytest.mark.parametrize("gates, outcome", [({"categories": {"read": "deny"}}, "ask"), ({"deny": ["read"]}, "ask"),
                                            ({"reserved": ["read"], "categories": {"opaque_exec": "allow"}}, "allow")])
def test_a_policy_that_names_read_does_not_name_a_rebind(gates, outcome):
    pol = normalize_policies({"gates": gates})
    for line in ("hash -p /tmp/x ls", "hash $O ls", "hash [-]p /tmp/x ls"):
        assert decide(classify(sh(line)), pol).outcome == outcome, line
    if outcome == "ask":
        assert decide(classify(sh("hash -p /tmp/x ls; ls")), pol).outcome == "deny"


# Group (4) also holds a line whose only read part was the rebind: a post beside it, and a store write over ssh that
# a read part denied on main. Each now asks under a read deny, as the post or the store write alone does.
@pytest.mark.parametrize("line", [
    "hash -p /tmp/x ls; gh pr comment 1 --body Zork",
    "ssh -E ~/.exo/state/log host 'hash -p /x ls; sort x'",
    "ssh -L ~/.exo/state/sock:localhost:5432 host 'sort x; hash -p /x ls'",
])
def test_a_read_deny_no_longer_reaches_a_line_whose_read_part_was_the_rebind(line, monkeypatch):
    monkeypatch.setenv("EXO_HOME", os.path.expanduser("~/.exo"))
    for gates in ({"categories": {"read": "deny"}}, {"deny": ["read"]}):
        assert decide(classify(sh(line)), normalize_policies({"gates": gates})).outcome == "ask", (line, gates)


def test_a_target_entry_on_the_path_denies_the_rebind():
    pol = normalize_policies({"gates": {"deny": ["target:/tmp/*"]}})
    assert decide(classify(sh("hash -p /tmp/x ls; ls")), pol).outcome == "deny"


def test_an_unread_option_word_is_the_target():
    c = classify(sh("hash $O ls"))
    assert c.target == "$O" and "$O" in c.summary, c


def test_a_bracket_option_word_is_the_target():
    c = classify(sh("hash [-]p /tmp/x ls; ls"))
    assert c.target == "[-]p" and "([-]p)" in c.summary, c


@pytest.mark.parametrize("line, word, path", [("hash -p $P; ls", "$P", "$P"), ("hash -p /a -l$O; ls", "-l$O", "/a"),
                                              ("hash -p [al]s; ls", "[al]s", "[al]s")])
def test_a_word_from_the_path_on_that_may_split_is_on_the_card(line, word, path):
    c = classify(sh(line))
    assert c.summary == f"hash -p may rebind a program name: a word the gate cannot read ({word})", c
    assert c.target == path, c


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
    "hash -p /proc/self/fd/3 {ls,/}; gh pr comment 1 --body Zork",   # a name or path the gate cannot read
    "hash -p $P; gh pr comment 1 --body Zork",
    "hash -p [al]s /dev/fd/3; gh pr comment 1 --body Zork",          # a path word that starts with `[` (abu-aska)
    # a rebind over ssh inside a line ssh runs: the marked part is checked the same way, however deep
    "ssh host 'hash -p /proc/self/fd/3 ls; ssh b \"hash -p /dev/fd/3 ls\"'; gh pr comment 1 --body Zork",
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
    # a part beside the rebind that is more than a read keeps the check of the words: here ssh's own log file (-E)
    # in the store
    "ssh -E ~/.exo/state/log host 'hash -p /x ls; eval y'",
    "ssh -E ~/.exo/state/log host 'rm x; hash -p /x ls'",
    "ssh -E ~/.exo/state/log host 'hash -p /x ls; sudo sort x'",   # sudo made it more than the read hash was
    "ssh -E ~/.exo/state/log host './tool; hash -p /x ls'",        # the line's own part is ./tool's, not the rebind's
    # a plain shell command does too, in either order, as one before the rebind did on main (exo-36, #409). ssh's own
    # -E, -S and UserKnownHostsFile name a local file it writes, whatever line it runs
    "ssh -E ~/.exo/state/log host 'sort x && hash -p /x ls'",
    "ssh -E ~/.exo/state/log host 'sort x; hash -p /x ls'",
    "ssh -E ~/.exo/state/log host 'hash -p /x ls; sort x'",
    "ssh -S ~/.exo/state/sock host 'hash -p /x ls; sort x'",
    "ssh -o UserKnownHostsFile=~/.exo/state/kh host 'sort x && hash -p /x ls'",
    "ssh -E ~/.exo/state/log a ssh b 'hash -p /x ls; sort x'",
    "ssh -o ProxyCommand='hash -p /x ls; sort x' -E ~/.exo/state/log host ls",
    "man -P 'sort x; hash -p /x ls' -l ~/.exo/state/x",
    "less ~/.exo/state/x '+!sort x; hash -p /x ls'",
    "more ~/.exo/state/x '+!hash -p /x ls; sort x'",
    # only the bare part of the hop that runs the rebind counts, not a login to another host on the line
    "ssh -E ~/.exo/state/log a 'ssh b; hash -p /x ls'",
    "ssh -E ~/.exo/state/log a 'hash -p /x ls; ssh -t b'",
    "ssh -E ~/.exo/state/log a 'ssh; hash -p /x ls'",
    "less ~/.exo/state/x '+!ssh b; hash -p /x ls'",
    # only BASH_CMDS and BASH_ALIASES are marked: a store path in another variable the gate reads as opaque is
    # still checked as on main, however the line sets it
    "export LD_PRELOAD=~/.exo/state/x",
    "printf -v LD_PRELOAD ~/.exo/state/x",
    "printf -v 'a[LD_PRELOAD=1]' ~/.exo/state/x",
])
def test_a_store_write_beside_hash_is_still_one(line, monkeypatch):
    monkeypatch.setenv("EXO_HOME", os.path.expanduser("~/.exo"))     # so a cd into ~/.exo enters the store
    c = classify(sh(line))
    assert c.category == "store_write", c
    assert decide(c, normalize_policies({"gates": {"categories": {"store_write": "deny"}}})).outcome == "deny"
    assert decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}})).outcome == "ask"
    r = decide(c, normalize_policies({"gates": {"reserved": ["store_write"]}}))   # so no standing grant covers it
    assert r.outcome == "ask" and any(cat == "store_write" for cat, _ in r.reserved), r


# Over ssh, a plain shell command beside the rebind keeps the check of ssh's words, so the descriptor path there is
# a store write, as main makes it with the shell command first. The post keeps its deny cell (#464) and its posting
# violations, named after the post (exo-36, #409).
@pytest.mark.parametrize("line", [
    "ssh host 'hash -p /proc/self/fd/3 ls; sort x'; gh pr comment 1 --body Zork",
    "ssh host 'sort x; hash -p /proc/self/fd/3 ls'; gh pr comment 1 --body Zork",
    "ssh host 'ssh b; hash -p /proc/self/fd/3 ls'; gh pr comment 1 --body Zork",
])
def test_a_post_beside_a_rebind_and_a_shell_command_over_ssh_keeps_its_deny(line):
    c = classify(sh(line))
    assert c.category == "store_write", c
    assert decide(c, normalize_policies({"gates": {"matrix": {"costly": {"public": "deny"}}}})).outcome == "deny"
    d = decide(c, POLICY)
    assert d.outcome == "ask", d
    assert d.violations == ["GitHub PR comment on #1: missing footer: F",
                            "GitHub PR comment on #1: forbidden word: Zork"], d


def test_a_credentials_floor_is_not_taken_for_a_rebind():
    # An environment dump's credentials floor carries every signal on its line, the rebind's too. Only the
    # opaque part that records the path counts as a read, so the descriptor path in ssh's words stays a store write.
    c = classify(sh("ssh host 'env BASH_CMDS[ls]=/proc/self/fd/3'"))
    assert c.category == "credentials", c
    assert any(p.category == "store_write" for p in _walk(c)), c.parts
    assert decide(c, normalize_policies({"gates": {"categories": {"store_write": "deny"}}})).outcome == "deny"
