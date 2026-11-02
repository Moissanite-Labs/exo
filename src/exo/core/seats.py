"""Seats: a named configuration of one agent (docs/SEATS.md).

A seat file lives at ``$EXO_HOME/seats/<name>.toml`` (or ``.json``), or ``<name>/seat.toml`` with a
``SOUL.md`` beside it. A few short spellings are accepted for each part so the orchestra templates and
hand-written files both load::

    name = "scout"                     # or [seat] name = ...; defaults to the file name
    principal = "ada"                # or [principal] id = ..., store = ...
    [models]  tier = "fast"            # a router role (fast, standard, deep): becomes the seat's main model
              roles = {main = ["anthropic:x"]}    # plus any router config keys (providers, local, fake)
    [tools]   toolsets = ["fs", "search", "skills"]   # or top-level toolsets; omit for every toolset
              env_allow = ["GH_TOKEN"]   # vars the scrub would drop (secrets, shell startup hooks) that bash and jobs keep
              env_deny = ["COMPANY_*"]   # more names or patterns bash, jobs and code_find never see (D-039)
    [skills]  folders = ["~/notes/skills"]   # also `pack`; `load = [names]` limits the index to those skills
    [mcp.files]  command = "uvx"  args = ["server"]      # see core/mcp_client.py
    [channels.bus]  seat = "scout"     # channels to run with the server, each with factory(link, **config)
    [trace]   turn_wall_s = 90      # (harness overhead; model_call_s is the per-call limit) per-seat anomaly thresholds over config.json's [trace] (D-032)
    [session] cwd = "~/Work/exo-live"   # where this seat's threads start (D-032)
    [peers]   wake = true  policy = {other = "hold"}   # peer messages (docs/PEERS.md)
    [jobs]    max_concurrent = 4  max_wall_s = 7200  wake = false   # background shell jobs (docs/JOBS.md)
    [plugins] enabled = ["exo-memory"]  # or a top-level list; omit for the config.json default
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ROUTER_KEYS = ("roles", "providers", "fake", "local", "models")


class SeatError(ValueError):
    pass


def seats_dir(home: str | Path) -> Path:
    return Path(home) / "seats"


def key_path(home: str | Path, name: str) -> Path:
    """Where a seat's Ed25519 signing key lives. The bus client and the gate use the same file."""
    return Path(home) / "keys" / f"{name}.key"


def ensure_key(path: str | Path) -> Path:
    """Create the key file (mode 0600, plus a .pub beside it) if it is missing. Never prints or returns key
    material, and never overwrites an existing key."""
    from exo import crypto

    path = Path(path)
    if not path.exists():
        try:
            crypto.save_key(crypto.keygen(), path)
        except FileExistsError:  # another process won the race
            pass
    return path


def _table(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _strs(v: Any) -> list[str] | None:
    return [str(x) for x in v] if isinstance(v, list) else None


def _names(v: Any, key: str) -> list[str] | None:
    """A list of names, or None when the key is not set. For these keys no list means more (every toolset, every
    skill, nothing more hidden), so a value of another type is refused, not read as no list (exo-desktop #52)."""
    if v is not None and not isinstance(v, list):
        raise SeatError(f"{key} must be a list of names, not {v!r}")
    return _strs(v)


@dataclass
class Seat:
    name: str
    role: str = ""
    description: str = ""
    principal: str | None = None
    principal_store: str | None = None
    tier: str | None = None
    models: dict[str, Any] = field(default_factory=dict)  # router config overrides
    toolsets: list[str] | None = None                       # None = every toolset
    env_allow: list[str] = field(default_factory=list)      # [tools] env_allow: variables tool subprocesses may keep
    env_deny: list[str] = field(default_factory=list)       # [tools] env_deny: names or patterns they never see (D-039)
    skill_folders: list[str] = field(default_factory=list)
    skill_load: list[str] | None = None                     # None = every skill found
    mcp: dict[str, dict[str, Any]] = field(default_factory=dict)
    plugins: list[str] | None = None                        # None = the config.json default
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)  # enabled channels -> factory config
    peers: dict[str, Any] = field(default_factory=dict)     # [peers]: policy, rules, wake, hold_minutes (D-026)
    jobs: dict[str, Any] = field(default_factory=dict)      # [jobs]: max_concurrent, max_wall_s, wake (D-032)
    trace: dict[str, Any] = field(default_factory=dict)     # [trace]: threshold overrides (D-032)
    cwd: str | None = None                                  # [session] cwd: default working directory (D-032)
    soul: str = ""
    soul_path: str = ""                                     # the SOUL.md the text came from; re-read when it changes
    path: str = ""
    key: str | None = None                                  # signing key file; default $EXO_HOME/keys/<name>.key

    def key_path(self, home: str | Path) -> Path:
        return Path(self.key).expanduser() if self.key else key_path(home, self.name)

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("soul"), d.pop("models"), d.pop("soul_path")
        return {**d, "has_soul": bool(self.soul)}


