# Gateway undo acceptance (#537)

The gateway undo path rewinds its transcript and evicts the cached agent without notifying the context provider. A fresh engine used to reload the removed ruling from the pin file. `test_gateway_undo_gap_537.py` holds that case, which now passes, and the controls that keep pins after transcript truncation, compaction and an earlier undo tombstone.

The repair persists a reachback witness for each pin: its lead, a digest of the user and summary rows before the first principal message that holds it, in order from the start of the host's transcript.

On the first turn of a session that a new engine runs for a host that sends `incoming_message`, a pin goes only when the transcript starts with its lead before the turn's own message, no principal message holds it, and no summary row comes after the lead.

That tells an undo from a truncated host window, keeps folded pins and tombstones, and drops through the undo path, so the drop is logged and later carries skip it. Absence from a fresh transcript alone still drops nothing. `DECISIONS.md` D-041, 2026-10-30 note, lists what stays uncovered, including the handoff file an earlier compaction wrote.

An explicit post-undo provider event from the host would still help: it reaches a live engine, and this check waits for a new one.
