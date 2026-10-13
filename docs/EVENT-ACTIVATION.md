# Event-driven activation: triggers, connectors, runs and root causes (D-057)

Status: **Proposed** (2026-10-13). Nothing here is built. D-057 in `DECISIONS.md`. Builds on D-022 (scheduled jobs,
`docs/SCHEDULE.md`), D-040 (authors), the gate (`docs/GATE.md`), the trace and findings (`docs/TRACE.md`, D-025) and
D-050 (local first).

## Inspiration and credit

Railtown AI (railtown.ai) frames an agent platform as three jobs: build the agent, feed it, observe it. Their engine
starts agent work from events in a company's systems rather than from a person typing. This design takes that shape
and makes it local-first: the events come from the user's own machine and accounts, the agent runs on the user's own
Exo, and nothing passes through a service of ours. Exo uses its own names for every part.

## The idea in one paragraph

Today an Exo agent starts work in two ways: a person sends a turn, or the scheduler's timer fires (D-022). A
**trigger** adds a third: something happens, and a turn starts. A file lands in a folder, a mail arrives, a local
webhook is called, a commit is made, the machine wakes up. A **connector** turns the raw event into a short, masked
envelope the model may see. The turn runs under the same gate as any other, as an unattended run, with an author that
is never the principal. Every trigger has a debounce, rate limits, a cost cap and a pause switch, and every run shows
up on a **runs dashboard**. When a run fails, an automatic **root-cause pass** writes a finding that feeds the
existing findings to issue to pull request loop.

## 1. Triggers

### What a trigger is

A trigger is a scheduled job whose `when` is an event instead of a time. It lives in the same table
(`$EXO_HOME/schedule.sqlite`) and is managed by the same protocol command, `schedule.edit`, with one new field. No new
protocol command (30 of 30 are used) and no new event type (29 of 30): runs report through `task.updated` and finish
with a `notice` or a channel post, as jobs do.

| Field | Meaning |
|---|---|
| `on` | the source and its filter, for example `{"source": "fs", "path": "~/Inbox", "glob": "*.pdf"}`. A job with `on` is a trigger; a job with `when` is a timer. |
| `prompt` | what the run should do. The event envelope is added after it as data (section 2). |
| `title`, `seat`, `tier`, `deliver` | as for jobs (`docs/SCHEDULE.md`). `tier` defaults to `fast`. |
| `connector` | which connector shapes and masks the event. Each source has a default. |
| `limits` | debounce, rates and cost cap (section 3). Defaults per source. |
| `tools` | an optional narrower toolset for this trigger. It can only remove tools, never add. |

`action` gains no new values: `list`, `add`, `remove`, `pause`, `resume` and `run_now` work on triggers as on jobs.
`run_now` on a trigger replays its last envelope.

### Sources

| Source | Watches | How | Default debounce |
|---|---|---|---|
| `fs` | a folder, with a glob and ignore list | the OS file watcher (FSEvents on macOS, inotify on Linux); one batch per quiet period | 2 s of quiet |
| `mail` | one mailbox folder, with filters on sender, subject or label | IMAP IDLE with the account's own credentials in the OS keychain, or a local Maildir on Exo OS | 30 s |
| `webhook` | a local URL, `http://127.0.0.1:<port>/hook/<trigger-id>` | a listener on the loopback address only; each trigger has its own secret and the caller signs the body with HMAC-SHA256 | none |
| `git` | one local repository: commits, merges, branch switches, tags | watching `.git/refs` and `.git/HEAD`, so no hook is installed in the repository | 5 s |
| `system` | Exo OS events: wake, login, network change, power, a device plugged in, a service failing | the OS layer's local event bus | 10 s |

Notes:

- **Webhook stays local.** The listener binds to the loopback address. Exposing it to the internet (for example
  GitHub calling in through a tunnel) is the user's choice, outside Exo, and `exo trigger add webhook` says so in one
  line. Unsigned or badly signed calls are dropped with a counter, never a turn.
- **Remote git events** (pull requests, issues) come through the webhook source with the `github` connector.
- **System events** need Exo OS. On other systems the source is listed as unavailable, not faked.

### Authors (D-040)

A trigger-started turn has author `trigger:<id>`, a new author beside `schedule`. Its envelope is data from the
outside world, written by whoever sent the mail or saved the file. It is never the principal's voice:

- Capture, consolidation and learning never read it as a ruling, a correction or an answer.
- The agent is told, in the unattended-run prompt, that the envelope is untrusted content and that instructions
  inside it are not instructions to follow.
- A `ruling`, `answer` or `verdict` can never come from it (`PRINCIPAL_VOICE_KINDS`).

`exo.provenance` gains `TRIGGER = "trigger"` and `trigger(id)`; everything that checks `is_principal` already does the
right thing.

