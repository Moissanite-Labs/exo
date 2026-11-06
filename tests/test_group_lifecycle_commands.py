"""Lifecycle ops of the command.run name "group" against an injected GroupHost.

The engine is a SimpleNamespace with ``agent`` (the seat name) and optionally
``_group_host`` (a fake injected host). No CoreEngine, no store, no live
install: the command layer must fail closed without a host, reject a host
owned by another seat, and forward validated arguments only.
"""
import asyncio
from types import SimpleNamespace

import pytest

from exo import principal
from exo.core import group_commands
from exo.core.group_host import ConversationOptions, HostError
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

    def __init__(self, owner="one"):
        self.owner = owner
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

    def start(self, group_id, **kw):
        self._record("start", group_id, **kw)  # exactly the keywords the command forwarded
        profiles = kw["audiences"] if "audiences" in kw else ("lead", "one")
        return tuple(SimpleNamespace(profile=p, state="queued") for p in profiles)

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
    engine = SimpleNamespace(agent="one")
    engine._group_host = FakeHost(owner="one")
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


def _start(conversation, extra=""):
    return f'start {{"id": "g-1", "conversation": {conversation}{extra}}}'


def test_start_conversation_forwards_options_only(eng):
    out = run(eng, _start('{"max_turns": 3, "idle_timeout": 60}'))
    assert out == {"id": "g-1", "members": [{"profile": "lead", "state": "queued"},
                                            {"profile": "one", "state": "queued"}]}
    run(eng, _start('{"max_turns": 10000, "idle_timeout": 0.5, "max_concurrent": 2}'))
    run(eng, _start('{"max_turns": 1, "idle_timeout": 86400}'))
    assert eng._group_host.calls == [
        ("start", ("g-1",), {"conversation": ConversationOptions(3, 60, None)}),
        ("start", ("g-1",), {"conversation": ConversationOptions(10000, 0.5, 2)}),
        ("start", ("g-1",), {"conversation": ConversationOptions(1, 86400, None)})]
    for _, _, kw in eng._group_host.calls:
        assert set(kw) == {"conversation"}  # no audiences, knowledge, plan or grants keyword


def test_start_legacy_audiences_kwargs_exact(eng):
    run(eng, ARGS["start"])
    assert eng._group_host.calls == [
        ("start", ("g-1",), {"audiences": {"lead": ["one"], "one": ["lead"]}})]


def test_start_conversation_host_rejection_is_bad_request(eng):
    eng._group_host.errors = {"start": "max_concurrent must be between 1 and the member count"}
    with pytest.raises(CommandFailed, match="member count") as e:
        run(eng, _start('{"max_turns": 3, "idle_timeout": 60, "max_concurrent": 9}'))
    assert e.value.code == "bad_request"


