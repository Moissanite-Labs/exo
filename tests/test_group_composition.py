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
from exo.core.group_workspace import WorkspaceError
from exo.core.groups import GroupMember, GroupStore, SavedGroup
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


# ------------------------------------------------------- opt-in workspace preflight (G10)
#
# The preflight itself is unit-tested in tests/test_group_workspace.py; what is proven here
# is the GroupComposition wiring: the flag is opt-in and strictly boolean, the check runs
# after member validation but BEFORE any binding, plugin load, engine construction or writer
# claim (nothing to clean up on failure), the engine's exact effective-cwd semantics are
# used, a one-member group is checked too, and a WorkspaceError surfaces as CompositionError.


def test_workspace_check_default_off_rejects_nothing(comp, tmp_path):
    """Default False is fully backward compatible: identical cwds compose fine."""
    c = comp()  # every member's effective cwd is the same tmp_path; no check runs
    engines = c(make_group())
    assert len(engines) == 3


def test_workspace_check_strict_bool(comp):
    for bad in (1, 0, "yes", None):
        with pytest.raises(CompositionError, match="workspace_check must be True or False"):
            GroupComposition(mapping(), owner="host", home="h", workspace_check=bad)


def test_workspace_check_fails_before_any_construction(comp, tmp_path):
    """A shared cwd on the opt-in path fails closed with zero engines, plugins or claims."""
    c = comp(workspace_check=True)
    with pytest.raises(CompositionError, match="workspace"):
        c(make_group())
    assert c.engines == {}


def test_workspace_check_runs_after_member_validation(comp, tmp_path):
    """A member outside the mapping is reported first even when the cwd also clashes."""
    c = comp(profiles=["lead", "one"], workspace_check=True)
    with pytest.raises(CompositionError, match="outside the authorized mapping"):
        c(make_group())
    assert c.engines == {}


def test_workspace_check_distinct_cwds_pass(comp, tmp_path):
    """Distinct unrelated folders validate and the engines really run in them."""
    roots = {}
    for name in PROFILES:
        roots[name] = tmp_path / name
        roots[name].mkdir()
    seats_ = mapping()
    for name in PROFILES:
        seats_[name] = seats.parse({"seat": {"name": name},
                                    "session": {"cwd": str(roots[name])}})
    c = comp(profiles=None, workspace_check=True)
    c._seats.update(seats_)
    engines = c(make_group())
    for name in PROFILES:
        assert engines[name].cwd == str(roots[name])  # never changed by the check


def test_workspace_check_single_member_still_checked(comp, tmp_path):
    """One member only: the path checks run even though no sharing is possible."""
    # A one-member group cannot be built through GroupStore (it demands >= 2 profiles), so the
    # SavedGroup is made directly. The effective cwd is a file, not a folder: sharing is
    # impossible, yet the per-member path check must still refuse it.
    not_a_folder = tmp_path / "not-a-folder"
    not_a_folder.write_text("x", encoding="utf-8")
    seats_ = {"solo": seats.parse({"seat": {"name": "solo"},
                                   "session": {"cwd": str(tmp_path / "gone")}})}
    group = SavedGroup(id="g-solo", owner="host", mode="conductor", conductor="solo",
                       members=(GroupMember(profile="solo", session="s-solo"),), created="now")
    c = comp(profiles=None, cwd=str(not_a_folder), workspace_check=True)
    c._seats.update(seats_)
    with pytest.raises(CompositionError, match="is not a directory"):
        c(group)
    assert c.engines == {}  # checked before any construction even for one member


def test_workspace_check_missing_seat_cwd_falls_back(comp, tmp_path):
    """The engine ignores a seat cwd that is not a folder (self.cwd_note) and uses the
    composition's cwd; the preflight validates that resolved folder, not the dead path."""
    dead = tmp_path / "gone"  # never created
    shared = tmp_path / "shared"  # the composition cwd (a sibling of the other members)
    shared.mkdir()
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    seats_ = mapping()
    seats_["lead"] = seats.parse({"seat": {"name": "lead"},
                                  "session": {"cwd": str(dead)}})
    for name in ("one", "two"):  # everyone else keeps their own folder, so only lead falls back
        seats_[name] = seats.parse({"seat": {"name": name},
                                    "session": {"cwd": str(tmp_path / name)}})
    c = comp(profiles=None, cwd=str(shared), workspace_check=True)
    c._seats.update(seats_)
    engines = c(make_group())
    assert engines["lead"].cwd == str(shared)  # fell back exactly like the engine


def test_workspace_check_relative_seat_cwd_is_dropped_not_used(comp, tmp_path):
    """A relative seat cwd is not a folder, so the engine ignores it (self.cwd_note); the
    preflight then validates the injected folder the member actually works in."""
    shared = tmp_path / "shared"
    shared.mkdir()
    for name in ("lead", "one"):
        (tmp_path / name).mkdir()
    seats_ = mapping()
    seats_["two"] = seats.parse({"seat": {"name": "two"}, "session": {"cwd": "rel/cwd"}})
    for name in ("lead", "one"):
        seats_[name] = seats.parse({"seat": {"name": name},
                                    "session": {"cwd": str(tmp_path / name)}})
    c = comp(profiles=None, cwd=str(shared), workspace_check=True)
    c._seats.update(seats_)
    engines = c(make_group())
    assert engines["two"].cwd == str(shared)  # never the dead relative string


