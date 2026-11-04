"""HTTP query bounds must reject numbers SQLite cannot bind and unbounded pages (#455)."""

import pytest

from .conftest import http

MAX_SEQ = 2**63 - 1


@pytest.mark.parametrize("path,param,minimum", [
    ("/v1/messages", "since", 0),
    ("/v1/inbox/beta", "since", 0),
    ("/v1/verify", "from", 1),
    ("/v1/verify", "to", 0),
])
@pytest.mark.parametrize("value", ["below", MAX_SEQ + 1, -(2**63) - 1, "abc", "1.5"])
def test_sequences_outside_sqlite_range_answer_422(bus, path, param, minimum, value):
    value = minimum - 1 if value == "below" else value
    # Verification is public; the read endpoints use a fresh signed request.
    if path == "/v1/verify":
        status, data, raw = http("GET", f"{bus.url}{path}", params={param: value})
    else:
        status, data, raw = bus.get("beta", path, **{param: value})
    assert status == 422, raw
    assert data["detail"][0]["loc"] == ["query", param]


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/inbox/beta"])
@pytest.mark.parametrize("limit", [-5, -1, 0, 1001, MAX_SEQ + 1, "abc", "1.5"])
def test_invalid_page_limits_answer_422(bus, path, limit):
    for i in range(3):
        assert bus.post("alpha", "beta", f"message {i}")[0] == 201
    status, data, raw = bus.get("beta", path, limit=limit)
    assert status == 422, raw
    assert data["detail"][0]["loc"] == ["query", "limit"]


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/inbox/beta"])
def test_valid_sequence_and_page_boundaries_still_work(bus, path):
    for i in range(3):
        assert bus.post("alpha", "beta", f"message {i}")[0] == 201
    status, data, raw = bus.get("beta", path, since=0, limit=1)
    assert status == 200 and [m["seq"] for m in data] == [1], raw
    status, data, raw = bus.get("beta", path, since=1, limit=1000)
    assert status == 200 and [m["seq"] for m in data] == [2, 3], raw
    status, data, raw = bus.get("beta", path, since=MAX_SEQ)
    assert status == 200 and data == [], raw


def test_valid_verification_boundaries_still_work(bus):
    status, data, raw = http("GET", f"{bus.url}/v1/verify", params={"from": 1, "to": 0})
    assert status == 200 and data["checked"] == 0, raw
    for i in range(3):
        assert bus.post("alpha", "beta", f"message {i}")[0] == 201
    status, data, raw = http("GET", f"{bus.url}/v1/verify", params={"from": 1, "to": MAX_SEQ})
    assert status == 200 and data["ok"] and data["checked"] == 3, raw
    status, data, raw = http("GET", f"{bus.url}/v1/verify", params={"from": MAX_SEQ})
    assert status == 200 and data["ok"] is False and data["checked"] == 0, raw
