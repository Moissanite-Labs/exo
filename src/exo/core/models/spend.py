"""Core adapter: bind routed providers to the public model-spend accounting boundary."""
from __future__ import annotations

from typing import Any

from exo.model_spend import NATIVE_BOUNDS, SpendDenied, SpendJournal, SpendStopped
from exo.model_spend.billing import SCALE, positive_tokens


async def admitted_stream(journal: SpendJournal, route: str, provider: Any,
                          messages: list, tools: list, opts: dict):
    """Strict adapter boundary. Refusal never enters a provider or executes a tool."""
    from exo.core.models.anthropic import AnthropicProvider
    from exo.core.models.openai_chat import OpenAIChatProvider
    from exo.core.models.fake import RuleFake
    from exo.core.models.base import error_event

    token, settled = None, False
    stream: Any = None
    try:
        journal.check()
        if type(provider) is RuleFake:
            # Exact installed implementation only, never a provider label/local flag/subclass.
            stream = RuleFake.stream(provider, messages, tools, **opts)
            async for event in stream:
                event.route = route
                if event.usage is not None:
                    event.usage.cost_usd = 0.0
                yield event
            return
        price = journal.prices.get(route)
        if price is None:
            raise SpendDenied(f"no pinned model-spend contract for {route}")
        bounds = NATIVE_BOUNDS.get((price.transport, price.base_url, price.model))
        if bounds is None:
            raise SpendDenied("model/endpoint lacks an audited native token ceiling")
        if price.input_bound < bounds[0] or price.max_output > bounds[1]:
            raise SpendDenied("policy must reserve the full native context and obey its output ceiling")
        expected = AnthropicProvider if price.transport == "anthropic" else OpenAIChatProvider
        if type(provider) is not expected or provider.base_url != price.base_url:
            raise SpendDenied("model-spend contract does not cover this transport or endpoint")
        if provider.extra_headers or provider.session_header:
            raise SpendDenied("custom or beta headers are outside the audited native model contract")
        if any(part.type == "image" for message in messages
               for part in [*message.content, *(p for r in message.tool_results for p in r.content)]):
            raise SpendDenied("image billing is outside the pinned text model-spend contract")
        output = positive_tokens(opts.get("max_tokens", price.max_output))
        if output > price.max_output:
            raise SpendDenied("requested output exceeds the pinned model-spend contract")
        body = provider.build_body(messages, tools, **{**opts, "max_tokens": output})
        limit = body.get("max_completion_tokens", body.get("max_tokens"))
        if body.get("model") != price.model or limit != output:
            raise SpendDenied("prepared request does not match the pinned model-spend contract")
        token = journal.reserve(route, str(opts.get("session_id") or ""), price.maximum(output), price)
        stream = provider.stream_prepared(body, session_id=opts.get("session_id"), require_usage=True)
        async for event in stream:
            event.route = route
            if event.kind == "error":
                raise SpendDenied("model call failed with uncertain spend; reservation retained")
            if event.kind == "done":
                if event.usage is None:
                    raise SpendDenied("missing final usage; reservation retained")
                actual = price.actual(event.usage, output, provider.input_includes_cache
                                      if hasattr(provider, "input_includes_cache") else False)
                journal.settle(token, actual)
                settled = True
                event.usage.cost_usd = actual / SCALE
                event.attempts = [{"route": route, "outcome": "ok"}]
                if event.message is not None:
                    event.message.meta.setdefault("model", route)
                yield event
                return
            # Do not expose untrusted cost estimates or incomplete token totals as settled spend.
            if event.kind != "usage":
                yield event
        raise SpendDenied("model stream ended without final usage; reservation retained")
    except Exception as exc:
        message = str(exc) if isinstance(exc, SpendDenied) else "model spend boundary failed closed"
        uncertain = token is not None and not settled
        stopped = uncertain or isinstance(exc, SpendStopped)
        if uncertain:
            journal.freeze_uncertain(message)
        label = "Model spend stopped" if stopped else "Model request refused"
        event = error_event(f"{label}: {message}. Ask the principal to review the policy/journal; "
                            "approval replies cannot raise the cap.", None, retryable=False)
        event.route = route
        event.attempts = [{"route": route, "outcome": "spend_stopped" if stopped else "spend_refused"}]
        yield event
    finally:
        if token is not None and not settled:
            # Cancellation, failed accounting, and unknown network outcomes require explicit reconciliation.
            journal.freeze_uncertain("uncertain model spend; ask the principal to reconcile retained reservations")
        if stream is not None:
            await stream.aclose()
