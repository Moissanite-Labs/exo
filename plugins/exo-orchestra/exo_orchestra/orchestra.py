"""Seat templates, skill pack lookup, and the /orchestra command.

A seat is a named config: model roles and tiers, a toolset, skills and a principal. The format is
documented in ``SEATS.md``. The pack is generic. Seat names, owner rulings and model choices belong
to the principal store (``agents/``, ``policies/``). ``render_seat`` fills the template placeholders so
a seat file can be created for any principal. Skills load natively: the plugin registers every skill
in the Exo pack through ``ctx.register_skill`` (no ``skills.external_dirs`` workaround).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROLES = ("mission-control", "orchestrator", "researcher", "architect", "designer", "builder", "reviewer")
TIERS = ("fast", "standard", "deep")
MODEL_ROLES = ("main", "aux", "decision", "eval")
SCHEMA_VERSION = 1
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SEAT_FILE = "seat.toml"
SOUL_FILE = "SOUL.md"

_TOP = {"schema", "seat", "principal", "models", "tools", "skills", "delegation"}
_KEYS = {
    "seat": {"name", "role", "description", "stage", "soul"},
    "principal": {"store"},
    "models": {"tier", *MODEL_ROLES, "tiers"},
    "tools": {"toolsets"},
    "skills": {"pack", "load"},
    "delegation": {"allow_upgrade", "escalate_on_fail"},
}


def seats_dir() -> Path:
    return PLUGIN_DIR / "seats"


def skills_dir(setting: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """The skill pack: explicit setting, then $EXO_SKILLS_DIR, then the repo's skills/ folder."""
    env = os.environ if env is None else env
    for cand in (setting, env.get("EXO_SKILLS_DIR")):
        if cand:
            return Path(cand).expanduser()
    return PLUGIN_DIR.parents[1] / "skills"


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    root = root or skills_dir()
    return json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))


def frontmatter(skill_md: Path) -> dict[str, str]:
    """Top-level scalar keys of a SKILL.md YAML frontmatter block (stdlib only)."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


# --- the seat format ------------------------------------------------------------------------------

def parse_seat(text: str) -> dict[str, Any]:
    """Parse seat TOML. Raises ValueError with the TOML message on bad syntax."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"seat is not valid TOML: {e}") from None


def load_seat(path: str | os.PathLike) -> dict[str, Any]:
    """Read and parse a seat file, or a seat folder holding ``seat.toml``."""
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / SEAT_FILE
    return parse_seat(p.read_text(encoding="utf-8"))


