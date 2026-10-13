"""Choose the installed ledger backend before opening any private key."""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path

from . import NAME, AuthorityError, AuthorityUnavailable, validate_identity


def required() -> bool:
    return os.environ.get("EXO_LEDGER_AUTHORITY_REQUIRED") == "1"


def configured() -> bool:
    return bool(os.environ.get("EXO_LEDGER_AUTHORITY")) or required()


def open_authority(path, writer=None):
    """Resolve a pinned backend without opening or repairing the local projection.

    ``writer=None`` asks the backend for verification access using an authorized
    root-provisioned identity. It must never create a new writer or trust key.
    """
    name = os.environ.get("EXO_LEDGER_AUTHORITY", "")
    if not name or not NAME.fullmatch(name):
        raise AuthorityUnavailable("required installed ledger authority is not configured")
    try:
        matches = [ep for ep in importlib.metadata.entry_points(group="exo.ledger_authorities") if ep.name == name]
        if len(matches) != 1:
            raise AuthorityUnavailable("ledger authority must name exactly one installed backend")
        authority = matches[0].load()(Path(path), writer)
        validate_identity(authority.identity)
        if writer is not None and (authority.writer != writer or writer not in authority.identity["writers"]):
            raise AuthorityError("authority returned an unexpected writer identity")
        for method in (("head", "read_since") if writer is None else ("append", "lookup", "head", "read_since")):
            if not callable(getattr(authority, method, None)):
                raise AuthorityError("incomplete ledger authority backend")
        return authority
    except AuthorityError:
        raise
    except Exception as exc:
        raise AuthorityUnavailable("installed ledger authority is unavailable") from exc


def open_writable_ledger(path, author, key_path=None, *, writer=None, auto_keygen=False):
    from exo import crypto
    from exo.ledger import Ledger

    if configured():
        authority = open_authority(path, writer or author)
        if author not in authority.identity["writers"][authority.writer]["authors"]:
            raise AuthorityError("author attribution is not provisioned")
        try:
            ledger = Ledger(path, authority=authority, authority_author=author)
            try:
                ledger.sync_authority()  # repair/validate before any runtime reads its local copy
            except BaseException:
                ledger.close()
                raise
            return ledger
        except AuthorityError:
            raise
        except Exception as exc:
            raise AuthorityUnavailable("installed ledger authority is unavailable") from exc
    if key_path is None:
        raise AuthorityError("local writable ledger requires an explicit key path")
    key_path = Path(key_path)
    if auto_keygen and not key_path.exists():
        crypto.save_key(crypto.keygen(), key_path)
    return Ledger(path, crypto.Signer.from_file(author, key_path))
