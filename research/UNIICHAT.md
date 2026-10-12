# UniiChat: what it is, how it works, what Exo should take

Research for Project Exo, 2026-10-11. Sources: the local install (`~/.local/bin/unii` v1.0.138,
`~/.unii/check.json`), the JavaScript bundle embedded in that binary (extracted to a scratch dir and read
as data, never run), `unii help`, the live site (uniichat.com, /pricing, /install.sh), A's
`aska-digital/optchat-memory` (README, DESIGN.md) and `plugins/exo-memory` in this repo.
The sign-in token was not read. No session was started.

Tags: **[verified]** means I read it in code or saw it run. **[inferred]** means a reasonable reading of
the code that I did not test. **[unverified]** means I could not check it.

---

## 1. What it is

- UniiChat is "the one chat you keep forever". One endless chat per user, never a new session. The memory
  is a binary tree of one-line summaries over a verbatim log. [verified: hello.md, prompt.txt]
- It is by the Higher Order Company (Victor Taelin, of Bend and HVM). The privacy policy contact is
  `contact@higherorderco.com`; the update check goes to `bend-lang.com`; credits are "Bend Credits".
  [verified]
- It is the production form of Taelin's **OptChat** spec. The code still says so: config lives in
  `~/.opt`, env vars are `OPT_*`, the MCP server calls itself `opt`. A's optchat-memory plugin was built
  from the same spec, so UniiChat is the "full" version of what A ported into Hermes. [verified]
- Three surfaces share one server: the `unii` terminal, a web app at uniichat.com, and an iOS app (APNs
  push and App Store purchases are wired in). [verified for terminal and web; iOS from code and privacy
  text only, unverified in use]
- It is marked experimental: "Expect bugs. Money you add may be lost." [verified: site]

## 2. Stack

- **One Bun single-file executable** (Mach-O arm64, 63 MB). Bun's JavaScriptCore runtime plus a
  `__BUN` segment holding the app. Links only system libs (libicucore, libresolv, libc++). [verified:
  otool, strings]
- The app is TypeScript bundled to plain, unminified JS (about 9,500 lines). Modules: Clip, Import,
  Computer, Machine, Call, Theme, Names, Md, Pick, Price, View, Anthropic, Config, Claude, AskAI, OpenAI,
  Disk, Cloud, Mail, Watch, Agent, Memory, Tools, Server, Paint, Screen, Term. [verified]
- The same binary is client and server. `unii` is the terminal; `unii serve` is a full server. The web app
  (HTML, CSS, a second JS bundle), legal pages and install script are embedded too. [verified]
- Storage: plain files for chat memory (JSONL plus fixed-width binary indexes), SQLite (`bun:sqlite`,
  WAL) for accounts, billing and ops. Writes are fsynced in batches. [verified]
- Computer use on macOS: a Swift helper is written to `~/.unii/computer.swift`, compiled with `swiftc`
  on first use, and driven with JSON; screenshots via `screencapture` and `sips`. [verified]
- No framework for the TUI. A small hand-written screen and line editor (Screen.ts, Paint.ts). [verified]

## 3. Architecture

```
 terminal (unii) ─┐ WebSocket /ws             ┌─ Anthropic API / OpenAI API
 web app ─────────┼──────────► server ────────┼─ Claude Code CLI (subscription mode)
 iOS app ─────────┘   (cloud or unii serve)   └─ Vercel AI Gateway (overflow only)
        ▲                     │
        └── tool jobs ◄───────┘  shell, read, write, edit, computer run on the device
```

- **The agent lives on the server, the hands live on your machines.** [verified: Tools.ts, Term.ts]
  Each open terminal attaches as a named **device** (macOS LocalHostName) with a work dir
  (`UNII_WORK`, default `~/.unii/work`). Every shell, read, write and edit tool call has a `device`
  argument. The server sends the job over the socket; the terminal runs it and returns the result. Several
  devices can be open at once. Closing a terminal fails its running jobs cleanly.
- **The model sees which devices are open now** as a block at the start of each turn, plus "Agents at
  work now". [verified]
