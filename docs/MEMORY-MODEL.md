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
  Private threads are logged, but never enter the tree, other threads' views or search. Nothing from them
  reaches the principal store either (see "Private threads" below).
- **Undo** hides the entries (`messages.hidden`) instead of deleting them, so the log stays append-only
  and verifiable. Hidden entries leave the thread view and search. Summaries whose ranges hold a hidden
  entry, and the merges above them, are rebuilt from the remaining entries. The thread's context notes stay.
- **Tree.** One summary tree per agent over log positions, stored in `summaries` under the key
  `agent:<id>`. Leaves cover `leaf_size` positions, parents merge pairs, as in D-013. Leaf text labels each
  entry with its thread. The compaction floor from the canary fix carries over: a span under
  `min_compact_tokens` stays verbatim, and a summary that is not smaller than its source is dropped.

## Private threads

A private thread (`/new private`, `session.create` with `kind: private`) is not remembered. It is logged in
`sessions.sqlite` like any thread, and it can read memory: the provider block and recall (`prefetch`) still run.
Nothing written from it reaches the principal store, the tree, the learning loop or another thread's memory
and context. The same holds for every session under it: its delegate children, and their children, carry briefs
built from it and their results (the core checks `SessionStore.private`, which walks up the parents):

- **Memory hooks.** The loop does not call `on_turn_start`, `sync_turn` or `checkpoint` in or under a private thread, so
  no utterance, ruling or correction from it goes to the signed ledger. The core enforces this, so it holds for
  any memory provider.
- **Memory tools.** A memory tool called in or under a private thread gets `agent_context="private"`. exo-memory then
  refuses `exo_note` and `exo_should_have_been` and says why. `exo_recall` and `exo_get` still answer.
- **The tree.** Private entries never enter it. Its plugin hooks (`on_pre_compress`, the pre-compress
  `checkpoint`, `capture_principal`, `on_compacted`) name the latest thread that is not private, even while a
  private thread is talking. `/compact` offers each principal message with the agent reply just before it in the
  log, and reads the log the same way: a private reply is skipped, so a correction keeps the reply it judges.
- **The context plugin.** It never sees a private thread: no `on_session_start`, `select_context` or
  `on_turn_complete` for it. exo-context therefore writes no pin and no handoff from one.
- **The learning loop.** It captures no correction from a private thread and starts no review for it.
  `feedback.send` on its answers returns `recorded: false` with the reason, and so does a verdict on a delegate
  child's answer (a client opens one with `/tasks` and `/open`). A reject or should_have_been on either files no
  finding, since every thread can list findings (`exo_trace`, `/findings`). `ui.respond` on a panel a private
  thread opened is not noted in the ledger: its value can carry a comment. Neither is one on a panel it updated,
  since `ui update` takes a known panel id from any thread. The event log tells which threads showed the panel.
- **Context notes.** Other threads' notes leave it out.
- **Subagents.** A delegate child of a private thread, and any child of that child, opens with
  `memory_zoom(subagent=)` only from that private thread or a fork of it. Another thread's lookup skips it, by
  role or by id. A fork of a private thread is private too, so the original cannot open the fork's children.
