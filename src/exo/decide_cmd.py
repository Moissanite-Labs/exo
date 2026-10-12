"""`exo decide`: ask the decision model one question (D-015), for quick experiments.

    exo decide "Is this safe to run?" --options yes,no,unsure [--state "rm -rf build/"]

Prints the probability of each option and the route that answered. Routes through the `decision`
role: local Laya, then any fallback (a chat model answers with probability 1.0 for its pick).
"""
from __future__ import annotations

import asyncio
import sys

from .core.models.decision import DecisionError, ask_choice_result
from .core.models.router import Router


def register(sp, common) -> None:
    p = sp.add_parser('decide', help='ask the decision model one question; prints probabilities and the route')
    p.add_argument('question')
    p.add_argument('--options', required=True, help='comma-separated options, at least two')
    p.add_argument('--state', default='', help='the context the question is about')
    p.add_argument('--role', default='decision', help='router role to use (default: decision)')


def render(res) -> str:
    rows = sorted(res.probabilities.items(), key=lambda kv: -kv[1])
    lines = [f'  {opt:<20} {p:.3f}' + ('  <-' if opt == res.choice else '') for opt, p in rows]
    conf = f' confidence={res.confidence:.2f}' if res.confidence is not None else ''
    lines.append(f'route: {res.route} source={res.source}{conf}')
    return '\n'.join(lines)


def cmd_decide(a, router: Router | None = None) -> int:
    options = [o.strip() for o in a.options.split(',') if o.strip()]
    if len(options) < 2:
        print('exo decide: --options needs at least two comma-separated options', file=sys.stderr)
        return 2
    if router is None:
        from .models_cmd import build_router
        router = build_router()
    try:
        res = asyncio.run(ask_choice_result(router, a.state, a.question, options, role=a.role))
    except (DecisionError, ValueError) as e:
        print(f'exo decide: {e}', file=sys.stderr)
        return 1
    print(render(res))
    return 0
