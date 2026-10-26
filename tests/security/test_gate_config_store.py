"""D-074 (#125): `$EXO_HOME/config.json` is a store path, so a tool's write to it asks the principal.

`config.json` names the plugins the core loads (this gate among them), their config, the environment that runs Hermes
and Exo, each provider's `base_url` and headers, and the principal a peer key with no seat file speaks for (#525). On
main edeb4f89 a plain `cp`, redirection, `tee` or file tool write to it ran with no ask, even under
`deny: [store_write]`. Every row is decided against main's reading of the line too (``Classification.ordinary``), so
no line main asks about or denies decides lower. A copy or extraction whose destination is a folder that holds the
file (`cp -t ~/.exo`, `tar -C ~/.exo -xf x.tar`) is #483, not this file. Every command string here is data for
exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(params=["~/.exo", "$HOME/.exo", "${HOME}/.exo", "default-abs", "$EXO_HOME", "${EXO_HOME}",
                        "custom-abs"])
def exo_home(request, tmp_path, monkeypatch):
    """The Exo home as a command spells it: the default ~/.exo or a custom EXO_HOME, by name or as a full path."""
    home = tmp_path / "home"
    (home / ".exo" / "plugins").mkdir(parents=True)
    (home / "project").mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    spelling = request.param
    if "EXO_HOME" in spelling or spelling == "custom-abs":
        custom = tmp_path / "srv" / "exo-home"
        (custom / "plugins").mkdir(parents=True)
        monkeypatch.setenv("EXO_HOME", str(custom))
        return str(custom) if spelling == "custom-abs" else spelling
    monkeypatch.delenv("EXO_HOME", raising=False)
    return str(home / ".exo") if spelling == "default-abs" else spelling


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME with the default ~/.exo and a project folder."""
    h = tmp_path / "home"
    (h / ".exo" / "plugins").mkdir(parents=True)
    (h / "project").mkdir()
    monkeypatch.setenv("HOME", str(h))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    return h


def _sh(cmd, tool="bash"):
    return {"tool": tool, "args": {"command": cmd}}


def _decide(action, gates=None, posting=None):
    c = gate.classify(action)
    pol = {k: v for k, v in (("gates", gates), ("posting", posting)) if v is not None}
    return c, gate.decide(c, gate.normalize_policies(pol) if pol else None)


def _assert_store_write(action):
    c, d = _decide(action)
    assert (c.category, d.outcome) == ("store_write", "ask"), (action, c.category, d)
    # the floor: no policy lowers it, and only the principal answers, whatever approvals.yaml delegates
    d = gate.decide(c, gate.normalize_policies({"gates": {"categories": {
        "store_write": "allow", "opaque_exec": "allow", "shell": "allow", "local_write": "allow", "delete": "allow"}}}))
    assert d.outcome == "ask", (action, d)
    assert gate.call_approvers(c, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"],
                                                                           "shell": ["seat:lead"]}}) == ["principal"]
    assert gate.no_grant(c) and gate.binds_exact(c)
    # a deny on store_write denies it
    assert _decide(action, {"deny": ["store_write"]})[1].outcome == "deny", action
    assert _decide(action, {"categories": {"store_write": "deny"}})[1].outcome == "deny", action


