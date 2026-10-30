# Command reference

Exo has two kinds of commands. A **slash command** is something you type in a client, like `/model`. A **CLI
command** is something you type in a shell, like `exo find`. This page lists every one, what it takes, and which
file does the work. Task-first help is in `docs/USER-GUIDE.md`. How to add a command is in
`docs/DEVELOPER-GUIDE.md` section 2.

## Part 1. Slash commands

### Who answers a slash command

A client reads the line and decides. Three things can answer:

1. **The client itself.** The terminal client (TUI) handles these in `ExoApp.slash` in
   `clients/tui/exo_tui/app.py`. They are listed in its `COMMANDS` table, and `/help` is built from that table.
2. **The core, as a built-in.** The client sends the protocol command `command.run` with `name`, `args` and
   `session`. `builtin_command` in `src/exo/diagnostics.py` answers a few names. It returns text, a dict
   or nothing, wrapped as `{"output": ...}`.
3. **A plugin.** If the core has no built-in with that name, `command.run` looks in the plugin registry
   (`ctx.register_command` in `src/exo/core/plugins.py`) and calls the handler with the raw argument string. An
   unknown name fails with the error code `not_found`.

`commands.list` returns the commands a client may offer: the four core ones below that are listed there (`context`,
`compact`, `trace`, `findings`) plus every plugin command, each with a name, a description and an argument hint.
The TUI shows the plugin ones under `other` in `/help` and in its palette (ctrl+p).

Any client can use categories 2 and 3 with no client code. Only category 1 is specific to the TUI. The line client
`exo chat` has no client-side commands except `/quit` and `/exit`. It sends every other `/name args` line straight to
`command.run`, so `/new` or `/model` there fail with `not_found`, while `/context` and `/grants` work.
The web client (`exo web`) runs `/context`, `/findings`, `/trace`, `/new`, `/rename` and `/layout` from its composer
(`WEB_COMMANDS` in `plugins/exo-command/exo_command/api.py`). The first three go to `command.run`. `/new` starts a thread
and `/layout` is saved in the page. `/rename <title>` calls `POST /api/chat/sessions/<id>/rename`, which runs the
`rename` built-in; double-clicking a thread title in the list renames it the same way.
It also supports `/model [ref]`, `/model default [ref]` and `/default [ref]`: without a reference they open a picker
(Esc closes it). As in the TUI, model selection changes the shared main route; `/model` also records the choice on the
open thread. `/default` saves through the provider routes, so it works only on a page opened at 127.0.0.1 or localhost
(D-034). A failed change keeps the line in the box. The web composer holds a `/name` it does not run but the core's
`commands.list` has (`/grants`), or that is the terminal client's own (`/help`, `/quit`, `/usage`), or a near typo of
one, with a note instead of starting a model turn. A near typo of a command the page runs (`/contex`) puts the
corrected line back in the box. Until the page has the core's command names it holds every `/name` line that is not
its own or the terminal client's, and keeps asking for them. Any other line is ordinary message text: paths such as
`/etc/hosts` or `/tmp is full`, and words such as `/me waves` that are close to no command (D-037).
It also runs `/permissions` through its own route (`GET`/`POST /api/permissions`); see D-065.

### Client commands (TUI)

