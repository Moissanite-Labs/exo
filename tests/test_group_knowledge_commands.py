"""The `group` command family's knowledge ops: owner-only publish, member-scoped views.

The engine is a SimpleNamespace touching only ``eng.agent`` (this seat, always the
owner) and ``eng.store`` (a real SessionStore), like ``test_group_commands``.
Readers are explicit members validated against the stored group through
``GroupKnowledge``; the owner inspects a member's view and never impersonates one.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from exo import principal
from exo.core import group_commands
from exo.core.group_knowledge import GroupKnowledge
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from exo.server.engine import CommandFailed

OWNER = "one"


@pytest.fixture
def eng():
    store = SessionStore()
    engine = SimpleNamespace(agent=OWNER, store=store)
    groups = GroupStore(store)
    group = groups.create(
        owner=OWNER,
        mode="conductor",
        members=["lead", "one", "two"],
        known_profiles=["lead", "one", "two"],
        conductor="lead",
    )
    other = groups.create(
        owner="other",
        mode="socratic",
        members=["lead", "two"],
        known_profiles=["lead", "two"],
    )
    GroupKnowledge(groups)  # create the knowledge tables before any denial assertion
    yield engine, group.id, other.id
    store.close()


def run(engine, args, who="principal"):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return asyncio.run(group_commands.command(engine, SimpleNamespace(args=args)))
    finally:
        principal.CALLER.reset(token)


def js(**kw):
    return json.dumps(kw)


def publish(engine, gid, **kw):
    body = {
        "id": gid,
        "key": "plan",
        "text": "Ship M02 on Friday",
        "sources": ["session:s-1", "doc:roadmap.md"],
    }
    body.update(kw)
    return run(engine, "knowledge_publish " + json.dumps(body))


def test_denial_local_and_channel_before_any_store_write(eng):
    engine, gid, _ = eng
    for who in ("local", "channel"):
        for op in (
            "knowledge_publish",
            "knowledge_read",
            "knowledge_list",
            "knowledge_history",
        ):
            args = js(id=gid, key="plan", text="t", sources=["s"], member="lead")
            with pytest.raises(CommandFailed) as e:
                run(engine, f"{op} {args}", who)
            assert e.value.code == "forbidden"
    assert engine.store._q("SELECT * FROM swarm_knowledge") == []


def test_roundtrip_publish_read_list_history(eng):
    engine, gid, _ = eng
    first = publish(engine, gid)
    assert first["key"] == "plan" and first["revision"] == 1
    assert first["publisher"] == OWNER  # owner is always this seat
    assert first["sources"] == ["session:s-1", "doc:roadmap.md"]
    assert json.dumps(first)  # response is JSON-serializable

    second = publish(
        engine,
        gid,
        text="Ship M03 on Monday",
        sources=["session:s-2"],
        expected_revision=1,
    )
    assert second["revision"] == 2

    read = run(engine, "knowledge_read " + js(id=gid, member="lead", key="plan"))
    assert read["member"] == "lead" and read["view"] == "human_owner_view"
    assert (
        read["item"]["revision"] == 2 and read["item"]["text"] == "Ship M03 on Monday"
    )

    listed = run(engine, "knowledge_list " + js(id=gid, member="two"))
    assert listed["keys"] == ["plan"] and listed["total"] == 1

    hist = run(engine, "knowledge_history " + js(id=gid, member="one", key="plan"))
    assert [r["revision"] for r in hist["revisions"]] == [1, 2]
    assert hist["revisions"][0]["sources"] == ["session:s-1", "doc:roadmap.md"]

    assert (
        run(engine, "knowledge_read " + js(id=gid, member="lead", key="missing"))[
            "item"
        ]
        is None
    )
    assert run(engine, "knowledge_list " + js(id=gid, member="lead", limit=1))[
        "keys"
    ] == ["plan"]
    assert (
        run(
            engine,
            "knowledge_history " + js(id=gid, member="lead", key="plan", limit=1),
        )["revisions"][0]["revision"]
        == 2
    )


def test_cross_group_and_nonmember_fail_closed(eng):
    engine, gid, other_gid = eng
    publish(engine, gid)
    with pytest.raises(CommandFailed) as e:
        publish(engine, other_gid)
    assert e.value.code == "bad_request" and "no group" in str(e.value)
    for args in (
        js(id=other_gid, member="lead", key="plan"),
        js(id=gid, member="stranger", key="plan"),
    ):
        with pytest.raises(CommandFailed, match="no group|unknown member"):
            run(engine, "knowledge_read " + args)
        with pytest.raises(CommandFailed, match="no group|unknown member"):
            run(engine, "knowledge_list " + args.replace(', "key": "plan"', ""))
    # nothing from another seat's group leaked into this seat's view
    assert [
        dict(r) for r in engine.store._q("SELECT group_id, key FROM swarm_knowledge")
    ] == [{"group_id": gid, "key": "plan"}]


def test_stale_expected_revision_refused_and_nothing_written(eng):
    engine, gid, _ = eng
    publish(engine, gid)
    for expected in (0, 5):
        with pytest.raises(CommandFailed, match="revision"):
            publish(engine, gid, text="overwrite", expected_revision=expected)
    rows = [
        dict(r) for r in engine.store._q("SELECT revision, text FROM swarm_knowledge")
    ]
    assert rows == [{"revision": 1, "text": "Ship M02 on Friday"}]
    assert len(engine.store._q("SELECT * FROM swarm_knowledge_revisions")) == 1


@pytest.mark.parametrize(
    "args",
    [
        js(id="x", key="plan", text="t"),  # sources required
        js(id="x", key="plan", text="t", sources=[]),  # empty sources
        js(id="x", key="plan", text="t", sources="session:s-1"),  # bare string
        js(id="x", key="plan", text="t", sources=[1]),  # non-string source
        js(id="x", key="plan", text="t", sources=["s"] * 51),  # over 50 sources
        js(id="x", key="plan", text="t", sources=["s"], expected_revision=-1),
        js(id="x", key="plan", text="t", sources=["s"], expected_revision=True),
        js(id="x", key="plan", text="t", sources=["s"], expected_revision="1"),
        js(id="x", key="PLAN", text="t", sources=["s"]),  # bad key
        js(id="x", key="plan", text="", sources=["s"]),  # empty text
        js(id="x", key="plan", text=1, sources=["s"]),  # text not a string
        js(
            id="x", key="plan", text="t", sources=["s"], publisher="other"
        ),  # no impersonation
        js(
            id="x", key="plan", text="t", sources=["s"], member="lead"
        ),  # publish is owner-only
        '{"id": 7, "key": "plan", "text": "t", "sources": ["s"]}',  # id not a string
        js(id="  ", key="plan", text="t", sources=["s"]),  # blank id
        js(id="x", key="plan", text="t", sources=["s"], expected_revision=0, extra=1),
    ],
)
def test_malformed_publish(eng, args):
    engine, gid, _ = eng
    args = args.replace('"x"', f'"{gid}"')
    with pytest.raises(CommandFailed) as e:
        run(engine, "knowledge_publish " + args)
    assert e.value.code == "bad_request"
    assert engine.store._q("SELECT * FROM swarm_knowledge") == []


@pytest.mark.parametrize(
    "args",
    [
        js(id="x", key="plan"),  # member required
        js(id="x", member=3, key="plan"),  # member not a string
        js(id="x", member=" lead", key="plan"),  # padded member
        js(id="x", member="lead"),  # key required
        js(id="x", member="lead", key=""),  # blank key
        js(id="x", member="lead", key="plan", after=1),  # unknown field
        js(
            id="x", member="lead", key="plan", viewer="lead"
        ),  # no caller-supplied identity
    ],
)
def test_malformed_read(eng, args):
    engine, gid, _ = eng
    args = args.replace('"x"', f'"{gid}"')
    with pytest.raises(CommandFailed) as e:
        run(engine, "knowledge_read " + args)
    assert e.value.code == "bad_request"


@pytest.mark.parametrize(
    "op,args",
    [
        ("knowledge_list", js(id="x")),  # member required
        ("knowledge_list", js(id="x", member="lead", limit=0)),
        ("knowledge_list", js(id="x", member="lead", limit=1001)),
        ("knowledge_list", js(id="x", member="lead", limit=True)),
        ("knowledge_list", js(id="x", member="lead", key="plan")),
        ("knowledge_history", js(id="x", member="lead")),  # key required
        ("knowledge_history", js(id="x", member="lead", key="plan", limit=0)),
        ("knowledge_history", js(id="x", member="lead", key="plan", nope=1)),
        ("knowledge_history", js(id="x", key="plan")),
    ],
)
def test_malformed_list_and_history(eng, op, args):
    engine, gid, _ = eng
    with pytest.raises(CommandFailed) as e:
        args = args.replace(json.dumps("x"), json.dumps(gid))
        run(engine, op + " " + args)
    assert e.value.code == "bad_request"


def test_bad_json_and_unknown_op(eng):
    engine, gid, _ = eng
    for args in ("knowledge_publish {nope", "knowledge_read [1]", 'knowledge_list "s"'):
        with pytest.raises(CommandFailed, match="JSON|json|object"):
            run(engine, args)
    with pytest.raises(CommandFailed) as e:
        run(engine, "knowledge_forget {}")
    assert e.value.code == "bad_request" and "knowledge_history" in str(e.value)


def test_no_new_protocol_command():
    from exo.protocol import schema

    assert "command.run" in schema.COMMANDS
    assert len(schema.COMMANDS) <= schema.MAX_COMMANDS
    assert not [n for n in schema.COMMANDS if "knowledge" in n]
