"""Search tools: glob (find files by pattern) and grep (regex over file contents). Pure stdlib."""

from __future__ import annotations

import codecs
import fnmatch
import os
import re
import stat
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Any, Callable, Iterator

from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.gate import StoreSkip

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build"}
MAX_RESULTS = 200
MAX_FILE_BYTES = 2_000_000
_CHUNK = 65_536
_LINE_BOUNDS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"  # every boundary str.splitlines() splits on

def _lines(p: Path) -> Iterator[str]:
    """A regular file's lines, streamed: exactly decode('utf-8', 'replace').splitlines() without
    holding the file, its text and its line list at once (#255 W1-4). Guards as in read_regular."""
    if not p.is_file(): raise ToolError(f"{p} is not a regular file (a FIFO, socket or device)")  # noqa: E701
    with open(os.open(p, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as f:
        if not stat.S_ISREG((st := os.fstat(f.fileno())).st_mode):
            raise ToolError(f"{p} is not a regular file (a FIFO, socket or device)")
        if st.st_size > MAX_FILE_BYTES:
            raise ToolError(f"{p} is {st.st_size:,} bytes, over the {MAX_FILE_BYTES:,}-byte limit")
        size, head = 0, []
        while size < 8192 and (chunk := f.read(_CHUNK)):
            head.append(chunk); size += len(chunk)  # noqa: E702
        if b"\x00" in b"".join(head)[:8192]: raise ToolError(f"{p} looks binary")  # noqa: E701
        dec, buf = codecs.getincrementaldecoder("utf-8")(errors="replace"), ""
        for i, chunk in enumerate(chain(head, iter(lambda: f.read(_CHUNK), b""))):
            if i >= len(head):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ToolError(f"{p} grew past the {MAX_FILE_BYTES:,}-byte limit while it was read")
            buf += dec.decode(chunk)
            lines = buf.splitlines()
            if buf and buf[-1] in _LINE_BOUNDS and buf[-1] != "\r":
                yield from lines; buf = ""  # noqa: E702
            elif lines:  # hold the last fragment; a trailing \r may yet pair with a \n
                yield from lines[:-1]; buf = lines[-1] + ("\r" if buf.endswith("\r") else "")  # noqa: E702
        yield from (buf + dec.decode(b"", final=True)).splitlines()


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
    return str(p.relative_to(root)) if p.is_relative_to(root) else str(p)


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
        file_out: list[str] = []; file_total = 0  # noqa: E702 - committed only if the file reads clean
        try:
            for n, line in enumerate(_lines(p), start=1):
                if files_only and file_total:
                    continue  # still drain the file: a late size or binary refusal skips it whole
                if rx.search(line):
                    file_total += 1
                    if len(out) + len(file_out) < limit:
                        file_out.append(rel if files_only else f"{rel}:{n}:{line[:300]}")
        except (OSError, ToolError):
            continue
        out += file_out; total += file_total  # noqa: E702
    if total > len(out):
        out.append(f"[{total - len(out)} more matches; narrow the search]")
    return "\n".join(filter(None, [*(out or ["No matches."]), skip.note()]))


def register(reg: Registry) -> None:
    from exo.search_worker import run_search

    reg.tool("glob", "Find files by glob pattern (e.g. '**/*.py', 'src/*.toml', 'README*'), "
             "newest first. Skips .git, node_modules, virtualenvs, caches and secret stores.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string"},
                 "path": {"type": "string", "description": "Folder to search (default cwd)"},
                 "limit": {"type": "integer"}},
              "required": ["pattern"]}, risk="low", toolset="search")(partial(run_search, "glob"))
    reg.tool("grep", "Search file contents with a Python regex. Returns path:line:text rows. "
             "Skips secret stores below the path.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string", "description": "Regular expression"},
                 "path": {"type": "string", "description": "File or folder (default cwd)"},
                 "glob": {"type": "string", "description": "Only files matching this glob"},
                 "ignore_case": {"type": "boolean"},
                 "files_only": {"type": "boolean", "description": "List matching files only"},
                 "limit": {"type": "integer"}},
              "required": ["pattern"]}, risk="low", toolset="search")(partial(run_search, "grep"))
