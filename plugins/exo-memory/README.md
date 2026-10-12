# exo-memory

The Exo memory provider for the Exo core (`exo.core.slots.MemoryProvider`). It fills the one memory-provider slot (D-007).

## What it does

| Hook | Behaviour |
|---|---|
| `system_prompt_block` | The store's `PRINCIPAL.md`, curator comments removed, trimmed to `pack_budget` tokens, plus one line naming the tools. Built once per process, so the prompt cache holds. |
| `prefetch` | Belief nodes from `exo.retrieve.assemble`, then episodic recall: keyword hits from the log and the summary view. Each section is labelled. The whole block stays under `prefetch_budget`. |
| `on_turn_start`, `sync_turn` | `utterance` events in the ledger. User turns are authored by the principal, assistant turns by the agent id. The turn also goes to the episodic log. |
| `on_memory_write` | A `note` event of type `memory_write`. |
| `on_delegation` | An `action` event of type `delegation`, and a `work` line in the episodic log. |
| `sync_turn` (capture) | The core has no pre-compress call, so each turn also runs `exo.capture` over a short window of recent turns and writes `ruling`, `verdict` and `question` events (config `capture_turns`). |
| `on_pre_compress` | Finds rulings, corrections and open decisions with `exo.capture`. Writes `ruling`, `verdict` and `question` events, then returns a short block for the compressor to keep verbatim. Checkpoint API v2. |
| `on_session_end` | A `note` event of type `consolidate_pending`. |

Tools: `exo_recall(query, budget)`, `exo_note(text, kind)`, `exo_should_have_been(target, wanted, reason)`,
`exo_zoom(id, n)`, `exo_date(id)`.

A correction becomes a `verdict` event that points at the agent output it judges. If that output is not
in the ledger yet, it is logged first.

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
| `prefetch_budget` | | 1500 tokens |
| `episodic_share` | | 0.4 of the prefetch budget |
| `compact_enabled` | | true. Uses the `aux` model role of the Exo router (`exo.ext.compat.aux_summarize`). |
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