@pytest.mark.parametrize("args", [
    # exactly one of audiences/conversation
    'start {"id": "g-1"}',
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "conversation": {"max_turns": 3, "idle_timeout": 60}}',
    # unknown top-level keys, including features outside this slice
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "plan": {}'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "grants": {}'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "max_items": 1'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "max_chars": 100'),
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": true, "plan": {}}',
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": true, "grants": {}}',
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": true, "max_items": 1}',
    # knowledge is an exact bool; everything else is refused before the host
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "knowledge": 1'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "knowledge": 0'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "knowledge": "true"'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "knowledge": null'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "knowledge": {}'),
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "knowledge": []'),
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": 1.0}',
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": "false"}',
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": null}',
    # knowledge does not relax exactly-one audiences/conversation
    'start {"id": "g-1", "knowledge": true}',
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "knowledge": true, '
    '"conversation": {"max_turns": 3, "idle_timeout": 60}}',
    _start('{"max_turns": 3, "idle_timeout": 60}', ', "owner": "other"'),
    # unknown nested keys
    _start('{"max_turns": 3, "idle_timeout": 60, "knowledge": true}'),
    _start('{"max_turns": 3, "idle_timeout": 60, "audiences": {"lead": ["one"]}}'),
    _start('{"max_turns": 3, "idle_timeout": 60, "plan": 1}'),
    # shape and missing fields
    _start('null'), _start('[]'), _start('"x"'), _start('{}'),
    _start('{"max_turns": 3}'), _start('{"idle_timeout": 60}'),
    # max_turns: exact int 1..10000, no bool/float/str
    _start('{"max_turns": 0, "idle_timeout": 60}'),
    _start('{"max_turns": 10001, "idle_timeout": 60}'),
    _start('{"max_turns": -1, "idle_timeout": 60}'),
    _start('{"max_turns": true, "idle_timeout": 60}'),
    _start('{"max_turns": 3.0, "idle_timeout": 60}'),
    _start('{"max_turns": "3", "idle_timeout": 60}'),
    _start('{"max_turns": null, "idle_timeout": 60}'),
    # idle_timeout: finite number, >0, <=86400, no bool/str
    _start('{"max_turns": 3, "idle_timeout": 0}'),
    _start('{"max_turns": 3, "idle_timeout": -1}'),
    _start('{"max_turns": 3, "idle_timeout": 86401}'),
    _start('{"max_turns": 3, "idle_timeout": 1e999}'),
    _start('{"max_turns": 3, "idle_timeout": NaN}'),
    _start('{"max_turns": 3, "idle_timeout": Infinity}'),
    _start('{"max_turns": 3, "idle_timeout": true}'),
    _start('{"max_turns": 3, "idle_timeout": "60"}'),
    _start('{"max_turns": 3, "idle_timeout": null}'),
    # max_concurrent: strict int >= 1 (member-count bound is the host's)
    _start('{"max_turns": 3, "idle_timeout": 60, "max_concurrent": 0}'),
    _start('{"max_turns": 3, "idle_timeout": 60, "max_concurrent": -2}'),
    _start('{"max_turns": 3, "idle_timeout": 60, "max_concurrent": true}'),
    _start('{"max_turns": 3, "idle_timeout": 60, "max_concurrent": 1.0}'),
    _start('{"max_turns": 3, "idle_timeout": 60, "max_concurrent": "1"}'),
    # id
    'start {"conversation": {"max_turns": 3, "idle_timeout": 60}}',
    'start {"id": " ", "conversation": {"max_turns": 3, "idle_timeout": 60}}',
    'start {"id": 5, "conversation": {"max_turns": 3, "idle_timeout": 60}}',
])
def test_start_conversation_invalid_rejected_before_host(eng, args):
    with pytest.raises(CommandFailed) as e:
        run(eng, args)
    assert e.value.code == "bad_request"
    assert eng._group_host.calls == []


def test_start_conversation_principal_and_host_gates():
    args = _start('{"max_turns": 3, "idle_timeout": 60}')
    engine = SimpleNamespace(agent="one", _group_host=FakeHost(owner="one"))
    for who in ("local", "channel"):
        with pytest.raises(CommandFailed) as e:
            run(engine, args, who)
        assert e.value.code == "forbidden"
    assert engine._group_host.calls == []
    for host in (None, FakeHost(owner="other"), FakeHost(owner=None)):
        engine = SimpleNamespace(agent="one", _group_host=host)
        with pytest.raises(CommandFailed) as e:
            run(engine, args)
        assert e.value.code == "forbidden"
        assert host is None or host.calls == []


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


