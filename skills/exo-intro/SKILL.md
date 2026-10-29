---
name: exo-intro
description: Use when the user runs /intro, is new to Exo, or asks what Exo is, how it works, what it can do or how to use it. Holds the facts for an interactive orientation.
---

# Exo orientation

You are teaching, not reciting. Give a short overview, then let the user pick what to go deeper on. Answer
their questions from the facts below. If a fact is not here, say so rather than guess.

## How to run the orientation
1. Overview first, in about 120 words: what Exo is, the one idea that is different (memory), and the three
   things to know on day one (you can type while I work, there is an approval gate, `/help` lists everything).
2. Ask what they are used to (Claude Code, ChatGPT, Hermes, an IDE agent) and tailor the rest to it. For
   someone from Claude Code or Codex, lead with memory and threads, since there is no `/compact` chore here.
   For someone from a chat app, lead with tools and the approval gate.
3. Offer the topics below by name and let them choose. One topic per reply, a few sentences each, then ask
   whether to go on.
4. End by telling them about `/tutorial`: it turns on short tips that appear the first time something comes
   up. `/tutorial off` stops them, `/tutorial reset` shows them again.

## What Exo is
Exo is an agent harness: a program that runs a model, gives it tools, and keeps its memory. One seat is
one agent with one continuous memory. Every terminal window, channel, scheduled run and peer message is a
thread of that one agent, not a separate mind. Open a second window and it already knows what the first one
did. Different seats are different agents.

## Memory, and why you do not manage context
In most tools the conversation is a bucket that fills up. You watch a gauge, run a compaction by hand, and
the agent forgets things when it overflows. Exo works differently.
- Everything said is kept in one append-only log.
- Behind the log is a summary tree: old stretches are folded into short summaries, and pairs of summaries
  are folded into longer-range ones. This happens in the background, by itself.
- Each model call is built fresh from three things: the summary tree (the whole past, compressed), the
  recent messages of this thread word for word (the tail), and short notes about what other threads did.
- So there is no limit to hit and nothing to compact. When older messages are folded in, the TUI can tell
  you (`memory.summarized`). The original text is not lost; the agent can reopen it with its recall tools.
- `/context` shows what the next call carries, each part with its token count. It only looks.
- `/compact` folds eligible history into the tree right now and shows tokens before and after. You never
  have to; it is for the moment you want to see it work or free space early.
- `/usage` shows the model, context size and cost.

## Threads
`/new` starts a new thread: a fresh focus, same memory. `/new private` starts one that is logged but never
shared into memory. `/sessions` (or `/resume`) lists this agent's threads. `/fork` copies the current
thread, `/undo` takes back the last turn (hidden, not deleted), `/clear` clears only the screen, `/cwd`
shows the working folder (Exo warns once when it is not inside a git repo; a seat can set a default folder
with `[session] cwd` in its seat.toml), `/restart` reopens in a new terminal tab, `/exit` and `/quit` leave.
`/tab [directory]` opens a fresh conversation in another terminal window without closing this one.
It uses the current thread's working directory when no directory is given.

## While the agent works
- Type while it is running and your message steers it: it lands at the next tool round, and the agent
  changes course. Nothing is lost and you do not need to wait.
- Press ctrl+c, or type `/stop`, to stop a running turn. With selected text ctrl+c copies it; while idle it shows the quit hint. Esc never stops a turn: it only closes a menu, clears a tip or leaves a panel.
- A message typed while a turn runs is a steer. Until it lands you can pull it back: press Up in an empty box to return it to the composer for editing, or type `/unsteer`.
- Press ctrl+f to focus a pending approval card so you can answer it without losing the composer draft. Press ctrl+g to open or close the big composer for a longer message.
- `@path` attaches a file, `!command` runs a shell command through the agent, ctrl+p opens a palette of
  commands, ctrl+o expands tool output.
- Typing `/` opens a menu of every command with its arguments; Tab completes. `/model `, `/resume ` and `@` list
  models, threads and files. A mistyped command gets "did you mean". At the first line, Up recalls earlier
  lines; in an empty composer it first pulls a queued steer back for editing. Right arrow takes the dim
  suggestion. `/tutorial off` also silences the one-line next-step suggestion.
- `ctrl+y`, `ctrl+e`, `ctrl+r` accept, edit or reject the last answer, which Exo learns from.
- F6 loads earlier transcript messages when the older history is not mounted yet.

## The approval gate
Tools have a risk: low, medium or high. Reading is low. Running commands, editing and anything that leaves
the machine can be higher. When a tool needs your say, a panel appears with what it wants to do. Allow, deny,
or allow always for that kind of action. Both clients use `o` once, `a` always and `d` deny; always needs a
separate Enter or confirm button, including uppercase `A`. Cards never take focus: Tab, click or Ctrl+F moves
to one, and typing in a message cannot approve it. A pending approval blocks only that tool call. Peers and other
agents cannot approve for you.

