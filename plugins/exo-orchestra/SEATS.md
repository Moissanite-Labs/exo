# Seat format

A seat is a named config for one agent: which models it asks for, which tools it may use, which skills
it loads and which principal it works for. Exo reads it as TOML. The same schema works as JSON with
the same keys. Templates for the seven roles ship in `seats/<role>/seat.toml`, next to a `SOUL.md`
that holds the role card.

`/orchestra seats` lists the templates. `/orchestra seat <role> [--seat NAME] [--out DIR]` prints one
in this format, or writes `seat.toml` and `SOUL.md` into `DIR`. `/orchestra check <file>` validates a
seat file.

## Example

```toml
schema = 1

[seat]
name = "forge"                 # required: letters, digits, dot, dash, underscore
role = "builder"               # free label; the shipped templates use the seven roles
description = "Implements the approved design and opens the PR."
stage = "4. build"             # where the seat sits in the pipeline, for people
soul = "SOUL.md"               # role card, relative to this file

[principal]
store = "/path/to/principal-store"   # where policies/ and agents/ live; empty means $EXO_PRINCIPAL_STORE

[models]
tier = "standard"              # fast, standard or deep (D-017)
# main = "anthropic:claude-sonnet-5-5"   # optional explicit routes, provider:model (D-018)
# aux = "..."
# decision = "..."
# eval = "..."
[models.tiers]                 # optional: route a tier for this seat's delegated children
# fast = "anthropic:claude-haiku-5-5"

[tools]
toolsets = ["fs", "shell", "search", "delegation", "memory", "skills"]

[skills]
pack = "/path/to/exo/skills"   # the Exo skills pack; empty means the core's default
load = ["operating-doctrine", "github-pr-workflow"]

[delegation]                   # optional
allow_upgrade = false          # a child may run above its parent's tier
escalate_on_fail = true        # retry a failed child one tier up
```

## Fields

| Key | Type | Meaning |
|---|---|---|
| `schema` | int | Must be `1`. Only additive changes happen inside a version. |
| `seat.name` | string | The seat's name. It becomes `seat` on sessions the core creates (`session.create`), so clients filter by it. |
| `seat.role`, `description`, `stage`, `soul` | string | Labels and the role card path. Not read by the loop. |
| `principal.store` | string | Principal store root. |
| `models.tier` | `fast`, `standard`, `deep` | The tier of model the seat asks for. The router maps a tier to a model, so a seat never names a vendor. |
| `models.main`, `aux`, `decision`, `eval` | `provider:model` | Optional per-seat routes for a model role. When absent, the user's router config decides (D-018: the eval model defaults to the main route, the decision model to the local one). |
| `models.tiers.<tier>` | `provider:model` | Optional route for one tier, for this seat's children. |
| `tools.toolsets` | list of string | Toolsets the seat may use. Names come from the tool registry: `fs`, `shell`, `search`, `delegation`, `memory`, `skills`. |
| `skills.pack` | string | Skills pack directory. |
| `skills.load` | list of string | Skills the seat loads at start. Each must exist in the pack. |
| `delegation.allow_upgrade`, `escalate_on_fail` | bool | Per-seat override of the delegation limits (D-017). |

Unknown tables and keys are errors, so a typo does not pass silently.

## How the core uses a seat

- `session.create` takes the seat name: `exo_orchestra.session_body(seat)` returns the body fields
  (`title`, `seat`, and `model` when `models.main` is set).
- `exo_orchestra.models_overlay(seat)` returns the router roles the seat sets, ready to merge into the
  `models.roles` config.
- Skills load natively. The plugin registers the whole pack with the core at start, so a seat's
  `skills.load` names skills the core already knows. No external skills directory is configured.

## Templates

Each template names no model and no provider. Tiers follow D-017: judgment roles (mission control,
orchestrator, architect, reviewer) ask for `deep`, building and design roles for `standard`, research
for `fast`. A principal changes a tier in their own copy of the file.
