"""The harness self-report tools (D-025): ``exo_trace`` (read) and ``exo_harness_report`` (file a finding).

``exo_trace`` gives the agent real numbers about how the harness performed this session and which model
and route it is running on, so it can answer "which model am I" and evaluate its environment from data.
``exo_harness_report`` files a finding about the harness itself (source ``agent``); the same title dedups.
Registered by ``register(reg, trace_text, report)``. Subagents get neither: the trace already rolls their
turns into the parent's, and their problems are caught by the anomaly rules.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from exo.core.findings import SEVERITIES
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.core.types import ToolSpec

REPORT = ToolSpec(
    name="exo_harness_report", toolset="harness", risk="low",
    description=("File a finding about the harness itself (Exo's tools, model routing, speed, errors or missing "
                 "abilities), not about your task. Look at exo_trace first and put the evidence in `detail`. The "
                 "developer reads these and fixes them. The same title is counted, not listed twice."),
    parameters={"type": "object", "required": ["title", "detail"], "properties": {
        "title": {"type": "string", "description": "One line: what is wrong."},
        "detail": {"type": "string", "description": "What happened, with numbers or the exact error."},
        "severity": {"type": "string", "enum": list(SEVERITIES),
                     "description": "blocker: cannot work. bug: wrong or failing. papercut: slow or awkward. "
                                    "idea: a suggestion. Default papercut."}}})
TRACE = ToolSpec(
    name="exo_trace", toolset="harness", risk="low",
    description=("Read how the harness performed in this session: per turn wall time, the model route that served "
                 "each call, fallbacks, tool calls and errors, tokens, and any anomaly flags. Also says which "
                 "model and route you are running on, and the harness version. Read only."),
    parameters={"type": "object", "properties": {
        "last": {"type": "integer", "description": "Only the last N turns (default 10)."}}})


def signature(title: str) -> str:
    return "agent:" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def register(reg: Registry, trace_text: Callable[[str | None, int], str],
             report: Callable[..., dict[str, Any]]) -> None:
    def trace(args: dict[str, Any], ctx: ToolContext) -> str:
        last = args.get("last", 10)
        return trace_text(ctx.session, last if isinstance(last, int) and last > 0 else 10)

    def file(args: dict[str, Any], ctx: ToolContext) -> str:
        title = str(args.get("title") or "").strip()
        detail = str(args.get("detail") or "").strip()
        if not title or not detail:
            raise ToolError("title and detail are both required")
        sev = args.get("severity") or "papercut"
        if sev not in SEVERITIES:
            raise ToolError(f"severity must be one of {', '.join(SEVERITIES)}")
        row = report(signature(title), title, source="agent", severity=sev, detail=detail, session=ctx.session,
                     turn=ctx.meta.get("turn_id") or None)
        return f"Filed finding {row['id']} ({row['severity']}, seen {row['count']}x). Thank you."

    reg.register(TRACE, trace, replace=True)
    reg.register(REPORT, file, replace=True)
