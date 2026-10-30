# Exo developer guide

This guide shows you how to extend Exo: add a tool, a plugin, a seat, a skill, a built-in command, and how to test
and land the change. Section 9 has short recipes: panels from a tool, decision models, the bus, the learning loop and the house taste. It says where to look and what rule to follow. It does not repeat the reference pages. The map
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

**The four layers.** Each change can touch up to four kinds of doc, for four kinds of reader:

- **Whitepaper** (`docs/WHITEPAPER.md`): the long paper. What Exo is, why it is built this way, and what is built
  versus designed. For someone deciding whether to trust the design.
- **User guide** (`docs/USER-GUIDE.md`): what a person types, presses and sees, in task order.
- **Developer guide** (this file): how to extend, test and land a change, with pointers.
- **Technical docs** (the other `docs/*.md` pages, plus `DECISIONS.md`): the reference for one part: fields, rules,
  files, the reason for each choice.

**The map.** In `docs/DOCS-MAP.md` each cell for a layer holds a file plus section, `n/a` (the layer does not apply
to this change), or `MISSING`. A cell stays `MISSING` only when the feature is not on main yet or the layer has
nothing real to say about it. Do not fill a cell with a vague link to make the gap vanish. The whitepaper is
batched per wave: each batch is a new version (`vX.Y`), with its own header, changelog line and tag (section 8).

## 1. Repo layout

The [agent and human work vessel proposal](AGENT-OPERATOR-DESIGN.md) describes
pending continuity and supervision work. It changes no runtime contract or policy.

**Automatic continuity checkpoints.** `exo.work_checkpoint` derives a bounded
record index from visible session messages and job metadata. Composition enables
it by default; it chains existing callbacks and workspace injection, adds no
model calls or replay, and reconstructs after abrupt exits. Keep it separate from
manual working notes. See [WORK-CHECKPOINT.md](WORK-CHECKPOINT.md) for privacy, undo,
uncertain-effects semantics and acceptance tests.

**Session working notes (D-086).** `exo.work_notebook` owns optional revisioned
state in `sessions.sqlite`. Composition validates `work_notes` before opening
runtime resources, then attaches the gated tool, per-session prompt and handoff
source. Updates compare revisions inside one transaction. Keep note authorship
as `agent`, preserve session scope, and use existing gate and executor dispatch.
The adapter and limits are documented in [WORK-NOTEBOOK.md](WORK-NOTEBOOK.md).

| Path | What lives there |
|---|---|
| `src/exo/core/` | The core: loop, engine, router, tools, sessions, context tree, approvals, ingress, jobs, plugin loader. Small on purpose. |
| `src/exo/compose.py` | `create()`, the factory `exo serve` runs: config, seat, executor, plugins. It attaches what lives outside the core: peers, the scheduler, learning. |
| `src/exo/peers.py`, `src/exo/scheduler.py` | The peer transport (D-026) and scheduled jobs (D-022), outside the core since D-067. |
| `src/exo/protocol/` | The wire protocol: `schema.py` (commands and events), `client.py`, `export.py`, `generated/`. |
| `src/exo/server/` | The server that carries the protocol (Unix socket, TCP with a token, or stdio). |
| `src/exo/diagnostics.py` | The read-only views (`/context`, `/compact`, `/trace`, `/findings`, handoff facts) and the built-in `command.run` names, as functions over the engine. |
| `src/exo/subagents.py` | List, open, steer and stop a running subagent (D-070). |
| `src/exo/*.py` | The rest of the generic mechanism: store, ledger, crypto, gate, capture, consolidate, eval, brief, onboard, CLI. |
| `src/exo/learn/` | The learning loop (`docs/LEARNING.md`). |
| `src/exo/baseline/` | The house taste (D-044): pack loader, taste layers, privacy check, `exo taste export-house` (`docs/TASTE-BASELINE.md`). |
| `plugins/` | The six plugins: `exo-memory`, `exo-context`, `exo-gate`, `exo-bus`, `exo-command` (web), `exo-orchestra`. |
| `clients/tui/` | The terminal client (Textual). |
| `skills/` | The skill pack and `MANIFEST.json`. |
| `templates/` | The empty principal store and the question bank. |
| `docs/` | Technical docs and the guides. `docs/ui/` holds screenshots. |
| `tests/` | The root suite. Plugin suites live in each plugin's `tests/`. |
| `site/` | The project site. `docs/WHITEPAPER.md` is the long paper. |
| `DECISIONS.md` | Every significant choice: what, why, what was rejected. |

