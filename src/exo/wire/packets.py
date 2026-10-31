"""Typed delegation packets: Brief (act 11 delegate) and Receipt (act 12 report).

One schema, three forms:

- **line**: the SYM-2P/2 packet with Exo vocabulary (``encode`` / ``decode``). This is what
  code stores, signs, sends over the bus and replays.
- **JSON**: the same fields as canonical JSON (``to_json`` / ``from_json``). The fallback
  wire for a peer that does not speak the line form, or a model that cannot emit it.
- **English**: ``render`` gives a deterministic plain-English text. People and models read
  this form; nobody is asked to read or write the line form by hand.

``decode_any`` accepts any of the machine forms (strict line, repaired line, JSON) and says
which one it got, so callers can count repairs and fallbacks.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import Any, Literal

from exo.wire.grammar import ACTS, CODE, PROFILE, ROLES, STATES, Str, WireError, check_line, dump, parse, repair
from exo.protocols.sym2p import v2 as up

TaskStatus = Literal["queued", "running", "done", "failed", "cancelled"]
TASK_STATUSES: tuple[str, ...] = ("queued", "running", "done", "failed", "cancelled")
STATUS_CODE = {"queued": 1, "running": 2, "done": 6, "failed": 100, "cancelled": 101}
CODE_STATUS = {v: k for k, v in STATUS_CODE.items()}
ROLE_CODE = {name: code for code, name in ROLES.items() if code}
RQ_CODE = {"none": 0, "ack": 1, "verify": 2}
CODE_NAME = {v: k for k, v in CODE.items()}
WIRE_FORMATS = ("sym2p/2+exo1", "json")  # preference order for negotiate()


@dataclass
class Brief:
    """A parent's instructions to one child. Limits of 0 mean "none"."""

    id: str
    thread: str
    goal: str
    context: str = ""
    role: str = "builder"
    sender: str = "coordinator"
    tools: list[str] = field(default_factory=list)
    max_turns: int = 0
    max_tokens: int = 0
    timeout_s: int = 0
    depth: int = 1
    max_depth: int = 1
    cwd: str = ""
    accept: list[str] = field(default_factory=list)
    base: str = ""
    request: str = "none"
    tier: str = ""  # fast | standard | deep; empty means the parent's own model
    route: str = ""  # a configured named model route (D02); empty means the tier or the parent's model


@dataclass
class Receipt:
    """What came back from one child. Filled by the harness, except ``summary``."""

    id: str
    thread: str
    brief: str
    status: str = "done"
    role: str = "builder"
    summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    rounds: int = 0
    tool_calls: int = 0
    files: list[str] = field(default_factory=list)
    error_code: str = ""
    error: str = ""
    child_session: str = ""
    confidence: int = 0
    tier: str = ""       # tier the child ran at
    model: str = ""      # resolved "provider:model" the child ran on
    escalated: int = 0  # 1 when this run was the retry one tier up after a failure
    route: str = ""      # the named route the parent asked for (D02); empty when none


Packet = Brief | Receipt


def _e(name: str, *args) -> list:
    return [str(CODE[name]), *args]


def _head(pid: str, tk: str, f: int, t: int, a: int, st: int, cf: int, exprs: list, rq: int, base: str,
          exp: int, rcp: bool) -> str:
    line = "|".join(["2", pid, tk, str(f), str(t), str(a), str(st), str(cf), "0", dump(exprs), str(rq),
                     base, str(exp)])
    return up.add_rcp(line) if rcp else line


def _code(table: dict, name: str, what: str) -> int:
    if name not in table:
        raise WireError(f"ERR:CODE unknown {what} {name!r}")
    return table[name]


def encode(p: Packet, rcp: bool = False) -> str:
    """The canonical line for a packet. Raises WireError if it would not check."""
    ex: list = [[str(CODE["profile"]), str(PROFILE)]]
    if isinstance(p, Brief):
        ex.append(_e("goal", Str(p.goal)))
        if p.context:
            ex.append(_e("context", Str(p.context)))
        if p.tools:
            ex.append(_e("tools", *p.tools))
        if p.max_turns or p.max_tokens or p.timeout_s:
            ex.append(_e("limits", str(p.max_turns), str(p.max_tokens), str(p.timeout_s)))
        ex.append(_e("depth", str(p.depth), str(p.max_depth)))
        if p.cwd:
            ex.append(_e("cwd", Str(p.cwd)))
        ex += [_e("accept", Str(a)) for a in p.accept]
        if p.tier:
            ex.append(_e("tier", p.tier))
        if p.route:
            ex.append(_e("asked_route", Str(p.route)))
        line = _head(p.id, p.thread, _code(ROLE_CODE, p.sender, "role"), _code(ROLE_CODE, p.role, "role"), 11, 1,
                     0, ex, _code(RQ_CODE, p.request, "request"), p.base, p.timeout_s, rcp)
    else:
        if p.summary:
            ex.append(_e("summary", Str(p.summary)))
        ex.append(_e("usage", str(p.input_tokens), str(p.output_tokens), str(round(p.cost_usd * 1e6))))
        ex.append(_e("work", str(p.duration_ms), str(p.rounds), str(p.tool_calls)))
        if p.files:
            ex.append(_e("files", *[Str(f) for f in p.files]))
        if p.error_code or p.error:
            ex.append(_e("error", p.error_code or "error", Str(p.error)))
        if p.child_session:
            ex.append(_e("child", p.child_session))
        if p.tier or p.model or p.escalated:
            ex.append(_e("route", p.tier or "none", Str(p.model), "1" if p.escalated else "0"))
        if p.route:
            ex.append(_e("asked_route", Str(p.route)))
        line = _head(p.id, p.thread, _code(ROLE_CODE, p.role, "role"), 1, 12,
                     _code(STATUS_CODE, p.status, "status"), p.confidence, ex, 0, f"{p.brief}:1", 0, rcp)
    errs = check_line(line)
    if errs:
        raise WireError(errs)
    return line


