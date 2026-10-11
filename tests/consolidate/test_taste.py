import math

import pytest

from exo import taste
from exo.taste import Belief, Evidence


def test_decay_factor_halves_each_half_life():
    assert taste.decay_factor(0, 30) == 1.0
    assert taste.decay_factor(30, 30) == pytest.approx(0.5)
    assert taste.decay_factor(60, 30) == pytest.approx(0.25)
    assert taste.decay_factor(90, None) == 1.0    # decay off
    assert taste.decay_factor(90, 0) == 1.0


def test_decayed_rejects_bad_strength():
    with pytest.raises(ValueError):
        taste.decayed(1.5, 1, 30)


def test_combine_is_noisy_or_and_bounded():
    assert taste.combine([], 30) == 0.0
    assert taste.combine([(0.6, 0)], 30) == pytest.approx(0.6)
    assert taste.combine([(0.6, 0), (0.6, 0)], 30) == pytest.approx(1 - 0.4 * 0.4)
    many = taste.combine([(0.9, 0)] * 50, 30)
    assert 0.99 < many <= 1.0
    # One old piece of evidence: 0.8 * 2^(-60/30) = 0.2
    assert taste.combine([(0.8, 60)], 30) == pytest.approx(0.2)


def test_belief_weight_decays_unless_reinforced():
    b = Belief("p-1", frozenset({"+table"}), "preference", "c", "2026-01-01T00:00:00Z",
               [Evidence("e1", "2026-01-01T00:00:00Z", 0.6)])
    fresh = b.weight("2026-01-01T00:00:00Z")
    later = b.weight("2026-03-02T00:00:00Z")                 # 60 days, two half-lives
    assert fresh == pytest.approx(0.6)
    assert later == pytest.approx(0.15)
    b.evidence.append(Evidence("e2", "2026-03-01T00:00:00Z", 0.6))
    reinforced = b.weight("2026-03-02T00:00:00Z")
    assert reinforced > later and reinforced > 0.6 * math.pow(2, -1 / 30)
    assert taste.label(fresh) == "medium" and taste.label(later) == "low"


def test_rules_do_not_decay_by_default():
    b = Belief("p-2", frozenset({"-merge"}), "rule", "c", "2026-01-01T00:00:00Z",
               [Evidence("e1", "2026-01-01T00:00:00Z", 0.9)])
    assert b.weight("2027-01-01T00:00:00Z") == pytest.approx(0.9)
    assert b.weight("2027-01-01T00:00:00Z", {"rule": 30}) < 0.01


def test_signature_relations():
    a = taste.signature("use tables", "bullet lists")
    b = taste.signature("bullet lists", "tables")
    c = taste.signature("use tables in replies", "bullets")
    assert taste.relation(a, b) == "conflict"
    assert taste.relation(a, c) == "agree"
    assert taste.relation(a, taste.signature("deploy on fridays", None)) is None
    never = taste.signature(None, "sign external comments with agent names")
    always = taste.signature("sign external comments with agent names", None)
    assert taste.relation(never, always) == "conflict"


def test_conflict_rule():
    old = Belief("p", frozenset(), "rule", "c", "t", guarded=False)
    assert taste.resolve_conflict(old) == "newer"
    old.guarded = True
    assert taste.resolve_conflict(old) == "ask"
    assert taste.resolve_conflict(old, "newer_wins") == "newer"
    assert taste.resolve_conflict(Belief("p", frozenset(), "rule", "c", "t"), "ask") == "ask"
    with pytest.raises(ValueError):
        taste.resolve_conflict(old, "coin_flip")
    assert taste.guards("never post public comments")
    assert not taste.guards("use tables in replies")
