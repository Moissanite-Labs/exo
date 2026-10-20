# Handoffs (D-032)

A handoff is a note that lets a fresh session pick up a workstream. In Exo the agent writes it, so it can say why
choices were made, not only what changed.

## `/handoff [path]`

1. The TUI runs the built-in command `handoff_facts` (a `command.run`, no new protocol command). The core gathers
   facts for the thread: the thread itself, git state of its folder, open findings and tasks, recent summaries,
   and the last turns of the trace.
2. The TUI sends one user turn that shows as `/handoff`. It tells the agent to load the `exo-handoff` skill
   (`skills/exo-handoff/SKILL.md`) and to write the note with its normal write tool.
3. The note goes to `<handoff folder>/<seat>-<date>.md` (`$EXO_HANDOFF_DIR`, else `handoff_dir` in `config.json`, else `$EXO_HOME/handoffs`), or to the path you give (a folder gets `<seat>-<date>.md`
   inside it). The write goes through the gate like any other write.
4. The agent replies with the path and two lines of summary.

The facts are capped at 14,000 characters. The agent can run `exo trace` for more.

An engine with a required tool executor omits Git facts and includes an explicit
unavailable note. Workspace Git configuration can launch programs even for
`git status`, so the host never collects these facts from a model-writable repo.
Any Git inspection requested through a tool must run through the executor.

## Rules the skill sets

The note keeps your own words for rulings and corrections, word for word. It is self-contained, so a new session
needs nothing else. See the skill for the template.

## Older module

`src/exo/handoff.py` is the earlier file-based handoff API (`write_handoff`, `load_handoff`, `resume_text`) with
pinned verbatim blocks. The `exo-context` plugin uses it for `/exo branch`, `/exo handoff` and `/exo resume`.
The TUI `/handoff` command does not use it.

Its rulings and corrections, the pins included, come only from the principal's own messages (D-040). Message dicts
are read through `provenance.author_of`, meta tags included. A user message whose author is not the principal is
skipped: a peer, a bus seat, a job notice, a context note. A peer, bus or job tag in `meta` refuses at any role. A
pinned block is carried only from a message that opens with it (exo-context's own block). A row opening with a
compaction marker (a host's summary) carries nothing itself, in a user row as much as an assistant one: a stock
summary a host built on its own reads as the principal's turn, and nothing separates it from one exo-context
escaped. exo-context keeps the continuity from its session pins, observed only from the principal's own messages. One
inside a skipped message, a tool result or the agent's reply is not carried. Neither is one
quoted further down a message, such as a peer's turn the Exo core's system message quotes word for word because it
was too small to summarize. There the core also escapes the block's tags, so the model does not read it as a block
either (#279, `docs/MEMORY-MODEL.md` "Who wrote it"). Every summary has its tags escaped as it is made, and
`resume_text` escapes every tag in the handoff before `/exo resume` puts it into the system message. The file holds
the agent's last report and a summary, and it records the principal's rulings and corrections as quoted items under
their own headings, never as a pinned block, so they read the same after the escape. The summary under `## State`
is quoted too, and `load_handoff` keeps the first section of each name, so a summary that holds its own `## `
heading (a second "Decisions made", say) cannot replace the principal's section or drop a real ruling (#315).
List fields and the topic line are flattened to one line for the same reason, and Next steps, the one section
written after every untrusted one, reads from its last copy in the file (#353). After `/undo`, exo-context drops
the pins of the undone turns and the summaries the undo deleted, then writes again each automatic handoff that may
quote them, another thread's included (`docs/MEMORY-MODEL.md`, "Undo and failed summaries").

Client code: `clients/tui/exo_tui/handoff.py`. Tests: `tests/clients/tui/test_handoff.py`.
