"""The sustained-change rule and the value step back as pure functions (D-044 wave 2, TASTE-BASELINE sections 3, 4).

Every number in the two worked examples is checked here. Days count from install, a Monday (day 0); a week's
values are taken at its close, day 7k - 1.
"""
import random
from datetime import date, timedelta

import pytest

from exo.baseline import shift as S

INSTALL = date(2026, 1, 5)          # a Monday
B_CLAIM = "Write answers as short paragraphs; no bullet lists unless asked."
BULLETS = "The user wants answers as bullet lists."


def ts(day: int, hour: int = 10) -> str:
    return (INSTALL + timedelta(days=day)).isoformat() + f"T{hour:02d}:00:00Z"


def con(day, session=None, hour=10, item="B", delta=0.8, strength=0.3, **kw):
    return S.Signal(item, ts(day, hour), "contradict", delta, strength, session or f"s{day}", claim=BULLETS, **kw)


def agr(day, item="B", session=None):
    return S.Signal(item, ts(day), "agree", 0.0, 0.3, session or f"s{day}")


def house(item="B", claim=B_CLAIM, cls="method", source="house", guarded=False):
    return S.Subject(item, claim, cls, source, guarded, innate=lambda d: S.precedence((d - INSTALL).days))


def at_week(sub, sigs, k, **kw):
    """The replay as seen on the Monday after week k closes."""
    kw.setdefault("active_weeks", {0})
    return S.replay(sub, sigs, origin=INSTALL, today=INSTALL + timedelta(days=7 * k), **kw)


# Sam's rewrites (section 3, worked example). Week 2: three rewrites in one session on day 7 (one episode) and
# one more on day 10. Week 4: paragraphs himself on day 23, rewrites on days 21 and 25. Week 7: holiday.
# Week 8: paragraphs on days 50 and 52. The days inside each week are the ones that give the table's numbers.
SAM = [con(7, "a", 9), con(7, "a", 10), con(7, "a", 11), con(10), con(15), con(21), agr(23), con(25), con(28),
       con(37), con(41), agr(50), agr(52), con(56), con(63), con(67), con(70), con(77), con(82)]

#        week: (S,  n,    B weight, Sam's version, status)
TABLE = {1: (0, None, 0.69, 0.0, "active"),
         2: (1, None, 0.68, 0.47, "active"),
         3: (2, None, 0.67, 0.57, "active"),
         4: (3, 0, 0.67, 0.74, "armed"),
         5: (4, 1, 0.57, 0.76, "armed"),
         6: (5, 2, 0.49, 0.84, "armed"),
         7: (5, 2, 0.48, 0.79, "armed"),
         8: (4, 1, 0.55, 0.73, "armed"),
         9: (5, 2, 0.47, 0.75, "armed"),
         10: (6, 3, 0.41, 0.84, "armed"),
         11: (7, 4, 0.35, 0.84, "armed")}


@pytest.mark.parametrize("week", sorted(TABLE))
def test_method_worked_example_week_by_week(week):
    s, n, w, c, status = TABLE[week]
    t = at_week(house(), SAM, week)
    assert (t.s, t.status) == (s, status)
    if n is not None:
        assert t.n == n
    assert round(t.weight, 2) == w
    assert round(t.challenger, 2) == c
    assert not t.outcomes


def test_method_worked_example_swaps_at_day_83_in_silent_mode():
    t = at_week(house(), SAM, 12)
    [o] = t.outcomes
    assert o.kind == "swapped" and o.week == 11 and o.at.startswith((INSTALL + timedelta(days=83)).isoformat())
    assert round(o.data["weight"], 3) == 0.299                 # below 0.30 ...
    assert round(o.data["challenger"], 2) == 0.89               # ... and 0.89 leads by more than 0.15
    assert o.data["claim"] == BULLETS and len(o.data["evidence"]) == 14   # one row per episode
    assert t.status == "swapped" and t.quiet_until == INSTALL + timedelta(days=83 + 90)


