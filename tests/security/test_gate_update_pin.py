"""Issue #133: `$EXO_HOME/update` holds the signed-update trust pin (`update/allowed_signers`, D-073), so a write to
it is a store write and asks the principal.

With `update.require_signed` on, `exo update` installs the upstream tip only when it verifies against that pin. A
tool that rewrote the pin with an allowed write could add its own key, and the principal's next `exo update` would
install code signed by it. Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(params=["~/.exo", "$HOME/.exo", "${HOME}/.exo", "default-abs", "$EXO_HOME", "${EXO_HOME}",
                        "custom-abs"])
def exo_home(request, tmp_path, monkeypatch):
    """The Exo home as a command spells it: the default ~/.exo or a custom EXO_HOME, by name or as a full path."""
    home = tmp_path / "home"
    (home / ".exo" / "update").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    spelling = request.param
    if "EXO_HOME" in spelling or spelling == "custom-abs":
        custom = tmp_path / "srv" / "exo-home"
        (custom / "update").mkdir(parents=True)
        monkeypatch.setenv("EXO_HOME", str(custom))
        return str(custom) if spelling == "custom-abs" else spelling
    monkeypatch.delenv("EXO_HOME", raising=False)
    return str(home / ".exo") if spelling == "default-abs" else spelling


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _assert_store_write(action):
    c = gate.classify(action)
    assert (c.category, gate.decide(c).outcome) == ("store_write", "ask"), (action, c.category)
    # the floor: no policy lowers it (nor a wrapper's opaque_exec part), and only the principal answers, whatever
    # approvals.yaml delegates
    d = gate.decide(c, gate.normalize_policies({"gates": {"categories": {"store_write": "allow", "opaque_exec": "allow"}}}))
    assert (d.outcome, d.rule) == ("ask", "floor"), (action, d)
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"]


PIN = "{h}/update/allowed_signers"
WRITES = [
    # the issue's rows: allowed on main bd503a5d
    "cp /tmp/evil " + PIN,
    "echo 'evil ssh-ed25519 AAAA' > " + PIN,
    # other writers of the file
    "cat /tmp/evil >> " + PIN,
    "tee " + PIN + " < /tmp/evil",
    "tee -a " + PIN + " < /tmp/evil",
    "sed -i s/a/b/ " + PIN,
    "mv /tmp/evil " + PIN,
    "ln -sf /tmp/evil " + PIN,
    "install -m 644 /tmp/evil " + PIN,
    "dd if=/tmp/evil of=" + PIN,
    "rm " + PIN,
    "bash -c 'cp /tmp/evil " + PIN + "'",
    # the folder itself: removed, moved, replaced by a link or a copy, opened up
    "rm -rf {h}/update",
    "mv {h}/update {h}/update.old",
    "ln -sfn /tmp/evil-dir {h}/update",
    "cp -rT /tmp/evil-dir {h}/update",
    "chmod 777 {h}/update",
    # relative names after a cd, and a brace set
    "cd {h} && printf x > update/allowed_signers",
    "cd {h}/update && cp /tmp/evil allowed_signers",
    "echo x > {h}/upd{{a,x}}te/allowed_signers",
]


@pytest.mark.parametrize("line", WRITES)
def test_a_shell_write_to_the_pin_folder_is_a_store_write(line, exo_home):
    _assert_store_write(_sh(line.format(h=exo_home)))


@pytest.mark.parametrize("tool, extra", [
    ("write_file", {"content": "evil ssh-ed25519 AAAA"}),
    ("edit_file", {"old_string": "a", "new_string": "b"}),
    ("patch", {"old_string": "a", "new_string": "b"}),
])
def test_a_file_tool_write_to_the_pin_is_a_store_write(tool, extra, exo_home):
    if "$" in exo_home:
        pytest.skip("a file tool takes its path as written; the shell spellings are covered above")
    path = PIN.format(h=exo_home)
    _assert_store_write({"tool": tool, "args": {"path": path, **extra}})
    if tool == "write_file":
        _assert_store_write({"tool": tool, "args": {"file_path": path, **extra}})


@pytest.mark.parametrize("tool, args", [
    ("delete_file", {"path": "{pin}"}),
    ("delete_file", {"path": "{h}/update"}),
    ("move_file", {"source": "/tmp/evil", "destination": "{pin}"}),
    ("move_file", {"source": "{h}/update", "destination": "/tmp/old"}),
    ("create_directory", {"path": "{h}/update"}),
    ("copy_file", {"source": "/tmp/evil", "destination": "{pin}"}),
])
def test_another_file_tool_on_the_pin_folder_is_a_store_write(tool, args, exo_home):
    if "$" in exo_home:
        pytest.skip("a file tool takes its path as written")
    pin = PIN.format(h=exo_home)
    _assert_store_write({"tool": tool, "args": {k: v.format(pin=pin, h=exo_home) for k, v in args.items()}})


def test_what_the_floor_does_to_a_deny(exo_home):
    """docs/GATE.md "What the floor does to a policy's denies". A shell write keeps its own reading as a part, so a
    deny on that category still denies it. A file tool's write is store_write alone, so a local_write deny no longer
    reaches it and it asks the principal: the trade-off the D-038 amendment of 2026-10-21 records."""
    pin = PIN.format(h=exo_home)
    for line, pol in ((f"printf x > {pin}", {"deny": ["local_write"]}), (f"cp /tmp/evil {pin}", {"deny": ["shell"]}),
                      (f"rm -rf {exo_home}/update", {"deny": ["delete"]})):
        d = gate.decide(gate.classify(_sh(line)), gate.normalize_policies({"gates": pol}))
        assert d.outcome == "deny", (line, pol, d)
    if "$" not in exo_home:
        c = gate.classify({"tool": "write_file", "args": {"path": pin, "content": "x"}})
        d = gate.decide(c, gate.normalize_policies({"gates": {"deny": ["local_write"]}}))
        assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor")


def test_an_apply_patch_to_the_pin_is_a_store_write(exo_home):
    if "$" in exo_home:
        pytest.skip("a patch names its paths as written")
    text = f"*** Begin Patch\n*** Update File: {PIN.format(h=exo_home)}\n@@\n-a\n+b\n*** End Patch\n"
    _assert_store_write({"tool": "apply_patch", "args": {"patch": text}})


def test_the_pin_is_a_store_path(exo_home):
    if "$" in exo_home:
        pytest.skip("is_store_path is given a path, not a shell word")
    assert gate.is_store_path(PIN.format(h=exo_home))
    assert gate.is_store_path(f"{exo_home}/update")
    assert gate.is_store_path(f"{exo_home}/update/anything")


@pytest.mark.parametrize("line", [
    "cat " + PIN,
    "cd {h} && cat update/allowed_signers",
    "ls -la {h}/update",
    "grep ssh-ed25519 " + PIN,
])
def test_reading_the_pin_stays_a_read(line, exo_home):
    c = gate.classify(_sh(line.format(h=exo_home)))
    assert (c.category, gate.decide(c).outcome) == ("read", "allow"), line


def test_a_file_tool_read_of_the_pin_stays_a_read(exo_home):
    if "$" in exo_home:
        pytest.skip("a file tool takes its path as written")
    assert gate.classify({"tool": "read_file", "args": {"path": PIN.format(h=exo_home)}}).category == "read"


@pytest.mark.parametrize("line, category", [
    # a project folder called update, outside the Exo home, is ordinary
    ("printf x > ~/project/update/notes.txt", "local_write"),
    ("cp x update/install.sh", "shell"),
    ("cd ~/project && printf x > update/notes.txt", "local_write"),
    # names in the Exo home that only start with update are not the pin folder
    ("printf x > {h}/updates/notes.txt", "local_write"),
    ("printf x > {h}/update.json", "local_write"),
    # the repo's own list is only a source for --pin-trust, and maintainers edit it
    ("cp x trust/allowed_signers", "shell"),
])
def test_other_update_like_paths_keep_their_class(line, category, exo_home):
    assert gate.classify(_sh(line.format(h=exo_home))).category == category, line


@pytest.mark.parametrize("action", [
    _sh("printf x > update/allowed_signers"),       # a distinctive name counts anywhere, as bus/registry.json
    _sh("cp /tmp/evil ./update/allowed_signers"),
    {"tool": "write_file", "args": {"path": "update/allowed_signers", "content": "x"}},
    # the shell tool's own `cwd` argument, which the store checks do not read: the name still counts
    {"tool": "bash", "args": {"command": "cp /tmp/evil update/allowed_signers", "cwd": "~/.exo"}},
    _sh("printf x > /srv/any/.exo/update/allowed_signers"),   # `.exo/update` counts whatever EXO_HOME is
])
def test_the_pin_names_match_anywhere(action, exo_home):
    _assert_store_write(action)


@pytest.mark.parametrize("line", ["exo update --pin-trust", "exo update --pin-trust @{u}",
                                  "uv run exo update --pin-trust HEAD"])
def test_pin_trust_is_a_store_write(line, exo_home):
    _assert_store_write(_sh(line))


def test_pin_trust_keeps_its_opaque_part_for_policy(exo_home):
    """It was `opaque_exec` on main. A policy that denied that still denies it; one that lowered it now asks."""
    c = gate.classify(_sh("exo update --pin-trust"))
    for pol in ({"deny": ["opaque_exec"]}, {"categories": {"opaque_exec": "deny"}},
                {"rules": [{"match": {"category": "opaque_exec"}, "decision": "deny", "reason": "no"}]}):
        assert gate.decide(c, gate.normalize_policies({"gates": pol})).outcome == "deny", pol
    for pol in ({"categories": {"opaque_exec": "allow"}}, {"categories": {"opaque_exec": "log"}}):
        assert gate.decide(c, gate.normalize_policies({"gates": pol})).outcome == "ask", pol


@pytest.mark.parametrize("line", ["exo update", "exo update --dry-run", "exo update -n"])
def test_other_exo_update_forms_keep_their_class(line, exo_home):
    c = gate.classify(_sh(line))
    assert (c.category, gate.decide(c).outcome) == ("opaque_exec", "ask"), line
