# Changelog

All notable changes to Exo. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow [docs/VERSIONING.md](docs/VERSIONING.md): SemVer, with a codename per MAJOR (0.x is egg).

## [Unreleased]

### Security
- The gate reads the command behind wrappers that run one (#160, D-038): `setsid`, `flock`, `chroot`, `unshare`,
  `nsenter`, `taskset`, `chrt`, `numactl`, `chronic`, `unbuffer`, `catchsegv`, `firejail`, `dbus-run-session`,
  `xvfb-run`, `systemd-run` and `watch`, after their options and fixed operands, and the command strings of
  `flock -c`, `script -c`, `watch`, `tmux new-session`/`new-window`/`split-window`/`respawn-pane`/`respawn-window`/
  `run-shell`, `tmux -c`, `screen -dm` and `parallel CMD ::: args`. `setsid git push origin main` was `opaque_exec`,
  so a deny on `vcs_push` only asked; it is now `vcs_push` and denied. Until D-075 lands, a wrapped line is denied
  only when its inner command outranks `opaque_exec`; `setsid systemctl enable foo` under `deny: [schedule]` still
  asks. A tmux command list is split at an argument ending in `;` (`'sleep 3;'`), not only at a `;` word, so
  `tmux new-session -d 'sleep 3;' new-window 'git push origin main'` is `vcs_push` too, and tmux's default
  aliases `splitp` and `split-pane` are read as `split-window`. An environment dump behind a wrapper
  (`setsid printenv GITHUB_TOKEN`, `taskset 1 env`, `xvfb-run -a env`) is `credentials` like the bare command
  (#155), so `deny: [credentials]` denies it; it was `opaque_exec` and asked. The wrapper keeps the
  `indirection` floor, so a line that asked still asks; `chroot`, `nsenter`, `unshare` and `systemd-run` add a
  signal like `sudo`. A command the gate cannot read (`setsid $CMD`, `tmux send-keys`, `parallel` reading its
  input) stays `opaque_exec`. A deny on `opaque_exec` itself (`deny:`, a category line or a rule) now asks for
  such a line instead of denying it; naming `opaque_exec` at `ask` or `deny` no longer turns off the indirection
  floor (#178), so none of these lines logs.
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
  A path the gate cannot resolve (a NUL byte) keeps its lexical check instead of raising. `cp -s`, `cp -l` and a
  relative `ln -s` target that alias the store ask too. `docs/GATE.md` lists what stays open (hardlinks, protected
  entries that link out of the store, case-insensitive filesystems, glob and brace spellings).
- Inspect commands inside `coproc` without losing quotes (or, in the simple form, the heredoc), while
  retaining the indirection floor (#131). A name before a compound command (`coproc w { ...; }`,
  `coproc w while ...`, `coproc w if ...`) is skipped as bash does, so the inner command is classified;
  `function NAME { ...; }` is read the same way. An environment dump inside either (`coproc env`) is
  `credentials`. Until D-075 lands, a coproc is denied only when its inner command outranks `opaque_exec`.
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

### Added
- Opt-in signed updates (#42, D-073): with `update.require_signed` true, `exo update` pulls only an upstream commit
  signed by a key in a pinned trust list (`exo update --pin-trust`). Off by default.
- Versioning and naming convention (D-056), this changelog, and `exo --version` in the `0.2.0 (egg)` form.
- Web chat: a completed reply renders as Markdown, GFM pipe tables included (#149, #152). Streaming stays plain
  text. Tables scroll sideways inside the message. A reply builds at most 10,000 table cells and a table at most 64
  columns and no more padded cells than source characters; past that, a table is shown as its source text. Quotes
  nest at most 8 deep and lists 9. Ordered lists keep their source numbers across blank lines, fences and nested
  items; a table, quote or fence indented under an item renders inside it; paragraph line breaks, hard breaks and
  `>` quotes are kept; a line that starts with inline code is not a fence; table cells unescape only `\|`; unclosed
  `[` and link URLs no longer cost quadratic time; the classic layout keeps following the conversation when a reply
  grows as it renders. Markdown panels share the renderer, so they too keep their line breaks and render quotes.

### Fixed
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

### Changed
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
- Git in bash and background jobs no longer fails with "missing config key GIT_CONFIG_KEY_0" when the core starts
  with `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` set (#159). The scrub dropped only the keys;
  it now keeps or drops the family as one unit (`docs/TOOLS.md` "Environment of bash and jobs").
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
- `scripts/deploy-site.sh` no longer tags and pushes a version after a failed deploy, which burned the version and
  exited 0. A failed copy or deploy now exits non-zero before tagging, a failed tag push exits non-zero, the script
  refuses to run without `rsync` (it used to deploy a near-empty site), and a versioned deploy refuses uncommitted
  changes under `site/`.

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
- The gate's decision call no longer falls back to the main model (D-081). With no decision or aux route, the rule
  outcome stands and no model is asked. The rules still apply in full, since a model could only make an outcome
  stricter (D-015). A setup with only a main model loses that second opinion: the main model no longer turns a
  call the rules allow into an ask (`docs/GATE.md`).
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