CONFIG = "{h}/config.json"
WRITES = [
    "cp /tmp/evil.json " + CONFIG,
    "mv /tmp/evil.json " + CONFIG,
    "install -m 600 /tmp/evil.json " + CONFIG,
    "tee " + CONFIG + " < /tmp/evil.json",
    "tee -a " + CONFIG + " < /tmp/evil.json",
    "ln -sf /tmp/evil.json " + CONFIG,
    "echo '{{}}' > " + CONFIG,
    "cat /tmp/evil.json >> " + CONFIG,
    "echo '{{}}' >| " + CONFIG,
    "sed -i s/a/b/ " + CONFIG,
    "dd if=/tmp/evil.json of=" + CONFIG,
    "truncate -s 0 " + CONFIG,
    "touch " + CONFIG,
    "chmod 666 " + CONFIG,
    "rm -f " + CONFIG,
    "echo x > {h}/con{{f,x}}ig.json",
    "echo x > {h}/notes/../config.json",
    "echo x > {h}//config.json",
    "echo x > {h}/./config.json",
    # wrapped: sudo, env, a nested shell, a local ssh hop, xargs, find -exec and a function body
    "sudo cp /tmp/evil.json " + CONFIG,
    "sudo tee " + CONFIG + " < /tmp/evil.json",
    "env A=1 cp /tmp/evil.json " + CONFIG,
    "env -i tee " + CONFIG + " < /tmp/evil.json",
    "bash -c 'cp /tmp/evil.json " + CONFIG + "'",
    "ssh localhost 'cp /tmp/evil.json " + CONFIG + "'",
    "ssh 127.0.0.1 'echo x > " + CONFIG + "'",
    "ssh -o ProxyCommand='cp /tmp/evil.json " + CONFIG + "' h",
    "echo /tmp/evil.json | xargs -I@ cp @ " + CONFIG,
    "find /tmp -name evil.json -exec cp {{}} " + CONFIG + " \\;",
    "f() {{ cp /tmp/evil.json " + CONFIG + "; }}; f",
    # a copy out of it asks, as a copy out of policies/ does: the store checks read every word of a write
    "cp " + CONFIG + " /tmp/backup.json",
]


@pytest.mark.parametrize("line", WRITES)
def test_a_shell_write_to_config_json_is_a_store_write(line, exo_home):
    _assert_store_write(_sh(line.format(h=exo_home)))


@pytest.mark.parametrize("line", [
    "cd ~ && echo x > .exo/config.json",
    "cd ~ && cp /tmp/evil.json .exo/config.json",
    "cd ~ && echo x > .exo/./config.json",
    "cd ~ && cp /tmp/evil.json .exo//config.json",
    "cd ~ && cp /tmp/evil.json .exo/notes/../config.json",
    "cd ~/project && echo x > ../.exo/config.json",
    "cd ~ && sed -i s/a/b/ .exo/./config.json",
    "tee x/../.exo/config.json < /tmp/evil.json",
])
def test_a_relative_config_json_is_a_store_write(line, home):
    """A relative name counts by what it names: `.exo/./config.json`, `.exo//config.json` and `.exo/x/../config.json`
    are `.exo/config.json`."""
    _assert_store_write(_sh(line))


@pytest.mark.parametrize("line", [
    "cp /tmp/evil.json $(echo ~/.exo/config.json)",
    "cp /tmp/evil.json \"$(echo ~/.exo/config.json)\"",
    "cp /tmp/evil.json `echo ~/.exo/config.json`",
    "echo x > $(echo ~/.exo/config.json)",
    "tee $(echo $HOME/.exo/config.json) < /tmp/evil.json",
])
def test_a_destination_a_command_prints_counts_by_name(line, home):
    """A word made by a command is read by the names in it, as `$(echo ~/.exo/policies/gates.yaml)` is on main."""
    _assert_store_write(_sh(line))


