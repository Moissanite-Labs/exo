"""Recover an untrusted local ledger copy using independently pinned signed records."""
from __future__ import annotations

import json

from exo import crypto

from . import AuthorityError, canonical, validate_identity, verify_checkpoint, verify_checkpoint_successor, verify_record


def verify_projection(ledger, identity: dict, checkpoints: list[dict]) -> dict:
    """Offline verification against independently supplied identity/checkpoints; never trust local head files.

    A checkpoint sequence is portable evidence for a witnessed/signer-confirmed prefix, not proof of freshness.
    """
    identity = validate_identity(identity)
    if not checkpoints:
        raise AuthorityError("an independently trusted checkpoint chain is required")
    previous = checkpoints[0]
    verify_checkpoint(identity, previous)
    if previous["seq"] != 0:
        raise AuthorityError("checkpoint chain must start at genesis")
    for cp in checkpoints[1:]:
        verify_checkpoint_successor(identity, cp, previous)
        previous = cp
    previous = checkpoints[0]
    events = list(ledger.iter())
    for index, cp in enumerate(checkpoints[1:], 1):
        verify_checkpoint(identity, cp)
        if cp["seq"] != index:
            raise AuthorityError("checkpoint sequence is not contiguous")
        if index > len(events):
            return {"ok": False, "state": "missing_tail", "local_seq": len(events),
                    "authority_seq": checkpoints[-1]["seq"], "fresh": False}
        try:
            verify_record(identity, {"event": events[index - 1], "checkpoint": cp}, previous)
        except AuthorityError:
            return {"ok": False, "state": "divergent", "seq": index, "fresh": False}
        previous = cp
    if len(events) > previous["seq"]:
        return {"ok": False, "state": "unconfirmed_tail", "local_seq": len(events),
                "authority_seq": previous["seq"], "fresh": False}
    return {"ok": True, "state": "verified_prefix", "local_seq": len(events),
            "authority_seq": previous["seq"], "chain": previous["chain"], "fresh": False}


def synchronize(ledger, authority) -> dict:
    """Append missing signed rows, refusing any divergent prefix or unconfirmed local tail.

    The full prefix is checked on each synchronization; optimizing this requires a protected checkpoint cache.
    """
    from exo.ledger import COLS
    identity = authority.identity
    with ledger._lock:
        ledger.db.execute("BEGIN IMMEDIATE")
        try:
            # Read the authority head after locking the copy: another local writer may
            # have advanced both while we waited for SQLite's cross-process lock.
            latest = authority.head()
            verify_checkpoint(identity, latest)
            previous = authority.head(0)
            verify_checkpoint(identity, previous)
            if previous["seq"] != 0:
                raise AuthorityError("authority did not return genesis")
            rows = list(ledger.iter())
            if len(rows) > latest["seq"]:
                raise AuthorityError("local ledger has an unconfirmed tail")
            while previous["seq"] < latest["seq"]:
                records = authority.read_since(previous["seq"], min(64, latest["seq"] - previous["seq"]))
                if not records:
                    raise AuthorityError("authority recovery is missing committed records")
                for record in records:
                    verify_record(identity, record, previous)
                    ev, cp = record["event"], record["checkpoint"]
                    if ev["seq"] > latest["seq"]:
                        raise AuthorityError("authority exceeded the requested recovery range")
                    index = ev["seq"] - 1
                    if index < len(rows):
                        if canonical(rows[index]) != canonical(ev):
                            raise AuthorityError("local ledger diverges from authoritative history")
                    else:
                        # Explicit sequence, not SQLite's next-row guess, preserves the committed order.
                        ledger.db.execute(f"INSERT INTO events ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (ev["seq"], ev["id"], ev["ts"], ev["author"], ev["kind"],
                             crypto.canonical(ev["body"]).decode(), ev["session"], json.dumps(ev["refs"]),
                             ev["prev"], ev["pub"], ev["sig"], ev["chain"]))
                    previous = cp
            if canonical(previous) != canonical(latest):
                raise AuthorityError("recovered checkpoint differs from the signed authority head")
            ledger.db.execute("COMMIT")
        except BaseException:
            if ledger.db.in_transaction:
                ledger.db.execute("ROLLBACK")
            raise
    return latest
