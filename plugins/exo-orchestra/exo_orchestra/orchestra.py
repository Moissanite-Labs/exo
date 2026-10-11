"""Role templates, skill pack lookup, and the /orchestra command.

The pack is generic. Seat names, owner rulings and model choices belong to the principal
store (`agents/`, `policies/`). `render_profile` fills the template placeholders so a new
profile directory can be created for any principal.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROLES = ("mission-control", "orchestrator", "researcher", "architect", "designer", "builder", "reviewer")
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def profiles_dir() -> Path:
    return PLUGIN_DIR / "profiles"


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


def render_profile(role: str, dest: Path, values: Mapping[str, str] | None = None,
                   overwrite: bool = False) -> list[Path]:
    """Copy one role template into `dest`, filling known placeholders.

    Known placeholders: seat_name, principal_store, exo_skills_dir. Unknown ones are left as is,
    so a principal-specific installer can fill them later. Refuses to overwrite by default.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    vals = {"seat_name": role, "principal_store": "", "exo_skills_dir": str(skills_dir())}
    vals.update({k: v for k, v in (values or {}).items() if v is not None})
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted((profiles_dir() / role).iterdir()):
        target = dest / src.name
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} exists; pass overwrite=True to replace it")
        text = PLACEHOLDER_RE.sub(lambda m: vals.get(m.group(1), m.group(0)), src.read_text(encoding="utf-8"))
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


# --- Hermes wiring ------------------------------------------------------------------------------

def _setting(get_config: Callable[[str, Any], Any] | None, key: str, default: Any = None) -> Any:
    if get_config is None:
        return default
    try:
        v = get_config(key, default)
    except Exception:
        return default
    return default if v in (None, "") else v


def command_handler(get_config: Callable[[str, Any], Any] | None = None) -> Callable[[str], str]:
    def handle(raw: str) -> str:
        args = shlex.split(raw or "")
        sub = args[0] if args else "roles"
        root = skills_dir(_setting(get_config, "skills_dir"))
        if sub == "roles":
            return "Roles: " + ", ".join(ROLES)
        if sub == "skills":
            try:
                entries = load_manifest(root)["skills"]
            except (OSError, ValueError, KeyError) as e:
                return f"Cannot read the skill manifest in {root}: {e}"
            return "\n".join(f"{s['name']}: {s['description']}" for s in entries)
        if sub == "profile" and len(args) >= 3:
            role, dest = args[1], args[2]
            seat = args[args.index("--seat") + 1] if "--seat" in args[:-1] else None
            store = _setting(get_config, "principal_store") or os.environ.get("EXO_PRINCIPAL_STORE", "")
            try:
                files = render_profile(role, Path(dest), {"seat_name": seat, "principal_store": store,
                                                           "exo_skills_dir": str(root)})
            except (ValueError, OSError) as e:
                return f"Not written: {e}"
            return "Wrote " + ", ".join(str(f) for f in files)
        return "Usage: /orchestra roles | skills | profile <role> <dir> [--seat NAME]"
    return handle


def register(ctx) -> None:
    get_config = getattr(ctx, "get_config", None)
    ctx.register_command("orchestra", command_handler(get_config),
                         description="Exo orchestra: list roles and skills, render a role profile",
                         args_hint="roles | skills | profile <role> <dir> [--seat NAME]")
    if not _setting(get_config, "register_skills", False):
        return
    root = skills_dir(_setting(get_config, "skills_dir"))
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is None:
        logger.info("exo-orchestra: this Hermes has no register_skill; use skills.external_dirs")
        return
    for entry in load_manifest(root)["skills"]:
        path = root / entry["path"] / "SKILL.md"
        try:
            register_skill(entry["name"], path, entry.get("description", ""), frontmatter(path))
        except (ValueError, FileNotFoundError) as e:
            logger.warning("exo-orchestra: skipped skill %s: %s", entry["name"], e)
