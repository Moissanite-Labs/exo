"""SYM-2P/2 lines with Exo's vocabulary: tokenize, parse, check, canonical form, repair.

A line keeps the SYM-2P/2 shape: ``2|ID|TK|F|T|A|ST|CF|CV|BODY|RQ|BASE|EXP[|SUM]``.
Exo adds three things inside that shell (docs/AGENT-WIRE.md section 4):

- **String atoms** in BODY: a JSON string literal (``"two words"``), with ``|`` written
  as ``\\u007c`` so the field split stays trivial. Upstream bodies hold only numbers and
  symbols, which cannot carry a task in words.
- **Extension opcodes 100 to 199** (upstream's proposed domain band). A body whose first
  expression is ``(100 1)`` uses Exo vocabulary version 1. Upstream readers reject it
  loudly ("unknown opcode 100") instead of misreading it.
- **Two task states**, 100 failed and 101 cancelled, which the upstream registry lacks.

A line with no ``(100 ...)`` marker is checked by the vendored upstream validator
unchanged (``exo.protocols.sym2p.v2``), so pure upstream packets keep working.
"""

from __future__ import annotations

import json
import re

from exo.protocols.sym2p import v2 as up

PROFILE = 1  # Exo vocabulary version carried by (100 N)
ROLES, ACTS, RQS = up.ROLES, up.ACTS, up.RQS
STATES = {**up.STATES, 100: "failed", 101: "cancelled"}

# opcode -> (name, shape). Shape letters: n unsigned int, s string, y symbol, t token;
# a trailing * means "zero or more of the last letter".
OPS: dict[int, tuple[str, str]] = {
    100: ("profile", "n"),
    101: ("goal", "s"),
    102: ("context", "s"),
    103: ("tools", "y*"),
    104: ("limits", "nnn"),     # turns, tokens, seconds (0 = no limit)
    105: ("depth", "nn"),       # this depth, max depth
    106: ("cwd", "s"),
    107: ("accept", "s"),
    108: ("tier", "y"),         # model tier asked for: fast, standard or deep
    110: ("summary", "s"),
    111: ("usage", "nnn"),      # input tokens, output tokens, cost in micro-USD
    112: ("work", "nnn"),       # duration ms, tool rounds, tool calls
    113: ("files", "s*"),
    114: ("error", "ys"),       # code symbol, message
    115: ("child", "t"),        # child session id
    116: ("route", "ysn"),      # tier used, resolved model, escalated (0 or 1)
    117: ("asked_route", "s"),  # the named delegation route the parent asked for (D02)
}
CODE = {name: op for op, (name, _) in OPS.items()}
SYM = re.compile(r"^(?:\*|[A-Za-z][A-Za-z0-9_./-]*)$")
_DEC = json.JSONDecoder()


