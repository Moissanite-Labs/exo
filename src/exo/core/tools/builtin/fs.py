"""File tools: read_file, write_file, edit_file (fuzzy replace) and apply_patch (V4A).

Wrappers are Exo's own; the matching and patch logic is lifted (see fuzzy.py, patch.py).
Relative paths resolve against ``ToolContext.cwd``.
"""

from __future__ import annotations

from typing import Any

from exo.core.tools.builtin.fuzzy import format_no_match_hint, fuzzy_find_and_replace
from exo.core.tools.builtin.patch import apply_patch, read_text, write_atomic
from exo.core.tools.registry import Registry, ToolContext, ToolError

DEFAULT_LIMIT = 2000
MAX_LINE_CHARS = 2000


def _read_existing(ctx: ToolContext, path: str) -> str:
    p = ctx.resolve(path)
    if not p.exists():
        raise ToolError(f"File not found: {path}")
    if p.is_dir():
        raise ToolError(f"{path} is a directory; use glob to list it")
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ToolError(f"{path} looks like a binary file")
    try:
        return read_text(p)
    except UnicodeDecodeError:
        raise ToolError(f"{path} is not valid UTF-8 text") from None


def read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    text = _read_existing(ctx, args["path"])
    offset = max(1, int(args.get("offset", 1)))
    limit = max(1, int(args.get("limit", DEFAULT_LIMIT)))
    lines = text.splitlines()
    if not lines:
        return "(empty file)"
    if offset > len(lines):
        raise ToolError(f"offset {offset} is past the end ({len(lines)} lines)")
    chunk = lines[offset - 1:offset - 1 + limit]
    out = []
    for n, line in enumerate(chunk, start=offset):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " [line truncated]"
        out.append(f"{n:6d}|{line}")
    end = offset + len(chunk) - 1
    if end < len(lines):
        out.append(f"[showing lines {offset}-{end} of {len(lines)}; use offset={end + 1} for more]")
    return "\n".join(out)


def write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    p = ctx.resolve(args["path"])
    if p.is_dir():
        raise ToolError(f"{args['path']} is a directory")
    existed = p.exists()
    write_atomic(p, args["content"])
    verb = "Overwrote" if existed else "Created"
    return f"{verb} {args['path']} ({len(args['content'])} chars)"


def edit_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path, old, new = args["path"], args["old_string"], args["new_string"]
    content = _read_existing(ctx, path)
    updated, count, strategy, err = fuzzy_find_and_replace(
        content, old, new, replace_all=bool(args.get("replace_all", False)))
    if err:
        raise ToolError(err + format_no_match_hint(err, count, old, content))
    write_atomic(ctx.resolve(path), updated)
    how = "" if strategy == "exact" else f" (matched via {strategy})"
    return f"Edited {path}: {count} replacement{'s' if count != 1 else ''}{how}"


def patch(args: dict[str, Any], ctx: ToolContext) -> str:
    ok, out = apply_patch(args["patch"], ctx.resolve)
    if not ok:
        raise ToolError(out)
    return out


def register(reg: Registry) -> None:
    reg.tool("read_file", "Read a UTF-8 text file. Returns numbered lines (N|text). Use offset and "
             "limit to page through large files.",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "File path, absolute or relative to cwd"},
                 "offset": {"type": "integer", "description": "First line to show (1-based)"},
                 "limit": {"type": "integer", "description": f"Max lines (default {DEFAULT_LIMIT})"}},
              "required": ["path"]}, risk="low", toolset="fs")(read_file)
    reg.tool("write_file", "Create or fully overwrite a file with the given content. Parent "
             "folders are created. Prefer edit_file for changes to an existing file.",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}},
              "required": ["path", "content"]}, risk="medium", toolset="fs")(write_file)
    reg.tool("edit_file", "Replace old_string with new_string in a file. old_string must match "
             "one place (whitespace and indentation drift is tolerated); set replace_all to "
             "change every exact occurrence.",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "old_string": {"type": "string", "description": "Existing text to replace"},
                 "new_string": {"type": "string", "description": "Replacement text"},
                 "replace_all": {"type": "boolean"}},
              "required": ["path", "old_string", "new_string"]}, risk="medium", toolset="fs")(edit_file)
    reg.tool("apply_patch", "Apply a multi-file patch in V4A format: '*** Begin Patch', then "
             "'*** Update File: p' with @@ hunks (' ' context, '-' remove, '+' add), "
             "'*** Add File: p' with '+' lines, '*** Delete File: p', '*** Move File: a -> b', "
             "then '*** End Patch'. Nothing is written unless every hunk applies.",
             {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"]},
             risk="medium", toolset="fs")(patch)
