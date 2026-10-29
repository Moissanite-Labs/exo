# Exo fix sweep, October 18, 2026

How to read this: what one agent team pushed to this repository, how the branches fit together, how to merge them, and what is left. Every branch is one commit on main 35b7de8. Agents working here in parallel: read "For the other agents" first.

## For the other agents

- **Who owns these branches.** The 45 branches below belong to this sweep. Please do not push to them. Comment on their pull requests instead, or open your own branch.
- **How they land.** 43 of the 45 branches are regrouped into nine pull requests, one commit per fix plus one CHANGELOG
  commit, rebased on main with the conflicts below resolved. Each merges only after a review by an agent other than
  the one that drafted it, with the checks green on its current head. The source branches stay as they are.

  | Pull request (in merge order) | Branches |
  |---|---|
  | #213 first run, part 1 | fix/first-run-skills, fix/tui-boot-resume, fix/claude-cli-route, docs/first-user |
  | #214 first run, part 2 | fix/memory-learning-principal, fix/provider-error-messages, fix/web-first-run, fix/decision-answer-cooldown, fix/gate-side-calls |
  | #215 core sessions | fix/running-session-state, fix/multi-seat-home, fix/shutdown-job-notice, fix/cron-dst, fix/serve-socket-takeover, fix/record-interrupted-model-call |
  | #218 protocol and config | fix/server-event-backpressure, fix/protocol-strict-decode, fix/usage-input-tokens, fix/seat-models-merge, fix/provider-table-merge, fix/command-cost-double-count, fix/chat-allow-always |
  | #216 privacy | fix/private-thread-ledger, fix/context-undo-fork, fix/private-thread-tail, fix/context-provenance |
  | #219 learning | fix/ingest-provenance, fix/learn-climb-gates, fix/learn-export-judge, fix/store-roots-recall |
  | #221 security, part 1 | fix/bus-channel, fix/relay-hardening, fix/update-trust, fix/ledger-integrity |
  | #222 security, part 2 | fix/tools-fs-safety, fix/bash-stream-redaction, fix/skill-gates, fix/edit-context-aware |
  | #212 clients (merged) | fix/web-chat-view, fix/web-command-stream, fix/tui-thread-switch, fix/tui-pump, fix/install-update-scripts |

  #212 merged before its review fixes were pushed, so they follow in #243. fix/gate-env-inject-config was rebuilt on
  main after #164 merged and lands as #231. site/raise-prep waits for the owner.
- **Decision numbers taken.** Main holds D-079 to D-084, D-086, D-087, D-089, D-091 and D-092. #500 holds D-088 (harness profiles). D-078 is free. #510, merged into #508's branch, uses D-084 too. Open PRs #509, #511, #844, #848 and #850 use D-083, D-085, D-090, D-093 and D-094.
- **Left to the gate agent.** The open gate pull requests change src/exo/gate.py every few hours, so this sweep did not touch the gate findings listed under "Not done". The gate agent is welcome to take them. One exception: #228 (merged) fixed #224 (a brace set in an Exo store path hid a write), found while reviewing #193.
- **What this sweep is doing next.** Answering reviews on those nine pull requests, merging main into them as it moves, and reviewing other agents' pull requests.

## Where things stand

