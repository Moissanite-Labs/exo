"""GroupComposition: authorized profiles, exact Seat mapping, real fake-route engines, claim conflicts."""

import asyncio

import pytest

from exo.application import CoreEngine
from exo.core import seats
from exo.core.group_composition import CompositionError, GroupComposition
from exo.core.groups import GroupStore
from exo.core.plugins import PluginManager
from exo.core.sessions import SessionStore

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"]}}
PROFILES = ["lead", "one", "two"]


def seat(name):
    return seats.parse({"seat": {"name": name}})


def mapping(*names):
    return {name: seat(name) for name in names}


def make_group(owner="host", members=("lead", "one", "two")):
    store = SessionStore()
    try:
        return GroupStore(store).create(owner=owner, mode="conductor", members=list(members),
                                        known_profiles=PROFILES, conductor="lead")
    finally:
        store.close()


@pytest.fixture
def comp(tmp_path):
    made = []

    def build(profiles=None, **kw):
        args = dict(owner="host", home=tmp_path / "home", models=MODELS, cwd=str(tmp_path), env={})
        args.update(kw)
        c = GroupComposition(mapping(*(profiles or PROFILES)), **args)
        made.append(c)
        return c

    yield build
    for c in made:
        asyncio.run(c.aclose())


def test_owner_rejected_before_construction(comp):
    c = comp()
    with pytest.raises(CompositionError, match="belongs"):
        c(make_group(owner="mallory"))
    assert c.engines == {}


def test_unauthorized_member_rejected_before_construction(comp):
    c = comp(profiles=["lead", "one"])  # "two" is not authorized
    with pytest.raises(CompositionError, match="outside the authorized mapping"):
        c(make_group())
    assert c.engines == {}


def test_mapping_is_exact(comp):
    with pytest.raises(CompositionError, match="does not match"):
        comp(profiles=["lead"], **{}) if False else GroupComposition(
            {"lead": seat("scout")}, owner="host", home="h")
    with pytest.raises(CompositionError, match="not bound to a Seat"):
        GroupComposition({"lead": "scout"}, owner="host", home="h")
    with pytest.raises(CompositionError):
        GroupComposition({}, owner="host", home="h")
    with pytest.raises(CompositionError):
        GroupComposition(mapping("lead"), owner=" ", home="h")


def test_distinct_engines_shared_home_exact_seats(comp, tmp_path):
    c = comp()
    group = make_group()
    engines = c(group)
    assert set(engines) == set(PROFILES)
    assert len({id(e) for e in engines.values()}) == 3  # distinct real CoreEngines
    for member in group.members:
        eng = engines[member.profile]
        assert eng.home == tmp_path / "home"  # one home: shared session DB
        assert eng.seat is c._seats[member.profile]  # the exact authorized Seat
        assert eng.agent == member.profile
        assert eng._writer is not None  # writer claimed
        assert eng.router.roles["main"] == ["fake:x"]  # hermetic fake route


def test_duplicate_members_rejected(comp):
    group = make_group()
    dup = group.__class__(group.id, group.owner, group.mode, group.conductor,
                          (group.members[0], group.members[0]), group.created)
    with pytest.raises(CompositionError, match="duplicate"):
        comp()(dup)
    assert comp().engines == {}


def test_claim_conflict_fails_closed_and_aclose_cleans_up(comp, tmp_path):
    home = tmp_path / "home"
    holder = CoreEngine(home, MODELS, cwd=str(tmp_path), env={},
                        plugins=PluginManager(), seat=seat("two"))
    try:
        holder.claim_writer()  # an external writer already holds "two"'s log
        c2 = comp()
        with pytest.raises(CompositionError, match="composition failed"):
            c2(make_group())  # "two" is already claimed; no takeover, no retry
        partial = c2.engines
        assert set(partial) == set(PROFILES)  # failed-claim engine is retained for cleanup
        asyncio.run(c2.aclose())  # async cleanup of the partial set
        assert partial["lead"]._writer is None and partial["two"]._writer is None
        assert c2.engines == {}
        assert holder._writer is not None  # the external writer is untouched
    finally:
        asyncio.run(holder.close())
    assert holder._writer is None


def test_reuse_rejected_until_cleanup(comp):
    c = comp()
    group = make_group()
    c(group)
    with pytest.raises(CompositionError, match="aclose"):
        c(group)  # a second call must not overwrite the tracked engines
    assert len(c.engines) == 3  # the first engines are still the tracked ones
    asyncio.run(c.aclose())
    assert c.engines == {}
    engines = c(group)  # after a successful aclose() the composition may be reused
    assert len(engines) == 3
    assert len(c.engines) == 3


def test_aclose_retains_failed_engines_for_retry(comp, monkeypatch):
    c = comp()
    engines = c(make_group())

    async def boom():
        raise RuntimeError("close blew up")

    monkeypatch.setattr(engines["one"], "close", boom)
    with pytest.raises(CompositionError, match="close failed"):
        asyncio.run(c.aclose())
    # only the failed engine is retained; the successfully closed ones are cleared
    assert set(c.engines) == {"one"}
    assert engines["lead"]._writer is None and engines["two"]._writer is None
    monkeypatch.undo()  # restore close so the retry succeeds
    asyncio.run(c.aclose())  # retry of the retained failure now succeeds
    assert c.engines == {}
    assert engines["one"]._writer is None
