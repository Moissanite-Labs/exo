"""Recursive cp into a store ancestor adds a write part, preserving the original reading (#483)."""
import pytest
from exo import gate


@pytest.fixture(autouse=True)
def store_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    (home / '.exo').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('EXO_HOME', str(home / '.exo'))


@pytest.mark.parametrize('line', ['cp -r /tmp/evil/.exo ~/', 'cp -rT /tmp/evil ~/.exo',
                                'cp -R /tmp/evil/. ~/.exo/', 'cp --recursive /tmp/evil/policies ~/.exo/',
                                'cp -rt ~/.exo /tmp/evil', 'cp -r --target-directory=~/.exo /tmp/evil'])
def test_recursive_ancestor_landing_is_denied(line):
    c = gate.classify({'tool': 'bash', 'args': {'command': line}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'deny': ['store_write']}}))
    assert c.category == 'store_write' and d.outcome == 'deny'


@pytest.mark.parametrize('line', ['cp /tmp/x /tmp/out', 'cp -r /tmp/evil /tmp/out'])
def test_unrelated_copy_is_not_a_store_write(line):
    c = gate.classify({'tool': 'bash', 'args': {'command': line}})
    assert c.category == 'shell'


def test_side_effect_deny_is_preserved_beside_landing():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'cp -r /tmp/evil ~/.exo; git push --force origin main'}})
    assert gate.decide(c, gate.normalize_policies({'gates': {'deny': ['force_push']}})).outcome == 'deny'