def decode(line: str) -> Packet:
    """Strict: a canonical, valid Exo line (bare or rcp) to its packet. Raises WireError."""
    errs = check_line(line)
    if errs:
        raise WireError(errs)
    fl = line.split("|")
    _, pid, tk, f, t, a, st, cf, _cv, body, rq, base, exp = fl[:13]
    ex = {CODE_NAME[int(e[0])]: e[1:] for e in parse(body) if int(e[0]) in CODE_NAME}
    accept = [str(e[1]) for e in parse(body) if e[0] == str(CODE["accept"])]
    ints = lambda k, n: [int(x) for x in ex.get(k, ["0"] * n)]  # noqa: E731
    if int(a) == 11:
        turns, tokens, secs = ints("limits", 3)
        depth, max_depth = ints("depth", 2) if "depth" in ex else (1, 1)
        return Brief(id=pid, thread=tk, goal=str(ex.get("goal", [""])[0]), context=str(ex.get("context", [""])[0]),
                     role=ROLES[int(t)], sender=ROLES[int(f)], tools=[str(x) for x in ex.get("tools", [])],
                     max_turns=turns, max_tokens=tokens, timeout_s=secs, depth=depth, max_depth=max_depth,
                     cwd=str(ex.get("cwd", [""])[0]), accept=accept, base=base, tier=str(ex.get("tier", [""])[0]),
                     route=str(ex.get("asked_route", [""])[0]),
                     request={v: k for k, v in RQ_CODE.items()}[int(rq)])
    if int(a) == 12:
        route = ex.get("route", ["none", "", "0"])
        asked = str(ex.get("asked_route", [""])[0])
        tin, tout, micro = ints("usage", 3)
        ms, rounds, calls = ints("work", 3)
        err = ex.get("error", ["", ""])
        return Receipt(id=pid, thread=tk, brief=base.rsplit(":", 1)[0], status=CODE_STATUS.get(int(st), STATES[int(st)]),
                       role=ROLES[int(f)], summary=str(ex.get("summary", [""])[0]), input_tokens=tin,
                       output_tokens=tout, cost_usd=micro / 1e6, duration_ms=ms, rounds=rounds, tool_calls=calls,
                       files=[str(x) for x in ex.get("files", [])], error_code=str(err[0]), error=str(err[1]),
                       child_session=str(ex.get("child", [""])[0]), confidence=int(cf),
                       tier="" if route[0] == "none" else str(route[0]), model=str(route[1]),
                       escalated=int(route[2]), route=asked)
    raise WireError(f"ERR:CODE act {ACTS.get(int(a), a)} is not a brief or a receipt")



# ---------------------------------------------------------------- JSON fallback


