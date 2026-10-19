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


# --- the skill pack ------------------------------------------------------------------

def test_skill_pack_is_installed_where_the_core_and_orchestra_find_it(box, monkeypatch, caplog):
    """/intro loads exo-intro, and exo-orchestra reads MANIFEST.json, both from $EXO_HOME/skills."""
    before = snapshot(box)
    r = run(box, "--dry-run")
    exo = (box / "exo").resolve()
    assert r.returncode == 0 and f"skill pack    {exo}/skills" in r.stdout and f"{exo}/skills/exo-intro" in r.stdout
    assert snapshot(box) == before
    r = run(box, "--no-pip", "--no-interview")
    assert r.returncode == 0, r.stderr + r.stdout
    skills = box / "exo" / "skills"
    assert (skills / "MANIFEST.json").read_bytes() == (REPO / "skills" / "MANIFEST.json").read_bytes()
    assert (skills / ".exo-managed").is_file() and (skills / "exo-intro" / ".exo-managed").is_file()
    assert (skills / "sym-protocol" / "tests" / "run-tests.py").is_file()  # a skill's own tests are part of it
    assert "skill pack: 17 skills" in r.stdout
    from exo.core.skills import SkillIndex
    assert "# Exo orientation" in SkillIndex.discover([skills]).load("exo-intro")
    monkeypatch.setenv("EXO_HOME", str(box / "exo"))
    monkeypatch.delenv("EXO_PLUGIN_DIRS", raising=False)
    monkeypatch.delenv("EXO_SKILLS_DIR", raising=False)
    pm = PluginManager()
    pm.load_all(entry_points=False)
    assert "exo-orchestra:exo-intro" in pm.skills
    assert "no skill manifest" not in caplog.text


def checkout_copy(tmp_path: Path) -> Path:
    """A checkout whose skills/ a test can change: the real source and plugins, a copy of the pack."""
    src = tmp_path / "checkout"
    src.mkdir()
    shutil.copy(REPO / "pyproject.toml", src / "pyproject.toml")
    (src / "src").symlink_to(REPO / "src")
    (src / "plugins").symlink_to(REPO / "plugins")
    shutil.copytree(REPO / "skills", src / "skills", ignore=shutil.ignore_patterns("__pycache__"))
    return src


def test_rerun_refreshes_the_pack_and_keeps_your_skills(box):
    """A re-run (and `exo update`, which re-runs the installer) brings the pack up to date, folder by folder."""
    src = checkout_copy(box)
    args = ("--no-pip", "--no-interview", "--exo-source", str(src))
    assert run(box, *args).returncode == 0
    skills = box / "exo" / "skills"
    (skills / "mine").mkdir()
    (skills / "mine" / "SKILL.md").write_text("---\nname: mine\ndescription: my own\n---\nbody\n")
    with open(src / "skills" / "exo-intro" / "SKILL.md", "a") as f:
        f.write("\n## New fact\n")
    shutil.rmtree(src / "skills" / "jevgrep")
    manifest = (src / "skills" / "MANIFEST.json").read_text().replace('"version": 1', '"version": 2')
    (src / "skills" / "MANIFEST.json").write_text(manifest)
    r = run(box, *args)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (skills / "exo-intro" / "SKILL.md").read_text().endswith("## New fact\n")
    assert not (skills / "jevgrep").exists()                # a pack skill that was removed upstream goes
    assert (skills / "mine" / "SKILL.md").read_text().endswith("body\n")  # yours stays
    assert (skills / "MANIFEST.json").read_text() == manifest
    first = snapshot(skills)
    assert run(box, *args).returncode == 0
    assert snapshot(skills) == first                        # nothing changed upstream: nothing rewritten


