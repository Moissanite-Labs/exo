"""A client's event feed against a newer core: a value it does not know is kept, an event it cannot read is skipped
and logged, and the feed goes on (schema.py header, D-045)."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from exo.core.types import Message, from_dict
from exo.protocol import schema as s
from exo.protocol.client import Client

GAUGE = {"id": "e1", "seq": 1, "session": "s1", "type": "ui.panel", "ts": "",
         "body": {"panel_id": "p1", "action": "open", "kind": "gauge", "text": "gauge text"}}


def test_loose_decoding_keeps_a_literal_value_it_does_not_know():
    assert from_dict(Message, {"role": "robot"}, strict=False).role == "robot"
    with pytest.raises(ValueError):
        from_dict(Message, {"role": "robot"})
    ev = s.Event.from_json(json.dumps(GAUGE), strict=False)
    assert isinstance(ev.body, s.UiPanel) and ev.body.kind == "gauge" and ev.body.text == "gauge text"
    with pytest.raises(s.ProtocolError) as e:
        s.Event.from_json(json.dumps(GAUGE))
    assert e.value.code == "bad_body"
    notice = {"seq": 2, "type": "notice", "body": {"text": "x", "level": "debug"}}
    assert s.Event.from_json(json.dumps(notice), strict=False).body.level == "debug"
    # commands stay strict: an approval decision the core does not know is refused, never passed on
    cmd = {"id": "c1", "type": "approval.respond", "body": {"request_id": "r1", "decision": "maybe"}}
    with pytest.raises(s.ProtocolError) as e:
        s.Command.from_json(json.dumps(cmd))
    assert e.value.code == "bad_body"


def feed_client(frames: list[str]) -> tuple[Client, list[int]]:
    """A protocol Client whose /events stream serves ``frames`` (SSE data lines) once, then nothing."""
    requests: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(int(req.url.params.get("since", 0)))
        body = "".join(f"data: {f}\n\n" for f in frames) if len(requests) == 1 else ""
        return httpx.Response(200, content=body.encode())

    c = Client(url="http://exod")
    c._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))
    return c, requests


FRAMES = [
    json.dumps(GAUGE),  # a newer kind: decoded and passed on
    json.dumps({"id": "e2", "seq": 2, "type": "notice", "body": {"level": "info"}}),  # no text: cannot be read
    "{not json",  # cannot be read either
    json.dumps({"id": "e3", "seq": 3, "type": "notice", "body": {"text": "after"}}),
    json.dumps({"id": "e4", "seq": 4, "type": "brand.new.event", "body": {"x": 1}}),  # unknown type: a raw dict
]


@pytest.mark.parametrize("reconnect", [True, False])
def test_feed_skips_an_event_it_cannot_read_and_goes_on(reconnect, caplog):
    async def go():
        c, requests = feed_client(FRAMES)
        got = []
        async for ev in c.events(since=0, reconnect=reconnect, backoff=0.01):
            got.append(ev)
            if ev.seq == 4:
                break
        await c.aclose()
        return got, requests

    with caplog.at_level(logging.WARNING, logger="exo.protocol.client"):
        got, requests = asyncio.run(go())
    assert [ev.seq for ev in got] == [1, 3, 4]
    assert got[0].body.kind == "gauge" and got[1].body.text == "after" and got[2].body == {"x": 1}
    assert requests == [0]  # one stream: the unreadable events did not end it
    skipped = [r.getMessage() for r in caplog.records if r.name == "exo.protocol.client"]
    assert len(skipped) == 2 and "bad_body" in skipped[0] and "bad_json" in skipped[1]
