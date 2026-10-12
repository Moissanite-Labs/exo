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
   `session`. `CoreEngine._builtin_command` in `src/exo/core/engine.py` answers a few names. It returns text, a dict
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
The web client (`exo web`) runs `/context`, `/findings` and `/trace` from its composer the same way.

### Client commands (TUI)

These exist only in the terminal client. Each one is a row in `COMMANDS` and a branch in `ExoApp.slash`.

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `/new` | Start a new thread: a fresh focus, the same memory. | `private` makes a thread that is not remembered. | `ExoApp.new_session` |
| `/sessions` | Pick one of this agent's threads to switch to. | none | `ExoApp.pick_session` (calls `session.list`) |
| `/resume` | Same as `/sessions`. | none | same |
| `/fork` | Fork this session and open the fork. | optional title | `session.fork` |
| `/undo` | Undo the last turn and reload the session. | none | `session.undo` |
| `/clear` | Clear the transcript view. History is kept. | none | `ExoApp.clear_transcript` |
| `/cwd` | Show this session's working folder. | none | `session.get` |
| `/restart` | Reopen this session in a new terminal tab and quit this one. | none | `ExoApp.restart`, `clients/tui/exo_tui/restart.py` |
| `/exit` | Quit. | none | `ExoApp.slash` |
| `/quit` | Same as `/exit`. | none | same |
| `/model` | Pick the model for this session, or set it. | `<ref>` sets it. `default <ref>` sets the default. | `ExoApp.set_model` (`models.list`, `models.set`) |
| `/default` | Set the default model for new sessions. Saved to `models.roles.main` in `config.json`, old routes kept as fallbacks. | `<ref>`, or none to pick | `ExoApp.set_default` (`models.set`, `config.set`) |
| `/providers` | Provider setup panel: status, masked key entry, test, remove, default. Client side; the key never reaches the composer, transcript or protocol. Sends `command.run providers reload <name>` after a change. | `add <name>` opens the key field | `ProvidersScreen` in `clients/tui/exo_tui/providers.py`, engine `src/exo/providers_setup.py` |
| `/usage` | Show context size, cost and the model in use. | none | `ExoApp.slash` (client state, no call) |
| `/panel` | Reopen the last panel. | none | `ExoApp.show_panel` |
| `/peers` | List other live sessions you can message. | none | `peers` protocol command, `action="list"` |
| `/send` | Message another session as yourself. | `<peer> <text>` | `peers` protocol command, `action="send"`, `via="human"` |
| `/handoff` | Ask the agent to write a handoff note for the next session. | optional path | `ExoApp.handoff`. The core gathers facts with the `handoff_facts` built-in. See `docs/HANDOFF.md`. |
| `/intro` | Ask the agent for an orientation. | none | `ExoApp.intro` (an ordinary turn) |
| `/tutorial` | Turn tips on or off, or show them again. | `on`, `off`, `reset`. None shows the state. | `ExoApp.tutorial_command` (`config.set` on `tutorial.enabled`) |
| `/help` | List every command. | none | `ExoApp.help_text` |

Not slash commands, but in the same composer: `@path` adds a file to the turn, `!cmd` asks the agent to run a shell
command and show the output. Keys: ctrl+p palette, ctrl+o tool output, ctrl+y accept, ctrl+e edit, ctrl+r reject.

Anything else the TUI does not know goes to `command.run`. That is how `/context`, `/compact`, `/trace`, `/findings`
and plugin commands work there.

### Core built-ins (`command.run`)

All in `CoreEngine._builtin_command`, `src/exo/core/engine.py`.

| Name | What it does | Arguments | Returns |
|---|---|---|---|
| `context` | Shows what the next model call for this thread carries, part by part, with token estimates. Read-only. | none | text (`context_text`) |
| `compact` | Summarizes now what is eligible in this agent's memory, then shows tokens before and after. Fails with `busy` if a turn is running. | none | text (`compact_text`) |
| `trace` | The model this session runs on, then the per-turn table: timing, route, estimated cost, flags. | a number N for the last N turns (default 10) | text (`trace_text`) |
| `findings` | The harness's open findings. | `count` returns `{"open": N}`. Anything else returns the open list as text. | text or dict |
| `handoff_facts` | The facts the agent needs to write a handoff: thread, git state of the folder, open findings and tasks, recent summaries, last turns. Reads only. | optional path | dict (`handoff_facts`) |
| `harness_error` | A client reports an exception it caught, so the core files a finding. Not for people. | JSON with `where`, `message`, `error_type`, `traceback`. Plain text also works. | nothing |

