import pytest

from exo import gate


@pytest.mark.parametrize('prefix', ['GH_HOST=evil.example', "GH_HOST='evil.example'", 'env GH_HOST=evil.example'])
def test_bare_repo_on_gh_host_gets_actual_host_target(prefix):
    action = {'tool': 'bash', 'args': {'command': prefix + ' gh pr comment 5 -R myorg/app -b ok'}}
    policy = gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']},
                                                'deny': ['target:evil.example/*']}})
    assert gate.decide(gate.classify(action), policy).outcome == 'deny'


def test_other_host_cannot_inherit_github_internal_scope():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'GH_HOST=evil.example gh pr comment 5 -R myorg/app -b ok'}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']}}}))
    assert d.outcome == 'ask' and d.exposure == 'public' and d.third_party


def test_explicit_host_repo_is_not_overridden_by_the_default_host():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'GH_HOST=evil.example gh pr comment 5 -R https://github.com/myorg/app -b ok'}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'scopes': {'internal': ['myorg/*']},
                                                           'deny': ['target:evil.example/*']}}))
    assert d.outcome == 'log'


def test_url_in_inline_body_does_not_hide_default_host():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'GH_HOST=evil.example gh pr comment 5 -R myorg/app -b https://example.com'}})
    d = gate.decide(c, gate.normalize_policies({'gates': {'deny': ['target:evil.example/*']}}))
    assert d.outcome == 'deny'
