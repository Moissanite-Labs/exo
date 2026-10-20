"""scripts/deploy-site.sh fails closed: a failed copy or deploy exits non-zero and tags nothing.

Runs the script from a scratch repo with a local bare origin and stand-ins for npx and rsync on a PATH
built for the test, so nothing reaches the network and the machine's own rsync is never used.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
TOOLS = ("dirname", "git", "tr", "grep", "head", "cut", "mktemp", "sed", "date", "cat")

pytestmark = pytest.mark.skipif(not BASH or not all(shutil.which(t) for t in TOOLS),
                                reason="needs bash, git and coreutils")

NPX = """import json, os, pathlib, sys
here = pathlib.Path(".")
version = here / "version.json"
pathlib.Path(os.environ["DEPLOY_LOG"]).write_text(json.dumps({
    "files": sorted(p.as_posix() for p in here.rglob("*") if p.is_file()),
    "links": sorted(p.as_posix() for p in here.rglob("*") if p.is_symlink()),
    "contents": {p.as_posix(): p.read_text() for p in here.rglob("*") if p.is_file() and p.suffix in (".txt", ".html")},
    "argv": sys.argv[1:],
    "version": json.loads(version.read_text()) if version.is_file() else None}))
rc = int(os.environ.get("NPX_RC", "0"))
print("Production: https://site.invalid" if rc == 0 else "Error: Deployment failed (rate limited)")
sys.exit(rc)
"""
RSYNC = """import os, shutil, sys
mode = os.environ.get("RSYNC_MODE", "ok")
if mode == "fail":
    sys.exit(23)
args = sys.argv[1:]
skip = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--exclude"]
if mode == "ok":
    # like rsync -a: links stay links, and a source directory that is itself a link is followed
    shutil.copytree(args[-2], args[-1], dirs_exist_ok=True, symlinks=True, ignore=shutil.ignore_patterns(*skip))
"""
GIT = """import os, sys
args = sys.argv[1:]
fail = os.environ.get("GIT_FAIL", "ls-files")      # the one git subcommand that fails; all others are real
sub = [a for a in args if not a.startswith("-") and a != "-C"]
cmd = next((a for a in args if a in ("status", "ls-files")), None)
if cmd == fail and (cmd != "ls-files" or "--others" in args):
    print("fatal: simulated failure" if cmd == "status" else "fatal: simulated failure reading the index", file=sys.stderr)
    sys.exit(128)
