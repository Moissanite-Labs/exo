"""Issue #269: `..` from run/jobs, or `run` from EXO_HOME, reaches the run folder's secrets and asks.

The run folder holds the principal credential (D-039, #169). A relative name read from a folder in EXO_HOME is
checked as the path it names, so `cd ~/.exo/run/jobs && cat ../exod*` asks as `cat ~/.exo/run/exod*` does, while
job output in run/jobs stays readable. Every command string here is data for exo.gate.classify/decide; none is run."""
import os

import pytest

from exo import gate


@pytest.fixture(params=["default", "custom"])
def layout(request, tmp_path, monkeypatch):
    """(EXO_HOME as a command spells it, its parent as an absolute path)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    if request.param == "custom":
        monkeypatch.setenv("EXO_HOME", str(tmp_path / "srv" / "exo-home"))
        return "$EXO_HOME", str(tmp_path / "srv"), "exo-home"
    monkeypatch.delenv("EXO_HOME", raising=False)
    return "~/.exo", str(home), ".exo"


def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


ASK = [
    # the issue's rows: read or shell, and allowed, on main 4ac092b
    "cd {h}/run/jobs && cat ../exod.principal.k*",
    "cd {h}/run/jobs && cat ../exod*",
    "cd {h}/run/jobs && cat ../agent-default*",
    "cd {h}/run/jobs && grep -r . ..",
    "tar -C {h} -czf - run",
    # the same folder by other relative spellings
    "cd {h}/run/jobs && ls ..",
    "cd {h}/run/jobs && du -a ..",
    "cd {h}/run/jobs && ls ${{PWD%/*}}",
    "cd {h}/run/jobs && cd .. && ls",
    "cd {h}/run/jobs/a && cd .. && ls ..",
    "cd {h} && cd run/jobs && grep -r . ..",
    "cd {h} && ls run",
    "cd {h} && cd run && ls",
    "cd {h} && find run",
    "cd {h}/principals/me/run/jobs && grep -r . ..",
    "cd {h}/principals/me/run/jobs && cat ../exod*",
    "cd {parent} && tar czf - {name}/run",
    "cd {parent} && cd {name}/run/jobs && grep -r . ..",
]


@pytest.mark.parametrize("cmd", ASK)
def test_a_relative_name_into_the_run_folder_asks(cmd, layout):
    h, parent, name = layout
    line = cmd.format(h=h, parent=parent, name=name)
    c = gate.classify(_sh(line))
    assert (c.category, gate.decide(c).outcome) == ("credentials", "ask"), line


@pytest.mark.parametrize("cmd", [
    "cd .exo/run/jobs && grep -r . ..",             # relative all the way: `.exo/run` by its name
    "cd ~ && cd .exo/run/jobs && cat ../exod*",
])
def test_a_relative_exo_folder_counts_by_its_name(cmd, layout):
    assert gate.classify(_sh(cmd)).category == "credentials"


def test_a_link_into_run_jobs_is_resolved(layout, tmp_path):
    h, _, _ = layout
    run = os.path.expanduser(os.path.expandvars(h.replace("$EXO_HOME", os.environ.get("EXO_HOME", "")))) + "/run"
    os.makedirs(run + "/jobs")
    os.symlink(run + "/jobs", tmp_path / "jobs-link")
    for line in (f"cd {tmp_path}/jobs-link && grep -r . ..", f"cd {tmp_path}/jobs-link && cat ../exod*"):
        assert gate.classify(_sh(line)).category == "credentials", line


@pytest.mark.parametrize("cmd, category", [
    ("cd {h}/run/jobs && cat 1.log", "read"),       # job output stays readable
    ("cd {h}/run/jobs && tail -f 3.log", "read"),
    ("cd {h}/run/jobs && ls", "read"),
    ("cd {h}/run/jobs && cat *.log", "read"),
    ("cd {h}/run/jobs && cat ./1.log ../jobs/2.log", "read"),
    ("cd {h}/run/jobs && cat ../exod.sock", "read"),
    ("cd {h} && cat policies/gates.yaml", "read"),
    ("cd {h} && ls", "read"),
    ("cd ~/project && ls ..", "read"),              # outside the Exo store nothing changes
    ("cd ~/project && cat run", "read"),
    ("cd ~/project/run/jobs && grep -r . ..", "read"),
    ("cd /srv/app/run/jobs && cat ../exod.sock", "read"),
])
def test_job_output_and_other_folders_keep_their_class(cmd, category, layout):
    h, _, _ = layout
    assert gate.classify(_sh(cmd.format(h=h))).category == category


def test_the_seats_folder_is_scanned_once_per_call(tmp_path, monkeypatch):
    """_run_secrets() read up to 4,096 seats/ entries for every glob word; one classify call reads them once."""
    home = tmp_path / "home"
    seats = home / ".exo" / "seats"
    seats.mkdir(parents=True)
    for i in range(50):
        (seats / f"s{i}.toml").write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXO_HOME", raising=False)
    calls = []
    real = os.scandir

    def scandir(path="."):
        calls.append(os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    line = "cd ~/.exo/run/jobs && ls " + " ".join(f"f{i}*" for i in range(200))
    assert gate.classify(_sh(line)).category in ("read", "credentials")
    assert sum(p.endswith("seats") for p in calls) == 1, len(calls)
    assert any(s.startswith("agent-s7.") for s in gate._run_secrets())      # outside a call: read afresh
