# Experimental desktop pilot, revision four

This source-only composition starts from revision three plus the reviewed ChatGPT credential-save fix
(`b1477f4e`). It is an experimental candidate, not a main release, downstream sync or installation receipt.

## Included changes

| Canonical change | Source pin | Behavior |
| --- | --- | --- |
| [ChatGPT credential save, #871](https://github.com/aska-digital/exo/pull/871) | `a5de2dbc` carried by `b1477f4e` | Native macOS credential reads, writes and deletion, with noninteractive authorization and a versioned account namespace. |
| [Core shutdown, #874](https://github.com/aska-digital/exo/pull/874) | `92b58c56` | Drain owned turns before storage cleanup and retain accepted input. |
| [Bash arguments, #877](https://github.com/aska-digital/exo/pull/877) | `6d1a74cf` | Reject unknown arguments instead of silently treating a misspelled background option as foreground work. |
| [Summary waits, #879](https://github.com/aska-digital/exo/pull/879) | `7cccbc14` | Avoid repeating the timed overflow-settle wait on every round of an identified turn. |

The composition preserves R3's full-access grants, uncapped-turn support, reasoning controls, provider
routes, GitHub credential binding, redaction, model picker and pane controls. Shutdown conflict resolutions
retain both the existing `max_rounds` forwarding and unlimited-turn state. Documentation resolutions add
only the selected changes and retain the earlier pilot record.

Pricing-window work (#844), auxiliary-cost receipts (#878) and the partial-summary correction (#880)
are excluded. Auxiliary usage is not fully represented in turn-cost receipts. Repeated-wait changes do
not implement the wall-time budget or harness-time attribution still open in #832. No model defaults,
permission scope, credentials or live settings are changed by preparing this candidate.

## Checks and known limits

The combined source/provider/authentication/loop/tool/context/job/delegation/docs batch passed 1,102
checks and skipped one before the final fixture correction. Its inherited privacy assertion misread a
synthetic URL as an email address. The full tracked-file scanner found the same false positive in a GitHub
auth fixture. Adjacent-literal splits preserve both exact runtime URLs and all assertions. The final root
privacy, GitHub auth and documentation batch passes 141 checks with one skip. The exact reasoning
capability check also passes.

The remaining inherited failure is the old line ceiling, also reproduced on the `b1477f4e` base. The core
count is 17,157 versus 17,082 on that base, against its inherited 15,824 ceiling. No ceiling was raised.
The current structural counter reports 44 inward imports against 39 and eight private reads against eight.

The complete shared web suite passed 394 tests. Its existing pending-client cleanup warnings remain.
Tests use fake providers and temporary state. They do not establish a successful real ChatGPT sign-in,
account eligibility, provider billing totals or live tool continuation.

## Handoff

The intended native bundle version is `0.1.0-experimental.4`. The native executable remains the reviewed
prior build. The launcher must attach to the same selected core home, seat and socket, preserving the
existing data and settings. No new provider or permission setting is selected automatically.

Installation and native acceptance remain pending the lead's checks. Preserve state before the controlled
handoff, require idle work and an empty composer, verify the exact source and bundle, and compare the
preserved session/message inventory afterward. This document does not authorize or claim a live restart.