def test_rerun_keeps_a_skill_you_copied_from_the_pack(box):
    """`cp -r` copies the hidden marker too. Exo replaces or removes only a folder it installed under that name."""
    src = checkout_copy(box)
    args = ("--no-pip", "--no-interview", "--yes", "--exo-source", str(src))  # `exo update` re-runs it like this
    assert run(box, *args).returncode == 0
    skills = box / "exo" / "skills"
    shutil.copytree(skills / "exo-intro", skills / "my-intro")  # a copy to change, marker and all
    (skills / "my-intro" / "SKILL.md").write_text("---\nname: my-intro\ndescription: mine\n---\nmy intro\n")
    shutil.copytree(skills / "council", skills / "my-council")  # a copy not changed yet
    (skills / "jevgrep").rename(skills / "my-grep")  # a pack skill you renamed
    kept = {name: snapshot(skills / name) for name in ("my-intro", "my-council", "my-grep")}
    r = run(box, *args)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "rm -rf" not in r.stdout
    assert {name: snapshot(skills / name) for name in kept} == kept
    assert (skills / "my-intro" / "SKILL.md").read_text().endswith("my intro\n")
    for name in kept:
        assert f"{skills / name} was not installed by Exo under this name" in r.stderr
    assert (skills / "jevgrep" / ".exo-managed").is_file()  # the pack's own skill comes back beside your copy
    shutil.copytree(src / "skills" / "jevgrep", src / "skills" / "my-intro", dirs_exist_ok=True)
    r = run(box, *args)  # the pack now ships a skill with your copy's name: yours still wins
    assert r.returncode == 0, r.stderr + r.stdout
    assert f"{skills / 'my-intro'} was not installed by Exo; left alone" in r.stderr
    assert snapshot(skills / "my-intro") == kept["my-intro"]
    assert (skills / "my-intro" / "SKILL.md").read_text().endswith("my intro\n")


def test_rerun_warns_when_it_replaces_an_edited_pack_skill(box):
    """abu-aska's review of #213: an edit inside a pack skill folder was replaced with no word. The folder is Exo's
    and is still replaced, as INSTALL step 5 says, but the marker records the SKILL.md Exo wrote, so a re-run says
    so first. An upstream change alone does not warn."""
    src = checkout_copy(box)
    args = ("--no-pip", "--no-interview", "--yes", "--exo-source", str(src))
    assert run(box, *args).returncode == 0
    skills = box / "exo" / "skills"
    with open(skills / "council" / "SKILL.md", "a") as f:
        f.write("\nmy edit\n")
    with open(src / "skills" / "exo-intro" / "SKILL.md", "a") as f:  # the pack changed, you did not
        f.write("\n## New fact\n")
    r = run(box, *args)
    assert r.returncode == 0, r.stderr + r.stdout
    assert r.stderr.count("was edited since Exo installed it") == 1
    assert f"{skills / 'council' / 'SKILL.md'} was edited since Exo installed it" in r.stderr
    assert "my edit" not in (skills / "council" / "SKILL.md").read_text()  # replaced, as documented
    assert (skills / "exo-intro" / "SKILL.md").read_text().endswith("## New fact\n")
    r = run(box, *args)
    assert r.returncode == 0 and "was edited" not in r.stderr


def test_a_changed_pack_skill_needs_a_new_folder_and_a_new_name(box):
    """INSTALL step 5: the skill index is first-wins by frontmatter name over sorted folders, so a copy that keeps
    the pack's name is hidden behind the pack's skill (or hides it). A copy with a new name shows beside it."""
    from exo.core.skills import SkillIndex
    src = checkout_copy(box)
    args = ("--no-pip", "--no-interview", "--yes", "--exo-source", str(src))
    assert run(box, *args).returncode == 0
    skills = box / "exo" / "skills"
    shutil.copytree(skills / "council", skills / "my-council")
    with open(skills / "my-council" / "SKILL.md", "a") as f:
        f.write("\nmy change\n")
    idx = SkillIndex.discover([skills])
    assert idx.skills["council"].path == skills / "council" / "SKILL.md" and "my-council" not in idx.skills
    text = (skills / "my-council" / "SKILL.md").read_text()
    (skills / "my-council" / "SKILL.md").write_text(text.replace("name: council", "name: my-council", 1))
    assert run(box, *args).returncode == 0
    idx = SkillIndex.discover([skills])
    assert idx.skills["council"].path == skills / "council" / "SKILL.md"
    assert "my change" in idx.load("my-council")


