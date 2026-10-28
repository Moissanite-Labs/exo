"""GNU time output is a write, not only the nested command. Never execute the lines."""
import pytest
from exo import gate


@pytest.mark.parametrize('option', ['-o ~/.exo/state/output', '-o~/.exo/state/output',
                                    '--output=~/.exo/state/output', '--output ~/.exo/state/output'])
@pytest.mark.parametrize('prefix', ['time ', '/usr/bin/time ', 'command time ', 'env time '])
def test_time_output_keeps_store_deny(option, prefix):
    c = gate.classify({'tool': 'bash', 'args': {'command': prefix + '-f GRANTS ' + option + ' true'}})
    assert gate.decide(c, {'gates': {'deny': ['store_write']}}).outcome == 'deny'


def test_time_output_keeps_local_write_deny_and_inner_delete_deny():
    c = gate.classify({'tool': 'bash', 'args': {'command': '/usr/bin/time -o /tmp/out rm -rf build'}})
    for category in ['local_write', 'delete']:
        assert gate.decide(c, {'gates': {'deny': [category]}}).outcome == 'deny'


def test_time_format_abbreviation_does_not_hide_the_output_option():
    c = gate.classify({'tool': 'bash', 'args': {'command': "/usr/bin/time --for '%E' -o ~/.exo/state/output true"}})
    assert gate.decide(c, {'gates': {'deny': ['store_write']}}).outcome == 'deny'


def test_inner_command_output_flag_is_not_a_time_output():
    c = gate.classify({'tool': 'bash', 'args': {'command': '/usr/bin/time printf -o'}})
    assert not any(p.category == 'local_write' for p in gate._each_part(c))
