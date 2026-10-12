# Exo developer guide

This guide shows you how to extend Exo: add a tool, a plugin, a seat, a skill, a built-in command, and how to test
and land the change. It says where to look and what rule to follow. It does not repeat the reference pages. The map
of all docs is `docs/DOCS-MAP.md`. Users read `docs/USER-GUIDE.md`.

## The docs rule

Every change that lands does all of this in the same commit:

1. Updates its technical doc (the `docs/*.md` page for that part) and `DECISIONS.md`.
2. Adds or updates a row in `docs/DOCS-MAP.md`.
3. Notes its impact on the user guide and the developer guide. If a command, key, setting or step changed, edit
   `docs/USER-GUIDE.md` or this file in the same commit. If nothing changed for them, say "none" in the commit
   message.

Whitepaper changes are not part of the commit. They are batched per wave, and the map shows which decisions the
whitepaper still lacks. `tests/docs/test_docs_map.py` fails when a `## D-0NN` heading has no row in the map, or when
the map links a file that does not exist.

## 1. Repo layout

| Path | What lives there |
|---|---|
| `src/exo/core/` | The core: loop, engine, router, tools, sessions, context tree, jobs, peers, scheduler, plugin loader. Small on purpose. |
| `src/exo/protocol/` | The wire protocol: `schema.py` (commands and events), `client.py`, `export.py`, `generated/`. |
| `src/exo/server/` | The server that carries the protocol (Unix socket, TCP with a token, or stdio). |
| `src/exo/*.py` | The generic mechanism: store, ledger, crypto, gate, capture, consolidate, eval, brief, onboard, CLI. |
| `src/exo/learn/` | The learning loop (`docs/LEARNING.md`). |
| `plugins/` | The six plugins: `exo-memory`, `exo-context`, `exo-gate`, `exo-bus`, `exo-command` (web), `exo-orchestra`. |
| `clients/tui/` | The terminal client (Textual). |
| `skills/` | The skill pack and `MANIFEST.json`. |
| `templates/` | The empty principal store and the question bank. |
| `docs/` | Technical docs and the guides. `docs/ui/` holds screenshots. |
| `tests/` | The root suite. Plugin suites live in each plugin's `tests/`. |
| `site/` | The project site. `docs/WHITEPAPER.md` is the long paper. |
| `DECISIONS.md` | Every significant choice: what, why, what was rejected. |

**Core, plugins, clients.** The core does the work and holds the state. Plugins add tools, commands, hooks, a memory
provider, a context engine, channels and skills. Clients only speak the protocol. A client never reads another
program's database. Nothing in the core may assume one person's names, accounts or machines (D-004).

## 2. The protocol

Source of truth: `src/exo/protocol/schema.py`. Commands go in, events come out, and every event has a `seq` so a
client resumes with `events.subscribe(since=seq)`. Reference: `docs/CORE-V2-INTERFACES.md` and
`docs/STANDALONE.md` section 5.

The protocol is capped at 30 commands and under 30 events. A test enforces it. All 30 command slots are used, so
a new feature does not get a new command type. Add a field to an existing type, or add a built-in
`command.run` command (below).

The 30 commands:

| Group | Commands |
|---|---|
| Handshake and events | `hello`, `events.subscribe` |
| Sessions | `session.create`, `session.list`, `session.get`, `session.fork`, `session.resume`, `session.undo` |
| Turns and approvals | `turn.send`, `turn.interrupt`, `approval.respond`, `approvals.list`, `question.answer` |
| Commands | `command.run`, `commands.list` |
| Lookup and config | `search`, `config.get`, `config.set`, `models.list`, `models.set`, `tools.list`, `skills.list`, `tasks.list`, `seat.list` |
| Learning and panels | `taste.list`, `taste.edit`, `feedback.send`, `ui.respond` |
| Scheduling and peers | `schedule.edit`, `peers` |

The events (28 of the 29 allowed): `session.created`, `session.updated`, `turn.started`, `turn.completed`,
`message.delta`, `message.completed`, `reasoning.delta`, `tool.started`, `tool.output`, `tool.completed`,
`approval.requested`, `approval.resolved`, `question.asked`, `context.compacted`, `memory.summarized`,
`job.updated`, `handoff.written`, `task.updated`, `usage.updated`, `decision.queued`, `taste.updated`, `ui.panel`,
`peer.message`, `peer.delivery`, `model.call`, `harness.error`, `notice`, `error`.

Rules for changes (written in the file header): additive only, within a protocol version. New optional fields with
defaults, or new types. Never rename or remove. Commands are strict: an unknown field is rejected. Clients decode
events with `strict=False` so a newer server does not break them.

After any change to the schema, regenerate the exports and commit them:

    python -m exo.protocol.export            # rewrites src/exo/protocol/generated/
    python -m exo.protocol.export --check    # exits 1 if the files are stale

### Extend through `command.run` built-ins

