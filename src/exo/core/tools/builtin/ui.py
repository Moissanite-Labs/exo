"""The ui tool: the model opens, updates or closes a panel beside the transcript (D-014).

Emits a ``ui.panel`` event. Every panel carries a plain ``text`` fallback so any client can show it.
Registered by ``register(reg, publish)``, where ``publish(body, session)`` puts an event on the hub.
"""

from __future__ import annotations

from typing import Any, Callable, get_args, get_type_hints

from exo.core.sessions import new_id
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.core.types import ToolSpec
from exo.protocol import schema as s

KINDS = get_args(get_type_hints(s.UiPanel)["kind"])
ACTIONS = ("open", "update", "close")

SPEC = ToolSpec(
    name="ui", toolset="ui", risk="low",
    description=("Open, update or close a panel beside the chat, for things that read better as a diff, table, "
                 "task list or choice than as text. `open` returns a panel_id; pass it to `update` or `close`. "
                 "`text` is required: plain words that say the same thing, shown by clients that cannot draw the panel."),
    parameters={"type": "object", "required": ["action", "text"], "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "kind": {"type": "string", "description": "One of: " + ", ".join(KINDS) + ". Default markdown."},
        "title": {"type": "string"},
        "text": {"type": "string", "description": "Plain-English fallback; required."},
        "data": {"type": "object", "description": (
            "Kind-specific JSON (docs/PANELS.md). diff {files:[{path,diff}]}; table {columns:[..],rows:[[..]]}; "
            "tasks {tasks:[{title,status}]}; choice {prompt,options:[{id,label}]}; file {path,content,language}; "
            "chart {labels:[..],values:[..]}; taste {rules:[{rule_id,text,confidence}]}; markdown {markdown}.")},
        "reason": {"type": "string", "description": "Why you opened it, one line."},
        "panel_id": {"type": "string", "description": "From open; needed for update and close."}}})


def register(reg: Registry, publish: Callable[[Any, str | None], Any]) -> None:
    def handler(args: dict[str, Any], ctx: ToolContext) -> str:
        action, kind = args.get("action"), args.get("kind") or "markdown"
        if action not in ACTIONS:
            raise ToolError(f"action must be one of {', '.join(ACTIONS)}")
        if kind not in KINDS:
            raise ToolError(f"kind must be one of {', '.join(KINDS)}")
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("text is required: say in plain words what the panel shows")
        data = args.get("data") or {}
        if not isinstance(data, dict):
            raise ToolError("data must be a JSON object")
        pid = args.get("panel_id") or ""
        if action == "open":
            pid = new_id("p")
        elif not pid:
            raise ToolError(f"panel_id is required to {action} a panel")
        publish(s.UiPanel(panel_id=pid, action=action, kind=kind, title=str(args.get("title") or ""), text=text,
                          data=data, reason=str(args.get("reason") or "")), ctx.session)
        return f"Panel {pid}: {action} done."

    reg.register(SPEC, handler, replace=True)
