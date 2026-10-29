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
`exo.compose` builds the engine `exo serve` runs and attaches `exo.peers` (the peer transport) and `exo.scheduler`
(scheduled jobs), which sit outside the core since D-067 (`docs/ENGINE-SPLIT.md`). The counted core (`exo.core`,
`exo.protocol`, `exo.server` and two command-line modules) is held by a test as a ratchet with headroom: the count
stays under a recorded floor plus 1,000 lines, the floor only goes down, and a security or robustness fix is never
blocked by it (D-077, `docs/ENGINE-SPLIT.md` "Budget policy"). `exo.protocol` and `exo.server` carry the wire.
`exo.learn`, `exo.consolidate`, `exo.ask`, `exo.eval` and
`exo.brief` are the learning side (`docs/LEARNING.md`, `docs/TOOLS-CLI.md`). `exo.bus` is the signed relay between
seats. `exo.gate` is the action gate (`docs/GATE.md`). `exo.ingest` imports past conversations.

## The protocol in numbers

`src/exo/protocol/schema.py` is the source of truth. It holds 30 commands (the cap, all used) and 29 event types (the
tests require fewer than 30). `tests/docs/test_docs_map.py` checks these two numbers against the schema, so this paragraph cannot go
stale without a failing test. After a schema change, run `python -m exo.protocol.export --check`.

## Where things live

- `EXO_HOME` defaults to `~/.exo`.
- A principal store defaults to `$EXO_HOME/principals/<id>`. Any directory works with `--root`. `exo init --root`
  and `exo import-store --root` record theirs in config `principals.<id>`, and `exo.store.principal_root` maps the
  id to it for every reader (store commands, `exo memory verify`, exo-memory, learning, the gate).
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
`trusted={author: [pubkeys]}` to also pin keys to authors, or a key registry (below).

### Pinned keys (D-040)

Each event carries the key that signed it, so a chain checked only against its own keys proves nothing about who
wrote it: a fresh key can sign anything. `$EXO_HOME/keys/registry.json` (0600, `exo.keyring.KeyRegistry`) lists the
keys this home trusts, the bus seats it has seen, the peer seats of other homes it pinned, and the ledger author ids
that are the principal.

- A key Exo makes in `$EXO_HOME/keys` is pinned when it is made (`crypto.save_key`). A name that already has a
  different pinned key is not changed: `exo keys pin NAME PUB --replace` does that by hand.
- A home made before D-040 gets a registry the first time it is opened: every key already in `keys/` is pinned, and
  every store under `principals/` is a principal id. That moment is trusted as it stands.
- `exo log verify` and `exo memory verify` pass the registry as `trusted`. An event fails when its key is not pinned,
  or when its author has its own pinned key and the event is signed by another one. The principal's words may be
  signed by any pinned key, because the agent's seat records them.
