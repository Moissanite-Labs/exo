# 0005 Auxiliary routing hook (spec, not yet a PR)

Status: spec only. No upstream PR is open. Open it upstream before Exo carries it as a patch (D-005).
Source issues: [#119778](https://github.com/NousResearch/hermes-agent/issues/119778) (budget-aware
downshift) and [#119780](https://github.com/NousResearch/hermes-agent/issues/119780) (failure-triggered
escalation with a sticky lock). Both are open. Line numbers below are from tag v0.21.6.

## Problem

Auxiliary LLM calls (titling, compression, vision, MoA, approval, plugin tasks) pick their route from
static config: explicit call arguments, then `auxiliary.<task>.*`, then auto. A plugin can watch these
calls (`pre_auxiliary_call` and `post_auxiliary_call`) but cannot change where they go. The
`llm_request` middleware only runs on the main turn (`agent/turn_api_request.py:143`), not on
auxiliary calls.

So any per-call routing policy must be a core change: spend downshift (#119778), sticky escalation
(#119780), quota-aware seat routing, or a cheap model for low-stakes tasks. Each of those would be its
own core PR on `agent/auxiliary_client.py`, a file touched about 170 times a month.

## The hook

One new request-middleware kind, `auxiliary_route`, in the existing middleware vocabulary. No new
`register_*` method. A plugin registers it the way it registers any middleware:

```python
def register(ctx):
    ctx.register_middleware("auxiliary_route", choose_route)

def choose_route(*, route, task, explicit, session_id, turn_id, platform, **_):
    # route: {"provider", "model", "base_url", "api_mode"} as Hermes resolved it
    # explicit: set of route keys the caller passed directly (for example {"model"} from MoA)
    if task == "title_generation":
        return {"route": {"provider": "openrouter", "model": "some/cheap-model"},
                "reason": "cheap model for titles"}
    return None  # keep Hermes's choice
```

Contract:

- Called once per logical auxiliary call, before the client is built. Not called again for retries
  or for configured fallback entries. Fallbacks stay under config control.
- Input: the resolved route, the task key, which keys the caller set explicitly, and the parent
  turn identity (`session_id`, `task_id`, `turn_id`, `platform`, the same fields
  `agent/auxiliary_hooks.py::_parent_turn_identity` already computes).
- Output: `None` keeps the route. `{"route": {...}, "reason": str}` replaces it. Allowed keys:
  `provider`, `model`, `base_url`, `api_mode`, `reasoning_effort`. Other keys are ignored.
- No credentials. A plugin cannot pass `api_key`. If the plugin changes `provider` or `base_url`,
  the configured `base_url` and `api_key` are dropped and Hermes resolves credentials for the new
  provider as usual. This mirrors the `_AUX_ROUTE_KEYS` rule in `agent/auxiliary_task_config.py`,
  so one vendor's key never goes to another vendor's endpoint.
- Several plugins chain in registration order (same as `llm_request`). The last change wins.
- Fail-open. A raising callback is logged and the route is unchanged.
- The change is recorded: the existing `route_info` and the `pre_auxiliary_call` payload gain
  `route_source` and `route_reason`, so observers see that a plugin moved the call.
- Zero cost when unused: `has_middleware("auxiliary_route")` short-circuits, as
  `apply_llm_request_middleware` does today.

## Call sites

One seam covers every auxiliary path. Rename the body of
`agent/auxiliary_client.py::_resolve_task_provider_model` (line 6174, six return points) to
`_resolve_task_provider_model_base`, and make `_resolve_task_provider_model` a thin wrapper that calls
the base and then `apply_auxiliary_route_middleware`. The name stays, so existing tests that patch it
keep working. Callers that then get the hook for free:

| Caller | Line | Path |
|---|---|---|
| `_prepare_aux_request` | 7493 | shared head of `call_llm` (8027) and `async_call_llm` (8324), the funnel for almost all tasks |
| `get_text_auxiliary_client` | 5516 | callers that build their own client |
| `resolve_vision_provider_client` | 5699 | vision |
| `agent/conversation_compression.py` | 2059 | compression provider probe, stays consistent with the real call |

New code:

- `hermes_cli/middleware.py`: `AUXILIARY_ROUTE_MIDDLEWARE = "auxiliary_route"`, added to
  `VALID_MIDDLEWARE` (line 24), and `apply_auxiliary_route_middleware(route, **context)` built on the
  existing `_apply_request_chain` with payload key `"route"`, plus the key filter and credential rule.
- `agent/auxiliary_client.py`: the wrapper above, about 25 lines.
- Tests: no middleware means identical routes; a change is applied and traced; `api_key` is refused;
  provider change drops configured credentials; a raising callback fails open; retries and
  fallbacks do not re-invoke it.
- Docs: one section in the plugin hooks guide.

Estimated diff: about 80 lines of code and 150 lines of tests.

## Why this shrinks the queue

With this hook, #119778 and #119780 become plugin logic (Exo's `exo-router`), not core PRs. It also
removes the reason to carry other routing patches later, such as busy-turn routing (#120840). If
upstream later adds the same seam for main-turn routing, the same plugin works there.

## Draft upstream PR body

Title: `feat(plugins): auxiliary_route middleware so plugins can pick the model per auxiliary call`

> Plugins can watch auxiliary LLM calls today through `pre_auxiliary_call` and `post_auxiliary_call`,
> but they cannot change where a call goes. Every per-call routing idea ends up as a core change to
> `agent/auxiliary_client.py`. Two open issues are examples: a spend-based downshift (#119778) and a
> sticky escalation after repeated failures (#119780).
>
> This PR adds one request-middleware kind, `auxiliary_route`, next to `llm_request` and
> `tool_request`. A plugin registers it with `ctx.register_middleware("auxiliary_route", fn)`. Hermes
> calls it once per auxiliary call, after it resolves the route from arguments and config and before
> it builds the client. The callback gets the resolved route, the task key, which keys the caller set
> explicitly, and the parent turn identity. It returns `None` to keep the route, or a new
> `provider`, `model`, `base_url`, `api_mode` or `reasoning_effort`.
>
> Safety:
> - Plugins never see or set API keys. If a plugin changes the provider or base URL, the configured
>   key and base URL are dropped and Hermes resolves credentials for the new provider, the same rule
>   inherited auxiliary tasks already follow.
> - Retries and configured fallbacks are not re-routed. They stay under config control.
> - A failing callback is logged and ignored.
> - With no middleware registered, the code path returns at once and behaviour is unchanged.
>
> The route change is recorded in `route_info` and in the `pre_auxiliary_call` payload
> (`route_source`, `route_reason`), so observability plugins can see it.
>
> Implementation: `_resolve_task_provider_model` becomes a thin wrapper around the existing logic and
> applies the middleware. Every auxiliary path already goes through it (`call_llm`,
> `async_call_llm`, `get_text_auxiliary_client`, `resolve_vision_provider_client`, the compression
> probe), so there is one seam and no per-task edits.
>
> Tests cover: no middleware gives identical routes; a change is applied and traced; `api_key` in
> the result is ignored; a provider change drops configured credentials; a raising callback fails
> open; retries and fallbacks do not call the middleware again.
>
> Closes nothing by itself. It lets #119778 and #119780 ship as plugins instead of core changes.
