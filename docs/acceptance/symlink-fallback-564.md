# Symlink fallback acceptance gap (#564)

When a private snapshot cannot be obtained, the read-only symlink path opens live SQLite. The executable regression forces that existing fallback seam and observes new `-wal` and `-shm` files beside the disposable target after reader close. It is a strict expected failure, not a production fix.

The passing control requires a reader to observe a concurrent writer append and checkpoint. Switching all fallback opens to immutable mode is therefore insufficient: it can retain stale page state. Removing sidecars when the reader closes is unsafe because a writer may still own or need them.

A repair must choose a safe availability policy: a clear failure when a private snapshot cannot be obtained, or a genuinely read-only live reader with the same freshness guarantees. Silent empty-ledger fallback is not acceptance. Snapshot integrity work in #776 is separate; this draft neither changes that code nor claims to close #564. Independent implementation and review remain required.
