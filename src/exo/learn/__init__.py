"""exo.learn: the fused learning loop (LEARNING-LOOP.md sections 5 and 6, D-012).

    handle_feedback(engine, body)    feedback.send: a verdict event, plus a preference pair for edits
    handle_ui_respond(engine, body)  ui.respond: a ui signal event
    attach(engine)                   register the fast loop on the engine's hooks
    nightly(engine)                  the slow loop (async): promote, climb, check regressions

The store comes from config `learn.store`, else the exo-memory plugin's store, else the active principal in
config.json (`exo init` sets it). With none, learning is off and says so once.
"""
from __future__ import annotations

from typing import Any, Mapping

from .learner import Learner, get_learner
from .signals import handle_feedback, handle_ui_respond

__all__ = ["Learner", "attach", "get_learner", "handle_feedback", "handle_ui_respond", "nightly", "revert",
           "export_taste", "learned_since"]


def attach(engine: Any, env: Mapping[str, str] | None = None) -> Learner:
    """Make the engine's learner and register its hooks (on_turn_start cancels reviews, on_turn_end starts them)."""
    return get_learner(engine, env).attach()


async def nightly(engine: Any, **kw: Any) -> dict:
    """Run the slow loop once. Safe to call when learning is off (returns {"status": "off"})."""
    return await get_learner(engine).nightly(**kw)


def revert(engine: Any, proposal_id: str, reason: str) -> dict:
    from .promote import revert as _revert
    return _revert(get_learner(engine), proposal_id, reason)


def export_taste(engine: Any):
    from .promote import export_taste as _export
    return _export(get_learner(engine))


def learned_since(engine: Any, since: str | None = None) -> list[str]:
    from .promote import learned_since as _learned
    return _learned(get_learner(engine), since)
