"""ssh's own -E log writes are independent of the remote command (#484)."""
import pytest
from exo import gate


@pytest.mark.parametrize('option', ['-E ~/.exo/state/log', '-E~/.exo/state/log', '-vE ~/.exo/state/log'])
@pytest.mark.parametrize('remote', ['ls', "'cat x; sort x'"])
def test_local_debug_log_remains_a_store_write(option, remote):
    c = gate.classify({'tool': 'bash', 'args': {'command': f'ssh {option} host {remote}'}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'deny': ['store_write']}}))
    assert c.category == 'store_write' and d.outcome == 'deny'


def test_plain_remote_read_stays_read():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'ssh host ls'}})
    assert c.category == 'read'


def test_remote_target_deny_is_kept():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'ssh -E ~/.exo/state/log host ls'}})
    assert gate.decide(c, gate.normalize_policies({'gates': {'deny': ['target:host']}})).outcome == 'deny'
