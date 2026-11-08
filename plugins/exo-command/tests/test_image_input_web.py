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


def test_attachment_reads_are_serialized_and_cancelled_on_take(tmp_path):
    import subprocess
    script = r"""
import assert from 'node:assert/strict';
import { attachImages } from '__IMAGES__';
class El {
  constructor() { this.listeners = {}; this.classList = { add(){}, remove(){} }; }
  addEventListener(k, f) { this.listeners[k] = f; }
  setAttribute() {} appendChild() {} replaceChildren() {} click() {}
}
globalThis.document = { createElement: () => new El(), createTextNode: () => new El() };
const readers = [];
globalThis.FileReader = class {
  readAsDataURL(f) { this.file = f; readers.push(this); }
  finish() { this.result = 'data:image/png;base64,AA=='; this.onload(); }
};
const tick = async () => { for (let i=0; i<8; i++) await Promise.resolve(); };
const input = new El(), wrap = new El(), notices = [];
const images = attachImages({ input, wrap, flash: x => notices.push(x) });
const f = size => ({ name:'image', type:'image/png', size });
const drop = files => wrap.listeners.drop({ preventDefault(){}, dataTransfer:{ files } });
drop([f(1)]); await tick(); assert(images.pending());
images.take(); readers.shift().finish(); await tick();
assert.equal(images.count(), 0); assert.equal(images.pending(), false);
drop([f(3*1024*1024)]); drop([f(3*1024*1024)]); await tick();
readers.shift().finish(); await tick();
assert.equal(readers.length, 0); assert.equal(images.count(), 1); assert(!images.pending());
assert(notices.some(x => x.includes('together')));
images.take();
drop([f(1),f(1),f(1)]); drop([f(1),f(1),f(1)]); await tick();
for (let i=0; i<4; i++) { readers.shift().finish(); await tick(); }
assert.equal(images.count(),4); assert.equal(readers.length,0); assert(!images.pending());
assert(notices.some(x => x.includes('at most')));
images.take(); drop([f(3*1024*1024)]); await tick();
images.restore([{mime:'image/png',data:'AA==',size:3*1024*1024,name:'restored'}]);
readers.shift().finish(); await tick(); assert.equal(images.count(),1);
assert(notices.some(x => x.includes('restoring')));
""".replace("__IMAGES__", (STATIC / "images.js").as_uri())
    file = tmp_path / "images.mjs"
    file.write_text(script)
    result = subprocess.run(["node", str(file)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