def parse(data: dict[str, Any], name: str = "", path: str = "", soul: str = "") -> Seat:
    head = _table(data.get("seat"))
    nm = str(head.get("name") or data.get("name") or name)
    if "{{" in nm or not ID_RE.match(nm):
        raise SeatError(f"bad seat name {nm!r}: use lowercase letters, digits, - and _")
    pr = data.get("principal")
    pr_id = pr if isinstance(pr, str) else _table(pr).get("id")
    models = _table(data.get("models"))
    tools = _table(data.get("tools"))
    skills = _table(data.get("skills"))
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        has_plugins = "enabled" in plugins
        plugin_list = plugins.get("enabled")
    else:
        has_plugins = "plugins" in data
        plugin_list = plugins
    if has_plugins and not isinstance(plugin_list, list):
        raise SeatError("plugins must be a list (or [plugins] enabled must be a list)")
    mcp = _table(data.get("mcp"))
    mcp = _table(mcp.get("servers")) if isinstance(mcp.get("servers"), dict) else mcp
    ch = data.get("channels")
    channels = {str(n): {} for n in ch} if isinstance(ch, list) else \
        {k: (v if isinstance(v, dict) else {}) for k, v in _table(ch).items()}
    folders = [*(_strs(skills.get("folders")) or []), *([str(skills["pack"])] if skills.get("pack") else [])]
    return Seat(
        name=nm, role=str(head.get("role") or data.get("role") or ""),
        description=str(head.get("description") or data.get("description") or ""),
        principal=str(pr_id) if pr_id else None, principal_store=_table(pr).get("store") or None,
        tier=models.get("tier"), models={k: models[k] for k in ROUTER_KEYS if k in models},
        toolsets=_names(tools["toolsets"] if "toolsets" in tools else data.get("toolsets"), "toolsets"),
        env_allow=_strs(tools.get("env_allow")) or [], env_deny=_names(tools.get("env_deny"), "env_deny") or [],
        skill_folders=folders, skill_load=_names(skills.get("load"), "skills.load"),
        mcp={k: v for k, v in mcp.items() if isinstance(v, dict)},
        plugins=_strs(plugin_list), channels=channels,
        peers=_table(data.get("peers")), jobs=_table(data.get("jobs")), trace=_table(data.get("trace")),
        cwd=str(_table(data.get("session")).get("cwd") or data.get("cwd") or "") or None, soul=soul, path=path, key=str(head.get("key") or data.get("key") or "") or None)


def _candidates(home: str | Path, name: str) -> list[Path]:
    d = seats_dir(home)
    return [d / f"{name}.toml", d / f"{name}.json", d / name / "seat.toml", d / name / "seat.json"]


def _read(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text("utf-8")) if path.suffix == ".toml" else json.loads(path.read_text("utf-8"))
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
        raise SeatError(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise SeatError(f"{path}: top level must be a table")
    return data


def load(home: str | Path, name: str) -> Seat:
    if not ID_RE.match(name or ""):
        raise SeatError(f"bad seat name {name!r}")
    for p in _candidates(home, name):
        if p.is_file():
            data = _read(p)
            soul_file = p.parent / str(_table(data.get("seat")).get("soul") or "SOUL.md")
            soul = soul_file.read_text("utf-8").strip() if p.name.startswith("seat.") and soul_file.is_file() else ""
            seat = parse(data, name, str(p), soul)
            if p.name.startswith("seat."):
                seat.soul_path = str(soul_file)
            return seat
    raise SeatError(f"no seat {name!r} in {seats_dir(home)}")


def list_seats(home: str | Path) -> list[dict[str, Any]]:
    """Every seat file under ``$EXO_HOME/seats``, with a per-file ``error`` instead of a raise."""
    d = seats_dir(home)
    names = sorted({p.stem if p.is_file() else p.name for p in d.iterdir() if p.suffix in ("", ".toml", ".json")}
                   if d.is_dir() else set())
    out = []
    for n in names:
        try:
            out.append(load(home, n).summary())
        except SeatError as e:
            out.append({"name": n, "error": str(e)})
    return out
