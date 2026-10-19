# Project Exo: technical whitepaper

Version 0.2, October 2026. Aska Digital.

Exo is early and in active build. This paper keeps a strict line between what exists and what is planned.
Anything marked **[designed, not built]** is a design that has been written down and reviewed, but has no
working code yet. Anything marked **[built]** exists in the repository and is covered by tests that pass
today. Part III lists both in full.

## Contents

- [Part I. Overview](#part-i-overview)
  - [1. The problem](#1-the-problem)
  - [2. Why the obvious fixes fall short](#2-why-the-obvious-fixes-fall-short)
  - [3. Start from a record, not a memory](#3-start-from-a-record-not-a-memory)
  - [4. A store that knows you](#4-a-store-that-knows-you)
  - [5. One continuous memory](#5-one-continuous-memory)
  - [6. Learning your taste, and proving it](#6-learning-your-taste-and-proving-it)
  - [7. Acting safely without asking about everything](#7-acting-safely-without-asking-about-everything)
  - [8. One core, many screens](#8-one-core-many-screens)
  - [9. A harness that watches itself](#9-a-harness-that-watches-itself)
  - [10. Fit one person, work for anyone](#10-fit-one-person-work-for-anyone)
- [Part II. Technical design](#part-ii-technical-design)
  - [11. Architecture at a glance](#11-architecture-at-a-glance)
  - [12. The core daemon and its event protocol](#12-the-core-daemon-and-its-event-protocol)
  - [13. The model router](#13-the-model-router)
  - [14. Decision models](#14-decision-models)
  - [15. Tools, hooks and gates](#15-tools-hooks-and-gates)
  - [16. The agent loop](#16-the-agent-loop)
  - [17. Context and the agent's memory](#17-context-and-the-agents-memory)
  - [18. Plugins and slots](#18-plugins-and-slots)
  - [19. The principal store](#19-the-principal-store)
  - [20. The signed ledger](#20-the-signed-ledger)
  - [21. Retrieval](#21-retrieval)
  - [22. The learning loop](#22-the-learning-loop)
  - [23. Trace, findings and re-verification](#23-trace-findings-and-re-verification)
  - [24. One surface](#24-one-surface)
  - [25. Provenance and selective upstream rebasing](#25-provenance-and-selective-upstream-rebasing)
  - [26. Privacy and security model](#26-privacy-and-security-model)
  - [27. Portability toward a companion device](#27-portability-toward-a-companion-device)
  - [28. Walkthrough: one turn through every layer](#28-walkthrough-one-turn-through-every-layer)
- [Part III. Status and roadmap](#part-iii-status-and-roadmap)
  - [29. Built and tested](#29-built-and-tested)
  - [30. Designed, not built](#30-designed-not-built)
  - [31. The build waves](#31-the-build-waves)
  - [32. Open questions](#32-open-questions)
- [33. Related work and acknowledgements](#33-related-work-and-acknowledgements)
- [34. Glossary](#34-glossary)

---

## Part I. Overview

### 1. The problem

Language models can now do real work. They write and fix code, draft documents, research questions and run
tasks on a computer. Yet working with them day to day has an odd shape. Each session starts from zero. You
explain the project, the constraints, the way you like things done. The model does good work. Then the
session ends and all of that is gone. Tomorrow you meet a brilliant stranger again.

A few terms first. A *model* here is a large language model: a program that reads text and predicts the text
that should come next. Everything a model knows about your situation must be in its *context window*, the
text sent to it in one request. Windows are measured in *tokens*, which are pieces of words (in English, a
token is about four characters). Anything outside the window does not exist for the model.

Three costs follow from this.

**Context is lost.** The facts of your work (which repository, which client, what was decided last Tuesday
and why) have to be supplied again, by you, or they are missing.

**It forgets you.** Some products keep a few notes about you inside one vendor's account. Switch model or
tool and the notes stay behind. You usually cannot see why a note exists, or fix it when it is wrong.

**It does not learn your taste.** By *taste* we mean the many small preferences that make work yours: tone,
structure, what counts as done, what must never happen without asking. When you correct a model ("shorter",
"lead with the result", "never push without asking me"), the correction lives in that one conversation. Next
week you make the same correction again.

Why this matters: what stops people from handing real work to models is more often missing context than
missing intelligence. A capable assistant that knows nothing about you can only do generic work, and generic
work needs your attention to become your work. Attention is the scarce resource. A system that needs you to
re-explain, re-correct and approve every step uses up the very thing it was meant to save.

### 2. Why the obvious fixes fall short

**Bigger context windows.** Windows have grown to around a million tokens. That helps, but a window is not a
memory. Everything in it is sent, and paid for, on every request. Very long transcripts also tend to blur a
model's focus as they grow, a problem practitioners call context rot. And when the session ends, the window
is empty again.

**Letting the model keep notes.** Saved notes help, but a note with no evidence behind it cannot answer the
question "why do you believe this?" A wrong note is applied again and again, and nothing checks it.

**Training a model per person.** Changing a model's weights for one user is slow and costly, and it ties your
history to one model that will be replaced within months.

Exo combines a handful of ideas instead. None is exotic on its own. The rest of Part I builds them up one at
a time.

### 3. Start from a record, not a memory

Think of how a bank keeps accounts. It does not treat your balance as the truth. It keeps every transaction,
and the balance is computed from them. If the balance is ever in doubt, you add up the transactions again.

Exo does the same with your work. Every meaningful event is written once to an append-only *ledger*: what you
said, a standing rule you gave, an action an agent took, and whether you accepted, edited or rejected what it
produced. *Append-only* means entries are added and never changed or deleted. A mistake is fixed by a new
entry that refers to the old one.

Everything else Exo knows about you (your profile, its beliefs about you, your skills, the search indexes) is
a *view*: something computed from the ledger that can be thrown away and rebuilt. When a better model arrives
next year, it can catch up by replaying the record, the way a new accountant can rebuild the books from the
transaction list.

Two protections make the record trustworthy.

- Each entry is *signed*. A digital signature is a short code made with a secret key. Anyone with the
  matching public key can check it, and nobody without the secret key can forge it. So each entry proves who
  wrote it.
- Each entry carries the *hash* of the entry before it. A hash is a fingerprint of data: a short string
  computed from the data that changes completely if a single character changes. Because each entry contains
  the fingerprint of the previous one, the entries form a chain. Altering or removing any entry breaks every
  link after it, and a check finds the break.

### 4. A store that knows you

The ledger is complete, but it is far too large and raw to show a model. So Exo keeps a *principal store*:
one folder that holds what the system knows about one person. That person is the *principal*, the one the
system works for. The store has layers, from small and always loaded to large and fetched on demand:

- **The pack.** One page, read on every request: who you are, your standing rules, your voice, what needs
  your approval.
- **Beliefs.** Short notes, each about one claim or a few related claims, with a confidence level, the dates
  when it holds, and links to the evidence. For example: "prefers a table over bullets when comparing
  options (high confidence, from four linked corrections)."
- **Items.** Things you saved elsewhere, such as bookmarks, notes and starred repositories. They are
  evidence, not beliefs.
- **The ledger**, at the bottom.

When a request arrives, Exo must choose which beliefs and items to show the model, because the window is
limited and every token costs money and focus. Choosing is called *retrieval*. Exo uses two kinds of search
and merges them. *Keyword search* ranks notes by the words they share with the request and gives more weight
to rare words, since a shared rare word says more than a shared common one. *Embedding search* turns each
note into a list of numbers, called an *embedding*, arranged so that texts with similar meaning sit near each
other, and then finds the notes nearest the request. It finds "keeps replies short" for a request about
"brevity" even though no word matches. After merging the two lists, Exo follows links from the best notes to
related ones, then packs the result under a fixed token budget.

The embedding model is small and runs on your own machine, so your text does not leave it to be indexed.
Keyword retrieval over the store is built. The embedding and link-following stages are designed, not built.

### 5. One continuous memory

The store holds what Exo knows about you. The work itself has a different problem. Most assistants keep
appending to a growing transcript and send all of it on each request. When it gets too long they compress it
once, in a hurry, and quality drifts. And each new chat window starts a new, empty transcript.

Exo builds every turn as a fresh request. Picture a long book with summaries at several levels: a line for
each chapter, a paragraph for each part. To keep reading well you need the summaries of everything so far,
plus the last few pages in full. Exo keeps exactly that. The most recent messages go to the model word for
word. This is the *verbatim tail*. Older messages are folded into a *summary tree*. Every eight older
messages get one short summary, called a leaf. When the summaries together grow too long, two neighbouring
summaries merge into one at the next level up, and so on, so the oldest material is the most compressed.

There is one book per agent, not one per window. An *agent* here is one configured Exo assistant, called a
*seat*. Every window you open on a seat, every scheduled run and every message from another agent is a
*thread* of that one agent. All threads write to one append-only log and share one summary tree. A thread
only changes focus: it brings its own recent messages, plus a short note of what the other threads did since
it last looked. So a fact you tell the agent in one window is known in a second window on its first turn.
Opening a new window starts a new topic, not a new mind. A thread can be marked private, and then it is
logged but never shared.

Before Exo builds the request for a new turn, it waits for any pending summaries to finish, so the view
always covers everything that came before. Every original message stays in a local database with full-text
search, so a detail that was summarized away can always be found again.

Summaries lose detail by design. The one thing they must never lose is what you told the agent. So capture
of your words is a mechanism, not a habit the model is asked to keep. The summary format has a section for
your instructions and corrections, quoted word for word. After each summary is written, the harness checks
it against your own messages and puts back any ruling the model dropped. And before a stretch of your
messages is summarized, it is handed to the memory plugin, which writes your rulings and corrections to the
signed ledger. All of this is built.

### 6. Learning your taste, and proving it

The most useful signal you can give an assistant is a correction on its own work. If it drafted X and you
changed it to Y, you have shown, at the exact point where it went wrong, what you wanted instead. Research on
imitation learning found the same thing in robots and games: corrections gathered on the learner's own
attempts teach far more than watching an expert work alone.

Exo writes these signals to the ledger: corrections, edits, accepts, rejections, explicit rules, and outcomes
such as "the tests passed" or "the post went out". In the background, a reviewer turns them into
*proposals*: small typed lessons with evidence, written as a positive claim. For example: "The principal
wants the result first and the detail after, not a long preamble, because they cut the preamble in three
drafts this week (entries 41, 77, 102)." The reviewer can only propose. It cannot edit your profile.

A lesson can sound right and still be wrong. Exo has a personal exam built from your own past decisions:
questions about you with known answers, pairs of a reply you rejected and the one you chose, and real past
moments cut off right before you gave your verdict. Changes covered by an evaluation gate are scored against
the current setup on this exam. Other promotion rules can promote a lesson without that gate, including your
explicit ruling and some repeated, high-confidence taste or fact evidence.

This method is called *hill climbing*. Picture standing on a hillside in thick fog. You cannot see the top,
but you can tell whether a step takes you higher. Take one small step. If you are higher, stay. If not, step
back and try another direction. Exo's height is the exam score, and each step is one change you can read,
such as one new taste rule.

Any exam has a weak spot: you can learn the answers instead of the subject. A student who memorizes last
year's practice paper scores well on it and badly on the real exam. So the newest part of your exam is *held
out*. The learning process never reads it. For changes that go through the evaluation gate, the held-out set
helps guard against a system that flatters its own test.

This loop is built: capture, the background reviewer, typed proposals, promotion rules, revert, and a climb
that evaluates selected changes against held-out cases. Principal-confirmed lessons and some repeated,
high-confidence taste or fact lessons can promote through separate rules. What is not yet built is the trigger
that runs the climb every night by itself.

### 7. Acting safely without asking about everything

An assistant that asks before every step is useless. People cannot watch a quiet system all day. They start
approving by reflex, which is worse than not asking. An assistant that never asks is dangerous.

Exo sorts actions by consequence, not by count. It asks whether the action can be undone, who can see it,
whether it costs money, and whether it affects someone else. Editing a file on your machine runs at once and
is logged. Pushing to a public repository, posting, paying, or sending an email to another person waits for
you, with a one-line summary and a sensible default. Some rules protect other people, and your own settings
cannot lower them. Anything that touches credentials, such as a login, a key file or a password store,
always asks you, and no rule can change that.

Asking is only half of the job. An approval is consent, and consent has to be about something exact. So
for anything that leaves your machine, Exo takes a fingerprint (a hash) of the exact text, target and
arguments, and your "yes" covers that fingerprint only. If the agent changes one character after you
approved, the fingerprint changes and it asks again. "Always allow" is not a blank cheque either. It covers
one kind of act on one target, and for an outward act one exact text. It expires after days, not never, it
is written to the ledger, and you can list and revoke it. Only you can grant it.

Who may approve is also explicit. By default only you can. You may name another agent as a reviewer for a
kind of act, for example public posts, and then that agent's answer must carry its own signature, so an
answer cannot be forged or replayed for a different text. A message from another agent is input, never
consent: if it tries to approve something, it is refused and shown to you. Exo knows an answer is yours by
the connection it came from, your window or your web page, never by what the answer claims, so a command the
agent runs cannot approve the agent's own request. And before a post goes to a
public place, Exo sweeps it for internal names, home folder paths, model names and internal ids, and holds
it if any appear. A false alarm costs one extra question. A leaked name in a public repository cannot be
taken back.

Many small choices in this process (which tool, whether a panel should open, which model fits a task) do not
need a large model at all. A *decision model* answers a fixed question with probabilities over fixed options
instead of writing text. It is fast and cheap. A probability lets Exo act when it is sure and ask when it is
not. One rule is fixed: a decision model may make a gate stricter, never looser. The gate, its approvals and
its sweep are built, and the gate already asks a decision model for a second opinion that can only tighten.
Calibrating those probabilities on your own decisions is designed, not built.

### 8. One core, many screens

Exo's working parts run as one background program, called `exod`. (A *daemon* is a program that runs in the
background and waits for requests. The "d" in `exod` is for daemon.) Every screen is a *client* of it: a
terminal, a web page, a chat channel, and one day a small device. Clients speak a small typed *protocol*:
they send commands in, and receive events out.

Every event gets a number, and every event is saved before it is sent. A client that drops off, such as a
phone on a bad connection, comes back and asks for everything after the last number it saw. It misses
nothing, and two clients watching the same session see exactly the same sequence. This part is built.

The screen itself feels like a terminal: a prompt and a transcript, with three prefix keys and one command
palette. Richer elements, such as a diff viewer, an approval card or a task board, are panels that the model
opens when they help, and that each client draws as well as it can. A terminal client, a web client and a
plain line client are built.

Your keyboard is not the only way in. Another agent may send a message. A long shell command may finish
while the agent does other work. Such inputs enter through one door, and the door asks the same three
questions every time: who sent this, may it in (yes, hold for you, or no), and should it reach a running
turn now or wait for the next one. Then it leaves a receipt. One door means one place to get the rules
right. The first two sources through it, messages between agents and the end notices of background shell
jobs, are built. Chat channels and scheduled runs are meant to move onto the same door later.

The agent can also hand work to helpers. A helper, or *subagent*, is a child thread on the same core with a
narrower set of tools and hard limits on depth, time and tokens. Each helper runs on the cheapest model tier
that can do its job: a small model for searching and reading, a mid-size one for building, the largest only
for judgment. A helper that fails gets one retry a tier up. This is built.

### 9. A harness that watches itself

A tool that cannot see its own faults cannot fix them. Early on, an Exo agent was asked to judge its own
working environment, and it missed the worst problems. One turn took more than eight minutes, and nothing
said why. Nothing recorded which model had actually answered, or that a fallback had happened. Cost was
always blank. The agent could only see its conversation, not the machinery under it.

So Exo records its own behaviour as data, reads it, reports on it, and checks its own fixes. Every model call
leaves an event: which model was asked for, which one answered, each failed try, the wait before the first
word, the tokens and the cost. Every turn leaves a timing summary. Every caught error leaves an event, so no
failure is silent. A set of plain rules reads these records at the end of each turn, with no model call, and
files a *finding* when something is wrong: a slow turn, a fallback, a tool error, the same command run twice,
a shown model that differs from the one that served.

A finding has a stable name, so the same problem counts up instead of piling up. When a developer marks a
finding fixed in a given version of the code, Exo keeps watching. If the problem comes back on that version
or a later one, the finding reopens. If it stays quiet for 20 turns or 3 days, it is marked verified. A fix
is a claim, and the trace is the test of it.

Cost follows one rule of honesty: a price Exo does not know is shown as unknown, never as zero. A zero would
look like a free call and hide real spend. All of this is built.

### 10. Fit one person, work for anyone

Exo holds two goals at once. It should fit one person as closely as possible, because an assistant that does
not really know you is not worth much. And it should work for anyone, because a system built around one
person's accounts and habits cannot be handed to a second.

The design answers both with one split. The *mechanism* (the code: the daemon, the ledger, the store format,
retrieval, the learning loop) is generic and ships to everyone. The *principal* (one person's data, profile,
rules and tuning) lives only in that person's store. No line of the core may name a user, an account or a
machine, and a test checks this. A fresh install with an empty store runs end to end, and an onboarding
interview builds a new person's store from zero. Updating the core must never lose anything in a store.

That is the whole system at a high level: a signed record at the bottom, a portable store of what it knows
about you, one continuous memory with fresh and complete context on every turn, explicit promotion rules
with held-out evaluation for selected changes, gates by consequence with consent bound to exact text, one core and one way in
behind every screen, and a harness that measures itself. Part II covers each piece in depth.

---

## Part II. Technical design

### 11. Architecture at a glance

<!-- fig:layers -->
```
 Clients        terminal | web | planned: desktop, chat channels, device
                         |   commands in, events out
 Protocol       one typed, numbered, replayable event stream
                         |
 Core (exod)    turn loop | router | tools + gate | memory | ingress | trace | plugins
                         |
 Learning       capture -> reviewer -> proposals -> n=1 eval -> keep or drop
                         |
 Store          pack | router | belief nodes | items | indexes
                         |
 Ledger         signed, hash-chained, append-only
```
*Figure 1. Exo's layers. Each layer is rebuilt from or served by the one below. Clients hold no state that
matters.*

The core is a Python package. Its runtime dependencies are two libraries: PyNaCl (signatures) and httpx (HTTP
to model providers). The HTTP server inside `exod` is written on the standard library's `asyncio`, so it adds
no web framework. Storage is SQLite files and plain Markdown files.

The core has a line budget. A test counts the lines in `src/exo/core`, `src/exo/protocol`, `src/exo/server`
and two command-line modules. Wave 1 shipped under 8,000. The plan allowed 15,000 by the end of wave 3. The budget
rose to 16,000 during the security wave. Engine split step 4 (D-067) cut the count to about 14,800 by deleting
unused code and moving the peer transport and the scheduler out of the core.

The budget is now a ratchet with headroom (D-077). The test records a floor, the count after the last real
reduction, and allows 1,000 lines above it. Each real reduction lowers the floor, and the floor never rises. Fixes
and features fit inside the headroom with no ceremony, and a security or robustness fix is never blocked by the
budget. A feature that would cross the ceiling waits for a reduction or carries a recorded decision.

The reason is a ruling that the core should be lean and optimized, but not at the cost of functionality, robustness
and product-market fit. The count is only a signal. The goal is structure: no module reads another's private state,
no product code is imported into the engine, each module has one job. Growth stays visible, and the core can later
shrink onto a small device (section 27).

Repository layout, in short:

| Path | Holds |
|---|---|
| `src/exo/core/` | types, turn loop, context, the agent log and threads, model router and transports, tool registry and built-in tools, background jobs, delegation, ingress and peers, approvals, scheduler, trace and findings, hooks, plugins, slots, seats, skills, an MCP client |
| `src/exo/protocol/` | the protocol schema, a client, and generated JSON Schema and TypeScript types |
| `src/exo/server/` | `exod`: the dispatcher, the event hub, three transports |
| `src/exo/*.py` | generic modules: ledger, store, retrieval, gate, capture, consolidate, taste, ask, eval, brief, onboard, handoff, upstream |
| `src/exo/learn/`, `src/exo/wire/` | the learning loop, and the agent wire codec (section 16) |
| `clients/tui/` | the terminal client |
| `plugins/` | six plugins: memory, context, gate, bus, command centre (which holds the web client), orchestra |
| `templates/` | an empty principal store with a generic prior |

### 12. The core daemon and its event protocol

#### 12.1 Why a daemon

One process owns all state that matters: sessions, running turns, tools, plugins. Clients render and send
input. This split has three effects. Every client gets the same behaviour (retries, routing, gates,
compaction) without reimplementing it. A client can crash or close without losing work. And a new kind of
client, such as a device with a two-line screen or no screen, needs only to speak the protocol.

#### 12.2 Commands, responses, events

The protocol has three envelopes, all JSON, all defined once as Python dataclasses in
`src/exo/protocol/schema.py`. A script generates JSON Schema and TypeScript types from them, so clients in
other languages agree with the server by construction.

- **Command**: `{id, type, body}`. A client asks for something.
- **Response**: `{id, ok, result, error}`. The direct answer to one command, matched by `id`.
- **Event**: `{id, seq, session, type, ts, body}`. Something that happened. `seq` is the event's number.

Decoding is strict. An unknown command type fails with `unknown_command`. An unknown or missing body field
fails with `bad_body`. Clients may decode events loosely, dropping fields they do not know, so an older
client keeps working against a newer server. The handshake (`hello`) carries `protocol: 1`. Within a version,
changes are additive only.

The surface is small on purpose. A registry decorator refuses to register more than 30 command types or 30
event types, and tests enforce it. For contrast, the terminal gateway of the harness Exo learned most from
has 257 methods. When a feature needs more, it adds fields or event types, not one-off methods. The cap is
now tight, and it has shaped the design: peers got one command with four actions instead of four commands,
and features such as `/trace`, `/context`, `/compact`, `/handoff` and `/grants` are named commands run
through the generic `command.run`, with no new protocol type.

Today the schema defines 30 commands and 28 events:

| Group | Commands |
|---|---|
| Handshake and stream | `hello`, `events.subscribe` |
| Sessions (threads) | `session.create`, `session.list`, `session.get`, `session.fork`, `session.resume`, `session.undo` |
| Turns | `turn.send` (mode `send`, `queue` or `steer`), `turn.interrupt` |
| Answers and feedback | `approval.respond`, `approvals.list`, `question.answer`, `feedback.send`, `ui.respond` |
| Commands | `command.run`, `commands.list` |
| Lookup | `search`, `models.list`, `models.set`, `tools.list`, `skills.list`, `tasks.list`, `seat.list` |
| Config, taste and schedule | `config.get`, `config.set`, `taste.list`, `taste.edit`, `schedule.edit` |
| Other agents | `peers` (list, send, deliver, receipt) |

| Group | Events |
|---|---|
| Sessions | `session.created`, `session.updated` |
| Turns | `turn.started`, `turn.completed` |
| Model output | `message.delta`, `message.completed`, `reasoning.delta`, `usage.updated`, `model.call` |
| Tools and jobs | `tool.started`, `tool.output`, `tool.completed`, `job.updated` |
| Approvals and questions | `approval.requested`, `approval.resolved`, `question.asked` |
| Context and memory | `context.compacted`, `memory.summarized`, `handoff.written` |
| Work and learning | `task.updated`, `decision.queued`, `taste.updated` |
| Panels and peers | `ui.panel`, `peer.message`, `peer.delivery` |
| General | `notice`, `harness.error`, `error` |

Not every type is live yet. `question.answer`, `taste.list` and `taste.edit` return a typed `unsupported`
error until their subsystems land. The core emits every event above except `question.asked`,
`handoff.written`, `decision.queued` and `taste.updated`.

#### 12.3 Ordering, durability and replay

A single *hub* inside the server numbers every event. It takes the next `seq`, writes the event to the
`events` table in `sessions.sqlite`, and only then hands it to live subscribers. Three properties follow:

1. `seq` rises strictly across the whole stream, and events of one session stay in order.
2. Nothing is ever shown that was not first saved. A crash cannot produce an event a client saw but the
   record lacks.
3. Replay is a database read. `events.subscribe` with `since=N` returns every event numbered above N, then
   continues live. Over HTTP, the standard `Last-Event-ID` header does the same, so a browser's built-in
   reconnect resumes in place.

The acceptance test for wave 1 checks this end to end. It runs a three-turn session through `exod` and the
line client (read a file, edit it, run a shell command), kills the server with `SIGKILL` in the middle,
restarts it, resumes the session, and then confirms that the numbering is dense across the restart, that two
clients replaying from zero receive byte-identical events, and that the database rows match what clients saw.

#### 12.4 Transports

One dispatcher, three ways in:

- **HTTP with Server-Sent Events over a Unix socket** (the default). `POST /command` carries a command and
  returns a response. `GET /events?since=N&session=S` is a Server-Sent Events (SSE) stream: one long HTTP
  response that the server keeps writing events into. A comment line every 15 seconds keeps idle connections
  alive. `GET /health` reports the protocol version and the last `seq`.
- **The same over TCP**, for other devices on the same machine or network.
- **JSON lines over standard input and output**, so a client can start the core as a child process and talk
  to it through a pipe.

The schema does not change between transports. A remote transport over the Exo bus or a tunnel is planned
and uses the same envelopes **[designed, not built]**.

#### 12.5 Security of the socket and the token

- **Unix socket.** A Unix socket is a file-like endpoint on the local disk. The server creates its directory
  with owner-only permissions, binds the socket under a restrictive file-creation mask so there is no moment
  when it is open to others, and then sets it to mode `0600`. Only the same operating-system user can
  connect, so the socket needs no token.
- **TCP.** The server refuses to start a TCP listener without a bearer token. The token is 32 random bytes,
  URL-safe encoded, written to a file created exclusively with mode `0600`, or taken from an environment
  variable. Every HTTP request must carry `Authorization: Bearer <token>`. The comparison is constant-time
  (`hmac.compare_digest`) so response timing does not leak how much of a guess was right.
- **Input limits.** Command bodies over 8 MB are refused. Malformed input gets a typed error, never a crash.
  An exception inside the engine becomes an `internal` error response and the connection stays up.
- **What is missing.** The TCP transport has no TLS today. It is meant for localhost or a trusted local
  network. Encrypted remote access goes through the bus or a tunnel, which is not built yet.

#### 12.6 The engine behind the dispatcher

The server talks to an `Engine` interface with one method, `handle(command, hub)`. Long work, such as a
turn, starts in the background, returns quickly (`{"accepted": true}`), and reports progress as events. The
real engine (`exo.core.engine`) wires together the router, tool registry, loop, session store and plugins. A
tiny echo engine exists for protocol tests.

#### 12.7 One way in: ingress and peers

Anything that does not come from the principal's own keyboard passes through one path, the *ingress*. It
takes four facts (source, sender, trust, body) and runs three steps:

1. **Policy.** The receiving seat's gate policy answers `auto`, `hold` or `deny`. A held message waits for an
   approval card for up to 30 minutes and then expires. A held message never enters the model's context.
2. **Delivery.** If a turn is running, the message is *steered* in at the next tool round. If the thread is
   idle, it is *queued* for the next turn. It *wakes* the thread (starts a turn by itself) only if the seat
   allows it, and by default it does not while a person has a client attached.
3. **Record.** A receipt goes back to the sender (delivered, held, released, denied or expired), and an event
   and a trace row record what happened.

The first source is *peers*: live Exo sessions on one machine that message each other. The idea comes from
Claude Code, where one running session can tell another what it is doing, for example to avoid editing the
same file. Each running `exod` writes a small presence file (owner-only, removed on exit). A message is an
envelope signed with the sending seat's key, and it names sender, target and a one-time number inside the
signed part, so the receiver can check the signature, the age and that it is not a replay.

| Sender | Default |
|---|---|
| the principal, typing from a client | auto |
| another session of the same principal on the same machine | auto |
| a different or unknown principal, or a remote seat | hold |
| a bad or missing signature | deny, and this cannot be changed |

The receiver sees the text framed as a message from a teammate, never as the user. A peer cannot approve a
gate, change settings or speak for the principal. A message that tries (it names an open approval or asks
to approve one) is refused and shown to the principal, and `approval.respond` from a peer client is refused.
The `exo-bus` plugin carries the same module for Hermes Agent sessions, so a Hermes agent and an Exo agent
can message each other. Held messages live in memory today, so a restart drops them.

The second source is the end notice of a background shell job (section 15.5). Chat channels, the scheduler
and external event feeds are meant to plug into the same path, each bringing only its transport.

### 13. The model router

#### 13.1 Providers and transports

Exo talks to models through two transports, lifted from Hermes Agent and trimmed: the Anthropic Messages API,
and the OpenAI-compatible chat API. The second covers OpenAI, OpenRouter and local servers such as llama.cpp,
Ollama, LM Studio and vLLM. Both stream. Both turn the provider's stream into one internal event shape:
`text` and `reasoning` deltas, whole `tool_call`s (the transport assembles argument fragments), `usage`, and
exactly one final `done` or `error`. An error carries the HTTP status, a `retryable` flag and any
`retry_after` the provider gave.

Which providers exist, their base URLs, and which environment variable holds each key are data, in
`catalog.toml`, not code. A provider may list several key variables. Each becomes a separate *credential*
that the router can rotate through.

A third kind of provider is a vendor's own command-line tool, run as a subprocess (`claude-cli`, `muse-cli`).
It exists for subscriptions that can only be used through the vendor's login. The login token stays with the
vendor's program, in the OS keychain, and Exo never touches it. The price is honest: one process per turn, no
token stream, and for `muse-cli` no tool calls, so the router skips it when a turn needs tools.

#### 13.2 Roles

Code never asks for a model by name. It asks for a *role*:

- `main`: the model that runs the conversation.
- `aux`: cheaper side work, such as summaries and titles.
- `decision`: small structured choices (section 14).
- `eval`: the judge in the learning loop. Empty by default, which means the `main` route.
- `fast`, `standard`, `deep`: three *tiers*, cheapest to most capable, used for subagents and scheduled
  runs (section 16.1). The Anthropic defaults are Haiku, Sonnet and Opus.

Each role is an ordered list of routes, written `provider:model`. If a role is empty or every route fails,
resolution falls back: `decision` to `aux` to `main`, `aux` to `main`, and each tier toward `main`. A tier
whose provider has no key is skipped, so a setup with one local model runs everything on it and nothing
breaks. Exo is provider agnostic: every role is the user's choice, the user's config overrides the catalog,
and `models.set` changes a role at run time. Keys come from environment variables, then the operating
system's keychain, then one owner-only key file, and never from chat.

#### 13.3 Fallback

For each route in the chain, the router picks the first credential that is not cooling down, and streams.
Two rules govern what happens on failure:

1. **Fall back only before output starts.** If the provider fails before sending any text, reasoning or tool
   call, the router quietly tries the next credential, then the next route. Once output has started, the
   error goes to the loop, because silently switching models in the middle of a reply would splice two
   different answers.
2. **Do not fall back on a bad request.** HTTP 400, 413 and 422 mean the request itself is wrong (malformed,
   too large). Another route would fail the same way, so the error goes straight back.

When everything is exhausted, the router returns one error that says why, and when the next route reopens.

#### 13.4 Cooldown

A failed credential is set aside for a while. How long depends on the failure:

| Failure | Cooldown |
|---|---|
| 401 (bad key) | 5 minutes |
| 402 (billing) | until the provider's stated reset, else 4 hours |
| 404 (model not found) | 4 hours, for that model on that credential only |
| Rate limit, credential has siblings | the provider's reset if given, else 60 s doubling per strike, capped at 4 hours |
| Rate limit, credential is the only one | see below |

The last row needs care. When a provider says "try again in 6 hours" and you have only one key, waiting the
full 6 hours is often wrong: limits are frequently lifted early. So a lone credential is probed again after
`max(60 s, 2% of the remaining window)`, never later than the stated reset, and each failed probe re-stamps
a shorter window. This rule came from a fix contributed to Hermes Agent and was written into Exo's router
from the start.

#### 13.5 Cost

The catalog carries list prices per million tokens for known models, and the user's config can add or
override a price. When a provider reports token usage without a cost, the router computes one from the price
of the route that actually served, counting cache reads and cache writes at their own rates. Some providers
count cached tokens inside their input total, so only the uncached part is charged at the input rate. An
early version charged those tokens twice. Usage flows to clients as `usage.updated` events with a running
session total, and each call's cost goes into the trace (section 23).

A route with no known price costs *unknown*, never `$0`. A turn with some unpriced calls shows the sum of the
priced ones followed by a plus sign. Subscription plans with no per-token price read unknown until the user
sets one. The reason is simple: a zero looks like a free call, and a wrong zero hides spend that is real.

### 14. Decision models

**Status.** The `decision` role, the typed probability-returning interface, the Jev and Laya providers and
the gate's tighten-only use of them are **[built]**. Calibration, `decision` ledger entries, panel and routing
decisions, and exo-decide are **[designed, not built]**.

#### 14.1 What a decision model is

Many choices inside a harness have a fixed set of answers. Which of these 12 tools does this turn need?
Should a panel open now, and which one? Is this message a correction? Is this action public? A generative
model can answer such questions by writing text, but it is slow and costly for the job, and its answer comes
with no usable measure of how sure it is.

A *decision model* takes a short text (the "state") and typed questions, and returns probabilities instead
of prose. Three question types are shared by the products available today: *choice* (pick one of N options),
*score* (a level on an ordered scale), and a yes-or-no *predicate*. It does not write text, so it never fills
in tool arguments. It picks which tool, which panel, which label. A generative model still writes the
arguments, drafts and plans.

#### 14.2 Where they fit

The rule of thumb: if the answer is a pick from a known list, a yes or no, or a level, a decision model fits.
If the answer is text, it does not. Exact rules stay in code.

| Mechanism | Fits | Question shape |
|---|---|---|
| Opening a UI panel | yes | yes or no "would a panel help now?", then a choice over panel types plus `none` |
| Model routing | yes | choice over task classes, within models the user approved |
| Gate assist | yes, tighten only | yes or no per risk: public? irreversible? spends money? |
| Learning-loop classification | yes | signal or noise, type, domain, and whether it agrees, conflicts or is new |
| Tool shortlist | partly | which toolsets this turn needs (arguments stay with the main model) |
| Reflection, critique, eval judging, drafts | no | text |

#### 14.3 Tighten only

A decision model may move an action toward "ask", never toward "allow". Rules in code run first, and anything
guarded (irreversible, public, financial, third-party) never depends on a model's answer. This keeps a
miscalibrated or manipulated model from opening a door that code had closed.

#### 14.4 Calibration, fallback and logging

Each vendor computes "confidence" with a different formula, and one independent test of a hosted product
found it right far less often than its stated confidence implied. So Exo plans to gate on the probability of
the chosen answer after per-provider, per-mechanism *calibration*: fitting a correction on the user's own
labelled decisions so that "0.9" really means right about 9 times in 10. The raw vendor number is kept in the
log but not used.

Each call runs in this order. Rules come first. Then providers are tried in the user's order, each with a
short timeout (planned defaults: 0.5 s local, 2 s hosted). A timeout, rate limit or network error moves to
the next provider. Below the threshold, Exo escalates to the main model, to the person, or to a safe default (no panel, keep the current model, ask before
acting). Abstaining is a normal outcome. The turn never blocks on a decision model.

Every decision is planned as a `decision` ledger entry holding hashes and probabilities, not raw text, so the
signed ledger can travel without carrying private content. Later signals that refer to it (the person opened
a different panel, undid the action, accepted the result) become its labels. That is what lets the learning
loop and the eval score each mechanism and each provider on the user's own data.

#### 14.5 Defaults: Jev first, Laya local

The first plan made Laya the default: an open-weight model (Apache 2.0) small enough to run on a laptop,
free and private. A live check changed that. Asked "Is deleting a production database reversible?", base
Laya said yes with probability 0.62. TypeSafe's Jev said no with 0.80. Laya's own server also warns that its
confidence is not calibrated, and its model card reports 0.362 against a random baseline of 0.318 on its
typed-decisions set without fine-tuning. A gate cannot lean on that.

So the default `decision` route is now Jev, then local Laya, then `aux`, then `main`. Jev is a hosted
service, so it is used only when the user has set a TypeSafe key. Without one, the role falls through to
Laya, and without Laya to the chat models, which answer the same typed question as JSON. A hosted decision model sees the
state it is asked about.

Laya can take the default back. Exo plans to ship a fine-tuned version, "exo-decide", trained on a generic
seed set and, per user, on that user's own verdicts. It becomes the default only if it beats Jev on a
held-out set.
Licence terms shape the data: TypeSafe's terms forbid training other models on its outputs,
so those outputs are never training labels. Weights stay private until they beat base Laya and Jev on a
held-out set. None of this is measured yet.

### 15. Tools, hooks and gates

#### 15.1 The registry

A tool is a `ToolSpec` (name, description, JSON Schema for its arguments, a `risk` hint of `low`, `medium` or
`high`, and a toolset name) plus a handler and an optional availability check (for example, "is this binary
on the PATH?"). Toolsets let a session or a subagent narrow what the model sees. The registry never raises
for a tool failure: it returns a result marked as an error, so the model sees what went wrong and the turn
continues. Results over 100,000 characters are cut. Handlers that block run in a worker thread so a slow
shell command never stalls the server.

Built-in tools today **[built]**:

| Tool | Risk hint | Notes |
|---|---|---|
| `read_file` | low | |
| `glob`, `grep` | low | |
| `write_file`, `edit_file` | medium | edit uses a fuzzy matcher lifted from Hermes: a chain of strategies that tolerate whitespace and escape drift, keep indentation and Unicode |
| `apply_patch` | medium | a multi-file patch format. All hunks are applied to an in-memory copy first and written only if all succeed |
| `bash` | high | timeout, working directory, capped output. On timeout the whole process group gets TERM, a grace period, then KILL. `background: true` starts a job (section 15.5) |
| `jobs`, `job_output`, `job_kill` | low, low, medium | list, read and stop background jobs |
| `code_find` | low | answers "where or how is X done?" with files and verbatim excerpts (below) |
| `delegate` | low | hands tasks to subagents (section 16.1) |
| `ui` | low | opens a panel (section 24) |
| `exo_trace`, `exo_harness_report` | low | read the trace, file a finding (section 23) |

The risk hint is information, not a decision. Deciding is the gate's job.

Shell commands run with a filtered environment: provider keys, Exo's own keys and any variable whose name
ends in `_TOKEN`, `_SECRET`, `_PASSWORD` or `_API_KEY` are removed unless the seat lets a named one through.
A shell reports only the exit code of a pipeline's last stage, so `false | head -1` looks like success. Exo
notices when an earlier stage failed and says so in the result.

`code_find` exists because "where is X decided?" costs an agent many rounds of search and read. It runs
jevgrep, an open-source tool that asks the Jev decision model to pick relevant files and excerpts. The key
never appears in the command line, the environment or a log. On four questions about Exo's own repository it
found the right files each time with about half the input tokens of a small subagent using grep and read, but
it was slower and no more correct. A native version waits for a measured win on larger, messier code.

#### 15.2 Hooks

Named hook points let plugins watch and steer. Their names are familiar to Hermes plugin authors, but Exo
accepts only hooks its core actually fires. These six are supported today **[built]**:

- `pre_tool_call`: may return `block` (the model receives the reason as an error result) or `approve` (ask
  the person first).
- `post_approval_response`: told what the person chose.
- `post_tool_call`: sees the result.
- `transform_tool_result`: may rewrite the result text.
- `on_turn_start` and `on_turn_end`: bracket each turn.

Names declared in Hermes contracts or earlier Exo versions for model calls, session lifecycle, compaction,
commands, subagents, gateway and auxiliary calls, kanban, and skills are known but not fired by Exo. The
default registry rejects these registrations so plugins cannot silently subscribe to a hook that never runs.
A failing supported hook is logged and skipped, so one broken plugin does not break a turn. A block decision
is still honoured.

#### 15.3 The approval flow

When a hook asks for approval, the engine publishes `approval.requested` with a request id, a summary, the
risk, the tool call, the list of who may answer (`approvers`) and a `binding` hash, and waits. A client
answers with `approval.respond`: `allow`, `deny` or `allow_always`. The engine publishes `approval.resolved`
and continues. Because the question is an event, a terminal, a web page, a phone or a device can answer it,
whichever the person has at hand. Four rules make the answer mean what it says:

1. **Bound to exact text.** For an outward act (a post, message, email, payment, account change, or anything
   public or irreversible) the gate hashes the exact text, title, target, tool and arguments. That hash is
   the `binding`. An answer covers that hash only, so an edit of one character is a new ask. A client may
   echo the binding. If it differs from the open request, the answer is refused as `stale`, so a person who
   read an older draft cannot pass a newer one. Hashing all the arguments, not only the text, means nothing
   else can change under an approval either.
2. **Who may answer.** A policy file maps each kind of act to `principal`, a named seat, or a role that lists
   seats. The default is the principal only. The principal can always answer, because their consent is the
   root. Anyone else is refused as `forbidden`.
3. **Signed seat answers.** A seat answers as itself and must sign the answer with its Ed25519 key. The
   signature covers the request id, the decision, the binding hash and the seat name, so it cannot be reused
   for another request, decision or text. An unsigned seat answer is refused. A principal answer is not
   signed: it is as strong as the socket's file permissions, which admit only the same operating-system
   user.
4. **Scoped, expiring grants.** `allow_always` becomes a *grant*. Its scope is the kind of act and the exact
   target, and for an outward act also the exact text hash, so one approval can never cover a later, different
   text. It expires (30 days by default, 7 for outward acts), each use is logged, and creating or revoking it
   writes a ledger row. Grants live in the principal store, not in the daemon's memory, so they survive a
   restart and hold for every seat of that principal. Only the principal can create one: a seat's
   `allow_always` is refused. `/grants` lists them and `/grants revoke` removes one. Credentials never get a
   grant.

#### 15.4 The gate

The gate (`exo.gate`, about 1,300 lines, stdlib only) is two pure steps. `classify` looks at a tool name and
its arguments and places the action on four axes: reversibility, exposure, cost, and effect on third parties.
It parses shell commands into their parts (pipes, `&&`, heredocs, `sudo` and `env` prefixes) and knows the
common cases, for example that `git push` to a shared remote is public, that `gh pr comment` writes where
others read, and that an HTTP POST to a non-local host leaves the machine. `decide` then maps that cell
through the person's policies: a gate matrix, scopes, overrides, reserved and denied actions, budget and
posting checks. The outcome is allow, log, ask or deny.

Under the person's policy sit two *floors* that no policy, rule, grant or scope can lower. The operator
floor: an irreversible public act that touches a third party always asks. Amplifying a person is neutral. It
would amplify a bad actor too, so rules that protect other people sit outside the model of the person. The
credentials floor: logins, key files, password stores and anything else that handles secrets always ask the
principal. After the rules, the gate asks the decision model whether the act is riskier than the rules
thought, and may only move the outcome toward ask (section 14.3).

The last check is the *outbound scrub*. A post to a public target is swept for internal identifiers: seat and
agent names, home folder paths, model ids from the catalog, internal ids of sessions, tasks and jobs, and any
names the user lists. A hit holds the post and lists each one. A plain one-word name matches only as a whole
word, so a seat called "scout" does not hold every post that mentions a scout. A distinctive name, with a
hyphen, underscore or digit, also matches inside longer words and with any separator. The sweep leans toward
holding, because the costs are lopsided: a false hit costs one question, a miss leaks a name for good. The
rule came from a real slip, when a public repository shipped with task ids, profile names, model strings and
a home path in it.

The gate is built and tested as a library and as the `exo-gate` plugin, which runs it on every tool call on
the core and, when a principal store with a signing key is attached, writes each decision to the signed
ledger.

#### 15.5 Background jobs

A build or a long test run should not hold a turn. With `background: true`, `bash` returns a job id at once
and the agent carries on. Output goes to an owner-only log file that rotates at 5 MB. A job belongs to the
agent, not to the turn: stopping the turn (ctrl+c or `/stop`) never stops the job. When the job ends, the core writes a
short notice (exit code, time, the last lines of output, where the full log is) and sends it through the
ingress path (section 12.7). It is steered into a running turn, queued for an idle one, or wakes the thread
if the seat allows. The notice is framed as data from the harness. It is never the user, never counts as the
principal's words, and cannot approve anything.

Limits are per agent: 4 jobs at once, 2 hours of wall time, 5 MB of output. Jobs end with `exod`. If
`exod` dies without closing, the next start reports the jobs as `lost` and leaves the processes alone,
because an operating system can reuse a process id, and killing by a stale id could hit an unrelated
program. A background command is gated exactly like a foreground one, and a test compares the two.
Subagents cannot start jobs, since their threads end with their report.

### 16. The agent loop

`exo.core.loop` runs one turn as a short series of steps. Everything it needs (the model, the tool runner,
the memory provider, the context engine) is injected, so the loop imports none of them and each can be tested
alone.

1. Close any tool call left dangling by a crash with an error result, so the transcript stays valid.
2. Store the user message and emit `message.completed`, then `turn.started`.
3. Build the system message: the base prompt, the memory provider's pack block, the thread's working folder
   and the agent's summary tree. Memory recalled for this message goes in a separate message just before it,
   not in the system message (section 17.1 explains why).
4. Build a fresh model input from the context engine (section 17).
5. Stream one model call. Text arrives as `message.delta`, reasoning as `reasoning.delta`. Usage arrives as
   `usage.updated`, and a `model.call` event records the route asked for, the route that served, every
   attempt and the timings (section 23).
6. Store the reply (`message.completed`). If it asked for tools, run them in order, each with
   `tool.started`, any `tool.output`, then `tool.completed`, and store all results as one tool message. Go
   back to step 4.
7. When the model stops asking for tools, emit `turn.completed` with the stop reason, usage and a timing
   summary, tell the memory provider about the exchange, and emit `session.updated`.

Limits and edge cases:

- **Iteration cap.** At most 24 tool rounds per turn by default. After that, the loop emits a
  `notice` and stops.
- **Retries.** A retryable model error is retried up to 3 times with exponential backoff, but only while
  nothing has been shown. Once text has streamed, retrying would show the person a different answer on top
  of the first.
- **Interrupt.** `turn.interrupt` stops the stream or the running tool. Partial text is kept, marked as
  interrupted. Every tool call still gets a result ("interrupted before completion"), so the transcript the
  model sees next time is well formed.
- **Steer and queue.** While a turn runs, `turn.send` with `mode: steer` slips the new message in between
  tool rounds. A steer that lands while the final reply is being written gets its own model call in the same
  turn, so it is never dropped. `queue` waits for the turn to end.
- **Fail soft.** A memory provider or context plugin that throws is ignored for that call, and a
  `harness.error` event records it. An unexpected error inside the loop ends the turn with `error` and a
  clean `turn.completed`, never a hung session.

#### 16.1 Subagents and the agent wire

A subagent is a child thread on the same core, started by the `delegate` tool with one goal or a list of
tasks. It runs on the same router, tools and gate, so every approval and hook applies to a child's calls
exactly as to the parent's. A child never gains a tool its parent lacks, and tools that need a person (ask,
clarify, send a message, schedule a job, open a panel) are blocked for it. Limits, by default: one level of
nesting, 10 tasks per call, 4 children at once per level, 24 tool rounds, 200,000 tokens and 15 minutes per
child. Each level has its own pool of slots, so a parent waiting on its children can never starve them.
Interrupting the parent cancels every child below it. The parent gets back a *receipt* in plain English:
outcome, what was done, the model that ran it, and why it failed if it did.

**Cheapest model that can do the job.** Each child gets a tier: a scout (reading, searching) runs `fast`, a
builder or verifier `standard`, a critic `deep`. The parent may set a tier per task. A child never runs above
its parent's tier unless the user allows it. A child that fails is retried once a tier up, as a separate
task with its own receipt. Most delegated work is retrieval and simple building, which a small model does
well at a fraction of the price. Judgment keeps its tier, and a failure buys one stronger try.

**Lines for machines, English for models.** Briefs and receipts are stored as compact typed lines in the
SYM-2P format: one packet per line, fields by position, small numeric codes for roles, acts and states, and a
nested symbolic body. Code writes, checks, signs and replays these lines. Models never read or write them.
A fixed renderer turns each packet into the same English every time, and that is what a model or a person
reads. The reason is a measurement with real tokenizers. Lines save 30 to 48% of bytes against JSON, but not
model tokens: a receipt is 121 tokens as a line and 83 as English, and a model would also need a 193-token key
to read lines at all. So the wire's value is not fewer tokens. It is that every brief and receipt is a typed
packet that validates strictly, carries limits the harness enforces, can be signed, and replays exactly.

### 17. Context and the agent's memory

#### 17.1 A fresh call every turn, one memory per agent

Exo does not keep a growing transcript. Every model call is built new from older history made of summaries
and the *verbatim tail* of recent messages. This idea comes from the OptChat design and its production form,
UniiChat (section 33). Exo reimplements the idea. It copies no code or prompt text from either.

The unit of memory is the agent, not the window. A seat is one agent with one append-only log and one
summary tree. Windows, channels, scheduled runs and peer messages are *threads* of it. Each message in a
thread also takes the next position in the agent's log, and each log entry stores a hash over the previous
entry's hash and its own content, so `exo memory verify` can check the log end to end. Undo hides entries
instead of deleting them, so the log stays append-only.

A model call for one thread has four parts, in this order:

1. **System message.** The base prompt, the memory provider's block, the thread's working folder, then the
   agent's summary tree across all threads, oldest first. It ends at the last finished summary, so it is
   byte-identical for every thread of the agent in the same folder until a new summary lands.
2. **This thread's tail.** Every message of the thread after the tree's frontier, and at least its last 12.
3. **Notes from other threads.** Before a call, what other threads did since this thread last looked is
   written into this thread as one compact note ("In thread "tui 2", 4 min ago: ..."). The note is stored, so
   the next call sends the same bytes. A new thread's first note covers everything after the frontier, so
   a second window knows what the first just did on its first turn.
4. **Recall.** What the memory provider recalls for this message, placed just before it and not stored.

The order is about the provider's *prompt cache*. Providers charge much less for the part of a request that
starts with exactly the same bytes as a recent one. So everything that changes rarely goes first, and
everything that changes each turn goes last. Recall used to sit in the system message, where it changed
every turn and broke the cache for the whole prompt. Providers that key their cache on a conversation id get
one id per agent, so every window shares the cached prefix. A thread marked private is logged but never
enters the tree, other threads' notes or search. Subagents keep their own transcripts outside the log, and
their result reaches the parent as the delegate receipt.

One process writes an agent's log: it holds an operating-system file lock for that agent. A second window
for the same seat joins the first window's core instead of starting a second one, and takes over if the
first closes. Two threads may run turns at the same time. Log positions are assigned inside one database
write, so they never collide, and each thread sees the other's entries at its next call through a note.

#### 17.2 The summary tree

<!-- fig:tree -->
```
 level 2        [ summary of messages 1-32 ]
                 /                        \
 level 1   [ 1-16 ]                    [ 17-32 ]
            /    \                      /     \
 level 0  [1-8] [9-16]             [17-24] [25-32]   [33-40]      tail: 41-52 verbatim
```
*Figure 2. The summary tree plus verbatim tail. Leaves cover 8 messages. When the view is over budget, the
oldest neighbouring pair at one level merges into a parent.*

The rules, with today's defaults **[built]**:

- Log entries older than the tail (the last 12) are cut into leaves of 8. Each leaf is summarized once, and
  its text labels each entry with its thread.
- A node at level L covers 2 to the power L leaves. Addresses are `(level, index)`, so a node's children are
  `(L-1, 2i)` and `(L-1, 2i+1)`.
- When the summaries in the view total more than 6,000 tokens (estimated as characters divided by four), the
  oldest adjacent pair of finished nodes at the same level is merged into its parent.
- The view shows the coarsest finished nodes that tile the summarized region, oldest first, each labelled
  with the range it covers. Anything after the last finished node stays in the recent part, word for word,
  so nothing is ever missing.
- A span too small to be worth summarizing stays verbatim, and a summary that is not shorter than its source
  is dropped.
- The view is introduced to the model as background, not instructions, and its header asks the model
  to say in its reply anything it learned that will matter later, since summaries keep little tool output.

#### 17.3 Lanes and settling

Summaries run in parallel in two *lanes* with separate limits: live chat (8 at once) and imported history (2
at once). A large import, such as a year of past chats, therefore never starves the live conversation, and
imports can be paused and resumed. Before building a turn, the context engine *settles*: it waits, up to 60
seconds, until no live summary is pending. On timeout it proceeds, using verbatim text for the unfinished
part. Interrupted summary jobs are retried when the session is next touched, including after a
restart. A failed job is retried with backoff, at most three times; then its span stays verbatim and one finding
is recorded (D-041). Each finished node is reported as a `memory.summarized` event. It is upkeep of the long-term memory,
not a compaction of the live context, so it is kept apart from `context.compacted`.

#### 17.4 The summarizer

The summarizer is any model on the `aux` role. Its prompt wording is lifted from Hermes Agent's compressor
and trimmed. It tells the summarizer that the turns are data to summarize, never instructions to follow. It
must replace any secret it sees with a placeholder, keep the user's language, and keep paths, commands,
errors and values exact. It must also record the user's latest unfinished request precisely, including a
request to stop or undo, so a cancelled task is not carried forward. And it must copy every instruction,
correction, preference and decision of the principal word for word into a section of its own, which merges
keep whole.

A prompt is a request, not a guarantee, so the harness checks it. After a leaf is summarized, the harness
finds the principal's rulings and corrections in that span with the same rules the learning loop uses for
capture. Any whose words are missing from the summary are appended under a heading that says the harness
restored them. Before a leaf that holds principal messages is summarized, the span is offered to the memory
provider's `capture_principal`. The memory plugin writes the rulings, corrections and open decisions to the
signed ledger, each with the agent reply it answers, so a correction keeps its target. The leaf records how
many principal messages it held and how many were captured. A *principal message* is a live message typed
by the principal: not a peer message, a job notice, an import, a scheduled prompt or a bare "ok".

Two commands make this visible. `/context` shows what the next call will carry, part by part, with token
estimates, and how many principal messages are not yet captured. `/compact` captures every uncaptured
principal message first, then summarizes everything eligible at once.

#### 17.5 Threads in SQLite

One `sessions.sqlite` file per Exo home holds threads (still called sessions in the protocol), messages, the
agent log, events, summary nodes, background jobs, findings and a full-text index. It runs in write-ahead-log
mode so readers do not block the writer. When the one-memory model arrived, existing sessions were imported
into their agent's log once, in time order, and nothing was deleted.

- **Search.** Every message's text, tool calls and tool results go into an SQLite FTS5 index. `search`
  returns matches with highlighted snippets. Input is quoted, so any text is a safe query.
- **Fork.** `session.fork` copies a session's messages up to a chosen message into a new session and
  records the parent and fork point, so lineage can be walked back.
- **Resume.** `session.resume` reattaches after a restart: it repairs a half-finished tool round and restarts
  pending summaries.
- **Undo.** `session.undo` hides the last N turns from the conversation and from search. A summary already
  made keeps them. It does not roll back files on disk. Snapshot undo of files is not built.

### 18. Plugins and slots

A plugin is a folder with a `plugin.yaml` manifest and a Python `register(ctx)` function, or an installed
package that advertises an `exo.plugins` entry point. This is the same format Hermes Agent uses, kept on
purpose so that Exo's six existing plugins load on the new core with import changes only.

The context object offers `register_tool`, `register_hook`, `register_command`, `register_memory_provider`,
`register_context_engine`, `register_channel` and `register_skill`. If `register` raises, everything that
plugin registered is rolled back and the error is kept on its record. One bad plugin never stops the core.

Two registrations are *slots*: exclusive seats that only one plugin can hold at a time.

- **Memory provider.** Supplies the pack block for the system prompt, recalls memory for each message, hears
  about each finished exchange, and may add its own tools.
- **Context engine.** May reshape the messages built for a turn and hears about usage after each turn.

The slot interfaces are lifted from Hermes, trimmed to the methods Exo's plugins use. A test loads all six of
Exo's plugins on the new core and checks that each registers cleanly and that the memory and context
plugins take their slots **[built]**. Since wave 2 all six do their full jobs on the core with no Hermes
imports: memory, context, the gate, the bus, the command centre and the orchestra of role templates. The same
wave added a skills loader, a client for MCP (the Model Context Protocol, a common way to plug outside tools
into an agent) and *seats*: one file per agent that names who it serves, its models, tools, skills, MCP
servers and plugins.

### 19. The principal store

**Status.** The version 1 store is **[built]**: a folder with a pack, a router, typed belief nodes, lint and
keyword search. The version 2 additions in this section (store kinds and nesting, the items table, derived
indexes, cross-store links) are **[designed, not built]**.

#### 19.1 One format, three kinds

Every store has the same layout and node schema. A manifest, `STORE.toml`, says which kind it is.

| Kind | Holds | Pack file |
|---|---|---|
| `principal` | one person: profile, beliefs, external items, ledger, policies | `PRINCIPAL.md` |
| `agent` | one agent's own memory: patterns, experiences, principles, charter | `ANIMA.md` |
| `group` | a team of agents' shared state: agreements, open tensions, practices, project state | `NEXUS.md` |

Agent and group stores belong to a person, so they nest inside that person's store. One folder then carries
a whole person and their team, and that folder is the unit that moves to a new machine or a device. Nested
stores share the parent's ledger and items table, and each ledger entry records which scope wrote it.

#### 19.2 Layers

| Layer | Format | Source of truth? |
|---|---|---|
| Pack | one Markdown page, under about 1,500 tokens, holding pointers to nodes | curated |
| Router | `INDEX.md`: a table of load rules ("when the task is about X, load these nodes") plus a generated node list | curated rules, generated list |
| Nodes | Markdown with frontmatter, 150 to 450 words each | curated, changed only through reviewed proposals |
| Items | rows in `items.sqlite`, metadata first | rebuildable from the ledger |
| Ledger | `ledger.sqlite`, signed and chained | yes, the root |
| Indexes | full-text, vector and link-graph files | no (derived, safe to delete) |

Three rules carry over from earlier knowledge-base work. *Hot path*: only the pack and router are always
loaded, and they hold pointers, never copies. *One fact, one home*: a fact about the person lives in the
person's store only. Agent stores link to it. *Machine numbers are truth*: counts live in data, and prose
cites them instead of restating them, because restated numbers drift.

#### 19.3 Nodes

Each node has required frontmatter: `id`, `title`, `type`, `domain`, `summary`, `load_when`, `confidence`
(high, medium, low), `stability` (core, durable, volatile), `valid_from`, `sources` and `links`. Types today
are trait, value, fact, preference, rule, episode, entity, procedure and question. Version 2 adds `pattern`
and `tension`. Links are typed: part-of, explains, evidence-for, contradicts, supersedes, applies-to,
instance-of, see-also, and in version 2 derived-from. A belief that is replaced is not deleted. It gets a
`valid_until` date and a `supersedes` link from its successor.

`exo lint` checks a store: missing or mistyped keys, an id that does not match its path, dangling links, a
missing pack or router, and *leak patterns* (phone numbers, API keys, tokens, passwords, private keys) in
any curated file. The empty template store lints with zero errors and zero warnings.

#### 19.4 Items

Items are things the person saved, such as bookmarks or notes **[designed, not built]**. Each row has a
deterministic id computed from its source (so re-running an import changes nothing), a normalized URL and a
content hash for de-duplication, a quality tier (curated, inferred, noise), and a review status. Every insert
also appends an `item` entry to the ledger, so the table can be rebuilt. Items are evidence, not beliefs: the
learning loop may propose a belief from many items, and the person reviews it like any other. Conversations
are not items. They enter the ledger as utterances, in the person's own words.

### 20. The signed ledger

**Status.** **[built]**, as `exo.ledger`, with tests. On the core, the memory plugin writes each turn's
words to it as `utterance` entries, and the gate writes its decisions and grants, when a principal store
with a signing key is attached. The agent log of section 17.1 is a separate, simpler chain: hashed but not
signed, and kept with the threads.

<!-- fig:chain -->
```
 +------------------+      +------------------+      +------------------+
 | event 1          |      | event 2          |      | event 3          |
 | prev: 000...000  |  +-->| prev: chain(1)   |  +-->| prev: chain(2)   |
 | id = sha256(...) |  |   | id = sha256(...) |  |   | id = sha256(...) |
 | sig  (Ed25519)   |  |   | sig  (Ed25519)   |  |   | sig  (Ed25519)   |
 +------------------+  |   +------------------+  |   +------------------+
   chain(1) = sha256(prev + id) ---+   chain(2) ---+
```
*Figure 3. Each event's `prev` is the chain hash of the one before. Changing any event changes its id, which
breaks every later link.*

Each event is one JSON document in a canonical form (sorted keys, no whitespace, UTF-8), so the same content
always produces the same bytes. Its fields:

| Field | Meaning |
|---|---|
| `id` | SHA-256 of the canonical event without `id` and `sig` |
| `ts` | UTC time to the microsecond |
| `author` | the person or agent that wrote it |
| `kind` | `utterance`, `ruling`, `verdict`, `action`, `artifact`, `ingest`, `question`, `answer`, `belief_proposed`, `belief_accepted` or `note` |
| `body` | a JSON object |
| `session` | session id, or null |
| `refs` | ids of earlier events this one refers to (each must already exist) |
| `prev` | the chain hash of the previous event (64 zeros for the first) |
| `pub`, `sig` | the signer's Ed25519 public key, and its signature over the SHA-256 of the event without `sig` |

The chain hash of an event is `sha256(prev + id)`. `verify()` recomputes every id, link, chain hash and
signature, and can also pin authors to known keys, so a valid signature by the wrong key is caught.

SQLite triggers abort any `UPDATE` or `DELETE` on the events table, so append-only is enforced by the
database, not by good manners. Appends run inside `BEGIN IMMEDIATE`, so several processes can write to one
ledger without racing for the chain head. Keys are stored in files with mode `0600`.

A `verdict` is the highest-value kind. Its body names the judged event and one of `accept`, `edit`, `reject`
or `should_have_been`, and the judged event is added to `refs`. This is the raw material of the learning loop.

Version 2 plans more kinds **[designed, not built]**: `item`, `retrieval` (which node and item ids went
into a prompt, which feeds use counts and evaluation), `decision` (section 14), and `state` and `lease` for
shared team state with revision checks, so two writers cannot overwrite each other.

### 21. Retrieval

**Status.** Keyword ranking with a budgeted pack is **[built]** (`exo.retrieve.assemble`, with a `Ranker`
seam). The staged pipeline below is **[designed, not built]**.

<!-- fig:retrieval -->
```
 query
   |
   v
 1 pack (always, cached, outside the budget)
 2 router pins (load rules whose trigger matches the query)
 3 candidates: BM25 top 50  +  embeddings top 50  -> reciprocal rank fusion
 4 filters: validity dates, privacy scope, never private corpus or archive
 5 graph expansion: one hop from the top 8, weighted by link type
 6 pack under budget (default 1,500 tokens): pinned, then ~70% nodes, ~30% items
 7 log a retrieval event
```
*Figure 4. The planned retrieval pipeline.*

The stages, explained:

1. **Pack.** Always first and prompt-cached (the provider keeps it so it is not re-billed at full price),
   outside the retrieval budget.
2. **Router pins.** Load rules in each `INDEX.md` whose trigger text matches the query pin their nodes, which
   enter first and in full. A human-written rule beats any ranking.
3. **Candidates.** Two ranked lists. *BM25* is a standard keyword ranking: a document scores higher the more
   query words it contains, rare words count more than common ones, repeating a word has diminishing returns,
   and long documents are not favoured for being long. Planned column weights: title 3, summary 2, load_when
   2, tags 1, body 1. The second list is embedding similarity (section 21.1). The two lists are combined by
   *reciprocal rank fusion*: each document's score is the sum, over the lists, of 1 divided by (60 plus its
   rank in that list). Fusion by rank avoids comparing two scores measured on different scales. Nodes get a
   1.5 times prior over items.
4. **Filters.** Drop beliefs past their `valid_until` unless the query is about the past. Drop private nodes
   for agent scopes. Never return raw private text or archives.
5. **Graph expansion.** From the top 8 nodes, walk one link. A `supersedes` link swaps in the newer node. A
   `contradicts` link pulls in the other side, because both views matter. Other links add their target at the
   parent's score times a weight: 0.5 for explains, evidence-for and applies-to, 0.4 for part-of and
   instance-of, and 0.3 for see-also.
6. **Pack under budget.** Pinned nodes first, then roughly 70% nodes and 30% items of what is left. A node
   that does not fit in full appears as its summary line. Items degrade from full card to title to bare URL.
   Every block carries its id, so the model can ask for the full node.
7. **Log** a `retrieval` event with the ids and token count.

#### 21.1 Local embeddings

The default embedding model is planned to be EmbeddingGemma 300M, which is built for phones and small boards
and handles many languages. Qwen3-Embedding 0.6B (Apache 2.0) is the alternative where Gemma's terms are not
acceptable or more strength on long text is wanted. Vectors are cut to 256 dimensions (both models are
trained so that a prefix of the vector is still a useful vector), normalized, and stored as 8-bit integers:
256 bytes per item, so 100,000 items take about 25 MB. Every vector row records its model, dimensions and
content hash, so unchanged text is never embedded twice and a model change triggers a clean rebuild. Search
is a flat scan in SQLite. At this scale no vector server is needed. A remote embedding service is possible
only as an opt-in that the person's policy must allow, because it sends personal text off the machine.

### 22. The learning loop

**Status.** The fused loop, `exo.learn`, is **[built]** (wave 3) on top of the earlier modules (`exo.capture`,
`exo.consolidate`, `exo.taste`, `exo.ask`, `exo.eval`): signals, the background reviewer, typed proposals,
promotion, revert, and a climb with the held-out gate. Clients send verdicts with `feedback.send` and panel
responses with `ui.respond`; panel responses are recorded as `ui_signal` ledger notes and are not yet
consumed by the learning loop. In a live acceptance run, one edit verdict produced three typed proposals.
**[designed, not built]**: the automatic nightly trigger, climb candidates other than adding or dropping one
lesson, and a "learned" section in the brief.

<!-- fig:loop -->
```
 live turn -> capture -> ledger
                           |
              reviewer (off the main turn, propose only)
                           |
                  proposals with evidence
             /             |                \
   auto-promote      eval gate on        question to
   (low risk,        held-out cases      the person
   strong evidence)        |
             \             |                /
                    accepted lessons
                           |
       views: pack, taste rules, skills, facts, retrieval
                           |
     slow loop at night: decay, merge, re-eval, revert
```
*Figure 5. One loop: capture, propose, test, keep or drop, show.*

#### 22.1 Signals

Every signal is a ledger event. Default strengths:

| Signal | Kind | Strength |
|---|---|---|
| Explicit rule ("from now on", "always", "never") | `ruling` | 1.0, no decay |
| Answer to a question Exo asked | `answer` | 1.0 for the node asked about |
| Correction in chat ("no", "I said", "instead") | `verdict: edit` or `should_have_been` | 0.8 |
| Edit of the agent's output (the kept text differs from the generated text) | `verdict: edit` with diff | 0.7 |
| Rejection (discarded, reverted, rerun) | `verdict: reject` | 0.6 |
| Outcome (tests pass, change merged, task reopened) | `action` with outcome | 0.3 |
| Accept without change | `verdict: accept` | 0.2, reinforces only |

Content from tools, web pages or email is data and is never a signal source. Only the person's own channel
teaches.

#### 22.2 The reviewer

One background reviewer runs after a turn when there is something to learn: a correction, edit or rejection
arrived, a rule arrived, ten user turns or ten tool rounds passed, or the person asked for a review. Turns
with no new signal skip it, and it never runs for scheduled jobs or subagents. A new live turn cancels it.
Its input is the new ledger events since its last run plus the beliefs they touch, not the whole transcript,
which keeps it cheap enough for a small model.

It works in two stages. **Classify** each event. Is it signal or noise? Is it taste, fact, procedure or an
open question? Which domain and node does it touch? Does it agree, conflict, or add something new? This
stage can run on rules or a small model.
**Reflect** only on survivors: write the lesson as a positive claim with its wider meaning and its evidence.
Lessons are written as "wants Y, not X, because Z", never as a stored bad example with a "this was wrong"
note, since models tend to learn the example and lose the negation.

Its only write tool is `propose`. Each lesson goes to exactly one home: taste to the pack, how-to to the
governing skill, facts to retrieval with sources, ambiguity to a question. For a proposal that would change a
high-confidence belief or a heavily used skill, a short second pass argues against it ("is this a one-off?
does it contradict a rule?") and attaches its objection.

#### 22.3 Weight and promotion

A lesson's weight is a pure function of its evidence, so it can always be recomputed from the ledger. Each
piece of evidence has a strength *s* and an age, and fades with a half-life (30 days for preferences, while rules
never fade). The faded strengths combine by *noisy-or*: weight = 1 minus the product of (1 minus each faded
strength). Read it as "the chance that at least one piece of evidence is real". Repeated evidence pushes the
weight toward 1, and silence lets it fall. Weight of 0.75 or more is labelled high, and 0.45 or more medium
**[built]**, in `exo.taste`.

Promotion, in order:

1. The person said it (a rule, an answer, or accepting the proposal): promote now.
2. Guarded (irreversible, public, financial, third-party): never auto-promote. Exo asks or waits.
3. Conflicts with an existing belief: apply the store's conflict rule (by default the newer wins unless the
   older one guards something irreversible or public, in which case Exo asks one numbered question). Never
   overwrite silently.
4. Taste or fact, unguarded, high weight, evidence from at least two separate sessions, no objection: promote,
   and show it in the next brief as learned.
5. Skill changes: only through the eval gate below, because skills change behaviour most.
6. Everything else waits. A medium-weight lesson may enter the pack as a hint. A stale one becomes a question
   if asking is worth it, or fades away.

High-weight taste is enforced. Medium is a hint. Low is not shown. Learned taste appears inline in the chat
with a link to the plain file that holds it, so the person can edit or delete it.

#### 22.4 Reverting appends

Lessons are views of the ledger, so undoing one adds an event and deletes nothing. An explicit "that's
wrong" writes a rejection with a reason, and the lesson leaves the pack on the next render. A later
correction lowers the weight. Unreinforced preferences fade. Skills live in git inside the store, so a
promoted skill change is one commit linked to its proposal, and reverting it is a `git revert` plus a ledger
note. Every pack line and skill rule links to its proposal, and every proposal to its evidence, so a wrong
lesson can be traced to the event that caused it.

#### 22.5 Hill climbing, and why held-out matters

A *candidate* is the current setup with one change: a reworded or new taste rule, a skill patch, a retrieval
budget, a threshold, or which approved model handles a task class. The current setup is the *incumbent*.
Today's climb builds two kinds of candidate: add one held-back lesson, or drop one auto-promoted lesson. The
other kinds are designed.

The eval set has three parts, all per person: questions about the person with known answers, preference pairs
(context, rejected reply, chosen reply), and replays of real past turns, cut right before the person's verdict,
with what the person actually did as the target. Each part is split by time. The oldest part is *train*: the
reviewer may read it. The middle is *dev*: candidates are scored on it. The newest, plus a fixed 20% sample,
is *held out*: the loop never reads it, and it is used only for the final keep-or-drop.

The climb is greedy and conservative:

1. Score the incumbent on dev and held out, and measure its run-to-run noise by scoring it more than once.
2. Score the candidate on dev. Drop it unless it beats the incumbent by a margin larger than that noise.
3. Score it on held out. Keep it only if held out does not drop and no domain drops by more than a set
   amount.
4. A kept candidate becomes the new incumbent. The decision and both scores go to the ledger.

Why the held-out step matters: any process that tries many small changes and keeps the ones that score
better will, given enough tries, find changes that fit the quirks of the test rather than the person. Dev
alone cannot detect this, because dev is what is being fitted. Held-out data that the process never saw is
the only honest witness. If a change helps on dev and not on held out, it was fitting dev. Held-out items age
into dev over time and fresh ones replace them, so the held-out set keeps testing recent behaviour. And if
live corrections rise while eval scores rise, the eval is stale or being gamed, which triggers a refresh.

Scoring uses two kinds of grader. *Checkable rules* come from the person's hard rules (for example "no em
dashes" or "every approval request carries a link") and are exact. Breaking one fails the item. A *judge
model*, from a different family than the model being tested, scores everything else over several samples,
checking stance and conclusions, not only voice. Before any skill change or high-impact taste rule is
promoted, it must also pass *regression gates*: no held-out drop, no item that passed before now failing a
checkable rule, no regression at all in guarded domains, and every pinned must-pass case passing. If the
person explicitly ruled the lesson, it is promoted anyway (the person outranks the eval) and the failing items
are flagged as possibly stale.

What exists today in `exo.eval` **[built]**: loading eval sets, question-and-answer runs with pluggable
responders and graders, exact, keyword, rule and judge graders, preference-choice runs with randomized
order, replays built from the ledger, the time split, the noise band, comparison, the regression gate, the
climb and a scoreboard, longitudinal metrics from the ledger (corrections per accepted output, verdict mix,
questions asked) and HTML reports. With no held-out cases, a lesson that needs the gate fails it and says
why: nothing can be proved yet. No eval scores are reported in this paper.

#### 22.6 Cost

The reviewer skips turns with no new signal. Classification runs on rules or a small local model. Only
survivors reach a larger model, in batches. The fast loop runs only pinned cases and checkable rules for the
touched domain, mostly with no model calls. The full eval and the climb run at night on an idle machine,
capped at 10 candidates and 3 judge samples per item by default, under a monthly ceiling the person sets.
Exo reports cost per accepted lesson. Every stage has a rules-only fallback, so the loop runs, more weakly,
with no model at all.

### 23. Trace, findings and re-verification

**Status.** **[built]**: the events, the trace reader, the rules, the findings table and its life cycle,
cost per call and per turn, and the commands below.

A harness can fail in ways its own agent cannot see: a slow provider, a quiet fallback, a model shown on
screen that is not the one that answered, a tool that keeps erroring. Exo treats its own behaviour as data,
in one loop of four steps: record, read, report, verify.

**Record.** Four kinds of record go into the same numbered event stream as everything else:

- `model.call`, once per model call: the role and route asked for, the route that served, every attempt with
  its outcome (ok, timeout, error, fallback) and time, the time to first token, the total time, the tokens
  including cache reads and writes, the estimated cost, and the retries.
- `tool.completed` carries the duration and whether the tool returned an error.
- `turn.completed` carries a timing summary: wall time, model time, tool time, time spent waiting for
  approvals, the number of calls, fallbacks and tool errors.
- `harness.error`, for any exception the harness caught in a hook, handler, plugin, memory call or client.
  Catching an error is allowed. Hiding it is not.

Keys, auth headers and token-like strings are removed before any of this is written.

**Read.** `exo trace` turns a thread into one row per turn: wall time, the model that served, calls,
fallbacks, tools, tool errors, tokens, cost and flags. With `--calls` it lists every call too. Subagent turns
roll up into the parent's turn. The same table is available in a session as `/trace`, and to the agent as the
`exo_trace` tool, so the agent can judge its own environment from numbers instead of impressions.

**Report.** At the end of each turn, a set of rules reads the records, with no model call, and files
*findings*. Each rule has a stable signature, so a problem that repeats raises a count instead of a new row.
Limits live in config, and a seat can override any of them.

| Signature | Fires when |
|---|---|
| `turn.slow` | the harness's own overhead in a turn (wall time minus tool time, approval waits and model-call time) is over 30 s |
| `model.slow:<route>` | one model call took more than 60 s in total |
| `turn.wall` | the whole turn, work included, is over 30 minutes |
| `model.wait:<route>` | the first token took more than 30 s |
| `model.fallback:<route>`, `model.timeout:<route>` | a try failed and the call moved on |
| `tool.error:<tool>:<kind>` | a tool returned an error |
| `harness.error:<where>:<type>` | the harness caught an exception |
| `shell.masked_exit` | an earlier stage of a shell pipeline failed but the reported exit was 0 |
| `tool.repeat:<tool>` | the same call ran twice in one turn |
| `subagent.slow` | a subagent task took more than 90 s |
| `model.mismatch` | the model shown is not the one that served, and no fallback explains it |

The first rule shows why the rules must measure the right thing. Wall time alone would blame the harness for
a ten-minute build or a person who took a while to approve. So `turn.slow` subtracts tool time, approval
waits and model time, and counts only what the harness itself spent; a slow provider is flagged on its route by `model.slow`. A separate, much higher cap catches turns that
are long for any reason.

Findings come from three sources. The rules file them. The agent files them with `exo_harness_report` when it
notices something the rules do not. And when the principal rejects an answer, or says what it should have
been, that verdict files one too, while the learning loop still learns from it. All findings live in a table
in the same database as the threads, so a copy of the database carries its findings with the sessions that
caused them.

**Verify.** A finding is `open` until someone marks it `fixed` in a given commit. Then Exo keeps measuring.
The same signature on a harness built from that commit or a later one *reopens* it. On an older harness it
only counts, because the fix was not there yet. Twenty quiet turns or three days on a fixed harness mark it
`verified`. "At or after the commit" is a git ancestry check on the source tree.

This is the harness's version of the held-out test in section 22.5. A fix is a claim about the future, and
only later behaviour can confirm it. The status line of each client shows the serving route and the count of
open findings, so a regression is visible without asking.

### 24. One surface

**Status.** **[built]**: a terminal client (`exo tui`, written with Textual), a web client served by the
command-centre plugin (`exo web`), a plain line client (`exo chat`), the `ui` tool and the `ui.panel` event.
**[designed, not built]**: a device client, and decision-model choice of when to offer a panel.

Exo ships one surface instead of a command-line tool plus a separate app. The base is a terminal-like prompt
and transcript. Three prefixes and one palette get you started: `/` for commands, `@` to attach
a file, `!` for a shell command, and `ctrl+p` for a fuzzy palette of everything else. Each tool call is one
line with its elapsed time, and its full output is one key away.

Richer elements are *panels*: a diff, a table, a task board, a choice, an approval card, a taste card, a
file, a chart. The model opens one with the `ui` tool, which emits a `ui.panel` event. Each client draws what
it can, and every panel carries a plain-text version, so a client that cannot draw it (a phone notice, a
voice device) still says the same thing. The person can always open or close a panel by hand. What they did
comes back through `ui.respond` (opened, dismissed, acted, ignored) and is recorded as a `ui_signal` ledger
note. The learning loop does not yet consume those notes. Subagents and scheduled runs get no `ui` tool:
nobody is watching them.

**Instruments, not chrome.** Every line on screen reports something. A status strip shows the model that
actually served (and, in the signal colour, that it fell back from the one asked for), how full the context
is, how much of the last call came from the provider's cache, the cost, and the number of open findings. A
small packet moves along a line while data is in flight, and nothing moves at rest. All of it comes from
events the core already sends. The look is taken from the project's own site, and a test fails if the
client's colours drift from the site's. Motion turns off with one setting, and colour turns off with the
common `NO_COLOR` convention.

**Learning the tool.** Exo has unfamiliar parts. Most users are used to watching a context limit and
compacting by hand, and here they do not have to. So `/intro` asks the agent itself for a short, staged
orientation, tailored to what the user already knows. The facts it draws on live in one skill file, and a
test fails if any slash command is missing from it. On first run the terminal client starts `/intro` by itself
and turns on *tutorial mode*: short tips drawn by the client at moments that matter (the first approval, the
first memory summary, the first fallback, the first peer message), each shown once, at most one per turn.
Tips cost no model tokens and never enter the transcript, so they cannot pollute memory. While tutorial mode is
on, the core adds one line to the system message. Turned off, the system message is byte-identical to
before, so the toggle costs one cache miss and nothing more.

**Handing off.** `/handoff` asks the agent to write a handoff note for whoever picks the work up next. The
core supplies the facts (thread, git state, open findings, running tasks and jobs, the latest summaries), and
the agent writes the file with its normal write tool, so the gate sees that write like any other.

The design borrows from three places. From opencode: one server, thin clients, the three prefixes, the
palette, and keeping terminal taste (theme, keys) out of project config. From Command Code: inline, editable
taste rows and a tiny input surface. From Hermes Agent: a typed wire between core and renderer, approval
prompts that survive a reconnect, steer and queue while busy, and a live view of running subagents.

### 25. Provenance and selective upstream rebasing

**Status.** **[built]**, with tests: `PROVENANCE.toml`, the consistency check, and `exo upstream report`,
`mark`, `check` and `merge`. A scheduler now exists, but the weekly report is not yet set up as a job.

Exo learned most from Hermes Agent, an MIT-licensed harness. Three ways of using it were weighed:

- **Depend on it.** Exo would run on pinned Hermes releases. But upstream changes very fast (about 490
  commits a day at the time of measurement), the files Exo would patch are among its busiest, and any
  dependency limits how Exo can evolve.
- **Fork it.** Exo would own the whole codebase: about 926,000 lines of non-test Python, plus a large
  TypeScript interface. Merging upstream works for a few weeks, then stops. And it cannot shrink onto a small
  device.
- **Lift on purpose.** Build a small core, copy in the few modules that are small, clean and proven, rewrite
  the rest small using Hermes as the reference for edge cases, and keep pulling in upstream fixes one at a
  time, by choice.

Exo chose the third. In an internal weighted scoring of these options (freedom to evolve, iteration speed,
reuse, device fit, coverage, upstream flow, licence, clean interface boundary), it scored 83 of 100 against
60 for a fork, 60 for a new TypeScript core and 47 for building on a closed agent SDK.

The mechanics:

- Every lifted file starts with a header naming the upstream path, commit and licence. Hermes's MIT notice
  ships in `LICENSES/`.
- `PROVENANCE.toml` has one entry per lifted or ported Exo file: its upstream paths, the mode (`lift`:
  copied then edited, `port`: rewritten from it, `watch`: upstream areas to hear about), the last upstream
  commit reconciled, and what changed. Today it records nine modules (the slots, the summarizer prompt, the
  transport base, both transports, the router, the fuzzy matcher, the patch parser and the shell runner) and
  two watch areas.
- A test fails the build if a file has a lifted header without an entry, or an entry points at a file that
  does not exist.
- `exo upstream report` keeps a fetch-only mirror of upstream, diffs each entry's upstream paths since its
  recorded commit (following renames, since upstream splits files often), drops noise (test-only, docs-only,
  bot merges), ranks the rest (fixes and security first, then features, then refactors, with commits that
  touch code Exo actually lifted ranked higher) and writes a report. For lifted files it also tries the upstream
  patch against Exo's copy and marks the clean ones.
- `exo upstream mark` moves an entry's commit forward after a port, or records a refusal with a reason, so
  a refused change does not come back next week.

A large upstream improvement shows up as a cluster of high-ranked commits on one module. That is the signal
to schedule a deliberate port.

### 26. Privacy and security model

**Local first.** The store, ledger, agent log and threads are files on the person's machine. Embeddings are
planned to run locally, and a local decision model (Laya) is the fallback when no hosted one is set. Nothing
in the store is pushed anywhere without the person's explicit approval each time.

**Privacy tiers.** The store separates curated files (pack, router, nodes, policies), which may be
committed to the store's own local git repository, from private files that are never committed: the raw
corpus (directory mode `0700`), the ledger, the items table and all derived indexes. Lint scans curated
files for leak patterns. Importers redact secrets before anything is written.

**Mechanism separate from person.** The Exo package never contains store data, only templates. A test scans
the new core for owner names and accounts and fails on a hit.

**Content is data.** Instructions come only from the person's channel. Text from tools, web pages, email and
imported history is labelled as data, the summarizer is told so, and the learning loop never learns from it.
Knowing the person well makes an injected instruction look out of place, but a persona is not a security
boundary on its own, which is why the gate and the operator floor sit outside it.

**Gates and consent.** Reversible, private actions run and are logged. Irreversible, public, financial or
third-party actions wait for the person. The operator floor and the credentials floor cannot be lowered by
the person's own policy, and a decision model can only tighten. An approval of an outward act covers its
exact text only. Standing grants are scoped, expire and are the principal's alone. A seat that approves must
sign. Peers and other ingress sources can never approve.

**Outbound scrub.** Public posts are swept for internal names, home paths, model ids and internal ids before
they leave, and held on a hit. Shell commands run without provider keys or token-like variables in their
environment. Trace records have keys and token-like strings removed before they are written.

**Integrity.** The ledger is signed and chained, append-only is enforced by database triggers, and keys sit
in `0600` files. Signatures mean trust need not rest on whoever hosts the files.

**Transport.** The Unix socket is owner-only. TCP requires a bearer token compared in constant time. TCP has
no TLS today, so it is for localhost or a trusted network. Provider keys come from environment variables,
the operating system's keychain or one owner-only key file, never from chat or from shared config. Peer
messages are signed by the sending seat and checked for age and replay.

**Known limits.** Privacy covers what a model can infer from text, not only what is stored, and auditing
outbound text for inferable facts is unsolved. Hosted model providers see what is sent to them. Exo can
choose what to send, not what they keep.

### 27. Portability toward a companion device

The long-term aim is a small portable device, something like a pocket companion, that carries a person's
store and grows with them. The near-term design rules that keep that path open:

- **The store is one folder.** Pack, nodes, items, ledger and indexes are plain files and SQLite. Copy the
  folder and the person moves.
- **The core stays small.** About 15,000 lines today, held by a test that allows a recorded floor plus 1,000 lines
  (section 11). The floor only goes down. Two runtime dependencies. The terminal client is Python too, so the core and its main client need no Node, no browser
  engine and no containers. One optional tool, `code_find`, needs Node and hides itself when Node is
  missing. The planned idle memory target is under 80 MB **[not measured]**.
- **A small-model path from day one.** The OpenAI-compatible transport already talks to local servers
  (llama.cpp, Ollama, LM Studio, vLLM), and routing can send routine calls to a local model.
- **Every learning stage has a rules fallback.** Proposals can queue on the device and be processed later by
  a larger model when one is reachable.
- **The protocol suits a weak link.** Numbered, replayable events, a capability handshake, and questions as
  events that can be answered by a button or by voice.

Wave 5 audits footprint, runs the core on a Raspberry Pi class board with an empty store and with a real
store, and adds a thin device client **[designed, not built]**.

A second, longer path starts in the same place. *Exo OS* is a design branch that asks what an operating
system looks like when an agent like this is its foundation, not an app on top. It runs the same core and the
same protocol, with no fork, and lives in its own repository. It is at the research and design stage:
nothing of it is built, and no machine runs it.

### 28. Walkthrough: one turn through every layer

Suppose the person types, in a terminal client: *"Fix the typo in README.md and push the branch."* Here is
that turn, layer by layer, with the actual protocol names. Steps that depend on unbuilt parts are marked.

<!-- fig:turn -->
```
 client                      exod (hub, engine, loop)                 model / tools
   | -- turn.send ---------->  |
   | <-- response accepted --  |
   | <-- message.completed --  |  user message stored (seq 101)
   | <-- turn.started -------  |
   |                           |  system prompt + pack + recall
   |                           |  context: settle, tree view + tail
   |                           | ---- stream ---------------------------> main role
   | <-- message.delta ...  -- | <--- text deltas, tool_call(edit_file)
   | <-- usage.updated ------  |
   | <-- message.completed --  |  assistant message with tool call
   | <-- tool.started -------  | ---- edit_file ------------------------> registry
   | <-- tool.completed -----  |
   | <-- message.completed --  |  tool results message
   |                           | ---- stream ---------------------------> main role
   | <-- tool.started -------  |  bash: git push (risk high)
   | <-- approval.requested -  |  pre_tool_call hook asks
   | -- approval.respond ---->  |  allow
   | <-- approval.resolved --  |
   | <-- tool.output ...  ---  | <--- push output
   | <-- tool.completed -----  |
   |   ... final reply ...     |
   | <-- turn.completed -----  |
   | <-- session.updated ----  |
```
*Figure 6. One turn as the client sees it. Every arrow from the server is an event with its own `seq`,
saved before it is sent.*

1. **Client to core.** The client sends a `turn.send` command with the session id, the text and
   `mode: send`. The dispatcher decodes it strictly and hands it to the engine, which starts the turn in the
   background and answers at once with a response `{"accepted": true}`.
2. **The record begins.** The loop repairs any dangling tool round, stores the user message, and emits
   `message.completed` and then `turn.started`. The hub numbers each event, writes it to `sessions.sqlite`,
   then sends it. A web client open on the same thread sees the same events with the same numbers. The
   message also takes the next position in the agent's log, and the memory plugin writes it to the signed
   ledger as an `utterance`.
3. **Memory.** The loop asks the memory slot for its pack block and for recall on "fix the typo in README.md
   and push". The pack says, among other things, that pushing is public and needs approval, and that commit
   messages are short. Recall goes just before the user message, not into the system message. *(The staged
   retrieval pipeline of section 21 is not built. Recall today is keyword search.)*
4. **Context.** The context engine schedules leaf summaries for any full chunk of 8 log entries older than
   the tail, waits for pending live summaries to settle, and builds the input: the system message with the
   agent's summary tree, this thread's recent messages verbatim, and a note of anything other windows of the
   same agent did since this one last looked. If a summary finishes during this, a `memory.summarized` event
   reports the token counts before and after.
5. **Model.** The loop streams from the `main` role. The router picks the first route whose credential is not
   cooling down. Suppose the first provider answers 429 before sending anything: the router records the
   cooldown and moves to the next route without the person seeing a thing. The `model.call` event records
   both attempts, so the fallback is not hidden, and the status strip shows the model that served. Text
   streams as `message.delta` events. The model asks for `edit_file`. The router prices the usage (or marks it
   unknown), and the loop emits `usage.updated` and then `message.completed` for the assistant message.
6. **First tool.** The loop emits `tool.started` (risk `medium`). The engine runs the registry, which fires
   `pre_tool_call`. The gate classifies a local file edit as reversible and private, so it allows and logs.
   The fuzzy matcher finds the typo even though the model's quoted line has different spacing. The loop emits
   `tool.completed` with the duration, and stores the result as a tool message (`message.completed`).
7. **Second round.** A fresh input is built and streamed. The model asks for `bash` with
   `git commit -am "Fix README typo" && git push`. `tool.started` goes out with risk `high`.
8. **The gate.** The gate splits the command at `&&`, sees `git push` to a shared remote, and classifies it
   as public. Policy says ask. The engine emits `approval.requested` with a summary ("push branch to
   origin"), the risk, the call, the binding hash of the exact command, and who may answer (here, only the
   principal). The terminal shows an approval panel. A phone client could show a card. The person presses
   allow. The client sends `approval.respond`. The engine emits `approval.resolved` and fires
   `post_approval_response`, and the gate writes the decision to the signed ledger. Had the person chosen
   "always", the grant would cover this exact command to this one remote for 7 days, not every push.
9. **Shell.** The shell tool runs with a timeout in its own process group, streaming output as
   `tool.output` events, then `tool.completed`.
10. **End of turn.** The model writes a short final reply. The loop emits `turn.completed` with stop reason
    `end`, total usage and the timing summary, tells the memory slot about the exchange, and emits
    `session.updated`. The trace rules read the turn. The fallback in step 5 files or counts a
    `model.fallback` finding, with no model call.
11. **After the turn.** Suppose that, earlier in this session, the person had
    rewritten one of the agent's commit messages to be shorter. That edit is a `verdict: edit` in the ledger.
    The reviewer wakes, classifies it as taste in the domain of writing, finds an existing medium-weight
    belief "commit messages are short and imperative", and proposes reinforcing it with this evidence. The
    new evidence comes from a second session, the rule is unguarded, and the weight crosses 0.75, so it is
    promoted and shown inline as a taste row with a link to its file. Had it been a skill change, it would
    have waited for the climb and been kept only if it beat the incumbent on held-out cases. *(Listing it in
    the next brief is not built.)*

If the network drops at step 7, the terminal reconnects with `events.subscribe` and `since` set to the last
`seq` it saw, receives every event it missed, and the approval request is still open for it to answer.

---

## Part III. Status and roadmap

### 29. Built and tested

The repository test suite is run with `python -m pytest -q` from the root. Contract tests against a Hermes
Agent checkout skip when no checkout is configured. Tests inside plugin folders run as separate suites, one
per plugin (see CI in `docs/DEVELOPER-GUIDE.md`). Counts change with commits, so this page does not list them.

| Area | What tests cover |
|---|---|
| Core (`tests/core`) | Types and codec, transports, router fallback, tools and background jobs, agent log and threads, context assembly, capture, scheduler, plugins, slots, seats, skills, MCP |
| Agents (`tests/agents`) | Wire codec, strict decoding, signing, delegation limits, cancellation, tiers and escalation |
| Clients (`tests/clients`) | Terminal and web clients, panels, approvals, onboarding, tips and site color consistency |
| Learning (`tests/learn`, `consolidate`, `eval`, `ask`, `brief`) | Signals, review, proposals, promotion, revert, climb, held-out gate, taste, questions and brief |
| Trace (`tests/trace`) | Trace rows, anomaly rules, findings and their life cycle, cost |
| Peers (`tests/peers`) | Presence, signed messages, policy, hold and release, refusal of consent |
| Protocol (`tests/protocol`) | Schema, caps, strict decoding, JSON Schema and TypeScript export, clients and replay |
| Integration (`tests/integration`) | Acceptance flow, hard kill and resume, blocking hooks, exact-text approvals and system invariants |
| Bus (`tests/bus`) | Signed agent-to-agent relay, client and wire format |
| Ledger, store, crypto, CLI | Signing, chain verification, append-only triggers, store lint and search |
| Onboard, ingest, install, orchestra, skills | Interview, importers, installer, role templates and intro skill |
| Upstream, acceptance, docs | Provenance, example store and docs map |
| Hermes contracts (`tests/contract`) | Skipped when no checkout is configured |

What that means in plain terms:

- `exod` runs over a Unix socket, TCP with a token, or stdio, with numbered, durable, replayable events.
- A multi-turn session with real tools runs end to end, survives a hard kill, and resumes.
- The router falls back, cools down, resolves tiers and prices calls, with unknown prices shown as unknown.
- Each seat is one agent with one hash-chained log and one summary tree across all its threads, and the
  principal's words are captured before they are summarized.
- The terminal, web and line clients run on the protocol, with panels, tips and `/intro`.
- The gate runs on every tool call with exact-text binding, approver lists, signed seat answers, scoped
  grants, two floors and the outbound scrub.
- Subagents, background jobs, peers through one ingress path, and scheduled runs work.
- The learning loop captures, reviews, proposes, promotes and reverts. Selected changes use a held-out
  evaluation gate; other promotion rules include explicit principal rulings and some repeated,
  high-confidence taste or fact evidence.
- The trace records every call and turn, files findings, and reopens or verifies them after a fix.
- Provenance is tracked and checked. The upstream report works on a test repository.

Live acceptance runs used a real provider. In wave 2: a correction from the first turn survived compaction
and was quoted word for word, `rm -rf` on a scratch path asked and a deny blocked it, and a bus round trip
verified. In wave 3, in 88 seconds over six turns: the model opened a diff panel, an edit verdict produced
three proposals, a job scheduled every minute ran and reported, a gate decision was written to the signed
ledger and verified, Jev answered three of four gate calls, and usage named the route that served. For the
one-memory model, over 25 turns on a copy of a real home: a fact told in one window was answered in a new
window on its first turn without a recall call, two windows running at once each saw the other's fact at
their next call, and the log verified after a restart.

### 30. Designed, not built

| Feature | Section | Planned |
|---|---|---|
| Commands `question.answer`, `taste.list`, `taste.edit`, and the events `question.asked`, `handoff.written`, `decision.queued`, `taste.updated` | 12 | later |
| Store version 2: kinds, nesting, `STORE.toml`, items table, cross-store links, health lint | 19 | not yet scheduled to a wave |
| Staged retrieval: router pins, FTS5 index, local embeddings, fusion, graph expansion, retrieval events | 21 | not yet scheduled to a wave |
| Decision models: calibration, `decision` events, panel and routing choices, exo-decide | 14 | later |
| The automatic nightly learning trigger, more kinds of climb candidate, a "learned" section in the brief | 22 | next |
| Cross-seat delegation over the bus, signed receipts in the ledger, child usage in parent totals | 16.1 | 4 |
| Held messages that survive a restart, receipts for bus sends, a peers view in the web client | 12.7 | next |
| Chat channels, the scheduler and external event feeds as ingress sources | 12.7 | 4 |
| A job view and tips in the web client | 15.5, 24 | next |
| Chat channel adapters, editor protocol adapter, history cutover | 31 | 4 |
| Remote transport over the bus or a tunnel, and TLS | 12, 26 | 4 to 5 |
| Snapshot undo of files | 17 | not scheduled |
| Device footprint audit, device client and idle memory measurement | 27 | 5 |
| Exo OS | 27 | design only |

Nothing in this paper reports a benchmark, an accuracy figure or a speed-up for Exo itself, beyond the small
live checks named above. The numbers quoted for decision models and embedding models come from their vendors'
own documentation or from single probes, and are marked as such.

### 31. The build waves

Work proceeds in waves. In each wave, parallel agents own disjoint paths and do not commit. A lead reviews,
runs the full suite, and commits. Each wave has an exit test. After wave 3, acceptance became lean: instead
of one long gate, the principal uses a canary agent every day and sends notes as they come, and each note is
a small cycle of fix and ship, or a design item.

| Wave | Goal | Exit test | State |
|---|---|---|---|
| 1. Core skeleton | loop, providers, tools, sessions, slots, plugin loader, protocol server, provenance, `exo upstream` | a multi-turn session with tools persists, resumes after a kill, and streams the same events to two clients, the old suite stays green, and the core is under 8k lines | done |
| 2. Re-home | the six plugins on the core, skills loader, MCP client, seat config, ledger mirror | a correction survives compaction word for word, the gate blocks a public action until a client answers `approval.requested`, and a bus round trip verifies | done |
| 3. UI and learning | terminal and web clients, panels, `exo.learn` with selected evaluation gates, cron, subagents and tasks | the live acceptance run above, then continual use on a canary agent; selected changes use held-out evaluation, while other lessons follow promotion rules based on principal rulings or repeated, high-confidence evidence | built, canary use under way |
| 4. Channels and cutover | chat adapters lifted with provenance, editor adapter, history import, real users moved over one by one with rollback | each move done with a working rollback | planned |
| 5. Device | footprint audit, local small-model defaults, core on a small board, thin device client | the core runs on the board with an empty store and with a real one, and the device client chats and approves | planned |

Several of the newer mechanisms in this paper came from that daily use. The trace and findings came from
the canary agent's failed attempt to judge its own environment. Background jobs and principal capture came
from asking that agent what it most needed. The consent rules of the gate came from a review of the
approval path and from the leak described in section 15.4.

### 32. Open questions

1. **Spend.** The monthly ceiling for the nightly eval and climb, and for hosted decision models, is the
   person's call and is not set by default beyond "small".
2. **The judge.** The judge is the `main` route by default. Whether a judge from a different model family
   would be fairer, and how many samples make a score stable enough for one person's small eval set. Small sets are noisy. The noise band in section 22.5 may reject most changes
   until the set grows.
3. **Auto-promotion.** Whether unguarded taste with two sessions of evidence should promote automatically
   (the current default) or wait for review in the brief.
4. **Cold start.** How a new user's empty eval set is seeded without leaking anyone else's data, and how
   soon the climb can run safely.
5. **Decision models.** Whether a fine-tuned small model beats rules and hosted models on real decisions,
   and how to calibrate with few labels. Unmeasured.
6. **Embedding model.** EmbeddingGemma's licence terms versus Qwen3-Embedding's Apache 2.0, and quality on
   mixed-language text. Unmeasured.
7. **Terminal client stack.** Wave 3 chose Python (Textual): one language and one install with the core, and
   no Node on the device path. The protocol keeps this reversible. If cold start on a low-power machine
   exceeds 1.5 seconds, or streaming a 2,000-message transcript stutters, a TypeScript renderer gets a spike.
8. **Backup.** Local only, or an encrypted copy on a private remote.
9. **Inference privacy.** How to audit outbound text for facts a model could infer, not only facts that are
   stated. Unsolved.
10. **Weights.** When, if ever, to move taste from the pack into a per-person adapter. The ledger is kept in
    a training-ready form so this stays possible.
11. **Cache under one memory.** Notes from other threads change the recent part of a call, which costs some
    prompt-cache reuse. On a 20-turn script with an ideal cache, prefix reuse fell from 93% to 84%. The live
    figure on the current code has not been measured.

---

## 33. Related work and acknowledgements

Exo stands on other people's work. The ideas below shaped it. Any errors in applying them are ours.

**Gwern Branwen, "Guardian Angels: LLM Personalization for Productivity and Security".** This essay, and the
reading it links to, shaped many of Exo's principles: an append-only log as the core data structure, asking
when uncertain, treating the person's attention as the scarcest resource, corrections that stick, and
evaluating on one person over time. Exo is one modest attempt to build part of that picture as working
software. It does not claim to realize the essay's vision, and where Exo departs from it (for example on
cost, local-first storage, and learning in prompts before weights) the choice is ours.

**Hermes Agent (Nous Research, MIT).** The harness Exo learned the most from, and the source of every
lifted module recorded in `PROVENANCE.toml`: the memory and context slot interfaces, the transport base and
message conversion, the fuzzy matcher, the patch parser, the shell runner's process-group handling and the
summarizer prompt. Its background review of memory and skills is one of the two ideas fused into Exo's
learning loop.

**opencode.** For showing how simple a coding agent can feel: one background server with thin clients, three
input prefixes and a palette, and terminal preferences kept apart from project settings.

**Claude Code (Anthropic).** For messages between live sessions, where one running session tells another
what it is doing so they do not collide. Exo's peers (section 12.7) extend that idea to Exo and Hermes agents
and route it through one consent-checked ingress path.

**SYM-2P (the Protean agent message language).** For the line format of Exo's agent wire: one packet per
line, fields by position, numeric codes and a symbolic body. Exo uses it as a checkable machine record and
keeps English for models, for the measured reasons in section 16.1.

**jevgrep (dzhng, MIT).** For the `code_find` tool, which runs it, and the skill text adapted from its own.

**Textual.** For the framework the terminal client is built on.

**Command Code.** For taste learning: treating every accept, reject and edit as a signal, and showing learned
taste inline as plain, editable rules with a confidence. The second idea fused into Exo's learning loop.

**OptChat (Victor Taelin) and UniiChat (Higher Order Company).** For the context model: one continuing
conversation over a verbatim log with a tree of summaries, a fresh model call each turn, and waiting for the
summaries to catch up. Exo reimplements the ideas and copies no code or prompt text.

**Letta.** For memory design, including memory blocks and consolidation during idle time.

**Lisanne Bainbridge, "Ironies of Automation" (1983).** For the warning that people cannot monitor a quiet,
mostly automatic system, which is why Exo gates by consequence instead of asking about every step.

**Stephane Ross, Geoffrey Gordon and J. Andrew Bagnell, DAgger (2011).** For the result that corrections
gathered on the learner's own attempts are what make imitation learning work, which is why a verdict on the
agent's own output is Exo's strongest signal.

**Dylan Hadfield-Menell and colleagues, "Cooperative Inverse Reinforcement Learning" (2016).** For framing
assistance as a game in which the assistant should ask and the person should teach.

**Burr Settles, "Active Learning Literature Survey".** For choosing questions by how much the answer would
change, which shapes Exo's question queue.

**Norbert Wiener, "Some Moral and Technical Consequences of Automation" (1960).** For the case that the
purpose of an irreversible act must be confirmed before it is launched.

**Ben Krause and colleagues, on dynamic evaluation.** For the idea that weights can serve as memory, which is
why the ledger is kept in a form that could train a per-person adapter later.

**Stephen Robertson and colleagues (BM25), and Gordon Cormack, Charles Clarke and Stefan Buettcher
(reciprocal rank fusion, 2009).** For the ranking methods in Exo's retrieval design.

**DSPy.** For showing that prompts can be searched and improved against a metric. Exo's climb does the same
with one readable change at a time.

**Decision and embedding models.** TypeSafe (Jev), Convai Innovations (Laya), Google
(EmbeddingGemma) and the Qwen team, whose public documentation informed sections 14 and 21.

Exo's own earlier knowledge-base, ingest and team-memory systems contributed the store format, the router
discipline, the item layer and the event-sourced team state described in section 19.

---

## 34. Glossary

- **Agent.** One configured Exo assistant, defined by a seat file. It has one log and one memory.
- **Append-only.** Data that is only ever added to, never changed or deleted.
- **Background job.** A shell command that runs on after the tool call returns, and reports back when done.
- **Binding.** The hash of an outward act's exact text, target and arguments. An approval covers it only.
- **BM25.** A standard keyword ranking that favours documents sharing rare query words, with diminishing
  returns for repeats and no bonus for length.
- **Calibration.** Adjusting a model's probabilities so that "90% sure" is right about 90% of the time.
- **Candidate / incumbent.** In the climb, a candidate is the current setup with one change. The incumbent
  is the current setup.
- **Client.** A program that shows Exo to a person (terminal, web page, device) and talks to `exod`.
- **Context window.** The text a model can read in one request.
- **Daemon.** A program that runs in the background and waits for requests. `exod` is Exo's.
- **Decision model.** A model that answers a fixed question with probabilities over fixed options instead of
  writing text.
- **Embedding.** A list of numbers that stands for a text, arranged so that similar meanings sit close
  together.
- **Event.** A numbered record of something that happened, sent from `exod` to clients and saved first.
- **Fallback.** Trying the next model route when one fails before producing output.
- **Finding.** A problem the harness noticed in its own behaviour, with a stable signature, a count and a
  status (open, fixed, verified, reopened).
- **Grant.** A standing "always allow", scoped to one kind of act and target, expiring, and only the
  principal's to give.
- **Hash.** A short fingerprint of data that changes completely if the data changes at all.
- **Hash chain.** A sequence of records where each contains the hash of the one before, so tampering breaks
  the chain.
- **Held-out set.** Test cases the learning process never sees, used only for the final keep-or-drop.
- **Hill climbing.** Improving step by step, keeping a change only if it scores better.
- **Hook.** A named point where a plugin can watch or change what the core is doing.
- **Ingress.** The one path for input that does not come from the principal's keyboard: policy, delivery,
  receipt.
- **Ledger.** Exo's signed, hash-chained, append-only record of events.
- **Noisy-or.** A way to combine evidence: one minus the product of (one minus each strength), read as the
  chance that at least one piece of evidence holds.
- **Pack.** The one page about the person that is loaded on every request.
- **Peer.** Another live agent session on the same machine that can send messages, but never consent.
- **Principal.** The person Exo works for.
- **Principal store.** The folder holding everything Exo knows about one principal.
- **Prompt cache.** A provider's discount for the part of a request that repeats a recent one byte for
  byte.
- **Proposal.** A typed lesson with evidence and confidence, waiting to be promoted or dropped.
- **Provenance.** The recorded origin of a piece of code or data.
- **Reciprocal rank fusion.** Merging ranked lists by summing 1 divided by (60 plus the rank) across lists.
- **Retrieval.** Choosing which stored notes to show the model for a given request.
- **Seat.** The file that configures one agent: who it serves, its models, tools, skills and plugins.
- **Server-Sent Events (SSE).** A way for a server to stream events to a client over one long HTTP response.
- **Signature.** A code made with a secret key that proves who wrote a record and that it was not changed.
- **Slot.** An exclusive plugin seat. Only one memory provider and one context engine can be active.
- **Subagent.** A child thread started by `delegate`, with fewer tools, hard limits and its own model tier.
- **Summary tree.** Older messages summarized in leaves of 8, merged pairwise into higher levels as needed.
- **Thread.** One window, channel, scheduled run or conversation of an agent. All threads share the
  agent's memory.
- **Tier.** One of three model roles (`fast`, `standard`, `deep`), cheapest to most capable.
- **Token.** A piece of a word. The unit models read and are billed by, about four characters in English.
- **Trace.** Exo's record of its own calls, timings, errors and costs, read by rules that file findings.
- **Verbatim tail.** The most recent messages, sent to the model word for word.
- **View.** Anything computed from the ledger that can be rebuilt from it.