def test_method_worked_example_in_ask_mode_proposes_once_and_keeps_the_arming_weight():
    ask = dict(mode_at=lambda _: "ask")
    t = at_week(house(), SAM, 12, **ask)
    assert [o.kind for o in t.outcomes] == ["proposed"] and t.proposal["kind"] == "swap"
    assert t.status == "armed" and round(t.prompt_weight, 2) == 0.67    # the weight it had when it armed
    later = SAM + [con(84), con(91)]
    t2 = at_week(house(), later, 14, **ask)
    assert [o.kind for o in t2.outcomes] == ["proposed"]                # asked once, never again
    yes = [S.Act(ts(86), "yes", "B")]
    t3 = at_week(house(), later, 14, acts=yes, **ask)
    assert [o.kind for o in t3.outcomes] == ["proposed", "swapped"] and t3.outcomes[1].at == ts(86)
    no = [S.Act(ts(86), "no", "B")]
    t4 = at_week(house(), later + [con(98), con(105), con(112)], 17, acts=no, **ask)
    assert [o.kind for o in t4.outcomes] == ["proposed"] and t4.s == 0  # cleared, then 90 days quiet


def test_the_precedence_numbers_the_table_uses():
    assert round(S.precedence(70), 2) == 0.61
    assert round(S.effective_weight(S.precedence(83), 5, 0.8), 3) == 0.299
    for d, net in ((1.0, 5), (0.75, 6), (0.5, 9), (0.25, 17)):          # "How long a swap takes"
        w = [S.effective_weight(0.61, n, d) for n in range(net + 1)]
        assert w[-1] < 0.3 <= w[-2]


def test_five_signals_in_one_day_are_one_episode():
    sigs = [con(3, f"s{i}", hour=8 + i) for i in range(5)]
    [e] = S.episodes(sigs)
    assert e.verdict == "contradict" and len(e.signals) == 5 and e.strength == 0.3


def test_signals_in_one_session_across_midnight_are_one_episode():
    sigs = [con(3, "late", hour=23), S.Signal("B", ts(4, 1), "contradict", 0.8, 0.6, "late")]
    [e] = S.episodes(sigs)
    assert e.strength == 0.6 and e.delta == pytest.approx(0.8)


def test_episode_majorities_and_void():
    e = S.episodes([con(3, "x"), agr(3, session="x")])[0]
    assert e.verdict == "void"
    e = S.episodes([con(3, "x"), con(3, "x", hour=11), agr(3, session="x")])[0]
    assert e.verdict == "contradict"


def test_a_mood_week_never_arms_and_leaves_no_residue():
    mood = [con(8, hour=h) for h in (9, 10, 11)] + [con(9)]            # five rewrites over two days
    t = at_week(house(), mood + [agr(15)], 3, active_weeks={0, 1, 2})
    assert t.s == 0 and t.status == "active" and not t.record and not t.outcomes
    clean = at_week(house(), [agr(15)], 3, active_weeks={0, 1, 2})
    assert t.weight == clean.weight and t.challenger == clean.challenger == 0


def test_a_swing_arms_and_disarms_with_no_residue():
    swing = [con(7), con(14), con(21), agr(28), agr(35)]                # weeks 2 to 4, then back, weeks 5 and 6
    armed = at_week(house(), swing, 4)
    assert armed.status == "armed" and round(armed.weight, 2) == 0.67   # armed in week 4, never faded
    t = at_week(house(), swing, 6)
    assert t.status == "active" and t.s == 0 and not t.record and t.challenger == 0 and not t.outcomes
    later = at_week(house(), swing + [con(42), con(49)], 8)
    assert later.s == 2 and later.status == "active"                    # starts again from S = 0


def test_disarm_after_four_quiet_active_weeks():
    sigs = [con(7), con(14), con(21), con(28), con(35)]                 # S = 5, armed
    t = at_week(house(), sigs, 10, active_weeks=set(range(10)))
    assert t.status == "active" and t.s == 0


def test_a_skipped_week_neither_fades_nor_unfades():
    base = [con(7), con(14), con(21), con(28)]
    a = at_week(house(), base, 5)
    b = at_week(house(), base, 6)                                       # week 6: no session at all
    assert (a.s, a.n) == (b.s, b.n) == (4, 1)
    assert b.weight == pytest.approx(S.effective_weight(S.precedence(41), 1, 0.8))


