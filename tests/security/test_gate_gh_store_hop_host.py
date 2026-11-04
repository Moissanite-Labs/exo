"""An ssh or mosh hop keeps its host on a gh download or clone into the store (#461 follow-up).

#461 made `gh release download -O <store>` and `gh repo clone <repo> <store>` a store write. Over a hop the store
write now leads the remote line and the gh read is a part below it, and the hop gave the host (#399) only to the
line's own parts: the nested read lost the host its empty other target took when it led, so a deny, rule or
reserved entry on `{read, target: host}` no longer saw it and the line asked where it was denied before #461. The
same held for the opaque part of an `eval` or a stdin form under `{opaque_exec, target: host}`, for mosh's local
helpers and the part that runs ssh's stdin, which led over the read and took the host. Each such part with an empty
target now has a copy on the host, and keeps its own reading. Under `ssh -o RemoteCommand` only the line's lead was
raised to internal, so the read below the store write stayed private: every part is raised now, and a nested part
keeps its private reading as a copy beside it. The same holds where main's newer readings make the store write: a
store path in another letter case (#635), a `$((` read as a command substitution (#652) and an `env -S` text (#634).
Every command string here is data for exo.gate.classify/decide; none is run.
"""
import pytest

from exo import gate

READ_HOST_DENY = {"gates": {"deny": [{"category": "read", "target": "host"}]}}
READ_HOST_RULE = {"gates": {"rules": [{"match": {"category": "read", "target": "host"}, "decision": "deny"}]}}
OPAQUE_HOST_DENY = {"gates": {"deny": [{"category": "opaque_exec", "target": "host"}]}}
READ_HOST_RESERVED = {"gates": {"reserved": [{"category": "read", "target": "host"}]}}
TERMINAL_RESERVED = {"gates": {"reserved": ["tool:terminal"]}}       # gate_diff's reserved_tool_terminal
READ_ALLOW = {"gates": {"categories": {"read": "allow"}}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The repro's temp home, with the store folders the rows write into."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_SEAT", "CLAUDE_CONFIG_DIR",
              "EXO_GATE_CASE_SENSITIVE"):
        monkeypatch.delenv(k, raising=False)
    for name in ("state", "policies", "update"):
        (home / ".exo" / name).mkdir(parents=True)
    return home


def run(line, policy, tool="bash"):
    c = gate.classify({"tool": tool, "args": {"command": line}})
    return c, gate.decide(c, policy)


# A gh download or clone into the store run on the host: denied before #461 under a deny or rule on the host's read.
HOPS = [
    "ssh host gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
    "ssh host gh repo clone pub/x ~/.exo/update",
    "ssh host gh run download 1 -R pub/x -D ~/.exo/state",
    "ssh host sudo gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
    "ssh -t host gh repo clone pub/x ~/.exo/update",
    "mosh host -- gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
    "mosh --local host -- gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
]
# The same line read as opaque on the host: an `eval` or `xargs` in the remote command, or the line fed to ssh.
OPAQUE = [
    "ssh host 'eval gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml'",
    "ssh host 'xargs gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml'",
    "echo 'gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml' | ssh host",
    "echo 'gh repo clone pub/x ~/.exo/update' | ssh host",
]


@pytest.mark.parametrize("policy", [READ_HOST_DENY, READ_HOST_RULE], ids=["deny", "rule"])
@pytest.mark.parametrize("line", HOPS + OPAQUE)
def test_a_deny_on_the_hosts_read_still_denies(line, policy):
    c, d = run(line, policy)
    assert d.outcome == "deny", (line, c, d)


@pytest.mark.parametrize("line", OPAQUE)
def test_a_deny_on_the_hosts_opaque_code_still_denies(line):
    c, d = run(line, OPAQUE_HOST_DENY)
    assert d.outcome == "deny", (line, c, d)


@pytest.mark.parametrize("line", HOPS + OPAQUE)
def test_a_reserved_entry_on_the_hosts_read_still_hits(line):
    c, d = run(line, READ_HOST_RESERVED)
    assert d.outcome == "ask" and ("read", "host") in d.reserved, (line, c, d)


@pytest.mark.parametrize("line", OPAQUE[:2])
def test_a_reserved_tool_keeps_the_hosts_opaque_hit(line):
    c, d = run(line, TERMINAL_RESERVED, tool="terminal")
    assert d.outcome == "ask" and {("opaque_exec", "host"), ("read", "host")} <= set(d.reserved), (line, c, d)


