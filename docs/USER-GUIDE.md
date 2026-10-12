# Exo user guide

This guide shows you how to use Exo day to day. It is short on purpose. Each section ends with a link to the
technical page that has the detail. The map of all docs is `docs/DOCS-MAP.md`.

## 1. Install and first run

Install steps are in the README (`## Quick start`: install, then type `exo`) and `docs/INSTALL.md`. Keys and models are in `docs/PROVIDERS.md`.
Setting up your own store and the first interview are in `docs/ONBOARDING.md`. This guide does not repeat them.

After install, set up a model provider. Pick one way; all three store the key the same way and show the same
status:

    exo providers                 # every provider: status, where its key is, models, default
    exo providers add anthropic   # paste the key at the prompt (no echo), then a test call runs
    exo providers test anthropic  # one tiny live call: ok with latency, or why it failed

Or type `/providers` in the terminal client, or open the Providers tab in `exo web`. Never paste a key into chat or
type it after the command name. If no provider works, `exo` and `exo web` tell you. Details and the security of
the web route: `docs/PROVIDERS.md`.

Then start the terminal client by typing the command, in the folder you want to work in:

    exo                     # same as `exo tui`; flags pass through: exo --resume (or `exo resume`), exo --seat NAME

The first time it runs on an `EXO_HOME`, the agent gives a short orientation (`/intro`) and turns on tutorial mode.
If no model provider works yet, the providers panel opens first and the orientation waits until you add one.
Skip the orientation with `exo --no-intro`. Upgrade with `exo update`; `exo --version` shows what you have.
More: `docs/ONBOARDING.md`.

## 2. The two clients

Both clients speak the same protocol to the same core. Use either, or both at once.

**Terminal (`exo tui`).** Type a message and press Enter. Options: `--resume` continues the newest thread,
`--session ID` continues one, `--seat NAME` picks the seat, `--title TEXT` names a new thread. Keys:

| Key or prefix | Does |
|---|---|
| `Esc` | Interrupts the running turn. |
| typing while a turn runs | Steers it: the text lands at the next tool step. The prompt shows `steer ›`. |
| `@path` | Adds that file to your message. |
| `!command` | Asks the agent to run that shell command and show the output. |
| `ctrl+p` | Opens the command palette. |
| `ctrl+o` | Expands or hides tool output. |
| `ctrl+y`, `ctrl+e`, `ctrl+r` | Accept, edit or reject the last answer. This is feedback the learning loop uses. |

The screen shows the model that served each call, how full memory is, how much of the call came from cache, the cost,
and the number of open findings. Screenshots are in `docs/ui/` (for example `docs/ui/01-empty.png`,
`docs/ui/03-approval.png`, `docs/ui/05-context.png`). The look is described in `docs/UI.md`.
Set `EXO_REDUCED_MOTION=1` to turn motion off. `NO_COLOR` turns colour off.

**Web (`exo web`).** Run `exo web` in a terminal. It opens the page in your browser for you. If it does not (no
browser, or `--no-open`), open the link it prints: `http://127.0.0.1:7342/#token=...`. The part after `#token=` is a
key for this launch only; the page needs it for everything and refuses requests without it. After you restart
`exo web`, use the new link: the old one stops working. Run `exo` and `exo web` in either order: both
see the same agent and threads. `exo web` joins a terminal window's core, or `exo serve`, if one is running; if
none is, it starts `exo serve` in the background (log `$EXO_HOME/logs/exod.log`). That background core keeps running
after you close the web page, so a later `exo` joins it. `exo stop` ends it.
It shows approvals, decisions waiting on you, live sessions, tasks, projects and cost. Screenshots are
`docs/ui/web-*.png`. Details are in `docs/ui/WEB.md` and `plugins/exo-command/README.md`.

## 3. Slash commands

Type `/help` in the terminal client for the live list. This table is the same list.
The technical reference, with arguments and where each command is implemented, is `docs/COMMANDS.md`.

