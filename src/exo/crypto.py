"""Canonical JSON, Ed25519 keys and signatures (PyNaCl).

Key files hold the base64 32-byte seed on one line, mode 0600. The public key file
(`<name>.pub`) holds the base64 verify key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .core.private import atomic_write

GENESIS = "0" * 64


def canonical(obj) -> bytes:
    """Sorted keys, no whitespace, UTF-8, no ASCII escaping. Stable across runs and machines."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


# --- keys --------------------------------------------------------------------

def keygen() -> SigningKey:
    return SigningKey.generate()


def pubkey_b64(key: SigningKey) -> str:
    return b64(bytes(key.verify_key))


def save_key(key: SigningKey, path: str | os.PathLike) -> Path:
    """Write the seed (0600) and the public key next to it. Refuses to overwrite. A key written into
    ``$EXO_HOME/keys`` is pinned in the key registry (exo.keyring)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(b64(bytes(key)) + "\n")
    os.chmod(path, 0o600)
    atomic_write(path.with_suffix(".pub"), pubkey_b64(key) + "\n", mode=None)  # a symlink there is not followed
    from .keyring import pin_new_key  # D-040: a key made in $EXO_HOME/keys is pinned as it is made
    pin_new_key(path, pubkey_b64(key))
    return path


def load_key(path: str | os.PathLike) -> SigningKey:
    path = Path(path)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"key file {path} is mode {oct(mode)}; expected 0600")
    return SigningKey(unb64(path.read_text().strip()))


def load_pub(path: str | os.PathLike) -> str:
    return Path(path).read_text().strip()


# --- signing -----------------------------------------------------------------

def digest(doc: dict, exclude: tuple[str, ...] = ("sig",)) -> bytes:
    """sha256 of the canonical document without the excluded keys."""
    return hashlib.sha256(canonical({k: v for k, v in doc.items() if k not in exclude})).digest()


def sign(data: bytes, key: SigningKey) -> str:
    return b64(key.sign(data).signature)


def verify(data: bytes, sig_b64: str, pub_b64: str) -> bool:
    try:
        VerifyKey(unb64(pub_b64)).verify(data, unb64(sig_b64))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def sign_doc(doc: dict, key: SigningKey) -> dict:
    doc = {k: v for k, v in doc.items() if k != "sig"}
    doc["sig"] = sign(digest(doc), key)
    return doc


def verify_doc(doc: dict, pub_b64: str) -> bool:
    sig = doc.get("sig")
    return isinstance(sig, str) and verify(digest(doc), sig, pub_b64)


@dataclass(frozen=True)
class Signer:
    """A signing identity: an author name bound to an Ed25519 key."""

    author: str
    key: SigningKey

    @property
    def pub(self) -> str:
        return pubkey_b64(self.key)

    @classmethod
    def from_file(cls, author: str, path: str | os.PathLike) -> "Signer":
        return cls(author, load_key(path))
