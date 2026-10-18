# Lifted from NousResearch/hermes-agent tools/patch_parser.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""V4A patch parser and applier (the codex / cline patch format).

``*** Begin Patch`` / ``*** End Patch`` wrap operations: ``*** Update File: p`` plus
hunks (``@@ hint @@``, `` ctx``, ``-old``, ``+new``); ``*** Add File: p`` plus ``+``
lines; ``*** Delete File: p``; ``*** Move File: a -> b``.

Exo trims: the parser is kept as is. Validation and apply are merged into one
in-memory simulation over an overlay; disk is touched only when every
operation simulated cleanly, so a bad hunk never leaves a half-applied patch.
"""

from __future__ import annotations

import difflib
import os
import re
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from exo.core.tools.builtin.fuzzy import (
    format_no_match_hint,
    fuzzy_find_and_replace,
    is_already_applied,
)
from exo.core.tools.registry import ToolError

MAX_READ_BYTES = 50_000_000


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass
class HunkLine:
    prefix: str  # ' ', '-', or '+'
    content: str


@dataclass
class Hunk:
    context_hint: str | None = None
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    operation: OperationType
    file_path: str
    new_path: str | None = None  # MOVE only
    hunks: list[Hunk] = field(default_factory=list)


# Markers must fill the whole line at column 0 so content lines that mention the
# format ("+*** End Patch") cannot truncate the patch.
_BEGIN = re.compile(r"^\*\*\*\s*Begin\s+Patch\s*$")
_END = re.compile(r"^\*\*\*\s*End\s+Patch\s*$")
_OPS: list[tuple[OperationType, re.Pattern]] = [
    (OperationType.UPDATE, re.compile(r"\*\*\*\s*Update\s+File:\s*(.+)")),
    (OperationType.ADD, re.compile(r"\*\*\*\s*Add\s+File:\s*(.+)")),
    (OperationType.DELETE, re.compile(r"\*\*\*\s*Delete\s+File:\s*(.+)")),
    (OperationType.MOVE, re.compile(r"\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)"))]
_HINT = re.compile(r"@@\s*(.+?)\s*@@")


def parse_v4a_patch(text: str) -> tuple[list[PatchOperation], str | None]:
    """``(operations, None)`` or ``([], "Parse error: ...")``."""
    lines = [ln.removesuffix("\r") for ln in text.split("\n")]
    start, end = -1, len(lines)
    for i, line in enumerate(lines):
        if _BEGIN.match(line):
            start = i
        elif _END.match(line):
            end = i
            break
    ops: list[PatchOperation] = []
    op: PatchOperation | None = None
    hunk: Hunk | None = None

    def flush_hunk() -> None:
        if op and hunk and hunk.lines:
            op.hunks.append(hunk)

    def flush() -> None:
        if op:
            flush_hunk()
            ops.append(op)

    for line in lines[start + 1:end]:
        found = next(((k, m) for k, rx in _OPS if (m := rx.match(line))), None)
        if found:
            kind, m = found
            flush()
            op = PatchOperation(kind, m.group(1).strip(),
                                m.group(2).strip() if kind is OperationType.MOVE else None)
            hunk = Hunk() if kind is OperationType.ADD else None
            if kind in (OperationType.DELETE, OperationType.MOVE):
                ops.append(op)
                op = None
        elif line.startswith("@@"):
            if op:
                flush_hunk()
                hm = _HINT.match(line)
                hunk = Hunk(context_hint=hm.group(1) if hm else None)
        elif op and line:
            if hunk is None:
                hunk = Hunk()
            if line[0] in "+- ":
                hunk.lines.append(HunkLine(line[0], line[1:]))
            elif line[0] != "\\":  # "\ No newline at end of file"
                hunk.lines.append(HunkLine(" ", line))
    flush()
    errors = []
    for o in ops:
        if not o.file_path:
            errors.append("Operation with empty file path")
        if o.operation is OperationType.UPDATE and not o.hunks:
            errors.append(f"UPDATE {o.file_path!r}: no hunks found")
    return ([], "Parse error: " + "; ".join(errors)) if errors else (ops, None)


def _split_hunk(h: Hunk) -> tuple[list[str], list[str]]:
    return ([x.content for x in h.lines if x.prefix != "+"],
            [x.content for x in h.lines if x.prefix != "-"])


def _insert_addition_only(content: str, h: Hunk, text: str) -> tuple[str | None, str | None]:
    """Place an addition-only hunk after its unique context hint, else at EOF."""
    if h.context_hint:
        n = content.count(h.context_hint)
        if n > 1:
            return None, f"context hint '{h.context_hint}' is ambiguous ({n} occurrences)"
        if n == 1:
            eol = content.find("\n", content.find(h.context_hint))
            if eol == -1:
                return content + "\n" + text, None
            return content[:eol + 1] + text + "\n" + content[eol + 1:], None
    return content.rstrip("\n") + "\n" + text + "\n", None


def _update(content: str, op: PatchOperation) -> tuple[str, list[str]]:
    errors: list[str] = []
    changed = False
    for i, h in enumerate(op.hunks, start=1):
        search, replace = _split_hunk(h)
        if search == replace:
            continue
        changed = True
        pattern, replacement = "\n".join(search), "\n".join(replace)
        if not search:
            new, err = _insert_addition_only(content, h, replacement)
            if err:
                errors.append(f"{op.file_path}: hunk {i}: {err}")
            else:
                content = new
            continue
        new, count, _s, err = fuzzy_find_and_replace(content, pattern, replacement)
        if not count and h.context_hint and (pos := content.find(h.context_hint)) != -1:
            lo, hi = max(0, pos - 500), min(len(content), pos + 2000)
            win, count, _s, err = fuzzy_find_and_replace(content[lo:hi], pattern, replacement)
            if count:
                new = content[:lo] + win + content[hi:]
        if count:
            content = new
        elif not is_already_applied(content, pattern, replacement):
            label = f"'{h.context_hint}'" if h.context_hint else "(no hint)"
            errors.append(f"{op.file_path}: hunk {i} {label} not found: {err}"
                          + format_no_match_hint(err, pattern, content))
    if not changed:
        errors.append(f"{op.file_path}: hunks contain no changes")
    return content, errors


def _diff(path: str, old: str | None, new: str | None) -> str:
    return "".join(difflib.unified_diff(
        (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
        fromfile="/dev/null" if old is None else f"a/{path}",
        tofile="/dev/null" if new is None else f"b/{path}"))


def read_regular(p: Path, cap: int) -> bytes:
    """Bytes of a regular file (links followed). A FIFO or device is refused before it is opened. A file over
    ``cap`` bytes is refused, also one that grows past it after ``fstat``: a cut copy would be written back whole."""
    if not p.is_file():
        raise ToolError(f"{p} is not a regular file (a FIFO, socket or device)")
    with open(os.open(p, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as f:  # a FIFO swapped in: no block
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ToolError(f"{p} is not a regular file (a FIFO, socket or device)")
        if st.st_size > cap:
            raise ToolError(f"{p} is {st.st_size:,} bytes, over the {cap:,}-byte limit; use grep, or sed -n in bash")
        raw = f.read(cap + 1)
        if len(raw) > cap:
            raise ToolError(f"{p} grew past the {cap:,}-byte limit while it was read; use grep, or sed -n in bash")
        return raw


def write_atomic(p: Path, text: str) -> None:
    """Write UTF-8 text via a fresh temp file and rename, keeping line endings as given."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.exo-tmp")
    tmp.unlink(missing_ok=True)  # a leftover, or a planted link: never write through it; O_EXCL fails on a race
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if p.exists():
        os.chmod(tmp, p.stat().st_mode & 0o7777)
    os.replace(tmp, p)