@pytest.mark.parametrize("tool, args", [
    ("write_file", {"path": CONFIG, "content": "{{}}"}),
    ("write_file", {"file_path": CONFIG, "content": "{{}}"}),
    ("edit_file", {"path": CONFIG, "old_string": "a", "new_string": "b"}),
    ("patch", {"path": CONFIG, "old_string": "a", "new_string": "b"}),
    ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: " + CONFIG + "\n@@\n-a\n+b\n*** End Patch\n"}),
    ("apply_patch", {"patch": "*** Begin Patch\n*** Add File: " + CONFIG + "\n+{{}}\n*** End Patch\n"}),
    ("move_file", {"source": "/tmp/evil.json", "destination": CONFIG}),
    ("copy_file", {"source": "/tmp/evil.json", "destination": CONFIG}),
    ("delete_file", {"path": CONFIG}),
    # The store path under the other keys the scan reads now (#494)
    ("move_file", {"source": "/tmp/evil.json", "to": CONFIG}),
    ("move_file", {"source": "/tmp/evil.json", "target": CONFIG}),
    ("rename_file", {"path": "/tmp/a", "new_path": CONFIG}),
    ("rename_file", {"old_path": CONFIG, "new_path": "/tmp/x"}),
    # A copy out of it asks under its own key, as `cp {h}/config.json /tmp/backup.json` does
    ("copy_file", {"from": CONFIG, "to": "/tmp/backup.json"}),
])
def test_a_file_tool_write_to_config_json_is_a_store_write(tool, args, exo_home):
    if "$" in exo_home:
        pytest.skip("a file tool takes its path as written; the shell spellings are covered above")
    _assert_store_write({"tool": tool, "args": {k: v.format(h=exo_home) for k, v in args.items()}})


def test_a_relative_file_tool_path_counts_by_name(home):
    _assert_store_write({"tool": "write_file", "args": {"path": ".exo/config.json", "content": "x"}})
    _assert_store_write({"tool": "write_file", "args": {"path": ".exo/./config.json", "content": "x"}})


def test_a_relative_config_json_from_the_session_folder_is_a_store_write(home):
    """#494: a file tool's relative write is read from the session folder, as a shell write after a `cd` there is,
    and only the principal answers. A read of the same name stays a read."""
    for tool, args in (("write_file", {"path": "config.json", "content": "x"}),
                       ("move_file", {"source": "/tmp/evil.json", "to": "config.json"})):
        c = gate.classify({"tool": tool, "args": args}, cwd=str(home / ".exo"))
        assert (c.category, gate.decide(c).outcome) == ("store_write", "ask"), (tool, c)
        assert gate.call_approvers(c, {"default": ["seat:lead"],
                                       "categories": {"store_write": ["seat:lead"]}}) == ["principal"], (tool, c)
        assert gate.no_grant(c) and gate.binds_exact(c), (tool, c)
    c = gate.classify({"tool": "read_file", "args": {"path": "config.json"}}, cwd=str(home / ".exo"))
    assert (c.category, gate.decide(c).outcome) == ("read", "allow"), c


def test_config_json_is_a_store_path(exo_home):
    if "$" in exo_home:
        pytest.skip("is_store_path is given a path, not a shell word")
    assert gate.is_store_path(CONFIG.format(h=exo_home))


# Guard rows: main decides these, and the change keeps main's reading.

@pytest.mark.parametrize("line", [
    "cd ~/.exo && echo x > config.json",
    "cd ~/.exo && cp /tmp/x config.json",
    "cd ~/.ex{o,} && cp /tmp/x config.json",
    "cd ~/.exo/plugins && cp /tmp/x ../config.json",
])
def test_a_write_after_a_cd_into_the_home_still_asks(line, home):
    """Main already reads any write after a cd into the home as a store write. fdn-0's brace form read lower on the
    first head of #125; it stays a store write."""
    _assert_store_write(_sh(line))


def test_config_json_names_only_the_homes_own_file(exo_home):
    if "$" in exo_home:
        pytest.skip("is_store_path is given a path, not a shell word")
    for name in ("config.json.bak", "config.jsonl", "config.json~", "config.json-old", "notes.md"):
        assert not gate.is_store_path(f"{exo_home}/{name}"), name


@pytest.mark.parametrize("line", [
    "cat " + CONFIG,
    "jq .models " + CONFIG,
    "grep base_url " + CONFIG,
    "ls -la {h}/",
])
def test_reading_config_json_stays_a_read(line, exo_home):
    c, d = _decide(_sh(line.format(h=exo_home)))
    assert (c.category, d.outcome) == ("read", "allow"), line


def test_a_file_tool_read_of_config_json_stays_a_read(exo_home):
    if "$" in exo_home:
        pytest.skip("a file tool takes its path as written")
    c, d = _decide({"tool": "read_file", "args": {"path": CONFIG.format(h=exo_home)}})
    assert (c.category, d.outcome) == ("read", "allow")


