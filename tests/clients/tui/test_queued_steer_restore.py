"""Saved pending input is visible and cancellable without starting model work."""
import json

import httpx

from exo.core.types import Message
from exo.protocol.client import Client
from test_tui import run, until


def queued(eng, sid, text="saved correction", author="principal"):
    msg = Message.user(text)
    msg.meta["author"] = author
    eng.store.queue_steer(sid, msg)
    return msg


def test_open_restores_original_pending_input_and_cancel(tmp_path):
    async def scenario(app, pilot, client, eng):
        sid = app.session
        msg = queued(eng, sid, author="delegate")
        await app.load_session(sid)
        await until(lambda: bool(app.pending_steers), pilot)
        mid, block = app.pending_steers[0]
        assert mid == msg.id and block.text == "saved correction"
        assert block.pending and "delegate" in block.meta and block.kind == "peer"
        assert not eng.store._q("SELECT 1 FROM events WHERE type IN ('model.call', 'turn.started')")
        assert await app.unsteer()
        assert "saved correction" in app.composer.text
        assert eng.store.pending_steers(sid) == []
        await app.load_session(sid)
        assert not app.pending_steers and not eng.store._q("SELECT 1 FROM events WHERE type IN ('model.call', 'turn.started')")
        eng.store.queue_steer(sid, msg)  # explicit same-ID requeue remains supported
        await app.load_session(sid)
        assert [mid for mid, _ in app.pending_steers] == [msg.id]
        assert app.pending_steers[0][1].text == msg.text()
    run(tmp_path, scenario)


def test_authoritative_empty_legacy_absence_and_event_race(tmp_path):
    async def scenario(app, pilot, client, eng):
        msg = queued(eng, app.session)
        await app.load_session(app.session)
        await pilot.pause()
        assert [mid for mid, _ in app.pending_steers] == [msg.id]
        app.restore_queued_steers({}, {msg.id})  # old core: absence is not cancellation
        assert [mid for mid, _ in app.pending_steers] == [msg.id]
        app.restore_queued_steers({"queued_steers": []}, {msg.id})
        assert app.pending_steers == []
        # A cancellation event received after request start wins over its stale snapshot.
        cutoff = app._steer_revision
        app.drop_pending_steer(msg.id)
        app.restore_queued_steers({"queued_steers": [{"id": msg.id, "content": [{"type": "text", "text": "stale"}]}]}, set(), cutoff)
        assert app.pending_steers == []
        # A newer accepted local steer is not deleted by an older empty snapshot.
        # A fresh authoritative snapshot can show an explicit same-ID requeue.
        app.restore_queued_steers({"queued_steers": [{"id": msg.id, "content": [{"type": "text", "text": "new"}]}]}, set())
        app.restore_queued_steers({"queued_steers": []}, set())
        assert [mid for mid, _ in app.pending_steers] == [msg.id]
    run(tmp_path, scenario)


def test_successful_transport_reconnect_hydrates_saved_queue_without_turn(tmp_path):
    async def scenario(app, pilot, client, eng):
        requests = []
        def handler(req):
            requests.append(req)
            event = {"id": f"e{len(requests)}", "seq": len(requests), "type": "notice", "body": {"text": "connected"}}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
        wire = Client(url="http://exod")
        await wire._http.aclose()
        wire._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))
        wire.on_reconnect = client.on_reconnect
        stream = wire.events(reconnect=True, backoff=0)
        try:
            await anext(stream)  # initial subscription
            msg = queued(eng, app.session)
            assert not app.pending_steers
            await anext(stream)  # a successful new subscription restores the queue
            await until(lambda: bool(app.pending_steers), pilot)
            assert [mid for mid, _ in app.pending_steers] == [msg.id]
            assert not eng.store._q("SELECT 1 FROM events WHERE type IN ('model.call', 'turn.started')")
            assert all(req.url.path == "/events" for req in requests)
            # Observer failure cannot prevent the next event from reaching the client.
            async def broken():
                raise RuntimeError("observer failed")
            wire.on_reconnect = broken
            assert (await anext(stream)).seq == 3
        finally:
            await stream.aclose()
            await wire.aclose()
    run(tmp_path, scenario)


def test_late_snapshot_cannot_revive_cancelled_input_or_cross_threads(tmp_path):
    import asyncio

    async def scenario(app, pilot, client, eng):
        msg = queued(eng, app.session)
        await app.load_session(app.session)
        original = client.call
        captured, release = asyncio.Event(), asyncio.Event()
        hold = True

        async def call(body):
            nonlocal hold
            result = await original(body)
            if type(body).TYPE == "session.get" and hold:
                hold = False
                captured.set()
                await release.wait()
            return result
        client.call = call
        task = asyncio.create_task(app.refresh_queued_steers())
        await asyncio.wait_for(captured.wait(), 2)
        assert await app.unsteer()  # successful cancellation wins even without observing its event
        release.set()
        await task
        assert not app.pending_steers
        # A snapshot for the old selection must never put input in a newly selected thread.
        eng.store.queue_steer(app.session, msg)
        hold = True
        captured.clear(); release.clear()
        task = asyncio.create_task(app.refresh_queued_steers())
        await asyncio.wait_for(captured.wait(), 2)
        await app.new_session()
        release.set()
        await task
        assert not app.pending_steers and app.session != eng.store._q("SELECT session FROM queued_steers")[0]["session"]
    run(tmp_path, scenario)
