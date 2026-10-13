"""Protected model-spend policy selection. Installed host code remains trusted.

The service selects a root-owned file; user/seat configuration cannot replace it.
Read through directory descriptors so a symlink swap cannot change the checked file.
"""
from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .billing import Price, SpendDenied, _money

POLICY_ENV = "EXO_MODEL_SPEND_POLICY"
MAX_BYTES = 65536
FIELDS = {"principal", "journal", "journal_id", "daily_usd", "monthly_usd", "routes"}
PRICE_FIELDS = {"contract", "base_url", "model", "transport", "input_bound", "max_output",
                "input", "output", "cache_read", "cache_write"}


def required() -> bool:
    return POLICY_ENV in os.environ or os.environ.get("EXO_MODEL_SPEND_REQUIRED") == "1"


def _absolute(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("policy paths must be absolute and normalized")
    path = Path(value)
    if path.anchor != "/" or str(path) != value or ".." in path.parts:
        raise ValueError("policy paths must be absolute and normalized")
    return path


def _trusted(fd: int, *, directory: bool):
    info = os.fstat(fd)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if info.st_uid != 0 or info.st_mode & 0o022 or not kind(info.st_mode):
        raise ValueError("model-spend policy and ancestors must be root-controlled")
    return info


def _read(path: Path) -> bytes:
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        _trusted(fd, directory=True)
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            _trusted(fd, directory=True)
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            if _trusted(leaf, directory=False).st_size > MAX_BYTES:
                raise ValueError("model-spend policy is too large")
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("model-spend policy is too large")
            return data
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate policy field")
        value[key] = item
    return value


def _decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,18})?", value):
        raise ValueError("protected amounts must be bounded exact decimal strings")
    _money(value)


def validate_policy(raw: Any) -> dict:
    """Validate parsed protected policy data; return the same dictionary without mutation."""
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise ValueError("invalid model-spend policy fields")
    if not isinstance(raw["principal"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", raw["principal"]):
        raise ValueError("invalid model-spend principal")
    _absolute(raw["journal"])
    if not isinstance(raw["journal_id"], str) or str(uuid.UUID(raw["journal_id"])) != raw["journal_id"]:
        raise ValueError("invalid journal UUID")
    for name in ("daily_usd", "monthly_usd"):
        _decimal(raw[name])
    if not isinstance(raw["routes"], dict) or len(raw["routes"]) > 128:
        raise ValueError("invalid protected routes")
    for ref, price in raw["routes"].items():
        if not re.fullmatch(r"[^:\s]{1,128}:[^\s]{1,256}", ref) or not isinstance(price, dict) or set(price) != PRICE_FIELDS:
            raise ValueError("invalid protected route contract")
        if any(not isinstance(price[k], str) for k in PRICE_FIELDS - {"input_bound", "max_output"}):
            raise ValueError("protected prices and identifiers must be strings")
        for name in ("input", "output", "cache_read", "cache_write"):
            _decimal(price[name])
        Price.parse(price)
    return raw


def decode_policy(data: bytes | str) -> dict:
    """Decode bounded UTF-8 JSON, reject duplicates, and validate the full policy schema."""
    if not isinstance(data, (bytes, str)) or len(data.encode() if isinstance(data, str) else data) > MAX_BYTES:
        raise ValueError("model-spend policy is too large or is not JSON text")
    return validate_policy(json.loads(data.decode("utf-8") if isinstance(data, bytes) else data,
                                      object_pairs_hook=_pairs))


def _freeze(raw):
    return MappingProxyType({key: _freeze(value) if isinstance(value, dict) else value for key, value in raw.items()})


@dataclass(frozen=True)
class ResolvedSpendPolicy:
    config: Mapping[str, Any] | None
    required: bool
    source: str | None


def resolve_spend_policy(config=None, *, principal: str | None = None) -> ResolvedSpendPolicy:
    source = os.environ.get(POLICY_ENV)
    if POLICY_ENV in os.environ:
        try:
            config = _freeze(decode_policy(_read(_absolute(source))))
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise SpendDenied("protected model-spend policy is unavailable or invalid") from exc
    active = principal or os.environ.get("EXO_PRINCIPAL")
    if config is not None and active and config.get("principal") != active:
        raise SpendDenied("model-spend policy principal does not match the active principal")
    if config is None and required():
        raise SpendDenied("required model-spend policy is missing; ask the principal to configure it")
    return ResolvedSpendPolicy(config, required(), source)