# mosh's local helpers (--ssh, --client, and --server with --local) led the line over the gh read and took the host.
# The store write leads now, and each helper keeps a copy on the host.
HELPERS = [
    "mosh --ssh='ssh -p 2' host -- gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
    "mosh --ssh='ssh -p 2' host -- gh repo clone pub/x ~/.exo/update",
    "mosh --client=mosh-client host -- gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
    "mosh --client=mosh-client host -- gh run download 1 -R pub/x -D ~/.exo/state",
    "mosh --local --server=mosh-server host -- gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
]
OPAQUE_HOST_RULE = {"gates": {"rules": [{"match": {"category": "opaque_exec", "target": "host"}, "decision": "deny"}]}}


@pytest.mark.parametrize("policy", [OPAQUE_HOST_DENY, OPAQUE_HOST_RULE], ids=["deny", "rule"])
@pytest.mark.parametrize("line", HELPERS)
def test_a_deny_on_the_hosts_mosh_helper_still_denies(line, policy):
    c, d = run(line, policy)
    assert d.outcome == "deny", (line, c, d)


@pytest.mark.parametrize("line", HELPERS)
def test_a_reserved_entry_on_the_hosts_mosh_helper_still_hits(line):
    c, d = run(line, {"gates": {"reserved": [{"category": "opaque_exec", "target": "host"}]}})
    assert d.outcome == "ask" and ("opaque_exec", "host") in d.reserved, (line, c, d)


# ssh runs its stdin (`echo x |`) beside a remote line that writes the store: the part that runs it led over the gh
# read and took the host, also a host from an expansion. It keeps a copy on the host, beside its own empty target.
STDIN = [
    "echo x | ssh $H gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml",
    'echo x | ssh "$H" gh repo clone pub/x ~/.exo/update',
]


@pytest.mark.parametrize("line", STDIN)
def test_the_part_that_runs_ssh_stdin_keeps_the_hosts_reserved_hit(line):
    c, d = run(line, TERMINAL_RESERVED, tool="terminal")
    assert d.outcome == "ask" and {("opaque_exec", "$H"), ("opaque_exec", "")} <= set(d.reserved), (line, c, d)


@pytest.mark.parametrize("line", STDIN)
def test_the_part_that_runs_ssh_stdin_still_denies_on_the_host(line):
    c, d = run(line, {"gates": {"deny": [{"category": "opaque_exec", "target": "$H"}]}})
    assert d.outcome == "deny", (line, c, d)


# ssh -o RemoteCommand runs the line on the host: every part of it is internal, as for the hop's own command (D-075),
# so a matrix that denies an internal action still reaches the gh read below the store write.
SCOPED_MATRIX = {"gates": {"scopes": {"private": ["host"]}, "matrix": {
    "reversible": {"private": "allow", "internal": "deny", "public": "deny"},
    "irreversible": {"private": "allow", "internal": "deny", "public": "deny"}}}}


REMOTE_COMMANDS = [
    "ssh -o RemoteCommand='gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml' host",
    "ssh -o RemoteCommand='gh repo clone pub/x ~/.exo/update' host",
    "ssh -oRemoteCommand='gh run download 1 -R pub/x -D ~/.exo/state' host",
]


@pytest.mark.parametrize("line", REMOTE_COMMANDS)
def test_a_remote_command_still_meets_the_internal_matrix_cell(line):
    c, d = run(line, SCOPED_MATRIX)
    assert (d.outcome, d.rule) == ("deny", "matrix"), (line, c, d)


# Guard, as on main: each raised part keeps its private reading as a copy, so an entry keyed on `exposure: private`
# still meets the gh read, also in an `eval` (a raise alone made these ask at the store floor).
READ_PRIVATE_RULE = {"gates": {"rules": [{"match": {"category": "read", "exposure": "private"}, "decision": "deny"}]}}


@pytest.mark.parametrize("line", REMOTE_COMMANDS + [
    "ssh -o RemoteCommand='gh release download v1 -O ~/.exo/policies/gates.yaml' host",
    "ssh -oRemoteCommand='eval gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml' host",
])
def test_a_remote_command_keeps_the_private_reading(line):
    c, d = run(line, READ_PRIVATE_RULE)
    assert (d.outcome, d.rule) == ("deny", "rule"), (line, c, d)


# Guards, as on main: the line is still the principal-only store write, the read keeps its repo, a gist clone
# (whose read names no repo, so it led with the host) still denies, and a path outside the store stays a read.
GIST = "ssh host gh gist clone abc ~/.exo/update"


@pytest.mark.parametrize("line", HOPS + [GIST])
def test_the_hop_line_still_asks_as_a_store_write(line):
    c, d = run(line, READ_ALLOW)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), (line, c, d)


@pytest.mark.parametrize("line", HELPERS + STDIN)
def test_the_hop_line_with_opaque_code_still_asks_as_a_store_write(line):
    c, d = run(line, READ_ALLOW)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "opaque"), (line, c, d)


