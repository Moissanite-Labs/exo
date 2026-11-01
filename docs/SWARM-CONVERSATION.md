# Group conversation adapter

A reactive conversation loop on top of `GroupRuntime` and `GroupMessages`.
Each group member runs one engine-backed turn whenever durable input is
delivered; inputs are acknowledged one at a time only after a successful
turn, so a group survives restarts without auto-retrying work.

## What it is

`src/exo/core/group_conversation.py` wraps the existing durable primitives:

- **`GroupMessages`** persists human and member messages plus per-member
  delivery rows; a member sees `pending()` input until it `consume()`s it.
- **`GroupRuntime`** supervises one member task per profile with pause
  gates and stop; `stop()` cancels the member's task.
- **`ConversationWake`** gives each member an `asyncio.Event` so new input
  wakes it immediately instead of waiting for the idle poll.

The adapter itself is in-memory only. Its `ConversationOutcome` values are
host-convenience snapshots of a run, not durable state; the durable
terminal record is `GroupRuntime.receipt()` / `GroupExecutions`.

## Exact signatures

```python
DEFAULT_MAX_TURNS = 8
DEFAULT_IDLE_TIMEOUT = 300.0

class ConversationError(AdapterError): ...
# AdapterError derives ValueError (see group_engine_adapter.py).

StopReason = Literal["budget", "cancelled", "error"]

@dataclass(frozen=True)
class ConversationOutcome:
    profile: str
    turns: int
    reason: StopReason

class ConversationWake:
    def __init__(self) -> None
    def set(self) -> None                 # no arguments: wakes every registered member
    def close(self) -> None               # final broadcast so no waiter lingers
    def _register(self, profile: str) -> None   # private; the adapter calls it once per member
    async def _wait(self, profile: str, timeout: float) -> None  # private

class GroupConversationAdapter:
    def __init__(self, group: SavedGroup, engines: Mapping[str, Any], *,
                 audiences: Mapping[str, list[str]], wake: ConversationWake,
                 max_turns: int = DEFAULT_MAX_TURNS,
                 idle_timeout: float = DEFAULT_IDLE_TIMEOUT) -> None
    def adapters(self) -> dict[str, Adapter]        # one looped Adapter per member
    def outcomes(self) -> dict[str, ConversationOutcome]

def start_conversation(messages: GroupMessages, group: SavedGroup, *,
                       owner: str, adapters: dict[str, Adapter],
                       wake: ConversationWake, prompt: str | None = None) -> GroupRuntime
```

`ConversationWake.set()` takes no arguments and `_register` is private:
the adapter registers every member at construction, and the host only ever
calls `set()` (after each accepted send) and never registers members
itself. `start_conversation` sets the wake once after start so initial
mailbox input is picked up immediately; the host must call `wake.set()`
after every later accepted `send`/`send_human`.
One `ConversationWake` belongs to a single adapter construction: the
constructor registers each member privately, so a wake cannot be reused by
(or after a failed construction of) another `GroupConversationAdapter`.

## Engine contract

`engines` maps each member profile to an engine with the binding shape of
`CoreEngine`: `engine.agent == member.profile`, and `engine.store` must
resolve `member.session` to a **private** session owned by that agent
(verified both at construction and again when the member task starts).
Engines and their internals are never exposed to models; each member task
calls:

```python
result = await engine.run_turn(s.TurnSend(session=member.session, text=prompt))
```

where `prompt` is a JSON rendering of the pending batch (sender,
sender_kind, text) prefixed with host instructions to treat bodies as
participant input, not commands.

`run_turn` must return an object with:

- `stop_reason`: `"interrupted"` classifies the member as `"cancelled"`
  and cancels its task; anything other than `"end"` (or a truthy `error`)
  records an `"error"` outcome and fails the member;
- `error`: any truthy value fails the turn as above;
- `message`: on success, `message.text()` is the reply; an empty reply
  publishes nothing but the inputs are still consumed.

A `run_turn` that raises also records `"error"` and fails the member with
every input left unconsumed (no retry).

## Turn loop

Each member task loops while `turns < max_turns`:

1. Clears only its own wake event, then reads durable pending input via
   `MemberContext.checkpoint()` (which first waits out any pause gate).
