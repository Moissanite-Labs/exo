"""grep and glob leave secret stores below their root out of the result (#241).

The gate checks only the path a search names; the tools walk everything below it. A search of the home folder
(or of EXO_HOME, or of the cwd with no path) must not return ~/.aws/credentials, Exo's keys or a project .env,
and must say that it left something out. A store the caller names is still read: the gate asks for that path.
Every secret here is fake and lives in a temporary HOME.
"""
import os
from pathlib import Path

import pytest

from exo import gate
from exo.core.tools import ToolContext, default_registry
from exo.core.tools.builtin import codefind, search
from exo.core.tools.registry import ToolError
from exo.core.types import ToolCall

REPO = Path(__file__).resolve().parents[2]
SECRETS = {  # path below HOME (or EXO_HOME for "exo:") -> fake content
    ".aws/credentials": "aws_secret_access_key = FAKEAWS0secret",
    ".ssh/id_ed25519": "FAKESSH0key",
    ".config/gh/hosts.yml": "oauth_token: FAKEGH0token",
    "project/.env": "API_KEY=FAKEENV0key",
    "exo:keys/trigger-abc.secret": "whsec_FAKEHOOK0secret",
    "exo:keys/exo-gate.key": "FAKEGATE0key",
    "exo:run/exod.principal.key": "FAKEPRINCIPAL0key",
}
NAMES = ("credentials", "id_ed25519", "hosts.yml", ".env", "trigger-abc", "exo-gate.key", "principal.key",
         "notes.txt", "FAKE")
SKIPPED = "secret-store paths; read one by name to be asked]"


