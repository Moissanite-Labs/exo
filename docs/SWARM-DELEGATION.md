# Swarm delegation (D02): named routes active

Named delegation routes are active in this slice. A parent may ask a child to run on one configured
model route, `delegation.routes.routes.<name>`, instead of a tier. A route pins the model exactly.
The child streams on that one route only: no tier chain, no fallback, and no escalation when the
child fails.

## Config

Under the top-level `delegation:` key of the config file:

```yaml
delegation:
  routes:
    backend: model_map          # "model_map" or "default"
    routes:
      cheap: "provider:model"   # name -> "provider:model" ref
```

- The block nests as `delegation.routes = {backend, routes: {name: "provider:model"}}`.
- `backend: default` (also when the block is missing) keeps today's delegation behavior: tiers and
  the parent's own model. It takes no routes. A route map beside a `default` backend is rejected as
  dead configuration.
- `backend: model_map` requires a non-empty `routes` map. A bare `routes` map implies it.
- Route names start with a letter, are at most 32 characters, and may not collide with the reserved
  roles (`main`, `aux`, `delegate`, `decision`, `eval`) or the delegate tiers (`fast`, `standard`,
  `deep`). Refs must be `"provider:model"` strings. Unknown keys, including `profile_routes`, are
  rejected rather than silently ignored.

## Active behavior

- **Either route or tier.** A delegate task may carry `route` or `tier`, never both. They are
  rejected together before any child starts.
- **Exact pin.** With a route, the child runs on exactly that route's `"provider:model"`, pinned
  via `Router.provider_for(ref)` / `Router.pinned(ref, role="delegate")`. There is no fallback to
  any other route. A failed route child is not retried one tier up and is not escalated (ordinary
  tier children are retried once up on failure when `escalate_on_fail` allows).
- **Never above the parent.** The route's own tier (the tier its model is ranked on) must be at or
  below the parent's tier, and the parent's tier must be known. This holds even when
  `allow_upgrade` is on: upgrade only ever relaxes *tier* requests, while a route picks one exact
  model that must still respect the no-upgrade cap.
- **Validation before any task row.** Each of these rejects the delegate call before any task or
  brief row exists:
  - an unknown route name
  - a ref that is not `"provider:model"`, whose provider is unknown, or that may not serve the
    delegate role
  - a route whose model is ranked on no known tier, so it cannot be verified safe
  - a parent whose own tier is unknown
  - a route above the parent's tier
- **Remote seats unsupported.** A remote (seat) delegation cannot carry a named route. The parent
  must run routed work locally.

## What is built (this slice)

- `src/exo/core/agents/route_config.py` (the pure config parser, credential-free and
  network-free): validates the block and returns an immutable mapping of route names to refs, and
  rejects reserved names and unknown keys. `Delegator` reads it at runtime.
- Router helpers: `Router.pinned(ref, role="delegate")`, `Router.provider_for(ref)`, and
  `Router.stream(..., _exact="provider:model")`. These pin exactly and never fall back, while
  ordinary credential rotation, cooldowns, circuit breaker and spend guard still apply.
- `src/exo/core/agents/delegate.py`: `Delegator` reads the block at init, validates route and tier
  choices in the tool spec (either-or, route-name checks via the parser, tier-ceiling checks), and
  streams the child on the pinned provider.
- Wire (`src/exo/wire/packets.py`, `src/exo/wire/grammar.py`): `Brief` and `Receipt` carry the
  optional `route` field as opcode 117 (`asked_route`). Route-less packets keep their old bytes, so
  peers on the older vocabulary are unaffected.

## What is deliberately NOT built (later slices)

- `profile_routes` (the profile-scoped variant): recognized and rejected, not silently ignored.
- Executing a named route on a remote seat over the bus.
- Whole-Swarm completeness: this slice makes named delegation routes *run*. Other D02 surface
  (profiles, remote routed work, additional backends) is out of scope here.

## Tests

- `tests/agents/test_route_config.py`: parser defaults, model_map, immutability, rejections.
- `tests/agents/test_wire.py`: route round-trip and the old-bytes guarantee for route-less packets.
- `tests/agents/test_delegate_routes.py`: runtime. A routed child runs on the named model and fails
  once without escalation, and every invalid case (route + tier, unknown route, unranked model,
  above-parent route, unknown parent tier, remote + route) rejects before any task row exists.
