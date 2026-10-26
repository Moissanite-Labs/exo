"""Taste layers, lookup order and the house layer on and off (D-044 wave 1)."""
import io
import json
from contextlib import redirect_stdout

import pytest

from exo import cli
from exo.baseline import export as X
from exo.baseline import layers as L
from exo.baseline import review as R
from exo.baseline.pack import HouseItem, load_pack
from exo.store import PrincipalStore

from .conftest import add_belief, note, open_ledger


def released(store, out, version="1.3.0"):
    res = X.export_house(store, out, version)
    assert res.ok, res.render()
    R.tick(out, [i.id for i in res.items if i.needs_tick])
    R.finalise(out)
    pack = load_pack(out)
    assert pack.final, pack.problem
    return pack


def test_precedence_curve():
    assert L.precedence(0) == pytest.approx(0.7)
    assert L.precedence(365) == pytest.approx(0.35)
    assert L.precedence(446) == pytest.approx(0.3, abs=0.002)        # reaches the floor at about day 446
    assert L.precedence(2000) == 0.3                                   # never below the floor
    assert L.precedence(70) == pytest.approx(0.61, abs=0.005)          # the doc's day-70 figure


def test_value_items_start_lower_inside_the_getting_to_know_window():
    pol = dict(L.DEFAULTS)
    v = HouseItem("h-v", "value", "value", "mind", "Say when the evidence is thin.", frozenset())
    m = HouseItem("h-m", "method", "rule", "work", "Run the tests.", frozenset())
    assert L.in_window(5, 3, pol) and L.in_window(40, 3, pol) and not L.in_window(40, 25, pol)
    assert not L.in_window(95, 3, pol)                                 # at most 90 days
    assert L.house_weight(v, 5, True, pol) == 0.5
    assert L.house_weight(m, 5, True, pol) == pytest.approx(L.precedence(5))
    assert L.house_weight(v, 100, False, pol) == pytest.approx(L.precedence(100))


def _h(i, claim):
    return HouseItem(f"h-{i}", "method", "preference", "mind", claim, frozenset({f"+topic{i}", f"+extra{i}"}))


def _u(i, over, source="learned", weight=0.6, scope=None):
    return L.UserItem(f"p-{i}", f"user version {i}", frozenset({f"+mine{i}"}), weight, source,
                      "2026-09-01T00:00:00Z", "2026-10-06T00:00:00Z", "preference", over, scope)


def test_resolution_order_ruling_pin_learned_scoped_house():
    house = [_h(i, f"house claim {i}") for i in range(1, 8)]
    user = [_u("1r", "h-1", "ruling", 0.9), _u("1l", "h-1"),       # ruling beats a learned item and a pin
            _u("2l", "h-2"),                                       # house item pinned beats the learned item
            _u("3l", "h-3"),                                       # the learned item is pinned
            _u("4l", "h-4", weight=0.72),                          # learned beats house
            _u("5s", "h-5", scope="code"),                         # scoped: only inside its scope
            _u("free", None, weight=0.4)]                          # matches nothing: listed on its own
    pins, offs = {"h-1", "h-2", "p-3l"}, {"h-6"}
    kw = dict(pins=pins, offs=offs, weight_of=lambda h: 0.7, version="1.3")
    got = {e.house_id or e.id: e for e in L.resolve(house, user, **kw)}
    assert got["h-1"].id == "p-1r" and got["h-1"].tag == "[you said, 2026-09-01]"
    assert got["h-2"].id == "h-2" and got["h-2"].tag == "[pinned]"
    assert got["h-3"].id == "p-3l" and got["h-3"].tag == "[pinned]"
    assert got["h-4"].id == "p-4l" and got["h-4"].tag == "[learned 0.72, 5 weeks]"
    assert got["h-5"].id == "h-5" and got["h-5"].tag == "[house 1.3]"           # outside the scope
    assert "h-6" not in got                                                     # turned off
    assert got["h-7"].tag == "[house 1.3]"
    assert got["p-free"].tag.startswith("[learned 0.40")
    ids = [e.id for e in L.resolve(house, user, **kw)]
    assert "p-1l" not in ids and "p-2l" not in ids                              # overridden, not listed twice
    scoped = {e.house_id or e.id: e for e in L.resolve(house, user, scope="code", **kw)}
    assert scoped["h-5"].id == "p-5s" and scoped["h-5"].tag == "[learned 0.60, 5 weeks, code]"


