"""Durable principal accounting, separate from model dispatch and application wiring."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .billing import SCALE, Price, SpendDenied, SpendStopped, _money


class SpendJournal:
    def __init__(self, config: Mapping[str, Any], *, clock=time.time, _review: bool = False, _readonly: bool = False):
        self.path = Path(config["journal"]).absolute()
        self.principal = str(config["principal"])
        self.identity = str(uuid.UUID(config["journal_id"]))
        if not self.principal:
            raise ValueError("model spend needs a principal")
        self.daily, self.monthly = _money(config["daily_usd"], ceiling=False), _money(config["monthly_usd"], ceiling=False)
        self.prices = MappingProxyType({k: Price.parse(v) for k, v in config["routes"].items()})
        self.clock = clock
        with self._db(readonly=_readonly) as db:
            # A recorded stop must leave transcript/status access available after restart.
            # Admission and tool boundaries still call check(); opening is not resuming.
            self._identity(db, check_block=False)

    @staticmethod
    def provision(path: str | Path, principal: str) -> str:
        """Trusted operator only: exclusively create a new journal; returns policy UUID."""
        path = Path(path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        identity = str(uuid.uuid4())
        db = sqlite3.connect(path)
        try:
            with db:  # commits; the connection itself is closed below
                db.executescript('''
                    CREATE TABLE identity (id TEXT NOT NULL, principal TEXT NOT NULL, blocked TEXT NOT NULL,
                        observed REAL NOT NULL);
                    CREATE TABLE calls (id TEXT PRIMARY KEY, day TEXT NOT NULL, month TEXT NOT NULL,
                        route TEXT NOT NULL, session TEXT NOT NULL, reserved INTEGER NOT NULL,
                        actual INTEGER, pricing TEXT NOT NULL, completed_day TEXT, completed_month TEXT);
                ''')
                db.execute("INSERT INTO identity VALUES (?, ?, '', 0)", (identity, principal))
        finally:
            db.close()
        return identity

    @contextmanager
    def _db(self, *, readonly=False):
        db = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=10)
        try:
            if not readonly:
                db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _identity(self, db, *, check_block=True):
        rows = db.execute("SELECT id, principal, blocked FROM identity").fetchall()
        if len(rows) != 1 or rows[0][:2] != (self.identity, self.principal):
            raise SpendDenied("model spend journal identity mismatch")
        if check_block and rows[0][2]:
            raise SpendStopped(rows[0][2])

    def _observe(self, db):
        timestamp = self.clock()
        previous = db.execute("SELECT observed FROM identity").fetchone()[0]
        if not math.isfinite(timestamp) or timestamp < previous:
            raise SpendDenied("clock moved backwards; ask the principal to restore trustworthy UTC time")
        db.execute("UPDATE identity SET observed=?", (timestamp,))
        return datetime.fromtimestamp(timestamp, timezone.utc)

    def check(self):
        with self._db() as db:
            self._identity(db)

    def status(self) -> dict:
        """Inspect one consistent snapshot without updating the clock or journal."""
        with self._db(readonly=True) as db:
            self._identity(db, check_block=False)
            reason = db.execute("SELECT blocked FROM identity").fetchone()[0]
            pending, count = db.execute("SELECT COALESCE(SUM(reserved), 0), COUNT(*) FROM calls WHERE actual IS NULL").fetchone()
            now = datetime.fromtimestamp(self.clock(), timezone.utc)
            charged = {}
            for column, period in (("day", now.strftime("%Y-%m-%d")), ("month", now.strftime("%Y-%m"))):
                charged[column] = db.execute(f"SELECT COALESCE(SUM(actual), 0) FROM calls "
                    f"WHERE actual IS NOT NULL AND ({column}=? OR completed_{column}=?)", (period, period)).fetchone()[0]
        money = lambda n: format(Decimal(n) / SCALE, ".9f")
        return {"state": "stopped" if reason else ("offline" if not self.prices else "ready"),
                "principal": self.principal, "daily_cap_usd": money(self.daily), "monthly_cap_usd": money(self.monthly),
                "day_charged_usd": money(charged["day"]), "month_charged_usd": money(charged["month"]),
                "pending_reserved_usd": money(pending), "pending_count": count, "stop_reason": reason}

    @classmethod
    def inspect(cls, config: Mapping[str, Any]) -> dict:
        """Public read-only inspection, including stopped journals; never provisions or resumes."""
        return cls(config, _readonly=True).status()

    def reserve(self, route: str, session: str, maximum: int, price: Price) -> str:
        with self._db() as db:
            self._identity(db)
            now = self._observe(db)
            day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
            for column, period, limit in (("day", day, self.daily), ("month", month, self.monthly)):
                # Uncertain spend survives every rollover, even a call straddling UTC midnight.
                used = db.execute(f"SELECT COALESCE(SUM(CASE WHEN actual IS NULL THEN reserved ELSE actual END), 0) "
                                  f"FROM calls WHERE actual IS NULL OR {column}=? OR completed_{column}=?",
                                  (period, period)).fetchone()[0]
                if used + maximum > limit:
                    raise SpendDenied(f"{column} model-spend cap cannot cover this request; "
                                      "ask the principal to review the budget and pending reservations")
            token = str(uuid.uuid4())
            db.execute("INSERT INTO calls (id, day, month, route, session, reserved, actual, pricing) "
                       "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                       (token, day, month, route, session, maximum, json.dumps(vars(price))))
        return token

    def settle(self, token: str, actual: int):
        with self._db() as db:
            # A global stop denies new work, but admitted calls still need accounting.
            # Settlement validates identity and leaves the blocked reason untouched.
            self._identity(db, check_block=False)
            row = db.execute("SELECT reserved, actual FROM calls WHERE id=?", (token,)).fetchone()
            if row is None or row[1] is not None or actual < 0 or actual > row[0]:
                raise SpendDenied("invalid model spend settlement")
            # Charge both admission and completion periods when calls straddle a boundary.
            # Carry unresolved reservations forever; successful settlement is charged at completion.
            now = self._observe(db)
            day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
            db.execute("UPDATE calls SET actual=?, completed_day=?, completed_month=? WHERE id=?",
                       (actual, day, month, token))

    @classmethod
    def operator_review(cls, config: Mapping[str, Any], *, settlements: dict[str, Any] | None = None,
                        resume: bool = False):
        """Explicit trusted operator action with engines stopped; never exposed to model tools.

        Values are confirmed USD charges from billing evidence. Retains every original
        reservation and its pinned price, including charges beyond the old estimate.
        Resume requires all uncertain calls reconciled; it never changes configured caps.
        """
        journal = cls(config, _review=True)
        with journal._db() as db:
            journal._identity(db, check_block=False)
            now = journal._observe(db)
            for token, amount in (settlements or {}).items():
                row = db.execute("SELECT actual FROM calls WHERE id=?", (token,)).fetchone()
                if row is None or row[0] is not None:
                    raise SpendDenied("operator settlement must identify an unresolved reservation")
                db.execute("UPDATE calls SET actual=?, completed_day=?, completed_month=? WHERE id=?",
                           (_money(amount), now.strftime("%Y-%m-%d"), now.strftime("%Y-%m"), token))
            if resume:
                if db.execute("SELECT 1 FROM calls WHERE actual IS NULL LIMIT 1").fetchone():
                    raise SpendDenied("reconcile every uncertain reservation before resuming")
                db.execute("UPDATE identity SET blocked=''")

    def freeze(self, reason: str):
        with self._db() as db:
            self._identity(db, check_block=False)
            db.execute("UPDATE identity SET blocked=?", (reason,))

    def freeze_uncertain(self, reason: str) -> None:
        """Keep the durable reservation even if recording a stop fails."""
        try:
            self.freeze(reason)
        except Exception:
            pass  # admission still checks the existing durable reservation


def configured(config: Mapping[str, Any] | None, *, required: bool = False) -> SpendJournal | None:
    if config is None:
        if required:
            raise SpendDenied("required model-spend policy is missing; ask the principal to configure it")
        return None
    return SpendJournal(config)
