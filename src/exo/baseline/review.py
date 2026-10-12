"""The principal's per-item tick on a house taste export (D-044 wave 1, ruling 4).

    exo taste review <out>                    what is ticked and what is left
    exo taste review <out> --tick ID [ID ...] tick value or contested items after reading them
    exo taste review <out> --final            write RELEASE.json; refuses while any tick is missing

Ticks live in `<out>/review/ticks.json`, keyed by item id; an id is a hash of the claim, so a changed claim
needs a new tick. RELEASE.json binds the ticks to the manifest's sha256, and the loader uses only a pack
whose RELEASE.json matches (exo.baseline.pack). Publishing the pack is still a public act and goes through
the gate with the principal's approval each release.
"""
from __future__ import annotations

import json
from pathlib import Path

from .pack import HouseItem, dumps, read_items, release_problem, sha256


class ReviewError(ValueError):
    pass


def _ticks_path(out: Path) -> Path:
    return out / "review" / "ticks.json"


def read_ticks(out: str | Path) -> dict[str, str]:
    p = _ticks_path(Path(out))
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")).get("ticks") or {})
    except (OSError, ValueError):
        return {}


def _items(out: Path) -> list[HouseItem]:
    if not (out / "MANIFEST.json").is_file():
        raise ReviewError(f"no export at {out}")
    return read_items(out)


def tick(out: str | Path, ids: list[str]) -> list[str]:
    """Tick items by id. Only value and contested items take a tick; an unknown id is an error."""
    out = Path(out)
    items = {i.id: i for i in _items(out)}
    ticks = read_ticks(out)
    done = []
    for i in ids:
        it = items.get(i)
        if it is None:
            raise ReviewError(f"{i}: no such item in this export")
        if not it.needs_tick:
            raise ReviewError(f"{i}: a method item; method items are approved with the release, not ticked")
        ticks[i] = sha256(it.claim.encode())
        done.append(i)
    p = _ticks_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps({"ticks": dict(sorted(ticks.items()))}), encoding="utf-8")
    return done


def status(out: str | Path) -> tuple[list[HouseItem], list[HouseItem]]:
    """(ticked, waiting) among the items that need a tick."""
    out = Path(out)
    ticks = read_ticks(out)
    need = [i for i in _items(out) if i.needs_tick]
    ok = [i for i in need if ticks.get(i.id) == sha256(i.claim.encode())]
    return ok, [i for i in need if i not in ok]


def finalise(out: str | Path) -> dict:
    """Write RELEASE.json when every value and contested item is ticked."""
    out = Path(out)
    ticked, waiting = status(out)
    if waiting:
        raise ReviewError(f"{len(waiting)} item(s) still need a tick: " + ", ".join(i.id for i in waiting))
    mbytes = (out / "MANIFEST.json").read_bytes()
    manifest = json.loads(mbytes)
    release = {"version": manifest.get("version"), "manifest_sha256": sha256(mbytes),
               "ticked": sorted(i.id for i in ticked)}
    (out / "RELEASE.json").write_text(dumps(release), encoding="utf-8")
    problem = release_problem(out, mbytes, manifest, read_items(out))
    if problem:
        (out / "RELEASE.json").unlink()
        raise ReviewError(problem)
    return release
