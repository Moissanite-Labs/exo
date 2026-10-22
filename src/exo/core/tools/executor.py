"""Trusted executor extension point; requests contain detached, immutable JSON.

An executor is installed Python code, not an untrusted plugin sandbox. Its
decision to invoke ``run_trusted`` explicitly authorizes a host handler.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import math
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from exo.core.types import ToolResult

if TYPE_CHECKING:
    from exo.core.tools.registry import Tool

ENTRY_POINT_GROUP = "exo.executors"
TrustedDispatch = Callable[[], Awaitable[ToolResult]]


class ExecutorConfigurationError(RuntimeError):
    """An explicitly configured or required executor cannot be used."""


def freeze_arguments(value: Any) -> Any:
    """Detach JSON values and deeply freeze them; reject non-JSON objects."""
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return MappingProxyType({key: freeze_arguments(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_arguments(item) for item in value)
    if value is None or type(value) in (str, bool, int) or (type(value) is float and math.isfinite(value)):
        return value
    raise ValueError("arguments must contain only finite JSON values")


def thaw_arguments(value: Any) -> Any:
    """Copy an immutable request into fresh mutable JSON for a worker/handler."""
    if isinstance(value, Mapping):
        return {key: thaw_arguments(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_arguments(item) for item in value]
    return value


@dataclass(frozen=True)
class ExecutionRequest:
    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    cwd: str
    session: str | None

    def __post_init__(self) -> None:
        # Also enforce the contract on requests built by executor tests/clients.
        object.__setattr__(self, "arguments", freeze_arguments(thaw_arguments(self.arguments)))


class ExecutionBroker(Protocol):
    async def execute(self, request: ExecutionRequest, tool: Tool,
                      run_trusted: TrustedDispatch) -> ToolResult:
        """Return the ordinary tool result; unknown/unsupported operations fail closed."""
        ...


def validate_executor(executor: Any) -> ExecutionBroker:
    execute = getattr(executor, "execute", None)
    if not inspect.iscoroutinefunction(execute):
        raise ExecutorConfigurationError("tool executor must expose async execute(request, tool, run_trusted)")
    try:
        inspect.signature(execute).bind(None, None, None)
    except (TypeError, ValueError) as exc:
        raise ExecutorConfigurationError("tool executor has an incompatible execute signature") from exc
    return executor


def load_executor(env: Mapping[str, str] | None = None) -> tuple[ExecutionBroker | None, bool]:
    """Load one named installed factory. No file path/module override or fallback."""
    source = os.environ if env is None else env
    required = source.get("EXO_TOOL_EXECUTOR_REQUIRED") == "1"
    name = source.get("EXO_TOOL_EXECUTOR", "")
    if not name:
        if required:
            raise ExecutorConfigurationError("EXO_TOOL_EXECUTOR_REQUIRED=1 requires EXO_TOOL_EXECUTOR")
        return None, required
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ExecutorConfigurationError("EXO_TOOL_EXECUTOR must name an installed exo.executors entry point")
    try:
        matches = [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
        if len(matches) != 1:
            raise ExecutorConfigurationError(f"tool executor {name!r} must match exactly one installed entry point")
        executor = matches[0].load()()
        return validate_executor(executor), required
    except ExecutorConfigurationError:
        raise
    except Exception as exc:
        raise ExecutorConfigurationError(f"cannot load tool executor {name!r}: {type(exc).__name__}: {exc}") from exc
