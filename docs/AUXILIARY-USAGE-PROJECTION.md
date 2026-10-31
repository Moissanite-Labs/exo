# Auxiliary usage projection (#878)

This is the aggregation slice of auxiliary accounting, not automatic accounting.
`aggregate_auxiliary` projects explicit request-attempt observations into separate
session/purpose buckets, including a None-session bucket. Failed requests with
reported usage count too. Identical duplicate IDs count once; conflicting copies
fail visibly. Main-loop purposes are refused to avoid double counting.

Unknown usage or price keeps total cost unknown. A separate known-cost subtotal
is explicitly incomplete. Request IDs must identify actual attempts: separate
retries need separate IDs, and usage snapshots from one attempt must be reconciled
before this projection. The bounded input limit fails rather than silently drops.

No code captures requests or writes session totals yet. Remaining #878 scope is
router/compressor and plugin instrumentation for every attempt, purpose and owning
session propagation, price-basis provenance, failed/partial usage capture, durable
storage and migration, UI/session totals integration, and controls proving main
calls are never counted twice. Existing missing historical usage is unrecoverable.
Tests consume a real FakeProvider usage event and synthetic priced/unpriced,
failed, retried and sessionless records; these are not live spending observations.