- **Tools are served over MCP.** The server runs a local JSON-RPC MCP endpoint on 127.0.0.1 with one URL
  per chat and per agent. Both its own agent loop and Claude Code subprocesses call the same tools.
  [verified]
- **Three model backends.** `claude-api` and `openai-api` run Unii's own loop (Agent.ts). `claude-sub`
  spawns the `claude` CLI in stream-JSON mode, with Claude Code's memory, CLAUDE.md, git instructions,
  slash commands and telemetry turned off, permissions bypassed, and only WebFetch and WebSearch left
  native; all other tools come from Unii's MCP server. It can rotate between several Claude accounts and
  resume a cut call with "(That try was cut off by an account switch. Go on.)". [verified]
- **Retries.** Background calls are "patient": they nap 60 s and retry until they work. On 429, 503 or
  529 a call can fall over to Vercel's AI Gateway for the same model. [verified]
- **Zero-downtime restarts.** A supervisor runs N workers, shards users across them, and can hand a live
  chat (its running agents and in-flight tool jobs) from an old process to a new one. [verified in code;
  inferred that it is used for deploys]
- **Exactly-once actions.** Every client action carries an id. The server records answers in an `acts`
  table for 7 days, so a resent message is never applied twice. [verified]
- **Cloud vs self-host.** The cloud keeps your chat on DigitalOcean servers and bills per call.
  `unii serve` runs the same server locally on your own API keys; "your chat never reaches us". [verified:
  pricing page, help]. The local install has only `check.json` and the token, so A's chat today lives on
  uniichat.com, not on this Mac. [verified]

## 4. Memory model: how optchat's log and tree show up

This is the same design as optchat-memory, but here it **is** the turn loop, not a recall add-on.

### 4.1 The log [verified: Memory.ts]
- `<user>/main/YYYY-MM-DD.jsonl`: every message verbatim, append only, one JSON per line. Kinds: `user`,
  `unii` (optchat's `talk`), `tool`, `echo`, `work` (agent reports), `note` (memories from before the
  chat), `ai` (imported replies from another AI, "not yours").
- A long message is split into pages of 30,000 characters (`more` flag). Images are stored by sha256 name
  under `docs/images/`, with a 1568 px JPEG copy for the model; at most 20 per message.
- `main.index`: 16 bytes per message (byte offset, day, length). Any message is one `pread` away. A torn
  last line is skipped and repaired on load.
- Each subagent has its own log, `docs/agent/<Name>.jsonl`. The main agent opens it with `zoom("Name")`.

### 4.2 The tree [verified]
- Same `id+n` addressing as optchat: line `id+n` covers n messages from id; n is a power of two.
- `tree/YYYY-MM-DD.jsonl` plus `tree.index`, using an in-order slot layout `(2i+1)*2^l - 1`, so every
  node is a direct seek.
- Nodes are at most **512 bytes** (about 70 words). The compaction prompt prints a 512-character ruler so
  the model can see the limit. A short message is its own line, word for word ("free" nodes, no call).
- If a line comes back too long, the retry shows the line cut at the limit with `| ← LIMIT` and asks for
  a rewrite. Up to 5 tries; the shortest wins; else a hard byte cut.
- The compactor prompt ranks what to keep: the user's words near verbatim first, then lasting effects and
  failures, then findings and replies, last tool steps. "Copy names, numbers, ids, paths and errors
  exactly." "Never make anything look further along than it was." This prompt is worth lifting whole.
- **Parallel**: up to 64 compaction jobs at once, in context order. optchat-memory and exo-memory run one
  at a time.
- **Two queues**: live chat and imported history compact separately, so a big import never starves the
  live chat. Imports can be paused and resumed.
- Default compactor: Haiku 5.5 at high effort. Default chat model: Sonnet 5.5 at medium. [verified:
  Config.ts]

### 4.3 The view [verified]
- Two views are kept: the **chat view** (budget 128 KB) that the agent sees, and a smaller **seen view**
  (32 KB) used as context for compaction calls.
