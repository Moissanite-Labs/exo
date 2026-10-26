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
was too small to summarize. The pins are kept per session id and follow it: a host that compacts into a
child session says so in `on_session_start`, with `boundary_reason="compression"` and `old_session_id`
set, and the pins carry to the child; a reset-only switch (/new, /resume, /branch) sends no
`on_session_start`, so the host calls `bind_session_state` instead, and a branch child inherits its
parent's pins. Open-decision pins keep only the newest 12. There the core also escapes the block's tags, so the model does not read it as a block
either (#279, `docs/MEMORY-MODEL.md` "Who wrote it"). Every summary has its tags escaped as it is made, and
`resume_text` escapes every tag in the handoff before `/exo resume` puts it into the system message. The file holds
the agent's last report and a summary, and it records the principal's rulings and corrections as quoted items under
their own headings, never as a pinned block, so they read the same after the escape. The summary under `## State`
is quoted too, so a summary that holds its own `## ` heading (a second "Decisions made", say) opens no section
(#315). List fields and the topic line are flattened to one line for the same reason (#353), and so are the header
values, the path line and the path in the launch line (#395).

`load_handoff` reads sections only after the header comment. The header ends at the first `-->` outside a quoted
value, so a `-->` inside `topic="..."` does not end it, and each value it recovers is flattened to one line. That
way a header value that spans lines in an older file opens no section, and the topic stays on one line where
`resume_text` and the `/exo resume` reply repeat it (#395). The reader keeps the first section of each name, with
two exceptions. Next steps, the one section written after every untrusted one, reads from its last copy in the
file (#353). Decisions reads from its one exact heading: the only one after the header, or the only one before
`## State` when the file has a single State heading. An older file can hold a forged copy before the real one (a
path or title that spanned lines) or after it (a summary written before #349 quoted it, or a list field written
before #353 flattened it). When two copies leave the real one unclear, the file reads as no rulings and no
corrections rather than the forged ones (#395). A heading with an extra space or tab after `##` is never read as
the section. `resume_text` puts the header on one line too, so its values reach the model as data. An older
file's path and launch lines, title, list fields and summary still reach it as that writer left them.

After `/undo`, exo-context drops the pins of the undone turns and the summaries the undo deleted, then writes again
each automatic handoff that may quote them, another thread's included (`docs/MEMORY-MODEL.md`, "Undo and failed
summaries"). A Hermes `/undo` in the CLI or TUI reaches exo-context through exo-memory, and the pins only the
undone turns held go the same way (#389). A handoff a compaction wrote before that keeps the ruling until the next
compaction writes it again.

Client code: `clients/tui/exo_tui/handoff.py`. Tests: `tests/clients/tui/test_handoff.py`.