- 45 branches are pushed. Each fix was reproduced on main first. Each has tests that fail on main and pass on the branch, an adversarial review, a conventions review, and a final run of the full suite, lint and the type baseline.
- All 45 merge together. In a scratch tree with every branch merged, the root suite gave 2,587 passed and 89 skipped with no failures. Every plugin suite passed: exo-bus 45, exo-command 217, exo-context 39, exo-gate 1,249, exo-memory 64 and exo-orchestra 17. Lint is clean, the type baseline matches at 243 (five fewer than main), and the counted core is 15,289 lines with 524 of headroom left.
- main moved to d545083 (PR #190, the gate's `$'..'` decoder). Every branch still merges with it, and the only conflict is CHANGELOG.md. main has since moved to 6471598 (#197 and #127). The nine group branches were then rebuilt on main b5622cf.
- Decision numbers in use: D-079 (event backpressure), D-080 (usage tokens), D-081 (gate side calls). No other branch uses them.

## How to merge

The nine pull requests above already apply all of this, except the last two rows of the table below. Those conflicts come up only when the other agent's open #175 meets #212 or #218. #212 has merged, so #175 applies the fix/tui-pump row against main. Whichever of #175 and #218 merges second applies the last row. The rest is kept for anyone merging the source branches by hand.

Most branches add a `### Fixed` block to CHANGELOG.md, so nearly every merge conflicts there. Keep both sides. A few docs pages (DOCS-MAP, USER-GUIDE, UI, SEATS, LEARNING, COMMANDS) conflict the same way: two branches add lines in the same place, so keep both.

Six code conflicts need a hand:

| Branches | File | Resolution |
|---|---|---|
| fix/gate-side-calls and fix/decision-answer-cooldown | src/exo/core/models/decision.py `_run` | Keep the `attempted` set and `_pick_async` loop, loop over `[r for r in router.chain(role) if r not in spared]`, and pass `model_only=bool(spare)` to the decision provider's `record_failure`. |
| fix/first-run-skills and fix/tui-boot-resume | clients/tui/exo_tui/app.py `boot` | Keep `await self.boot_over()` and use `self.onb.intro_due`. |
| fix/learn-climb-gates and fix/ingest-provenance | src/exo/eval.py `build_replays` | Keep the authorship block from learn-climb-gates, with the role test `in ("user", "principal")`. |
| fix/multi-seat-home and fix/running-session-state | tests/core/scheduler/test_scheduler.py | Both append tests at the end. Keep both. |
| fix/tui-pump and the other agent's feat/unix-peer-auth-2 | src/exo/protocol/client.py `events` | Keep their `sse_frames` loop and wrap the `Event.from_json` call in tui-pump's `try/except s.ProtocolError` that logs and skips. |
| fix/protocol-strict-decode (#218) and feat/unix-peer-auth-2 (#175) | src/exo/server/app.py `Dispatcher._run` | Keep #218's `try` around `hello` and `events.subscribe`, and add #175's `instance` and `heartbeat` keys to the hello result. Keeping only #218's side fails three `test_unix_peer_auth.py` tests. Keeping only #175's fails the two `test_server.py` replay_fault tests. |

Two test-only steps apply once both sides are in. The patches are in `docs/fix-sweep/`:

- After fix/private-thread-tail joins fix/private-thread-ledger and fix/context-undo-fork, apply `private-thread-tail-tests.patch`. A private thread now has its own summary tree, so two asserts check the agent tree and the private tree apart.
- After fix/gate-side-calls joins fix/decision-answer-cooldown, apply `gate-side-calls-x-decision-answer-cooldown-test.patch`. The gate no longer falls back to the main model (D-081), so that test's router gets an aux route.

fix/gate-env-inject-config was stacked on PR #164. #164 has merged, and the branch lands as #231 on main.

## The branches

### First run and readiness

These close the blockers and most majors in READINESS.md.

| Branch | What it fixes |
|---|---|
| fix/first-run-skills | The installer copies the skill pack, so `/intro` works. A static intro shows if the skill is missing, and a failed intro is not resent at every launch. Each pack skill is listed once in the prompt. |
| fix/claude-cli-route | On the Claude Code route the agent names the real working folder, and a message starting with `/` is not run as a Claude Code command. |
| fix/tui-boot-resume | No boot-screen freeze when the main route is down. The providers panel opens on a keyless first run. `exo --resume` skips empty threads. |
| fix/memory-learning-principal | Learning finds the installer's principal. Memory without a store is one quiet note, and the boot line says what memory really is. |
| fix/gate-side-calls | Loading a skill is a read. Gate decision calls never use or cool the main route (D-081). |
| fix/provider-error-messages | A rejected key names the key to fix. A stopped local model says to start it. A passing provider test unblocks the chat. |
| fix/web-first-run | Memory notes no longer show as user messages in the web chat. A second `exo web` explains the busy port. `exo stop -h` prints help. |
| docs/first-user | The README says what Exo is in one sentence. Private-preview access, the full web link, the Claude Code route and a glossary are added, and the Muse advice is fixed. |

### Core, protocol and scheduling

| Branch | What it fixes |
|---|---|
| fix/running-session-state | Scheduled runs and subagents share the engine's session state, so no second turn starts mid-run and resume does not write a false "not run". |
| fix/multi-seat-home | Two seats on one home share event numbers, delegate tasks and scheduled jobs safely. |
| fix/server-event-backpressure | Subscriber queues are bounded, and every client resumes after a lag close (D-079). |
| fix/protocol-strict-decode | Malformed commands get typed errors instead of crashing the handler. |
| fix/usage-input-tokens | `input_tokens` means the same thing on every transport (D-080). |
| fix/record-interrupted-model-call | A model call cut short by a stop counts as model time. |
| fix/seat-models-merge | A seat's `[models]` tables merge over config.json entry by entry. |
| fix/provider-table-merge | Provider config tables merge over their catalog entries without leaking the catalog key to a new base URL. |
| fix/decision-answer-cooldown | A decision model's parse failure no longer cools the shared credential. |
| fix/command-cost-double-count | A finished subagent's spend counts once in the cost panel. |
| fix/shutdown-job-notice | A job killed at shutdown is recorded once, with no notice and no model turn. |
| fix/chat-allow-always | `exo chat` can grant allow_always after a confirmation. An unknown reply denies. |
| fix/cron-dst | A cron job does not run again and again in the hour the clocks go back. |
| fix/serve-socket-takeover | A second `exo serve` refuses a live core's socket and leaves its pid file alone. |
| fix/serve-start-lock-242 | Two cores started at once leave exactly one: the socket's start is under a lock file. |

### Privacy and provenance

| Branch | What it fixes |
|---|---|
| fix/private-thread-ledger | Private threads stay out of memory, learning and the context plugins (D-028). |
| fix/private-thread-tail | A private thread keeps its own history through a private summary tree. |
| fix/context-provenance | exo-context pins only the principal's own words, and an undone ruling is dropped (D-040, D-041). |
| fix/context-undo-fork | Undo keeps other threads' news notes. A fork no longer resends its parent's whole transcript. |
| fix/ingest-provenance | Imported principal history feeds consolidation, and unverified imported rows do not. |
| fix/learn-export-judge | export-house never ships the principal's name, organisation or email domain. The judge score parser reads plain replies. |
| fix/learn-climb-gates | The nightly climb follows rule 5, counts only principal-authored verdicts, and keys its cached baseline on what it depends on. |

### Security and integrity

| Branch | What it fixes |
|---|---|
| fix/bus-channel | No reply leak to a remote seat, no inbox wedged by one sealed message, and only the channel can pin a seat key. |
| fix/relay-hardening | The relay's web view hides message bodies unless the operator opts in, and envelopes that would break `/v1/verify` are refused. |
| fix/update-trust | Signed updates check only the pinned SSH signers, fail closed on an unreadable config, and move the pin only to a list the current pin signed. |
| fix/tools-fs-safety | Writes land on the path the gate classified, a symlinked file is refused rather than replaced, reads skip FIFOs and devices, and `code_find` never takes the question as an option. |
| fix/bash-stream-redaction | A secret split across output reads is still redacted, and characters split across reads stay whole. |
| fix/ledger-integrity | The ledger head is anchored after COMMIT. `exo import-store` leaves a working store, and `exo memory verify` reports a missing ledger. |
| fix/store-roots-recall | Stores made with `--root` are found by every reader, and recall sees beliefs accepted after its first call. |
| fix/gate-env-inject-config | The gate reads env vars that inject code or set git config, and `clone -c`. Lands as #231. |
| fix/skill-gates | The draft pipeline's prose gate, the identifier gates and the SYM-2P validators fail closed. |
| fix/edit-context-aware | A one-line `edit_file` pattern no longer overwrites a line whose content changed. |

### Clients and install

| Branch | What it fixes |
|---|---|
| fix/web-chat-view | Peer, bus and job messages show their author. A held approval with no thread shows everywhere and can be answered. A dismissed panel stays dismissed. |
| fix/web-command-stream | Allow always in the Command view takes a second confirmation. Counts do not double after a stream drop, and a slow reader loses no event. |
| fix/tui-thread-switch | Switching threads drops the old thread's busy state, cost and approval card, and opening a thread restores its waiting approvals. |
| fix/tui-pump | The terminal keeps its event feed after a boot error and skips an event it cannot read. |
| fix/install-update-scripts | `exo update` keeps the plugin choice made at install. deploy-site.sh stops on a failed deploy and never tags it. |
| site/raise-prep | Waitlist, founder section and contact link on the site. Nothing shows until `window.EXO_SITE` is set. |

## Not done, and why

- Five gate findings were held because the gate changes every few hours. Each is now tracked:
  - A post whose body comes from a file or a command bypasses the outbound scrub: #208. High.
  - A symlink with a harmless name into ~/.ssh, ~/.aws or a .env file is read as a plain file: #209. High.
  - The bus registry is not protected like the store files: #210.
  - Tightening approvals.yaml mid-session waits for `/gate accept`: #211.
  - A quoted `}` inside `${..}` hides the rest of the line from the gate: #143, fixed by open PR #158.
- Nested `$( )` costs about n^5 to classify, and at about 500 levels observe mode lets a store write run: #220, also
  filed as #227. Open PR #158 fixes the classifier half.
- A safety stop halted the store-glob task, and it was not retried.
- The CHANGELOG backfill is left alone, because every branch edits that file.
- Gate decision calls still do not show in `/trace` or the turn cost. That needs a plugin event seam in the core.

## Follow-ups the reviewers raised

None of these blocks a merge.

- The same Hermes turn captured live and imported later counts twice toward the rule of three.
- A scheduled job with no seat can run in two seats at once when a run outlasts its interval.
- In a private thread, `exo_harness_report` and turn-end anomaly findings still reach the global findings list.
- `exo chat --resume` can still open an empty thread. The terminal client is fixed.
- The relay's web view still shows who messages whom.
- Pin files written before fix/context-provenance can hold peer text until the next undo in that thread.
- A few whitepaper lines need the next batched update.

## Decisions for the owner

1. Settled: 43 branches land as the nine group pull requests above. Each merges after a review by an agent other than the one that drafted it.
2. Push policy. Should git pushes ask by default, or should the README and GATE.md say pushes are only reported?
3. Settled: the held gate findings are filed as #208 to #211 and #220. The quoted `}` case is #143, fixed by open PR #158.
4. site/raise-prep needs the `EXO_SITE` values, and your OK on the founder line.

One more thing to know. On October 17, between about 15:30 and 16:00 UTC, one implementer ended test runs by a broad process match. That may have cut short another agent's test run on this machine in that window. Every branch here ran its full suite again afterwards.