@pytest.mark.parametrize("line, category", [
    # a project's config.json, and the home's other files, keep their class
    ("cp /tmp/x ~/project/config.json", "shell"),
    ("cp /tmp/x config.json", "shell"),
    ("printf x > ~/project/config.json", "local_write"),
    ("cd ~/project && printf x > config.json", "local_write"),
    ("cp ~/project/config.json ~/project/config.json.bak", "shell"),
    ("cp /tmp/x {h}/notes.md", "shell"),
    ("printf x > {h}/notes.md", "local_write"),
    ("printf x > {h}/config.json.bak", "local_write"),
    ("printf x > {h}/config.jsonl", "local_write"),
    ("cp /tmp/x $(echo ~/.exo/config.json.bak)", "shell"),
    ("cp /tmp/x ~/.exo/config.json-old", "shell"),
])
def test_other_config_like_paths_keep_their_class(line, category, exo_home):
    c, d = _decide(_sh(line.format(h=exo_home)))
    assert (c.category, d.outcome) == (category, "allow"), line


def test_the_update_pin_keeps_its_reading(home):
    """`exo update --pin-trust` is the pattern (#443): a store write that keeps its opaque_exec part."""
    for gates, outcome in ((None, "ask"), ({"deny": ["store_write"]}, "deny"), ({"deny": ["opaque_exec"]}, "deny")):
        c, d = _decide(_sh("exo update --pin-trust ssh-ed25519 AAAA"), gates)
        assert (c.category, d.outcome) == ("store_write", outcome), gates


@pytest.mark.parametrize("line", ["exo config show", "exo config get models.default", "exo config path",
                                  "python -m exo.config show"])
def test_other_exo_config_forms_keep_their_class(line, home):
    c, d = _decide(_sh(line))
    assert (c.category, d.outcome) == ("opaque_exec", "ask"), line


# exo commands that write config.json: a store write that keeps its opaque_exec reading as a part, as #443 reads
# `exo update --pin-trust`, so `deny: [opaque_exec]` still denies them.
EXO_WRITERS = ["exo config set models.default x", "exo config unset models.default", "uv run exo config set a b",
               "python -m exo.config set a b", "python3 -m exo.config unset a", "python -m exo.cli config set a b",
               "python -m exo.onboard import-store /tmp/s", "exo import-store /tmp/old-store",
               "exo import-store /tmp/old-store --id me --activate", "exo interview", "sudo exo config set a b",
               "ssh localhost exo config set a b", "exo config -- set a b", "python -m exo.config -- unset a",
               "python -m exo.cli providers default a:b"]


@pytest.mark.parametrize("line", EXO_WRITERS)
def test_an_exo_command_that_writes_config_json_is_a_store_write(line, home):
    _assert_store_write(_sh(line))


@pytest.mark.parametrize("line", EXO_WRITERS)
def test_an_exo_command_that_writes_config_json_keeps_its_opaque_part(line, home):
    """It was opaque_exec on main. A policy that denied that still denies it; one that lowered it now asks."""
    for gates in ({"deny": ["opaque_exec"]}, {"categories": {"opaque_exec": "deny"}},
                  {"rules": [{"match": {"category": "opaque_exec"}, "decision": "deny", "reason": "no"}]}):
        assert _decide(_sh(line), gates)[1].outcome == "deny", (line, gates)
    for gates in ({"categories": {"opaque_exec": "allow"}}, {"categories": {"opaque_exec": "log"}},
                  {"rules": [{"match": {"command": "."}, "decision": "allow", "reason": "all"}]}):
        assert _decide(_sh(line), gates)[1].outcome == "ask", (line, gates)


# `exo providers default` rewrites config.json's model routes. It stays credentials (D-034), with a store write as a
# part: a deny on either denies it, and only the principal answers it.
PROVIDER_WRITERS = ["exo providers default anthropic:claude", "uv run exo providers default a:b"]


