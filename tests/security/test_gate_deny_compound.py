"""A reserved or deny entry matches any simple command on a line, not only the most severe one (D-075).

`classify_shell` reduces a compound line to its most severe part. Before, `decide` checked the principal's
`deny` and `reserved` lists against that one part only, so `git push origin main && rm -rf build` was an
`ask` for the delete and the denied push ran once the delete was approved.
"""
import json
import shlex

import pytest

from exo.gate import classify, decide, normalize_policies, parse_yaml

DENY_PUSH = normalize_policies({"gates": {"deny": ["vcs_push", "force_push"]}})
RESERVE_CLOSE = normalize_policies({"gates": {"reserved": ["github_close"]}})


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def test_denied_push_alone_is_denied():
    d = decide(classify(sh("git push origin main")), DENY_PUSH)
    assert (d.outcome, d.rule) == ("deny", "deny")


@pytest.mark.parametrize("command", [
    "git push origin main && rm -rf build",                      # hidden behind a delete
    "rm -rf build && git push origin main",
    "git push origin main; cat ~/.ssh/id_rsa",                   # hidden behind a credential read
    "git push origin main && gh pr comment 3 --body hi",         # hidden behind a post
    "ls && git push origin main || echo failed",
    "bash -c 'git push origin main && rm -rf build'",            # inside a shell the gate can read
    "python -c \"import subprocess; subprocess.run(['git', 'push', 'origin', 'main']); print(1)\" && rm -rf build",
])
def test_denied_action_on_a_compound_line_denies_the_line(command):
    c = classify(sh(command))
    d = decide(c, DENY_PUSH)
    assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)


def test_reserved_action_on_a_compound_line_asks():
    c = classify(sh("gh issue close 9 -R acme-org/app && ls"))
    d = decide(c, RESERVE_CLOSE)
    assert d.outcome == "ask" and d.rule == "reserved", d


# The reserved half: each of these passes only when `decide` checks reserved entries against every part.
# The reserved action is the less severe part, so the delete sets the card and the category.

def test_reserved_action_behind_a_more_severe_part_asks():
    c = classify(sh("gh issue close 9 -R acme-org/app && rm -rf build"))
    d = decide(c, RESERVE_CLOSE)
    assert d.outcome == "ask" and d.rule == "reserved", d
    assert d.reserved == [("github_close", "acme-org/app#9")]


def test_reserved_part_is_not_lowered_by_a_category_override_on_the_top_part():
    pol = normalize_policies({"gates": {"reserved": ["github_close"], "categories": {"delete": "allow"}}})
    c = classify(sh("rm -rf build && gh issue close 9 -R acme-org/app"))
    assert decide(c, pol).outcome == "ask"
    assert decide(classify(sh("rm -rf build")), pol).outcome == "allow"


def test_plain_category_reserved_entry_matches_a_less_severe_part():
    pol = normalize_policies({"gates": {"reserved": ["vcs_push"]}})
    d = decide(classify(sh("git push origin feature && rm -rf build")), pol)
    assert d.rule == "reserved" and d.reserved == [("vcs_push", "origin")], d


def test_lines_without_the_denied_action_are_unchanged():
    for command in ("rm -rf build", "ls && cat README.md", "git status && git log -1"):
        d = decide(classify(sh(command)), DENY_PUSH)
        assert d.outcome != "deny", (command, d)


def test_parts_are_kept_on_the_classification_but_not_in_the_ledger_view():
    c = classify(sh("git push origin main && rm -rf build"))
    assert {p.category for p in c.parts} >= {"vcs_push"}
    assert "parts" not in c.as_dict()


# Parts carry the tool of the call they came in on, not an internal tag.

