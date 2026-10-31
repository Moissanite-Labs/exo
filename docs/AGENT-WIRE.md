# Agent wire: how Exo agents talk to each other

Status: design plus first build, 2026-10-12. Author: agent-wire research and build agent, for the lead.
Inputs: the upstream repo `aska-digital/protean-sym2p` at `2d93ed6` (README, `SPEC-V2-DRAFT.md`, `VISION.md`,
`bench/`, `scripts/`, tests), Exo's vendored copy `src/exo/protocols/sym2p/`, `src/exo/bus/`, the standalone
core (`src/exo/core/`, `src/exo/protocol/`), `docs/STANDALONE.md`, `docs/CORE-V2-INTERFACES.md`,
`DECISIONS.md` (D-001 to D-015), `research/DECISION-MODELS.md`, `research/LEARNING-LOOP.md`, and Hermes's
delegation code (`tools/delegate_tool*.py` at `dce1e9b`).

A's ask, verbatim: "project exo should integrate this tech, or a better implementation of its concept, in
its backend systems for interagent communcation, delegation, and wherever else appropriate/relevant:
https://github.com/aska-digital/protean-sym2p"

## 0. The answer in one paragraph

Exo adopts SYM-2P as its machine wire for delegation and seat-to-seat traffic, with a better version of the
idea built on the upstream format. Code writes, checks, stores, signs and replays compact SYM-2P lines.
Models never write them and never read them: a model reads a short plain-English rendering made from the
packet by a fixed renderer. The reason is a measurement. With real tokenizers, the dense line is the
smallest form in bytes but not in tokens: a receipt costs 121 tokens as a line and 83 tokens as English
(o200k), and a model would also need a 193-token legend to read lines at all. Upstream's density gain over
compact JSON, 3.1x in bytes, is 1.7x in tokens, and against a terse English sentence it turns into a loss. So the wire's real value in Exo is not fewer tokens for models. It is checkability:
every brief and receipt is a typed packet that validates strictly, carries limits the harness enforces,
signs with the existing Ed25519 keys, and renders the same English every time. The first piece, a
`delegate` tool on the standalone core, is built and tested (section 6).

## 1. SYM-2P from first principles

Agents that hand work to each other need a message format. The common choice is English or JSON. English is
easy for models and people to read, but a program cannot check it: "do not touch the tests" is a sentence,
not a rule. JSON adds structure, but repeats key names in every message and still puts the meaning in
English strings.

