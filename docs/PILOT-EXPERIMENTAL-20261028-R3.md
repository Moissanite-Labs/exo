# Experimental desktop pilot, revision three

This local test build extends revision two (`9fab4f94`) with the reviewed ChatGPT, Meta and DeepSeek
provider implementations. Shared changes remain canonical in Exo. It is an experimental composition,
not a main release or a desktop sync approval.

## Provider changes

- [ChatGPT and Meta change](https://github.com/aska-digital/exo/pull/813): ChatGPT subscription connection through the
  official open-source sign-in flow, OS credential storage, account-specific models, rotating-token
  refresh and principal-only connection controls. Subscription-selected requests cannot silently use
  paid API-key fallback routes. Meta Model API uses the shared Responses transport.
  Canonical source `190b5fbb` plus client-ID compatibility correction `b70218f0`.
- [DeepSeek change](https://github.com/aska-digital/exo/pull/816), source `fc9f43a6`: direct DeepSeek API access,
  complete assistant reasoning history for tool continuations and documented thinking controls.

The ChatGPT connection is created by the person's browser sign-in. Existing Hermes and Codex credentials
are not imported. Meta and DeepSeek use the existing masked key setup. A connection does not change a
default role or model automatically. No real provider credential or model request is part of build checks.

The runtime includes the optional `chatgpt` dependency extra. Base Exo dependencies remain unchanged.
The native executable is unchanged from the prior alpha, source `a7f9b528`; only its launcher target and
bundle version move to `0.1.0-experimental.3`. The desktop remains attached to an independently started core.

## Composition and checks

The provider patches retain revision two's searchable model picker, reasoning controls, pane controls,
GitHub credential binding, redaction, permission scope and uncapped-turn support. Merge resolutions keep
both provider transports in model discovery and the existing picker and reasoning lifecycle. Incidental
documentation about later main features is excluded.

Canonical source and the combined provider tree receive independent review. Checks cover authentication,
streamed tools, reasoning replay, failed and interrupted responses, account changes, old-account outcomes,
provider discovery, existing core behavior and the web UI. Real account eligibility, sign-in and live
provider tool execution remain acceptance steps for the person testing the build.

The inherited architecture limit remains unresolved: 44 inward product imports against 39, and eight
private-state reads against eight. No limit is raised and no spending or permission guard is weakened.
The existing protected spending contract still refuses Responses until separately supported.

## Local handoff

Before installing, preserve the current database, per-message hashes, session inventory, settings and
credential-binding metadata. Require an idle core, no approvals, no queued messages and no running jobs.
Close the empty composer window and recheck the exact old process, socket, model routes and permission
scope immediately before the external restart.

Verify all prior messages and session identities after starting the candidate. Restore only the captured
model route order and permission scope. Preserve the previous source, app and launcher for rollback. Open
Providers for the next sign-in step; do not send a model turn or continue an agent as an installation check.
