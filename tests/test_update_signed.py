"""`update.require_signed`: the update target must carry an SSH signature from a pinned trusted key."""
import shutil
import subprocess
from pathlib import Path

import pytest

from exo import update_cmd, update_verify

GIT = shutil.which("git")
KEYGEN = shutil.which("ssh-keygen")
pytestmark = pytest.mark.skipif(not (GIT and KEYGEN), reason="git or ssh-keygen missing")


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def keygen(path: Path) -> str:
    r = sh(path.parent, KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(path), "-C", "t@example.test")
    assert r.returncode == 0, r.stderr
    return f"t@example.test {path.with_suffix('.pub').read_text().strip()}\n"


def commit(repo: Path, msg: str, key: Path | None, files: dict | None = None):
    for name, text in (files or {}).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, GIT, "add", "-A")
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false"]
    if key:
        cfg += ["-c", "gpg.format=ssh", "-c", f"user.signingkey={key}"]
    r = sh(repo, GIT, *cfg, "commit", "-q", *(["-S"] if key else []), "-m", msg)
    if key and r.returncode:
        pytest.skip("this git cannot sign with ssh keys: " + r.stderr.strip())
    assert r.returncode == 0, r.stderr


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exohome"))
    good, other = tmp_path / "good", tmp_path / "other"
    good_line, other_line = keygen(good), keygen(other)
    origin, local = tmp_path / "origin", tmp_path / "local"
    origin.mkdir()
    sh(origin, GIT, "init", "-q", "-b", "main")
    commit(origin, "base", good, {"a.txt": "1", update_verify.TRUST_PATH: good_line})
    assert sh(tmp_path, GIT, "clone", "-q", str(origin), str(local)).returncode == 0
    return dict(origin=origin, local=local, good=good, other=other, good_line=good_line, other_line=other_line)


def pin(w):
    assert update_verify.pin_trust(w["local"]) == 0


def test_flag_off_by_default(world):
    assert update_verify.required() is False


def test_flag_on_via_config(world):
    from exo.config import Config
    c = Config.load(); c.set("update.require_signed", True); c.save()
    assert update_verify.required() is True


def test_signed_target_accepted(world):
    pin(world)
    commit(world["origin"], "next", world["good"], {"a.txt": "2"})
    sha = update_verify.verify_target(world["local"])
    assert sha == sh(world["origin"], GIT, "rev-parse", "HEAD").stdout.strip()


def test_unsigned_target_refused(world):
    pin(world)
    commit(world["origin"], "next", None, {"a.txt": "2"})
    with pytest.raises(ValueError, match="not signed|signature"):
        update_verify.verify_target(world["local"])


def test_wrong_key_refused(world):
    pin(world)
    commit(world["origin"], "next", world["other"], {"a.txt": "2"})
    with pytest.raises(ValueError, match="not signed|signature"):
        update_verify.verify_target(world["local"])


def test_tampered_trust_list_not_honoured(world):
    """A new commit that adds its own key to the repo's trust list does not make itself trusted."""
    pin(world)
    commit(world["origin"], "evil", world["other"],
           {update_verify.TRUST_PATH: world["good_line"] + world["other_line"]})
    with pytest.raises(ValueError, match="not signed|signature"):
        update_verify.verify_target(world["local"])


def test_no_pin_refuses_with_instructions(world):
    commit(world["origin"], "next", world["good"], {"a.txt": "2"})
    with pytest.raises(ValueError, match="--pin-trust"):
        update_verify.verify_target(world["local"])


def test_pin_trust_from_revision(world):
    """Rotation: review the new list in the fetched revision, then pin it explicitly."""
    pin(world)
    commit(world["origin"], "rotate", world["good"], {update_verify.TRUST_PATH: world["other_line"]})
    commit(world["origin"], "later", world["other"], {"a.txt": "3"})
    sh(world["local"], GIT, "fetch", "-q")
    with pytest.raises(ValueError):
        update_verify.verify_target(world["local"])
    assert update_verify.pin_trust(world["local"], "@{u}") == 0
    update_verify.verify_target(world["local"])


def _plan(world, monkeypatch):
    steps = [["git-pull-marker"], ["install-marker"]]
    monkeypatch.setattr(update_cmd, "plan", lambda: {"mode": "checkout", "checkout": str(world["local"]),
                                                     "python": "py", "steps": steps})
    monkeypatch.setattr(update_cmd, "_preflight", lambda p, read: None)
    monkeypatch.setattr(update_cmd, "_remote_version", lambda *a, **k: "v")
    monkeypatch.setattr(update_cmd, "self_check", lambda *a, **k: [])
    return steps


def enable():
    from exo.config import Config
    c = Config.load(); c.set("update.require_signed", True); c.save()


def test_cmd_update_flag_off_does_not_check(world, monkeypatch):
    _plan(world, monkeypatch)
    commit(world["origin"], "next", None, {"a.txt": "2"})  # unsigned, no pin: still fine
    ran = []
    rc = update_cmd.cmd_update([], run=lambda s: ran.append(s) or subprocess.CompletedProcess(s, 0),
                               read=lambda s: subprocess.CompletedProcess(s, 0, "", ""))
    assert [["git-pull-marker"], ["install-marker"]] == [s for s in ran if s[0].endswith("marker")] and rc == 0


def test_cmd_update_on_refuses_unsigned_and_runs_nothing(world, monkeypatch, capsys):
    _plan(world, monkeypatch)
    enable(); pin(world)
    commit(world["origin"], "next", None, {"a.txt": "2"})
    ran = []
    rc = update_cmd.cmd_update([], run=lambda s: ran.append(s) or subprocess.CompletedProcess(s, 0))
    assert rc == 1 and ran == []
    assert "signature" in capsys.readouterr().err


def test_cmd_update_on_signed_pins_target_sha(world, monkeypatch):
    _plan(world, monkeypatch)
    enable(); pin(world)
    commit(world["origin"], "next", world["good"], {"a.txt": "2"})
    sha = sh(world["origin"], GIT, "rev-parse", "HEAD").stdout.strip()
    ran = []
    rc = update_cmd.cmd_update([], run=lambda s: ran.append(s) or subprocess.CompletedProcess(s, 0))
    assert rc == 0 and ["install-marker"] in ran
    merge = [s for s in ran if "merge" in s]
    assert len(merge) == 1 and sha in merge[0] and ["git-pull-marker"] not in ran  # the pull step now merges exactly the verified commit
