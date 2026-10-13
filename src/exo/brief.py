"""Morning brief (principle 16): result-first status, numbered decisions, a few questions.

Inputs, all inside the principal store:

    ledger.sqlite             agent activity in the window (action, artifact, note, verdict)
    queues/decisions.jsonl    decisions waiting on the principal (exo-command schema v1:
                              plugins/exo-command/schemas/decision-event.schema.json)
    queues/questions.jsonl    exo.ask queue; only questions the interruption budget allows

Format rules live in `policies/brief.yaml` (optional; defaults below), so another principal
can change them without touching code:

    title: Morning brief
    order: [status, blockers, decisions, questions, taste]
    headings: {status: Status, blockers: Blockers, decisions: Decisions, questions: Questions, taste: Taste}
    window_hours: 24              # ledger look-back for status
    group_by: [project, workstream]   # body keys naming the workstream; else the session; else "general"
    max_status: 8
    max_decisions: 5
    max_questions: 3              # also capped by the interruption budget
    min_voi: 0.05                 # questions worth less than this never reach the brief
    show_counts: true             # "(4 actions, 1 accepted, 1 correction)" after a status line
    decision_format: "{n}. {title}{links}. Default: {default}."
    question_format: "{n}. {text}{node}"
    mark_asked: false             # record brief questions as asked in the queue
    empty: "Nothing new in the window."

A status line leads with the latest result of the workstream. Workstreams with a failed or
blocked event go to Blockers first, which is how "result first, failures first" is kept.
"""
from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ask import QuestionQueue, iso, parse_ts, utcnow

DECISIONS_REL = Path("queues") / "decisions.jsonl"
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
BAD_STATUS = {"failed", "failure", "error", "blocked", "broken", "unverified"}
CORRECTIONS = {"edit", "reject", "should_have_been"}
DEFAULTS: dict = {
    "title": "Morning brief",
    "order": ["status", "blockers", "decisions", "questions", "taste"],
    "headings": {"status": "Status", "blockers": "Blockers", "decisions": "Decisions", "questions": "Questions",
                 "taste": "Taste"},
    "window_hours": 24,
    "group_by": ["project", "workstream"],
    "max_status": 8,
    "max_decisions": 5,
    "max_questions": 3,
    "min_voi": 0.05,
    "show_counts": True,
    "decision_format": "{n}. {title}{links}. Default: {default}.",
    "question_format": "{n}. {text}{node}",
    "mark_asked": False,
    "empty": "Nothing new in the window.",
}


def load_policy(root: str | os.PathLike | None) -> dict:
    raw: dict = {}
    if root:
        p = Path(root) / "policies" / "brief.yaml"
        if p.is_file():
            from .gate import parse_yaml
            raw = parse_yaml(p.read_text(encoding="utf-8")) or {}
    return merge_policy(raw)


def merge_policy(raw: Mapping | None) -> dict:
    pol = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in (raw or {}).items():
        pol[k] = {**pol[k], **v} if isinstance(pol.get(k), dict) and isinstance(v, Mapping) else v
    return pol


# --- decisions queue ----------------------------------------------------------------------------

def _epoch(v: Any) -> float | None:
    dt = parse_ts(v)
    return dt.timestamp() if dt else None


def read_decisions(path: str | os.PathLike, now: datetime | None = None) -> list[dict]:
    """Fold the decisions JSONL (v1) and return open, unsnoozed decisions in priority order:
    overdue, priority, irreversible first, deadline, age. Bad lines are skipped."""
    p = Path(path)
    if not p.is_file():
        return []
    now_e = (now or utcnow()).timestamp()
    items: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict) or not ev.get("id") or _epoch(ev.get("ts")) is None:
            continue
        did, op = ev["id"], ev.get("op")
        meta = {k: v for k, v in ev.items() if k not in ("v", "op", "id", "ts")}
        if op == "open" and ev.get("title"):
            items[did] = {"id": did, "status": "open", "opened_at": _epoch(ev["ts"]), **meta}
        elif did not in items:
            continue
        elif op == "update":
            meta.pop("status", None)
            items[did].update(meta)
        elif op == "snooze":
            items[did]["snoozed_until"] = _epoch(ev.get("until"))
        elif op in ("resolve", "withdraw"):
            items[did]["status"] = "resolved" if op == "resolve" else "withdrawn"
    out = []
    for d in items.values():
        if d["status"] != "open" or (d.get("snoozed_until") or 0) > now_e:
            continue
        d["deadline_at"] = _epoch(d.get("deadline"))
        d["overdue"] = bool(d["deadline_at"] and d["deadline_at"] < now_e)
        out.append(d)
    out.sort(key=lambda d: (not d["overdue"], PRIORITY_RANK.get(d.get("priority"), 1),
                            d.get("reversible") is not False, d["deadline_at"] or float("inf"),
                            d.get("opened_at") or 0))
    return out


