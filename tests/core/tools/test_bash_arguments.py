"""Bash argument validation must preserve execution intent before dispatch."""
import asyncio

import pytest

from exo.core.tools.builtin import shell
from exo.core.tools.registry import Registry, ToolContext
from exo.core.types import ToolCall


@pytest.mark.parametrize('extra', [{'run_in_background': True}, {'background': True, 'time_out': 900}])
def test_unknown_bash_argument_never_dispatches(extra, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(shell, 'run_command', lambda *a, **kw: calls.append('foreground') or (0, '', 'done'))
    monkeypatch.setattr(shell, '_start_job', lambda *a: calls.append('background') or 'started')
    registry = Registry()
    shell.register(registry)
    result = asyncio.run(registry.call(ToolCall('fixture', 'bash', {'command': 'synthetic-only', **extra}),
                                      ToolContext(cwd=str(tmp_path))))
    assert result.is_error and 'unknown argument' in result.text()
    assert next(key for key in extra if key not in ('background',)) in result.text()
    assert calls == []


@pytest.mark.parametrize('background,timeout', [(False, 30), (True, 900)])
def test_valid_bash_execution_mode_and_timeout_are_preserved(background, timeout, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(shell, 'run_command', lambda command, cwd, limit, **kw:
                        calls.append(('foreground', limit)) or (0, 'fixture', 'done'))
    monkeypatch.setattr(shell, '_start_job', lambda args, ctx, cwd:
                        calls.append(('background', args['timeout'])) or 'started')
    registry = Registry()
    shell.register(registry)
    result = asyncio.run(registry.call(ToolCall('fixture', 'bash', {
        'command': 'synthetic-only', 'background': background, 'timeout': timeout}), ToolContext(cwd=str(tmp_path))))
    assert not result.is_error
    assert calls == [('background' if background else 'foreground', timeout)]


def test_foreground_timeout_limit_still_rejects_before_dispatch(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(shell, 'run_command', lambda *a, **kw: calls.append('foreground'))
    registry = Registry()
    shell.register(registry)
    result = asyncio.run(registry.call(ToolCall('fixture', 'bash', {
        'command': 'synthetic-only', 'timeout': shell.MAX_TIMEOUT + 1}), ToolContext(cwd=str(tmp_path))))
    assert result.is_error and 'timeout must be between' in result.text()
    assert calls == []
