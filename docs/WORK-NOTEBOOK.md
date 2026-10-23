# Session working notes

**Status:** In progress (D-086, [#506](https://github.com/aska-digital/exo/issues/506)). Implemented on the feature branch and awaiting review.

Exo can save an agent's working notes for one conversation: the goal, constraints,
steps, blockers, next action and references to transcript evidence. Resuming that
conversation restores a bounded snapshot. Notes are optional in every profile.

## Enable and inspect

```sh
exo config set work_notes.enabled true
exo stop
exo
```

Configuration is read when the core starts. Ask the agent to maintain working
notes for the task. In the TUI or `exo chat`, `/work` shows the current document
and `/work history` lists recent revisions. These commands require a principal
connection and pass through the normal tool gate and configured executor.
The web composer does not yet execute `/work`.

An example configuration in `$EXO_HOME/config.json`:

```json
{
  "work_notes": {
    "enabled": true,
    "max_bytes": 16384,
    "resume_chars": 3000,
    "max_revisions": 1000
  }
}
```

| Setting | Default | Accepted values |
| --- | --- | --- |
| `enabled` | `false` | Boolean. No profile turns this feature on. |
| `max_bytes` | `16384` | Integer from 1024 to 32768. Maximum UTF-8 bytes in each normalized document. |
| `resume_chars` | `3000` | Integer from 512 to 8000. Complete rendered snapshot, including its labels and truncation notice. |
| `max_revisions` | `1000` | Integer from 1 to 10000. Maximum saved revisions per session. |

Unknown settings and wrong types fail before runtime resources open. With the
feature omitted, composition does not import the notebook module. With it off,
there is no notebook table creation, tool, command listing, prompt contribution,
worker or handoff source. Existing saved revisions remain on disk.

## Document and tool interface

The `work_notes` tool accepts `read`, `update` and `history`. It uses the current
tool context's session. It accepts no target session or author argument.
An update replaces the complete document and appends a revision:

```json
{
  "action": "update",
  "expected_revision": 0,
  "document": {
    "goal": "Fix the reported API error",
    "constraints": ["Preserve the public response format"],
    "steps": [
      {"id": "reproduce", "text": "Reproduce the error", "status": "done"},
      {"id": "verify", "text": "Run the affected tests", "status": "pending"}
    ],
    "blockers": [],
    "next_action": "Inspect the response validation",
    "evidence": [
      {"kind": "tool", "id": "the-actual-completed-call-id", "note": "Reproduction output"}
    ]
  }
}
```

Use `{"action":"read"}` to get the current revision. Revision zero means there
are no saved notes. Updating requires `expected_revision` and `document`.
A stale revision fails without changing state. Read the latest document before
reconciling another writer's changes. There is no silent merge or retry.

`{"action":"read","revision":2}` reads a saved revision.
`{"action":"history","limit":10}` lists revision metadata, newest first.
History accepts limits from 1 to 20 and omits document bodies. A document needs
a nonempty goal. Other fields default to empty strings or lists.

| Field | Bound |
| --- | --- |
| `goal`, `next_action` | 2000 characters each. |
| `constraints`, `blockers` | 20 strings each, at most 500 characters per string. |
| `steps` | 40 items. Unique IDs of at most 40 letters, digits, underscores or hyphens. Text at most 500 characters. |
| Step `status` | `pending`, `in_progress`, `done` or `blocked`. |
| `evidence` | 40 unique references. Each has `kind`, `id` and optional `note`. IDs at most 160 characters, notes at most 500. |

Unknown document, step and evidence fields are rejected. The byte limit applies
after optional fields are filled and the document is encoded as JSON.

Evidence kind `message` names a visible message ID. Kind `tool` names a call ID
with a visible completed tool result. Both must exist in this session's actual
transcript. An uncompleted call, hidden message or another session's record fails
validation. This checks record presence. It does not establish that a claim is
true, that a tool succeeded, or that a step marked `done` was independently checked.

## Resume, handoff and undo

The next turn's prompt includes a deterministic snapshot, read afresh from the
session database. It labels all notes as agent-authored claims and escapes
pinned-instruction tags. If the snapshot exceeds `resume_chars`, a visible notice
directs the agent to read the complete revision. No model call builds the snapshot.

`handoff_sources["work"]` supplies the same bounded snapshot to existing handoff
facts. This adds working state to the existing handoff path. It does not copy
notes into principal memory or the signed ledger as rulings.

Each revision records the latest visible transcript message when it is written.
Undo that hides or removes this anchor makes the revision stale. The prompt and
handoff then show only a stale-note notice, with no saved goal or next action.
Explicit reads retain the stale document for review. A new revision can record
the corrected state. Undo does not delete notebook history or roll it backward.

Forks start without notebook revisions, including forks of private threads.
Subagents and scheduled runs cannot use the notebook. Private conversations can
save notes, scoped to that conversation. Notes are not inherited or included in
another thread's snapshot. The current engine agent must own the session.

A fork still copies ordinary transcript messages, which can include earlier
`work_notes` tool results. Nonprivate transcripts retain the existing shared
agent-log behavior. Notebook scope controls saved revisions and automatic
snapshots. It does not change transcript copying or memory isolation.

The configured principal is bound when a session's first revision is written.
Changing that principal refuses access to existing notes. An installation with
no configured principal uses an empty local binding. Selecting a principal later
does not silently adopt those earlier notes.

Session transcripts currently have no principal binding. Changing the configured
principal does not add a new access boundary to those existing transcripts.
The notebook's principal check applies to its own saved revisions and snapshots.

## Storage and execution

`exo.work_notebook.NotebookStorage` owns the `work_revisions` table in the existing
`sessions.sqlite`. It concentrates access to the session store's private `_q`
and `_tx` adapters. Revision comparison, evidence checks, anchor selection and
insertion share one SQLite transaction. Concurrent connections cannot overwrite
each other's revisions. The module creates no separate database or worker.

History is append-only. At `max_revisions`, further updates fail and the existing
revisions remain readable. Start a new conversation for further notes. There is
no automatic deletion, retention job or archival service. The size and revision
caps bound each session's notebook growth.

The model's tool call must appear as a visible assistant call in the same
session and turn, matching the tool context's call ID. `/work` is a principal-only
read path. Both dispatch through the registry. The gate classifies reads as
`read` and updates as `local_write`, so existing category policies still apply.
The configured executor can refuse notebook operations without a host fallback.

Notes cannot create goals independently, execute steps, answer questions, grant
permissions, alter budgets or approve actions. Every revision has `author=agent`,
including notes saved at a person's request. Principal instructions, approvals
and signed evidence retain their existing paths and authority.

## Validation

`tests/test_work_notebook.py` covers real composed fake-provider turns with the
real gate, gate and executor denials, restart, concurrent connections, principal
and session isolation, forks, undo, evidence validation and strict limits. It
also checks workspace-instruction composition, handoff snapshots and disabled
composition in a fresh Python process.
