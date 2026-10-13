# Consumer path acceptance plan

Status: proposed implementation checklist, 2026-10-13. The accepted deployment choices remain D-050 in
`DECISIONS.md`: local first, signed desktop apps after the build settles, no hosted custody of user keys.
This plan adds reviewable acceptance criteria; it does not claim that the consumer experience has shipped.

## First user and surface

The initial user is an individual who wants help with their own files and tasks, can install an application,
and should not need to understand terminals, API routing, seats or peers to get a useful reply.
The proposed first surface is the locally served web client. A signed desktop application can later launch
the same core and client. The terminal remains available for developers and experienced operators.

## First five minutes

1. Install the current local package, with its approval gate and client plugins enabled. The installer and
   updater remain developer-oriented today; a downloadable, signed desktop installer is future work.
2. Open the page using the link printed by `exo web`, which includes the token for that launch. Show a clear
   route back to a fresh link when the server restarts. A bare loopback URL is not an authenticated entry point.
3. Reuse a supported, already authenticated local provider when available. Otherwise open provider setup,
   explain the cost and credential choice, and store an entered key outside chat. Never silently use a fake
   model. A universally keyless route is not available today; own inference remains the later D-050 option.
4. Send one ordinary request. The first-run milestone requires a nonempty reply from a real provider.
   Cancellation, missing credentials or a failed call must leave setup unfinished with a recovery action.
5. If the task needs permission, show explicit Once, Always and Deny controls. Always needs a separate
   confirmation. Typing in the composer must preserve the draft and cannot authorize a tool.

## Calm default

The proposed consumer default shows the conversation, composer, connection/model readiness and pending
permission controls. Model usage, context capacity, cache, cost, findings, seats and peers belong behind
an explicit details control. A user should see technical information when it explains a choice or failure.
The operator's instruments remain available; their current visibility is not evidence of consumer acceptance.

## Acceptance still required

- A fresh macOS and Linux installation loads the gate before any model tool can run.
- Both an existing local login and a configured non-Anthropic API provider produce a real first reply;
  no working provider produces a clear setup state. Manual acceptance uses a real configured provider with
  explicit consent for the call. Regression tests use synthetic credentials and offline endpoints and cannot
  establish real-provider acceptance on their own.
- A clean profile reaches the local web conversation without manually editing URLs or configuration.
- Server restart and a sleeping tab recover with clear guidance and a current transcript.
- Ordinary typing cannot approve, and every standing grant requires a second deliberate action in both clients.
- A person unfamiliar with Exo completes a small task using the default surface without explaining the
  developer vocabulary. This requires an observed usability session, not a screenshot or a unit test.

Issue #50 remains open until the surface/defaults are accepted and these checks are demonstrated. Related
implementation work is tracked in #27, #32, #33, #34 and #43. Signing/distribution and keyless inference remain
separate release work; this checklist does not create a delivery date for them.
