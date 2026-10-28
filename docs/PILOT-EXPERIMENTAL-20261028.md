# Experimental desktop pilot, 2026-10-28

This local test build composes the reviewed Full Access and second-pilot core with the latest account isolation, route-warning, chat rename, context-estimate and model-picker fixes. It keeps the existing canary store and credentials. The app attaches to that core and does not own its lifetime.

## Included behavior

- Visible Rename controls use an in-page editor and preserve the selected conversation and draft.
- Route warnings belong to their conversation and clear after the matching call succeeds.
- Next-call estimates, planned model, known capacity and last-call usage have separate labels. Pending or failed reports cannot reuse an old utilization gauge. Late reports cannot cross request or conversation boundaries.
- Saving a provider default refreshes its header and provider row while preserving model search, focus and caret.
- Pane controls hide, move and resize the Chat panes. The native bridge persists preferences between launches.
- Reasoning controls expose supported provider options. This build does not change the user's selected models or effort settings.
- GitHub token mode blocks ordinary host-account fallback when token variables are removed. Credentials remain in their existing secure store.
- Full Access audits redact raw values before JSON encoding. Terminal restarts do not regrant Full Access.
- Internal steer messages retain their identity before they enter the queue.

## Source coordinates

Core composition: branch `alpha/experimental-20261028` in `aska-digital/exo`. The native app is built from `a7f9b528a5e835ab36c38f4453cae5f8388ed3e4` in `aska-digital/exo-desktop`, with an attached launcher pointing to the pinned experimental core checkout. This is a local source build, not a portable package or a main-branch release.

The source changes remain in their feature PRs: account isolation #734, route warnings #735, memory display #736, rename #737 and model search #673. This composition does not close their parent integration work or replace independent review of a new head.

## Verification and limits

The composed core passes 533 credential, permission, reasoning, loop, protocol and restart checks. Two platform-dependent cases are skipped. The full Command plugin suite passes 390 tests without skips, including disposable browser tests. Native Swift typecheck, optimized arm64 compilation, launcher fixtures, pane tests and signing verification pass.

The repository's architecture limit remains unresolved. Applying the accepted main-branch dependency counter to this core reports 44 inward product imports against the existing limit of 39, and 8 private-state reads against 8. No limit or guard is weakened. The inherited line-budget test predates the decision to remove the line ceiling. This pilot is not cleared for a main merge, and hosted CI is not claimed.

Installation requires an externally controlled idle handoff. Save a private store backup and message hashes, verify every conversation and job is idle, stop the exact old core, then start the new source. Before opening the app, verify the old messages, route order, credential binding and latest permission scope. Never send a continuation or model request as part of installation.
