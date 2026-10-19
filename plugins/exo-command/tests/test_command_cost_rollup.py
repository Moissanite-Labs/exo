"""The cost panel counts a subagent's spend once.

When a delegated child finishes, the core adds the child's total to its parent's running total and
publishes ``usage.updated`` on the parent (``Delegator._roll_up``), right after the terminal
``task.updated``. From then on the parent's ``session_total`` holds the child, so the panel lists the
child row but must not add it to the totals again. A child that is still running, or that the core
never rolled up, is summed as before.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from exo.core.models.fake import tool_turn
from exo.protocol import schema as s
from exo_command.config import load_settings
from exo_command.costs import per_session
from exo_command.service import CommandCentre
from exo_command.source import DispatcherSource
from exo_command.state import State
from test_protocol import make, until

TS = "2026-10-11T10:00:00Z"
NOW = 1791712800.0  # TS


def ev(seq, typ, body, sid=None):
    return {"seq": seq, "type": typ, "body": body, "session": sid, "ts": TS}


def use(tin, cost):
    return {"input_tokens": tin, "output_tokens": 10, "cost_usd": cost}


def usage(seq, sid, call, total):
    return ev(seq, "usage.updated", {"usage": call, "session_total": total}, sid)


def task(seq, parent, tid, child, status):
    return ev(seq, "task.updated", {"task_id": tid, "status": status, "child_session": child}, parent)


def panel(st):
    return per_session(st.sessions.values(), now=NOW)


def rows(out):
    return {r["session_id"]: r for r in out["top"]}


def finished_child():
    """Parent p (0.10) delegates child c (0.05); c finishes and the core rolls it into p (0.15)."""
    st = State()
    st.apply_all([
        usage(1, "p", use(1000, 0.10), use(1000, 0.10)),
        task(2, "p", "k1", "c", "running"),
        usage(3, "c", use(500, 0.05), use(500, 0.05)),
        task(4, "p", "k1", "c", "done"),
        usage(5, "p", use(500, 0.05), use(1500, 0.15)),  # the roll-up
    ])
    return st


def test_finished_child_is_listed_but_not_summed_again():
    out = panel(finished_child())
    assert out["total_usd"] == 0.15
    assert out["sessions"] == 1 and out["priced_sessions"] == 1 and out["mean_per_priced_session_usd"] == 0.15
    assert [(b["seat"], b["cost_usd"], b["input_tokens"], b["sessions"]) for b in out["by_seat"]] == [
        ("main", 0.15, 1500, 1)]
    assert sum(m["cost_usd"] for m in out["by_model"]) == 0.15
    assert sum(m["input_tokens"] for m in out["by_model"]) == 1500
    r = rows(out)
    assert r["p"]["cost_usd"] == 0.15 and not r["p"]["rolled_up"] and not r["p"]["subagent"]
    assert r["c"]["cost_usd"] == 0.05 and r["c"]["rolled_up"] and r["c"]["subagent"]


def test_total_holds_steady_while_a_child_runs_finishes_and_rolls_up():
    st = State()
    st.apply_all([usage(1, "p", use(1000, 0.10), use(1000, 0.10)), task(2, "p", "k1", "c", "running"),
                  usage(3, "c", use(500, 0.05), use(500, 0.05))])
    out = panel(st)  # running: the parent's total does not hold the child yet
    assert out["total_usd"] == 0.15 and out["sessions"] == 2 and not rows(out)["c"]["rolled_up"]
    st.apply(task(4, "p", "k1", "c", "done"))
    out = panel(st)  # finished, roll-up not seen yet: still counted
    assert out["total_usd"] == 0.15 and not rows(out)["c"]["rolled_up"]
    st.apply(usage(5, "p", use(500, 0.05), use(1500, 0.15)))
    out = panel(st)
    assert out["total_usd"] == 0.15 and rows(out)["c"]["rolled_up"]
    st.apply(usage(6, "p", use(200, 0.02), use(1700, 0.17)))  # the parent's next call keeps the child in
    out = panel(st)
    assert out["total_usd"] == 0.17 and out["by_seat"][0]["input_tokens"] == 1700


def test_nested_children_are_each_counted_once():
    st = State()
    st.apply_all([
        usage(1, "p", use(1000, 0.10), use(1000, 0.10)),
        task(2, "p", "k1", "c", "running"),
        usage(3, "c", use(400, 0.04), use(400, 0.04)),
        task(4, "c", "k2", "g", "running"),
        usage(5, "g", use(100, 0.01), use(100, 0.01)),
        task(6, "c", "k2", "g", "done"),
        usage(7, "c", use(100, 0.01), use(500, 0.05)),  # g rolls into c, which is still running
    ])
    out = panel(st)
    assert out["total_usd"] == 0.15 and out["by_seat"][0]["input_tokens"] == 1500
    assert rows(out)["g"]["rolled_up"] and not rows(out)["c"]["rolled_up"]
    st.apply_all([task(8, "p", "k1", "c", "done"), usage(9, "p", use(500, 0.05), use(1500, 0.15))])
    out = panel(st)
    assert out["total_usd"] == 0.15 and out["sessions"] == 1
    assert rows(out)["c"]["rolled_up"] and rows(out)["g"]["rolled_up"]


def test_child_of_a_task_without_parent_is_summed():
    st = finished_child()  # a scheduled job's task has no parent session, so the core rolls it into nothing
    st.apply_all([task(6, None, "k2", "j", "running"), usage(7, "j", use(300, 0.03), use(300, 0.03)),
                  task(8, None, "k2", "j", "done")])
    out = panel(st)
    assert out["total_usd"] == 0.18 and not rows(out)["j"]["rolled_up"]


def test_fork_is_summed():
    st = finished_child()  # a fork has a parent but is never rolled up (SessionStore.fork)
    st.apply_all([ev(6, "session.created", {"session": {"id": "f", "parent": "p"}}, "f"),
                  usage(7, "f", use(300, 0.03), use(300, 0.03))])
    out = panel(st)
    assert out["total_usd"] == 0.18 and rows(out)["f"]["subagent"] and not rows(out)["f"]["rolled_up"]


def test_child_failed_by_a_core_restart_is_summed():
    st = finished_child()
    st.apply_all([task(6, "p", "k2", "x", "running"), usage(7, "x", use(300, 0.03), use(300, 0.03))])
    # The core died mid-child: on start it marks the task failed in its table without an event and
    # without a roll-up, so only tasks.list (at bootstrap) says it is over.
    st.load_tasks([{"task_id": "k2", "parent": "p", "child_session": "x", "status": "failed"}])
    out = panel(st)
    assert out["total_usd"] == 0.18 and not rows(out)["x"]["rolled_up"]


def test_cost_table_marks_a_rolled_up_row():
    js = (Path(__file__).resolve().parent.parent / "exo_command" / "static" / "command.js").read_text(encoding="utf-8")
    assert "s.rolled_up ? \" in parent\" : null" in js  # the row stays, labelled, so the table adds up


RULES = [("PARENT", [tool_turn("delegate", {"goal": "CHILD find the loader", "role": "scout"}), "parent done"]),
         ("CHILD", ["found it"])]


def test_live_panel_matches_the_core_total_after_a_delegation(tmp_path):
    eng, disp = make(tmp_path, RULES)

    async def main():
        centre = CommandCentre(load_settings({"principal_store": str(tmp_path / "store")}), DispatcherSource(disp))
        await centre.bootstrap()
        follow = asyncio.create_task(centre.follow())
        sid = (await centre.source.call(s.SessionCreate(title="parent", seat="lead")))["session"]["id"]
        await centre.source.call(s.TurnSend(session=sid, text="PARENT go"))
        await until(lambda: centre.state.tasks and all(t["status"] == "done" for t in centre.state.tasks.values())
                    and sid in centre.state.sessions and centre.state.sessions[sid]["stop_reason"], "parent turn end")
        follow.cancel()
        return centre, sid

    centre, sid = asyncio.run(main())
    want = eng.loop.state(sid).total  # the core's own total for the parent, the child rolled in
    out = per_session(centre.state.sessions.values())
    assert sum(b["input_tokens"] for b in out["by_seat"]) == want.input_tokens
    assert sum(b["output_tokens"] for b in out["by_model"]) == want.output_tokens
    kids = [r for r in out["top"] if r["session_id"] != sid]
    assert len(kids) == 1 and kids[0]["subagent"] and kids[0]["rolled_up"] and kids[0]["input_tokens"] > 0


def test_panel_bootstrapped_after_a_delegation_matches_the_core_total(tmp_path):
    eng, disp = make(tmp_path, RULES)

    async def main():
        src = DispatcherSource(disp)
        sid = (await src.call(s.SessionCreate(title="parent", seat="lead")))["session"]["id"]
        await src.call(s.TurnSend(session=sid, text="PARENT go"))
        await until(lambda: any(e.type == "turn.completed" and e.session == sid for e in eng.hub.since(0)), "turn")
        centre = CommandCentre(load_settings(), src)  # joins late: replay plus tasks.list
        await centre.bootstrap()
        return centre, sid

    centre, sid = asyncio.run(main())
    want = eng.loop.state(sid).total
    out = per_session(centre.state.sessions.values())
    assert [(b["seat"], b["input_tokens"]) for b in out["by_seat"]] == [("lead", want.input_tokens)]
    assert out["sessions"] == 1 and len(out["top"]) == 2
