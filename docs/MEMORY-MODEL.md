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
   (all threads, oldest first). The view ends at the last summarized node: small spans after it (too
   small to summarize, or a summary still running) stay in the recent part below. So the system message is
   byte-identical for every thread of the agent until a real summary lands.
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

Providers that key routing and prompt caching on a conversation id (opencode Go's `x-opencode-session`)
get one id per agent, not per thread, so every window reuses the same cached prefix.

Tree upkeep reports each finished node as `memory.summarized` (level, start, end, tokens before and after).
It is not a compaction of the live context, so it no longer shows as `context.compacted`.

## Looking at it and compacting by hand

Two TUI commands. Both are built-in `command.run` commands (`name="context"`, `name="compact"`), because
the protocol is full at 30 commands. No new command type and no new event.

- `/context` shows what the next model call for this thread will carry: system (prompt and memory block),
  the summary tree (summaries, nodes, levels, log range), the note of other threads' news (stored, plus
  news not yet stored), and this thread's recent tail. Each part has a token estimate, and the total
  follows. It also counts tree nodes by state (done, pending, skipped, failed) and says how many leaf
  chunks `/compact` would fold. It reads only: it stores no note and starts no summary.
- `/compact` summarizes now whatever is eligible for this agent. Every full leaf chunk is eligible, even
  inside the tail (the 12-message wait is skipped), then finished neighbours are merged even if the view
  is under budget. The size rules stay: a span under `min_compact_tokens`, or a summary that is not shorter
  than its source, stays verbatim. The thread's tail then starts at the new summaries instead of keeping
  the last `tail_messages` (`compact_seq` in the thread meta; it fades as the thread grows again). It
  reports each folded node and the context tokens before and after. It runs in the core that holds the
  agent lock, so a window that joined another window's core asks that core to do it. It refuses while a
  turn runs in the thread.

A merge left as is (its two children summaries, under the size floor) shows its children's summaries in
the view, not the raw messages of its span.

## Principal capture is a mechanism (D-032)

The log keeps every word verbatim, so nothing is lost. What can still go wrong is a summary that blurs an
instruction, or an instruction that never reaches the principal store. Three parts make that hard.

1. **Verbatim directives.** The summary template has a section, `Principal Directives (verbatim)`: every
   instruction, correction, preference and decision of the principal, quoted word for word. Merges keep it whole.
   The prompt is not trusted alone: after a leaf is summarized, the harness reads the principal's rulings and
   corrections in the span (`exo.capture`) and, for any whose words are not in the summary, appends them under
   `Principal directives restored verbatim by the harness`.
2. **Capture before summarizing.** A principal message is a live user message in a `thread` (not a peer message, job
   notice, context note, import, scheduled prompt, slash command or bare acknowledgement). Before a leaf that holds
   some is summarized, the leaf's span is offered to the memory provider's `capture_principal(messages, session_id,
   source)` (exo-memory writes rulings, corrections and open decisions to the signed ledger, deduped by key). Each
   user message in the offer carries `principal: true|false`, and the agent reply a message answers comes with it,
   so a correction keeps its target. The leaf records the count (`summaries.principal`, `summaries.captured`) and each
   captured log position goes in the `captures` table. The log is not touched (its hash chain stays). A hook that
   raises or returns None leaves them uncaptured; summarizing still goes on.
3. **Seen and forced.** `/context` shows `Principal messages not yet captured: N` (and says when no principal store
   is attached, in which case nothing ever captures them). `/compact` offers every uncaptured principal message to
   the hook first, tail included, then folds, and reports how many it captured and how many are still not.

Same commands and events as before: no protocol change for capture.

## Undo and failed summaries (D-041)

- **Undo.** Undo hides the entries of the undone turns. It also deletes every tree node whose range reaches the
  first hidden position, in the agent tree (`agent:<name>`, ranges are log positions) as well as in a
  per-thread tree (ranges are message numbers). A summary that was already running when undo ran is not saved
  afterwards. The next turn rebuilds the summary from the entries that are left.
- **Failures.** A node whose summary fails is retried with backoff (30 s, then 60 s, ...; `retry_backoff`), at most
  `max_attempts` (3) times in all; the count is stored on the node (`summaries.attempts`). During backoff the span
  stays verbatim and the turn does not wait for it. After the last try the node is marked `skipped`: the span stays
  verbatim for good, and one `harness.error` event (`where` = `summary`) records it, so it shows in `exo trace` and
  findings once, not every turn.

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
- `/context` and `/compact` (above); both also report principal capture (D-032). Commands stay at 30.
