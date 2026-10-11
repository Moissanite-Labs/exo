"""The signed seat registry: who may talk, which team they sit on, and their public keys."""
from __future__ import annotations

import json
import logging
import threading
from datetime import date

from . import wire

log = logging.getLogger("exo.bus.registry")
SEAT_FIELDS = ("seat", "team", "platform", "pubkey", "role", "since")


class RegistryError(Exception):
    pass


def registry_hash(reg: dict) -> str:
    """Hash of the full signed document; the next version carries it as prev_hash."""
    return wire.sha256_hex(wire.canonical(reg))


def check_registry(reg: dict, leader_pubkey: str | None = None) -> str:
    """Validate structure and signature. Returns the leader pubkey the document is signed by."""
    for name in ("seats", "version", "prev_hash", "signed_by", "sig"):
        if name not in reg:
            raise RegistryError(f"registry missing {name!r}")
    seats = {s["seat"]: s for s in reg["seats"]}
    if len(seats) != len(reg["seats"]):
        raise RegistryError("duplicate seat id in registry")
    for s in reg["seats"]:
        for name in SEAT_FIELDS:
            if name not in s:
                raise RegistryError(f"seat {s.get('seat')!r} missing {name!r}")
        if ":" in s["seat"] or s["seat"] == "group":
            raise RegistryError(f"invalid seat id {s['seat']!r}")
    signer = seats.get(reg["signed_by"])
    if signer is None or signer["role"] != "leader":
        raise RegistryError(f"registry signer {reg['signed_by']!r} is not a leader seat")
    pub = leader_pubkey or signer["pubkey"]
    if pub != signer["pubkey"]:
        raise RegistryError("registry signer key does not match the pinned leader key")
    if not wire.verify_doc(reg, pub):
        raise RegistryError("registry signature does not verify")
    return pub


def parse_seat(text: str) -> dict:
    """`seat,team,platform,pubkey[,role[,since]]` to a seat record."""
    parts = text.split(",")
    if len(parts) < 4:
        raise ValueError(f"seat needs seat,team,platform,pubkey[,role[,since]]: {text!r}")
    seat, team, platform, pubkey = parts[:4]
    role = parts[4] if len(parts) > 4 else "member"
    since = parts[5] if len(parts) > 5 else date.today().isoformat()
    return {"seat": seat, "team": team, "platform": platform, "pubkey": pubkey, "role": role, "since": since}


def build_registry(leader_key, leader_seat: str, seats: list[dict], previous: dict | None = None,
                   remove: list[str] | tuple = ()) -> dict:
    """Merge seats onto `previous` (same ids replace), bump the version, link prev_hash, sign."""
    prior = [s for s in (previous or {}).get("seats", []) if s["seat"] not in remove]
    merged = {s["seat"]: s for s in prior}
    merged.update({s["seat"]: s for s in seats})
    doc = {"seats": sorted(merged.values(), key=lambda s: s["seat"]),
           "version": (previous["version"] + 1) if previous else 1,
           "prev_hash": registry_hash(previous) if previous else None,
           "signed_by": leader_seat}
    signed = wire.sign_doc(doc, leader_key)
    check_registry(signed)
    return signed


class Registry:
    """Loaded from a signed registry.json. Thread-safe reads; reload() replaces atomically."""

    def __init__(self, path, leader_pubkey: str | None = None):
        self.path = str(path)
        self.leader_pubkey = leader_pubkey  # pinned by config, else trust on first load
        self._lock = threading.Lock()
        self._doc: dict = {}
        self._seats: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        with open(self.path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        pub = check_registry(doc, self.leader_pubkey)
        with self._lock:
            if self._doc and doc["version"] < self._doc["version"]:
                raise RegistryError("refusing to load an older registry version")
            self.leader_pubkey = pub
            self._doc = doc
            self._seats = {s["seat"]: s for s in doc["seats"]}
        log.info("registry v%s loaded: %d seats", doc["version"], len(self._seats))

    @property
    def document(self) -> dict:
        return self._doc

    @property
    def seats(self) -> list[dict]:
        return list(self._doc["seats"])

    def seat(self, seat_id: str) -> dict | None:
        return self._seats.get(seat_id)

    def pubkey(self, seat_id: str) -> str | None:
        s = self._seats.get(seat_id)
        return s["pubkey"] if s else None

    def teams(self) -> set[str]:
        return {s["team"] for s in self._seats.values()}

    def audiences(self, seat_id: str) -> list[str]:
        """Every `to` value that reaches this seat: itself, its team, and the whole group."""
        s = self._seats.get(seat_id)
        return [seat_id, f"team:{s['team']}", "group"] if s else []

    def valid_recipient(self, to: str) -> bool:
        if to == "group" or to in self._seats:
            return True
        return to.startswith("team:") and to[5:] in self.teams()