def option_label(d: Mapping, key: str | None) -> str | None:
    if key is None:
        return None
    for i, o in enumerate(d.get("options") or [], 1):
        if isinstance(o, Mapping) and str(o.get("key")) == str(key):
            return str(o.get("label") or key)
        if isinstance(o, str) and (str(i) == str(key) or o == key):
            return o
    return str(key)


# --- status from the ledger ---------------------------------------------------------------------

@dataclass
class Workstream:
    name: str
    result: str = ""
    result_ts: str = ""
    result_rank: int = 0
    blocked: str | None = None
    actions: int = 0
    artifacts: int = 0
    accepted: int = 0
    corrections: int = 0
    links: list[str] = field(default_factory=list)
    last_ts: str = ""


def _text(b: Mapping) -> str:
    for k in ("result", "summary", "title", "text", "message", "description"):
        v = b.get(k)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())
    return ""


def _ws_name(ev: Mapping, keys: list[str]) -> str:
    b = ev.get("body") or {}
    for k in keys:
        if b.get(k):
            return str(b[k])
    return str(ev.get("session") or "general")


def workstreams(ledger, since: str, group_by: list[str]) -> list[Workstream]:
    ws: dict[str, Workstream] = {}
    names: dict[str, str] = {}
    if ledger is None:
        return []
    for ev in ledger.iter(kind=("action", "artifact", "note", "verdict"), since=since):
        b = ev.get("body") or {}
        if ev["kind"] == "verdict":
            tgt = b.get("target")
            name = names.get(tgt)
            if name is None:
                t = ledger.get(tgt) if tgt else None
                name = _ws_name(t, group_by) if t else _ws_name(ev, group_by)
            w = ws.setdefault(name, Workstream(name))
            if b.get("verdict") == "accept":
                w.accepted += 1
            elif b.get("verdict") in CORRECTIONS:
                w.corrections += 1
            w.last_ts = max(w.last_ts, ev["ts"])
            continue
        name = _ws_name(ev, group_by)
        names[ev["id"]] = name
        w = ws.setdefault(name, Workstream(name))
        if ev["kind"] == "action":
            w.actions += 1
        elif ev["kind"] == "artifact":
            w.artifacts += 1
        w.last_ts = max(w.last_ts, ev["ts"])
        txt = _text(b)
        status = str(b.get("status") or "").lower()
        if status in BAD_STATUS:
            w.blocked = txt or status
        elif status in ("ok", "done", "success", "passed", "resolved") and w.blocked:
            w.blocked = None
        # A result beats activity: artifacts and bodies with an explicit `result` rank above
        # plain actions and notes. Within a rank the newest wins.
        rank = 2 if (ev["kind"] == "artifact" or b.get("result")) else 1
        if txt and (rank > w.result_rank or (rank == w.result_rank and ev["ts"] >= w.result_ts)):
            w.result, w.result_ts, w.result_rank = txt, ev["ts"], rank
        for k in ("url", "path", "link"):
            if isinstance(b.get(k), str) and b[k] not in w.links:
                w.links.append(b[k])
        for l in b.get("links") or []:
            if isinstance(l, str) and l not in w.links:
                w.links.append(l)
    return sorted(ws.values(), key=lambda w: (w.blocked is None, -(w.actions + w.artifacts + w.accepted), w.name))


# --- the brief ----------------------------------------------------------------------------------