class WireError(ValueError):
    """A line or body that does not parse or check. ``errors`` lists typed reasons."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


class Str(str):
    """A string atom (quoted on the wire), as opposed to a bare symbol or number."""


# ---------------------------------------------------------------- body


def tokenize(body: str) -> list[str]:
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c in "()":
            out.append(c)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            try:
                val, i = _DEC.raw_decode(body, i)
            except ValueError:
                raise WireError("ERR:BODY unterminated or bad string atom") from None
            out.append(Str(val))
        else:
            j = i
            while j < n and not body[j].isspace() and body[j] not in '()"':
                j += 1
            out.append(body[i:j])
            i = j
    return out


def parse(body: str) -> list[list]:
    """Concatenated s-expressions to nested lists. Atoms stay strings (Str for quoted)."""
    toks, pos = tokenize(body), 0

    def expr() -> list:
        nonlocal pos
        pos += 1
        items: list = []
        while pos < len(toks) and not (toks[pos] == ")" and not isinstance(toks[pos], Str)):
            if toks[pos] == "(" and not isinstance(toks[pos], Str):
                items.append(expr())
            else:
                items.append(toks[pos])
                pos += 1
        if pos >= len(toks):
            raise WireError("ERR:BODY unbalanced parentheses")
        pos += 1
        return items

    out = []
    while pos < len(toks):
        if toks[pos] != "(" or isinstance(toks[pos], Str):
            raise WireError("ERR:BODY top-level atom outside expression")
        out.append(expr())
    return out


def atom(x) -> str:
    if isinstance(x, list):
        return "(" + " ".join(atom(a) for a in x) + ")"
    if isinstance(x, Str):
        return json.dumps(str(x), ensure_ascii=False).replace("|", "\\u007c")
    if isinstance(x, bool):
        raise WireError("ERR:TYPE bool is not an atom")
    return str(x)


def dump(exprs: list[list]) -> str:
    """Canonical body text: single spaces, no padding, strings in JSON form."""
    return "".join(atom(e) for e in exprs)


def _has_str(e) -> bool:
    return isinstance(e, Str) or (isinstance(e, list) and any(_has_str(a) for a in e))


def check_expr(e) -> str | None:
    if not isinstance(e, list) or not e or isinstance(e[0], Str) or not str(e[0]).isdigit():
        return "expression head is not an opcode"
    op = int(e[0])
    if op < 100:
        return "strings are only allowed in Exo opcodes" if _has_str(e) else up.check_expr(e)
    if op not in OPS:
        return f"unknown opcode {op}"
    name, shape = OPS[op]
    args = e[1:]
    star = shape.endswith("*")
    fixed = shape.rstrip("*")
    if (len(args) != len(fixed)) if not star else len(args) < len(fixed) - 1:
        return f"{name} wants {len(fixed)}{'+' if star else ''} arguments"
    for i, a in enumerate(args):
        kind = fixed[min(i, len(fixed) - 1)]
        ok = {"n": not isinstance(a, (list, Str)) and str(a).isdigit(),
              "s": isinstance(a, Str),
              "y": isinstance(a, str) and not isinstance(a, Str) and bool(SYM.match(a)),
              "t": isinstance(a, str) and not isinstance(a, Str) and bool(up.TOKEN.match(a))}[kind]
        if not ok:
            return f"{name} argument {i + 1} has the wrong type"
    return None


# ---------------------------------------------------------------- lines


def is_exo(body: str) -> bool:
    return body.startswith("(100 ")


def check_line(line: str) -> list[str]:
    """Typed diagnostics for one line; an empty list means valid and canonical."""
    if not isinstance(line, str):
        return ["ERR:TYPE a packet is one text line"]
    if line != line.strip() or "\n" in line:
        return ["ERR:CANON leading, trailing or embedded line breaks or spaces"]
    fields, ferr = up.split_fields(line)
    if ferr:
        return [f"ERR:SHAPE {ferr}"]
    if not is_exo(fields[9]):
        return up.check_line(line, 1)
    errs: list[str] = []
    _, pid, tk, f, t, a, st, cf, cv, body, rq, base, exp = fields[:13]
    for name, tok in (("ID", pid), ("TK", tk)):
        if not up.TOKEN.match(tok):
            errs.append(f"ERR:TOKEN {name} fails the token pattern")
    if len(tk) > 64:
        errs.append("ERR:RANGE TK longer than 64 characters")
    if base and not up.BASE.match(base):
        errs.append("ERR:TOKEN BASE is neither empty nor id:ver")
    for name, raw, reg in (("F", f, ROLES), ("T", t, ROLES), ("A", a, ACTS), ("ST", st, STATES),
                           ("CF", cf, None), ("CV", cv, None), ("RQ", rq, RQS), ("EXP", exp, None)):
        if not re.fullmatch(r"0|[1-9]\d*", raw):
            errs.append(f"ERR:TYPE {name} is not a canonical unsigned integer")
        elif reg is not None and int(raw) not in reg:
            errs.append(f"ERR:CODE {name}={raw} is not in the registry")
        elif reg is None and name in ("CF", "CV") and int(raw) > 100:
            errs.append(f"ERR:RANGE {name}={raw} outside 0-100")
    try:
        exprs = parse(body)
    except WireError as exc:
        return errs + exc.errors
    for e in exprs:
        why = check_expr(e)
        if why:
            errs.append(f"ERR:BODY {why}")
    if exprs and exprs[0][1:] != [str(PROFILE)]:
        errs.append(f"ERR:VER Exo vocabulary {exprs[0][1:]} is not {PROFILE}")
    if not errs and dump(exprs) != body:
        errs.append("ERR:CANON BODY is not in canonical form")
    if len(fields) == 14 and not errs:
        if not up.SUM.match(fields[13]):
            errs.append("ERR:RCP SUM is not 16 lowercase hex characters")
        elif fields[13] != up.rcp_sum("|".join(fields[:13])):
            errs.append("ERR:RCP SUM does not match the line")
    return errs


def repair(text: str) -> tuple[str | None, list[str]]:
    """Fix only safe, meaning-preserving damage, then re-check.

    Safe: surrounding whitespace and code fences, a leading label, leading zeros in
    integer fields, non-canonical body spacing. Never safe: a wrong checksum, an unknown
    code, a missing field. Returns (line, notes) or (None, errors).
    """
    notes: list[str] = []
    cand = next((ln.strip().strip("`").strip() for ln in str(text).splitlines()
                 if "2|" in ln), None)
    if cand is None:
        return None, ["ERR:SHAPE no SYM-2P line found"]
    if not cand.startswith("2|"):
        cand = cand[cand.index("2|"):]
        notes.append("dropped text before the packet")
    if cand != str(text).strip():
        notes.append("trimmed surrounding text")
    fields = cand.split("|")
    if len(fields) in (13, 14):
        for i in (3, 4, 5, 6, 7, 8, 10, 12):
            if re.fullmatch(r"0\d+", fields[i]):
                fields[i] = str(int(fields[i]))
                notes.append(f"field {i + 1}: dropped leading zeros")
        try:
            if is_exo(fields[9].strip()):
                body = dump(parse(fields[9]))
                if body != fields[9]:
                    fields[9] = body
                    notes.append("canonical body spacing")
        except WireError:
            pass
        cand = "|".join(fields)
    errs = check_line(cand)
    return (cand, notes) if not errs else (None, errs)


__all__ = ["ACTS", "CODE", "OPS", "PROFILE", "ROLES", "RQS", "STATES", "Str", "WireError", "atom",
           "check_expr", "check_line", "dump", "is_exo", "parse", "repair", "tokenize"]
