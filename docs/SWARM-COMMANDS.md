# Swarm human commands (`/group …`)

Principal-only commands for a seat's own groups, dispatched as the
`command.run` name `"group"` (`src/exo/core/group_commands.py`). Every
operation — including read-only listing and observation — first calls
`principal.require_principal`: a local or channel caller gets
`forbidden`. The group owner is always this engine's seat (`eng.agent`),
never caller-supplied, so one seat cannot read or mutate another seat's
groups.

The argument is one JSON object after the subcommand name:

```
/group list
/group inspect {"id": "<group>"}
/group send {"id": "<group>", "text": "change direction", "audience": ["lead"]}
/group observe {"id": "<group>", "member": "lead", "after": 0, "limit": 100}
/group create {"mode": "conductor", "members": ["lead", "one"], "conductor": "lead"}
/group start {"id": "<group>", "audiences": {"lead": ["one"], "one": ["lead"]}}
/group start {"id": "<group>", "conversation": {"max_turns": 10, "idle_timeout": 60, "max_concurrent": 2}}
/group start {"id": "<group>", "audiences": {"lead": ["one"], "one": ["lead"]}, "knowledge": true}
/group status {"id": "<group>"}
/group pause {"id": "<group>", "member": "one"}
/group resume {"id": "<group>"}
/group stop {"id": "<group>"}
/group plan_status {"id": "<group>", "plan_id": "<64 hex>", "limit": 100}
/group knowledge_publish {"id": "<group>", "key": "plan", "text": "...", "sources": ["session:s-1"], "expected_revision": 0}
/group knowledge_read {"id": "<group>", "member": "lead", "key": "plan"}
/group knowledge_list {"id": "<group>", "member": "lead", "limit": 100}
/group knowledge_history {"id": "<group>", "member": "lead", "key": "plan", "limit": 100}
```

- `list` — this seat's groups (no fields).
- `inspect` — one group's mode, conductor and members. `id` is required.
- `send` — a human message (`sender_kind: "human"`) into the group;
  `audience` defaults to all member profiles. Unknown ids/audiences fail.
- `observe` — read-only. Without `member` it returns the owner-level
  message log; with `member` that member's history. It never marks
  delivery or consumption. `after` is a nonnegative integer, `limit` a
  positive integer (≤ 1000); bools are not integers.
- `create` — persist a group owned by this seat. `mode` is `"socratic"`
  or `"conductor"`; `members` is a non-empty list of profile strings;
  `conductor` is optional. Unknown ids/modes/fields fail before the host.
- `start` — validate `audiences` (a non-empty object mapping each member
  profile to a list of profile strings; never inferred or defaulted) and
  hand off to the host, which re-validates stored members against the
  injected profile set, builds engines through the authorized factory
  and claims one durable execution. A failed start leaves no runtime
  and no durable claim; a partial failure after the claim still blocks
  retry by design.
  Give exactly one of `audiences` or `conversation`; both or neither is
  `bad_request`. `conversation` is an explicit opt-in object with
  `max_turns` (an exact integer, 1 to 10000), `idle_timeout` (a finite number
  greater than 0 and at most 86400, in seconds) and optional `max_concurrent`
  (an integer, at least 1). Booleans are not numbers, and unknown keys at either
  level fail, including `plan` and `grants`, and a `knowledge` key inside
  `conversation`: this command does not start plans. It calls the injected host's
  `start(<id>, conversation=...)` with no `audiences`; the host derives the
  fixed audiences from the stored group mode, rejects `max_concurrent` above the
  member count before it builds engines or claims anything, and binds the reactive
  conversation, so a later `send` through the same host wakes it. A host
  rejection is `bad_request`. This adds no engines, grants or permissions and
  no recovery of an earlier claim: it is the same start through the same
  injected, owner-matched host, and the same principal gate applies.
  `knowledge` is an optional top-level field of either form. It must be exactly
  `true` or `false` and defaults to `false`; any other value, including `1`,
  `"true"` and `null`, is `bad_request` before the host is called. Only `true`
  is forwarded, as `start(..., knowledge=True)`; omitted or `false` makes the
  same host call as before. It uses the host's existing opt-in with the host's
  default caps (this command exposes no `max_items` or `max_chars`; they fail as
  unknown fields). Each member turn then carries a bounded snapshot of the
  owner-published knowledge for that group, placed after the participant
  messages as quoted JSON and framed as untrusted reference data, not
  instructions. The host reads the snapshot at the start of each turn, so a
  conversation turn sees the latest revision published by then and only that
  revision. This grants no permissions and does not publish anything.
  Use `knowledge_publish` for that. The command does not claim that knowledge is
  supported on any other launch path.
- `status` — the host snapshot: per-member state, the durable receipt or
  `null`, whether the runtime is active in this process and whether
  reconciliation is required (active claim, no local runtime). An active
  receipt is never proof of liveness.
- `pause` / `resume` / `stop` — group-wide or member-scoped hooks via
  `member`; `stop` is immediate, not graceful. Host rejections surface
  as `bad_request` with the host's reason.
