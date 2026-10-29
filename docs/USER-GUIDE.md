# Exo user guide

This guide shows you how to use Exo day to day. It is short on purpose. Each section ends with a link to the
technical page that has the detail. The map of all docs is `docs/DOCS-MAP.md`.

## Words Exo uses

| Word | Means |
|---|---|
| principal | You, the person the agent works for. Only what you type counts as your ruling or correction. |
| store | Your principal store: a folder, `~/.exo/principals/<id>`, with what Exo knows about you, your policies and your ledger. The installer's interview or `exo init` makes it (§11). |
| ledger | A signed log in your store. Entries are only added, and a changed entry shows when `exo log verify` runs (§11). |
| seat | One named agent, with its own models, tools and memory. With no seat you talk to the agent named `default`, which the terminal header shows as `main` (§8). |
| thread | One conversation with that agent: a terminal window, a web chat or a scheduled run. All threads of a seat share its memory (§4). |
| gate | The check on each tool call: allow, log, ask you or deny (§6). |
| grant | An "always" answer to the gate. It expires (§6). |
| finding | A problem Exo found in its own work, such as a slow turn or a model fallback. `/findings` lists them (§9). |
| wave | A stage of the build plan. Waves 1 to 3 are built. You need the word only to read the project's plans. |
| D-number | A numbered decision, such as D-038, in `DECISIONS.md`: what was chosen, why, and what was rejected. |

## 1. Install and first run

**Continuing after interruption.** Exo automatically includes a short index of recent
tool and job records when the same ordinary conversation continues. `/checkpoint`
shows it in the TUI or `exo chat`. Interrupted tool effects remain unknown until
checked. It never replays work. Disable with
`exo config set work_checkpoint.enabled false` and restart. See
[automatic continuity checkpoints](WORK-CHECKPOINT.md) for limits and privacy.

Install steps are in the README (`## Quick start`: install, then type `exo`) and `docs/INSTALL.md`. Keys and models are in `docs/PROVIDERS.md`.
Setting up your own store and the first interview are in `docs/ONBOARDING.md`. This guide does not repeat them.

**Saved working notes.** Enable them with `exo config set work_notes.enabled true`
and restart Exo. Ask the agent to keep the task's goal, constraints, steps and
evidence in its working notes. `/work` shows them in the TUI or `exo chat`, and
`/work history` lists revisions. Resuming the same conversation restores a short
snapshot. Forks start with empty notes. Undo marks affected notes stale. These
are the agent's notes and never grant approval. See [working notes](WORK-NOTEBOOK.md).

If Claude Code is installed (the `claude` command is on your PATH) and logged in, you need no key. While the
main model has no key (by default Anthropic, then OpenRouter) and you have not chosen one, Exo uses your Claude
Code login as the main model: `claude-cli:sonnet`. The status line shows it. Cost reads `unknown`, because a
subscription has no per-call price. `exo models` and `exo config roles` show the configured routes, which do
not include this automatic one. To choose it yourself, run
`exo providers default claude-cli:sonnet` (or `claude-cli:opus`). Details: `docs/PROVIDERS.md` ("Use your Claude Code login").

Otherwise, set up a model provider. Pick one of three ways. They store the key the same way and show the same
status:

    exo providers                 # every provider: status, where its key is, models, default
    exo providers add anthropic   # paste the key at the prompt (no echo), then a test call runs
    exo providers test anthropic  # one tiny live call: ok with latency, or why it failed

Or type `/providers` in the terminal client, or open the Providers tab in `exo web`. Never paste a key into chat or
type it after the command name. If no provider works, `exo` and `exo web` tell you. Details and the security of
the web route: `docs/PROVIDERS.md`.

A Muse Code subscription (`muse-cli`) serves only the `aux` role, the side work such as context summaries and the
learning review. It cannot be your main model, so it does not replace a key or the Claude Code login. Its setup and
limits are in `docs/PROVIDERS.md` ("Use a Muse Code subscription").

Then start the terminal client by typing the command, in the folder you want to work in:

    exo                     # same as `exo tui`; flags pass through: exo --resume (or `exo resume`), exo --seat NAME

The first time it runs on an `EXO_HOME`, the agent gives a short orientation (`/intro`) and turns on tutorial mode.
If no model provider works yet, a note says so and the providers panel opens. If one provider is set up but no
main model is chosen, the model picker opens instead. The orientation waits until a provider works. Type
`/providers` to open the panel again.
Skip the orientation with `exo --no-intro`. Upgrade with `exo update`. `exo --version` shows what you have.
More: `docs/ONBOARDING.md`.

The installer puts Exo's skill pack in `$EXO_HOME/skills`, one folder per skill. Skills you add there are yours
and stay. The pack's folders belong to Exo: a re-run of the installer or `exo update` replaces an edit made in one.
It warns when it replaces an edited `SKILL.md`. To change a pack skill, copy its folder under a new folder name and
set a new `name:` in the copy's `SKILL.md` frontmatter. Exo finds skills by that name and keeps one per name, so a
copy that keeps the old name hides the pack's skill or is hidden by it. Then delete the copy's `.exo-managed`, or
each re-run warns that Exo did not install it. Details: `docs/INSTALL.md` step 5.

## 2. The two clients

The web chat status strip wraps to fit the conversation width in either layout. Model names and values remain
readable as the window narrows; notices appear on their own wrapped row. In a short window, scroll the chat column
to reach the status, message box and thread.

Both clients speak the same protocol to the same core. Use either, or both at once.

**Terminal (`exo tui`).** Type a message and press Enter. Options: `--resume` continues the newest thread
with messages (each launch opens a thread, so it skips the empty ones), `--session ID` continues one, `--seat NAME`
picks the seat, `--title TEXT` names a new thread, and `--cwd DIRECTORY` chooses the working directory for new
threads. For a local connection, a new thread defaults to this terminal window's current directory, including
when it joins a core whose seat has another default. A resumed thread keeps its saved directory. With `--url`,
`--cwd` names a directory on the server; `@file` completion and attachments still use the client's local directory.
Keys:

