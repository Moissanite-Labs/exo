# Gateway undo acceptance gap (#537)

The gateway undo path rewinds its transcript and evicts the cached agent without notifying the context provider. A fresh engine then reloads the removed ruling from the pin file. `test_gateway_undo_gap_537.py` reproduces this as one strict expected failure; three passing controls preserve pins after transcript truncation and compaction, and preserve existing undo tombstones.

A production repair remains unimplemented. Prefer an explicit post-undo provider event carrying the session and authoritative surviving transcript. If reconciliation instead persists a reachback witness, it must distinguish undo from a truncated host window, retain folded pins, preserve tombstones, and rewrite handoff context when required. Absence from a fresh transcript alone is insufficient evidence to discard a pin.

This draft supplies executable acceptance and safety constraints. It does not claim gateway undo is fixed and does not close #537.
