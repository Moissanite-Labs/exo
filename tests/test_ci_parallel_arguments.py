"""Exercise the workflow's string/boolean selector, including a falsy empty result."""
import ast
from pathlib import Path
import re

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml'


def parallel_arguments(event):
    workflow = WORKFLOW.read_text()
    matches = re.findall(r'^      PYTEST_PARALLEL: \$\{\{ (.+) \}\}$', workflow, re.MULTILINE)
    assert len(matches) == 1
    expression = matches[0].replace('github.event_name', 'event_name').replace('&&', 'and').replace('||', 'or')
    tree = ast.parse(expression, mode='eval')
    # This selector uses strings and booleans only. Python's and/or have the same
    # operand-returning short circuit behavior, so an empty true arm falls through.
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq,
               ast.Name, ast.Load, ast.Constant)
    assert all(isinstance(node, allowed) for node in ast.walk(tree))
    assert all(node.id == 'event_name' for node in ast.walk(tree) if isinstance(node, ast.Name))
    assert all(type(node.value) is str for node in ast.walk(tree) if isinstance(node, ast.Constant))
    return eval(compile(tree, '<CI selector>', 'eval'), {'__builtins__': {}}, {'event_name': event})


@pytest.mark.parametrize('event, expected', [
    ('schedule', ''),
    ('pull_request', '-n auto'),
    ('push', '-n auto'),
    ('merge_group', '-n auto'),
])
def test_event_selects_serial_or_parallel_arguments(event, expected):
    result = parallel_arguments(event)
    assert type(result) is str
    assert result == expected