def test_signature_match_counts_as_an_override():
    h = HouseItem("h-x", "method", "preference", "mind", "Short paragraphs, not bullet lists.",
                  frozenset({"+short", "+paragraph", "-bullet", "-list"}))
    u = L.UserItem("p-x", "Bullet lists, not paragraphs.", frozenset({"+bullet", "+list", "-short", "-paragraph"}),
                   0.8, "learned", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
    [e] = L.resolve([h], [u], version="1.3")
    assert e.id == "p-x" and e.house_id == "h-x"


def test_house_item_survives_when_its_only_match_is_spent_elsewhere():
    """One ruling whose signature matches two house items: the second house item falls back to itself
    instead of vanishing from the list."""
    sig = frozenset({"+table", "-bullet", "+grid"})
    h1 = HouseItem("h-1", "method", "rule", "work", "Use tables, not bullets.", frozenset({"+table", "-bullet"}))
    h2 = HouseItem("h-2", "method", "rule", "work", "Use grid tables for layouts.",
                   frozenset({"+grid", "+table", "-bullet"}))
    ruling = L.UserItem("p-r", "Always use grid tables.", sig, 0.9, "ruling",
                        "2026-09-01T00:00:00Z", "2026-10-06T00:00:00Z")
    got = {e.house_id or e.id: e for e in L.resolve([h1, h2], [ruling], weight_of=lambda h: 0.7, version="1.3")}
    assert got["h-1"].id == "p-r"                # the ruling wins the first house item
    assert got["h-2"].id == "h-2"                # the second house item is still listed, as itself
    assert got["h-2"].tag == "[house 1.3]"


def test_fresh_install_with_an_empty_store_runs_end_to_end_with_the_house_layer(principal_store, tmp_path,
                                                                                monkeypatch):
    pack = released(principal_store, tmp_path / "pack")
    monkeypatch.setenv("EXO_HOUSE_TASTE", str(tmp_path / "pack"))
    with redirect_stdout(io.StringIO()):
        assert cli.main(["init", "newuser"]) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.main(["taste", "--principal", "newuser"]) == 0
    out = buf.getvalue()
    assert out.startswith("mode: silent (policies/consolidate.yaml)")         # a new user: silent (wave 3)
    assert out.splitlines()[1].startswith("house: on, house taste 1.3.0 (6 items: 3 method, 3 value)")
    assert out.count("[house 1.3]") == 6
    assert "0.50  value" in out and "0.70  method" in out          # values start lower in the window
    store = PrincipalStore.for_principal("newuser")
    block = L.taste_block(store)
    assert block.startswith("# Taste") and block.count("[house 1.3]") == 6
    assert L.taste_block(store, budget_tokens=50).count("\n") < 6  # the budget cuts low-weight lines
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(["taste", "--principal", "newuser", "--json"])
    data = json.loads(buf.getvalue())
    assert data["house"]["on"] and len(data["items"]) == len(pack.items)


def test_principal_install_loses_nothing_and_shows_house_off(principal_store, tmp_path):
    pack = released(principal_store, tmp_path / "pack")
    before = L.view(principal_store, pack=load_pack(tmp_path / "nowhere"))
    v = L.view(principal_store, pack=pack)
    assert not v.house_on and "source" in v.reason
    assert [e.id for e in v.entries] == [e.id for e in before.entries]          # his own items, all of them
    assert {e.source for e in v.entries} == {"learned"} and all(e.house_id is None for e in v.entries)
    assert L.render(v).startswith("house: off (this store is the house taste's source")


def test_start_blank_turns_the_house_layer_off(principal_store, tmp_path):
    pack = released(principal_store, tmp_path / "pack")
    s = PrincipalStore.create(tmp_path / "blank")
    pol = s.root / "policies" / "consolidate.yaml"
    pol.write_text(pol.read_text() + "taste:\n  house: off\n")
    v = L.view(s, pack=pack)
    assert not v.house_on and "house: off" in v.reason and not v.entries


@pytest.mark.parametrize("value", ['"Off"', '" OFF "', '"No"', '" FALSE "'])
def test_house_policy_false_strings_are_trimmed_and_case_insensitive(principal_store, value):
    pol = principal_store.root / "policies" / "consolidate.yaml"
    pol.write_text(f"taste:\n  house: {value}\n")
    assert L.taste_policy(principal_store)["house"] is False


def test_unknown_taste_policy_key_warns_and_is_ignored(principal_store, caplog):
    pol = principal_store.root / "policies" / "consolidate.yaml"
    pol.write_text("taste:\n  half_life: 100\n  baseline_half_life: 180\n")
    with caplog.at_level("WARNING", logger="exo.baseline.layers"):
        got = L.taste_policy(principal_store)
    assert got["baseline_half_life"] == 180
    assert "half_life" not in got
    assert "unknown taste policy key 'half_life'" in caplog.text


def test_unreleased_pack_is_never_used(principal_store, tmp_path):
    X.export_house(principal_store, tmp_path / "pack", "1.3.0")
    s = PrincipalStore.create(tmp_path / "user")
    v = L.view(s, pack=load_pack(tmp_path / "pack"))
    assert not v.house_on and "not released" in v.reason and not v.entries


def test_shipped_pack_is_empty_until_the_first_release():
    p = load_pack(None)
    assert p.version == "0.0.0" and not p.items and not p.final


def test_pins_and_offs_come_only_from_the_principal(principal_store, tmp_path):
    pack = released(principal_store, tmp_path / "pack")
    s = PrincipalStore.create(tmp_path / "user")
    ids = sorted(i.id for i in pack.items)
    with open_ledger(s) as led:
        note(led, "taste_off", item=ids[0])
        note(led, "taste_off", author="agent", item=ids[1])            # an agent cannot turn a house item off
        note(led, "taste_pin", item=ids[2])
        note(led, "taste_pin", author="peer:x", item=ids[3])
        add_belief(led, "p-mine", "I like my own rule.", "mind/mine", overrides=ids[4])
    v = L.view(s, pack=pack)
    got = {e.house_id or e.id: e for e in v.entries}
    assert ids[0] not in got and ids[1] in got
    assert got[ids[2]].tag == "[pinned]" and got[ids[3]].tag == "[house 1.3]"
    assert got[ids[4]].id == "p-mine" and got[ids[4]].tag.startswith("[learned")
