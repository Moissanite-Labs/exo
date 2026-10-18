"""`config.json` and the pinned trust list are part of the protected Exo store (D-074).

`config.json` names the plugins the core loads (the approval gate among them), the plugin config, the models
and the interpreter `exo update` runs. `update/allowed_signers` is the trust root for signed updates (D-073).
A write to either steers the gate or the code that later runs as the principal, so it asks like `policies/`.
Every command string here is data for the classifier; nothing is run.
"""
import time

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


def _asks(command, **args):
    c = _bash(command, **args)
    assert c.category == "store_write", (command, args, c.category)
    assert gate.decide(c, ALLOW_ALL).outcome == "ask", (command, args)


@pytest.mark.parametrize("command", [
    "tar -xf evil.tar -C {p}",
    "tar -xC{p} -f evil.tar",
    "unzip -o evil.zip -d {p}",
    "unzip -od{p} evil.zip",
    "cp -t {p} /tmp/x",
    "cp -rt{p} /tmp/x",
    "install -m 600 -t {p} /tmp/x",
    "cp /tmp/x {p}/",
    "cp /tmp/x {p}",
    "cp -r /tmp/abc {home}/principals/",          # a folder copied over a principal store merges into it
    "tar -xf evil.tar -C {home}/principals",      # members land as principals/<id>/policies/...
    "tar -xf evil.tar -C {p}/state",
])
def test_writes_into_a_default_layout_principal_store_ask(tmp_path, monkeypatch, command):
    # With EXO_PRINCIPAL_STORE unset the store is `principals/<id>` in the home. Its `policies/`, `state/`, `keys/`
    # and ledger sit right under that folder, so the folder as a destination lands on them like the home does.
    home = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    (home / "principals" / "abc").mkdir(parents=True)
    _asks(command.format(home=home, p=home / "principals" / "abc"))


@pytest.mark.parametrize("command", [
    "tar -xf evil.tar -C {p}", "unzip -o evil.zip -d {p}", "cp -t {p} /tmp/x", "cp /tmp/x {p}/", "cp /tmp/x {p}",
    "tar -xf evil.tar -C {p}/keys",
])
def test_writes_into_a_named_principal_store_ask(tmp_path, monkeypatch, command):
    _home(tmp_path, monkeypatch)
    store = tmp_path / "pstore"
    store.mkdir()
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(store))
    _asks(command.format(p=store))