@pytest.mark.parametrize("line", PROVIDER_WRITERS)
def test_exo_providers_default_keeps_credentials_and_writes_the_store(line, home):
    c, d = _decide(_sh(line))
    assert (c.category, d.outcome) == ("credentials", "ask"), (line, c)
    for gates in ({"deny": ["credentials"]}, {"categories": {"credentials": "deny"}}, {"deny": ["store_write"]},
                  {"categories": {"store_write": "deny"}}):
        assert _decide(_sh(line), gates)[1].outcome == "deny", (line, gates)
    assert _decide(_sh(line), {"categories": {"credentials": "allow", "store_write": "allow"}})[1].outcome == "ask"
    assert gate.call_approvers(c, {"default": ["seat:lead"], "categories": {"credentials": ["seat:lead"]}}) == \
        ["principal"]


# Fail closed: main's own reading of the line still decides it, and the stricter decision stands.

MATRIX_DENY_CELLS = {"matrix": {"irreversible": {"public": "deny", "internal": "deny"}, "costly": {"public": "deny"}}}
REMOTE_FORMS = [
    "ssh -o RemoteCommand={q} h",
    "ssh -oRemoteCommand={q} h",
    'ssh -o "RemoteCommand {p}" h',
    "scp -o RemoteCommand={q} x h:y",
    "sftp -o RemoteCommand={q} h",
    "sudo ssh -o RemoteCommand={q} h",
    "env A=1 timeout 5 ssh -o RemoteCommand={q} h",
    "true; ssh -o RemoteCommand={q} h",
    "echo $(ssh -o RemoteCommand={q} h)",
    'bash -c "ssh -o RemoteCommand={q} h"',
]


def _remote(payloads):
    return [f.format(q=f"'{p}'", p=p) for f in REMOTE_FORMS for p in payloads]


@pytest.mark.parametrize("tool", ["bash", "run_command"])
@pytest.mark.parametrize("line", _remote(["rm ~/.exo/config.json", "rm -rf /srv/data; cp /tmp/x ~/.exo/config.json",
                                          "git push --force origin main; cp /tmp/x ~/.exo/config.json"]))
def test_a_matrix_deny_on_the_remote_command_still_denies(line, tool, home):
    """The built-in matrix_deny_cells mapping denied these on main: the command ssh, scp or sftp runs on the host is
    irreversible and internal."""
    c, d = _decide(_sh(line, tool), MATRIX_DENY_CELLS)
    assert d.outcome == "deny", (line, c.category, d)


OPAQUE_INTERNAL = {"category": "opaque_exec", "exposure": "internal"}


@pytest.mark.parametrize("line", _remote(["perl -pi -e s/a/b/ ~/.exo/config.json", "exo config set a b",
                                          "python3 -c 1 > ~/.exo/config.json"]))
def test_an_internal_exposure_entry_on_the_remote_command_still_holds(line, home):
    """A deny, a rule or a reserved entry on opaque_exec at internal exposure held on main for the remote command."""
    for gates in ({"deny": [OPAQUE_INTERNAL]},
                  {"rules": [{"match": OPAQUE_INTERNAL, "decision": "deny", "reason": "remote"}]}):
        c, d = _decide(_sh(line), gates)
        assert d.outcome == "deny", (line, gates, c.category, d)
    c, d = _decide(_sh(line), {"reserved": [OPAQUE_INTERNAL]})
    assert d.outcome == "ask" and "opaque_exec" in {r[0] for r in d.reserved}, (line, d)


