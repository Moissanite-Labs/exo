# SWARM-HOST: trusted-host injection layer

`exo.core.group_host.GroupHost` is the owner-scoped seam between a trusted
human host and the Swarm group primitives (`GroupRunner`,
`GroupEngineAdapter`, `GroupRuntime`, `GroupMessages`).

Scope of this slice: trusted host injection only. There is no protocol or
client integration, and no real engine factory. Tests use fakes, and a real
factory that constructs authorized engines is deliberately out of scope.

## Trust model

The host supplies at construction:

- the authenticated `owner`,
- an explicit authorized profile set (`known_profiles`),
- an authorized engine factory (`engine_factory`).

Authorization is never inferred from seat files, stored metadata, credentials
or configuration. This module constructs no profiles, engines or CoreEngine
instances itself, and is not an authentication boundary or an OS sandbox.
`GroupHost` objects must never be exposed to models.

## Lifecycle rules

- `create` restricts membership to the injected profile set.
- `start` re-validates every stored group member against the injected profile
  set **before** the engine factory is invoked or any durable claim is made.
  A factory failure or invalid audience leaves no runtime and no durable
  claim, so the caller may fix inputs and start once.
- One durable claim per group (`GroupExecutions`): no automatic retry, no
  stale-claim takeover. A partial start failure after the claim still blocks
  retry by design.
- Failed, interrupted or cancelled turns leave inputs pending (no
  acknowledgement, no retry); replies are routed before acknowledgement. This
  is not exactly-once delivery.

## `status()` API

`status(group_id)` returns a structured snapshot dict:

- `group_id`, `owner`: the stored group identity.
- `members`: tuple of `MemberStatus`. Live runner states for a group started
  in this process; otherwise the settled receipt's terminal states; otherwise
  `"unknown"` when an active claim exists without a local runtime; otherwise
  `"idle"` when nothing was ever claimed.
- `receipt`: the durable `ExecutionReceipt` or `None`. The receipt is never a
  liveness probe.
- `local_runtime`: whether a process-local runtime exists here. Without one
  there is no claimed liveness.
- `reconciliation_required`: `True` when the durable claim is still `active`
  but no local runtime exists. A prior process claimed execution and may
  still hold it, so no start may be attempted until the receipt is reconciled.