def apply_patch(text: str, resolve: Callable[[str], Path]) -> tuple[bool, str]:
    """Parse and apply a V4A patch. ``resolve`` maps a patch path to a real path.

    Returns ``(ok, unified_diff_or_errors)``. All-or-nothing on validation errors.
    """
    ops, err = parse_v4a_patch(text)
    if err:
        return False, err
    if not ops:
        return False, "Patch contains no operations"
    overlay: dict[Path, str | None] = {}  # None = deleted
    originals: dict[Path, str | None] = {}
    labels: dict[Path, str] = {}
    errors: list[str] = []

    def read(p: Path) -> str | None:
        if p in overlay:
            return overlay[p]
        if p not in originals:
            originals[p] = read_regular(p, MAX_READ_BYTES).decode("utf-8") if p.is_file() else None
        return originals[p]

    def put(p: Path, label: str, value: str | None) -> None:
        read(p)
        overlay[p] = value
        labels[p] = label

    for op in ops:
        src = resolve(op.file_path)
        cur = read(src)
        if op.operation is not OperationType.DELETE and src.is_symlink():  # the rename would replace the link
            errors.append(f"{op.file_path}: is a symlink to {os.path.realpath(src)}; patch that path instead")
        elif op.operation is OperationType.ADD:
            if cur is not None or src.is_dir():
                errors.append(f"{op.file_path}: file already exists; use Update File, not Add File")
            else:
                put(src, op.file_path, "\n".join(
                    x.content for h in op.hunks for x in h.lines if x.prefix == "+"))
        elif cur is None:
            errors.append(f"{op.file_path}: file not found")
        elif op.operation is OperationType.DELETE:
            put(src, op.file_path, None)
        elif op.operation is OperationType.MOVE:
            dst = resolve(op.new_path or "")
            if read(dst) is not None or dst.is_dir() or dst.is_symlink():
                errors.append(f"{op.new_path}: destination already exists; move would overwrite")
            else:
                put(dst, op.new_path or "", cur)
                put(src, op.file_path, None)
        else:
            new, errs = _update(cur, op)
            errors.extend(errs)
            if not errs:
                put(src, op.file_path, new)
    if errors:
        return False, "Patch not applied (no files changed):\n" + "\n".join(f"  - {e}" for e in errors)
    diffs = []
    for p, value in overlay.items():
        if value is None:
            if p.exists():
                p.unlink()
        else:
            write_atomic(p, value)
        diffs.append(_diff(labels[p], originals.get(p), value))
    return True, "".join(diffs) or "(no textual change)"