These exist only in the terminal client. Each one is a row in `COMMANDS` and a branch in `ExoApp.slash`.

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `/new` | Start a new thread: a fresh focus, the same memory. | `private` makes a thread that is not remembered. | `ExoApp.new_session` |
| `/sessions` | Pick one of this agent's threads to switch to. | optional thread id or title | `ExoApp.pick_session` (calls `session.list`) |
| `/resume` | Same as `/sessions`. `/resume <thread>` opens an id or the best title match without the picker. | optional thread | same |
| `/fork` | Fork this session and open the fork. | optional title | `session.fork` |
| `/undo` | Undo the last turn and reload the session. | none | `session.undo` |
| `/clear` | Clear the transcript view. History is kept. | none | `ExoApp.clear_transcript` |
| `/cwd` | Show this session's working folder. | none | `session.get` |
| `/restart` | Reopen this session in a new terminal tab and quit this one. | none | `ExoApp.restart`, `clients/tui/exo_tui/restart.py` |
| `/stop` | Stop the running turn (same as ctrl+c). With a task, stop that running subagent instead. | optional `<task>` | `ExoApp.stop_turn`; with a task, `ExoApp.subagent` (`command.run` built-in `subagent`) |
| `/rename` | Give this thread a new title; it shows in `/sessions`, `/resume` and the web list. Principal only (D-069). | title | `command.run` built-in `rename` (`session.updated`) |
| `/unsteer` | Pull your newest queued steer message back into the composer, if it has not landed yet (Up in an empty composer does the same). Never stops the turn. | none | `ExoApp.unsteer` (`command.run` built-in `unsteer`) |
| `/exit` | Quit. | none | `ExoApp.slash` |
| `/quit` | Same as `/exit`. | none | same |
| `/model` | Pick the model for this session, or set it. | `<ref>` sets it. `default <ref>` sets the default. | `ExoApp.set_model` (`models.list`, `models.set`) |
| `/default` | Set the default model for new sessions. Saved to `models.roles.main` in `config.json`, old routes kept as fallbacks. | `<ref>`, or none to pick | `ExoApp.set_default` (`models.set`, `config.set`) |
| `/providers` | Provider setup panel: status, masked key entry, test, remove, default. Client side; the key never reaches the composer, transcript or protocol. Sends `command.run providers reload <name>` after a change. | `add <name>` opens the key field; `<name>` selects that provider | `ProvidersScreen` in `clients/tui/exo_tui/providers.py`, engine `src/exo/providers_setup.py` |
| `/usage` | Show context size, cost and the model in use. | none | `ExoApp.slash` (client state, no call) |
| `/panel` | Reopen the last panel. | none | `ExoApp.show_panel` |
| `/tasks` | List the subagents this session handed work to, with status (D-070). | none | `ExoApp.subagent` (`command.run` built-in `subagent`, verb `list`) |
| `/open` | Watch a subagent's session, read only. Finished subagents stay locked. | `<task>`: a task id, a prefix of it, or the child's session id | `ExoApp.subagent` (verb `open`) |
| `/steer` | Send a note to a running subagent. The parent's receipt records "steered by principal". | `<task> <text>` | `ExoApp.subagent` (verb `send`) |
| `/peers` | List other live sessions you can message. | none | `peers` protocol command, `action="list"` |
| `/send` | Message another session as yourself. | `<peer> <text>` | `peers` protocol command, `action="send"`, `via="human"` (honoured only from a principal connection; others send as `agent`) |
| `/handoff` | Ask the agent to write a handoff note for the next session. | optional path | `ExoApp.handoff`. The core gathers facts with the `handoff_facts` built-in. See `docs/HANDOFF.md`. |
| `/intro` | Ask the agent for an orientation. | none | `ExoApp.intro` (an ordinary turn) |
| `/tutorial` | Turn tips on or off, or show them again. | `on`, `off`, `reset`. None shows the state. | `ExoApp.tutorial_command` (`config.set` on `tutorial.enabled`) |
| `/copy` | Copy text to the clipboard. | `last` or none: the last answer. `error`: the last error. `all`: the whole transcript. | `ExoApp.copy_command` (client only) |
| `/compose` | Open the big editor for a long prompt (also `ctrl+g`). Enter adds a line, `ctrl+s` or `ctrl+enter` sends, Esc closes and keeps the draft. | `task` adds Goal, Context, Constraints, Done when headings; `edit` opens `$VISUAL` or `$EDITOR`; `close`. None opens it. | `ExoApp.compose_command` (client only) |
| `/layout` | `inverse` puts the input on top and the newest message under it; `classic` is the usual order. Saved. The web page runs it too. | `inverse`, `classic`. None shows the layout. | `ExoApp.layout_command`, `exo_tui/prefs.py` (web: `static/layout.js`) |
| `/help` | List every command. | none | `ExoApp.help_text` |

Not slash commands, but in the same composer: `@path` adds a file to the turn, `!cmd` asks the agent to run a shell
command and show the output (both complete as you type, see "Completion and guidance").

Keys: ctrl+p palette, ctrl+o tool output, ctrl+y accept, ctrl+e edit, ctrl+r reject, ctrl+f put the focus on the approval card, ctrl+c stop a running turn (idle: a quit hint; with text selected: copy), F6 load 60 earlier messages when the transcript shows "N earlier messages" (scrolling to that end does the same), ctrl+g big composer (inside it: ctrl+s send, ctrl+l line numbers, ctrl+t $EDITOR, Esc close).

Anything else the TUI does not know goes to `command.run`. That is how `/context`, `/compact`, `/trace`, `/findings`
and plugin commands work there. `/context` and `/compact` are also rows in the TUI `COMMANDS` table (so `/help` lists
them), but the core answers them. The TUI asks `/context` for its `json` form to update the status line.

### Completion and guidance

The composer helps you type. It is built in `src/exo/complete.py` (no UI, no model calls) and drawn by the terminal
client and by the web client. The idea of a `/` menu and ghost text from history comes from the Hermes CLI
(`hermes_cli/commands_completion.py`); this is our own code.

