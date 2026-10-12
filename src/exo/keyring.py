"""Pinned keys (D-040): which public keys may sign this home's ledgers, and which key each bus seat speaks with.

The registry is one JSON file, ``$EXO_HOME/keys/registry.json`` (0600), next to the key files. The gate treats
``keys/`` as credentials, so the agent cannot change it without asking.

    {"v": 1,
     "keys":       {"<name>": ["<base64 Ed25519 public key>", ...]},   local signing keys, by key file name
     "seats":      {"<bus seat>": "<base64 public key>"},               bus seats, pinned on first sight
     "principals": ["<principal id>", ...]}                             ledger authors that are the principal

How keys get pinned:

- A key Exo creates in ``$EXO_HOME/keys`` (``exo keygen``, a seat's first run, exo-memory, the gate) is pinned
  when it is written, unless that name already has a different pinned key.
- The first time a home without a registry is opened, every key already in ``keys/`` is pinned (the upgrade
  from a store made before D-040). That moment is trusted as it is: whatever key files exist then are pinned.
- ``exo keys pin NAME PUB`` pins by hand; ``--replace`` swaps a name's key (key rotation).
- A bus seat's key is pinned the first time the channel sees a message from it. A later message signed by a
  different key is refused until the principal pins the new key with ``exo keys pin --seat``.

What ``allows(author, pub)`` checks for a ledger event: the signing key is pinned under some name, and an
author that is itself a pinned name (a seat or tool with its own key) is signed by its own key. The principal's
words are recorded by the agent's seat, so an author that is the principal may be signed by any pinned key.

What it does not protect against: a process running as the same user can write the key files and this registry
directly. The gate asks before such writes; nothing here can stop a process that skips the gate.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Iterator
from pathlib import Path

from . import crypto

FILE = "registry.json"


def keys_dir(home: str | os.PathLike | None = None) -> Path:
    from .store import exo_home
    return (Path(home).expanduser() if home else exo_home()) / "keys"


class KeyRegistry:
    def __init__(self, path: Path, data: dict | None = None) -> None:
        self.path = Path(path)
        d = data or {}
        self.keys: dict[str, list[str]] = {str(k): [str(x) for x in (v if isinstance(v, list) else [v])]
                                           for k, v in (d.get("keys") or {}).items()}
        self.seats: dict[str, str] = {str(k): str(v) for k, v in (d.get("seats") or {}).items()}
        self.principals: list[str] = [str(x) for x in d.get("principals") or []]

    # ---------------------------------------------------------------- load and save

    @classmethod
    def path_for(cls, home: str | os.PathLike | None = None) -> Path:
        return keys_dir(home) / FILE

    @classmethod
    def load(cls, home: str | os.PathLike | None = None, *, create: bool = True) -> "KeyRegistry":
        """The registry for ``home`` (default ``$EXO_HOME``). With ``create`` and no registry yet, pin the keys
        already in ``keys/`` and the principal stores already under ``principals/`` (the upgrade path)."""
        path = cls.path_for(home)
        if path.is_file():
            return cls(path, json.loads(path.read_text(encoding="utf-8") or "{}"))
        reg = cls(path)
        if create:
            with reg._locked():
                if path.is_file():  # another process made it while we waited
                    return cls(path, json.loads(path.read_text(encoding="utf-8") or "{}"))
                reg._adopt_existing(home)
                reg._write()
        return reg

    def _adopt_existing(self, home: str | os.PathLike | None) -> None:
        d = self.path.parent
        if d.is_dir():
            for f in sorted(d.iterdir()):
                pub = ""
                if f.suffix == ".pub":
                    pub = f.read_text(encoding="utf-8").strip()
                elif f.suffix == ".key" and not f.with_suffix(".pub").exists():
                    try:
                        pub = crypto.pubkey_b64(crypto.load_key(f))
                    except (OSError, ValueError, PermissionError):
                        pub = ""
                if pub and pub not in self.keys.get(f.stem, []):
                    self.keys.setdefault(f.stem, []).append(pub)
        principals = d.parent / "principals"
        if principals.is_dir():
            for p in sorted(principals.iterdir()):
                if p.is_dir() and p.name not in self.principals:
                    self.principals.append(p.name)

    def to_dict(self) -> dict:
        return {"v": 1, "keys": self.keys, "seats": self.seats, "principals": self.principals}

    def _write(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{FILE}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path.with_name(".registry.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _update(self, fn) -> object:
        """Re-read under the lock, apply ``fn(self)``, write back. Returns what ``fn`` returns."""
        with self._locked():
            if self.path.is_file():
                fresh = KeyRegistry(self.path, json.loads(self.path.read_text(encoding="utf-8") or "{}"))
                self.keys, self.seats, self.principals = fresh.keys, fresh.seats, fresh.principals
            out = fn(self)
            self._write()
            return out

    # ---------------------------------------------------------------- pinning

    def pin(self, name: str, pub: str, *, replace: bool = False) -> str:
        """Pin ``pub`` as ``name``'s key. Returns ``pinned``, ``known`` (already pinned), or ``conflict`` (the
        name has another key; nothing changed unless ``replace``, which makes it the only key)."""
        def fn(r: KeyRegistry) -> str:
            have = r.keys.get(name, [])
            if pub in have:
                return "known"
            if have and not replace:
                return "conflict"
            r.keys[name] = [pub]
            return "pinned"
        return str(self._update(fn))

    def pin_seat(self, seat: str, pub: str, *, replace: bool = False) -> str:
        """A bus seat's key: ``pinned`` on first sight, ``match`` after, ``changed`` when it differs (refused
        unless ``replace``)."""
        if not seat or not pub:
            return "changed"
        if self.seats.get(seat) == pub:
            return "match"

        def fn(r: KeyRegistry) -> str:
            have = r.seats.get(seat)
            if have == pub:
                return "match"
            if have and not replace:
                return "changed"
            r.seats[seat] = pub
            return "pinned"
        return str(self._update(fn))

    def add_principal(self, pid: str) -> None:
        if not pid or pid in self.principals:
            return
        self._update(lambda r: r.principals.append(pid) if pid not in r.principals else None)

    # ---------------------------------------------------------------- checks

    def owners(self, pub: str) -> list[str]:
        return sorted(n for n, ks in self.keys.items() if pub in ks)

    def is_principal(self, author: str) -> bool:
        return author == "principal" or author in self.principals

    def reason(self, author: str, pub: str) -> str:
        """Why an event by ``author`` signed with ``pub`` is not trusted, or '' when it is."""
        owners = self.owners(pub)
        if not owners:
            return "the signing key is not pinned in the key registry"
        own = self.keys.get(author)
        if own and pub not in own and not self.is_principal(author):
            return f"{author!r} has its own pinned key, but this event is signed by {', '.join(owners)}"
        return ""

    def allows(self, author: str, pub: str) -> bool:
        return not self.reason(author, pub)


def pin_new_key(path: str | os.PathLike, pub: str) -> None:
    """Called by ``crypto.save_key``: a key written into ``$EXO_HOME/keys`` is pinned under its file name."""
    path = Path(path)
    try:
        if path.suffix != ".key" or path.parent.resolve() != keys_dir().resolve():
            return
        KeyRegistry.load().pin(path.stem, pub)
    except OSError:
        pass  # pinning never breaks key creation; an unpinned key fails `exo memory verify`, which says so
