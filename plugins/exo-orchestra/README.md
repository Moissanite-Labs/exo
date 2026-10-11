# exo-orchestra

The Protean multi-agent system as a generic Exo template. It ships three things.

1. **Role profiles** in `profiles/<role>/`: `SOUL.md` plus a `config.yaml` skeleton for mission
   control, orchestrator, researcher, architect, designer, builder and reviewer. No model or
   provider is set. Model choice belongs to the principal.
2. **The skill pack** in the repo's `skills/` folder, listed in `skills/MANIFEST.json`: control
   plane, operating doctrine, council, choreography, clean-writing (with the `check-prose.py`
   gate), the GitHub pack and SYM-2P.
3. **The `/orchestra` command**: `roles`, `skills`, and `profile <role> <dir> [--seat NAME]`,
   which writes a filled-in profile for one role.

## Install

Enable the plugin and point the profile's skills at the pack:

```yaml
skills:
  external_dirs: ["<exo checkout>/skills"]
plugins:
  enabled: [exo-orchestra]
```

The pack is read through `skills.external_dirs` so skills keep their bare names (`clean-writing`,
`control-plane`). Set `register_skills: true` under `plugins.entries.exo-orchestra.settings` to
also register each skill as `exo-orchestra:<name>` through `ctx.register_skill`.

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
