# Gateway undo acceptance gap (#537)

The gateway undo path rewinds its transcript and evicts the cached agent without notifying the context provider. A fresh engine then reloads the removed ruling from the pin file. `test_gateway_undo_gap_537.py` reproduces this as one strict expected failure; three passing controls preserve pins after transcript truncation and compaction, and preserve existing undo tombstones.

A production repair remains unimplemented. Prefer an explicit post-undo provider event carrying the session and authoritative surviving transcript. If reconciliation instead persists a reachback witness, it must distinguish undo from a truncated host window, retain folded pins, preserve tombstones, and rewrite handoff context when required. Absence from a fresh transcript alone is insufficient evidence to discard a pin.

This draft supplies executable acceptance and safety constraints. It does not claim gateway undo is fixed and does not close #537.

A concrete host-notification implementation is now proposed in `patches/0006-gateway-undo-notice.patch`. It notifies before eviction and leaves optional import/hook failures nonfatal. The patch is not installed or upstreamed; complete host acceptance and the handoff-summary rewrite remain open. The prior no-notice strict xfail remains valid for an unpatched host.
