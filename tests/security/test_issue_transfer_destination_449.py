import pytest

from exo import gate


@pytest.mark.parametrize('source', ['5 -R myorg/app', 'https://github.com/myorg/app/issues/5'])
@pytest.mark.parametrize('destination', ['pub/other', 'https://github.com/pub/other', 'github.com/pub/other'])
@pytest.mark.parametrize('deny', [False, True])
def test_transfer_destination_gets_scope_and_target_policy(source, destination, deny):
    # Place repo flags after both operands, as gh accepts.
    command = 'gh issue transfer ' + source.split(' -R ')[0] + ' ' + destination
    if ' -R ' in source:
        command += ' -R ' + source.split(' -R ')[1]
    pol = {'gates': {'scopes': {'internal': ['myorg/*']}}}
    if deny:
        pol['gates']['deny'] = ['target:pub/*']
    d = gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), gate.normalize_policies(pol))
    assert d.outcome == ('deny' if deny else 'ask')
    if not deny:
        assert d.exposure == 'public' and d.third_party


def test_other_instance_destination_keeps_raw_target_beside_repo_scope():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'gh issue transfer 5 ghe.corp/myorg/app -R myorg/app'}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']},
                                                            'deny': ['target:ghe.corp/*']}}))
    assert d.outcome == 'deny'


def test_internal_destination_preserves_internal_control():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'gh issue transfer 5 myorg/other -R myorg/app'}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']}}}))
    assert d.outcome == 'log'
