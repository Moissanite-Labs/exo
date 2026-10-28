"""Decoder nesting failures stay HTTP input errors (follow-up to #751).

Inject RecursionError only for designated request bytes: decoder nesting limits differ across Python versions.
The real HTTP readers, routing, authentication and dispatch guards still run.
"""
import json

import pytest

from exo_command import providers
from exo_command.server import MAX_BODY
from test_approval_json_body import post

DEEP_JSON = [b"[" * 1500 + b"]" * 1500, b'{"nested":' * 1500 + b"null" + b"}" * 1500]
ROUTES = ["/api/approvals/r1", "/api/chat/sessions", "/api/providers/default"]


@pytest.fixture(autouse=True)
def decoder_nesting_failure(monkeypatch):
    decode = json.loads

    def loads(data, *args, **kwargs):
        if isinstance(data, bytes) and data in DEEP_JSON:
            raise RecursionError("synthetic JSON decoder nesting limit")
        return decode(data, *args, **kwargs)

    monkeypatch.setattr(json, "loads", loads)


@pytest.fixture(autouse=True)
def no_provider_dispatch(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("invalid JSON reached the provider handler")
    monkeypatch.setattr(providers, "handle", unexpected)


@pytest.mark.parametrize("data", DEEP_JSON, ids=["array", "object"])
@pytest.mark.parametrize("path", ROUTES)
def test_deep_json_returns_400_before_dispatch(data, path):
    assert len(data) < MAX_BODY
    reply, calls = post(data, path)
    expected = "bad json" if path.startswith("/api/approvals/") else "body must be a json object"
    assert reply == (400, {"error": expected})
    assert calls == []


@pytest.mark.parametrize("data", DEEP_JSON, ids=["array", "object"])
@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("token", ["", "wrong-token"])
def test_deep_json_does_not_bypass_page_token_checks(data, path, token):
    reply, calls = post(data, path, token=token)
    assert reply[0] == 403 and "page token" in reply[1]["error"]
    assert calls == []


@pytest.mark.parametrize("data", DEEP_JSON, ids=["array", "object"])
def test_launch_keeps_refusing_deep_json_without_a_core_command(data):
    reply, calls = post(data, "/api/launch")
    assert reply[0] == 403 and reply[1]["error"]
    assert calls == []