| Key or prefix | Does |
|---|---|
| `ctrl+c` | Stops the running turn. Your draft stays. With nothing running it only shows a hint that `ctrl+q` quits; a single press never quits. `/stop` does the same. |
| `Esc` | Never stops a turn. It closes the menu or ghost text, clears a tip, or leaves a panel. |
| typing while a turn runs | Steers it: the text lands at the next tool step. The line shows `steer sending`, then `steer queued` while waiting and `steer delivered` when it reaches the conversation. The queued count stays beside the composer while you type. Delivered means received, not yet acted on. |
| `Up` in an empty composer, or `/unsteer` | Pulls your newest queued steer back into the composer to edit, if it has not landed yet. On the web page, the `×` on the queued chip does the same. It never stops the turn. |
| `/tasks`, `/open <task>`, `/steer <task> <text>`, `/stop <task>` | List the subagents your session handed work to, watch one's session, send a running one a note (it lands at its next tool step) or stop it. A finished subagent answers with a clear error and nothing changes. The parent's receipt says it was steered or stopped. On the web page the Tasks cards have Open, Steer and Stop. |
| `F6` | Loads 60 earlier messages when the top of the transcript says "N earlier messages" (with `/layout inverse` it is the bottom). Scrolling to that end does the same. |
| `ctrl+f` | Puts the focus on an approval card, wherever you are. Your draft stays. |
| `@path` | Adds that file to your message. |
| `!command` | Asks the agent to run that shell command and show the output. |
| `ctrl+p` | Opens the command palette. |
| `ctrl+o` | Expands or hides tool output. |
| `ctrl+y`, `ctrl+e`, `ctrl+r` | Accept, edit or reject the last answer. This is feedback the learning loop uses. |
| `ctrl+g` or `/compose` | Opens the big editor for a long, structured prompt. Enter adds a line. **`ctrl+s` sends** (`ctrl+enter` too, on terminals that report it). `Esc` closes it and your draft stays. `ctrl+l` shows line numbers, `ctrl+t` edits in `$EDITOR`. `/compose task` starts you with Goal, Context, Constraints and Done when headings. Slash and `@` completion work inside it. |
| `/layout inverse` or `classic` | Inverse puts the input on top and the newest message directly under it, so a thread opens on its latest. Classic is the usual order. Your choice is saved. |

`/copy` tries `pbcopy`, `wl-copy`, `xclip`, then `xsel` when available. Without a successful helper it sends OSC52
and reports that clipboard receipt cannot be verified; a terminal can ignore that escape sequence.

Click anywhere on the transcript and you are typing again; keys typed while nothing else has focus go to the composer.

The screen shows the model that served each call, how full memory is, how much of the call came from cache, the cost,
and the number of open findings. Screenshots are in `docs/media/` (for example `docs/media/tui-01-start.png`,
`docs/media/tui-03-approval.png`, `docs/media/tui-04-context.png`). They are older captures and may not show every
current control. The look is described in `docs/UI.md`.
Set `EXO_REDUCED_MOTION=1` to turn motion off. `NO_COLOR` turns colour off.

**Web (`exo web`).** Run `exo web` in a terminal. It opens the page in your browser for you. If it does not (no
browser, `--no-open`, or a `--host` other than `127.0.0.1`, `localhost`, `::1` or a wildcard bind), open the link it prints: `http://127.0.0.1:7342/#token=...`. The part after `#token=` is a
key for this launch only. The page sends it with every data request (every `/api` route except `POST /api/launch`,
and both event streams), and the server refuses those without it; only the page itself, its static files and the
launch route load without it. The browser `exo web` opens gets a one-time code (`#code=...`) instead of the key: it
works once, within 120 seconds, and the page trades it for the key at the launch route. After you restart
`exo web`, use the new link: the old one stops working. Run `exo` and `exo web` in either order: both
see the same agent and threads. `exo web` joins a terminal window's core, or `exo serve`, if one is running; if
none is, it starts `exo serve` in the background (log `$EXO_HOME/logs/exod.log`). That background core keeps running
after you close the web page, so a later `exo` joins it. `exo stop` ends it.
It shows approvals, decisions waiting on you, live sessions, tasks, projects and cost. Screenshots are
`docs/media/web-*.png`, and the Providers tab is `docs/ui/web-providers.png`. Details are in `docs/ui/WEB.md` and
`plugins/exo-command/README.md`.

The web page has its own sub-pages for Tasks, Schedule, Command and Providers. Its composer runs `/context`,
`/findings`, `/trace`, `/new`, `/rename` and `/layout`. Type `/` for the menu. The Layout button in the page does what
`/layout` does. Use Rename beside a thread to edit its title, then Save or Cancel. Enter saves and Escape cancels.
Double-click opens the same editor. Anything else you type there goes to the agent as a
message. Add `--no-open` to `exo web` if you do not want it to open a browser tab.

