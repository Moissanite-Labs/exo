# UI research: opencode, Command Code, Hermes, and a spec for Exo

Date: 2026-10-11. Feeds D-010 (see D-009 in `DECISIONS.md`).
Goal set by A: "the UI simplicity of opencode and commandcode but the robustness and customizability of hermes agent".

Sources. opencode: a shallow clone of github.com/sst/opencode (now anomalyco/opencode, commit 707de58,
2026-10-11) and opencode.ai/v2/docs. "D/" below means `services/www/src/docs/content/` in that repo,
which is the source of opencode.ai/v2/docs. Command Code: commandcode.ai/docs (the source is closed; the GitHub
repo CommandCodeAI/command-code holds only a readme; npm `command-code` v1.79.2 is UNLICENSED).
Hermes: local checkout `~/.hermes/hermes-agent` at `v0.21.6+canary.20261010`. The Exo pin at `~/.exo/hermes`
has no `PINNED` file yet, so the live install was read instead.

---

## 1. opencode (V2, v2.0.26, 2026-10-08)

Note: V2 changed a lot. Older write-ups describe V1 (Go + Bubble Tea, Tab to switch agents, `opencode attach`).

### First run
- Install with one curl line, brew, or npm (`@opencode/cli`). Desktop builds exist too. (D/index.mdx)
- Free models ship by default. The sidebar shows a "Getting started" card with a "Connect provider /connect"
  button. You can chat before entering any key. (`packages/tui/src/feature-plugins/sidebar/footer.tsx`)
- `/connect` handles API keys, OAuth (`o` opens the URL, `c` copies the code) and several accounts per provider.
  (D/cli/providers.mdx)
- `/init` is a guided AGENTS.md setup. AGENTS.md loads from global, then every level up to the project root.
  Nested ones load lazily when the agent reads that folder. No CLAUDE.md fallback. (D/instructions.mdx)

### Main screen
- Full-screen transcript above a composer. The composer footer shows agent, model and variant. Assistant
  footers show cost and tokens per second. (`packages/tui/src/component/prompt/metadata.tsx`)
- Optional sidebar (`<leader>b`, or `auto` when space allows) with Context (tokens, % used, $) and MCP status.
  Each sidebar section is an internal plugin in a UI slot. (`feature-plugins/sidebar/context.tsx`, `mcp.tsx`)
- Extras on demand: session tabs, a terminal pane (`<leader>t`), a diff viewer, a timeline (`<leader>g`).
  (D/cli/config.mdx, D/cli/keybinds.mdx)
- Themes: `opencode` default plus a `system` theme taken from the terminal palette. Custom themes are JSON
  files. (D/cli/theme.mdx)

### Commands and keys
- About 35 TUI slash commands (agents, btw, clear, connect, diff, editor, fork, models, new, sessions,
  settings, skills, status, themes, undo, redo, variants, worktrees, and so on), plus server commands
  `/init`, `/review`, custom commands and MCP prompts.
- `/btw <q>` asks a side question that does not enter the conversation. (D/cli/tui.mdx)
- One leader key, `ctrl+x`, then a mnemonic letter: `m` models, `a` agents, `n` new, `l` list, `u`/`r`
  undo/redo, `c` compact, `e` editor.
- `ctrl+p` is a command palette with "every action available in the current view".
- Composer: `@` fuzzy-attaches files (with line ranges, `@src/auth.ts#20-45`), agents and skills. `!` at the
  start of an empty prompt is shell mode. Enter while the agent runs steers it; `ctrl+x Enter` queues.
- Custom commands: one markdown file per command in `.opencode/commands/`. Supports `$ARGUMENTS`, `$1`, and
  `` !`shell` `` interpolation. Frontmatter can pin agent, model, or run as a background subagent.
  (D/commands.mdx)
- Keybinds rebind in `cli.json`; unknown IDs are rejected. (D/cli/keybinds.mdx)

