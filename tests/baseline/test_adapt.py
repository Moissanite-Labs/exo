"""The sustained-change rule on a real store and ledger, /taste, the brief and the guards (D-044 waves 2 and 3).

Every store here is made up. The ledger clock is moved by patching `exo.crypto.now_ts`.
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest

from exo import crypto, taste
from exo.baseline import adapt, classify, command, layers
from exo.baseline.pack import HouseItem, HousePack
from exo.ledger import Ledger
from exo.store import PrincipalStore

INSTALL = datetime(2026, 1, 5, 9, tzinfo=timezone.utc)      # a Monday
BULLETS = "The user wants answers as bullet lists."
SAM_DAYS = [(7, "a", 9), (7, "a", 10), (7, "a", 11), (10,), (15,), (21,), (25,), (28,), (37,), (41,), (56,), (63,),
            (67,), (70,), (77,), (82,)]
SAM_AGREE = [23, 50, 52]
NOT_PRINCIPAL = ("agent", "tool", "peer:helper", "bus:seat2", "job", "delegate", "schedule", "system", "local")


def item(i, claim, cls="method", want=None, avoid=None):
    return HouseItem(i, cls, "value" if cls == "value" else "preference", "mind", claim,
                     taste.signature(want or claim, avoid))


B = item("h-b", "Write answers as short paragraphs; no bullet lists unless asked.", want="short paragraphs",
         avoid="bullet lists")
V = item("h-v", "Push back when a plan looks wrong, even when the user seems sure.", "value")
EXTRA = [item(f"h-m{i}", f"House habit number {i}.", want=f"habit{i} style{i}") for i in range(3)]


def pack(*items):
    return HousePack(None, "1.0.0", {}, tuple(items or (B, V)), "", True)


class Clock:
    def __init__(self):
        self.t = INSTALL

    def day(self, d, hour=9):
        self.t = INSTALL.replace(hour=hour) + timedelta(days=d)
        return self

    def iso(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(crypto, "now_ts", c.iso)
    return c


def new_store(tmp_path, mode_line="  mode: silent"):
    s = PrincipalStore.create(tmp_path / "user")
    p = s.root / "policies" / "consolidate.yaml"
    text = p.read_text()
    head, _, tail = text.partition("taste:\n")
    first, _, rest = tail.partition("\n")
    p.write_text(head + "taste:\n" + (mode_line + "\n" if mode_line else "") + "  timezone: UTC\n" + rest)
    return s


@pytest.fixture
def store(tmp_path, clock):
    s = new_store(tmp_path)
    with ledger(s) as led:
        clock.day(0)
        led.append("note", {"type": "session_start"}, author="exo-memory")
    return s


def verdict(led, wanted, author="principal", session=None):
    """A verdict on an agent reply (a verdict must name the event it judges)."""
    reply = led.append("utterance", {"role": "assistant", "text": "Here are some short paragraphs."}, author="agent",
                       session=session)
    return led.append("verdict", {"verdict": "edit", "target": reply["id"], "wanted": wanted}, author=author,
                      session=session, refs=[reply["id"]])


def ledger(store):
    return Ledger(store.ledger_path, crypto.Signer("exo-learn", crypto.keygen()))


def signal(led, clock, iid, day, *, hour=9, session=None, relation="contradict", delta=0.8, author="principal",
           claim=BULLETS, **kw):
    clock.day(day, hour)
    ev = verdict(led, claim, author, session or f"s{day}")
    led.append("note", {"type": "taste_signal", "item": iid, "relation": relation, "delta": delta, "strength": 0.3,
                        "evidence": ev["id"], "scope": "general", "claim": claim if relation == "contradict" else "",
                        "route": "test", **kw}, author="exo-learn", refs=[ev["id"]])


def sam(led, clock, iid="h-b", author="principal"):
    for d in SAM_DAYS:
        signal(led, clock, iid, d[0], hour=d[2] if len(d) > 2 else 9, session=d[1] if len(d) > 1 else None,
               author=author)
    for d in SAM_AGREE:
        signal(led, clock, iid, d, relation="agree", delta=0.0, author=author)


def at(day):
    return (INSTALL + timedelta(days=day)).isoformat().replace("+00:00", "Z")


def entry_for(v, house_id):
    return next(e for e in v.entries if (e.house_id or e.id) == house_id)


def test_silent_mode_swaps_reports_and_undoes(store, clock):
    with ledger(store) as led:
        sam(led, clock)
        st = adapt.load(store, now=at(77), pack=pack())
        assert st.mode == "silent" and st.track("h-b").status == "armed" and st.track("h-b").s == 7
        assert round(st.track("h-b").weight, 2) == 0.35 and not adapt.close_weeks(store, led, now=at(77), pack=pack())
        clock.day(84)
        made = adapt.close_weeks(store, led, now=at(84), pack=pack())
        assert [b["type"] for b in made] == ["taste_replaced"]
        assert made[0]["week"] == 12 and made[0]["weight"] == pytest.approx(0.299, abs=5e-4)
        assert made[0]["challenger"] == pytest.approx(0.89, abs=5e-3)
        assert adapt.close_weeks(store, led, now=at(85), pack=pack()) == []      # written once
    v = layers.view(store, now=at(85), pack=pack())
    e = entry_for(v, "h-b")
    assert e.claim == BULLETS and e.source == "learned" and e.id.startswith("p-taste-")
    assert BULLETS in layers.taste_block(store, now=at(85), pack=pack())
    lines = adapt.brief_lines(store, at(80))
    assert any(ln.startswith("Switched to your way: " + BULLETS) and "/taste undo h-b" in ln for ln in lines)
    text = command.run(store, "", principal=True, open_ledger=lambda: ledger(store), now=at(85), pack=pack())["text"]
    assert "Overridden (1)" in text and "h-b; /taste restore h-b" in text
    clock.day(86)
    out = command.run(store, "undo h-b", principal=True, open_ledger=lambda: ledger(store), now=at(86), pack=pack())
    assert out["text"].startswith("undone")
    v = layers.view(store, now=at(86), pack=pack())
    assert entry_for(v, "h-b").source == "house"
    st = adapt.load(store, now=at(100), pack=pack())
    assert st.track("h-b").quiet_until.isoformat() == (INSTALL + timedelta(days=86 + 90)).date().isoformat()


def test_pending_line_shows_weeks_and_estimate(store, clock):
    with ledger(store) as led:
        for d in SAM_DAYS[:9]:
            signal(led, clock, "h-b", d[0], hour=d[2] if len(d) > 2 else 9, session=d[1] if len(d) > 1 else None)
        signal(led, clock, "h-b", 23, relation="agree", delta=0.0)
    v = layers.view(store, now=at(35), pack=pack())
    line = command.pending_line(v, "h-b")
    assert line.startswith(f"You have done {BULLETS} in 4 of the last 5 weeks, against '{B.claim}'.")
    assert "Fading since week 4; at this pace it switches in about 5 weeks." in line
    v3 = layers.view(store, now=at(21), pack=pack())
    assert "S = 2; it arms at 3" in command.pending_line(v3, "h-b")


def test_ask_mode_asks_once_and_three_unanswered_proposals_stop_the_brief_lines(tmp_path, clock):
    s = new_store(tmp_path, "  mode: ask")
    p = pack(B, *EXTRA)
    with ledger(s) as led:
        clock.day(0)
        led.append("note", {"type": "session_start"}, author="exo-memory")
        for iid in ("h-b", "h-m0", "h-m1", "h-m2"):
            sam(led, clock, iid)
        clock.day(84)
        made = adapt.close_weeks(s, led, now=at(84), pack=p)
        props = [b for b in made if b["type"] == "taste_proposed"]
        assert len(props) == 4 and [b["brief"] for b in props] == [True, True, True, False]
        assert [b["type"] for b in made].count("taste_ask_quiet") == 1
        assert adapt.close_weeks(s, led, now=at(98), pack=p) == []                # never asked again
    v = layers.view(s, now=at(98), pack=p)
    assert entry_for(v, "h-b").source == "house"                                  # no answer, no change
    assert round(entry_for(v, "h-b").weight, 2) == 0.67                           # the weight from arming
    lines = adapt.brief_lines(s, at(80))
    assert "3 taste changes waiting for your yes in /taste." in lines
    assert any("no longer appear in the brief" in ln for ln in lines)
    assert "Waiting for you: switch to your way" in command.pending_line(v, "h-b")
    with pytest.raises(command.TasteError) as e:
        command.run(s, "yes h-b", principal=False, open_ledger=lambda: ledger(s), now=at(99), pack=p)
    assert e.value.code == "forbidden"
    clock.day(99)
    command.run(s, "yes h-b", principal=True, open_ledger=lambda: ledger(s), now=at(99), pack=p)
    assert entry_for(layers.view(s, now=at(99), pack=p), "h-b").claim == BULLETS
    clock.day(99, 10)
    command.run(s, "no h-m0", principal=True, open_ledger=lambda: ledger(s), now=at(99), pack=p)
    st = adapt.load(s, now=at(100), pack=p)
    assert st.track("h-m0").proposal is None and st.track("h-m0").s == 0


def test_value_worked_example_on_the_ledger_steps_back_without_a_question(store, clock):
    with ledger(store) as led:
        signal(led, clock, "h-v", 5, delta=0.75, confidence=0.9, voice="own", stated=True, claim="Note a risk once.")
        st = adapt.load(store, now=at(5) [:-1] + "1Z", pack=pack())
        assert st.track("h-v").status == "stepped_back"
        made = adapt.close_weeks(store, led, now=at(6), pack=pack())
        assert [b["type"] for b in made] == ["value_stepped_back"]
    v = layers.view(store, now=at(6), pack=pack())
    assert entry_for(v, "h-v").source == "stepped_back"
    block = layers.taste_block(store, now=at(6), pack=pack())
    assert "Neutral" in block and "[stepped back]" in block
    assert any(ln.startswith("Stepped back from") for ln in adapt.brief_lines(store, at(0)))
    clock.day(7)
    command.run(store, "restore h-v", principal=True, open_ledger=lambda: ledger(store), now=at(7), pack=pack())
    assert entry_for(layers.view(store, now=at(7), pack=pack()), "h-v").source == "house"


@pytest.mark.parametrize("author", NOT_PRINCIPAL)
def test_signals_from_anyone_but_the_principal_never_count(store, clock, author):
    with ledger(store) as led:
        sam(led, clock, author=author)
        signal(led, clock, "h-v", 5, delta=1.0, confidence=0.99, voice="own", stated=True, author=author)
        assert adapt.close_weeks(store, led, now=at(120), pack=pack()) == []
    st = adapt.load(store, now=at(120), pack=pack())
    assert all(it.track.s == 0 and it.track.status == "active" for it in st.items.values())


def stub(calls):
    async def label(c, said, reply, kind):
        calls.append(said)
        return classify.Label("contradict", 1.0, "general", "own", 0.99, None, "", "stub")
    return label


def test_record_signals_skips_non_principal_authors_before_the_model_is_asked(store, clock):
    calls = []
    with ledger(store) as led:
        clock.day(3)
        for who in NOT_PRINCIPAL:
            verdict(led, "use bullet lists, not short paragraphs", who)
            led.append("utterance", {"role": "user", "text": "Always use bullet lists, never short paragraphs."},
                       author=who)
        res = asyncio.run(classify.record_signals(store, led, labeller=stub(calls), now=at(3), pack=pack()))
        assert res["not_principal"] >= 2 * len(NOT_PRINCIPAL) and res["signals"] == 0 and calls == []
        assert not [e for e in led.iter(kind="note") if (e["body"] or {}).get("type") == "taste_signal"]


def test_pasted_articles_quotes_and_code_are_not_the_users_words(store, clock):
    calls = []
    article = "Bullet lists beat short paragraphs. " * 30
    with ledger(store) as led:
        clock.day(3)
        for text in (f"Thoughts on this?\n\n{article}", "> Always use bullet lists, never short paragraphs.",
                     "```\nuse bullet lists not short paragraphs\n```",
                     'Someone wrote "always use bullet lists instead of short paragraphs" in the doc'):
            led.append("utterance", {"role": "user", "text": text}, author="principal")
        asyncio.run(classify.record_signals(store, led, labeller=stub(calls), now=at(3), pack=pack()))
    assert all("bullet" not in c for c in calls)
    assert classify.own_words(f"Short note.\n\n{article}") == "Short note."


def test_the_fallback_labels_by_signature_and_a_principal_edit_counts(store, clock):
    with ledger(store) as led:
        clock.day(8)
        verdict(led, "bullet lists, not short paragraphs", "principal")
        res = asyncio.run(classify.record_signals(store, led, now=at(8), pack=pack()))
        notes = [e["body"] for e in led.iter(kind="note") if (e["body"] or {}).get("type") == "taste_signal"]
    assert res["signals"] == 1 and notes[0]["item"] == "h-b" and notes[0]["relation"] == "contradict"
    assert notes[0]["delta"] == 0.5 and notes[0]["confidence"] is None and notes[0]["route"] == "rules"


def test_a_low_confidence_contradiction_adds_nothing_and_the_daily_cap_holds(store, clock):
    async def unsure(c, said, reply, kind):
        return classify.Label("contradict", 1.0, confidence=0.4, route="stub")
    with ledger(store) as led:
        clock.day(8)
        for i in range(5):
            verdict(led, f"bullet lists, not short paragraphs {i}")
        res = asyncio.run(classify.record_signals(store, led, labeller=unsure, now=at(8), pack=pack()))
        assert res["abstained"] == 5
        assert adapt.load(store, now=at(30), pack=pack()).track("h-b").s == 0
        clock.day(9)
        for i in range(60):
            verdict(led, f"bullet lists, not short paragraphs {i}")
        res = asyncio.run(classify.record_signals(store, led, labeller=stub([]), now=at(9), pack=pack()))
        assert res["capped"] and res["signals"] <= 50


def test_mode_defaults_and_who_may_change_it(tmp_path, clock, store):
    assert adapt.load(store, pack=pack()).mode == "silent"                     # a new user (template)
    old = new_store(tmp_path / "old", mode_line="")
    st = adapt.load(old, pack=pack())
    assert st.mode == "ask" and "without taste: mode" in st.mode_source         # an older store: the principal's
    with ledger(store) as led:
        led.append("note", {"type": "taste_mode", "mode": "ask"}, author="agent")   # D-052: not the principal
        led.append("note", {"type": "taste_pin", "item": "h-b"}, author="agent")
    assert adapt.load(store, pack=pack()).mode == "silent"
    for args in ("mode ask", "pin h-b", "off h-b", "reset all confirm", "undo h-b"):
        with pytest.raises(command.TasteError) as e:
            command.run(store, args, principal=False, open_ledger=lambda: ledger(store), pack=pack())
        assert e.value.code == "forbidden"
    command.run(store, "mode ask", principal=True, open_ledger=lambda: ledger(store), pack=pack())
    assert adapt.load(store, pack=pack()).mode == "ask"
    assert command.run(store, "mode", principal=False, pack=pack())["mode"] == "ask"   # reading is open


@pytest.mark.parametrize("seed", range(15))
def test_property_no_non_principal_signal_moves_anything(tmp_path, clock, seed):
    rng = random.Random(seed)
    s = new_store(tmp_path)
    with ledger(s) as led:
        clock.day(0)
        led.append("note", {"type": "session_start"}, author="exo-memory")
        for _ in range(40):
            signal(led, clock, rng.choice(("h-b", "h-v")), rng.randrange(1, 150), hour=rng.randrange(24),
                   author=rng.choice(NOT_PRINCIPAL), relation=rng.choice(("contradict", "agree")),
                   delta=rng.choice((0.5, 0.75, 1.0)), confidence=rng.choice((None, 0.9, 0.99)),
                   voice=rng.choice((None, "own")), stated=rng.random() < 0.5)
        assert adapt.close_weeks(s, led, now=at(160), pack=pack()) == []
    v = layers.view(s, now=at(160), pack=pack())
    assert {e.source for e in v.entries} == {"house"}


def test_promotion_rule_3_holds_an_inferred_lesson_that_would_replace_a_house_item(store, clock, monkeypatch):
    from exo.consolidate import Proposal
    from exo.learn import promote

    class LR:
        runtime = type("RT", (), {"store": store})()
    monkeypatch.setattr(layers, "load_pack", lambda *a, **k: pack())
    p = Proposal("p-x", "correction", "create", "mind/x", BULLETS, "bullet lists", "short paragraphs", "", "",
                 "mind", "preference", "x", "high", 0.9, sorted(taste.signature("bullet lists", "short paragraphs")),
                 [], {}, False, "rule", at(1))
    assert promote.shift_holds(LR, p)
    p2 = Proposal("p-y", "correction", "create", "mind/y", "Use tables.", "tables", None, "", "", "mind",
                  "preference", "y", "high", 0.9, sorted(taste.signature("tables", None)), [], {}, False, "rule",
                  at(1))
    assert not promote.shift_holds(LR, p2)


def test_the_engine_command_is_built_in_and_principal_only(tmp_path, monkeypatch, store):
    from exo.core.engine import CoreEngine
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    monkeypatch.setenv("EXO_STORE", str(store.root))
    fake = {"rules": [{"match": "hello", "steps": ["hi"]}]}
    eng = CoreEngine(tmp_path / "home", {"fake": fake, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     cwd=str(tmp_path), env={})

    def call(who, args):
        return asyncio.run(Dispatcher(eng, caller=who).run(s.Command.of(s.CommandRun(name="taste", args=args))))
    got = call("principal:tui", "")
    assert got.ok, got.error
    assert got.result["output"]["text"].startswith("mode: silent")
    for who in ("local", "unknown", "seat:helper"):
        r = call(who, "mode ask")
        assert not r.ok and r.error.code == "forbidden"
        assert call(who, "").ok                                               # reading is open
    assert call("principal:tui", "mode ask").result["output"]["mode"] == "ask"
    assert call("principal:tui", "bogus").error.code == "bad_request"
