"""`exo trace` and `exo findings` (D-025): read how the harness performed, and the findings it kept.

    exo trace [SESSION] [--last N] [--json]       per-turn table and anomaly flags (default: newest session)
    exo findings [--status open|fixed|verified|reopened|all] [--json]   the findings store, counts per state on top
    exo findings fix ID --in SHA                  mark fixed; later turns re-measure it
    exo findings add TITLE [--severity bug|papercut] [--signature S] [--detail TEXT]  file one by hand
    exo findings export --md                      a digest for the developer session
    exo findings backlog [--limit 20] [--json]     unresolved counts by severity and age, oldest rows
    exo findings triage ID --owner SEAT          assign a data owner
    exo findings close ID --reason TEXT          record a closed disposition
    exo findings history ID | retests [--session ID] | ack-retest REQUEST
"""
from __future__ import annotations

import json
import re
import sys

from .store import exo_home


def _store():
    from .core.sessions import SessionStore

    path = exo_home() / 'sessions.sqlite'
    if not path.exists():
        sys.exit(f'exo: no sessions at {path}')
    return SessionStore(path)


def _thresholds(seat: str | None = None):
    """config.json's [trace], with the session's seat.toml [trace] over it (D-032)."""
    from .config import Config
    from .core.trace import Thresholds

    cfg = dict(Config.load().data.get('trace') or {})
    if seat:
        from .core import seats

        try:
            cfg.update(seats.load(exo_home(), seat).trace)
        except seats.SeatError:
            pass
    return Thresholds.of(cfg)


def register(sp, common) -> None:
    p = sp.add_parser('trace', help='how the harness performed in a session: per turn timing, routes, flags')
    p.add_argument('session', nargs='?', help='session id (default: the newest)')
    p.add_argument('--last', type=int, metavar='N', help='only the last N turns')
    p.add_argument('--calls', action='store_true', help='list every model call with its cost (default: last turn)')
    p.add_argument('--json', action='store_true')
    p = sp.add_parser('findings', help='what the harness found wrong with itself')
    p.add_argument('action', nargs='?', default='list', choices=['list', 'fix', 'export', 'add', 'backlog', 'triage', 'close', 'history', 'retests', 'ack-retest'])
    p.add_argument('id', nargs='?', help='finding id (for fix) or title (for add)')
    p.add_argument('--status', choices=['open', 'triaged', 'fixed', 'verified', 'closed', 'reopened', 'all'],
                   help='default: all states (verified ones too)')
    p.add_argument('--in', dest='sha', metavar='SHA', help='commit the fix landed in (for fix)')
    p.add_argument('--md', action='store_true', help='markdown (for export)')
    p.add_argument('--severity', choices=['bug', 'papercut'], default='papercut', help='for add')
    p.add_argument('--signature', help='for add: dedup key (default: made from the title)')
    p.add_argument('--detail', default='', help='for add: what happened, how to see it')
    p.add_argument('--limit', type=int, default=20, help='for backlog: oldest unresolved rows, 0..100 (default 20)')
    p.add_argument('--owner', help='for triage: person or seat identifier, not an authority grant')
    p.add_argument('--reason', help='for close: a recorded disposition reason')
    p.add_argument('--session', help='for retests: original filing session')
    p.add_argument('--json', action='store_true')


def cmd_trace(a) -> int:
    from .core import trace

    store = _store()
    sid = a.session
    if not sid:
        rows = store.list_sessions(1)
        if not rows:
            sys.exit('exo: no sessions')
        sid = rows[0].id
    elif store.get_session(sid) is None:
        sys.exit(f'exo: no session {sid}')
    info = store.get_session(sid)
    turns = trace.build(store, sid, a.last, _thresholds(info.seat if info else None))
    from .core.findings import Findings

    fnd = Findings(store)
    if a.json:
        print(json.dumps({**trace.as_dict(sid, turns), 'findings': fnd.counts()}, indent=2))
    else:
        print(fnd.summary())
        print(trace.table(turns, f'session {sid} ({info.seat or "no seat"}, {len(turns)} turns shown)', a.calls))
        if peers := trace.peer_text(store, sid):
            print("\n" + peers)
    return 0


def cmd_findings(a) -> int:
    from .core.findings import Findings

    fnd = Findings(_store(), _thresholds())
    if a.action in ('triage', 'close', 'history', 'retests', 'ack-retest'):
        try:
            if a.action != 'retests' and not a.id:
                raise ValueError('this findings action requires an id')
            if a.action == 'triage':
                data = fnd.triage(a.id, a.owner)
            elif a.action == 'close':
                data = fnd.close(a.id, a.reason)
            elif a.action == 'history':
                data = fnd.history(a.id, a.limit)
            elif a.action == 'retests':
                data = fnd.retests(a.session, a.limit)
            else:
                data = fnd.ack_retest(a.id)
        except KeyError:
            sys.exit(f'exo: no finding or retest request {a.id}')
        except ValueError as exc:
            sys.exit(str(exc))
        if a.json:
            print(json.dumps(data, indent=2))
        elif a.action in ('triage', 'close'):
            print(f"{data['id']} {data['status']}" + (f" owner {data['owner']}" if data['owner'] else ''))
        elif a.action == 'ack-retest':
            print(f"{data['id']} acknowledged (verification remains separate)")
        elif a.action == 'history':
            print('\n'.join(f"{r['at']} {r['before_state'] or 'new'} -> {r['after_state']} owner {r['owner'] or '-'}" for r in data) or 'no transitions')
        else:
            print('\n'.join(f"{r['id']} {r['finding']} fix {r['fix_marker']} session {r['target_session'] or 'unrouted'}" for r in data) or 'no pending retest requests')
        return 0
    if a.action == 'backlog':
        if a.status:
            sys.exit('exo findings backlog includes open, triaged, reopened and fixed; --status applies to list/export')
        try:
            print(json.dumps(fnd.backlog(a.limit), indent=2) if a.json else fnd.backlog_text(a.limit))
        except ValueError as exc:
            sys.exit(str(exc))
        return 0
    if a.action == 'fix':
        if not a.id or not a.sha:
            sys.exit('usage: exo findings fix ID --in SHA')
        try:
            row = fnd.fix(a.id, a.sha)
        except KeyError:
            sys.exit(f'exo: no finding {a.id}')
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"{row['id']} fixed in {row['fixed_in']}")
        return 0
    if a.action == 'add':
        if not a.id:
            sys.exit('usage: exo findings add TITLE [--severity bug|papercut] [--signature S] [--detail TEXT]')
        sig = a.signature or 'manual:' + re.sub(r'[^a-z0-9]+', '-', a.id.lower()).strip('-')[:60]
        row = fnd.file(sig, a.id, source='principal', severity=a.severity, detail=a.detail)
        print(f"{row['id']} {row['status']} x{row['count']} {row['severity']}  {row['title']}")
        return 0
    if a.action == 'export':
        print(fnd.export_md(a.status), end='')
    elif a.json:
        print(json.dumps(fnd.list(a.status), indent=2))
    else:
        print(fnd.text(a.status, viewer='cli'))
    return 0
