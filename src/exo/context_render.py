"""Model-facing message labels and excerpts, shared by summaries and read-only context views.

The renderer stays outside the turn engine; core.context re-exports render_message for existing callers.
"""
from __future__ import annotations

import json

from exo import provenance
from exo.core.types import Message


def cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" ...[{len(text) - limit} chars cut]"


def render_message(msg: Message, limit: int = 4000) -> str:
    """One message as labeled text for a summarizer or a view placeholder. Unless the principal wrote it, its
    pinned-block tags are escaped (D-040, #279): a peer's, bus seat's, job's or tool's ``<exo-pinned>`` printed here
    would otherwise sit in the system message as if exo-context had pinned the principal's words."""
    author = provenance.author_of(msg)
    label = provenance.user_label(author).upper() if msg.role == "user" else msg.role.upper()
    origin = msg.meta.get("origin")
    if origin == "import":
        label += " (imported)"
    if msg.meta.get("thread_label"):  # an agent log entry (D-028): which thread it came from
        label += f" in thread {msg.meta['thread_label']}"
    lines = []
    for p in msg.content:
        if p.type == "text" and p.text:
            lines.append(cut(p.text, limit))
        elif p.type == "image":
            lines.append(f"[image {p.url or p.mime or ''}]".replace(" ]", "]"))
    for c in msg.tool_calls:
        lines.append(f"  {c.name}({cut(json.dumps(c.arguments, ensure_ascii=False), 400)})")
    for r in msg.tool_results:
        flag = " error" if r.is_error else ""
        lines.append(f"[result {r.call_id}{flag}] {cut(r.text(), limit // 2)}")
    text = f"[{label}] " + "\n".join(lines)
    return text if author == provenance.PRINCIPAL else provenance.defuse_pins(text)
