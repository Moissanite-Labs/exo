"""Skills: SKILL.md folders found on disk, shown to the model as a compact index.

Progressive disclosure: the system prompt carries one line per skill (name and
description). The model calls the ``skill`` tool to load one body when it needs it.

Sources, later ones lose to earlier ones on a name clash (a seat can shadow the pack):
seat folders, ``$EXO_HOME/skills``, the Exo pack (``skills/`` in the checkout or
``$EXO_SKILLS_PACK``), then skills plugins registered with ``register_skill``.
Each folder is scanned one level deep: ``<dir>/<name>/SKILL.md`` or ``<dir>/SKILL.md``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from exo.core.tools.registry import Registry, ToolError
from exo.core.types import ToolSpec

MAX_DESC = 300
_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)


@dataclass
class Skill:
    name: str
    description: str
    path: Path

    def body(self) -> str:
        m = _FM.match(text := self.path.read_text(encoding="utf-8", errors="replace"))
        return (text[m.end():] if m else text).strip()


def _scalar(v: str) -> Any:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    return v


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Top-level ``key: value`` pairs of the YAML header (enough for name and description).

    Handles quoted values, inline lists and ``>``/``|`` block scalars; nested maps are skipped."""
    m = _FM.match(text)
    if not m:
        return {}
    out: dict[str, Any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        km = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", lines[i])
        i += 1
        if not km:
            continue
        key, val = km.group(1), km.group(2).strip()
        if val in (">", "|", ">-", "|-"):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
                block.append(lines[i].strip())
                i += 1
            out[key] = (" " if val[0] == ">" else "\n").join(b for b in block if b)
        elif val:
            out[key] = _scalar(val)
    return out


def read_skill(path: Path, name: str | None = None) -> Skill | None:
    try:
        fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    nm = str(name or fm.get("name") or path.parent.name).strip()
    if not nm:
        return None
    return Skill(nm, str(fm.get("description") or "").strip(), path)


def pack_dir(env: dict[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    if env.get("EXO_SKILLS_PACK"):
        return Path(env["EXO_SKILLS_PACK"]).expanduser()
    p = Path(__file__).resolve().parents[3] / "skills"  # a source checkout; absent in a wheel
    return p if p.is_dir() else None


def scan(folder: Path) -> list[Skill]:
    folder = Path(folder).expanduser()
    found = []
    if (folder / "SKILL.md").is_file():
        found.append(folder / "SKILL.md")
    if folder.is_dir():
        found += sorted(p for p in folder.glob("*/SKILL.md") if p.is_file())
    return [sk for p in found if (sk := read_skill(p))]


class SkillIndex:
    """All known skills by name. ``add_folders`` is first-wins; plugins come last."""

    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}

    def add(self, sk: Skill) -> None:
        self.skills.setdefault(sk.name, sk)

    def add_folders(self, folders: Iterable[str | os.PathLike]) -> None:
        for f in folders:
            for sk in scan(Path(f)):
                self.add(sk)

    def add_plugin_skills(self, entries: Iterable[Any]) -> None:
        """``entries``: PluginManager.skills values (name, path, description, frontmatter, plugin). A plugin
        entry for a file a folder already gave (an installed pack under $EXO_HOME/skills) is skipped."""
        seen = {s.path.resolve() for s in self.skills.values()}
        for e in entries:
            if Path(e.path).resolve() in seen:
                continue
            sk = read_skill(Path(e.path), name=e.name)
            if sk:
                sk.description = e.description or sk.description
                self.add(sk)

    @classmethod
    def discover(cls, folders: Iterable[str | os.PathLike], plugin_skills: Iterable[Any] = (),
                 only: Iterable[str] | None = None) -> "SkillIndex":
        """Folders in priority order, then plugin skills. ``only`` keeps just those names."""
        idx = cls()
        idx.add_folders(folders)
        idx.add_plugin_skills(plugin_skills)
        if only is not None:
            keep = set(only)
            idx.skills = {k: v for k, v in idx.skills.items() if k in keep or k.split(":")[-1] in keep}
        return idx

    def listing(self) -> list[dict[str, str]]:
        return [{"name": s.name, "description": s.description, "path": str(s.path)}
                for _, s in sorted(self.skills.items())]

    def prompt(self) -> str:
        """The compact index for the system prompt (empty when there are no skills)."""
        if not self.skills:
            return ""
        rows = []
        for name, s in sorted(self.skills.items()):
            d = " ".join(s.description.split())
            rows.append(f"- {name}: {d[:MAX_DESC] + '...' if len(d) > MAX_DESC else d}")
        return ("# Skills\nCall the `skill` tool with a name to load its full instructions "
                "before doing work it covers.\n" + "\n".join(rows))

    def load(self, name: str) -> str:
        sk = self.skills.get(name)
        if sk is None:
            low = {k.lower(): k for k in self.skills}
            sk = self.skills.get(low.get(name.lower(), ""))
        if sk is None:  # a bare name for one plugin skill, e.g. exo-intro for exo-orchestra:exo-intro
            tail = [s for k, s in self.skills.items() if k.split(":")[-1].lower() == name.lower()]
            sk = tail[0] if len(tail) == 1 else None
        if sk is None:
            raise ToolError(f"no skill {name!r}. Known: {', '.join(sorted(self.skills)) or 'none'}")
        return f"# Skill: {sk.name}\n(folder: {sk.path.parent})\n\n{sk.body()}"


def register_skill_tool(reg: Registry, index: SkillIndex) -> None:
    """Add the ``skill`` tool (toolset ``skills``, low risk, read only)."""
    spec = ToolSpec(
        name="skill", toolset="skills", risk="low",
        description="Load the full instructions of one skill from the skills index in the system prompt.",
        parameters={"type": "object", "properties": {"name": {"type": "string", "description": "skill name"}},
                    "required": ["name"], "additionalProperties": False})
    reg.register(spec, lambda args, ctx: index.load(str(args["name"])), replace=True)