def test_a_principal_store_folder_that_is_not_protected_stays_ordinary(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    notes = home / "principals" / "abc" / "notes"
    notes.mkdir(parents=True)
    assert _bash(f"tar -xf a.tar -C {notes}").category != "store_write"
    assert _bash(f"cp /tmp/x {notes}/").category != "store_write"


def test_any_folder_of_the_home_counts_as_the_store(tmp_path, monkeypatch):
    # `plugins/`, `venv/` and `src/` are not store paths (D-074), but from there `..`, `$OLDPWD`, an option's value,
    # a glob or a program's own writes reach the store in more spellings than the gate can resolve, so every write
    # made from a folder of the home asks, as it did before D-074.
    home = _home(tmp_path, monkeypatch)
    (home / "plugins" / "foo").mkdir()
    foo = home / "plugins" / "foo"
    for command in (f"cd {foo} && cp /tmp/x config.json", f"env -C {foo} cp /tmp/x config.json",
                    f"cd {foo} && printf x > config.json", f"cd {foo} && cp -r /tmp/x ..",
                    f"cd {home}/venv && cp /tmp/x config.json", f"cd {foo} && cd .. && cp /tmp/x config.json",
                    f"cd {foo} && cp /tmp/x ../../config.json", f"cd {foo} && printf x > ../../config.json",
                    f"cd {foo} && cp /tmp/x ../../update/", f"cd {foo} && cp /tmp/x ../..",
                    f"cd {foo} && tar -xf a.tar -C ../..", f"cd {foo} && rm -rf ../..",
                    f"env -C {foo} cp /tmp/x ../../config.json", f"cd {foo} && cd ../.. && cp /tmp/x config.json",
                    f"cd {foo} && echo $(cp /tmp/x ../../config.json)",
                    f"cd {foo} && cd $D && cp /tmp/x config.json", f"cd {foo} && cd - && cp /tmp/x config.json",
                    f"pushd {foo} && popd && cp /tmp/x config.json", f"cd {foo} && make",
                    f"cd {foo} && cd .exo && cp /tmp/x config.json",
                    # Spellings a resolver against the folder would miss.
                    f"cd {foo} && cp /tmp/x $OLDPWD/config.json", f"cd {foo} && cp /tmp/x ~-/config.json",
                    f"cd {foo} && cp /tmp/x ../../conf*.json", f"cd {foo} && cp /tmp/x ../../{{config,x}}.json",
                    f"cd {foo} && curl -o../../config.json https://e.test/x",
                    f"cd {foo} && GIT_DIR=../../principals/abc/.git git checkout .",
                    f"cd {foo} && sudo -D ../.. cp /tmp/x config.json",
                    f"cd {foo} && tar -xf /tmp/a.tar", f"cd {foo} && npm install"):
        _asks(command)
    for args in ({"cwd": str(foo)}, {"workdir": str(foo)}):
        for command in ("cp /tmp/x config.json", "printf x > config.json", "cp /tmp/x ../../config.json", "make"):
            _asks(command, **args)


def test_a_project_folder_stays_ordinary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EXO_HOME")
    (tmp_path / ".exo").mkdir()
    (tmp_path / "project").mkdir()
    for command, args in (("env -C ~/project cp /tmp/x config.json", {}),
                          ("cp /tmp/x config.json", {"cwd": "~/project"}),
                          ("cd ~/project && cp /tmp/x config.json", {}),
                          ("cp /tmp/x config.json", {"workdir": "~/project"}),
                          ("cd /tmp/$X && cp /tmp/x config.json", {})):
        assert _bash(command, **args).category != "store_write", (command, args)


@pytest.mark.parametrize("command, args", [
    # A slashless destination that names a principal store, resolved against the folder the line stands in.
    ("cd {foo} && cp -r /tmp/abc ../../principals", {}),
    ("cp -r /tmp/abc ../../principals", {"cwd": "{foo}"}),
    ("cp -r /tmp/abc ../../principals", {"workdir": "{foo}"}),
    ("cd {foo} && cp /tmp/x ../../principals/abc", {}),
    ("cd {foo} && install /tmp/x ../../principals/abc", {}),
    ("cd {foo} && scp h:x ../../principals/abc", {}),
    ("cd {foo} && mv /tmp/x ../../principals/abc", {}),
    ("cd {home}/plugins && cp -r /tmp/abc ../principals", {}),
    # A folder of the home the gate cannot spell out may be the home itself or a principal store.
    ("cd {home}/plugins/${{X:-..}} && cp /tmp/x config.json", {}),
    ("cd {home}/plugins/$X && cp /tmp/x config.json", {}),
    ("cd {home}/$X && cp /tmp/x config.json", {}),
    ("D=..; cd {home}/plugins/$D && cp /tmp/x config.json", {}),
    ("cd {home}/princ* && cp -r /tmp/abc .", {}),
    ("cd {home}/$(echo principals) && cp -r /tmp/abc .", {}),
    ("cd {home}/`echo .` && cp /tmp/x config.json", {}),
    ("env -C {home}/$X cp /tmp/x config.json", {}),
    ("pushd {home}/$X && cp /tmp/x config.json", {}),
    ("cp /tmp/x config.json", {"cwd": "{home}/$X"}),
    ("cp /tmp/x config.json", {"workdir": "{home}/*"}),
    # `git -C` from a folder of the home: the principal store is a git repo, and a merge rewrites its policies.
    ("cd {foo} && git -C ../../principals/abc pull /tmp/evil", {}),
    ("cd {foo} && git -C ../../principals/abc stash pop", {}),
    ("cd {foo} && git -C ../.. pull /tmp/evil", {}),
    ("git -C ../../principals/abc merge evil", {"cwd": "{foo}"}),
    ("cd {foo} && git --work-tree=../.. checkout .", {}),
    # The folder the line stands in, spelled as `$PWD`, `~+` or `$(pwd)`.
    ("cd {foo} && cp /tmp/x $PWD/../../config.json", {}),
    ("cd {foo} && cp /tmp/x ${{PWD}}/../../config.json", {}),
    ("cd {foo} && cp /tmp/x \"$(pwd)/../../config.json\"", {}),
    ("cd {foo} && cp /tmp/x `pwd`/../../config.json", {}),
    ("cd {foo} && cp /tmp/x ~+/../../config.json", {}),
    ("cp /tmp/x $PWD/../../update/allowed_signers", {"cwd": "{foo}"}),
])
def test_a_folder_of_the_home_still_reaches_the_store(tmp_path, monkeypatch, command, args):
    home = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    (home / "plugins" / "foo").mkdir()
    (home / "principals" / "abc").mkdir(parents=True)
    f = {"home": home, "foo": home / "plugins" / "foo"}
    _asks(command.format(**f), **{k: v.format(**f) for k, v in args.items()})


def test_a_relative_principal_store_counts_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EXO_HOME", raising=False)
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    (tmp_path / ".exo" / "principals" / "abc").mkdir(parents=True)
    for command, args in (("cd ~ && cp /tmp/x .exo/principals/abc", {}), ("cp /tmp/x .exo/principals/abc", {}),
                          ("cp /tmp/x .exo/principals/abc", {"cwd": str(tmp_path)}),
                          ("cd ~ && cp -r /tmp/abc .exo/principals", {}),
                          ("cd ~ && install /tmp/x .exo/principals/abc", {}),
                          ("cd ~ && mv /tmp/x .exo/principals/abc", {}), ("mv -t .exo/principals/abc /tmp/x", {})):
        _asks(command, **args)


def test_a_custom_home_spelled_from_a_known_folder_counts(tmp_path, monkeypatch):
    # The folder the line stands in is known after `cd` or from `cwd`, so a relative custom home resolves.
    monkeypatch.setenv("HOME", str(tmp_path))
    home = _home(tmp_path, monkeypatch, "my-exo")
    _asks("cd ~ && cp /tmp/x my-exo/")
    _asks("cp /tmp/x my-exo/config.json", cwd=str(tmp_path))
    _asks("cd ~ && mv /tmp/x my-exo")
    assert _bash("cd ~ && cp /tmp/x my-exo/plugins/").category != "store_write"
    assert home.is_dir()


@pytest.mark.parametrize("command, args", [
    ("cp /tmp/x {home}/config.json", {}),
    ("cp /tmp/x {home}/", {}),
    ("cp /tmp/x {home}/state/approval-grants.json", {}),
    ("cd {home} && cp /tmp/x config.json", {}),
    ("cd {home} && echo hi > state/foo", {}),
    ("cp /tmp/x config.json", {"cwd": "{home}"}),
    ("git -C {home} checkout -- .", {}),
    ("tar -xf evil.tar -C {home}", {}),
])
def test_a_relative_exo_home_still_protects_the_absolute_home(tmp_path, monkeypatch, command, args):
    # The core takes EXO_HOME as given, so a relative one names the home from the gate's own folder; a line
    # that spells the home absolutely, or moves into it, writes the same store.
    home = _home(tmp_path, monkeypatch, "rel-exo")
    (home / "state").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXO_HOME", "rel-exo")
    _asks(command.format(home=home), **{k: v.format(home=home) for k, v in args.items()})
    c = gate.classify({"tool": "write_file", "args": {"path": str(home / "config.json"), "content": "{}"}})
    assert c.category == "store_write"


def test_a_relative_principal_store_still_protects_the_absolute_store(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    (tmp_path / "ps" / "keys").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", "ps")
    _asks(f"cd {tmp_path / 'ps'} && cp /tmp/x keys/a")
    _asks("cp /tmp/x keys/a", cwd=str(tmp_path / "ps"))


def test_a_long_line_of_folder_moves_stays_fast(tmp_path, monkeypatch):
    # Outside the home each word is also resolved against the folder the line moved into; that stays cheap.
    home = _home(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    args = " ".join(f"a{i}" for i in range(300))
    start = time.monotonic()
    assert _bash(f"cd {project}; " + "cd x; cd ..; " * 300 + f"cp {args} d/").category != "store_write"
    assert _bash(f"cd {project}; " + "; ".join(f"cd d{i}" for i in range(300)) + f"; cp {args} d/").category \
        != "store_write"
    _asks(f"cd {project}; " + "cd x; cd ..; " * 300 + f"cp {args} {home}/")
    _asks(f"cd {home}/plugins; " + "; ".join(f"cd d{i}" for i in range(300)) + f"; cp {args} d/")
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("command, args", [
    ("cd {home} && echo $(cp /tmp/e config.json)", {}),
    ("cd {home} && echo `cp /tmp/e config.json`", {}),
    ("cd {home} && cat <(cp /tmp/e config.json)", {}),
    ("cd {home} && tee >(cat > config.json) < /tmp/e", {}),
    ("cd {home}/update && echo \"$(cp /tmp/e allowed_signers)\"", {}),
    ("env -C {home} echo $(cp /tmp/e config.json)", {}),
    ("echo $(cp /tmp/e config.json)", {"cwd": "{home}"}),
    ("echo $(echo $(cp /tmp/e config.json))", {"cwd": "{home}"}),
])
def test_the_store_folder_carries_into_substitutions(tmp_path, monkeypatch, command, args):
    home = _home(tmp_path, monkeypatch)
    _asks(command.format(home=home), **{k: v.format(home=home) for k, v in args.items()})


@pytest.mark.parametrize("command", [
    "python -m exo.cli config set core_plugins []",
    "python3 -m exo.cli config unset models",
    "python3.11 -m exo.cli update --pin-trust",
    "uv run python -m exo.cli config set install.venv /tmp/evil",
    "python -m exo.onboard init me",
    "python -m exo.onboard import-store /tmp/s --activate",
    "python -m exo.onboard interview --answers /tmp/a.yaml",
    "python -m exo.onboard config set principal me",
    "exo import-store /tmp/s",
    "exo interview",
])
def test_the_exo_modules_that_write_the_store_ask(command):
    # `exo.onboard` is `exo init`, `interview`, `import-store` and `config`: init and import-store write
    # config.json (they activate the principal), interview writes the principal's ledger.
    _asks(command)


def test_other_exo_module_runs_stay_opaque():
    # `-m` loads `exo` from wherever Python finds it first, so only a write is mapped; the rest stays opaque.
    for command in ("python -m exo.cli find x", "python -m exo.cli", "python -m exo.onboard --help",
                    "python -m exo.config show"):
        assert _bash(command).category == "opaque_exec", command


@pytest.mark.parametrize("command", [
    "curl -o {home}/config.json https://e.test/x",
    "curl --output={home}/config.json https://e.test/x",
    "curl -sSLo{home}/config.json https://e.test/x",
    "curl --output-dir {home} -O https://e.test/config.json",
    "curl --output-dir={home}/update -o allowed_signers https://e.test/x",
    "wget -O {home}/update/allowed_signers https://e.test/x",
    "wget -P {home} https://e.test/config.json",
    "wget -qP{home}/update https://e.test/allowed_signers",
    "wget --directory-prefix={home} https://e.test/config.json",
    "scp host:config.json {home}/",
    "scp host:config.json {home}",
    "scp -r host:/tmp/update {home}/",
    "rsync host:config.json {home}/",
])
def test_download_destinations_in_the_store_ask(tmp_path, monkeypatch, command):
    home = _home(tmp_path, monkeypatch)
    _asks(command.format(home=home))


def test_downloads_elsewhere_stay_ordinary(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    for command in (f"curl --output-dir {tmp_path} -O https://e.test/a", f"wget -P {tmp_path} https://e.test/a",
                    "curl -o /tmp/a https://e.test/a", f"scp host:a {tmp_path}/", "wget -qO- https://e.test/a"):
        assert _bash(command).category != "store_write", command


def test_dotted_relative_spellings_normalize(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    for path in ("update/./allowed_signers", "./update/allowed_signers", "update//allowed_signers",
                 "x/../update/allowed_signers", "policies/./gates.yaml"):
        assert gate.is_store_path(path), path
        _asks(f"cp /tmp/e {path}")
    _asks("cp /tmp/e policies/../config.json", cwd=str(home))
    _asks(f"cd {home}/update && cp /tmp/e ./../config.json")
    assert not gate.is_store_path("update/../README")


def test_the_workdir_argument_counts_like_cwd(tmp_path, monkeypatch):
    # exec_command-style tools name the folder `workdir`; either argument in the store makes relative names count.
    home = _home(tmp_path, monkeypatch)
    _asks("cp /tmp/x config.json", workdir=str(home))
    _asks("cp /tmp/x allowed_signers", workdir=str(home / "update"))
    _asks("cp /tmp/x config.json", cwd=str(tmp_path), workdir=str(home))
    c = gate.classify({"tool": "exec_command", "args": {"cmd": "sed -i s/a/b/ config.json", "workdir": str(home)}})
    assert c.category == "store_write"
    assert _bash("ls", workdir=7).category == "read"


def _custom_home(tmp_path, monkeypatch):
    # A home not named `.exo`: the name rule would catch `.exo` spellings even where the folder rule is missing.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    home = _home(tmp_path, monkeypatch, "my-exo")
    (home / "plugins" / "foo").mkdir()
    (tmp_path / "project").mkdir()
    return home


@pytest.mark.parametrize("command", [
    "git -C ~/my-exo/plugins/foo checkout -- ../../config.json",
    "git -C ~/my-exo/plugins/$X pull",
    "git -C ~/my-exo/plugins/foo stash pop",
    "git --work-tree=~/my-exo/plugins/foo checkout .",
    "git --work-tree ~/my-exo/plugins/foo checkout .",
    "git --git-dir=$EXO_HOME/plugins/foo/.git checkout .",
    "GIT_WORK_TREE=~/my-exo/plugins/foo git checkout .",
    "GIT_DIR=~/my-exo/plugins/foo/.git git checkout .",
    "env GIT_WORK_TREE=~/my-exo/plugins/foo git pull",
    # Each `-C` moves on from the one before, as `cd` does, and the file names resolve from the last.
    "git -C ~ -C my-exo/plugins/foo pull",
    "git -C ~ -C my-exo/plugins/foo checkout -- ../../config.json",
    "git -C ~/project -C ../my-exo/plugins/foo pull",
    "git -C ~ checkout -- my-exo/config.json",
    "git -C ~ --work-tree=my-exo/plugins/foo checkout .",
    "git -C ~ commit -C HEAD -- my-exo/config.json",     # `commit -C` names a commit, not a folder
    "cd ~ && git -C my-exo/plugins/foo pull",
    # `--output` writes a file, relative to git's folder.
    "git -C ~/my-exo/plugins/foo diff --output=../../config.json",
    "git -C ~/my-exo/plugins/foo log -p --output ../../config.json",
    "cd ~/my-exo && git diff --output=config.json",
])
def test_git_folders_in_the_home_follow_the_folder_rule(tmp_path, monkeypatch, command):
    # git's `-C`, `--work-tree`, `--git-dir`, `GIT_DIR=` and `GIT_WORK_TREE=` work in a folder like `cd` does:
    # from a folder of the home, `..` and whatever the subcommand writes reach the store.
    _custom_home(tmp_path, monkeypatch)
    _asks(command)


def test_git_folders_elsewhere_stay_ordinary(tmp_path, monkeypatch):
    _custom_home(tmp_path, monkeypatch)
    for command in ("git -C ~/project pull", "git -C ~ -C project pull", "cd ~ && git commit -C HEAD",
                    "git -C ~ commit -C HEAD -- project/a.py", "GIT_DIR=/tmp/x/.git git checkout .",
                    "git diff --output=/tmp/x", "git -C ~/project diff --output=a.patch"):
        assert _bash(command).category != "store_write", command
    for command in ("git -C /tmp/x status", "git -C ~/my-exo/plugins/foo status",
                    "git diff --output-indicator-new=+ HEAD"):
        assert _bash(command).category == "read", command


@pytest.mark.parametrize("command", [
    'curl --output-dir /tmp --output-dir "$EXO_HOME" -O https://example.invalid/config.json',
    "curl --output-dir=/tmp --output-dir ~/.exo -O https://e.test/config.json",
    "wget -P /tmp -P ~/.exo https://e.test/config.json",
    "wget -P /tmp --directory-prefix=~/.exo https://e.test/config.json",
    "tar -xf a.tar -C /tmp -C ~/.exo",
    "tar -C /tmp -xf a.tar -C ~/.exo",
    "tar xCf /tmp a.tar -C ~/.exo",
    "cp -t /tmp -t ~/.exo/principals/abc x",
    "install -t /tmp --target-directory=~/.exo x",
    "unzip z.zip -d /tmp -d ~/.exo",
])
def test_a_repeated_destination_option_counts_every_value(tmp_path, monkeypatch, command):
    # curl and wget take the last value and each tar `-C` applies to the members after it, so no one value decides.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    home = _home(tmp_path, monkeypatch, ".exo")
    (home / "principals" / "abc").mkdir(parents=True)
    _asks(command)
    assert _bash("curl --output-dir /tmp -O https://e.test/x").category == "web_read"
    assert _bash("tar -cf a.tar -C /tmp -C ~/.exo .").category != "store_write"


@pytest.mark.parametrize("command", [
    "cp -r -t ~/ -- -tx /tmp/.exo",                     # after `--`, `-tx` is a source, not a second folder
    "cp -r -t ~/ /tmp/.exo -- -tx",
    "cp -r -t ~/ -- --target-directory=y /tmp/.exo",
    "mv -t ~/ -- -tx /tmp/.exo",
    "cp -r -S -t/tmp/x -t ~/ /tmp/.exo",                # `-t/tmp/x` is the backup suffix; the last `-t` is the folder
    # a word after `--` spelled like the source is a source too, never a reason to drop that source
    "cp -r -t ~/ -- -t/tmp/.exo /tmp/.exo",
    "cp -r -t ~/ -- -t /tmp/.exo",
    "cp -r -t ~/ /tmp/.exo -- -t/tmp/.exo",
    "cp -r -t ~/ -- --target-directory=/tmp/.exo /tmp/.exo",
    "install -t ~/ -- -t/tmp/.exo /tmp/.exo",
    "cp -r -S -t/tmp/.exo -t ~/ /tmp/.exo",
    "cp -r -t ~/ -- -x/.exo",                           # a source named with a leading `-` after `--`
])
def test_a_target_option_that_is_not_one_still_pairs_the_sources(tmp_path, monkeypatch, command):
    # cp refuses a second `-t`, so another one the gate reads is a source or another option's value: the sources
    # still land in the first and in the last folder.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EXO_HOME")
    (tmp_path / ".exo").mkdir()
    _asks(command)


def test_many_destination_options_stay_fast(tmp_path, monkeypatch):
    # Only the first and the last folder are paired with every source name; the others are checked alone.
    home = _home(tmp_path, monkeypatch)
    dests = " ".join(f"-t /var/d{i}" for i in range(500))
    sources = " ".join(f"s{i}" for i in range(500))
    start = time.monotonic()
    assert _bash(f"cp {dests} {sources}").category != "store_write"
    _asks(f"cp {dests} -t {home} {sources}")
    assert time.monotonic() - start < 5


def test_a_long_git_folder_chain_stays_fast(tmp_path, monkeypatch):
    # Each `-C` moves on from the one before. Past 64 of them the gate stops following and asks, so a line of many
    # relative folders does not resolve every word against an ever longer folder.
    _custom_home(tmp_path, monkeypatch)
    chain = " ".join(f"-C d{i}" for i in range(1000))
    start = time.monotonic()
    _asks(f"cd ~ && git {chain} checkout -- " + " ".join(f"f{i}" for i in range(1000)))
    _asks("git -C ~ " + "-C . " * 62 + "-C my-exo/plugins/foo pull")      # 64 folders are still followed
    assert _bash("git -C ~/project " + "-C . " * 63 + "pull").category != "store_write"
    assert time.monotonic() - start < 5