- When the view passes its budget, a merge pass folds the oldest, finest pairs up a level until it is at
  half the budget. The view is saved to `view.json` and reloaded, never rebuilt (same as optchat).
- The view is cut into blocks of 4 lines with prompt-cache marks (1 h TTL on Anthropic). [verified]
  Whether this gives a high cache hit rate in practice: [unverified].
- "No message appears in full, not even the last ones." The agent zooms for detail.

### 4.4 The turn [verified: Server.ts]
- Every turn is a **fresh model call**: `[system prompt + user rules] [view blocks] [devices open]
  [agents at work] [Turn: the user's new message]`.
- **The turn waits for the tree to catch up** (`Memory.settle`) before it calls the model, so the view
  always covers everything before the new message. optchat-memory chose the opposite (never wait) because
  Hermes owned the loop.
- The system prompt makes zoom the only memory path: "Never grep or search memories manually; zoom is your
  only allowed mechanism." It also says: "Summaries keep little of tool output, so say in your reply what
  you learned that will matter later."
- Messages the user sends mid-turn are injected between tool calls.

### 4.5 What UniiChat adds over optchat-memory

| Area | optchat-memory / exo-memory | UniiChat |
|---|---|---|
| Turn loop | Hermes owns it; view comes in as per-turn recall | Fresh call per turn built from the view |
| Consistency | Never waits; unbuilt lines show a placeholder | Waits until the tree covers the new message |
| Compaction | One node at a time | 64 in parallel, two queues (live, import) |
| Over-length | Retry | Retry with a visible cut marker, keep shortest |
| Random access | Scan or in-memory | Fixed 16-byte index files, O(1) seek |
| Agents | Reports as `work` lines | Each agent has its own zoomable log |
| Images | Not handled | Hashed store, resized copy, `[image ...]` refs in lines |
| Import | None | Claude Code and Codex session dirs, ChatGPT and Claude website zips, cost estimate first |
| Export | None | `/export` as one JSON (messages, summaries, agents, images); `/tree` HTML browser |
| Search | exo-memory adds keyword search | Forbidden: zoom only |

## 5. UI and UX [verified: Term.ts, Theme.ts, style.css, web bundle]

- **Look**: "black ink on white paper". What the machine wrote is filled (tool calls, summaries); what a
  person said is framed. Tool calls show with a glyph per tool and their result hooked right under them.
  Thinking shows as a dim italic `mind` block.
- **Fold**: `Ctrl+F` or `/fold` shows the whole chat as the fewest tree blocks; `/open #id` and
  `/close #id` expand any block, down to the message. The UI navigates the same tree the model does.
- **Tabs**: each subagent is a tab (`Tab`, `/tab name`). The main chat goes on while you watch one.
- **Model picking without menus**: `Shift+Left/Right` model, `Shift+Up/Down` effort, `Shift+Tab` speed
  (fast). A pick is `vendor:model:effort:speed`.
- **Input**: Enter sends; Enter on empty input interrupts; Esc interrupts the main turn but agents go on;
  shell-style line editing; Up and Down recall sent messages.
- **`/quit` leaves the chat running** without this terminal. The chat never ends; terminals come and go.
- **Web app**: a one-time risk warning, then "How do you want to start?": import past AI chats or start
  fresh. Voice dictation (gpt-transcribe). A browser folder picker for `~/.claude` or `~/.codex`.
