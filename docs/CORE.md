# The exo package: ledger, store and CLI

The `exo` package is the generic mechanism (L1 in `ARCHITECTURE.md`). It holds no data about any
person. Everything about a principal lives in their store. Python 3.11 or newer. The base
dependencies are PyNaCl and httpx (`pyproject.toml`). The terminal client, MCP and the bus relay are extras.

This page covers the signed ledger, the principal store and the store commands. Other parts have their own pages:
the protocol and the running core in `docs/CORE-V2-INTERFACES.md`, the command list in `docs/COMMANDS.md`, tools in
`docs/TOOLS.md`.

## Modules

| Module | Job |
|---|---|
| `exo.crypto` | Canonical JSON, Ed25519 keygen, key files (0600), sign and verify. `Signer` binds an author name to a key. |
| `exo.ledger` | Append-only event log in SQLite. Signed, hash-chained, no updates or deletes. |
| `exo.store` | `PrincipalStore(root)`: create from template, load nodes, BM25 find, get, budgeted ctx, links, lint, graph, index. |
| `exo.retrieve` | `assemble(store, query, budget)` returns text and node ids. `Ranker` is the seam for embeddings and graph expansion. |
| `exo.cli` | The `exo` command. Every subcommand is in `docs/COMMANDS.md`. |

The package holds much more than these four. `exo.core` is the loop, engine, tools and plugin loader.
`exo.protocol` and `exo.server` carry the wire. `exo.learn`, `exo.consolidate`, `exo.ask`, `exo.eval` and
`exo.brief` are the learning side (`docs/LEARNING.md`, `docs/TOOLS-CLI.md`). `exo.bus` is the signed relay between
seats. `exo.gate` is the action gate (`docs/GATE.md`). `exo.ingest` imports past conversations.

## The protocol in numbers

`src/exo/protocol/schema.py` is the source of truth. It holds 30 commands (the cap, all used) and 28 event types (the
tests require fewer than 30). `tests/docs/test_docs_map.py` checks these two numbers against the schema, so this paragraph cannot go
stale without a failing test. After a schema change, run `python -m exo.protocol.export --check`.

## Where things live

- `EXO_HOME` defaults to `~/.exo`.
- A principal store defaults to `$EXO_HOME/principals/<id>`. Any directory works with `--root`.
- Keys default to `$EXO_HOME/keys/<name>.key`, with the public key in `<name>.pub`.
- The ledger of a store is `<store>/ledger.sqlite`.
- The empty store template is `templates/principal-store/`. Wheels carry it inside the package.

## Event schema

Each event is one canonical JSON document (sorted keys, no whitespace, UTF-8).

| Field | Meaning |
|---|---|
| `id` | sha256 of the canonical event without `id` and `sig` |
| `ts` | UTC time, `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| `author` | the person or agent seat that wrote it |
| `kind` | see below |
| `body` | JSON object |
| `session` | session id, or null |
| `refs` | ids of earlier events in the same ledger |
| `prev` | chain hash of the previous event, or 64 zeros for the first |
| `pub` | base64 Ed25519 public key of the signer |
| `sig` | Ed25519 signature over sha256 of the canonical event without `sig` |

The chain hash of an event is `sha256(prev + id)`. It is stored per row and becomes the next
event's `prev`. `verify()` recomputes every id, link, chain hash and signature. Pass
`trusted={author: [pubkeys]}` to also pin keys to authors.

Kinds: `utterance`, `ruling`, `verdict`, `action`, `artifact`, `ingest`, `question`, `answer`,
`belief_proposed`, `belief_accepted`, `note`.

A `verdict` body needs `verdict` (one of `accept`, `edit`, `reject`, `should_have_been`) and
`target`, the id of the judged event. The target is added to `refs`. Every ref must already
exist in the ledger.

Appends run inside `BEGIN IMMEDIATE`, so several processes can write to one ledger safely.
SQLite triggers abort any `UPDATE` or `DELETE` on the events table.

## Python API

```python
from pathlib import Path
from exo import crypto
from exo.ledger import Ledger
from exo.store import PrincipalStore
from exo.retrieve import assemble

store = PrincipalStore.for_principal("someone")
signer = crypto.Signer("agent-one", crypto.load_key(Path.home() / ".exo/keys/agent-one.key"))
led = Ledger(store.ledger_path, signer)
ev = led.append("utterance", {"text": "hello"}, "agent-one", session="s1")
led.append("verdict", {"verdict": "edit", "target": ev["id"], "text": "shorter"}, "principal")
led.verify()            # {"ok": True, "checked": 2, "head": "..."}

store.find("posting rules", n=5)     # [(node_id, score), ...]
store.ctx("posting rules", budget=1500).text
assemble(store, "posting rules", 1500).node_ids
```

## Store checks

`exo lint` reports errors for missing keys, bad types, an id that does not match its path,
bad edge types, dangling links, a missing `INDEX.md` or `PRINCIPAL.md`, and leak patterns
(phone numbers, API keys, tokens, passwords, private keys). It warns on orphans, long bodies,
long summaries, nodes without sources, and unknown confidence or stability values. The
template store lints with zero errors and zero warnings.

## Budgets

`ctx` estimates tokens as characters divided by 4. The header counts against the budget. A
node whose body does not fit is listed by its summary line if that fits.

## CLI

The store and ledger commands, in short. Arguments, exit codes and the rest of the CLI are in `docs/COMMANDS.md`.
The task-first version is in `docs/USER-GUIDE.md` section 11.

```
exo init <id> [--from-template [PATH]] [--root DIR] [--interview]
exo find|get|ctx|links|lint|graph|index ...   [--principal ID | --root DIR]
exo log append <kind> --author NAME (--text T | --body JSON) [--session S] [--ref ID ...]
exo log verify | exo log tail [-n 10] [--kind K]
exo keygen <name> [--out PATH]
exo import-store <path> [--id ID] | exo interview | exo ingest <kind> <path>
```

The active principal comes from `--principal`, else `$EXO_PRINCIPAL`.

## Tests

`pytest` runs unit tests for crypto, ledger, store, retrieve and the CLI. The acceptance
test in `tests/acceptance/` copies a populated store (`EXO_ACCEPTANCE_STORE`) to a temp dir,
without `corpus/private`, and checks lint, find and ctx on three queries. It skips when no
such store is present.
