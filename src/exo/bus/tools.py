"""Operator tools: build and sign the seat registry, write signed checkpoints of the log head."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import wire
from .registry import build_registry, parse_seat
from .store import Store


def make_registry(leader_key, leader_seat: str, out, seats: list[str] | list[dict] = (), previous=None,
                  remove=(), leader_team: str = "leadership", leader_platform: str = "unknown") -> dict:
    """Build, sign and write registry.json. `previous` is a path or dict to carry forward.

    The leader seat is added automatically if it is in neither `seats` nor `previous`.
    """
    key = wire.load_signing_key(leader_key) if not hasattr(leader_key, "verify_key") else leader_key
    if isinstance(previous, (str, os.PathLike)):
        previous = json.loads(Path(previous).read_text())
    records = [parse_seat(s) if isinstance(s, str) else s for s in seats]
    known = {s["seat"] for s in records} | {s["seat"] for s in (previous or {}).get("seats", []) if s["seat"] not in remove}
    if leader_seat not in known:
        records.append({"seat": leader_seat, "team": leader_team, "platform": leader_platform,
                        "pubkey": wire.pubkey_b64(key), "role": "leader",
                        "since": datetime.now(timezone.utc).date().isoformat()})
    signed = build_registry(key, leader_seat, records, previous, remove)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return signed


def sign_checkpoint(seq: int, head: str, signers: dict, ts: str | None = None) -> dict:
    """`signers` maps a signer name to its SigningKey. Each signs the same core {seq, head, ts}."""
    core = {"seq": seq, "head": head, "ts": ts or wire.now_ts()}
    sigs = {name: wire.sign_doc(core, key)["sig"] for name, key in signers.items()}
    return {**core, "sigs": sigs, "pubkeys": {name: wire.pubkey_b64(k) for name, k in signers.items()}}


def verify_checkpoint(doc: dict, pubkeys: dict[str, str] | None = None) -> bool:
    """True if every signature verifies, against `pubkeys` if given, else the keys the document carries."""
    core = {k: doc[k] for k in ("seq", "head", "ts")}
    keys = pubkeys or doc.get("pubkeys", {})
    return bool(doc.get("sigs")) and all(
        name in keys and wire.verify_doc({**core, "sig": sig}, keys[name]) for name, sig in doc["sigs"].items())


def checkpoint(db, relay_key, out_dir, cosigners: dict | None = None, stamp: bool = True,
               git_push: bool = False, log=print) -> tuple[dict, Path]:
    """Sign the current head, record it in the relay's checkpoints table and write <out_dir>/<date>.json.

    `cosigners` maps extra signer names to key files or seeds. With `stamp`, runs `ots stamp` if the
    OpenTimestamps client is installed. With `git_push`, commits and pushes `out_dir` if it is a git repo.
    """
    signers = {"relay": wire.load_signing_key(relay_key)}
    for name, k in (cosigners or {}).items():
        signers[name] = wire.load_signing_key(k)
    store = Store(db)
    seq, head = store.head()
    doc = sign_checkpoint(seq, head, signers)
    store.add_checkpoint(doc)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".json")
    path.write_bytes(wire.canonical(doc) + b"\n")
    log(f"checkpoint seq={seq} head={head[:16]}… -> {path}")
    if stamp:
        ots = shutil.which("ots")
        if not ots:
            log("ots not installed; skipping OpenTimestamps proof (pip install opentimestamps-client)")
        else:
            try:
                subprocess.run([ots, "stamp", str(path)], check=True, timeout=120)
                log(f"OpenTimestamps proof: {path}.ots")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                log(f"ots stamp failed, checkpoint still written: {e}")
    if git_push and (out_dir / ".git").exists():
        git = ["git", "-C", str(out_dir)]
        subprocess.run([*git, "add", "-A"], check=True)
        if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run([*git, "commit", "-q", "-m", f"checkpoint {path.stem}"], check=True)
        subprocess.run([*git, "push", "-q"], check=True)
    return doc, path