SYM-2P (the "Protean" agent message language, our own org's repo) makes four moves:

1. **One packet is one line.** Lines append to logs and files cleanly, and a reader can process them one at
   a time.
2. **Fields by position, not by name.** `V|ID|TK|F|T|A|ST|CF|CV|BODY|RQ|BASE|EXP` means: version, packet
   id, thread, from, to, act, state, confidence, coverage, body, request, base reference, expiry. No key
   names travel.
3. **Codes instead of words.** Roles, acts and states are small integers from fixed tables: role 2 is
   "builder", act 11 is "delegate", state 6 is "closed". Unknown codes are errors, so a typo cannot slip
   through.
4. **A symbolic body.** The body is a list of s-expressions, nested lists in parentheses where the first item
   is an operation code: `(10 sym-2p/1.0 (20 sec (3 5)))` means "scope: implement sections 3 to 5 of
   SYM-2P/1.0".

It adds a validator with typed errors (`ERR:CODE`, `ERR:BODY` and so on), a renderer that turns packets into
English for operators, and an optional 14th field, a 16-hex checksum ("-rcp" edition), that detects a
packet damaged in transit. Upstream's bet, stated in `VISION.md`, is larger: that models reason and
coordinate better in symbols than in English. Upstream ships a harness to test that bet (`bench/EVAL.md`)
but has not run it against models yet, and says so.

### 1.1 How current Exo's copy is

Exo vendored the library in phase 4 (`src/exo/protocols/sym2p/`, NOTICE pins commit `2d93ed6`). That commit
is still upstream `main` today, and the registries match byte for byte. So the copy is current. One local
change: the grammar patterns end in `\Z`, not `$`, because `$` also matches before a final newline and let an
id, ref or hash such as `"builder\n"` pass. It holds
the normative 1.0 JSON profile (`v1.py`) and the 2.0 draft dense lines with the v2.1 reasoning extension and
the -rcp edition (`v2.py`). Before this work, only the bus used it, as an optional message body
(`exo.bus.client.post_sym2p`, `exo-bus` plugin). Nothing in the standalone core used it.

Upstream's own limits matter for Exo:
- The body holds only numbers and symbols. It cannot carry a task described in words, and real
  delegation tasks are words ("add a --dry-run flag, follow the argparse style, keep it under 60 lines").
- The opcode table covers one toy domain (sections of a spec, "modify no paths", "validator exits 0").
- v2 is a draft. Signing (open item O-1), negotiation and a model eval are open.

## 2. Where agent-to-agent traffic happens in Exo

The question for each channel is who writes the message, who reads it, and what goes wrong.

| Channel | Writer | Reader | Wire helps? | Verdict |
|---|---|---|---|---|
| Delegation brief, parent to child | parent model (as tool-call JSON, fixed by provider APIs), then the harness | child model; the harness; clients | Yes for the harness record: limits, toolset, depth become checked fields. No for the child model, which reads English. | **Built.** Brief packet is the record; the child gets its English rendering. |
| Receipt, child to parent | harness (status, usage, time, tools, files) plus the child's summary | parent model, every later round; clients; the board | Yes for the record and for honesty: numbers come from the harness, not the model. The parent model reads English (fewest tokens, section 3). | **Built.** |
| Tasks board (`tasks` table, `task.updated`, `tasks.list`) | harness | clients, replay, audit | Yes: one compact canonical line per brief and receipt, signed, replayable. People see `detail` in English. | **Built.** |
| Seat-to-seat bus messages | seat harnesses | other seats' harnesses | Yes: bodies are machine-checked before send and after receipt; the bus envelope already signs. | **Built for bodies** (`bus.wrap` accepts Exo lines); cross-seat delegation is wave 4. |
| Learning-loop reviewer lessons (D-012) | reviewer model | eval, the principal | Little: lessons are prose claims with evidence ids, already typed JSON events in the signed ledger. | **No.** When the reviewer runs as a child (wave 3), its run uses brief and receipt; the lesson stays a ledger event. |
| Decision-model calls (D-015) | harness | Laya, Jev, Perplexity, OpenAI | No: these models read short English state and return probabilities over a provider's own JSON API. Symbols would hurt their accuracy. | **No.** |
| Handoffs (`exo.handoff`) | model | the next session and people | No: people read them. | **No.** |
| Context summaries (`exo.core.context`) | aux model | main model | No: summarizing is a meaning task; symbols lose nuance and the lifted prompt is tuned for prose. | **No.** |
| Tool calls, model to harness | model | harness | Not changeable: providers mandate JSON arguments. | **No.** |

The pattern: the wire pays off where code is the reader. Where a model or a person is the reader, English
wins on tokens and on errors.

## 3. Measurements

Script: `scripts/wire_bench.py` (run it with a Python that has `tiktoken` and `tokenizers`; it falls back to
bytes only). Tokenizers used, all offline: OpenAI `o200k_base` and `cl100k_base`, a 151,669-entry vocabulary
of the Qwen3 size (from the local `pplx-embed-v1-0.6b`), and the local `edge0-8b` tokenizer. Claude's
tokenizer is not public, and counting with Anthropic's API would need network and spend, so it is not
included. The samples are realistic Exo delegation messages (a scout brief, a builder brief, three
receipts) plus upstream's own bench corpus.

### 3.1 Results (o200k_base tokens; bytes in brackets)

| Sample | Hermes JSON today | Exo JSON (fallback) | Exo line | Exo line, -rcp | Exo English |
|---|---:|---:|---:|---:|---:|
| scout brief | 43 [148] (args only) | 106 [335] | 81 [171] | 92 [188] | 55 [171] |
| builder brief | 122 [503] (args only) | 196 [740] | 181 [593] | 189 [610] | 156 [619] |
| scout receipt | 180 [535] | 148 [390] | 121 [201] | 132 [218] | **83** [199] |
| builder receipt | 337 [1,069] | 196 [608] | 174 [424] | 186 [441] | **135** [431] |
| timeout receipt | 524 [1,599] | 138 [405] | 113 [224] | 124 [241] | **76** [227] |
| legend a model needs to read lines | | | 193 [576] | | 0 |

The other tokenizers agree in direction; the open-weight vocabularies punish the line more (scout receipt:
line 144 to 148 tokens, English 93 to 97).

Upstream's six paired bench packets: compact v1 JSON 1,359 bytes against v2 lines 433 bytes, a 3.1x gain.
In tokens the same pairs are 475 against 274 (1.7x) with o200k and 498 against 321 (1.55x) with the Qwen3-size
vocabulary. Bytes overstate the gain because each digit, pipe and parenthesis tends to be its own token.

### 3.2 What the numbers say

- **For models, terse English beats the dense line.** On every receipt the English rendering is 22 to 33
  percent fewer tokens than the line, and it needs no legend. The line repeats ids (packet, thread, base,
  child session) and numeric fields the model does not act on; the English form keeps only what the model
  uses.
- **The big saving is against today's Hermes receipt, and it comes from content, not encoding.** Hermes
  returns a JSON receipt with a per-call tool trace. Three receipts cost 1,041 tokens there against 294 in
  Exo English. A parent that keeps them in context for 20 more rounds re-reads about 20,800 tokens against
  5,900. Choosing what to say mattered more than how to encode it.
- **For storage and the bus, the line is the compact form.** A receipt line is 201 to 424 bytes, 52 to 70
  percent of the canonical JSON form (390 to 608 bytes); the checksum adds 17 bytes.
- **Briefs from the parent model cannot use the wire.** The parent writes tool-call JSON (43 tokens for the
  scout brief) because provider APIs require it. The harness then adds limits, depth and toolset, so the
  model pays only for the goal it wrote.
- **Speed is not a factor.** Strict encode takes about 41 microseconds, strict decode 55, English render 1.4
  (Python 3.14, this machine). A model call takes seconds.
- **Parse error rates are not measured.** With the fake model, every packet is written by the harness, so a
  zero error rate there proves only that the code is consistent. Whether real models can emit valid lines
  needs upstream's compile tasks (`bench/EVAL.md`) run on real models, which costs money and was not done.
  The design does not depend on the answer, because no model has to emit a line.

## 4. The design: Exo's wire (`exo.wire`)

Exo keeps the upstream line exactly (13 fields, or 14 with the checksum) and fixes the limits inside it.
Pure upstream lines still validate with the vendored upstream validator, unchanged.

### 4.1 What Exo adds to the line

- **String atoms.** A body may hold JSON string literals, so a goal travels in words:
  `(101 "Find every caller of load_config")`. A pipe inside text is written `|`, so splitting a line on
  `|` stays trivial and safe.
- **Exo vocabulary in the 100 to 199 band**, the range upstream's draft proposes for domain extensions. The
  first expression `(100 1)` names the vocabulary version. Codes: 101 goal, 102 context, 103 allowed tools,
  104 limits (tool rounds, tokens, seconds), 105 depth (this level, maximum), 106 working directory, 107
  done-when criterion, 110 summary, 111 usage (tokens in, tokens out, cost in micro-dollars), 112 work
  (milliseconds, tool rounds, tool calls), 113 files changed, 114 error (code, reason), 115 child session.
  Integers only, so no float rounding differs between machines.
- **Two task states** the upstream table lacks: 100 failed, 101 cancelled. Exo task status maps one to one:
  queued 1, running 2, done 6, failed 100, cancelled 101. These are the same five words as the protocol's
  `task.updated` status, and a test keeps them equal.
- **Loud failure for old readers.** An upstream validator rejects an Exo line ("unknown opcode 100") instead
  of misreading it, which is the same rule upstream used when it added the checksum field.

A brief and a receipt as they are stored:

```
2|k_3f9a2c1d|s_8e7d6c5b|1|4|11|1|0|0|(100 1)(101 "Find every caller of load_config and list them as file:line.")(103 read_file grep glob)(104 12 60000 300)(105 1 1)|0||300
2|r_3f9a2c1d|s_8e7d6c5b|4|1|12|6|0|0|(100 1)(110 "3 callers: src/exo/cli.py:52, src/exo/onboard.py:118, src/exo/core/engine.py:342.")(111 18240 388 21300)(112 9400 3 4)(115 s_1b2c3d4e)|0|k_3f9a2c1d:1|0
```

What the parent model and every client see for the receipt:

```
Done: task k_3f9a2c1d by a scout in 9.4 s, 4 tool calls over 3 rounds, 18,240 tokens in and 388 out, $0.0213.
Result: 3 callers: src/exo/cli.py:52, src/exo/onboard.py:118, src/exo/core/engine.py:342.
```

### 4.2 Improvements over upstream

| Upstream gap | Exo's answer |
|---|---|
| No free text in bodies | String atoms with pipe escaping (4.1). |
| No schema shared with the program | Typed `Brief` and `Receipt` dataclasses in `exo.wire.packets`; statuses equal the protocol's `task.updated` values. |
| Validator only | Strict `decode` (rejects anything non-canonical) plus `repair`, which fixes only meaning-preserving damage: surrounding text, code fences, leading zeros, body spacing. It never repairs a bad checksum, an unknown code or a missing field; those are refused, because guessing would change meaning. |
| One version literal, no negotiation | The `(100 N)` vocabulary marker, and `negotiate(mine, theirs)` over advertised formats (`sym2p/2+exo1`, `json`), falling back to JSON when nothing matches. |
| Checksum only; signing open (O-1) | Detached Ed25519 signatures over the exact line bytes with Exo's existing keys (`exo.crypto.Signer`), stored with each receipt. Bus envelopes are already signed and hash-chained, so the checksum is only needed on unsigned paths. A caller pins the key: `verify(line, sig, trusted_pub)` checks the signature against `trusted_pub` only, and returns False when none is given. The `pub` a signature carries is never trusted on its own, because whoever made the packet chose it (exo-desktop #17). |
| Renderer wording is "informative" | A deterministic renderer: same packet, same English, every time. Tests cover every status and every opcode. |
| A model that cannot write a line has no path | No model writes lines in Exo. For peers that cannot speak the line, the same packet has a strict canonical JSON form; `decode_any` reads line, repaired line or JSON and reports which, so repairs and fallbacks can be counted. |

### 4.3 The human rule

Anything shown to a person renders in plain English. `task.updated` carries `detail` (English) for display and
`packet` (the line) for replay and audit; clients show `detail`. `exo wire render` turns any packet into
English for someone debugging, and `exo wire check` prints the typed errors. Bus bodies holding Exo lines
render through the same renderer, and a damaged one shows a short notice, never raw packet text.

### 4.4 Costs and risks

- **Debugging is harder with dense lines.** Mitigated by the CLI, by English in every event, and by never
  asking a person to read a line.
- **Upstream v2 is a draft.** If upstream changes the line shape, Exo's vendored copy and the `(100 1)` marker
  keep old packets readable; a new vocabulary version would be `(100 2)`.
- **Tool names must be symbols** (letters first, then letters, digits, `_ . / -`). The delegator leaves
  other names out of a child's toolset rather than fail; every current Exo tool qualifies.
- **The upstream bet is untested here.** If an eval later shows models reason better reading lines, the
  child's first message can switch from English to the line plus legend behind a setting. The packet stays
  the same, so nothing else changes.

## 5. Versioning and compatibility

- The line form is versioned twice: `V` stays `2` (upstream), `(100 N)` versions Exo's vocabulary. A reader
  rejects a vocabulary it does not know.
- The protocol change is additive: `TaskUpdated.packet: str = ""`. Old senders stay valid; clients decoding
  with `strict=False` (the documented client mode) accept it. Generated JSON Schema and TypeScript types are
  re-exported.
- The old import path `exo.protocols.sym2p` is unchanged and still checks pure upstream packets. Its bus
  helpers now also accept Exo lines.

## 6. What is built: delegation on the standalone core

`docs/STANDALONE.md` planned it in one line: "A subagent is a child session on the same core with a
narrower toolset." Code: `src/exo/core/agents/delegate.py` (306 lines), wired into `CoreEngine` with a few
lines; the codec lives outside the core in `src/exo/wire/` because the bus and the ledger share it.

**The flow.** The parent model calls `delegate` with one `goal` or a list of `tasks` (goal, context, role,
tools, accept). For each task the delegator:
1. builds a `Brief`, narrows the toolset, applies the limits, and checks that it encodes as a valid packet;
2. records a `tasks` row (status queued) and publishes `task.updated`;
3. waits for a slot, creates a child session (`parent` set to the parent session), and runs a child `Loop` on
   the same store, hub, router and tool runner, so approvals and plugin hooks (exo-gate) apply to the child's
   tool calls exactly as to the parent's. The child loop also shares the engine's per-session state, so the
   engine sees the child's turn running. `session.list` shows it `live`, and `session.resume` leaves its open tool
   call alone. The principal typing into the child's session (`turn.send`, any mode) steers it (D-070). Any other
   caller gets `forbidden` there (D-052): the parent uses `subagent send`. While the child starts or finishes, no
   round can take a steer, so `turn.send` fails with `conflict` and never starts a turn in the child.
   An unaddressed peer message never picks a child;
4. gives the child a focused system prompt (lifted from Hermes) and the brief in English as its first message;
5. builds the `Receipt` from what the harness measured, signs it if a signer is set, stores it, publishes
   `task.updated`, and returns the English receipt to the parent model. Several tasks are labelled
   "Task 2 of 3: ..." so the parent can match them.

**Roles and tools.** Roles are upstream's: builder, scout, verifier, critic. Scouts and critics get only
low-risk tools (read, glob, grep). A child never gains a tool its parent lacks. Blocked for children, from
Hermes's list: delegate (unless depth allows), clarify, ask, memory, send_message, bus_post, cron, and the
memory, bus, delegation and kanban toolsets. The parent can narrow further with `tools`. A child calling a
tool outside its list gets an error result.

**Limits** (config `delegation` in `config.json`; defaults in brackets): depth [1, flat, as in Hermes],
tasks per call [10, Hermes's default], concurrent children per depth level [4], tool rounds per child [24],
tokens per child [200,000], wall time per child [900 s], summary length [6,000 characters]. A separate
slot pool per depth level means a parent waiting on its children can never starve them of slots. The token
budget counts what the child's calls newly process: uncached input, cache writes and output. Cache reads do not
count, so re-reading a cached context each round does not spend it, on any provider (D-080). The receipt's
tokens in are the same count: uncached input plus cache writes.

**Cancel.** Interrupting the parent's turn cancels its `delegate` call, which interrupts every child loop and
waits for it to stop cleanly; a child's own `delegate` call is cancelled the same way, so the whole tree
stops. `turn.interrupt` on a child session stops just that child, and the parent gets a cancelled receipt.

**Outcomes.** done; failed with a reason code (`timeout`, `turns`, `budget`, `error`); cancelled (`cancelled`
by the parent, `interrupted` by a client). After a restart, the agent's own tasks left queued or running are
marked failed once its core holds the writer lock (`run/agent-<id>.lock`).

**Protocol.** `tasks.list` now works (rows with English `detail`, the brief and receipt lines, signature).
`exo chat` prints `[task running]` and `[task done]` lines.

**Provenance.** `PROVENANCE.toml` records the lift: the child prompt wording, the blocked-tool list and two
defaults from `tools/delegate_tool_progress.py`, `delegate_tool_toolsets.py` and `delegate_tool_config.py`
at `dce1e9b`. A new `[[watch]]` follows Hermes's delegation files for edge-case fixes.

**Tests.** `tests/agents/` (49 tests, fake models only, no network): codec round trips, strict rejection,
repair, JSON fallback, signing, negotiation, renderer coverage, bus bodies, the CLI; and on the engine: one
child end to end, a parallel fan-out held at the concurrency cap, parent interrupt cancelling all children, a
client interrupting one child, timeout, turn and token limits, toolset narrowing, depth limits, nested
delegation, signed receipts, restart recovery and client rendering. The core's counted lines are
reported against a floor, with no ceiling (D-077, D-083, `docs/ENGINE-SPLIT.md` "Budget policy"); the figure the
wave 3 plan set is in `docs/STANDALONE.md`.

### Model tiers

A child should run on the cheapest model that can do its job. The router has three tier roles, `fast`,
`standard` and `deep`, ordered cheapest to most capable. Fallback: fast to standard to main; standard to
main; deep to main. A tier whose provider has no key is skipped, so a local-only setup with no tier
configured runs every child on main and nothing breaks. Catalog defaults (Anthropic): fast is
`claude-haiku-5-5`, standard is `claude-sonnet-5-5`, deep is `claude-opus-5-5`. Override them in config
`[models.roles]`.

- **Default per role** (config `delegation.role_tiers`): scout fast, verifier standard, builder standard,
  critic deep.
- **Per task.** The `delegate` task item takes an optional `tier`. Use fast for retrieval, search, reading and
  summarizing, running known commands and simple specified edits. Use standard to implement a clear spec,
  write tests and docs, or do contained reviews. Use deep only when judgment errors compound: architecture,
  open research synthesis, hard cross-file debugging. When unsure, take the cheaper tier.
- **No upgrade.** A child never runs above its parent's tier, unless `delegation.allow_upgrade = true`
  (default false). The parent's tier is the main model's tier if the catalog knows it, else deep.
- **Escalate once.** If a child fails (not cancelled) and `delegation.escalate_on_fail` is true (default),
  it runs again one tier up, within the cap above. A retry is skipped when the next tier resolves to the same
  model. Both attempts are separate tasks with their own receipts.
- **On the wire.** The brief carries `tier` (opcode 108). The receipt carries `route` (opcode 116: tier,
  resolved model, escalated 0 or 1), and the English form adds a `Model:` line. `task.updated` detail shows
  it. Packets without these fields still decode, with empty tier and model.

## 7. Left for later waves

- **Cross-seat delegation over the bus** (minimal send built; the receiving side is wave 4). Design:
  1. `delegate` takes an optional `seat` per task. With a seat, the delegator builds the same typed `Brief`,
     posts it as a signed bus message (`kind = "directive"`, `thread = brief id`, body a SYM-2P line, through
     `Client.post_sym2p`) and records the task as `queued` with `child = "bus:<seat>"`. It returns at once;
     nothing runs locally. A failed post marks the task failed with the reason.
  2. The sender signs with its seat key, `$EXO_HOME/keys/<seat>.key` (the same file the gate uses for its
     ledger). The relay checks the signature against the registry, so only registered seats can send.
  3. The receiving seat (wave 4) reads `kind = "directive"` messages from its inbox, decodes the brief, runs it
     through its own `Delegator` (its own limits, tiers and gate), and posts the `Receipt` line back in the
     same thread with `kind = "report"`. The sender matches by thread and updates the task row.
  4. Restart: remote tasks stay `queued` (local ones are marked failed), because the work lives elsewhere.
  5. Still open: format negotiation through the seat registry, receipt matching on the sender, a timeout
     for tasks that never answer, and usage from the remote receipt joining the parent's totals.
- **Ledger mirror**: append each signed receipt to the principal ledger as an `action` event, so the learning
  loop and the eval can score delegations (which roles and limits worked).
- **Done in wave 3:** child usage rolls into the parent session total and the parent emits `usage.updated`
  for it; finished child sessions are locked (`SessionStore.lock`, `assert_open`), so a new turn fails with
  a clear error. The lock is a key in the session's meta. Every meta write (`SessionStore.update_meta`) sets its
  own keys and keeps the rest in one write transaction, so a context note or `/compact` that writes at the same
  time cannot drop it (exo-desktop #52).
- **Background delegation** (Hermes's async mode): return at once and deliver receipts later as messages.
- **Worktree isolation** for parallel builders that edit the same repo (Hermes `subagent_worktree.py`).
- **Decision role** (D-015): pick the child role and toolset with a decision model instead of the parent.
- **The model eval**: run upstream's `bench/EVAL.md` on the models A uses, then decide whether any model
  should read lines. Needs spend, so it waits for A.
- **Upstream contribution**: propose string atoms, the 100 band and the two states to protean-sym2p as a
  v2.2 draft, so Exo's profile becomes the upstream format instead of a fork of it.
- **Signer wiring**: `CoreEngine` accepts a signer through the delegator today; reading a seat key from
  config comes with seat config in wave 2.

## 8. Proposed D-016

## D-016 (2026-10-12) Agent wire: SYM-2P lines for machines, English for models and people

- **Decision:** adopt `docs/AGENT-WIRE.md`. Exo's agent-to-agent records and transport use SYM-2P/2 lines
  with Exo's vocabulary (`exo.wire`): string atoms, opcodes 100 to 115 under a `(100 1)` version marker, task
  states failed and cancelled, typed `Brief` and `Receipt` packets, strict decode with safe-only repair, a
  canonical JSON fallback, format negotiation, detached Ed25519 signatures with Exo's keys, and one
  deterministic English renderer. Code writes and reads lines; models and people read only the English
  rendering, and no model is asked to emit a line. First use: the `delegate` tool on the standalone core
  (`exo.core.agents`), where a subagent is a child session on the same engine with a narrower toolset,
  bounded by depth, concurrency, tool-round, token and time limits, with cancel propagating down the tree,
  and every brief and receipt kept in a `tasks` table and published in `task.updated` (English in
  `detail`, the line in `packet`). Bus bodies accept Exo lines. Learning-loop lessons, decision-model calls,
  handoffs and context summaries stay as they are.
- **Why:** measured with real tokenizers, the dense line saves bytes (30 to 48 percent less than canonical JSON) but not model
  tokens: a receipt is 121 tokens as a line, 83 as English, plus a 193-token legend a model would need for
  lines. Upstream's 3.1x byte gain over compact JSON is 1.7x in tokens and becomes a loss against terse
  English. What the wire does buy is checkability: limits, toolsets and outcomes become fields the harness
  enforces and fills from measurement, not prose a model may misstate, and every record signs and replays.
  The upstream format and the vendored validator are kept, so pure upstream packets still work and Exo's
  additions can go upstream.
- **Calls made by the agent (reversible):** delegation is flat by default (depth 1, Hermes's default); 10
  tasks per call; 4 concurrent children per depth level; 24 tool rounds, 200,000 tokens and 900 s per child;
  scouts and critics get low-risk tools only; the child prompt and blocked-tool list are lifted from Hermes
  with provenance.
- **Rejected:** models writing or reading dense lines (more tokens than terse English, plus a legend, plus an
  unmeasured comprehension risk); Hermes's JSON receipts with tool traces (3.5x the tokens of the English
  receipt in the parent's context); upstream v2 unchanged (cannot carry a task in words); a new format from
  scratch (loses compatibility with our own org's spec and validator).
- **Revisit if:** upstream's model eval, run on the models A uses, shows models reason or coordinate better
  reading lines; or upstream adopts or rejects the Exo additions in a v2.2 draft.
