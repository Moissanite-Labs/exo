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

## 1. Repo layout

| Path | What lives there |
|---|---|
| `src/exo/core/` | The core: loop, engine, router, tools, sessions, context tree, jobs, peers, scheduler, plugin loader. Small on purpose. |
| `src/exo/protocol/` | The wire protocol: `schema.py` (commands and events), `client.py`, `export.py`, `generated/`. |
| `src/exo/server/` | The server that carries the protocol (Unix socket, TCP with a token, or stdio). |
| `src/exo/*.py` | The generic mechanism: store, ledger, crypto, gate, capture, consolidate, eval, brief, onboard, CLI. |
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
`CoreEngine._builtin_command` in `src/exo/core/engine.py`: `context`, `compact`, `trace`, `findings`,
`handoff_facts`, `harness_error` and `unsteer` (pull a queued steer message back; args is its id, empty means the
newest; publishes `steer.cancelled`, D-045). Anything else goes to a command a plugin registered. To add one:

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

Exo OS can require an installed execution broker after approvals and validation.
New tools need an explicit broker decision before their handler may run;
unsupported operations fail closed. The immutable request, installed entry point,
trusted plugin inventory and host-code limits are in `docs/EXECUTORS.md`.

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
`harness.error` and become findings.

**Who wrote it (D-040).** Every message dict a hook receives has `author` (`principal`, `agent`, `tool`,
`peer:<name>`, `bus:<seat>`, `job`, `delegate`, `schedule`, `system`) and user messages have `principal`. Treat
text as the principal's only when `principal` is true; `exo.provenance.principal_view(messages)` turns every other
user message into role `other`, which the capture extractors skip. A new way for text to reach a session must stamp
`meta.author` itself (pass it as `meta` to `loop.send`, or set it on the stored `Message`), never derive it from
the text. A new ledger writer that records the principal's words writes them under a principal id; anything the
agent decides on its own is written under the agent's name.

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
folder, so a test never reads your real setup. The root fixture also clears inherited `NO_COLOR` so terminal
settings cannot change the expected TUI theme. Tests that need no-colour mode set `NO_COLOR` themselves.

Check your prose before you commit docs:

    python3 skills/clean-writing/scripts/check-prose.py docs/YOUR-FILE.md

### CI, lint, lockfile and the privacy deny-list (D-042)

**CI.** `.github/workflows/ci.yml` runs on every push to main and every pull request. It runs the root suite and
then each plugin suite on its own (from the plugin folder, because each has its own conftest), on macOS and Ubuntu,
with Python 3.11 and 3.13. It also runs `pytest plugins` once, to prove the plugin tests collect together. Plugin
test files need unique names (`test_<plugin>_...py`) because the plugin test folders are not packages.

**Lint.** `ruff check` with the pyflakes rules only (`F`, set in `pyproject.toml`). CI runs it with `--exit-zero` for
now, so findings show but do not fail the build: the tree still has unused imports (F401) that were not mass-fixed
while several branches edit the same files. Keep new code clean. When the baseline is clean, drop `--exit-zero`.

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
`~/.config/exo/deploy.conf`. It deploys from a copy outside git.

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

### Cutting a release

Names, branches and tags are in [`docs/VERSIONING.md`](VERSIONING.md). To release Exo: bump the version in
`pyproject.toml` and the files that section lists, move `[Unreleased]` in `CHANGELOG.md` under a new
`[X.Y.Z] (codename) - date` heading, run the suites (`tests/test_versioning.py` fails if they drift), merge, then tag
main with an annotated `exo-vX.Y.Z`. Site deploys take `--version`; see `scripts/deploy-site.sh`.

## 9. Recipes

Short, working patterns for four things people ask about. Each one names the file to read next.

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
from exo.core.models.decision import ask_choice_result, ask_score, tighten_only, DecisionError

res = await ask_choice_result(router, state, "How strictly should this call be gated?",
                              ["allow", "log", "ask", "deny"])
res.choice, res.probabilities, res.source, res.route, res.confidence
#   source is "decision" (a probability model) or "generative" (a chat model fallback, probability 1.0 on its pick)

sc = await ask_score(router, state, "How risky is this?", ["harmless", "minor", "serious"])
sc.score         # a level index counted from 0; it can fall between levels, such as 1.43. Use 2 to 10 levels.

probs = await ask_choice(router, state, "Which fits?", ["a", "b"])   # just {option: probability}
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
body so only that seat reads it. `post_sym2p(to, line)` sends a structured agent-wire packet (`docs/AGENT-WIRE.md`).

From an agent, the `exo-bus` plugin gives four tools: `bus_post`, `bus_inbox`, `bus_thread`, `bus_verify`. They stay
hidden until `EXO_BUS_URL` and `EXO_BUS_SEAT` are set. The plugin's channel turns a message from another seat into
a turn and posts the reply back in the same thread. A bus message is peer input, never consent: the core refuses to
treat input from a peer as an approval (`docs/GATE.md`, `docs/PEERS.md`).

From a shell: `python -m exo.bus keygen|post|inbox|tail|thread|verify|relay|registry|checkpoint` (flags in
`src/exo/bus/cli.py`). Tests: `tests/bus/` runs a relay in a temp folder. Use a bus address that goes nowhere for
plugin tests, as section 7 says.

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
