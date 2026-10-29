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
```

- `list` — this seat's groups (no fields).
- `inspect` — one group's mode, conductor and members. `id` is required.
- `send` — a human message (`sender_kind: "human"`) into the group;
  `audience` defaults to all member profiles. Unknown ids/audiences fail.
- `observe` — read-only. Without `member` it returns the owner-level
  message log; with `member` that member's history. It never marks
  delivery or consumption. `after` is a nonnegative integer, `limit` a
  positive integer (≤ 1000); bools are not integers. Unknown keys are
  rejected everywhere.

There is deliberately **no create, start, pause, stop or control** here:
no authorized engine factory exists yet, so group creation and control
stay unimplemented until a trusted host can supply per-profile engines
(see `docs/SWARM-ENGINE-ADAPTER.md`).
