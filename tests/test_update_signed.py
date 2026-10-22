"""`update.require_signed`: the update target must carry an SSH signature from a pinned trusted key."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from exo import update_cmd, update_verify

GIT = shutil.which("git")
KEYGEN = shutil.which("ssh-keygen")
GPG = shutil.which("gpg")
TRUST = update_verify.TRUST_PATH


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def keygen(path: Path) -> str:
    r = sh(path.parent, KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(path), "-C", "t@example.com")
    assert r.returncode == 0, r.stderr
    return f"t@example.com {path.with_suffix('.pub').read_text().strip()}\n"


def _cfg(key: Path | None) -> list[str]:
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    if key:  # sign with this key file, whatever signing program the user's git config names
        cfg += ["-c", "gpg.format=ssh", "-c", f"gpg.ssh.program={KEYGEN}", "-c", f"user.signingkey={key}"]
    return cfg


def commit(repo: Path, msg: str, key: Path | None, files: dict | None = None):
    for name, text in (files or {}).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, GIT, "add", "-A")
    r = sh(repo, GIT, *_cfg(key), "commit", "-q", *(["-S"] if key else []), "-m", msg)
    if key and r.returncode:
        pytest.skip("this git cannot sign with ssh keys: " + r.stderr.strip())
    assert r.returncode == 0, r.stderr


def merge(repo: Path, branch: str, key: Path | None):
    r = sh(repo, GIT, *_cfg(key), "merge", "-q", "--no-ff", *(["-S"] if key else []), "-m", f"merge {branch}", branch)
    assert r.returncode == 0, r.stderr


@pytest.fixture
def world(tmp_path, monkeypatch):
    if not (GIT and KEYGEN):
        pytest.skip("git or ssh-keygen missing")
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


def test_flag_off_by_default():
    assert update_verify.required() is False


def test_flag_on_via_config():
    from exo.config import Config
    c = Config.load(); c.set("update.require_signed", True); c.save()
    assert update_verify.required() is True


def test_signed_target_accepted(world):
    pin(world)
    commit(world["origin"], "next", world["good"], {"a.txt": "2"})
    sha = update_verify.verify_target(world["local"])
    assert sha == sh(world["origin"], GIT, "rev-parse", "HEAD").stdout.strip()


def test_signed_target_with_a_latin1_message_accepted(world):
    """Only the commit's header is read for the signature check; its message need not be UTF-8 (#221 review)."""
    pin(world)
    (world["origin"] / "a.txt").write_text("2")
    msg = world["origin"].parent / "msg.txt"
    msg.write_bytes("caf\xe9\n".encode("latin-1"))
    sh(world["origin"], GIT, "add", "-A")
    r = sh(world["origin"], GIT, *_cfg(world["good"]), "-c", "i18n.commitEncoding=ISO-8859-1", "commit", "-q", "-S",
           "-F", str(msg))
    assert r.returncode == 0, r.stderr
    assert update_verify.verify_target(world["local"]) == sh(world["origin"], GIT, "rev-parse", "HEAD").stdout.strip()


def test_pin_trust_rotation_through_a_commit_with_a_latin1_message(world):
    """The rotation check reads the commit that set the list the same way: a pinned key's signature counts, whatever
    the message's encoding (#221 delta review)."""
    pin(world)
    o, both = world["origin"], world["good_line"] + world["other_line"]
    (o / TRUST).write_text(both)
    msg = o.parent / "msg.txt"
    msg.write_bytes("rotation caf\xe9\n".encode("latin-1"))
    sh(o, GIT, "add", "-A")
    r = sh(o, GIT, *_cfg(world["good"]), "-c", "i18n.commitEncoding=ISO-8859-1", "commit", "-q", "-S", "-F", str(msg))
    assert r.returncode == 0, r.stderr
    sh(world["local"], GIT, "fetch", "-q")
    assert update_verify.pin_trust(world["local"], "@{u}") == 0
    assert update_verify.pin_file().read_text() == both


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