| Command | Does |
|---|---|
| `/new` | Start a new thread: a fresh focus, the same memory. |
| `/new private` | Start a thread that is not remembered. Its words never enter shared memory or search. |
| `/sessions`, `/resume` | Pick one of this agent's threads to switch to. |
| `/fork [title]` | Fork this session. |
| `/undo` | Undo the last turn. |
| `/clear` | Clear the transcript view. History is kept. |
| `/cwd` | Show this thread's working folder. |
| `/context` | Show what the next model call carries, part by part, with token counts. Read-only. |
| `/compact` | Summarize now what is eligible, then show tokens before and after. |
| `/restart` | Reopen this session in a new terminal tab and exit this one. |
| `/exit`, `/quit` | Quit. |
| `/model [ref]` | Pick the model for this session, or set it with a reference. `/model default <ref>` sets the default. |
| `/default [ref]` | Set the default model for new sessions, or pick one. |
| `/usage` | Show context size, cost and the model in use. |
| `/providers` | Set up model providers: status, add or replace a key in a masked field, test, remove, default model. `/providers add <name>` opens the key field. |
| `/trace [N]` | Show timing, route and estimated cost for the last N turns (default 10). |
| `/findings` | Show what the harness found wrong with itself. |
| `/grants` | List standing approvals. `/grants revoke <id>` removes one. |
| `/gate` | Show whether policy files changed this session. `/gate accept` adopts the change. |
| `/peers` | List other live sessions you can message. |
| `/send <peer> <text>` | Message another session yourself. |
| `/handoff [path]` | The agent writes a handoff note for the next session. |
| `/intro` | The agent's orientation: what Exo is and how to use it. |
| `/tutorial on`, `off`, `reset` | Tips the first time something comes up. Bare `/tutorial` shows the state. |
| `/panel` | Reopen the last panel. |
| `/help` | List every command. |

Plugins add commands. They show in `/help` under `other`. `/exo` (the dispatcher) and `/orchestra` are examples when
those plugins load. The core answers `/context`, `/compact`, `/trace` and `/findings` itself. `/grants` comes from
the `exo-gate` plugin. The web client runs `/context`, `/findings` and `/trace` from its composer.

## 4. Memory

Exo has one memory per agent, not one per window. A seat is one agent. Every window, channel and scheduled job of
that seat is a thread of the same mind. Open a second window and the agent already knows what the first did.
Different seats are different agents. They share your store and talk through peers.

- A thread is a focus. `/new` starts one. Memory carries over.
- The log keeps every word, word for word. Nothing is lost when the context is compacted.
- Old parts of the log are folded into summaries so the model call stays small. This happens by itself.
  `/compact` does it now.
- `/context` shows the parts of the next call: the system text, the summary tree, news from other threads, and this
  thread's recent messages.
- Your rulings and corrections are kept verbatim in summaries and are copied to your principal store, so a
  summary cannot blur an instruction. `/context` says how many of your messages are not yet captured.
- A private thread (`/new private`) is logged but never shared.

Details: `docs/MEMORY-MODEL.md`.

## 5. Background jobs

The agent can start a long command, such as a build or a test run, and keep working. It uses `bash` with
`background: true`, then `jobs`, `job_output` and `job_kill`. When the job ends, the thread gets a short notice with
the exit code and the last lines of output. The notice is never from you and cannot approve anything.

- Esc does not stop a job. Ending the core does.
- Limits (four at a time, two hours each) are per seat. See `docs/JOBS.md`.

## 6. Approvals

Some actions wait for you. The gate looks at each tool call and decides: allow, log, ask or deny, from your policy
files (`policies/gates.yaml` in your store). When it asks, a panel shows what the agent wants to do and why.

In the terminal: `o` allows once, `a` allows always, `d` denies. On the web page: `y`, `a`, `n`.

What the gate asks about, and why:

- **Credentials always ask.** Anything that reads or fills a password, token or key asks every time. An "always"
  answer is never stored for credentials.
