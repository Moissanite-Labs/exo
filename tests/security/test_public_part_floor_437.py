import pytest

from exo import gate


@pytest.mark.parametrize('suffix', [
    "gh pr comment 6 -R other/repo --body $'fine\\nF'",
    'gh issue close 9 -R other/repo',
])
def test_internal_lead_cannot_hide_public_part_escalation(suffix):
    command = "gh pr comment 5 -R myorg/app --body $'ok\\nF' && " + suffix
    policy = gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']}}})
    result = gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy)
    assert result.outcome == 'ask'
    assert result.exposure == 'public' and result.third_party


def test_internal_only_and_standalone_public_controls():
    policy = gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']}}})
    for command, expected in [('gh pr comment 5 -R myorg/app -b ok; gh issue close 9 -R myorg/app', 'log'),
                              ('gh pr comment 6 -R other/repo -b ok', 'ask')]:
        assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == expected


def test_stricter_part_matrix_survives_when_third_party_escalation_is_disabled():
    policy = gate.normalize_policies({'gates': {
        'scopes': {'internal': ['myorg/*']}, 'escalate': {'third_party': False},
        'matrix': {'costly': {'internal': 'allow', 'public': 'log'}},
    }})
    command = 'gh pr comment 5 -R myorg/app -b ok; gh pr comment 6 -R other/repo -b ok'
    assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == 'log'


def test_duplicate_part_reasons_remain_bounded():
    command = '; '.join('gh pr comment 5 -R myorg/app -b ok' for _ in range(100))
    policy = gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']}}})
    result = gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy)
    assert len(result.reason) < 500
