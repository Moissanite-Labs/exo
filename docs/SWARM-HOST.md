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
- `aclose` stops and **awaits settlement** of every local runtime before any
  optional `engine_factory.aclose()` cleanup runs, so engines are never
  released while a runtime task may still be running. Factory cleanup is
  optional (the host works fine with a factory that cannot be closed).
- Factory cleanup is **fail-closed**: a failed factory close keeps the
  runtimes in place and leaves the failed attempt recorded rather than
  flipping `closed`. The close may be retried (idempotent), because the
  settlement has already happened and cleanup alone is retriable.
- Concurrent `aclose()` callers share **one** in-flight cleanup task: the
  factory is closed at most once per attempt, and `asyncio.shield` keeps
  the cleanup running when an individual caller is cancelled. A caller's
  `CancelledError` therefore says nothing about cleanup; poll `closed`
  and retry `aclose()` explicitly to await or re-drive it. A failed
  shared attempt is dropped once retrieved, so an explicit later
  `aclose()` starts a fresh attempt; a successful close is idempotent.
- Once closing begins, no new `create` or `start` is accepted: both raise
  `HostError` ("closing or closed").

## Conversation opt-in

Without the opt-in, `start` keeps the exact one-shot
`GroupEngineAdapter` behavior with caller-supplied `audiences`. With the
explicit `conversation=ConversationOptions(max_turns=..., idle_timeout=...)`
opt-in, `start` binds the reactive `GroupConversationAdapter` instead:

- Fixed authorized audiences are derived from the stored group mode
  (conductor mode: workers target the conductor, the conductor targets its
  first peer; socratic mode: every member targets all others). Caller
  `audiences` are then rejected with `HostError`.
- `max_turns` (int >= 1) and `idle_timeout` (number > 0) are validated
  **before** the engine factory is invoked or any durable claim exists, so
  an invalid bound leaves no engines and no claim; the caller may fix the
  inputs and start once. Without explicit options, the defaults mirror
  `DEFAULT_MAX_TURNS`/`DEFAULT_IDLE_TIMEOUT`.
- A per-start `ConversationWake` is created, set once after the successful
  start (initial mailbox input needs no idle poll) and stored only then; a
  failed construction closes the unregistered wake and leaves no stored
  wake.
- No client activation: a conversation start is a host-side runtime
  binding only. It constructs no client, connection or UI, sends nothing
  on the wire, and requires no member presence: member loops idle on
  durable pending input until a host send sets the wake or a peer reply
  arrives. Host objects (and this opt-in) are never exposed to models.

A host `send` persists the human message first, then sets the stored wake;
a member that misses the wake still picks the message up on its bounded
idle poll. `stop` cancels the member loops; `aclose` settles every runtime
and then closes each stored wake so no member lingers inside a long idle
wait.

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

## Turn concurrency

`start` and the `conversation=ConversationOptions(...)` opt-in accept an
optional host turn-concurrency bound. `max_concurrent=None` (the default)
keeps full member concurrency. A strict `int` in `1..member count` (inclusive)
bounds how many members may be inside an actual engine turn at once, never the
member loops themselves. Bounds are validated **before** the engine factory is
invoked or any durable claim exists. A `bool` is rejected even though it is an
`int` subclass, and an out-of-range value raises `HostError`.

For a conversation start the `ConversationOptions.max_concurrent` field takes
precedence when set (not `None`), otherwise the `start` `max_concurrent`
kwarg is used as the fallback.

The limit is runtime-owned and shared per group across every adapter (one-shot
and reactive). It wraps only `engine.run_turn` via `turn_slot`, never a whole
conversation loop, so every member still starts, keeps its claim and observes
pause/stop between turns. A member waiting for a turn permit reports state
`queued` rather than `running`, a pause requested while queued prevents the turn
until resume, a stop requested while queued prevents it permanently, and
cancellation in the queue or inside the turn releases the permit so peers stay
unblocked.

This is a turn-concurrency bound only. It is not a spend or token limit, not an
OS sandbox, and it does not constrain what happens inside engine tools. The
installed code is unchanged by this documentation-only slice.
