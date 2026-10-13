"""Wave 1 acceptance (docs/STANDALONE.md 6.1), run against real processes.

exod is started with ``exo serve`` (default engine: the real core) in a temporary
EXO_HOME, driven by ``exo chat`` over the Unix socket, killed, restarted and resumed.
The model is the config-scripted fake, so nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from exo.config import Config
from exo.protocol import schema as s
from exo.protocol.client import Client

REPO = Path(__file__).resolve().parents[2]

FAKE = {"rules": [
    {"match": "read", "steps": [{"tool": "read_file", "args": {"path": "notes.txt"}}, "notes.txt says hello"]},
    {"match": "edit", "steps": [{"tool": "edit_file", "args": {"path": "notes.txt", "old_string": "hello",
                                                               "new_string": "goodbye"}}, "edited notes.txt"]},
    {"match": "run", "steps": [{"tool": "bash", "args": {"command": "cat notes.txt"}}, "ran cat"]},
]}


@pytest.fixture
def home():
    # Unix socket paths are capped near 104 bytes on macOS, so keep the root short.
    d = Path(tempfile.mkdtemp(prefix="exoi", dir="/tmp" if os.path.isdir("/tmp") else None))
    work = d / "work"
    work.mkdir()
    (work / "notes.txt").write_text("hello world\n")
    (d / "config.json").write_text(json.dumps({"version": 1, "cwd": str(work), "models": {"fake": FAKE}}))
    # This fixture exercises persistence with scripted tools, independently of gate policy.
    plugin = d / "plugins" / "test-allow"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: test-allow\n")
    (plugin / "__init__.py").write_text(
        'def register(ctx):\n    ctx.register_hook("pre_tool_call", lambda **kw: None)\n')
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EXO_", "ANTHROPIC_"))}
    return {**env, "EXO_HOME": str(home), "PYTHONUNBUFFERED": "1"}


def _exo(*args: str) -> list[str]:
    return [sys.executable, "-m", "exo.cli", *args]


def _serve(home: Path) -> subprocess.Popen:
    sock = home / "run" / "exod.sock"
    sock.unlink(missing_ok=True)
    proc = subprocess.Popen(_exo("serve"), env=_env(home), stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    while not sock.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            raise AssertionError(f"exod did not start: {proc.stderr.read() if proc.stderr else ''}")
        time.sleep(0.05)
    return proc


def _chat(home: Path, lines: list[str], *args: str) -> str:
    r = subprocess.run(_exo("chat", *args), input="\n".join(lines) + "\n", env=_env(home),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


async def _collect(home: Path, since: int = 0) -> list[s.Event]:
    """A client that attaches with ``since`` and reads until it has caught up."""
    async with Client(socket=str(home / "run" / "exod.sock")) as c:
        last = (await c.call(s.Hello(client="test")))["seq"]
        out: list[s.Event] = []
        if last <= since:
            return out
        async for ev in c.events(since=since):
            out.append(ev)
            if ev.seq >= last:
                return out
    return out


def test_three_turn_session_restart_resume_and_replay(home):
    """Checks 2, 3 and 4: read, edit and run through exod and exo chat; kill -9 and resume;
    a second client with since=0 sees the same sequence the log holds."""
    proc = _serve(home)
    try:
        out = _chat(home, ["read notes.txt", "edit it"], "--title", "acceptance")
        assert "[read_file]" in out and "hello world" in out and "notes.txt says hello" in out
        assert "[edit_file]" in out and "edited notes.txt" in out
        assert (home / "work" / "notes.txt").read_text() == "goodbye world\n"
        before = asyncio.run(_collect(home))
    finally:
        proc.send_signal(signal.SIGKILL)  # a crash, not a clean shutdown
        proc.wait(10)

    proc = _serve(home)
    try:
        out = _chat(home, ["run cat"], "--resume")
        assert re.search(r"resumed s_\w+ \(\d+ messages\)", out)
        assert "[bash]" in out and "goodbye world" in out and "ran cat" in out
        first = asyncio.run(_collect(home))
        second = asyncio.run(_collect(home))
        mid = asyncio.run(_collect(home, since=before[-1].seq))
    finally:
        proc.terminate()
        proc.wait(10)

    seqs = [e.seq for e in first]
    assert seqs == list(range(1, len(first) + 1))  # dense across the restart
    assert [e.to_json() for e in first] == [e.to_json() for e in second]
    assert [e.to_json() for e in first[: len(before)]] == [e.to_json() for e in before]
    assert [e.seq for e in mid] == seqs[len(before):]
    with sqlite3.connect(home / "sessions.sqlite") as db:
        rows = db.execute("SELECT seq, id, type FROM events ORDER BY seq").fetchall()
    assert rows == [(e.seq, e.id, e.type) for e in first]
    types = [e.type for e in first]
    assert types.count("turn.completed") == 3 and types.count("session.created") == 1
    assert {"tool.started", "tool.completed", "tool.output", "message.delta"} <= set(types)


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=True).stdout.strip()


def _commit(repo: Path, msg: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def test_upstream_report_on_fixture_mirror(tmp_path):
    """Check 5: `exo upstream report` lists changes to both lifted slot files since the baseline."""
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    _git(mirror, "init", "-q", "-b", "main")
    base = _commit(mirror, "feat: slots", {"agent/memory_provider.py": "class MemoryProvider:\n    pass\n",
                                           "agent/context_engine.py": "class ContextEngine:\n    pass\n"})
    _commit(mirror, "fix(memory): prefetch caches", {"agent/memory_provider.py": "class MemoryProvider:\n    x = 1\n"})
    _commit(mirror, "fix(context): compress keeps tail", {"agent/context_engine.py": "class ContextEngine:\n    y = 2\n"})
    root = tmp_path / "exo"
    (root / "src/exo/core").mkdir(parents=True)
    (root / "src/exo/core/slots.py").write_text("# Lifted from hermes-agent\n")
    (root / "PROVENANCE.toml").write_text(f'''[upstream.hermes]
repo = "https://example.invalid/hermes"

[[module]]
exo = "src/exo/core/slots.py"
mode = "lift"
upstream = ["agent/memory_provider.py", "agent/context_engine.py"]
commit = "{base}"
''')
    args = ("upstream", "report", "--repo", str(mirror), "--json", "--provenance-root", str(root))
    r = subprocess.run(_exo(*args), capture_output=True, text=True, timeout=60, env=_env(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    text = r.stdout
    assert "fix(memory): prefetch caches" in text and "fix(context): compress keeps tail" in text
    assert "agent/memory_provider.py" in text and "agent/context_engine.py" in text


CORE = ["src/exo/core", "src/exo/protocol", "src/exo/server", "src/exo/cli_chat.py", "src/exo/upstream.py"]


def test_core_line_budget():
    """Check 6: the core stays under budget (generated protocol files excluded).

    Wave 1 shipped under 8k. STANDALONE.md section 6 allows 15k by the end of wave 3; delegation (D-016)
    is a wave 3 item, so the budget moved to 15k on 2026-10-12. Raised to 16k the same day for the audit
    security wave (D-038..D-042). Engine split step 4 (D-067) brought it under the old 15k goal; the ceiling stays
    at 16k until the budget decision in docs/ENGINE-SPLIT.md. Issue #46 tracks the rest of the split."""
    files = [p for c in CORE for p in ((REPO / c).rglob("*.py") if (REPO / c).is_dir() else [REPO / c])
             if "generated" not in p.parts]
    total = sum(len(p.read_text().splitlines()) for p in files)
    assert total < 16000, total


