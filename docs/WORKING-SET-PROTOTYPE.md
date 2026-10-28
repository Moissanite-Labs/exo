# Offline working-set prototype (#625 slice)

`exo.working_set` selects a bounded set of provenance-bearing records. It is not
attached to runtime recall, prompt construction, a model, or any live store.
Callers must supply visibility from a trusted source; the selector is not an
authorization boundary. Unknown/private/protected visibility, private ancestry,
other agents and non-thread records are excluded even when pinned.

Each immutable `WorkingSet` belongs to one conversation and agent. Adjustments
return a new revision with inspectable pins/exclusions and can be reversed.
Queries refresh selection from current candidates, not a cache of earlier text.
Callers provide visible transcript IDs to avoid duplication. Conflicting source
IDs are dropped. Returned records preserve text, author, kind and a content hash;
the hash is not an authenticated ledger proof. Retrieved text grants no authority.

Limits are 256 candidates, 6,000 characters per record, 4,096 query characters,
128 adjustment/visible IDs and 32 selected items. Budgets count a documented
UTF-8 byte/4 estimate plus provenance overhead, not actual model tokens. Oversize
records fail explicitly; an entry that does not fit cannot block smaller entries.
Relevant principal corrections get priority; an unrelated correction is not made
globally authoritative.

Run the repeatable local example with:

```sh
PYTHONPATH=src python research/working_set/evaluate.py
```

The fixture covers two project tasks, a task change, a private-data query and a
known synonym failure. It reports precision/recall and estimated tokens against
a synthetic recency-only comparator. That comparator is not Exo's current recall.
These handwritten examples establish no representative quality or latency gain.

Still open in #625: trusted runtime retrieval and private-ancestry integration,
durable per-session adjustment UI/storage, real token accounting, the existing
retrieval baseline, long-session and representative multi-project evaluations,
and measured latency/citation quality. No automatic model calls run here.