def test_an_agreeing_week_unfades():
    base = [con(7), con(14), con(21), con(28), con(35)]
    a = at_week(house(), base, 6)
    b = at_week(house(), base + [agr(42)], 7)
    assert (a.n, b.n) == (2, 1) and b.weight > a.weight


def test_a_project_bound_pattern_becomes_a_scoped_item_and_leaves_the_house_item_alone():
    grant = [S.Signal("B", s.ts, s.relation, s.delta, s.strength, s.session, project="grant-report", claim=BULLETS)
             for s in SAM if s.relation == "contradict" and s.ts < ts(26)]          # weeks 2 to 4
    t = at_week(house(), grant, 4)
    [o] = t.outcomes
    assert o.kind == "scoped" and o.data["scope"] == "project:grant-report" and o.week == 3   # from week 4
    assert t.status == "active" and t.weight == pytest.approx(S.precedence(28))           # B never faded
    ask = at_week(house(), grant, 4, mode_at=lambda _: "ask")
    assert [o.kind for o in ask.outcomes] == ["proposed"] and ask.outcomes[0].data["change"] == "scope"


def test_a_period_pattern_makes_nothing():
    deadline = [con(d, scope="period") for d in (7, 8, 14, 15, 21, 22, 28, 35, 42, 49, 56, 63, 70, 77, 84)]
    t = at_week(house(), deadline, 13)
    assert t.s == 0 and not t.outcomes and not S.episodes(deadline)


def test_low_confidence_quoted_and_unrelated_signals_do_not_count():
    sigs = [con(d, confidence=0.5) for d in (7, 14, 21)] + [con(d, voice="quoting") for d in (8, 15, 22)]
    sigs += [S.Signal("B", ts(d), "unrelated", 1.0, 0.8, f"s{d}") for d in (9, 16, 23)]
    assert at_week(house(), sigs, 4).s == 0


def test_a_ruling_never_fades_and_asks_once_when_the_pattern_would_arm():
    sub = house(item="R", claim="Always use tables for comparisons.", source="ruling")
    sigs = [con(d, item="R") for d in (7, 14, 21, 28, 35, 42)]
    t = at_week(sub, sigs, 7)
    assert [o.kind for o in t.outcomes] == ["question"] and t.outcomes[0].data["why"] == "ruling"
    assert t.status == "active" and t.weight == pytest.approx(S.precedence(49))         # never faded


def test_a_guarded_item_raises_a_question_instead_of_arming():
    t = at_week(house(item="G", guarded=True), [con(d, item="G") for d in (7, 14, 21)], 4)
    assert [o.data["why"] for o in t.outcomes] == ["guarded"] and t.status == "active"


def test_a_pinned_item_never_arms():
    t = at_week(house(), SAM, 12, acts=[S.Act(ts(1), "pin", "B")])
    assert t.status == "active" and not t.outcomes and t.s == 0


def test_undo_restores_and_starts_the_90_day_quiet_period():
    acts = [S.Act(ts(85), "undo", "B")]
    more = SAM + [con(d) for d in range(91, 200, 7)]
    t = at_week(house(), more, 25, acts=acts)
    assert [o.kind for o in t.outcomes] == ["swapped"] and t.status == "active"
    assert t.quiet_until == INSTALL + timedelta(days=85 + 90) and t.s <= 3              # nothing counted till then


def test_two_swaps_within_a_year_stop_automatic_changes_and_ask_once():
    first = [con(d) for d in range(7, 7 * 13, 7)] + [con(d) for d in range(8, 7 * 13, 7)]
    t1 = at_week(house(), first, 13)
    swap1 = next(o for o in t1.outcomes if o.kind == "swapped")
    undo = S.Act(ts((date.fromisoformat(swap1.at[:10]) - INSTALL).days + 1), "undo", "B")
    second = first + [con(d) for d in range(200, 300, 7)] + [con(d) for d in range(201, 300, 7)]
    t2 = at_week(house(), second, 44, acts=[undo])
    kinds = [o.kind for o in t2.outcomes]
    assert kinds == ["swapped", "swapped", "question"] and t2.stopped
    assert t2.outcomes[-1].data["why"] == "flips"


