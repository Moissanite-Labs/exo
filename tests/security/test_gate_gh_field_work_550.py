"""One parse of invariant gh text fields per target-reading set, and other targets decided in linear work (#550).

Every command string here is data for exo.gate.classify and decide. None is run."""
from collections import Counter

import pytest

from exo import gate


def action(count, verb='comment', text='-b synthetic-body'):
    return {'tool': 'bash', 'args': {'command': f'gh pr {verb} ' + ' '.join(
        f'https://ghe.corp/o/r/pull/{i}' for i in range(count)) + ' ' + text}}


# A close's or reopen's comment and a merge's subject are read once too, not once per other target.
@pytest.mark.parametrize('verb,text', [('comment', '-b synthetic-body'), ('close', '--comment synthetic-body'),
                                       ('reopen', '-c synthetic-body'), ('merge', '--subject synthetic-subject')])
def test_text_option_scan_work_grows_with_input_not_alt_target_product(monkeypatch, verb, text):
    original = gate._opt
    words = 0

    def counted(argv, *names):
        nonlocal words
        words += len(argv)
        return original(argv, *names)

    monkeypatch.setattr(gate, '_opt', counted)
    gate.classify(action(8, verb, text))
    small, words = words, 0
    gate.classify(action(32, verb, text))
    assert words <= 6 * small + 200, (small, words)


@pytest.mark.parametrize('verb,text,field,want', [('close', '--comment Zork', 'text', 'Zork'),
                                                  ('reopen', '-c Zork', 'text', 'Zork'),
                                                  ('merge', '--subject Zork', 'title', 'Zork')])
def test_each_copy_keeps_the_text_it_posts(verb, text, field, want):
    c = gate.classify(action(3, verb, text))
    copies = [p for p in c.parts if p.alt_copy]
    assert len(copies) == 3 and all(getattr(p, field) == want for p in [c, *copies])


def test_all_targets_and_posting_checks_are_retained():
    c = gate.classify(action(32))
    targets = {p.target for r in gate.readings(c) for p in gate._each_part(r)}
    assert all(f'ghe.corp/o/r#{i}' in targets for i in range(32))
    d = gate.decide(c, gate.normalize_policies({'gates': {'deny': ['target:ghe.corp/o/r#31']},
                                               'posting': {'footer': 'REQUIRED'}}))
    assert d.outcome == 'deny' and d.violations


SCOPE = {'scopes': {'internal': ['myorg/*']}}
POLICIES = [
    {'gates': SCOPE},
    {'gates': {**SCOPE, 'deny': ['target:ghe.corp/o/r#9']}},
    {'gates': {**SCOPE, 'reserved': ['target:ghe.corp/*']}},
    {'gates': {**SCOPE, 'rules': [{'match': {'target': 'ghe.corp/*'}, 'decision': 'allow'}]}},
    {'gates': {**SCOPE, 'rules': [{'match': {'target': 'pub/*'}, 'decision': 'deny'}]}},
    {'gates': {**SCOPE, 'rules': [{'match': {'command': '^gh pr'}, 'decision': 'allow'}]}},
    {'gates': {**SCOPE, 'categories': {'github_post': 'allow'}}},
    {'gates': SCOPE, 'posting': {'footer': 'REQUIRED', 'forbid_words': ['Zork']}},
    {'gates': SCOPE, 'posting': {'forbid_words': ['Zork']},
     'scrub': {'enabled': True, 'exposures': ['public'], 'rules': [{'label': 'home', 'pattern': '/home/[a-z]+'}]}},
    {'gates': {'scopes': {'private': ['myorg/*']}},
     'scrub': {'enabled': True, 'exposures': ['public', 'private'],
               'rules': [{'label': 'home', 'pattern': '/home/[a-z]+'}]}},
]
URLS = ' '.join(f'https://ghe.corp/o/r/pull/{i}' for i in range(2, 12))
LINES = [
    f'gh pr comment https://github.com/myorg/app/pull/1 {URLS} -R pub/repo -b "Zork see /home/bob"',
    f'gh pr comment 1 -R myorg/app https://github.com/pub/r/pull/2 {URLS} -b "/home/bob Zork"',
    f'true && gh pr close {URLS} --comment Zork',
    f'gh pr comment {URLS} -b x; gh issue comment 4 -R pub/repo -b "/home/bob"',
    f'ssh h gh pr merge {URLS} --subject Zork',
]


@pytest.mark.parametrize('line', LINES)
@pytest.mark.parametrize('n', range(len(POLICIES)))
def test_past_eight_other_targets_each_decides_as_with_every_part(monkeypatch, line, n):
    """Past eight other targets the line on most of them is decided without the copies of the action (#550). Each
    copy is a part on its own target, which the line on its own target decides, and the first other target of each
    exposure keeps the copies, so the decision is the one every part gives: the same outcome, exposure, third party
    and reserved hits, and every finding as often as before."""
    c = gate.classify({'tool': 'bash', 'args': {'command': line}})
    assert len(c.alt_targets) > gate._ALTS_IN_FULL
    pol = gate.normalize_policies(POLICIES[n])
    lean = gate.decide(c, pol)
    monkeypatch.setattr(gate, '_ALTS_IN_FULL', 10 ** 6)
    full = gate.decide(c, pol)
    assert (lean.outcome, lean.exposure, lean.third_party) == (full.outcome, full.exposure, full.third_party)
    assert sorted(lean.reserved) == sorted(full.reserved)
    assert not Counter(full.violations) - Counter(lean.violations), (lean.violations, full.violations)
