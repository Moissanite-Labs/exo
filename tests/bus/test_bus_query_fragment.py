"""A live relay verifies the query the app parses. A '#' in the request target ends the query for a URL parser but
not for the parameter parser, so a suffix after '#' must not add parameters the signature never covered."""

import socket
from urllib.parse import urlencode

from exo.bus import wire


def _headers(bus, query: str) -> dict:
    return wire.request_headers(
        "beta", bus.keys["beta"], "GET", "/v1/messages", query=query
    )


def _raw_get(bus, target: str, headers: dict):
    lines = [
        f"GET {target} HTTP/1.1",
        f"Host: 127.0.0.1:{bus.port}",
        "Connection: close",
    ]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    with socket.create_connection(("127.0.0.1", bus.port), timeout=10) as s:
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return int(data.split(b" ", 2)[1]), data


def test_a_suffix_after_a_fragment_is_refused_on_the_live_relay(bus):
    query = urlencode({"since": 0, "limit": 200, "to": "team:core"})
    status, _ = _raw_get(bus, f"/v1/messages?{query}", _headers(bus, query))
    assert status == 200
    for suffix in ("#&to=group", "#&thread=zzz", "#&since=1&limit=1"):
        status, body = _raw_get(
            bus, f"/v1/messages?{query}{suffix}", _headers(bus, query)
        )
        assert status == 401 and b"bad request signature" in body, suffix