def test_tool_execute_code_entry_does_not_match_inline_code_run_from_bash():
    pol = normalize_policies({"gates": {"deny": ["tool:execute_code"]}})
    assert decide(classify({"tool": "execute_code", "args": {"code": "print(1)"}}), pol).outcome == "deny"
    for command in ("python -c 'print(1)'", "node -e 'console.log(1)'", "python3 <<EOF\nprint(1)\nEOF"):
        d = decide(classify(sh(command)), pol)
        assert (d.outcome, d.rule) == ("ask", "opaque"), (command, d)


def test_tool_keyed_mapping_entry_matches_a_part():
    pol = normalize_policies({"gates": {"deny": [{"match": {"tool": "bash", "category": "vcs_push"}}]}})
    assert decide(classify(sh("git push origin main")), pol).outcome == "deny"
    assert decide(classify(sh("git push origin main && rm -rf build")), pol).outcome == "deny"


def test_ssh_exposure_reaches_the_inner_parts():
    pol = normalize_policies({"gates": {"deny": [{"category": "delete", "exposure": "internal"}]}})
    assert decide(classify(sh("rm -rf /data")), pol).outcome != "deny"
    assert decide(classify(sh("ssh build-host 'cat ~/.netrc; rm -rf /data'")), pol).outcome == "deny"


def test_ssh_hop_is_a_part_so_a_target_entry_matches_the_host():
    pol = normalize_policies({"gates": {"deny": ["target:prod-db"]}})
    for command in ("ssh prod-db ls", "ssh prod-db 'git push origin main'", "ssh prod-db 'rm -rf /data'"):
        d = decide(classify(sh(command)), pol)
        assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)
    assert decide(classify(sh("ssh build-host 'rm -rf /data'")), pol).outcome != "deny"


def test_flat_mapping_entry_with_a_reason_or_name_still_matches():
    pol = normalize_policies({"gates": {"deny": [{"category": "delete", "reason": "no deletes"}],
                                        "reserved": [{"category": "vcs_push", "name": "pushes"}]}})
    d = decide(classify(sh("rm -rf build")), pol)
    assert (d.outcome, d.reason) == ("deny", "denied by policy (no deletes)"), d
    d = decide(classify(sh("git push origin feature")), pol)
    assert d.rule == "reserved" and "pushes" in d.reason, d


def test_keys_that_are_not_match_keys_widen_an_entry_instead_of_voiding_it():
    # A note or a misspelt key used to make the entry match nothing, so the deny silently failed open.
    pol = normalize_policies(parse_yaml(
        "gates:\n"
        "  deny:\n"
        "    - {category: delete, reason: no deletes, note: ruling of 3 May}\n"
        "  reserved:\n"
        "    - {match: {category: vcs_push, branch: main}, name: pushes}\n"))
    for command in ("rm -rf build", "ls && rm -rf build"):
        d = decide(classify(sh(command)), pol)
        assert (d.outcome, d.reason) == ("deny", "denied by policy (no deletes)"), (command, d)
    d = decide(classify(sh("git push origin feature && rm -rf build")), pol)
    assert d.outcome == "deny" and d.reserved == [("vcs_push", "origin")], d
    d = decide(classify(sh("git push origin feature")), pol)
    assert d.rule == "reserved" and "pushes" in d.reason, d
    # An entry left with no match key at all matches every action: fail closed, and the reason names it.
    pol = normalize_policies({"gates": {"deny": [{"catgory": "delete"}]}})
    assert decide(classify(sh("ls")), pol).outcome == "deny"


# Commands inside code: a string literal counts when any simple command in it is non-benign, not only when
# the most severe one is, so the order of the commands in the literal does not matter.

def code(source):
    return {"tool": "execute_code", "args": {"code": source}}


