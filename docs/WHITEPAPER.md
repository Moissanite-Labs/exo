# Project Exo: technical whitepaper

Version 0.1, October 2026. Aska Digital.

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
  - [5. Staying sharp in long work](#5-staying-sharp-in-long-work)
  - [6. Learning your taste, and proving it](#6-learning-your-taste-and-proving-it)
  - [7. Acting safely without asking about everything](#7-acting-safely-without-asking-about-everything)
  - [8. One core, many screens](#8-one-core-many-screens)
  - [9. Fit one person, work for anyone](#9-fit-one-person-work-for-anyone)
- [Part II. Technical design](#part-ii-technical-design)
  - [10. Architecture at a glance](#10-architecture-at-a-glance)
  - [11. The core daemon and its event protocol](#11-the-core-daemon-and-its-event-protocol)
  - [12. The model router](#12-the-model-router)
  - [13. Decision models](#13-decision-models)
  - [14. Tools, hooks and gates](#14-tools-hooks-and-gates)
  - [15. The agent loop](#15-the-agent-loop)
  - [16. Context management and sessions](#16-context-management-and-sessions)
  - [17. Plugins and slots](#17-plugins-and-slots)
  - [18. The principal store](#18-the-principal-store)
  - [19. The signed ledger](#19-the-signed-ledger)
  - [20. Retrieval](#20-retrieval)
  - [21. The learning loop](#21-the-learning-loop)
  - [22. One surface](#22-one-surface)
  - [23. Provenance and selective upstream rebasing](#23-provenance-and-selective-upstream-rebasing)
  - [24. Privacy and security model](#24-privacy-and-security-model)
  - [25. Portability toward a companion device](#25-portability-toward-a-companion-device)
  - [26. Walkthrough: one turn through every layer](#26-walkthrough-one-turn-through-every-layer)
- [Part III. Status and roadmap](#part-iii-status-and-roadmap)
  - [27. Built and tested](#27-built-and-tested)
  - [28. Designed, not built](#28-designed-not-built)
  - [29. The five build waves](#29-the-five-build-waves)
  - [30. Open questions](#30-open-questions)
- [31. Related work and acknowledgements](#31-related-work-and-acknowledgements)
- [32. Glossary](#32-glossary)

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

### 5. Staying sharp in long work

Inside one long session there is a different problem. Most assistants keep appending to a growing
transcript and send all of it on each request. When it gets too long they compress it once, in a hurry, and
quality drifts.

Exo builds every turn as a fresh request. Picture a long book with summaries at several levels: a line for
each chapter, a paragraph for each part. To keep reading well you need the summaries of everything so far,
plus the last few pages in full. Exo keeps exactly that. The most recent messages go to the model word for
word. This is the *verbatim tail*. Older messages are folded into a *summary tree*. Every eight older
messages get one short summary, called a leaf. When the summaries together grow too long, two neighbouring
summaries merge into one at the next level up, and so on, so the oldest material is the most compressed.

Before Exo builds the request for a new turn, it waits for any pending summaries to finish, so the view
always covers everything that came before. Every original message stays in a local database with full-text
search, so a detail that was summarized away can always be found again. This part is built.

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

A lesson can sound right and still be wrong. So Exo tests lessons before keeping them. It keeps a personal
exam built from your own past decisions: questions about you with known answers, pairs of a reply you
rejected and the one you chose, and real past moments cut off right before you gave your verdict. A change is
scored against the current setup on this exam.

This method is called *hill climbing*. Picture standing on a hillside in thick fog. You cannot see the top,
but you can tell whether a step takes you higher. Take one small step. If you are higher, stay. If not, step
back and try another direction. Exo's height is the exam score, and each step is one change you can read,
such as one new taste rule.

Any exam has a weak spot: you can learn the answers instead of the subject. A student who memorizes last
year's practice paper scores well on it and badly on the real exam. So the newest part of your exam is *held
out*. The learning process never reads it, and a change is kept only if it also does not score worse there.
That is the guard against a system that flatters its own test.

The building blocks for this loop exist (signal capture, belief proposals, taste weighting, a basic exam
runner). The full loop, with the background reviewer and the held-out gate, is designed, not built.

### 7. Acting safely without asking about everything

An assistant that asks before every step is useless. People cannot watch a quiet system all day. They start
approving by reflex, which is worse than not asking. An assistant that never asks is dangerous.

Exo sorts actions by consequence, not by count. It asks whether the action can be undone, who can see it,
whether it costs money, and whether it affects someone else. Editing a file on your machine runs at once and
is logged. Pushing to a public repository, posting, paying, or sending an email to another person waits for
you, with a one-line summary and a sensible default. Some rules protect other people, and your own settings
cannot lower them.

Many small choices in this process (which tool, whether a panel should open, which model fits a task) do not
need a large model at all. A *decision model* answers a fixed question with probabilities over fixed options
instead of writing text. It is fast, cheap, and small enough to run on your machine. A probability lets Exo
act when it is sure and ask when it is not. One rule is fixed: a decision model may make a gate stricter,
never looser. The action gate exists as a library and plugin. Decision models are designed, not built.

### 8. One core, many screens

Exo's working parts run as one background program, called `exod`. (A *daemon* is a program that runs in the
background and waits for requests. The "d" in `exod` is for daemon.) Every screen is a *client* of it: a
terminal, a web page, a chat channel, and one day a small device. Clients speak a small typed *protocol*:
they send commands in, and receive events out.

Every event gets a number, and every event is saved before it is sent. A client that drops off, such as a
phone on a bad connection, comes back and asks for everything after the last number it saw. It misses
nothing, and two clients watching the same session see exactly the same sequence. This part is built.

The screen itself is meant to feel like a terminal: a prompt and a transcript, with three prefix keys and
one command palette. Richer elements, such as a diff viewer, an approval card or a task board, are panels
that the model opens when they help, and that each client draws as well as it can. The terminal and web
clients are designed, not built. Today there is a plain line client.

### 9. Fit one person, work for anyone

Exo holds two goals at once. It should fit one person as closely as possible, because an assistant that does
not really know you is not worth much. And it should work for anyone, because a system built around one
person's accounts and habits cannot be handed to a second.

The design answers both with one split. The *mechanism* (the code: the daemon, the ledger, the store format,
retrieval, the learning loop) is generic and ships to everyone. The *principal* (one person's data, profile,
rules and tuning) lives only in that person's store. No line of the core may name a user, an account or a
machine, and a test checks this. A fresh install with an empty store runs end to end, and an onboarding
interview builds a new person's store from zero. Updating the core must never lose anything in a store.

That is the whole system at a high level: a signed record at the bottom, a portable store of what it knows
about you, fresh and complete context on every turn, lessons that are kept only if they prove themselves,
gates by consequence, and one core behind every screen. Part II covers each piece in depth.

---

## Part II. Technical design

### 10. Architecture at a glance

<!-- fig:layers -->
```
 Clients        terminal | web | desktop | chat channels | device
                         |   commands in, events out
 Protocol       one typed, numbered, replayable event stream
                         |
 Core (exod)    turn loop | model router | tools + hooks | sessions | plugins + slots
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
and two command-line modules, and fails above 8,000. Today the count is 7,631. The budget exists so that
growth is a decision, and so the core can later shrink onto a small device (section 25).

Repository layout, in short:

| Path | Holds |
|---|---|
| `src/exo/core/` | types, turn loop, context, sessions, model router and transports, tool registry and built-in tools, hooks, plugins, slots |
| `src/exo/protocol/` | the protocol schema, a client, and generated JSON Schema and TypeScript types |
| `src/exo/server/` | `exod`: the dispatcher, the event hub, three transports |
| `src/exo/*.py` | earlier generic modules: ledger, store, retrieval, gate, capture, consolidate, taste, ask, eval, brief, onboard, handoff, upstream |
| `plugins/` | six plugins: memory, context, gate, bus, command centre, orchestra |
| `templates/` | an empty principal store with a generic prior |

### 11. The core daemon and its event protocol

#### 11.1 Why a daemon

One process owns all state that matters: sessions, running turns, tools, plugins. Clients render and send
input. This split has three effects. Every client gets the same behaviour (retries, routing, gates,
compaction) without reimplementing it. A client can crash or close without losing work. And a new kind of
client, such as a device with a two-line screen or no screen, needs only to speak the protocol.

#### 11.2 Commands, responses, events

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
has 257 methods. When a feature needs more, it adds fields or event types, not one-off methods.

Today the schema defines 25 commands and 21 events:

| Group | Commands |
|---|---|
| Handshake and stream | `hello`, `events.subscribe` |
| Sessions | `session.create`, `session.list`, `session.get`, `session.fork`, `session.resume`, `session.undo` |
| Turns | `turn.send` (mode `send`, `queue` or `steer`), `turn.interrupt` |
| Answers | `approval.respond`, `question.answer` |
| Commands | `command.run`, `commands.list` |
| Lookup | `search`, `models.list`, `models.set`, `tools.list`, `skills.list`, `tasks.list`, `seat.list` |
| Config and taste | `config.get`, `config.set`, `taste.list`, `taste.edit` |

| Group | Events |
|---|---|
| Sessions | `session.created`, `session.updated` |
| Turns | `turn.started`, `turn.completed` |
| Model output | `message.delta`, `message.completed`, `reasoning.delta`, `usage.updated` |
| Tools | `tool.started`, `tool.output`, `tool.completed` |
| Approvals and questions | `approval.requested`, `approval.resolved`, `question.asked` |
| Context | `context.compacted`, `handoff.written` |
| Work and learning | `task.updated`, `decision.queued`, `taste.updated` |
| General | `notice`, `error` |

Not every type is live yet. The wave 1 engine implements the session, turn, approval, search, model, tool,
command and skill commands. `question.answer`, `config.get`, `config.set`, `tasks.list`, `seat.list`,
`taste.list` and `taste.edit` return a typed `unsupported` error until their subsystems land. The core
emits every event above except `question.asked`, `handoff.written`, `task.updated`, `decision.queued` and
`taste.updated`, which belong to later waves.

#### 11.3 Ordering, durability and replay

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

#### 11.4 Transports

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

#### 11.5 Security of the socket and the token

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

#### 11.6 The engine behind the dispatcher

The server talks to an `Engine` interface with one method, `handle(command, hub)`. Long work, such as a
turn, starts in the background, returns quickly (`{"accepted": true}`), and reports progress as events. The
real engine (`exo.core.engine`) wires together the router, tool registry, loop, session store and plugins. A
tiny echo engine exists for protocol tests.

### 12. The model router

#### 12.1 Providers and transports

Exo talks to models through two transports, lifted from Hermes Agent and trimmed: the Anthropic Messages API,
and the OpenAI-compatible chat API. The second covers OpenAI, OpenRouter and local servers such as llama.cpp,
Ollama, LM Studio and vLLM. Both stream. Both turn the provider's stream into one internal event shape:
`text` and `reasoning` deltas, whole `tool_call`s (the transport assembles argument fragments), `usage`, and
exactly one final `done` or `error`. An error carries the HTTP status, a `retryable` flag and any
`retry_after` the provider gave.

Which providers exist, their base URLs, and which environment variable holds each key are data, in
`catalog.toml`, not code. Keys are read from environment variables only. A provider may list several key
variables. Each becomes a separate *credential* that the router can rotate through.

#### 12.2 Roles

Code never asks for a model by name. It asks for a *role*:

- `main`: the model that runs the conversation.
- `aux`: cheaper side work, such as summaries and titles.
- `decision`: small structured choices (section 13).

Each role is an ordered list of routes, written `provider:model`. If a role is empty or every route fails,
resolution falls back: `decision` to `aux` to `main`, and `aux` to `main`. The user's config overrides the
catalog, and `models.set` changes a role at run time.

#### 12.3 Fallback

For each route in the chain, the router picks the first credential that is not cooling down, and streams.
Two rules govern what happens on failure:

1. **Fall back only before output starts.** If the provider fails before sending any text, reasoning or tool
   call, the router quietly tries the next credential, then the next route. Once output has started, the
   error goes to the loop, because silently switching models in the middle of a reply would splice two
   different answers.
2. **Do not fall back on a bad request.** HTTP 400, 413 and 422 mean the request itself is wrong (malformed,
   too large). Another route would fail the same way, so the error goes straight back.

When everything is exhausted, the router returns one error that says why, and when the next route reopens.

#### 12.4 Cooldown

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

#### 12.5 Cost

The catalog carries list prices per million tokens for known models. When a provider reports token usage
without a cost, the router computes one, counting cache reads and cache writes at their own rates. Usage
flows to clients as `usage.updated` events with a running session total.

### 13. Decision models

**Status.** The `decision` role exists in the router, with fallback to `aux` and `main`. The typed,
probability-returning interface described below is **[designed, not built]**.

#### 13.1 What a decision model is

Many choices inside a harness have a fixed set of answers. Which of these 12 tools does this turn need?
Should a panel open now, and which one? Is this message a correction? Is this action public? A generative
model can answer such questions by writing text, but it is slow and costly for the job, and its answer comes
with no usable measure of how sure it is.

A *decision model* takes a short text (the "state") and typed questions, and returns probabilities instead
of prose. Three question types are shared by the products available today: *choice* (pick one of N options),
*score* (a level on an ordered scale), and a yes-or-no *predicate*. It does not write text, so it never fills
in tool arguments. It picks which tool, which panel, which label. A generative model still writes the
arguments, drafts and plans.

#### 13.2 Where they fit

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

#### 13.3 Tighten only

A decision model may move an action toward "ask", never toward "allow". Rules in code run first, and anything
guarded (irreversible, public, financial, third-party) never depends on a model's answer. This keeps a
miscalibrated or manipulated model from opening a door that code had closed.

#### 13.4 Calibration, fallback and logging

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

#### 13.5 Local by default

The planned default provider is Laya, an open-weight model (Apache 2.0) small enough to run on a laptop.
Users may choose TypeSafe Jev, Perplexity's pplx-decider, OpenAI's Decisions API, or rules only. Hosted
providers are opt-in and receive redacted state.

Laya's own model card is candid that the base model is near chance on mixed decisions without fine-tuning
(it reports 0.362 against a random baseline of 0.318 on its typed-decisions set). So Exo plans to ship a
fine-tuned version, "exo-decide", trained on a generic seed set and, per user, on that user's own verdicts.
Licence terms shape the data: TypeSafe's and OpenAI's terms forbid training other models on their outputs,
so those outputs are never training labels. Weights stay private until they beat base Laya and Jev on a
held-out set. None of this is measured yet.

### 14. Tools, hooks and gates

#### 14.1 The registry

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
| `patch` | medium | a multi-file patch format. All hunks are applied to an in-memory copy first and written only if all succeed |
| `bash` | high | timeout, working directory, capped output. On timeout the whole process group gets TERM, a grace period, then KILL |

The risk hint is information, not a decision. Deciding is the gate's job.

#### 14.2 Hooks

Named hook points let plugins watch and steer. The names follow Hermes so existing plugins keep working. The
engine fires these today **[built]**:

- `pre_tool_call`: may return `block` (the model receives the reason as an error result) or `approve` (ask
  the person first).
- `post_approval_response`: told what the person chose.
- `post_tool_call`: sees the result.
- `transform_tool_result`: may rewrite the result text.

More names are reserved and validated (for model calls, turns, sessions, compaction, commands, subagents and
skills) but not yet fired by the loop. A failing hook is logged and skipped, so one broken plugin never
breaks a turn. A block decision is still honoured.

#### 14.3 The approval flow

When a hook asks for approval, the engine publishes `approval.requested` with a request id, a summary, the
risk and the tool call, and waits. Any client may answer with `approval.respond`: `allow`, `deny` or
`allow_always`. The engine publishes `approval.resolved` and continues. `allow_always` records the rule key,
so the same kind of call does not ask again in this run. Because the question is an event, a terminal, a web
page, a phone or a device can answer it, whichever the person has at hand.

#### 14.4 The gate

The gate (`exo.gate`, about 1,300 lines, stdlib only) is two pure steps. `classify` looks at a tool name and
its arguments and places the action on four axes: reversibility, exposure, cost, and effect on third parties.
It parses shell commands into their parts (pipes, `&&`, heredocs, `sudo` and `env` prefixes) and knows the
common cases, for example that `git push` to a shared remote is public, that `gh pr comment` writes where
others read, and that an HTTP POST to a non-local host leaves the machine. `decide` then maps that cell
through the person's policies: a gate matrix, scopes, overrides, reserved and denied actions, budget and
posting checks. The outcome is allow, log, ask or deny.

Under the person's policy sits an *operator floor* that the policy cannot lower: an irreversible public act
that touches a third party always asks. Amplifying a person is neutral. It would amplify a bad actor too, so
rules that protect other people sit outside the model of the person.

The gate is built and tested as a library and as the `exo-gate` plugin. On the new core, the plugin loads and
its hooks fire. The full re-homing, including its signed action records, is wave 2.

### 15. The agent loop

`exo.core.loop` runs one turn as a short series of steps. Everything it needs (the model, the tool runner,
the memory provider, the context engine) is injected, so the loop imports none of them and each can be tested
alone.

1. Close any tool call left dangling by a crash with an error result, so the transcript stays valid.
2. Store the user message and emit `message.completed`, then `turn.started`.
3. Build the system prompt: the base prompt, the memory provider's pack block, and any memory it recalls for
   this message.
4. Build a fresh model input from the context engine (section 16).
5. Stream one model call. Text arrives as `message.delta`, reasoning as `reasoning.delta`. Usage arrives as
   `usage.updated`.
6. Store the reply (`message.completed`). If it asked for tools, run them in order, each with
   `tool.started`, any `tool.output`, then `tool.completed`, and store all results as one tool message. Go
   back to step 4.
7. When the model stops asking for tools, emit `turn.completed` with the stop reason and usage, tell the
   memory provider about the exchange, and emit `session.updated`.

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
  tool rounds. `queue` waits for the turn to end.
- **Fail soft.** A memory provider or context plugin that throws is ignored for that call. An unexpected
  error inside the loop ends the turn with `error` and a clean `turn.completed`, never a hung session.

### 16. Context management and sessions

#### 16.1 A fresh call every turn

Exo does not keep a growing transcript. Every model call is built new from three parts: the system prompt,
a *view* of older history made of summaries, and the *verbatim tail* of recent messages. This idea comes from
the OptChat design and its production form, UniiChat (section 31). Exo reimplements the idea. It copies no
code or prompt text from either.

#### 16.2 The summary tree

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

- Messages older than the tail (the last 12) are cut into leaves of 8. Each leaf is summarized once.
- A node at level L covers 2 to the power L leaves. Addresses are `(level, index)`, so a node's children are
  `(L-1, 2i)` and `(L-1, 2i+1)`.
- When the summaries in the view total more than 6,000 tokens (estimated as characters divided by four), the
  oldest adjacent pair of finished nodes at the same level is merged into its parent.
- The view shows the coarsest finished nodes that tile the summarized region, oldest first, each labelled
  with the message range it covers. A leaf whose summary is not ready yet appears verbatim instead, so nothing
  is ever missing.
- The view is introduced to the model as background, not instructions, and its header asks the model
  to say in its reply anything it learned that will matter later, since summaries keep little tool output.

#### 16.3 Lanes and settling

Summaries run in parallel in two *lanes* with separate limits: live chat (8 at once) and imported history (2
at once). A large import, such as a year of past chats, therefore never starves the live conversation, and
imports can be paused and resumed. Before building a turn, the context engine *settles*: it waits, up to 60
seconds, until no live summary is pending. On timeout it proceeds, using verbatim text for the unfinished
part. Failed or interrupted summary jobs are retried when the session is next touched, including after a
restart.

#### 16.4 The summarizer

The summarizer is any model on the `aux` role. Its prompt wording is lifted from Hermes Agent's compressor
and trimmed. It tells the summarizer that the turns are data to summarize, never instructions to follow. It
must replace any secret it sees with a placeholder, keep the user's language, and keep paths, commands,
errors and values exact. It must also record the user's latest unfinished request precisely, including a
request to stop or undo, so a cancelled task is not carried forward.

#### 16.5 Sessions in SQLite

One `sessions.sqlite` file per Exo home holds sessions, messages, events, summary nodes and a full-text
index. It runs in write-ahead-log mode so readers do not block the writer.

- **Search.** Every message's text, tool calls and tool results go into an SQLite FTS5 index. `search`
  returns matches with highlighted snippets. Input is quoted, so any text is a safe query.
- **Fork.** `session.fork` copies a session's messages up to a chosen message into a new session and
  records the parent and fork point, so lineage can be walked back.
- **Resume.** `session.resume` reattaches after a restart: it repairs a half-finished tool round and restarts
  pending summaries.
- **Undo.** `session.undo` removes the last N turns from the conversation. It does not roll back files on
  disk. Snapshot undo of files is not built.

### 17. Plugins and slots

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
plugins take their slots **[built]**. Making each plugin do its full job on the new core is wave 2.

### 18. The principal store

**Status.** The version 1 store is **[built]**: a folder with a pack, a router, typed belief nodes, lint and
keyword search. The version 2 additions in this section (store kinds and nesting, the items table, derived
indexes, cross-store links) are **[designed, not built]**.

#### 18.1 One format, three kinds

Every store has the same layout and node schema. A manifest, `STORE.toml`, says which kind it is.

| Kind | Holds | Pack file |
|---|---|---|
| `principal` | one person: profile, beliefs, external items, ledger, policies | `PRINCIPAL.md` |
| `agent` | one agent's own memory: patterns, experiences, principles, charter | `ANIMA.md` |
| `group` | a team of agents' shared state: agreements, open tensions, practices, project state | `NEXUS.md` |

Agent and group stores belong to a person, so they nest inside that person's store. One folder then carries
a whole person and their team, and that folder is the unit that moves to a new machine or a device. Nested
stores share the parent's ledger and items table, and each ledger entry records which scope wrote it.

#### 18.2 Layers

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

#### 18.3 Nodes

Each node has required frontmatter: `id`, `title`, `type`, `domain`, `summary`, `load_when`, `confidence`
(high, medium, low), `stability` (core, durable, volatile), `valid_from`, `sources` and `links`. Types today
are trait, value, fact, preference, rule, episode, entity, procedure and question. Version 2 adds `pattern`
and `tension`. Links are typed: part-of, explains, evidence-for, contradicts, supersedes, applies-to,
instance-of, see-also, and in version 2 derived-from. A belief that is replaced is not deleted. It gets a
`valid_until` date and a `supersedes` link from its successor.

`exo lint` checks a store: missing or mistyped keys, an id that does not match its path, dangling links, a
missing pack or router, and *leak patterns* (phone numbers, API keys, tokens, passwords, private keys) in
any curated file. The empty template store lints with zero errors and zero warnings.

#### 18.4 Items

Items are things the person saved, such as bookmarks or notes **[designed, not built]**. Each row has a
deterministic id computed from its source (so re-running an import changes nothing), a normalized URL and a
content hash for de-duplication, a quality tier (curated, inferred, noise), and a review status. Every insert
also appends an `item` entry to the ledger, so the table can be rebuilt. Items are evidence, not beliefs: the
learning loop may propose a belief from many items, and the person reviews it like any other. Conversations
are not items. They enter the ledger as utterances, in the person's own words.

### 19. The signed ledger

**Status.** **[built]**, as `exo.ledger`, with tests. Mirroring every core turn into it is wave 2.

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
into a prompt, which feeds use counts and evaluation), `decision` (section 13), and `state` and `lease` for
shared team state with revision checks, so two writers cannot overwrite each other.

### 20. Retrieval

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
   2, tags 1, body 1. The second list is embedding similarity (section 20.1). The two lists are combined by
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

#### 20.1 Local embeddings

The default embedding model is planned to be EmbeddingGemma 300M, which is built for phones and small boards
and handles many languages. Qwen3-Embedding 0.6B (Apache 2.0) is the alternative where Gemma's terms are not
acceptable or more strength on long text is wanted. Vectors are cut to 256 dimensions (both models are
trained so that a prefix of the vector is still a useful vector), normalized, and stored as 8-bit integers:
256 bytes per item, so 100,000 items take about 25 MB. Every vector row records its model, dimensions and
content hash, so unchanged text is never embedded twice and a model change triggers a clean rebuild. Search
is a flat scan in SQLite. At this scale no vector server is needed. A remote embedding service is possible
only as an opt-in that the person's policy must allow, because it sends personal text off the machine.

### 21. The learning loop

**Status.** Signal capture, belief proposals with a rule of three, taste weighting with decay, the
question queue and a rules-only eval runner are **[built]** as separate modules with tests
(`exo.capture`, `exo.consolidate`, `exo.taste`, `exo.ask`, `exo.eval`). The fused loop, `exo.learn`, with
the background reviewer, typed proposals, promotion policy, held-out gate and nightly climb, is
**[designed, not built]**.

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

#### 21.1 Signals

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

#### 21.2 The reviewer

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

#### 21.3 Weight and promotion

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

#### 21.4 Reverting appends

Lessons are views of the ledger, so undoing one adds an event and deletes nothing. An explicit "that's
wrong" writes a rejection with a reason, and the lesson leaves the pack on the next render. A later
correction lowers the weight. Unreinforced preferences fade. Skills live in git inside the store, so a
promoted skill change is one commit linked to its proposal, and reverting it is a `git revert` plus a ledger
note. Every pack line and skill rule links to its proposal, and every proposal to its evidence, so a wrong
lesson can be traced to the event that caused it.

#### 21.5 Hill climbing, and why held-out matters

A *candidate* is the current setup with one change: a reworded or new taste rule, a skill patch, a retrieval
budget, a threshold, or which approved model handles a task class. The current setup is the *incumbent*.

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
responders and graders, exact and keyword graders, a parser for judge scores, preference-choice runs with
randomized order, longitudinal metrics from the ledger (corrections per accepted output, verdict mix,
questions asked) and HTML reports. The split, comparison, gate and scoreboard functions are designed, not
built. No eval scores have been measured on the new core yet.

#### 21.6 Cost

The reviewer skips turns with no new signal. Classification runs on rules or a small local model. Only
survivors reach a larger model, in batches. The fast loop runs only pinned cases and checkable rules for the
touched domain, mostly with no model calls. The full eval and the climb run at night on an idle machine,
capped at 10 candidates and 3 judge samples per item by default, under a monthly ceiling the person sets.
Exo reports cost per accepted lesson. Every stage has a rules-only fallback, so the loop runs, more weakly,
with no model at all.

### 22. One surface

**Status.** A line client, `exo chat`, is **[built]**: it renders streamed text, tool calls and output, and
answers approvals. The terminal and web clients and model-summoned panels are **[designed, not built]**.

Exo ships one surface instead of a command-line tool plus a separate app. The base is a terminal-like prompt
and transcript. Three prefixes and one palette are the whole learning curve: `/` for commands, `@` to attach
context (a file, a line range, an agent, a skill), `!` for a shell command, and `ctrl+p` for a fuzzy palette
of everything else. Finished turns collapse to their answer. Full tool output is one key away. A one-line
status shows agent, model, mode, context use and cost.

Richer elements are *panels*: a diff viewer, a task board, an approval card, a taste card, a session tree, a
chart, a file preview. The model opens one with a `ui` tool, which will emit a `ui.panel` event. Each client
draws what it can: the web and desktop clients draw the full panel, the terminal a compact form or a link, a
device a card or a spoken line. Clients announce what they can draw in `hello` (`capabilities`, screen
columns), and the core shapes output to match. The person can always open or close a panel by hand. Those
choices feed the learning loop, because an ignored panel teaches as much as an opened one. Deciding when to
offer a panel is a decision-model job (section 13).

The design borrows from three places. From opencode: one server, thin clients, the three prefixes, the
palette, and keeping terminal taste (theme, keys) out of project config. From Command Code: inline, editable
taste rows and a tiny input surface. From Hermes Agent: a typed wire between core and renderer, approval
prompts that survive a reconnect, steer and queue while busy, and a live view of running subagents.

### 23. Provenance and selective upstream rebasing

**Status.** **[built]**, with tests: `PROVENANCE.toml`, the consistency check, and `exo upstream report`,
`mark`, `check` and `merge`. Running the report weekly as a scheduled job waits for cron in wave 3.

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

### 24. Privacy and security model

**Local first.** The store, ledger and sessions are files on the person's machine. Embeddings and the
default decision model are planned to run locally. Nothing in the store is pushed anywhere without the
person's explicit approval each time.

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

**Gates.** Reversible, private actions run and are logged. Irreversible, public, financial or third-party
actions wait for the person. The operator floor cannot be lowered by the person's own policy, and a decision
model can only tighten.

**Integrity.** The ledger is signed and chained, append-only is enforced by database triggers, and keys sit
in `0600` files. Signatures mean trust need not rest on whoever hosts the files.

**Transport.** The Unix socket is owner-only. TCP requires a bearer token compared in constant time. TCP has
no TLS today, so it is for localhost or a trusted network. Provider keys come from environment variables
only, never from config files.

**Known limits.** Privacy covers what a model can infer from text, not only what is stored, and auditing
outbound text for inferable facts is unsolved. Hosted model providers see what is sent to them. Exo can
choose what to send, not what they keep.

### 25. Portability toward a companion device

The long-term aim is a small portable device, something like a pocket companion, that carries a person's
store and grows with them. The near-term design rules that keep that path open:

- **The store is one folder.** Pack, nodes, items, ledger and indexes are plain files and SQLite. Copy the
  folder and the person moves.
- **The core stays small.** Under 8,000 lines now (7,631), with a target of under 15,000 after wave 3,
  enforced by tests. Two runtime dependencies. No Node, no browser engine, no containers. The planned idle
  memory target is under 80 MB **[not measured]**.
- **A small-model path from day one.** The OpenAI-compatible transport already talks to local servers
  (llama.cpp, Ollama, LM Studio, vLLM), and routing can send routine calls to a local model.
- **Every learning stage has a rules fallback.** Proposals can queue on the device and be processed later by
  a larger model when one is reachable.
- **The protocol suits a weak link.** Numbered, replayable events, a capability handshake, and questions as
  events that can be answered by a button or by voice.

Wave 5 audits footprint, runs the core on a Raspberry Pi class board with an empty store and with a real
store, and adds a thin device client **[designed, not built]**.

### 26. Walkthrough: one turn through every layer

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
   then sends it. A web client open on the same session sees the same events with the same numbers.
   *(Mirroring the turn into the signed ledger as an `utterance` is wave 2.)*
3. **Memory.** The loop asks the memory slot for its pack block and for recall on "fix the typo in README.md
   and push". The pack says, among other things, that pushing is public and needs approval, and that commit
   messages are short. *(The memory plugin loads today. Its full behaviour on the new core, and the staged
   retrieval pipeline of section 20, are later waves.)*
4. **Context.** The context engine schedules leaf summaries for any full chunk of 8 messages older than the
   tail, waits for pending live summaries to settle, and builds the input: system prompt, the summary view
   labelled as background, then the last messages verbatim. If a summary finishes during this, a
   `context.compacted` event reports the token counts before and after.
5. **Model.** The loop streams from the `main` role. The router picks the first route whose credential is not
   cooling down. Suppose the first provider answers 429 before sending anything: the router records the
   cooldown and moves to the next route without the person seeing a thing. Text streams as `message.delta`
   events. The model asks for `edit_file`. The router prices the usage, and the loop emits `usage.updated`
   and then `message.completed` for the assistant message.
6. **First tool.** The loop emits `tool.started` (risk `medium`). The engine runs the registry, which fires
   `pre_tool_call`. The gate classifies a local file edit as reversible and private, so it allows and logs.
   The fuzzy matcher finds the typo even though the model's quoted line has different spacing. The loop emits
   `tool.completed` with the duration, and stores the result as a tool message (`message.completed`).
   *(On the new core today, a plugin's `pre_tool_call` hook can allow, block or ask, and that path is
   tested. Running the gate's full policy there is wave 2.)*
7. **Second round.** A fresh input is built and streamed. The model asks for `bash` with
   `git commit -am "Fix README typo" && git push`. `tool.started` goes out with risk `high`.
8. **The gate.** The gate splits the command at `&&`, sees `git push` to a shared remote, and classifies it
   as public. Policy says ask. The engine emits `approval.requested` with a summary ("push branch to
   origin"), the risk and the call. The terminal shows a compact approval line. A phone client could show a
   card. The person presses allow. The client sends `approval.respond`. The engine emits `approval.resolved`
   and fires `post_approval_response`. *(Writing the signed action record for this approval is wave 2.)*
9. **Shell.** The shell tool runs with a timeout in its own process group, streaming output as
   `tool.output` events, then `tool.completed`.
10. **End of turn.** The model writes a short final reply. The loop emits `turn.completed` with stop reason
    `end` and total usage, tells the memory slot about the exchange, and emits `session.updated`.
11. **After the turn.** *(Designed, not built.)* Suppose that, earlier in this session, the person had
    rewritten one of the agent's commit messages to be shorter. That edit is a `verdict: edit` in the ledger.
    The reviewer wakes, classifies it as taste in the domain of writing, finds an existing medium-weight
    belief "commit messages are short and imperative", and proposes reinforcing it with this evidence. The
    new evidence comes from a second session, the rule is unguarded, and the weight crosses 0.75, so it is
    promoted, shown inline as a taste row with a link to its file, and listed in the next brief. Had it been
    a skill change, it would have waited for the nightly eval and been kept only if it beat the incumbent on
    held-out cases.

If the network drops at step 7, the terminal reconnects with `events.subscribe` and `since` set to the last
`seq` it saw, receives every event it missed, and the approval request is still open for it to answer.

---

## Part III. Status and roadmap

### 27. Built and tested

Verified on 2026-10-11 by running `.venv/bin/python -m pytest -q` from the repository root: **396 passed,
61 skipped**, in about 31 seconds. All 61 skips are contract tests against a Hermes Agent checkout, which
skip when no checkout is configured. Tests that live inside the plugin folders are not part of this default
run.

| Area | Tests collected | What they cover |
|---|---|---|
| Core (`tests/core`) | 99 | types and codec, both transports from recorded fixtures, router fallback and cooldown, fake provider, tools (fs, patch, search, shell timeouts), loop (tool rounds, interrupt, compaction, resume, fork), plugins and slots |
| Protocol (`tests/protocol`) | 38 | schema, caps, strict decoding, export to JSON Schema and TypeScript, two clients, replay with `since` |
| Integration (`tests/integration`) | 8 | the wave 1 acceptance run with a hard kill and resume, plugin hooks that block and ask, the context slot, the line budget, dependency list, no owner names |
| Upstream (`tests/upstream`) | 19 | provenance consistency, report and mark on a fixture repository |
| Bus (`tests/bus`) | 92 | the signed agent-to-agent relay, client, wire format |
| Ledger, store, crypto, CLI | 33 | signing, chain verification, append-only triggers, store lint and search |
| Consolidate, ask, eval, brief | 48 | proposals, rule of three, taste, question queue and value of information, eval runs, brief |
| Onboard, ingest, install, orchestra | 52 | onboarding interview, five history importers, installer, role templates |
| Acceptance (`tests/acceptance`) | 7 | an example store: lint, search, budgeted context |
| Hermes contracts (`tests/contract`) | 61 | skipped in this run |

What that means in plain terms:

- `exod` runs over a Unix socket, TCP with a token, or stdio, with numbered, durable, replayable events.
- A multi-turn session with real tools runs end to end, survives a hard kill, and resumes.
- The router falls back and cools down as described. Anthropic and OpenAI-compatible transports stream.
- The context engine builds each turn from a summary tree and a verbatim tail, in two lanes, and settles.
- Sessions fork, resume, undo and are searchable.
- Plugins load with rollback. The six existing Exo plugins register on the new core.
- The signed, hash-chained ledger, the version 1 store, keyword retrieval, the gate, capture,
  consolidation, taste weighting, the question queue, the eval runner, the brief, the onboarding interview,
  history importers and the bus exist as modules with tests. Most are not yet wired into the new core.
- Provenance is tracked and checked. The upstream report works on a test repository.

### 28. Designed, not built

| Feature | Section | Planned wave |
|---|---|---|
| Ledger mirror of every core turn, and signed action records for approvals | 19, 26 | 2 |
| The six plugins doing their full jobs on the new core, a skills loader and an MCP client | 17 | 2 |
| Commands `question.answer`, `config.get`, `config.set`, `tasks.list`, `seat.list`, `taste.list`, `taste.edit` | 11 | 2 to 3 |
| Store version 2: kinds, nesting, `STORE.toml`, items table, cross-store links, health lint | 18 | not yet scheduled to a wave |
| Staged retrieval: router pins, FTS5 index, local embeddings, fusion, graph expansion, retrieval events | 20 | not yet scheduled to a wave |
| Decision models: typed interface, providers, calibration, `decision` events, exo-decide | 13 | 2 to 3 |
| `exo.learn`: reviewer, typed proposals, promotion policy, held-out eval gate, climb, scoreboard | 21 | 3 |
| Terminal and web clients, `ui` tool, `ui.panel` event | 22 | 3 |
| Cron, subagents and a task board, with the weekly upstream report as a job | 23 | 3 |
| Chat channel adapters, editor protocol adapter, history cutover | 29 | 4 |
| Remote transport over the bus or a tunnel, and TLS | 11, 24 | 4 to 5 |
| Snapshot undo of files | 16 | not scheduled |
| Device footprint audit, device client and idle memory measurement | 25 | 5 |

Nothing in this paper reports a benchmark, an accuracy figure or a speed-up for Exo itself, because none has
been measured. The numbers quoted for decision models and embedding models come from their vendors' own
documentation and are marked as such.

### 29. The five build waves

Work proceeds in waves. In each wave, parallel agents own disjoint paths and do not commit. A lead reviews,
runs the full suite, and commits. Each wave has an exit test.

| Wave | Goal | Exit test | State |
|---|---|---|---|
| 1. Core skeleton | loop, providers, tools, sessions, slots, plugin loader, protocol server, provenance, `exo upstream` | a multi-turn session with tools persists, resumes after a kill, and streams the same events to two clients, the old suite stays green, and the core is under 8k lines | done |
| 2. Re-home | the six plugins on the core, skills loader, MCP client, seat config, ledger mirror | a correction survives compaction word for word, the gate blocks a public action until a client answers `approval.requested`, and a bus round trip verifies | next |
| 3. UI and learning | terminal and web clients, panels, `exo.learn` with the eval gate, cron, subagents and tasks | a full working day in the new terminal client, and a lesson promoted only after it scores better on held-out cases | planned |
| 4. Channels and cutover | chat adapters lifted with provenance, editor adapter, history import, real users moved over one by one with rollback | each move done with a working rollback | planned |
| 5. Device | footprint audit, local small-model defaults, core on a small board, thin device client | the core runs on the board with an empty store and with a real one, and the device client chats and approves | planned |

### 30. Open questions

1. **Spend.** The monthly ceiling for the nightly eval and climb, and for hosted decision models, is the
   person's call and is not set by default beyond "small".
2. **The judge.** Which model family judges the eval, and how many samples make a score stable enough for one
   person's small eval set. Small sets are noisy. The noise band in section 21.5 may reject most changes
   until the set grows.
3. **Auto-promotion.** Whether unguarded taste with two sessions of evidence should promote automatically
   (the current default) or wait for review in the brief.
4. **Cold start.** How a new user's empty eval set is seeded without leaking anyone else's data, and how
   soon the climb can run safely.
5. **Decision models.** Whether a fine-tuned small model beats rules and hosted models on real decisions,
   and how to calibrate with few labels. Unmeasured.
6. **Embedding model.** EmbeddingGemma's licence terms versus Qwen3-Embedding's Apache 2.0, and quality on
   mixed-language text. Unmeasured.
7. **Terminal client stack.** Python (Textual) for one language end to end, or a TypeScript renderer. The
   protocol makes this reversible. A spike in wave 3 decides.
8. **Backup.** Local only, or an encrypted copy on a private remote.
9. **Inference privacy.** How to audit outbound text for facts a model could infer, not only facts that are
   stated. Unsolved.
10. **Weights.** When, if ever, to move taste from the pack into a per-person adapter. The ledger is kept in
    a training-ready form so this stays possible.

---

## 31. Related work and acknowledgements

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

**Decision and embedding models.** TypeSafe (Jev), Convai Innovations (Laya), Perplexity, OpenAI, Google
(EmbeddingGemma) and the Qwen team, whose public documentation informed sections 13 and 20.

Exo's own earlier knowledge-base, ingest and team-memory systems contributed the store format, the router
discipline, the item layer and the event-sourced team state described in section 18.

---

## 32. Glossary

- **Append-only.** Data that is only ever added to, never changed or deleted.
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
- **Hash.** A short fingerprint of data that changes completely if the data changes at all.
- **Hash chain.** A sequence of records where each contains the hash of the one before, so tampering breaks
  the chain.
- **Held-out set.** Test cases the learning process never sees, used only for the final keep-or-drop.
- **Hill climbing.** Improving step by step, keeping a change only if it scores better.
- **Hook.** A named point where a plugin can watch or change what the core is doing.
- **Ledger.** Exo's signed, hash-chained, append-only record of events.
- **Noisy-or.** A way to combine evidence: one minus the product of (one minus each strength), read as the
  chance that at least one piece of evidence holds.
- **Pack.** The one page about the person that is loaded on every request.
- **Principal.** The person Exo works for.
- **Principal store.** The folder holding everything Exo knows about one principal.
- **Proposal.** A typed lesson with evidence and confidence, waiting to be promoted or dropped.
- **Provenance.** The recorded origin of a piece of code or data.
- **Reciprocal rank fusion.** Merging ranked lists by summing 1 divided by (60 plus the rank) across lists.
- **Retrieval.** Choosing which stored notes to show the model for a given request.
- **Server-Sent Events (SSE).** A way for a server to stream events to a client over one long HTTP response.
- **Signature.** A code made with a secret key that proves who wrote a record and that it was not changed.
- **Slot.** An exclusive plugin seat. Only one memory provider and one context engine can be active.
- **Summary tree.** Older messages summarized in leaves of 8, merged pairwise into higher levels as needed.
- **Token.** A piece of a word. The unit models read and are billed by, about four characters in English.
- **Verbatim tail.** The most recent messages, sent to the model word for word.
- **View.** Anything computed from the ledger that can be rebuilt from it.
