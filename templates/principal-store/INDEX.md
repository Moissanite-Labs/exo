# INDEX (router)

Read this first. Load only what the task needs. Commands work from anywhere once the
principal is active (`EXO_PRINCIPAL=<id>` or `--principal <id>`):
`exo find|get|ctx|links <...>`.

## Load rules

| If the task is... | Load |
|---|---|
| Acting as or for the principal (drafting in their voice, making their call) | `PRINCIPAL.md` (one page) |
| Reading or answering a message from the principal | `mind/communication-style` |
| Deciding whether to act or ask the principal | `policies/gates.yaml`, `policies/interruptions.yaml` |
| Reporting to the principal | `relationship/reporting` |
| Posting anything public or sending anything outside | `policies/posting.md` |
| Spending money or calling paid models | `policies/budget.yaml` |
| Who the principal is | `person/identity` |
| Projects and current work | `work/projects` |
| Machines, accounts, tools | `systems/environment` |
| What happened on a day | `timeline/<date>.md` |
| Unknown topic | `exo ctx <words> --budget 1500` |

Raw sources (private, never load whole): `corpus/private/`. Training sets: `training/`.
Method and schema: `method/`. The principal's own agents and charters: `agents/`.

## Nodes

<!-- NODES:BEGIN -->
<!-- NODES:END -->
