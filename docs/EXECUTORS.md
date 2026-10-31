# Required tool executors

Exo OS uses the shared Exo registry with an installed execution broker. Set
`EXO_TOOL_EXECUTOR` to a named `exo.executors` entry point and
`EXO_TOOL_EXECUTOR_REQUIRED=1` to refuse startup if it cannot load. The entry
point is a synchronous, no-argument factory returning a broker. Names select
installed distributions. Module strings and arbitrary file paths are rejected.
An explicitly configured executor that is missing, duplicated, malformed or
fails during loading aborts startup even without the required flag.

The ordinary Exo engine retains direct handlers when no executor is configured
or required. `CoreEngine(executor=broker, executor_required=True)` supports
explicit injection with an explicitly loaded trusted folder gate. A custom registry
receives the same broker. Missing or failed gate registration, ambient entry-point
gates, and gates outside a declared executor inventory are rejected before stores open.

## Dispatch contract

```python
from exo.core.tools.executor import ExecutionRequest, TrustedDispatch, thaw_arguments
from exo.core.tools.registry import Tool
from exo.core.types import ToolResult

async def execute(self, request: ExecutionRequest, tool: Tool,
                  run_trusted: TrustedDispatch) -> ToolResult:
    ...
```

Every registered main-turn, delegated and scheduled tool goes through the same
`Registry.call`: lookup and availability, pre-tool hooks (including approvals),
schema validation, executor, then post-tool hooks and secret redaction. Unknown
tools, blocked calls and invalid arguments never dispatch. In broker mode the
registry binds a detached immutable request before pre-tool hooks. Registry hooks
cannot change its arguments, name, id, cwd or session. Each required plugin hook
receives an independent ordinary JSON copy for inspection and serialization.
One observer cannot change the gate's input or the dispatched request, including
nested arguments. Ordinary mode retains argument rewriting, but not identity changes. The schema validator checks
required keys and top-level types/enums. It is not a complete JSON Schema engine.

The frozen request contains `call_id`, `tool_name`, `arguments`, `cwd`, and
`session`. Objects become read-only mappings and arrays become tuples, detached
from the model call and hook-held references. Non-JSON and non-finite values
fail closed. `thaw_arguments(request.arguments)` produces a fresh mutable copy
for serialization or a worker. The broker receives the registered `Tool` as
trusted metadata and the no-argument async `run_trusted` callback. The callback
runs the host handler with a fresh argument copy and the snapshotted cwd/session.
An OS broker must explicitly audit each tool it permits through that callback.
Its default for unknown or unsupported tools must be denial.

A broker must return the standard `ToolResult`. The registry fixes its call id,
caps text at the usual 100,000 characters and runs post-hooks. Broker exceptions
become error results and also reach post-hooks. Cancellation sets the shared
cancel event and propagates `CancelledError`, just as handler cancellation does.
There is no automatic handler retry or fallback after a broker failure or loss.

The required gate must commit its signed decision event before allowing a call
or asking for approval. It must also commit the approval event before execution.
Missing stores, unusable keys, failed appends or missing event IDs block the call
with a generic error. Gate callback failures in these two phases propagate to
the registry. Optional observer hooks retain their existing failure behavior.
The exception fallbacks (observe mode lets the call ahead; otherwise a
once-only card, reads included, D-084) cannot permit a failed required gate
check, and observe mode still asks before a required executor runs.

Every gate approval carries a unique internal `approval_key`, separate from the
reusable `rule_key` and content binding. The engine returns that token to the
approval hook so concurrent identical calls retain their own session, call id
and decision reference. Required mode refuses missing or mismatched tokens.
Ordinary legacy hosts may omit the token only when one pending call matches.

Policy acceptance is audited before the candidate replaces the pinned policy.
A required standing grant is prepared without writing, then receives a signed
`phase: grant`, `state: authorized`, `activation: pending` event before its file
is atomically replaced. If auditing fails there is no new authority. If grant
persistence fails, the call is blocked and the audit records authorization only.
It does not claim successful activation. Audit and grant files are separate
stores, so this ordering does not promise a cross-file atomic transaction.

Required mode refuses `gh --body-file` inspection before touching the selected
host path, including symlinks. Inline text can be checked without opening a file.
An unseen body cannot be approved by this gate: a body file, stdin, an `@file`
field, `--input` or an expansion in the text is a recorded `deny` (#208,
`docs/GATE.md` "Required executor audit"). A gh line the gate also reads as it
did before #399 is refused when either reading has one (`gh pr "$V" 5 -F f`).
Policy, grant and scrub name files come from trusted store/configuration paths
and remain host code.

Background bash arguments reach the broker before any host job starts. The
broker must deny background mode until it has an isolated background worker.
Existing job recovery marks interrupted jobs lost. It does not restart them.
Scheduled turns and delegated turns use `CoreEngine.run_tool` and this registry.
Local delegation setup encodes data and queries the engine store without running
workspace commands. Required mode refuses cross-seat delegation unless the
broker explicitly sets `supports_remote_delegation=True`, because that path
opens a trusted host Bus connection.

Required engines omit the Git portion of `/handoff` facts with an explicit
unavailable note. Even read-only Git commands can execute a workspace's
`core.fsmonitor` helper. File completion in a process with the required flag,
or a `FileIndex(executor_required=True)`, uses a bounded filesystem listing
without Git. That listing does not apply `.gitignore`. Embedded TUI and web
completion also use the explicit engine flag. Web caches separate required and
ordinary indexes. Remote clients use the bounded listing because they cannot
establish the remote engine mode. Git tool requests may
still run inside an isolated worker according to the broker's policy.

## Startup and trusted code

Required mode rejects every configured MCP transport, including HTTP, before
startup unless the installed broker explicitly sets `supports_mcp_startup=True`.
Such a declaration authorizes MCP startup separately from tool dispatch. Its
process and network isolation are the broker/distribution's responsibility.

Required mode disables user folder discovery (`EXO_PLUGIN_DIRS` and
`EXO_HOME/plugins`). A broker may supply `trusted_plugin_dirs`, a tuple of
absolute paths, and `required_plugins`, a tuple of plugin names. The OS broker
must verify ownership, permissions and symlinks for its trusted directories
before returning them. Required factory startup loads only those directories.
Ambient installed plugin entry points are disabled. `exo-gate` is always required, whatever the broker lists, because
it supplies the decision and approval evidence. Disabling a required plugin in
config, missing plugins, or failed required imports/registration abort startup. Executor loading happens before
any plugin import. The manager is marked required before registration and cannot
be downgraded afterwards. A preloaded gate also observes this pinned manager flag
when attached directly to a required engine. Non-required mode keeps existing
discovery behavior.

A configured but optional broker still blocks tools when any pre-tool policy hook
fails. Broker-backed calls always isolate hook arguments and refuse dispatch if
the gate approval callback fails. Only required engines tolerate failures from
non-gate pre-tool hooks.

This interface mediates model tool requests. It does not sandbox arbitrary
Python code loaded into the engine. Installed executor and plugin imports,
availability checks, hooks, plugin commands, memory/context/channel adapters,
provider calls (including CLI providers), scheduler bookkeeping and core
protocol commands execute trusted host code. Runtime configuration and installed
code must remain outside the model worker's writable filesystem. Passing a
malicious Python plugin this interface is not a security boundary. The OS
distribution must maintain its audited inventory and isolate worker execution.