def test_pin_trust_judges_the_merge_that_brought_the_list(world):
    """Rotation on a side branch: the commit on main that set the list is the merge. Unsigned, it is refused;
    signed by a pinned key, the list is taken."""
    pin(world)
    o = world["origin"]
    sh(o, GIT, "checkout", "-q", "-b", "side")
    commit(o, "rotate", world["good"], {TRUST: world["other_line"]})
    sh(o, GIT, "checkout", "-q", "main")
    commit(o, "main moves", world["good"], {"b.txt": "1"})
    merge(o, "side", None)
    sh(world["local"], GIT, "fetch", "-q")
    assert update_verify.pin_trust(world["local"], "@{u}") == 1
    sh(o, GIT, "reset", "-q", "--hard", "HEAD^")
    merge(o, "side", world["good"])
    commit(o, "later", world["other"], {"a.txt": "3"})
    sh(world["local"], GIT, "fetch", "-q")
    assert update_verify.pin_trust(world["local"], "@{u}") == 0
    update_verify.verify_target(world["local"])


@pytest.mark.parametrize("signer", [None, "other"])
def test_pin_trust_refuses_a_list_not_committed_under_the_pin(world, signer, capsys):
    """Someone who can push rewrites the list, unsigned or signed by their own key: the pin does not move."""
    pin(world)
    before = update_verify.pin_file().read_text()
    commit(world["origin"], "rotate", world[signer] if signer else None, {TRUST: world["other_line"]})
    commit(world["origin"], "payload", world["other"], {"a.txt": "3"})
    sh(world["local"], GIT, "fetch", "-q")
    assert update_verify.pin_trust(world["local"], "@{u}") == 1
    assert update_verify.pin_file().read_text() == before
    assert "nothing pinned" in capsys.readouterr().err
    with pytest.raises(ValueError, match="not signed"):
        update_verify.verify_target(world["local"])
    assert update_verify.pin_trust(world["local"]) == 0  # the list already pinned can be pinned again


@pytest.mark.parametrize("own_signers", [False, True])
def test_pin_trust_rotation_with_log_show_signature(world, monkeypatch, own_signers):
    """`log.showSignature = true` in the user's git config prints a signature check ("No signature", or "Good ...
    signature" with an allowed-signers file of their own) before each sha. The rotation still finds the commit
    that set the list: an unsigned one is refused, one the pin signed is taken."""
    pin(world)
    o, both = world["origin"], world["good_line"] + world["other_line"]
    cfg = {"log.showSignature": "true"}
    if own_signers:
        (world["local"].parent / "own_signers").write_text(both)
        cfg["gpg.ssh.allowedSignersFile"] = str(world["local"].parent / "own_signers")
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(cfg)))
    for i, (k, v) in enumerate(cfg.items()):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", k)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", v)
    commit(o, "rotate unsigned", None, {TRUST: both})
    sh(world["local"], GIT, "fetch", "-q")
    assert update_verify.pin_trust(world["local"], "@{u}") == 1
    sh(o, GIT, "reset", "-q", "--hard", "HEAD^")
    commit(o, "rotate", world["good"], {TRUST: both})
    sh(world["local"], GIT, "fetch", "-q")
    assert update_verify.pin_trust(world["local"], "@{u}") == 0
    assert update_verify.pin_file().read_text() == both


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


# A fake git with no subprocess: it calls every signature good, as a local gpg keyring does for a key it holds.
TIP, SETTER = "a" * 40, "b" * 40


class FakeGit:
    def __init__(self, armor="SSH SIGNATURE", message="msg\n", verify_rc=0, trust="t@example.com ssh-ed25519 BBBB\n"):
        self.armor, self.message, self.verify_rc, self.trust = armor, message, verify_rc, trust
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        out = ""
        if "rev-parse" in cmd:
            out = TIP + "\n"
        elif "log" in cmd:
            out = SETTER + "\n"
        elif "show" in cmd:
            out = self.trust
        elif "cat-file" in cmd:
            sig = f"gpgsig -----BEGIN {self.armor}-----\n x\n -----END {self.armor}-----\n" if self.armor else ""
            out = f"tree {'c' * 40}\ncommitter t <t@example.com> 0 +0000\n{sig}\n{self.message}"
        return subprocess.CompletedProcess(cmd, self.verify_rc if "verify-commit" in cmd else 0, out, "")

    def verified(self):
        return [c[-1] for c in self.calls if "verify-commit" in c]