# --- values ------------------------------------------------------------------------

V_CLAIM = "Push back when a plan looks wrong, even when the user seems sure."
SAMS_VIEW = "Once the user has decided, note a risk once and then help; do not argue."


def vsig(day, delta=0.5, conf=0.8, stated=False, voice=None, strength=0.3):
    return S.Signal("V", ts(day), "contradict", delta, strength, f"s{day}", voice=voice, confidence=conf,
                    stated=stated, claim=SAMS_VIEW if stated else "")


SAM_V = [vsig(5, 0.75, 0.9, True, "own", 0.6),          # day 5: "Stop arguing with me about decisions I've made."
         vsig(9, 0.75, 0.85, True, "own", 0.6),         # week 2: repeats the view in his own words
         vsig(16), vsig(23)]                            # weeks 3, 4: waves off a risk note


def value(source="house"):
    return S.Subject("V", V_CLAIM, "value", source, innate=lambda d: 0.5)


def vat(day, sigs=SAM_V, mode="silent", window=True, **kw):
    return S.replay(value(kw.pop("source", "house")), sigs, origin=INSTALL, today=INSTALL + timedelta(days=day),
                    mode_at=lambda _: mode, window_at=lambda _: window, **kw)


@pytest.mark.parametrize("mode", ["silent", "ask"])
def test_value_worked_example(mode):
    t = vat(5, mode=mode)
    [o] = t.outcomes
    assert o.kind == "stepped_back" and o.at == ts(5) and "inside the getting-to-know window" in o.data["why"]
    assert t.status == "stepped_back"                   # at once, with no question, in either mode
    assert vat(7, mode=mode).s == 1                     # week 1 close: S = 1
    assert vat(21, mode=mode).s == 3
    assert [o.kind for o in vat(27, mode=mode).outcomes] == ["stepped_back"]   # not before the week 4 close
    t = vat(28, mode=mode)
    kinds = [o.kind for o in t.outcomes]
    week4_close = (INSTALL + timedelta(days=27)).isoformat()
    if mode == "silent":
        assert kinds == ["stepped_back", "adopted"] and t.outcomes[1].at.startswith(week4_close)
        assert t.outcomes[1].data["claim"] == SAMS_VIEW and t.status == "adopted"
    else:
        assert kinds == ["stepped_back", "proposed"] and t.outcomes[1].data["change"] == "adopt"
        assert t.status == "stepped_back" and t.proposal["kind"] == "adopt"


def test_a_stepped_back_value_with_nothing_more_stays_neutral():
    t = vat(120, sigs=SAM_V[:1])
    assert t.status == "stepped_back" and [o.kind for o in t.outcomes] == ["stepped_back"]


def test_behaviour_alone_never_adopts_a_value():
    acts_only = [vsig(d, 0.75, 0.9) for d in (5, 12, 19, 26, 33, 40, 47)]
    t = vat(60, sigs=acts_only)
    assert t.status == "stepped_back" and t.s >= 4 and "adopted" not in [o.kind for o in t.outcomes]


def test_after_the_window_one_medium_signal_does_not_step_back_but_a_strong_one_does():
    assert vat(40, sigs=[vsig(35, 0.5, 0.9)], window=False).status == "active"
    assert vat(40, sigs=[vsig(35, 0.75, 0.7)], window=False).status == "active"     # opposite at 0.7: no
    assert vat(40, sigs=[vsig(35, 0.75, 0.8)], window=False).status == "stepped_back"
    assert vat(40, sigs=[vsig(35, 0.5, 0.6, True, "own")], window=False).status == "stepped_back"


def test_two_contradicting_weeks_step_a_value_back_even_without_a_model():
    sigs = [vsig(40, 0.5, None), vsig(47, 0.5, None)]
    assert vat(46, sigs=sigs, window=False).status == "active"
    t = vat(56, sigs=sigs, window=False)
    assert t.status == "stepped_back" and "2 contradicting weeks" in t.outcomes[0].data["why"]