Only `context`, `compact`, `trace` and `findings` appear in `commands.list`. `handoff_facts` and `harness_error` are
for clients to call.

### Plugin commands

A plugin command exists only while its plugin is loaded. Registered with `ctx.register_command(name, handler,
description, args_hint)` (`src/exo/core/plugins.py`). A name that is already taken raises a `PluginError`.

| Command | Plugin | What it does | Arguments | Implemented in |
|---|---|---|---|---|
| `/grants` | `exo-gate` | Lists standing approval grants. Revoking one writes a `revoke` row to the ledger. | `list` (default), or `revoke <id>` | `grants_command` in `plugins/exo-gate/exo_gate/hook.py`. See `docs/GATE.md`. |
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
`src/exo/cli.py`, which also calls the `register` function of each module below. `exo --version` prints the version.

### Choosing a store

Commands that work on a principal store take two flags:

- `--principal ID`: the principal id. Default: `$EXO_PRINCIPAL`.
- `--root DIR`: a store directory. It wins over `--principal`.

With neither, the command stops and tells you to pass one. Stores live under `$EXO_HOME/principals/<id>`
(`EXO_HOME` defaults to `~/.exo`). In the table, "store" means a command needs one.

### Store and ledger

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo init <id>` | Creates a principal store and an empty signed ledger (mode 0600), and makes it the active principal if none is set. Prints the lint summary. | `--from-template [PATH]` start from a template folder (default: the bundled empty store). `--interview` run the onboarding interview. Also takes the interview flags below. `--root DIR` where to create it. | `cmd_init` in `src/exo/onboard.py` |
| `exo interview` | Asks the next onboarding questions for a store (store). | `--answers FILE` read answers from a YAML file. `--max-questions N` (default 12, 0 is all). `--generator MOD:ATTR` a model hook that drafts more questions. `--plan` print the questions in order and exit. `--seed-queue N` hand the next N unasked questions to the ask queue (default 8). | `cmd_interview` in `src/exo/onboard.py` |
| `exo import-store <path>` | Copies an existing store into a new principal. Never edits the source. Skips `.git`, caches, key files, symlinks, and `corpus/private` and the ledger unless asked. | `--id ID`, `--root DIR`, `--include-private`, `--activate` | `cmd_import_store`, `import_store` in `src/exo/onboard.py` |
| `exo find <words...>` | Ranked nodes for some words (store). | `-n N` (default 8) | `cmd_find` in `src/exo/cli.py` |
| `exo get <id>` | Prints one node (store). | `--body-only` | `cmd_get` |
| `exo ctx <words...>` | The best nodes packed under a token budget (store). | `--budget N` (default 1500) | `cmd_ctx` |
| `exo links <id>` | Typed edges going out of and into a node (store). | none | `cmd_links` |
| `exo lint` | Checks the store: schema, dangling links, orphans, leak patterns. Exit code 1 on errors (store). | none | `cmd_lint` |
| `exo graph` | Writes `graph/graph.json` (store). | none | `cmd_graph` |
| `exo index` | Refreshes the node table in `INDEX.md` (store). | none | `cmd_index` |
| `exo log append <kind>` | Appends a signed event to the ledger (store). `<kind>` is one of the ledger kinds in `docs/CORE.md`. | `--author NAME` (required). `--key PATH` (default `$EXO_HOME/keys/<author>.key`). `--text T` or `--body JSON`, else stdin. `--session S`. `--ref ID` (repeatable). | `cmd_log` |
| `exo log verify` | Checks every id, link, chain hash and signature. Prints JSON. Exit code 1 if broken (store). | none | `cmd_log` |
| `exo log tail` | Prints the last events (store). | `-n N` (default 10), `--kind K` | `cmd_log` |
| `exo keygen <name>` | Creates an Ed25519 key, mode 0600. Refuses to overwrite. | `--out PATH` (default `$EXO_HOME/keys/<name>.key`) | `cmd_keygen` |
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

### Running Exo

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo serve` | Runs `exod`, the core protocol server. | `--socket PATH` (default `$EXO_HOME/run/exod.sock`). `--tcp HOST:PORT` also listen on TCP (a bearer token is required). `--token-file PATH`. `--stdio` JSON lines on stdin and stdout. `--engine MOD:FACTORY` (default `exo.core.engine:create`, or `echo`). `--seat NAME` load `$EXO_HOME/seats/<name>.toml`. | `cmd_serve` in `src/exo/cli_chat.py` |
| `exo chat` | A line client for `exod`. Sends `/name args` lines as `command.run`. `/quit` and `/exit` leave. | `--socket PATH`, `--url URL`, `--token-file PATH`, `--session ID`, `--resume`, `--title T` | `cmd_chat` in `src/exo/cli_chat.py` |
| `exo` (no command) | Opens the terminal client in the current directory, same as `exo tui`. Used when there are no arguments, or the first argument is a flag other than `-h`, `--help` or `--version`; all flags go to the client. | `--resume`, `--session ID`, `--seat NAME`, `--socket`, `--url`, `--token-file`, `--title`, `--no-intro` | `main` and `cmd_tui` in `src/exo/cli.py` |
| `exo resume` | Reopens the last session in the terminal client: `exo --resume`. Other flags pass through (`exo resume --seat X`). | same as `exo` | `main` in `src/exo/cli.py` |
| `exo --version` | Package version, plus the git sha for a checkout install. | none | `version_string` in `src/exo/update_cmd.py` |
| `exo update` | Upgrades Exo in place (installer re-run, git pull and reinstall, or `uv tool upgrade`), prints `old -> new`, then a quick self-check (import, protocol export current, `exo models`). | `--dry-run` prints the steps and changes nothing | `cmd_update` in `src/exo/update_cmd.py` |
| `exo tui` | The terminal client. Runs the core in-process if no `exod` is running. Needs `pip install 'exo[tui]'`. | `--resume`, `--session ID`, `--seat NAME`, `--socket`, `--url`, `--token-file`, `--title`, `--no-intro` | `cmd_tui` in `src/exo/cli.py`, `clients/tui/exo_tui/__init__.py` |
| `exo web` | The command centre web client. Needs a running `exo serve`. | `--socket`, `--url`, `--token-file`, `--host` (default 127.0.0.1), `--port` (default 7342) | `cmd_web` in `src/exo/cli.py`, `plugins/exo-command/exo_command/__main__.py` |

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
| `exo providers` | Provider setup (D-034): status table; `add NAME` (getpass, needs a terminal, never from argv), `test NAME`, `remove NAME`, `default REF`. | `--store keychain\|secret-service\|file` on add; `--yes` on remove; `--role` on default | `src/exo/providers_cmd.py`, `src/exo/providers_setup.py` |
| `exo decide <question>` | Asks the decision model one question and prints the probability of each option and the route used. | `--options a,b,c` (required, at least two), `--state TEXT`, `--role ROLE` (default `decision`) | `cmd_decide` in `src/exo/decide_cmd.py` |

### How the harness is doing

| Command | What it does | Arguments | Implemented in |
|---|---|---|---|
| `exo trace [SESSION]` | The per-turn table for a session (default: the newest), with anomaly flags and peer traffic. | `--last N`, `--calls` list every model call with its cost, `--json` | `cmd_trace` in `src/exo/trace_cmd.py` |
| `exo findings [list]` | What the harness found wrong with itself. | `--status` (`open`, `fixed`, `verified`, `reopened`), `--json` | `cmd_findings` |
| `exo findings add <title>` | Files a finding by hand. | `--severity bug\|papercut` (default `papercut`), `--signature S`, `--detail TEXT` | same |
| `exo findings fix <id>` | Marks a finding fixed. Later turns re-measure it. | `--in SHA` (required) | same |
| `exo findings export` | A digest for the developer session. | `--md` | same |
| `exo memory [verify]` | Checks the hash chain of each agent's one log. Exit code 1 if broken. | `--agent NAME` (default: all), `--json` | `cmd_memory` in `src/exo/memory_cmd.py` |
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
