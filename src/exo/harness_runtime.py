"""Inert runtime services for profiles that omit jobs or delegation.

These objects preserve the core's lifecycle and query interfaces without constructing
the optional managers, opening their stores, or starting workers. Tool visibility is
selected by the application; direct execution still fails closed here.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from exo.core.tools.registry import PRE_TOOL_CALL, Block, ToolContext, ToolError
from exo.core.types import ToolCall, ToolSpec


@dataclass
class _DisabledTarget:
    closed: bool = False


class DisabledJobs:
    """No job resources, with a foreground-only description of the bash tool."""

    def __init__(self, engine: Any, cfg: dict[str, Any] | None = None) -> None:
        self.e = engine
        self.loop: asyncio.AbstractEventLoop | None = None
        self.target = _DisabledTarget()
        self.live: dict[str, Any] = {}
        self.recovered = False
        # Brokers dispatch before local handlers, so the disabled manager alone is
        # insufficient. This runs after the core's plugin bridge and before dispatch.
        engine.registry.add_hook(PRE_TOOL_CALL, self._guard)
        tool = engine.registry.get("bash")
        if tool is not None:
            parameters = deepcopy(tool.spec.parameters)
            parameters.get("properties", {}).pop("background", None)
            if "required" in parameters:
                parameters["required"] = [name for name in parameters["required"] if name != "background"]
            # The built-in's final recommendation names tools omitted by this profile.
            description = tool.spec.description.partition(" For a command that takes longer")[0]
            spec = replace(tool.spec, parameters=parameters, description=description)
            engine.registry.register(spec, tool.handler, check_fn=tool.check_fn, replace=True)

    def _guard(self, call: ToolCall, spec: ToolSpec, ctx: ToolContext) -> Block | None:
        if call.name == "bash" and call.arguments.get("background") is True:
            return Block("Background jobs are disabled by the active harness profile; run the command in the foreground")
        return None

    def start(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ToolError("Background jobs are disabled by the active harness profile; run the command in the foreground")

    def rows(self, limit: int = 30) -> list[dict[str, Any]]:
        return []

    async def recover(self) -> list[str]:
        self.recovered = True
        return []

    def abort_startup(self) -> None:
        self.target.closed = True

    async def close(self) -> None:
        self.abort_startup()


class DisabledDelegator:
    """No child sessions or task storage when delegation is disabled."""

    def __init__(self, engine: Any, cfg: dict[str, Any] | None = None, signer: Any = None) -> None:
        self.engine = engine
        self.loops: dict[str, Any] = {}
        self.tiers: dict[str, str] = {}
        self.marks: dict[str, list[str]] = {}
        self.allow: dict[str, set[str]] = {}
        self.over: set[str] = set()

    def install(self) -> None:
        pass

    def fail_stale(self) -> None:
        pass

    def depth(self, session: str | None) -> int:
        return 0

    def route(self, tier: str) -> tuple[Any, str]:
        """Scheduled turns share model routing without creating delegation resources."""
        from exo.core.agents.delegate import Delegator
        return Delegator.route(self, tier)  # type: ignore[arg-type]  # route only reads self.engine

    def list(self, session: str | None = None) -> list[dict[str, Any]]:
        return []

    def interrupt(self, session: str) -> bool:
        return False

    async def tool(self, args: dict[str, Any], ctx: ToolContext) -> str:
        raise ToolError("Delegation is disabled by the active harness profile; do this work directly")
