# Swarm profile composition

`exo.group_composition.GroupComposition` is a synchronous `EngineFactory` for
`GroupHost`: `Callable[[SavedGroup], Mapping[str, CoreEngine]]`. It builds one distinct real
`CoreEngine` per group member.

## Trust model

The host injects everything trusted; the composition reads nothing:

- an **explicit authorized profile -> `Seat` mapping** — the only profiles it can run, each
  bound to the exact `Seat` (the seat's name must equal the profile ID);
- the authenticated **owner** — verified against the stored group;
- `home`, `models`, `cwd`, `env` — the same `home` for every engine, so all members share
  one session DB.

There is **no automatic metadata/config authorization**: no seat files, config.json or
environment are consulted for membership decisions.

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
  An exception raised inside a CoreEngine constructor before it returns cannot
  supply an engine reference for this composition to retain.

## Limitations

- **No seat plugin wiring.** Each engine is built with a fresh, empty `PluginManager()`: no
  plugin folders or entry points are loaded, so plugin-provided **skills, tools, commands,
  channels and memory/context backends are NOT integrated** into member engines. The seat
  is bound exactly, but the composition deliberately does not attempt to discover or load
  per-seat plugins. Wire those at a higher layer (or extend the factory) if a group needs
  them.
- **Tools fail closed.** With no approval-gate hook, built-in tool calls are
  blocked too. Required-executor mode cannot be satisfied by this empty-plugin
  composition and refuses startup. No permission or executor bypass is added.
- **Environment and saved keys remain an open integration question.** This layer
  currently copies `env` into a dictionary; omitted/`None` becomes `{}`. It passes
  an explicit dictionary to each CoreEngine, unlike ordinary `env=None` behavior:
  process provider variables/PATH are not inherited through that mapping, and
  the router's saved-key/keychain lookup is disabled even if the caller supplies
  a copy of the process environment. Some core setup still reads process
  environment directly, so this is not environment isolation. This recovery
  preserves that behavior; the existing review's requested environment contract
  must be decided before claiming live provider compatibility.
- **Explicit cleanup before reuse.** GroupHost does not call this factory's
  `aclose()` in this layer, either after adapter rejection or after a completed
  group. Retained engines keep their writer locks. A subsequent start is refused
  (the repaired host reports fixed `HostError("engine factory failed")`) until
  the trusted caller awaits cleanup. Do not close engines while member work is
  still active: stop/settle it first. Host-owned cleanup is separate work in #1005;
  this layer adds no cleanup ownership or retry policy.
- **A running owner can conflict with a member.** If an existing owner engine on
  the same home holds the writer lock for a member profile, composition refuses
  that profile with a writer conflict. It never borrows or takes over that engine.

## Example

```python
from exo.core import seats
from exo.group_composition import GroupComposition

mapping = {p: seats.parse({"seat": {"name": p}}) for p in ("lead", "one", "two")}
comp = GroupComposition(mapping, owner="host", home=home, models=models, cwd=cwd, env={})
try:
    engines = comp(saved_group)  # Or supply comp as GroupHost's engine_factory.
    ...                         # Stop and settle any member work before closing.
finally:
    await comp.aclose()          # Also required after a partial construction failure.
```