@pytest.mark.parametrize("action", [
    sh("python3 -c \"import os; os.system('ls; git push origin main')\""),
    sh("python3 -c \"import os; os.system('git push origin main; ls')\""),
    sh("python3 -c \"import os; os.system('ls && git push origin main')\""),
    sh("python3 -c \"import os; os.system('ls\\ngit push origin main')\""),       # a \n escape in the literal
    sh("python3 -c \"import subprocess; subprocess.run(['sh', '-c', 'ls; git push origin main'])\""),
    code("import os; os.system('ls; git push origin main')"),
    code("import os; os.system('git push origin main; ls')"),
    code("import os; os.system('ls && git push origin main')"),
    code("import os; os.system('ls\\ngit push origin main')"),
    code("import os\nos.system('ls')\nos.system('git push origin main')"),
    code("import subprocess; subprocess.run(['sh', '-c', 'ls; git push origin main'])"),
])
def test_denied_command_inside_code_denies_in_either_order(action):
    d = decide(classify(action), DENY_PUSH)
    assert (d.outcome, d.rule) == ("deny", "deny"), (action, d)


@pytest.mark.parametrize("source", [
    "import os; os.system('./build.sh; systemctl enable backup.timer')",    # the opaque part dominates the literal
    "import os; os.system('systemctl enable backup.timer; ./build.sh')",
    "import os; os.system('./build.sh && systemctl enable backup.timer')",
    "import subprocess; subprocess.run(['sh', '-c', './build.sh; systemctl enable backup.timer'])",
])
def test_denied_part_behind_a_more_severe_benign_one_in_a_literal_denies(source):
    pol = normalize_policies({"gates": {"deny": ["schedule"]}})
    for action in (code(source), sh(f"python3 -c {shlex.quote(source)}")):
        d = decide(classify(action), pol)
        assert (d.outcome, d.rule) == ("deny", "deny"), (action, d)


def test_a_literal_that_is_all_benign_is_still_dropped():
    c = classify(code("import os; os.system('ls; cat README.md')"))
    assert (c.category, c.parts) == ("code", [])
    d = decide(c, DENY_PUSH)
    assert (d.outcome, d.rule) == ("ask", "opaque"), d


# Nested parts and per-part scopes.

def test_a_part_nested_two_levels_down_is_matched_with_its_own_scopes():
    command = "bash -c \"bash -c 'git push origin main'\" && rm -rf build"
    assert decide(classify(sh(command)), DENY_PUSH).outcome == "deny"
    pol = {"gates": {"deny": [{"category": "vcs_push", "exposure": "internal"}]}}
    assert decide(classify(sh(command)), normalize_policies(pol)).outcome == "deny"
    # The principal's scope makes `origin` private on the nested part, so the internal-only entry no longer fits.
    pol["gates"]["scopes"] = {"private": ["origin"]}
    assert decide(classify(sh(command)), normalize_policies(pol)).outcome != "deny"


# The ssh hop names the bare host, so a `target:<host>` entry matches whatever form the destination takes.

@pytest.mark.parametrize("command", [
    "ssh user@prod-db 'git push origin main'",
    "ssh ssh://user@prod-db:22 ls",
    "ssh -p 2222 prod-db ls",
    "ssh -o Port=2222 user@prod-db 'rm -rf /data'",
    "ssh -P tag prod-db ls",                                  # -B and -P take a value; it is not the host
    "ssh user@[prod-db]:2222 ls",
])
def test_target_entry_matches_the_ssh_host_in_any_form(command):
    d = decide(classify(sh(command)), normalize_policies({"gates": {"deny": ["target:prod-db"]}}))
    assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)


def test_ssh_target_forms_keep_matching_the_destination_as_written():
    pol = normalize_policies({"gates": {"deny": ["target:root@*"]}})
    assert decide(classify(sh("ssh root@prod-db ls")), pol).outcome == "deny"
    assert decide(classify(sh("ssh user@build-host ls")), pol).outcome != "deny"
    pol = normalize_policies({"gates": {"deny": ["target:prod-db"]}})
    assert decide(classify(sh("ssh user@build-host 'git push origin main'")), pol).outcome != "deny"


