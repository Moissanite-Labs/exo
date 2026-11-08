# Harness continuation limits

Scope: request-driven continuation and receipt-driven parent resumption,
verified with offline fake-provider runtime tests. This is not evidence of live deployment.

## Requests are bounded, not durable jobs

`exo_end_turn` is a main-agent harness tool. A successful, authorized request
finishes the current tool batch, then allows one final reply with
`tool_choice="none"`. Sibling calls in that batch still receive their results.
Provider tool calls returned despite that setting are not executed or retained
in the final reply or provider replay metadata. Native non-tool reasoning and
message replay are retained. The final reply
uses the ordinary usage accounting and Stop/error handling. A principal steer
clears the old finish intent and restores ordinary tool use for the new work.
The request's wake flags are applied only after the turn actually completes.
`continue_now` and `wake_after` are mutually exclusive. Delays are whole seconds
from 1 through 3600. Only one request per session is retained; a later request
replaces the earlier one. The default chain budget is five harness wakes.

Wakes are **process-local asyncio tasks**. They are not persisted and are lost
on restart. Closing the runtime cancels pending wakes. There is no recovery,
replay, or guarantee that a requested wake survives a supervisor restart. Use a
separately managed durable scheduling mechanism when recovery is required;
do not treat a successful tool response as a durable scheduling receipt.

`followup_pending` is **record-only**: the request's follow-up reason can be
retained in continuation state, but the flag alone never schedules a wake.
There is no demonstrated user-visible delivery/read path. It is not a durable
outbox or a promise of a later message.

`workers_pending` is **absent** from the tool. The model cannot assert worker state. Actual background tasks launched by the
harness each earn one completion receipt credit, owned by the parent generation.
Queued completion receipts resume an idle parent even when a UI is connected,
without requiring an `exo_end_turn` timer. Receipts delivered into an accepting
turn do not add a wake. Credits coalesce and do not consume or reset the five
request-driven wake limit; the model cannot create them by polling.

Stop, human takeover (including steer), failed/denied parent turns, cancelled
children, engine shutdown, `jobs.wake=false`, and open approvals prevent receipt
resumption. A receipt blocked by an approval remains in session context for the
next authorized turn; approval resolution alone does not retry the wake. Receipts
and ownership are process-local and cannot resume after restart.

## Stop and takeover precedence

A model's request is subordinate to the harness, not an unconditional next turn:

- Explicit Stop clears the pending request and cancels any timer, and invalidates
  the generation of a wake that already fired. A late request from the displaced
  turn cannot rearm it.
- Human/non-continuation input takes over: it clears the request, cancels the
  timer, resets the chain, and invalidates the old generation.
- A principal steer also fences workers and requests from the earlier rounds.
  Once the running turn consumes that steer, subsequent rounds acquire the
  current generation and can request continuation or launch owned background
  work. Already-running workers retain their old fence. A Stop or newer takeover
  before consumption prevents an old steer from restoring ownership.
- A fired wake is checked before execution and again under the session lock.
  Stop or takeover that wins before that locked start prevents a new wake
  message/turn; it is not merely a best-effort timer cancellation.
- Only a matching request from a normally completed turn (`stop_reason == "end"`
  and no error) can schedule a wake. A denied tool in that turn, an error, a
  spend refusal, interruption, or a non-end completion takes precedence over
  the request. Even an `end` stop reason with a nonempty error cancels it.
- Follow-up-only requests do not wake; the chain cap stops further automatic
  turns. A wake finding the session already running is dropped, not run in
  parallel or held as a durable retry.

This describes scheduling precedence, not a total ordering of diagnostic labels
when multiple failure conditions occur simultaneously. Stop cannot undo tool
side effects or a turn that already ran.

## Offline test evidence and boundaries

Run from the canary checkout:

```sh
EXO_NO_KEYCHAIN=1 PYTHONPATH=src:tests \
  .venv/bin/python -m pytest \
  tests/core/test_continuation.py \
  tests/core/test_continuation_engine.py \
  tests/core/test_continuation_sprint.py -p no:randomly -q
```

The sprint adds four cases using the real `CoreRuntime` with fake providers:

1. A terminal provider error after an accepted continuation request clears it
   and produces no timer or extra user message.
2. A dedicated `SpendDenied` raised by the router's spend `check()` after the
   request produces `model_spend_refused`, an error completion, and no wake.
   The check is asserted to run exactly once. This is **not** inferred from the
   generic provider-error case. It does not test paid billing, journal arithmetic,
   actual cap exhaustion, or the distinct `model_spend_stopped` accounting path.
3. A real scheduler run omits the tool from discovery, rejects a model's attempted
   call, and rejects direct registry dispatch while its actual session is in the
   scheduler's live loop map.
4. A real delegated child proves the equivalent discovery, attempted-call, and
   direct-registry rejection using the delegator's actual child session/map.

Existing focused tests cover Stop, pending/fired timer races, takeover, denial,
close, chain caps, and record-only follow-up behavior. All providers here are
in-memory test doubles; no network or live service is needed. Restart recovery
is not tested or supplied. The external sprint receipt records exact results
and SHA-256 hashes; these tests do not establish the model identity of any live
process.

## Parent continuity evidence

Capability: `exo.delegation.parent-continuity`. Repeated calls to `exo_end_turn`
inside one turn are distinct from repeated receipt wakes. Diagnose a claimed
wake loop by comparing actual system-origin continuation messages and
`turn.started` events with tool rounds in the same turn. A model's description
of being woken does not establish a new harness ingress. Offline replay showed
that advisory finish instructions alone allowed repeated tool rounds; explicit
final-reply tool disabling prevents that pattern. Live behavior after adoption
still needs verification; no receipt scheduler or wake limit changes follow
from this finding.
