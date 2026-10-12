"""Signed approval answers from seats (D-033).

A client that answers ``approval.respond`` as ``seat:<name>`` must sign the answer with that seat's
Ed25519 key. The signature covers the request id, the decision, the request's binding hash and the
seat name, so it cannot be replayed on another request, another decision or another text.

The public key comes from the bus registry (``$EXO_HOME/bus/registry.json`` or
``$EXO_BUS_REGISTRY``) when that document verifies, else from ``<keys dir>/<seat>.pub`` (the file
``exo.crypto.save_key`` writes beside the seat key).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from exo import crypto

KIND = "exo.approval.v1"


def approval_payload(request_id: str, decision: str, binding: str, seat: str) -> bytes:
    return crypto.canonical({"kind": KIND, "request_id": request_id, "decision": decision,
                             "binding": binding or "", "seat": seat})


def sign_approval(key, request_id: str, decision: str, binding: str, seat: str) -> str:
    """Base64 signature for ``ApprovalRespond.sig``. ``key`` is a SigningKey or a key file path."""
    if isinstance(key, (str, os.PathLike)):
        key = crypto.load_key(key)
    return crypto.sign(approval_payload(request_id, decision, binding, seat), key)


def seat_pubkeys(seat: str, env: Mapping[str, str] | None = None) -> list[str]:
    """Trusted public keys for ``seat``: the verified bus registry first, then the keys folder."""
    env = os.environ if env is None else env
    exo_home = Path(env.get("EXO_HOME") or Path.home() / ".exo").expanduser()
    out: list[str] = []
    reg_path = Path(env.get("EXO_BUS_REGISTRY") or exo_home / "bus" / "registry.json").expanduser()
    if reg_path.is_file():
        try:
            from exo.bus.registry import check_registry
            reg = json.loads(reg_path.read_text("utf-8"))
            check_registry(reg, env.get("EXO_BUS_LEADER_PUBKEY") or None)
            out += [s["pubkey"] for s in reg["seats"] if s.get("seat") == seat and s.get("pubkey")]
        except Exception:
            pass  # an unverified registry is not a key source
    kd = env.get("EXO_BUS_KEYS_DIR") or env.get("DIVAN_KEYS_DIR")
    pub = (Path(kd).expanduser() if kd else exo_home / "keys") / f"{seat}.pub"
    if pub.is_file():
        try:
            out.append(crypto.load_pub(pub))
        except OSError:
            pass
    return list(dict.fromkeys(out))


def verify_approval(sig: str, request_id: str, decision: str, binding: str, seat: str,
                    env: Mapping[str, str] | None = None) -> bool:
    if not sig or not seat:
        return False
    data = approval_payload(request_id, decision, binding, seat)
    return any(crypto.verify(data, sig, pub) for pub in seat_pubkeys(seat, env))