@pytest.mark.parametrize("command", [
    "ssh -B eth0 ls rm -rf /data",
    "ssh -P tag ls rm -rf /data",
    "ssh -I lib ls rm -rf /data",
    "ssh -vB eth0 ls rm -rf /data",                           # the value letter ends a cluster
    "ssh -4B eth0 ls rm -rf /data",
    "ssh -vP tag ls rm -rf /data",
    "ssh -vp 22 ls rm -rf /data",
    "ssh -vl admin echo rm -rf /data",
    "ssh -vi key ls rm -rf /data",
    "ssh -vJ jump ls rm -rf /data",
    "ssh -vo Port=22 ls rm -rf /data",
    "ssh -Ap 22 ls rm -rf /data",
    "mosh --port 60001 ls rm -rf /data",                     # a mosh long option with its value as the next word
    "mosh --family inet ls rm -rf /data",
    "mosh --predict always ls rm -rf /data",
    "mosh --bind-server any ls rm -rf /data",
])
def test_ssh_option_values_are_not_read_as_the_host(command):
    d = decide(classify(sh(command)), normalize_policies({"gates": {"deny": ["delete"]}}))
    assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)
    assert decide(classify(sh(command))).outcome == "ask", command


def test_mosh_command_options_are_read_and_ask():
    pol = normalize_policies({"gates": {"deny": ["delete"]}})
    for command in ("mosh --ssh 'rm -rf /data' prod-db", "mosh --ssh='rm -rf /data' prod-db",
                    "mosh --client 'rm -rf /x' h"):
        assert decide(classify(sh(command)), pol).outcome == "deny", command
    for command in ("mosh --ssh='ssh -p 2222' prod-db", "mosh --server 'mosh-server new' prod-db"):
        assert decide(classify(sh(command))).outcome == "ask", command
    assert decide(classify(sh("mosh --port=60001 prod-db"))).outcome == decide(classify(sh("mosh prod-db"))).outcome


def test_mosh_local_helpers_keep_their_exposure():
    # --ssh and --client run on this machine; only --server and the inner command run on the remote host.
    private = normalize_policies({"gates": {"deny": [{"match": {"category": "delete", "exposure": "private"}}]}})
    internal = normalize_policies({"gates": {"deny": [{"match": {"category": "delete", "exposure": "internal"}}]}})
    for command in ("mosh --ssh='sh -c \"rm -rf /tmp/data; exit\"' host", "mosh --client='rm -rf /tmp/data' host"):
        assert decide(classify(sh(command)), private).outcome == "deny", command
    for command in ("mosh --server='rm -rf /data' host", "mosh host rm -rf /data"):
        assert decide(classify(sh(command)), internal).outcome == "deny", command
        assert decide(classify(sh(command)), private).outcome == "ask", command


def test_ssh_value_inside_the_word_and_plain_value_options_read_as_before():
    pol = normalize_policies({"gates": {"deny": ["delete"]}})
    for command in ("ssh -vpls 22 rm -rf /data", "ssh -Beth0 prod-db rm -rf /data", "ssh -p 22 prod-db rm -rf /data"):
        assert decide(classify(sh(command)), pol).outcome == "deny", command
    assert decide(classify(sh("ssh -p 22 prod-db ls"))).outcome == "allow"
    # Where the gate used to take the value for the host, the line asks rather than reading as allow.
    assert decide(classify(sh("ssh -vp 22 prod-db ls"))).outcome == "ask"
    assert decide(classify(sh("ssh -P tag prod-db ls"))).outcome == "ask"


# mosh reads its options with Perl's Getopt::Long in its default mode: one or two dashes (or `+`), any case, a
# unique prefix, `--opt=value` or `--opt value`, and options after the host. `--` ends the options.

