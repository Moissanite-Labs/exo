---
name: github-pr-audit
description: "Use when auditing a GitHub PR or issue before merge."
version: 1.16.0
author: Team Protean / Reviewer
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [GitHub, Code-Review, Pull-Requests, QA, Audit]
    related_skills: [github-code-review, github-auth, adversarial-review]
---

# GitHub PR / Issue Audit (pre-merge QA)

A focused workflow for the QA pass on a GitHub contribution — PR or feature
issue — before it lands on a high-visibility open-source repo. This is the
VERIFICATION DISCIPLINE that prevents publishing a wrong or sloppy review, plus
the GitHub permission realities that shape how you sign off. For the raw
diff/comment mechanics (gh vs REST, inline comments), load `github-code-review`
alongside this — that skill covers commands; this one covers gates.

## When to use
- "Review this PR", "audit this contribution", "make sure we don't look stupid
  on this merge".
- Any PR/issue on a watched repo where a bad review reflects on the team.
- Companion to `github-code-review`: load both. That one is the tool; this one
  is the QA checklist.

## The non-negotiable sequence

### 1. Get LIVE state — never trust a handoff diff
A `.diff` file dropped in chat or `/tmp` is a SNAPSHOT and may be a stale draft.
The branch is often tightened BEFORE push. Critiquing it produces a review of
code that isn't in the PR — the exact "look stupid" failure.
- `git diff main...HEAD` on the local checkout. First confirm you're actually on
  the PR branch: `git branch --show-current`.
- `gh pr diff N --repo O/R` for the remote truth.
- If they disagree, the local checkout is stale — trust `gh` output and say so.

### 2. Verify the bug premise against `main`
Do not accept the PR's rationale. Read the actual code on `main`:
- `git show main:<path>` (pipe through sed/awk to the function), or
- `git worktree add -q /tmp/hm-main main`, read/run there, then
  `git worktree remove /tmp/hm-main --force`.
Confirm the broken branch/symptom the PR claims to fix REALLY exists on main.
If it doesn't, the fix may be mischaracterized (partial, or already fixed).
State it explicitly.

### 2a. Prove the finding is a DEFECT, not the documented design
A reproducible observation is not a defect. Read the project's own intent for the behaviour and
name which bar the finding clears:
- a user-visible failure — duplicate execution, wrong or lost data, a crash;
- a demonstrated violation of a written invariant under the conditions you measured;
- proof the behaviour is unintended: a surface requests a resource nothing needs.
Read the area's `AGENTS.md`, the user docs, and any "do not fix this" list, and quote the line that
settles it. Topology is the trap: two processes, two holders of one file, a second connection or
lock all look alarming and are usually the architecture. The mechanism is not the harm — a count, a
holder, or a repeated shape only counts together with the failure it produces. State the falsifier
(the observation that would show the behaviour is intended) and look for it before you file or fix.

### 2b. Runtime-changing fix? Prove it on the live system FIRST
Green tests are not this proof: they show the function is right on the inputs you handed it, not that it is reached when the bug happens, nor that its inputs are populated then. Author-side gate: `github-pr-workflow` §2b. Run it against the PR head before any sign-off.
- Rebuild the artifact the real entry point loads and confirm the change is in it — a stale build exercises the old code and produces a false reading either way.
- Reproduce the original symptom; capture the process tree with parent PIDs, listening ports, `lsof` on the contested file, and the record the decision reads.
- If the live run shows the fix does not work, that IS the finding. Say so and send it back (or withdraw), rather than signing off on unit tests.
- If it cannot be run in this environment, say what was verified and what was not. Never imply the live behaviour is confirmed.

### 2c. Observability lane — diagnostics emitted before logging is configured

A buffer that records failures before `setup_logging()` and replays them later
is a logging-lifecycle change, not just a formatting one. Audit it as such:

- List every reader of a new buffer. If the only reader is one UI surface, FAIL
  until a post-`setup_logging()` replay-into-logger (or per-command surfacing)
  exists.
- For every `logger.debug` emitted before `setup_logging()`, state where the
  record lands after logging configures. Discarded-unless-one-surface-replays is
  not an answer for non-TUI and file consumers.
