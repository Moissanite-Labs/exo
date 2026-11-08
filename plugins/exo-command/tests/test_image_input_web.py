"""Image input on the send route: limits, refusals that echo nothing, the body cap scoped to send, and the client wiring."""
from __future__ import annotations

import base64

from exo_command import api, server
from exo_command.server import STATIC

from test_chat import FakeSource, call, centre

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).decode()


def test_images_reach_turn_send_as_image_parts():
    src = FakeSource()
    c = centre(src)
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"text": "look", "images": [{"mime": "image/png", "data": PNG}]})[0] == 200
    send = src.calls[-1]
    assert send.text == "look" and len(send.parts) == 1
    assert (send.parts[0].type, send.parts[0].mime, send.parts[0].data) == ("image", "image/png", PNG)


def test_image_only_message_is_allowed_and_text_only_is_unchanged():
    src = FakeSource()
    c = centre(src)
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"images": [{"mime": "image/webp", "data": PNG}]})[0] == 200
    assert call(c, "POST", "/api/chat/sessions/s1/send", {"text": "plain"})[0] == 200
    assert src.calls[-1].parts == [] and src.calls[-1].text == "plain"
    assert call(c, "POST", "/api/chat/sessions/s1/send", {})[0] == 400


def test_bad_images_are_refused_before_any_command_without_echo():
    src = FakeSource()
    c = centre(src)
    secretish = "image/svg+xml;SECRET"
    bad = [
        {"images": "nope"},
        {"images": [{"mime": "image/png", "data": PNG}] * 5},
        {"images": ["x"]},
        {"images": [{"mime": secretish, "data": PNG}]},
        {"images": [{"mime": "image/png", "data": "not base64!!SECRET"}]},
        {"images": [{"mime": "image/png", "data": ""}]},
        {"images": [{"mime": "image/png", "data": "A" * (api.MAX_IMAGE_BYTES // 3 * 4 + 8)}]},
    ]
    for body in bad:
        code, data = call(c, "POST", "/api/chat/sessions/s1/send", dict(body, text="x"))
        assert code == 400, body
        assert "SECRET" not in str(data)
    assert src.calls == []


def test_large_body_cap_applies_only_to_send():
    assert server.SEND_ROUTE.fullmatch("/api/chat/sessions/s1/send")
    for other in ("/api/chat/sessions/s1/rename", "/api/feedback", "/api/chat/sessions/s1/send/x", "/api/chat/sessions"):
        assert not server.SEND_ROUTE.fullmatch(other)
    assert server.MAX_BODY == 64 * 1024


def test_send_cap_matches_the_core_and_fits_the_image_total():
    from exo.server import app as core
    assert server.MAX_SEND_BODY == core.MAX_BODY
    # All images at the total cap, as base64, plus the largest escaped text, still fit one core /command body.
    assert (api.MAX_IMAGES_TOTAL + 2) // 3 * 4 + api.MAX_TEXT * 6 + 4096 < core.MAX_BODY


def test_images_over_the_total_are_refused_and_at_the_total_accepted():
    src = FakeSource()
    c = centre(src)
    half = base64.b64encode(b"\0" * (api.MAX_IMAGES_TOTAL // 2)).decode()
    ok = {"images": [{"mime": "image/png", "data": half}] * 2}
    assert call(c, "POST", "/api/chat/sessions/s1/send", ok)[0] == 200
    over = {"images": [{"mime": "image/png", "data": half}] * 2 + [{"mime": "image/png", "data": PNG}]}
    code, data = call(c, "POST", "/api/chat/sessions/s1/send", over)
    assert code == 400 and "together" in str(data)
    assert len(src.calls) == 1


def test_client_keeps_images_in_memory_and_wires_the_composer():
    js = (STATIC / "images.js").read_text()
    chat = (STATIC / "chat.js").read_text()
    for banned in ("localStorage", "sessionStorage", "indexedDB", "innerHTML", "postMessage"):
        assert banned not in js
    assert 'from "./images.js"' in chat and "images.take()" in chat and "images.restore(shots)" in chat
    assert "images: images.payload(shots)" in chat
    assert "MAX_IMAGES_TOTAL" in js
    # A failed send only refills its own conversation's tray, and switching conversation empties the tray.
    assert "current === origin) images.restore(shots)" in chat
    assert "if (current !== id) images.take()" in chat
