# Changelog

All notable changes to Exo. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow [docs/VERSIONING.md](docs/VERSIONING.md): SemVer, with a codename per MAJOR (0.x is egg).

## [Unreleased]

### Added
- Modular `lean`, `coding` and `full` harness profiles, individual capability overrides, dependency
  explanations and dry-run CLI configuration. Disabled services omit tools and startup resources.
  The approval gate and required executor constraints remain enforced. Existing configurations
  without a profile retain legacy behavior (`docs/HARNESS.md`, D-082).
- Dependency-aware plugin composition and synchronous resource cleanup on failed registration or
  final shutdown. Folder requirements are validated before import. Entry-point requirements before registration.
- Optional scoped `AGENTS.md`/`AGENTS.override.md` workspace guidance, with bounded reads and independent
  main/child session scope. `CLAUDE.md` fallback is configurable (`docs/WORKSPACE-INSTRUCTIONS.md`).
- A sourced harness landscape comparison and phased roadmap with evaluation gates (`docs/HARNESS-LANDSCAPE.md`).

### Fixed
- Plugin tool/slot adaptation and hook-verdict translation now share the plugin lifecycle bridge, with
  explicit runtime collaborators and compatibility shims. Counted core size falls by 67 lines and the
  headroom allowance returns to 1,000. Approval behavior is unchanged (D-077/D-082, #498).
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
  `overflow_settle` for them, as the agent tree's turns do. With a slow summarizer its first calls can still carry
  most of the thread.
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
