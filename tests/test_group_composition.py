"""GroupComposition: authorized profiles, exact Seat mapping, real fake-route engines, claim conflicts."""

import asyncio

import pytest

from exo.application import CoreEngine
from exo.core import seats
from exo.group_composition import CompositionError, GroupComposition
from exo.core.group_host import GroupHost, HostError
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
        GroupComposition(
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
    c = comp()
    with pytest.raises(CompositionError, match="duplicate"):
        c(dup)
    assert c.engines == {}


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


def test_validation_failure_does_not_require_cleanup_before_valid_call(comp):
    c = comp()
    with pytest.raises(CompositionError, match="belongs"):
        c(make_group(owner="other"))
    assert c.engines == {}
    assert set(c(make_group())) == set(PROFILES)


def test_host_failure_and_completion_require_explicit_composition_cleanup(comp, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = SessionStore(home / "sessions.sqlite")
    c = comp()
    host = GroupHost(GroupStore(store), owner="host", known_profiles=PROFILES, engine_factory=c)
    group = host.create(mode="conductor", members=PROFILES, conductor="lead")
    valid = {"lead": ["one"], "one": ["lead"], "two": ["lead"]}

    async def scenario():
        try:
            with pytest.raises(HostError, match="conductor must target one worker"):
                host.start(group.id, audiences={**valid, "lead": ["one", "two"]})
            retained = c.engines
            assert set(retained) == set(PROFILES)
            assert host.status(group.id)["receipt"] is None
            assert host.status(group.id)["local_runtime"] is False
            assert all(e._writer is not None for e in retained.values())
            with pytest.raises(HostError, match="^engine factory failed$"):
                host.start(group.id, audiences=valid)
            assert c.engines == retained
            # Explicit trusted caller cleanup; GroupHost does not own it in this layer.
            await c.aclose()
            assert c.engines == {}
            host.send(group.id, audience=PROFILES, text="question")
            host.start(group.id, audiences=valid)
            assert {s.state for s in await asyncio.wait_for(host.wait(group.id), 5)} == {"completed"}
            assert host.status(group.id)["receipt"].state == "settled"
            for member in group.members:
                assert c.engines[member.profile].store.messages(member.session)
            assert all(e._writer is not None for e in c.engines.values())
            second = host.create(mode="conductor", members=PROFILES, conductor="lead")
            with pytest.raises(HostError, match="^engine factory failed$"):
                host.start(second.id, audiences=valid)
            assert host.status(second.id)["receipt"] is None
            await c.aclose()
            host.start(second.id, audiences=valid)
            await asyncio.wait_for(host.wait(second.id), 5)
        finally:
            for saved in host.groups.listing(owner="host"):
                if host.status(saved.id)["local_runtime"]:
                    host.stop(saved.id)
                    await asyncio.wait_for(host.wait(saved.id), 5)
            await c.aclose()

    try:
        asyncio.run(asyncio.wait_for(scenario(), 15))
    finally:
        store.close()


def test_empty_plugins_block_builtin_tools(comp):
    from exo.core.tools.registry import Block, ToolContext
    from exo.core.types import ToolCall, ToolSpec

    c = comp()
    engines = c(make_group())

    async def scenario():
        engine = engines["lead"]
        assert not engine.plugins.hooks.has("pre_tool_call")
        result = await engine._pre_hook(ToolCall(id="one", name="read_file", arguments={}),
                                        ToolSpec(name="read_file", description="", parameters={}),
                                        ToolContext())
        assert isinstance(result, Block)
        assert "No tool approval gate is available" in result.reason

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_required_executor_fails_closed_without_retained_engine(comp, monkeypatch):
    from exo.core.tools.executor import ExecutorConfigurationError

    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    c = comp()
    with pytest.raises(CompositionError, match="composition failed") as error:
        c(make_group())
    assert isinstance(error.value.__cause__, ExecutorConfigurationError)
    assert c.engines == {}
