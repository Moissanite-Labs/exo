"""Export the protocol schema to JSON Schema and TypeScript types.

    python -m exo.protocol.export            # rewrite src/exo/protocol/generated/
    python -m exo.protocol.export --check    # exit 1 if the files are stale

Output is deterministic (sorted definitions), so the generated files diff
cleanly and a test catches schema changes that were not re-exported.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal

from exo.protocol import schema as s

OUT_DIR = Path(__file__).parent / "generated"
JSON_NAME = "protocol.schema.json"
TS_NAME = "protocol.d.ts"
ENVELOPES = (s.Command, s.Response, s.Event)


def _defaults(f: dataclasses.Field) -> tuple[bool, Any]:
    if f.default is not dataclasses.MISSING:
        return True, f.default
    if f.default_factory is not dataclasses.MISSING:
        value = f.default_factory()
        # id and ts factories are random or time-based; they have no stable default.
        return True, value if isinstance(value, (list, dict)) else None
    return False, None


def _union_args(tp: Any) -> tuple[list[Any], bool] | None:
    if typing.get_origin(tp) in (typing.Union, types.UnionType):
        args = typing.get_args(tp)
        return [a for a in args if a is not type(None)], type(None) in args
    return None


def _walk(cls: type, seen: dict[str, type]) -> None:
    if cls.__name__ in seen:
        return
    seen[cls.__name__] = cls
    for tp in typing.get_type_hints(cls).values():
        stack = [tp]
        while stack:
            t = stack.pop()
            if isinstance(t, type) and dataclasses.is_dataclass(t):
                _walk(t, seen)
            stack.extend(typing.get_args(t))


def _classes() -> dict[str, type]:
    seen: dict[str, type] = {}
    for cls in [*s.COMMANDS.values(), *s.EVENTS.values(), *ENVELOPES]:
        _walk(cls, seen)
    return dict(sorted(seen.items()))


# ---------------------------------------------------------------- JSON Schema


def _js(tp: Any) -> dict[str, Any]:
    if tp is Any:
        return {}
    u = _union_args(tp)
    if u is not None:
        args, nullable = u
        opts = [_js(a) for a in args] + ([{"type": "null"}] if nullable else [])
        return opts[0] if len(opts) == 1 else {"anyOf": opts}
    origin = typing.get_origin(tp)
    if origin is Literal:
        return {"enum": list(typing.get_args(tp))}
    if origin is list:
        (arg,) = typing.get_args(tp) or (Any,)
        return {"type": "array", "items": _js(arg)}
    if origin is dict or tp is dict:
        return {"type": "object"}
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return {"$ref": f"#/$defs/{tp.__name__}"}
    prim = {str: "string", int: "integer", float: "number", bool: "boolean"}
    if tp in prim:
        return {"type": prim[tp]}
    raise TypeError(f"no JSON Schema mapping for {tp!r}")


def _js_class(cls: type) -> dict[str, Any]:
    hints = typing.get_type_hints(cls)
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        prop = _js(hints[f.name])
        has, default = _defaults(f)
        if not has:
            required.append(f.name)
        elif default is not None or f.default is None:
            prop = {**prop, "default": default}
        props[f.name] = prop
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    if cls.__doc__ and not cls.__doc__.startswith(cls.__name__ + "("):
        out["description"] = " ".join(cls.__doc__.split())
    return out


def json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"Exo protocol v{s.PROTOCOL_VERSION}",
        "protocol": s.PROTOCOL_VERSION,
        "commands": {name: {"$ref": f"#/$defs/{cls.__name__}"} for name, cls in s.COMMANDS.items()},
        "events": {name: {"$ref": f"#/$defs/{cls.__name__}"} for name, cls in s.EVENTS.items()},
        "$defs": {name: _js_class(cls) for name, cls in _classes().items()},
    }


# ---------------------------------------------------------------- TypeScript


def _ts(tp: Any) -> str:
    if tp is Any:
        return "unknown"
    u = _union_args(tp)
    if u is not None:
        args, nullable = u
        return " | ".join([_ts(a) for a in args] + (["null"] if nullable else []))
    origin = typing.get_origin(tp)
    if origin is Literal:
        return " | ".join(json.dumps(a) for a in typing.get_args(tp))
    if origin is list:
        (arg,) = typing.get_args(tp) or (Any,)
        inner = _ts(arg)
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if origin is dict or tp is dict:
        return "Record<string, unknown>"
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return tp.__name__
    prim = {str: "string", int: "number", float: "number", bool: "boolean"}
    if tp in prim:
        return prim[tp]
    raise TypeError(f"no TypeScript mapping for {tp!r}")


def typescript() -> str:
    out = ["// Generated by `python -m exo.protocol.export` from exo/protocol/schema.py. Do not edit.",
           f"export const PROTOCOL_VERSION = {s.PROTOCOL_VERSION};", ""]
    for name, cls in _classes().items():
        hints = typing.get_type_hints(cls)
        out.append(f"export interface {name} {{")
        for f in dataclasses.fields(cls):
            opt = "?" if _defaults(f)[0] else ""
            out.append(f"  {f.name}{opt}: {_ts(hints[f.name])};")
        out += ["}", ""]
    for kind, table in (("Command", s.COMMANDS), ("Event", s.EVENTS)):
        out.append(f"export interface {kind}Bodies {{")
        out += [f"  {json.dumps(n)}: {c.__name__};" for n, c in table.items()]
        out += ["}", f"export type {kind}Type = keyof {kind}Bodies;", ""]
    out += [
        "export type AnyCommand = { [K in CommandType]: { id: string; type: K; body: CommandBodies[K] } }[CommandType];",
        "export type AnyEvent = {",
        "  [K in EventType]: { id: string; seq: number; session: string | null; type: K; ts: string; body: EventBodies[K] };",
        "}[EventType];",
        "",
    ]
    return "\n".join(out)


def render() -> dict[str, str]:
    return {JSON_NAME: json.dumps(json_schema(), indent=2) + "\n", TS_NAME: typescript()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m exo.protocol.export")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--check", action="store_true", help="fail if generated files are stale")
    a = ap.parse_args(argv)
    stale = []
    for name, text in render().items():
        path = a.out / name
        if a.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("stale: " + ", ".join(stale) + " (run python -m exo.protocol.export)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
