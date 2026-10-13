"""scripts/install.sh. Runs under bash with a fake HOME. Never pins Hermes or touches the network."""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from exo.core.plugins import PluginManager, default_dirs, discover

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "install.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(not BASH, reason="bash not available")


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*")}


def run(tmp_path, *args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EXO_", "HERMES_"))}
    env.update(HOME=str(tmp_path / "home"), EXO_HOME=str(tmp_path / "exo"), **(env_extra or {}))
    return subprocess.run([BASH, str(SCRIPT), "--python", sys.executable, *args], env=env,
                          capture_output=True, text=True, timeout=120, umask=0o022)


@pytest.fixture
def box(tmp_path):
    (tmp_path / "home" / ".hermes").mkdir(parents=True)     # a live Hermes home that must stay untouched
    (tmp_path / "home" / ".hermes" / "config.yaml").write_text("live: true\n")
    return tmp_path


def test_dry_run_prints_plan_and_writes_nothing(box):
    before = snapshot(box)
    r = run(box, "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "plan (dry run" in out
    exo = str((box / "exo").resolve())
    assert f"core plugins  {exo}/plugins" in out and f"Hermes home   {exo}/hermes-home" in out
    assert "pin-hermes.sh" not in out and "-m venv" in out and "pip install" in out
    assert "exo-hermes" not in out and "exo models" in out   # Hermes is opt-in
    assert "plugins/exo-memory" in out and "config.json: plugins" in out
    assert "/.hermes" not in out.replace(f"{exo}/hermes-home", "")
    assert snapshot(box) == before                           # nothing written anywhere


def test_dry_run_with_hermes_pins_and_writes_launcher(box):
    r = run(box, "--dry-run", "--with-hermes")
    assert r.returncode == 0, r.stderr
    assert "pin-hermes.sh" in r.stdout and "exo-hermes" in r.stdout and "Hermes + exo" in r.stdout


def test_hermes_only_options_need_the_flag(box):
    r = run(box, "--dry-run", "--tag", "v1")
    assert r.returncode != 0 and "--with-hermes" in r.stderr


def test_refuses_live_hermes_home_unless_explicit(box):
    live = str(box / "home" / ".hermes")
    r = run(box, "--dry-run", env_extra={"EXO_HERMES_HOME": live})
    assert r.returncode != 0 and "refusing" in r.stderr
    r = run(box, "--dry-run", "--hermes-home", live)
    assert r.returncode == 0 and "(explicit)" in r.stdout


def test_ignores_hermes_home_env(box):
    r = run(box, "--dry-run", env_extra={"HERMES_HOME": str(box / "home" / ".hermes")})
    assert r.returncode == 0
    assert "Hermes home   " + str((box / "exo").resolve()) + "/hermes-home" in r.stdout


def test_real_run_without_pin_or_pip_is_idempotent(box):
    answers = box / "answers.yaml"
    answers.write_text("answers:\n  identity.name: Sam\n  work.projects: A website.\n")
    live_before = snapshot(box / "home")
    r = run(box, "--with-hermes", "--no-pin", "--no-pip", "--principal", "sam", "--answers", str(answers))
    assert r.returncode == 0, r.stderr + r.stdout
    exo = box / "exo"
    hh = exo / "hermes-home"
    assert (hh / "plugins" / "exo-memory" / ".exo-managed").is_file()
    assert not (hh / "plugins" / "exo-memory" / "tests").exists()
    assert "exo-memory" in (hh / "config.yaml").read_text()
    assert (exo / "bin" / "exo-hermes").is_file() and (exo / "env.sh").is_file()
    assert (exo / "principals" / "sam" / "nodes" / "person" / "identity.md").is_file()
    cfg = (exo / "config.json").read_text()
    assert '"principal": "sam"' in cfg and str(hh) in cfg
    assert snapshot(box / "home") == live_before             # ~/.hermes untouched

    first = snapshot(exo)
    r = run(box, "--with-hermes", "--no-pin", "--no-pip", "--principal", "sam", "--answers", str(answers))
    assert r.returncode == 0, r.stderr
    assert "up to date" in r.stdout and "skipping init" in r.stdout
    second = snapshot(exo)
    changed = {k for k in second if first.get(k) != second[k]}
    assert changed <= {"config.json"}, changed               # only config.json is rewritten


def test_refuses_unmanaged_plugin_dir(box):
    hh = box / "exo" / "plugins" / "exo-memory"
    hh.mkdir(parents=True)
    (hh / "mine.py").write_text("x")
    r = run(box, "--no-pin", "--no-pip", "--no-interview")
    assert r.returncode != 0 and "not installed by Exo" in r.stderr
    assert (hh / "mine.py").read_text() == "x"


def test_default_install_leaves_hermes_alone(box):
    r = run(box, "--no-pip", "--no-interview")
    assert r.returncode == 0, r.stderr
    exo = box / "exo"
    assert (exo / "plugins" / "exo-memory" / ".exo-managed").is_file()
    assert not (exo / "hermes").exists() and not (exo / "bin" / "exo-hermes").exists()
    assert not (exo / "hermes-home" / "config.yaml").exists()
    assert "hermes_dir" not in (exo / "config.json").read_text()


@pytest.mark.parametrize("target", ["standalone", "hermes", "custom", "custom-env"])
def test_installed_plugins_are_discovered_and_loaded_by_standalone_core(box, monkeypatch, target):
    args = ["--no-pip", "--no-interview", "--yes"]
    custom = box / "custom-hermes"
    env_extra = None
    if target == "hermes":
        args += ["--with-hermes", "--no-pin"]
    elif target == "custom":
        args += ["--with-hermes", "--no-pin", "--hermes-home", str(custom)]
    elif target == "custom-env":
        env_extra = {"EXO_HERMES_HOME": str(custom)}
    result = run(box, *args, env_extra=env_extra)
    assert result.returncode == 0, result.stderr + result.stdout
    assert stat.S_IMODE((box / "exo").stat().st_mode) == 0o700
    monkeypatch.setenv("EXO_HOME", str(box / "exo"))
    monkeypatch.delenv("EXO_PLUGIN_DIRS", raising=False)
    names = {"exo-bus", "exo-command", "exo-context", "exo-gate", "exo-memory", "exo-orchestra"}
    assert {m.name for m in discover(default_dirs())} == names
    pm = PluginManager()
    records = pm.load_all(entry_points=False)
    assert {r.manifest.name for r in records} == names
    assert all(r.ok for r in records), pm.errors()
    assert "exo-command" in pm.commands
    assert any(e.plugin == "exo-gate" for e in pm.hooks.entries("pre_tool_call"))
    assert pm.slots.memory.name == "exo-memory" and pm.slots.context.name == "exo-context"
    if target != "standalone":
        hermes_home = box / "exo" / "hermes-home" if target == "hermes" else custom
        assert {p.name for p in (hermes_home / "plugins").iterdir()} == names


def test_selected_plugins_install_to_standalone_and_explicit_hermes_home(box, monkeypatch):
    target = box / "custom-hermes"
    result = run(box, "--no-pip", "--no-interview", "--yes", "--hermes-home", str(target),
                 "--plugins", "exo-command,exo-gate")
    assert result.returncode == 0, result.stderr + result.stdout
    monkeypatch.setenv("EXO_HOME", str(box / "exo"))
    monkeypatch.delenv("EXO_PLUGIN_DIRS", raising=False)
    assert {m.name for m in discover(default_dirs())} == {"exo-command", "exo-gate"}
    assert {p.name for p in (target / "plugins").iterdir()} == {"exo-command", "exo-gate"}


# --- the `exo` command on PATH -----------------------------------------------------

def run_sh(tmp_path, *args, shell="/bin/zsh"):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EXO_", "HERMES_"))}
    env.update(HOME=str(tmp_path / "home"), EXO_HOME=str(tmp_path / "exo"), SHELL=shell,
               PATH="/usr/bin:/bin:" + str(Path(sys.executable).parent))
    return subprocess.run([BASH, str(SCRIPT), "--python", sys.executable, *args], env=env,
                          capture_output=True, text=True, timeout=600)


def test_dry_run_plans_the_exo_command_and_path_line(box):
    before = snapshot(box)
    r = run_sh(box, "--dry-run", "--no-interview")
    assert r.returncode == 0, r.stderr
    bin_dir = (box / "home" / ".local" / "bin")
    assert f"ln -sfn {box.resolve() / 'exo' / 'venv' / 'bin' / 'exo'}" in r.stdout.replace("\\", "")
    assert "is not on your PATH" in r.stdout and 'export PATH=' in r.stdout and ".zshrc" in r.stdout
    assert "[tui]" in r.stdout.replace("\\", "")
    assert snapshot(box) == before and not bin_dir.exists()


def test_dry_run_modify_path_prints_but_writes_nothing(box):
    before = snapshot(box)
    r = run_sh(box, "--dry-run", "--no-interview", "--modify-path")
    assert r.returncode == 0 and "append to" in r.stdout
    assert snapshot(box) == before


def _pypi_reachable() -> bool:
    import socket
    try:
        socket.create_connection(("pypi.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")
@pytest.mark.skipif(sys.version_info < (3, 11), reason="needs Python 3.11")
def test_real_install_puts_exo_on_path_and_rerun_upgrades(box):
    if not _pypi_reachable():
        pytest.skip("no network for pip")
    rc = box / "home" / ".zshrc"
    rc.write_text("# mine\n")
    r = run_sh(box, "--no-interview", "-y", "--exo-source", str(REPO))
    assert r.returncode == 0, r.stderr + r.stdout
    link = box / "home" / ".local" / "bin" / "exo"
    assert link.is_symlink() and link.resolve() == (box / "exo" / "venv" / "bin" / "exo").resolve()
    assert rc.read_text() == "# mine\n"                       # rc files are not edited without --modify-path
    assert "export PATH=" in r.stdout
    v = subprocess.run([str(link), "--version"], capture_output=True, text=True,
                       env={**os.environ, "EXO_HOME": str(box / "exo")})
    assert v.returncode == 0 and v.stdout.startswith("exo 0.")
    import json
    cfg = json.loads((box / "exo" / "config.json").read_text())
    assert cfg["install"]["mode"] == "venv" and cfg["install"]["venv"].endswith("/venv")
    # a second run, now with --modify-path, adds the line once; a third changes nothing
    assert run_sh(box, "--no-interview", "-y", "--exo-source", str(REPO), "--modify-path").returncode == 0
    assert rc.read_text().count("export PATH=") == 1
    assert run_sh(box, "--no-interview", "-y", "--exo-source", str(REPO), "--modify-path").returncode == 0
    assert rc.read_text().count("export PATH=") == 1
    assert link.is_symlink()
    # a foreign file at the link path is never replaced
    link.unlink()
    link.write_text("mine")
    r = run_sh(box, "--no-interview", "-y", "--exo-source", str(REPO))
    assert r.returncode == 0 and link.read_text() == "mine" and "left alone" in r.stderr


@pytest.mark.parametrize("custom_venv", [False, True])
@pytest.mark.parametrize("with_hermes", [False, True], ids=["standalone", "with-hermes"])
def test_no_pip_refresh_preserves_existing_install_metadata_and_default_paths(box, custom_venv, with_hermes):
    import json
    exo = box / "exo"
    exo.mkdir()
    venv = box / "custom-venv" if custom_venv else exo / "venv"
    install = {"mode": "venv", "checkout": str(REPO), "python": "/recorded/python",
               "venv": str(venv), "source": str(REPO)}
    (exo / "config.json").write_text(json.dumps({"install": install}))
    mode_args = ["--with-hermes", "--no-pin"] if with_hermes else []
    result = run(box, "--no-pip", "--no-interview", "--yes", "--exo-source", str(REPO), *mode_args,
                 env_extra={"EXO_VENV": str(venv)} if custom_venv else None)
    assert result.returncode == 0, result.stderr + result.stdout
    config = json.loads((exo / "config.json").read_text())
    assert config["install"] == install
    assert config["venv"] == str(venv)
    assert "exo-memory" in config["plugins"]
    assert (exo / "plugins" / "exo-memory" / ".exo-managed").is_file()
    hermes_plugins = exo / "hermes-home" / "plugins"
    if with_hermes:
        assert (hermes_plugins / "exo-memory" / ".exo-managed").is_file()
        assert config["hermes_home"] == str(exo / "hermes-home")
        assert (exo / "bin" / "exo-hermes").is_file()
    else:
        assert not hermes_plugins.exists()
        assert "hermes_home" not in config
        assert not (exo / "bin" / "exo-hermes").exists()
    envsh = (exo / "env.sh").read_text()
    assert f'export EXO_HOME="{exo}"' in envsh
    assert "PYTHONPATH" not in envsh and "EXO_PYTHON" not in envsh and "EXO_VENV" not in envsh
