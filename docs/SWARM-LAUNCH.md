# SWARM-LAUNCH

One-call trusted-Python launch of a swarm group: `exo.group_launch.create`.

```python
from exo.group_launch import create

launch = create(seat=None, profiles=["lead", "one"])   # owner: seat arg, else $EXO_SEAT, else "default"
launch.start()                 # builds the member engines (GroupComposition) and starts the run
await launch.wait()            # settle
await launch.aclose()          # host + owner engine + session store, every part attempted
```

## Inputs, exactly

- `profiles`: the explicit, exclusive membership — at least two unique seat IDs
  (`[a-z0-9][a-z0-9_-]*`), duplicates rejected. Nothing else is ever discovered.
- `seat` / `$EXO_SEAT` / `"default"`: the owner seat, resolved exactly as
  `exo.compose.create` resolves it. The owner may never be a member; this is
  rejected before anything is allocated.
- `plugins`: an optional `exo.core.group_composition.GroupPlugins`. `None` means the
  empty profile — no folders, nothing enabled — so plugins are disabled by default.
  Anything that is not a `GroupPlugins` is rejected.

## Guarantees

- **Validation before allocation.** Profile IDs, owner eligibility and the plugin
  profile are checked before the config is read; named member seats are loaded from
  `$EXO_HOME/seats` (named seats only — no scans) and their declared names checked
  before the session store is opened. A rejected launch creates no `sessions.sqlite`.
- **No config or environment activation.** The module reads the ambient `$EXO_HOME`
  config (as `compose` does) and reads `$EXO_SEAT`; it never writes config, never
  sets environment variables, and never discovers seats, plugins or members
  implicitly. Member engines run `cfg.models()` against the one shared session DB.
- **No event loop in `create`.** Member engines do not exist until
  `GroupHost.start` invokes the factory, so every failure inside `create` needs
  only the synchronous `SessionStore.close()` — `create` is safe to call from a
  running event loop (`asyncio.run` appears nowhere in the module). Async teardown
  is `GroupLaunch.aclose`: await `host.aclose()` (runtimes settle, then the factory
  closes the member engines), then the owner engine, then the store — always, even
  when a part above it failed. The host is always fully settled before the backing
  store is closed: a cancellation of the `aclose()` caller does not abandon the
  host's shielded shared cleanup, it is awaited out to settlement first.
- **`LaunchError` never wraps process control.** Cancellation, `SystemExit` and
  `KeyboardInterrupt` raised while closing propagate from `aclose()` unchanged —
  after every part has been closed. An ordinary close failure that happened first
  stays visible as the chained `__cause__`; it never masks the cancellation. Only
  an ordinary part failure becomes `LaunchError("group launch close failed: …")`.
- **Store-open failures are launch failures.** `SessionStore` creation is sqlite
  work (WAL pragma, schema migration): a locked, corrupt or unwritable
  `sessions.sqlite` surfaces as `LaunchError` with the raw sqlite/OSError chained.
- **Composition stays fail-closed.** The `GroupComposition` factory retains partial
  engines for `aclose()` and never retries or takes over another writer's claim,
  exactly as documented in `docs/SWARM-COMPOSITION.md`.

## Shape

`create` returns a `GroupLaunch`: `.engine` (the owner `CoreEngine`, with the host
injected), `.host` (the `GroupHost`), `.group` (the stored `SavedGroup`, conductor
mode, first profile as conductor), `.members` (member engines, empty until
`start()`), `.start()`, `.wait()` and `aclose()`. `LaunchError` marks any launch
input or close failure; underlying failures chain from it.
