"""code_find: ask a repository what some code does; jevgrep (``jg``) answers with files and excerpts.

Wraps dzhng/jevgrep (MIT), which uses the TypeSafe Jev decision model to pick relevant files and verbatim
excerpts. ``jg`` takes its provider and key only from a saved config file (it ignores key environment
variables), so each call writes a throwaway config in a 0700 temp dir (``XDG_CONFIG_HOME``), feeds the key
to ``jg auth --stdin`` (never argv, never the environment, never a log), runs the search, and deletes the
folder. Backend order: TypeSafe Jev with the key Exo's jev provider uses, then local Laya through jg's
custom endpoint (same /v1/systemone API) when it is configured and listening. The result names the
backend that answered. Hidden when Node 22+ or ``jg``/``npx`` is missing (reason in ``exo models``).
See docs/TOOLS.md and D-029.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

from exo.core.models.keys import lookup_key
from exo.core.tools.builtin.search import _root
from exo.core.tools.builtin.shell import DEFAULT_TIMEOUT, MAX_TIMEOUT, kill_process_group, timeout_arg
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.gate import holds_secret_store

PACKAGE = "@dzhng/jevgrep@0.8.0"
KEY_ENV = "TYPESAFE_API_KEY"
PAGE_CHARS = 20_000       # characters per page of output
MAX_JG_BYTES = 256_000    # passed to jg --max-output-bytes
MIN_FALLBACK_S = 10.0     # no point starting a second backend with less time than this
LAYA_DEFAULT_URL = "http://127.0.0.1:8000/v1"
LAYA_MODEL = "laya"

_pages: OrderedDict[tuple[str, str], str] = OrderedDict()  # recent full outputs, for page=2..n
_PAGE_KEEP = 8


# ------------------------------------------------------------------ detection

def _node_major() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.", out.strip())
    return int(m.group(1)) if m else None


def detect_runner() -> tuple[list[str] | None, str]:
    """(argv prefix for jg, status text). None prefix means the tool is hidden and the text says why."""
    major = _node_major()
    if major is None:
        return None, "hidden: node not found (jevgrep needs Node.js 22+)"
    if major < 22:
        return None, f"hidden: Node {major} found, jevgrep needs Node.js 22+"
    if jg := shutil.which("jg"):
        return [jg], f"jg at {jg}"
    if npx := shutil.which("npx"):
        return [npx, "-y", PACKAGE], f"npx {PACKAGE} (downloads on first use)"
    return None, "hidden: neither jg nor npx is on PATH (npm install -g @dzhng/jevgrep)"


_runner, _why = detect_runner()


def status() -> str:
    """One line for ``exo models``: is code_find offered, and with which runner."""
    return f"code_find: {'available, ' + _why if _runner else _why}"


def _available() -> bool:
    return _runner is not None


# ------------------------------------------------------------------ backends

class Backend(NamedTuple):
    name: str
    auth: list[str]
    secret: str


def _laya_url() -> str | None:
    """Base URL of the local Laya provider when configured and accepting connections, else None."""
    try:
        from exo.config import Config
        providers = (Config.load().data.get("models") or {}).get("providers") or {}
    except Exception:
        providers = {}
    url = (providers.get("laya") or {}).get("base_url")
    if not url:
        from exo.core.models.router import Router
        url = (Router().providers.get("laya") or {}).get("base_url") or LAYA_DEFAULT_URL
    if not isinstance(url, str):
        return None  # a number or a list in the config names no address
    try:
        u = urlparse(url)
        if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost"):
            return None  # jg accepts http only for loopback
        with socket.create_connection((u.hostname, u.port or 80), timeout=0.5):
            pass
    except (OSError, ValueError):  # ValueError: a broken host, or a port that is not a number or is over 65535
        return None
    return url.rstrip("/")


def pick_backends(env: dict[str, str] | None = None) -> list[Backend]:
    """Jev first when a key exists, then local Laya when it is up."""
    out: list[Backend] = []
    key = lookup_key(KEY_ENV, env)
    if key:
        out.append(Backend("jev", ["auth", "--provider", "typesafe", "--stdin"], key))
    if url := _laya_url():
        out.append(Backend("laya", ["auth", "--provider", "custom", "--base-url", url,
                                    "--model", LAYA_MODEL, "--stdin"], "local"))
    return out


# ------------------------------------------------------------------ running

def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[key]")
    return text


def _run(argv: list[str], env: dict[str, str], cwd: str, timeout: float, stdin: str | None,
         cancel: threading.Event | None) -> tuple[int, str, str, str]:
    """Run one process. Returns (exit code, stdout, stderr, status) with status ok, timeout or interrupted."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True, text=True,
                            encoding="utf-8", errors="replace")
    deadline, data, status = time.monotonic() + timeout, stdin, "ok"
    out = err = ""
    while True:
        try:
            out, err = proc.communicate(data, timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            data = None
            if cancel is not None and cancel.is_set():
                status = "interrupted"
            elif time.monotonic() > deadline:
                status = "timeout"
            if status != "ok":
                kill_process_group(proc)
                with contextlib.suppress(Exception):
                    out, err = proc.communicate(timeout=2)
                break
    code = {"timeout": 124, "interrupted": 130}.get(status, proc.returncode or 0)
    return code, out or "", err or "", status


def _child_env(cfg_home: str, allow: Any = (), deny: Any = ()) -> dict[str, str]:
    """The parent environment through the one secret scrub (D-039, with the seat's env_allow and env_deny), with
    jg's config pointed at the temp dir."""
    from exo.envscrub import scrub_env

    env = scrub_env(os.environ, allow, deny)
    env.update({"XDG_CONFIG_HOME": cfg_home, "NO_COLOR": "1", "TERM": "dumb"})
    return env


def _search(runner: list[str], backend: Backend, question: str, root: Path, cwd: str, timeout: float,
            cancel: threading.Event | None, meta: dict[str, Any] | None = None) -> tuple[int, str, str]:
    """One backend: temp config, auth, search, cleanup. Returns (code, stdout, note)."""
    t0 = time.monotonic()
    cfg = tempfile.mkdtemp(prefix="exo-jg-")
    try:
        os.chmod(cfg, 0o700)
        env = _child_env(cfg, (meta or {}).get("env_allow") or (), (meta or {}).get("env_deny") or ())
        code, out, err, status = _run([*runner, *backend.auth], env, cwd, min(timeout, 60), backend.secret, cancel)
        if code != 0:
            return code, out, _scrub(f"jg auth failed: {err or out}", [backend.secret])[:500]
        left = max(1.0, timeout - (time.monotonic() - t0))
        code, out, err, status = _run([*runner, "--max-output-bytes", str(MAX_JG_BYTES), "--", question, str(root)],
                                      env, cwd, left, None, cancel)
        return code, out, _scrub(err, [backend.secret]) if status == "ok" else status
    finally:
        shutil.rmtree(cfg, ignore_errors=True)


def _page(text: str, page: int) -> str:
    """Page ``page`` (1-based) of ``text``, with a footer when there is more."""
    n = max(1, -(-len(text) // PAGE_CHARS))
    if page > n:
        raise ToolError(f"page {page} out of range; the result has {n} page(s)")
    chunk = text[(page - 1) * PAGE_CHARS: page * PAGE_CHARS]
    if n > 1:
        more = f"; call code_find again with page={page + 1} for the rest" if page < n else ""
        chunk += f"\n[page {page} of {n}{more}]"
    return chunk


def code_find(args: dict[str, Any], ctx: ToolContext) -> str:
    question = str(args["question"]).strip().lstrip("-").strip()  # jg gets it after "--"; this is a second guard
    if not question:
        raise ToolError("question must not be empty")
    root = _root(args, ctx)
    if holds_secret_store(str(root)):  # jg reads the whole folder and cannot skip Exo's stores (#241)
        raise ToolError(f"code_find does not search {root}: it holds secret stores (home folder, EXO_HOME, "
                        "~/.ssh...). Name a project folder, or use grep, which skips them.")
    page = int(args.get("page") or 1)
    cache_key = (str(root), question)
    if page > 1 and cache_key in _pages:
        return _page(_pages[cache_key], page)
    timeout = timeout_arg(args)
    if _runner is None:
        raise ToolError(_why)
    backends = pick_backends()
    if not backends:
        raise ToolError("code_find has no backend: set TYPESAFE_API_KEY (Jev) or start local Laya "
                        "(laya-serve) and use grep or read instead")
    cwd = str(ctx.resolve("."))
    t0, errors = time.monotonic(), []
    for b in backends:
        left = timeout - (time.monotonic() - t0)
        if errors and left < MIN_FALLBACK_S:
            errors.append(f"{b.name}: skipped, {left:.0f}s left")
            break
        try:
            code, out, note = _search(_runner, b, question, root, cwd, left, ctx.cancel, ctx.meta)
        except OSError as exc:
            errors.append(f"{b.name}: could not start jg ({type(exc).__name__})")
            continue
        if code in (0, 2) and out.strip():
            ms = int((time.monotonic() - t0) * 1000)
            head = f"[code_find backend={b.name} duration_ms={ms}" + (" incomplete" if code == 2 else "")
            if errors:
                head += " fallback_from=" + "; ".join(errors)
            _pages[cache_key] = out.rstrip()
            while len(_pages) > _PAGE_KEEP:
                _pages.popitem(last=False)
            return f"{head}]\n{_page(_pages[cache_key], page)}"
        if code == 130:
            raise ToolError("code_find interrupted")
        errors.append(f"{b.name}: {note or ('exit ' + str(code))}"[:300])
    ms = int((time.monotonic() - t0) * 1000)
    raise ToolError(f"code_find failed after {ms}ms ({'; '.join(errors)}). Use grep and read instead.")


DESCRIPTION = (
    "Ask what code does and get back relevant files with verbatim excerpts, using jevgrep (Jev decision "
    "model). Use it for unfamiliar code and behavior questions ('where is X decided', 'how does Y work') "
    "across many files. When the symbol or path is known, use grep or read_file instead; they are faster. "
    "Read-only. Slow (about 10-60 s) and sends eligible source to the backend, so ask one precise question. "
    "Treat the output as reading leads, not proof: open the files to confirm. The first line names the "
    "backend that answered. Output is paged; pass page=2 for more.")


def register(reg: Registry) -> None:
    reg.tool("code_find", DESCRIPTION,
             {"type": "object", "properties": {
                 "question": {"type": "string", "description": "A natural-language question about behavior"},
                 "path": {"type": "string", "description": "Folder to search (default cwd)"},
                 "page": {"type": "integer", "description": "Page of a long result (default 1)"},
                 "timeout": {"type": "number", "description": f"Seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})"}},
              "required": ["question"]}, risk="low", toolset="search", check_fn=_available)(code_find)
