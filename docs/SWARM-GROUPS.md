# Saved Swarm groups

`exo.core.groups.GroupStore` saves group definitions in the existing sessions database.
This is the G01 storage foundation from the [Swarm plan](https://github.com/Moissanite-Labs/exo/discussions/934).
It does not start agents, route messages, or expose client commands.

## Definition

A group has a stable ID, a metadata owner, a mode, member profile references,
a session per member, and a creation time. Conductor mode requires one conductor
from the membership. Socratic mode rejects a conductor. Members must be distinct
valid seat IDs present in the catalog supplied by the trusted caller.
The catalog must be a collection of complete profile IDs, not a string or bytes.

Saving a group creates fresh private sessions for its members in the same write
transaction. Failure leaves neither the group nor partial sessions. Existing
conversations are never attached or copied. Reopening the database restores the
same group and session IDs.

Mode, membership and conductor selection are immutable in this slice. To change
these choices, create a separate group with fresh sessions. No history transfer
is implied. There is no edit or deletion operation yet.

## Boundaries

Reads require the metadata owner's ID. A missing group and a foreign group both
return no record. The owner argument must come from trusted runtime identity,
not an agent-supplied field. Metadata ownership does not authorize execution as
a member profile, reading a member's private session, or approving that profile's actions.

A member session remains an ordinary private thread of its profile: that profile's
own clients can list it and send turns into it, and it can recall that profile's memory.

The adapter is internal and has no protocol or tool entry point. It stores no
credentials or model settings. Future runtime integration must validate profile
availability and execution authority again before starting members. It must also
enforce Socratic and conductor message audiences. Session privacy alone is not
an implementation of group message routing.

## Remaining G01 work

- Shared create/list/get APIs with caller authorization and profile resolution.
- Lifecycle and safe configuration changes, including retention policy.
- Client controls and recovery integrated with the concurrent runtime.

`tests/test_saved_groups.py` covers validation, restart, owner-scoped metadata,
fresh private sessions, and rollback when the group insert fails.

## Message delivery foundation (G03, G04)

`GroupMessages` saves explicit sender and audience IDs with each message. In
conductor mode, workers send only to the conductor. The conductor sends to one
worker per message. Socratic messages may address multiple members.

Each recipient has separate delivered and consumed receipts for each message.
Reading pending messages marks them delivered. Consumption acknowledges an exact
message ID, so acknowledging a later message never skips an earlier one. Pending
reads replay unconsumed messages after restart with the same IDs. This is
at-least-once delivery, not exactly-once execution. A runtime must deduplicate
input IDs and track ambiguous external actions separately before retrying work.

The adapter is internal. Trusted callers supply owner and member identity. It is
not a public endpoint or an agent tool, and does not authorize impersonation.
Runtime execution, shared APIs and client controls remain
separate work. Delivery receipts do not prove a model processed the input.

## Concurrent lifecycle foundation (G02, G06)

`GroupRunner` starts independent async runners for all group members, rejecting
duplicate member profiles before creating tasks. An ordinary `Exception` in one
does not cancel its siblings. It distinguishes queued, running, stopping,
stopped, completed and failed. Stop requests are idempotent during cleanup.
Cancelling a status waiter does not cancel the participants. Execution cannot
restart through the same supervisor, and failed work is never retried here.

`stopped` acknowledges a requested stop only after the task exits, including a
runner that catches cancellation, finishes cleanup and returns. Cancellation
without a stop request reports `failed`. Repeated waits preserve terminal states.
A stop before `start()` is a no-op; it does not reserve a future stop. Stopping
after `start()` but before a task's first step prevents that runner from entering.
Other `BaseException` behavior is not isolated: in particular, `SystemExit` and
`KeyboardInterrupt` follow asyncio's loop termination and sibling cancellation
semantics. Loop shutdown cancellation without a stop request is not a stop receipt.

This is an internal, process-local supervisor, not a profile execution API.
A trusted host must authorize and construct runners, enforce single ownership
across clients and processes, and isolate workspaces. Runners must finish cleanup
before exiting. This layer cannot prove an adapter stopped detached processes.
Persistent recovery, concurrency limits, pause/resume, streaming state and
message consumption integration are not implemented by this supervisor.

## Human messages and observation (G05, C04)

The internal message store supports human messages with an explicit audience.
The host can target one member or broadcast to all members. Human authorship is
stored separately from member authorship, even when their names match. These
messages do not grant permissions or approve actions.

`observe` reads all group channels for the authorized human owner. `history`
reads only a member's sent and addressed messages. Both page by sequence and
leave delivery and consumption receipts unchanged. A worker cannot read another
worker's private channel through member history. Human observation must never be
exposed as a member tool.

This history is group-channel history, not a member's private session. Storage
currently imposes no message-text size cap: the page limit bounds rows, not bytes.
An API follow-up must choose and enforce a shared text budget for human and member
sends before claiming bounded payloads. This slice does not select that policy.

The host must authenticate human access and bind member identities before calling
these methods. No public API or client controls are added here. Human input is
queued for delivery, not injected into a running provider prompt. The runtime
below adds cooperative checkpoints; provider-specific integration remains separate.

## Runtime checkpoints (G05, G07)

`GroupRuntime` connects the store and concurrent runner. The trusted host supplies
one authorized adapter per member. Each adapter receives a `MemberContext` with
its assigned member identity used for sending, history, delivery and consumption.
This is a routing convention for trusted Python adapters, not an immutable
identity boundary. Contexts are mutable and retain a private runtime reference;
adapters must not rebind the member or use that reference to obtain host controls.

Adapters call `await context.checkpoint()` between units of work. It waits while
paused, then returns unconsumed input. Delivery alone does not acknowledge input:
the adapter calls `consume(message_id)` after incorporating it. Reaching the same
checkpoint again can return the same unconsumed messages. Each checkpoint returns
at most the oldest 100 unconsumed messages. A backlog of 100 or more unconsumed
messages can hide newer human steering; adapters must still acknowledge only
input they have actually incorporated. No message starts an
adapter or triggers a reply automatically.

Pause first reports `pausing`. It reports `paused` only while the adapter waits at
a checkpoint. Resume releases the wait. Stop cancels execution through the runner,
including paused waits, and retains the runner's cleanup-based stop receipts.
Pause cannot alter an in-flight provider request or suspend detached adapter work.
A pause before start prevents adapter invocation until resume.
If resume and pause occur before a parked waiter runs, the cleared gate keeps it
parked. Cancelling one of several checkpoints does not hide the remaining waiters.

These objects are internal Python APIs, not a sandbox or model tools. Only the
trusted human host receives group controls and full observation. The host still
must use the runtime execution claim across clients and processes. Pause state is
process-local. Durable execution recovery, provider adapters, protocol commands
and client controls are not implemented by this layer.


## Durable execution claims (G07)

`GroupRuntime.start()` claims the saved group in SQLite before invoking any
adapter. Another runtime using the same database cannot start that group, even
after the first execution finishes. This is a one-execution-per-group guard,
not an exactly-once guarantee for adapter side effects. The lower-level
`GroupRunner` remains process-local and does not provide this guard.

`receipt()` reads the persisted claim and final member outcomes. `active` means
an execution was claimed and has no final receipt. It does not prove the process
is alive. A crash or partial start failure leaves that claim in place. There is
no timeout takeover, automatic retry, reset or resume API in this layer.
Reconciliation and safe recovery are separate work, not deletion of the claim.

A background task writes `settled` only after every participant has exited and
adapter cleanup has completed. Cancelling an observer does not cancel execution
or prevent settlement. Adapter error text is not persisted. If saving the final
receipt fails, `wait()` raises and the existing claim continues to block restarts.
The host must keep the store and event loop open through `wait()` on shutdown.

Claims cover callers sharing one database. Independent copies of the database
and direct use of `GroupRunner` are outside this guarantee. Python callers are
trusted: receipt IDs are correlation identifiers, not authorization tokens.