### Model switching
- `/models` or `<leader>m`. `f2` cycles recent models. Favourites with `ctrl+f`.
- "Variants" are named reasoning or budget presets per model, cycled with `ctrl+t`. (D/models.mdx)
- Agents: `build` (default) and `plan` (read-only) are primary; `shift+tab` cycles them. `general` and
  `explore` are subagents in child sessions. (D/agents.mdx)

### Sessions
- `/sessions` list with pins and quick slots `<leader>1-9`. `/fork`.
- Child sessions are navigated with arrows: down into a subagent, left/right across siblings, up to the parent.
- Undo is snapshot-backed: files and conversation roll back together, and `/undo` puts your prompt back in
  the composer to edit. (D/snapshots.mdx)
- Auto compaction keeps about the last 15k tokens raw. (D/compaction.mdx)

### Permissions
- An ordered list of `{action, resource, effect: allow|ask|deny}`. Last match wins. Default is `ask`.
  (D/permissions.mdx)
- Base policy is permissive: allow, but ask for outside folders and `.env` reads.
- The prompt is a "Permission required" panel with an inline diff for edits. Choices: Allow once, Always allow,
  Reject. "Always allow" first shows the exact pattern it will save, for example `shell: git status * -> allow`.
  Reject can carry feedback. (`packages/tui/src/routes/session/permission.tsx`)

### Config without clutter
- Two files with a clean split. `opencode.json` holds project and server behaviour and layers from global down
  to the cwd. `cli.json` holds terminal taste (theme, keybinds, tabs) and is global only, so it never lands in a
  repo. Both hot-reload. (D/config.mdx, D/cli/config.mdx)
- Agents, commands, plugins, themes and skills are each one file in `.opencode/`. Adding a feature means
  dropping a file, not editing a schema.
- `opencode mcp add` writes MCP config for you.

### Architecture
- TUI is TypeScript on OpenTUI + Solid (`packages/tui/package.json`). Desktop is Electron + Solid.
- One per-user background server owns sessions, config, permissions and tools. Every client connects to it.
  `opencode service start|stop|status`, `--standalone`, `--server <url>`. (D/cli/index.mdx)
- OpenAPI 3.1 with about 142 operations (`packages/protocol/openapi.json`). Events over SSE at `/api/event`.
  Generated client SDK. (`specs/v2/event-stream-architecture.md`)
- `opencode pair` prints one-time links and a QR code for remote clients. `opencode run` for headless,
  `opencode acp` for Zed and other ACP editors. (D/cli/web.mdx, D/cli/acp.mdx)

### Why it feels simple
Three prefixes (`/`, `@`, `!`) and one palette cover everything. One leader key with mnemonic letters.
Terminal taste is separate from project config. Files beat settings screens. Free models mean zero setup.
One server, many thin clients.

---

## 2. Command Code (CLI v1.79.2, 2026-10-09)

### First run
- `npm i -g command-code`, then `cmd login` (browser authorize, or paste a key). (commandcode.ai/docs/quickstart)
- First `cmd` in a folder asks once to trust it. If you have no sessions it offers a taste demo.
- Optional: `/init` writes AGENTS.md. `/import` copies setup from other agents (CLAUDE.md, skills, agents,
  commands, MCP). `/learn-taste` learns from your past sessions in other agents. (docs/import, docs/memory)

### Main screen
- Top to bottom: an action feed (reads, edits, shell), the reply, a todo bar (`TODOS 2/4`), a `>` input box,
  and a status line with current file, model and permission mode. (docs/interactive-mode)
- Hints fade (the paste hint disappears after 20 s). The desktop app folds finished turns into one line that
  leads with the answer. (docs/changelog, 2026-10-02)
- `/theme dark|light|auto`.

### Commands and keys
- The docs say it plainly: "Three characters change what the input does. That's most of the surface."
  `/` commands, `!` shell (output goes into context), `@` paths (and pulls in AGENTS.md files on the way to
  root). (docs/interactive-mode)
- Keys: `shift+tab` cycles permission mode, `esc` stops, `esc esc` rewinds, `ctrl+o` toggles full tool output,
  `ctrl+g` opens `$EDITOR`, `ctrl+s` stashes the prompt, `alt+p` quick model switch, `ctrl+e` on a permission
  prompt explains the shell command.
