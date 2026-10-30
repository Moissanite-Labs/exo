# Parent approval authority for delegated work

This implements [the requested parent approval capability](https://github.com/Moissanite-Labs/exo/discussions/1002).

This capability is off by default and lasts only in the running core. It lets a human authorize one selected
parent model to inspect and answer eligible approval requests in active delegated descendants. It does not
make the model the principal, grant siblings authority, or change permission modes and round limits.

## Human setup and revocation

Select a main conversation acting as orchestrator in the terminal or web Chat view. Delegated workers
cannot receive grants because their narrower toolsets exclude the review helper. The check uses actual task
ownership, not session kind, so a separate fork is not excluded just for inheriting subagent metadata. Worker
status and revoke remain available. A granted main parent can review nested task descendants. Use:

```
/approval-authority status
/approval-authority grant
/approval-authority revoke
```

The standalone CLI attaches to an existing core, without starting one:

```
exo approval-authority status --socket /path/to/core.sock --session PARENT_ID
exo approval-authority grant --socket /path/to/core.sock --session PARENT_ID
exo approval-authority revoke --socket /path/to/core.sock --session PARENT_ID
```

Each client probes capability before sending a grant or revoke. An older core refuses explicitly rather than
sending the command to a model. The server requires an authenticated principal connection for every management
operation. A signed gate ledger must be available before a grant takes effect. Restart clears all grants.
Revocation removes authority even if recording its audit fails. Status reports the current process-local state.
There is no automatic live activation or migration of existing permissions.

## What the parent model can do

Ordinary delegation remains synchronous without a grant. With a grant, a delegated call waiting for an
eligible child approval yields a structured result to the same parent model turn. A grant can also wake an
already-waiting delegate call. This creates no additional reviewer model or automatic inference request.

The result names an invocation and grant generation. The model uses the `delegation_review` tool:

- `inspect`, with `invocation` and `generation`, lists eligible requests with redacted details and bindings.
- `allow` or `deny` also requires the exact `request_id` and `binding`. Allow is once only.
- `wait` waits for task receipts or another eligible request using completion signals, without model polling.

The existing parent and child model/round limits still apply. A replacement grant does not upgrade an old
invocation's generation. Revocation wakes waiting control calls and prevents further agent answers. Human
approval remains available. The parent should wait for delegated receipts before finishing. If it ends or is
cancelled without joining its work, the runtime cancels and joins those children with truthful receipts.
An extension that ignores cancellation reports incomplete cleanup and remains runtime-owned, preventing
store and writer closure. Normal worker deadlines are unchanged.
All child tasks and their gather are registered with runtime shutdown ownership before yielding.

## Authority and evidence boundaries

Only active task records establish ancestry. Fork metadata does not. Nested descendants are allowed, with
cycle-safe traversal. Terminal or retargeted edges, a different parent, stale bindings and resolved requests
are refused. The acting parent, turn, call and author come from the tool context, not tool arguments.

Only trusted `exo-gate` verdicts can mark a request delegatable. Unknown plugins default to false. Ordinary
opaque executable asks can be eligible under the explicit human grant. This is not a claim that an unknown
executable is safe or sandboxed. Credential/store floors, reserved controls, unseen outbound bodies, posting
violations and explicit principal-only policy restrictions remain human. The gate rechecks current policy
and the original binding before exposing details and again before a decision.

The model never receives a principal credential or a general `ApprovalRespond` capability. The original desk
resolves the existing request once, then runs the usual post-approval and executor checks. No `allow_always`,
standing rule or policy mutation is available. Existing human and signed-seat approval paths are unchanged.
Signed decision-attempt records precede resolution. The normal close event confirms the winning answer.
Audit records bind the actual author, control call, parent, child, task chain, grant generation and
original request/call hash. Audit failure withholds the decision. Human answers, revocation and cancellation
that win a race make the agent answer stale.

## Implementation and validation boundaries

This source stacks on the reviewed turn-drain prerequisite, PR #874 at `92b58c56`, merged with main in
`019f2e80`. `CoreRuntime.track_owned_task` is the public internal lifecycle seam for trusted continuations.
The structural caps remain unchanged. Fake-provider tests cover the complete parent/child cycle, late grants,
revocation and human races, scope and binding refusal, signed audit failure, parent end/cancel and core close.
Client tests cover capability refusal without a model send. Local tests are separate from hosted CI and live
acceptance. Swarm `GroupHost` integration and remote bus children are not implemented by this capability.
