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
/group status {"id": "<group>"}
/group pause {"id": "<group>", "member": "one"}
/group resume {"id": "<group>"}
/group stop {"id": "<group>"}
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
- `status` — the host snapshot: per-member state, the durable receipt or
  `null`, whether the runtime is active in this process and whether
  reconciliation is required (active claim, no local runtime). An active
  receipt is never proof of liveness.
- `pause` / `resume` / `stop` — group-wide or member-scoped hooks via
  `member`; `stop` is immediate, not graceful. Host rejections surface
  as `bad_request` with the host's reason.

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