- **Outward acts are bound to the exact text.** A post, message, email, social post, payment, or anything public or
  irreversible is approved for the exact text, title and target you saw. If the agent changes one character, you
  are asked again. This is so a draft you read cannot be swapped for another.
- **Posts to public places are swept for internal names**, such as seat names, home paths and model ids. A hit holds
  the post and lists what it found.
- **Code the gate cannot see into asks (D-038).** A script file, `python -c`, `node -e`, a heredoc fed to a shell,
  `make`, `npm run` or `npm test`, `npx`, `xargs`, `find -exec`, a variable or `$( )` in the program's place, a shell
  function, `source`, or a program the gate does not know: each asks once, for that exact command. Plain work still
  runs: `pytest`, `uv run pytest`, `git status`, `ls`, `rg`, `npm install`, `cargo test` and the like.
- **Your store is like a key (D-038).** A write to your store's `policies/`, `state/` (your standing grants), the
  ledger, `keys/`, seat files, or the core's socket asks you every time, and no "always" covers it. Reading your
  policies does not ask.
- **Policy edits during a session only tighten (D-038).** If a policy file changes while Exo runs, the stricter of
  the old and new policy applies. Run `/gate accept` to adopt the change, or restart. `/gate` shows the state.
- **Hard to undo, or visible to others.** Under the default policy, an act that cannot be undone asks. So does an
  act on a public place. Reading and local edits do not. Your `policies/gates.yaml` can change any cell.

Who may approve: you can always, from the terminal window or the web page. A policy file can let named seats or
roles approve some kinds of act, and they must sign. A peer never can. A message from a peer that tries to approve
something is refused and shown to you. The agent cannot answer its own approvals: a command it runs that talks to
Exo is not you, whatever it claims, and only you can answer "always".

"Always" answers become standing grants. They expire (7 days for outward acts, 30 days for others) and an outward
grant covers one exact text. `/grants` lists them. `/grants revoke <id>` removes one.

Details: `docs/GATE.md`.

## 7. Peers

Peers are other live sessions on the machine: Exo sessions, Hermes agents and bus seats.

- `/peers` lists them. `/send <peer> <text>` sends a message from you.
- An agent can message a peer with its `peer_send` tool.
- Your own sessions deliver at once. A message from another person, or from a bus seat, is held until you allow it.
  A held message that nobody answers expires after 30 minutes.
- A peer message shows as "from another session, not from you". It is input, not an order.

Details: `docs/PEERS.md`.

## 8. Seats

A seat is one named setup of an agent: whom it serves, which models, tools, skills and plugins it has, and where its
threads start. Seat files live in `$EXO_HOME/seats/`. Run one with `exo serve --seat NAME` or `exo tui --seat NAME`.
Ready-made role templates are in `plugins/exo-orchestra/seats/`. To write your own, see `docs/SEATS.md`.

A seat can set the folder its threads start in, with `[session] cwd` in `seat.toml`. Exo warns once when a thread's
folder is not inside a git repo.

## 9. Cost and trace

- The status line and `/usage` show the session cost so far. It is an estimate in US dollars, priced per call from the
  catalog. A call with no known price shows `unknown`, never `$0`.
- `/trace` shows one row per turn: time, model that served, calls, fallbacks, tools, tokens and cost.
- `exo trace [SESSION] [--last N] [--calls] [--json]` shows the same from a shell.
- `/findings` and `exo findings` list problems Exo found in its own work, such as a slow turn or a model fallback.

Details and the cost rules: `docs/TRACE.md`.

## 10. Troubleshooting

