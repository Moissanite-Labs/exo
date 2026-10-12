# Memory model: one agent, one continuous memory (D-028)

A seat is one agent. All of its windows, channels, scheduled jobs and peer messages are threads of one
mind with one log and one summary tree. Opening a new window does not start a new mind; it starts a new
focus inside the same memory. Different seats are different agents: they share the principal store and
talk through peers and the bus.

## Data model

Everything stays in `sessions.sqlite`.

- **Agent.** `sessions.agent` names the agent a thread belongs to: the seat name, or `default` when the
  engine runs with no seat.
- **Thread.** A thread is a session row. `sessions.kind` is one of `thread` (a window or channel),
  `scheduled` (a cron run), `private` (opts out of shared memory), `subagent` (a delegate child). Protocol
  names stay `session.*`; clients call them threads.
- **The log.** Messages keep their per-thread `seq`, so undo, fork, tool repair and the TUI work as before.
  A message in an agent thread also gets `messages.pos`: its place in the agent's one append-only log
  (1, 2, 3, ... per agent, given inside the write transaction). Each log entry also stores
  `hash = sha256(previous hash, agent, pos, thread, data)`, so the log can be checked end to end
  (`SessionStore.verify_log`, `exo memory verify`).
- **Source.** Each entry knows its thread (`messages.session`), the thread's kind, and its origin in
  `meta` (`import`, `peer`, or live).
- **Not in the log** (`pos` is empty): subagent threads (they keep their own transcript, opened by name
  with `memory_zoom`), fork copies (the original is already in the log) and context notes (below).
  Private threads are logged, but never enter the tree, other threads' views or search.
- **Undo** hides the entries (`messages.hidden`) instead of deleting them, so the log stays append-only
  and verifiable. Hidden entries leave the thread view and search; a summary already made keeps them.
- **Tree.** One summary tree per agent over log positions, stored in `summaries` under the key
  `agent:<id>`. Leaves cover `leaf_size` positions, parents merge pairs, as in D-013. Leaf text labels each
  entry with its thread. The compaction floor from the canary fix carries over: a span under
  `min_compact_tokens` stays verbatim, and a summary that is not smaller than its source is dropped.

## Context assembly per turn

Each model call is built fresh, in this order:

1. **System message** (stable prefix): system prompt, memory provider block, then the agent's tree view
   (all threads, oldest first). This is byte-identical for every thread of the agent until a new node
   finishes, so the prompt cache is shared across windows and turns.
2. **This thread's verbatim tail**: every message of the thread after the tree frontier, and at least its
   last `tail_messages`, widened back so it never starts with an orphan tool result. Context notes stored
   in the thread are part of it.
3. **Context notes.** Before a model call, entries other threads committed since this thread last looked
   (and after the frontier) are written into this thread as one compact note: `In thread "tui 2", 4 min
   ago: user: ... / you: ...`. The note is stored (thread-local, not in the log), so the next call sees
   the same bytes and the cache holds. A new thread's first note covers everything after the frontier,
   so window B knows what window A just did on its first turn.
4. **Recall.** Per-turn recall from the memory provider moves out of the system message. It goes in an
   unstored message just before the turn's user message, so it never piles up in the thread. Before, it
   sat in the system message and changed every turn, which broke the cache for the whole prompt; now the
   prefix up to the previous turn stays cached.

## Concurrency

- One process writes an agent's log and tree: it holds `$EXO_HOME/run/agent-<id>.lock` (an OS file lock,
  released when the process dies). `exo serve` takes it; a second `exo serve` for the same agent refuses
  to start.
- The TUI with no daemon runs the core in process, takes the lock and serves `run/agent-<id>.sock`. A second
  window for the same seat connects to that socket instead of starting a second core. If the holding
  window closes, the other windows reconnect and one of them takes over.
- Inside the writer, two threads can run turns at once (each thread has its own turn lock). Positions are
  given inside one SQLite write transaction, so they never collide. Each thread sees the other's committed
  entries at its next model call through a note.

## Migration

On first open by the engine (`PRAGMA user_version` below 2), existing sessions become threads of their
seat's agent (`default` when they have no seat). Delegate children (found in `tasks`) become `subagent`,
`scheduled: ...` runs become `scheduled`. Their messages are put in the log in time order with
`origin=import`, so their leaves are summarized on the import lane. Fork copies stay out of the log. Old
per-session summaries stay in the table unused. Nothing is deleted.

## exo-memory

The engine hands the memory provider an `agent_log` view (search, zoom, date, length). With it,
exo-memory stops writing its own episodic log, so there is one log. Its recall searches the agent log,
and its summary view is not repeated, since the tree is already in the system message. `exo_zoom` and
`exo_date` read the agent log. Without it (another host), exo-memory keeps its own episodic layer as
before. Belief recall and the signed ledger are unchanged.

## What changes for clients

- `session.create` takes `kind` (`private` for a thread that is not remembered).
- `SessionInfo` carries `agent` and `kind`.
- TUI: `/new` starts a new thread, `/sessions` and `/resume` list this agent's threads (subagent
  threads hidden), `/new private` starts a private thread. Context notes are not shown in the
  transcript.
- Commands stay at 30. No new events.