def test_without_a_decision_model_a_value_never_steps_back_on_one_signal():
    assert vat(10, sigs=[vsig(5, 1.0, None)]).status == "active"


def test_a_quoted_or_asked_view_never_steps_a_value_back():
    for voice in ("quoting", "asking", "role-playing"):
        t = vat(60, sigs=[vsig(d, 1.0, 0.95, True, voice) for d in (5, 12, 19, 26, 33)])
        assert t.status == "active" and not t.outcomes


def test_small_value_deltas_are_refinements():
    t = vat(60, sigs=[vsig(d, 0.25, 0.95) for d in (5, 12, 19, 26, 33)])
    assert t.status == "active" and t.s == 0


def test_a_stepped_back_value_is_restored_after_two_net_agreeing_weeks():
    back = SAM_V[:1] + [S.Signal("V", ts(d), "agree", 0.0, 0.3, f"s{d}") for d in (14, 21)]
    t = vat(28, sigs=back)
    assert [o.kind for o in t.outcomes] == ["stepped_back", "restored"] and t.status == "active"
    t = vat(28, sigs=back, mode="ask")
    assert [o.kind for o in t.outcomes] == ["stepped_back", "proposed"] and t.proposal["kind"] == "restore"


def test_a_learned_value_has_no_window_and_a_ruling_value_never_steps_back():
    assert vat(10, sigs=[vsig(5, 0.5, 0.9)], source="learned").status == "active"
    assert vat(10, sigs=[vsig(5, 1.0, 0.95, True, "own")], source="ruling").status == "active"


# --- properties ------------------------------------------------------------------------

def _random_signals(rng: random.Random, n: int, days: int, item="B") -> list[S.Signal]:
    out = []
    for _ in range(n):
        d = rng.randrange(days)
        rel = rng.choice(("contradict", "agree", "refine", "unrelated"))
        out.append(S.Signal(item, ts(d, rng.randrange(24)), rel, rng.choice(S.DELTAS), rng.choice((0.3, 0.6, 0.8)),
                            rng.choice(("a", "b", None, f"s{d}")), rng.choice(S.SCOPES),
                            rng.choice((None, "p1")), rng.choice((None, "code")),
                            rng.choice((None, "own", "quoting")), rng.choice((None, 0.5, 0.7, 0.9)),
                            rng.random() < 0.3, "x"))
    return out


@pytest.mark.parametrize("seed", range(40))
def test_property_a_mood_inside_one_week_followed_by_the_house_way_leaves_no_residue(seed):
    rng = random.Random(seed)
    week = rng.randrange(1, 20)
    burst = [S.Signal("B", ts(7 * week + rng.randrange(7), rng.randrange(24)), "contradict",
                      rng.choice((0.5, 0.75, 1.0)), rng.choice((0.3, 0.6, 0.8)), rng.choice(("a", None)))
             for _ in range(rng.randrange(1, 9))]
    back = [agr(7 * (week + 1) + rng.randrange(7))]
    for cls in ("method", "value"):
        sub = S.Subject("B", B_CLAIM, cls, "learned", innate=lambda d: S.precedence((d - INSTALL).days))
        kw = dict(origin=INSTALL, today=INSTALL + timedelta(days=7 * (week + 3)), active_weeks=set(range(week + 3)))
        with_mood = S.replay(sub, burst + back, **kw)
        without = S.replay(sub, back, **kw)
        if cls == "method":
            assert with_mood.outcomes == []
        assert (with_mood.s, with_mood.record, with_mood.challenger, with_mood.weight) == \
               (without.s, without.record, without.challenger, without.weight)
        assert with_mood.status in ("active", "stepped_back")            # a value may stay neutral, never adopt
        assert all(o.kind == "stepped_back" for o in with_mood.outcomes)


@pytest.mark.parametrize("seed", range(20))
def test_property_signals_about_other_items_move_nothing(seed):
    rng = random.Random(seed)
    noise = _random_signals(rng, 60, 200, item="other")
    t = S.replay(house(), noise, origin=INSTALL, today=INSTALL + timedelta(days=210), active_weeks=set(range(30)))
    assert t.s == 0 and not t.outcomes and t.status == "active"
