# Swarm Shared Knowledge

Explicit, owner-published knowledge for one saved Swarm group
(`exo.core.group_knowledge.GroupKnowledge`, built on `GroupStore` /
`SessionStore`).

## Model

- **Explicit owner publication.** The human owner deliberately publishes text
  plus source references for a key. Nothing is inferred or collected on the
  owner's behalf; there is no background writer.
- **Authorized member reads.** Only profiles that belong to the group may
  `read` an item, list `keys`, or view `history`, and only when the group's
  owner is also presented. Wrong owner, unknown member, or cross-group access
  all fail closed with `ValueError`. Owner authority here is metadata
  authority, not permission to impersonate a member. Caller identity must be
  resolved by a trusted runtime before calling this adapter.
- **Immutable revisions, CAS conflicts.** Every accepted publish appends a
  permanent revision row. History is never rewritten or deleted. Publishing
  passes `expected_revision` (0 for a new key) and the write happens inside
  one `BEGIN IMMEDIATE` transaction: a stale expectation raises
  `RevisionConflict` and rolls back, storing nothing.
- **No automatic private memory ingestion.** The store never reads sessions,
  memories, or messages. Writing to a member's private session creates no
  group knowledge; only an explicit `publish` call does.
- **No OS sandbox.** Content is stored as plain text in the local SQLite
  database. There is no OS-level isolation, encryption, or sandboxing around
  knowledge content. Hosts are trusted, and source references are validated
  only for shape (count, length, non-empty, unique), not resolved or fetched.

## Validation

Keys match `^[a-z0-9][a-z0-9_.-]{0,127}$`. Text is required and capped at
100,000 characters. Each publish needs 1–50 unique, non-blank source
references of at most 500 characters each.

## M02 consumption slice (member prompt context)

`GroupHost.start` exposes knowledge to a group run as one independent, explicit
opt-in:

```python
host.start(group_id, *, audiences=[...], knowledge=True,
           max_items=20, max_chars=16000)
```

- **Opt-in, off by default.** `knowledge` defaults to `False` and must be an
  explicit boolean (0/1 and truthy strings are rejected). With
  `knowledge=False` no reader is built and every prompt stays byte-identical to
  the knowledge-free prompt.
- **Host-built reader.** The reader is a `GroupKnowledge` over the host's own
  `GroupStore` (the same database), always constructed by the host, never
  supplied by the caller, and shared read-only by every member.
- **Bounded snapshot per member turn.** Each member turn appends one bounded
  snapshot of the group's current knowledge after the participant messages,
  as a quoted, labelled, untrusted reference document, never instructions,
  system text, or configuration.
- **Strict caps.** `max_items` is an integer between 1 and 20; `max_chars`
  is an integer between 256 and 16,000 **characters** (not bytes: the JSON
  envelope renders as real Unicode). Both are validated before the engine
  factory is invoked or any durable claim exists. An out-of-range or
  non-finite value fails closed as a `HostError`, and nothing is truncated to
  fit.
- **Deterministic key order.** Rows are fetched by key (`ORDER BY key`) and
  the envelope renders with fixed key order and stable separators, so the same
  knowledge always yields the same snapshot.
- **Whole oversize items omitted, flagged.** Items are fetched one past
  `max_items` so "more exists" is known. A single item whose rendered JSON
  would exceed `max_chars` is omitted whole, never sliced into invalid JSON
  and never silently dropped. The snapshot reports `truncated: true`. Per
  item, text beyond `max_text_chars` (100,000 by default) is cut with an
  explicit `…[truncated]` marker and the item's `truncated` flag is set. If
  the rendered envelope itself would exceed `max_chars`, rendering raises
  instead of emitting a half-written record. That failure happens before the
  engine turn, so no message is consumed and no turn is charged.
- **Owner-only publish, member-authorized read.** Only the stored owner
  publishes. Only group members may read items, list keys, or render
  snapshots, and only when the group's owner is presented. Wrong owner,
  unknown member, or cross-group access all fail closed. The snapshot is quoted
  reference data, not a directive: it can never change permissions or
  audiences.
- **No full-Swarm-completion claim.** Knowledge is one input to a member's
  prompt. It does not perform, claim, or guarantee group completion.

## Example

```python
knowledge = GroupKnowledge(groups)
item = knowledge.publish(group.id, owner="host", key="plan",
                         text="Ship M02 on Friday",
                         sources=["session:s-1", "doc:roadmap.md"])
item = knowledge.publish(group.id, owner="host", key="plan", text="...",
                         sources=["session:s-2"], expected_revision=item.revision)
seen = knowledge.read(group.id, owner="host", member="alpha", key="plan")
revs = knowledge.history(group.id, owner="host", member="beta", key="plan")
```
