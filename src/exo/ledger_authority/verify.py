"""Read-only portable verification against independently obtained checkpoint files.

python -m exo.ledger_authority.verify --ledger copy.sqlite --identity identity.json --checkpoints checkpoints.jsonl
This checks a signed prefix. It cannot establish that a supplied checkpoint is current.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from exo.ledger import COLS, _row
from . import MAX_BATCH, MAX_BATCH_BYTES, MAX_DOCUMENT, AuthorityError, AuthorityUnavailable, canonical, validate_identity, verify_checkpoint, verify_record
from .projection import verify_projection


def verify_files(ledger_path: Path, identity_path: Path, checkpoints_path: Path) -> dict:
    with Path(identity_path).open("rb") as source:
        raw = source.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise AuthorityError("identity exceeds limit")
    identity = validate_identity(json.loads(raw))
    checkpoints = []
    with Path(checkpoints_path).open("rb") as source:
        while raw := source.readline(MAX_DOCUMENT + 1):
            if len(raw) > MAX_DOCUMENT:
                raise AuthorityError("checkpoint exceeds limit")
            checkpoints.append(json.loads(raw))
    return verify_copy(ledger_path, identity, checkpoints)


def verify_copy(ledger_path, identity, checkpoints):
    # Unlike Ledger construction, read-only verification neither creates a missing
    # database nor reinstalls triggers or changes the journal mode of an existing one.
    db = sqlite3.connect(Path(ledger_path).resolve().as_uri() + "?mode=ro", uri=True)
    class ReadOnlyCopy:
        def iter(self):
            return (_row(row) for row in db.execute(f"SELECT {COLS} FROM events ORDER BY seq"))
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return verify_projection(ReadOnlyCopy(), identity, checkpoints)
    finally:
        db.close()


def _verify_authority_once(ledger_path, authority, reader=None):
    """Inspect a copy against a captured authenticated service head, without repair.

    The live signer is queried, but this is not an independently fresh witness
    receipt. The prefix result intentionally retains ``fresh: false``.
    """
    identity = validate_identity(authority.identity)
    latest, previous = authority.head(), authority.head(0)
    verify_checkpoint(identity, latest)
    verify_checkpoint(identity, previous)
    if previous["seq"] != 0:
        raise AuthorityError("authority did not return genesis")
    checkpoints = [previous]
    while previous["seq"] < latest["seq"]:
        limit = min(MAX_BATCH, latest["seq"] - previous["seq"])
        records = authority.read_since(previous["seq"], limit)
        if not isinstance(records, list) or not 1 <= len(records) <= limit or len(canonical(records)) > MAX_BATCH_BYTES:
            raise AuthorityError("invalid authority checkpoint replay")
        for record in records:
            verify_record(identity, record, previous)
            previous = record["checkpoint"]
            checkpoints.append(previous)
    if canonical(previous) != canonical(latest):
        raise AuthorityError("checkpoint replay differs from captured authority head")
    result = verify_projection(reader, identity, checkpoints) if reader is not None else verify_copy(ledger_path, identity, checkpoints)
    return result, latest


def verify_authority(ledger_path, authority, *, reader=None):
    # If another append commits while replay/copy inspection is in progress, a
    # newer local copy must not be called divergent from the older captured head.
    for _ in range(3):
        result, captured = _verify_authority_once(ledger_path, authority, reader)
        after = authority.head()
        verify_checkpoint(authority.identity, after)
        if canonical(after) == canonical(captured):
            result["authority_contacted"] = True
            return result
    raise AuthorityUnavailable("authority advanced during verification; retry with a stable head")


def verify_configured(ledger_path):
    from .factory import open_authority
    try:
        return verify_authority(ledger_path, open_authority(ledger_path))
    except (AuthorityError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "state": "unverifiable", "error": str(exc), "fresh": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path,
                        help="independently pinned authority identity, not a file supplied by the ledger owner")
    parser.add_argument("--checkpoints", required=True, type=Path, help="signed checkpoint JSONL from genesis")
    args = parser.parse_args(argv)
    try:
        result = verify_files(args.ledger, args.identity, args.checkpoints)
    except (AuthorityError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        result = {"ok": False, "state": "unverifiable", "error": str(exc), "fresh": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
