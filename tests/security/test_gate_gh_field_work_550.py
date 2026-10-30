"""One parse of invariant gh text fields per target-reading set (#550, classification slice)."""
from exo import gate


def action(count):
    return {'tool': 'bash', 'args': {'command': 'gh pr comment ' + ' '.join(
        f'https://ghe.corp/o/r/pull/{i}' for i in range(count)) + ' -b synthetic-body'}}


def test_text_option_scan_work_grows_with_input_not_alt_target_product(monkeypatch):
    original = gate._opt
    words = 0

    def counted(argv, *names):
        nonlocal words
        words += len(argv)
        return original(argv, *names)

    monkeypatch.setattr(gate, '_opt', counted)
    gate.classify(action(8))
    small, words = words, 0
    gate.classify(action(32))
    assert words <= 6 * small + 200, (small, words)


def test_all_targets_and_posting_checks_are_retained():
    c = gate.classify(action(32))
    targets = {p.target for r in gate.readings(c) for p in gate._each_part(r)}
    assert all(f'ghe.corp/o/r#{i}' in targets for i in range(32))
    d = gate.decide(c, gate.normalize_policies({'gates': {'deny': ['target:ghe.corp/o/r#31']},
                                               'posting': {'footer': 'REQUIRED'}}))
    assert d.outcome == 'deny' and d.violations
