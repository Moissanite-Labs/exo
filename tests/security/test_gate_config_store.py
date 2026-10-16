"""`config.json` and the pinned trust list are part of the protected Exo store (D-074).

`config.json` names the plugins the core loads (the approval gate among them), the plugin config, the models
and the interpreter `exo update` runs. `update/allowed_signers` is the trust root for signed updates (D-073).
A write to either steers the gate or the code that later runs as the principal, so it asks like `policies/`.
Every command string here is data for the classifier; nothing is run.
"""
import pytest

from exo import gate

ALLOW_ALL = {"gates": {"categories": {"store_write": "allow", "local_write": "allow", "shell": "allow",
                                      "opaque_exec": "allow"}}}


def _home(tmp_path, monkeypatch, name="exo-home"):
    home = tmp_path / name
    (home / "update").mkdir(parents=True)
    (home / "plugins").mkdir()
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


def _bash(command, **args):
    return gate.classify({"tool": "bash", "args": {"command": command, **args}})


def test_config_and_trust_pin_are_store_paths(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert gate.is_store_path(str(home / "config.json"))
    assert gate.is_store_path(str(home / "update"))
    assert gate.is_store_path(str(home / "update" / "allowed_signers"))
    assert gate.is_store_path("~/.exo/config.json")
    assert gate.is_store_path("~/.exo/update/allowed_signers")
    # Only the home's own config.json: a project's config.json and the home's other files stay ordinary.
    assert not gate.is_store_path(str(tmp_path / "project" / "config.json"))
    assert not gate.is_store_path(str(home / "plugins" / "x" / "config.json"))
    assert not gate.is_store_path(str(home / "config.json.bak"))
    assert not gate.is_store_path(str(home / "README.md"))


def test_only_the_homes_own_config_json_counts_when_the_home_is_named_dot_exo(tmp_path, monkeypatch):
    # With the home literally named `.exo`, the name rule (`.exo/config.json`) is what is under test: a
    # backup, a `.jsonl` or an editor copy beside the file must not become a principal-only ask.
    home = _home(tmp_path, monkeypatch, ".exo")
    assert gate.is_store_path(str(home / "config.json"))
    for name in ("config.json.bak", "config.jsonl", "config.json~", "config.json5", "config.json.orig"):
        assert not gate.is_store_path(str(home / name)), name
        assert _bash(f"cp /tmp/x {home / name}").category != "store_write", name
    assert not gate.is_store_path("~/.exo/config.json.bak")
    assert gate.is_store_path("~/.exo/config.json")


def test_the_relative_trust_pin_counts_wherever_the_tool_runs(tmp_path, monkeypatch):
    # `update/allowed_signers` is a distinctive name, like `policies/gates.yaml`: a tool whose working folder
    # is the home can spell it without the home, and the gate does not know that folder.
    _home(tmp_path, monkeypatch)
    assert gate.is_store_path("update/allowed_signers")
    assert gate.is_store_path("./update/allowed_signers")
    assert not gate.is_store_path("update/README")
    c = _bash("cp /tmp/evil update/allowed_signers")
    assert c.category == "store_write"
    assert gate.decide(c, ALLOW_ALL).outcome == "ask"


@pytest.mark.parametrize("name", ["config.json", "update/allowed_signers"])
def test_file_tool_writes_ask_and_no_policy_lowers_them(tmp_path, monkeypatch, name):
    home = _home(tmp_path, monkeypatch)
    target = home / name
    for call in ({"tool": "write_file", "args": {"path": str(target), "content": "{}"}},
                 {"tool": "edit_file", "args": {"path": str(target), "old": "a", "new": "b"}}):
        c = gate.classify(call)
        assert c.category == "store_write", call
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", call


@pytest.mark.parametrize("command", [
    "cp /tmp/evil {home}/config.json",
    "mv /tmp/evil {home}/config.json",
    "tee {home}/config.json < /tmp/evil",
    "cat /tmp/evil > {home}/config.json",
    "sed -i s/true/false/ {home}/config.json",
    "cp /tmp/evil {home}/update/allowed_signers",
    "printf x > {home}/update/allowed_signers",
    "rm -rf {home}/update",
])
def test_shell_writes_ask(tmp_path, monkeypatch, command):
    home = _home(tmp_path, monkeypatch)
    c = _bash(command.format(home=home))
    assert c.category == "store_write", command
    assert gate.decide(c, ALLOW_ALL).outcome == "ask", command


@pytest.mark.parametrize("command", [
    "cp /tmp/x/config.json {home}/",                 # the home named as the folder, with a slash
    "cp /tmp/x/config.json {home}",                  # or without: it exists and is a folder
    "cp -t {home} /tmp/x/config.json",
    "cp --target-directory={home} /tmp/x/config.json",
    "cp --target-directory {home} /tmp/x/config.json",
    "cp -r /tmp/update {home}/",
    "install -m 600 -t {home} /tmp/x/config.json",
    "install -m 600 /tmp/x/config.json {home}/",
    "tar -xf evil.tar -C {home}",
    "tar -xC {home} -f evil.tar",
    "tar --directory={home} -xf evil.tar",
    "unzip -o evil.zip -d {home}",
    "unzip -o evil.zip -d {home}/update/",
    "env -C {home} cp /tmp/evil config.json",
    "env --chdir={home} cp /tmp/evil config.json",
    "env -C {home}/update cp /tmp/evil allowed_signers",
    "cp -t{home} /tmp/x/config.json",                # the folder attached to the option, as getopt reads it
    "cp -rt {home} /tmp/update",                     # or the option ending a cluster
    "install -m 600 -t{home} /tmp/x/config.json",
    "tar -xf evil.tar -C{home}",
    "tar -xzC{home} -f evil.tgz",
    "tar xCf {home} evil.tar",                       # old style: each of C, f takes the next operand in order
    "tar xfC evil.tar {home}",
    "tar xf evil.tar -C {home}",
    "tar --extract -f evil.tar --directory {home}",
    "unzip -o evil.zip -d{home}",
    "unzip -od{home} evil.zip",
    "env -C{home} cp /tmp/evil config.json",
])
def test_writes_into_the_home_folder_ask(tmp_path, monkeypatch, command):
    # The file written is `<folder>/<name of the source>`, so a folder destination that is the home itself or
    # `update/` lands on the protected files without ever spelling their names.
    home = _home(tmp_path, monkeypatch)
    c = _bash(command.format(home=home))
    assert c.category == "store_write", command
    assert gate.decide(c, ALLOW_ALL).outcome == "ask", command


def test_folder_writes_elsewhere_stay_ordinary(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    for command in (f"cp /tmp/x {home}/plugins/", f"cp /tmp/x {tmp_path}/", f"tar -xf a.tar -C {tmp_path}",
                    f"unzip -o a.zip -d {tmp_path}", f"env -C {home} ls", f"env -C {home} ls && cp /tmp/x /tmp/y",
                    f"cp -t {tmp_path} /tmp/x", f"cp -t{tmp_path} /tmp/x", f"tar -xf a.tar -C{tmp_path}",
                    f"env -C{home} ls"):
        assert _bash(command).category != "store_write", command


def test_tar_reading_from_the_home_is_not_a_write(tmp_path, monkeypatch):
    # `-C` is where tar extracts only when it extracts; a backup or a listing with `-C` reads there.
    home = _home(tmp_path, monkeypatch)
    for command in (f"tar -czf /tmp/b.tgz -C {home} .", f"tar -cC {home} -f /tmp/b.tgz .", f"tar -tf a.tar -C {home}",
                    f"tar cCf {home} /tmp/b.tar .", f"tar --create --directory={home} -f /tmp/b.tar .",
                    f"tar -rf /tmp/b.tar -C {home} config.json"):
        assert _bash(command).category != "store_write", command


def test_a_relative_dot_exo_folder_counts_by_name(tmp_path, monkeypatch):
    # The home under its default name, spelled relatively: `.exo/` as a folder destination counts like
    # `.exo/config.json` does, whatever the gate knows of the working folder.
    _home(tmp_path, monkeypatch)
    for command in ("cd ~ && cp /tmp/x/config.json .exo/", "cd ~ && cp -t .exo /tmp/x/config.json",
                    "cd ~ && tar -xf e.tar -C .exo", "cd ~ && unzip -o e.zip -d .exo", "cp /tmp/x ./.exo/",
                    "cd ~ && tar -xf e.tar -C .exo/update", "cd ~ && cp -r /tmp/x .exo/policies/",
                    "cd ~ && unzip -o e.zip -d.exo", "cd ~ && tar -xf e.tar -C.exo/update"):
        c = _bash(command)
        assert c.category == "store_write", command
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", command
    for command in ("cd ~ && cp /tmp/x .exo/plugins/", "cd ~ && cp /tmp/x .exo-backup/", "cd ~ && cp /tmp/x exo/",
                    "cd ~ && tar -xf e.tar -C .exorc"):
        assert _bash(command).category != "store_write", command


def test_the_tools_cwd_counts_like_a_leading_cd(tmp_path, monkeypatch):
    # The bash tool runs the command in its `cwd` argument, so a relative `config.json` there is the home's.
    home = _home(tmp_path, monkeypatch)
    for command, cwd in (("sed -i s/x/y/ config.json", str(home)),
                         ("cp /tmp/evil allowed_signers", str(home / "update")),
                         ("printf x > config.json", str(home) + "/"),
                         ("cp /tmp/evil config.json", f"/{home}")):
        c = _bash(command, cwd=cwd)
        assert c.category == "store_write", (command, cwd)
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", (command, cwd)
    assert _bash("sed -i s/x/y/ config.json", cwd=str(tmp_path / "project")).category == "local_write"
    assert _bash("sed -i s/x/y/ config.json").category == "local_write"
    assert _bash("cat config.json", cwd=str(home)).category == "read"
    assert _bash("ls", cwd=42).category == "read"      # an odd cwd is ignored, never raises


def test_a_doubled_leading_slash_names_the_same_file(tmp_path, monkeypatch):
    # The home has its own name here, so only the comparison against the home's path can match it.
    home = _home(tmp_path, monkeypatch)
    assert gate.is_store_path(f"/{home}/config.json")
    assert gate.is_store_path(f"/{home}/update/allowed_signers")
    for command in (f"cp /tmp/evil /{home}/config.json", f"cd /{home} && cp /tmp/evil config.json",
                    f"printf x > /{home}/update/allowed_signers"):
        c = _bash(command)
        assert c.category == "store_write", command
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", command


@pytest.mark.parametrize("command", [
    "exo config set core_plugins '[\"exo-memory\"]'",
    "exo config unset plugin_config.exo-gate",
    "python -m exo.config set install.venv /tmp/evil",
    "python3 -m exo.config unset models",
    "exo update --pin-trust",
    "exo update --pin-trust v0.3.0",
])
def test_exo_commands_that_write_the_files_are_store_writes(command):
    # Before, these were `opaque_exec`, which a policy, a rule or a seat in approvals.yaml can lower.
    c = _bash(command)
    assert c.category == "store_write", command
    assert gate.decide(c, ALLOW_ALL).outcome == "ask", command


def test_other_exo_commands_are_unchanged():
    assert _bash("exo update").category == "opaque_exec"
    assert _bash("exo update --dry-run").category == "opaque_exec"
    assert _bash("exo config show").category == "opaque_exec"
    assert _bash("exo find x").category == "read"


def test_reads_stay_reads(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    for command in (f"cat {home}/config.json", f"cat {home}/update/allowed_signers", f"ls {home}/",
                    f"jq .models {home}/config.json"):
        c = _bash(command)
        assert c.category == "read", command
        assert gate.decide(c, ALLOW_ALL).outcome == "allow", command


def test_a_folder_copied_onto_the_home_asks(tmp_path, monkeypatch):
    # `cp -r SRC DEST/` writes `DEST/<name of SRC>`: a folder named like the home, copied into the home's parent,
    # merges into the home and overwrites `config.json` without the command spelling the home or the file.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EXO_HOME")
    (tmp_path / ".exo").mkdir()                      # the default home exists, as on any machine that runs Exo
    for command in ("cp -r /tmp/.exo ~/", "cp -a /tmp/.exo ~/", "cp -R /tmp/.exo ~", "cp -rt ~/ /tmp/.exo",
                    "cp -r /tmp/.exo/ ~/", "cp -r /tmp/x /tmp/.exo ~/", "cd ~ && cp -r /tmp/.exo .",
                    f"cp -r /tmp/.exo {tmp_path}/", "cp -r /tmp/update ~/.exo/", "cp -r /tmp/update ~/.exo"):
        c = _bash(command)
        assert c.category == "store_write", command
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", command
    for command in ("cp -r /tmp/other ~/", "cp -r /tmp/.exo /tmp/backup/", "cp -r /tmp/.exo/. ~/"):
        assert _bash(command).category == "shell", command
    # `rsync` is a wide program: `~/` contains the home, so the ancestor rule already caught it.
    assert _bash("rsync -a /tmp/.exo ~/").category == "store_write"
    # A custom home: the folder that lands on it carries its name.
    monkeypatch.setenv("EXO_HOME", "/srv/exohome")
    assert _bash("cp -r /tmp/evil/exohome /srv/").category == "store_write"
    assert _bash("cp -r /tmp/evil/other /srv/").category == "shell"
    home = _home(tmp_path, monkeypatch)
    for command in (f"cp -r /tmp/evil/{home.name} {tmp_path}/", f"cp -r /tmp/evil/{home.name} {tmp_path}",
                    f"cp -rt {tmp_path} /tmp/evil/{home.name}"):
        assert _bash(command).category == "store_write", command
    assert _bash(f"cp -r /tmp/evil/other {tmp_path}/").category == "shell"


def test_env_option_clusters_are_read_as_getopt_reads_them(tmp_path, monkeypatch):
    # `env -iC DIR cmd` is `-i` then `-C DIR`: the first option letter that takes a value ends the cluster and
    # the value is the rest of the word or the next one. Before, `DIR` was taken for the program.
    home = _home(tmp_path, monkeypatch)
    for command in (f"env -iC {home} cp /tmp/evil config.json", f"env -iC{home} cp /tmp/evil config.json",
                    f"env -vC {home}/update cp /tmp/evil allowed_signers",
                    f"env -i -C {home} cp /tmp/evil config.json", "env -iS 'cp /tmp/evil ~/.exo/config.json'"):
        c = _bash(command)
        assert c.category == "store_write", command
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", command
    assert _bash("env -i cp /tmp/x /tmp/y").category == "shell"
    assert _bash("env -iu FOO cp /tmp/x /tmp/y").category == "shell"          # `-u FOO` takes a name, not a folder
    assert _bash(f"env -iC {tmp_path} cp /tmp/x config.json").category != "store_write"
    assert _bash(f"env -iC {home} ls").category == "read"


def test_a_bare_relative_dot_exo_destination_counts_by_name(tmp_path, monkeypatch):
    # Without a trailing slash, `cp x .exo` copies into `.exo` when that is a folder, and whether it is one is a
    # question about the tool's working folder, which the gate does not have (isdir() would ask about its own).
    # The name decides, as it does for `.exo/`.
    _home(tmp_path, monkeypatch)
    for command in ("cd ~ && cp /tmp/x/config.json .exo", "cd ~ && cp -r /tmp/update .exo", "cp /tmp/x ./.exo",
                    "cd ~ && install -m 600 /tmp/x/config.json .exo"):
        c = _bash(command)
        assert c.category == "store_write", command
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", command
    for command in ("cd ~ && cp /tmp/x .exo-backup", "cd ~ && cp /tmp/x exo", "cd ~ && cp /tmp/x .exorc",
                    "cp /tmp/x .exo.json"):
        assert _bash(command).category != "store_write", command
