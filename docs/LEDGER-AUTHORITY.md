# Protected append authority

`exo.ledger_authority` supplies an optional backend for the existing portable ledger format. The normal Exo installation keeps its existing local-key behavior. Setting `EXO_LEDGER_AUTHORITY_REQUIRED=1` requires a named, installed backend in `EXO_LEDGER_AUTHORITY`; missing configuration, a missing backend, an unknown writer, or unavailable established state fails closed. No production key is generated or migrated by enabling these variables.

The factory resolves one `exo.ledger_authorities` entry point. Its callable receives `(projection_path: Path, writer: str | None)` and returns a bound client with an independently pinned `identity`, `writer`, and `append`, `lookup`, `head`, and `read_since` methods. `writer=None` requests verification access through an already provisioned caller identity; it does not create a writer or trust key. `open_authority` resolves that client without opening or repairing the local projection. The OS adapter authenticates this client/service channel and protects the authoritative journal and keys under a separate service account. The portable library alone does not provide that process boundary.

## Append contract

The pinned manifest is `{version: 1, stream_id, checkpoint_pub, writers: {alias: {pub, author, authors}}}`. Each writer has an explicit list of permitted author attributions. The writer alias selects the signing key; the event's `author` names the attributed speaker. For example, an agent key signs a captured principal utterance without claiming that the principal's private key signed it. Checkpoint and witness keys are separate from event and peer/delegation keys.

`append(operation_id, expected_head={seq, chain}, intent={author, kind, body, session, refs})` returns `{event, checkpoint}`. Only the service creates timestamps, links, event IDs and signatures. It serializes every writer in one stream with SQLite `BEGIN IMMEDIATE`, validates the expected head against its own journal, and commits the complete event, signed checkpoint and unique operation binding under WAL/FULL durability before returning any signature. Committed records are also the durable checkpoint outbox; there is no separate enqueue step that can be lost.

Repeating the same operation ID with the same writer and intent returns the original record, including after an ambiguous reply. Reusing the ID with different content or a different writer is refused. `HeadConflict.head` supplies a signed current checkpoint; the caller synchronizes and retries the same operation. A transport failure raises `AppendOutcomeUnknown(operation_id)`: look up that ID and repair the projection, without repeating any associated external action. `ProjectionPending(operation_id, record)` means the authoritative event is already committed but its local copy could not commit. Neither condition authorizes retrying an external side effect. Higher-level workflows must preserve their operation IDs when they require recovery across a caller process crash.

`head(seq=None)` returns the latest or exact signed checkpoint. `lookup(operation_id)` returns the original record or `None`. `read_since(seq, limit=64)` is bounded by both count and encoded bytes; a short batch is **not** an end marker. Replay continues to the previously authenticated head sequence. Intents reserve 32 KiB and records reserve 16 KiB within the 1 MiB wire document budget; replay reserves a further 4 KiB envelope budget.

`AuthorityJournal.provision(...)` is an explicit administrator-only creation primitive. Normal construction opens established state with SQLite `mode=rw`, checks identity/key agreement and the complete history in a coherent read transaction, and refuses missing, damaged or non-WAL state. It never re-creates a deleted journal or regenerates a lost key. OS provisioning must separately record the expected stream identity outside rollback, refuse partially provisioned state, and protect the directory. SQLite durability relies on the host filesystem honoring synchronization. Restoring both protected state and its trust root to an older backup cannot be detected locally without an independently retained checkpoint.

## Local projection and writer coverage

`open_writable_ledger` selects the authority before any local key read or generation and repairs/verifies the local projection before returning it. Each append validates the full local prefix, requests an authoritative append, then copies committed events. Copy loss is repairable; divergent or unconfirmed local tails are refused. Legacy event fields, IDs, signatures and hash chains are unchanged. `Ledger.author` and `Ledger.writable` work for both backends; a remote ledger has no `Signer` or private key.

These core writer paths use the factory:

