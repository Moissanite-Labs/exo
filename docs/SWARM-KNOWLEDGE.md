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
  fail closed with `ValueError` when the supplied identity is unauthorized.
  Authorized reads of a missing key return `None` or an empty list within that
  group; keys and history never include another group's rows. Owner authority here is metadata
  authority, not permission to impersonate a member; caller identity must be
  resolved by a trusted runtime before calling this adapter.
  There is no separate owner-only read API: an owner who is not also a member
  cannot read by supplying the owner name as `member`. Hosts must not treat owner
  authority as permission to impersonate an arbitrary member.
- **Immutable revisions, CAS conflicts.** Every accepted publish appends a
  permanent revision row; history is never rewritten or deleted. Publishing
  passes `expected_revision` (0 for a new key) and the write happens inside
  one `BEGIN IMMEDIATE` transaction: a stale expectation raises
  `RevisionConflict` and rolls back, storing nothing.
- **No automatic private memory ingestion.** The store never reads sessions,
  memories, or messages. Writing to a member's private session creates no
  group knowledge; only an explicit `publish` call does.
- **No OS sandbox.** Content is stored as plain text in the local SQLite
  database. There is no OS-level isolation, encryption, or sandboxing around
  knowledge content; hosts are trusted, and source references are validated
  only for shape (count, length, non-empty, unique), not resolved or fetched.

## Validation

Keys match `^[a-z0-9][a-z0-9_.-]{0,127}$`. Text is required and capped at
100,000 characters. Each publish needs 1–50 unique, non-blank source
references of at most 500 characters each.

The limits count Python string characters, not encoded bytes, and accepted text
and source references are not trimmed or normalized. `expected_revision` must be
a nonnegative integer; bool is rejected. Validation failures and CAS conflicts
leave both the current-item and revision tables unchanged. Both writes are in the
same transaction, including rollback if a revision insert fails.

## Consumer and storage limits

Knowledge is participant/source data. Any future context consumer must render it
as quoted data, not privileged instructions. This storage layer does not inject
it into prompts or resolve/fetch source references.

Revision history grows without a per-key cap. `keys()` and `history()` materialize
their full results; this layer adds no pagination, pruning or retention policy.

Unicode scalar/UTF-8 validation is not uniform: a lone surrogate in text fails at
the SQLite encoding boundary, while one in a source reference can round-trip
through escaped JSON and fail later UTF-8 serialization. That existing limitation
is preserved here, without a new surrogate-handling rule or change to source
validation. The broader #1161 message-size policy and #969 decision allocation
remain separate questions.

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