def test_dependencies_are_listed():
    """Check 6, second half: only httpx (core) and pynacl (store signing) are required."""
    import tomllib

    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"].get("dependencies", [])
    assert all(re.split(r"[<>=\[ ;]", d)[0].lower() in {"httpx", "pynacl"} for d in deps), deps


from exo import privacy  # noqa: E402

OWNER = privacy.deny_regex()  # names come from an untracked file; None when absent
NEW_FILES = ["src/exo/core", "src/exo/protocol", "src/exo/server", "src/exo/cli_chat.py", "src/exo/upstream.py",
             "src/exo/config.py", "tests/integration", "tests/core", "tests/protocol", "tests/upstream",
             "docs/CORE-V2-INTERFACES.md", "PROVENANCE.toml"]


def test_no_owner_names_in_new_files():
    """Check 7: no path, name or account of the owner in wave 1 files."""
    hits = []
    for c in NEW_FILES:
        p = REPO / c
        for f in (p.rglob("*") if p.is_dir() else [p]):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix in {".py", ".md", ".toml", ".json", ".ts"}:
                hits += [f"{f.relative_to(REPO)}:{i}" for i, line in enumerate(f.read_text().splitlines(), 1)
                         if f.name != Path(__file__).name
                         and ((OWNER and OWNER.search(line)) or privacy.generic_findings(line))]
    assert not hits, hits


def test_model_defaults_from_environment_only(tmp_path):
    cfg = Config(home=tmp_path)
    assert cfg.models(env={}) == {}  # real catalog; no credentials never selects the fake
    assert cfg.models(env={"ANTHROPIC_API_KEY": "x"}) == {}  # catalog routes, Anthropic first
    cfg.data["models"] = {"local": {"base_url": "http://127.0.0.1:8080/v1", "model": "qwen"}}
    m = cfg.models(env={"ANTHROPIC_API_KEY": "x"})
    assert m["roles"]["main"] == ["local:qwen"] and m["providers"]["local"]["transport"] == "openai"
    cfg.data["models"] = {"roles": {"main": ["anthropic:claude-haiku-5-5"]}}
    assert cfg.models(env={})["roles"]["main"] == ["anthropic:claude-haiku-5-5"]
