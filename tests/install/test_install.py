"""scripts/install.sh. Runs under bash with a fake HOME. Never pins Hermes or touches the network."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
                          capture_output=True, text=True, timeout=120)


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
    assert f"plugin home   {exo}/hermes-home" in out
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
    assert "plugin home   " + str((box / "exo").resolve()) + "/hermes-home" in r.stdout


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
    hh = box / "exo" / "hermes-home" / "plugins" / "exo-memory"
    hh.mkdir(parents=True)
    (hh / "mine.py").write_text("x")
    r = run(box, "--no-pin", "--no-pip", "--no-interview")
    assert r.returncode != 0 and "not installed by Exo" in r.stderr
    assert (hh / "mine.py").read_text() == "x"


def test_default_install_leaves_hermes_alone(box):
    r = run(box, "--no-pip", "--no-interview")
    assert r.returncode == 0, r.stderr
    exo = box / "exo"
    assert (exo / "hermes-home" / "plugins" / "exo-memory" / ".exo-managed").is_file()
    assert not (exo / "hermes").exists() and not (exo / "bin" / "exo-hermes").exists()
    assert not (exo / "hermes-home" / "config.yaml").exists()
    assert "hermes_dir" not in (exo / "config.json").read_text()
