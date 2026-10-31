# Turn limits and caller authority: decision proposal

Status: proposal for [#692](https://github.com/aska-digital/exo/issues/692). No policy is accepted or activated here. Main currently limits normal turns to 24 tool rounds, then permits one tool-less report call. [PR #363](https://github.com/aska-digital/exo/pull/363) proposes removing that default cap. Its observer condition asks whether someone is watching; watching alone does not authenticate a principal.

The new `tests/security/test_turn_limit_authority.py` runs a 40-round, in-memory fake provider through the real dispatcher. It covers unknown, local, stdio, seat and principal callers, both at the current default and an explicit three-round limit. Every fixture holds the observer condition true. The tests assert the stop event, tool count, report-call count and ingress authorship. They make no network or paid model calls. These are baseline guards, not proof of transport authentication or a decision that 24 is the permanent product limit.

## Decision before uncapping turns

| Choice | Benefit | Boundary to document and test |
| --- | --- | --- |
| Any socket caller may run uncapped | Simple consistent behavior for all clients. | Socket/token possession explicitly authorizes unlimited model rounds; retain interruption, budget stops and client-disconnect handling. |
| Only a server-verified principal may run uncapped | Keeps ordinary tools and local integrations finite. | Define signed request authority, freshness and replay protection; labels and a generic bearer token cannot grant the exception. |
| Only the terminal's trusted in-process path may run uncapped | Smaller initial authority surface. | All TCP/socket paths remain finite, including principal web clients; document the resulting client difference. |

Recommendation: choose the verified-principal rule, with finite limits for other callers. This is a recommendation only. The implementation must first define a verifiable request grant; the existing `principal:*` dispatcher label is not itself cryptographic evidence. Do not infer authorization from an open event stream, model output, a tool argument or a caller-supplied label.

Whichever choice is accepted needs one implementation in Exo, explicit documentation in CORE, USER-GUIDE and the D-010 amendment, and counterexample tests for forged labels, stale/replayed grants, TCP/socket ingress, a watcher disconnect, internal wakeups, cancellation and a finite override. Update these baseline tests to the chosen matrix in that same change. The policy issue stays open; this draft supplies reviewable acceptance work without silently changing spend authority.
