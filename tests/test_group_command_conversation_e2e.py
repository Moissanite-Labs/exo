"""Offline e2e: an authenticated ``/group`` command drives a conversation on a REAL CoreEngine.

``group_launch.create`` builds the owner CoreEngine (host injected) and the member
CoreEngines; nothing is spied or replaced. Only the provider transport is substituted
(``RuleFake.stream`` records each member's last prompt and delegates to the real rule
engine), under a temp ``EXO_HOME`` with ``EXO_NO_KEYCHAIN=1``. No network, no real provider.

Rules: ``MARK-ALPHA`` and ``ALPHA-REPLY``/``BETA-REPLY`` prompts are answered with an empty
reply (nothing is published), ``MARK-BETA`` answers ``BETA-REPLY``. Peer-reply timing is
racy by design, so assertions are on durable rows and on marks, never on poll timing; the
conversation idle timeout is far above the test bound, so an idle member can only run a
turn because the command's ``send`` woke it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from exo import principal
from exo.core import group_commands
from exo.core.models.fake import RuleFake
from exo.core.groups import GroupStore
from exo.group_launch import create
from exo.application import CoreEngine
from exo.server.engine import CommandFailed

BOUND = 10        # seconds; a deadline, never a pacing sleep
IDLE = 600        # idle poll far beyond BOUND: only a wake can run a turn within the test
MODELS = {"fake": {"rules": [{"match": "MARK-BETA", "steps": ["BETA-REPLY"]},
                             {"match": "BETA-REPLY", "steps": [""]},
                             {"match": "MARK-ALPHA", "steps": [""]}]},
          "roles": {"main": ["fake:x"]}}
MARKS = ("MARK-ALPHA", "MARK-BETA")


def seat_home(tmp_path, monkeypatch, names):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    for name in names:
        (home / "seats" / f"{name}.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    return home


@pytest.fixture
def seen(monkeypatch):
    """Last user prompt per provider call, in call order (real RuleFake still answers)."""
    prompts = []
    original = RuleFake.stream

    async def stream(this, messages, tools, **opts):
        prompts.append(next((m.text() for m in reversed(messages) if m.role == "user"), ""))
        async for event in original(this, messages, tools, **opts):
            yield event

    monkeypatch.setattr(RuleFake, "stream", stream)
    return prompts


def marks(seen, mark):
    return [p for p in seen if mark in p]


async def cmd(launch, args, who="principal"):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return await group_commands.command(launch.engine, SimpleNamespace(args=args))
    finally:
        principal.CALLER.reset(token)


async def until(pred, what):
    async def spin():
        while not pred():
            await asyncio.sleep(0)
    try:
        await asyncio.wait_for(spin(), BOUND)
    except asyncio.TimeoutError:
        raise AssertionError(f"never reached: {what}") from None


def consumed(launch, gid, text):
    """{member: consumed flag} for the one stored message whose text starts with ``text``."""
    rows = launch.store._q(
        "SELECT d.member, d.consumed FROM swarm_delivery d JOIN swarm_messages m ON m.seq=d.seq "
        "WHERE m.group_id=? AND m.text LIKE ?", (gid, text + "%"))
    return {r["member"]: bool(r["consumed"]) for r in rows}


def run(launch, body):
    """One loop with a hard deadline and a guaranteed ``aclose`` of every engine."""
    async def go():
        try:
            return await asyncio.wait_for(body(), BOUND * 3)
        finally:
            await asyncio.wait_for(launch.aclose(), BOUND)
    return asyncio.run(go())


def conv(turns=4):
    return json.dumps({"max_turns": turns, "idle_timeout": IDLE})


def test_command_starts_conversation_and_send_wakes_idle_member(tmp_path, monkeypatch, seen):
    seat_home(tmp_path, monkeypatch, ["builder", "checker", "owner"])
    launch = create(seat="owner", profiles=["builder", "checker"], mode="socratic")
    gid = launch.group.id

    async def body():
        # Pre-start human input is durable only; nothing runs without a conversation.
        first = await cmd(launch, f'send {{"id": "{gid}", "text": "MARK-ALPHA kickoff"}}')
        assert first["sender_kind"] == "human" and first["audience"] == ["builder", "checker"]
        assert seen == []
        assert launch.members == {}                         # no member engine before start
        started = await cmd(launch, f'start {{"id": "{gid}", "conversation": {conv()}}}')
        members = dict(launch.members)                      # real member engines now bound
        assert {m["profile"] for m in started["members"]} == {"builder", "checker"}
        # Initial member results: each member's real engine turn consumed the kickoff.
        await until(lambda: len(marks(seen, "MARK-ALPHA")) == 2
                    and consumed(launch, gid, "MARK-ALPHA") == {"builder": True, "checker": True},
                    "both members took their initial turn and acked the kickoff")
        before = len(seen)   # both members have drained their input and are idle-waiting
        sent = await cmd(launch, f'send {{"id": "{gid}", "audience": ["checker"], "text": "MARK-BETA wake"}}')
        assert sent["audience"] == ["checker"]
        await until(lambda: len(marks(seen, "MARK-BETA")) >= 1 and
                    consumed(launch, gid, "MARK-BETA wake") == {"checker": True},
                    "idle checker woken by the command send and acked")
        stopped = await cmd(launch, f'stop {{"id": "{gid}"}}')
        assert stopped == {"id": gid, "stopped": True, "member": None}
        statuses = await asyncio.wait_for(launch.wait(), BOUND)
        final = await cmd(launch, f'status {{"id": "{gid}"}}')
        return members, before, statuses, final

    members, before, statuses, final = run(launch, body)
    assert all(isinstance(e, CoreEngine) for e in (launch.engine, *members.values()))
    assert launch.engine.agent == "owner"
    assert set(members) == {"builder", "checker"}
    assert len(marks(seen, "MARK-BETA")) == 1                    # delivered once, to checker only
    assert len(seen) > before                                   # a turn ran after the idle point
    assert len(statuses) == 2
    assert final["id"] == gid and final["receipt"] is not None   # stop left a durable receipt
    assert launch.closed


def test_command_authorization_and_owner_binding(tmp_path, monkeypatch, seen):
    seat_home(tmp_path, monkeypatch, ["builder", "checker", "owner"])
    launch = create(seat="owner", profiles=["builder", "checker"], mode="socratic")
    gid = launch.group.id
    other = GroupStore(launch.store).create(owner="mallory", mode="socratic",
                                            members=["builder", "checker"],
                                            known_profiles=["builder", "checker"])

    async def body():
        for who in ("local", "channel"):
            for args in (f'start {{"id": "{gid}", "conversation": {conv()}}}',
                         f'send {{"id": "{gid}", "text": "MARK-ALPHA nope"}}',
                         f'stop {{"id": "{gid}"}}'):
                with pytest.raises(CommandFailed) as e:
                    await cmd(launch, args, who)
                assert e.value.code == "forbidden"
        # Another owner's group is invisible to this seat's engine, for send and start.
        for args in (f'send {{"id": "{other.id}", "text": "MARK-ALPHA x"}}',
                     f'start {{"id": "{other.id}", "conversation": {conv()}}}'):
            with pytest.raises(CommandFailed) as e:
                await cmd(launch, args)
            assert e.value.code == "bad_request"
        # Explicit opt-in is required: neither or both of audiences/conversation is refused.
        for args in (f'start {{"id": "{gid}"}}',
                     f'start {{"id": "{gid}", "audiences": {{"builder": ["checker"], "checker": ["builder"]}}, '
                     f'"conversation": {conv()}}}'):
            with pytest.raises(CommandFailed) as e:
                await cmd(launch, args)
            assert e.value.code == "bad_request"
        assert launch.members == {} and seen == []
        assert launch.store._q("SELECT 1 FROM swarm_messages") == []   # nothing persisted

    run(launch, body)


def test_stored_conductor_mode_isolates_audience_on_real_engines(tmp_path, monkeypatch, seen):
    seat_home(tmp_path, monkeypatch, ["lead", "w1", "w2", "owner"])
    launch = create(seat="owner", profiles=["lead", "w1", "w2"], mode="conductor", conductor="lead")
    gid = launch.group.id

    async def body():
        await cmd(launch, f'send {{"id": "{gid}", "audience": ["w1", "w2"], "text": "MARK-ALPHA go"}}')
        await cmd(launch, f'start {{"id": "{gid}", "conversation": {conv()}}}')
        await until(lambda: consumed(launch, gid, "MARK-ALPHA") == {"w1": True, "w2": True},
                    "both workers took the initial turn")
        await cmd(launch, f'send {{"id": "{gid}", "audience": ["w1"], "text": "MARK-BETA only-w1"}}')
        await until(lambda: consumed(launch, gid, "MARK-BETA") == {"w1": True}, "w1 woken and acked")
        await until(lambda: _w1_reply(launch, gid) is not None, "w1 reply stored")
        reply = _w1_reply(launch, gid)
        hist = {m: (await cmd(launch, f'observe {{"id": "{gid}", "member": "{m}"}}'))["messages"]
                for m in ("lead", "w1", "w2")}
        await cmd(launch, f'stop {{"id": "{gid}"}}')
        await asyncio.wait_for(launch.wait(), BOUND)
        return reply, hist

    reply, hist = run(launch, body)
    assert len(marks(seen, "MARK-BETA")) == 1                    # only the addressed worker ran it
    assert consumed(launch, gid, "MARK-BETA only-w1") == {"w1": True}   # no delivery row for lead/w2
    assert reply["audience"] == ["lead"]                      # worker output goes to the conductor only
    texts = {m: [x["text"] for x in msgs] for m, msgs in hist.items()}
    assert any("MARK-BETA" in t for t in texts["w1"])
    assert not any("MARK-BETA" in t or "BETA-REPLY" in t for t in texts["w2"])
    assert not any("MARK-BETA" in t for t in texts["lead"])
    assert any("MARK-ALPHA" in t for t in texts["w2"])
    assert launch.closed


def _w1_reply(launch, gid):
    rows = launch.store._q("SELECT * FROM swarm_messages WHERE group_id=? AND sender='w1' "
                           "AND text LIKE '%BETA-REPLY%'", (gid,))
    return {"audience": json.loads(rows[0]["audience"]), "text": rows[0]["text"]} if rows else None
