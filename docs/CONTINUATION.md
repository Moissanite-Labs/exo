# Turn continuation

During a turn, the agent can call `exo_end_turn` to ask the harness to wake its session after the turn ends. The flags are structured, so nothing is parsed from reply text.

- `continue_now` wakes the session once, right after a clean end.
- `wake_after` wakes it after 1 to 3600 seconds.
- `followup_pending` is only recorded. It never wakes the session.

## Who can stop a wake

A pending wake is dropped when any of these happens:

- The principal presses Stop while a turn is running.
- The principal steers the running turn.
- Someone undoes turns in the session.
- Any new turn that is not itself a wake starts.
- The engine closes.

A wake that has already fired but is still waiting for the session lock is dropped too.

## Wake budget

A session gets at most five harness wakes in a row. After that, the harness stops and posts a notice. Only a turn from the principal resets the budget. A job, peer or bus turn does not reset it, and neither does Stop.

## Authorship

The engine stamps a wake turn with author `system`. The continuation module does not import provenance and sets no author itself.

## Known gap

The TUI sends Stop only while a turn is running. Between turns, a pending `wake_after` timer stays armed until it fires, a new turn starts, or the session is undone. This change does not fix that.
