---
name: exo-handoff
description: Use when the user runs /handoff or asks you to write a handoff, a session summary for the next session, or a state-of-work note. Holds the template and the rules for writing it.
---

# Writing a handoff

A handoff lets a fresh session, with no memory of this one, pick the work up. You write it; the client only
gathered the facts. Write for a reader who is capable but has not seen any of this.

## Inputs
The `/handoff` message carries a JSON block of facts and the path to write. The facts come from the core, so
trust them over your recollection for anything they cover:
- `thread`: id, title, model, cwd, how many user turns.
- `git`: repo, branch, head, changed files (`changed`, with `changed_total`) and recent commits. `repo: null`
  means the cwd is not in a git repo; say so, because nothing there is tracked.
- `findings`: open harness findings (id, severity, title).
- `tasks`: subagent tasks still queued or running.
- `jobs`: open background jobs (empty when there are none or the harness does not have them yet).
- `decisions`: the latest summaries of this agent's memory tree. Rulings and choices are in there.
- `trace`: the last turns' timing, cost and flags.

Add what only you know: the goal in the principal's words, what was tried, why a choice was made. Do not
invent. If a section has nothing, write "none", not filler.

## Template
Write Markdown with exactly these sections, in this order.

```
# Handoff: <seat>, <date>

## Goal
One or two sentences: what the principal wants, in their words where you can.

## State
Where things stand now. Branch, uncommitted files, what runs and what does not.

## Done
What this session finished. Short bullets, each with the file or commit.

## Decisions
Choices made and rulings the principal gave, each with the reason. Quote a principal instruction verbatim.

## Next
Ordered steps for the next session. The first one should be doable without asking anything.

## Blockers
What is waiting on someone or something: an approval, a credential, a failing test, an open finding.

## Open jobs and tasks
Background jobs (id, command, state) and subagent tasks still running, and what to do with each.

## Paths
Files and folders the next session needs, one per line, absolute.
```

## Rules
1. Write the file with your normal write tool, to the path given. It goes through the approval gate like any
   other write; do not work around a denial. Create the folder first if it is missing.
2. No secrets: never put a key, token or password in the file, even if you saw one.
3. Keep it under about 80 lines. A handoff that needs scrolling is not read.
4. Facts, not feelings. No em dashes. Say what is not done as plainly as what is.
5. When the file is written, reply with the path and a two-line summary. Do not repeat the file.
