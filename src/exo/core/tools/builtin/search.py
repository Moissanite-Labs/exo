"""Search tools: glob (find files by pattern) and grep (regex over file contents). Pure stdlib."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterator

from exo.core.tools.builtin.patch import read_regular
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.gate import StoreSkip

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build"}
MAX_RESULTS = 200
MAX_FILE_BYTES = 2_000_000


def _walk(root: Path, skip: Callable[[str], bool] = lambda p: False) -> Iterator[Path]:
    """Files below ``root``, less what ``skip`` refuses (secret stores, #241); a refused folder is not entered."""
    if os.path.realpath(root) == os.path.realpath(os.sep):   # minutes of walking, through /proc and /sys
        raise ToolError(f"{root} is the whole file system; name a folder below it")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not skip(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if not skip(os.path.join(dirpath, name)):
                yield Path(dirpath) / name


def _root(args: dict[str, Any], ctx: ToolContext) -> Path:
    root = ctx.resolve(args.get("path") or ".")
    if not root.exists():
        raise ToolError(f"Path not found: {args.get('path')}")
    return root


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _glob_regex(pattern: str) -> re.Pattern:
    """Translate a path glob: ``**/`` spans zero or more folders, ``*`` and ``?`` stay in one."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and (j := pattern.find("]", i + 1)) != -1:
            body = pattern[i + 1:j]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = j + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _matches(rel: str, pattern: str) -> bool:
    """Path glob match; a pattern without ``/`` matches the file name at any depth."""
    if "/" not in pattern:
        return fnmatch.fnmatch(os.path.basename(rel), pattern)
    return bool(_glob_regex(pattern).match(rel))


def glob(args: dict[str, Any], ctx: ToolContext) -> str:
    root, pattern = _root(args, ctx), args["pattern"]
    limit = int(args.get("limit", MAX_RESULTS))
    if root.is_file():
        return str(root)
    skip = StoreSkip(str(root), args.get("path") or "")
    hits = [p for p in _walk(root, skip) if _matches(_rel(p, root), pattern)]
    hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    out = [_rel(p, root) for p in hits[:limit]] or ["No files matched."]
    if len(hits) > limit:
        out.append(f"[{len(hits) - limit} more; narrow the pattern]")
    return "\n".join(filter(None, [*out, skip.note()]))


def grep(args: dict[str, Any], ctx: ToolContext) -> str:
    root = _root(args, ctx)
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        rx = re.compile(args["pattern"], flags)
    except re.error as exc:
        raise ToolError(f"Bad regex: {exc}") from None
    include = args.get("glob")
    limit = int(args.get("limit", MAX_RESULTS))
    files_only = bool(args.get("files_only"))
    skip = StoreSkip(str(root), args.get("path") or "")
    files = [root] if root.is_file() else _walk(root, skip)
    out: list[str] = []
    total = 0
    for p in files:
        rel = p.name if root.is_file() else _rel(p, root)
        if include and not _matches(rel, include):
            continue
        try:
            raw = read_regular(p, MAX_FILE_BYTES)  # skips FIFOs and devices, which would block or never end
        except (OSError, ToolError):
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                total += 1
                if len(out) < limit:
                    out.append(rel if files_only else f"{rel}:{n}:{line[:300]}")
                if files_only:
                    break
    if total > len(out):
        out.append(f"[{total - len(out)} more matches; narrow the search]")
    return "\n".join(filter(None, [*(out or ["No matches."]), skip.note()]))


def register(reg: Registry) -> None:
    reg.tool("glob", "Find files by glob pattern (e.g. '**/*.py', 'src/*.toml', 'README*'), "
             "newest first. Skips .git, node_modules, virtualenvs, caches and secret stores.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string"},
                 "path": {"type": "string", "description": "Folder to search (default cwd)"},
                 "limit": {"type": "integer"}},
              "required": ["pattern"]}, risk="low", toolset="search")(glob)
    reg.tool("grep", "Search file contents with a Python regex. Returns path:line:text rows. "
             "Skips secret stores below the path.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string", "description": "Regular expression"},
                 "path": {"type": "string", "description": "File or folder (default cwd)"},
                 "glob": {"type": "string", "description": "Only files matching this glob"},
                 "ignore_case": {"type": "boolean"},
                 "files_only": {"type": "boolean", "description": "List matching files only"},
                 "limit": {"type": "integer"}},
              "required": ["pattern"]}, risk="low", toolset="search")(grep)
