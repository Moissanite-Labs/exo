"""less logfile targets are writes, even when reading stdin. Commands are never executed."""
import pytest
from exo import gate


@pytest.mark.parametrize('flag', ['-o ', '-O ', '-o', '-O', '--log-file=', '--LOG-FILE=', '--log-file '])
@pytest.mark.parametrize('prefix', ['less ', '/usr/bin/less ', 'command less ', 'env less '])
def test_less_logfile_into_store_keeps_store_write_deny(flag, prefix):
    line = 'cat f | ' + prefix + flag + '~/.exo/state/output'
    c = gate.classify({'tool': 'bash', 'args': {'command': line}})
    assert gate.decide(c, {'gates': {'deny': ['store_write']}}).outcome == 'deny'


def test_less_logfile_is_local_write_but_a_store_input_is_only_read():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'less -o /tmp/out ~/.exo/plugins/foo.py'}})
    assert any(p.category == 'local_write' and p.target == '/tmp/out' for p in gate._each_part(c))
    assert not any(p.category == 'store_write' for p in gate._each_part(c))


def test_less_end_of_options_does_not_create_a_logfile():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'less -- -o ~/.exo/plugins/foo.py'}})
    assert c.category == 'read'
