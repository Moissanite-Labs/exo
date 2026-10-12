# exo-orchestra

The multi-agent system as a generic Exo template. It ships three things.

1. **Seat templates** in `seats/<role>/`: a `seat.toml` and a `SOUL.md` for mission control,
   orchestrator, researcher, architect, designer, builder and reviewer. A seat is a named config of
   model tier, toolsets, skills and principal; the format is documented in `SEATS.md`. No model or
   provider is named. Model choice belongs to the principal and the router (D-017, D-018).
2. **The skill pack** in the repo's `skills/` folder, listed in `skills/MANIFEST.json`: control
   plane, operating doctrine, council, choreography, clean-writing (with the `check-prose.py`
   gate), the GitHub pack and SYM-2P. The plugin registers it natively with the core, so skills
   need no `skills.external_dirs` setting.
3. **The `/orchestra` command**: `seats`, `skills`, `seat <role> [--seat NAME] [--out DIR]` (print or
   write a filled-in seat) and `check <file>` (validate a seat).

## Use

```sh
# in a chat or any client: list templates, print one, write one, check one
/orchestra seats
/orchestra seat builder --seat forge
/orchestra seat builder --seat forge --out ~/.exo/seats/forge
/orchestra check ~/.exo/seats/forge/seat.toml
```

```python
from exo_orchestra import load_seat, validate_seat, session_body
seat = load_seat("~/.exo/seats/forge")
assert not validate_seat(seat)
body = session_body(seat)       # fields for the protocol's session.create command
```

Settings (`plugins.entries.exo-orchestra.settings`): `skills_dir`, `register_skills` (default true),
`principal_store`.

## Where principal data goes

Nothing here names a person, an account or an agent. A principal binds the roles to their own
agents and rules in the principal store:

| Store path | Holds |
|---|---|
| `agents/` | seat names per role, charters, each agent's own SOUL additions |
| `policies/prose.json`, `policies/internal-names.txt` | style profile and names the prose gate refuses |
| `policies/` | owner-only action list, posting rules, budgets, model choices |
| `records/ops/` | dispatch ledger, inflight claims, rotation, learnings, contribution state |

## Provenance

See `PROVENANCE.md` for the source of each part and what was changed.