def test_skill_pack_leaves_folders_it_did_not_install(box):
    skills = box / "exo" / "skills"
    (skills / "exo-intro").mkdir(parents=True)
    (skills / "exo-intro" / "SKILL.md").write_text("---\nname: exo-intro\ndescription: mine\n---\nmy intro\n")
    (skills / "MANIFEST.json").write_text('{"skills": []}')
    r = run(box, "--no-pip", "--no-interview")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "exo-intro was not installed by Exo; left alone" in r.stderr
    assert "MANIFEST.json was not written by Exo; left alone" in r.stderr
    assert (skills / "exo-intro" / "SKILL.md").read_text().endswith("my intro\n")
    assert not (skills / "exo-intro" / ".exo-managed").exists()
    assert (skills / "MANIFEST.json").read_text() == '{"skills": []}'
    assert (skills / "clean-writing" / ".exo-managed").is_file()  # the rest of the pack still installs


def test_a_hand_copied_pack_is_adopted(box):
    """The earlier workaround, `cp -r skills $EXO_HOME/skills`, becomes a managed pack without warnings."""
    skills = box / "exo" / "skills"
    shutil.copytree(REPO / "skills", skills, ignore=shutil.ignore_patterns("__pycache__"))
    r = run(box, "--no-pip", "--no-interview")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "left alone" not in r.stderr
    assert (skills / ".exo-managed").is_file() and (skills / "exo-intro" / ".exo-managed").is_file()


def test_a_skills_folder_linked_to_the_checkout_is_left_alone(box):
    """`ln -s <checkout>/skills $EXO_HOME/skills` keeps the pack live. Adopting it would rm -rf the checkout's skills."""
    src = checkout_copy(box)
    before = snapshot(src / "skills")
    (box / "exo").mkdir()
    (box / "exo" / "skills").symlink_to(src / "skills")
    for _ in range(2):  # the second run is `exo update`
        r = run(box, "--no-pip", "--no-interview", "--exo-source", str(src))
        assert r.returncode == 0, r.stderr + r.stdout
        assert "so the pack is live there; left alone" in r.stdout and "rm -rf" not in r.stdout
        assert snapshot(src / "skills") == before  # nothing deleted, rewritten or marked in the checkout
    (box / "exo" / "skills").unlink()
    (box / "exo" / "skills").symlink_to(box / "home")  # a link to somewhere else of yours
    r = run(box, "--no-pip", "--no-interview", "--exo-source", str(src))
    assert r.returncode == 0 and "is a link Exo did not make; left alone" in r.stderr
    assert not (box / "home" / "exo-intro").exists()


def test_skills_and_manifest_linked_into_the_checkout_are_left_alone(box):
    """A link that resolves to its own source is never adopted or replaced; the rest of the pack still installs."""
    src = checkout_copy(box)
    before = snapshot(src / "skills")
    skills = box / "exo" / "skills"
    skills.mkdir(parents=True)
    (skills / "exo-intro").symlink_to(src / "skills" / "exo-intro")
    (skills / "MANIFEST.json").symlink_to(src / "skills" / "MANIFEST.json")
    (skills / "clean-writing").symlink_to(box / "home")  # a link elsewhere stands in for the pack's skill
    r = run(box, "--no-pip", "--no-interview", "--exo-source", str(src))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "skill exo-intro:" in r.stdout and "links to the pack's; left alone" in r.stdout
    assert "clean-writing was not installed by Exo; left alone" in r.stderr
    assert "MANIFEST.json was not written by Exo" not in r.stderr  # it is the pack's own, live
    assert all(p.is_symlink() for p in (skills / "exo-intro", skills / "MANIFEST.json", skills / "clean-writing"))
    assert snapshot(src / "skills") == before and not list((box / "home").glob(".exo-managed"))
    assert (skills / "council" / ".exo-managed").is_file()


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


# --- re-runs and `exo update` keep the plugins and Hermes home chosen at install -----------

ALL_PLUGINS = {p.parent.name for p in (REPO / "plugins").glob("*/plugin.yaml")}


