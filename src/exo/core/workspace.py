"""Where the agent is working (D-032): the cwd guard and the git facts a handoff needs.

Pure functions over paths. ``git_root`` walks up looking for ``.git`` (a folder, or a file in a worktree), so
the guard costs nothing and never runs a subprocess; ``git_facts`` runs read-only ``git`` commands with a short
timeout and returns text, never raising.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

_CD = re.compile(r"^\s*cd\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))\s*(?:&&|;)")


def git_root(path: str | Path) -> Path | None:
    """The folder holding ``.git`` at or above ``path``, else None."""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return None
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def cwd_notice(path: str | Path, why: str = "") -> str | None:
    """One line when ``path`` is not inside a git repo (edits there are not tracked or recoverable), else None."""
    if git_root(path) is not None:
        return None
    return (f"cwd {path} is not inside a git repo{f' ({why})' if why else ''}: changes here are not tracked; "
            "set a default cwd in the seat's [session] if this is not where you mean to work")


def leading_cd(command: str, cwd: str | Path) -> Path | None:
    """The folder a command moves to with a leading ``cd X &&`` (or ``;``), resolved against ``cwd``; else None."""
    m = _CD.match(command or "")
    if not m:
        return None
    target = Path(next(g for g in m.groups() if g)).expanduser()
    return target if target.is_absolute() else Path(cwd) / target


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def git_facts(cwd: str | Path, max_status: int = 30) -> dict[str, Any]:
    """Branch, uncommitted changes and recent commits of the repo around ``cwd``. ``{"repo": None}`` outside one."""
    root = git_root(cwd)
    if root is None:
        return {"repo": None}
    r = str(root)
    status = _git(r, "status", "--short").splitlines()
    return {"repo": r, "branch": _git(r, "rev-parse", "--abbrev-ref", "HEAD"),
            "head": _git(r, "log", "-1", "--format=%h %s"),
            "changed": status[:max_status], "changed_total": len(status),
            "recent": _git(r, "log", "-5", "--format=%h %s").splitlines()}


def handoff_dir() -> Path:
    """The default folder for handoff files: ``$EXO_HANDOFF_DIR``, else ``handoff_dir`` in the config file,
    else ``$EXO_HOME/handoffs``. Nothing here assumes one person's folder layout."""
    from ..config import Config, ConfigError
    from ..store import exo_home

    env = os.environ.get("EXO_HANDOFF_DIR")
    if env:
        return Path(env).expanduser()
    try:
        configured = Config.load().get("handoff_dir")
    except (ConfigError, OSError):
        configured = None
    return Path(configured).expanduser() if configured else exo_home() / "handoffs"


def handoff_path(seat: str, arg: str = "", today: date | None = None) -> Path:
    """Where a handoff goes: ``arg`` if given (a folder gets ``<seat>-<date>.md`` inside it), else
    ``<handoff_dir()>/<seat>-<date>.md`` (``$EXO_HOME/handoffs`` unless configured)."""
    name = f"{seat}-{(today or date.today()).isoformat()}.md"
    arg = (arg or "").strip()
    if not arg:
        return handoff_dir() / name
    p = Path(arg).expanduser()
    return p / name if arg.endswith("/") or p.is_dir() else p
