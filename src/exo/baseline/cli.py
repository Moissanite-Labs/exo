"""`exo taste` (D-044 wave 1).

    exo taste [list] [--root DIR | --principal ID] [--json]
        the house layer's state (on, or off and why) and every active taste item with its weight and source
    exo taste export-house --store DIR --out DIR --version 1.3.0 [--check-only] [--soul FILE] [--decision]
        build the house pack from the principal's store; writes nothing if any privacy check fails
    exo taste review OUT [--tick ID ...] [--final]
        the principal's tick on each value and contested item, then the release

The `/taste` view with mode, pending shifts and step backs comes in wave 3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def register(sp, common) -> None:
    p = sp.add_parser('taste', parents=[common], help='the taste layers: house, learned, pins (D-044)')
    p.add_argument('action', nargs='?', default='list', choices=['list', 'export-house', 'review'])
    p.add_argument('target', nargs='?', help='review: the export folder')
    p.add_argument('--json', action='store_true', help='list: machine-readable output')
    p.add_argument('--store', help='export-house: the source (principal) store')
    p.add_argument('--out', help='export-house: the output folder')
    p.add_argument('--version', dest='pack_version', help='export-house: the pack version, as 1.3.0')
    p.add_argument('--check-only', action='store_true', help='export-house: run every check, write nothing')
    p.add_argument('--soul', help='export-house: the house soul file (default: the built-in voice)')
    p.add_argument('--decision', action='store_true',
                   help='export-house: also ask the decision model (it can only hold an item)')
    p.add_argument('--tick', nargs='+', default=[], metavar='ID', help='review: tick these items')
    p.add_argument('--final', action='store_true', help='review: write RELEASE.json once all are ticked')


def cmd_taste(a) -> int:
    if a.action == 'export-house':
        return _export(a)
    if a.action == 'review':
        return _review(a)
    from ..cli import resolve_store
    from .layers import render, view
    v = view(resolve_store(a))
    if a.json:
        print(json.dumps({'house': {'on': v.house_on, 'reason': v.reason, 'version': v.pack.version},
                          'items': [e.__dict__ for e in v.entries]}, indent=2))
    else:
        print(render(v))
    return 0


def _export(a) -> int:
    from ..store import PrincipalStore
    from .export import ExportError, decision_judge, export_house
    if not (a.store and a.out and a.pack_version):
        sys.exit('exo taste export-house needs --store, --out and --version')
    soul = Path(a.soul).read_text(encoding='utf-8') if a.soul else None
    try:
        res = export_house(PrincipalStore(a.store), a.out, a.pack_version, check_only=a.check_only, soul=soul,
                           judge=decision_judge() if a.decision else None)
    except ExportError as e:
        sys.exit(f'exo taste export-house: {e}')
    print(res.render())
    if res.ok and res.written:
        print(f'next: read {Path(a.out) / "review" / "REVIEW.md"}, tick each value item, then '
              f'exo taste review {a.out} --final')
    return 0 if res.ok else 1


def _review(a) -> int:
    from .review import ReviewError, finalise, status, tick
    if not a.target:
        sys.exit('exo taste review needs the export folder')
    try:
        if a.tick:
            print(f'ticked {len(tick(a.target, a.tick))}')
        if a.final:
            r = finalise(a.target)
            print(f'released {r["version"]}: {len(r["ticked"])} ticked item(s); publishing still needs approval')
            return 0
        done, waiting = status(a.target)
    except ReviewError as e:
        print(f'exo taste review: {e}', file=sys.stderr)
        return 1
    print(f'{len(done)} ticked, {len(waiting)} waiting')
    for i in waiting:
        print(f'  [ ] {i.id} ({i.cls}{", contested" if i.contested else ""}) {i.claim}')
    return 0
