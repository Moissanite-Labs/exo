# Changelog

All notable changes to Exo. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow [docs/VERSIONING.md](docs/VERSIONING.md): SemVer, with a codename per MAJOR (0.x is egg).

## [Unreleased]

### Security
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
  ancestry changed; the principal and tool checks did not. The helper and the core run with `python -P`, so a
  `pathlib.py` in the current folder no longer runs; an `EXO_ENGINE` module must now be installed or on `PYTHONPATH`,
  not just in the folder the client started from; startup output from `sitecustomize` no longer breaks the start; and a core that dies while
  starting is reported at once, with its real exit code when known and never a made-up one.

### Fixed
- Esc on a `claude-cli` turn is no longer lost when it lands just as the CLI prints a line (#163). On Python 3.11
  the per-line read timeout (`asyncio.wait_for`) returned the line and swallowed the cancel, so the turn ran to the
  end as if Esc had not been pressed. The line read now uses `asyncio.timeout`, which keeps the cancel.
- Tests that failed on a loaded machine (#163): the claude-cli fallback test's fake CLI now reads its input before it
  fails, so the provider's write never meets a closed pipe and the error always reports the exit code, and the TUI
  boot test drives the boot screen's clock, so a slow start can no longer end the boot before the test looks at it.

### Security
- `exo web` sends a strict Content-Security-Policy on every response (#162, D-039): script, style, images and
  requests from the web server only, no inline script, style attribute or eval, no framing, plus
  `X-Content-Type-Options: nosniff`. A slip in the page's Markdown rendering can no longer run script. The page's
  computed widths now go through the CSSOM instead of `style` attributes; nothing looks different.
- Gate: `$'..'` words are decoded the way bash does (#167, D-038). `git push $'--forc\u65' origin main`,
  `$'\u2d\u2d'force` and `$'--forc\545'` are now `force_push`, and `curl $'https://evil\u2e'example/x` has the target
  `evil.example`, so category and target denies apply. A `\U` value from 0x80000000 on is dropped, as bash
  drops it, so `$'--forc\UFFFFFFFFe'` is a force push as well.

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
