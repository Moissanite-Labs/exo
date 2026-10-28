# Durable queued steering

A correction accepted while a turn is running is saved in `sessions.sqlite`
before the core acknowledges it. The same message ID, content parts and author
survive a core process restart. This applies to client steer sends and internal
peer, job or subagent steering through `Loop.steer`.

## Operator behavior

After reconnecting or reopening a conversation, the terminal and web clients
restore saved queued corrections and their cancellation controls. A recovered
correction stays queued: reading or resuming the session does not start a model
turn or deliver it into the transcript. Cancel it with the existing `/unsteer`
command, Up in an empty composer, or the web chip's close button.

When you send the next message, recovered corrections enter the transcript before
that newer message. Your new request keeps its own author and authority. New
corrections arriving during that turn follow the normal live steering behavior.

A successful cancellation is saved before acknowledgement. Undo retires queued
corrections anchored to the removed transcript work. A fork does not inherit the
parent's pending queue. Private and child queues stay in their original session;
shared memory and search retain their existing privacy rules.

## Storage and delivery contract

`SessionStore` owns `queued_steers`: a session-scoped, ordered queue with a stable
message ID, original serialized `Message`, and the latest visible transcript
message as its undo anchor. There is no second mutable queue cache in the loop.
A failed queue write or cancellation raises an error rather than acknowledging
success. Reusing an existing ID for the same session and content is idempotent;
reusing it for different content or another session is rejected.

Delivery moves a batch from the queue to transcript messages in one SQLite
transaction, using the same sequence, agent-log hash and FTS insertion path as
ordinary messages. It validates anchor visibility again inside that transaction.
Existing `message.completed` events are emitted only after commit. A crash after
commit but before an event leaves the message in the transcript, never in both
places, so reconnect snapshots recover it without duplicate transcript delivery.

The guarantee concerns committed queue records and transcript insertion across
process exits. It does not promise survival of storage hardware failure,
exactly-once model processing, or exactly-once external tool effects. A lost send
reply before acknowledgement does not establish acceptance, and retrying a new
`turn.send` request can create another message because this protocol has no
client-supplied idempotency key.

## Snapshot compatibility

`session.get` and `session.resume` keep `steers: [message_id]` and add an optional
`queued_steers: [Message]`, from the same queue read. It exposes pending content
under the same authorization and session boundary as the transcript. Present
`[]` is authoritative; absent means an older core without this extension.
Clients reconcile cancellation and delivery events racing the snapshot and retain
sends accepted after the snapshot began. No protocol event type was added.

## Implementation and checks

- `src/exo/core/sessions.py`: queue, cancellation, atomic delivery and undo retirement.
- `src/exo/core/loop.py`: persist before acknowledgement, database queue projection,
  common delivery path, and recovered-message ordering before fresh user input.
- `src/exo/core/engine.py`: snapshot projection and existing cancellation events on undo.
- `tests/core/loop/test_durable_steer.py`: real acknowledgement followed by process
  exit, restart inspection without provider calls, cancellation, failed writes,
  atomic rollback, crash after commit before events, identity conflicts, provenance,
  private sessions, forks, undo and log/FTS integrity.

Run the new regressions with
`uv run --frozen --all-extras --python 3.11 python -m pytest -q tests/core/loop/test_durable_steer.py`.