@pytest.fixture(params=["exo-home-in-home", "exo-home-elsewhere"])
def home(request, tmp_path, monkeypatch):
    """A fake HOME with stores, a link to ~/.ssh named notes.txt, and ordinary files that match `key`."""
    home = tmp_path / "home"
    exo = home / ".exo" if request.param == "exo-home-in-home" else tmp_path / "exo-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(exo))
    monkeypatch.delenv("EXO_KEYS_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    for rel, text in SECRETS.items():
        p = exo / rel[4:] if rel.startswith("exo:") else home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    (home / "notes.txt").symlink_to(home / ".ssh" / "id_ed25519")
    (home / "project" / "app.py").write_text("key = 'ordinary'\n")
    (exo / "config.toml").write_text("key_name = 'ordinary'\n")
    return home, exo


def run(name, cwd, **args):
    return default_registry().call_sync(ToolCall(id="c1", name=name, arguments=args), ToolContext(cwd=str(cwd)))


def clean(text):
    leaked = [n for n in NAMES if n in text] + [s for s in SECRETS.values() if s in text]
    assert not leaked, text
    assert SKIPPED in text.splitlines()[-1], text


@pytest.mark.parametrize("tool,args", [
    ("grep", {"pattern": "FAKE|key|secret|token"}),
    ("grep", {"pattern": ".", "files_only": True}),
    ("glob", {"pattern": "**/*"}),
    ("glob", {"pattern": "*"}),
])
def test_search_of_home_leaves_stores_out(home, tool, args):
    h, exo = home
    for cwd, extra in ((h, {"path": str(h)}), (h, {}), (h.parent, {"path": "home"}), (h, {"path": "~"})):
        res = run(tool, cwd, **args, **extra)
        assert not res.is_error
        clean(res.text())
        assert "project/app.py" in res.text()


@pytest.mark.parametrize("tool,args", [("grep", {"pattern": "."}), ("glob", {"pattern": "**/*"})])
def test_search_of_exo_home_leaves_keys_out(home, tool, args):
    _, exo = home
    for cwd, extra in ((exo, {"path": str(exo)}), (exo, {})):
        text = run(tool, cwd, **args, **extra).text()
        clean(text)
        assert "config.toml" in text


def test_count_names_what_was_left_out(home):
    h, _ = home
    text = run("glob", h / "project", pattern="*").text()
    assert text.splitlines() == ["app.py", "[skipped 1 " + SKIPPED]
    assert run("glob", h / "project", pattern="*.py", path=".").text() == "app.py\n[skipped 1 " + SKIPPED
    assert run("grep", h / "project", pattern="nothing-matches").text() == "No matches.\n[skipped 1 " + SKIPPED
    assert run("glob", h / "project", pattern="*.rs").text() == "No files matched.\n[skipped 1 " + SKIPPED


def test_ordinary_tree_has_no_note(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("key = 1\n")
    assert run("grep", tmp_path, pattern="key").text() == "src/a.py:1:key = 1"
    assert run("glob", tmp_path, pattern="**/*.py").text() == "src/a.py"


def test_link_into_a_store_is_left_out_whatever_its_name(home):
    h, _ = home
    (h / "project" / "readme.md").symlink_to(h / ".aws" / "credentials")
    (h / "project" / "docs").symlink_to(h / ".ssh")           # a folder link is not followed at all
    text = run("grep", h / "project", pattern=".").text()
    clean(text)
    assert "readme.md" not in text and "app.py:1:key = 'ordinary'" in text


def test_cwd_inside_a_store_with_no_path_reads_nothing(home):
    h, _ = home
    text = run("grep", h / ".aws", pattern=".").text()
    assert text == "No matches.\n[skipped 1 " + SKIPPED


@pytest.mark.parametrize("path", ["~/.aws/credentials", "~/.aws", ".aws", "{HOME}/.aws"])
def test_a_store_named_by_the_caller_is_read_after_the_gate_asks(home, path):
    h, _ = home
    path = path.format(HOME=h)
    policies = gate.load_policies(REPO / "templates" / "principal-store")
    c = gate.classify({"tool": "grep", "args": {"pattern": "aws", "path": path}})
    assert c.category == "credentials" and gate.decide(c, policies).outcome == "ask"
    res = run("grep", h, pattern="aws_secret", path=path)
    assert "FAKEAWS0secret" in res.text() and SKIPPED not in res.text()
    assert "credentials" in run("glob", h, pattern="*", path=path).text()


def test_a_named_store_does_not_follow_a_link_out_into_another(home):
    h, _ = home
    (h / ".aws" / "backup").symlink_to(h / ".ssh" / "id_ed25519")
    text = run("grep", h, pattern="FAKE", path=str(h / ".aws")).text()
    assert "FAKEAWS0secret" in text and "FAKESSH0key" not in text and text.endswith("[skipped 1 " + SKIPPED)


@pytest.mark.parametrize("tool,args", [("grep", {"pattern": "."}), ("glob", {"pattern": "**/*"})])
@pytest.mark.parametrize("path", ["~/.ssh/..", ".ssh/..", "~/.ssh/../..", "{HOME}/.aws/..", "~/backup.key"])
def test_a_store_name_that_is_not_the_folder_walked_reads_no_store(home, tool, args, path):
    """`~/.ssh/..` and a link `backup.key` to HOME look like a store to the gate, which asks; the folder walked is
    HOME (or above it), so the stores in it stay out. Every path below `backup.key/` still looks like a store,
    so that search reads nothing at all."""
    h, _ = home
    (h / "backup.key").symlink_to(h)
    c = gate.classify({"tool": tool, "args": {**args, "path": path.format(HOME=h)}})
    assert c.category == "credentials"
    text = run(tool, h, **args, path=path.format(HOME=h)).text()
    clean(text)
    assert ("project/app.py" in text) == (path != "~/backup.key")


def test_through_the_gate_with_template_policies(home):
    """The #241 probe: the gate allows a search of HOME as a read, and the result holds no store."""
    h, exo = home
    policies = gate.load_policies(REPO / "templates" / "principal-store")
    for tool, args in (("grep", {"pattern": "secret|key", "path": str(h)}), ("grep", {"pattern": ".", "path": str(exo)}),
                       ("glob", {"pattern": "**/*", "path": str(h)})):
        c = gate.classify({"tool": tool, "args": args})
        assert c.category == "read" and gate.decide(c, policies).outcome in ("allow", "log")
        clean(run(tool, h, **args).text())


@pytest.mark.parametrize("path", ["~", "{HOME}", "{EXO}", "{HOME}/.config", "/", "{HOME}/.ssh"])
def test_code_find_refuses_a_folder_that_holds_stores(home, path):
    h, exo = home
    with pytest.raises(ToolError, match="holds secret stores"):
        codefind.code_find({"question": "where are the keys?", "path": path.format(HOME=h, EXO=exo)},
                           ToolContext(cwd=str(h)))


def test_holds_secret_store_is_false_for_a_project(home):
    h, _ = home
    assert not gate.holds_secret_store(str(h / "project" / "src"))
    assert gate.holds_secret_store(str(h / "project" / ".env"))


@pytest.fixture
def custom_stores(tmp_path, monkeypatch):
    """EXO_HOME, EXO_KEYS_FILE and CLAUDE_CONFIG_DIR in plain folders, so only their configured paths mark them."""
    exo, kf, cc = tmp_path / "exodata", tmp_path / "kf" / "keyring.cfg", tmp_path / "claudecfg"
    for k, v in (("HOME", tmp_path / "home"), ("EXO_HOME", exo), ("EXO_KEYS_FILE", kf), ("CLAUDE_CONFIG_DIR", cc)):
        monkeypatch.setenv(k, str(v))
    stores = [exo / "keys", exo / "keys" / "trigger-abc.secret", kf, cc, cc / "settings.json"]
    for p in (stores[1], kf, stores[4]):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("FAKEDYN0secret\n")
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "app.py").write_text("key = 'ordinary'\n")
    assert not any(gate._SENSITIVE_PATH.search(str(p)) for p in stores)   # only the dynamic roots can see them
    return stores, tmp_path / "project" / "app.py"


@pytest.mark.parametrize("root", ["link", "/proc/self/root", "/"])
def test_a_root_that_resolves_to_slash_still_skips_configured_stores(custom_stores, tmp_path, root):
    """A root whose real path is `/` (a link to it, /proc/self/root) must not build `//`-prefixed real paths that
    the configured stores never match. The walk itself is not run here: it would cover the whole file system."""
    stores, ordinary = custom_stores
    if root == "link":
        root = str(tmp_path / "rootlink")
        os.symlink("/", root)
    if not os.path.isdir(root):
        pytest.skip(f"{root} does not exist here")
    skip = gate.StoreSkip(root, "")
    for p in stores:
        assert skip(root.rstrip("/") + str(p)), p
    assert not skip(root.rstrip("/") + str(ordinary))


@pytest.mark.parametrize("tool,args", [("grep", {"pattern": "FAKE"}), ("glob", {"pattern": "**/*"})])
@pytest.mark.parametrize("path", ["{LINK}", "/", "/proc/self/root", None])
def test_a_search_of_the_whole_file_system_is_refused(custom_stores, tmp_path, monkeypatch, tool, args, path):
    """A walk of `/` takes minutes and enters /proc; it is refused before it starts, whatever spells it."""
    def walk(*a, **k):
        raise AssertionError("the search walked the file system")

    monkeypatch.setattr(search.os, "walk", walk)   # never walk the real `/` from a test
    os.symlink("/", tmp_path / "rootlink")
    if path == "/proc/self/root" and not os.path.isdir(path):
        pytest.skip("no /proc here")
    extra = {} if path is None else {"path": path.format(LINK=tmp_path / "rootlink")}
    res = run(tool, "/", **args, **extra)
    assert res.is_error and "whole file system" in res.text() and "FAKE" not in res.text(), res.text()


@pytest.fixture
def claude_dirs(tmp_path, monkeypatch):
    """A repo with its own .claude folder (settings, commands, a worktree checkout) in a HOME that is itself a git
    checkout (dotfiles), with Claude's config folder at ~/.claude."""
    home, repo = tmp_path / "home", tmp_path / "home" / "repo"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    monkeypatch.delenv("EXO_KEYS_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    wt = repo / ".claude" / "worktrees" / "feat"
    files = {home / ".claude" / ".credentials.json": "FAKECLAUDE0cred", home / ".claude" / "projects" / "s.jsonl":
             "FAKECLAUDE0chat", repo / ".claude" / "settings.local.json": "FAKELOCAL0key",
             repo / ".claude" / "commands" / "deploy.md": "ordinary deploy", wt / "src" / "main.py": "ordinary = 1",
             wt / ".env": "FAKEENV0key", wt / ".claude" / "settings.local.json": "FAKEWT0key", wt / ".git": "gitdir: x"}
    for p, text in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    (home / ".git").mkdir()
    (repo / ".git").mkdir()
    return home, repo, wt


def test_a_project_claude_folder_is_searched_but_its_settings_are_not(claude_dirs):
    """A repo's own .claude (commands, a worktree checkout) holds no Claude login, so a search inside or above it
    finds its files; the settings files directly in it and ~/.claude stay out. read_file there still asks."""
    home, repo, wt = claude_dirs
    for cwd, extra in ((wt, {}), (wt, {"path": "."}), (repo / ".claude" / "worktrees", {})):
        text = run("grep", cwd, pattern="ordinary|FAKE", **extra).text()
        assert "src/main.py:1:ordinary = 1" in text and "FAKE" not in text, text
    for cwd in (repo, home):
        text = run("grep", cwd, pattern="ordinary|FAKE").text()
        assert ".claude/commands/deploy.md:1:ordinary deploy" in text and "main.py:1:ordinary" in text, text
        assert "FAKE" not in text and text.endswith(SKIPPED), text
    assert "worktrees/feat/src/main.py" in run("glob", repo, pattern="**/*.py").text()
    assert gate.is_sensitive_path(str(wt / "src" / "main.py"))


def test_a_home_that_is_a_checkout_keeps_its_claude_folder_out(claude_dirs, tmp_path, monkeypatch):
    """HOME is pinned as not a repo: with CLAUDE_CONFIG_DIR elsewhere, a ~/.claude holding no login or transcripts
    yet is still not a repo's .claude when HOME is a git checkout, so its skills and commands stay out."""
    home, _, _ = claude_dirs
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    (home / ".claude" / ".credentials.json").unlink()
    (home / ".claude" / "projects" / "s.jsonl").unlink()
    (home / ".claude" / "projects").rmdir()
    for rel, text in (("skills/s/SKILL.md", "FAKEHOME0skill"), ("commands/c.md", "FAKEHOME0cmd")):
        p = home / ".claude" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    for cwd, extra in ((home, {}), (tmp_path, {"path": "home"})):
        text = run("grep", cwd, pattern="ordinary|FAKE", **extra).text()
        assert "ordinary deploy" in text and "FAKE" not in text and text.endswith(SKIPPED), text
    assert "SKILL.md" not in run("glob", home, pattern="**/*.md").text()


@pytest.mark.parametrize("marker", ["projects/p/s.jsonl", "shell-snapshots/snap.sh", "history.jsonl",
                                    ".credentials.json"])
def test_another_homes_claude_folder_stays_out(claude_dirs, tmp_path, marker):
    """Another home below the searched folder that is a git checkout (dotfiles) is not a repo for its .claude: a
    folder holding a Claude login, transcripts or shell snapshots stays out whole, its commands too."""
    alice = tmp_path / "users" / "alice"
    for p, text in ((alice / ".claude" / marker, "FAKEALICE0store"), (alice / ".claude" / "commands" / "x.md",
                    "FAKEALICE0cmd"), (alice / "notes.md", "ordinary note")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    (alice / ".git").mkdir()
    for cwd, extra in ((tmp_path / "users", {}), (tmp_path, {}), (alice / ".claude" / "commands", {})):
        text = run("grep", cwd, pattern="ordinary|FAKE", **extra).text()
        assert "FAKE" not in text and text.endswith(SKIPPED), text
    assert "notes.md:1:ordinary note" in run("grep", tmp_path / "users", pattern="ordinary").text()
    assert "x.md" not in run("glob", tmp_path, pattern="**/*.md").text()
    assert gate.is_sensitive_path(str(alice / ".claude" / marker))


def test_a_project_claude_folder_is_searched_only_in_its_known_subfolders(claude_dirs):
    """A repo's .claude is searched in its worktrees, commands, agents and skills; any other folder in it (hooks, a
    hand-made secrets folder), and a file called .claude, stays out, as read_file would ask for it."""
    _, repo, _ = claude_dirs
    for rel, text in (("hooks/h.sh", "FAKEHOOK0token"), ("secrets/api.txt", "FAKEAPI0key"),
                      ("agents/a.md", "ordinary agent"), ("skills/s/SKILL.md", "ordinary skill")):
        p = repo / ".claude" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    (repo / "sub" / ".git").mkdir(parents=True)
    (repo / "sub" / ".claude").write_text("FAKEFILE0key\n")   # a file, not a folder: a store as named
    for cwd, extra in ((repo, {}), (repo / ".claude", {}), (repo / ".claude", {"path": "."})):
        text = run("grep", cwd, pattern="ordinary|FAKE", **extra).text()
        assert "ordinary agent" in text and "ordinary skill" in text and "ordinary deploy" in text, text
        assert "FAKE" not in text and text.endswith(SKIPPED), text
