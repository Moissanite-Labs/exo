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
    shutil.copytree(args[-2], args[-1], dirs_exist_ok=True, ignore=shutil.ignore_patterns(*skip))
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


def deploy(box, *args, npx_rc=0, rsync="ok"):
    """Run the script; rsync=None leaves rsync off PATH, "fail" makes it fail, "empty" copies nothing."""
    env = {k: v for k, v in os.environ.items() if k.startswith("GIT_")}
    path = [box / "npx-bin", *([box / "rsync-bin"] if rsync else []), box / "tools"]
    env.update(PATH=os.pathsep.join(map(str, path)), HOME=str(box / "home"), TMPDIR=str(box / "tmp"),
               VERCEL_TOKEN="x", CF_API_TOKEN="y", EXO_VERCEL_TEAM="team_x", EXO_VERCEL_SCOPE="scope",
               DEPLOY_LOG=str(box / "deployed.json"), NPX_RC=str(npx_rc), RSYNC_MODE=rsync or "")
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