`command.run` takes `name`, `args` and `session`. The core answers a few names itself, in
`CoreEngine._builtin_command` in `src/exo/core/engine.py`: `context`, `compact`, `trace`, `findings`,
`handoff_facts` and `harness_error`. Anything else goes to a command a plugin registered. To add one:

1. For a core command, add a branch in `_builtin_command` and an entry in `_commands_list` so it shows in
   `commands.list`. Return a string, dict or list.
2. For a plugin command, call `ctx.register_command(name, handler, description=..., args_hint=...)` (section 4).
   The handler gets the raw argument string.
3. For a TUI command, add a row to the `COMMANDS` table in `clients/tui/exo_tui/app.py`. `/help` is built from it.
   A test checks that every command in `/help` appears in `skills/exo-intro/SKILL.md`, so update the skill too.

## 3. Add a tool

A tool is a name, a description, a JSON schema, a risk, a toolset and a handler. Built-in tools live in
`src/exo/core/tools/builtin/`. Each module has a `register(reg)` function, called from `register_builtins` in
`builtin/__init__.py`. Pattern (from `codefind.py`):

```python
reg.tool("code_find", DESCRIPTION, {"type": "object", "properties": {...}, "required": ["question"]},
         risk="low", toolset="search", check_fn=_available)(code_find)
```

The handler is `handler(args: dict, ctx: ToolContext) -> str | ToolResult`. Raise `ToolError` for a failure the model
should see. A sync handler runs in a worker thread. `check_fn` hides the tool when it cannot run here.
`ToolContext.resolve(path)` resolves a path against the thread's folder.

Then do the next three steps.

**Classify it in the gate.** The gate decides allow, log, ask or deny by category. Add the tool to `TOOLS` in
`src/exo/gate.py`, mapped to a category such as `read`, `local_write`, `shell`, `delegate`, `credentials`,
`message_send`. A tool the classifier does not know and the registry calls high risk is an ask, never an allow.
If the tool sends something outward, its category must be one the gate treats as outward, so approvals bind to the
exact text (`docs/GATE.md`).