| You type | You get |
|---|---|
| `/` | A dropdown under the composer with every command: name, argument shape, one line of description. Client commands, core commands from `commands.list` (asked once at start) and plugin commands. |
| `/cmp` | Fuzzy filter. Exact name, then prefix, then a word start, then contains, then the letters in order, then a word of the description. |
| `/model ` | The next argument. `/model` and `/default` list the models the picker lists. `/resume` lists recent threads by title. `/tutorial` lists `on`, `off`, `reset`. `/grants revoke ` lists grant ids. `/providers ` lists `add` and the provider names. `/handoff ` shows the default path. `/trace`, `/copy`, `/new`, `/findings` list their words. `/send ` lists live peers. |
| `/handoff ` | Ghost text of the expected argument (`[path]`). It is a hint, not text: right arrow does not take it. |
| `@sr` | Files under the working folder, from `git ls-files` (tracked and untracked, minus `.gitignore`) or a bounded walk without git. A bare `@` or a path ending in `/` browses a folder, directories first. Names only: files are not read, secret stores (`gate.is_sensitive_path`) are left out, and nothing outside the folder is listed. |
| `!ls` | A one-line note: the agent runs it in its shell tool and the gate may ask first. |
| `/comand` | Not sent. "unknown command /comand. did you mean /compact?" and the corrected line waits in the composer. With nothing close, the line goes to the core as before. |

Keys. Up and down move in the menu, Tab or Enter completes (Enter on an already complete line sends it), Esc closes
the menu and keeps the text (then ghost text, then a tip, then a panel; Esc never stops a turn, `/stop` or ctrl+c does). With no menu: up and down walk this seat's history, and right arrow or Tab takes the
ghost text from history (the newest earlier line that starts with what you typed, like a shell autosuggestion).
History is per seat in `$EXO_HOME/history/<seat>.jsonl`, mode 0600, the newest 500 lines; a line that looks like a
key or password is never kept.

Guidance. With the composer empty, one dim line may suggest a next step, chosen by local rules: first run (`/intro`),
a failed model call (`/providers` to check the provider), a finished job (ask for its `job_output`), open findings
(`/findings`), a slow turn (`/trace`), a long thread (`/context`). At most one new line per 25 seconds, each rule twice
a session, none while a turn runs, and a rule stops once you run its command. Tab fills the line in. Turn it off with
`/tutorial off` (this also sets `guidance.enabled` to `false`; `/tutorial on` sets it back) or set that config key. It
is the composer placeholder, so it never moves anything, and without a timer under `EXO_REDUCED_MOTION`.

The web client has the same menu for the commands it runs (`/context`, `/model`, `/default`, `/findings`, `/trace`,
`/new`, `/rename`, `/layout`), `@` files
through `GET /api/complete/files`, ghost text, history for the page's life and the guidance line. See
`docs/ui/WEB.md` "The composer".

### Core built-ins (`command.run`)

All in `builtin_command`, `src/exo/diagnostics.py` (called from `CoreEngine._command_run`).

