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