- `exo keys` lists the registry; `exo keys pin` and `exo keys principal` change it.
- `peers` holds peer seats of other homes, pinned with `exo keys pin --peer SEAT PUB` (#132). Their keys check that
  seat's peer messages and receipts only. `owners` and `allows` read `keys` alone, so a peer pin never signs a
  ledger event (`docs/PEERS.md` "Peer keys").

### Head anchor (D-040)

The triggers stop a careless edit, not a deliberate one: drop the delete trigger, delete the tail, put the trigger
back, and the shorter chain still links. So each append also writes the head (seq, row count, chain hash) to
`<ledger>.head` beside the database and to `$EXO_HOME/keys/heads/<hash>.json`, both 0600.

- The anchors are written after the append's COMMIT, from the committed head, in a second short transaction under
  the write lock, so they only move forward. A writer killed in between (SIGKILL, out of memory, power loss) leaves
  them one event behind, never ahead: verify still passes and the next append catches them up. If the anchor write
  fails after the COMMIT, the event stands and a warning is logged.
- When the anchor beside the ledger cannot be written, the copy under `keys/heads/` still is. Before it writes,
  an append catches the anchors up when the beside anchor trails the head or no anchor is at the head. It refuses
  ("cannot be written") when the beside anchor trails and cannot be written, or when no anchor is at the head and
  none can be written. So appends never go on more than one event past every anchor. The refusal names the files
  to make writable again. `exo log anchor` reports an anchor beside the ledger it cannot write in one line, with
  exit code 1. A keys/heads copy it cannot write is a warning only.
  `verify()` reports `anchored_seq`, the lowest seq an anchor holds, next to the head. When the anchors are more
  than one event apart, the result also carries a `warning` that names the one not being written. It still passes.
- The anchor check before an append counts the ledger once per anchor. The write after COMMIT takes indexed
  lookups only, so an append costs what it did before the anchor moved after COMMIT.
- The anchor under `keys/heads/` is named after the ledger's path with its folder resolved but not the file. A
  ledger file swapped for a symlink to a truncated copy still meets that anchor. A ledger file that is a symlink
  fails `verify()`, and `append()` and `anchor()` refuse it. A `Ledger` without an authority opens such a file
  read-only (#277), and only when it is a regular file, so opening it writes nothing through the link: no WAL
  switch, no tables, no file made at a dangling target. A target with no `-wal` file beside it is copied at open
  (#317), and reads come from the copy. They show the target as it stood at open. A fresh open shows later
  appends. The copy reads the target with SQLite's `immutable` flag, which makes no `-wal` or `-shm` file beside
  it but takes no lock. So the open checks the target's inode, size and mtime, and that no `-wal` file is there,
  before and after the copy. A copy taken while any of them changed is taken again. The copy goes into SQLite's
  private temporary database. About 2 MB of it stays in memory. The rest is an owner-only temporary file in
  SQLite's temporary folder, unlinked at once and gone at close. So an open reader holds disk space the size of the
  ledger, not memory: a 99 MB ledger adds 99 MB there and about 6 MB of memory. The copy is read-only. A target
  that has a `-wal` file is read live (another process has it open, or one crashed): SQLite reads the `-wal` file
  and may add or rewrite a `-shm` file. A target that changed during three copies in a row is read live too, and so
  is one whose copy fails while it stays the same (a full temporary folder). SQLite then makes `-wal` and `-shm`
  files beside it. They stay after the reader closes, until a writer closes the ledger while no reader has it open.
  Until then, later opens read the target live. Where SQLite cannot make them (read-only media, or a folder the
  reader cannot write), a target with no `-wal` file is read with the `immutable` flag instead, which makes no file.
  A target that is missing, is not a regular file (a FIFO would block the open) or holds no ledger reads as empty.
  An authority-mode ledger trusts its signed checkpoints: its appends skip the symlink refusal and write the
  projection through the link.
- An anchor is read only as a regular file: a symlink in its place is unreadable. Anchors are written through a
  fresh temporary file (`mkstemp`, with `O_EXCL` and `O_NOFOLLOW`), so a symlink planted in the anchor's folder
  cannot redirect the write.
- `verify()` checks every anchor it finds: the ledger must reach that seq and the chain hash there must match. It also
  fails when an append-only trigger was missing when the ledger was opened (the open puts it back).
- `append()` refuses to write when an anchor does not match, so the next event cannot hide a truncation.
- `exo memory verify` also fails a store whose `ledger.sqlite` is gone while an anchor says it held events. It reads
  the anchors without opening the database, so it does not put an empty one in its place. A store that never held
  an event reports nothing.
- With no anchor at all (a ledger from before D-040, or a copy without its `.head`), `verify()` is ok and says
  `anchored: false`. The next append anchors it. `exo log anchor` does it now.
- `exo log anchor` refuses to replace an anchor that does not match, since events may have been removed.
  `--force` replaces it and lists what it replaced. It is for a store an older Exo left stuck, not for a
  truncation: that version wrote the anchor just before COMMIT, so a writer killed in between left it one event
  ahead, and every later append was refused with "events were removed".
- `exo init` clears an anchor left at its path by a deleted store. `exo import-store` clears the one under
  `keys/heads`. A ledger copied with `--include-private` keeps its own `.head`.

What this does not protect against: a process running as the same user that rewrites the database and both anchor
files together, or deletes both anchors. The gate is what stands in the way of that: `keys/` and the store are
credentials to it. Nor a store folder deleted whole: `exo memory verify` lists only the stores that exist, so that
store's anchor under `keys/heads/` is read only with `--principal <id>`. Nor a store folder replaced by a symlink to
another folder: a crafted copy behind the link verifies clean, because the anchor under `keys/heads/` is named after
the folder the link resolves to. A code fix is tracked with the deferred scan of `keys/heads/` (#277). It has to
allow for the M1 migration in `docs/ARCHITECTURE.md` (symlink first, then move), where a legitimate move leaves its
anchor under the old path.

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
led.verify()            # {"ok": True, "checked": 2, "head": "...", "anchored": True}

store.find("posting rules", n=5)     # [(node_id, score), ...]
store.ctx("posting rules", budget=1500).text
assemble(store, "posting rules", 1500).node_ids
```

## Find

`find` ranks nodes with BM25. A node's title counts three times, its summary and `load_when` twice, and its tags,
id and body once. The first `find` tokenizes every node and keeps the tables it builds: each node's word counts and
length, how many nodes hold each word, and the average length. Later calls tokenize only the query (#254). `ctx`
and the default ranker of `assemble` call `find`, so they share the tables.

The tables belong to the node set they were built from. `reload()` makes a new set, and so does a `refresh()` that
sees a node file added, edited or removed. The reload drops the old tables at once, and the next `find` builds new
ones. So `find` always ranks the nodes that `nodes` returns. Like `nodes`, it does not see a file written after the
last read until a `refresh()` or a `reload()`. Callers treat the node dicts as read-only and change a node by
writing its file.

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
exo log verify | exo log anchor | exo log tail [-n 10] [--kind K]
exo keygen <name> [--out PATH]
exo keys [list | pin NAME PUB [--seat] [--replace] | principal ID]
exo memory [verify]                              agent logs and every store's ledger (D-040)
exo import-store <path> [--id ID] | exo interview | exo ingest <kind> <path>
```

The active principal comes from `--principal`, else `$EXO_PRINCIPAL`.

## Tests

`pytest` runs unit tests for crypto, ledger, store, retrieve and the CLI. The acceptance
test in `tests/acceptance/` copies a populated store (`EXO_ACCEPTANCE_STORE`) to a temp dir,
without `corpus/private`, and checks lint, find and ctx on three queries. It skips when no
such store is present.


### Explicit runtime shutdown

`CoreRuntime.close()` closes turn admission before interrupting its active loop states. It waits for
runtime-owned turn tasks before closing the session store or releasing the writer. Cooperative turns
record an interrupted terminal event and retain accepted steers. Accepted sends already waiting for a
session are saved as `shutdown_pending` context without another model call. A subsequent explicit turn
can use that context; shutdown does not automatically replay it.

The turn drain allows two seconds for interruption, then cancels remaining tasks and waits one second.
If an extension still refuses cancellation, close reports failure and leaves the store and writer open
rather than allowing that task to write through closed resources. Other shutdown cleanup still runs.
A forced cancellation may skip normal terminal events, but accepted steers are retained once the owned
tasks stop. Direct callers of `run_turn` remain responsible for their caller task's lifetime.

This is explicit core shutdown (#873). Closing a client connected to an external core remains a separate
operation and does not stop authorized background work. No suspend or client-disconnect policy is added.
