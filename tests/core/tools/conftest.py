import pytest

from exo.core.tools import ToolContext, default_registry
from exo.core.types import ToolCall


@pytest.fixture
def reg():
    return default_registry()


@pytest.fixture
def run(reg, tmp_path):
    """Call a tool by name in tmp_path; returns the ToolResult."""
    counter = iter(range(10_000))

    def _run(name, **args):
        return reg.call_sync(ToolCall(id=f"c{next(counter)}", name=name, arguments=args),
                             ToolContext(cwd=str(tmp_path)))
    return _run
