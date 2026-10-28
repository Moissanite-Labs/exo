# Experimental desktop pilot, revision 2

This local test build adds warnings for repeated tool calls during a turn, corrects failed-call telemetry, and repairs repeated masking of shell output. It keeps the first pilot's features and shared canary store. The native executable is unchanged.

## Changes for this run

- The primary loop warns after three calls with matching normalized arguments. It files at most one finding per tool per turn. Successful file-changing tools reset the comparison. This is a heuristic: legitimate polling can repeat, and the warning does not measure semantic progress.
- A terminal model failure stays an error. Recorded fallback totals require confirmed recovery. Timeouts and successful fallbacks remain visible, and legacy trace inference remains available.
- Registry-owned bash results receive one strict final redaction pass. A failed final secret lookup or redaction withholds the entire result. Direct bound shell calls keep their default protection, including streamed reads and raw credentials containing masking markers, quotes or backslashes.

The warning does not stop, retry or switch models. It leaves the user's round limit unchanged. The observed investigation loop motivated the warning, but its model or context cause has not been established.

## Source

The starting point is the installed first pilot at `a69f8e79870e920e853bf872acabcfccbf490c55`. The fixes remain canonical changes in [telemetry fix](https://github.com/aska-digital/exo/pull/750), [active warning fix](https://github.com/aska-digital/exo/pull/752) and [shell redaction fix](https://github.com/aska-digital/exo/pull/754). The source composition is tracked in the existing experimental build PR, with exact installed coordinates in its receipt.

The first pilot's feature inventory remains in [its notes](PILOT-EXPERIMENTAL-20261028.md). Native source is `aska-digital/exo-desktop` at `a7f9b528a5e835ab36c38f4453cae5f8388ed3e4`. Only its attached launcher and revision metadata change for this build.

## Verification

Fake model and tool checks prove the warning appears before a terminal event, does not duplicate its finding at turn end, respects edits, survives a diagnostic-store failure, and permits cancellation. A disposable composition proof requests an uncapped turn, runs eight tools and nine fake model calls, and finishes normally. An independent proof also verifies that request exceeds a configured two-round limit without changing that setting.

The composed core, auth, trace, permissions and documentation suite passes 1,372 checks with one skip. The desktop web suite passes all 390 tests. Exact source and installation coordinates are recorded in the build receipt. No real model turn is used for installation testing. Hosted CI and cross-team merge clearance are not claimed.

This remains an experimental composition. Its inherited architecture limit is unresolved: 44 inward product imports against 39, and eight private-state reads against eight. No limit is raised or guard weakened.

## Local handoff

Installation requires a private database backup and per-message hashes, a closed app, and fresh confirmation that all sessions and jobs are idle with no approvals or queued messages. Recheck after model and permission inspection, then verify the exact old PID and socket before the external stop.

Before opening the app, verify every prior message, the session inventory, route order and credential-binding metadata. Restore only the permission scope captured immediately before this restart. Never send a model request or continuation as an installation check. The old source, app and launcher remain available for rollback.