- `plan_status`: read-only view of one durable plan run: the plan state and
  each task's profile, attempt, state, timestamps, result and error. It needs
  no injected host and writes nothing: on a database that has no plan tables it
  refuses and creates none. `plan_id` must be 64 lowercase hex characters, the
  value `validate_plan` returns. An unknown group, an unknown plan, a plan in
  another group and a plan owned by another seat all give the same refusal
  (`not_found: no such plan`). Result and error are untrusted engine output.
  All result and error text in one response shares a 32000-character budget.
  Each task gets an equal share of it. Its error is capped at the smaller of
  256 characters and a quarter of the share, and its result gets the rest of
  the share up to 4000 characters. `result_truncated` and `error_truncated`
  flag every clip. The budget counts text only: the JSON keys, escaping and the
  metadata come on top. Task id and profile are at most 64 characters each by
  the plan contract, state is a fixed word and the timestamps are ISO strings,
  so metadata stays finite and is never clipped. `limit` (1 to 200, default
  100) caps the task rows; `task_count`, `tasks_returned` and `tasks_truncated`
  report any omission. It starts nothing
  and grants nothing. No client or desktop path calls it yet.
  The response also carries two additive fields. `liveness.status` is
  `settled` when the plan state is `settled` and `unknown` for every other
  state, including `running`: the command reads no host or process evidence, so
  it never says whether a runner is gone. `liveness.reconcile_hint` is a fixed
  sentence. A running plan may be live in another process. Do not restart the
  same plan id and do not mark it terminal. No tool reconciles a running plan.
  `settled` means only that the plan row is terminal. It does not prove that every
  task outcome is known: read `reconcile_counts.uncertain` as well.
  `reconcile_counts` tallies the per-task `reconcile` labels over all tasks, not
  only the `limit` page, and each task is counted once:
  `in_flight_or_lost` (`in_flight_or_lost`), `uncertain`
  (`terminal_outcome_uncertain`, `needs_inspection`,
  `needs_inspection_intent_not_started`), `never_started` (`not_admitted`,
  `reserved`) and `terminal` (`settled`). `unlabelled` counts tasks with no
  durable ledger record (a legacy plan with no ledger), so the five numbers add
  up to `task_count`. If any ledger row cannot be read, `durable.status` is
  `unavailable` and `reconcile_counts` is `null`: no count is given, and a
  missing count never means zero. Tasks that were read keep their `reconcile`
  label. The counts need the ledger row of every task, not only the `limit`
  page, so the cost of one call grows linearly with the plan's task count. The
  command writes nothing and creates no table.

## Shared knowledge (M02–M04)

`knowledge_publish` / `knowledge_read` / `knowledge_list` /
`knowledge_history` are the human control surface for
`exo.core.group_knowledge.GroupKnowledge` (see `docs/SWARM-KNOWLEDGE.md`).
They are additional subcommands of the existing `group` command; no new
protocol command is added.

- `knowledge_publish` — owner-only, always as this engine's seat
  (`eng.agent`). Required `key` (matching
  `^[a-z0-9][a-z0-9_.-]{0,127}$`), `text` (non-empty, ≤ 100,000
  characters), and `sources` (a non-empty list of 1–50 unique, non-blank
  reference strings) are validated by the knowledge module. `sources`
  must be a JSON array of strings — a bare string is rejected. `text`
  is stored as given, so no session, memory or mail content is ever
  collected automatically. `expected_revision` is optional and defaults
  to `0` (a new key); it must be a nonnegative integer (bools are not
  integers) and equal the current revision, otherwise the publish is
  refused as `bad_request` and nothing is written. There is no
  `publisher` field: a caller cannot publish as another profile.
- `knowledge_read` — one current item, or `"item": null` for a missing
  key. Requires an explicit `member`; membership is validated against
  the stored group by `GroupKnowledge`, so an unknown profile fails
  closed.
- `knowledge_list` — the group's current keys in key order; `limit` is a
  positive integer (≤ 1000, default 100) and `total` reports the full
  count.
- `knowledge_history` — every preserved revision of one key, oldest
  first, with the newest `limit` entries returned and `total` reporting
  the full count.

These are **human owner views of a member's perspective, not member
reads**: the owner supplies which member view to inspect, and the
response carries `"view": "human_owner_view"` so it is never mistaken
for that member's own retrieval. Owner authority here is metadata
authority — it is not permission to impersonate a member, and these
subcommands expose no member-side tool. Automatic member retrieval of
group knowledge remains an open item: nothing here pushes or delivers
knowledge to a running member, so members still learn of it only
through their own explicit reads outside this slice.

Malformed arguments (unknown fields, wrong types, bad keys, empty
sources, stale revisions) fail with `bad_request` before or at the
store, following the same conventions as the other subcommands.

## Fail-closed injection prerequisite

The lifecycle operations (`create`/`start`/`status`/`pause`/`resume`/
`stop`) act only through a trusted `GroupHost` explicitly injected as
`eng._group_host` by the wiring that owns this engine. This command layer
never constructs the host, engines or profiles and never reads
configuration to build one. With no injected host, or a host whose
`owner` differs from `eng.agent`, every lifecycle operation fails closed
(`forbidden`); malformed arguments fail with `bad_request` before the
host is ever touched. `GroupHost` is an internal composition seam, not an
authentication boundary: host objects must never be exposed to models
(see `docs/SWARM-HOST.md`).

Unknown keys are rejected everywhere.

## Not wired

Real per-profile engine composition, client controls and authorized
engine factories are not wired: the wired host must be supplied by
trusted external wiring that is out of scope for this slice.
