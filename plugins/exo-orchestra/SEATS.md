# Seat templates

There is one seat format: the core loader's, documented in [`docs/SEATS.md`](../../docs/SEATS.md)
(`src/exo/core/seats.py`). This plugin does not define a second one. It ships templates in that
format and a few helpers.

Templates for the seven roles sit in `seats/<role>/seat.toml`, next to a `SOUL.md` that holds the role card.
Each uses the canonical keys only: `[seat]` (name, role, description, soul), `[principal] store`,
`[models] tier`, `[tools] toolsets`, `[skills] folders` and `load`. Fields the loader does not read
(`schema`, `stage`, `delegation`, `models.main`, `models.tiers`) are not emitted. Explicit routes go in
`[models.roles]`, for example `main = ["provider:model"]`; delegation limits stay in `config.json`.

Templates name no model and no provider (D-018). Tiers follow D-017: judgment roles (mission control,
orchestrator, architect, reviewer) ask for `deep`, building and design roles for `standard`, research
for `fast`. The tier is a router role, so the principal's router config decides which model serves it. A
principal changes a tier in their own copy of the file.

## Commands and helpers

- `/orchestra seats` lists the templates. `/orchestra seat <role> [--seat NAME] [--out DIR]` prints one, or
  writes `seat.toml` and `SOUL.md` into `DIR` (use `$EXO_HOME/seats/<name>/`, then `exo serve --seat <name>`).
- `/orchestra check <file>` validates a seat file: unknown keys are errors, skills in `skills.load` must exist
  in the pack, and the core loader must accept it.
- `exo_orchestra.session_body(seat)` returns the `session.create` fields (`title`, `seat`, and `model` when
  `models.roles.main` is set). `exo_orchestra.models_overlay(seat)` returns `models.roles`, ready to merge
  into the router config.
- Skills load natively. The plugin registers the whole pack with the core at start, so a seat's
  `skills.load` names skills the core already knows.
