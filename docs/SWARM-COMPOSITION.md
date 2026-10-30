# Swarm profile composition

`exo.core.group_composition.GroupComposition` is a synchronous `EngineFactory` for
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

## Limitations

- **No seat plugin wiring.** Each engine is built with a fresh, empty `PluginManager()`: no
  plugin folders or entry points are loaded, so plugin-provided **skills, tools, commands,
  channels and memory/context backends are NOT integrated** into member engines. The seat
  is bound exactly, but the composition deliberately does not attempt to discover or load
  per-seat plugins. Wire those at a higher layer (or extend the factory) if a group needs
  them.

## Example

```python
from exo.core import seats
from exo.core.group_composition import GroupComposition

mapping = {p: seats.parse({"seat": {"name": p}}) for p in ("lead", "one", "two")}
comp = GroupComposition(mapping, owner="host", home=home, models=models, cwd=cwd, env={})
engines = comp(saved_group)      # GroupHost passes this as its engine_factory
...
await comp.aclose()
```
