"""Render ui.panel bodies as Rich renderables. Every renderer falls back to the plain `text`."""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from exo_tui.theme import rich_style

STATUS_MARK = {"done": "[x]", "completed": "[x]", "running": "[~]", "in_progress": "[~]",
               "failed": "[!]", "cancelled": "[-]", "queued": "[ ]", "pending": "[ ]", "todo": "[ ]"}


def diff_text(diff: str) -> Text:
    out = Text()
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            style = rich_style("fg", "bold")
        elif line.startswith("@@"):
            style = rich_style("cold")
        elif line.startswith("+"):
            style = "green"  # the terminal's own green and red: a diff must read the same in every theme
        elif line.startswith("-"):
            style = "red"
        else:
            style = rich_style("fg2")
        out.append(line + "\n", style=style)
    return out


def _unified(old: str, new: str, path: str = "") -> str:
    import difflib

    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{path}", f"b/{path}"))


def _diff(data: dict[str, Any], text: str) -> RenderableType:
    out = Text()
    files = data.get("files")
    if isinstance(files, list) and files:
        for f in files:
            out.append(f"{f.get('path', '')}\n", style=rich_style("fg", "bold"))
            out.append_text(diff_text(f.get("diff") or _unified(f.get("old", ""), f.get("new", ""), f.get("path", ""))))
        return out
    if data.get("diff"):
        return diff_text(str(data["diff"]))
    if "old" in data or "new" in data:
        return diff_text(_unified(str(data.get("old", "")), str(data.get("new", "")), str(data.get("path", ""))))
    # no structured data: the text may itself be a unified diff
    return diff_text(text) if any(ln.startswith(("+", "-", "@@")) for ln in text.splitlines()) else Text(text)


def _table(data: dict[str, Any], text: str) -> RenderableType:
    cols = data.get("columns") or data.get("headers") or []
    rows = data.get("rows") or []
    if rows and isinstance(rows[0], dict):
        cols = cols or list(rows[0])
        rows = [[r.get(c, "") for c in cols] for r in rows]
    if not cols and rows:
        cols = [""] * len(rows[0])
    if not rows and not cols:
        return Text(text)
    t = Table(show_header=bool(any(cols)), header_style=rich_style("fg3", "bold"), box=None, padding=(0, 2, 0, 0))
    for c in cols:
        t.add_column(str(c))
    for r in rows:
        t.add_row(*[str(x) for x in r])
    return t


def _tasks(data: dict[str, Any], text: str) -> RenderableType:
    items = data.get("tasks") or data.get("items") or []
    if not items:
        return Text(text)
    out = Text()
    for it in items:
        if isinstance(it, str):
            it = {"title": it}
        status = str(it.get("status", "queued"))
        role = {"done": "ok", "completed": "ok", "running": "accent", "in_progress": "accent", "failed": "err"}.get(status, "fg3")
        out.append(f"{STATUS_MARK.get(status, '[ ]')} ", style=rich_style(role))
        out.append(f"{it.get('title') or it.get('text') or it.get('id', '')}", style=rich_style("fg2"))
        if it.get("detail"):
            out.append(f"  {it['detail']}", style=rich_style("fg3"))
        out.append("\n")
    return out


def choice_options(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(id, label) pairs from data.options; items are strings or {id, label}."""
    out = []
    for o in data.get("options") or data.get("choices") or []:
        if isinstance(o, dict):
            oid = str(o.get("id", o.get("value", o.get("label", ""))))
            out.append((oid, str(o.get("label", oid))))
        else:
            out.append((str(o), str(o)))
    return out


def _taste(data: dict[str, Any], text: str) -> RenderableType:
    rules = data.get("rules") or ([data] if data.get("rule") or data.get("rule_id") else [])
    if not rules:
        return Text(text)
    out = Text()
    for r in rules:
        out.append("taste ", style=rich_style("cold", "bold"))
        out.append(str(r.get("rule") or r.get("text") or r.get("rule_id") or "") + "\n")
        if r.get("path"):
            out.append(f"  {r['path']}\n", style=rich_style("fg3"))
    return out


def _file(data: dict[str, Any], text: str) -> RenderableType:
    content = data.get("content")
    if content is None:
        return Text(text or str(data.get("path", "")))
    path = str(data.get("path", ""))
    return Syntax(str(content), data.get("language") or Syntax.guess_lexer(path or "x.txt", str(content)),
                  line_numbers=True, word_wrap=True)


def bar_chart(data: dict[str, Any], width: int = 30) -> Text | None:
    pairs: list[tuple[str, float]] = []
    if data.get("labels") and data.get("values"):
        pairs = [(str(a), float(b)) for a, b in zip(data["labels"], data["values"])]
    else:
        pts = data.get("points") or data.get("data") or data.get("series") or []
        for p in pts:
            if isinstance(p, dict):
                pairs.append((str(p.get("label", p.get("x", ""))), float(p.get("value", p.get("y", 0)))))
            elif isinstance(p, (list, tuple)) and len(p) == 2:
                pairs.append((str(p[0]), float(p[1])))
    if not pairs:
        return None
    top = max(abs(v) for _, v in pairs) or 1.0
    lw = max(len(a) for a, _ in pairs)
    out = Text()
    for a, v in pairs:
        out.append(f"{a:<{lw}} ", style=rich_style("fg3"))
        out.append("█" * max(0, round(abs(v) / top * width)), style=rich_style("cold" if v >= 0 else "err"))
        out.append(f" {v:g}\n")
    return out


def render_panel(kind: str, text: str, data: dict[str, Any] | None) -> RenderableType:
    data = data if isinstance(data, dict) else {}
    try:
        if kind == "diff":
            return _diff(data, text)
        if kind == "table":
            return _table(data, text)
        if kind == "tasks":
            return _tasks(data, text)
        if kind == "taste":
            return _taste(data, text)
        if kind == "file":
            return _file(data, text)
        if kind == "chart":
            return bar_chart(data) or Text(text)
        if kind == "markdown":
            return Markdown(str(data.get("markdown") or text))
        if kind == "choice":
            return Text(str(data.get("prompt") or text))
    except Exception:
        pass
    return Text(text)