def _strs(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


def validate_seat(seat: Mapping[str, Any], skills_root: Path | None = None) -> list[str]:
    """Return a list of problems, empty when the seat is valid. Unknown keys are problems.

    With ``skills_root`` set, every skill in ``skills.load`` must exist in that pack.
    """
    errs: list[str] = []
    if seat.get("schema") != SCHEMA_VERSION:
        errs.append(f"schema must be {SCHEMA_VERSION}")
    for k in seat:
        if k not in _TOP:
            errs.append(f"unknown table or key: {k}")
    for table, allowed in _KEYS.items():
        t = seat.get(table, {})
        if not isinstance(t, dict):
            errs.append(f"{table} must be a table")
            continue
        errs += [f"unknown key: {table}.{k}" for k in t if k not in allowed]
    s = seat.get("seat") if isinstance(seat.get("seat"), dict) else {}
    name = s.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errs.append("seat.name is required: letters, digits, dot, dash, underscore")
    for k in ("role", "description", "stage", "soul"):
        if k in s and not isinstance(s[k], str):
            errs.append(f"seat.{k} must be a string")
    m = seat.get("models") if isinstance(seat.get("models"), dict) else {}
    if "tier" in m and m["tier"] not in TIERS:
        errs.append(f"models.tier must be one of {', '.join(TIERS)}")
    for k in MODEL_ROLES:
        if k in m and not (isinstance(m[k], str) and ":" in m[k]):
            errs.append(f"models.{k} must be a provider:model string")
    tiers = m.get("tiers", {})
    if not isinstance(tiers, dict):
        errs.append("models.tiers must be a table")
    else:
        for k, v in tiers.items():
            if k not in TIERS:
                errs.append(f"models.tiers.{k}: tier must be one of {', '.join(TIERS)}")
            elif not (isinstance(v, str) and ":" in v):
                errs.append(f"models.tiers.{k} must be a provider:model string")
    tools = seat.get("tools") if isinstance(seat.get("tools"), dict) else {}
    if "toolsets" in tools and not _strs(tools["toolsets"]):
        errs.append("tools.toolsets must be a list of names")
    sk = seat.get("skills") if isinstance(seat.get("skills"), dict) else {}
    if "load" in sk and not _strs(sk["load"]):
        errs.append("skills.load must be a list of skill names")
    elif skills_root is not None:
        errs += [f"skills.load: no skill {n!r} in {skills_root}" for n in sk.get("load", [])
                 if not (skills_root / n / "SKILL.md").is_file()]
    d = seat.get("delegation") if isinstance(seat.get("delegation"), dict) else {}
    errs += [f"delegation.{k} must be true or false" for k in d if k in _KEYS["delegation"]
             and not isinstance(d[k], bool)]
    return errs


def session_body(seat: Mapping[str, Any]) -> dict[str, Any]:
    """Fields for the protocol's ``session.create`` command: the seat name, and its main model if set."""
    name = seat["seat"]["name"]
    body: dict[str, Any] = {"title": name, "seat": name}
    main = (seat.get("models") or {}).get("main")
    if main:
        body["model"] = main
    return body


def models_overlay(seat: Mapping[str, Any]) -> dict[str, list[str]]:
    """Router roles this seat sets: ``main``, ``aux``, ``decision``, ``eval`` and any tier routes."""
    m = seat.get("models") or {}
    out = {k: [m[k]] for k in MODEL_ROLES if m.get(k)}
    out.update({k: [v] for k, v in (m.get("tiers") or {}).items()})
    return out


def render_seat_text(role: str, values: Mapping[str, str] | None = None) -> str:
    """The template for one role with placeholders filled. Known: seat_name, principal_store, exo_skills_dir."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    vals = {"seat_name": role, "principal_store": "", "exo_skills_dir": str(skills_dir())}
    vals.update({k: v for k, v in (values or {}).items() if v is not None})
    if not NAME_RE.match(str(vals["seat_name"])):
        raise ValueError(f"bad seat name {vals['seat_name']!r}: letters, digits, dot, dash, underscore")
    for k in ("principal_store", "exo_skills_dir"):
        vals[k] = str(vals[k]).replace("\\", "\\\\").replace('"', '\\"')  # safe inside TOML basic strings
    text = (seats_dir() / role / SEAT_FILE).read_text(encoding="utf-8")
    return PLACEHOLDER_RE.sub(lambda m: str(vals.get(m.group(1), m.group(0))), text)


def render_seat(role: str, dest: Path, values: Mapping[str, str] | None = None,
                overwrite: bool = False) -> list[Path]:
    """Write one role's ``seat.toml`` and ``SOUL.md`` into ``dest``. Refuses to overwrite by default."""
    seat_text = render_seat_text(role, values)
    vals = {"seat_name": role, "principal_store": "", "exo_skills_dir": str(skills_dir())}
    vals.update({k: v for k, v in (values or {}).items() if v is not None})
    soul = PLACEHOLDER_RE.sub(lambda m: str(vals.get(m.group(1), m.group(0))),
                              (seats_dir() / role / SOUL_FILE).read_text(encoding="utf-8"))
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    targets = [(dest / SEAT_FILE, seat_text), (dest / SOUL_FILE, soul)]
    for t, _ in targets:
        if t.exists() and not overwrite:
            raise FileExistsError(f"{t} exists; pass overwrite=True to replace it")
    for t, text in targets:
        t.write_text(text, encoding="utf-8")
    return [t for t, _ in targets]


def list_seats() -> list[dict[str, Any]]:
    """One summary row per shipped template: role, tier, toolsets, skills."""
    rows = []
    for role in ROLES:
        seat = parse_seat(render_seat_text(role))
        rows.append({"role": role, "tier": seat["models"]["tier"], "toolsets": seat["tools"]["toolsets"],
                     "skills": seat["skills"]["load"], "stage": seat["seat"].get("stage", "")})
    return rows


# --- Exo wiring -----------------------------------------------------------------------------------

def _setting(get_config: Callable[[str, Any], Any] | None, key: str, default: Any = None) -> Any:
    if get_config is None:
        return default
    try:
        v = get_config(key, default)
    except Exception:
        return default
    return default if v in (None, "") else v


USAGE = "Usage: /orchestra seats | skills | seat <role> [--seat NAME] [--out DIR] | check <file>"


def command_handler(get_config: Callable[[str, Any], Any] | None = None) -> Callable[[str], str]:
    def handle(raw: str) -> str:
        try:
            args = shlex.split(raw or "")
        except ValueError as e:
            return f"Cannot parse arguments: {e}"
        sub = args[0] if args else "seats"
        root = skills_dir(_setting(get_config, "skills_dir"))
        if sub in ("seats", "roles"):
            return "\n".join(f"{r['role']}: tier {r['tier']}, tools {', '.join(r['toolsets'])}, "
                             f"{len(r['skills'])} skills" for r in list_seats())
        if sub == "skills":
            try:
                entries = load_manifest(root)["skills"]
            except (OSError, ValueError, KeyError) as e:
                return f"Cannot read the skill manifest in {root}: {e}"
            return "\n".join(f"{s['name']}: {s['description']}" for s in entries)
        if sub == "seat" and len(args) >= 2:
            role = args[1]
            opts = {a: args[i + 1] for i, a in enumerate(args[:-1]) if a in ("--seat", "--out")}
            store = _setting(get_config, "principal_store") or os.environ.get("EXO_PRINCIPAL_STORE", "")
            vals = {"seat_name": opts.get("--seat"), "principal_store": store, "exo_skills_dir": str(root)}
            try:
                if "--out" in opts:
                    return "Wrote " + ", ".join(str(f) for f in render_seat(role, Path(opts["--out"]), vals))
                return render_seat_text(role, vals)
            except (ValueError, OSError) as e:
                return f"Not written: {e}"
        if sub == "check" and len(args) >= 2:
            try:
                errs = validate_seat(load_seat(args[1]), root)
            except (ValueError, OSError) as e:
                return f"Cannot read seat: {e}"
            return "Seat is valid." if not errs else "Seat has problems:\n" + "\n".join(f"- {e}" for e in errs)
        return USAGE
    return handle


def register(ctx) -> None:
    get_config = getattr(ctx, "get_config", None)
    ctx.register_command("orchestra", command_handler(get_config),
                         description="Exo orchestra: list seat templates and skills, render or check a seat",
                         args_hint="seats | skills | seat <role> [--seat NAME] [--out DIR] | check <file>")
    if not _setting(get_config, "register_skills", True):
        return
    root = skills_dir(_setting(get_config, "skills_dir"))
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is None:
        logger.info("exo-orchestra: this host has no register_skill; skills not registered")
        return
    try:
        entries = load_manifest(root)["skills"]
    except (OSError, ValueError, KeyError) as e:
        logger.warning("exo-orchestra: no skill manifest in %s: %s", root, e)
        return
    for entry in entries:
        path = root / entry["path"] / "SKILL.md"
        try:
            register_skill(entry["name"], path, entry.get("description", ""), frontmatter(path))
        except (ValueError, FileNotFoundError) as e:
            logger.warning("exo-orchestra: skipped skill %s: %s", entry["name"], e)