@pytest.mark.parametrize("policy", [READ_HOST_DENY, READ_HOST_RULE], ids=["deny", "rule"])
def test_a_gist_clone_on_the_host_still_denies(policy):
    c, d = run(GIST, policy)
    assert d.outcome == "deny", (c, d)
    assert ("read", "host") in run(GIST, READ_HOST_RESERVED)[1].reserved


@pytest.mark.parametrize("line", HOPS[:2] + OPAQUE[:1])
def test_a_deny_on_the_repo_still_denies(line):
    c, d = run(line, {"gates": {"deny": [{"category": "read", "target": "pub/x"}]}})
    assert d.outcome == "deny", (line, c, d)


@pytest.mark.parametrize("line", [
    "ssh host gh release download v1 -R pub/x -O /tmp/gates.yaml",
    "ssh host gh repo clone pub/x /tmp/work",
])
def test_a_path_outside_the_store_stays_a_read(line):
    c, d = run(line, READ_ALLOW)
    assert (c.category, d.outcome) == ("read", "allow"), (line, c, d)


@pytest.mark.parametrize("line", [
    "mosh --ssh='ssh -p 2' host -- gh release download v1 -R pub/x -O /tmp/gates.yaml",
    "ssh -o RemoteCommand='gh release download v1 -R pub/x -O /tmp/gates.yaml' host",
])
def test_a_path_outside_the_store_is_no_store_write(line):
    c, _ = run(line, READ_ALLOW)
    assert "store_write" not in {p.category for p in gate._each_part(c)}, (line, c)


def test_the_nested_parts_keep_their_own_targets():
    """The copy on the host is added beside each part: the read keeps its repo and its empty other target."""
    c, _ = run("ssh host gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml", READ_ALLOW)
    reads = {(p.target, p.alt_copy) for p in gate._each_part(c) if p.category == "read"}
    assert {("pub/x", False), ("", True), ("host", True)} <= reads, reads


# Main's newer readings make the same store write below the hop: a store path in another letter case (#635), a `$((`
# that bash reads as a command substitution (#652) and an `env -S` text (#634). The gh read, or the opaque part above
# it, keeps the host there too.
NEWER = [
    "ssh host gh release download v1 -R pub/x -O ~/.EXO/policies/gates.yaml",
    "ssh host gh repo clone pub/x ~/.Exo/Update",
    "mosh host -- gh repo clone pub/x ~/.Exo/Update",
    "ssh host 'echo $((gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml) )'",
    "ssh host 'x=$((gh repo clone pub/x ~/.exo/update) )'",
    "mosh host -- 'echo $((gh repo clone pub/x ~/.exo/update) )'",
]
NEWER_OPAQUE = [
    "ssh host 'eval gh release download v1 -R pub/x -O ~/.EXO/policies/gates.yaml'",
    "echo 'gh release download v1 -R pub/x -O ~/.EXO/policies/gates.yaml' | ssh host",
    "echo 'echo $((gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml) )' | ssh host",
    "ssh host env -S 'A=1 gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml'",
]


@pytest.mark.parametrize("policy", [READ_HOST_DENY, READ_HOST_RULE], ids=["deny", "rule"])
@pytest.mark.parametrize("line", NEWER + NEWER_OPAQUE)
def test_mains_newer_readings_keep_the_hosts_read(line, policy):
    c, d = run(line, policy)
    assert d.outcome == "deny", (line, c, d)


@pytest.mark.parametrize("line", NEWER)
def test_mains_newer_readings_keep_the_hosts_reserved_read(line):
    c, d = run(line, READ_HOST_RESERVED)
    assert d.outcome == "ask" and ("read", "host") in d.reserved, (line, c, d)


@pytest.mark.parametrize("line", NEWER_OPAQUE)
def test_mains_newer_readings_keep_the_hosts_opaque_code(line):
    c, d = run(line, OPAQUE_HOST_DENY)
    assert d.outcome == "deny", (line, c, d)


# Guards, as on main: those lines still ask as the store write, the case-variant delete keeps #601's host entry, and a
# replace tool on a store file (#659) is still the store write it is on main.
@pytest.mark.parametrize("line", NEWER)
def test_mains_newer_readings_still_ask_as_a_store_write(line):
    c, d = run(line, READ_ALLOW)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), (line, c, d)


def test_a_case_variant_delete_on_the_host_keeps_its_reserved_hit():
    c, d = run("ssh host 'rm -rf ~/.EXO/policies'", {"gates": {"reserved": [{"category": "delete", "target": "host"}]}})
    assert d.outcome == "ask" and ("delete", "host") in d.reserved, (c, d)


def test_a_replace_tool_on_a_store_file_still_asks():
    c = gate.classify({"tool": "mcp__fs__search_replace", "args": {
        "path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}})
    d = gate.decide(c, READ_ALLOW)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), (c, d)
