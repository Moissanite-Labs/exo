"""Bus wire format: envelopes, chain hashes, sealed bodies, signed read headers.

Wire compatible with Divan Bus v0.1 (envelope v1, X-Divan-* headers). Canonical JSON, base64,
document signing and key helpers come from `exo.crypto`; only bus-specific pieces live here.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.public import Box
from nacl.signing import SigningKey, VerifyKey

from exo.crypto import (GENESIS, b64, canonical, now_ts, pubkey_b64, sha256_hex, sign_doc,  # noqa: F401
                        unb64, verify_doc)

ENVELOPE_KEYS = frozenset({"v", "id", "ts", "from", "to", "thread", "kind", "body", "attach", "enc", "prev", "sig"})
KINDS = frozenset({"msg", "ack", "claim", "notice", "directive", "report", "file"})
ENC_ALG = "x25519-xsalsa20-poly1305"
REPLAY_MINUTES = 5
HEADER_SEAT, HEADER_TS, HEADER_SIG = "X-Divan-Seat", "X-Divan-Ts", "X-Divan-Sig"


def chain_hash(prev: str, msg_id: str) -> str:
    return sha256_hex((prev + msg_id).encode("ascii"))


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must carry a timezone")
    return dt.astimezone(timezone.utc)


def ts_within(ts: str, minutes: float) -> bool:
    try:
        return abs(datetime.now(timezone.utc) - parse_ts(ts)) <= timedelta(minutes=minutes)
    except (ValueError, TypeError, AttributeError):
        return False


def load_signing_key(path_or_b64: str | os.PathLike) -> SigningKey:
    """A key file holds the base64 32-byte seed; the value may also be the seed itself.

    Unlike `exo.crypto.load_key` this accepts an inline seed (relay keys passed by env) and does not
    enforce 0600, matching the Divan relay. Seat keys written by `keygen` are still 0600.
    """
    try:
        with open(path_or_b64, "rb") as fh:
            raw = fh.read().strip().decode()
    except (OSError, ValueError, TypeError):
        raw = str(path_or_b64).strip()
    return SigningKey(unb64(raw))


def verify_key(pub_b64: str) -> VerifyKey:
    return VerifyKey(unb64(pub_b64))


def envelope_id(env: dict) -> str:
    return sha256_hex(canonical({k: v for k, v in env.items() if k not in ("id", "sig")}))


def finish_envelope(env: dict, key: SigningKey) -> dict:
    """Fill id and sig. The caller supplies every other field including prev."""
    env = {k: v for k, v in env.items() if k not in ("id", "sig")}
    env["id"] = envelope_id(env)
    return sign_doc(env, key)


def make_envelope(seat: str, to, body: str, prev: str, kind: str = "msg", thread: str | None = None,
                  enc: dict | None = None, attach: list | None = None, ts: str | None = None) -> dict:
    """The unsigned v1 envelope fields (no id, no sig)."""
    return {"v": 1, "ts": ts or now_ts(), "from": seat, "to": [to] if isinstance(to, str) else list(to),
            "thread": thread, "kind": kind, "body": body, "attach": list(attach or []), "enc": enc, "prev": prev}


def relay_mark(seat: str) -> dict:
    """The attach item the relay adds to an envelope it signs with its own key for a seat (an MCP post)."""
    return {"relay": {"composed_for": seat}}


def relay_composed(env: dict) -> bool:
    """True when the envelope says the relay composed it for its ``from`` seat (D-040). The relay key is valid
    only on such an envelope, and a reader never takes it as the seat's own signature."""
    return relay_mark(str(env.get("from") or "")) in (env.get("attach") or [])


def core(env: dict) -> dict:
    """The signed document of a stored envelope (drops seq, chain and any other relay fields)."""
    return {k: v for k, v in env.items() if k in ENVELOPE_KEYS}


# --- sealed bodies (NaCl Box between Ed25519 keys converted to X25519) ---------------

def _box(my_key: SigningKey, their_pub_b64: str) -> Box:
    return Box(my_key.to_curve25519_private_key(), verify_key(their_pub_b64).to_curve25519_public_key())


def encrypt_body(text: str, my_key: SigningKey, to_seat: str, to_pub_b64: str) -> tuple[str, dict]:
    sealed = _box(my_key, to_pub_b64).encrypt(text.encode("utf-8"))
    return b64(sealed.ciphertext), {"alg": ENC_ALG, "to": to_seat, "nonce": b64(sealed.nonce)}


def decrypt_body(body_b64: str, enc: dict, my_key: SigningKey, from_pub_b64: str) -> str:
    if enc.get("alg") != ENC_ALG:
        raise CryptoError(f"unsupported enc alg {enc.get('alg')!r}")
    return _box(my_key, from_pub_b64).decrypt(unb64(body_b64), unb64(enc["nonce"])).decode("utf-8")


# --- signed request headers for reads -------------------------------------------------

def request_message(method: str, path: str, ts: str) -> bytes:
    return f"{method.upper()}\n{path}\n{ts}".encode("utf-8")


def request_headers(seat: str, key: SigningKey, method: str, path: str) -> dict[str, str]:
    ts = now_ts()
    return {HEADER_SEAT: seat, HEADER_TS: ts, HEADER_SIG: b64(key.sign(request_message(method, path, ts)).signature)}


class ReplayCache:
    """Remembers (seat, ts, sig) triples seen inside the replay window; rejects repeats."""

    def __init__(self, minutes: float = REPLAY_MINUTES):
        self.ttl = minutes * 60
        self._seen: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def first_use(self, seat: str, ts: str, sig: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._seen) > 4096:
                self._seen = {k: t for k, t in self._seen.items() if t > now}
            key = (seat, ts, sig)
            if self._seen.get(key, 0) > now:
                return False
            self._seen[key] = now + self.ttl
            return True


def verify_request(headers, method: str, path: str, pubkey_for, replay: ReplayCache) -> str:
    """Return the authenticated seat or raise ValueError with a short reason.

    `headers` is any case-insensitive mapping (or a dict with lower-case keys);
    `pubkey_for(seat)` returns the base64 pubkey or None.
    """
    seat, ts, sig = (headers.get(h.lower()) for h in (HEADER_SEAT, HEADER_TS, HEADER_SIG))
    if not (seat and ts and sig):
        raise ValueError("missing X-Divan-Seat/Ts/Sig headers")
    pub = pubkey_for(seat)
    if pub is None:
        raise ValueError(f"unknown seat {seat!r}")
    if not ts_within(ts, REPLAY_MINUTES):
        raise ValueError("request timestamp outside the 5-minute window")
    try:
        verify_key(pub).verify(request_message(method, path, ts), unb64(sig))
    except (BadSignatureError, ValueError):
        raise ValueError("bad request signature") from None
    if not replay.first_use(seat, ts, sig):
        raise ValueError("replayed request")
    return seat
