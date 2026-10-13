"""The tool-round cap: how many tool rounds one turn may run before it wraps up (D-064).

Sources, first match wins: ``/rounds N`` for one session (principal only), ``EXO_MAX_TOOL_ROUNDS``, the seat's
``[loop] max_tool_rounds``, ``config.json`` ``loop.max_tool_rounds``, then the Loop default (64).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Mapping

from exo.principal import require_principal
from exo.server.engine import CommandFailed

if TYPE_CHECKING:
    from exo.core.engine import CoreEngine

LIMIT = 1000  # a typo like 64000 should not let one turn run unbounded


def parse(value: Any) -> int | None:
    """A whole number from 1 to LIMIT, else None."""
    if isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= LIMIT else None


def configured(env: Mapping[str, str], seat_loop: Mapping[str, Any] | None,
               config_loop: Any) -> int | None:
    """The configured cap, or None for the Loop default. Bad values are named on stderr and skipped."""
    sources = (("EXO_MAX_TOOL_ROUNDS", env.get("EXO_MAX_TOOL_ROUNDS")),
               ("seat [loop] max_tool_rounds", (seat_loop or {}).get("max_tool_rounds")),
               ("config loop.max_tool_rounds",
                config_loop.get("max_tool_rounds") if isinstance(config_loop, dict) else None))
    for where, raw in sources:
        if raw is None or raw == "":
            continue
        n = parse(raw)
        if n is not None:
            return n
        print(f"exo: ignoring {where}={raw!r}: use a whole number from 1 to {LIMIT}", file=sys.stderr)
    return None


def command(eng: CoreEngine, session: str | None, args: str) -> str:
    """``/rounds`` shows the cap; ``/rounds N`` sets it for this session; ``/rounds default`` clears that."""
    if not session or eng.store.get_session(session) is None:
        raise CommandFailed("bad_request", "rounds needs a session")
    arg = args.strip()
    st = eng.loop.state(session)
    if arg:
        n = None if arg in ("default", "reset") else parse(arg)
        if n is None and arg not in ("default", "reset"):
            raise CommandFailed("bad_request", f"usage: /rounds [N|default], N from 1 to {LIMIT}")
        require_principal("change the tool-round cap")
        st.rounds = n
    base = eng.loop.max_iterations
    if st.rounds:
        return f"tool rounds per turn: {st.rounds} for this session (default {base})"
    return f"tool rounds per turn: {base} (default; /rounds N sets this session's cap)"
