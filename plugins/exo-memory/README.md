# exo-memory

The Exo memory provider for the Exo core (`exo.core.slots.MemoryProvider`). It fills the one memory-provider slot (D-007).

## What it does

| Hook | Behaviour |
|---|---|
| `system_prompt_block` | The store's `PRINCIPAL.md`, curator comments removed, trimmed to `pack_budget` tokens, then the taste block (D-044: house, learned and pinned taste items with their source, under `taste_budget` tokens; `docs/TASTE-BASELINE.md`), plus one line naming the tools. Built once per process, so the prompt cache holds. |
| `prefetch` | Belief nodes from `exo.retrieve.assemble`, then episodic recall: keyword hits from the log and the summary view. Each section is labelled. The whole block stays under `prefetch_budget`. |
| `on_turn_start`, `sync_turn` | `utterance` events in the ledger. User turns are authored by the principal, assistant turns by the agent id. The turn also goes to the episodic log. |
| `on_memory_write` | A `note` event of type `memory_write`. |
| `on_delegation` | An `action` event of type `delegation`, and a `work` line in the episodic log. |
| `sync_turn` (capture) | The core has no pre-compress call, so each turn also runs `exo.capture` over a short window of recent turns and writes `ruling`, `verdict` and `question` events (config `capture_turns`). |
| `on_pre_compress` | Finds rulings, corrections and open decisions with `exo.capture`. Writes `ruling`, `verdict` and `question` events, then returns a short block for the compressor to keep verbatim. Checkpoint API v2. |
| `on_session_end` | A `note` event of type `consolidate_pending`. |

Tools: `exo_recall(query, budget)`, `exo_get(id, body_only)` (a whole belief node, same store and access as recall; D-032), `exo_note(text, kind)`, `exo_should_have_been(target, wanted, reason)`,
`exo_zoom(id, n)`, `exo_date(id)`.

Who wrote it (D-040): every write above reads the author the core stamped on the message. Only a message whose
author is `principal` becomes a ruling, a correction or a principal utterance; a peer's, a bus seat's or a job's
turn is logged under that author. `exo_note` cannot write `ruling` or `answer` (it keeps them as a `note` of type
`agent_note`), and `exo_should_have_been` is signed as the agent with `for: <principal>`. On first use the provider
adds its configured principal id to the key registry, so consolidation reads that id as the principal.

A correction becomes a `verdict` event that points at the agent output it judges. If that output is not
in the ledger yet, it is logged first.

## One log on the Exo core (D-028)

The Exo core keeps each agent's one log and summary tree in `sessions.sqlite` and passes a read view of it to
`initialize(..., agent_log=view)`. Then exo-memory keeps no episodic log of its own: it writes nothing to
`<store>/episodic/`, its recall searches the agent log (the summary view is skipped, since the tree is already in
the system message), and `exo_zoom` / `exo_date` give way to the core's `memory_zoom`. The ledger and belief
recall are unchanged. On any other host it keeps its own episodic layer as below.

## Where data lives

Everything lives in the principal store. Nothing is written to the Exo home except the plugin config and the signing key.

- `<store>/ledger.sqlite`: the signed event log.
- `<store>/episodic/`: the verbatim daily log, the summary tree and the view. It carries its own
  `.gitignore`, so the raw record never enters the store's git history.
- `$EXO_HOME/keys/<agent_id>.key`: the agent's signing key. It is created on first use unless
  `auto_keygen` is false.

## Install

The Exo plugin loader finds this folder under `plugins/`. Select it with `memory_provider: exo-memory` in `$EXO_HOME/config.json` and set `EXO_PRINCIPAL` (or `plugin_config`).

A store must exist first: `exo init <id>`.

## Config

Order, lowest first: defaults, environment, `<exo_home>/exo-memory/config.json`, constructor values.

| Key | Env | Default |
|---|---|---|
| `principal` | `EXO_PRINCIPAL` | none |
| `exo_home` | `EXO_HOME` | `~/.exo` |
| `store_root` | `EXO_STORE` | `<exo_home>/principals/<principal>` |
| `agent_id` | | the seat identity, else `exo-memory` |
| `pack_budget` | | 1500 tokens |
| `taste_budget` | | 400 tokens (0 = no taste block) |
| `prefetch_budget` | | 1500 tokens |
| `episodic_share` | | 0.4 of the prefetch budget |
| `compact_enabled` | | true. Uses the `aux` model role of the Exo router (`exo.core.models.router.aux_sync`). |
| `principal_user_ids` | | empty. When set, other human authors are logged as `user:<id>`. |

See `exo_memory/config.py` for the rest.

## Tests

```
.venv/bin/python -m pytest plugins/exo-memory/tests
```

The smoke test (`tests/test_core_smoke.py`) runs the provider on the Exo core with the fake model.
One test copies an example store (`EXO_ACCEPTANCE_STORE`) to a temp dir and skips when there is none.

## Attribution

The episodic layer (`exo_memory/episodic/`) is vendored from
[aska-digital/optchat-memory](https://github.com/aska-digital/optchat-memory) v1.0.0, MIT,
Copyright (c) 2026 aska-digital. The licence is in `exo_memory/episodic/LICENSE-optchat`.
Exo changes are marked `exo:` in the code. One fix: optchat seeded a new session's message cursor with
the log length, which skipped that session's first turns. exo-memory starts at the turn's user message.