def test_workspace_check_rejects_relative_injected_cwd(comp, tmp_path):
    """A relative host cwd (no seat cwd anywhere) is rejected on opt-in, without cwd_note."""
    c = comp(cwd="relative/host-cwd", workspace_check=True)
    for engine_seat in c._seats.values():
        assert engine_seat.cwd is None  # so the injected cwd is the effective one
    with pytest.raises(CompositionError, match="is not absolute"):
        c(make_group())
    assert c.engines == {}


def test_workspace_check_missing_cwd_everywhere_checks_process_cwd(comp, tmp_path):
    """No seat cwd and no injected cwd resolves to os.getcwd(); it is still checked, and three
    members landing on that one folder is a shared-workspace refusal."""
    c = comp(cwd=None, workspace_check=True)
    with pytest.raises(CompositionError, match="share the folder"):
        c(make_group())
    assert c.engines == {}


def test_workspace_check_duplicate_worktree_rejected(comp, tmp_path):
    """Two members in one git working tree (plain .git folder) never compose."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    for name in ("sub", "other"):
        (repo / name).mkdir()
    seats_ = mapping()
    seats_["lead"] = seats.parse({"seat": {"name": "lead"},
                                  "session": {"cwd": str(repo / "sub")}})  # a subfolder: still one tree
    seats_["one"] = seats.parse({"seat": {"name": "one"},
                                 "session": {"cwd": str(repo / "other")}})  # another folder, same tree
    c = comp(cwd=None, workspace_check=True)
    c._seats.update(seats_)
    with pytest.raises(CompositionError, match="same git working tree"):
        c(make_group())
    assert c.engines == {}


def test_workspace_check_nested_worktrees_overlap_rejected(comp, tmp_path):
    """Two members in nested checkouts of the same repo are rejected, not just exact equals."""
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    nested = outer / "nested"
    (nested / ".git").mkdir(parents=True)
    seats_ = mapping()
    seats_["lead"] = seats.parse({"seat": {"name": "lead"}, "session": {"cwd": str(outer)}})
    seats_["one"] = seats.parse({"seat": {"name": "one"}, "session": {"cwd": str(nested)}})
    c = comp(cwd=None, workspace_check=True)
    c._seats.update(seats_)
    with pytest.raises(CompositionError, match="workspace"):
        c(make_group())
    assert c.engines == {}


def test_workspace_check_plugin_order_irrelevant(comp, tmp_path):
    """The preflight is before plugin discovery: an unreadable injected plugin folder cannot
    mask a workspace failure, and a workspace failure wins regardless of folder order."""
    plugin_a = make_plugin_folder(tmp_path, "plug-a")
    missing = tmp_path / "missing-plugins"
    folder_pair = (plugin_a, missing)
    bad_seats = mapping()
    bad_seats["lead"] = seats.parse({"seat": {"name": "lead"},
                                     "session": {"cwd": str(tmp_path)}})  # shared cwd
    for pair in (folder_pair, tuple(reversed(folder_pair))):
        c = comp(profiles=None, plugins=GroupPlugins(dirs=pair), workspace_check=True)
        c._seats.update(bad_seats)
        with pytest.raises(CompositionError, match="workspace"):
            c(make_group())
        assert c.engines == {}


def test_workspace_check_workspace_error_mapped_pre_allocation(comp, tmp_path, monkeypatch):
    """A WorkspaceError (any validator failure) surfaces as CompositionError before the
    try-block that allocates engines, so it is never wrapped a second time and nothing
    is ever allocated to clean up."""

    def boom(*a, **kw):
        raise WorkspaceError("injected by the test")

    monkeypatch.setattr("exo.core.group_composition.validate_workspaces", boom)
    c = comp(workspace_check=True)
    with pytest.raises(CompositionError, match="injected by the test") as excinfo:
        c(make_group())
    # Mapped once, pre-allocation: never re-wrapped by the engine-construction try-block.
    assert "workspace check failed" in str(excinfo.value)
    assert "composition failed" not in str(excinfo.value)
    assert c.engines == {}


def test_workspace_check_accepts_linked_worktrees_of_one_repo(comp, tmp_path):
    """Two members in two DIFFERENT linked worktrees of one repo are allowed."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    seats_ = mapping()
    for name in PROFILES:
        wt = tmp_path / f"wt-{name}"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / name}\n",
                                 encoding="utf-8")
        seats_[name] = seats.parse({"seat": {"name": name},
                                    "session": {"cwd": str(wt)}})
    c = comp(cwd=None, workspace_check=True)
    c._seats.update(seats_)
    engines = c(make_group())
    assert len(engines) == 3


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