**Keep secrets out.** The registry removes known key values from every tool result before it reaches the model or a
client. A tool that runs a subprocess must use the filtered environment (`docs/TOOLS.md`, "Environment of bash and
jobs"). A tool that needs a secret reads it from the keys file or the environment, never from its arguments, and
never puts it in a command line.

**Document and test it.** Add a row to `docs/TOOLS.md`. Add a test in `tests/core/tools/`. If it is a read, add it to
the gate tests. Decide whether a subagent may use it: the blocked list is `BLOCKED_TOOLS` in
`src/exo/core/agents/delegate.py`.

A plugin registers a tool with `ctx.register_tool(name, toolset, schema, handler, risk=...)`.

## 4. Add a plugin

A plugin is a folder with `plugin.yaml`, an `__init__.py` that has `register(ctx)`, and a `tests/` folder.
The manifest needs a `name`. Other keys in use: `version`, `description`, `kind`, `hooks`, `config_schema` (with a
default for each key), `tags`. Look at `plugins/exo-command/plugin.yaml` for a full one.

Plugins are found in `$EXO_PLUGIN_DIRS`, then `$EXO_HOME/plugins`, then the repo's `plugins/`. A seat can limit the
list with `[plugins] enabled`. A failed `register` is rolled back, so a half-loaded plugin leaves nothing behind.

What `ctx` offers (`src/exo/core/plugins.py`):

| Call | Does |
|---|---|
| `register_tool` | Adds a tool. |
| `register_hook(name, fn)` | Subscribes to a hook point. |
| `register_command(name, handler, description, args_hint)` | Adds a slash command, run through `command.run`. |
| `register_memory_provider(p)` | Fills the memory slot (one provider is active). |
| `register_context_engine(e)` | Fills the context slot. |
| `register_channel(name, factory)` | Adds a channel the server runs. |
| `register_skill(name, path)` | Adds a skill file. |
| `get_config(key, default)` | Reads a plugin setting. |

**Hooks.** The names are in `VALID_HOOKS` in `src/exo/core/hooks.py`: `pre_tool_call`, `post_tool_call`,
`transform_tool_result`, `pre_llm_call`, `post_llm_call`, `transform_llm_output`, `on_session_start`,
`on_session_end`, `on_session_reset`, `on_turn_start`, `on_turn_end`, `on_compaction`, `pre_approval_request`,
`post_approval_response`, `pre_command`, `subagent_start`, `subagent_stop`, `on_skill_lifecycle`. Callbacks take
keyword arguments only. A failing callback is logged and skipped (it fails open), except that a `pre_tool_call`
block is honoured. To veto a call, `pre_tool_call` returns `{"action": "block", "message": ...}`. To ask the
principal, it returns `{"action": "approve", ...}` (see `plugins/exo-gate/exo_gate/hook.py`).

**Memory provider hooks.** A memory provider is a subclass of `MemoryProvider` in `src/exo/core/slots.py`. Two
methods matter for the principal's words:

- `capture_principal(messages, session_id=, source=)`: the core offers the span about to be summarized, before it is
  folded, so the provider can write the principal's rulings and corrections to the store (D-032). It must return
  quickly and never raise past its own handling. A hook that raises or returns `None` leaves them uncaptured, and
  summarizing carries on. Messages carry `principal: true|false`. See `plugins/exo-memory/exo_memory/provider.py`
  and `docs/MEMORY-MODEL.md`.
- `on_pre_compress(messages)`: insights from messages about to be compacted, returned as text for the summary prompt.
  `checkpoint(reason="pre_compress")` and `on_compacted(session, node)` are optional siblings.

Do not log or print message text from these hooks. Report errors through `exo.core.trace.report` so they show as
`harness.error` and become findings.

## 5. Add a seat

A seat is a TOML file in `$EXO_HOME/seats/<name>.toml`, or `<name>/seat.toml` with a `SOUL.md`. The full format is
`docs/SEATS.md`. The tables:

| Table | Purpose |
|---|---|
| `[seat]`, `[principal]` | Name, role, soul file, and the principal id the seat serves. |
| `[models]` | `tier` (fast, standard, deep) and per-role router overrides. |
| `[tools]` | `toolsets` to allow, and `env_allow`: secret-looking variables that bash and jobs may keep. Default none. |
| `[skills]` | `folders` to search first, `load` as an allow list. |
| `[mcp.<name>]` | An MCP server: `command` or `url`, `env`, `risk`. |
| `[channels.<name>]` | Channels to run with the server. |
| `[plugins]` | `enabled` list. |
| `[peers]` | `wake`, `hold_minutes`, `policy` by trust, `rules` by sender (`docs/PEERS.md`). |
| `[jobs]` | `max_concurrent`, `max_wall_s`, `output_cap`, `wake`, `notice_lines`, `notice_chars` (`docs/JOBS.md`). |
| `[trace]` | Anomaly limits that override config.json (`docs/TRACE.md`). |
| `[session]` | `cwd`: where the seat's threads start. |

Validate a role template with `/orchestra check <file>`. Templates live in `plugins/exo-orchestra/seats/`. Keys the
loader does not read are ignored, so check the spelling.

## 6. Add a skill

A skill is a folder `skills/<name>/` with a `SKILL.md`. The frontmatter needs `name` and `description`. The
description says when to use it. The system prompt carries one line per skill, and the `skill` tool loads the body.
Search order, first name wins: the seat's folders, `$EXO_HOME/skills`, the Exo pack (`skills/`), then skills that
plugins register.

Add the skill to `skills/MANIFEST.json`. Each entry has `name`, `path`, `description`, `source` (where it came from),
`generalized` (what you removed to make it generic) and `scripts` (paths under the skill folder). The skill pack is
generic. Nothing about one person or one team goes in it. Their rules live in their store.

## 7. Tests

Run these from the repo root, in a venv with the dev extras and `textual`:

    python3 -m venv .venv
    .venv/bin/pip install -e '.[dev,tui,mcp,bus]'
    ulimit -n 4096                       # macOS defaults to 256 open files
    .venv/bin/python -m pytest -q        # the root suite

Plugin suites are separate. They need a bus address that goes nowhere and a seat name, so they never touch a real bus:

    EXO_BUS_URL=http://127.0.0.1:9 EXO_BUS_SEAT=builder .venv/bin/python -m pytest plugins/exo-gate/tests -q

Run each plugin folder you changed: `plugins/<name>/tests`.

If you changed the protocol schema, also run `python -m exo.protocol.export --check`.

Rules that tests enforce: the protocol caps, the core line budget, the TUI colour tokens against the site's
`style.css`, that every `/help` command appears in the intro skill, and the docs map (`tests/docs/test_docs_map.py`).
Tests use fake models and no network. The conftest fixtures isolate `EXO_HOME`, the bus identity and the peers
folder, so a test never reads your real setup.

Check your prose before you commit docs:

    python3 skills/clean-writing/scripts/check-prose.py docs/YOUR-FILE.md

## 8. Decisions and the merge flow

**`DECISIONS.md`.** Each significant choice gets a heading `## D-NNN (date) title`, in number order, newest last.
Fields: Decision, Why, Rejected, Revisit if. Add the entry in the same commit as the change. A number that is reserved but not written (D-027 today) has no
heading, and the map says so.

**Branch and merge.**

1. One branch and one pull request per change. A trivial doc or script sync may share one.
2. Run the root suite and the plugin suites you touched. Green before you push.
3. Commit message: what changed and why, plus the docs and user-impact line from the docs rule.
4. Push the branch. Never push to `main` directly.
5. Open a pull request. The body says `Fixes #N` when it closes an issue. Keep every word fit for a public
   repository: no internal names, no paths from your machine, no keys.
6. A reviewer reads the diff, runs the suite, and merges. Merge your own pull request only where you hold merge
   rights.

Private data and secrets never go in the repo. The principal store is separate from this repository.