- **Harness findings.** These are not memory, so they still run: the anomaly rules check its turns, and
  `exo_harness_report` files from it. Every thread lists open findings by title. An anomaly title names no
  text from the thread. Its detail can (a tool's error, a subagent goal), and so can a failed tool's signature.

## Context assembly per turn

Each model call is built fresh, in this order:

1. **System message** (stable prefix): system prompt, memory provider block, the thread's working folder,
   then the agent's tree view (all threads, oldest first). The view ends at the last summarized node: small
   spans after it (too small to summarize, or a summary still running) stay in the recent part below. So the
   system message is byte-identical for every thread of the agent in the same folder until a real summary
   lands.
2. **This thread's verbatim tail**: every message of the thread after the tree frontier, and at least its last
   `tail_messages`, widened back so it never starts with an orphan tool result. A private thread's call can
   leave out spans its own tree is still summarizing (below). Context notes stored in the thread are part of
   it. A fork's copies count at their original's log position, so a fork opens with its
   parent's tail, not the parent's whole transcript, and `/compact` shrinks a fork's tail too, except for the
   kept copies below. A copy counts only while the tree can hold its original. A private thread's fork has a
   tree of its own (below), which summarizes its copies. In any other fork, the parent may undo an entry after
   the fork. The tree then drops the original, so no tree holds the fork's copy of it (#245). The fork's tail
   keeps such copies verbatim, in order, ahead of the fork's own entries. They sit outside `tail_messages` and
   `/compact`, so every call of the fork carries them: a fork whose parent undid 20 turns carries all 20. In a
   fork of that fork, the kept copies come after the middle fork's own entries, which the tree holds, so they
   read later than they happened. Copied context notes are not kept this way. A note was never in the log, so
   it leaves a fork's tail as the tail moves on, as it does in the parent.
3. **Context notes.** Before a model call, entries other threads committed since this thread last looked
   (and after the frontier) are written into this thread as one compact note: `In thread "tui 2", 4 min
   ago: user: ... / you: ...`. The note is stored (thread-local, not in the log), so the next call sees
   the same bytes and the cache holds. A new thread's first note covers everything after the frontier,
   so window B knows what window A just did on its first turn.
4. **Recall.** Per-turn recall from the memory provider moves out of the system message. It goes in an
   unstored message just before the turn's user message, so it never piles up in the thread. Before, it
   sat in the system message and changed every turn, which broke the cache for the whole prompt; now the
   prefix up to the previous turn stays cached.

A private thread's entries never enter the agent tree, so the agent's frontier says nothing about them. A private
thread therefore keeps a summary tree of its own over its own messages, as a subagent thread does (key: its
session id, ranges: message numbers). Its tail starts at that tree's frontier, not the agent's, and the tree's
view opens the tail as an unstored message headed "Earlier in this private thread". As in the agent view, that
view ends at its last summarized node: spans too small to summarize, or a summary still running, stay whole in
the tail. Until the first summary lands, the whole thread is in the tail. A long private thread with no tree yet,
such as one from before it had one, gets its leaves on its next turn. That turn waits for them as the agent
tree's turns do (see "A turn does not wait for memory upkeep"), so a fast summarizer bounds the call. A slow one
does not, so while the private tree has leaves pending, the call leaves out their spans (#244). It cuts the
oldest pending leaf first, a whole leaf at a time, until what comes before the turn fits `view_budget`. An
unstored "[earlier messages are being summarized]" message stands in each gap. When a later leaf lands first, the
view shows the pending leaves before it verbatim. Those are the oldest, so they go first, and each keeps its
heading over the marker. Nothing else is cut. The turn stays whole: its user message, found by its turn id, and
all after it, tool rounds and context notes included. The turn can start in such a leaf, and the cut stops there.
A span that is not pending stays whole too: one skipped for good, one waiting to retry, and the last
`tail_messages`, which are in no leaf yet. A tool call and its results
are kept or cut together. The cut moves only when it must grow, and then by a whole leaf, so the calls in between
extend the one before. The size counts tool-call arguments in full, but an image only as its placeholder. A span
of large tool-call arguments can still be skipped as too small, since a leaf's size cuts each call's arguments at
400 characters, and then it stays verbatim. The system message is the same as in the agent's other threads, so
the cached prefix holds. Plugin hooks never see that tree: no `on_pre_compress` (and its `checkpoint`),
`capture_principal` or `on_compacted`. `/context` counts its view and the marker in the recent tail, and its
`from_seq` is the first stored message there. It finds the turn by the thread's last turn id, so during a turn it
keeps the running turn whole, as the call does, steer messages included. Between turns it keeps the last turn
whole too, though the next turn's call can cut it. `/compact` folds only the agent tree.

A fork copies its parent's finished per-session summaries, but it numbers its copies 1 to n. Undo hides
rows that are in the agent log, so their numbers stay used, and it deletes rows outside the log, such as a
fork's copies, while later logged rows keep their numbers. A summary with such a gap at or before its end
would cover other messages in the fork, so the fork leaves it behind and summarizes that span again from
its own copies.

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
  chunks `/compact` would fold. It reads only: it stores no note and starts no summary. In a private
  thread whose leaves are pending, it matches the next call during a turn. Between turns it keeps the last
  turn whole, which the next turn's call can cut (#244).
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

## A turn does not wait for memory upkeep (D-068, issue #99)

A turn is built from the summaries that are ready plus the verbatim tail. A span whose summary is still running
stays verbatim in the view ("summary pending"); the summary and merge work goes on in the background and the next
turn picks it up. Nothing the turn says is delayed by bookkeeping.

The one exception is size. If that view (finished summaries, unfinished spans verbatim, and the tail) is over
`view_budget`, the turn waits for pending summaries, but only for `overflow_settle` seconds (5 by default, never
more than `settle_timeout`), then builds with what is ready. The rule lives in `src/exo/turn_settle.py`. A private
thread checks its own tree the same way, after the agent tree. Its tree also waits when the call would leave out a
pending span (#244). That check counts each message in full, where the view cuts it short, so a leaf the call
just scheduled gets its chance to run first. A fast summarizer, or a span too small to summarize, then leaves
nothing to cut. Both waits of the private tree are for its own jobs only, leaves and merges: a merge shrinks the
view the cut counts. The agent tree's jobs shrink neither the private view nor the cut, so they do not stretch
either wait. So a private turn can now wait where it did not: while a cut would apply, each call waits up to
`overflow_settle` for those jobs.

The turn never sees a half-merged memory. `build` reads the summary tree and the tail in one synchronous step, and a
summary node turns `done` (text and status together) in one step, so each span is either its old verbatim or leaf
summaries or the finished summary. Messages are never both summarized and verbatim, and never neither, except
that a private thread's call can leave out a span whose summary is running, behind a marker. Memory
content is the same once the summaries finish. Tests: `tests/core/test_turn_settle.py`.

## Undo and failed summaries (D-041)

- **Undo.** Undo hides the entries of the undone turns. It also deletes each tree node whose range holds one of
  them, and the merges above it, in the agent tree (`agent:<name>`, ranges are log positions) as well as in a
  per-thread tree (ranges are message numbers). Other nodes stay, so the other threads' summarized entries stay
  in the view. One gap is left: if the last summary in the view held an undone entry, the view ends before
  that span until a summary at or after it lands again, and other threads miss the span's other entries until
  then. Context notes stored in the thread stay. They hold other threads' news, which the thread has already
  marked as seen, so dropping them would lose that news. Undo does not reach copies made before it: a note
  another thread stored keeps the undone text (that thread's model has seen it, and the note is replayed byte
  for byte), and so does a fork, whose tail keeps such a copy for good ("Context assembly per turn"). A summary of
  a deleted node that was already running when undo ran is not saved afterwards. The next turn rebuilds the summary
  from the entries that are left.
- **Failures.** A node whose summary fails is retried with backoff (30 s, then 60 s, ... up to five minutes, set by
  `retry_backoff`). During backoff the span stays verbatim and the turn does not wait for it. A temporary failure
  keeps its turn on later upkeep: an outage, or an error that says when its route reopens (`retry_after`), such as
  a refused key or a local server that is down. Any other failure is tried at most `max_attempts` (3) times in all.
  The count is stored on the node (`summaries.attempts`). After the last try the node is marked `skipped`: the span
  stays verbatim for good, and one `harness.error` event (`where` = `summary`) records it, so it shows in `exo trace`
  and findings once, not every turn.

Undo also tells the context plugin. The core calls `on_session_switch(session, rewound=True, messages=,
summaries=)` with every message left in the thread and the ids of the summary nodes still stored for its tree
(`{tree key: [ids]}`). exo-context then drops each pin that no remaining principal message holds. It also drops
each summary node it kept from `on_compacted` that the undo deleted, since the core may have restored the undone
ruling in that node word for word. The undone turns leave the window it writes handoffs from when that window is
the thread's, even if the engine is bound to another thread by then. Then it writes again, replacing that day's
file, each automatic handoff that may quote them: one that lost a summary node, the thread's own when a pin went,
and one written from the thread's window. On an agent tree a summary node goes to the thread that was newest, so
that handoff may be another thread's. A file whose window has been replaced since is written from its summaries and
pins alone. The pinned block links a handoff only if it is current. A handoff the principal wrote under its own
topic with `/exo handoff` or `/exo branch` before the undo stays on disk as written, but is no longer linked. So does
an automatic handoff written before the host last started, since exo-context keeps its summary nodes and its list of
handoffs in memory. exo-context never puts an undone ruling back in the system message or in a handoff it writes
after the undo. A ruling that was only summarized stays pinned.

A host's undo reaches exo-context too (#389). Hermes's CLI and TUI report `/undo` only to their memory providers, as
`on_session_switch(session, rewound=True)`. exo-memory passes the session id on through `exo.host_undo`, and
exo-context takes it in `on_host_undo`. Only the engine that saw the session's last turn acts. Hermes can build a
new agent on the same session (after `/reasoning` or `/fast`, say), and the old engine may live on with an old
window, so `exo.host_undo` numbers each session's turns. The engine reads the pins file again, then the window it
saw last. Hermes's `/undo N` removes the last N user turns with one notice, so the window's last user message opens
an undone turn. A pin goes when that turn holds it and no message left in the window does. A pinned block holds
nothing in this test, since it is the engine's own copy of the pins. The undo then waits in `exo.host_undo`, under
its session, with the window the notice left. The session's next turn reads the host's whole transcript and takes
the record from there, whichever engine runs that turn, so an agent Hermes built in between resolves it too
(#580): a pin the window showed goes when that transcript shows it no longer, so `/undo 3` drops the pins of all
three turns. A request that is no turn, or a turn of another session, leaves the undo pending, and a second undo
before the next turn keeps the window the first one left. A transcript that does not reach back to where the
window started was cut short, not undone, so it drops nothing, and the turn settles the record all the same. When
`/undo N` cuts at a row Hermes merged its summary into, Hermes keeps
only that row's summary part, and that part reaches back too. A compaction since the undo reaches back when its
summary row folded the window's first row or that part, whether the compaction ran in the engine that pended the
undo or in a rebuilt one, and so does one before the undo: it notes the fold with the pins (the row's digest and
the summary blocks written for it), the undo seeds its reach from that note, and a compaction that folds a row
already noted hands the note on. One that folded only rows the window never held notes nothing, so a transcript
cut short below the window's first row still drops nothing. The note follows the pins into a compression child
and a `/branch` child.

Each compaction ties the pins whose message it folded to the block it writes into its summary row, and the pins
file keeps those ties. A summary row shows the pins tied to its block. So a folded pin stays while a summary row
that carries it is left, even when the undone turn says the ruling again, word for word too: the rows a compaction
kept are matched to its input from the start and from the end, each text as many times as the result holds it. That
holds after a restart, in a new agent, and in a compression or `/branch` child. Hermes keeps its first rows before
its first summary row, so `/undo N` can take that row too, and then the folded pin goes. When no role alternates,
Hermes merges its summary into the next row. That row's own message counts as kept, not folded, and an undo of its
turn keeps only the summary part. A pin whose message sits in a tail that `/compress here N` held out of `compress`
stays: compaction never drops a pin. A compression child takes over its parent's window and pending undo. The link
to a handoff a compaction wrote before the undo goes, as on the core. Each drop is logged as counts per kind and
the session id, and a transcript cut short is logged with the session id.

A rebind puts an undone pin back no more (#592). Hermes builds a new agent on a `/model`, `/reasoning` or `/fast`
change and on `/resume`, and every such bind merges the parent session's pins into the child's, so a `/branch` or
compression child that had undone a ruling held it again, and the next turn's block or compaction wrote it back.
The pins file now keeps the digests of what an undo dropped beside the folded pins' ties, and a pin carry skips a
parent pin whose digest is there. A first bind still inherits, a pin the child still holds carries as before, and
a ruling said again after the undo is pinned again, so only its undone copy stays out.

An undo no one reports is settled by the next new engine (#537). Hermes's messaging gateway tells no one about
`/undo`: it cuts its transcript and drops the cached agent. Nor does the CLI while it has no agent, from a command
such as `/reasoning`, `/fast` or `/personality` to the next turn. So each pin has a lead in the pins file: a digest
of the user and summary rows before the first principal message that holds it, in order from the start of the
host's transcript (`conversation_messages`). The file also keeps the transcript's leads, one after each user or
summary row, up to the last pin's lead. Each request that sends `incoming_message` notes them, and the first such
turn of a session that an engine runs checks the pins. The transcript before the turn's own message must end at
one of the noted leads, so it is the noted transcript with its tail cut. A pin goes when its lead is that one or a
later one, no principal message holds it, and no summary row carries it. An undo cuts the tail, so it passes, also
an `/undo N` that takes turns before the ruling's. A transcript cut short at its start fails at its first row, and
one that opens with the same words as a lead but goes on past it fails at its end. A pin said in the session's
first message stays, as it has no lead. So does a pin a compaction folded while the transcript holds the summary
row, which comes after the pin's lead or replaces part of it. So does a folded pin the principal said again: its lead moves to the later message, but
the summary row before it still carries the pin. The core sends no `incoming_message`, so its requests note no
leads and never check: it reports every undo itself. The same check settles an undo before the engine has seen a
turn of that session.

Some cases are not covered. An undo no one reports waits for a new engine: while one engine runs the session, only
a notice drops pins. A host that runs exo-context without exo-memory passes no notice, so its undo waits the same
way. A compaction after the last request that noted the leads keeps the pins. An undo of every turn keeps every
pin. A transcript cut short at its start whose rows read word for word like the session's first rows looks the
same as an undo, so it drops the pins after them. A `/compress` between the last turn and the undo can keep the
earlier undone turns' pins. A summary row or handoff file written before the drop keeps the ruling until the next
compaction.

## Who wrote it: every message has an author (D-040)

Before D-040 the core decided "is this the principal?" by looking for tags that say it is not (a peer tag, a job
tag). Per-turn capture then got bare role and content, so a peer message framed as a ruling was saved as the
principal's ruling, and the agent could write `exo_note(kind="ruling")` for itself. Now the core stamps an author on
every message from the path it came in by, never from its text (`src/exo/provenance.py`):

| Path in | Author |
|---|---|
| `turn.send` from an authenticated principal connection | `principal` |
| `turn.send` from a local, TCP, stdio or unstamped caller | `local` |
| the model's reply | `agent` |
| a tool result | `tool` |
| a peer message (D-026) | `peer:<name>` |
| a bus message | `bus:<seat>` |
| a background job notice | `job` |
| a delegation brief to a subagent | `delegate` |
| a scheduled prompt | `schedule` |
| harness notes, thread sync, imports | `system` |

The author is stored in `meta.author`. A message stored before D-040 has none; it is read by its tags as before.

What uses it:

- `is_principal(msg)` is true only when the author is `principal` (and the old checks still pass).
- Every memory hook gets the author. `on_turn_start(..., author=)`, `sync_turn(..., turn_author={"author": ...})`,
  and each message dict in `checkpoint`, `on_pre_compress` and `capture_principal` has `author` and, for user
  messages, `principal: true|false`. exo-memory captures rulings and corrections only from principal messages and
  logs a peer's or job's turn under that author, not the principal's id.
- The learning loop's chat capture (`record_chat_signals`) and `/handoff` pins read the same flags.
- The context plugin's `select_context` gets `author` and `principal` on each message, with `principal` read from
  the author alone. exo-context pins a ruling, correction or open decision, and quotes it in a handoff as the principal's words,
  only from a principal message. A fork's copy of the principal's message keeps its author, so the child thread pins
  it too (`is_principal` skips the copy, so capture does not take it twice). A copy of a message stored before D-040
  has no author to keep. It reads as `system`, so the fork does not pin it. The recalled-memory block before each
  turn is authored `system`. Host message dicts with no core stamp are read through `provenance.author_of` the same
  way, meta tags included. A peer, bus or job tag in `meta` refuses at any role, an untagged user turn still reads
  as the principal, and an untagged assistant turn reads as the agent's. A handoff carries a pinned block only from
  a message that opens with it (exo-context's own block). A row opening with a compaction marker (a host's
  summary) carries nothing itself, in an assistant row where the host alternates roles or in a user row where a
  stock summary reads as the principal's turn. Its `_compressed_summary` flag shows a host made the row, not that
  exo-context escaped it. exo-context keeps continuity from its session pins (rulings, corrections and open
  decisions), observed only from the principal's own messages and persisted per session. One in peer, bus, job, tool or
  agent text is not carried, and neither is one quoted further down a message.
  The core's system message quotes each span too small to
  summarize word for word, a peer's turn included, so a peer's block there is data too.
- Where the harness prints a message for the model, a pinned-block tag in text the principal did not write is
  escaped: `<exo-pinned>` becomes `&lt;exo-pinned&gt;`, and the closing tag likewise (#279). That covers the
  verbatim spans and the summarizer's source (`render_message`), the note on other threads' turns, and recalled
  memory. So a peer's, bus seat's, job notice's, tool result's or the agent's copy of a block is still there word
  for word, but it no longer reads as one. The principal's own words keep their tags. The note names a bus seat's
  turn `bus:`, not `user:`.
- A summary is never the principal's words, so its tags are escaped too, whoever wrote what it summarizes. A
  summarizer reads the escaped source, but it is a model: it may decode `&lt;exo-pinned&gt;` back into a tag, or
  write one fresh. The core escapes each summary node's text as it is made (`TreeContext._run`, before the node is
  shown, merged, or passed to `on_compacted`), the directives it restores into a leaf included. exo-context escapes
  the summary in its `[CONTEXT COMPACTION]` message, its own compressor's or a host's, before it adds its own block.
  The items of a block carried from an earlier compaction come back in that new block. `/exo resume` escapes the
  whole handoff it puts into the system message: the file records the principal's rulings and corrections as
  quoted items under their own headings, never as a pinned block, so nothing of theirs is lost. The live block in a
  system message is then exo-context's own.
- `exo.provenance.defuse_pins` matches any spelling a model may read as either tag: a `<`, then the letters of
  `exo` and `pinned` in any case, with any run of characters that are not an ASCII letter, a digit or an angle
  bracket around or inside them (spaces, `-`, `_`, `/`, zero-width and other invisible characters), and a `>` on
  the same line or none. Only the brackets change: the `<` always, the `>` when it closes the tag. A blockquote `>`
  on the next line is left alone, `&` is not escaped, and escaping twice changes nothing. The match also runs over
  same-length folded views of the text, so a tag spelled in characters a later NFKC pass or tag decode would fold
  to ASCII is escaped too (#381). Not matched: look-alike letters from other scripts, and characters whose folded
  form runs to more than one character.
- Peer, bus and job text enters the context inside its own tag (`[peer message from ...]`, `[bus message from
  ...]`, `[job notice ...]`), with a closing line that says it is not the user.

The agent's own tools cannot speak for the principal. `exo_note` writes `note`, `question`, `action` or
`artifact`. Asked for `ruling` or `answer`, it writes a `note` with `type: agent_note` and `claimed_kind`, and says
so. `exo_should_have_been` records the agent's report of a correction, signed as the agent with `for: <principal>`.
The principal's own correction is captured from their own message.

In the ledger, the principal's voice is an event whose author is one of `exo.provenance.principal_names(store)`:
`principal`, the store's id, and the principal ids listed in the key registry (`exo init`, `exo import-store` and
exo-memory add them; `exo keys principal ID` adds one by hand). Consolidation and learning read rulings, verdicts,
answers and utterances only from those authors (LEARNING.md).

Turn authorship uses the transport caller defined in D-039. The core snapshots it before launching a
background turn. Send and steer use the same check. Local callers may drive turns, but their text never
counts as principal evidence. A background turn without explicit source metadata is authored as system.
`feedback.send` and `ui.respond` require a live principal caller before any learning or findings mutation.
Caller identity expires when a command returns or is cancelled. A child task gains no standing authority.

The same-user process limit in D-039 still applies. This does not provide an OS sandbox or repair older
messages already recorded with the wrong author.

## Concurrency

- One process writes an agent's log and tree: it holds `$EXO_HOME/run/agent-<id>.lock` (an OS file lock,
  released when the process dies). `exo serve` takes it; a second `exo serve` for the same agent refuses
  to start. Two starts at the same instant for one agent are settled by this writer lock, which `exo serve` takes before it reaches the socket. The socket's own lock (`run/exod.sock.lock`) settles two seats, or the echo engine, on one socket.
- The TUI with no daemon runs the core in process, takes the lock and serves `run/agent-<id>.sock`. A second
  window for the same seat connects to that socket instead of starting a second core. If the holding
  window closes, the other windows reconnect and one of them takes over.
- Seats in one home are separate cores on one `sessions.sqlite`, each with its own lock. An event's `seq` is
  taken inside its write transaction, one above the table's highest, so two cores never reuse one. A core fails
  its agent's leftover delegate tasks only once it holds the lock (docs/SEATS.md, "Several seats in one home").
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
before. Belief recall and the signed ledger are unchanged, except that a private thread writes nothing to the
ledger (see "Private threads").

## What changes for clients

- `session.create` takes `kind` (`private` for a thread that is not remembered).
- `SessionInfo` carries `agent` and `kind`.
- TUI: `/new` starts a new thread, `/sessions` and `/resume` list this agent's threads (subagent
  threads hidden), `/new private` starts a private thread. Context notes are not shown in the
  transcript.
- `/context` and `/compact` (above); both also report principal capture (D-032). Commands stay at 30.


### Fork-copy lookup index (#450)

A fork copy keeps its original log position in a derived SQLite `copy_pos` column and index. Context lookup
compares those integers instead of parsing every copied message JSON on each model call. Existing databases
backfill the derived column once when it is added. Signed message data and log hashes stay byte-for-byte intact.
Only copies tagged `meta.local = fork` receive the derived position; visibility, private-thread and undo rules
remain the same.