def update_step(tmp_path):
    """Run the installer step that `exo update` runs for an installer-managed install."""
    from exo import update_cmd
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    install = {"mode": "venv", "checkout": str(REPO), "source": str(REPO), "python": sys.executable}
    step = update_cmd.plan(install)["steps"][2]
    assert "--no-pip" in step and "--plugins" not in step and "--hermes-home" not in step
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EXO_", "HERMES_"))}
    env.update(HOME=str(tmp_path / "home"), EXO_HOME=str(tmp_path / "exo"))
    return subprocess.run(step, env=env, capture_output=True, text=True, timeout=120, umask=0o022)


def installed(where: Path) -> set:
    return {p.name for p in where.iterdir()}


def config(tmp_path) -> dict:
    import json
    return json.loads((tmp_path / "exo" / "config.json").read_text())


def test_update_step_keeps_the_plugin_subset_chosen_at_install(box):
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate,exo-memory")
    assert r.returncode == 0, r.stderr + r.stdout
    assert config(box)["plugin_selection"] == ["exo-gate", "exo-memory"]
    r = update_step(box)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "keeping the --plugins selection" in r.stdout
    assert installed(box / "exo" / "plugins") == {"exo-gate", "exo-memory"}
    cfg = config(box)
    assert cfg["plugins"] == ["exo-gate", "exo-memory"] and cfg["plugin_selection"] == ["exo-gate", "exo-memory"]


def test_plugins_all_goes_back_to_every_plugin(box):
    assert run(box, "--no-pip", "--no-interview", "--yes").returncode == 0
    assert "plugin_selection" not in config(box)               # a default install records no list,
    assert update_step(box).returncode == 0
    assert installed(box / "exo" / "plugins") == ALL_PLUGINS   # so new upstream plugins still arrive
    assert run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate").returncode == 0
    assert config(box)["plugin_selection"] == ["exo-gate"]
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "all")
    assert r.returncode == 0, r.stderr + r.stdout
    assert config(box)["plugin_selection"] == "all" and set(config(box)["plugins"]) == ALL_PLUGINS
    r = update_step(box)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "keeping" not in r.stdout and set(config(box)["plugins"]) == ALL_PLUGINS


@pytest.mark.parametrize("mode", ["with-hermes", "hermes-home"])
def test_update_step_refreshes_the_hermes_copies(box, mode):
    custom = box / "custom-hermes"
    args = ["--with-hermes", "--no-pin"] if mode == "with-hermes" else ["--hermes-home", str(custom)]
    hh = box / "exo" / "hermes-home" if mode == "with-hermes" else custom
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate,exo-memory", *args)
    assert r.returncode == 0, r.stderr + r.stdout
    stale = hh / "plugins" / "exo-memory" / "plugin.yaml"
    stale.write_text(stale.read_text() + "# stale\n")
    kept = [p for p in (hh / "config.yaml", box / "exo" / "bin" / "exo-hermes") if p.exists()]
    before = {p: p.read_bytes() for p in kept}
    r = update_step(box)
    assert r.returncode == 0, r.stderr + r.stdout
    assert stale.read_text() == (REPO / "plugins" / "exo-memory" / "plugin.yaml").read_text()
    assert installed(hh / "plugins") == installed(box / "exo" / "plugins") == {"exo-gate", "exo-memory"}
    assert {p: p.read_bytes() for p in kept} == before         # the launcher and config.yaml stay as they were
    assert len(kept) == (2 if mode == "with-hermes" else 0)


def test_update_step_leaves_a_remembered_live_hermes_home_alone(box):
    live = box / "home" / ".hermes"
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate", "--hermes-home", str(live))
    assert r.returncode == 0, r.stderr + r.stdout
    stale = live / "plugins" / "exo-gate" / "plugin.yaml"
    stale.write_text(stale.read_text() + "# stale\n")
    before = snapshot(live)
    r = update_step(box)
    assert r.returncode == 0, r.stderr + r.stdout
    assert snapshot(live) == before                            # ~/.hermes changes only with --hermes-home
    assert "not refreshed" in r.stderr and "--hermes-home" in r.stderr
    assert installed(box / "exo" / "plugins") == {"exo-gate"}