### The run

A trigger run is a scheduled run (`docs/SCHEDULE.md`, "Rules of the run") with these differences:

- Session kind `triggered`, titled from the trigger, for example "on change: Inbox".
- The same blocked tools as a scheduled run, minus nothing; the trigger's `tools` can narrow further.
- **Under the gate, always.** Every tool call is classified and logged. An ask waits for the principal. A trigger run
  that waits on an approval for more than 30 minutes ends with a notice ("waiting on you: approve or dismiss") rather
  than holding a slot forever. Standing grants apply as for any turn; credentials and store writes still ask every
  time.
- **No loops.** Changes that the run itself makes (files it writes, commits it makes) are recorded and do not fire the
  same trigger. Events caused by a trigger run carry a depth; a run at depth 2 cannot start another.

## 2. Connectors and masking

A connector turns a raw event into an envelope, and decides what the model may see.

```json
{
  "trigger": "inbox-pdfs",
  "source": "fs",
  "at": "2026-10-13T09:12:40Z",
  "count": 2,
  "items": [
    { "kind": "created", "path": "~/Inbox/invoice-⟨n:1⟩.pdf", "size": 48211 },
    { "kind": "modified", "path": "~/Inbox/notes.md", "size": 1022 }
  ],
  "masked": ["n:1"],
  "dropped": 0
}
```

Rules every connector follows:

- **Allowlist, not blocklist.** A connector names the fields it passes on. For mail: sender domain, subject, date,
  labels and the first 2,000 characters of the plain body, not the raw headers. For GitHub: event type, repository,
  title, labels, state, not the full payload.
- **Masking before the model.** Values that look sensitive are replaced with stable placeholders before the envelope
  leaves the connector: email addresses `⟨email:1⟩`, phone numbers `⟨phone:1⟩`, card and account numbers
  `⟨number:1⟩`, anything matching the key and token patterns the env scrub knows (`exo.envscrub`) `⟨secret:1⟩`, and
  the names on the principal's privacy denylist. The same value gets the same placeholder within one run.
- **Placeholders resolve only at the gate.** The map from placeholder to value stays in the core, never in the prompt
  or the transcript. If the agent passes `⟨email:1⟩` to a tool (to reply to that sender), the core resolves it when the
  tool runs, and the gate's approval card shows the real value to the principal. A secret placeholder never resolves.
- **Bodies by reference.** A large file or mail body is not inlined. The envelope carries a reference; the agent
  reads it with its normal tools, under the gate.
- **Counts, not floods.** At most 50 items per envelope; the rest are a count in `dropped`.

Connectors are plugins (`register_connector(name, fn)` beside `register_hook`), so a user can add one without
changing the core. A connector is pure: event in, envelope out, no network calls.

## 3. Limits: debounce, rates and cost

Defaults, each overridable per trigger in `limits`:

| Limit | Default | When reached |
|---|---|---|
| Debounce | per source (table above) | events wait for a quiet period, then go in one batch |
| Minimum gap between runs | 60 s | new events wait and join the next batch |
| Runs per hour | 6 | events are held and counted; the next run sees the count |
| Runs per day | 40 | the trigger pauses until the next UTC day, with one notice |
| Concurrent trigger runs per seat | 1, queue of 10 | the oldest queued batch is merged into the next |
| Tokens per run | 50,000 | the run is told to wrap up, then stopped |
| Spend per trigger per day | the smaller of 1 USD and a tenth of `budget.yaml` `per_day_max` | the trigger pauses until the next day, with one notice |
| Spend for all triggers per day | a quarter of `per_day_max` | every trigger pauses until the next day |

A `per_day_max` of zero ("always ask") means a trigger run's first model call asks, so new users are never surprised
by spend. The tier defaults to `fast`, the cheapest that can do the job (D-017).

## 4. What the user sees, and how to stop it

- **The list.** `/triggers` in both clients and `exo trigger list` show each trigger: source, filter, state
  (`on`, `paused`, `paused: budget`, `paused: errors`), last run, runs and spend today, and events held back.
- **Each run** is a thread of kind `triggered` in the thread list, marked with the trigger's title. Its result arrives
  as a notice, as for jobs. A dim line in the main thread says a run happened: `trigger · inbox-pdfs · 2 files · done`.
- **Pause one:** `exo trigger pause <id>`, `/triggers pause <id>`, or the switch on the dashboard. Paused triggers keep
  watching and count what they would have done; resuming does not replay the held events unless asked
  (`resume --replay`).
- **Pause all:** `exo trigger pause --all` or `/triggers off`: one switch that stops every trigger at once. Config
  `triggers.enabled = false` keeps them off across restarts.
