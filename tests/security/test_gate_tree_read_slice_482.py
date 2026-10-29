"""Literal option-free tree reads; output options and expansion ambiguity stay protected (#482)."""
import pytest
from exo import gate


def inspect(line, gates=None):
    c = gate.classify({'tool': 'bash', 'args': {'command': line}})
    return c, gate.decide(c, gate.normalize_policies({'gates': gates or {}}))


@pytest.mark.parametrize('line', ['tree ~/.exo/plugins', 'tree ~/.exo/plugins/foo ~/.exo/policies'])
def test_literal_operand_only_tree_is_read(line):
    c, d = inspect(line)
    assert c.category == 'read' and d.outcome == 'allow'


@pytest.mark.parametrize('line', ['tree -o ~/.exo/state/log ~/.exo/plugins',
                                'tree -o~/.exo/state/log ~/.exo/plugins',
                                'tree -ao ~/.exo/state/log ~/.exo/plugins',
                                'tree $FLAGS ~/.exo/plugins', 'tree {-o,x} ~/.exo/plugins',
                                'tree ~/.exo/plugins > ~/.exo/state/log'])
def test_output_and_ambiguous_forms_keep_the_store_deny(line):
    _, d = inspect(line, {'deny': ['store_write']})
    assert d.outcome == 'deny'


def test_read_target_deny_remains_effective():
    _, d = inspect('tree ~/.exo/plugins', {'deny': ['target:*/.exo/plugins*']})
    assert d.outcome == 'deny'


def test_credential_read_and_other_write_denies_remain():
    _, credentials = inspect('tree ~/.exo/keys', {'deny': ['credentials']})
    _, force = inspect('tree ~/.exo/plugins; git push --force origin main', {'deny': ['force_push']})
    assert credentials.outcome == 'deny' and force.outcome == 'deny'
