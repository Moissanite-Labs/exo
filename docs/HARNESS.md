# Modular harness profiles

**Status:** In progress (D-082, [#496](https://github.com/aska-digital/exo/issues/496)). Implemented on the feature branch and awaiting review.

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
overrides, retaining unrelated settings. `--dry-run` validates and prints the proposed
selection without saving it. `EXO_PROFILE=lean exo` overrides the base profile for that
process. Saved enable/disable overrides still apply. It affects a newly created core,
not one a client reconnects to. Remove `harness` with `exo config unset harness` and
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
`exo-gate`. A missing/failed gate stops startup. Profiles cannot disable an executor's
mandatory plugins or bypass its trusted inventory.

## Precedence and runtime behavior

1. `EXO_PROFILE`, if set, chooses the preset. Otherwise `harness.profile` does.
2. Apply explicit capability enables/disables and required dependencies.
3. The profile selects built-in plugins. Explicit third-party names from `core_plugins`
   remain selected. Profiles do not implicitly discover other third-party plugins.
4. Existing subsystem settings still apply. A selected capability may be configured off.
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
Disabling peers avoids its service and presence directory. Learning honors profiles even
when a client sends feedback. All configured channels are governed by `bus`.
The scheduler can be enabled independently of delegation and background jobs. Scheduled
turns also use workspace instructions when that capability is enabled.

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
Dependency names do not currently express version constraints.

Resources a plugin creates during `register` can be paired with synchronous cleanup:

```python
def register(ctx):
    resource = open_resource()
    ctx.register_cleanup(resource.close)
    ctx.register_command("example", lambda raw="": resource.status())
```

Callbacks run once, in reverse acquisition order, on failed registration or final manager
shutdown. A failing callback is logged and does not prevent the rest. Async callbacks
are rejected. Runtime async channel shutdown continues through the existing channel API.
Manager shutdown is final and idempotent. It preserves registration records and security
hooks. It is not a live plugin unload/reload API. Do not close a manager owned by a live
engine. The engine owns shutdown ordering and startup rollback.

Engine shutdown first stops new turns, interrupts/drains active work and waits for actual
synchronous tool workers before releasing plugin resources. Python cannot forcibly stop
a worker thread. If that work does not finish within five seconds, `close()` raises and
retains the plugin resources, session store and writer claim. The owner can retry after
the handler finishes. No detached cleanup watcher is created. Plugin authors should
make blocking work bounded and cancellation-aware.
Call `close()` from the engine owner, outside its running turn/tool callbacks. Reentrant
shutdown from owned work is rejected before resources are released.

## Architecture and evidence

`exo.harness` resolves a plan without runtime side effects. `exo.compose` applies it.
`exo.harness_runtime` supplies inert optional services. The runtime accepts injected job
and delegation factories and a skills switch. Workspace prompt injection is per session
and reused by child loops. Plugin graph/resource ownership lives in
`exo.plugin_lifecycle`. Its registration interfaces remain in `exo.core.plugins`.
No wire command or event was added.

[HARNESS-LANDSCAPE.md](HARNESS-LANDSCAPE.md) records the dated comparison, sources,
remaining features and evaluation plan. These changes adopt design ideas. No competitor
implementation was copied. A claim that Exo outperforms other harnesses still requires
the comparative evaluations described there.

## Verification of this change

Checked on 2026-10-23 with Python 3.11.17 on Linux:

- Full root suite: **7,368 passed, 124 skipped**, with one asyncio subprocess-transport
  finalizer warning in `test_claude_cli.py` (`Event loop is closed`). No root test failed.
- Final focused composition, lifecycle, workspace, learning, CLI, plugin, docs and budget
  sweep after the last edits: **234 passed**. Independent scheduler/instruction tests also pass.
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
