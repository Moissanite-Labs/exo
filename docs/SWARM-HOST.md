# SWARM-HOST: trusted-host injection layer

`exo.core.group_host.GroupHost` is the owner-scoped seam between a trusted
human host and the Swarm group primitives (`GroupRunner`,
`GroupEngineAdapter`, `GroupRuntime`, `GroupMessages`).

The principal-only group command can forward lifecycle operations to an explicitly
injected host with the same owner and session-store object as its engine. There
is no live host activation or client control integration. An explicit real-engine
factory now exists in `exo.group_composition` (see [Swarm composition](SWARM-COMPOSITION.md));
its empty-plugin, environment and caller-owned cleanup limits still apply.

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
- An existing receipt (active or settled) and a missing running event loop are
  rejected before constructing engines. Ordinary factory/conversion exceptions
  become `HostError("engine factory failed")`; cancellation and other
  `BaseException` signals are not wrapped. The receipt preflight is not a lock:
  another host can claim while the factory runs, and the atomic runtime claim
  still decides whether execution may start.
- This layer has no engine cleanup hook. The trusted factory/host integration
  must arrange resource ownership if construction partly fails, validation
  rejects returned engines, or another host wins the claim race. No automatic
  close, rollback of external factory effects, or cleanup policy is supplied.
- One durable claim per group (`GroupExecutions`): no automatic retry, no
  stale-claim takeover. A partial start failure after the claim still blocks
  retry by design.
- Failed, interrupted or cancelled turns leave inputs pending (no
  acknowledgement, no retry); replies are routed before acknowledgement. This
  is not exactly-once delivery.
- Pause is cooperative: it holds pre-entry work and the member checkpoint
  before the adapter's single turn. It does not suspend an in-flight turn. A
  member can report `pausing` and then complete without ever becoming `paused`.

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
  there is no claimed liveness. A retained local runtime can also have completed;
  the flag describes an object, not whether its tasks are alive.
- `reconciliation_required`: `True` when the durable claim is still `active`
  but no local runtime exists in this host object. Another host object in the
  same process, another process, or a prior process may have claimed execution.
  This flag does not distinguish a live host from a crash. No reconciliation
  mechanism is provided here, and a settled receipt still prevents another start.
