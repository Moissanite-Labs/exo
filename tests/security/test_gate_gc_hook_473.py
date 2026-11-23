"""Git runs gc.recentObjectsHook through a shell; classify only, never execute it."""
import pytest
from exo import gate


@pytest.mark.parametrize('line', [
    "git -c gc.recentObjectsHook='rm -rf /srv/data' gc",
    "git -c GC.RECENTOBJECTSHOOK='rm -rf /srv/data' gc --auto",
    "git -c gc.recentObjectsHook='rm -rf /srv/data' maintenance run --task=gc",
    "git config gc.recentObjectsHook 'rm -rf /srv/data'",
    "git config set gc.recentObjectsHook 'rm -rf /srv/data'",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=gc.recentObjectsHook GIT_CONFIG_VALUE_0='rm -rf /srv/data' git gc",
    '''GIT_CONFIG_PARAMETERS="'gc.recentObjectsHook'='rm -rf /srv/data'" git gc''',
])
def test_recent_objects_hook_keeps_nested_delete_deny(line):
    c = gate.classify({'tool': 'bash', 'args': {'command': line}})
    assert gate.decide(c, {'gates': {'deny': ['delete']}}).outcome == 'deny'


def test_recent_objects_hook_from_environment_is_unread_code():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'git --config-env=gc.recentObjectsHook=X gc'}})
    assert gate.decide(c).outcome == 'ask'


def test_plain_gc_config_remains_ordinary():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'git -c gc.auto=0 gc'}})
    assert gate.decide(c).outcome == 'allow'
