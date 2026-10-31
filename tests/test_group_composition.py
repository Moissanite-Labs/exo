"""GroupComposition: authorized profiles, exact Seat mapping, real fake-route engines, claim conflicts.

The host-injected ``GroupPlugins`` tests below use real plugin folders (a minimal fixture
plugin the PluginManager loads for real), so they verify wiring end-to-end; they do NOT
exercise plugin *behavior* beyond what the fixture registers.
"""

import asyncio

from pathlib import Path

import pytest

from exo.application import CoreEngine
from exo.core import seats
from exo.core.group_composition import CompositionError, GroupComposition, GroupPlugins
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


def make_plugin_folder(root, name):
    """A minimal real plugin folder the PluginManager can load (manifest + register)."""
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "plugin.yaml").write_text(
        f'name: "{name}"\ndescription: "fixture plugin"\n', encoding="utf-8")
    (folder / "__init__.py").write_text(
        "def register(ctx):\n"
        "    ctx.register_command(\"{0}-cmd\", lambda raw='': 'ran ' + raw,\n"
        "                         description=\"fixture\", args_hint=\"\")\n".format(name),
        encoding="utf-8")
    return folder


def make_skill(root, name):
    """A minimal real skill the SkillIndex discovers (<dir>/<name>/SKILL.md)."""
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f'---\nname: "{name}"\ndescription: "fixture skill"\n---\nbody\n',
        encoding="utf-8")
    return root


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


# ------------------------------------------------------------- host-injected plugins


def test_plugins_none_injected_means_empty(comp):
    """No GroupPlugins at all: explicit host default — no folders, nothing loaded."""
    engines = comp()(make_group())
    for eng in engines.values():
        assert isinstance(eng.plugins, PluginManager)
        assert eng.plugins.plugins == {}  # nothing discovered implicitly
        assert eng.plugins.executor_required is False


def test_plugins_enabled_none_loads_every_name_in_injected_folders(comp, tmp_path):
    """enabled=None allows every name discovered in the injected folders (never entry points)."""
    folders = [make_plugin_folder(tmp_path, f"plug-{n}") for n in ("a", "b")]
    c = comp(plugins=GroupPlugins(dirs=tuple(folders), enabled=None))
    engines = c(make_group())
    for eng in engines.values():
        assert set(eng.plugins.plugins) == {"plug-a", "plug-b"}  # only the injected names
        assert eng.plugins.errors() == {}
        for name in ("plug-a", "plug-b"):
            assert f"{name}-cmd" in eng.plugins.commands  # the plugin really registered


def test_plugins_enabled_default_empty_loads_nothing(comp, tmp_path):
    """GroupPlugins(dirs=...) alone is fail-safe: the folder exists but nothing loads."""
    folder = make_plugin_folder(tmp_path, "plug-a")
    c = comp(plugins=GroupPlugins(dirs=(folder,)))
    engines = c(make_group())
    for eng in engines.values():
        assert eng.plugins.plugins == {}  # no implicit enable from an injected folder
        assert eng.plugins.errors() == {}


def test_plugins_enabled_empty_stays_empty(comp, tmp_path):
    """An empty allow-list is explicit: nothing loads even with a folder injected."""
    folder = make_plugin_folder(tmp_path, "plug-a")
    c = comp(plugins=GroupPlugins(dirs=(folder,), enabled=()))
    engines = c(make_group())
    for eng in engines.values():
        assert eng.plugins.plugins == {}


def test_seat_plugins_enabled_overrides_injected_default(comp, tmp_path):
    """A seat's own [plugins] enabled list wins over the injected default."""
    folder = make_plugin_folder(tmp_path, "plug-a")
    seats_ = mapping()
    seats_["lead"] = seats.parse({"seat": {"name": "lead"}, "plugins": {"enabled": []}})
    c = comp(profiles=None, plugins=GroupPlugins(dirs=(folder,), enabled=None))
    c._seats.update({"lead": seats_["lead"]})
    engines = c(make_group())
    assert engines["lead"].plugins.plugins == {}  # the seat's [] stays empty
    assert set(engines["one"].plugins.plugins) == {"plug-a"}  # injected default elsewhere