def to_json(p: Packet) -> str:
    """Canonical JSON form (sorted keys, no spaces) with a ``kind`` tag."""
    return json.dumps({"kind": "brief" if isinstance(p, Brief) else "receipt", **asdict(p)},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def from_json(text: str | dict) -> Packet:
    """Strict: unknown keys or wrong types are rejected, then the packet must encode."""
    try:
        data = json.loads(text) if isinstance(text, str) else dict(text)
    except ValueError as exc:
        raise WireError(f"ERR:SYN {exc}") from None
    if not isinstance(data, dict):
        raise WireError("ERR:SYN want a JSON object")
    cls = {"brief": Brief, "receipt": Receipt}.get(data.pop("kind", None))
    if cls is None:
        raise WireError("ERR:SYN kind must be brief or receipt")
    names = {f.name: f for f in fields(cls)}
    extra = set(data) - set(names)
    if extra:
        raise WireError(f"ERR:SYN unknown fields {sorted(extra)}")
    for k, v in data.items():
        f = names[k]
        want = list if f.default_factory is not MISSING else str if f.default is MISSING else type(f.default)
        ok = isinstance(v, want) and not isinstance(v, bool) or (want is float and type(v) is int)
        if not ok or (want is list and not all(isinstance(x, str) for x in v)):
            raise WireError(f"ERR:TYPE {k} must be {want.__name__}")
    try:
        p = cls(**data)
    except TypeError as exc:
        raise WireError(f"ERR:SYN {exc}") from None
    encode(p)  # same checks as the line form
    return p


def decode_any(text: str) -> tuple[Packet, str]:
    """Best machine reading of ``text``: ("line" | "repaired" | "json", packet). Raises WireError."""
    s = text.strip() if isinstance(text, str) else text
    if isinstance(s, str) and not s.startswith("{"):
        if not check_line(s):
            return decode(s), "line"
        fixed, why = repair(s)
        if fixed is not None:
            return decode(fixed), "repaired"
        if "{" not in s:
            raise WireError(why)
        s = s[s.index("{"):s.rindex("}") + 1]
    return from_json(s), "json"


def negotiate(mine: list[str] | tuple[str, ...], theirs: list[str] | tuple[str, ...]) -> str:
    """The first of my formats the peer also speaks; JSON when nothing matches."""
    return next((f for f in mine if f in theirs), "json")


# ---------------------------------------------------------------- signing


def sign(line: str, signer: Any) -> dict[str, str]:
    """Detached Ed25519 signature over the exact line bytes (``exo.crypto.Signer``)."""
    from exo import crypto

    return {"by": signer.author, "pub": signer.pub, "sig": crypto.sign(line.encode("utf-8"), signer.key)}


def verify(line: str, sig: dict[str, str], trusted_pub: str | None = None) -> bool:
    from exo import crypto

    # Never authenticate against the public key embedded in the (attacker-controlled)
    # signature envelope. The caller must supply the pinned trusted key; otherwise the
    # verification is meaningless and we refuse.
    if not trusted_pub:
        return False
    return crypto.verify(line.encode("utf-8"), sig.get("sig", ""), trusted_pub)


# ---------------------------------------------------------------- English


def _n(x: int) -> str:
    return f"{x:,}"


def _limits(b: Brief) -> str:
    parts = [f"{b.max_turns} tool rounds" if b.max_turns else "",
             f"{_n(b.max_tokens)} tokens" if b.max_tokens else "",
             f"{b.timeout_s} seconds" if b.timeout_s else ""]
    return ", ".join(p for p in parts if p)


def render(p: Packet | str) -> str:
    """Deterministic plain English. Accepts a packet or any machine form of one."""
    if isinstance(p, str):
        p = decode_any(p)[0]
    if isinstance(p, Brief):
        out = [f"Task {p.id} for a {p.role}: {p.goal}"]
        if p.context:
            out.append(f"Context: {p.context}")
        if p.accept:
            out.append("Done when: " + "; ".join(p.accept) + ".")
        out.append("Tools: " + (", ".join(p.tools) if p.tools else "the role's defaults") + ".")
        if p.tier:
            out.append(f"Model tier: {p.tier}.")
        if p.route:
            out.append(f"Model route: {p.route}.")
        if _limits(p):
            out.append(f"Limits: {_limits(p)}.")
        if p.depth < p.max_depth:
            out.append(f"It may delegate further ({p.max_depth - p.depth} more level).")
        return "\n".join(out)
    verb = {"done": "Done", "failed": "Failed", "cancelled": "Cancelled", "running": "Running",
            "queued": "Queued"}.get(p.status, p.status.capitalize())
    head = (f"{verb}: task {p.brief} by a {p.role} in {p.duration_ms / 1000:.1f} s, {p.tool_calls} tool calls "
            f"over {p.rounds} rounds, {_n(p.input_tokens)} tokens in and {_n(p.output_tokens)} out")
    head += f", ${p.cost_usd:.4f}." if p.cost_usd else "."
    out = [head]
    if p.tier or p.model or p.route:
        out.append(f"Model: {p.tier or 'default'} tier" + (f", {p.model}" if p.model else "")
                   + (f", requested route {p.route}" if p.route else "")
                   + (", retried one tier up after a failure" if p.escalated else "") + ".")
    if p.error_code or p.error:
        out.append(f"Reason: {p.error_code}" + (f": {p.error}" if p.error else "") + ".")
    if p.files:
        out.append("Files changed: " + ", ".join(p.files) + ".")
    out.append("Result: " + (p.summary or "(no text)"))
    return "\n".join(out)


__all__ = ["Brief", "Packet", "Receipt", "TASK_STATUSES", "TaskStatus", "WIRE_FORMATS", "decode", "decode_any",
           "encode", "from_json", "negotiate", "render", "sign", "to_json", "verify"]
