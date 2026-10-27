# Next local Canary iteration: 2026-10-27

This candidate is staged for the next pilot. It has not been installed or launched. The active Canary, its launcher, credentials and data remain unchanged.

Core acceptance commit: `55a403ade6fd496931f5adc4e9410b40ade71a37`, built on the previously accepted `e2431e1b` Canary. Native shell source: `846974c253e40110dee99cc01eb613e60687418c`, built from the secure-credential shell plus the reviewed pane bridge. The native bundle is configured for attached mode and passes strict signing verification.

## Included changes

| Change | Review units |
| --- | --- |
| Separate core availability from the web connection, preserve last known activity and drafts, retry failed refreshes, allow a deliberate Stop attempt during event-feed loss | [Core 699](https://github.com/aska-digital/exo/pull/699), [desktop 62](https://github.com/aska-digital/exo-desktop/pull/62) |
| Hide, resize and rearrange the three Chat panes, retain existing composer and approval nodes, persist validated layout metadata through the native bridge | [Core 701](https://github.com/aska-digital/exo/pull/701), [desktop 64](https://github.com/aska-digital/exo-desktop/pull/64) |
| Save supported reasoning effort for future MAIN turns in this core and seat, with explicit inheritance and requested versus forwarded metadata | [Core 700](https://github.com/aska-digital/exo/pull/700), [desktop 63](https://github.com/aska-digital/exo-desktop/pull/63) |
| Preserve the context-capacity correction from the parallel desktop work | [Core 697](https://github.com/aska-digital/exo/pull/697), [desktop 61](https://github.com/aska-digital/exo-desktop/pull/61) |

The earlier secure credentials, searchable models, approval shortcuts and replay fixes are retained. The native bundle contains no credential values. Layout preferences contain no conversation, draft or page token.

## Verification

| Check | Result |
| --- | --- |
| Complete Command web suite on the final composition | 358 passed, zero skips |
| Focused core reasoning, GitHub auth, vault, shell and jobs checks after pane integration | 136 passed |
| Independent final checks across credentials, reasoning, approvals, recovery, picker, context and panes | 95 passed |
| Native origin/schema test using a disposable preferences suite | 1 passed |
| Native build and strict signing verification | Passed |

The final independent review binds tree `afe2bc2096bc3ac8c2eeee3423ccabc3c6b02d1e`, now committed as `55a403ad`. Two old test assumptions were updated: composer position is measured within the conversation below the pane toolbar, and the storage guard explicitly permits only validated pane metadata. The guard still rejects arbitrary browser storage elsewhere.

Disposable wide and narrow browser screenshots were inspected. Native app acceptance, an actual app relaunch and hosted CI are not established by these checks. No real external credentials or live inference settings were changed.

## Remaining limits

Reasoning controls currently enable only the verified direct OpenAI GPT-5.1 Chat Completions routes. OpenCode Go forwarding is unverified. Direct DeepSeek remains dependent on [reasoning replay](https://github.com/aska-digital/exo/pull/656). The current DeepSeek route must show that controls are unavailable rather than inventing support.

The reasoning PR remains a draft under D-077. Its unchanged size check fails on its base at 15,828 counted lines against a 15,824 ceiling, and its feature tree reaches 16,000. A reduction or recorded feature decision is required before landing. No budget was raised.

The desktop pane branch separately inherits the picker CSS failure tracked in [issue 45](https://github.com/aska-digital/exo-desktop/issues/45). This composition already includes the existing token correction. The broader activity and cancellation display work in desktop issue 37 remains open, as does the unknown old-core exit cause in core issue 694.

GitHub host-mode environment handling in desktop issue 50 is tracked as a separate follow-up. No host mode was enabled. A separate account and a real token still require the user's setup through Credentials; no account or token was provisioned by this iteration.
