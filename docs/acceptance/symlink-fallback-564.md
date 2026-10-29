# Symlink copy refusal proposal (#564)

The production draft now refuses a failed private copy when the target has no writer WAL. It raises an explicit LedgerError instead of silently opening live and creating sidecars. The former strict expected failure is a passing regression: the target folder is unchanged. Existing-writer live reads still pass the append/checkpoint freshness control.

This is a deliberate availability tradeoff: a full temporary folder or a target changing throughout all copy attempts now requires retry, including on read-only media. No stale immutable fallback or sidecar deletion is introduced. Existing-WAL behavior, including SQLite sidecar races, remains unchanged. Snapshot integrity work #776 remains separate; this draft changes only copy-unavailable fallback policy and its documented tests. Independent review is required before adopting the policy.