os.execv(os.environ["REAL_GIT"], ["git", *args])
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A scratch repo with the real script, a one-page site/ and a local bare origin."""
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{k}_NAME", "Test")
        monkeypatch.setenv(f"{k}_EMAIL", "t@example.invalid")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "deploy-site.sh", repo / "scripts" / "deploy-site.sh")
    (repo / "site").mkdir()
    (repo / "site" / "index.html").write_text("<h1>exo</h1>\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "exo"\nversion = "0.0.0"\n')
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "site")
    git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(repo, "push", "-q", "origin", "HEAD")
    tools = tmp_path / "tools"            # what the script needs, and no rsync unless a test adds the stand-in
    tools.mkdir()
    for t in TOOLS:
        (tools / t).symlink_to(shutil.which(t))
    for name, body in (("npx", NPX), ("rsync", RSYNC)):
        (tmp_path / f"{name}.py").write_text(body)
        shim = tmp_path / f"{name}-bin" / name
        shim.parent.mkdir()
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path / f"{name}.py"}" "$@"\n')
        shim.chmod(0o755)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "home").mkdir()
    return tmp_path


def deploy(box, *args, npx_rc=0, rsync="ok", extra_env=None):
    """Run the script; rsync=None leaves rsync off PATH, "fail" makes it fail, "empty" copies nothing."""
    env = {k: v for k, v in os.environ.items() if k.startswith("GIT_")}
    path = [box / "npx-bin", *([box / "rsync-bin"] if rsync else []), box / "tools"]
    env.update(PATH=os.pathsep.join(map(str, path)), HOME=str(box / "home"), TMPDIR=str(box / "tmp"),
               VERCEL_TOKEN="x", CF_API_TOKEN="y", EXO_VERCEL_TEAM="team_x", EXO_VERCEL_SCOPE="scope",
               DEPLOY_LOG=str(box / "deployed.json"), NPX_RC=str(npx_rc), RSYNC_MODE=rsync or "", **(extra_env or {}))
    return subprocess.run([BASH, "scripts/deploy-site.sh", "--redeploy", "--yes", *args], cwd=box / "repo",
                          env=env, capture_output=True, text=True, timeout=60)


def tags(box, version):
    return (git(box / "origin.git", "tag", "-l", version).split(), git(box / "repo", "tag", "-l", version).split())


def deployed(box):
    log = box / "deployed.json"
    return json.loads(log.read_text()) if log.exists() else None


def test_failed_deploy_exits_nonzero_and_leaves_the_version_free(site):
    r = deploy(site, "--version", "site-v9.9-rc.1", npx_rc=1)
    assert r.returncode != 0
    assert "vercel deploy failed" in r.stderr and "site-v9.9-rc.1 is not tagged" in r.stderr
    assert tags(site, "site-v9.9-rc.1") == ([], [])
    r = deploy(site, "--version", "site-v9.9-rc.1")            # the retry can use the same version
    assert r.returncode == 0, r.stderr + r.stdout
    assert tags(site, "site-v9.9-rc.1") == (["site-v9.9-rc.1"], ["site-v9.9-rc.1"])
    sent = deployed(site)
    assert {"index.html", "version.json"} <= set(sent["files"])
    head = git(site / "repo", "rev-parse", "HEAD")[:7]
    assert sent["version"]["site"] == "site-v9.9-rc.1" and sent["version"]["commit"] == head


def test_missing_rsync_stops_before_deploying(site):
    r = deploy(site, rsync=None)
    assert r.returncode != 0 and "rsync is required" in r.stderr
    assert deployed(site) is None


@pytest.mark.parametrize("mode", ["fail", "empty"])
def test_failed_or_empty_copy_stops_before_deploying(site, mode):
    r = deploy(site, "--version", "site-v9.9-rc.1", rsync=mode)
    assert r.returncode != 0 and "nothing deployed" in r.stderr
    assert deployed(site) is None and tags(site, "site-v9.9-rc.1") == ([], [])


def test_versioned_deploy_needs_a_clean_site(site):
    (site / "repo" / "site" / "index.html").write_text("<h1>edited</h1>\n")
    r = deploy(site, "--version", "site-v9.9-rc.2")
    assert r.returncode != 0 and "uncommitted changes" in r.stderr
    assert deployed(site) is None and tags(site, "site-v9.9-rc.2") == ([], [])
    r = deploy(site)                                             # an unversioned preview still deploys,
    assert r.returncode == 0, r.stderr + r.stdout
    assert deployed(site)["version"]["commit"].endswith("-dirty")  # marked as not matching its commit


def test_tag_push_failure_exits_nonzero(site):
    git(site / "repo", "remote", "set-url", "origin", str(site / "missing.git"))
    r = deploy(site, "--version", "site-v9.9-rc.3")
    assert r.returncode != 0 and "pushing the tag failed" in r.stderr
    assert deployed(site) is not None


@pytest.mark.parametrize("args", [(), ("--version", "site-v9.9-rc.4")])
def test_untracked_files_in_site_refuse_every_deploy(site, args):
    pitch = site / "repo" / "site" / "pitch"
    pitch.mkdir()
    (pitch / "index.html").write_text("<h1>private</h1>\n")
    r = deploy(site, *args)
    assert r.returncode != 0 and "untracked files" in r.stderr and "site/pitch/index.html" in r.stderr
    assert deployed(site) is None and tags(site, "site-v9.9-rc.4") == ([], [])
    shutil.rmtree(pitch)                                          # once it is gone the deploy goes through
    r = deploy(site, *args)
    assert r.returncode == 0, r.stderr + r.stdout


def test_ignored_untracked_file_also_refuses_but_finder_litter_does_not(site):
    (site / "repo" / ".gitignore").write_text("site/secret.txt\n")
    git(site / "repo", "add", ".gitignore")
    git(site / "repo", "commit", "-qm", "ignore")
    (site / "repo" / "site" / ".DS_Store").write_text("x")
    (site / "repo" / "site" / "secret.txt").write_text("x")
    r = deploy(site)
    assert r.returncode != 0 and "site/secret.txt" in r.stderr and ".DS_Store" not in r.stderr
    (site / "repo" / "site" / "secret.txt").unlink()
    r = deploy(site)
    assert r.returncode == 0, r.stderr + r.stdout
    assert ".DS_Store" not in deployed(site)["files"]


def _install_git_shim(box):
    (box / "git.py").write_text(GIT)
    shim = box / "tools" / "git"
    shim.unlink()
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{box / "git.py"}" "$@"\n')
    shim.chmod(0o755)


def test_a_failed_untracked_listing_stops_the_deploy(site):
    """git fails only the untracked-file listing (exit 128, no output); every other git call is real."""
    _install_git_shim(site)
    r = deploy(site, extra_env={"REAL_GIT": shutil.which("git")})
    assert r.returncode == 1
    assert "could not list untracked files" in r.stderr and "git exit 128" in r.stderr
    assert "nothing deployed" in r.stderr
    assert deployed(site) is None


def test_vercel_cli_is_pinned(site):
    r = deploy(site)
    assert r.returncode == 0, r.stderr + r.stdout
    pins = [a for a in deployed(site)["argv"] if a.startswith("vercel@")]
    assert len(pins) == 1 and pins[0] != "vercel@latest"
    assert pins[0].removeprefix("vercel@").replace(".", "").isdigit()


@pytest.mark.parametrize("bad", ["latest", "64", "^64.1.0", "64.1.0 --foo"])
def test_an_unpinned_cli_override_is_refused(site, bad):
    r = deploy(site, extra_env={"EXO_VERCEL_CLI": bad})
    assert r.returncode == 2 and "exact X.Y.Z" in r.stderr
    assert deployed(site) is None
    r = deploy(site, extra_env={"EXO_VERCEL_CLI": "64.2.0"})
    assert r.returncode == 0 and "vercel@64.2.0" in deployed(site)["argv"]


def _add_submodule(box):
    """A tracked gitlink under site/ whose working tree holds an ignored private file (the #350 repro)."""
    sub = box / "repo" / "site" / "modules" / "private"
    sub.mkdir(parents=True)
    git(sub, "init", "-q")
    (sub / ".gitignore").write_text("secret.txt\n")
    (sub / "page.html").write_text("<p>public</p>\n")
    git(sub, "add", "-A")
    git(sub, "commit", "-qm", "sub")
    (sub / "secret.txt").write_text("private\n")
    git(box / "repo", "add", "site/modules/private")
    git(box / "repo", "commit", "-qm", "add submodule")
    assert "160000" in git(box / "repo", "ls-files", "--stage", "site/modules")
    return sub


@pytest.mark.parametrize("args", [(), ("--version", "site-v9.9-rc.5")])
def test_a_submodule_under_site_refuses_every_deploy(site, args):
    _add_submodule(site)
    r = deploy(site, *args)
    assert r.returncode != 0 and "submodule" in r.stderr and "site/modules/private" in r.stderr
    assert deployed(site) is None and tags(site, "site-v9.9-rc.5") == ([], [])


def test_a_corrupt_index_stops_the_deploy(site):
    (site / "repo" / "site" / "pitch").mkdir()
    (site / "repo" / "site" / "pitch" / "private.txt").write_text("private\n")
    (site / "repo" / ".git" / "index").write_bytes(b"not an index")
    r = deploy(site)
    assert r.returncode != 0 and "could not" in r.stderr and "git exit 128" in r.stderr and "nothing deployed" in r.stderr
    assert deployed(site) is None


def test_an_empty_cli_override_is_refused(site):
    r = deploy(site, extra_env={"EXO_VERCEL_CLI": ""})
    assert r.returncode == 2 and "exact X.Y.Z" in r.stderr
    assert deployed(site) is None


def test_an_empty_cli_override_in_the_conf_file_is_refused(site):
    conf = site / "deploy.conf"
    conf.write_text("EXO_VERCEL_CLI=\n")
    r = deploy(site, extra_env={"EXO_DEPLOY_CONF": str(conf)})
    assert r.returncode == 2 and "exact X.Y.Z" in r.stderr
    assert deployed(site) is None


def test_a_failed_git_status_stops_a_versioned_deploy(site):
    """#358: git fails only `status` (exit 128); a modified tracked file must not ship under a tag at HEAD."""
    (site / "repo" / "site" / "index.html").write_text("DIRTY VERSION MUST NOT BE TAGGED\n")
    _install_git_shim(site)
    r = deploy(site, "--version", "site-v9.9-rc.77", extra_env={"REAL_GIT": shutil.which("git"), "GIT_FAIL": "status"})
    assert r.returncode == 1
    assert "could not read the status" in r.stderr and "git exit 128" in r.stderr and "nothing deployed" in r.stderr
    assert deployed(site) is None and tags(site, "site-v9.9-rc.77") == ([], [])


def _outside_private_dir(box):
    out = box / "outside"
    out.mkdir()
    (out / "index.html").write_text("<h1>outside</h1>\n")
    (out / "private.txt").write_text("outside private\n")
    return out


def test_a_tracked_symlink_as_the_site_root_refuses_the_deploy(site):
    """#359: site is a tracked symlink to an outside directory; git lists nothing untracked, rsync would copy it all."""
    repo = site / "repo"
    out = _outside_private_dir(site)
    git(repo, "rm", "-rq", "site")
    shutil.rmtree(repo / "site", ignore_errors=True)
    (repo / "site").symlink_to(out)
    git(repo, "add", "site")
    git(repo, "commit", "-qm", "site is a link")
    assert git(repo, "ls-files", "--stage", "site").startswith("120000")
    assert git(repo, "ls-files", "--others", "--", "site") == ""      # the inventory sees nothing, as in the report
    for args in ((), ("--version", "site-v9.9-rc.78")):
        r = deploy(site, *args)
        assert r.returncode != 0 and "symlink" in r.stderr and "nothing deployed" in r.stderr
        assert deployed(site) is None and tags(site, "site-v9.9-rc.78") == ([], [])


def test_an_untracked_symlink_as_the_site_root_refuses_the_deploy(site):
    repo = site / "repo"
    out = _outside_private_dir(site)
    shutil.rmtree(repo / "site")
    (repo / "site").symlink_to(out)
    r = deploy(site)
    assert r.returncode != 0 and "symlink" in r.stderr
    assert deployed(site) is None


def test_a_site_root_tracked_as_a_symlink_but_checked_out_as_a_directory_refuses(site):
    """The index says mode 120000 for site while the working tree holds a real directory: still refused."""
    repo = site / "repo"
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="outside", text=True,
                          capture_output=True, check=True).stdout.strip()
    git(repo, "rm", "-rq", "--cached", "site")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},site")
    r = deploy(site)
    assert r.returncode != 0 and "nothing deployed" in r.stderr
    assert deployed(site) is None