- Short, plain, honest copy everywhere. Errors tell you what to do next ("Only the user opens it, so don't
  retry or wait: end your turn, saying what is left").

## 6. Commands, tools and config

### Terminal commands [verified: `unii help` and Term.ts]
- CLI: `unii [url]`, `login`, `update` (shows the curl | sh first), `version`, `serve [port]`, `help`.
- Chat: `/goal <msg>`, `/halt`, `/image <path>`, `/open #id`, `/close #id`, `/fold`, `/import [...]`,
  `/export`, `/tree`, `/quit`, `/help`.
- Pick: `/model`, `/effort`, `/speed`, `/vendor sub|api`.
- Agents: `/tab [name]`, `/stop [name]`.
- Account: `/topup <usd>`, `/account`, `/delete <name>`, `/logout`.
- About 20 commands in total, grouped in four sections.

### `/goal` [verified]
At the end of each turn, a separate judge call reads the view, the latest messages and the goal, and
answers one word: yes or no. On no, the server posts "The goal isn't satisfied yet: keep working." as a
user message and the agent continues. On yes it says "Goal met." `/halt` drops the goal.

### Agent tools [verified]
Only nine: `zoom`, `date`, `shell`, `read`, `write`, `edit` (multi-edit, atomic), `spawn` (parallel
subagents, optional effort), `tell` (message a running or idle agent), `computer` (start a screen task on
a device). Plus native web search and fetch. Subagents get the view and the user's rules but not `spawn`.
The computer agent gets only the mouse, keyboard and screenshot tool, with a hard rule: "pay, buy, send or
delete only what it names; if a page asks for a password, a card or a confirmation it does not cover, stop
and report". Background reports arrive as `[Name] report` messages; the agent is told never to sleep or
poll.

### Config [verified: Config.ts]
`~/.opt/config.json` with `OPT_*` env overrides. Keys: `site`, `bendai` (billing backend), `vendors`,
`models`, `work`, `rules` (one file of user instructions appended to every system prompt), `after` (a
shell hook run after each turn), `accounts` (Claude accounts to rotate), `pick`, `compactor`, `agents`,
`workers`, `sample`, `apple`, `apns`, `admin_pass`, `alert`. API keys come from env and are deleted from
`process.env` once read. Terminal env: `UNII_URL`, `UNII_HOME`, `UNII_WORK`, `UNII_COMPUTER=1`,
`UNII_NO_TELEMETRY=1`.

## 7. Cloud parts [verified]

- Billing per call at list price times speed (fast x2), $0.01 per web search, 10% top-up fee. Calls stop
  at zero balance; unsummarized messages wait. Every call is a row in a `calls` table.
- The live cloud offers only Claude models on the pricing page; OpenAI models exist in code for
  self-host. [verified: /pricing]
- An admin dashboard (`/admin`, Watch.ts) tracks use, lag, CPU, memory and disk, and emails alerts via
  Resend.
- Sign-in by email or GitHub, with a device code shown in the terminal. Consent is versioned by a hash of
  the legal texts.
- Telemetry: one daily version check to bend-lang.com (version, OS, CPU, whether you ever self-hosted).

## 8. What is better than Hermes, opencode and Command Code

1. **Memory is the loop, not a feature.** One chat, forever, with no session picker, no `/compact`, no
   context-full wall. Hermes, opencode and Command Code all have sessions and lossy compaction. UniiChat
   loses nothing: every line can be zoomed to the verbatim message.
2. **Fresh context every turn.** No context rot inside a long session, which DESIGN.md of
   optchat-memory names as the price the plugin had to pay.
3. **Server brain, device hands.** One agent can act on several of your machines at once, and survives
   terminals closing. None of the three does this as a core idea.
4. **Tiny surface.** Nine tools, about 20 commands, four config knobs that matter. Simpler than even
   Command Code.
5. **`/goal` with a judge.** A clean, cheap "keep going until done" loop.
6. **Import of past chats** from Claude Code, Codex and the ChatGPT and Claude sites, with a cost
   estimate before you commit.
7. **Production hardening in few lines**: exactly-once actions, crash-safe JSONL with repair, batched
   fsync, live handoff between processes, patient retries, gateway overflow.
8. **Claude Code as a backend**: use a subscription while keeping your own tools and memory.

Where it is weaker: no permission system at all (bypass everywhere, risk warning instead); no taste or
learning loop; no skills; a single rules file; no undo or snapshots; macOS only for computer use;
memory search is zoom-only, which can be slow for "find every mention of X".

## 9. What Exo should fuse in, ranked

| # | Take | Why | Exo module |
|---|---|---|---|
| 1 | **Fresh call per turn built from the view**, as the default turn loop. D-009 removed the Hermes constraint that forced optchat-memory into recall mode. | No context rot; one endless chat is A's ask for optchat. | new core turn loop (D-010), `exo-memory` episodic |
| 2 | **Turn waits for the tree to settle**, with a timeout that falls back to placeholders. | The view is always complete; no half-memory turns. | core turn loop, `episodic/compact.py` |
| 3 | **Parallel compaction (bounded pool) with separate live and import queues**, pause and resume for imports. | Current exo compactor is sequential; settle-before-turn needs speed. | `episodic/compact.py` |
| 4 | **Lift the compactor prompt and the over-length retry** (ruler, `\| ← LIMIT` cut, keep the shortest of 5). Port the master prompt's "say what you learned that will matter later" rule. | Proven wording; cheap quality win. | `episodic/compact.py`, core system prompt |
| 5 | **Fixed-width index files** for log and tree (16-byte slots, in-order tree layout). | O(1) zoom on years of history; fits the portable store and small devices. | `episodic/store.py`, `episodic/tree.py` |
| 6 | **Per-agent logs, zoomable by name**, and reports as `[Name] report` messages; agents never poll. | Subagent work stays in memory without bloating the main view. | `exo.orchestra` (plugin logic), `exo.ledger` |
| 7 | **Device model: core holds the agent, clients are devices that run tools**, listed to the model each turn. | Same shape as UI.md 4.2 (core server, thin clients) and the companion device. A phone or charm is just another device. | wire protocol (UI.md 4.2), core tools, `exo.bus` for device identity |
| 8 | **Tools over a local MCP endpoint** so any backend, including the Claude Code CLI, uses Exo's tools and memory. | Lets Exo run on a subscription and still own memory, gate and ledger. | core model router (D-010), `exo.gate` stays in front of every tool |
| 9 | **`/goal` with a yes/no judge** at turn end. | Small, useful, and the judge is reusable as an eval grader. | core loop, `exo.eval` (judge grader), `exo-command` |
| 10 | **`/import` of Claude Code, Codex, ChatGPT and Claude exports** into the episodic log as kind `ai`, with a cost estimate first. | Day-one memory for any new user; replicable beyond A. | `exo.ingest` / `exo.intake` |
| 11 | **Tree-native UI**: fold the chat into tree blocks, open and close by id, `/tree` HTML browser, one-file `/export`. | Makes memory visible and inspectable; fits "teaching is first-class". | Textual TUI (UI.md 4.3), `exo.brief` |
| 12 | **Exactly-once client actions** (action ids, answer cache) and **batched fsync** with torn-line repair. | Needed for flaky links to the companion device. | wire protocol, `exo.ledger` |
| 13 | **`after` hook** (shell command after each turn) and **patient retries** for background calls. | Simple extension point; robust background work. | core config, learning loop runner |
| 14 | **Pick as `vendor:model:effort:speed`** with Shift-arrow cycling. | Faster than a model menu; maps to UI.md keybinds. | TUI keybinds, core model router |
| 15 | **Computer-use agent with a narrow mandate** (only the screen tool, stop at passwords, cards, confirmations). | Good safety template for any hands agent. | `exo.gate` policy, `exo.orchestra` |

Do not take: the billing stack, bypass-all permissions, the zoom-only ban on search (Exo's belief retrieval
and keyword recall are an advantage; keep both, but zoom first), and the single rules file (Exo has the
principal pack and skills).

## 10. Gaps and open questions

- I did not run a session, so latency of settle-before-turn and real cache hit rates are [unverified].
- The view "batch" merge logic may refit often once the view sits near half its budget; whether that
  breaks the prompt cache much is [unverified].
- iOS app behaviour, Apple purchase flow and push are seen only in code. [unverified]
- No public docs site exists (`/docs` is 404). There is no source repo for UniiChat that I found;
  everything here comes from the shipped bundle. [verified for /docs]
- Licence of the bundled code is not stated in the binary. Lift ideas and prompts with care and credit;
  optchat-memory (MIT, from Taelin's public spec) remains Exo's code base. [unverified licence]