- About 60 slash commands, grouped: sessions, context, modes, models, extensibility, workspace, billing,
  utilities. (docs/reference/slash-commands)
- Skills are folders with `SKILL.md`, looked up in `.commandcode/skills/`, `.agents/skills/`, then the user
  folders. Invoke with `/name` anywhere in the prompt, several per prompt. (docs/skills)

### Model switching
- `/model` picker, `/model <id>` direct (case-insensitive, suffix after `/` is enough), `alt+p`, or `-m`.
  `/config` can set separate models for planning and implementing. (docs/reference/cli/models, changelog v1.73.0)

### Sessions
- Append-only JSONL per session under `~/.commandcode/projects/<slug>/`. (docs/sessions)
- `cmd -c` continue, `cmd -r` resume, `/resume` picker. `/fork` copies the session, `/clone` copies the active
  branch, `/tree` shows every branch. `esc esc` rewinds conversation, code, or both; checkpoints kept 30 days.
- Headless `cmd -p` with NDJSON output and typed exit codes (3 unauthenticated, 4 permission denied, 8 max
  turns). Edits and shell are denied unless `--yolo`. (docs/headless)

### Permissions
- Modes: default (ask before writes and shell; reads free), accept-edits, plan (read-only), yolo (deny rules and
  a root/home delete breaker still apply), dont-ask (fail-closed, CI). `shift+tab` cycles the first four.
  (docs/permissions)
- Rules `deny/ask/allow` with patterns like `Shell(git:*)`. Deny beats ask beats allow, in every mode.
- Prompt: allow once, allow and remember, deny with feedback. Destructive actions only get a one-time yes/no.
- Plan approval is a GitHub-style review panel with per-line comments. `ctrl+r` sends comments back,
  `ctrl+a` approves and can flip to accept-edits. (docs/plan-mode)

### Config and taste
- Same three tiers everywhere: local (gitignored), project (committed), user (global). Applies to settings,
  MCP, skills, memory and taste. Learn it once. (docs/settings, docs/mcp)
- `/config [query]` is a searchable panel with a curated subset of settings. Popular keys get their own
  command (`/model`, `/theme`, `/effort`). The rest stays in files.
- Taste: "Every accept, reject, and edit becomes a signal", plus correction diffs from your commits. A model
  turns these into signals, a classifier (`taste-1`) scores confidence 0 to 1 and drops noise, and a model
  writes markdown into `.commandcode/taste/taste.md` and category subfolders. Confidence rises on repeats and
  falls on contradiction; conflicts are flagged, not overwritten. (docs/taste)
- Taste is visible: a green inline `TASTE` row in the transcript shows what was learned, its confidence, and a
  link to the file. (docs/workflows) It can be edited (`npx taste open`), linted, and pushed or pulled as
  packages. (docs/taste)
- The docs never use the phrase "branch-off learning". D-009's phrase is our framing, not theirs.

### Why it feels simple
Tiny input surface. The headline feature needs no setup and shows its work inline as plain markdown. One tier
model for every kind of config. Calm feed: collapsed turns, fading hints, full output only on `ctrl+o`.

---

## 3. Hermes: where it is heavy, and what it has that the others lack

### Heavy or confusing
- **Four front ends plus messaging.** Classic prompt_toolkit CLI (`cli.py`, `hermes_cli/cli_*_mixin.py`),
  Ink TUI (`ui-tui/`, `hermes --tui`), Electron desktop (`apps/desktop/`), web dashboard (`web/`), and the
  messaging gateway. `tui_gateway/AGENTS.md` says the TUI "fully replaces" the classic CLI, but both still ship.
  A user cannot tell which one is "Hermes".
- **Command sprawl.** 103 slash commands in `hermes_cli/commands.py`, including `pet`, `hatch`, `battery`,
  `indicator`, `topup`, `subscription`, `codex-runtime`, `moa`, `egress`. The CLI has about 158 subcommand
  names across its argparse trees (`hermes_cli/*.py`), with many repair verbs (`repair-prompts`,
  `repair-routing`, `repair-profiles`, `migrate-legacy`). Opencode has about 35 slash commands; Command Code
  about 60.