def write_pin(line="t@example.com ssh-ed25519 AAAA\n") -> Path:
    p = update_verify.pin_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(line)
    return p


@pytest.mark.parametrize("armor", ["PGP SIGNATURE", "PGP MESSAGE", "SIGNED MESSAGE", None])
def test_only_ssh_signatures_count_whatever_the_keyring_says(tmp_path, armor):
    write_pin()
    git = FakeGit(armor)
    with pytest.raises(ValueError, match="not signed by a pinned trusted key"):
        update_verify.verify_target(tmp_path, read=git)
    assert git.verified() == []


def test_ssh_header_in_the_message_is_not_a_signature(tmp_path):
    write_pin()
    git = FakeGit("PGP SIGNATURE", message="msg\n\ngpgsig -----BEGIN SSH SIGNATURE-----\n")
    with pytest.raises(ValueError, match="not signed by a pinned trusted key"):
        update_verify.verify_target(tmp_path, read=git)


def test_verify_commit_runs_with_gpg_and_gpgsm_switched_off(tmp_path):
    pin = write_pin()
    git = FakeGit()
    assert update_verify.verify_target(tmp_path, read=git) == TIP and git.verified() == [TIP]
    argv = next(c for c in git.calls if "verify-commit" in c)
    assert {"gpg.openpgp.program=false", "gpg.x509.program=false", f"gpg.ssh.allowedSignersFile={pin}"} <= set(argv)


def test_pin_trust_rotation_verifies_the_commit_that_set_the_list(tmp_path, capsys):
    pin = write_pin()
    git = FakeGit(verify_rc=1)
    assert update_verify.pin_trust(tmp_path, "@{u}", read=git) == 1
    assert pin.read_text() == "t@example.com ssh-ed25519 AAAA\n" and git.verified() == [SETTER]
    assert "nothing pinned" in capsys.readouterr().err
    git = FakeGit(verify_rc=0)
    assert update_verify.pin_trust(tmp_path, "@{u}", read=git) == 0
    assert pin.read_text() == git.trust and git.verified() == [SETTER]


@pytest.mark.parametrize("pinned", [False, True])
def test_pin_trust_refuses_a_list_that_is_not_utf8(tmp_path, capsys, pinned):
    """git's output keeps bytes that are not UTF-8 as surrogates. Such a list is refused before anything is written,
    so no temporary file is left behind and the pin stays as it was."""
    before = write_pin() if pinned else None
    git = FakeGit(trust="t@example.com ssh-ed25519 B\udce9\n")
    assert update_verify.pin_trust(tmp_path, "@{u}", read=git) == 1
    assert "not UTF-8" in capsys.readouterr().err
    p = update_verify.pin_file()
    assert (p.read_text() if pinned else p.exists()) == ("t@example.com ssh-ed25519 AAAA\n" if before else False)
    assert not [f.name for f in p.parent.iterdir() if f.name.endswith(".tmp")] if p.parent.exists() else True


def test_pin_trust_refuses_an_option_as_revision(tmp_path):
    git = FakeGit()
    assert update_verify.pin_trust(tmp_path, "--output=x", read=git) == 1 and git.calls == []


BROKEN = ['{"version": 1, "update": {"require_signed": true,}}', '{"version": 99, "update": {"require_signed": true}}',
          '[]', '{"version": "1.0", "update": {"require_signed": true}}']