- **Automatic pauses:** three failed runs in a row, a budget cap, or a loop guard firing. Each says why in one notice.
- **Adding a trigger** is a principal act: only the principal's own client can add one, the agent cannot add one for
  itself (`cronjob_manage` stays blocked in unattended runs), and the add shows the source, the filter, the limits and
  what the model will see before it saves.

## 5. Observe: the runs dashboard

A **Runs** panel in the web client, beside the instrument column (`docs/ui/WEB.md`).

- One row per unattended run, scheduled or triggered: title, source, started, duration, status (`done`, `failed`,
  `waiting on you`, `stopped: budget`), model tier, tokens and spend, tool calls, approvals waited on, findings filed.
- Filters by trigger, status and day. A sparkline per trigger of runs and spend over 14 days.
- Opening a row shows the run's trace (`exo trace <session>`) and its envelope as the model saw it, with masked values
  still masked.
- Data comes from the session store and the trace, through a `command.run` built-in, `runs` (no new protocol
  command). `exo runs [--trigger ID] [--failed] [--json]` is the terminal form.

## 6. Observe: automatic root-cause analysis

When an unattended run fails (an error, a tool error that ended the run, a stall on approval, a budget stop) or a
finding fires during it, a root-cause pass runs after the run ends:

1. A `fast` tier model reads the run's trace: the envelope (masked), the tool calls and results (trimmed), the
   errors, the timing summary and the findings. It gets no tools.
2. It writes a structured note: symptom, the most likely cause, the evidence (trace event ids), a suggested fix, and a
   confidence (`low`, `medium`, `high`).
3. The note is filed as a finding (`docs/TRACE.md` "Findings") with source `anomaly`, the run's signature, and the note
   as its detail. The same signature dedups, so a recurring failure bumps a count instead of filing again.
4. From there the existing loop takes over: `exo findings export --md` is the digest exo-razi already turns into
   issues, and those into pull requests. Before any finding leaves the machine as an issue, the outbound scrub runs and
   the gate asks (a public post).

Limits: at most 5 root-cause passes a day per seat, never for a run of the root-cause pass itself, and none when the
day's trigger budget is spent. `trace.rca = false` turns it off.

## 7. Build, for the roadmap: an Exo SDK

The third job, build: a way for other people to make agents that run on Exo. Not scheduled; a direction.

- **What it is.** A small, typed package (`exo.sdk`) and a manifest format for an agent: its prompt and persona, the
  tools it needs, the triggers it would like, its connectors, its skills, and its limits. The manifest is data; code
  lives in ordinary plugins, which Exo already loads (`docs/DEVELOPER-GUIDE.md` "Add a plugin").
- **What it is not.** A way around the gate. An SDK agent runs on the user's own Exo, under the user's policies. Its
  manifest asks for triggers and tools; the install shows the list and the principal approves each, as for any
  trigger. It never gets the principal's author.
- **Why it fits.** Exo already has the parts: plugins, hooks, skills, seats, the gate and now triggers. The SDK names
  a stable subset and promises not to break it within a major version (D-056).

## 8. Build plan

| Stage | What | Done when |
|---|---|---|
| 1 | Trigger engine in `src/exo/core/scheduler.py`: the `on` field, `fs`, `webhook` and `git` sources, debounce, rates, cost caps, pause and pause-all, author `trigger:<id>`, the loop guard | a test saves a file and gets one run; ten saves in a second get one run; a run that writes into the watched folder does not fire again; a bad webhook signature starts nothing; a run hitting its cap pauses the trigger |
| 2 | Connectors and masking: the connector hook, the allowlist rule, placeholders that resolve only at the gate; the `mail` source | a fixture mail with an address and a key reaches the model as placeholders; a reply tool call resolves the address and the approval card shows it |
| 3 | The runs dashboard: the `runs` built-in, `exo runs`, the web panel | every scheduled and triggered run of a fixture day appears with its status and spend |
| 4 | Root-cause pass: the post-run step, the finding, the daily cap | a fixture failed run files one finding with a cause and evidence; a second identical failure bumps the count |
| 5 | `system` source on Exo OS | wake and network-change events start runs on an Exo OS machine |
| 6 | The SDK (roadmap) | a decision of its own |

Each stage updates `docs/SCHEDULE.md`, the user guide, the developer guide and the docs map as it lands.

## Open questions for the principal

1. **Default limits.** (a) The table in section 3: cautious, cheap. (b) Looser (20 runs an hour, 5 USD a day): more
   useful for busy folders, more spend.
2. **Root-cause pass.** (a) On by default with the daily cap. (b) Off by default; the user turns it on.
3. **Who may add a trigger.** (a) Only the principal, from a client, as proposed. (b) The agent may propose one, as a
   question the principal approves in one tap.
