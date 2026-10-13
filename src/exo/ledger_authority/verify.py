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
from . import MAX_DOCUMENT, AuthorityError, validate_identity
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
