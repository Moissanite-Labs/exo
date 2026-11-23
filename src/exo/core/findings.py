"""The findings store (D-025): what the harness learned about itself, with a lifecycle that re-measures fixes.

One row per signature in the ``findings`` table of sessions.sqlite (not a separate jsonl file: a dedup is one
upsert in one transaction, the server and the CLI can both write, and a copy of sessions.sqlite carries the
findings with the sessions they came from).

Lifecycle: open -> fixed (``fix``, with the commit) -> verified (quiet for ``verify_turns`` eligible turns or on
``verify_days`` days of activity, on a harness at or after that commit; ``trace.eligible`` says which turns count) or
reopened (the same signature came back on such a harness). A recurrence on an older harness only bumps the count and
restarts the quiet clock.
"""

from __future__ import annotations

import json
import math
import secrets
from typing import Any

from exo.core.trace import Flag, Thresholds, TurnTrace, at_or_after, eligible, harness_version, scrub
from exo.protocol.schema import now_ts

SEVERITIES = ("blocker", "bug", "papercut", "idea")
SOURCES = ("anomaly", "agent", "principal")
STATUSES = ("open", "fixed", "verified", "reopened")
MAX_SESSIONS = 10


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
                    sets["quiet"], sets["fixed_at"] = None, now
            db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), row["id"]))
            return row["id"]

        fid = self.store._tx(write)
        return self.get(fid)  # type: ignore[return-value]

    def file_flag(self, f: Flag, source: str = "anomaly") -> dict[str, Any]:
        return self.file(f.signature, f.title, source=source, severity=f.severity, detail=f.detail,
                         session=f.session or None, turn=f.turn or None, model=f.model or None)

    def fix(self, fid: str, sha: str) -> dict[str, Any]:
        """Mark a finding fixed in commit ``sha``; the quiet clock starts now, from zero."""
        row = self.get(fid)
        if row is None:
            raise KeyError(fid)
        self.store._q("UPDATE findings SET status='fixed', fixed_in=?, fixed_at=?, quiet=NULL WHERE id=?",
                      (sha.strip(), now_ts(), row["id"]))
        return self.get(row["id"])  # type: ignore[return-value]

    def observe(self, t: TurnTrace, tripped: set[str]) -> list[str]:
        """Count a finished turn toward verifying fixed findings (D-025); ``tripped``: the signatures it flagged.

        Verified after ``verify_turns`` eligible turns, or on ``verify_days`` distinct days with a counting turn, all
        quiet and on a harness at or after the fix. A turn that could not have tripped the signature counts for
        neither; one where no rule can tell (``eligible`` is None) counts only as a day. Returns the verified ids."""
        day, need, done = now_ts()[:10], max(1, math.ceil(self.th.verify_days)), []
        for r in self.store._q("SELECT * FROM findings WHERE status='fixed'"):
            ok = eligible(r["signature"], t)
            if ok is False or r["signature"] in tripped or not at_or_after(self.version, r["fixed_in"]):
                continue  # unrelated turn, it came back (``file`` reopened it), or the fix is not running here
            q = json.loads(r["quiet"] or "{}")
            n, days = q.get("turns", 0) + bool(ok), sorted({*q.get("days", []), day})[-need:]
            yes = n >= self.th.verify_turns or len(days) >= need
            self.store._q("UPDATE findings SET quiet=?, status=? WHERE id=?",
                          (json.dumps({"turns": n, "days": days}), "verified" if yes else "fixed", r["id"]))
            done += [r["id"]] if yes else []
        return done

    # ---------------------------------------------------------------- read

    def get(self, fid: str) -> dict[str, Any] | None:
        """The finding with this id, or the one whose id starts with it. ``%`` and ``_`` match only themselves, and an
        empty id names none (exo-desktop #52)."""
        if not fid:
            return None
        prefix = fid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.store._q("SELECT * FROM findings WHERE id=? OR id LIKE ? ESCAPE '\\'", (fid, prefix))
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

    def backlog(self, limit: int = 20, *, now: str | None = None) -> dict[str, Any]:
        """One consistent read of counts and oldest unresolved rows; output size is bounded."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
            raise ValueError("backlog limit must be an integer from 0 to 100")
        at = now or now_ts()

        def read(db: Any) -> dict[str, Any]:
            if db.execute("SELECT julianday(?)", (at,)).fetchone()[0] is None:
                raise ValueError("backlog time must be a valid timestamp")
            counts = {status: 0 for status in STATUSES}
            severity = {name: 0 for name in SEVERITIES}
            pending = 0
            for status, sev, count in db.execute("SELECT status,severity,COUNT(*) FROM findings GROUP BY status,severity"):
                counts[status] = counts.get(status, 0) + count
                if status in ("open", "reopened", "fixed"):
                    pending += count
                    severity[sev] = severity.get(sev, 0) + count
            ages = {name: 0 for name in ("under_1_day", "1_to_7_days", "7_to_30_days", "30_days_or_more", "unknown")}
            for bucket, count in db.execute(
                "SELECT CASE WHEN julianday(first_seen) IS NULL THEN 'unknown' "
                "WHEN julianday(?) - julianday(first_seen) < 1 THEN 'under_1_day' "
                "WHEN julianday(?) - julianday(first_seen) < 7 THEN '1_to_7_days' "
                "WHEN julianday(?) - julianday(first_seen) < 30 THEN '7_to_30_days' "
                "ELSE '30_days_or_more' END AS age, COUNT(*) FROM findings "
                "WHERE status IN ('open','reopened','fixed') GROUP BY age", (at, at, at)):
                ages[bucket] = count
            rows = [dict(row) for row in db.execute(
                "SELECT id,status,severity,title,first_seen,last_seen,fixed_in, "
                "CASE WHEN julianday(first_seen) IS NULL THEN NULL "
                "ELSE MAX(0, julianday(?) - julianday(first_seen)) END AS age_days FROM findings "
                "WHERE status IN ('open','reopened','fixed') "
                "ORDER BY julianday(first_seen) IS NULL, julianday(first_seen), id LIMIT ?", (at, limit))]
            return {"at": at, "counts": {**counts, "total": sum(counts.values())}, "pending": pending,
                    "by_severity": severity, "by_age": ages, "oldest": rows, "shown": len(rows)}

        return self.store._read(read)

    def backlog_text(self, limit: int = 20) -> str:
        report = self.backlog(limit)
        lines = [f"findings backlog: {report['pending']} unresolved, showing {report['shown']} oldest",
                 "severity: " + ", ".join(f"{key} {value}" for key, value in report["by_severity"].items()),
                 "age since first seen: " + ", ".join(f"{key} {value}" for key, value in report["by_age"].items())]
        for row in report["oldest"]:
            age = "unknown age" if row["age_days"] is None else f"{row['age_days']:.1f}d"
            lines.append(f"{row['id']}  {row['status']:<8} {row['severity']:<8} {age:>11}  {row['title']}")
        return "\n".join(lines)

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