def test_a_symlink_inside_site_is_kept_as_a_link_and_its_target_is_not_copied(site):
    """The existing behaviour: rsync -a keeps a link inside site/ and copies nothing from its target."""
    repo = site / "repo"
    out = _outside_private_dir(site)
    (repo / "site" / "link").symlink_to(out)
    git(repo, "add", "site/link")
    git(repo, "commit", "-qm", "link inside site")
    r = deploy(site)
    assert r.returncode == 0, r.stderr + r.stdout
    sent = deployed(site)
    assert "link" in sent["links"]
    assert not any(f.startswith("link/") or f.endswith("private.txt") for f in sent["files"])
    assert "outside private" not in json.dumps(sent["contents"])


def test_an_empty_cli_override_beats_the_conf_file(site):
    """The environment wins even when it is set and empty: deploy.conf must not fill it in with 64.2.0."""
    conf = site / "deploy.conf"
    conf.write_text("EXO_VERCEL_CLI=64.2.0\n")
    r = deploy(site, extra_env={"EXO_DEPLOY_CONF": str(conf), "EXO_VERCEL_CLI": ""})
    assert r.returncode == 2 and "exact X.Y.Z" in r.stderr
    assert deployed(site) is None
    r = deploy(site, extra_env={"EXO_DEPLOY_CONF": str(conf), "EXO_VERCEL_CLI": "64.3.0"})   # a set value wins too
    assert r.returncode == 0 and "vercel@64.3.0" in deployed(site)["argv"]
    r = deploy(site, extra_env={"EXO_DEPLOY_CONF": str(conf)})                              # unset: the conf applies
    assert r.returncode == 0 and "vercel@64.2.0" in deployed(site)["argv"]
