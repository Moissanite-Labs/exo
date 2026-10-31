# Swarm delegation (D02) — INCOMPLETE, configuration and wire only

Named delegation routes: the parent may ask a child to run on one configured model route,
`delegation.routes.<name>`, instead of a tier.

**This is not an active feature.** What exists today is a configuration parser and the wire
carrying a name; nothing resolves, routes, or activates a route at runtime.

## What is built (this slice)

- **`src/exo/core/agents/route_config.py`** — a pure configuration parser, independent of any
  runtime. It validates the shape of the `delegation.routes` block and returns an immutable
  mapping of route names to `"provider:model"` refs.
  - `backend = "default"` (the default, also when the block is missing): tiers and the parent's
    own model, exactly as delegation behaves today. Takes no routes — a route map beside it is
    rejected as dead configuration.
  - `backend = "model_map"`: needs a non-empty `routes` map; `routes` alone implies it.
  - Route names are structural strings, validated only for shape: they start with a letter, are
    at most 32 characters, and may not collide with the reserved roles (`main`, `aux`,
    `delegate`, `decision`, `eval`) or the delegate tiers (`fast`, `standard`, `deep`).
  - Refs must be `"provider:model"` strings (shape only).
  - Unknown keys, including `profile_routes`, are rejected rather than silently ignored.
  - No router validation, no credential lookup, no provider construction, no `Delegator`
    integration: the parser answers the same on a machine with no keys and no network.
- **Wire** (`src/exo/wire/packets.py`, `src/exo/wire/grammar.py`): `Brief` and `Receipt` carry an
  optional `route` field, encoded as opcode 117 (`asked_route`). Route-less packets keep their old
  bytes exactly, so peers on the older vocabulary are unaffected. The wire carries the name;
  nothing on the wire resolves it.

## Runtime prerequisite (router helpers, D02 not yet active)

The router side now has the primitives the later activation slice will build on, but nothing calls
them outside the router's own tests:

- **`Router.stream(..., _exact="provider:model")`** (private, optional): pins the call to the one
  configured route the ref names. The chain is that route alone — no fallback to any other route,
  in this role or another — while the ordinary credential rotation, cooldowns, circuit breaker and
  spend guard of this Router all still apply. A bad ref raises `ValueError` before anything is
  streamed.
- **`Router.provider_for(ref, role="delegate")`**: a `RoleProvider` for one route on this same
  Router (one shared cooldown, breaker and spend state; default `provider()` is unchanged). It
  validates the ref up front — shape, known provider, and the provider's `roles` table permitting
  `role` — and raises `ValueError` before any credential is read.
- Helpers (`_exhausted`, `_chain_cooldowns`, `_refused`) now scope their wait hints and refusals to
  the chain the call actually walked, so a pinned failure speaks only for its route.

## What is deliberately NOT built (later slices)

- No runtime activation: no code path reads `route_config.py` yet, and delegation itself does not
  use the router helpers above; `Delegator` and the child loop ignore the `route` field.
- No resolution, fallback, or profile_routes behavior for a failed route; `provider_for` is not
  called by any delegation path.

D02 is incomplete until that runtime slice lands. Until then, setting `delegation.routes` in a
config file parses (see `tests/agents/test_route_config.py`) and does nothing else.

## Tests

`tests/agents/test_route_config.py` (parser: defaults, model_map, immutability, rejections) and
`tests/agents/test_wire.py` (route round-trip, old-bytes guarantee for route-less packets).