- For every sink named in user-facing copy (a log file, stderr, a UI), name the
  test proving delivery to that sink. A test asserting the pointer text ("see
  agent.log") without asserting the content reaches the file is banned as
  sufficient.
- Every `logger.warning` to `logger.debug` downgrade in the diff gets its own
  justification line: who still surfaces this to the user, on which commands,
  proven by which test. No named consumer plus test means rejecting the downgrade.
- Enabled-but-unloadable is actionable user config, never routine gating. A
  policy that excludes refusals from a surfaced channel must distinguish
  disabled-by-user (may stay quiet) from enabled-but-blocked (must surface with
  the remedy).
- Sibling-loader consistency: for the same refusal or error primitive, grep all
  loaders. A level split across loaders (one warns, one debugs) must be
  justified or unified, defaulting to the more visible level for enabled entries.
- A buffer PR may not land on single-surface-replay plus stderr-absence tests
  alone. The file test, the non-TUI test, the overflow-resolution test, and the
  refusal test are merge-blocking. Two adversarial shapes join them: the late-arrival test (an entry recorded after the replay or flush must still reach the user) and the boundary test (every bounded buffer driven at exactly its limit, cursor asserted independently of list shifting). An untested exclusion policy ("NOT buffered,
  stays debug") blocks merge until a test pins the exclusion and a reviewer
  signs off naming the affected user-visible case.

### 3. RUN the test suite — in the repo's own interpreter
Never trust a PR body's "17 passed". Execute it.
- `python` is often NOT on PATH. Check for `venv/bin/python` (target version per
  `.python-version`), or use `uv run`. Repo venv example:
  `./venv/bin/python -m pytest tests/tui_gateway/test_x.py -q`
- Run the touched file AND the sibling suites the PR depends on.
- Test node IDs are CLASS-qualified: `file.py::TestClass::test_method`, not
  `file.py::test_method` (the latter fails with "no match"). Grep the class name
  first if unsure.
- Record the real pass/fail counts in your review output.

### 4. Verify evidence claims in the PR/issue body
If the body cites a "pre-existing failure" or a specific test as proof, RUN that
exact test on `main` AND on the branch. A reviewer who runs the suite will catch
a false claim — so you must catch it first. If a claim is false or orthogonal
(e.g. cites a GUI test that passes and is unrelated to the logic touched), strip
it via `gh pr edit N --repo O/R --body-file <file>` — you have edit rights on
your own account's PR. Never leave a verifiable falsehood in a public PR.

### 4a. Audit the evidence register
For a multi-finding PR or issue, define the counted unit and count each item once at its highest impact. Separate fixes included in the current head from requests, overlaps, and unresolved findings. Map every row to its exact artifact, commit, test, and status. Record the evidence boundary: base or head, commit or composition, environment, and measurement time when relevant. Missing transferred artifacts or unrun tests do not establish a claim; list them as separate unverified follow-ups.

### 5. Scope & rubric fit
Read the repo's `AGENTS.md` / `CONTRIBUTING.md`. Check specifically:
- **Speculative infrastructure** — shared registries/helpers with NO in-PR
  consumer. Textbook case: a `_X_KEYS` frozenset + `_drop_stale_key` helper
  gating behavior `config.pop` already provides. Trace the code: if an unknown
  key already survives a persist (e.g. `dict(existing)` copy + the function never
  enumerates it), the registry adds ZERO behavior. Flag and recommend deletion.
- New `HERMES_*` env vars for non-secret config, cache-breaking mid-conversation
  changes, scope creep that revives a closed direction.
- Feature requests belong in SEPARATE issues, not bundled into a bugfix PR.
- The bars a good fix meets: "fix real bugs well" + "behavior contracts over
  snapshots" + "E2E validation not just green mocks".

### 6. Posting the sign-off — GitHub blocks self-approval
`gh pr review N --approve` from the PR AUTHOR's account fails:
`Review Can not approve your own pull request` (hard API rule, not policy).
- Do NOT fake an outside-approver stamp or spin up a second account.
- Post a transparent QA-pass COMMENT instead: `gh pr comment N --repo O/R
  --body-file <file>`. Lead with "QA pass" (honest provenance), document
  the substance, the test numbers you actually ran, and any housekeeping fixed.
### 6b. When you also own the MERGE lane
Self-approval being impossible does NOT block the merge — review and merge are
separate permissions. Check the real gates instead of assuming a human must act:
`gh api repos/O/R/branches/main --jq .protected` and `gh api repos/O/R --jq .permissions`.
With no branch protection and `admin`, merge it yourself (`gh pr merge N --repo O/R
--<method>`), then post the QA-pass comment on the merged PR and read the merge back.
- Take the merge METHOD from the repo's own history, not the provider default:
  `git rev-list --count --merges origin/main`. Zero merges = linear history, so use
  `--rebase` or `--squash`; a bare `--merge` would add the repo's first merge commit.
  Record the choice and the rejected alternative in the receipt.
- Rebase/squash REWRITE the SHAs you audited. State the new main SHA and note that the
  pre-merge SHA still exists on the branch/PR refs, or the receipt contradicts the audit.
- Re-check the stack after every merge: re-read `mergeable`/`mergeStateStatus` on the remaining open PRs before merging the next (UNKNOWN means GitHub is recomputing, wait and re-read). A sibling landing can turn a CLEAN PR CONFLICTING; that PR stays open with a rebase note, and the new head is re-verified before merge. Verified 2026-09-17.
- For issues: post scoping nits (e.g. "proposed `sessions list --model` collides
  with the existing `sessions list` command — extend it instead") as
  `gh issue comment`.

### 6c. Say it was an automated post

Anything the agent writes and posts itself (a PR body, a review, an issue or PR
comment) closes with one short line saying a machine wrote it:

> Automated posting by agentic team with human oversight.

- Last line of the body or comment, as a blockquote. GitHub renders it muted, so it
  reads as a footnote instead of a claim inside the argument.
- No team or product name, no apology, no hedging, no "I am only an AI" framing, no
  offer to defer to a human. It is a fact sentence like any other.
- Once per thread. A second PR from the same team gets its own line; a comment on a
  thread whose body already carries one does not repeat it, so read the body first.
- Provenance is not a discount. The evidence bar here (red-on-base test, real
  counts, live state) does not drop because the post is automated, and the line
  never softens a claim or an ask.
- **Read the body before posting any comment.** The line lives once per thread, and
  on your own PR the body is that place. So check first, every time:
  `gh pr view N --repo O/R --json body --jq '.body' | grep -c 'Automated posting'`
  Non-zero: the thread is already disclosed, so the comment goes without the line.
  Zero: either it is someone else's thread, or your own body never got one — add it.
  Skipping this check is how a comment ends up repeating a disclosure that is already
  rendered directly above it.
- Commit bodies stay in the repo's own register (short, no trailer). The PR body
  carries the disclosure for the branch.

### 6d. A runtime fix is only proven against the live process tree

A green unit test proves the decision function works on the inputs you handed it. It says nothing about whether that function is reached at the moment the bug happens, or whether its inputs are populated when it runs. Run this gate **before the PR exists** (`github-pr-workflow` §2b); an auditor runs the same steps against the PR head, and a fix that has not passed it is not ready to publish. For any fix claiming to stop a process being spawned, a file being written, or a route being taken, reproduce it live and name the evidence: the process list with parent PIDs, the listening port, `lsof` on the contested file, and the record the decision reads.

- **A stale build tests the old code.** Rebuild the artifact the entry point actually loads and confirm your change is in it before drawing any conclusion from the run.
- **A cache fed by a later event cannot affect the first decision.** If the input arrives from a poll, a status response, or any request that happens after startup, it is empty exactly when a boot-time decision runs. Read the authoritative record the other process already publishes, ideally one that revalidates liveness on read, instead of mirroring it in memory.
- **Check that the process you route to is the process you mean.** "Route it to the primary" is wrong whenever the primary is a different instance from the one the problem is about; only the live socket wiring proves which is which.
- **If the live run shows the fix does not work, withdraw rather than land.** Close the PR with the evidence, state what the fix cannot do, and file the reproduction where triage will see it. A withdrawn PR with a live repro beats a merged one that does not fix the bug.

## Cross-session audit SOP (own repos — standing rule)

Every PR on a team-owned repo ships in two sessions. Build session stages the PR unmerged on a branch plus a handoff prompt file (`cache/orchestrator/<name>-handoff.md`) with PR URL, head SHA, advisory verdicts, reference paths, and standing constraints. Fresh-eyes session on different model configs re-verifies every claim live, then merges on PASS or returns defect rows on FAIL. Build session refreshes the PR body on every push so it always declares the live head SHA. A body naming a superseded head fails audit on paperwork alone (hermes-desktop-mods #2). Proven: hermes-desktop-mods #1, where fresh eyes caught 2 real defects the build session missed. Staged verdicts are advisory only, never merge authority. Journal every SOP step.

When a fix has two defensible policies and the maintainer should pick one, ship
both as independent commits on one branch rather than asking first. One review
thread, no second branch to rebase, and their pick is a single revert instead of
a request for you to redo the work.

- Commit 1 is the base fix; commit 2 swaps in the alternative, so HEAD carries
the alternative and one revert returns the base.
- In the body, say what each does, which is HEAD, and that a revert switches
policies. Name the trade-off in one line each (what is lost, what is paid).
- Keep both commits green on their own. A commit that only passes on top of the
  other is not a choice.
- Keep the title neutral about which commit is HEAD. A title lifted from one
  commit's subject (or a checkmark on one policy) reads as the recommendation you
  were trying not to make. Name the shared outcome — "no card renders without a
  title" — and let the body say what each policy does.

## Approval boundary for evolving reviews

External approval binds to the exact review text and target that was approved. If live discussion or a changed head requires a material rewrite after approval, present the revised body for approval before posting. Do not treat permission to post a prepared draft as permission to publish a different reconciliation. On a busy maintainer thread, remove framing and verdicts that can read as condescending; lead with the concrete evidence.

## Keep the public text short

Reviewers pay for every word. The audit work is verbose; the PR text is not.

- Do not add `[P#]` severity labels to findings by default. They add noise when the finding order and blocking/non-blocking sections already communicate priority. Use an explicit severity label only when it changes a maintainer's decision or is required by the repository's review convention.

- **Plain-language floor applies to PR/issue text.** If the repo's audience is
  not all-native-English or not all-steeped-in-the-project, the PR title, body
  and comments follow the same bar as a README: say what the change does in the
  first lines, no jargon when a plain word exists, one idea per sentence, no
  invented hyphen compounds. A reviewer who cannot parse the body will not parse
  the diff either.
- **PR body: one screen.** What it does, why it matters, repro, one test line, the
  checklist. Cut the reasoning that the diff and tests already show.
- **Comments: a few lines.** State what changed and anything a reviewer must know to
  judge it. Do not restate the body or narrate the investigation. If the claim already
  links the relevant issue or ruling, do not add a redundant "See #..." closing sentence.
- **Diagrams only when the relationship is the message.** A tree, mindmap, flowchart or
  sequence diagram earns its space only for topology, dependency, branching or state-flow
  relationships that are hard to follow in prose: a before/after pipeline shift, or a
  failure path that diverges by configuration or version. Prefer a compact ASCII/Markdown
  tree for shallow structure; reserve mermaid for the cases where arrows and branches
  carry the claim. Add a legend when node labels are abbreviated, and link the code
  (commit, line range) each node summarizes. Never diagram a list: counts, comparisons
  and per-item evidence belong in a table, and a diagram that restates the adjacent prose
  is clutter. Worked example, PR #111800 review (2026-09-18): two mermaid flowcharts for
  the before/after validation pipeline and one sequence diagram for the two-branch
  cancellation failure carried the findings; the verification numbers stayed in tables.
- **Recommend, don't direct, on a repo you do not maintain.** An imperative in a
  comment ("Keep one, close the other three", "Settle the contract first") reads as
  authority you do not have. Give the observation and leave the decision with the
  maintainer: "this is the only one covering both platforms, so it may be the cleanest
  to keep — maintainers' call". Same rule for issues and review requests.
- **State the fact, do not forecast the merge.** "This one skips non-regular files;
  #109752 refuses them" — not "one difference if this one lands". Predicting whether it
  merges hedges and reads as an order at once.
- **Superseded comments get deleted, not stacked.** Self-correcting in a new comment
  leaves a stale claim in the thread; edit or delete it and post one current comment
  (the user reads the whole thread cost first).
- **Check whether the repo enforces its template before obeying it.** A
  `pull_request_template.md` proves nothing on its own — sample the last ~15 merged PRs
  (`gh pr list --state merged --limit 15 --json number,author,title`, then read each
  `.body`). hermes-agent: none use the template; house bodies are 14–29 non-blank lines
  with no checklist, shaped as an outcome sentence → `## Changes` (terse
  `path::symbol — what` bullets) → a before/after `## Validation` table → refs. A body
  3–4× that reads as a diary, and a checklist nobody else fills in is not a virtue.
  Match the merged-PR register; keep whatever sections the house style actually uses.
- **Only condense where nobody has engaged.** Editing the description of a PR or issue that
  already has comments or reviews makes contributors re-read text they had already judged
  (and looks like rewriting history under discussion). Zero third-party comments/reviews is
  the bar; leave the rest alone.
- **Check where a long explanation lands before parking it.** A code comment or commit
  body is read only by someone already in that file or history — but "in that file" is not
  automatically cheap: a file that is `@embedFile`'d into a binary, or written to disk by an
  installer, ships its comments to every installation. Grep the ship path first, then match
  the neighbouring comments' length (2-3 lines is the usual register) and keep only the why.
- **Measure the prose share of an asset diff.** Count added comment bytes against total
  added bytes; a file grown mostly by prose is a review liability and a per-install
  footprint at once. Trim to the why and let the tests carry the rest.
- **Match the maintainer's real register for commit bodies.** Sample their recent commits
  (`gh api repos/O/R/commits?author=X`) and count non-blank body lines instead of guessing;
  a body several times the house median reads as a diary. Keep root cause plus a pointer to
  the proof, leave raw test output in the PR body where a reviewer looks for it, and keep
  the subject on one line.
- **Copy the maintainer's SHAPE, not just their length.** Read 3-4 of their PR bodies before
  writing on that repo (`gh pr list --repo O/R --state merged --author <them> --limit 4 --json
  number`, then `gh pr view N --repo O/R --json body`). hermes-agent / teknium1, verbatim from
  #109880, #109841, #109649: one sentence giving the new behaviour in plain words, blank line,
  `## Root cause` (1-2 sentences), `## Changes` (terse `path::symbol — what` bullets),
  `## Validation` as a before/after table, then plain lines for follow-ups. No hedging (`may`,
  `worth`, `arguably`), no framing lines ("Two notes for..."), no first person, no restating the
  diff. A comment is the same voice in 2-3 lines.
- **Never open with a frame you invented.** These are AI tells a reader deletes: "Two things worth
  stating plainly", "Findings first, because", "The honest gap I will not paper over", "I want to
  be upfront that", "One caveat worth flagging". A frame that promises the reader something is not
  the thing. Start with the fact.
- **State a limitation as a fact, not a confession.** "Not verified live: idle-reap persistence is
  unit tests only" beats any sentence about being honest; the reader forms the trust judgement.
- **Do not vouch for your own compliance.** "Only the test file changed, and it is exactly the salvage
  that check asked for" is the agent assuring the reader it followed instructions; the maintainer edited
  it to "Only the test file changed (the salvage that check asked for)". State the correspondence flatly,
  or put it in a parenthesis. Same family as the verdicts and rankings below.
- **Keep drafting scaffolding out of posted text.** "DRAFT 1", "Option (a) vs (b)", "PLAN, pending
  your go" belong in chat while a decision is pending, never in a PR body, issue, or comment.
- **Narrate the result, not the search.** "after tracing", "I checked X and found", "it turns out".

### 6e. Before you post, run the prose gate

The register rules in this skill are enforced by a command, because a comment went out
carrying five em dashes and a self-referential opener while the writing skill sat unloaded:

```bash
python3 <skills-root>/clean-writing/scripts/check-prose.py <file-to-post>
```

Exit 1 blocks the post. It flags em dashes, clause-stitching semicolons, banned frame
openers and signposts, paragraphs over 420 characters, and internal identifiers, and it
skips fenced code so quoted command output is not punished. Fix or justify each hit, post,
then re-fetch the live body and run it again:

```bash
gh api repos/O/R/issues/comments/<id> --jq .body > /tmp/live.md
python3 <skills-root>/clean-writing/scripts/check-prose.py /tmp/live.md
```

A clean local file proves nothing about the published text, and the gate is the only check
that has actually caught anything. Posting with it unrun is the failure mode to avoid, not
a style preference.

## Never publish internal identifiers

Public text carries no internal names: profile names, team or agent names, local filesystem paths,
usernames, hostnames, session ids. A profile becomes "a custom profile"; a home path becomes
`~/.hermes/state.db`. Ambient detail feels harmless while writing and is exactly what the reader
reads as a leak.

Run the pass before posting, not after:

```bash
TEXT=comment.md
grep -niE "<profile names>|<team name>|<usernames>|/Users/|/home/" "$TEXT" || echo clean
# the redactor ships in the PROFILE skills dir, not team-skills:
# ~/.hermes/profiles/<profile>/skills/desert-ant-local/scripts/da_redact.py
cat "$TEXT" | python3 "$SKILLS/desert-ant-local/scripts/da_redact.py" --json \
  | grep -o '"label": *"[A-Za-z_]*"' | sort | uniq -c
```

A non-zero `EXIT:` from the wrapper means a review-required label class, so inspect before posting.
`IP_ADDRESS` on a loopback literal (`127.0.0.1`) is informational. After posting, re-fetch the
published body and grep it again: an audit of the local file proves nothing about the live text.

**Audit by surface class, not by what you just posted.** An issue body is a body you own, and it is
the surface most often missed — a sweep that covers the comment you just wrote leaves the report you
opened the thread with. Enumerate every surface you authored in the engagement, then grep each:
every PR body, every issue body, every comment, and any quoted command output inside them. Quoted
terminal output is where identifiers hide: a pasted `served_profiles` list, `lsof` rows, and
`--profile <name>` command lines carry names and home paths that prose does not. Redact the list to a
count (`"served_profiles": [9 profiles, names withheld]`) and a home path to `~/.hermes`.
A second model's independent pass is what caught the issue body the author's own audit missed.

**Timestamp a point-in-time measurement in the artifact itself.** A number taken from `ps` or `lsof`
(RSS, fd counts, pids) cannot be reproduced minutes later, so an independent verifier can only call it
unverifiable, and a reader cannot tell whether it was ever true. Write the value with its basis —
"462.7 MB RSS at 12:11Z (`ps -o rss= -p 69004`)". Structural claims re-verify on demand; measurements
carry their timestamp or they decay into noise.

## Pitfalls
- **A red platform leg on a PR is judged against the same leg on `main` and against an open tracking issue, never from the PR's diff alone.** A matrix leg can be red for years while the other platforms are green, so the question is which assertion the red is actually on. `gh api repos/O/R/actions/runs --jq` filtered by `head_sha`, then `repos/O/R/actions/runs/<id>/jobs` for the leg's job id, then `repos/O/R/actions/jobs/<job>/logs` and slice the run's own outcome lines (`fitted geometry:`, `pat: CHANGED`, the failing assertion) verbatim. Compare those lines across main's newest run, the PR head, and every prior branch that touched the same area. Two runs of the same SHA (a `push` run and its `pull_request` twin) failing on identical lines is the repeat-run evidence that rules out flake, so a re-run is not needed to call it. Then search the repo's issues for the symptom before attributing anything to the PR: an open issue naming the exact failing assertion is the root cause of record, and the verdict is 'pre-existing, tracked as #N' rather than a fix request against the contributor.
- **A rebase is not verified by 'it applied cleanly'. Compare trees.** `git merge-tree --write-tree <base> <prhead>` (git 2.38+) prints the tree a correct three-way merge would produce, and exits 0 when the merge is clean. If that tree hash equals the rebased head's tree (`git rev-parse <rebased>^{tree}`), the rebased commit IS the merge result: no hand-edit, no mis-resolved hunk, no silently dropped file can hide in it. Pair it with `git range-diff <old-base>..<prhead> <new-base>..<rebased>`, which shows path renames (the usual rebase-byproduct) and any hunk that actually changed, and with `git diff --numstat <new-base> <rebased>` to confirm the file scope is exactly the PR's.
- **A wide pytest run can die on the shell's file-descriptor limit and look like broken code.** On macOS the default is `ulimit -n 256`; a multi-file invocation then fails during tmp-path iteration with `OSError: [Errno 24] Too many open files`, producing hundreds of collection ERRORs. Re-run with `ulimit -n 6144` before reporting or dismissing them, and say in the receipt which limit was in force.
- **Prove red-on-base without mutating the checkout.** `git show <base>:<path> > <path>` edits a tracked file in the tree you are auditing. Instead extract a read-only copy: `mkdir -p /tmp/base && git archive <base-sha> | tar -x -C /tmp/base`, copy the PR's NEW test files over it, and run pytest from there with the repo's interpreter. `git archive` writes nothing back, and the failures you get are genuine base failures (name them: the missing symbol in the AttributeError is the proof the test binds to the new code).
- **A scanner that only sees the diff is not a scanner.** A repo gate that
  enumerates via `git add -A -n` (or `git status`) lists only index-relative
  changes — on a clean checkout it scans **zero files** and prints PASS. Prove a
  gate enumerated something before trusting its PASS: in a throwaway clone run
  `git read-tree --empty`, then re-run it. The first PASS on a pristine clone is
  usually vacuous; the real failure appears only once a file is modified.
- **Count the terms a scan loaded, not the ones you assume.** A term/denylist
  built from environment or a gitignored config is empty in your checkout, so one
  scan can miss most names actually present. Check the inventory size, and use a
  direct `grep` per known name to get the true exposure count before reporting
  either a leak or a clean pass.
- **A pre-existing gate failure is still a merge gate.** If a branch's
  public-safety scan also fails on `main`, reproduce it in a `main` worktree and
  report it as a blocker with an owner decision — never silently edit a deliberate
  authored artifact (branding copy, published names) just to force green.
- **A content-hash dedupe collapses legitimate repeats when the producer's payload carries no per-event identity.** Before accepting an "idempotent by payload hash" claim, read the producer: if it writes the payload verbatim and puts the identity in the file name (`pid-nowMs-seq.json`), then two genuinely distinct events with the same body are indistinguishable from one double-posted event. Prove it against the machine's own history — count distinct payload contents against the file count (a spool with 699 files and 3 distinct bodies means 696 legit events the dedupe would swallow) — and reproduce it by spooling two identical payloads in one run and again in a later run, comparing against the base behaviour. A per-run window is not a fix: the second event is dropped inside a single run too.
- **Test crash recovery by asserting the spool EMPTIES, not only that XP is conserved.** A drain that records a "consumed" cursor before writing state and then skips those files on re-read will award nothing on replay — which looks correct — while the already-consumed files sit in the spool forever (the prune loop only walks the freshly classified set). Run the kill/replay, then assert the spool is empty and diff against the design doc's acceptance check about the spool holding no absorbed event.
- **A bounded "consumed" cursor is a ceiling, and a ceiling below one batch re-awards the overflow.** When a fix makes a cursor durable (names and/or payload hashes) so a crash-then-replay cannot award twice, prove the BOUND against the largest single run the producer can absorb, not against the observed steady state. Spool more files than the bound, kill the process between the durable write and the prune, re-run, and compare the award against an uninterrupted run. A run that drops its oldest names or hashes hands them back to the replay, and any dedupe the same fix scoped away (a route exemption, a per-run window) no longer catches them, so the extra award is permanent and the leftover files are never pruned. Size the case from the producer's own artefacts before calling it unreachable: bucket the consumed directory's mtimes into per-interval arrival bursts, and quote the design doc's own backlog figures. Verified 2026-09-19 on a TamaHermes drain: cursor caps of 1000 names and 2000 hashes against a documented 914-file backlog, where 1200 care presses re-awarded 200 events (600 XP) and 2500 route-B payloads re-awarded 500 events (2000 XP) after a SIGKILL in the window.
- **Review comments are claims, not results.** When an automated/peer review flags
  your PR, reproduce the finding against the PR HEAD in a throwaway worktree
  (`git worktree add /tmp/prN <sha>`) before replying: the substance can be right
  while the detail is wrong (a bot claimed PyYAML folds `y`/`n` to bools — it does
  not; they resolve as strings). Then run the PR body's OWN "how to test" steps —
  a body that documents a repro which doesn't reproduce is worse than no repro,
  because the next reviewer runs it. Best reply = confirm what's real, correct the
  wrong detail, add the case the comment missed, and name what is deliberately out
  of scope.
- **Check for an adjacent instance of the same bug class the comment missed.** A
  guard keyed on a parsed type silently ignores the same value written in another
  form (e.g. a `isinstance(value, bool)` gate misses the quoted STRING `'false'`
  that the CLI itself writes for string-typed defaults). Probe the neighbouring
  writer/entry point, not just the reported token.
- **Read the consumer's precedence before judging a fallback's policy.** When a PR
  adds a fallback value to a payload field, the component that READS the field may
  prefer it over a different fallback it already applies further down, so the new
  value displaces a better existing label/behaviour — and the variant the body calls
  the "safer default" can be the one that loses information. Grep the field to its
  read site and name which source wins the `orelse`/`??`/`||` chain. Live user data
  is the tiebreak: the artefact on the author's own machine showed the displaced
  value in use while the prose claimed otherwise.
- **A new test can pass with the change deleted — test the trigger, not the tick.**
  Attribute each new case individually. If an earlier fallback short-circuits the
  condition the case is meant to exercise, its assertion is vacuous (delete the new
  clause and it still passes). Build the input only the new branch can decide (the
  unreadable/keyless case, not the readable one an existing fallback already covers)
  and confirm that case goes red without the code.
- **Intent comes from the repo's own user-facing surfaces, not from inference.** To
  settle "is this behaviour a bug or intended", quote the maintainers' own artefacts:
  a settings label, a documented model, the framing of the linked issue. A defect-filed
  issue plus a merged guard on the same class settles it; a behaviour the author merely
  likes is a fork concern, not a reason to re-open PR scope.
- **Detector/linter additions must be idempotent and meaning-preserving.** For any
  check that rewrites config on `--fix`: run it twice and assert the second pass
  reports and rewrites nothing (a guard that re-fires every run is worse than no
  check), and pin that the rewrite never changes the resolved runtime behavior.
  Prefer the value the runtime already resolves to over "the sane default" — the
  fix's job is to make an existing state legible, not to choose for the user.
- **Flag resolver-side accidents instead of cementing them.** When a legacy value
  only resolves the way it does through an accident (e.g. a blank/null value
  stringified to `"none"` and caught by an alias map), the surface fix should name
  it and preserve behavior, and the PR/comment should hand the underlying quirk to
  the resolver's owners as its own item.
- **A text search for a key's value is wrong in both directions — read the parsed node.**
  Detection that greps a file for the value while reading the effective value from a
  dotted key will report a documented value as drift when a decoy line exists
  elsewhere (another section, a block scalar, a duplicate block), AND miss the shapes
  a value really takes: flow style, a quoted key, an anchored `*alias`, and a
  duplicate block whose last entry wins. `yaml.compose()` + walking to the key's own
  node gives the scalar as written (quoted vs plain, `off` vs `false`) with no
  false-positive surface. Write the test table with both classes: decoys that must
  stay silent, and shapes that must be caught.
- **A repo's self-test that clones HEAD tests the last COMMIT, not your working tree.**
  A `fresh-clone-test.sh`-style gate (`git clone <repo> <tmp>` internally) silently
  re-verifies the previous commit while your new edits sit uncommitted — it passes, and
  proves nothing about the change you are about to push. Commit first, then re-run the
  gate, and check the gate's own printed SHA equals your new HEAD.
- **A fresh `git clone` lands on the DEFAULT branch, not the PR.** `git clone` then
  `git diff origin/main...origin/pr` audits correctly, but running the repo's gates in
  that working tree runs them against `main` — and they PASS, so nothing looks wrong.
  Fetch `refs/pull/N/head`, `git checkout --detach <head-sha>`, assert
  `git rev-parse HEAD` equals the audited head, and only then run the gates. Re-run after
  any such slip and say so; a green suite on the wrong tree is a fabricated result.
- **Merging is not publishing.** Before claiming a site or deploy update, prove a deployer
  exists: a CI workflow, a git-linked hosting project (a Vercel `link: null` plus deployments
  carrying no commit sha = manual CLI uploads only), or hosting enabled (`GET /repos/O/R/pages`
  → 404 = disabled). A merged docs PR with no linked deployer changes zero served bytes.
- **Credentials + a found project do not authorise a production write.** Knowing where the
  site lives and holding a valid token is not a documented deployment path. When no repo,
  KB or runbook defines the artifact set and the deploy would publish more than the audited
  diff, report "merged, site pending external deployment" and hand the user the decision
  instead of inventing a deploy — and never let a merge imply a live change.
- **A frozen-lockfile failure on a dependency-bot PR is the lockfile, not the bump.** On
  bun/pnpm/yarn repos the bot edits `package.json` and leaves the lockfile alone, so the
  install step dies with `lockfile had changes, but lockfile is frozen` and every later
  step (typecheck, tests) is skipped. The red check reads as a compatibility failure it
  never tested, so say which steps did not run. Repair: run the installer on the branch,
  commit the lock diff, push to the bot's branch, then read the new head back. Check the
  lock diff holds only the bumped entries, so unrelated packages were not re-resolved.
- **Same change mirrored across repos/forks: compare TREES, not titles or SHAs.** When the
  same PR appears in an upstream repo and one or more forks/siblings, a different head SHA
  can still be a byte-identical change. Prove it with the tree hash
  (`git rev-parse <head>^{tree}`) and file hashes — equal trees mean one review covers all,
  and the change must land in each repo or be synced. A per-repo head SHA alone tells you nothing.
- **A fork's CI can show "no checks reported" because the workflow never ran there.**
  `gh api repos/O/R/actions/runs --jq .total_count` = 0 with `actions/permissions.enabled=true`
  means the fork's Actions were never triggered (GitHub disables workflows in forks by default),
  not that the branch is clean. State the count; do not read an empty rollup as a pass.
- **A "partition"/split PR pair is atomic: reconcile both deletion sets against the base.**
  Two PRs that split one tree must have complementary, non-overlapping deletion sets whose kept
  unions re-cover the base; if one lands alone the repos sit in an inconsistent split. Flag the
  pair as one change with two merge points, and check the KIT/TEAM (or A/B) classification is
  backed by a real artifact — a body citing "per manifest" when no manifest uses those terms is
  an unsourced hand-assignment.
- **A squash merge's tree equals the PR head tree only when the branch base is main's head.** The
  post-merge check `git rev-parse <merged main>^{tree}` == `git rev-parse <prhead>^{tree}` proves the
  squash introduced nothing beyond the diff, but it is only valid when the branch was cut from the
  current main. A branch cut before another main commit lands differs by exactly those commits. When
  the trees differ, run `git diff --name-status <prhead> <merged main>` and `git diff <prhead> <merged
  main> -- <the extra paths>`, confirm the delta is the later merge and nothing else, and state it.
  Read the inequality as a defect, or the equality as clean without checking the base, and the receipt
  is wrong either way.
- **A stacked PR whose base branch was SQUASH-merged reports MERGEABLE against a base that no longer exists.** When #4 lands by squash, the branch ref `feat/verify-hashes` keeps pointing at its pre-squash head, and GitHub computes the child PR's `mergeable`/`mergeStateStatus` against that stale ref — CLEAN on a PR that conflicts with the real `main`. Never read the badge as the landing check. Run `git merge-base --is-ancestor <base-head> origin/main` (false = stale base), then `git merge-tree --write-tree origin/main <pr-head>`: exit 1 names the conflicting paths. The usual conflict is one hunk where both sides appended at the same place in a docs file, and the fix is to keep both additions. Also note the child branch carries its own pre-squash copies of the parent's commits, so a `git rebase origin/main` conflicts far worse than the merge does — resolving the merge is the cheaper path. Verified 2026-09-19 on {{org}}/protean-sym2p PR #9.
- **A fresh-`git clone` landing on the default branch is only half the trap; a stacked PR has no CI at all.** A `pull_request` workflow with `branches: [main]` does not run for a PR whose base is another branch, so `actions/runs?head_sha=<pr-head>` returns `total_count: 0` for the whole life of the PR. An empty rollup there is expected, not a stall — but it also means the audit's own local runs are the ONLY evidence, so say so rather than implying CI covered it.
- **A gate's file count is not the gate's coverage.** `check-internal-names.py` printed `49 files scanned` on a tree of 53 tracked files (it skips its own script, the blocklist, and binary extensions) and printed the same `49` on a tree that had just GAINED a file while losing another. Diff the scanned count across the two trees you are comparing, and when it is unchanged, confirm the new file was actually enumerated before accepting the clean result.
- **A signature convention can collide with the repo's own leak gate.** An org rule to sign public posts `protean-<role>` becomes a leak when the gate hashes every alphanumeric token and blocks the roster names: `protean-reviewer` tokenizes to `protean` + `reviewer`, and `reviewer` is a blocked digest, so the signature itself fails the gate on a public artifact. Before posting a signed identity, run the repo's own gate over a file containing the signature (or hash the tokens against its blocklist) and pick a suffix whose tokens are all clean (`protean-review` passes). Verified 2026-09-19 against `gates/protean-sym2p/internal-names.blocklist`.
- **A published-file writer's refusal is only proven by the bytes.** For any claim that tooling "refuses to rewrite", verify by hash, not by exit code: hash the published file, run the rewrite attempts (same path with changed content, an explicit version pin, a declared version contradicting the chain, a symlink or directory occupying the target path), then re-hash and require the bytes to be unchanged. Add an N-way concurrent race for one id and validate every resulting file with the repo's own reader; a chain that comes out monotonic and hole-free under contention is the real evidence. Verified 2026-09-19 on `sym-publish.py` (8-way race: 7 versions, all valid, one racer refused `ERR:VER`).
- **A `--dry-run` that skips directory creation can still fail on the directory it skipped.** `sym-publish.py` skipped `makedirs` under `--dry-run` (correct) and then listed the directory anyway, so the documented first-use check exited 1 on every fresh delegation root — while the same command without `--dry-run` succeeded. The suite's dry-run case reused a sandbox an earlier case had already populated, so it never reached the fresh-root path. When auditing a dry-run flag, run it against a virgin tree, not the one the suite hands you. Verified 2026-09-19 on {{org}}/protean-sym2p PR #9.
- **A stacked sibling PR keeps the superseded rule, and its thread comment keeps asserting interop.**
  When a PR is force-pushed after review, any PR whose base is that branch still carries the old
  head's contract, plus its own copy of the retired rule and a passing test count measured against
  it. A comment reporting "N/N interop pass" then reads as evidence for the rule in the branch under
  review. Check the sibling's base SHA (`gh pr view <n> --json baseRefName,headRefOid`) and grep its
  diff for the superseded rule before repeating any interop claim; name the rebase and the amendment
  it needs in the review record.
- **An offline-fallback claim cannot be tested with CDP offline emulation.** `Network.emulateNetworkConditions{offline:true}` is scoped to the page target, so the service worker's own `fetch` still reaches the real server: the uncached navigation returns the server's 404 and the browser paints an error page, which reads as the fallback being broken. It is not. Kill the serving process for a genuine connection refusal (the SW sees it too), then re-test the uncached navigation — and if the phase needs a live server first, have the harness spawn it, register/install, then SIGKILL it. Verified 2026-09-20 on nutrak PR #5: emulated offline gave `Error response`, killed-server gave the cached app shell (1358 chars, correct title).
- **A receipt that names the head you already audited cannot contain new work.** When a "fix pushed at <sha>" note repeats the SHA of the last commit you reviewed, the claimed change is not in the tree — read the branch's commit list (`git log --oneline origin/main..<sha>`) and the file the note says changed. Two commits whose newest subject is the earlier finding means nothing was pushed for the newer one. Verified 2026-09-20 on nutrak PR #5: a watch note claimed the send-back items were fixed at c3dd279, the head that already carried only the earlier npm-test wiring, and both items were still absent from that tree.
- **A green suite can still miss a deliverable the acceptance bar ENUMERATES.** When a
  PR answers a spec whose bar names files, URLs or assertions one by one, walk that list
  against the diff yourself: the bar's own test list is usually written as "the lists are
  defined and every entry exists", which a REDUCED list passes. Verified 2026-09-19 on a
  PWA package whose precache list specified six optional URLs, shipped three, and passed
  both suites while the body summarised its own reduced count as conformant. Diff the
  enumeration, do not read the test result.
- **The required checks live in the repo's RULESET, and branch protection can read "not protected" while an active ruleset enforces them.** `gh api repos/O/R/branches/main --jq .protected` returned `true` with `protection.enabled: false` and `/branches/main/protection` 404'd, yet an active repository ruleset required three status checks on the default branch. Read `gh api repos/O/R/rules/branches/main` (or `/rulesets`) before calling any leg required: on {{github_owner}}/EvoPet the required contexts were App tests and typecheck, bun install --frozen-lockfile and SDK pins agree, so the long-red `build windows` leg was outside the required set even though the workflow's own header says no leg may fail. A red leg outside the required set is a pre-existing note, never a merge gate, and a ruleset with `required_approving_review_count: 0` means the provider enforces no approval at all.
- `gh api -f body=@file` posts the literal path, not the file. `-f`/`--raw-field` never expands `@path` — only `-F`/`--field` reads a file (or pass `--input payload.json`). A review submitted with the wrong flag is a contentless entry whose body is the local path itself (one leaked a `/path/to/home/kanban/workspaces/.../review_body.md` onto a public PR), and it cannot be deleted afterwards: `DELETE /pulls/N/reviews/<id>` returns HTTP 422 `Can not delete a non-pending pull request review`. Repair it in place with the GraphQL mutation `updatePullRequestReview(input:{pullRequestReviewId:"PRR_…", body:"…"})`, which does work on a COMMENTED review (verified 2026-09-22 on PRs 119165 and 119172). Then always read back the POSTED body — `gh api repos/O/R/pulls/N/reviews/<id> --jq .body` — not just the POST response's id and state: the id/state read-back passes even when the body is junk. HARD GATE: never publish with `-f body=@...`; use `gh pr review N --comment --body-file FILE` or `gh api ... --input JSON`. Before posting, reject any body whose first nonblank line begins `@/`, contains `/Users/`, `/home/`, or `/tmp/`; after posting, fetch the exact review body and reject the operation if it differs byte-for-byte from the intended file.
- **A merge-path verdict binds approval to the exact audited head, and "verification only" is not an approval.** Count the review objects, not the comments: `gh api repos/O/R/pulls/N/reviews --jq length` was 0 while the thread carried a changes-requested pass against an earlier head and a later "verification only" note on the current head. The fix landing on the new head does not carry the verdict forward, and a re-verification that explicitly disclaims a verdict does not either, so the honest verdict is HOLD with the missing item named. State the clearance path in the same breath (a fresh-eyes verdict on that head posted as a QA-pass comment, because the author account cannot approve its own PR) so the HOLD routes work instead of stalling it.
- **Stale handoff diff** → you review phantom code. Always re-diff live.
- **Trusting PR-body test counts** → false evidence a reviewer catches. Run it.
- **`gh pr diff` needs `--repo O/R`** when not inside a cloned repo context.
- **`gh pr review --approve` self-block** → use a comment, never a fake approve.
- **A pull-only account cannot submit `REQUEST_CHANGES` either.** On a repo where `gh api repos/O/R --jq .permissions` returns `{"pull":true,"push":false}`, both `gh pr review --request-changes` and `POST /pulls/N/reviews` with `event=REQUEST_CHANGES` fail with `Review Can not request changes on a pull request without explicit repository access` (HTTP 422). Check permissions BEFORE promising a changes-requested verdict, then post the identical body as `event=COMMENT` (`gh api -X POST repos/O/R/pulls/N/reviews --input payload.json` with `{"event":"COMMENT","body":...}`) and state the verdict in the body's first line. Verified 2026-09-19 on NousResearch/hermes-agent PR 116492; review id and URL still resolve normally.
- **Never chain the prose gate and the post in one shell line.** `python3 check-prose.py f && gh pr review ...` is fine, but a `;` or newline between them publishes the text the gate just rejected. Run the gate as its own call, read the exit code, then post, then re-fetch the live body and run the gate on that too. A signature line (`— <lane>`) that matches the thread's own convention is a justified exception; a paragraph over 420 chars is not.
- **`python` missing from PATH** → use `./venv/bin/python` or `uv run`.
- **Unqualified test IDs** → class-qualify them (`TestClass::test_method`).
- **Editing a PR body you don't own** → only do it on your own account's PRs.
- **`gh pr comment --edit-last` REPLACES the comment body.** It edits your last
  comment in place; it does not append. So never write "Correction to my comment
  above/below" — after the edit nothing remains to point at. Rewrite the whole
  comment self-contained.
- **GitHub auto-cross-references — check the timeline before posting a pointer.**
  When another PR or issue names this one, GitHub adds a "referenced this" event to
  the timeline on its own. So a comment that says "this is already handled in #N" is
  redundant the moment #N already names this item. Check first:
  `gh api repos/O/R/issues/N/timeline --jq '[.[] | select(.event=="cross-referenced") | .source.issue.number]'`
  — if the PR you would name is in that list, the link is already visible in the
  thread. Comment only when it would otherwise be invisible.
- **One cross-reference comment per PR, not two.** Two tracking notes on the same
  PR read as two maps of one thing. Fold them into one, then delete the redundant
  one — list ids with `gh api repos/O/R/issues/N/comments --jq '.[] | "\(.id)
  \(.user.login) \(.body[0:40])"'`, delete your own with `gh api -X DELETE
  repos/O/R/issues/comments/<id>`.
- **Don't tie your mechanism to a peer's reproduction without checking the harness.**
  A comment claimed a peer's orphan-FD count "partly stemmed from" another writer;
  their harness ran against a temp database with no such process in it. Read what
  the reported repro actually runs before claiming it is your bug too — the
  retraction costs more than the claim was worth.
- **A browser tool that refuses `127.0.0.1` is not a blocker: the local Chrome is still a real browser.**
  For the live-DOM half of §2b, serve the tree and drive Chrome yourself over CDP from node. Serve with
  `terminal(background=true)` (a tracked process; `nohup`/`setsid` wrappers are rejected, and a stale
  listener on a reused port answers with an empty reply, so pick an unused port and assert HTTP 200 first).
  Node >= 22 has a global `WebSocket`, so no dependency is needed: launch Chrome with `--headless=new
  --remote-debugging-port=N --user-data-dir=<tmp>`, `PUT /json/new?about:blank`, `Runtime.enable` +
  `Log.enable` + `Page.enable`, collect `Runtime.exceptionThrown` / `Log.entryAdded` for console errors,
  and assert with `Runtime.evaluate` + `returnByValue` against the live DOM and `getComputedStyle`.
  Top-level function declarations in the app's classic script are reachable as globals, so a probe can call
  them directly. Run the same script against the deployed URL after the merge: with Pages enabled the
  merge publishes, and that run is the click-through the body usually says is missing.
- **`encodeURIComponent` leaves `'` unencoded, so a single-quoted attribute is not safe to build from it.**
  `<button data-prov='${encodeURIComponent(JSON.stringify(obj))}'>` truncates the moment any value contains
  an apostrophe, and the consumer's `JSON.parse` then throws. Flag it by probing with a value that carries
  one (`"women's RDA"`) and check whether any shipped data reaches it before calling severity: a field fed
  only by engine output with no apostrophes is latent, not live. HTML-escaping the encoded string fixes it,
  because the character reference decodes back to `'` after tokenization without ending the attribute.
  When a fix escapes some fields in a template but not the ones beside them (`n.target` left raw while
  `n.unit` is escaped), name the remaining fields; partial escaping is the usual shape of this gap.
- **Prove red on the real base, not in prose.** Do not assert the old behaviour
  in the body. Swap the base version of the touched file into the working tree
  (`git show origin/main:<path> > <path>`), run the new tests, restore, and quote
  the exact failure. A test that never failed against the base can pass for the
  wrong reason, and a reviewer who runs it will find out.
- **Do not claim an unverified half.** When the repo's toolchain is missing
  locally (no zig, no rust, no pnpm), fix and test the surface you can execute
  and say in the body which surface you could not build. Never let a green local
  run imply the unbuilt half passes; drop it from the diff or flag it plainly.
- **Verify which artifact carries a cost before acting on it — or dismissing it.** "This
  ships with every install" is a claim about one specific surface: git metadata, an
  embedded/installed file, or reviewer attention. Measure the mechanism before agreeing or
  pushing back (grep how the file reaches the user, size the delta) and name the surface
  that pays. The fix often lands in a different artifact than the claim names — and when the
  named surface is right, the delta can still be too small to matter (hundreds of bytes in a
  multi-MB binary). Argue from the measurement, not from the instinct.
- **Writing into a live install: verify both ends by hash, and quote the restore path from
  a listing you read.** Prove the backup equals the pre-change source and the installed file
  equals the new source (`shasum` on both sides, `git show ref:path | shasum` as the
  reference), then state the rollback command from the real filenames — tooling flattens or
  renames copies (`_Users_name_.hermes_plugins_x___init__.py`), so a path you assembled from
  memory does not exist. An unverified restore instruction is worse than none, because it is
  followed at the worst moment.
- **The docs gate can validate a file nobody links.** A checker reads one path while README/CONTRIBUTING
  link another (Typejoy: `scripts/check-docs.mjs` reads repo-root `API_REFERENCE.md`, the links point at
  `docs/API_REFERENCE.md`). The gate then passes green while readers see the stale copy, and the two can
  already disagree on a signature the PR just changed. Before accepting a "docs pass now" claim, read the
  path inside the script and the path inside the links, and require one canonical home.
- **The repo's own CONTRIBUTING rules are part of the merge bar.** Read them for operational obligations the
  change must satisfy and quote the line. Typejoy requires bumping the `?v=N` cache-buster in `demo.html` on
  every behavior-changing rebuild, and a manual `demo.html` browser run for any wiring or feedback-layer
  change. A body that says "not verified in a browser" while CONTRIBUTING mandates exactly that run is not
  mergeable on paperwork, whatever the unit tests say.
- **A hash-verification PR is judged against the hashes already in the repo, and the covered SCOPE is
  what drifts.** Before accepting a content-hash recomputation, reproduce every declared hash already
  in the tree under the candidate rules: hash over the object body alone, over the whole document minus
  the hash field, each in sorted-key compact and insertion order, plus any trailing newline. A change
  can be internally consistent (its own fixture matches its own function, its own new tests pass)
  while disagreeing with every artifact shipped before it, and then the new fail-closed flag declares
  the repository's own worked example tampered. Verified on a protocol repo: five declared object
  hashes all reproduced as sha256 over the canonical body, the PR proposed the whole document, and
  `--verify-hashes` exited 1 on the shipped example while CI stayed green because the suite only ran
  the flag on the PR's own fixtures. The author's search had varied key order and encoding but never
  the covered scope, so the body called the pre-existing values placeholders. Reproduce the old value,
  name what the alternative scope leaves outside the hash (version, author, retention, access list,
  lineage), and ask for a test case over the repo's own artifacts.
- **Drive a local build in a real browser through Chrome's own CDP, because the browser tool refuses
  loopback.** `browser_exec` answers "URL targets a private or internal address" for `127.0.0.1`, so serve
  the tree (`python3 -m http.server <port>`) and control Chrome directly: `--headless=new
  --remote-debugging-port=<p>` with a throwaway `--user-data-dir`, then
  `websocket.create_connection(ws_url, suppress_origin=True)` (without `suppress_origin` Chrome 403s the
  handshake), `Target.createTarget` + `Target.attachToTarget{flatten: true}`, and
  `Runtime.evaluate{awaitPromise: true}` with a driver script that returns JSON. Check the port is free
  first: another lane's preview server can hold it and the bind fails silently while the page 404s.

## When a docs pass is the actual task

Sometimes the request is not "audit this PR" but "this text confused a reader,
fix the writing". The audit discipline still applies, scoped down:

- Verify the served/repo text live (API or curl), not a cached copy. Baseline
  the SHA before editing; read the result back from the API after pushing — a
  push that reports success but changes no served bytes is a failed push.
- Edit on a branch even for docs-only changes; commit with a message naming the
  pass (`docs: rewrite README in plain language`), not a vague `docs update`.
- Enumerate the reader-facing surfaces before editing: README, repo About line
  (`gh repo view --json description`), site HTML (often `index.html` in the repo
  root, served from the same repo), landing-page meta description. One branch,
  one PR, all surfaces — so the reviewer sees the whole pass in one thread.
- Copy in place when the asset lives in the same repo: a site rewrite is a
  normal file edit, no new repo or deploy step. Verify what is actually served
  with a cache-busted fetch before and after.
- Structure is not copy: in a page rewrite touch only text nodes, `title`,
  `meta description`, `og:title`. CSS classes, ids, anchors, asset references
  and the nav stay byte-identical or rendering breaks. Verify: HTML parses, all
  `#anchor` links resolve to existing ids, local serve returns 200 for the page
  and each referenced asset.
- Specific method names (Erlang/OTP, YAML, Apache-2.0) stay — introduced with
  one plain sentence each, not stripped.
- If the text sits in an open PR thread a human already judged, the
  only-condense-where-nobody-has-engaged rule applies to it too.

## References
- `references/verification-recipe.md` — copy-paste command sequence for a full
  pre-merge audit (live diff, main premise check, venv test run, body-claim
  verification, comment posting).
