"""The house taste pack on disk: format, loader and release check (D-044 wave 1).

A pack is a folder:

    MANIFEST.json     version, script version, item counts, sha256 of each file, source hashes
    items.jsonl       one item per line, sorted by id: {id, class, type, domain, claim, sig, evidence, contested}
    soul/house.md     the house soul: voice only, no names, no values, no safety rules
    RELEASE.json      written by `exo taste review --final` once every value and contested item is ticked
    review/           REVIEW.md and ticks.json for the principal; never loaded, never shipped

A pack is **final** only when RELEASE.json names the manifest's sha256 and ticks every value and contested
item, and every file matches its sha256. The loader uses a pack only when it is final, so an item nobody
read can never reach a user. The shipped pack lives in `src/exo/baseline/house-taste/`; `$EXO_HOUSE_TASTE`
points at another (tests, a release candidate).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = 1
PACK_NAME = "exo-house-taste"
SHIPPED = Path(__file__).with_name("house-taste")
PACK_FILES = ("items.jsonl", "soul/house.md")
CLASSES = ("method", "value")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(obj) -> str:
    """Canonical JSON for pack files: sorted keys, two-space indent, trailing newline."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def item_line(item: dict) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class HouseItem:
    id: str
    cls: str                      # method | value
    type: str
    domain: str
    claim: str
    sig: frozenset[str]
    evidence: int = 0
    contested: bool = False

    @classmethod
    def from_json(cls, d: dict) -> "HouseItem":
        return cls(str(d["id"]), str(d["class"]), str(d.get("type") or ""), str(d.get("domain") or ""),
                   str(d["claim"]), frozenset(d.get("sig") or ()), int(d.get("evidence") or 0),
                   bool(d.get("contested")))

    def to_json(self) -> dict:
        return {"id": self.id, "class": self.cls, "type": self.type, "domain": self.domain, "claim": self.claim,
                "sig": sorted(self.sig), "evidence": self.evidence, "contested": self.contested}

    @property
    def needs_tick(self) -> bool:
        return self.cls == "value" or self.contested


@dataclass
class HousePack:
    root: Path | None
    version: str = ""
    manifest: dict = field(default_factory=dict)
    items: tuple[HouseItem, ...] = ()
    soul: str = ""
    final: bool = False
    problem: str = ""             # why the pack is not usable, when it is not

    @property
    def short_version(self) -> str:
        """`1.3` for version `1.3.0`: the tag shown beside each house item."""
        parts = self.version.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else self.version

    @property
    def source_id(self) -> str:
        return str(self.manifest.get("source_id") or "")


def pack_path(path: str | os.PathLike | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    env = os.environ.get("EXO_HOUSE_TASTE")
    return Path(env).expanduser() if env else SHIPPED


def read_items(root: Path) -> list[HouseItem]:
    p = root / "items.jsonl"
    if not p.is_file():
        return []
    return [HouseItem.from_json(json.loads(ln)) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def release_problem(root: Path, manifest_bytes: bytes, manifest: dict, items: list[HouseItem]) -> str:
    """Empty when the pack is final; else the reason it is not."""
    for rel, want in (manifest.get("files") or {}).items():
        p = root / rel
        if not p.is_file() or sha256(p.read_bytes()) != want:
            return f"{rel} does not match the manifest"
    for rel in PACK_FILES:
        if rel not in (manifest.get("files") or {}):
            return f"the manifest does not list {rel}"
    if not items:
        return "the pack has no items (no house taste has been released yet)"
    rel_path = root / "RELEASE.json"
    if not rel_path.is_file():
        return "not released: value and contested items need the principal's tick (exo taste review --final)"
    try:
        release = json.loads(rel_path.read_text(encoding="utf-8"))
    except ValueError:
        return "RELEASE.json is not valid JSON"
    if release.get("manifest_sha256") != sha256(manifest_bytes):
        return "RELEASE.json is for another export"
    ticked = set(release.get("ticked") or [])
    missing = [i.id for i in items if i.needs_tick and i.id not in ticked]
    if missing:
        return f"{len(missing)} value or contested item(s) not ticked"
    return ""


def load_pack(path: str | os.PathLike | None = None) -> HousePack:
    """The house pack at `path` (default `$EXO_HOUSE_TASTE`, else the shipped one). Never raises: a missing,
    broken or unreleased pack comes back with `final=False` and a `problem`."""
    root = pack_path(path)
    mpath = root / "MANIFEST.json"
    if not mpath.is_file():
        return HousePack(root, problem=f"no house pack at {root}")
    try:
        mbytes = mpath.read_bytes()
        manifest = json.loads(mbytes)
        items = read_items(root)
        soul_p = root / "soul" / "house.md"
        soul = soul_p.read_text(encoding="utf-8") if soul_p.is_file() else ""
    except (OSError, ValueError, KeyError, TypeError) as e:
        return HousePack(root, problem=f"house pack unreadable: {e}")
    pack = HousePack(root, str(manifest.get("version") or ""), manifest, tuple(items), soul)
    pack.problem = release_problem(root, mbytes, manifest, items)
    pack.final = not pack.problem
    return pack