def test_a_list_removes_the_plugins_an_earlier_run_installed(box):
    """An install widened to every plugin (by an update before plugin_selection existed) goes back to its list."""
    custom = box / "custom-hermes"
    assert run(box, "--no-pip", "--no-interview", "--yes", "--hermes-home", str(custom)).returncode == 0
    plugins = box / "exo" / "plugins"
    assert installed(plugins) == installed(custom / "plugins") == ALL_PLUGINS
    (plugins / "mine").mkdir()                                  # a folder of yours: no .exo-managed marker
    (plugins / "mine" / "notes.txt").write_text("mine\n")
    elsewhere = box / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".exo-managed").write_text("marked, but behind a link\n")
    (plugins / "linked").symlink_to(elsewhere, target_is_directory=True)
    before = snapshot(box)
    r = run(box, "--dry-run", "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate,exo-memory")
    assert r.returncode == 0, r.stderr + r.stdout
    assert f"rm -rf {plugins.resolve()}/exo-bus" in r.stdout and snapshot(box) == before
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate,exo-memory")
    assert r.returncode == 0, r.stderr + r.stdout
    assert installed(plugins) == {"exo-gate", "exo-memory", "mine", "linked"}
    assert {m.name for m in discover([plugins])} == {"exo-gate", "exo-memory"}    # what the core loads
    assert config(box)["plugins"] == config(box)["plugin_selection"] == ["exo-gate", "exo-memory"]
    assert installed(custom / "plugins") == ALL_PLUGINS         # a Hermes home: its config.yaml decides
    assert (elsewhere / ".exo-managed").exists() and (plugins / "mine" / "notes.txt").exists()
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate")
    assert r.returncode == 0, r.stderr + r.stdout
    assert installed(plugins) == {"exo-gate", "mine", "linked"}  # a narrower list leaves no stale copy behind
    assert update_step(box).returncode == 0
    assert installed(plugins) == {"exo-gate", "mine", "linked"}


@pytest.mark.parametrize("spaced", ["exo-gate, exo-memory", " exo-memory ,exo-gate "])
def test_spaces_in_a_plugin_list_do_not_drop_a_plugin(box, spaced):
    assert run(box, "--no-pip", "--no-interview", "--yes").returncode == 0
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", spaced)
    assert r.returncode == 0, r.stderr + r.stdout
    assert installed(box / "exo" / "plugins") == {"exo-gate", "exo-memory"}
    assert config(box)["plugin_selection"] == ["exo-gate", "exo-memory"]


def test_a_plugin_name_that_matches_nothing_stops_the_install(box):
    """A typo must not narrow the install: the list removes the other plugins and is remembered for updates."""
    assert run(box, "--no-pip", "--no-interview", "--yes").returncode == 0
    for args in (["--dry-run"], []):
        r = run(box, *args, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate,exo-memroy")
        assert r.returncode != 0 and "exo-memroy" in r.stderr and "exo-memory" in r.stderr  # names what exists
        assert "rm -rf" not in r.stdout
    assert installed(box / "exo" / "plugins") == ALL_PLUGINS
    assert "plugin_selection" not in config(box)


def test_a_remembered_name_this_checkout_lacks_warns_and_goes_on(box):
    import json
    assert run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-gate,exo-memory").returncode == 0
    path = box / "exo" / "config.json"
    path.write_text(json.dumps({**config(box), "plugin_selection": ["exo-gate", "exo-memory", "exo-gone"]}))
    r = run(box, "--no-pip", "--no-interview", "--yes")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "exo-gone" in r.stderr
    assert installed(box / "exo" / "plugins") == {"exo-gate", "exo-memory"}


def test_a_list_without_the_gate_keeps_the_gate(box):
    """Without exo-gate the core refuses every tool call, so a list (such as INSTALL.md's example) keeps it."""
    assert run(box, "--no-pip", "--no-interview", "--yes").returncode == 0
    r = run(box, "--no-pip", "--no-interview", "--yes", "--plugins", "exo-memory,exo-context")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "adding exo-gate" in r.stdout
    assert installed(box / "exo" / "plugins") == {"exo-context", "exo-gate", "exo-memory"}
    assert config(box)["plugin_selection"] == ["exo-context", "exo-gate", "exo-memory"]