- **Config size.** `cli-config.yaml.example` is 2,333 lines in 28 top-level sections; `config_defaults.py` is
  3,190 lines. `display:` alone spans about 290 lines with per-platform overrides. Approval mode is one of three
  values (`manual|smart|off`, `hermes_cli/approval_mode.py`), but it sits inside this sea.
- **Billing and account noise in the core UI.** `/billing`, `/credits`, `/topup`, `/subscription`, free-tier
  offers, Nous Portal setup (`ui-tui/src/app/slash/commands/billing.ts`, `hermes_cli/free_tier_offer.py`).
- **Setup weight.** `hermes_cli/setup*.py` plus banner is about 4,200 lines: quick setup, Blank Slate, portal
  one-shot, per-platform wizards. The default banner is full ASCII art with a tool and skill summary
  (`display.compact: false`).
- **~/.hermes holds about 100 entries** (locks, caches, pids, sockets, quarantine folders). Nothing is hidden
  from a curious user.
- **Keybind lists are long and split across files.** The TUI README has a 30-row table for the main input
  alone, and notes that logic is "split across `app.tsx`, `components/textInput.tsx`, and the prompt/picker
  components". No leader key; many separate ctrl and meta chords.

### Robustness and customization the others lack
- **A typed wire already exists.** `tui_gateway` is newline JSON-RPC over stdio, and the same server serves
  desktop and the dashboard over WebSocket. Every method, server-to-client request and event is a Pydantic model
  with `extra="forbid"`, generated into TypeScript (`tui_gateway/contracts/`). About 139 registered methods.
  This is the same shape as opencode's server, written in Python.
- **Server-to-client requests survive reconnects.** Approval, clarify, sudo and secret prompts are requests
  from the agent to the UI. `open_requests(sid)` replays still-open questions when a client reconnects
  (`tui_gateway/server_requests.py`, `session.events.since`). Neither opencode nor Command Code documents this.
- **Crash recovery.** The TUI respawns and resumes the Python gateway with a 3-tries-in-60-s budget
  (`ui-tui/src/app/gatewayRecovery.ts`).
- **Busy-input modes.** While the agent works, input can queue, steer (applied at the next tool boundary), or
  interrupt (`useSubmission.ts`). Empty Enter twice interrupts when a queue exists.
- **Live subagent dock and steering.** A dock above the composer shows running children. `ctrl+t` expands the
  roster; per child you can tail, steer (`e`), stop (`x`) or stop the subtree (`X`) (`ui-tui/README.md`).
- **Approval scopes.** once, session, always, deny, with quick keys `o s a d`. "Session" scope is a middle step
  the others lack.
- **Clarify prompts.** The agent can ask multiple-choice questions with number quick-picks and an "Other"
  free-text option. This fits the CIRL note in `research/NOTES.md`: teaching moments should be first-class.
- **Profiles, skins, personalities, gateways, cron, kanban, memory providers, plugins with isolation and
  provenance, MCP catalog, checkpoints and rollback, per-platform display tuning, managed (policy-locked) config
  scopes** (`hermes_cli/managed_scope.py`, `plugin_isolation.py`, `profiles.py`). Also `{!cmd}` inline shell
  interpolation and `$EDITOR` drafting.

Verdict: Hermes has the right bones (Python core, typed RPC, thin TS renderer, replayable prompts). The problem
is surface area, not architecture. Exo should lift the bones and refuse the surface.

---

## 4. UI spec proposal for Exo

