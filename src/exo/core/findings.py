"""The findings store (D-025): what the harness learned about itself, with a lifecycle that re-measures fixes.

One row per signature in the ``findings`` table of sessions.sqlite (not a separate jsonl file: a dedup is one
upsert in one transaction, the server and the CLI can both write, and a copy of sessions.sqlite carries the
findings with the sessions they came from).

Lifecycle: open -> fixed (``fix``, with the commit) -> verified (quiet for ``verify_turns`` turns or
``verify_days`` days on a harness at or after that commit) or reopened (the same signature came back on such a
harness). A recurrence on an older harness only bumps the count and restarts the quiet clock.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from exo.core.trace import Flag, Thresholds, at_or_after, harness_version, scrub
from exo.protocol.schema import now_ts

SEVERITIES = ("blocker", "bug", "papercut", "idea")
SOURCES = ("anomaly", "agent", "principal")
STATUSES = ("open", "fixed", "verified", "reopened")
MAX_SESSIONS = 10


def _age_days(ts: str | None) -> float:
    try:
        then = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


class Findings:
    def __init__(self, store: Any, th: Thresholds | None = None, version: str | None = None) -> None:
        self.store = store
        self.th = th or Thresholds()
        self.version = version or harness_version()

    # ---------------------------------------------------------------- write

    def file(self, signature: str, title: str, *, source: str = "anomaly", severity: str = "papercut",
             detail: str = "", session: str | None = None, turn: str | None = None,
             model: str | None = None) -> dict[str, Any]:
        """Add a finding, or count a recurrence of the same signature. Returns the row."""
        if source not in SOURCES or severity not in SEVERITIES:
            raise ValueError(f"source is one of {SOURCES}, severity one of {SEVERITIES}")
        title, detail, now = scrub(title, 200), scrub(detail, 1500), now_ts()

        def write(db: Any) -> str:
            row = db.execute("SELECT * FROM findings WHERE signature=?", (signature,)).fetchone()
            if row is None:
                fid = "f_" + secrets.token_hex(4)
                db.execute("INSERT INTO findings (id, signature, source, severity, title, detail, session, turn, model,"
                           " version, first_seen, last_seen, sessions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (fid, signature, source, severity, title, detail, session, turn, model, self.version, now,
                            now, json.dumps([session] if session else [])))
                return fid
            seen = json.loads(row["sessions"])
            if session and session not in seen:
                seen = (seen + [session])[-MAX_SESSIONS:]
            status, sev = row["status"], row["severity"]
            sets = {"count": row["count"] + 1, "last_seen": now, "detail": detail or row["detail"],
                    "sessions": json.dumps(seen), "session": session or row["session"], "turn": turn or row["turn"],
                    "model": model or row["model"], "version": self.version,
                    "severity": min(sev, severity, key=SEVERITIES.index)}
            if status in ("fixed", "verified"):
                if at_or_after(self.version, row["fixed_in"]):
                    sets["status"] = "reopened"
                else:  # an older harness still shows it: the fix is not live there, restart the quiet clock
                    sets["fixed_turns"] = self._turns(db)
                    sets["fixed_at"] = now
            db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), row["id"]))
            return row["id"]

        fid = self.store._tx(write)
        return self.get(fid)  # type: ignore[return-value]

    def file_flag(self, f: Flag, source: str = "anomaly") -> dict[str, Any]:
        return self.file(f.signature, f.title, source=source, severity=f.severity, detail=f.detail,
                         session=f.session or None, turn=f.turn or None, model=f.model or None)

    def fix(self, fid: str, sha: str) -> dict[str, Any]:
        """Mark a finding fixed in commit ``sha``; the quiet clock starts now."""
        row = self.get(fid)
        if row is None:
            raise KeyError(fid)
        self.store._q("UPDATE findings SET status='fixed', fixed_in=?, fixed_at=?, fixed_turns=? WHERE id=?",
                      (sha.strip(), now_ts(), self._turns(), row["id"]))
        return self.get(row["id"])  # type: ignore[return-value]

    def sweep(self) -> list[str]:
        """Move fixed findings that stayed quiet long enough to verified. Returns their ids."""
        rows = self.store._q("SELECT * FROM findings WHERE status='fixed'")
        if not rows or not any(at_or_after(self.version, r["fixed_in"]) for r in rows):
            return []
        turns, done = self._turns(), []
        for r in rows:
            if not at_or_after(self.version, r["fixed_in"]):
                continue  # the fix is not running here, so quiet proves nothing
            if turns - (r["fixed_turns"] or 0) >= self.th.verify_turns or _age_days(r["fixed_at"]) >= self.th.verify_days:
                self.store._q("UPDATE findings SET status='verified' WHERE id=?", (r["id"],))
                done.append(r["id"])
        return done

    def verify_clean(self, seen: set[str]) -> list[str]:
        """Mark fixed findings verified when a run at/after the fix did not trip their signature.

        ``seen`` holds the signatures this run's anomaly rules tripped. A fixed finding whose
        signature stayed quiet is verified after one clean run; a signature that tripped again
        was already reopened by ``file``. Returns the verified ids."""
        done = []
        for r in self.store._q("SELECT * FROM findings WHERE status='fixed'"):
            if r["signature"] in seen:
                continue
            if not at_or_after(self.version, r["fixed_in"]):
                continue  # the fix is not running here, so a clean run proves nothing
            self.store._q("UPDATE findings SET status='verified' WHERE id=?", (r["id"],))
            done.append(r["id"])
        return done

    def _turns(self, db: Any = None) -> int:
        q = "SELECT COUNT(*) FROM events WHERE type='turn.completed'"
        return (db.execute(q).fetchone() if db is not None else self.store._q(q)[0])[0]

    # ---------------------------------------------------------------- read

    def get(self, fid: str) -> dict[str, Any] | None:
        rows = self.store._q("SELECT * FROM findings WHERE id=? OR id LIKE ?", (fid, fid + "%"))
        return self._row(rows[0]) if len(rows) == 1 else None

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        status = None if status == "all" else status
        rows = self.store._q("SELECT * FROM findings" + (" WHERE status=?" if status else "") +
                             " ORDER BY last_seen DESC", (status,) if status else ())
        return [self._row(r) for r in rows]

    def open_count(self) -> int:
        return self.store._q("SELECT COUNT(*) FROM findings WHERE status IN ('open','reopened')")[0][0]

    def counts(self) -> dict[str, int]:
        """Findings per state (every state present, zero included) and the total."""
        out = {s: 0 for s in STATUSES} | {r[0]: r[1] for r in self.store._q("SELECT status, COUNT(*) FROM findings GROUP BY status")}
        return {**out, "total": sum(out.values())}

    def summary(self) -> str:
        """``findings 7: 3 open, 2 fixed (awaiting re-verification), 1 verified, 1 reopened``"""
        c = self.counts()
        return (f"findings {c['total']}: {c['open']} open, {c['fixed']} fixed (awaiting re-verification), "
                f"{c['verified']} verified, {c['reopened']} reopened")

    def changes(self, viewer: str, save: bool = False) -> list[str]:
        """New findings and state changes since ``viewer`` last looked ([] on the first view); ``save`` sets the baseline."""
        rows = {r["id"]: r for r in self.store._q("SELECT id, status, title FROM findings")}
        snap = self.store._q("SELECT snapshot FROM finding_views WHERE viewer=?", (viewer,))
        out: list[str] = []
        if snap:
            before = json.loads(snap[0]["snapshot"])
            for fid, r in rows.items():
                old = before.get(fid)
                if old is None:
                    out.append(f"new: {fid} {r['title'][:60]} ({r['status']})")
                elif old != r["status"]:
                    out.append(f"{old} -> {r['status']}: {fid} {r['title'][:60]}")
        if save:
            self.store._q("INSERT OR REPLACE INTO finding_views (viewer, snapshot, at) VALUES (?,?,?)",
                          (viewer, json.dumps({k: v["status"] for k, v in rows.items()}), now_ts()))
        return out

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = {k: r[k] for k in r.keys()}
        d["sessions"] = json.loads(d["sessions"] or "[]")
        return d

    def export_md(self, status: str | None = None) -> str:
        """A digest for the developer session: grouped by severity, newest first."""
        rows = [r for r in self.list(status) if status or r["status"] in ("open", "reopened", "fixed")]
        out = [f"# Harness findings ({len(rows)})", ""]
        for sev in SEVERITIES:
            group = [r for r in rows if r["severity"] == sev]
            if not group:
                continue
            out += [f"## {sev} ({len(group)})", ""]
            for r in group:
                fix = f", fixed in {r['fixed_in']}" if r["fixed_in"] else ""
                out.append(f"- **{r['title']}** [{r['status']}{fix}] x{r['count']}, last {r['last_seen'][:16]}, "
                           f"from {r['source']}, harness {r['version'] or '?'}")
                out.append(f"  - id `{r['id']}`, signature `{r['signature']}`, sessions {', '.join(r['sessions']) or '-'}")
                if r["detail"]:
                    out.append(f"  - {r['detail'][:300]}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def text(self, status: str | None = None, viewer: str | None = None) -> str:
        """Summary line, then the rows (None or "all": every state; fixed ones show their commit). With ``viewer``:
        what changed since it last looked."""
        rows = self.list(status)
        head = [self.summary()]
        if viewer:
            moved = self.changes(viewer, save=True)
            head.append("since last view: " + ("; ".join(moved) if moved else "no changes"))
        rows_text = [f"{r['id']}  {r['status']:<8} {r['severity']:<8} x{r['count']:<3} {r['title'][:70]}  ({r['signature'][:40]})"
                     + (f"  fixed in {r['fixed_in']}" if r["fixed_in"] else "") for r in rows]
        return "\n".join(head + (rows_text or ["no findings"]))