@dataclass
class Brief:
    title: str
    generated: str
    policy: dict
    status: list[Workstream]
    blockers: list[Workstream]
    decisions: list[dict]
    questions: list[dict]
    budget: dict
    taste: list[str] = field(default_factory=list)   # D-044: swaps, step backs, adoptions, proposals

    def order(self) -> list[str]:
        """The policy's sections; taste lines are shown even under an older order that does not name them."""
        order = list(self.policy.get("order") or [])
        return order + (["taste"] if "taste" not in order and self.taste else [])

    def _link(self, s: str, md: bool) -> str:
        if md:
            return f"[link]({s})" if re.match(r"^[a-z]+://", s) else f"`{s}`"
        e = html.escape(s)
        return f'<a href="{e}">link</a>' if re.match(r"^[a-z]+://", s) else f"<code>{e}</code>"

    def _counts(self, w: Workstream) -> str:
        if not self.policy.get("show_counts"):
            return ""
        parts = []
        for n, word in ((w.actions, "action"), (w.artifacts, "artifact"), (w.accepted, "accepted"),
                        (w.corrections, "correction")):
            if n:
                parts.append(f"{n} {word}" + ("s" if n > 1 and word in ("action", "artifact", "correction") else ""))
        return f" ({', '.join(parts)})" if parts else ""

    def lines(self, md: bool = True) -> dict[str, list[str]]:
        esc = (lambda s: s) if md else html.escape
        out: dict[str, list[str]] = {}
        out["status"] = [f"{'**' if md else '<b>'}{esc(w.name)}{'**' if md else '</b>'}: "
                         f"{esc(w.result or 'activity, no result line')}.{esc(self._counts(w))}"
                         for w in self.status]
        out["blockers"] = [f"{'**' if md else '<b>'}{esc(w.name)}{'**' if md else '</b>'}: {esc(w.blocked or '')}"
                           + (" " + " ".join(self._link(l, md) for l in w.links[:2]) if w.links else "")
                           for w in self.blockers]
        fmt = str(self.policy.get("decision_format"))
        dl = []
        for i, d in enumerate(self.decisions, 1):
            links = d.get("links") or []
            link_s = (" " + " ".join(self._link(l, md) for l in links)) if links else ""
            default = option_label(d, d.get("default")) or "none, waits for you"
            line = fmt.format(n=i, title=esc(str(d.get("title", "")).strip().rstrip(".")),
                              links=link_s, default=esc(default), id=esc(d["id"]),
                              priority=d.get("priority", "normal"),
                              overdue=" (overdue)" if d.get("overdue") else "")
            dl.append(line.replace("?.", "?"))
        out["decisions"] = dl
        qfmt = str(self.policy.get("question_format"))
        out["questions"] = [qfmt.format(n=i, text=esc(q["text"]), id=esc(q["id"]),
                                        node=(f" (`{q['node']}`)" if md else f" (<code>{esc(q['node'])}</code>)")
                                        if q.get("node") else "",
                                        default=esc(q.get("default") or ""))
                            for i, q in enumerate(self.questions, 1)]
        out["taste"] = [esc(x) for x in self.taste]
        return out

    def to_markdown(self) -> str:
        heads = self.policy.get("headings") or {}
        ls = self.lines(md=True)
        parts = [f"# {self.title}"]
        for sec in self.order():
            items = ls.get(sec) or []
            if not items:
                if sec == "status":
                    parts.append(f"\n**{heads.get(sec, sec.title())}**\n{self.policy.get('empty')}")
                continue
            bullet = "" if sec in ("decisions", "questions") else "- "
            parts.append(f"\n**{heads.get(sec, sec.title())}**\n" + "\n".join(bullet + x for x in items))
        return "\n".join(parts).strip() + "\n"

    def to_html(self) -> str:
        heads = self.policy.get("headings") or {}
        ls = self.lines(md=False)
        body = []
        for sec in self.order():
            items = ls.get(sec) or []
            if not items and sec != "status":
                continue
            h = html.escape(str(heads.get(sec, sec.title())))
            if not items:
                body.append(f'<h2>{h}</h2><p class="empty">{html.escape(str(self.policy.get("empty")))}</p>')
                continue
            if sec in ("decisions", "questions"):
                lis = "".join(f"<li>{re.sub(r'^[0-9]+[.)] ?', '', x)}</li>" for x in items)
                body.append(f"<h2>{h}</h2><ol>{lis}</ol>")
            else:
                body.append(f"<h2>{h}</h2><ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
        t = html.escape(self.title)
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t}</title>'
                f"<style>{CSS}</style></head><body><main><h1>{t}</h1>"
                f'<div class="meta">{html.escape(self.generated)}</div>{"".join(body)}</main></body></html>')

    def to_dict(self) -> dict:
        return {"title": self.title, "generated": self.generated, "markdown": self.to_markdown(),
                "decisions": [d["id"] for d in self.decisions], "questions": [q["id"] for q in self.questions],
                "budget": self.budget}


