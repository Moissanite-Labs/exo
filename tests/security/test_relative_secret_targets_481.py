import pytest

from exo import gate


@pytest.mark.parametrize('command', ['cd ~/.exo/policies; cat ../keys/seat',
                                     'tar -C ~/.exo/policies -czf - ../keys/seat'])
@pytest.mark.parametrize('kind', ['deny', 'reserved', 'rule'])
def test_relative_secret_target_keeps_resolved_alias(command, kind, tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('EXO_HOME', str(tmp_path / '.exo'))
    policy = ({'rules': [{'match': {'target': '*/.exo/keys*'}, 'decision': 'deny'}]} if kind == 'rule'
              else {kind: ['target:*/.exo/keys*']})
    c = gate.classify({'tool': 'bash', 'args': {'command': command}})
    d = gate.decide(c, gate.normalize_policies({'gates': policy}))
    assert d.outcome == ('ask' if kind == 'reserved' else 'deny')
    if kind == 'reserved':
        assert any(target.endswith('/.exo/keys/seat') for _, target in d.reserved)


def test_old_relative_target_and_dev_descriptor_denies_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EXO_HOME", str(tmp_path / ".exo"))
    command = 'cd ~/.exo/policies; cat ../keys/seat; cp /tmp/x /dev/fd/3'
    c = gate.classify({'tool': 'bash', 'args': {'command': command}})
    for target in ['../keys/seat', '/dev/fd/3']:
        p = gate.normalize_policies({'gates': {'deny': ['target:' + target]}})
        assert gate.decide(c, p).outcome == 'deny'
