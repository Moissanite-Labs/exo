"""Pinned chats: the /api/chat/pins routes over a fake source and a temporary Exo home."""
from __future__ import annotations

import json

import pytest

from exo_command import api
from test_chat import FakeSource, call, centre


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    return tmp_path


def pin(c, pid, pinned=True):
    return call(c, "POST", "/api/chat/pins", {"id": pid, "pinned": pinned})


def test_empty_when_nothing_pinned():
    assert call(centre(), "GET", "/api/chat/pins") == (200, {"pins": []})


def test_round_trip_survives_a_new_centre_and_unpin(home):
    assert pin(centre(), "s1") == (200, {"pins": ["s1"]})
    assert pin(centre(), "s2") == (200, {"pins": ["s1", "s2"]})
    assert pin(centre(), "s1") == (200, {"pins": ["s1", "s2"]})   # pinning twice keeps one entry
    assert call(centre(), "GET", "/api/chat/pins") == (200, {"pins": ["s1", "s2"]})
    assert json.loads((home / "state" / "chat-pins.json").read_text()) == {"pins": ["s1", "s2"]}
    assert pin(centre(), "s1", False) == (200, {"pins": ["s2"]})
    assert pin(centre(), "nope", False) == (200, {"pins": ["s2"]})
    assert call(centre(), "GET", "/api/chat/pins") == (200, {"pins": ["s2"]})
    assert not list((home / "state").glob("*.tmp"))


@pytest.mark.parametrize("bad", ["", "a b", "../x", "a/b", "x" * 201, "é", 5, None, ["a"]])
def test_bad_ids_are_refused(bad, home):
    status, data = pin(centre(), bad)
    assert status == 400 and "error" in data
    assert not (home / "state").exists()


@pytest.mark.parametrize("bad", ["true", 1, None])
def test_pinned_must_be_a_bool(bad):
    assert call(centre(), "POST", "/api/chat/pins", {"id": "s1", "pinned": bad})[0] == 400


def test_cap_is_200_and_unpin_still_works():
    c = centre()
    for i in range(200):
        assert pin(c, "s%d" % i)[0] == 200
    status, data = pin(c, "extra")
    assert status == 400 and "200" in data["error"]
    assert pin(c, "s0")[0] == 200   # already pinned: not a new entry
    status, data = pin(c, "s0", False)
    assert status == 200 and len(data["pins"]) == 199
    assert pin(c, "extra")[0] == 200


@pytest.mark.parametrize("text", ["{not json", "", "[1,2]", '{"pins": "s1"}', '{"pins": [1, "ok", "bad id", "ok"]}'])
def test_corrupt_file_reads_as_empty_or_cleaned(home, text):
    (home / "state").mkdir()
    (home / "state" / "chat-pins.json").write_text(text)
    status, data = call(centre(), "GET", "/api/chat/pins")
    assert status == 200
    assert data == {"pins": ["ok"] if "ok" in text and "bad id" in text else []}
    assert pin(centre(), "s9")[0] == 200   # and a write replaces the damaged file
    assert "s9" in call(centre(), "GET", "/api/chat/pins")[1]["pins"]


def test_non_principal_is_refused_and_nothing_is_written(home):
    src = FakeSource(fail={"command.run": "forbidden"})
    status, data = pin(centre(src), "s1")
    assert status == 403 and data["code"] == "forbidden"
    assert not (home / "state").exists()
    assert call(centre(src), "GET", "/api/chat/pins") == (200, {"pins": []})   # reading stays open


def test_principal_gate_is_a_harmless_rename_probe():
    class PrincipalSource(FakeSource):
        async def call(self, body):
            from exo_command.source import SourceError
            self.calls.append(body)
            raise SourceError("bad_request", "usage: /rename <title>")
    src = PrincipalSource()   # exact refusal after the core principal check
    assert pin(centre(src), "s1") == (200, {"pins": ["s1"]})
    probe = [b for b in src.calls if type(b).TYPE == "command.run"][0]
    assert (probe.name, probe.args, probe.session) == ("rename", "", "s1")


def test_other_core_errors_pass_through_without_writing(home):
    src = FakeSource(fail={"command.run": "unavailable"})
    assert pin(centre(src), "s1")[0] >= 500
    assert not (home / "state").exists()


def test_wrong_method_is_405():
    assert call(centre(), "DELETE", "/api/chat/pins")[0] == 405


def test_unrelated_bad_request_does_not_authorize_a_pin(home):
    src = FakeSource(fail={"command.run": "bad_request"})
    assert pin(centre(src), "s1")[0] == 400
    assert not (home / "state").exists()
