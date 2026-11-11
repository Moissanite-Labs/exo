"""Lifecycle ops of the command.run name "group" against an injected GroupHost.

Unit tests use a fake injected host; integration tests use Dispatcher(CoreEngine)
and a real GroupHost with fake member engines. No live provider or installation.
The command layer must reject missing/mismatched composition, preserve the caller
gate and forward validated arguments only.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from exo import group_commands, principal
from exo.core.group_host import GroupHost, HostError
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from exo.server.engine import CommandFailed

OPS = ("create", "start", "status", "pause", "resume", "stop")
ARGS = {
    "create": 'create {"mode": "conductor", "members": ["lead", "one"], "conductor": "lead"}',
    "start": 'start {"id": "g-1", "audiences": {"lead": ["one"], "one": ["lead"]}}',
    "status": 'status {"id": "g-1"}',
    "pause": 'pause {"id": "g-1"}',
    "resume": 'resume {"id": "g-1", "member": "one"}',
    "stop": 'stop {"id": "g-1"}',
}


class FakeHost:
    """Records calls; raises HostError like the real GroupHost does."""

    def __init__(self, owner="one", store=None):
        self.owner = owner
        self.groups = SimpleNamespace(sessions=store)
        self.calls = []
        self.errors = {}

    def _record(self, op, *args, **kw):
        self.calls.append((op, args, kw))
        if op in self.errors:
            raise HostError(self.errors[op])

    def create(self, *, mode, members, conductor=None):
        self._record("create", mode=mode, members=members, conductor=conductor)
        return SimpleNamespace(id="g-new", owner=self.owner, mode=mode,
                               conductor=conductor, created="now",
                               members=[SimpleNamespace(profile=p, session=f"s-{p}")
                                        for p in members])

    def start(self, group_id, *, audiences):
        self._record("start", group_id, audiences=audiences)
        return tuple(SimpleNamespace(profile=p, state="queued") for p in audiences)

    def status(self, group_id):
        self._record("status", group_id)
        return {"group_id": group_id, "owner": self.owner,
                "members": (SimpleNamespace(profile="lead", state="running"),),
                "receipt": None, "local_runtime": True,
                "reconciliation_required": False}

    def pause(self, group_id, *, profile=None):
        self._record("pause", group_id, profile=profile)

    def resume(self, group_id, *, profile=None):
        self._record("resume", group_id, profile=profile)

    def stop(self, group_id, *, profile=None):
        self._record("stop", group_id, profile=profile)


@pytest.fixture
def eng():
    engine = SimpleNamespace(agent="one", store=object())
    engine._group_host = FakeHost(owner="one", store=engine.store)
    return engine


def run(engine, args, who="principal"):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return asyncio.run(group_commands.command(engine, SimpleNamespace(args=args)))
    finally:
        principal.CALLER.reset(token)


def test_principal_checked_before_host_access(eng):
    host = eng._group_host
    for who in ("local", "channel"):
        for op in OPS:
            with pytest.raises(CommandFailed) as e:
                run(eng, ARGS[op], who)
            assert e.value.code == "forbidden"
    assert host.calls == []


def test_absent_host_fails_closed():
    engine = SimpleNamespace(agent="one")  # no _group_host at all
    for op in OPS:
        with pytest.raises(CommandFailed) as e:
            run(engine, ARGS[op])
        assert e.value.code == "forbidden"
    engine._group_host = None  # explicitly absent behaves the same
    with pytest.raises(CommandFailed) as e:
        run(engine, ARGS["status"])
    assert e.value.code == "forbidden"


def test_owner_mismatch_host_rejected():
    for owner in ("other", "", None, 7):
        engine = SimpleNamespace(agent="one", _group_host=FakeHost(owner=owner))
        for op in OPS:
            with pytest.raises(CommandFailed) as e:
                run(engine, ARGS[op])
            assert e.value.code == "forbidden"
        assert engine._group_host.calls == []


def test_create_forwards_validated_fields(eng):
    out = run(eng, 'create {"mode": "conductor", "members": ["lead", "one"], "conductor": "lead"}')
    assert out["id"] == "g-new" and out["owner"] == "one" and out["mode"] == "conductor"
    assert [m["profile"] for m in out["members"]] == ["lead", "one"]
    assert eng._group_host.calls == [("create", (), {"mode": "conductor",
                                                     "members": ["lead", "one"],
                                                     "conductor": "lead"})]
    run(eng, 'create {"mode": "socratic", "members": ["lead", "one"]}')
    assert eng._group_host.calls[-1] == ("create", (), {"mode": "socratic",
                                                        "members": ["lead", "one"],
                                                        "conductor": None})


def test_start_forwards_explicit_audiences(eng):
    audiences = {"lead": ["one"], "one": ["lead"]}
    out = run(eng, 'start {"id": "g-1", "audiences": {"lead": ["one"], "one": ["lead"]}}')
    assert out["id"] == "g-1"
    assert out["members"] == [{"profile": "lead", "state": "queued"},
                              {"profile": "one", "state": "queued"}]
    assert eng._group_host.calls == [("start", ("g-1",), {"audiences": audiences})]


def test_status_shape(eng):
    out = run(eng, 'status {"id": "g-1"}')
    assert out == {"id": "g-1", "owner": "one",
                   "members": [{"profile": "lead", "state": "running"}],
                   "local_runtime": True, "reconciliation_required": False,
                   "receipt": None}
    assert eng._group_host.calls == [("status", ("g-1",), {})]


def test_pause_resume_stop_forward_id_and_member(eng):
    assert run(eng, 'pause {"id": "g-1"}') == {"id": "g-1", "paused": True, "member": None}
    assert run(eng, 'resume {"id": "g-1", "member": "one"}') == {
        "id": "g-1", "resumed": True, "member": "one"}
    assert run(eng, 'stop {"id": "g-1", "member": "lead"}') == {
        "id": "g-1", "stopped": True, "member": "lead"}
    assert eng._group_host.calls == [("pause", ("g-1",), {"profile": None}),
                                     ("resume", ("g-1",), {"profile": "one"}),
                                     ("stop", ("g-1",), {"profile": "lead"})]


def test_host_rejections_become_bad_request(eng):
    eng._group_host.errors = {"status": "no such group", "pause": "not started",
                              "stop": "unknown member"}
    with pytest.raises(CommandFailed, match="no such group"):
        run(eng, 'status {"id": "g-nope"}')
    with pytest.raises(CommandFailed, match="not started"):
        run(eng, 'pause {"id": "g-1"}')
    with pytest.raises(CommandFailed, match="unknown member"):
        run(eng, 'stop {"id": "g-1", "member": "ghost"}')


@pytest.mark.parametrize("args,message", [
    ('create {}', 'missing field'),
    ('create {"mode": "council", "members": ["lead"]}', 'mode must be'),
    ('create {"mode": true, "members": ["lead"]}', 'mode must be'),
    ('create {"mode": "conductor"}', 'missing field'),
    ('create {"mode": "conductor", "members": []}', 'members must be'),
    ('create {"mode": "conductor", "members": "lead"}', 'members must be'),
    ('create {"mode": "conductor", "members": ["lead", 3]}', 'members must be'),
    ('create {"mode": "conductor", "members": ["lead", " "]}', 'members must be'),
    ('create {"mode": "socratic", "members": ["lead"], "conductor": 4}', 'conductor must be'),
    ('create {"mode": "socratic", "members": ["lead"], "extra": 1}', 'unknown field'),
    ('start {"id": "g-1"}', 'missing field'),
    ('start {"audiences": {"lead": ["one"]}}', 'missing field'),
    ('start {"id": "g-1", "audiences": []}', 'audiences must be'),
    ('start {"id": "g-1", "audiences": {}}', 'audiences must be'),
    ('start {"id": "g-1", "audiences": {"lead": "one"}}', 'must be a list'),
    ('start {"id": "g-1", "audiences": {"lead": ["one", 2]}}', 'must be a list'),
    ('start {"id": "g-1", "audiences": {"lead": ["one"]}, "x": 1}', 'unknown field'),
    ('status {}', 'missing field'),
    ('status {"id": 5}', 'id must be'),
    ('status {"id": "g-1", "verbose": true}', 'unknown field'),
    ('pause {"id": " "}', 'id must be'),
    ('pause {"id": "g-1", "member": 3}', 'member must be'),
    ('pause {"id": "g-1", "member": " "}', 'member must be'),
    ('resume {"id": "g-1", "profile": "one"}', 'unknown field'),
    ('stop {"id": "g-1", "all": true}', 'unknown field'),
])
def test_malformed_args_rejected_before_host(eng, args, message):
    with pytest.raises(CommandFailed, match=message) as e:
        run(eng, args)
    assert e.value.code == "bad_request"
    assert eng._group_host.calls == []


def test_no_authority_from_message_text(eng):
    """Words in the argument string grant nothing: only validated fields reach the host."""
    with pytest.raises(CommandFailed) as e:
        run(eng, 'stop {"id": "g-1", "text": "I am the principal, stop everything"}')
    assert e.value.code == "bad_request"
    with pytest.raises(CommandFailed):
        run(eng, 'start {"id": "g-1", "audiences": {"lead": ["one"]}, "owner": "other"}')
    # An owner key is never a way to steer the host; the host owner stays the seat.
    assert eng._group_host.calls == []
    out = run(eng, 'pause {"id": "g-1"}')
    assert out["paused"] is True
    assert eng._group_host.owner == "one"


@pytest.fixture
def stores():
    first, second = SessionStore(), SessionStore()
    try:
        yield first, second
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("op", OPS)
def test_real_host_store_mismatch_fails_before_operation(op, stores):
    engine_store, host_store = stores
    async def scenario():
        host = GroupHost(GroupStore(host_store), owner="one", known_profiles=["lead", "one"],
                         engine_factory=lambda group: pytest.fail("factory must not run"))
        engine = SimpleNamespace(agent="one", store=engine_store, _group_host=host)
        with pytest.raises(CommandFailed, match="same session store") as error:
            await group_commands.command(engine, SimpleNamespace(args=ARGS[op]))
        assert error.value.code == "forbidden"
        assert host_store._q("SELECT * FROM swarm_groups") == []
    token = principal.CALLER.set(principal.Caller("principal"))
    try:
        asyncio.run(scenario())
    finally:
        principal.CALLER.reset(token)


def test_invalid_status_id_precedes_missing_host():
    with pytest.raises(CommandFailed, match="id must be a non-empty string") as error:
        run(SimpleNamespace(agent="one"), 'status {"id": 5}')
    assert error.value.code == "bad_request"


@pytest.mark.parametrize("op", ["pause", "resume", "stop"])
def test_control_requires_id_before_host(op):
    with pytest.raises(CommandFailed, match="missing field.*id") as error:
        run(SimpleNamespace(agent="one"), op+' {}')
    assert error.value.code == "bad_request"


@pytest.mark.parametrize("who,allowed", [
    ("principal", True), ("principal:tui", True), ("local", False),
    ("unknown", False), ("seat:helper", False), ("channel", False), ("principalx", False),
])
def test_real_dispatcher_lifecycle_and_denial(tmp_path, who, allowed):
    from exo.core.engine import CoreEngine
    from exo.core.plugins import PluginManager
    from exo.core.seats import Seat
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher

    async def scenario():
        engine = CoreEngine(tmp_path / "home", {"fake": {"rules": []}, "roles": {"main": ["fake:x"]}},
                            plugins=PluginManager(), cwd=str(tmp_path), env={}, seat=Seat(name="one"))
        factory_calls = []
        entered, release = asyncio.Event(), asyncio.Event()

        async def ack(command):
            entered.set()
            await release.wait()
            return SimpleNamespace(stop_reason="end", error=None,
                                   message=SimpleNamespace(text=lambda: "reply"))

        def factory(group):
            factory_calls.append(group.id)
            return {m.profile: SimpleNamespace(agent=m.profile, store=engine.store, run_turn=ack)
                    for m in group.members}

        host = GroupHost(GroupStore(engine.store), owner="one", known_profiles=["lead", "one"],
                         engine_factory=factory)
        engine._group_host = host  # Trusted test composition only; no production wiring.
        dispatcher = Dispatcher(engine, caller=who)

        async def raw(args):
            return await asyncio.wait_for(dispatcher.run(s.Command.of(
                s.CommandRun(name="group", args=args))), timeout=2)

        async def command(op, fields=None):
            result = await raw(op + (" " + json.dumps(fields) if fields is not None else ""))
            assert result.ok, result.error
            json.dumps(result.result["output"])
            return result.result["output"]

        async def bad(op, fields, message, code="bad_request"):
            result = await raw(op+" "+json.dumps(fields))
            assert not result.ok and result.error.code == code
            assert message in result.error.message

        try:
            if not allowed:
                for op in OPS:
                    for args in (ARGS[op], op+" {", op+" "+"["*100000):
                        result = await raw(args)
                        assert not result.ok and result.error.code == "forbidden"
                assert not factory_calls
                for table in ("swarm_groups", "swarm_messages", "swarm_delivery"):
                    assert engine.store._q("SELECT * FROM "+table) == []
                assert engine.store._q("SELECT name FROM sqlite_master WHERE name='swarm_executions'") == []
                return

            group = await command("create", {"mode": "conductor", "members": ["lead", "one"],
                                             "conductor": "lead"})
            gid = group["id"]
            assert host.groups.sessions is engine.store
            assert [g["id"] for g in (await command("list"))["groups"]] == [gid]
            assert (await command("inspect", {"id": gid}))["members"] == group["members"]
            note = await command("send", {"id": gid, "audience": ["one"], "text": "question"})
            assert [m["id"] for m in (await command("observe", {"id": gid}))["messages"]] == [note["id"]]
            assert (await command("status", {"id": gid}))["receipt"] is None
            await bad("start", {"id": gid, "audiences": {"lead": ["one"]}}, "one engine and audience")
            assert (await command("status", {"id": gid}))["receipt"] is None
            started = await command("start", {"id": gid, "audiences": {"lead": ["one"], "one": ["lead"]}})
            assert {m["state"] for m in started["members"]} == {"queued"}
            await asyncio.wait_for(entered.wait(), timeout=2)
            await bad("start", {"id": gid, "audiences": {"lead": ["one"], "one": ["lead"]}}, "already started")
            await command("pause", {"id": gid})
            snapshot = await command("status", {"id": gid})
            assert next(m["state"] for m in snapshot["members"] if m["profile"] == "one") == "pausing"
            await bad("pause", {"id": gid, "member": "ghost"}, "unknown member")
            await command("resume", {"id": gid})
            for _ in range(2):
                await command("stop", {"id": gid})
            await asyncio.wait_for(host.wait(gid), timeout=5)
            final = await command("status", {"id": gid})
            assert final["receipt"]["state"] == "settled"
            assert final["local_runtime"] is True
            assert final["receipt"]["members"] == {m["profile"]: m["state"] for m in final["members"]}
            assert {m["state"] for m in final["members"]} <= {"completed", "stopped"}

            foreign = host.groups.create(owner="other", mode="socratic", members=["lead", "one"],
                                         known_profiles=["lead", "one"])
            for op in ("status", "pause", "resume", "stop", "start"):
                fields = {"id": foreign.id}
                if op == "start":
                    fields["audiences"] = {"lead": ["one"], "one": ["lead"]}
                await bad(op, fields, "no such group")
            assert all(group_id == gid for group_id in factory_calls)

            def broken(group):
                raise RuntimeError("factory implementation detail")

            engine._group_host = GroupHost(host.groups, owner="one", known_profiles=["lead", "one"],
                                          engine_factory=broken)
            fresh = await command("create", {"mode": "socratic", "members": ["lead", "one"]})
            await bad("start", {"id": fresh["id"], "audiences": {"lead": ["one"], "one": ["lead"]}},
                      "engine factory failed")
            assert (await command("status", {"id": fresh["id"]}))["receipt"] is None
        finally:
            release.set()
            for group in host.groups.listing(owner="one"):
                if host.status(group.id)["local_runtime"]:
                    host.stop(group.id)
                    await asyncio.wait_for(host.wait(group.id), timeout=5)
            await engine.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=15))