| Writer path | Authority alias | Attribution |
|---|---|---|
| exo-gate `LedgerSink` | `exo-gate` | configured gate author |
| exo-memory `_Runtime` | `exo-memory` | configured agent and principal/capture authors |
| exo-learn `Runtime` | `exo-learn` | configured learner and explicit principal authors |
| `exo log append` | requested author | requested author |
| `exo ask answer`, `next --mark` | requested author, otherwise `agent` | requested author |
| consolidation/proposal commands | requested seat | requested seat or explicitly attributed principal |
| `exo ingest` | requested seat, default `ingest` | principal, adapter speaker identities, ingest seat |
| onboarding answers/questions and import | principal / `exo-onboard` | corresponding principal / interviewer |

Aliases and attribution lists still require explicit OS provisioning. Unknown custom seats or imported Hermes profile authors are refused until an administrator provisions them; they do not cause automatic key creation. Existing generic peer, bus and delegation signing stays separate and is not routed to this service. Read-only legacy callers do not gain write authority. Direct-key `Ledger` construction and append are refused in required mode, including a signer assigned after construction. Arbitrary trusted Python code can still generate unrelated keys; independent verification must pin the protected authority identity rather than accepting those signatures.

The OS `ExoLedgerSink` is a separate integration in Exo OS. Required gate execution must also retain its audit-failure blocking behavior. Memory capture and some learning paths remain best-effort at their host hooks: this change does not promise that every interaction is recorded, or that every non-gate mutation is atomic with its audit. It protects the history that was accepted by the authority.

## Independent verification and witness

Run the portable verifier with an independently obtained identity and signed checkpoint chain, not trust files supplied by the owner of the ledger being checked:

```sh
python -m exo.ledger_authority.verify \
  --ledger copy.sqlite --identity pinned-identity.json --checkpoints checkpoints.jsonl
```

The verifier opens SQLite read-only and never creates a missing copy or repairs its triggers. It distinguishes `verified_prefix`, `missing_tail`, `divergent`, and `unconfirmed_tail`. Every result explicitly reports `fresh: false`: a valid historical checkpoint alone does not establish the latest head. With an authority configured or required, both `exo log verify` and the ledger portion of `exo memory verify` contact the pinned authority, validate its captured head and signed replay, and inspect the copy read-only. They report deleted tails, divergent rows, unconfirmed extra rows, or unavailable verification without repairing the evidence; unavailable authority never falls back to local trust files. A second authenticated head read detects concurrent appends and causes a bounded retry; an actively changing stream reports unavailable verification. A stable head with a missing local tail can also mean an interrupted or in-flight projection commit, so that state alone is not proof of malicious deletion. The ordinary `Ledger.verify()` still checks the legacy portable format and local head files; it is not a substitute for an independently pinned authority checkpoint.

`WitnessJournal` implements the separate witness's durable state machine with its own pinned key. It accepts sequential authority checkpoints, refuses gaps/forks, and commits checkpoint plus signed receipt under WAL/FULL before returning the receipt. Identical retries return the original receipt. It retains no event bodies. `verify_receipt` checks independently pinned authority and witness identities and optionally receipt continuity. Deploying this witness under the same rollback/admin domain as the signer does not create independent protection. No off-machine endpoint, key, transport or receipt-delivery worker is enabled by this change. That deployment needs an independently administered destination and a fresh authenticated head query. Cached receipts prove only a witnessed prefix.

A compromised authorized daemon can submit invented future events, attribute content within its permitted writer lists, omit actions or stop sending checkpoints. It cannot extract the protected keys or obtain two conflicting committed histories through the append API. Independent witnesses can expose rollback/forks after witnessing a prefix; they do not prove honest consent or truthful descriptions of external actions.

## Validation

Disposable test stores cover competing writers, head conflicts, operation reuse, real process exits before/after commit, SQLite commit denial, lost replies, startup corruption, required WAL, missing established state, key mismatch, tampered projections, byte-bounded replay, local-copy repair, independent read-only verification, required runtime/CLI paths without key I/O, witness conflicts and stale-receipt limits. No test provisions or migrates a real user store.
