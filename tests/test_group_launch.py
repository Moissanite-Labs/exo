"""GroupLaunch: trusted end-to-end launch with real fake-route engines, fail-closed validation.

No network: models are the hermetic fake route. Every launch that succeeds is closed
inside one event loop (``drive``), and failure tests assert no allocation happened.
"""

import asyncio
import json

import pytest

from exo.core.group_composition import GroupPlugins
from exo.core.group_host import ConversationOptions
from exo.core.sessions import SessionStore
from exo.group_launch import LaunchError, create

PROFILES = ["lead", "one"]
MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"]}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    for name in (*PROFILES, "owner"):
        (home / "seats" / f"{name}.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


def make_plugin_folder(root, name):
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


def drive(launch):
    """Start, wait and close inside ONE event loop; return the member engines."""

    async def run():
        try:
            launch.start()
            await launch.wait()
            return dict(launch.members)
        finally:
            await launch.aclose()

    return asyncio.run(run())


def test_launch_end_to_end(home):
    launch = create(profiles=PROFILES)  # no seat, no EXO_SEAT: owner is "default"
    try:
        assert launch.engine.agent == "default"
        assert launch.group.owner == "default"
        assert {m.profile for m in launch.group.members} == set(PROFILES)
        members = drive(launch)
        assert set(members) == set(PROFILES)
        for profile, eng in members.items():
            assert eng.agent == profile
            assert eng.home == home  # one shared home / session DB
            assert eng.router.roles["main"] == ["fake:x"]  # the fake route, no network
            assert eng.plugins.plugins == {}  # plugins disabled by default
        assert launch.engine._writer is None  # owner engine released by aclose
    finally:
        if launch.members or launch.engine._writer is not None:
            asyncio.run(launch.aclose())


def test_seat_argument_and_env_seat_own_the_engine(home, monkeypatch):
    launch = create(seat="owner", profiles=PROFILES)
    try:
        assert launch.engine.agent == "owner"
        drive(launch)
    finally:
        if launch.engine._writer is not None:
            asyncio.run(launch.aclose())
    monkeypatch.setenv("EXO_SEAT", "owner")
    launch = create(profiles=PROFILES)
    try:
        assert launch.engine.agent == "owner"
        drive(launch)
    finally:
        if launch.engine._writer is not None:
            asyncio.run(launch.aclose())


def test_owner_membership_rejected_before_allocation(home, monkeypatch):
    monkeypatch.setenv("EXO_SEAT", "lead")
    with pytest.raises(LaunchError, match="member"):
        create(profiles=PROFILES)
    assert not (home / "sessions.sqlite").exists()  # nothing allocated
    with pytest.raises(LaunchError, match="member"):
        create(seat="one", profiles=PROFILES)  # the explicit argument counts too
    assert not (home / "sessions.sqlite").exists()


def test_invalid_profiles_rejected_before_allocation(home):
    bad = [
        "lead,one",  # a bare string is never a membership
        ["lead"],  # fewer than two
        ["lead", "lead"],  # duplicates are rejected, not silently deduped
        ["lead", "Bad Seat"], ["lead", ""], ["lead", "x y"], ["lead", 7], ["lead", None],
        None, 7,
    ]
    for profiles in bad:
        with pytest.raises(LaunchError):
            create(profiles=profiles)
    assert not (home / "sessions.sqlite").exists()


def test_unknown_or_mismatched_member_seat_rejected_before_allocation(home):
    with pytest.raises(LaunchError, match="ghost"):
        create(profiles=["lead", "ghost"])
    assert not (home / "sessions.sqlite").exists()
    (home / "seats" / "wrong.toml").write_text('name = "other"\n', encoding="utf-8")
    with pytest.raises(LaunchError, match="declares the name"):
        create(profiles=["lead", "wrong"])
    assert not (home / "sessions.sqlite").exists()


def test_plugins_rejected_unless_group_plugins(home):
    with pytest.raises(LaunchError, match="GroupPlugins"):
        create(profiles=PROFILES, plugins={"dirs": ()})
    assert not (home / "sessions.sqlite").exists()


def test_supplied_plugins_load_in_members(home, tmp_path):
    folder = make_plugin_folder(tmp_path, "plug-a")
    launch = create(profiles=PROFILES, plugins=GroupPlugins(dirs=(folder,), enabled=None))
    try:
        members = drive(launch)
        for eng in members.values():
            assert set(eng.plugins.plugins) == {"plug-a"}  # exactly the injected plugin
            assert "plug-a-cmd" in eng.plugins.commands
    finally:
        if launch.members:
            asyncio.run(launch.aclose())


def test_default_plugins_disabled_despite_folders(home, tmp_path):
    folder = make_plugin_folder(tmp_path, "plug-a")
    launch = create(profiles=PROFILES, plugins=GroupPlugins(dirs=(folder,)))
    try:
        for eng in drive(launch).values():
            assert eng.plugins.plugins == {}  # the empty allow-list stays empty
    finally:
        if launch.members:
            asyncio.run(launch.aclose())


def test_compose_failure_closes_store_synchronously_inside_a_loop(home, monkeypatch):
    (home / "seats" / "owner.toml").write_text('name = "owner"\nprincipal = "ada"\n',
                                               encoding="utf-8")
    monkeypatch.setenv("EXO_PRINCIPAL", "mallory")  # compose rejects the conflict

    async def attempt():
        with pytest.raises(LaunchError, match="principal"):
            create(seat="owner", profiles=PROFILES)  # no asyncio.run inside create

    asyncio.run(attempt())  # proving create is safe on an active event loop
    store = SessionStore(str(home / "sessions.sqlite"))
    try:
        rows = store._q("SELECT id FROM swarm_groups")  # the group row was persisted...
        assert len(rows) == 1
    finally:
        store.close()  # ...and the launch's own store was released, so this opens cleanly


class ExplodingClose:
    """Stand-in owner engine: a working close or one that raises the given error."""

    def __init__(self, error=None):
        self.error = error
        self.closed = False

    async def close(self):
        self.closed = True
        if self.error is not None:
            raise self.error


def test_aclose_cancellation_survives_and_cleanup_runs(home, monkeypatch):
    """CancelledError is process control: everything still closes, then it propagates."""
    launch = create(profiles=PROFILES)
    engine = ExplodingClose(asyncio.CancelledError("simulated process control"))
    store_closes = []
    monkeypatch.setattr(launch, "engine", engine)
    monkeypatch.setattr(launch.store, "close", lambda: store_closes.append(True))

    async def attempt():
        with pytest.raises(asyncio.CancelledError):
            await launch.aclose()

    asyncio.run(attempt())
    assert launch.closed  # the host settled before the store was closed
    assert engine.closed  # the owner engine close was attempted
    assert store_closes == [True]  # the backing store was always released


def test_aclose_cancellation_not_masked_by_earlier_ordinary_error(home, monkeypatch):
    """An ordinary close failure never masks process control: it chains as the cause."""
    launch = create(profiles=PROFILES)
    ordinary = RuntimeError("ordinary close failure")
    engine = ExplodingClose(asyncio.CancelledError("simulated process control"))

    async def failing_host_close(self):  # the first part fails ordinarily...
        raise ordinary

    monkeypatch.setattr(type(launch.host), "aclose", failing_host_close)
    monkeypatch.setattr(launch, "engine", engine)  # ...then cancellation arrives lower down

    caught = None

    async def attempt():
        nonlocal caught
        with pytest.raises(asyncio.CancelledError) as caught:
            await launch.aclose()

    asyncio.run(attempt())
    assert caught.value.__cause__ is ordinary  # the earlier failure stays visible, never masks


def test_aclose_ordinary_close_error_wraps_after_full_cleanup(home, monkeypatch):
    """An ordinary part failure becomes LaunchError only after every part is closed."""
    launch = create(profiles=PROFILES)
    engine = ExplodingClose(ValueError("owner engine close broke"))
    store_closes = []
    monkeypatch.setattr(launch, "engine", engine)
    monkeypatch.setattr(launch.store, "close", lambda: store_closes.append(True))
    with pytest.raises(LaunchError, match="close failed") as caught:
        asyncio.run(launch.aclose())
    assert isinstance(caught.value.__cause__, ValueError)  # the real part failure is chained
    assert launch.closed and engine.closed and store_closes == [True]


class BlockedFactoryClose:
    """Factory stand-in whose aclose blocks on an event, proving the shared cleanup
    of a REAL running host stays live (and the store/engine stay open) across
    repeated cancellations until the event is released."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False

    async def aclose(self):
        self.entered.set()
        await self.release.wait()
        self.finished = True


def test_aclose_cancelled_twice_while_host_cleanup_pending(home, monkeypatch):
    """Two real cancels during a live host cleanup: nothing closes early, the retry
    has no unprotected await, and the task ends CancelledError with cleanup done."""
    launch = create(profiles=PROFILES)
    engine = ExplodingClose()
    store_closes = []
    factory = BlockedFactoryClose()
    monkeypatch.setattr(launch.host, "_factory", factory)  # real host, blocked cleanup
    monkeypatch.setattr(launch, "engine", engine)
    monkeypatch.setattr(launch.store, "close", lambda: store_closes.append(True))

    async def attempt():
        task = asyncio.create_task(launch.aclose())
        await factory.entered.wait()  # the host's shared cleanup is genuinely live
        assert launch.host._cleanup_task is not None and not launch.host._cleanup_task.done()
        task.cancel()  # first cancellation: interrupts the shielded await only
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()  # the retry re-awaits the live cleanup; no escape
        task.cancel()  # second cancellation: would have hit the old sleep(0) escape
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()  # still blocked on cleanup; the store is NOT closed
        assert not launch.closed
        assert not engine.closed
        assert store_closes == []
        factory.release.set()  # let the host cleanup finish
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()  # process control delivered, not masked
        assert launch.closed and factory.finished  # cleanup ran to settlement first
        assert engine.closed
        assert store_closes == [True]

    asyncio.run(attempt())


def test_aclose_store_close_error_does_not_mask_cancellation(home, monkeypatch):
    """A store.close failure during a cancelled teardown never masks process control."""
    launch = create(profiles=PROFILES)
    monkeypatch.setattr(launch, "engine", ExplodingClose())

    def broken_store_close():
        raise OSError("store close broke")

    monkeypatch.setattr(launch.store, "close", broken_store_close)

    async def attempt():
        task = asyncio.create_task(launch.aclose())
        await asyncio.sleep(0)  # let aclose reach the host await
        task.cancel()  # the host settles instantly; cancellation is delivered after
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(attempt())
    assert launch.closed  # full cleanup still ran despite the broken store close


def test_default_mode_conductor_is_first_member(home):
    launch = create(profiles=PROFILES)
    try:
        assert launch.group.mode == "conductor"
        assert launch.group.conductor == "lead"  # explicit default: the first profile
        assert {m.profile for m in launch.group.members} == set(PROFILES)
        assert launch.group.owner == "default"
    finally:
        if launch.engine._writer is not None:
            asyncio.run(launch.aclose())


def test_socratic_mode_saves_no_conductor(home):
    launch = create(profiles=PROFILES, mode="socratic")
    try:
        assert launch.group.mode == "socratic"
        assert launch.group.conductor is None
        members = drive(launch)
        assert set(members) == set(PROFILES)
    finally:
        if launch.members:
            asyncio.run(launch.aclose())


def test_explicit_conductor_member_accepted(home):
    launch = create(profiles=PROFILES, conductor="one")
    try:
        assert launch.group.mode == "conductor"
        assert launch.group.conductor == "one"
        members = drive(launch)
        assert set(members) == set(PROFILES)
    finally:
        if launch.members:
            asyncio.run(launch.aclose())


def test_bad_mode_and_conductor_rejected_before_allocation(home):
    bad = [
        dict(mode="bogus"),  # unknown mode
        dict(mode="socratic", conductor="lead"),  # socratic groups have no conductor
        dict(conductor="ghost"),  # non-member conductor
        dict(conductor="Bad Seat"),  # not a valid profile ID
        dict(conductor=7),  # not a string
    ]
    for kwargs in bad:
        with pytest.raises(LaunchError):
            create(profiles=PROFILES, **kwargs)
    assert not (home / "sessions.sqlite").exists()  # nothing allocated


def test_start_conversation_forwards_options_without_audiences(home, monkeypatch):
    launch = create(profiles=PROFILES)
    calls = []

    def fake_host_start(group_id, **kwargs):
        calls.append((group_id, kwargs))
        return ("status",)

    try:
        monkeypatch.setattr(launch.host, "start", fake_host_start)
        options = ConversationOptions(max_turns=5, idle_timeout=3.0)
        status = launch.start(conversation=options)
        assert status == ("status",)
        assert len(calls) == 1
        group_id, kwargs = calls[0]
        assert group_id == launch.group.id
        # no audiences passed; the turn bound is forwarded explicitly as None
        # (full overlap) rather than being left implicit.
        assert kwargs == {"conversation": options, "max_concurrent": None}
        # the default one-shot start still passes the derived audiences
        launch.start()
        assert len(calls) == 2
        assert calls[1][1].keys() == {"audiences", "max_concurrent"}
        assert calls[1][1]["audiences"] == {"lead": ["one"], "one": ["lead"]}
        assert calls[1][1]["max_concurrent"] is None
        # An explicit bound is forwarded verbatim for BOTH start kinds; the
        # host/runtime does the strict validation, never the launcher.
        launch.start(conversation=options, max_concurrent=1)
        launch.start(max_concurrent=1)
        assert calls[2][1] == {"conversation": options, "max_concurrent": 1}
        assert calls[3][1] == {"audiences": {"lead": ["one"], "one": ["lead"]},
                              "max_concurrent": 1}
        with pytest.raises(LaunchError, match="ConversationOptions"):
            launch.start(conversation=object())
    finally:
        if launch.engine._writer is not None:
            asyncio.run(launch.aclose())