@pytest.mark.parametrize("line", [
    "cat ~/.ssh/id_rsa; cp /tmp/x ~/.exo/config.json",
    "cp /tmp/x ~/.exo/config.json; cat ~/.aws/credentials",
    "tar -C ~/.exo -czf - config.json ../.ssh/id_rsa",
    "cat ~/.ssh/id_rsa; ssh -o RemoteCommand='rm -rf /srv/data; cp /tmp/x ~/.exo/config.json' h",
    "cat ~/.ssh/id_rsa | ssh -o RemoteCommand='cp /tmp/x ~/.exo/config.json' h",
    "cat ~/.ssh/id_rsa; bash -c 'rm -rf /srv/data; cp /tmp/x ~/.exo/config.json'",
])
def test_a_credentials_deny_still_denies(line, home):
    c, d = _decide(_sh(line), {"deny": ["credentials"]})
    assert d.outcome == "deny", (line, c.category, d)


SECRET_IRREVERSIBLE = {"category": "credentials", "reversibility": "irreversible"}


@pytest.mark.parametrize("line", [
    "cat ~/.ssh/id_rsa; bash -c 'rm -rf /srv/data; cp /tmp/x ~/.exo/config.json'",
    "cat ~/.ssh/id_rsa; ssh h 'rm -rf /srv/data; cp /tmp/x ~/.exo/config.json'",
    "cat ~/.ssh/id_rsa; sh -c 'rm -rf /srv/data; exo config set a b'",
    "cat ~/.ssh/id_rsa; echo $(rm -rf /srv/data; cp /tmp/x ~/.exo/config.json)",
    "cat ~/.ssh/id_rsa; ssh -o RemoteCommand='rm -rf /srv/data; cp /tmp/x ~/.exo/config.json' h",
])
def test_a_secret_beside_a_nested_delete_keeps_its_weight(line, home):
    """Main weighed the secret as the delete beside it, so a rule, a deny and a reserved entry on irreversible
    credentials, and an irreversible and private matrix cell, still hold."""
    for gates in ({"rules": [{"match": SECRET_IRREVERSIBLE, "decision": "deny", "reason": "no"}]},
                  {"deny": [SECRET_IRREVERSIBLE]}, {"matrix": {"irreversible": {"private": "deny"}}}):
        assert _decide(_sh(line), gates)[1].outcome == "deny", (line, gates)
    d = _decide(_sh(line), {"reserved": [SECRET_IRREVERSIBLE]})[1]
    assert d.outcome == "ask" and "credentials" in {r[0] for r in d.reserved}, (line, d)


DEV_PROC = {"deny": ["target:/dev*", "target:/proc*"]}


@pytest.mark.parametrize("line", [
    "cp ~/.exo/config.json /dev/fd/3",
    "cp /dev/fd/3 ~/.exo/config.json",
    "tee ~/.exo/config.json /dev/fd/3 < /tmp/x",
    "cp /tmp/x ~/.exo/config.json /proc/self/fd/3",
    "cat /tmp/x > ~/.exo/config.json 2> /dev/fd/3",
    "cd /dev && cp x ~/.exo/config.json",
    "cd /proc/self/fd && cp x ~/.exo/config.json",
    "ssh h 'python3 -c 1 > ~/.exo/config.json' > /dev/fd/3",
    "exo config set a b > /dev/fd/3",
])
def test_a_descriptor_target_still_denies(line, home):
    """The line keeps main's descriptor target, so a `target:/dev*` deny still denies."""
    c, d = _decide(_sh(line), DEV_PROC)
    assert d.outcome == "deny", (line, c.target, d)


@pytest.mark.parametrize("line", [
    "echo x > ~/.exo/config.json; git push --force origin main",
    "cp /tmp/x ~/.exo/config.json && git push origin --delete main",
    "npm publish; echo '{}' > ~/.exo/config.json",
    "ssh host 'rm -f ~/.exo/config.json'",
])
def test_a_matrix_deny_on_another_part_still_denies(line, home):
    c, d = _decide(_sh(line), {"matrix": {"irreversible": {"public": "deny", "internal": "deny", "private": "deny"},
                                          "costly": {"public": "deny"}}})
    assert d.outcome == "deny", (line, c.category, d)


COSTLY_RULE = {"rules": [{"match": {"category": ["local_write", "shell", "read"], "reversibility": "costly"},
                          "decision": "deny", "reason": "run by a privileged wrapper"}]}