Web conversation drafts stay with each thread while the page is open, including when you visit another
view. A pending send lets you keep writing and blocks duplicate Enter submissions. If a send is unconfirmed,
your text remains recoverable in that thread without replacing newer edits. Check the transcript before
sending again; a lost reply may still have reached the core. Reloading or closing the page discards drafts.
See [conversation drafts](ui/WEB.md#conversation-drafts-and-unconfirmed-sends-d-045).

Find a conversation searches the loaded titles, IDs and agent names. Use the status selector to show
working conversations or those waiting for an approval. It does not search message contents. Show latest
messages returns to the newest message when you have scrolled away. Appearance offers Dark, Light or
System; that preference is saved in this browser. Back and Forward move between the web views.

## 3. Slash commands

Type `/help` in the terminal client for the live list. This table is the same list.
The technical reference, with arguments and where each command is implemented, is `docs/COMMANDS.md`.

| Command | Does |
|---|---|
| `/new` | Start a new thread: a fresh focus, the same memory. |
| `/tab [directory]` | Open another terminal window with a new thread in that directory, leaving this thread open. With no directory, use this thread's working directory. |
| `/new private` | Start a thread that is not remembered. Its words never enter shared memory or search. |
| `/sessions`, `/resume [thread]` | Pick one of this agent's threads to switch to, or name one by id or title. |
| `/fork [title]` | Fork this session. |
| `/rename <title>` | Rename this thread. The new title shows in `/sessions`, `/resume` and the web thread list, where Rename or a double-click opens the title editor. |
| `/undo` | Undo the last turn. |
| `/clear` | Clear the transcript view. History is kept. |
| `/cwd` | Show this thread's working folder. |
| `/context` | Show what the next model call carries, part by part, with token counts. Read-only. |
| `/compact` | Summarize now what is eligible, then show tokens before and after. |
| `/restart` | Reopen this session in a new terminal tab and exit this one. |
| `/stop` | Stops the running turn. What was done is kept. `/stop <task>` stops a running subagent instead (see "Subagents" in section 5). |
| `/unsteer` | Pull your newest queued steer message back into the composer, if it has not landed yet. It never stops the turn. |
| `/exit`, `/quit` | Quit. |
| `/model [ref]` | Pick the model for this session, or set it with a reference. `/model default <ref>` sets the default. |
| `/default [ref]` | Set the default model for new sessions, or pick one. |
| `/usage` | Show context size, cost and the model in use. |
| `/providers` | Set up model providers: status, add or replace a key in a masked field, test, remove, default model. `/providers add <name>` opens the key field. |
| `/trace [N]` | Show timing, route and estimated cost for the last N turns (default 10). |
| `/findings` | Show what the harness found wrong with itself. |
| `/grants` | List standing approvals. `/grants revoke <id>` removes one. |
| `/gate` | Show whether policy files changed this session, and any error in them. `/gate accept` adopts the change. |
| `/permissions` | Show whether the gate asks or is in bypass, and how many calls bypass approved. `/permissions bypass` stops the asking (except Exo's own records and secrets); `/permissions ask` turns it back on. |
| `/peers` | List other live sessions you can message. |
| `/send <peer> <text>` | Message another session yourself. |
| `/handoff [path]` | The agent writes a handoff note for the next session. |
| `/intro` | The agent's orientation: what Exo is and how to use it. |
| `/tutorial on`, `off`, `reset` | Tips the first time something comes up. Bare `/tutorial` shows the state. |
| `/panel` | Reopen the last panel. |
| `/tasks` | List the subagents this session handed work to, with their status. |
| `/open <task>` | Watch a subagent's session. `<task>` is a task id, the start of one, or the subagent's session id. |
| `/steer <task> <text>` | Send a note to a running subagent. |
| `/copy [error\|all]` | Copy the last answer to the clipboard. `/copy error` copies the last error, `/copy all` the whole transcript. |
| `/compose [task\|edit]` | Open the big editor for a long prompt (also `ctrl+g`). `task` starts it with headings, `edit` opens `$EDITOR`. |
| `/layout [inverse\|classic]` | Put the input on top with the newest message under it, or back to the usual order. Saved. |
| `/help` | List every command. |

Plugins add commands. They show in `/help` under `other`. `/exo` (the dispatcher) and `/orchestra` are examples when
those plugins load. The core answers `/context`, `/compact`, `/trace` and `/findings` itself. `/grants` comes from
the `exo-gate` plugin, and so does `/permissions` (D-065). The web client runs `/context`, `/findings`, `/trace`,
`/new`, `/rename`, `/permissions` and `/layout` from its composer, and `/model` and `/default` (with a reference, or
bare for a picker; `/default` only on a page opened at 127.0.0.1 or localhost). A command it does not run, such as
`/grants` or `/help`, stays in the box with a note; a line like `/tmp is full` is sent as a message.

### Typing help

You do not need to remember any of the above. Type `/` and a menu opens under the composer with every command, what
it takes and what it does. Keep typing to narrow it (`/cmp` finds `/compact`), press Tab or Enter to complete, the
arrows to move, Esc to close. After a command the menu offers its arguments: `/model ` lists your models, `/resume `
your recent threads by title, `/tutorial ` the words `on`, `off` and `reset`, `/grants revoke ` the grant ids.
`@` lists files in your working folder (directories first, secrets left out); `!` shows a one-line note about
running a shell command. A mistyped `/comand` is not sent: Exo says "did you mean /compact?" and leaves the fixed
line in the box. Up arrow brings back earlier lines, and as you type, the rest of an earlier line shows dim; press
right arrow to take it. With the box empty, a dim line sometimes suggests a next step (`/providers` after a failed
call, `/context` in a long thread). `/tutorial off` silences it. The web page has the same help, except that its
line after a failed call reads the error: a refused key or login points at the Providers page, a network failure or
timeout says the model could not be reached, and anything else points at `/trace` (D-092). Inspired by the
Hermes CLI; the details are in `docs/COMMANDS.md` "Completion and guidance".

## 4. Memory

Exo has one memory per agent, not one per window. A seat is one agent. Every window, channel and scheduled job of
that seat is a thread of the same mind. Open a second window and the agent already knows what the first did.
Different seats are different agents. They share your store and talk through peers.

- A thread is a focus. `/new` starts one. Memory carries over.
- The log keeps every word, word for word. Nothing is lost when the context is compacted.
- Old parts of the log are folded into summaries so the model call stays small. This happens by itself.
  `/compact` does it now. A temporary provider failure keeps those words verbatim and retries on later upkeep,
  with delays up to five minutes. Only explicitly non-retryable summary failures reach the permanent attempt cap.
- `/context` shows the parts of the next call: the system text, the summary tree, news from other threads, and this
  thread's recent messages.
- Your rulings and corrections are kept verbatim in summaries and are copied to your principal store, so a
  summary cannot blur an instruction. `/context` says how many of your messages are not yet captured.
- A private thread (`/new private`) is logged but never shared. It keeps summaries of its own. While they are
  being written, a long private thread's call can leave out the older messages they cover. A line saying
  `[earlier messages are being summarized]` marks the gap. Your current turn is never cut.
- Only what you type counts as you. Messages from peers, bus seats, job notices and the agent's own notes are kept,
  but never saved as your rulings or corrections, and never teach the agent your taste.
- What Exo keeps about you lives in your principal store. An install that skipped the interview has none yet. The
  log and summaries still work, but the boot line says `memory off, no principal store` and one dim note names the
  fix: `exo init <id>`. Memory and learning use the new store from the next start.

Details: `docs/MEMORY-MODEL.md`.

### Taste

Exo keeps your taste as items: how you like answers written, how work is checked, what matters to you. Each item
the agent sees carries its source in brackets: `[house 1.3]` (the house taste that ships with Exo),
`[learned 0.72, 5 weeks]` (learned from what you did, with its weight), `[you said, <date>]` (a ruling of yours),
`[pinned]`. Your own items always win over house items. House items fade slowly over about 15 months but never
vanish.

- `exo taste` lists every item in use, and says whether the house taste is on. It is off when no house taste has
  been released yet, on the install whose store the house taste came from, or when you start blank.
- To start blank, add `taste:` with `house: off` under it to your store's `policies/consolidate.yaml`.
- The taste block in the agent's system message is limited to 400 tokens (`taste_budget` in the `exo-memory`
  config; 0 turns it off). Low-weight items drop out of the prompt first; they stay in `exo taste`.

Safety settings (approvals, budget, posting rules) are not taste. They never come from the house taste and only
change when you edit them. Changing taste slowly to fit you, and the `/taste` view, are still to come. Details:
`docs/TASTE-BASELINE.md`.

## 5. Background jobs and subagents

The agent can start a long command, such as a build or a test run, and keep working. It uses `bash` with
`background: true`, then `jobs`, `job_output` and `job_kill`. When the job ends, the thread gets a short notice with
the exit code and the last lines of output. The notice is never from you and cannot approve anything.

- Stopping a turn (ctrl+c or `/stop`) does not stop a job. Ending the core does.
- Limits (four at a time, two hours each) are per seat. See `docs/JOBS.md`.

### Subagents

A subagent is a helper the agent starts with its `delegate` tool, to do a part of the work in its own session while
the main thread carries on. Each one runs on the cheapest model tier that can do the job: `fast` for reading, search
and simple edits, `standard` for building and tests, `deep` only for hard judgment. If a subagent fails, it is tried
once more one tier up. Limits keep it small: by default a subagent cannot start its own helpers, at most four run at
once, and each has a cap on tool rounds, tokens and time. The briefs and receipts it passes are in
`docs/AGENT-WIRE.md` section 6.

You can watch and control them:

- `/tasks` lists them with their status. `/open <task>` shows one's session (read only).
- `/steer <task> <text>` sends a running subagent a note. It lands at its next tool step.
- `/stop <task>` stops one subagent and leaves the rest. A bare `/stop` stops the main turn.
- On the web page, the Tasks cards have Open, Steer and Stop buttons.
- A subagent that has finished, failed or been cancelled is locked: steering or stopping it gives a clear error and
  changes nothing.
- The agent's receipt for the task records what you did: "steered by principal" or "stopped by principal".

Through these commands, only you can steer or stop a subagent. The rules also allow the agent that started it,
but nothing on main gives that agent the path yet. Other sessions and peers get a refusal (D-070). A subagent is
still a session, though, so a program on your machine that sends to or stops its session directly can reach it.
Such a note is recorded as that program's, never as yours.

### Scheduled jobs

A scheduled job is a prompt that runs by itself on a timer, such as a morning brief or a test run every six hours.
Each run is a fresh unattended session, and it ends with a short plain-English result that arrives as a notice.
The run goes through the same gate as any turn, so an action that needs your approval waits for it and the run can
stall there.

The place to manage jobs is the **Schedule** page of `exo web`. The form takes:

- **When**: a cron line such as `0 9 * * 1-5` (minute, hour, day of month, month, day of week), or `every 30m`,
  `every 2h`, `every 1d`.
- **Prompt**: what the run should do. **Title**: an optional label.
- **Tier**: `fast` (the default), `standard` or `deep`. Pick the cheapest that can do the job.

Each job shows as a card with its next and last run. The card has buttons to pause or resume it, run it now (the run
appears on the Tasks page) and remove it. Remove cannot be undone, so it asks for a second click on Confirm remove.
Focus moves to Cancel, so pressing Enter again cancels. Escape cancels too.
Jobs are kept in `$EXO_HOME/schedule.sqlite`, and `exo serve` (which `exo web`
starts if needed) runs the timer. A run missed while Exo was down happens once at the next start.

There is no terminal command for jobs, and the agent has no tool to create them. The web page and the protocol
command `schedule.edit` are the entry points. Only your authenticated Exo client can create, pause, resume,
run or remove a job. Local scripts and channels can list jobs, but changes return `forbidden`.
An unattended run cannot call a tool outside the list it received. Details: `docs/SCHEDULE.md`.

## 6. Approvals

On an Exo OS installation, the execution broker checks approved actions too.
An action the installed broker does not support returns a tool error. Approval
does not enable that operation. Installation details are in `docs/EXECUTORS.md`.
The handoff facts also mark Git information unavailable; inspecting Git runs
through the installed executor instead of the host handoff collector.
If the required gate cannot save its decision or approval evidence, the tool
returns an error and does not run. A post whose text the gate cannot read on the
line (a body file, stdin, `$( )` or a variable) is also refused; inline text can
be checked by the gate.
Simultaneous approvals are recorded separately. Changed policies and standing
grants become active only after their required authorization audit succeeds.

Some actions wait for you. The gate looks at each tool call and decides: allow, log, ask or deny, from your policy
files (`policies/gates.yaml` in your store). When it asks, a panel shows what the agent wants to do and why.

Both clients use `o` to allow once, `a` to choose always, and `d` to deny with an optional note. Choosing always,
including uppercase `A` or clicking Always, waits for a separate Enter or Confirm always button. Escape cancels;
it never answers. The keys act only inside a deliberately focused card. Cards never take focus automatically,
even when the composer is empty. Typing in an editable field cannot authorize. Press Tab, click the card, or press
`ctrl+f` to move to it. Both clients show the answer sequence: `Ctrl+F`, then `o` once, `a` followed by Enter
for always, or `d` followed by Enter to deny. The web card lists its available choices and changes its help during
confirmation or note entry. Press Ctrl+F from the composer, then the answer keys, with no click. Your draft stays
in place. A card goes away the moment the request is closed, whoever closed it, and also when you stop
the turn that was waiting. Each window also checks its card against the core every few seconds, so a card the core no
longer holds never stays on screen. If you answer a web card that was already closed, the page drops it and says so.

File searches with the agent's `grep` and `glob` tools stop after 30 seconds and ask it to narrow the pattern.
Other sessions and approval cards remain responsive while a search runs. Stopping the turn also stops its search.

What the gate asks about, and why:

- **Credentials always ask.** Anything the gate sees reading or filling a password, token or key asks every time.
  An "always" answer is never stored for credentials. The folder that holds Exo's own connection secret (`run/`)
  is a secret store: a read of it or of a wildcard in it (`cat ~/.exo/run/*`, `grep -r token ~/.exo/run`) asks
  (#169, fixed in #185). A `grep` or `glob` over a folder leaves out the secret stores in it (`~/.ssh`, `~/.aws`,
  `.env`, Exo's keys and `run/`) and says how many it skipped; the agent must name one to read it, which asks
  (#241). The gate judges a read by the path it names, so a shell walk from a folder above (`grep -r token
  ~/.exo`) is still an ordinary read, and so is a harmlessly named symbolic link to a key file, read by its own
  name (#209). A read by a short name inside a secret store asks too when the shell tool was told to run there
  (its `cwd` or `workdir`), as it does after a `cd` there: `cat config` run in `~/.kube` asks every time and offers
  no "always" (#311). `docs/GATE.md` lists the other spellings it misses.
- **Outward acts are bound to the exact text.** A post, message, email, social post, payment, or anything public or
  irreversible is approved for the exact text, title and target you saw. If the agent changes one character, you
  are asked again. This is so a draft you read cannot be swapped for another. A post whose text the gate cannot
  read (piped in, a file given by a relative path, `$( )` or a variable) asks, and offers no "always". The gate
  reads a body file given by its full path when the post is alone on its line. On a line that does more
  (`cd /tmp && gh ...`, a copy, a redirect to a file, `GH_EDITOR=x gh ...`), the rest of the line could change the
  file first, so the gate leaves it unread: the post asks once and says why (#343). When the gate cannot tell which
  repo a `gh` command acts on, it reads the command on each one, and the question names the others ("It may land on
  pub/repo#7 instead."). A deny in your policy for any of them holds. An "always" on such a command covers that
  exact command only (#399).
- **Posts to public places are swept for internal names**, such as seat names, home paths and model ids. A hit holds
  the post and lists what it found. The sweep and the exact-text check see only text the gate can read, so a body
  it cannot read asks instead (#208, above).
- **Code the gate cannot see into asks (D-038).** A script file, `python -c`, `node -e`, a heredoc fed to a shell,
  `make`, `npm run` or `npm test`, `npx`, `xargs`, `find -exec`, a variable or `$( )` in the program's place, a shell
  function, `source`, or a program the gate does not know: each asks once, for that exact command, also beside a
  push or post that would only log (#471). Read the whole
  command before you allow it: the card names only the most severe part of the line, and approving runs all of it,
  including code the gate could not see into. Plain work still
  runs: `pytest`, `uv run pytest`, `git status`, `ls`, `rg`, `npm install`, `cargo test` and the like. These are
  trusted by name, so a test runner still runs your project's files. Options of trusted programs that run a
  command (`tar --to-command`, `rsync -e`, `ssh -o ProxyCommand` and the like) are read as the command they run
  (#164). A variable that loads code or changes where programs and config are found asks when the line sets it
  (`LD_PRELOAD`, `PATH`, `PYTHONPATH`, `HOME`, git config through `git -c` or `GIT_CONFIG_*`; #231), and so does
  `unset PATH`, since bash then looks programs up in the current folder (#329). Common setups
  still run: `PATH=$PATH:./node_modules/.bin`, a virtualenv's `bin` first in `PATH`, `PYTHONPATH=src python -m
  pytest` and `GIT_CONFIG_GLOBAL=/dev/null`. Any other folder in `PATH`, such as
  `export PATH="$HOME/.local/bin:$PATH"`, asks. So does `hash -p /tmp/x ls`, which makes `ls` run another file
  (#330). A `$( )` inside a single-quoted array subscript given to `printf -v`, `unset`, `declare`, `read` and the
  like (`printf -v 'a[$(...)]'`) is read (#179, fixed in #189), and so is a command run behind a wrapper such as
  `setsid`, `flock` or `watch` (#160, fixed in #196), a heredoc or pipe into a group such as `{ bash; }`
  (#161, fixed in #198), and a pipe, here-string or heredoc into the shell `sudo -s` or `sudo -i` starts
  (#491). The gate finds programs only in the shell forms its parser reads. A command inside a
  parameter expansion such as `${x:-$(...)}` is read too, and a quote or brace in such a word no longer hides the
  command after it (#143, fixed in #158), down to three levels of `$( )` inside it; a deeper one, or one past the
  24 such parts a call may open (a word inside a `$( )` counts again for each pass over it), asks. Such a word is
  read in an `env -S` text and in what a `sudo -s` or `sudo -i` shell reads from its stdin too, and behind or
  inside a quoted prefix assignment (`X='a b' echo ${v:-$(...)}`, `echo ${v:-$(X='a b' ...)}`, #471), and in a loop
  that `cd`s into your store it is read as running there on the loop's next pass (#479). `${!name}` and
  `${name@P}`, which run code a variable holds, ask, also for the positional parameters (`${@@P}`, `${!@}`, #554).
  A `${name@P}` prompt transformation runs the value's command substitutions and parameters, so
  `printf -v p '%s(%s)' '$' 'id'; echo ${p@P}` asks (#579); `p=x; echo ${p@P}`, which the reading before #158
  keeps a read because the line assigns `p` a literal value, asks too, as bash's reading of the word does not see
  the value. A command nested more than four levels deep
  (`bash -c` inside `bash -c` and so on) or an `ssh` or `mosh` chain more than four hops deep (#153) asks, even
  when your policy denies it (#137).
- **A deny holds for every command on a line (D-075).** A `deny:` or `reserved` entry in your policy is checked
  against each command the gate reads on a line, so with pushes denied `git push origin main && rm -rf build` is
  denied, not asked about the delete. A `categories: {X: deny}` setting or a rule with `decision: deny` holds the
  same way (#191), and so does a `matrix:` cell set to `deny` for one command on the line (#464). A store write on
  the line no longer hides those denies, and a file tool on a store or secret path keeps them too: under
  `deny: [delete]`, `delete_file ~/.exo/state/x` is denied rather than asked about (#448).
- **An allow rule names the command it allows (#394).** A rule with `decision: allow` or `log` and a `command:` regex
  lowers a line of several commands, or a command with another run inside it (`ssh host '...'`, `bash -c '...'`,
  `eval`), only when the regex matches the line, as before, and also the command that decides the line, as you wrote
  it. So a rule for `^git status` no longer allows `git status; git push --force origin main`, while a rule for
  `git push --force origin main` still allows `cd repo && git push --force origin main`. The regex must also match any
  other command on the line that would be asked about or stopped alone (`rm -rf build`), and any command that sets the
  shell up for the next one (`export`, `alias`, `hash`, a function). This change never makes a decision looser than it
  was: a rule that did not match a line before still does not. So a rule with anchors such as
  `^git push --force origin main$` still covers only the push on its own. A rule written for a whole line such as
  `^git push origin main && git push origin dev$` now has to name each command too, for example as
  `['^git push origin main && git push origin dev$', '^git push origin (main|dev)$']`. Ask and deny rules still see the
  whole line, but an ask rule lifts a deny only from the commands it names. `docs/GATE.md` has the details.
- **Your store is like a key (D-038).** A write the gate sees going to your store's `policies/`, `state/` (your
  standing grants), the ledger, `keys/`, seat files, the core's socket, or the signed-update trust pin in `update/`
  (also `exo update --pin-trust`, #133) asks you every time, and no "always" covers it. So does a write into
  `~/.exo/plugins` (#435), because Exo loads and runs the code it finds there, and into the other folders Exo loads
  code from too (#459): the venv `${EXO_VENV:-$EXO_HOME/venv}` that holds the `exo` package and the gate itself,
  Hermes' plugin home (`${EXO_HERMES_HOME:-$EXO_HOME/hermes-home}/plugins`, loaded with `--with-hermes`), the source
  copy `$EXO_HOME/src`, and every folder `EXO_PLUGIN_DIRS` names. A `pip install` into the venv Exo itself runs from
  now asks; everyday work installs into a project or scratch venv instead. To work on a plugin, leave
  `EXO_PLUGIN_DIRS` unset while you edit, or point it at your copy only for sessions that must load it. Only you can
  answer such a write, whatever `approvals.yaml` says, also
  when the same command or file tool names a secret that a seat may approve (#447). Reading your policies or plugins
  with `cat`, `grep` or a read tool does not ask. Some other commands that only read, such as `sed -n` or `sort`, do
  ask. So does a write to your Exo home's `config.json` (D-074, #125), which names the plugins Exo loads, your
  models, your provider settings and your principal, and so does an agent running `exo config set` or `unset`,
  `exo import-store`, `exo providers default` or `exo interview`. Reading `config.json` with `cat`, `grep` or a read
  tool does not ask, `exo config set` typed in your own
  terminal is not a tool call and does not ask, and a project's own `config.json` is not affected. A copy that drops
  a `config.json` into your Exo home without naming it (`cp /tmp/x/config.json ~/.exo/`) is not seen yet (#483). The
  gate resolves symbolic
  links in an absolute path (#130), but otherwise goes by the path as the command spells it, so some writes are not
  seen as store writes yet: a relative name such as `gates.yaml` written by a shell command whose working folder is
  `policies/` (a file tool's relative destination from a session folder inside the store is seen, #494), a glob
  spelling, and a hard link. A brace spelling (`polic{i..i}es`) is seen (#224). `docs/GATE.md`
  lists the rest.
- **Policy edits during a session only tighten (D-038).** If a policy file changes while Exo runs, the stricter of
  the old and new policy applies. Run `/gate accept` to adopt the change, or restart. `/gate` shows the state.
  A standing "always" grant covers a call only where the old and the new policy would both run it (#586). This
  covers `approvals.yaml` too: a seat you remove cannot answer the next ask, and a shorter "always" length
  (or 0) applies at once. A seat you add waits for `/gate accept`.
- **Hard to undo, or visible to others.** Under the default policy, an act that cannot be undone asks. So does an
  act on a public place. Reading and local edits do not. Your `policies/gates.yaml` can change any cell.

Who may approve: you can always, from the terminal window or the web page. A policy file can let named seats or
roles approve some kinds of act, and they must sign. A peer never can. A message from a peer that tries to approve
something is refused and shown to you. The agent cannot answer its own approvals: a command it runs that talks to
Exo is not you, whatever it claims, and only you can answer "always". The check has limits. A program running as
you that obtains your window's secret and detaches from Exo can pass (`docs/GATE.md` "Who answered"). The web
page's link carries its own secret, so a program that reads the link can answer from the page too, and the link
stays in your browser's history once you open it. When `exo web` opens the browser itself, the launcher's command
line, which other programs on the machine can read, holds only the one-time code, never the key (#166, fixed in
#177). If a used code comes back, `exo web` replaces the key, closes the page's connections and prints a warning
with a new link: paste it into the browser that is already open. A program that uses the code before your browser
does gets the key without a sound; only your browser's later use of the code replaces the key and warns. A browser
that never runs the page's script never uses its code, so then nothing warns (`docs/ui/WEB.md`).

"Always" answers become standing grants. They expire (7 days for outward acts, 30 days for others) and an outward
grant covers one exact text. `/grants` lists them. `/grants revoke <id>` removes one.

### Bypass: let the agent work without asking (D-065)

If you want the agent to get on with it, like Claude Code with `--dangerously-skip-permissions`, start Exo with

    exo --skip-permissions

or type `/permissions bypass` in a running window or the web page. The gate then approves on its own everything it
would have asked you about: comments, deletes, network posts, scripts. Bypass auto-approves everything except
changes to Exo's own records and reading secrets. Those two still ask you, every time: a write to your policies,
ledger, keys, grants, seat files or the core's socket, and reading a key or token. Your `deny` rules still block. A
public post that breaks your posting rules (an internal name, a home path) is sent back to the agent with the fixes
instead of to you.

You always know it is on. The terminal status line starts with `permissions: bypass` and a count, and every web page
shows a banner with an "Ask again" button. Every call it approved is in your ledger, marked `decided_by: bypass`,
with the exact command. `/permissions` shows the counts. `/permissions ask` turns asking back on.

Only you can turn it on, from your own window or page. The agent, a tool it runs, a peer or a bus message cannot. It
lasts until the core stops; the next start asks again unless you give the flag again. To make it the default for a
seat, put `[permissions] mode = "bypass"` in the seat file (`docs/SEATS.md`); changing a seat file always asks you.
On an Exo OS installation (a required tool executor) bypass is not available. It is your consent, and it covers only
your own turns: a turn a peer, a bus wake, a job notice or a local caller started asks as if bypass were off.

Details: `docs/GATE.md`.

## 7. Peers

Peers are other live sessions on the machine: Exo sessions, Hermes agents and bus seats.

- `/peers` lists them. `/send <peer> <text>` sends a message from you. Malformed discovery records
  are ignored and removed during normal discovery so other live peers remain available.
- An agent can message a peer with its `peer_send` tool.
- Your own sessions deliver at once. A message from another person, or from a bus seat, is held until you allow it.
  A held message that nobody answers expires after 30 minutes.
- A peer message shows as "from another session, not from you". It is input, not an order.
- A peer counts as one of your sessions only when this Exo home holds its key and names you as its principal. Seats
  that share one home hold each other's keys in `$EXO_HOME/keys/`, and each seat file names its principal, so they
  deliver at once. A peer whose key this home does not hold is held, and the card says `key not pinned`. A presence
  file that claims your name changes nothing (#132).
- An unseated daemon or a Hermes profile on this home has no seat file. Toward a daemon with no seat principal of
  its own, it counts as the principal `config.json` names (`exo config set principal <id>`). If it runs as another
  principal (`EXO_PRINCIPAL`), its messages are held for you. A seat whose seat file names a principal holds its
  messages for you too. To have them delivered at once, give it a seat file that names you:
  `$EXO_HOME/seats/<name>.toml` with `principal = "<id>"`.
- A seat from another home: copy its key from `<that home>/keys/<seat>.pub` and run `exo keys pin --peer <seat>
  <key>` here. Its messages are then checked against that key, and still held for you. A `[peers] rules` entry for
  its name delivers them at once. A message under a name whose key this home holds, signed by another key, is
  refused. Use `--peer`: a plain `exo keys pin` also lets that key sign your ledgers.
- If a seat's key was made again, its messages are refused until you pin the new key:
  `exo keys pin <seat> $EXO_HOME/keys/<seat>.pub --replace`. A Hermes profile's name must be a seat id (lowercase
  letters, digits, `-` and `_`), or its messages are refused. `docs/PEERS.md` "Peer keys" lists these cases.
- A send to a seat whose key this home holds goes out only when that seat signs its presence file and its answer
  with that key (#523). If the presence file does not verify, the send fails with `presence file does not verify
  ... not sent` and nothing reaches the peer. That happens when the peer runs an older Exo: update it. It also
  happens when the peer's key was made again and this home still pins the old one: pin the new key with
  `exo keys pin <seat> <that home>/keys/<seat>.pub --replace` (add `--peer` for a seat of another home), then send
  again. If instead the send fails with `first reply that does not verify ... not published`, the text was sent
  and the peer may already have it; only its answer was refused. Check with the peer before you send again, or it
  gets the message twice.
- A bus seat's message is checked against the key Exo pinned for that seat the first time, held for you by default,
  and answered on the bus only if your rules let that seat through on its own. `exo keys` shows the pinned seats.

Details: `docs/PEERS.md`.

## 8. Seats

A seat is one named setup of an agent: whom it serves, which models, tools, skills and plugins it has, and where its
threads start. Seat files live in `$EXO_HOME/seats/`. Run one with `exo serve --seat NAME` or `exo tui --seat NAME`.
When an `exo serve` already runs, a second seat needs its own socket:
`exo serve --seat NAME --socket $EXO_HOME/run/NAME.sock`. Without it, `exo serve` refuses to start and says so, and so does the second of two started at the same moment.
Ready-made role templates are in `plugins/exo-orchestra/seats/`. To write your own, see `docs/SEATS.md`.

A seat can set the folder its threads start in, with `[session] cwd` in `seat.toml`. Exo warns once when a thread's
folder is not inside a git repo.

## 9. Cost and trace

- The status line and `/usage` show the session cost so far. It is an estimate in US dollars, priced per call from the
  catalog. A call with no known price shows `unknown`, never `$0`.
- `/trace` shows one row per turn: time, model that served, calls, fallbacks, tools, tokens and cost.
- `exo trace [SESSION] [--last N] [--calls] [--json]` shows the same from a shell.
- `/findings` and `exo findings` list problems Exo found in its own work, such as a slow turn or a model fallback.
- `exo findings backlog [--limit N] [--json]` counts the unresolved ones by severity and by age, and lists the oldest.

Details and the cost rules: `docs/TRACE.md`.

## 10. Troubleshooting

| Symptom | Try |
|---|---|
| No model answers | Run `exo providers`. It shows which providers have keys and whether a test call worked; `exo providers test <name>` says why one fails. Add a key with `exo providers add <name>` or `/providers`. |
| The error says a provider "rejected the key" | The key is wrong, revoked or refused. Replace it in `/providers` (`a`), or fix the environment variable the message names and restart Exo. Waiting does not help, so Exo does not retry. Some providers also send HTTP 403 for flagged input. The provider's text in the parentheses then says so. |
| The error says a provider "is over its provider spend limit" | The provider stopped billing this key: top up the account or raise the limit in the provider's own console, then test it in `/providers` (`t`). A new key does not help. |
| The error says a local model "is not running" | Start the server it names (for Ollama, `ollama serve`), then test it in `/providers` (`t`). A passing test lets the next message through at once. |
| The error says "next route reopens in" some seconds | The provider failed and Exo rests it for a while. Wait, or test it in `/providers` (`t`): a passing test retries it at once. |
| A second `exo serve` refuses to start | One process writes an agent's log. Another `exo serve` or `exo tui` for that seat already holds it. Use that one, or stop it. |
| A second `exo web` says Exo's web client is already running | One already serves that port. Keep using its page, or open the full link it printed, or stop it with Ctrl+C in its terminal. `exo web --port 7343` starts a second one on the same core. |
| Cost shows `unknown` | The route has no price. On the Claude Code login (`claude-cli`) this is expected. Add one under `models.models` in `config.json` (`docs/TRACE.md`). |
| `code_find` is missing | It needs Node.js 22 or newer. `exo models` says why on its `code_find:` line (`docs/TOOLS.md`). |
| Memory or learning is off | Both need a principal store. Without one the boot line says `memory off, no principal store`. Run `exo init <id>` (any short id, such as your first name), then start Exo again. If `exo web` started the core, run `exo stop` first (`docs/LEARNING.md`). |
| A peer message never arrived | It may be held. Look for an approval card. Held messages expire after 30 minutes (`docs/PEERS.md`). |
| An edit to a skill in `$EXO_HOME/skills` is gone | That folder is the pack's, and the installer or `exo update` replaced it. Copy the skill under a new folder name with a new `name:` in its `SKILL.md`, and delete the copy's `.exo-managed` (section 1). |
| Memory looks wrong | Run `/context`, then `exo memory verify` to check the log's hash chain (`docs/MEMORY-MODEL.md`). |
| The agent seems slow | Run `/trace`. Look at the model that served and any fallbacks. |
| The warning about "not inside a git repo" | The thread's folder is not a repo. Set `[session] cwd` in the seat, or `/cwd` to see where you are. |
| The web page says "missing or wrong page token" | Open the link `exo web` printed this time. Each launch has a new token. |
| The terminal says `not sent: principal connection refused: ...` | The window checked the process on the core's socket against the core's launch record, and the check failed (the message says why: a stale record, another process on the socket, or a core from before an update), so nothing was sent. The text stays in the box. Run `exo stop`, then start `exo` again (`docs/GATE.md` "Unix peer authentication"). |
| The web page says "this browser link was already used or has expired" | The browser link is good once, for two minutes. Paste the link `exo web` printed in the terminal into this tab; do not click it in the terminal. If the terminal also warned that a link was used twice, paste the new link it printed into the browser that is already open. |
| Keys must stay private | Exo never writes secrets through `config.set`, and removes known key values from tool output. See `docs/SEATS.md`. |

## 11. Your store and the ledger

Everything Exo knows about you sits in a **principal store**: a folder of small notes (nodes) and a signed ledger, a
log nobody can edit without it showing. These commands work on them from a shell. You rarely need them. The agent
reads and writes the store for you. They help when you want to look, check, or move things.

Pick the store with `--principal ID` or `--root DIR`, or set `EXO_PRINCIPAL` once. Stores live in
`~/.exo/principals/<id>` unless `EXO_HOME` says otherwise. A store you made somewhere else with
`exo init <id> --root DIR` or `exo import-store --root DIR` is remembered, so its id finds it from then on.

**Make or adopt a store**

| Command | What you get |
|---|---|
| `exo init <id>` | A new, empty store with its ledger. It becomes your active principal if you have none, and memory and learning use it from the next start. Add `--interview` to answer the onboarding questions straight away. |
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
| `exo log verify` | Re-checks every event, link and signature. Each key must be one Exo pinned, and the ledger must still reach the last head it recorded. Prints `{"ok": true, ...}` or fails. Run it when something feels off. |
| `exo memory verify` | The same check for every store, plus each agent's memory log. A ledger that was cut short, edited or deleted fails here. A warning after `ok` (in `exo log verify`, a `warning` field) means the copy of the head it names is not being written: make that file writable again. |
| `exo keys` | The keys Exo trusts: its own signing keys, the bus seats it has seen, the peer seats of other homes you pinned, and which ledger names are you. `exo keys pin` adds or replaces one (`--peer` for a peer seat). |
| `exo log tail` | The last events. `-n 20` for more, `--kind ruling` to filter. |

Example: make a key, write a note, check the chain.

    exo keygen me
    exo log append note --author me --text "Prefer short answers."
    exo log verify

**Review what Exo learned.** These commands read your store and its ledger. None of them calls a model API on its own.
They need `--principal ID` or `--root DIR`, like the others.

| Command | What you get |
|---|---|
| `exo brief` | The morning brief in Markdown. `--html` prints HTML. `--write` also saves `reports/brief-YYYY-MM-DD.md` and `.html` in the store. |
| `exo ask list` | The questions Exo wants to put to you, with how much each is worth asking. `exo ask next` gives the next few within your daily interruption budget (`-n 3` by default). `exo ask answer <id> "text" --author NAME` records your answer in the ledger, signed with that author's key. |
| `exo consolidate` | Turns new ledger events into proposed changes to what Exo believes. `--dry-run` reports without writing. |
| `exo proposals` | Lists those proposals. `show <id>` reads one. `accept <id>` applies it. `reject <id> --reason "..."` and `revert <id> --reason "..."` need a reason. `--all` includes decided ones. |
| `exo eval` | A report that measures Exo against your own questions and past choices (files in the store's `training/` folder). `--no-write` prints it without saving. |

Details and the rules inside each: `docs/TOOLS-CLI.md`, and `docs/LEARNING.md` for the loop that uses them.

Every flag is in `docs/COMMANDS.md`. How the ledger and store work: `docs/CORE.md` and `docs/STORE-V2.md`. To review
what Exo proposes to learn from the ledger, see `exo consolidate` and `exo proposals` in `docs/TOOLS-CLI.md`.