def write_config(text: str):
    home = Path(os.environ["EXO_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(text)


@pytest.mark.parametrize("text", BROKEN)
def test_unreadable_config_refuses_instead_of_turning_the_check_off(text):
    write_config(text)
    with pytest.raises(ValueError, match="cannot read config.json"):
        update_verify.required()


@pytest.mark.parametrize("value", ['"true"', '"yes"', "1", "null"])
def test_require_signed_must_be_true_or_false(value):
    write_config('{"version": 1, "update": {"require_signed": %s}}' % value)
    with pytest.raises(ValueError, match="true or false"):
        update_verify.required()


@pytest.mark.parametrize("text", BROKEN + ['{"version": 1, "update": {"require_signed": "yes"}}'])
def test_cmd_update_with_unreadable_config_runs_nothing(tmp_path, monkeypatch, capsys, text):
    _plan({"local": tmp_path}, monkeypatch)
    write_config(text)
    ran = []
    rc = update_cmd.cmd_update([], run=lambda s: ran.append(s) or subprocess.CompletedProcess(s, 0),
                               read=lambda s: subprocess.CompletedProcess(s, 0, "", ""))
    assert rc == 1 and ran == []
    assert "update.require_signed" in capsys.readouterr().err


@pytest.mark.parametrize("text, rc, says", [
    (None, 0, "signed check: off"),
    ('{"version": 1, "update": {"require_signed": true}}', 0, "signed check: on"),
    (BROKEN[0], 1, "cannot read config.json"),
])
def test_dry_run_says_whether_the_signed_check_is_on(tmp_path, monkeypatch, capsys, text, rc, says):
    _plan({"local": tmp_path}, monkeypatch)
    if text:
        write_config(text)
    assert update_cmd.cmd_update(["--dry-run"]) == rc
    out = capsys.readouterr()
    assert says in out.out + out.err


@pytest.fixture
def gnupg():
    """The signer's keyring and the user's, which holds only the signer's public key, imported and never trusted.
    Short folders under /tmp: gpg-agent socket paths have a length limit."""
    if not (GIT and GPG):
        pytest.skip("git or gpg missing")
    signer, user = (Path(tempfile.mkdtemp(prefix="g", dir="/tmp")) for _ in range(2))

    def gpg(home, *args):
        return subprocess.run([GPG, "--batch", "--quiet", *args], capture_output=True, text=True,
                              env={**os.environ, "GNUPGHOME": str(home)})
    try:
        r = gpg(signer, "--passphrase", "", "--quick-gen-key", "signer <signer@example.com>", "ed25519", "sign",
                "never")
        fpr = next((ln.split(":")[9] for ln in gpg(signer, "--with-colons", "--list-secret-keys").stdout.splitlines()
                    if ln.startswith("fpr:")), "")
        if r.returncode or not fpr:
            pytest.skip("gpg cannot make a key here: " + r.stderr.strip()[:200])
        exported = str(signer / "signer.asc")
        gpg(signer, "--armor", "--output", exported, "--export", fpr)
        gpg(user, "--import", exported)
        yield signer, fpr, user
    finally:
        for home in (signer, user):
            subprocess.run(["gpgconf", "--kill", "gpg-agent"], capture_output=True,
                           env={**os.environ, "GNUPGHOME": str(home)})
            shutil.rmtree(home, ignore_errors=True)


def test_openpgp_signature_from_the_local_keyring_refused(tmp_path, monkeypatch, gnupg):
    """git verifies an OpenPGP signature with gpg and the user's keyring, never reading the pin."""
    signer, fpr, user = gnupg
    origin, local = tmp_path / "origin", tmp_path / "local"
    origin.mkdir()
    sh(origin, GIT, "init", "-q", "-b", "main")
    commit(origin, "base", None, {"a.txt": "1"})
    assert sh(tmp_path, GIT, "clone", "-q", str(origin), str(local)).returncode == 0
    (origin / "a.txt").write_text("2")
    sh(origin, GIT, "add", "-A")
    r = subprocess.run([GIT, *_cfg(None), "-c", "gpg.format=openpgp", "-c", f"gpg.program={GPG}",
                        "-c", f"user.signingkey={fpr}", "commit", "-q", "-S", "-m", "openpgp"],
                       cwd=origin, capture_output=True, text=True, env={**os.environ, "GNUPGHOME": str(signer)})
    assert r.returncode == 0, r.stderr
    monkeypatch.setenv("GNUPGHOME", str(user))
    sh(local, GIT, "fetch", "-q")
    if sh(local, GIT, "-c", f"gpg.program={GPG}", "verify-commit", "@{u}").returncode:
        pytest.skip("this git and gpg do not call the imported key's signature good")
    write_pin()
    with pytest.raises(ValueError, match="not signed by a pinned trusted key"):
        update_verify.verify_target(local)