def test_plugin_config_deep_copied_per_member_memory_identity(comp, tmp_path):
    """Each member gets its own deep copy of the config with its exo-memory identity."""
    shared = {"some-plugin": {"opts": {"nested": ["x"]}}}
    c = comp(plugins=GroupPlugins(config=shared))
    engines = c(make_group())
    configs = {p: e.plugins.config for p, e in engines.items()}
    for profile, cfg in configs.items():
        memory = cfg["exo-memory"]
        assert memory["exo_home"] == str(tmp_path / "home")
        assert memory["agent_id"] == profile  # the member's own identity
        assert "principal" not in memory and "store_root" not in memory
    ids = [id(c["some-plugin"]["opts"]["nested"]) for c in configs.values()]
    assert len(set(ids)) == 3  # deep-copied: members never share mutable config
    assert shared == {"some-plugin": {"opts": {"nested": ["x"]}}}  # injected config untouched


def test_principal_store_prevalidated_before_any_construction(comp):
    """A relative principal_store in ANY member fails closed before any engine is built."""
    seats_ = mapping()
    seats_["two"] = seats.parse({"seat": {"name": "two"},
                                 "principal": {"id": "p-two", "store": "relative/store"}})
    c = comp()
    c._seats.update({"two": seats_["two"]})
    with pytest.raises(CompositionError, match="principal store must be an absolute"):
        c(make_group())
    assert c.engines == {}  # no partial construction at all


def test_principal_store_absolute_bound_per_member(comp, tmp_path):
    store = tmp_path / "principal-store"
    store.mkdir()
    seats_ = mapping()
    seats_["one"] = seats.parse({"seat": {"name": "one"},
                                 "principal": {"id": "p-one", "store": str(store)}})
    c = comp()
    c._seats.update({"one": seats_["one"]})
    engines = c(make_group())
    memory = engines["one"].plugins.config["exo-memory"]
    assert memory["principal"] == "p-one"
    assert memory["store_root"] == str(store)  # expanded, absolute
    assert "store_root" not in engines["lead"].plugins.config["exo-memory"]


def test_seat_skills_and_cwd_wired(comp, tmp_path):
    seat_cwd = tmp_path / "lead-cwd"
    seat_cwd.mkdir()
    skills = make_skill(tmp_path / "lead-skills", "lead-skill")
    extra = make_skill(tmp_path / "extra-skills", "extra-skill")
    seats_ = mapping()
    seats_["lead"] = seats.parse({"seat": {"name": "lead"},
                                  "session": {"cwd": str(seat_cwd)},
                                  "skills": {"folders": [str(skills)]}})
    c = comp(plugins=GroupPlugins(skill_folders=(extra,)))
    c._seats.update({"lead": seats_["lead"]})
    engines = c(make_group())
    assert engines["lead"].cwd == str(seat_cwd)  # the seat's own folder beats the shared cwd
    assert engines["one"].cwd == str(tmp_path)  # others keep the composition default
    assert "lead-skill" in engines["lead"].skills.skills  # the seat's own folder
    assert "extra-skill" in engines["lead"].skills.skills  # the injected extra folder


def test_executor_required_passed_through_unchanged(comp, tmp_path):
    # Required mode demands a trusted exo-gate folder with security hooks (engine contract).
    gate_dir = Path(__file__).resolve().parents[1] / "plugins" / "exo-gate"

    class ExecutorStub:  # passes CoreEngine's validate_executor() contract check
        trusted_plugin_dirs = (str(gate_dir.parent),)

        async def execute(self, request, tool, run_trusted):
            raise NotImplementedError  # never invoked in these tests

    executor = ExecutorStub()
    store = tmp_path / "gate-store"
    store.mkdir()
    c = comp(plugins=GroupPlugins(
        dirs=(gate_dir,), enabled=("exo-gate",),
        config={"exo-gate": {"principal_store": str(store), "key_path": str(tmp_path / "gate.key"),
                             "decision_model": False}},
        executor=executor, executor_required=True))
    engines = c(make_group())
    for eng in engines.values():
        assert eng.executor_required is True  # never silently downgraded
        assert eng.executor is executor
        assert eng.plugins.executor_required is True
