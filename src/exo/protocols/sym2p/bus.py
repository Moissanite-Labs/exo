"""SYM-2P as an optional body format inside bus envelopes.

The bus envelope `body` stays a string on the wire, so a structured body is JSON
text inside it: {"data":"<sym2p packet>","fmt":"sym2p"} in canonical form (sorted
keys, no whitespace, UTF-8). Anything shown to a person goes through
`render_body`, which returns plain English for SYM-2P bodies and the text
unchanged for everything else.
"""
from __future__ import annotations

import json

from . import Sym2pError, render, validate

BODY_FMT = "sym2p"


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_exo(data: str) -> bool:
    """An Exo-profile line (exo.wire: string atoms, opcodes 100+), which the upstream validator rejects."""
    from exo.wire.grammar import is_exo

    fields = data.split("|")
    return data.startswith("2|") and len(fields) in (13, 14) and is_exo(fields[9])


def wrap(data: str) -> str:
    """Validate one SYM-2P packet line and return the canonical bus body string.

    Upstream packets go through the vendored validator; Exo-profile lines (delegation briefs and
    receipts, docs/AGENT-WIRE.md) go through ``exo.wire``.
    """
    if not isinstance(data, str):
        raise Sym2pError(["ERR:TYPE data: a SYM-2P packet travels as one text line"])
    if _is_exo(data):
        from exo.wire import check_line

        errs = check_line(data)
    else:
        errs = validate(data)
    if errs:
        raise Sym2pError(errs)
    return _canonical({"data": data, "fmt": BODY_FMT})


def unwrap(body: str) -> str | None:
    """The SYM-2P line if `body` is a wrapped SYM-2P body, else None. Does not validate."""
    if not isinstance(body, str) or not body.startswith("{"):
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if (isinstance(obj, dict) and set(obj) == {"data", "fmt"} and obj["fmt"] == BODY_FMT
            and isinstance(obj["data"], str)):
        return obj["data"]
    return None


def render_body(body: str, roles: dict | None = None) -> str:
    """Plain English for a SYM-2P body; any other body comes back unchanged.

    A wrapped body that fails validation renders as a short notice, never as raw packet text.
    """
    data = unwrap(body)
    if data is None:
        return body
    if _is_exo(data):
        from exo.wire import WireError, render as render_exo

        try:
            return render_exo(data)
        except WireError as e:
            return f"[unreadable SYM-2P packet: {e.errors[0] if e.errors else 'invalid'}]"
    try:
        return render(data, roles=roles)
    except Sym2pError as e:
        return f"[unreadable SYM-2P packet: {e.errors[0] if e.errors else 'invalid'}]"
