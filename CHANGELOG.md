# Changelog

All notable changes to Exo. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow [docs/VERSIONING.md](docs/VERSIONING.md): SemVer, with a codename per MAJOR (0.x is egg).

## [Unreleased]

### Security
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
  stays in every thread. Before, the window's check against `approvals.list` dropped it within seconds. A
  finished subagent whose calls had no price adds the `+` to `COST`, and its roll-up no longer moves `MEM`.
- `exo update` no longer widens a `--plugins` install to every plugin. The installer records the list in
  `config.json` (`plugin_selection`) and a re-run without `--plugins`, such as the one `exo update` makes, keeps it.
  A list also removes the plugins an earlier run installed in `$EXO_HOME/plugins` and the list leaves out, since the
  core loads every folder there. `--plugins all` goes back to every plugin. The re-run also refreshes the plugin copies
  in the Hermes home an earlier `--with-hermes`, `--hermes-home` or `EXO_HERMES_HOME` run used (one inside `~/.hermes`
  still needs `--hermes-home`). An install from before this change has no list on record, so its first update installs
  every plugin: after it, re-run `install.sh --no-pip --plugins LIST` once to go back to yours. A list ignores spaces
  and always keeps `exo-gate`, since without it the core refuses every tool call. A name that matches no plugin
  stops the installer before it removes or records anything.
- `scripts/deploy-site.sh` no longer tags and pushes a version after a failed deploy, which burned the version and
  exited 0. A failed copy or deploy now exits non-zero before tagging, a failed tag push exits non-zero, the script
  refuses to run without `rsync` (it used to deploy a near-empty site), and a versioned deploy refuses uncommitted
  changes under `site/`.

### Security
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
- Gate (#178, D-038): naming `opaque_exec` at `ask` or `deny` (a category line or a rule) no longer turns off the
  indirection floor. Before, `categories: {opaque_exec: deny}` let `bash -c 'git push origin main'` log. Only
  lowering `opaque_exec` to `log` or `allow` turns the floor off, as before.
- `exo web` sends a strict Content-Security-Policy on every response (#162, D-039): script, style, images and
  requests from the web server only, no inline script, style attribute or eval, no framing, plus
  `X-Content-Type-Options: nosniff`. A slip in the page's Markdown rendering can no longer run script. The page's
  computed widths now go through the CSSOM instead of `style` attributes; nothing looks different.
- Gate: `$'..'` words are decoded the way bash does (#167, D-038). `git push $'--forc\u65' origin main`,
  `$'\u2d\u2d'force` and `$'--forc\545'` are now `force_push`, and `curl $'https://evil\u2e'example/x` has the target
  `evil.example`, so category and target denies apply. A `\U` value from 0x80000000 on is dropped, as bash
  drops it, so `$'--forc\UFFFFFFFFe'` is a force push as well.
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