@pytest.mark.parametrize("command", [
    "mosh --serv='rm -rf /data' prod-db",                     # a unique prefix
    "mosh -server='rm -rf /data' prod-db",                    # one dash
    "mosh --cl='rm -rf /tmp/d' prod-db",
    "mosh -ssh='rm -rf /tmp/d' prod-db",
    "mosh --SERVER='rm -rf /data' prod-db",                   # any case
    "mosh +server='rm -rf /data' prod-db",
    "mosh prod-db --server='rm -rf /data'",                   # after the host
    "mosh prod-db --serv 'rm -rf /data'",
    "mosh -o prod-db rm ls",                                  # -o is --predict-overwrite and takes no value
    "mosh prod-db -- rm -rf /data",                           # `--` ends the options; the command follows
    "mosh -- prod-db rm -rf /data",
    "ssh prod-db -- rm -rf /data",
])
def test_mosh_options_are_read_as_getopt_long_reads_them(command):
    d = decide(classify(sh(command)), normalize_policies({"gates": {"deny": ["delete"]}}))
    assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)
    assert decide(classify(sh(command))).outcome == "ask", command


@pytest.mark.parametrize("command", [
    "mosh --s='rm -rf /data' prod-db ls",                     # server, ssh or ssh-pty: mosh stops on it
    "mosh --pre=always prod-db ls",                           # predict or predict-overwrite
    "mosh --sx=1 prod-db ls",
    "mosh --local=1 prod-db ls",                              # takes no value
    "mosh --server= prod-db ls",                              # an empty value is an error
    "mosh prod-db ls -x",
])
def test_mosh_word_that_names_no_single_option_asks(command):
    assert decide(classify(sh(command))).outcome == "ask", command


def test_mosh_options_that_resolve_read_the_host_and_command():
    for command in ("mosh prod-db ls", "mosh -a -n -4 -6 --no-init --nossh-pty --predict-o prod-db ls",
                    "mosh -p 22 prod-db ls", "mosh --po=22 prod-db ls", "mosh prod-db -- ls", "ssh prod-db -- ls"):
        assert decide(classify(sh(command))).outcome == "allow", command


def test_mosh_local_runs_the_server_and_the_command_here():
    private = normalize_policies({"gates": {"deny": [{"match": {"category": "delete", "exposure": "private"}}]}})
    internal = normalize_policies({"gates": {"deny": [{"match": {"category": "delete", "exposure": "internal"}}]}})
    for command in ("mosh --local --server='rm -rf /tmp/data' 127.0.0.1", "mosh --loc 127.0.0.1 rm -rf /tmp/data",
                    "mosh -l 127.0.0.1 -- rm -rf /tmp/data"):
        c = classify(sh(command))
        assert decide(c, private).outcome == "deny", command
        assert decide(c, internal).outcome != "deny", command
        assert decide(c).outcome == "ask", command


def test_command_entry_matches_a_part_through_the_line_views():
    # `g''it` decodes to `git` only in the line's views (#136); each part matches with them too.
    pol = normalize_policies({"gates": {"deny": [{"match": {"category": "vcs_push", "command": "git push"}}]}})
    for command in ("g''it push origin main", "g''it push origin main && rm -rf build",
                    "rm -rf build; g\\it push origin main", "bash -c \"g''it push origin main\" && rm -rf build"):
        d = decide(classify(sh(command)), pol)
        assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)
    assert decide(classify(sh("g''it status && rm -rf build")), pol).outcome != "deny"


def test_line_views_are_searched_once_per_pattern_not_once_per_part(monkeypatch):
    # Each `$(..)` part adds its own views to the line's; matching every part over all of them is quadratic.
    import re

    import exo.gate as gate

    searches = 0

    class Counting:
        def __init__(self, rx):
            self.rx = rx

        def search(self, text, *a):
            nonlocal searches
            searches += 1
            return self.rx.search(text, *a)

        def __getattr__(self, name):
            return getattr(self.rx, name)

    class Re:
        def __getattr__(self, name):
            return getattr(re, name)

        def compile(self, pattern, flags=0):
            return Counting(re.compile(pattern, flags))

    pol = normalize_policies({"gates": {"deny": [{"match": {"command": "git push"}}, {"match": {"command": "x+y"}}],
                                        "reserved": [{"match": {"command": "deploy"}}]}})
    n = 300
    c = classify(sh(" ".join(f"echo $(echo a{i});" for i in range(n)) + " ls"))
    monkeypatch.setattr(gate, "re", Re())
    assert decide(c, pol).outcome == "allow"
    assert searches < 40 * n, searches