| Symptom | Try |
|---|---|
| No model answers | Run `exo providers`. It shows which providers have keys and whether a test call worked; `exo providers test <name>` says why one fails. Add a key with `exo providers add <name>` or `/providers`. |
| A second `exo serve` refuses to start | One process writes an agent's log. Another `exo serve` or `exo tui` for that seat already holds it. Use that one, or stop it. |
| Cost shows `unknown` | The route has no price. Add one under `models.models` in `config.json` (`docs/TRACE.md`). |
| `code_find` is missing | It needs Node.js 22 or newer. `exo models` says why on its `code_find:` line (`docs/TOOLS.md`). |
| Learning does nothing | It needs a principal store. Without one it is off and says so once (`docs/LEARNING.md`). |
| A peer message never arrived | It may be held. Look for an approval card. Held messages expire after 30 minutes (`docs/PEERS.md`). |
| Memory looks wrong | Run `/context`, then `exo memory verify` to check the log's hash chain (`docs/MEMORY-MODEL.md`). |
| The agent seems slow | Run `/trace`. Look at the model that served and any fallbacks. |
| The warning about "not inside a git repo" | The thread's folder is not a repo. Set `[session] cwd` in the seat, or `/cwd` to see where you are. |
| The web page says "missing or wrong page token" | Open the link `exo web` printed this time. Each launch has a new token. |
| Keys must stay private | Exo never writes secrets through `config.set`, and removes known key values from tool output. See `docs/SEATS.md`. |

## 11. Your store and the ledger

Everything Exo knows about you sits in a **principal store**: a folder of small notes (nodes) and a signed ledger, a
log nobody can edit without it showing. These commands work on them from a shell. You rarely need them. The agent
reads and writes the store for you. They help when you want to look, check, or move things.

Pick the store with `--principal ID` or `--root DIR`, or set `EXO_PRINCIPAL` once. Stores live in
`~/.exo/principals/<id>` unless `EXO_HOME` says otherwise.

**Make or adopt a store**

| Command | What you get |
|---|---|
| `exo init <id>` | A new, empty store with its ledger. It becomes your active principal if you have none. Add `--interview` to answer the onboarding questions straight away. |
| `exo interview` | The onboarding questions for an existing store. It asks a few, saves each answer as a low-confidence "you said" note, and queues more for later. `--plan` shows the questions first. `--answers FILE` fills them from a file. Press Enter to skip, `q` to stop. Do not type passwords. |
| `exo import-store <path>` | Copies a store you already have into a new principal. The source is never touched. Private files and keys are left out unless you add `--include-private`. |

**Look things up**

| Command | What you get |
|---|---|
| `exo find <words>` | The best matching notes, one per line, with a score. `-n 20` for more. |
| `exo get <id>` | One note in full. `--body-only` skips the header. |
| `exo ctx <words>` | The best notes packed into a size limit (`--budget`, default 1500 tokens). This is what the agent sees. |
| `exo links <id>` | What a note points to, and what points to it. |

**Keep it healthy**

| Command | What you get |
|---|---|
| `exo lint` | A check for broken fields, links to notes that do not exist, orphans, and text that looks like a key or a phone number. It exits with 1 if it finds errors. |
| `exo graph` | Writes `graph/graph.json`, the notes and their links, for other tools. |
| `exo index` | Refreshes the note table in `INDEX.md`. |

**The ledger**

| Command | What you get |
|---|---|
| `exo keygen <name>` | A signing key in `~/.exo/keys/<name>.key`, readable by you only. It will not overwrite an existing key. Keep it private. |
| `exo log append <kind> --author NAME --text "..."` | Adds one signed event. Kinds include `note`, `ruling`, `utterance`, `question`, `answer`. Needs the author's key. |
| `exo log verify` | Re-checks every event, link and signature. Prints `{"ok": true, ...}` or fails. Run it when something feels off. |
| `exo log tail` | The last events. `-n 20` for more, `--kind ruling` to filter. |

Example: make a key, write a note, check the chain.

    exo keygen me
    exo log append note --author me --text "Prefer short answers."
    exo log verify

Every flag is in `docs/COMMANDS.md`. How the ledger and store work: `docs/CORE.md` and `docs/STORE-V2.md`. To review
what Exo proposes to learn from the ledger, see `exo consolidate` and `exo proposals` in `docs/TOOLS-CLI.md`.