### 4.1 Principles
1. **Core and UI are separate processes.** The core is a Python server. Every UI is a client. Nothing about
   agent behaviour lives in a renderer (Hermes's own rule, kept).
2. **Three prefixes and one palette.** `/` commands, `@` context, `!` shell, `ctrl+p` palette. That is the
   whole thing a new user must learn.
3. **Power is reachable, not visible.** Every Hermes-level feature exists as a file, a palette entry, or a
   `/config` search hit. None is on screen by default.
4. **Teaching is first-class.** Corrections, rulings and learned taste show inline, are editable as plain
   files, and feed the learning loop from D-009.
5. **Small enough for the companion device.** The protocol must work over a slow link with a tiny screen or
   no screen (voice only).

### 4.2 The boundary: an Exo wire protocol
- **Shape:** JSON-RPC 2.0, three message kinds, lifted from `tui_gateway`: client method calls, server events,
  and server-to-client requests (approve, clarify, secret).
- **Schema:** Pydantic models are the source of truth. Export JSON Schema and an OpenAPI-style document from
  them; generate TS or other clients from that. Strict by default (`extra="forbid"`).
- **Transports:** stdio for the local TUI (no port, no auth, simplest). Unix socket for a per-user daemon.
  WebSocket or HTTP+SSE for web, phone and device, with pairing by one-time link or QR (opencode's `pair`).
- **Replay:** every session is an append-only event log (fits the ledger in `src/exo/ledger.py` and gwern's
  "append-only log as core data structure" in NOTES.md). A client that reconnects calls
  `session.events.since(cursor)` and gets missed events plus still-open requests.
- **Size:** start with about 25 methods, not 139. Sessions (new, list, resume, fork, undo), prompt (send,
  steer, queue, interrupt), models (list, set), config (get, set, search), approvals (respond), taste (list,
  edit), commands (list, run). Grow only when a client needs it.
- **Capabilities:** clients declare what they render (`diff`, `markdown`, `images`, `voice`, `screen_cols`).
  The core degrades output to match. This is how one core serves a 200-column terminal and a coin-sized screen.
- **Why not opencode's TS server:** the core is Python and the store, gate, bus and learning loop are Python.
  Keep the server in Python and match opencode's contract style instead.

### 4.3 Screens (TUI)
Only one screen by default. Everything else is an overlay that `esc` closes.

1. **Chat (default).** Transcript, then a one-line activity strip (running tool, live subagent count), then
   the composer, then a one-line status: `agent · model · mode · ctx 34% · $0.12`. No banner beyond one line.
   Finished turns collapse to their answer; `ctrl+o` expands tool output.
2. **Palette** (`ctrl+p`). Fuzzy list of every command, setting, session, model, skill and agent.
3. **Picker overlays.** Models, sessions (with branch tree), agents. All the same fuzzy list widget.
4. **Approval panel.** Inline, not modal. Shows the diff or command, plus `o` once, `s` session, `a` always
   (shows the saved pattern first), `d` deny with optional note, `?` explain.
5. **Plan review.** Command Code's panel: per-line comments, `ctrl+r` revise, `ctrl+a` approve.
6. **Agents dock** (`ctrl+t`). Hermes's roster: tail, steer, stop. Hidden unless children are running.
7. **Config** (`/config`). Searchable panel over a curated subset; "open file" for the rest.

### 4.4 Commands (day one, about 15)
`/new` `/sessions` `/fork` `/undo` `/redo` `/model` `/agent` `/mode` `/compact` `/config` `/taste` `/init`
`/btw` `/help` `/exit`.
Everything else (skills, custom commands, MCP prompts, Hermes-derived tools like cron or kanban) appears as
`/name` only when the user installs or enables it. The palette lists all of them.

### 4.5 Keybinds (defaults)
| Key | Action |
|---|---|
| `enter` | send; while busy, steer |
| `shift+enter` | newline |
| `ctrl+p` | palette |
| `shift+tab` | cycle mode: ask, accept-edits, plan, auto |
| `esc` | interrupt; `esc esc` rewind |
| `ctrl+o` | expand or collapse tool output |
| `ctrl+g` | edit draft in `$EDITOR` |
| `ctrl+t` | agents dock |
| `ctrl+x` then letter | leader: `m` model, `n` new, `l` sessions, `u` undo, `r` redo, `c` compact, `q` queue |
Rebind in one global file. Unknown key IDs are rejected.

### 4.6 Progressive disclosure (four layers)
1. **Zero config.** Install, run `exo`, a local or free model works, chat. Trust prompt once per folder.
2. **Palette and `/config`.** Find any feature by name. Curated settings only.
3. **Files.** One file per thing, Command Code's tiers (local, project, user):
   `.exo/commands/*.md`, `.exo/agents/*.md`, `.exo/skills/<name>/SKILL.md`, `.exo/taste/*.md`,
   `.exo/settings.json`. Terminal taste (theme, keys) in a separate global `~/.config/exo/ui.json`, never in
   a repo (opencode's split).
4. **Plugins and protocol.** Python plugins in the core; any new client speaks the wire. Hermes-level power
   (profiles, gateways, cron, memory providers) lives here as opt-in packs, not core UI.

Permissions use one ordered rule list (`allow|ask|deny`, last match wins as in opencode) plus the mode cycle,
plus a hard deny list that no mode overrides (Command Code's breaker).

### 4.7 Tech stack
| Option | Fit |
|---|---|
| **Textual (Python)** | Same language as the core. Rich widgets, CSS-like theming, good diff and markdown rendering, a web mode (`textual serve`). Startup is slower than TS TUIs, and it is less snappy on huge transcripts. |
| prompt_toolkit (Python) | Hermes's classic CLI. Fine for a line REPL, painful for overlays and panes. Hermes itself moved off it. |
| OpenTUI + Solid (TS) | opencode's stack. Fast and polished, but young (Zig core) and adds a Node or Bun runtime next to Python. |
| Ink + React (TS) | Hermes's current TUI. Proven, but Hermes had to fork Ink (`packages/hermes-ink`) to get what it needed. |

**Recommendation:** Python core server first, with the wire protocol as the product boundary. First client in
**Textual**, run as a separate process over stdio. One language, one install, and the stdio boundary keeps it
honest. Because the boundary is a protocol, a TS client (OpenTUI or Ink) can replace or join it later without
touching the core. Also ship `exo run` (headless, NDJSON, typed exit codes) and an ACP adapter for editors,
both thin clients of the same server.

**Companion device:** the device runs a tiny client (or voice only) that speaks the WebSocket transport to a
core on a phone, laptop or home box. Later, a small-model core can run on the device itself with the same
protocol. The capability handshake, replayable events and server-to-client requests (approve by button,
clarify by voice) are what make this work. Design those now.

---

## 5. Ten UI ideas worth stealing (ranked)

1. **Client/server split with a typed, replayable wire** (opencode's server and SDK; Hermes's Pydantic
   contracts and `open_requests` replay). The base for the device horizon.
2. **Three prefixes plus one palette** (`/`, `@`, `!`, `ctrl+p`). The whole learning curve. (Command Code
   interactive mode; opencode tui docs)
3. **Inline, editable taste rows** (Command Code `TASTE` row with confidence and file link). The visible face
   of the D-009 learning loop.
4. **`shift+tab` mode cycle with a hard deny breaker** (Command Code permissions). One key for trust level.
5. **Approval panel that shows the rule it will save** before "always allow", plus Hermes's "session" scope
   and deny-with-feedback. (opencode `permission.tsx`; Hermes approval keys)
6. **Snapshot undo that hands the prompt back** for editing; files and chat roll back together. (opencode
   D/snapshots.mdx; Command Code `esc esc`)
7. **Busy input: Enter steers, leader+Enter queues, esc interrupts** (opencode; Hermes busy modes).
8. **Calm feed:** collapse finished turns to the answer, `ctrl+o` for full output, fading hints, one-line
   banner. (Command Code)
9. **One file per thing, three tiers everywhere, terminal taste kept out of repos.** (Command Code tiers;
   opencode `cli.json` split)
10. **Live subagent dock with steer and stop**, hidden when idle, plus arrow-key navigation through child
    sessions. (Hermes `ui-tui` README; opencode child sessions)

Honourable mentions: `/btw` side questions, plan review with line comments, `/import` from other agents,
typed headless exit codes, QR pairing.
