# Evidence-bound skill candidate review artifacts

Refs #678. `exo.skill_drafts` exports a proposed procedure as a private JSON review artifact, never as an
installed skill. The host supplies an established caller session and agent, a proposed title/purpose and
explicit character spans from visible session message text. Each proposed step is copied verbatim from its
source span and carries the session ID, message ID, sequence, role, offsets and full message-text SHA-256.
The digest is a payload identity, not a verified ledger signature or proof a procedure works.

```python
from pathlib import Path
from exo.skill_drafts import Excerpt, queue_draft

artifact = queue_draft(
    store, authenticated_session, authenticated_agent,
    queue=Path("/host-selected/private-review-queue"),
    title="Inspect a fixture before editing",
    purpose="Candidate procedure for review and generalization",
    excerpts=[Excerpt(seq=12, start=0, end=80)],
)
```

The span must exist in that exact visible message. Empty, duplicate, oversized and missing evidence is
refused. There are at most 16 steps of 2,000 characters each. Undo-hidden messages, other agents, private
sessions and descendants of private sessions cannot supply evidence. Tool calls/results are not silently
converted to instructions: this slice selects message text only. The host must not take caller identities
or the queue path directly from an untrusted model argument.

`draft_from_session` builds the artifact without writing. `queue_draft` additionally writes a unique `.json`
file with mode 0600 to a private 0700 directory using exclusive creation. A symlink queue or a directory
readable by others is refused. The host must choose a review directory outside skill discovery paths.
Nothing writes `SKILL.md`, registers a tool, invokes a model, installs a skill or executes recorded text.

All exports have `status: pending_human_review`, `loadable: false` and an explicit recorded-text-only claim.
A source message can contain an incorrect success claim or malicious instructions; recording it does not
endorse it. Title and purpose are proposals, and the artifact does not invent generalized steps or assert
success. A reviewer must inspect the evidence, remove sensitive material, test the procedure, generalize it
and decide whether to create an actual skill through a separate workflow. The review artifact has no
approval or installation operation.

Remaining work for #678 includes task-completion detection, model-assisted synthesis of procedures,
evidence beyond message text, an in-product review queue, revision history, controlled installation,
feedback/refinement and contextual nudges. This draft makes the evidence-bound export boundary executable;
it does not claim autonomous skill learning or close the full issue.