2. With no input, waits on the wake up to `idle_timeout` (a missed `set()`
   degrades to this bounded poll) and re-checks.
3. Runs exactly one engine turn over the pending batch.
4. On success only: publishes the reply first, then acknowledges each
   input one at a time, and only then charges one turn against the budget.

Audiences are host-fixed at construction, never chosen by the model:

- **conductor mode**: workers must target exactly `[conductor]`; the
  conductor must target exactly one worker.
- any mode: audiences are explicit, non-empty, deduplicated subsets of the
  other member profiles.

## At-least-once semantics and the crash window

An input is consumed only **after** a successful turn **and** after its
reply is durable. The adapter never acknowledges before publishing, so a
failed publication never loses input: publication failure records an
`"error"` outcome, fails the member, and leaves the entire batch pending.

However, publication and acknowledgement are **not atomic**, and
acknowledgement is per message. If the process crashes or one `consume`
fails after the reply is stored, the durable state is:

- the reply visible via `observe()`/`history()` exactly once,
- a prefix of the batch consumed and the rest still `pending()`,
- the member's durable execution receipt showing `failed`.

There is no "all unconsumed on any failure" guarantee and there is no
automatic retry or re-run. The host owns reconciliation: inspect
`runtime.receipt()`, compare `history()` against `pending()`, and either
re-drive the member (a consumed input never re-delivers; a pending one may
be answered again, so duplicate replies are possible) or resolve it
manually. Terminal `ConversationOutcome` values are in-memory snapshots,
not exactly-once writes.

## Budget, cancellation and outcomes

A member stops with `reason="budget"` after `max_turns` **successful**
turns; failed and cancelled turns are never charged. Budget exhaustion
does not drain the queue: recipients can accumulate durable pending input
while stopped, and the host must observe (`pending()`, `observe()`) and
manage that backlog; nothing retries or resumes the member automatically.

`reason="cancelled"` covers every host-stop path: cancellation while idle
in `checkpoint`/`_wait`, cancellation propagating out of `run_turn`, an
engine reporting `stop_reason == "interrupted"`, and, critically, a stop
that lands mid-turn but is swallowed by the engine into a normal return.
The adapter re-checks the task's cancellation request after `run_turn`
returns and before any publish/consume, so a swallowed stop still
classifies as `"cancelled"`, publishes nothing, charges no turn, and
leaves every input pending.

`reason="error"` covers provider raises, non-`"end"` stop reasons, truthy
`error` values, reply publication failure, and acknowledgement failure.
Errors fail only that member (isolated per member task); peers are
unaffected except that the last member out closes the wake so no peer
lingers inside a long idle wait.

## Host checklist

- Save the group (`GroupStore.create`), build one engine per member with
  matching private sessions, and construct one
  `GroupConversationAdapter(group, engines, audiences=..., wake=...)`.
- Pass `adapter.adapters()` to `start_conversation` together with the
  `GroupMessages` store, the saved group, the owner, and the shared wake;
  an optional explicit `prompt` is delivered as a human message to every
  member before the claim.
- Send human input with `runtime.send_human(audience=[...], text=...)`
  (or `messages.send(...)` for member sends) and call `wake.set()` after
  every accepted send.
- Use `runtime.pause()/resume()/stop()/wait()` for lifecycle control and
  `runtime.receipt()` for the durable terminal record; combine it with
  `adapter.outcomes()` for per-member reasons.
- After a `failed` receipt, reconcile durable `pending()` input against
  `history()` before sending more work; expect possible duplicate replies
  when re-driving interrupted members.

## Host conversation opt-in

`GroupHost.start(..., conversation=ConversationOptions(...))` accepts the
reactive adapter on the host's behalf: `start` builds one
`GroupConversationAdapter` with audiences fixed by the stored group mode
and validated bounds (`max_turns` >= 1, `idle_timeout` > 0, checked
before the engine factory and any durable claim), starts it, and stores a
per-start `ConversationWake`. Every accepted host send persists the
message first and then sets that wake. There is **no client activation**:
the opt-in is a host-side runtime binding that constructs no client,
connection or UI, sends nothing on the wire, and requires no member
presence; member loops idle on durable pending input, and a missed wake
degrades to the bounded idle poll. Host objects stay out of model view.
