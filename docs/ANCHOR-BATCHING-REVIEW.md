# Anchor batching needs a different durability design

The current ledger catches anchors up before another append and writes the committed head afterward.
Deferring both phases changes the truncation-detection contract. The disposable-process counterexample in
`tests/security/test_anchor_batching_counterexample.py` starts at three anchored events, commits two more,
and exits without cleanup before a deferred flush. Both anchors still name event three. Restoring the consistent three-event database image, with its original triggers and without changing either
anchor, then passes verification. The production-path control writes both anchors through event
five and detects the same removal.

This rejects the naive debounce/batch prototype for #256. It is not a production change, an exhaustive crash
proof or a physical power-loss simulation. The existing commit/anchor interruption tests and Wave 0 anchor
probes remain useful controls. No append-rate improvement is claimed.

Any replacement must preserve the existing detection window across abrupt exit, concurrent writers, failed
anchor writes and recovery, and provide independently reviewed crash evidence before changing the writer.
Until then the production anchor cadence stays unchanged. Further COUNT-query optimization can be evaluated
separately without postponing durable integrity evidence.
