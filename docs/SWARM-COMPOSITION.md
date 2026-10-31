# Swarm profile composition

`exo.core.group_composition.GroupComposition` is a synchronous `EngineFactory` for
`GroupHost`: `Callable[[SavedGroup], Mapping[str, CoreEngine]]`. It builds one distinct real
`CoreEngine` per group member.

## Trust model

The host injects everything trusted; the composition reads nothing from the outside world:

- an **explicit authorized profile -> `Seat` mapping** — the only profiles it can run, each
  bound to the exact `Seat` (the seat's name must equal the profile ID);
- the authenticated **owner** — verified against the stored group;
- `home`, `models`, `cwd`, `env` — the same `home` for every engine, so all members share
  one session DB;
- an optional **`GroupPlugins`** profile — the plugin folders, allow-list, config,
  memory/context choices, extra skill folders and executor that every member engine uses.

There is **no automatic metadata/config authorization**: no seat files, config.json or
environment are consulted for membership or plugin decisions. Plugin loading is explicit
only: `pm.load_all()` runs with `entry_points=False`, and no `$EXO_HOME/plugins` default
folder is ever consulted, so no plugin is discovered implicitly.

## Guarantees

- **Validate before build.** Owner mismatch, duplicate members or a member outside the
  authorized mapping raise `CompositionError` before any engine is constructed.
- **Exact seats.** Each engine receives the mapped `Seat` object unchanged and claims its
  writer lock (`claim_writer()`); each engine is a separate `CoreEngine` instance.
- **Fail closed.** If construction or a claim fails mid-group (e.g. another writer holds a
  seat's log), the partial engines — including the engine whose claim failed — are retained,
  the error is re-raised as `CompositionError`, and there is no retry and no takeover of
  another member's claim.
- **No overwrite.** While engines are retained (after a successful call or a partial
  failure), further calls are rejected with `CompositionError` until `aclose()` completes;
  tracked engines are never overwritten (which would leak their writer locks). After a
  successful `aclose()` the same composition may be reused.
- **Async cleanup.** `aclose()` closes every retained engine — from a successful call or a
  partial failure — attempting all of them and re-raising the first error. References are
  cleared only for engines whose close succeeded, so a failed `aclose()` can be retried.
  Construction is synchronous, so no nested event loops are ever run; cleanup only happens
  in `aclose()`.

An explicit constructor flag adds the G10 workspace preflight before any of the above:

`GroupComposition(..., workspace_check=True)` — opt-in, default `False` (fully backward
compatible — the default behavior and every existing test are unchanged). When enabled,
after member validation but **before** any binding, plugin load or engine construction (a
one-member group is checked too), the factory validates the **effective cwd** every member
will actually get — the seat's own folder when it exists, else the injected `cwd`, else the
process cwd, exactly the way `CoreEngine` resolves it, but pre-construction and without
touching any engine's `cwd`. It rejects a relative effective cwd and any two members that
share a folder, nest inside each other or sit in one git working tree (nested worktrees
included). Failures raise `CompositionError` (the validator's `WorkspaceError` is mapped)
before anything is allocated. This is a one-shot preflight, not a lock or sandbox — see
`docs/SWARM-WORKSPACES.md` and `exo.core.group_workspace.validate_workspaces` for what is
and is not checked.

## GroupPlugins wiring

`GroupComposition(..., plugins=GroupPlugins(...))` is optional. When omitted (or defaulted)
the factory constructs `GroupPlugins()` itself, which means: no directories, empty
allow-list, no config, and default memory/context choices. Every member engine still gets a
built `PluginManager`, but with nothing to load, so it behaves like before this field was
added.

Fields and their exact semantics:

- `dirs` — the ONLY plugin folders ever scanned. No entry points, no default plugin
  folders under `home`, nothing implicit. Each folder must contain a `plugin.yaml`
  manifest, discovered exactly the way a single-seat compose discovers plugins.
- `enabled` — the allow-list actually passed to `load_all(enabled=...)`.
  - A plain tuple/list, including the empty tuple `()`, is used literally: nothing loads
    with the empty default, so injecting `dirs` alone never activates a plugin.
  - The special value `None` is an explicit host opt-in to "every plugin found in the
    injected `dirs`". On a seat whose own `[plugins] enabled` value is set, the seat list
    still wins; `None` only applies to seats with no per-seat list. This is NOT "all
    installed plugins on the machine" and plugin entry points are never consulted.
  - Per-seat enabled lists always override this default. `seat.plugins` equal to `()`
    disables plugins for that seat even when `plugins.enabled` would allow some.
- `config` — a mapping of plugin name to its config dict. This object is deep-copied once
  per member before the seat's exo-memory identity is merged in, so two members never
  share a mutable dict by accident.
- `memory_choice`, `context_choice` — passed to `PluginManager` unchanged for every
  member, exactly as a single-seat compose would.
- `skill_folders` — extra per-seat skill folders, combined (seat folders first, then these)
  with the bundled skill pack directory and handed to `CoreEngine(skill_folders=...)`.
- `executor`, `executor_required` — forwarded unchanged to `PluginManager` and to
  `CoreEngine`. `executor_required=True` is enforced by the engine itself, the same as in a
  single-seat compose; no composition-level code weakens or substitutes it.

### Per-member exo-memory binding

Before any plugin is loaded or any engine is constructed, each member's plugin config is
built and validated like this:

1. start from a deep copy of `plugins.config`;
2. set `exo-memory.exo_home` to the composition's `home` (string form) so the member
   writes into the shared session DB home;
3. set `exo-memory.agent_id` to the seat name;
4. if the seat has a `principal`, set `exo-memory.principal`;
5. if the seat has a `principal_store`, it must be an absolute path (after `expanduser`);
   a relative path raises `CompositionError` before anything is built. The absolute string
   is set as `exo-memory.store_root`.

Important: `principal` and `store_root` are only set when the seat itself carries them.
If the seat has no `principal` / `principal_store` and the host's `plugins.config` already
contains an explicit `exo-memory.principal` / `store_root`, the seat does NOT override it;
the host's explicit value is what the member inherits. Do not read this as "every member
automatically gets its own isolated memory store": members always share `home`, and
`store_root` separation only happens for seats that actually name their own absolute
principal store path.

All `principal_store` validation for every member happens before a single plugin is loaded
or a single engine is constructed, so a bad seat fails closed with no partial construction
(the constructor and `__call__` keep the validate-before-build guarantee described above).

### Executor

`GroupPlugins.executor` and `executor_required` are preserved as-is on their way to
`CoreEngine`. The required-executor behavior tests in this repo cover the composition
passing the pair through; enforcement itself already lives in `CoreEngine`, unchanged from
a single-seat compose.

### Skills

For each member, the skill folder list is `seat.skill_folders`, then
`plugins.skill_folders`, then the bundled pack directory (`exo.core.skills.pack_dir()`,
skipped when it returns nothing). The combined list is passed to
`CoreEngine(skill_folders=...)` exactly once per member.

## Limitations

- **Proven by tests only, not by live runs.** 18 focused tests exercise the wiring logic,
  but no test (or manual run) actually retrieves anything through a real memory backend on
  disk, or does a real end-to-end round trip against a live provider through a
  group-composed engine. The wiring is unit-tested in isolation; treat "the memory config
  bound here is what a live run would use" as unverified until exercised for real.
- **Plugin MCP not wired (tools, commands and channels are).** A plugin that registers
  commands or tools through the loaded `PluginManager` reaches the runtime through
  `CoreEngine`'s standard bridges (registry, command list, `start_channels`), the same as a
  single-seat compose. Plugins cannot register MCP servers: MCP still comes only from each
  seat's own `mcp` configuration (deep-copied into the engine, as before).
- **Tool/command behavior proven by fixture only.** The focused tests load real fixture
  plugins and observe their registered command names; live tool execution, channel start
  and compat with third-party plugins are not exercised here.

## Trusted Python caller wiring: `compose.create(..., group_host=...)`

A trusted Python caller (never config, a protocol command, or a model tool) may bind one
explicitly-constructed `GroupHost` into the single-seat compose path:

```python
from exo import compose
from exo.core.group_host import GroupHost
from exo.core.group_composition import GroupComposition
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore

store = GroupStore(SessionStore(str(home / "sessions.sqlite")))  # MUST be home's own session DB
comp = GroupComposition(profiles, owner="default", home=home, models=models,
                        cwd=cwd, env={}, plugins=None)            # the engine factory
host = GroupHost(store, owner="default", known_profiles={"lead", "one", "two"},
                 engine_factory=comp)
engine = compose.create(group_host=host)  # binds host as engine._group_host
```

Exact signatures (no other wiring exists — no authorization inference, no client wiring,
no auto-discovery):

- `GroupStore(sessions: SessionStore)` — pass a `SessionStore` whose path is exactly
  `<home>/sessions.sqlite`.
- `GroupHost(groups: GroupStore, *, owner: str, known_profiles: Collection[str],
  engine_factory: EngineFactory)` where `EngineFactory = Callable[[SavedGroup],
  Mapping[str, object]]` — typically a `GroupComposition` instance.
- `GroupComposition(profiles: Mapping[str, Seat], *, owner: str,
  home: str | os.PathLike, models: dict | None = None, cwd: str | None = None,
  env: Mapping[str, str] | None = None, plugins: GroupPlugins | None = None,
  workspace_check: bool = False)`.
- `compose.create(seat: str | None = None, *, group_host: GroupHost | None = None)`.

Guarantees of the binding:

- **Validated before any engine exists.** `compose.create` checks — before `PluginManager`
  runs or `CoreEngine` is constructed — that `group_host` is a `GroupHost`, that
  `host.owner` equals the engine's agent (the seat name, or `"default"` without a seat),
  and that the host's `GroupStore` wraps `<home>/sessions.sqlite`. Any mismatch raises
  `HostError` and nothing is created.
- **Closed first.** `CoreEngine.close()` awaits `host.aclose()` before releasing any
  owner resource (jobs target, writer lock, channels, peers, scheduler, MCP, store). A
  host failure or caller cancellation propagates out of `close()` and all owner resources
  stay available for an explicit retry.
- **Host lifecycle stays with the caller.** `host.create(mode=..., members=...,
  conductor=None)` and `start/stop/pause` are unchanged; the compose path never drives
  them.

## Example

```python
from exo.core import seats
from exo.core.group_composition import GroupComposition, GroupPlugins

mapping = {p: seats.parse({"seat": {"name": p}}) for p in ("lead", "one", "two")}
plugs = GroupPlugins(
    dirs=("/srv/exo/plugins",),        # the only folders ever scanned
    enabled=None,                      # opt in to every plugin found in dirs
    config={"my-plugin": {"key": "value"}},
    memory_choice="exo-memory",
    executor=my_executor,
    executor_required=True,
)
comp = GroupComposition(mapping, owner="host", home=home, models=models, cwd=cwd, env={},
                        plugins=plugs)
engines = comp(saved_group)      # GroupHost passes this as its engine_factory
...                              # each member inherits a deep copy of plugins.config
await comp.aclose()
```

Members with a seat-level `[plugins] enabled` list use that list instead of `enabled=None`;
seats without one inherit the injected default.
