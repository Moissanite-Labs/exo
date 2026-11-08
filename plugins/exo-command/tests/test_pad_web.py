"""The Pad view's web routes against a fake core: read, save, conflict and pad-off, with no text echoed on refusals."""
from __future__ import annotations

import json

from exo_command.server import STATIC
from test_chat import FakeSource, call, centre


class PadSource(FakeSource):
    """Answers the core `pad` command from a fixed reply, and records the parsed args."""

    def __init__(self, reply=None, fail=None):
        super().__init__(fail=fail)
        self.reply = reply or {}
        self.args = []

    async def call(self, body):
        if type(body).TYPE == "command.run":
            self.calls.append(body)
            if "command.run" in self.fail:
                return await super().call(body)
            self.args.append(json.loads(body.args))
            return {"output": self.reply}
        return await super().call(body)


def test_get_reads_the_current_revision():
    src = PadSource({"ok": True, "snapshot": {"pad": "main", "revision": 3, "created": "t", "document": "ideas"}})
    code, data = call(centre(src), "GET", "/api/pad")
    assert code == 200 and data == {"text": "ideas", "revision": 3}
    assert src.args == [{"action": "read"}]


def test_empty_pad_reads_as_revision_zero():
    code, data = call(centre(PadSource({"ok": True, "snapshot": {"revision": 0, "document": ""}})), "GET", "/api/pad")
    assert code == 200 and data == {"text": "", "revision": 0}


def test_save_sends_an_update_and_returns_only_the_revision():
    src = PadSource({"ok": True, "snapshot": {"revision": 4, "document": "new text"}})
    code, data = call(centre(src), "POST", "/api/pad", {"text": "new text", "revision": 3})
    assert code == 200 and data == {"revision": 4}
    assert src.args == [{"action": "update", "expected_revision": 3, "document": "new text"}]


def test_conflict_is_409_with_the_current_revision_and_no_text():
    src = PadSource({"ok": False, "code": "revision_conflict", "current": {"revision": 7, "document": "SECRET other"}})
    code, data = call(centre(src), "POST", "/api/pad", {"text": "mine", "revision": 3})
    assert code == 409 and data["revision"] == 7 and data["code"] == "conflict"
    assert "SECRET" not in json.dumps(data) and "mine" not in json.dumps(data)


def test_bad_bodies_are_refused_before_any_command_without_echo():
    for body in ({"revision": 1}, {"text": 5, "revision": 1}, {"text": "SECRET", "revision": -1},
                 {"text": "SECRET", "revision": "1"}, {"text": "SECRET", "revision": True}, {"text": "SECRET"}):
        src = PadSource()
        code, data = call(centre(src), "POST", "/api/pad", body)
        assert code == 400, body
        assert src.args == [] and "SECRET" not in json.dumps(data)


def test_pad_off_or_older_core_answers_501():
    for fault in ("unsupported", "unknown_command"):
        code, _ = call(centre(PadSource(fail={"command.run": fault})), "GET", "/api/pad")
        assert code == 501


def test_non_principal_is_forbidden():
    code, _ = call(centre(PadSource(fail={"command.run": "forbidden"})), "POST", "/api/pad", {"text": "x", "revision": 0})
    assert code == 403


def test_page_is_wired_and_keeps_text_in_memory_only():
    js = (STATIC / "pad.js").read_text()
    assert '["pad", "Pad", pad]' in (STATIC / "app.js").read_text()
    for banned in ("localStorage", "sessionStorage", "indexedDB", "innerHTML", "setInterval"):
        assert banned not in js, banned
    assert '"/api/pad"' in js and "e.status === 409" in js


def test_http_pad_accepts_full_escaped_document_without_widening_other_routes():
    import asyncio
    from exo_command.server import MAX_BODY, MAX_PAD_BODY, WebServer

    async def main():
        src = PadSource({"ok": True, "snapshot": {"revision": 1}})
        web = WebServer(centre(src), token="t0k")
        port = await web.start("127.0.0.1", 0)

        async def post(path, body, length=None):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                          f"X-Exo-Token: t0k\r\nContent-Length: {len(body) if length is None else length}\r\n\r\n").encode() + body)
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            await writer.wait_closed()
            return int(reply.split()[1]), reply.split(b"\r\n\r\n", 1)[1]

        try:
            for encoded in ('\\n' * 32768, '\\u0061' * 32768):
                body = ('{"text":"' + encoded + '","revision":0}').encode()
                assert MAX_BODY < len(body) <= MAX_PAD_BODY
                status, reply = await post("/api/pad", body)
                assert status == 200 and json.loads(reply) == {"revision": 1}
            count = len(src.args)
            for path, limit in (("/api/pad", MAX_PAD_BODY), ("/api/chat/sessions", MAX_BODY),
                                ("/api/providers", MAX_BODY), ("/api/approvals/r9", MAX_BODY)):
                status, reply = await post(path, b"", limit + 1)
                assert status == 400 and json.loads(reply) == {"error": "body too large"}
            assert len(src.args) == count
        finally:
            await web.close()
    asyncio.run(main())


def test_reload_and_save_preserve_drafts_while_requests_are_in_flight(tmp_path):
    import shutil
    import subprocess
    import pytest
    node = shutil.which('node')
    if not node:
        pytest.skip('node required for Pad request race test')
    source = (STATIC / 'pad.js').read_text().replace('import { h, api, errorBox, pageHead } from "./dom.js";', '')
    script = r'''
import assert from 'node:assert/strict';
const els = [], pending = [];
function h(tag, attrs, ...kids) {
  const el = {tag, ...attrs, value: '', children: kids, replaceChildren(...c) {this.children = c;}};
  els.push(el); return el;
}
function errorBox(t) {return t;}
function pageHead(t) {return t;}
function api(...args) {return new Promise((resolve, reject) => pending.push({args, resolve, reject}));}
const tick = () => new Promise(resolve => setImmediate(resolve));
''' + source + r'''
const stop = mount({replaceChildren(){}});
const area = els.find(e => e.tag === 'textarea');
const [save, reload] = els.filter(e => e.tag === 'button');
assert.equal(area.disabled, true);
pending.shift().resolve({text:'base', revision:1}); await tick();
area.value = 'draft'; area.oninput();
await reload.onclick(); assert.equal(pending.length, 0); // confirm discard first
const loading = reload.onclick();
assert.equal(area.disabled, true); assert.equal(save.disabled, true); assert.equal(reload.disabled, true);
await save.onclick(); await reload.onclick(); assert.equal(pending.length, 1);
pending.shift().reject(new Error('offline')); await loading;
assert.equal(area.value, 'draft'); assert.equal(area.disabled, false);
const saving = save.onclick();
area.value = 'newer draft'; area.oninput();
pending.shift().resolve({revision:2}); await saving;
assert.equal(area.value, 'newer draft'); assert.equal(save.disabled, false);
const conflict = save.onclick();
pending.shift().reject(Object.assign(new Error('conflict'), {status:409, data:{revision:3}})); await conflict;
assert.equal(area.value, 'newer draft'); assert.equal(save.disabled, false);
const disabled = save.onclick();
pending.shift().reject(Object.assign(new Error('off'), {status:501})); await disabled;
assert.equal(area.value, 'newer draft'); assert.equal(area.readOnly, true);
assert.equal(save.disabled, true); stop();
'''
    path = tmp_path / 'pad-races.mjs'
    path.write_text(script)
    result = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
