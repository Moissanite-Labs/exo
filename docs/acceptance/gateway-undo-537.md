# Gateway undo acceptance (#537)

The gateway undo path rewinds its transcript and evicts the cached agent without notifying the context provider. A fresh engine used to reload the removed ruling from the pin file. `test_gateway_undo_gap_537.py` holds that case, which now passes, and the controls that keep pins after transcript truncation, compaction and an earlier undo tombstone.

The repair persists a reachback witness for each pin: its lead, a digest of the user and summary rows before the first principal message that holds it, in order from the start of the host's transcript. The pins file also keeps the transcript's leads, one after each user or summary row, up to the last pin's lead. Only a request that sends `incoming_message` notes them.

On the first turn of a session that a new engine runs for a host that sends `incoming_message`, the transcript before the turn's own message must end at one of the noted leads. It is then the noted transcript with its tail cut.

A pin then goes only when its lead is that one or a later one, no principal message holds it, and no summary row carries it. So an `/undo N` that also takes the turns before the ruling's drops it too. The last rule keeps a folded ruling the principal said again after the compaction, when the undo takes only the later message.

That tells an undo from a truncated host window. A window fails at its first row, and one that opens with the same words as a lead but goes on past it fails at its end. A window whose rows read word for word like the session's first rows still looks the same as an undo.

The check keeps folded pins and tombstones, and drops through the undo path, so the drop is logged and later carries skip it. Absence from a fresh transcript alone still drops nothing. `DECISIONS.md` D-041, 2026-10-30 note, lists the cases that stay uncovered, including the handoff file an earlier compaction wrote.

An explicit post-undo provider event from the host would still help: it reaches a live engine, and this check waits for a new one. A live engine that runs a turn after the undo leaves a transcript that goes on past the noted lead, so no later check drops the pin.