**The compose and diagnostics split (#46, `docs/ENGINE-SPLIT.md` "Progress").** `exo.compose:create` builds the
engine that `exo serve` runs; `exo.core.engine:create` forwards to it. The diagnostic views and the built-in
`command.run` names live in `src/exo/diagnostics.py`, outside the counted core. The rule for contributors: new wiring
goes in compose, new read-only views go in diagnostics, and neither goes into `CoreEngine`. These modules still read
some private state (`loop._state`, `store._q`, tree `_view`) that step 1 of the split will replace with public queries.

**The core budget (D-077, D-083).** `test_core_line_budget` in `tests/integration/test_wave1_acceptance.py` counts the lines
in `src/exo/core`, `src/exo/protocol`, `src/exo/server`, `src/exo/cli_chat.py` and `src/exo/upstream.py` (generated
protocol files excluded). The ceiling is off (D-083): the test reports the count against `CORE_FLOOR = 14,813` and
asserts nothing about lines. Run it with `-s` to see the number. It does assert two step 4 structure counts, which
ratchet down: `INWARD_IMPORTS_MAX = 39`
imports of a product module from the core, and `PRIVATE_STATE_MAX = 8` cross-module private-state reads. A real
reduction lowers the floor, and the floor never rises. Section 7 has the rules
(`docs/ENGINE-SPLIT.md` "Budget policy"). Never exclude counted modules to pass. Put a product feature built on the
engine outside the counted core, as compose, diagnostics, `subagents.py` and `turn_settle.py` do. The protocol has
its own cap (section 2).

**Core, plugins, clients.** The core does the work and holds the state. Plugins add tools, commands, hooks, a memory
provider, a context engine, channels and skills. Clients only speak the protocol. A client never reads another
program's database. Nothing in the core may assume one person's names, accounts or machines (D-004).

**The engine and what is attached to it (D-067, `docs/ENGINE-SPLIT.md`).** `CoreEngine` runs turns and answers
commands. Its collaborators have small interfaces:

- `engine.approvals` is an `ApprovalDesk` (`src/exo/core/approvals.py`): `ask`, `listing`, `respond` and
  `announce_resolved`, one `Pending` record per open request. Code that needs a human answer calls
  `engine.approvals.ask(...)`. A test that fakes an open request puts a `Pending` in `engine.approvals.open`.
- Peer discovery rejects non-object JSON and non-integer (including boolean) pids before constructing
  presence records. `Directory.scan(prune=False)` leaves rejected files in place; normal scans prune them.
  Unknown signed fields remain in `Directory.entry`'s original document for signature verification.
- `engine.peers` is the `PeerService` from `exo.peers`, or `None`. `exo.compose` passes
  `attach_peers=exo.peers.attach`. A test that needs peers does the same, then calls `await engine.peers.start(sock)`.
- `engine.scheduler` is the `Scheduler` from `exo.scheduler`, or `None`. `exo.compose` passes
  `attach_scheduler=Scheduler`; without it `schedule.edit` answers `unsupported`. `stop()` keeps it restartable;
  `close()` closes schedule.sqlite and is final.
- `await engine.close()` stops jobs, peers, channels and the scheduler, then closes schedule.sqlite and the
  session store. The store is not final: a later read or write reopens sessions.sqlite (#281). Errors reported while
  it stops them are still recorded as `harness.error` events. The engine drops its trace sink just before the
  session store closes.
- `engine.run_turn(body, meta)` runs one turn with the lifecycle hooks around it. Product code outside the core
  (the scheduler) uses it, not the loop's private state.
- A plugin callback that may be sync or async goes through `exo.core.hooks.maybe_await`. One that may not declare
  every keyword goes through `exo.core.hooks.accepted`. Do not write a new copy of either.

A new subsystem that is a product feature built on the engine (like the scheduler) goes outside `src/exo/core` and
is attached by `exo.compose`, at the point in `CoreEngine.__init__` where its tools must register, so the tool order
the model sees stays the same.

## 2. The protocol

Source of truth: `src/exo/protocol/schema.py`. Commands go in, events come out, and every event has a `seq` so a
client resumes with `events.subscribe(since=seq)`. Reference: `docs/CORE-V2-INTERFACES.md` and
`docs/STANDALONE.md` section 5.

The protocol is capped at 30 commands and under 30 events. `MAX_COMMANDS` and `MAX_EVENTS` in `schema.py` make
registering a 31st type raise an error, and `tests/protocol/test_schema.py` keeps events under 30. All 30 command
slots and all 29 event slots are used, so a new feature does not get a new command or event type. Add a field to an
existing type, or add a built-in `command.run` command (below).

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

The 29 events (every slot): `session.created`, `session.updated`, `turn.started`, `turn.completed`,
`message.delta`, `message.completed`, `reasoning.delta`, `tool.started`, `tool.output`, `tool.completed`,
`approval.requested`, `approval.resolved`, `steer.cancelled` (D-045), `question.asked`, `context.compacted`, `memory.summarized`,
`job.updated`, `handoff.written`, `task.updated`, `usage.updated`, `decision.queued`, `taste.updated`, `ui.panel`,
`peer.message`, `peer.delivery`, `model.call`, `harness.error`, `notice`, `error`.

Rules for changes (written in the file header): additive only, within a protocol version. New optional fields with
defaults, or new types. Never rename or remove. Commands are strict: an unknown field is rejected. Clients decode
events with `strict=False` so a newer server does not break them.

Recovery fields (D-060): `hello` returns `instance`, and over HTTP also `heartbeat` (that server's ping interval,
with `heartbeat` in `capabilities`); stdio and in-process callers get no pings and no `heartbeat` (#226).
`approvals.list` returns a `cursor`
(`instance`, `seq`) taken with the list. A client replaces its pending approvals from the list, applies events above
`cursor.seq`, and takes a new snapshot when `instance` changes. Never resend a turn or an approval answer whose
reply was lost; reconcile from the snapshot instead. Principal clients on the Unix socket go through
`exo.protocol.client.Client(principal=...)`, which checks the peer with the kernel first (`docs/GATE.md`); a refusal
is `exo.peerauth.PrincipalRefused`, a `ConnectError` whose message names the reason. `GET /events` frames are raw
UTF-8 JSON (U+0085, U+2028, U+2029 and lone surrogates escaped); the Python client splits lines at CRLF, LF or CR
(`exo.peerauth.bounded_lines`), holds at most 16 Mi characters of one, skips a frame over that and resumes after its seq, with or without `reconnect`, so a caller that keeps its own `since` gets the next event and
never sees the skipped one.

Who sent a command is stamped by the transport, not read from the body (D-039, `docs/GATE.md` "Who answered").
A client of the principal that answers approvals passes a label: `Client(socket=..., principal="tui")` sends the
core's per-launch credential; an in-process `Dispatcher(engine, caller="principal:<label>")` is the principal by
construction. Everything else is `local` and can answer an approval only as a signed seat. Tests that answer
approvals through a `Dispatcher` pass `caller` (for example `d.run(cmd, "principal:tui")`).

After any change to the schema, regenerate the exports and commit them:

    python -m exo.protocol.export            # rewrites src/exo/protocol/generated/
    python -m exo.protocol.export --check    # exits 1 if the files are stale

### Extend through `command.run` built-ins

`command.run` takes `name`, `args` and `session`. The core answers a few names itself, in
`builtin_command` in `src/exo/diagnostics.py`: `context`, `compact`, `trace`, `findings`,
`handoff_facts`, `harness_error`, `memory_status`, `providers` (status, or `reload [name]`, D-034), `subagent` (list, open, steer and stop a running child; the rules live in
`src/exo/subagents.py`, outside the counted core, D-070), `unsteer` (pull a queued steer message back; args is its id, empty means the
newest; publishes `steer.cancelled`, D-045) and `rename` (set the session title; principal only; publishes
`session.updated`, D-069). `builtin_command` returns `MISSING` for a name it does not know, so a plugin command
gets its turn. Anything else goes to a command a plugin registered. To add one:

1. **A core command.** Add a branch in `builtin_command` (`src/exo/diagnostics.py`). If it is more than a few lines,
   put the logic in a module outside the core, as `subagents.command` does, and call it from the branch.
   - Check who is asking with `is_principal(current_caller())` (`src/exo/principal.py`). For an action only the
     principal may take, raise `CommandFailed("forbidden", ...)` (D-052).
   - Raise `CommandFailed("bad_request", ...)` for missing or bad arguments.
   - Announce a state change with an event that already exists, for example `session.updated` as `rename` does. Do
     not add an event type.
   - Return a string, a dict or a list. A client gets it as `{"output": ...}`.
   - List it in `_commands_list` (`src/exo/core/engine.py`) only if a person should see it in menus. Today that is
     `context`, `compact`, `trace` and `findings`. Commands for clients to call (`rename`, `subagent`) stay off it.
2. **A plugin command.** Call `ctx.register_command(name, handler, description=..., args_hint=...)` (section 4).
   The handler gets the raw argument string.
3. **A TUI command.** Add a row to the `COMMANDS` table in `clients/tui/exo_tui/app.py`, and a branch in
   `ExoApp.slash` if the client does the work. `/help` is built from the table. A test checks that every command in
   `/help` appears in `skills/exo-intro/SKILL.md`, so add its line to the skill too.
4. **Tests and docs.** `tests/core/test_rename.py` is a good model: it covers the principal path, the `forbidden`
   path, bad arguments, the published event and menu completion. Then add a row to `docs/COMMANDS.md` (and to the user guide's
   command table if a person types it).

Worked examples: `rename` (D-069) is a short branch with a principal check and an existing event. `subagent` (D-070)
is a thin branch that calls `src/exo/subagents.py`, which holds the rules and adds no protocol command or event.

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
`message_send`. A tool the classifier does not know and the registry calls high risk is an ask, never an allow,
also when the gate reads a known word in its camelCase name (`mcp__fs__writeFile`): the unsplit name is still read
as `unknown` beside it, and any reading counts.
If the tool sends something outward, its category must be one the gate treats as outward, so approvals bind to the
exact text (`docs/GATE.md`).

**Keep secrets out.** The registry removes known key values from every tool result before it reaches the model or a
client. A tool that runs a subprocess must use the filtered environment (`docs/TOOLS.md`, "Environment of bash and
jobs"). A tool that needs a secret reads it from the keys file or the environment, never from its arguments, and
never puts it in a command line.

**Document and test it.** Add a row to `docs/TOOLS.md`. Add a test in `tests/core/tools/`. If it is a read, add it to
the gate tests. Decide whether a subagent may use it: the blocked list is `BLOCKED_TOOLS` in
`src/exo/core/agents/delegate.py`.

The registered `grep` and `glob` handlers use `exo.search_worker.run_search`, which owns a child process and
enforces timeout and cancellation after the gate and executor. Their synchronous functions are worker
implementation details. Call tools through the registry so those bounds and the executor policy apply.

A plugin registers a tool with `ctx.register_tool(name, toolset, schema, handler, risk=...)`.

Exo OS can require an installed execution broker after approvals and validation.
New tools need an explicit broker decision before their handler may run;
unsupported operations fail closed. The immutable request, installed entry point,
trusted plugin inventory and host-code limits are in `docs/EXECUTORS.md`.

## 4. Add a plugin

A plugin is a folder with `plugin.yaml`, an `__init__.py` that has `register(ctx)`, and a `tests/` folder.
The manifest needs a `name`. Other keys in use: `version`, `description`, `kind`, `hooks`, `config_schema` (with a
default for each key), `tags`. Look at `plugins/exo-command/plugin.yaml` for a full one.

Plugin folders are found in `$EXO_PLUGIN_DIRS`, then `$EXO_HOME/plugins`; installed `exo.plugins` entry points
also load. To use the repo's plugins during development, set `EXO_PLUGIN_DIRS` to its `plugins/` folder.
A tool call that writes into `$EXO_HOME/plugins`, or into any of the other folders Exo loads code from (the venv,
Hermes' plugin home, the source copy and every folder `$EXO_PLUGIN_DIRS` names; #459, `docs/GATE.md` "The store is
credentials"), asks the principal every time, so do not copy a plugin there to try it and use a different venv than
`${EXO_VENV:-$EXO_HOME/venv}` for private experiments; loose `EXO_PLUGIN_DIRS` while you edit instead.
A seat can limit the list with `[plugins] enabled`. A failed `register` is rolled back, so a half-loaded plugin
leaves nothing behind.

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

**Hooks.** `register_hook` accepts only the six names in `VALID_HOOKS` in `src/exo/core/hooks.py`, which the
core currently fires: `pre_tool_call`, `post_approval_response`, `post_tool_call`, `transform_tool_result`,
`on_turn_start`, `on_turn_end`, `delegated_approval_validate` and `delegated_approval_audit`. Callbacks take keyword arguments only. The tool hooks (`pre_tool_call`,
`post_tool_call`, `transform_tool_result`) get `tool_name`, `args`, `session_id`, `task_id`, `tool_call_id`,
`turn_id`, `author` (who started the turn, D-040: `principal` or `principal:<label>`, `local`, `peer:<name>`,
`bus:<seat>`, `job`, `schedule`, `delegate`, `system`; empty from an older caller), `risk` and `cwd`, the
session's working folder from which relative paths in `args` resolve (the gate reads them there, #209). Each
callback gets only the keywords it declares, or all of them when it takes `**kwargs`, so a key added later does
not break an older callback. A failing callback is logged and
skipped (fail open), except that a failing `pre_tool_call` callback blocks tool execution. The core also refuses
tools when no `pre_tool_call` callback is registered: reinstall the plugins or enable `exo-gate`, then restart.
To veto a call, `pre_tool_call` returns `{"action": "block", "message": ...}`. To ask the principal, it returns
`{"action": "approve", ...}` (see `plugins/exo-gate/exo_gate/hook.py`).

`KNOWN_UNFIRED_HOOKS` names hooks present in Hermes contracts or earlier Exo declarations that Exo does not
fire: `pre_llm_call`, `post_llm_call`, `transform_llm_output`, `on_session_start`, `on_session_end`,
`on_session_finalize`, `on_session_reset`, `on_compaction`, `pre_approval_request`, `pre_command`,
`subagent_start`, `subagent_stop`, `on_skill_lifecycle`, `pre_auxiliary_call`, `post_auxiliary_call`,
`pre_gateway_dispatch`, `kanban_task_completed`, and `kanban_task_blocked`. Default registration raises
`HookError` instead of accepting a callback that would never run. A custom `HookRegistry(valid=...)` can define
a separate fire surface for an isolated consumer; it does not add hooks to the Exo core. Hermes contract tests
continue to check Hermes's own hook metadata.

Required-executor engines propagate failures of `exo-gate` in `pre_tool_call`
and `post_approval_response`, blocking dispatch. The gate requires saved signed
decision and approval events; ordinary optional hooks keep the behavior above.
See `docs/GATE.md` "Required executor audit".
The gate's approval verdict adds an `approval_key` for one pending call; the core
passes it back to `post_approval_response`. Keep `rule_key` stable for grant
scope and do not substitute it for this correlation token.

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
`harness.error` and become findings. `trace.add_sink(fn)` subscribes to those reports. A bound method is held weakly
and does not keep its owner alive, so keep the owner referenced for as long as it should receive reports (the engine
registers its own and removes it in `close()`, before the store closes). Sinks are copy-on-write and no sink runs
under a lock, so a sink or a finalizer may call `report`, `add_sink` or `remove_sink`. A report already delivering
may still call a sink removed after it began.

**Context engine hooks.** `select_context(messages, session_id=)` gets each message with `author` and `principal`,
like the memory hooks. After `session.undo` the core calls `on_session_switch(session, rewound=True, messages=,
summaries=)` with every message left in the thread and `{tree key: [ids of the summary nodes still stored]}`. Drop
anything kept from the undone turns (D-041), including the text of each `on_compacted` node that is gone (its
`node.session` is the tree key). exo-context drops their pins and those nodes, then writes again each handoff
that may quote them. Hermes's CLI and TUI tell only their memory provider about `/undo`, so exo-memory passes the
notice on with `exo.host_undo.report(session)`. exo-context drops the last undone turn's pins in
`on_host_undo(session)`, records the undo with `exo.host_undo.pend(session, window)`, and those of the earlier
turns of `/undo N` go in the next `select_context` of that session, from its `conversation_messages`, by
`exo.host_undo.take(session)`: the record waits under the session, not under one engine, so the turn resolves it
in whichever agent the host built then (#389, #580). A compression move carries the record into the child with
`exo.host_undo.move(old, new)`. A memory provider used beside exo-context in exo-memory's place can do the same. A
listener calls `exo.host_undo.turn(session)` on each turn it sees, and acts on a notice only when its last number is
`exo.host_undo.latest(session)`, so an old engine left behind by an agent rebuild stays out of it. A host with no
agent left reports `exo.host_undo.report(session)` itself, which reaches the engines still registered for the
session (#580).

**Who wrote it (D-040).** Every message dict a hook receives has `author` (`principal`, `agent`, `tool`,
`peer:<name>`, `bus:<seat>`, `job`, `delegate`, `schedule`, `system`) and user messages have `principal`. Treat
text as the principal's only when `principal` is true; `exo.provenance.principal_view(messages)` turns every other
user message into role `other`, which the capture extractors skip. A new way for text to reach a session must stamp
`meta.author` itself (pass it as `meta` to `loop.send`, or set it on the stored `Message`), never derive it from
the text. A new ledger writer that records the principal's words writes them under a principal id; anything the
agent decides on its own is written under the agent's name. New code that prints a stored message's text where the
model reads it (a view, a note, a summarizer's source) uses `exo.core.context.render_message`, or passes text whose
author is not `principal` through `exo.provenance.defuse_pins`, so it cannot carry a live `<exo-pinned>` block (#279).
A summarizer's output is model text, never the principal's: pass it through `defuse_pins` where it is made, before
anything adds a real block to it.

**State files (#273).** Write a file that replaces an earlier one with `exo.core.private.atomic_write(path, data)`.
It writes a temporary file that `mkstemp` makes in the same folder, fsyncs it and renames it over the path. The mode
is 0600 unless you pass `mode` (`None` gives what a plain open gives). A file written many times a minute, such as
a cursor or a cache, can pass `sync=False` to skip the fsync. Do not write `<file>.tmp` and rename it, and do not
open the final name for writing: a symlink planted at either name turns the write into a write to another file.
To change the process umask for a while, use `with exo.core.private.umask(mask):`, not `os.umask`. It holds the
lock the `mode=None` probe takes, so the two never undo each other (#317). Do not await or yield inside the block.
The lock is not reentrant, so a second block on the same thread would wait forever (#563). To make a socket under
a mask, bind it inside the block and start the server on it after the block, as `HttpServer.start_unix` does.

## 5. Add a seat

A seat is a TOML file in `$EXO_HOME/seats/<name>.toml`, or `<name>/seat.toml` with a `SOUL.md`. The full format is
`docs/SEATS.md`. The tables:

| Table | Purpose |
|---|---|
| `[seat]`, `[principal]` | Name, role, soul file, and the principal id the seat serves. |
| `[models]` | `tier` (fast, standard, deep) and per-role router overrides. |
| `[tools]` | `toolsets` to allow, and `env_allow`: variables the scrub would drop (secrets, shell startup hooks) that bash and jobs may keep (git's `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` only as a family, through `GIT_CONFIG_COUNT`). Default none; `docs/TOOLS.md` "Environment of bash and jobs" has the startup-code caveat. |
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

One test is opt-in because it spends money and needs a logged-in CLI: `tests/core/models/test_claude_cli_real.py`
(marker `real_claude`) runs one tiny real turn through the claude-cli provider, to catch a Claude CLI release that
changes the stream-json envelope. It is skipped unless `EXO_TEST_REAL_CLAUDE=1` is set and `claude` is on PATH. Default
runs and CI never run it.

If you changed the protocol schema, also run `python -m exo.protocol.export --check`.

Rules that tests enforce: the protocol caps, the core line budget (a reported count over a floor, no ceiling; explained below), the
TUI colour tokens against the site's
`style.css`, that every `/help` command appears in the intro skill, and the docs map (`tests/docs/test_docs_map.py`).
Tests use fake models and no network. The conftest fixtures isolate `EXO_HOME`, the bus identity and the peers
folder, so a test never reads your real setup. The root fixture also clears inherited `NO_COLOR` so terminal
settings cannot change the expected TUI theme. Tests that need no-colour mode set `NO_COLOR` themselves.

**The core line budget (D-077, D-083).** `test_core_line_budget` (`tests/integration/test_wave1_acceptance.py`)
counts the lines in `src/exo/core`, `src/exo/protocol`, `src/exo/server`, `src/exo/cli_chat.py` and
`src/exo/upstream.py`, generated files excluded. The ceiling is off: the test records `CORE_FLOOR = 14,813`, prints
the count and its distance from the floor, and asserts nothing about lines. Run it with `-s` or `-rP` to see the
count and the largest counted files. It does assert the two step 4 structure caps. The rules are in
`docs/ENGINE-SPLIT.md` "Budget policy":

- Fixes and features land on their own merits. No headroom note, no waiting for a reduction.
- A PR that makes a real reduction (deletions, simplification, a real seam from the engine split) sets `CORE_FLOOR` to
  its post-merge count in the same PR. The floor never goes up.
- A count that climbs far past the floor is a reason to schedule a reduction, never a reason to refuse work.
- The structure caps are the gate: `INWARD_IMPORTS_MAX` and `PRIVATE_STATE_MAX` only go down. A PR that adds one has
  to remove another first.
- Never edit the `CORE` list to dodge the count. The goal is structural: no private-state reads across module
  boundaries, no product imports into the engine, one responsibility per module. The count is a signal for it.

Check your prose before you commit docs:

    python3 skills/clean-writing/scripts/check-prose.py docs/YOUR-FILE.md

### Checking a gate change against main

Gate-related pull requests also run `gate-preflight.yml`: the gate plugin suite and the base revision's
comparator against the composed PR tree, with the JSON differential retained as an artifact. A nonzero
comparator result fails that job. Hosted execution and branch-protection enforcement remain separate:
maintainers must make `never-looser` a required check to enforce it at merge. Reviewers still inspect docs and
explain every intentional lowering; this workflow does not waive the docs rule.

A change to `src/exo/gate.py` must not decide anything lower than main does, unless the change says so (D-038,
amendment of 2026-10-22). `scripts/gate_diff.py` checks this (#423). Make a worktree of main next to your branch and
run main's copy of the script on both trees:

    git worktree add ../exo-main origin/main
    .venv/bin/python ../exo-main/scripts/gate_diff.py --base ../exo-main --head . --json /tmp/gate-diff.json

Run main's copy, not the branch's. The script holds the seeds, the generated forms and the policies, so a branch
that edits it would check itself with its own copy. A reviewer of a gate PR runs main's copy against the PR head and
reads any change the PR makes to `scripts/gate_diff.py`.

The script compares two trees, not a diff. If main moved after your branch began, merge main into the branch
first. Otherwise each line a later main change made stricter shows up as lowered.

The script runs `gate.classify` and `gate.decide` of each tree in a process of its own, with the same HOME and no
`EXO_*` variables. That HOME is a temp folder under /var/tmp, outside /tmp, so a line that names /tmp does not touch the
store. It holds a key file, a grants file in the store, and two plain names that are links to them
(`~/notes-link.txt` and `~/grants-link.json`), so a head that stops following links shows. `--home` sets another
HOME. The script never imports either gate itself.

The actions are the lines and tool calls the gate suites test, read from the test files of both trees, so a line still
runs when the head deletes its test. Each call runs as written: a `bash` call stays a `bash` call, and a line under
`cmd` stays there. The script reads the gate's own tables from both trees the same way, with `ast`: a literal at
module level, and each `|=`, `+=` or `NAME[key] =` of a literal after it. So a head that drops a table entry still
runs the base's line for it, and a head that adds one runs its own. A table a tree holds in any other form (built by a
helper call, changed with `.update()` or inside a function, or, where the two trees hold different kinds of value, one
the script does not read for that table, such as a string for a set of names or a list for a mapping) is named on
stderr and in the summary, since its entries may get no lines. To these come generated forms:

- a line for each entry of the program, prefix and shell tables: each program of `PROGRAMS` with a file, each
  `SUBCOMMANDS` pair, `rm -rf build` behind each prefix (`sudo`, `nohup`, `doas`), wrapper and tmux command, run by
  each shell with `-c` and from its input, and code that runs it given to each interpreter with each of its flags.
  Also a line for each program and subcommand of the allowlists, and `python -m` with each module of
  `_SAFE_PY_MODULES` and each pip subcommand.
- a line for each value option: `doas -u x rm -rf build`, `flock --wait x 1 rm -rf build`, a tmux command's
  options, an interpreter's (`python -W x -c ...`) and a run wrapper's (`uv run --with x rm -rf build`). Long options
  of a prefix or wrapper run as `--opt=x` too. Also each option whose value is a command line (`git rebase --exec`,
  `tar --to-command`) or a script file, `touch` behind each privileged wrapper (`chroot`), a write by each
  redirection operator, a POST to each local host, a read in each store folder, and a read of each secret sample
  path and of a glob that matches it.
- a string for each alternative of a few patterns, written by `regex_samples`: a read of a path for each
  alternative of the secret path pattern (`x.pem`, `.gnupg`), a SQL line for each destructive statement kind (`drop
  database`), and a patch header for each kind (`*** Add File:`) that names the store or a secret.
- compound forms of each simple line: in a list or a pipe, in `$( )`, after `bash -c`, `sudo`, `env`, `timeout` or
  `ssh`, fed to `bash` by a heredoc or a here-string, and after a `cd` into the store or `/dev`
- each line under the other tool names and keys the gate reads as a shell line: `shell`, `bash`, `run_command` and
  `exec_command` with the line under `command` or `cmd`, and a name the tool table does not hold that ends in
  `terminal` or `shell` (`powershell`, `remote_shell`) with the line under `command`. Every seed line runs under each
  of them, every other line under one. Each seed also runs under each tool table name that ends in `terminal` or
  `shell` (`read_terminal`), so a head that adds such a name to the table shows, and in a call that holds both keys,
  with `true` or an empty string under the other one, since the gate reads `command` first and `cmd` when
  `command` is empty. A name read by its suffix also gets each seed under `cmd` beside an empty `command`.
- folder rows: calls that read a secret store only as resolved from a folder (#209), such as `cat keys` in `~/.exo`,
  `ls` in `~/.exo/keys`, `cat settings.json` in `~/.claude`, and file tool calls like them. Each line runs under
  every shell form, once with the folder as the session folder and once as the call's own `cwd`. Main decides each
  row otherwise without its folder, and a test checks that, so a head that stops reading the folder shows.
- posts that trip each posting rule (each also twice to one PR behind a delete, so main lists its texts twice): a
  home path, an internal id and a model id for the default scrub, and each forbid entry, forbidden word, agent name
  and scrub pattern of the policies the run uses. Each runs as a `gh` comment and title, under each text and title
  key of the first outbound tool, and under the first text key of the others.
- calls of the other tools: each tool of the tool table and each tool name the classifier names, with no arguments,
  and a tool name for each name hint word (`mcp_acme_trash_item`). A store path and a secret path under each path key
  (`path`, `file_path`, `destination` and the rest), and for an outbound tool, a post with a forbidden word or a
  `pub/` target under each text, title and target key. Payment and account calls with a plain target and amounts
  below and above the budget policies' limits. For a tool whose branch compares an argument (the `action` of
  `cronjob_manage` and `send_message`), each value it names and others (`remove`, `reply`, `xyz`).

That is about 52,000 actions. The policies are built into the script (more than 80). A test checks that every policy
key `check_post`, `decide`, `normalize_policies`, `normalize_scrub` and `check_scrub` read appears in a built-in
policy, `footer_exposures`, `ignore_case` and the scrub's `patterns` among them. A key that appears in a policy is not
yet a key whose loss changes a decision, so two more tests take things out of main's gate in turn. Each table entry
is removed alone, and main must decide one of its lines or calls otherwise. Each step of `decide` that raises an
outcome (the budget, the escalation, the posting rules, the floors, the deny entries) is removed alone, and a built-in
pair must come out lower. A step that every built-in policy masks with another ask fails that test. Some entries
main never reads on their own, such as an opaque program (an unknown program asks too) or a long option that is a
prefix of a longer one. The test lists each with the reason. A full run takes three to four minutes on four cores.
`--jobs` sets the workers in each tree (half the cores by default).

Add actions with `--corpus lines.txt`: one command per line, or a JSON list of commands and `{"tool", "args"}`
actions. An action may also hold `"cwd"`, the session folder the hook passes to `classify` (#209). An item in another
shape, such as a call with no `args` mapping, stops the run with exit status 2 and names the item. The `--corpus`
lines get the same generated forms as the harvested ones.

- `--only-corpus` runs the `--corpus` items, the compound forms of their simple lines, and each line under one other
  shell tool name and key. It skips the harvest, the seeds, the lines and calls of the tables, the posts, the folder
  rows and the calls of the other tools.
- `--no-variants` runs only the `--corpus` items, the harvest and the six budget lines (#551, below). It skips the
  seeds, the lines and calls of the tables, the posts, the compound forms, the other shell tool names and keys, the
  folder rows and the calls of the other tools.

The budget lines (`BUDGET_LINES`, #551) are long lines no test line reaches: 70 `cd`s into store folders (past the 64
`_exo_relative` resolves), a store folder 62 deep with 1,600 names, the stress tests' `_NEAR_CAP` line, two `<( )`
of 400 names each (each under `_EXO_PARTS` alone, past it together), and four brace words of 1,000 forms and a fifth
for `cat` and `touch` (past `_BRACE_BUDGET`). Each asks on main only because its budget is spent, so a head that
fails open past a budget is lowered on it. A test checks that main reaches each budget and allows the line once the
budget is lifted; when a budget moves, resize its line.

Neither reads the gate's tables for lines, so neither names a table it cannot read.

Add policies with `--policies more.py` (a `POLICIES` mapping). Their forbid and scrub entries get posts too.

Exit status 0 means nothing is lower. Exit 1 means at least one pair of action and policy is lower: an outcome below
main's in the order allow, log, ask, deny, a reserved hit lost, or a posting violation main lists that the head does
not (one the head words otherwise counts too). A line of several posts lists a violation after the name of its post
(`<post>: <text>`). Either tree may do so, and the post's name may change with its target, so the texts are compared
without the name. A violation the head keeps under its post's name, or under a new one, is kept. The summary counts
those pairs as reworded, shows a few with the texts each tree lists that the other does not, and they do not exit 1.
A reworded pair is not also counted as another detail changed. The line `and N more on other posts`, which stands
for the violations a long list leaves out, names no text, so it is not compared as one. Its N counts in each tree's
total, and a head whose total is lower than main's is lower, so a post skipped past the cap still shows. The texts
behind that line are compared by count only, so a head that lists more and hides another text there in place of
one main hid shows nothing. A text the head lists fewer times than main does is lower: one post's violation lost
while another post still has it, or one of two posts of one name skipped. At an equal deny, lost reserved hits and
violations are not counted. The call is blocked, and both only matter for an ask: a reserved hit keeps a standing
grant from covering it, and a violation is named in its message. A classify or decide that raises counts as the
hook's answer to an error: ask, a read tool too (D-084).
At the same outcome below deny a pair is also lower when the head loses one of the hook's checks after `decide`
that depend only on the call and the decision (#551), each run with the tree's own gate: `no_grant` (the once rule),
`body_unseen` (a post body the gate did not see), `binds_exact` (an approval bound to the exact call) and
`reserved_elsewhere` (a reserved hit on another part than the line's own category and target, which keeps a standing
grant from covering the call; the hook's #471 branch that also checks the line read as before, `c.before`, is not
modelled). These are the script's `FLAGS`. Each is compared on its own, at every outcome below deny, so a head that
drops one while another that also refuses grants stays still counts as lower: strict, and on the cautious side. The
summary counts each one lost. A check a tree's gate does not have is not held; one
that raises sends the hook to its error card, so the pair counts as that error. The card is once-only, so a head
pair that is one loses none of these checks and no reserved hit. The script marks such a pair with `error_card` in
its flags, which no gate decision can carry, so a decision whose rule is `"error"` is compared like any other. An
outcome other than allow, log, ask or deny (`"Ask"`, `"block"`) is one the hook runs (when the decision model gives
a verdict, `tighten_only` raises on it and the hook gives its error card): it ranks as allow in the head and as ask in the base,
and the summary counts such pairs on each side.
The summary groups the lowered pairs by transition, such as `store_write ask -> read allow`, and by policy.
Tightenings are only counted. `--json` writes the lowered actions and pairs and the reworded pairs, the first 5,000
of each in corpus order. The `--corpus` lines come first in that order, so the cap keeps them before the built-in
lines. Fix each lowered line, or name it in the decision amendment the change records, with the reason it is safe.

Exit 2 means a tree could not run or the script failed, such as on a folder that does not exist or a policy file
that raises. A tree that dies or runs past `--timeout` (an hour by default) is stopped with exit 2 too. So is a run
stopped by SIGTERM or SIGHUP, such as a CI cancel. The script stops both trees and removes the temp folder first.
A signal the script starts with ignored stays ignored, so a run under `nohup` goes on when the terminal closes.
Exit 2 also follows a run with nothing lower when a tree holds a gate table in a form the script does not read. The
summary lists each such table with the reason, as `INCOMPLETE`. Keep the table a literal, or read the change and
name the table in `--accept-unread TOOLS,...` to accept the missing lines. A lowered pair still exits 1.

**What the check leaves out.** It runs `classify`, `decide` and the hook's `FLAGS` checks above, nothing else of the
hook, so a change to any of these needs its own tests and a line in its decision amendment:

- Session folders. The hook passes `classify` the session's folder (#209). Only the corpus's own `"cwd"` values and
  the folder rows run here. The rows cover what main reads from a folder today: a relative name that lands in a
  secret root (`~/.exo/keys`, `~/.claude`) or on the run folder. The harvest keeps a literal folder a test passes to `classify`,
  not one the test builds as it runs, such as `cwd=str(tmp_path)`.
- Names and keys outside those tables. A tool name, argument key or program the script does not read from one of the
  tables above runs only where a test or a seed names it: the `urls` and `code` keys, and programs such as `find
  -exec`, `xargs` or the `git` and `gh` subcommands.
- Patterns and helper sets. Only the secret path, SQL and patch header patterns and the scrub patterns get a string
  for each alternative. Any other alternative of a regex or a helper (the URL and host patterns, `_GH_URL`, the
  option tables of `_VALUE_OPTS`) is seen only where a test or a seed names it.
- Files and links in HOME. The HOME holds one key file, one grants file and the two links to them. Other file
  contents, links and folders a real HOME has do not run.
- Stores the environment moves: `EXO_HOME`, `EXO_PRINCIPAL_STORE`, `EXO_KEYS_FILE` and `EXO_BUS_*` (and their
  `DIVAN_*` names). Both trees run without them.
- The hook steps after `decide` other than the `FLAGS`: which standing grants exist and match, the body files the hook
  reads into a post, who may answer, the registry risk floor, the required executor, observe and bypass, a policy
  file changed mid-session, errors past `decide`, and the decision model.

### CI, lint, lockfile and the privacy deny-list (D-042)

**CI.** `.github/workflows/ci.yml` runs on every push to main and every pull request. It runs the root suite and
then each plugin suite on its own (from the plugin folder, because each has its own conftest), on macOS and Ubuntu,
with Python 3.11 and 3.13. It also runs `pytest plugins` once, to prove the plugin tests collect together. Plugin
test files need unique names (`test_<plugin>_...py`) because the plugin test folders are not packages.
A newer push to a pull request cancels the pull request's older `ci` and `gate preflight` runs (a `concurrency`
group per pull request, #907); each push to main keeps its own run.
The gate-diff process-tree tests, including the nohup test, require `/proc/self/cmdline` and skip without it.
The cleanup signal-handler test does not need `/proc` and still runs where SIGHUP is available.

**Lint.** `uv run --frozen --all-extras ruff check .` checks the pyflakes rules (`F`, set in `pyproject.toml`).
Findings fail CI. Keep intentional public re-exports explicit (`from module import name as name`), and preserve
calls with side effects when removing unused bindings.

**Types.** CI checks `src/exo` and the packaged terminal client `clients/tui/exo_tui` with mypy 2.4.0,
Python 3.11 and the Linux platform. Both are explicit targets so mypy checks the same source whether the
editable installation points at this checkout or another one. It follows local imports using `src`,
`clients/tui` and `plugins/exo-command`. Diagnostics from those imports also count. This is the standard
mypy check, not strict mode or a check of every unannotated function body. Run the same check locally:

    uv sync --frozen --all-extras --python 3.11
    uv run --frozen --all-extras python scripts/check_types.py

The reviewed `.github/mypy-baseline.json` records existing errors by relative path, message, code and occurrence
count. Source line shifts do not matter. Another occurrence of an existing error does. New errors fail the check
and must be fixed. Resolved errors also fail until their stale entries are removed. After fixing errors, run:

    uv run --frozen --all-extras python scripts/check_types.py --update

Review and commit the smaller baseline. `--update` refuses new errors and never expands it. CI never writes it.
When a local name changes meaning, use a fresh name rather than widening its type. Read-only
environment parameters accept `Mapping[str, str]`; bind the selected environment to a new local
instead of assigning `os.environ` back into an optional dictionary parameter (#710).
`--initialize` creates a first baseline only when the file is absent, for a separately reviewed migration of the
compiler or check scope. The helper refuses missing dependencies, mismatched settings and malformed or fatal
compiler output. It uses no disk cache. `--root PATH --baseline PATH` can check another checkout without changing
that checkout. No global or per-module type-error suppressions are applied.

**Lockfile.** `uv.lock` pins every dependency, extras included. CI installs with `uv sync --frozen --all-extras`.
After you change `dependencies` or an extra in `pyproject.toml`, run `uv lock` and commit the result. Declare every
package the code imports directly, even when another dependency would bring it in. The terminal client in
`clients/tui` does not depend on `exo` by name (that name is another project on PyPI): install the repo root first.

**Privacy deny-list.** Rule D-004 says nothing in the package names the principal, so the names to refuse are not in
the repo. Put one word or phrase per line (a `#` starts a comment) in `$EXO_PRIVACY_DENYLIST`, or in
`~/.config/exo/privacy-denylist.txt`. Tests that need it (`exo.privacy.deny_regex()`) skip when the file is absent.
The generic checks always run: no `/Users/<name>` path and no email address outside a short allowlist
(`tests/test_privacy.py`, `exo.privacy`). The list is read when the test module is collected, because the repo
conftest points HOME and `EXO_*` at a temp folder for each test. Never paste the real names into a commit message,
an issue or a test.

**Private notes live outside the repo.** The early inventory and the project brief are in the handoffs folder of the
person who runs the project, not in `inventory/` or `docs/BRIEF.md`. The handoff folder defaults to
`$EXO_HOME/handoffs`. Set `handoff_dir` in `config.json` (or `EXO_HANDOFF_DIR`) to put it elsewhere; the canary seat
sets it to `~/Work/handoffs`.

**Deploying the site.** `scripts/deploy-site.sh` reads the Vercel team id and slug from `EXO_VERCEL_TEAM` and
`EXO_VERCEL_SCOPE`, and the token file from `EXO_DEPLOY_ENV`. Put them in the environment or in an untracked
`~/.config/exo/deploy.conf`. It deploys from a copy outside git, made with `rsync`. A failed copy or deploy stops it
with a non-zero exit before anything is tagged. Any deploy, versioned or not, refuses when `site/` holds untracked
files (ignored ones too, `.DS_Store` aside), so a private draft folder such as `site/pitch/` can never ship; commit it
or move it out. It also refuses when `site/` contains a submodule (its working tree can hold files git does not list)
and when git cannot list the files or read the status of `site/` (a corrupt index), rather than treating that as an empty
list or a clean tree. It refuses a `site/` that is itself a symlink, untracked or tracked with mode 120000, because git does
not see through it while `rsync` follows it; symlinks inside `site/` are copied as links. The Vercel CLI is pinned to an exact version set at the top of the script. `EXO_VERCEL_CLI` may name
another exact `X.Y.Z` (a tag such as `latest`, or an empty value, is refused with exit 2). To move the pin, change the script in a PR.

Staging first (D-047). With no flag the script deploys to `staging.exo.askaconsult.com` (Vercel project
`exo-site-staging`), which sends `X-Robots-Tag: noindex, nofollow` and serves a `robots.txt` that disallows everything.
Check the change there, then promote with `scripts/deploy-site.sh --prod`, which names the target, asks you to type
`production` when run interactively, and deploys to `exo.askaconsult.com` (project `exo-site`). `--yes` skips the
prompt for scripted runs, `--redeploy` skips the project, domain and DNS setup. Both targets deploy from a git-free copy.

Keep site claims tied to the revision that was checked. `docs/SITE-STATUS.md` records the roadmap status rules and
current evidence snapshot. Unmerged work is in progress even when tested locally. D-055 sets the delivery order:
core verification, browser workflow polish, then Electron desktop packaging.

**Before the repo goes public.** Git history still holds the files removed in D-042. Rewrite history first (for
example with `git filter-repo`), then publish.

## 8. Decisions and the merge flow

**`DECISIONS.md`.** Each significant choice gets a heading `## D-NNN (date) title`, in number order, newest last.
Fields: Decision, Why, Rejected, Revisit if. Add the entry in the same commit as the change. A number that is reserved but not written (D-027 today) has no
heading, and the map says so.

**Repository routing (D-089).** Shared code has one implementation PR in Exo. Native shell and packaging work belongs in Exo Desktop. The fork receives batches of merged Exo commits through reviewed sync PRs. See [Repository workflow](REPOSITORY-WORKFLOW.md) for existing pairs, alpha carries and verification.

**Branch and merge.**

1. One branch and one pull request per change. A trivial doc or script sync may share one.
2. Run the root suite and the plugin suites you touched. Green before you push.
3. Commit message: what changed and why, plus the docs and user-impact line from the docs rule.
4. Push the branch. Never push to `main` directly.
5. Open a pull request. The body says `Fixes #N` when it closes an issue. Keep every word fit for a public
   repository: no internal names, no paths from your machine, no keys.
6. A reviewer reads the diff, runs the suite, and merges. Merge your own pull request only where you hold merge
   rights.

**Changelog merges (#842).** `.gitattributes` gives `CHANGELOG.md` git's union merge driver. Two branches that each
add a bullet in the same place merge without a conflict: both bullets stay, the checked-out branch's first.
`DECISIONS.md` and `docs/DOCS-MAP.md` keep the normal driver, so two branches that take the same `D-NNN` or edit the
same map row still conflict.

- Git reads the attribute from the branch you have checked out. A branch cut before it landed still conflicts when
  it merges `main`. Run `echo 'CHANGELOG.md merge=union' >> "$(git rev-parse --git-common-dir)/info/attributes"`
  once in your clone, and every branch and worktree there gets it. (In a linked worktree `.git` is a file, so
  `.git/info/attributes` fails.) A `git rebase main` or a cherry-pick onto `main` needs no step: it replays onto a
  tree that already has the attribute.
- Union only decides the lines both sides changed: it keeps both versions, ours first, where git would stop with a
  conflict. A change that only one side made, a deletion included, applies as in any merge.
- Union does not keep an entry under its heading. Git takes two changes a few lines apart as one hunk. So when
  both sides add under `### Fixed` and under the `### Added` just below it, one side's fix can land under `### Added`,
  and the lines between the two changes appear twice. `tests/test_merge_attributes.py` fails when a version section
  holds a bullet twice or a `###` heading with nothing under it. Where each entry sits is yours to check: read the
  merged `[Unreleased]` section before you push.

Private data and secrets never go in the repo. The principal store is separate from this repository.

### Cutting a release

Names, branches and tags are in [`docs/VERSIONING.md`](VERSIONING.md). Four things are versioned on their own.

**Exo** (SemVer, tag `exo-vX.Y.Z`, with a codename per MAJOR: `exo 0.2.0 (egg)`).

1. Merge the work. Pick the next version: MINOR for a milestone, PATCH for fixes only.
2. Bump the number in `pyproject.toml` and the files that VERSIONING section 1 lists (`src/exo/__init__.py`,
   `clients/tui/pyproject.toml`, `plugins/*/plugin.yaml`, `plugins/exo-command/exo_command/__init__.py`, `uv.lock`).
3. In `CHANGELOG.md`, move `[Unreleased]` under a new `[X.Y.Z] (codename) - date` heading. The union driver
   (#842) gives no conflict when an open PR's entry sits where the new heading goes, so that entry lands in the
   released section. After a cut, each open PR that merges `main` checks which heading its entries are under.
4. Run the root suite and the plugin suites. `tests/test_versioning.py` fails if the version files or the changelog drift.
5. After the merge, sign the tag on main with `git tag -s exo-vX.Y.Z -m "one-line summary"` and push the tag.

**Signing releases (D-073).** Users who set `update.require_signed` (`docs/INSTALL.md`) accept only commits signed
by a key in `trust/allowed_signers`, in the format `ssh-keygen -Y verify` reads: `<principal> <key type> <public key>`,
one per line (add `namespaces="git"` after the principal to limit the key to git). Sign the commits that land on
main (`git config gpg.format ssh`, `git config user.signingkey ~/.ssh/<key>.pub`, `git config commit.gpgsign true`;
for merges made on GitHub, use a merge you sign locally, since GitHub's web signature is not an SSH key). The signed
tag of step 5 is for people who check it by hand. `exo update` checks the commit. To change keys, add the new key in
a commit signed by an old key, announce it, and have users run `exo update --pin-trust @{u}` after reviewing the
list. `--pin-trust` refuses a new list unless the commit that set it on main (the merge, if it came in through one)
carries a good signature from a key the user already pinned. Never remove the old key from the list in the same
commit that the old key must sign.

**The site** (`site-vMAJOR.MINOR`). MAJOR is for a redesign. MINOR goes up per production deploy.

1. Deploy to staging first. A staging candidate may carry `--version site-vX.Y-rc.N`.
2. Before production, archive the outgoing version: branch `archive/site-vX.Y` and a local tarball.
3. Run `scripts/deploy-site.sh --prod --version site-vX.Y`. It refuses if the tag exists, then tags the deployed commit.

**The whitepaper** (`whitepaper-vX.Y`).

1. Edit `docs/WHITEPAPER.md` and update the version and month in its header line, the line after the title, which
   reads `Version X.Y, Month YYYY. Aska Digital.`. The page takes both from that line only, and the build stops with
   a message if the line is missing or reworded.
2. Run `python3 scripts/build_whitepaper.py` to regenerate `site/whitepaper/index.html`. Never edit the HTML by hand:
   `tests/docs/test_build_whitepaper.py` fails when the committed page differs from a fresh build.
3. Add a line to `CHANGELOG.md`.
4. Tag `whitepaper-vX.Y` only when the paper is published, meaning deployed to production. A merged but unpublished
   draft has no tag.

**The house taste pack** (`house-vX.Y.Z`). The version is `"version"` in `src/exo/baseline/house-taste/MANIFEST.json`.
Export with `exo taste export-house --version X.Y.Z`, review and tick the pack, then tag `house-vX.Y.Z`.

### The local-first workflow

1. Build each change on its own local branch, one change per branch. Prefixes are in VERSIONING section 6 (`feat/`,
   `fix/`, `docs/`, `chore/`, and so on).
2. When several changes are ready, the principal tests them together on the canary seat, from a local integration
   branch named like `test/local-round-N`. Nothing goes to GitHub before that.
3. Then open one pull request per change. A reviewer reads it and merges to `main`.
4. Site changes deploy to staging first (D-047, D-055) and to production only with `--prod`.
5. Whitepaper and site tags come at publication, not at merge.

Unmerged work counts as in progress, even when it was tested locally (`docs/SITE-STATUS.md`). Do not describe it as
built in any doc.

## 9. Recipes

Short, working patterns for things people ask about. Each one names the file to read next.

### Open a panel from a tool

A panel is a `ui.panel` event (`docs/PANELS.md`). The model normally opens one with the built-in `ui` tool, so the
easiest way for your tool to cause a panel is to return the data and let the model call `ui` with it.

If your tool must open the panel itself, it has to be a core tool that holds a way to publish events. The `ui` tool
does it by taking a `publish(body, session)` function when it is registered
(`src/exo/core/tools/builtin/ui.py`, wired in `CoreEngine.__init__` in `src/exo/core/engine.py`). A tool handler's
`ToolContext` has no publish function, and a plugin tool has no handle on the hub today, so use the same shape:

```python
from exo.core.sessions import new_id
from exo.protocol import schema as s

def register(reg, publish):                      # publish(body, session) puts an event on the hub
    def handler(args, ctx):
        panel_id = new_id("p")
        publish(s.UiPanel(panel_id=panel_id, action="open", kind="table",
                          title="Open items", reason="the user asked for the list",
                          text="Three items are open: a, b, c.",           # required fallback for any client
                          data={"columns": ["item"], "rows": [["a"], ["b"], ["c"]]}), ctx.session)
        return f"Panel {panel_id} opened."
    reg.register(SPEC, handler)                  # SPEC is the ToolSpec, as in builtin/ui.py
```

Rules that matter:

- `text` is required and must say the same thing in plain words. A client that cannot draw the panel shows only that.
- `kind` is one of `markdown`, `diff`, `table`, `tasks`, `approval`, `taste`, `choice`, `file`, `chart`. The `data`
  shape for each is the table in `docs/PANELS.md`.
- To change or close a panel, publish again with the same `panel_id` and `action="update"` or `"close"`.
- `reason` is one line. It feeds the learning loop.
- What the user did comes back as the protocol command `ui.respond` (`opened`, `dismissed`, `acted`, `ignored`, with a
  `value`). The core records it as a `note` event of type `ui_signal` in the ledger
  (`handle_ui_respond` in `src/exo/learn/signals.py`).

Test it the way `tests/core/tools/test_ui_tool.py` does: register with a list-appending publish function, call the
tool, and assert on the `UiPanel` bodies.

### Call a decision model

A decision model returns probabilities, not prose (`docs/PROVIDERS.md`). The calls are in
`src/exo/core/models/decision.py` and are all async. They go through the router's `decision` role and fall back to
`aux`, then `main`.

```python
from exo.core.models.decision import ask_choice_result, tighten_only, DecisionError

res = await ask_choice_result(router, state, "How strictly should this call be gated?",
                              ["allow", "log", "ask", "deny"])
res.choice, res.probabilities, res.source, res.route, res.confidence
#   source is "decision" (a probability model) or "generative" (a chat model fallback, probability 1.0 on its pick)
```

- `state` is the context the question is about. It can be a string or JSON. `router` comes from the plugin context
  (`ctx.router`, read lazily as `exo-gate` does) or, in a script, from `exo.models_cmd.build_router()`.
- Wrap the call. It raises `DecisionError` when every route fails. `error.retryable` says whether trying later can
  help. Keep a safe default, as `exo-gate` does: on failure or after a timeout it keeps the rule outcome.
- **A decision model may only tighten a gate (D-015).** `tighten_only(current, proposed)` returns the stricter of two
  outcomes in the order `allow`, `log`, `ask`, `deny`. An unknown `proposed` value is ignored. An unknown `current`
  raises `ValueError`. Never let a model's answer loosen a rule. See `Gate._verdict` and `Gate._tighten` in
  `plugins/exo-gate/exo_gate/hook.py`.
- To try one from a shell: `exo decide "Is this safe?" --options safe,risky --state "rm -rf build/"`.

Tests use a fake router. Do not call a real provider in tests (section 7). `tests/core/models/` has examples.

### Add a CLI-backed provider

Two providers run a vendor CLI as a subprocess instead of calling an API: `claude-cli`
(`src/exo/core/models/claude_cli.py`) and `muse-cli` (`src/exo/core/models/muse_cli.py`, D-051). Use this shape
when a subscription is only reachable through the vendor's own login. The rules:

1. Exo never reads or stores the token. The CLI owns it. Read at most non-secret status fields, and return
   only the fields you need.
2. Give the child `exo.envscrub.minimal_env` minus any API-key variable that would switch billing.
3. Kill the whole process group on cancel, timeout and completion, even after the leader has exited. Bound
   cleanup waits and finish cleanup before reporting an error. Check signing only for native binaries;
   launchers have unknown signing state, and actual CLI signing errors must still allow router fallback.
4. Say what the CLI cannot do (tools, streaming, usage counts) in the module docstring and in
   `docs/PROVIDERS.md`. Refuse what you cannot carry with a status that lets the router fall back.
5. Wire it in `catalog.toml` (`transport`), `Router.credentials`, `providers_setup.rows` and `test_provider`,
   and `models_cmd.available_models`. Map failures to the existing error classes, with fixed detail text.
6. Test with a fake script on `PATH` (`tests/core/models/test_muse_cli.py`). No network, no real keychain.
7. Add the vendor's login and logout commands to the gate's credential rules.

### Work with subagents

A subagent is a child session the agent starts with the `delegate` tool (`src/exo/core/agents/delegate.py`). The
parent's tool call turns each task into a typed brief (`exo.wire`, SYM-2P), runs a child loop on the same engine with
a narrower toolset (`BLOCKED_TOOLS` lists what it never gets), and returns one receipt per child. Detail:
`docs/AGENT-WIRE.md` section 6.

- **Tiers.** Each child runs on `fast`, `standard` or `deep`, the cheapest that fits. A task item may carry `tier`.
  Otherwise its role picks one (`delegation.role_tiers`). A child never runs above its parent's tier unless
  `delegation.allow_upgrade` is true, and a failed child is retried once one tier up (`delegation.escalate_on_fail`).
  A seat can run on a tier with `[models] tier = "fast"` in its seat file (`docs/SEATS.md`).
- **Limits** (`Limits` in `delegate.py`): depth 1 (flat), 10 tasks per call, 4 running children at once, 24 tool
  rounds, 200,000 tokens and 900 seconds per child. Cancelling the parent's tool call interrupts every child.
- **The wire.** Check or read a packet with `exo wire check` and `exo wire render` (`docs/COMMANDS.md`). Models only
  ever read the English form. In code, pin the signer's key: `exo.wire.verify(line, sig, trusted_pub)` returns
  False without one, because the key a signature carries proves nothing (D-016 amendment).
- **Control (D-070).** The built-in `command.run` name `subagent` has the verbs `list`, `open`, `send` and `stop`, and
  lives in `src/exo/subagents.py`. Through this built-in only the principal may act: `command()` calls `steer` and
  `stop` without `by`, so the parent-session path (`by=<parent session id>`, which speaks as `delegate`, D-040) is
  in the rules but nothing on main calls it yet. Anyone else gets `forbidden`. A finished child answers `conflict`.
  A note typed by the principal is stored with author `principal`. The receipt records "steered by principal" or
  "stopped by principal". The built-in is not the only path: a child is a session, so any local caller can still steer
  it with `turn.send` or stop it with `turn.interrupt` on its session id (D-070 "Limits"). Such a note is stored with
  that caller's author and leaves no mark on the receipt.
  Tests: `tests/agents/test_delegate.py` and `tests/clients/tui/test_subagents.py`.

### Set up a provider from a client

One engine, `src/exo/providers_setup.py` (D-034), sits behind `exo providers` (`src/exo/providers_cmd.py`), the TUI
`/providers` panel and the web Providers page. Reuse it. Do not write a second key path.

- **Keys are write-only.** No function there returns a stored value. A key never goes through `config.set` or any
  protocol command. A client reads the masked field, calls `set_key`, and shows only status.
- **After a client stores a key**, it sends `command.run` with name `providers` and args `reload <name>`. The core
  re-reads its credentials without a restart (`reload` of an unknown name is `not_found`). `providers` with no
  arguments returns the status rows.
- To add a provider, wire it as in "Add a CLI-backed provider" (rows and `test_provider` in `providers_setup.py`).

User-facing steps: `docs/PROVIDERS.md` "Set up with the app or the TUI".

### Use the bus

The bus carries signed, hash-chained messages between seats. Reference: `plugins/exo-bus/README.md` and
`src/exo/bus/`. Three words:

- A **seat** is one agent identity: an id, a team, a platform and a public key, all listed in a signed registry. Its
  private key is a file, `$EXO_BUS_KEYS_DIR/<seat>.key` (default `$EXO_HOME/keys`).
- A **recipient** (`to`) is a seat id, `team:<name>`, or `group`. A seat's inbox holds messages to it, to its team and
  to the group.
- A **thread** is a string id you reuse to keep a conversation together. Read it back with `thread(id)`.

Settings come from `EXO_BUS_URL` (the relay) and `EXO_BUS_SEAT`. Legacy `DIVAN_*` names work too.

From Python, with `exo.bus` (the client needs only the base install, and the relay needs `pip install 'exo[bus]'`):

```python
from exo.bus import Client

cli = Client.from_env()                       # reads EXO_BUS_URL, EXO_BUS_SEAT, EXO_BUS_KEYS_DIR
cli.post(["team:builders"], "Build is green.", kind="report", thread="build-42")
for env in cli.inbox(since=0):                # envelopes; cli.text(env) gives plain text, decrypted if sealed to you
    print(env["from"], cli.text(env))
cli.thread("build-42")                        # one conversation, in order
cli.verify()                                  # re-check every id, signature and chain link
```

Message `kind` is one of `msg`, `ack`, `claim`, `notice`, `directive`, `report`, `file`. `enc_to="<seat>"` seals the
body so only that seat reads it. Every seat can read a body that is not sealed. The relay's web view (`/`, no auth)
shows no bodies unless the relay runs with `EXO_BUS_WEB_BODIES=1`. `post_sym2p(to, line)` sends a structured
agent-wire packet (`docs/AGENT-WIRE.md`).

From an agent, the `exo-bus` plugin gives four tools: `bus_post`, `bus_inbox`, `bus_thread`, `bus_verify`. They stay
hidden until `EXO_BUS_URL` and `EXO_BUS_SEAT` are set. The plugin's channel turns a message from another seat into
a turn and posts the reply back in the same thread. A bus message is peer input, never consent: the core refuses to
treat input from a peer as an approval (`docs/GATE.md`, `docs/PEERS.md`).

From a shell: `python -m exo.bus keygen|post|inbox|tail|thread|verify|relay|registry|checkpoint` (flags in
`src/exo/bus/cli.py`). Tests: `tests/bus/` runs a relay in a temp folder. Use a bus address that goes nowhere for
plugin tests, as section 7 says.

### Write to the signed ledger

The ledger is a signed, hash-chained SQLite log (`src/exo/ledger.py`). Reference: `docs/CORE.md` "Python API".

```python
from exo import crypto
from exo.ledger import Ledger

led = Ledger(store.ledger_path, crypto.Signer("agent-one", key))   # no signer: read only
ev = led.append("note", {"text": "hello"}, "agent-one", session="s1", refs=[])
led.verify(trusted)       # recomputes ids, chain links and signatures; returns {"ok": ..., "checked": ..., ...}
```

- `append(kind, body, author, session, refs)` signs and stores one event. Kinds are listed in `docs/CORE.md`.
  Triggers make the table append-only.
- `verify(trusted)` takes a map of author to allowed public keys, or a `KeyRegistry` (`src/exo/keyring.py`). An event
  signed by a key the registry does not pin for that author fails (D-040).
- **Head anchors.** `append` also writes the current head to anchor files outside the database, and `verify` checks
  them, so a ledger cut short or rolled back fails. `Ledger.anchor()` writes them by hand for an older store.
- **Do not open a signing ledger directly in product code.** Use `open_writable_ledger(path, author, key_path)` from
  `exo.ledger_authority.factory`, as `onboard.py`, `ask.py` and `consolidate.py` do. With
  `EXO_LEDGER_AUTHORITY_REQUIRED=1` a `Ledger` built with a signer raises, and the factory routes the append to the
  installed authority instead (`docs/LEDGER-AUTHORITY.md`).
- Only a principal author's events count as the principal's words (D-040). Write the principal's words under a
  principal id, and nothing else under it.

### Use retrieval

`exo.retrieve.assemble(store, query, budget=1500, ranker=None)` returns an `Assembly` with `text`, `node_ids` and
`tokens`: the best nodes packed under a token budget. The `Ranker` protocol is the seam: an object with
`rank(store, query) -> [(node_id, score)]`. The default is `BM25Ranker`, which calls `store.find`. To change how nodes
are chosen, write a `Ranker` and pass it in. Do not edit `assemble`. The same call backs `exo ctx`.

Only this much is built. The staged pipeline in `docs/STORE-V2.md` section 3 (pack, router pins, BM25 plus embedding
candidates, fusion, a budget fill) is designed, not built, and its signature there differs from the code. Test with a
made-up store, as in `tests/`.

### Track upstream

Code lifted from an upstream project (Hermes) is recorded so we can see what changed there since we took it. Reference:
`docs/STANDALONE.md` section 3 and `docs/PINNING.md`.

1. Start the lifted file with the one-line header `# Lifted from NousResearch/hermes-agent <path> @ <commit> (MIT,
   Copyright (c) 2025 Nous Research). See PROVENANCE.toml.`
2. Add a `[[module]]` entry for it to `PROVENANCE.toml` (or to a `PROVENANCE*.part.toml` file, then run
   `exo upstream merge`). `tests/upstream/test_provenance.py` fails when a file with the header has no entry, or an
   entry's `exo` path does not exist.
3. `exo upstream report` ranks upstream changes since each recorded commit. After you port or refuse them, run
   `exo upstream mark <exo-path> --commit SHA` (add `--rejected REASON` for a refusal). A commit only moves forward.
4. `exo upstream check` validates the file against the tree. Flags are in `docs/COMMANDS.md`.

### Schedule a job

A scheduled job is a prompt on a timer (`docs/SCHEDULE.md`, D-022). The scheduler is `src/exo/scheduler.py`
(`Scheduler`), outside the core since D-067. `exo.compose` attaches it (`attach_scheduler=Scheduler`) and the engine
holds it as `engine.scheduler`. An engine built without composition has none, and `schedule.edit` answers
`unsupported` there. `exo serve` runs its timer.

- **Create and manage** with the protocol command `schedule.edit`: `action` is `list`, `add`, `remove`, `pause`,
  `resume` or `run_now`, with `when` (cron or `every 30m`), `prompt`, `title`, `seat`, `tier` and `deliver`. The web
  Schedule page calls it through `POST /api/schedule` (`plugins/exo-command/exo_command/api.py`). There is no TUI
  command and no agent tool for it. The name `cronjob_manage` is only in the gate's category table and the
  subagent blocked list. Every action except `list` requires a live principal caller at
  `handle_schedule_edit` (called by `Scheduler.edit`). In-process client tests pass `caller="principal:tui"`
  to `Dispatcher`. A local, channel or seat caller cannot mutate jobs. Direct `Scheduler.add`, `pause`,
  `remove` and `launch` methods are trusted internal APIs, used by the timer and unit tests, not transport entry points.
- **Run.** Each run is an ordinary turn (D-041) in a fresh session of kind `scheduled`: the same context tree, memory
  provider, soul refresh and hooks, with the job's model tier, a narrowed toolset and an unattended-job prompt. It
  reports through `task.updated` and a `notice`. The run freezes its available, non-blocked tool names before
  starting and checks each call against that set before `engine.run_tool`. Tools added later are unavailable
  to that run. The gate and executor still apply to allowed names, so an approval can stall a run.
- Jobs live in `$EXO_HOME/schedule.sqlite`. A one-shot engine, such as a test, does not run the timer, so drive
  `Scheduler` directly in tests. Set `schedule` to `false` in `config.json` to turn it off.

### Work on the learning loop

How it behaves is in `docs/LEARNING.md`. This is where to put a change. Code is in `src/exo/learn/` and
`src/exo/consolidate.py`.

**The pieces.** A signal is a ledger event. The fast loop (`learn/review.py`) reads new `verdict`, `ruling` and
`answer` events (`SIGNAL_KINDS`), asks the `aux` model to write typed proposals, and writes them with
`propose_typed` (`consolidate.py`). Promotion (`learn/promote.py`, `apply_policy`) then decides each open proposal.
Accepting a proposal edits a node or a skill, and every step is a ledger event, so a revert is more events.

**Add a proposal type.** Today the types are `taste`, `skill`, `fact`, `question` (`PTYPES` in `consolidate.py`).

1. Add the name to `PTYPES`. The reviewer's output filter (`_clean` in `learn/review.py`) accepts only these.
2. Teach the reviewer: add the type to the routing list and the JSON template in `PROMPT` in `learn/review.py`.
3. Handle it in `propose_typed`. `skill` and `question` have their own branches. `taste` and `fact` share the
   signal path.
4. Make it acceptable and reversible: `proposal_accept` and `proposal_revert` in `consolidate.py` must apply and
   undo what your type changes. A skill goes through `_accept_skill`. Others write a node.
5. Decide when it promotes: see below. If it must pass the held-out eval first, add it to `promote.eval_gate_for` in
   the defaults.
6. Add tests in `tests/learn/`. `tests/learn/helpers.py` has builders for utterances, corrections, rulings and typed
   proposals, and the test fixtures give a scripted model.

**Add a promotion rule.** The rules are numbered and tried in order in `_decide` in `learn/promote.py`. The first one
that returns a `Decision` wins, so position is meaning. A `Decision` carries an `action` and the `rule` number.
`apply_policy` acts on three actions only: `promoted` and `auto_promoted` accept the proposal, and `question` asks the
principal once. Any other action leaves the proposal as it is. Keep the order in the module docstring and in the list
in `docs/LEARNING.md` in step with the code. The ruling that a guarded change (irreversible, public, financial, third
party) never promotes on its own must stay above any new rule that could promote.

**Add a setting.** Put the key and its default in `DEFAULTS` in `learn/runtime.py`. A key not listed there is dropped
when the config is merged, so a setting you forget to add does nothing.

**Add a signal.** Write a ledger event of a kind in `SIGNAL_KINDS`, with `refs` pointing at the output it judges, so
the reviewer finds it. Its author must be a principal id (`exo.provenance.principal_names`) or the loop ignores it
(D-040). Panel responses are recorded as `note` events of type `ui_signal` and are not in that list.
The protocol commands `feedback.send` and `ui.respond` reach the loop through `handle_feedback` and
`handle_ui_respond` in `learn/signals.py`.

Run `tests/learn` after any change, and read `docs/LEARNING.md` rule list again to be sure the page still matches.

### Work on the house taste

The design is `docs/TASTE-BASELINE.md`; its "Build status" says what is built. Code is in `src/exo/baseline/`.

- **Lookup order** lives in `resolve` in `baseline/layers.py`: ruling, pin, learned (or scoped), house. Keep that
  order; a new source goes into it, not around it. Every `Entry` must carry a source tag.
- **The privacy check** is `baseline/privacy.py`. A new rule adds hits; it never removes one. Add the case to the
  red table in `tests/baseline/test_privacy.py` first, and make sure the green table still passes. A word that is
  ordinary English can go in `plain-words.txt`; never a name, place, nation, party or faith.
- **The decision model** may only hold an export item (`export_house(..., judge=...)`). It is asked only about items
  that passed every rule.
- **Never run the export on a real store in tests**, and never commit a pack built from real data. The fixture
  stores in `tests/baseline/conftest.py` are made up. Point `$EXO_HOUSE_TASTE` at a released fixture pack to test
  the house layer.
- **Release.** A pack loads only when final (`baseline/pack.py`, `release_problem`). Tests build one with
  `export_house`, then `review.tick` on every value and contested item, then `review.finalise`.
- Policy files are product policy, not taste. Nothing in `baseline/` exports them.

Run `tests/baseline` and the `exo-memory` plugin suite after a change.

### Add an `exo` subcommand

`src/exo/cli.py` imports a command's module only when that command runs (#260), so `exo --help`, `exo --version`
and bare `exo` stay fast. A command that lives in its own module needs:

1. In the module, a `register(sp, common)` that adds the parser (`sp.add_parser(NAME, parents=[common], help=...)`
   when it works on a store), and a handler `cmd_<name>(a)`, or `cmd_<name>(a, store)` for a store command.
2. A row in `LAZY` in `src/exo/cli.py`, at the place the command should appear in `exo --help`: the module, the
   handler and the same help line `register` gives. `exo --help` prints the help line from the table, so it never
   imports the module.
3. For a store command, its name in `STORE_CMDS`, so `main` passes the resolved store.

Keep the module's own top-level imports to what the command needs: they run only for that command. A command that
belongs in `cli.py` itself (store reads, keys, the ledger) imports what it needs inside its function.
`tests/test_cli_lazy.py` fails when the table and `register` disagree: it compares the help of every command, and of
`exo --help`, against the parser that `build_parser()` builds with every module imported. It also checks that
`exo --help` loads no command module, and that a command is in `STORE_CMDS` exactly when its handler takes the
store. If the handler's second argument has a default, add the command to `OPTIONAL_STORE` in that test.

### Web composer draft ownership

`plugins/exo-command/exo_command/static/chat.js` owns a module-local draft map keyed by session ID, with a
null key before the first conversation is created. A successful creation transfers ownership to its real
session ID. Capture selection on input/navigation and restore only when switching the composer owner.
Pending sends are per session; failure reconciliation compares the draft revision before restoring text.
Mount listeners update a remounted chat when an older request finishes. Never persist prompt text to browser
storage or automatically retry an unconfirmed send. `composer.restore()` resets suggestions/history browsing
without changing the restored selection. Run `plugins/exo-command/tests/test_drafts_web.py` with Chrome for
thread selection, view remount, rejected-send, duplicate-submit and new-thread race coverage.

The delegated-approval hooks form an explicit security capability. Only registered `exo-gate` callbacks
are used. Validation rechecks the gate request identity, current policy and exact binding before inspection
and decision. Audit must commit a signed event before resolution, including without a required executor.
Unknown or missing callbacks withhold delegation. These hooks do not change principal authentication.
