"""Portable append authority contract. No private key is part of the client interface."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from exo import crypto

MAX_DOCUMENT = 1024 * 1024
MAX_INTENT = MAX_DOCUMENT - 32768
MAX_RECORD = MAX_DOCUMENT - 16384
MAX_BATCH_BYTES = MAX_DOCUMENT - 4096  # reserve space for the OS reply envelope
MAX_BATCH = 64
MAX_SEQ = (1 << 63) - 1
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")


class AuthorityError(ValueError):
    pass


class AuthorityUnavailable(AuthorityError):
    pass


class HeadConflict(AuthorityError):
    def __init__(self, head: dict):
        super().__init__("authority head changed; synchronize before retrying the same operation")
        self.head = head


class ProjectionPending(AuthorityError):
    """The append is committed. Repair the local copy; never repeat its external action."""
    def __init__(self, operation_id: str, record: dict):
        super().__init__("authoritative append committed; local ledger copy needs recovery")
        self.operation_id, self.record = operation_id, record


class AppendOutcomeUnknown(AuthorityUnavailable):
    def __init__(self, operation_id: str):
        super().__init__("append outcome unknown; use this operation id for lookup, never repeat the external action")
        self.operation_id = operation_id


class LedgerAuthority(Protocol):
    identity: dict  # independently pinned manifest; never adopted from an RPC reply
    writer: str

    def append(self, operation_id: str, expected_head: dict, intent: dict) -> dict: ...
    def lookup(self, operation_id: str) -> dict | None: ...
    def head(self, seq: int | None = None) -> dict: ...
    def read_since(self, seq: int, limit: int = MAX_BATCH) -> list[dict]: ...


def canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AuthorityError("expected finite JSON") from exc
    if len(raw) > MAX_DOCUMENT:
        raise AuthorityError("authority document exceeds limit")
    return raw


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_identity(identity: dict) -> dict:
    if not isinstance(identity, dict) or set(identity) != {"version", "stream_id", "checkpoint_pub", "writers"}:
        raise AuthorityError("invalid authority identity")
    if type(identity["version"]) is not int or identity["version"] != 1:
        raise AuthorityError("unsupported authority identity")
    if not isinstance(identity["stream_id"], str) or not NAME.fullmatch(identity["stream_id"]):
        raise AuthorityError("invalid stream identity")
    writers = identity["writers"]
    if not isinstance(writers, dict) or not 1 <= len(writers) <= 64:
        raise AuthorityError("invalid writer inventory")
    pubs = [identity["checkpoint_pub"]]
    for name, config in writers.items():
        if (not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(config, dict) or
                set(config) != {"pub", "author", "authors"}):
            raise AuthorityError("invalid writer identity")
        if (not isinstance(config["authors"], list) or not config["authors"] or
                any(not isinstance(a, str) or not NAME.fullmatch(a) for a in config["authors"]) or
                config["author"] not in config["authors"]):
            raise AuthorityError("writer attribution is not provisioned")
        pubs.append(config["pub"])
    try:
        if any(not isinstance(pub, str) for pub in pubs):
            raise ValueError()
        decoded_pubs = [crypto.unb64(pub) for pub in pubs]
        if any(len(pub) != 32 for pub in decoded_pubs):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise AuthorityError("invalid pinned public key") from exc
    # Unused base64 padding bits can differ without changing the Ed25519 key.
    # Compare key material without rewriting the pinned identity document.
    if decoded_pubs[0] in decoded_pubs[1:]:
        raise AuthorityError("checkpoint and event keys must be separate")
    return json.loads(canonical(identity))


def validate_intent(intent: Mapping) -> dict:
    from exo.ledger import KINDS, VERDICTS
    if not isinstance(intent, Mapping) or set(intent) != {"author", "kind", "body", "session", "refs"}:
        raise AuthorityError("invalid append intent")
    raw = canonical(dict(intent))
    if len(raw) > MAX_INTENT:
        raise AuthorityError("append intent exceeds limit")
    doc = json.loads(raw)
    if (not isinstance(doc["author"], str) or not NAME.fullmatch(doc["author"]) or
            not isinstance(doc["kind"], str) or doc["kind"] not in KINDS or not isinstance(doc["body"], dict) or
            (doc["session"] is not None and not isinstance(doc["session"], str)) or
            not isinstance(doc["refs"], list) or len(doc["refs"]) > 4096 or
            any(not isinstance(ref, str) or not HASH.fullmatch(ref) for ref in doc["refs"])):
        raise AuthorityError("invalid append fields")
    if len(set(doc["refs"])) != len(doc["refs"]):
        raise AuthorityError("duplicate references")
    if doc["kind"] == "verdict" and (not isinstance(doc["body"].get("verdict"), str) or
                                    doc["body"].get("verdict") not in VERDICTS or
                                    doc["body"].get("target") not in doc["refs"]):
        raise AuthorityError("invalid verdict reference")
    return doc


def validate_expected_head(head: dict) -> dict:
    if (not isinstance(head, dict) or set(head) != {"seq", "chain"} or type(head["seq"]) is not int or
            not 0 <= head["seq"] <= MAX_SEQ or not isinstance(head["chain"], str) or not HASH.fullmatch(head["chain"])):
        raise AuthorityError("invalid expected head")
    return dict(head)


def verify_checkpoint(identity: dict, checkpoint: dict) -> None:
    canonical(checkpoint)  # reject non-JSON and oversized inputs before signature parsing
    fields = {"version", "type", "stream_id", "identity_hash", "seq", "event_id", "chain", "prev_chain",
              "prev_checkpoint", "writer", "pub", "sig"}
    if (not isinstance(checkpoint, dict) or set(checkpoint) != fields or
            type(checkpoint["version"]) is not int or checkpoint["version"] != 1 or
            checkpoint["type"] != "exo-ledger-checkpoint" or checkpoint["stream_id"] != identity["stream_id"] or
            checkpoint["identity_hash"] != digest(identity) or checkpoint["pub"] != identity["checkpoint_pub"] or
            type(checkpoint["seq"]) is not int or not 0 <= checkpoint["seq"] <= MAX_SEQ or
            any(not isinstance(checkpoint[k], str) or not HASH.fullmatch(checkpoint[k])
                for k in ("chain", "prev_chain", "prev_checkpoint")) or
            not crypto.verify_doc(checkpoint, identity["checkpoint_pub"])):
        raise AuthorityError("untrusted authority checkpoint")
    if checkpoint["seq"] == 0:
        if (checkpoint["chain"] != crypto.GENESIS or checkpoint["prev_chain"] != crypto.GENESIS or
                checkpoint["prev_checkpoint"] != crypto.GENESIS or checkpoint["event_id"] is not None or
                checkpoint["writer"] is not None):
            raise AuthorityError("invalid genesis checkpoint")
    elif (not isinstance(checkpoint["event_id"], str) or not HASH.fullmatch(checkpoint["event_id"]) or
          not isinstance(checkpoint["writer"], str) or checkpoint["writer"] not in identity["writers"]):
        raise AuthorityError("invalid event checkpoint")


def verify_checkpoint_successor(identity: dict, checkpoint: dict, previous: dict) -> None:
    from exo.ledger import chain_hash
    verify_checkpoint(identity, previous)
    verify_checkpoint(identity, checkpoint)
    if (checkpoint["seq"] != previous["seq"] + 1 or
            checkpoint["prev_checkpoint"] != digest(previous) or
            checkpoint["prev_chain"] != previous["chain"] or
            checkpoint["chain"] != chain_hash(previous["chain"], checkpoint["event_id"])):
        raise AuthorityError("checkpoint does not extend the pinned history")


def verify_record(identity: dict, record: dict, previous: dict) -> None:
    from exo.ledger import SIGNED_KEYS, chain_hash, event_id, signed_part
    if not isinstance(record, dict) or set(record) != {"event", "checkpoint"}:
        raise AuthorityError("invalid committed record")
    ev, cp = record["event"], record["checkpoint"]
    verify_checkpoint_successor(identity, cp, previous)
    if (not isinstance(ev, dict) or set(ev) != set(SIGNED_KEYS) | {"seq", "chain"} or
            cp["seq"] != previous["seq"] + 1 or cp["prev_checkpoint"] != digest(previous) or
            cp["prev_chain"] != previous["chain"] or type(ev["seq"]) is not int or ev["seq"] != cp["seq"] or
            not isinstance(ev["ts"], str) or
            ev["prev"] != previous["chain"] or ev["id"] != cp["event_id"] or ev["chain"] != cp["chain"]):
        raise AuthorityError("record does not extend the pinned checkpoint")
    writer = identity["writers"][cp["writer"]]
    if ev["pub"] != writer["pub"] or ev["author"] not in writer["authors"]:
        raise AuthorityError("event writer or attribution is not pinned")
    validate_intent({k: ev[k] for k in ("author", "kind", "body", "session", "refs")})
    doc = signed_part(ev)
    if (event_id(doc) != ev["id"] or chain_hash(ev["prev"], ev["id"]) != ev["chain"] or
            not crypto.verify_doc(doc, ev["pub"])):
        raise AuthorityError("invalid portable event signature or chain")