CSS = """
:root{color-scheme:dark}
body{margin:0;background:#0d1117;color:#e6edf3;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
main{max-width:760px;margin:0 auto;padding:40px 24px 64px}
h1{font-size:22px;margin:0 0 4px;font-weight:600}
h2{font-size:14px;margin:30px 0 8px;color:#9da7b3;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.meta,.empty{color:#8b949e;font-size:13px}
ul,ol{padding-left:22px;margin:0}li{margin:6px 0}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
code{background:#161b22;border:1px solid #2a313c;border-radius:5px;padding:1px 5px;font-size:13px}
b{font-weight:600}
"""


def build_brief(root: str | os.PathLike, *, ledger=None, now: datetime | None = None,
                policy: Mapping | None = None, queue: QuestionQueue | None = None,
                author: str | None = None) -> Brief:
    root = Path(root)
    now = now or utcnow()
    pol = merge_policy(policy) if policy is not None else load_policy(root)
    if ledger is None and (root / "ledger.sqlite").exists():
        from .ledger import Ledger
        ledger = Ledger(root / "ledger.sqlite")
    since = iso(now - timedelta(hours=float(pol.get("window_hours", 24))))
    ws = workstreams(ledger, since, list(pol.get("group_by") or []))
    blockers = [w for w in ws if w.blocked]
    status = [w for w in ws if not w.blocked][: int(pol.get("max_status", 8))]
    decisions = read_decisions(root / DECISIONS_REL, now)[: int(pol.get("max_decisions", 5))]
    qq = queue or QuestionQueue(root, ledger=ledger)
    budget = qq.budget(now)
    min_voi = float(pol.get("min_voi") or 0)
    room = min(int(pol.get("max_questions", 3)), budget["remaining"])
    qs = [q for q in qq.open(now) if q.status == "proposed" and q.voi >= min_voi][:max(0, room)]
    if qs and pol.get("mark_asked"):
        qq.mark_asked([q.id for q in qs], "brief", author=author, now=now)
    return Brief(title=str(pol.get("title")), generated=now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                 policy=pol, status=status, blockers=blockers, decisions=decisions,
                 questions=[q.to_dict() for q in qs], budget=budget, taste=_taste_lines(root, since))


def _taste_lines(root: Path, since: str) -> list[str]:
    """What the taste rule did in the window (D-044): never fails the brief."""
    if not (root / "ledger.sqlite").exists():
        return []
    try:
        from .baseline.adapt import brief_lines
        from .store import PrincipalStore
        return brief_lines(PrincipalStore(root), since)
    except Exception:
        return []


def write_brief(root: str | os.PathLike, brief: Brief, day: date | None = None) -> dict[str, Path]:
    day = day or datetime.now(timezone.utc).date()
    out = Path(root) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    md, hp = out / f"brief-{day.isoformat()}.md", out / f"brief-{day.isoformat()}.html"
    md.write_text(brief.to_markdown(), encoding="utf-8")
    hp.write_text(brief.to_html(), encoding="utf-8")
    return {"markdown": md, "html": hp}


# --- CLI hook -----------------------------------------------------------------------------------
# `exo.cli` lists `brief` in its LAZY table. It imports this module, calls `register(sp, common)` and runs
# `cmd_brief` only when `exo brief` runs.

def register(sp, common) -> None:
    p = sp.add_parser("brief", parents=[common], help="morning brief (Markdown and HTML)")
    p.add_argument("--html", action="store_true", help="print HTML instead of Markdown")
    p.add_argument("--write", action="store_true", help="also write <store>/reports/brief-YYYY-MM-DD.{md,html}")


def cmd_brief(a, store) -> int:
    b = build_brief(store.root)
    if a.write:
        paths = write_brief(store.root, b)
        print(f"wrote {paths['markdown']} and {paths['html']}")
    print(b.to_html() if a.html else b.to_markdown(), end="")
    return 0
