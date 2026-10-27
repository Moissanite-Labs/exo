# Reasoning effort for future main turns

Providers includes **Main model reasoning** when the selected core implements `main-route-reasoning-v1`. It shows the actual core seat and current main route. An older core reports that an upgrade is required and receives no setting change.

Choose a supported effort and select **Save main-route default**. This saves a preference for that exact route in this core's data home and logical seat. It applies to future MAIN conversation turns, across conversations. It does not change the model, an active turn, auxiliary calls, scouts or evaluation roles. A turn snapshots its preference before its first call and keeps it through retries and tool rounds.

**Inherit existing settings** removes the saved preference. It adds no new reasoning field to requests. Existing provider options remain unchanged. No preference file is created merely by opening the view.

## Verified routes

This first version enables `none`, `low`, `medium` and `high` only for direct OpenAI Chat Completions routes using `gpt-5.1` or `gpt-5.1-2025-11-13` at `https://api.openai.com/v1`. These values and transport support are documented on the [GPT-5.1 model page](https://developers.openai.com/api/docs/models/gpt-5.1). The model is marked deprecated there. Controls do not establish current account access or model availability, and this feature does not select it automatically.

Other routes show why controls are unavailable. A model name alone is not a capability contract. Custom gateways and OpenCode Go are not assumed to support the same controls as the direct provider.

Direct DeepSeek documents thinking off and `low`, `high` and `max`, but its [thinking guide](https://api-docs.deepseek.com/guides/thinking_mode/) also requires reasoning replay for tool continuations. Exo's existing [issue #616](https://github.com/aska-digital/exo/issues/616) and PR #656 own that dependency. This feature does not duplicate their work or enable DeepSeek controls before a reviewed replay implementation is integrated.

GPT-6 controls are also outside this first version. The [GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) requires Responses for tool calling, while this control covers the installed Chat Completions transport.

## Requested and sent

The view reports the last main call's requested effort, the option sent to the adapter and the route used. The core records these fields in `model.call` and its attempt metadata. They describe options forwarded to the provider adapter. A transport can still fail validation before making a network request. They do not confirm delivery or measure adaptive computation.

Each fallback attempt checks its own route's verified support. An unsupported fallback receives no new reasoning parameter and is reported as using the provider default. Spend refusals and uncertain stopped calls report that sending was not confirmed. The feature does not expand spend limits, change tool permissions or bypass approvals.

Changing a provider configuration can invalidate an earlier preference. The next turn then inherits rather than forcing an unsupported parameter. Refresh the view before saving again. Returning to a previously selected route restores that route's saved preference if its capability remains supported.

## Authority and persistence

HTTP requests require the existing loopback, Host, Origin and page-token checks. The core independently requires principal authority before reading or changing preferences. The browser cannot choose a different data home or seat. A stale model route is refused rather than saving a setting for the wrong model.

Preferences are nonsecret metadata stored in an owner-only file under `state/reasoning/`. They persist across core and web restarts and changing local web ports. Tokens, API keys and reasoning text are not stored in that preference file.

Unreadable, malformed or oversized preferences never block a main turn. The core uses existing settings and reports a fixed warning without displaying file contents. The view allows an explicit **Reset saved efforts** to clear this seat's saved preferences. A reset requires principal authority and confirmation. Ordinary Save cannot silently overwrite an unavailable preference file.

Tests use disposable homes, fake providers, mocked HTTP and a separate headless browser. No live inference or current runtime settings are changed by verification.
