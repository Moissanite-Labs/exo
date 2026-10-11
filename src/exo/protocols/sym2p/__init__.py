"""SYM-2P: a compact agent-to-agent message language, as a library.

Two profiles are supported:

- **1.0 (JSON)**, the tagged normative profile: one canonical JSON object per line
  with a fixed key order (`v1` module).
- **2.0 draft (dense)**, positional `|` fields with an s-expression body, about 100
  bytes per brief (`v2` module).

Vendored from aska-digital/protean-sym2p (MIT). See NOTICE. Party names are not part
of the library: renderers take an optional `roles` mapping supplied by the caller
(for example from the principal store's `agents/`).

Public API:
    validate(text_or_packet, **opts) -> list[str]
    encode_brief(**fields) -> str
    decode(text) -> dict
    render(text_or_dict, roles=None) -> str
"""
from __future__ import annotations

import json

from . import v1, v2

__all__ = ["validate", "encode_brief", "decode", "render", "profile_of", "Sym2pError", "v1", "v2"]

SOURCE_REPO = "aska-digital/protean-sym2p"
SOURCE_COMMIT = "2d93ed603e94ddd3e4ddcab4b1d5fc4227832a1b"


class Sym2pError(ValueError):
    """Raised by encode and decode when a packet does not validate. `.errors` lists why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def profile_of(packet) -> str | None:
    """'1.0' for a JSON packet (dict or text), '2.0' for a dense line, None otherwise."""
    if isinstance(packet, dict):
        return "1.0"
    if isinstance(packet, (bytes, bytearray)):
        packet = packet.decode("utf-8", "replace")
    if not isinstance(packet, str):
        return None
    s = packet.strip()
    if s.startswith("{"):
        return "1.0"
    if s.startswith("2|"):
        return "2.0"
    return None


def _fmt(findings) -> list[str]:
    return [f"{f['code']} {f['field']}: {f['msg']}" for f in findings]


def validate(text_or_packet, **opts) -> list[str]:
    """Return typed diagnostics; an empty list means valid.

    For 1.0 packets, `opts` may carry `kind`, `now` (ISO-8601 Z or datetime), `state`
    ({id: version}), `require_refs`, `strict_canonical`, `verify_hashes`, `run_state`.
    """
    prof = profile_of(text_or_packet)
    if prof == "1.0":
        if isinstance(text_or_packet, dict):
            opts.pop("strict_canonical", None)
            findings, _ = v1.validate_doc(text_or_packet, **opts)
        else:
            findings, _ = v1.validate_text(text_or_packet, **opts)
        return _fmt(findings)
    if prof == "2.0":
        line = text_or_packet.decode() if isinstance(text_or_packet, (bytes, bytearray)) else text_or_packet
        if line != line.strip():
            return ["ERR:CANON line 1: leading or trailing whitespace"]
        return v2.check_line(line, 1)
    return ["ERR:SYN <document>: not a SYM-2P packet (want a JSON object or a '2|' line)"]


# --- encoding -----------------------------------------------------------------

def _code(value, table: dict, field: str) -> int:
    if isinstance(value, bool):
        raise Sym2pError([f"ERR:TYPE {field}: bool is not a code"])
    if isinstance(value, int):
        return value
    for code, name in table.items():
        if name == value:
            return code
    aliases = {"ack": 1, "verify": 2} if table is v2.RQS else {}
    if value in aliases:
        return aliases[value]
    raise Sym2pError([f"ERR:CODE {field}: unknown name {value!r}"])


def _atom(x) -> str:
    return str(x)


def encode_brief(*, id: str, tk: str, f="coordinator", t="builder", a="assign",
                 st="proposed", cf: int = 0, cv: int = 0, scope=None, unit: str = "sec",
                 nomod=None, accept=None, body: str = "", rq=0, base: str = "",
                 exp: int = 0, rcp: bool = False) -> str:
    """Build one validated SYM-2P 2.0 dense line.

    Role, act, state and request accept registry names or integer codes.
    `scope=(target, lo, hi)` adds `(10 target (20 unit (lo hi)))`.
    `nomod="*"` (or a path symbol) adds `(11 (21 paths))`.
    `accept=(program, ok, bad)` adds `(12 (22 program ok bad))`.
    `body` appends raw canonical s-expressions after those.
    `rcp=True` appends the 16-hex checksum field.
    """
    exprs = []
    if scope is not None:
        target, lo, hi = scope
        exprs.append(f"(10 {target} (20 {unit} ({_atom(lo)} {_atom(hi)})))")
    if nomod is not None:
        exprs.append(f"(11 (21 {nomod}))")
    if accept is not None:
        prog, ok, bad = accept
        exprs.append(f"(12 (22 {prog} {ok} {bad}))")
    if body:
        exprs.append(body)
    fields = ["2", str(id), str(tk),
              str(_code(f, v2.ROLES, "F")), str(_code(t, v2.ROLES, "T")),
              str(_code(a, v2.ACTS, "A")), str(_code(st, v2.STATES, "ST")),
              str(cf), str(cv), "".join(exprs), str(_code(rq, v2.RQS, "RQ")),
              str(base or ""), str(exp)]
    line = "|".join(fields)
    if rcp:
        line = v2.add_rcp(line)
    errs = v2.check_line(line, 1)
    if errs:
        raise Sym2pError(errs)
    return line


# --- decoding -----------------------------------------------------------------

def decode(text) -> dict:
    """Parse and validate a packet. Raises Sym2pError when invalid.

    A 2.0 line decodes to {"profile": "2.0", "v", "id", "tk", "f", "t", "a", "st",
    "cf", "cv", "body", "exprs", "rq", "base", "exp", "sum", plus "<field>_name"
    for coded fields}. A 1.0 packet decodes to {"profile": "1.0", "packet": {...}}.
    """
    errs = validate(text)
    if errs:
        raise Sym2pError(errs)
    if isinstance(text, dict):
        return {"profile": "1.0", "packet": dict(text)}
    if profile_of(text) == "1.0":
        return {"profile": "1.0", "packet": json.loads(text)}
    fields, _ = v2.split_fields(text)
    _, pid, tk, f, t, a, st, cf, cv, body, rq, base, exp = fields[:13]
    exprs, _ = v2.parse_all(body) if body.strip() else ([], None)
    out = {"profile": "2.0", "v": 2, "id": pid, "tk": tk, "f": int(f), "t": int(t),
           "a": int(a), "st": int(st), "cf": int(cf), "cv": int(cv), "body": body,
           "exprs": exprs, "rq": int(rq), "base": base, "exp": int(exp),
           "sum": fields[13] if len(fields) == 14 else None}
    out.update(f_name=v2.ROLES[out["f"]], t_name=v2.ROLES[out["t"]], a_name=v2.ACTS[out["a"]],
               st_name=v2.STATES[out["st"]], rq_name=v2.RQS[out["rq"]])
    return out


# --- rendering ----------------------------------------------------------------

_V1_RQ = {"ack": "acknowledgement", "upd": "an update", "ver": "verification",
          "rev": "review", "exec": "execution", "src": "sources", "exp": "an explanation",
          "full": "full state", "decide": "a decision"}


def _render_v1(p: dict, roles: dict | None) -> str:
    name = lambda x: (roles or {}).get(x, x)  # noqa: E731
    head = f"{p.get('a', '?')} {p.get('id', '?')} [{name(p.get('f', '?'))} -> {name(p.get('t', '?'))}]"
    if "st" in p:
        head += f" ({p['st']})"
    scores = [f"{label} {p[k]}" for k, label in (("cf", "confidence"), ("cov", "coverage"),
                                                   ("err", "error risk")) if k in p]
    if scores:
        head += " " + ", ".join(scores)
    out = [head]
    if "s" in p:
        out.append(f"Subject: {p['s']}.")
    for c in p.get("cond", []):
        out.append(c[0].upper() + c[1:] + ("" if c.endswith(".") else "."))
    if p.get("ev"):
        out.append("Evidence: " + ", ".join(p["ev"]) + ".")
    if p.get("ce"):
        out.append("Counter-evidence: " + ", ".join(p["ce"]) + ".")
    for op in p.get("ops", []):
        out.append(f"Change: {op.get('op')} {op.get('path', '')}".rstrip() + ".")
    if "rq" in p:
        out.append(f"Requires: {_V1_RQ.get(p['rq'], p['rq'])}.")
    if "base" in p:
        out.append(f"Base: {p['base']}.")
    if "exp" in p:
        out.append(f"Expires at step {p['exp']}.")
    if "auth" in p:
        au = p["auth"]
        out.append(f"Approved by a person for {au.get('scope')} until {au.get('expires_at')} "
                   f"({au.get('ref')}).")
    err = (p.get("ext") or {}).get("err")
    if err:
        have = (p.get("ext") or {}).get("have")
        out.append(f"Error: {err}" + (f", receiver holds {have}." if have else "."))
    return "\n".join(out)


def render(text_or_dict, roles: dict | None = None) -> str:
    """Plain English for a person. Never shows the raw packet.

    `roles` maps role codes (2.0) or role ids (1.0) to display names.
    Raises Sym2pError when the packet does not validate.
    """
    d = decode(text_or_dict) if not (isinstance(text_or_dict, dict) and "profile" in text_or_dict) \
        else text_or_dict
    if d["profile"] == "1.0":
        return _render_v1(d["packet"], roles)
    line = "|".join(str(x) for x in (2, d["id"], d["tk"], d["f"], d["t"], d["a"], d["st"], d["cf"],
                                      d["cv"], d["body"], d["rq"], d["base"], d["exp"]))
    if d.get("sum"):
        line += "|" + d["sum"]
    text, err = v2.render_packet(line, roles=roles)
    if err:
        raise Sym2pError([err])
    return text