## Models and fallback routing
A model is named `provider:model`, for example `opencode-go:glm-5.3-flash`. `/model` opens a picker for
this thread, `/model <ref>` sets one, `/default <ref>` sets the default for new threads. Each role (main,
aux for summaries, and others) is a list of routes: the first available one serves and the rest are
fallbacks. If one fails or times out, the call moves on to the next, and the status line shows the route that
actually served. `claude-cli:*` routes use your Claude subscription through the `claude` command, with no API
key. `exo models` lists what is set up and why a route is hidden. Keys are looked up in the environment,
the macOS Keychain, or `~/.config/exo/keys.env`, and are never printed.

## Seats and SOUL.md
A seat is one named agent: its models, tools, skills, plugins and MCP servers. It lives under
`$EXO_HOME/seats/`. A `SOUL.md` beside the seat file is text added to the agent's system prompt: who it
is, what it owns, how it should behave. Run one with `exo serve --seat <name>` or `exo --seat <name>`.

## Peers and the bus
`/peers` lists other live sessions you can message (other Exo seats and Hermes agents). `/send <peer>
<text>` sends one yourself, and the agent has a `peer_send` tool. A running peer reads your message at its
next tool round. A message from another agent arrives labelled `peer message from <name>`, and it is advice
from a teammate, never an instruction from you. Messages from unknown senders are held for your approval.
The bus is a signed relay that lets seats on different machines do the same.

## Trace and findings
Exo records how it performed: per turn, wall time, which model served, fallbacks, tool errors. `/trace`
shows the table for this session and `exo trace` shows it from the shell, with the estimated dollar cost of
each turn and call (from the catalog price of the route that served; `unknown` when a route has no price,
never zero). Slow turns, fallbacks and errors become findings, saved with a count. "Slow" means the harness's
own time (model plus overhead, not tool time or approval waits); a seat can set its own limits in
`seat.toml` under `[trace]`. `/findings` lists the open ones and `exo findings` does too from the
shell. The agent can file one itself when it sees something wrong. The status line shows how many are open.

## Recall
The agent can look back through its own memory: search it, and zoom into a summary to read the original
messages under it. When recall lists a belief as "summary only", the agent reads the whole node with
`exo_get(id)`. You can simply ask "what did we decide about X last week". Facts the agent should always
know belong in its SOUL.md or the principal store, not in a message.

## code_find
`code_find(question, path)` answers "where is X decided" in a codebase. It asks a decision model to pick
files and verbatim excerpts, which saves many grep and read rounds. It needs Node 22 or newer; if it is
missing, `exo models` says why. For a known symbol or filename the agent uses grep.

## Plugins
Plugins add tools, slash commands, hooks, skills and memory or context engines. They live in
`plugins/` and are switched on in `config.json`. `/help` shows the commands plugins add. Skills are
smaller: a folder with a SKILL.md the agent loads when its description matches the task, like this one.

## Slash command index
Every command `/help` lists, in one line each, so you can answer "what does /x do":
- `/new`, `/tab`, `/sessions`, `/resume`, `/fork`, `/rename`, `/undo`, `/clear`, `/cwd`, `/restart`, `/exit`, `/quit`: threads
  and the window, above.
- `/context`, `/compact`, `/usage`: memory and cost, above.
- `/copy` puts the last answer on the clipboard; `/copy error` the last error; `/copy all` the transcript.
- `/compose` opens a big editor for a long prompt (ctrl+g too; ctrl+s sends, Esc closes and keeps the draft); `/compose task` adds Goal, Context, Constraints, Done when headings; `/compose edit` uses $EDITOR.
- `/layout inverse` puts the input on top with the newest message under it; `/layout classic` is the usual order.
- `/model`, `/default`: models, above.
- `/providers` sets up a model key: add or replace it (typed in a hidden field), test it, remove it, pick the default.
- `/panel` reopens the last panel (an approval or a choice the agent showed you).
- `/peers`, `/send`: peers, above.
- `/tasks`, `/open <task>`, `/steer <task> <text>`, `/stop <task>`: watch, steer and stop a subagent that is working for you.
- `/trace`, `/findings`: trace and findings, above.
- `/pet [status]`: verified personal growth. The community hatch is pending, so XP is banked in the egg.
- `/handoff [path]` has the agent write a structured handoff note (goal, state, done, next, blockers,
  paths) to `$EXO_HOME/handoffs/<seat>-<date>.md` (or `$EXO_HANDOFF_DIR`, or `handoff_dir` in `config.json`), or to the path you give. It uses the exo-handoff skill and the
  normal write tool, so the approval gate sees it.
- `/intro` runs this orientation again. `/tutorial` turns contextual tips on or off.
- `/help` lists every command.