@pytest.mark.parametrize("line", ["sudo ssh host 'cp /dev/null ~/.exo/config.json'",
                                  "chroot /srv ssh host 'cp /dev/null ~/.exo/config.json'",
                                  "sudo cp /tmp/x ~/.exo/config.json"])
def test_a_rule_on_a_costly_part_still_denies(line, home):
    c, d = _decide(_sh(line), COSTLY_RULE)
    assert d.outcome == "deny", (line, c.category, d)


@pytest.mark.parametrize("line, host", [
    ("ssh h 'exo config set a b'", "h"),
    ("ssh host 'perl -pi -e s/a/b/ ~/.exo/config.json'", "host"),
    ("sudo ssh host 'perl -pi -e s/a/b/ ~/.exo/config.json'", "host"),
])
def test_a_reserved_entry_on_the_remote_reading_still_hits(line, host, home):
    for gates in ({"reserved": ["opaque_exec"]}, {"reserved": [{"category": "opaque_exec", "target": host}]}):
        d = _decide(_sh(line), gates)[1]
        assert d.outcome == "ask" and ("opaque_exec", host) in {tuple(r) for r in d.reserved}, (line, gates, d)


ORDER_ASK_UNDER_CATEGORY_DENY = {"categories": {"delete": "deny", "force_push": "deny"},
                                 "rules": [{"match": {"command": "^git status"}, "decision": "ask", "reason": "a"},
                                           {"match": {"command": ".*"}, "decision": "allow", "reason": "b"}]}


@pytest.mark.parametrize("line", [
    "f() { rm -rf /srv/data; cp /tmp/x ~/.exo/config.json; }; f",
    "f() ( git push --force origin main; cp /tmp/x ~/.exo/config.json ); f",
    "bash -c 'f() { rm -rf /srv/data; cp /tmp/x ~/.exo/config.json; }; f'",
    "f() { rm -rf /srv/data; }; f; exo config set a b",
])
def test_a_category_deny_main_kept_under_a_catch_all_rule_still_denies(line, home):
    """The built-in order_ask_under_category_deny mapping: the catch-all rule does not name the function definition
    (#394), so the category denied the line on main."""
    assert _decide(_sh(line), ORDER_ASK_UNDER_CATEGORY_DENY)[1].outcome == "deny", line


FOOTER = "missing footer: -- sent by an agent"


@pytest.mark.parametrize("line, listed", [
    ("sh -c 'gh pr comment 1 --body hi; cp /tmp/x ~/.exo/config.json'; "
     "sh -c 'gh pr comment 1 --body hi; cp /tmp/x ~/.exo/config.json'", [FOOTER, f"GitHub PR comment on #1: {FOOTER}"]),
    ("gh pr comment 1 --body hi; gh pr comment 2 --body hi; cp /tmp/x ~/.exo/config.json",
     [FOOTER, f"GitHub PR comment on #2: {FOOTER}"]),
    ("ssh -o RemoteCommand='gh pr comment 1 --body hi; exo config set a b' h", [FOOTER]),
])
def test_a_post_keeps_mains_violations(line, listed, home):
    """Each violation main listed for the posts on the line is still listed."""
    d = _decide(_sh(line), posting={"footer": "-- sent by an agent"})[1]
    assert d.outcome == "ask" and set(listed) <= set(d.violations), (line, d.violations)


def test_a_line_main_read_otherwise_keeps_that_reading(home):
    """The line as main read it is kept beside the store write (``ordinary``), and decide takes both."""
    c = gate.classify(_sh("ssh -o RemoteCommand='rm -rf /srv/data; cp /tmp/x ~/.exo/config.json' h"))
    assert c.category == "store_write" and c.ordinary is not None and c.ordinary.category == "delete"
    assert c.ordinary in gate.readings(c) and "ordinary" not in c.as_dict()
    assert gate.classify(_sh("cp /tmp/x ~/.exo/notes.md")).ordinary is None