def test_mosh_option_value_that_reads_as_a_command_option_still_asks():
    # mosh takes `--server=...` here as the port or bind address and never runs it, but the gate read it as
    # the server command before, and still does.
    for command in ("mosh --po '--server=rm -rf /data' prod-db cat f",
                    "mosh --bind-s '--server=rm -rf /data' prod-db cat f", "mosh -p '-ssh=rm -rf /d' prod-db ls"):
        d = decide(classify(sh(command)), normalize_policies({"gates": {"deny": ["delete"]}}))
        assert (d.outcome, d.rule) == ("deny", "deny"), (command, d)
        assert decide(classify(sh(command))).outcome == "ask", command


@pytest.mark.parametrize("command,category", [
    ("mosh prod-db ls --port 'x; rm -rf /data'", "delete"),
    ("mosh prod-db ls -p 'x && rm -rf /data'", "delete"),
    ("mosh prod-db ls --bind-server '$(rm -rf /data)'", "delete"),
    ("mosh prod-db ls --predict 'x;git push origin main'", "vcs_push"),
    ("POSIXLY_CORRECT=1 mosh prod-db sh -e -c 'rm -rf /data'", "delete"),
    ("env POSIXLY_CORRECT=1 mosh prod-db sh -e -c 'rm -rf /data'", "delete"),
])
def test_words_mosh_takes_as_options_after_the_host_are_still_read(command, category):
    # In its default mode mosh takes these as its options, but with POSIXLY_CORRECT set Getopt::Long stops at
    # the host and mosh runs them as the command, so the words after the host are read both ways and the line asks.
    c = classify(sh(command))
    assert decide(c).outcome == "ask", (command, c)
    d = decide(c, normalize_policies({"gates": {"deny": [category]}}))
    assert d.outcome == "deny", (command, d)


def test_mosh_double_dash_after_the_host_needs_no_second_reading():
    c = classify(sh("mosh prod-db -- rm -rf /data"))
    assert decide(c, normalize_policies({"gates": {"deny": ["delete"]}})).outcome == "deny", c
    assert "POSIXLY_CORRECT" not in c.summary, c


# Decision.reserved reaches the ledger: no URL userinfo, and each hit once.

def test_reserved_hits_carry_no_url_userinfo_and_are_listed_once():
    pol = normalize_policies({"gates": {"reserved": ["vcs_push", "read", "credentials"]}})
    d = decide(classify(sh("git push https://bot:ghp_s3cretT0ken@example.com/acme/app main && rm -rf build")), pol)
    assert d.rule == "reserved" and d.as_dict()["reserved"] == [("vcs_push", "https://***@example.com/acme/app")], d
    assert "s3cretT0ken" not in json.dumps(d.as_dict()) + d.reason
    # The decision keeps the hits as written, so two pushes with different credentials stay two hits and the
    # hook's grant check can tell them apart; only the ledger view redacts them.
    two = "git push https://a:tokA@example.com/r.git main && git push https://b:tokB@example.com/r.git main"
    d = decide(classify(sh(two)), pol)
    assert len(d.reserved) == 2, d
    assert d.as_dict()["reserved"] == [("vcs_push", "https://***@example.com/r.git")]
    d = decide(classify(sh("cat ~/.ssh/id_rsa && ls")), pol)
    assert d.reserved and len(d.reserved) == len(set(d.reserved)), d
