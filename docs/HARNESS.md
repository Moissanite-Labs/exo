# Modular harness profiles

**Status:** Built (D-088, [#496](https://github.com/aska-digital/exo/issues/496)). Profiles are opt-in. An install with no profile keeps its behavior.

Exo can run with a small set of capabilities or with its full set of integrations.
Profiles select runtime components. They do not change models, approval policy,
executor requirements, stored conversations, or installed Python packages.
An installation with no `harness` setting keeps its existing behavior.

## Choose a profile

```sh
exo harness list
exo harness use coding --dry-run
exo harness use coding
exo harness show
exo harness show --json
```

Restart the shared core after changing a profile: `exo stop`, then launch Exo again.
Selection is per `EXO_HOME`. It applies to the terminal and browser clients using that core.
The CLI validates configuration without importing plugins, connecting to providers,
starting MCP servers, or sending a model request. `show` explains selection and dependency
reasons. It does not probe installed plugin availability or an already running daemon.

| Profile | Selected capabilities |
|---|---|
| `lean` | Coding tools, durable sessions/context tree, tracing and `exo-gate` |
| `coding` | Lean plus principal memory, plugin context compression, skills, workspace instructions, configured MCP servers, background jobs and delegation |
| `full` | Coding plus peers, scheduler, learning, bus/channels, browser plugin and orchestration skills |

The durable agent log/context tree is part of the core in every profile. Turning off
`memory` removes the principal-memory plugin. It does not delete conversation history.
`full` permits integrations but does not invent credentials, configure servers, enroll
channels, create schedules, or override an individual service's explicit off setting.

## Compose your own setup

```sh
exo harness use lean
exo harness enable skills instructions jobs
exo harness disable jobs --dry-run
exo harness disable jobs
```

These commands write only the `harness` object in `$EXO_HOME/config.json`:

```json
{
  "harness": {
    "profile": "lean",
    "enable": ["skills", "instructions"],
    "disable": ["jobs"]
  }
}
```

`use` resets capability overrides to the named preset. `enable` and `disable` change
overrides on a saved profile, retaining unrelated settings. Without a saved profile they
refuse and ask for `use` first, so an install never moves off legacy discovery by accident.
`--dry-run` validates and prints the proposed selection without saving it.
`EXO_PROFILE=lean exo` overrides the base profile for that process. Saved enable/disable
overrides still apply. It affects a newly created core, not one a client reconnects to. Remove `harness` with `exo config unset harness` and
unset `EXO_PROFILE` to return to legacy discovery.

The full capability names are `gate`, `memory`, `context`, `skills`, `instructions`,
`mcp`, `jobs`, `delegation`, `peers`, `scheduler`, `learning`, `bus`, `web`, and `orchestra`.
`context` and `learning` require `memory`. `orchestra` requires `delegation` and `skills`.
Dependencies are included automatically unless explicitly disabled, which is an error.
For example, disabling memory in `full` also requires disabling context and learning:

```sh
exo harness disable memory context learning
```

An unknown capability, invalid JSON shape, contradictory override, or disabled required
dependency fails before a configuration write. Every explicit profile requires a working
`exo-gate` that registers its `pre_tool_call` hook. A missing or failed gate stops startup,
even when another plugin registers its own hook. Profiles do not change the gate's mode or
policy. Profiles cannot disable an executor's mandatory plugins or bypass its trusted
inventory. When an executor requires a third-party plugin, list it in `core_plugins` (or
the seat's `plugins`). Otherwise startup stops and names the plugin.
With a profile, `core_plugins` must be a list of nonempty names. Without one it is read as
before D-088: an entry that is not a name selects no plugin and does not stop startup.

## Precedence and runtime behavior

1. `EXO_PROFILE`, if set, chooses the preset. Otherwise `harness.profile` does. An
   unknown preset stops startup, and the error names the one that was set.
2. Apply explicit capability enables/disables and required dependencies.
3. The profile selects built-in plugins. Explicit third-party names from `core_plugins`
   remain selected. Profiles do not implicitly discover other third-party plugins.
   `exo harness show` and `exo harness use` name each discovered plugin that the profile
   will not load (a `Not loaded:` line, and `unselected_plugins` in `--json`). Add it to
   `core_plugins` to keep it. They read only `plugin.yaml` files and entry-point names.
4. Existing subsystem settings still apply. A selected capability may be configured off.
   With a profile, `jobs.enabled: false` or `delegation.enabled: false` omits that manager.
   Without a profile these two keys change nothing, as before D-088.
5. A seat may narrow the selection. A seat plugin list must retain the gate and cannot
   name a disabled built-in plugin. Disabled MCP/channels/peers are suppressed even if
   a seat configures them. Disabling jobs in the profile or seat omits its manager.

Settings suppressed at runtime remain in the saved file, so a later profile can reuse
them. Provider configuration, spending limits, principal identity and gate policy retain
their existing resolution rules. Profile changes do not hot-reload an existing engine.

Disabled jobs and delegation use inert lifecycle adapters: no job workers, delegated
task tables, or tools are created for them. Background mode is removed from the bash
schema and rejected before local or broker execution even if a caller supplies it.
Foreground commands remain available. Disabling skills skips discovery and its tool.
Disabling MCP avoids configured transport startup. Disabling scheduler avoids its store.
Disabling peers avoids Exo's own peer service and its presence directory. The Hermes peer
adapter in the exo-bus plugin is separate: it follows `EXO_PEERS`, which stays off under
Exo (see the exo-bus README). Learning honors profiles even when a client sends feedback.
All configured channels are governed by `bus`. Disabling `bus` does not stop `delegate`
from sending a signed directive to another seat that is already set up. That send belongs
to delegation, so disable `delegation` to stop it.
The scheduler can be enabled independently of delegation and background jobs. Scheduled
turns also use workspace instructions when that capability is enabled.

Working notes and continuity checkpoints are not capabilities. Their own settings decide in
every profile and without one: `work_notes.enabled` (off by default, D-086) and
`work_checkpoint.enabled` (on by default, D-086 amendment). A checkpoint is built from the
durable session records, which every profile keeps. It adds no tool, process or network use.
compose checks the checkpoint setting before plugins load, and attaches it after the notebook,
inside the startup rollback. Scheduled and delegated loops share the workspace prompt, but
the checkpoint records ordinary root threads only. See [WORK-CHECKPOINT.md](WORK-CHECKPOINT.md).

Some shared implementation modules remain bundled/imported by the core. Runtime profiles
are not an installation-size optimizer or an OS sandbox. Third-party Python plugins are
trusted host code and can allocate their own resources. `web` controls the plugin's core
registration. An explicit `exo web` still launches the separately installed browser client.

Workspace guidance is described in [WORKSPACE-INSTRUCTIONS.md](WORKSPACE-INSTRUCTIONS.md).
Required-executor mode currently refuses enabled workspace instructions because those
reads do not yet go through the executor. Disable `instructions` there until an executor
read adapter is implemented. The rest of the profile remains usable.

## Plugin dependency and resource contracts

A folder plugin may declare required plugin names in `plugin.yaml`:

```yaml
name: code-review
version: "1.0"
requires: [repository-context]
```

Requirements load before their consumer, deterministically. They must already be selected.
The loader does not install packages or expand an explicit allowlist. Missing, excluded,
failed, malformed or cyclic requirements prevent a dependent folder's code from importing.
Independent plugins still load. A failed registration rolls back its registrations.

Installed `exo.plugins` entry points can expose `PLUGIN_MANIFEST`. Python must import
the entry point to read that metadata, so dependency validation prevents registration,
not its import-time effects. Folder plugins take precedence over duplicate entry points.
Entry points register in the order Python lists them, as before D-088, so their hooks
keep that order. A requirement can still move a dependency ahead of its consumer.
Dependency names do not currently express version constraints.

Resources a plugin creates during `register` can be paired with synchronous cleanup:

```python
def register(ctx):
    resource = open_resource()
    ctx.register_cleanup(resource.close)
    ctx.register_command("example", lambda raw="": resource.status())
```

Callbacks run once, in reverse acquisition order, on failed registration or final manager
shutdown. A failing callback is logged and does not prevent the rest. Ctrl-C or
`SystemExit` in a callback also lets the rest run, and is raised after them. The manager
owns a plugin's cleanups before `register` runs, so an interrupted `register` still has
them released at shutdown. Async callbacks are rejected. Runtime async channel shutdown
continues through the existing channel API.
Manager shutdown is final and idempotent. It preserves registration records and security
hooks. It is not a live plugin unload/reload API. Do not close a manager owned by a live
engine. The engine owns shutdown ordering and startup rollback.

Engine shutdown runs in this order:

1. It refuses new commands, turns, tools and background jobs. A command fails with
   `closed`, so it cannot start a service again. A start in progress is cancelled. The
   next command retries a failed or cancelled start (D-010 amendment), but not once close
   has begun. A job that ends from here on is
   recorded for its thread and never starts a turn. Active turns are cancelled.
2. It stops channels, peers, background jobs (their process groups), the scheduler and MCP.
3. It waits up to five seconds for turns, synchronous tool workers and learning reviews.
   Then it closes the learner.
4. It closes plugin resources, removes the trace sink, releases the writer claim and
   closes the session store last.

When a step in 2 fails, the steps after it still run. They also run after a hard stop
there, such as a second Ctrl-C. Background jobs are killed all at once, so a hard stop
during the kills still ends every job. A learner that fails to close still lets step 4
run. `close()` raises the first error at the end. A hard stop during the wait in step 3
ends `close()` at once. Jobs and transports are stopped by then, and the rest stays as
after a timeout.

Python cannot stop a worker thread. If a synchronous tool is still running at step 3,
`CoreRuntime.close()` raises `ShutdownTimeout`. By then jobs and transports are stopped.
It keeps only what that tool can still touch: plugin resources, the session store and
the writer claim. If a step in 2 also failed, `ShutdownTimeout` names that error and lists
it in `errors`. A later `close()` finishes the job once the tool returns. The engine
that `exo serve` and the terminal build (`exo.application.CoreEngine`) logs a warning and
closes again until it does. Its plugin cleanups then run once and exod exits normally.
It does not close again after a hard stop in step 2. That `CancelledError`, Ctrl-C or
`SystemExit` is raised once the wait ends, and the rest stays as after a timeout.
No detached cleanup watcher is created.

Plugin tool handlers are called as `fn(args, session_id=, task_id=, tool_call_id=)`. They
get no cancellation signal, so keep blocking work bounded, for example with a timeout on
each network call. Shutdown waits for a handler to return.
Call `close()` from the engine owner, outside its running turn/tool callbacks. Reentrant
shutdown from owned work is rejected before anything is stopped.

## Architecture and evidence

`exo.harness` resolves a plan without runtime side effects. `exo.compose` applies it.
`exo.harness_runtime` supplies inert optional services. `exo.core.shutdown` holds the
shutdown steps around the core's transport and job steps (`stop_admission` and `drain`).
The runtime accepts injected job and delegation factories and a skills switch. Workspace prompt injection is per session
and reused by child loops. Plugin graph/resource ownership lives in
`exo.core.plugin_lifecycle`. Both modules are part of the counted core, so the core imports
no product module for them, and they read no other module's private state (D-083's step 4
caps). `PluginManager.bind_tools` also binds tool/slot adapters and
translates lifecycle verdicts through the existing registry and approval desk. Its explicit
collaborators keep plugin compatibility independent of the engine's private state. Direct
`CoreRuntime` construction retains plugin support and the former helper methods remain shims.
Registration interfaces remain in `exo.core.plugins`. The extraction does not change approval
policy or exclude modules.
No wire command or event was added.

[HARNESS-LANDSCAPE.md](HARNESS-LANDSCAPE.md) records the dated comparison, sources,
remaining features and evaluation plan. These changes adopt design ideas. No competitor
implementation was copied. A claim that Exo outperforms other harnesses still requires
the comparative evaluations described there.

## Verification of this change

This is the first check, on 2026-10-23, before the review rounds changed shutdown,
instructions and the CLI. It ran with Python 3.11.17 on Linux. Each later commit
names the tests it ran.

- Full root suite: **7,368 passed, 124 skipped**, with one asyncio subprocess-transport
  finalizer warning in `test_claude_cli.py` (`Event loop is closed`). No root test failed.
- Focused composition, lifecycle, workspace, learning, CLI, plugin, docs and budget
  sweep: **234 passed**. Independent scheduler/instruction tests also pass.
- Each plugin suite ran separately: **2,471 passed, 1 skipped, 2 failed** initially.
  A Chrome fixture startup failure passed immediately on isolated retry. The remaining
  [browser layout assertion](https://github.com/aska-digital/exo/issues/499) (`inputY=134`, expected `<120`) reproduces unchanged on base
  commit `01101d3c60aeafbf04949bee1128b4e40e1e92ec`.
- Ruff, protocol export freshness, diff whitespace, and the type baseline pass. The type
  check retains the repository's **233 existing diagnostics**. It is not a clean mypy claim.
- The internal footprint benchmark is reproducible with `scripts/benchmark_harness.py`.
  Its assumptions and measurements are in the landscape document.

The full root run used four pytest workers and a temporary Linux subreaper supervisor.
This container's PID 1 is `tail` and does not reap orphaned test subprocesses. Without
the supervisor, existing descendant-cleanup assertions mistake terminated zombies for
live processes. The same failure was reproduced in an untouched archive of the base
commit. No application process-management code was changed to accommodate that environment.
`pytest-xdist` was installed only in the test environment. Package dependencies are unchanged.
Live provider credentials and external integrations were not exercised.
