# Required tool executors

Exo OS uses the shared Exo registry with an installed execution broker. Set
`EXO_TOOL_EXECUTOR` to a named `exo.executors` entry point and
`EXO_TOOL_EXECUTOR_REQUIRED=1` to refuse startup if it cannot load. The entry
point is a synchronous, no-argument factory returning a broker. Names select
installed distributions; module strings and arbitrary file paths are rejected.
An explicitly configured executor that is missing, duplicated, malformed or
fails during loading aborts startup even without the required flag.

The ordinary Exo engine retains direct handlers when no executor is configured
or required. `CoreEngine(executor=broker, executor_required=True)` supports
explicit injection; a custom registry receives the same broker.

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
tools, blocked calls and invalid arguments never dispatch. Pre-hooks may rewrite
arguments but cannot change a call's name or id. The schema validator checks
required keys and top-level types/enums; it is not a complete JSON Schema engine.

The frozen request contains `call_id`, `tool_name`, `arguments`, `cwd`, and
`session`. Objects become read-only mappings and arrays become tuples, detached
from the model call and hook-held references. Non-JSON and non-finite values
fail closed. `thaw_arguments(request.arguments)` produces a fresh mutable copy
for serialization or a worker. The broker receives the registered `Tool` as
trusted metadata and the no-argument async `run_trusted` callback. The callback
runs the host handler with a fresh argument copy and the snapshotted cwd/session.
An OS broker must explicitly audit each tool it permits through that callback;
its default for unknown or unsupported tools must be denial.

A broker must return the standard `ToolResult`. The registry fixes its call id,
caps text at the usual 100,000 characters and runs post-hooks. Broker exceptions
become error results and also reach post-hooks. Cancellation sets the shared
cancel event and propagates `CancelledError`, just as handler cancellation does.
There is no automatic handler retry or fallback after a broker failure or loss.

Background bash arguments reach the broker before any host job starts. The
broker must deny background mode until it has an isolated background worker.
Existing job recovery marks interrupted jobs lost; it does not restart them.
Scheduled turns and delegated turns use `CoreEngine.run_tool` and this registry.
Local delegation setup encodes data and queries the engine store without running
workspace commands. Required mode refuses cross-seat delegation unless the
broker explicitly sets `supports_remote_delegation=True`, because that path
opens a trusted host Bus connection.

Required engines omit the Git portion of `/handoff` facts with an explicit
unavailable note. Even read-only Git commands can execute a workspace's
`core.fsmonitor` helper. File completion in a process with the required flag,
or a `FileIndex(executor_required=True)`, uses a bounded filesystem listing
without Git; that listing does not apply `.gitignore`. OS client launchers must
export the required flag as well as the engine service. Git tool requests may
still run inside an isolated worker according to the broker's policy.

## Startup and trusted code

Required mode rejects every configured MCP transport, including HTTP, before
startup unless the installed broker explicitly sets `supports_mcp_startup=True`.
Such a declaration authorizes MCP startup separately from tool dispatch; its
process and network isolation are the broker/distribution's responsibility.

Required mode disables user folder discovery (`EXO_PLUGIN_DIRS` and
`EXO_HOME/plugins`). A broker may supply `trusted_plugin_dirs`, a tuple of
absolute paths, and `required_plugins`, a tuple of plugin names. The OS broker
must verify ownership, permissions and symlinks for its trusted directories
before returning them. Core loads only those directories plus installed plugin
entry points. Disabling a required plugin in config, missing plugins, or failed
required imports/registration abort startup. Executor loading happens before
any plugin import. Non-required mode keeps existing discovery behavior.

This interface mediates model tool requests; it does not sandbox arbitrary
Python code loaded into the engine. Installed executor and plugin imports,
availability checks, hooks, plugin commands, memory/context/channel adapters,
provider calls (including CLI providers), scheduler bookkeeping and core
protocol commands execute trusted host code. Runtime configuration and installed
code must remain outside the model worker's writable filesystem. Passing a
malicious Python plugin this interface is not a security boundary. The OS
distribution must maintain its audited inventory and isolate worker execution.
