# Provenance

This plugin and the `skills/` pack were generalized on 2026-10-11 from the Protean system that
runs principal #1's agent team. Sources, all read-only:

- `~/.hermes/team-skills` (repo `aska-digital/protean-team-skills`): orchestration
  (protean-control-plane, team6-operating-doctrine, protean-council), creative/clean-writing,
  github/*, sym-protocol, scripts/check-prose.py.
- `aska-digital/protean-doctrine`: the public doctrine and clean-writing skills.
- `aska-digital/protean-kit`: choreography contracts and the role map.
- The live profile SOUL files, for the shape of a role card.

## Role mapping

The source team used named seats. The template uses roles. A principal re-binds them in the
store's `agents/`.

| Template role | Source seat |
|---|---|
| mission-control | orda |
| orchestrator | proteus |
| researcher | hazen |
| architect | leo |
| designer | frida |
| builder | mozi |
| reviewer | shaka |

## What moved out

Agent names, personal paths, the owner's GitHub handle and email domain, dated owner rulings,
ledger row ids, the owner-only decision list, and the hard-coded internal-name list in the prose
gate. These are principal data and now load from the principal store. Per-skill details are in
`skills/MANIFEST.json` (`generalized` field).

## Wave 2 change

The role profile skeletons (`profiles/<role>/config.yaml` plus `SOUL.md`) became seat templates
(`seats/<role>/seat.toml` plus `SOUL.md`). The `SOUL.md` files moved unchanged. The yaml settings
(`pipeline_stage`, `default_skills`) became `seat.stage` and `skills.load`; model tier and toolsets are new
(D-017). Skills now register natively with the core instead of through `skills.external_dirs`.
