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

Its rulings and corrections, the pins included, come only from the principal's own messages (D-040). A user message
whose `author` or `principal` flag says otherwise is skipped: a peer, a bus seat, a job notice, a context note. A
pinned block inside such a message, a tool result or the agent's reply is not carried. After `/undo`, exo-context
drops the pins of the undone turns and the summaries the undo deleted, then writes again each automatic handoff that
may quote them, another thread's included (`docs/MEMORY-MODEL.md`, "Undo and failed summaries").

Client code: `clients/tui/exo_tui/handoff.py`. Tests: `tests/clients/tui/test_handoff.py`.