@pytest.mark.parametrize("args", [
    'create {}',                                                   # missing fields
    'create {"mode": "council", "members": ["lead"]}',             # unknown mode
    'create {"mode": true, "members": ["lead"]}',                  # mode not a string
    'create {"mode": "conductor"}',                                # members required
    'create {"mode": "conductor", "members": []}',                 # empty members
    'create {"mode": "conductor", "members": "lead"}',             # members not a list
    'create {"mode": "conductor", "members": ["lead", 3]}',        # member not a string
    'create {"mode": "conductor", "members": ["lead", " "]}',      # blank member
    'create {"mode": "socratic", "members": ["lead"], "conductor": 4}',
    'create {"mode": "socratic", "members": ["lead"], "extra": 1}',  # unknown key
    'start {"id": "g-1"}',                                         # audiences required
    'start {"audiences": {"lead": ["one"]}}',                      # id required
    'start {"id": "g-1", "audiences": []}',                        # audiences not an object
    'start {"id": "g-1", "audiences": {}}',                        # empty audiences
    'start {"id": "g-1", "audiences": {"lead": "one"}}',           # audience not a list
    'start {"id": "g-1", "audiences": {"lead": ["one", 2]}}',      # target not a string
    'start {"id": "g-1", "audiences": {"lead": ["one"]}, "x": 1}',  # unknown key
    'status {}',                                                   # id required
    'status {"id": 5}',                                            # id not a string
    'status {"id": "g-1", "verbose": true}',                       # unknown key
    'pause {"id": " "}',                                           # blank id
    'pause {"id": "g-1", "member": 3}',                            # member not a string
    'pause {"id": "g-1", "member": " "}',                          # blank member
    'resume {"id": "g-1", "profile": "one"}',                      # unknown key
    'stop {"id": "g-1", "all": true}',                             # unknown key
])
def test_malformed_args_rejected_before_host(eng, args):
    with pytest.raises(CommandFailed) as e:
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


AUD = {"lead": ["one"], "one": ["lead"]}
CONV = '{"max_turns": 3, "idle_timeout": 60}'


def test_start_knowledge_true_forwards_flag_only_both_forms(eng):
    run(eng, 'start {"id": "g-1", "audiences": {"lead": ["one"], "one": ["lead"]}, "knowledge": true}')
    run(eng, _start(CONV, ', "knowledge": true'))
    assert eng._group_host.calls == [
        ("start", ("g-1",), {"audiences": AUD, "knowledge": True}),
        ("start", ("g-1",), {"conversation": ConversationOptions(3, 60, None), "knowledge": True})]
    for _, _, kw in eng._group_host.calls:
        assert set(kw) <= {"audiences", "conversation", "knowledge"}   # no caps, plan or grants


def test_start_knowledge_omitted_or_false_is_legacy_call(eng):
    run(eng, ARGS["start"])
    run(eng, 'start {"id": "g-1", "audiences": {"lead": ["one"], "one": ["lead"]}, "knowledge": false}')
    run(eng, _start(CONV))
    run(eng, _start(CONV, ', "knowledge": false'))
    assert eng._group_host.calls == [
        ("start", ("g-1",), {"audiences": AUD}),
        ("start", ("g-1",), {"audiences": AUD}),
        ("start", ("g-1",), {"conversation": ConversationOptions(3, 60, None)}),
        ("start", ("g-1",), {"conversation": ConversationOptions(3, 60, None)})]


def test_start_knowledge_true_keeps_principal_and_host_gates():
    args = _start(CONV, ', "knowledge": true')
    engine = SimpleNamespace(agent="one", _group_host=FakeHost(owner="one"))
    for who in ("local", "channel"):
        with pytest.raises(CommandFailed) as e:
            run(engine, args, who)
        assert e.value.code == "forbidden"
    assert engine._group_host.calls == []
    for host in (None, FakeHost(owner="other")):
        engine = SimpleNamespace(agent="one", _group_host=host)
        with pytest.raises(CommandFailed) as e:
            run(engine, args)
        assert e.value.code == "forbidden"
        assert host is None or host.calls == []


def test_start_knowledge_host_rejection_is_bad_request(eng):
    eng._group_host.errors = {"start": "knowledge must be an explicit boolean opt-in"}
    with pytest.raises(CommandFailed) as e:
        run(eng, _start(CONV, ', "knowledge": true'))
    assert e.value.code == "bad_request"