| Name | What it does | Arguments | Returns |
|---|---|---|---|
| `context` | Shows what the next model call for this thread carries, part by part, with token estimates. Read-only. | none, or `json` | text (`context_text`), or the report dict for `json` (`context_report`) |
| `compact` | Summarizes now what is eligible in this agent's memory, then shows tokens before and after. Fails with `busy` if a turn is running. | none | text (`compact_text`) |
| `rename` | Gives the thread a new title and announces `session.updated` (D-069). Principal only (D-052): any other caller gets `forbidden`. Extra spaces are squeezed and the title is cut at 200 characters. | the title (empty fails with `bad_request`) | dict: `{"session": ...}` |
| `unsteer` | Cancels queued steer messages that have not landed yet and publishes `steer.cancelled` for each (D-045). | a message id, or none for the newest | dict: `{"cancelled": [{id, text}]}` |
| `providers` | Provider status (D-034), as rows plus the main model's status. `reload` re-reads credentials after a client stored a key, and never carries a key. | none for status; `reload [name]` (an unknown name is `not_found`) | dict: `{"providers": [...], "main": ...}` or `{"reloaded": ...}` |
| `trace` | The model this session runs on, then the per-turn table: timing, route, estimated cost, flags. | a number N for the last N turns (default 10) | text (`trace_text`) |
| `findings` | The harness's findings by state. | `count` returns `{"open": N, "by_state": {open, fixed, verified, reopened, total}}` (`open` is what needs work: open plus reopened). Anything else returns text: the per-state summary, what changed since this session last looked, then every finding with its state and fixed-in commit. `open`, `fixed`, `verified` or `reopened` limits the list. | text or dict |
| `handoff_facts` | The facts the agent needs to write a handoff: thread, git state of the folder, open findings and tasks, recent summaries, last turns. Reads only. | optional path | dict (`handoff_facts`) |
| `subagent` | Inspect, steer and stop a subagent a session handed work to (D-070). The verb is the first word of the arguments; the task is a task id, a unique prefix of it, or the child's session id. Through this built-in only the principal may steer or stop: `command()` calls `steer` and `stop` without `by`, so the parent-session path in `src/exo/subagents.py` is in the rules but nothing on main calls it yet. Any other caller gets `forbidden`. A local caller can still reach the child's session with `turn.send` or `turn.interrupt` (D-070 "Limits"). A finished child answers `conflict`. No protocol command or event was added. | `list` (this session's tasks), `open <task>`, `send <task> <text>`, `stop <task>` | dict: `list` gives `{"tasks": [{task_id, parent, child_session, status, title, live}]}`; `open` gives the task row, `steerable`, the child's session and the tail of its messages; `send` gives `{steered, author}`; `stop` gives `{stopped}` |
| `harness_error` | A client reports an exception it caught, so the core files a finding. Not for people. | JSON with `where`, `message`, `error_type`, `traceback`. Plain text also works. | nothing |
| `memory_status` | The memory slot's state, for a client's boot line: `on`, `off` until a setup step (no principal store, and `text` is the notice naming `exo init <id>`), `failed` (an error at start) or `none` (no memory plugin). | none | dict `{state, provider, text}` |

Only `context`, `compact`, `trace` and `findings` appear in `commands.list`. `rename`, `unsteer`, `providers`,
`handoff_facts`, `harness_error`, `memory_status` and `subagent` are for clients to call (`/tasks`, `/open`, `/steer` and `/stop <task>` in the TUI, the Tasks page on the web).

### Plugin commands

A plugin command exists only while its plugin is loaded. Registered with `ctx.register_command(name, handler,
description, args_hint)` (`src/exo/core/plugins.py`). A name that is already taken raises a `PluginError`.

| Command | Plugin | What it does | Arguments | Implemented in |
|---|---|---|---|---|
| `/grants` | `exo-gate` | Lists standing approval grants. Revoking one writes a `revoke` row to the ledger. | `list` (default), or `revoke <id>` | `grants_command` in `plugins/exo-gate/exo_gate/hook.py`. See `docs/GATE.md`. |
| `/permissions` | `exo-gate` | Shows mode and counters. Full authorizes all calls in one session, or explicitly all sessions, after each signed audit. Only a live principal enables it. Ask revokes it from any caller. | `status`, `full <session-id>`, `full all`, `bypass`, `bypass launch`, `ask`, `json` | `plugins/exo-gate/exo_gate/hook.py`. Capability fields and scope: `docs/GATE.md` "Full permission mode for one session (D-065)". |
| `/gate` | `exo-gate` | Shows whether policy files changed this session, and a policy error in the pinned or the changed files (#557). `accept` adopts the change (ledger row `policy_accept`); until then the stricter of old and new applies (D-038). | `status` (default), or `accept` | `gate_command` in `plugins/exo-gate/exo_gate/hook.py`. See `docs/GATE.md`. |
| `/exo` | `exo-memory`, `exo-context` | One front door for Exo plugins. `/exo help` lists subcommands. `/exo help <name>` shows one. | `<subcommand> [args]` | `src/exo/dispatcher.py`, `register_root` |
| `/exo branch` | `exo-context` | Writes a handoff and prints the line that starts a fresh session on it. | `<topic>` | `cmd_branch` in `plugins/exo-context/exo_context/commands.py` |
| `/exo handoff` | `exo-context` | Writes a handoff for this session. | optional topic | `cmd_handoff`, same file |
| `/exo resume` | `exo-context` | Loads the newest handoff for a topic (or a path) into this session. | `<topic or path>` | `cmd_resume`, same file |
| `/orchestra` | `exo-orchestra` | Lists seat templates and skills, renders or checks a seat file. | `seats` (default), `skills`, `seat <role> [--seat NAME] [--out DIR]`, `check <file>` | `command_handler` in `plugins/exo-orchestra/exo_orchestra/orchestra.py` |
| `/exo-command` | `exo-command` | Prints a digest of the command centre: approvals, decisions, live sessions, tasks, projects. Needs a running `exo serve`. | none | `_command` in `plugins/exo-command/__init__.py` |
| `/peers` | `exo-bus` | Lists live peer sessions and held messages. Registered only when peers are on (`EXO_PEERS=1`). | none | `command_handlers` in `plugins/exo-bus/exo_bus_hermes/peers.py` |
| `/peer-release` | `exo-bus` | Delivers a held peer message. | `<id>` | same |
| `/peer-deny` | `exo-bus` | Drops a held peer message. | `<id>` | same |

Notes. `/exo` is one command with its own subcommands, so the plugin list that follows `/exo` is whatever plugins
loaded. A plugin adds one with `register_subcommand` from `exo.dispatcher`. In the TUI, `/peers` is answered by the
client through the `peers` protocol command, not by the plugin's `/peers`. `/handoff` in the TUI and `/exo handoff`
are different things: the first asks the agent to write a note, the second writes the handoff file mechanically.

## Part 2. The `exo` CLI

Entry point: `exo = "exo.cli:main"` in `pyproject.toml`. The parser is built in `build_parser` in
`src/exo/cli.py`. A command that lives in another module has a row in its `LAZY` table: the module, the handler and
the help line. `exo` imports that module, and calls its `register` function, only when the command runs (#260). The
other commands keep their name and help line, so usage, errors and `exo --help` read the same. `exo --help`,
`exo --version` and bare `exo` import none of these modules, and a module that fails to import breaks only its own
commands. `build_parser()` with no command builds every command and imports every module.
`exo --version` prints the version.

### Choosing a store

Commands that work on a principal store take two flags:

- `--principal ID`: the principal id. Default: `$EXO_PRINCIPAL`, then the active principal in config.json.
- `--root DIR`: a store directory. It wins over `--principal`.

With neither, the command stops and tells you to pass one. Stores live under `$EXO_HOME/principals/<id>`
(`EXO_HOME` defaults to `~/.exo`), unless `exo init --root` or `exo import-store --root` put one elsewhere.
Those record the directory in config `principals.<id>`, and the id finds it from then on
(`exo.store.principal_root`). In the table, "store" means a command needs one.

### Store and ledger

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo init <id>` | Creates a principal store and an empty signed ledger (mode 0600), and makes it the active principal if none is set. Prints the lint summary. | `--from-template [PATH]` start from a template folder (default: the bundled empty store). `--interview` run the onboarding interview. Also takes the interview flags below. `--root DIR` where to create it, recorded in config `principals.<id>`. | `cmd_init` in `src/exo/onboard.py` |
| `exo interview` | Asks the next onboarding questions for a store (store). | `--answers FILE` read answers from a YAML file. `--max-questions N` (default 12, 0 is all). `--generator MOD:ATTR` a model hook that drafts more questions. `--plan` print the questions in order and exit. `--seed-queue N` hand the next N unasked questions to the ask queue (default 8). | `cmd_interview` in `src/exo/onboard.py` |
| `exo import-store <path>` | Copies an existing store into a new principal. Never edits the source. Skips `.git`, caches, key files, symlinks, and `corpus/private` and the ledger with its `.head` anchor unless asked. Drops a stale anchor a deleted store left for the destination (D-040). | `--id ID`, `--root DIR`, `--include-private`, `--activate` | `cmd_import_store`, `import_store` in `src/exo/onboard.py` |
| `exo find <words...>` | Ranked nodes for some words (store). | `-n N` (default 8) | `cmd_find` in `src/exo/cli.py` |
| `exo get <id>` | Prints one node (store). | `--body-only` | `cmd_get` |
| `exo ctx <words...>` | The best nodes packed under a token budget (store). | `--budget N` (default 1500) | `cmd_ctx` |
| `exo links <id>` | Typed edges going out of and into a node (store). | none | `cmd_links` |
| `exo lint` | Checks the store: schema, dangling links, orphans, leak patterns. Exit code 1 on errors (store). | none | `cmd_lint` |
| `exo graph` | Writes `graph/graph.json` (store). | none | `cmd_graph` |
| `exo index` | Refreshes the node table in `INDEX.md` (store). | none | `cmd_index` |
| `exo log append <kind>` | Appends a signed event to the ledger (store). `<kind>` is one of the ledger kinds in `docs/CORE.md`. | `--author NAME` (required). `--key PATH` (default `$EXO_HOME/keys/<author>.key`). `--text T` or `--body JSON`, else stdin. `--session S`. `--ref ID` (repeatable). | `cmd_log` |
| `exo log verify` | Checks every id, link, chain hash and signature, each key against the key registry, the head anchors and the append-only triggers (D-040). A ledger file that is a symlink fails, and opening it writes nothing through the link (`docs/CORE.md` "Head anchor" names the exceptions). Prints JSON, with `anchored_seq`, the lowest seq an anchor holds, and a `warning` when the two anchors are more than one event apart (the ledger still passes). Exit code 1 if broken (store). | none | `cmd_log` |
| `exo log anchor` | Writes the current head to the ledger's anchor files, for a store made before D-040. The next append does it too. Refuses to replace an anchor that does not match the ledger, since events may have been removed. Exit code 1 then, when the anchor beside the ledger cannot be written, and for a ledger file that is a symlink, even with `--force`. | `--force` replaces it and prints what it replaced: for a store an older Exo left with its anchor ahead after a crash, not for a truncation (`docs/CORE.md` "Head anchor") | `cmd_log` |
| `exo log tail` | Prints the last events (store). | `-n N` (default 10), `--kind K` | `cmd_log` |
| `exo keygen <name>` | Creates an Ed25519 key, mode 0600. Refuses to overwrite. A key made in `$EXO_HOME/keys` is pinned in the key registry. | `--out PATH` (default `$EXO_HOME/keys/<name>.key`) | `cmd_keygen` |
| `exo keys [list]` | The key registry (D-040): pinned signing keys, bus seats, peer seats of other homes, principal ids. | `--json` | `cmd_keys` |
| `exo keys pin NAME PUB` | Pins a public key (base64 or a `.pub` file) under a name. Refuses to change a pinned key without `--replace`. Exit code 1 if refused. Without `--seat` or `--peer` the key may sign this home's ledgers. For peer messages, a name with no key file in `keys/` and no seat file here counts as a seat of another home, so its messages are held as `other`. Pin a peer with `--peer`. | `--seat` (NAME is a bus seat), `--peer` (NAME is a peer seat of another home: its key checks that seat's peer messages and receipts and never signs a ledger, `docs/PEERS.md` "Peer keys"), `--replace` | `cmd_keys` |
| `exo keys principal ID` | Marks a ledger author id as the principal, for consolidation and learning. | none | `cmd_keys` |
| `exo ingest <kind> <path>` | Imports conversations into the ledger as signed events. Kinds: `claude_code`, `hermes`, `claude_ai_export`, `chatgpt_export`, `grok_export` (aliases such as `claude-code`, `chatgpt`). The source is read only (store, or `--ledger`). | `--since TIME`, `--author NAME`, `--as SEAT` (signing seat, default `ingest`), `--key PATH`, `--ledger PATH` | `src/exo/ingest/cli.py` |

### Learning and review

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo ask propose <text>` | Adds a question to the queue (store). | `--node`, `--rationale`, `--impact` (0 to 1), `--uncertainty` (0 to 1), `--channel`, `--default` | `src/exo/ask.py` |
| `exo ask next` | The next questions worth asking, within the interruption budget. | `-n N` (default 3), `--mark` record them as asked, `--author`, `--key` | same |
| `exo ask answer <id> <text>` | Records an answer as a signed `answer` event. | `--author` (required), `--key` | same |
| `exo ask drop <id>` | Drops a question. | `--reason` | same |
| `exo ask list` | Open questions with their value of information. | none | same |
| `exo ask budget` | Shows the interruption budget state as JSON. | none | same |
| `exo ask seed <path>` | Loads a numbered Markdown question list into the queue. | none | same |
| `exo ask decide` | Ask or act? Reads a JSON list of predicted choices on stdin and prints the verdict. | `--domain` (default `default`) | same |
| `exo eval` | An n=1 evaluation report. See `docs/TOOLS-CLI.md`. | `--responder MOD:FN`, `--qa FILE`, `--pairs FILE`, `--seed N`, `--no-write` | `src/exo/eval.py` |
| `exo brief` | The morning brief. See `docs/TOOLS-CLI.md`. | `--html`, `--write` | `src/exo/brief.py` |
| `exo consolidate` | Turns new ledger events into belief proposals. See `docs/TOOLS-CLI.md`. | `--as SEAT`, `--key PATH`, `--dry-run` | `src/exo/consolidate.py` |
| `exo proposals [list\|show\|accept\|reject\|revert] [id]` | Reviews belief proposals. Default action is `list`. | `--reason` (required for `reject` and `revert`), `--all` list decided ones too, `--as SEAT` (default `principal`), `--key PATH` | `cmd_proposals` in `src/exo/consolidate.py` |
| `exo taste [list]` | The taste layers (D-044): whether the house layer is on (or off, and why), then every active item with its weight, class and source tag (`[house 1.3]`, `[learned 0.72, 5 weeks]`, `[you said, <date>]`, `[pinned]`). Read-only. See `docs/TASTE-BASELINE.md`. | `--root DIR` or `--principal ID`, `--json` | `src/exo/baseline/cli.py` |
| `exo taste export-house` | Builds the house taste pack from the principal's store, mechanically normalizes selected claims, and runs privacy and value checks. It does not turn first-person stories into principles; rephrase those before export. Writes nothing if any check fails. Byte-identical output requires the same store snapshot, exporter/runtime, explicit inputs, privacy-check inputs and results, checkout skill manifest and optional judge results. An installed wheel without `skills/MANIFEST.json` records an empty skills list. | `--store DIR` (source), `--out DIR`, `--version X.Y.Z` (all required), `--check-only`, `--soul FILE`, `--decision` (the decision model may hold items, never pass them) | `src/exo/baseline/export.py` |
| `exo taste review OUT` | Shows which value and contested items still need the principal's tick; `--tick` ticks them, `--final` writes `RELEASE.json` once all are ticked. A pack without a valid release is never used. | `--tick ID [ID ...]`, `--final` | `src/exo/baseline/review.py` |

### Running Exo

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo serve` | Runs `exod`, the core protocol server. | `--socket PATH` (default `$EXO_HOME/run/exod.sock`). `--tcp HOST:PORT` also listen on TCP (a bearer token is required). `--token-file PATH`. `--stdio` JSON lines on stdin and stdout. `--engine MOD:FACTORY` (default `exo.compose:create`; `exo.core.engine:create` still works; or `echo`). `--seat NAME` load `$EXO_HOME/seats/<name>.toml`. `--stop` stop the background exod and exit (same as `exo stop`). Refuses, with exit 1, a socket that another core answers on or is starting on (the other core's pid is named): give a second seat its own `--socket`. Each socket has an owner-only lock file beside it (`exod.sock.lock`), held for the core's life, so two cores started at once leave exactly one; a crashed core's lock is gone with it. Only the core that holds the default socket's lock writes `run/exod.pid`, the file `exo stop` reads. | `cmd_serve` in `src/exo/cli_chat.py` |
| `exo chat` | A line client for `exod`. Sends `/name args` lines as `command.run`. `/quit` and `/exit` leave. An approval prompt (`allow/deny/allow_always>`) takes an option it shows, in any case, or `a`, `y` or `yes` for `allow`. Any other reply denies. `allow_always`, like Always in the terminal client, waits for a second step: Enter confirms the standing grant, and any other reply cancels it and shows the options again. | `--socket PATH`, `--url URL`, `--token-file PATH`, `--session ID`, `--resume`, `--title T` | `cmd_chat` in `src/exo/cli_chat.py` |
| `exo` (no command) | Opens the terminal client in the current directory, same as `exo tui`. Used when there are no arguments, or the first argument is a flag other than `-h`, `--help` or `--version`. All flags go to the client. | `--resume`, `--session ID`, `--seat NAME`, `--socket`, `--url`, `--token-file`, `--title`, `--no-intro`, `--full-access` (full for the selected or resumed session after connection, separate from bypass), `--skip-permissions` (alias `--dangerously-skip-permissions`: after connecting, the window sends `/permissions bypass launch`, D-065) | `main` and `cmd_tui` in `src/exo/cli.py` |
| `exo resume` | Reopens the newest thread with messages in the terminal client: `exo --resume`. Other flags pass through (`exo resume --seat X`). | same as `exo` | `main` in `src/exo/cli.py` |
| `exo --version` | Package version, plus the git sha for a checkout install. | none | `version_string` in `src/exo/update_cmd.py` |
| `exo update` | Upgrades Exo in place (installer re-run, git pull and reinstall, or `uv tool upgrade`), prints `old -> new`, then a quick self-check (import of every command's module, protocol export current, `exo models`). | `--dry-run` prints the steps and changes nothing | `cmd_update` in `src/exo/update_cmd.py` |
| `exo tui` | The terminal client. Runs the core in-process if no `exod` is running. Needs `pip install 'exo[tui]'`. | `--resume`, `--session ID`, `--seat NAME`, `--socket`, `--url`, `--token-file`, `--title`, `--no-intro`, `--full-access` (full for the selected or resumed session after connection, separate from bypass), `--skip-permissions` (alias `--dangerously-skip-permissions`) | `cmd_tui` in `src/exo/cli.py`, `clients/tui/exo_tui/__init__.py` |
| `exo stop` | Stops the background core (exod) that `exo web` or `exo serve` started. Reads `$EXO_HOME/run/exod.pid`. When no core answers it clears the stale socket and pid file, unless a core holds the socket's start lock: it then says the core is starting and removes nothing. | none (`-h` prints help and any other word is an error: neither stops the core) | `stop_core` in `src/exo/ensure_core.py` |
| `exo web` | The command centre web client. Joins a running core (exod, or a terminal window's) or starts `exo serve` in the background (D-036). | `--socket`, `--url`, `--token-file`, `--host` (default 127.0.0.1), `--port` (default 7342), `--no-open` (do not open a browser tab) | `cmd_web` in `src/exo/cli.py`, `plugins/exo-command/exo_command/__main__.py` |

### Config, models and decisions

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo config show` | Every key with its effective value. | none | `main` in `src/exo/config.py` |
| `exo config path` | Where `config.json` lives. | none | same |
| `exo config roles` | The model route per role. No secrets. | none | same |
| `exo config get <key>` | One value. Exit code 1 if unset. | `<key>` | same |
| `exo config set <key> <value>` | Sets a key. The value is read as JSON when it parses, else kept as text. | `<key> <value>` | same |
| `exo config unset <key>` | Removes a key. | `<key>` | same |
| `exo models` | Providers, whether a key is found (yes or no, never the value), the route per role, and the `code_find` status. | `--remote PROVIDER` lists that provider's models over the network | `cmd_models` in `src/exo/models_cmd.py` |
| `exo providers` | Provider setup (D-034): status table; `add NAME` (getpass, needs a terminal, never from argv), `test NAME`, `remove NAME`, `default REF`. | `--store keychain\|secret-service\|file` and `--no-test` (skip the live test) on add; `--yes` on remove; `--role` on default | `src/exo/providers_cmd.py`, `src/exo/providers_setup.py` |
| `exo decide <question>` | Asks the decision model one question and prints the probability of each option and the route used. | `--options a,b,c` (required, at least two), `--state TEXT`, `--role ROLE` (default `decision`) | `cmd_decide` in `src/exo/decide_cmd.py` |

### How the harness is doing

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo trace [SESSION]` | The per-turn table for a session (default: the newest), with anomaly flags and peer traffic. | `--last N`, `--calls` list every model call with its cost, `--json` | `cmd_trace` in `src/exo/trace_cmd.py` |
| `exo findings [list]` | What the harness found wrong with itself. | `--status` (`open`, `fixed`, `verified`, `reopened`, `all`; default all), `--json` | `cmd_findings` |
| `exo findings add <title>` | Files a finding by hand. | `--severity bug\|papercut` (default `papercut`), `--signature S`, `--detail TEXT` | same |
| `exo findings fix <id>` | Marks a finding fixed. Later turns re-measure it. | `--in SHA` (required) | same |
| `exo findings export` | A digest for the developer session. | `--md` | same |
| `exo memory [verify]` | Checks the hash chain of each agent's one log, and each principal store's ledger against the pinned keys, its head anchors and its triggers (D-040). A store whose ledger file is gone while an anchor says it held events fails too, and so does one whose ledger file is a symlink. A ledger whose two anchors are more than one event apart passes with a warning. Exit code 1 if broken. | `--agent NAME` (default: all), `--principal ID` or `--root DIR` (default: every store in the home), `--json` | `cmd_memory` in `src/exo/memory_cmd.py` |
| `exo memory threads` | Lists each agent's threads. | `--agent NAME`, `--json` | same |

### Tracking upstream and reading wire packets

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo upstream report` | Ranked upstream changes since the commit recorded for each lifted module. Writes `upstream/REPORT-*.md`. | `--source` (default `hermes`), `--to REF`, `--repo PATH`, `--no-fetch`, `--no-write`, `--json`, `--limit N` (default 30), `--provenance-root DIR` | `cmd_upstream` in `src/exo/upstream.py` |
| `exo upstream mark <exo-path>` | Moves a module's recorded commit after a port, or records a refusal. | `--commit SHA` (required), `--rejected REASON`, `--note`, `--repo`, `--force`, `--provenance-root` | same |
| `exo upstream check` | Validates `PROVENANCE.toml` against the tree. | `--provenance-root` | same |
| `exo upstream merge` | Folds `PROVENANCE*.part.toml` files into `PROVENANCE.toml`. | `--remove` deletes the parts after, `--provenance-root` | same |
| `exo wire render` | Prints an agent wire packet (SYM-2P brief or receipt) in plain English. Reads one packet from the argument, else one per line from stdin. | `[TEXT]` | `cmd_wire` in `src/exo/cli.py`. See `docs/AGENT-WIRE.md`. |
| `exo wire check` | Same input. Prints `ok` or the diagnostics. Exit code 1 if any packet is invalid. | `[TEXT]` | same |

### The bus (a separate program)

The bus has its own entry point, `python -m exo.bus`, not an `exo` subcommand. It needs the `bus` extra for the
relay. Subcommands: `keygen`, `pubkey`, `post`, `inbox`, `tail`, `thread`, `head`, `verify`, `relay`, `registry`,
`checkpoint`. Flags `--url`, `--seat` and `--keys-dir` come before the subcommand. See `src/exo/bus/cli.py` and
`docs/DEVELOPER-GUIDE.md` section 9.

### Exit codes

`0` is success. `1` is a failed check (`lint`, `log verify`, `memory`, a failed decision call, an unset `config get`).
`2` is a usage error from `exo decide` and `exo upstream`. Argument parsing errors exit with `2`, as `argparse` does.
`exo chat` exits with `130` on ctrl+c. Most other errors print one line and exit.


## Core-wide run limits

`/limits [rounds|worker-timeout default|off|number]` controls new work on the connected core through the principal-only `limits` built-in. Worker deadlines also accept `15m` or `1h`. `/limits default` clears both ephemeral overrides. Running work keeps its admitted limits. Clients probe support before mutations. See [Run limits](RUN-LIMITS.md).
## Delegated approval authority

`/approval-authority status|grant|revoke` selects the terminal or web Chat conversation as the parent.
`exo approval-authority status|grant|revoke --socket PATH --session PARENT_ID` uses an existing core.
All management requires the principal, and clients probe support before mutation. The model-only
`delegation_review` tool can inspect, allow once, deny or wait for its owned invocation. It cannot grant
itself authority. See [the capability contract](DELEGATED-APPROVAL-AUTHORITY.md).
