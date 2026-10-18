# The action gate: approvals and the outbound scrub

`exo.gate` classifies every tool call and decides allow, log, ask or deny from the principal's
policies (`policies/gates.yaml`, `budget.yaml`, `posting.md`). The `exo-gate` plugin runs it on
each call, records the decision in the signed ledger, and turns an ask into the protocol event
`approval.requested`. This page covers what D-033 added: what an approval covers, who may give
it, how long "always" lasts, and the sweep for internal names in outbound posts. D-038 made the
shell classifier an allowlist and put the store itself behind the gate (see the last three sections).

## Required executor audit

With a required tool executor, the gate refuses execution unless its signed
decision event was committed to the principal ledger. After a principal answers,
the approval event must also commit before the broker or handler runs. Missing
stores, unusable signing keys, failed writes and gate hook exceptions block with
a generic error, including read tools and observe-mode exception fallbacks.
Optional observer hook failures remain optional. Observe mode still asks, and
`exo-gate` must load. Ordinary Exo retains its existing logging-only behavior
when no ledger is available.

Required mode blocks `gh --body-file` requests before inspecting the selected
host path, including symlinks, even when body-file reading is disabled. The gate
cannot approve unseen contents. Pass inline text for inspection; the executor
then applies its own operation policy. Policy and grant files remain trusted
configuration outside the worker's writable workspace.

Concurrent identical requests keep separate approval tokens and audit
references. The reusable rule key still scopes a standing grant; it does not
identify a pending call. Required mode needs the exact per-call token when the
engine reports an answer.

Accepting changed policies commits the acceptance audit before replacing the
pinned policy. Creating a required standing grant first commits an authorization
event (`phase: grant`, `state: authorized`, `activation: pending`) and only then
atomically writes the grant file. Audit failure leaves authority unchanged. A
subsequent grant-file failure blocks the call; its audit describes authorization,
not successful storage. These two stores do not form one atomic transaction.

## What an approval covers

An outward act is a post, message, email, social post, payment or account action, or anything
the gate marks public or irreversible. For those, the gate hashes the exact text, title, target,
tool and arguments (`approval_binding`). Text read from a `--body-file` counts too. The hash is
sent as `binding` on `approval.requested`.

- An answer covers that hash only. If the agent edits one character, the next call has a new
  hash and is asked again. This is the same rule as a review verdict that a new head sha voids.
- A client may echo `binding` in `approval.respond`. If it differs from the open request's hash
  the answer is refused as `stale`, so an approver who read an older draft cannot pass a newer
  one. A client that sends no binding keeps the old behaviour.

## Who may approve

`policies/approvals.yaml` maps a category to who may answer:

    approvers:
      default: [principal]
      categories:
        github_post: ["role:public-reviewer"]
        social_post: ["role:public-reviewer"]
    roles:
      public-reviewer: ["seat:my-lead-seat"]

Entries are `principal`, `seat:<name>` or `role:<name>`. The principal can always answer. With
no file, only the principal can, which is the behaviour of a single-user install. A peer or
ingress client never can (D-026). The allowed list is sent as `approvers` on
`approval.requested` so a client can show who must answer; the engine refuses anyone else as
`forbidden`.

A client answers as a seat by setting `client` to `seat:<name>` and signing the answer with that
seat's Ed25519 key: `sig = exo.core.approvals.sign_approval(key, request_id, decision, binding,
seat)`. The signature covers the request id, the decision, the request's binding hash and the
seat name, so it cannot be reused for another request, decision or text. An unsigned or badly
signed seat answer is refused as `forbidden`. The public key comes from the bus registry when
that document verifies, else from `<keys dir>/<seat>.pub`.

## Who answered: the connection, not the body (D-039)

The core never takes the answerer from the `approval.respond` body. The transport stamps a caller:

- **principal**: the terminal client's own in-process core, the web client, or a socket client
  that sends the core's per-launch principal credential in the `X-Exo-Principal` header. The
  credential is a random token the core writes beside its socket when it starts
  (`$EXO_HOME/run/exod.principal.key` or `run/agent-<seat>.principal.key`, mode 0600) and deletes
  when it stops. A credential sent from a process that is a child, grandchild and so on of the
  core (a bash tool call or a background job) does not count.
- **local**: everything else on the socket (a tool subprocess, a channel, another daemon), and
  every stdio or TCP connection.

A principal connection answers as the principal; `client` is only a label (`principal:tui`,
`principal:web`). A local connection can answer only as a seat, with the signature above. An
empty `client`, `principal`, `exo-tui` or any other name from a local connection is refused as
`forbidden`, so a tool that speaks the protocol cannot answer its own approval. The answerer shows
in `approval.resolved` as `by`.

A seat may approve one exact text with `allow`. Only a principal connection can answer
`allow_always`; anything else is refused.

Limits. The credential file is guarded by the gate: its name ends in `.key`, so reading it is a
`credentials` act that always asks. No environment variable carries it, and `tool_env` drops
`EXOD_*`, `EXO_PRINCIPAL*` and `EXO_*SOCK*`. A tool process is the same OS user, so code that
both reads the file without the gate seeing it and detaches from the core's process tree could
still pass. The full fix is an OS sandbox for tools; until then the gate and the process-tree
check are the guard.

## Secret-store paths

A path names a secret store (`credentials`, always asks) when it matches the store list in `exo.gate`
(`~/.ssh`, `~/.aws`, `~/.config/gh/hosts.yml`, `~/.docker/config.json`, `~/.kube/config`, `.env`, `*.key`,
token files, Exo's `keys/`...), or when it is a glob that could match one of the sample store paths. This holds
for shell words, redirection targets, `bash -c` and `$( )` text, string literals in code, and the path arguments
of file tools. Brace sets are expanded as bash does before matching (issue #184). In a shell word the gate
expands the source text, quotes kept, and then removes the quotes from each word, so only unquoted braces
expand: `'{a,b}'`, `"{a,b}"` and `\{a,b\}` stay literal, while in `''{},.env}` the `{` follows a quote, is not
at the start of the word, and opens a group. A `$"..."` is also read as the plain `"..."` bash gives it without
a message catalog, so `cat $""{.env,x}` asks. A path argument of a file tool has no quoting and is expanded as
written. The scan follows bash's: a group ends at the first top-level `}` after a comma or `..`, so `{},hosts}`
is one group; a `{x..y[..n]}` sequence expands to its members, padded as bash pads them (a leading zero on
either bound pads every member to the longer bound, so `{007..1000..993}` is `0007 1000`); nested groups expand
too; `${...}`, `$(...)` and groups with neither a comma nor a sequence stay literal. The word as written and
every word it expands to are checked, and a hit on any counts, so `cat ~/.config/gh/{hosts,x}.yml`,
`cat ~/.docker/{*,}` and `cat ~/{},.netrc}` ask.

The work is bounded. A word that would expand to more than 1,024 words (or past a million characters in all),
nests groups more than 32 deep, or has a sequence bound longer than 18 digits counts as a store path (asks)
rather than being matched in part. So does a zero-padded sequence with a bound past a 32-bit int: bash prints
its members as C ints, so they wrap (`{04294967296..04294967296}` is `00000000000`). One tool call expands at
most 4,096 words in all, across its words, redirections, nested commands and code literals (a glob word counts
four times, and a long word once more per 64 characters); past that, each further word with a brace set counts
as a store path too. The trade-off: this fails closed, so an ordinary line past a limit, such as
`echo {1..2000}` or `for i in {1..2000}`, is `credentials` and asks every time with no grant. A string literal
in code is read as a shell command, so compact JSON with 11 or more objects in one literal
(`'[{"a":1,"b":2},...]'`) goes past the cap the same way.
A relative name read in a store folder a `cd` entered counts too, since #192 also for stores matched only by
sample paths (`cd ~/.config/gh && cat hosts.yml` asks; see "The working folder" below).
Known limit: a file tool's path is checked, not what the tool reaches below it. The built-in `grep` and `glob`
tools walk every file under their path (the working folder when none is given), so a search rooted above a
store, such as the home folder or `$EXO_HOME`, is `read` and allowed: `grep` returns matching lines from the
store's files and `glob` lists them (#241).
Tests: `tests/security/test_gate_brace_sets.py`.

## Muse CLI credentials

`muse login`, `muse auth` and `muse logout` are `credentials` acts, like `vercel login`: they always ask and never
get a standing grant. The match is on the word anywhere after `muse`, so `muse --yolo login` is caught, and a
prompt that is exactly one of these words over-asks, which is the safe side. `~/.config/muse/` is a secret-store
path: reading or writing `auth.json` there is `credentials` too. Other `muse` commands are unknown programs and
ask by default. Paths to the Muse executable and nested shell commands keep the credential classification;
lowering `opaque_exec` by policy or granting an opaque command cannot bypass the credential floor.
Tests: `plugins/exo-gate/tests/test_credentials.py`.

## Standing grants ("always")

An `allow_always` answer becomes a grant with a scope, an expiry and a ledger row
(`phase: grant`). Grants live in the principal store at `state/approval-grants.json` (mode 0600),
or at `$EXO_HOME/state/gate-grants.json` without a store, so they survive a restart. Each use is
logged with the grant id.

- Outward acts: the scope is the category, the target and the exact text hash. There is no
  unscoped "always" for an outward category. Default expiry 7 days (`always.outward_days`).
- Other acts: the scope is the category and the exact target. Default expiry 30 days
  (`always.days`).
- Credentials and store writes never get a grant; they ask every time (`gate.NO_GRANT`).
- Code the gate cannot see into (`opaque_exec`, or any call with the `indirection` signal) is
  granted for the exact command only, like an outward act.
- Only the principal can create a grant.
- Set a value to 0 to turn standing grants off for that kind.
- `/grants` (a `command.run` command, no new protocol command) lists live grants;
  `/grants revoke <id>` removes one and writes a ledger row (`phase: revoke`).

## The outbound scrub

Posts to public targets (`posting.applies_to`, by default GitHub posts, messages, email and
social posts) are swept for internal identifiers. A hit holds the post and lists each one, for
example `internal name: my-lead-seat` or `home path: ~/`. The gate builds the list itself:

- seat names from `$EXO_HOME/seats` and the bus registry (`$EXO_HOME/bus/registry.json`), and
  agent names from the store's `agents/`;
- home paths: `/Users/<name>`, `/home/<name>`, `~/`;
- model ids from the model catalog, `provider:model` refs, and `claude-*-<n>` and `gpt-<n>`;
- internal ids of the form `<prefix>_<16 hex>` (sessions, tasks, jobs, approvals);
- the user's own list: `policies/internal-names.txt` (one per line), `$EXO_INTERNAL_NAMES`, and
  `posting.scrub.names` and `posting.scrub.patterns` in `posting.md`.

**Matching tradeoff.** A name that is one plain word (a seat called `canary` or `scout`) matches
only as a whole token, in any case: "scouting" passes, "the scout said" is held. A name with a
hyphen, underscore or digit and four or more characters is distinctive, so it also matches
inside longer words and with any separator ("my lead seat", "my_lead_seat"). The cost of a false
positive is one extra ask; the cost of a miss is a leaked internal name in a public repo, which
cannot be taken back. So the sweep leans to holding. Put a word in `posting.scrub.allow` to stop
a false positive that keeps recurring, or rename the seat.

The sweep runs on public targets only (`posting.scrub.exposures`). Internal team chats and the
principal's own channels are not swept.

## The footer

`posting.footer` is the exact line an outbound post must end with. It is empty by default, so no
line is built in. A principal who wants a disclosure line sets it in `posting.md`, for example
`footer: "> Automated posting by agentic team with human oversight."`, and can limit it to public
targets with `footer_exposures: [public]`.

## Shell commands: an allowlist (D-038)

Command regexes in `deny` and `reserved` match both the original command and a literal-word
view from the shell lexer, so `command:rm -rf` also vetoes `r"m" -"rf" ~`, `'rm' -rf ~`,
`r\m -rf ~`, `$'rm' -rf ~` and `$"rm" -rf ~`: common literal quoting cannot weaken a veto.
A word that holds a variable or a substitution is a placeholder in the view, so a regex that needs
the literal part of such a word matches only the original text.
A `$"..."` word is read as bash reads it without a catalog, so the line keeps its category and
target (`curl $"https://evil.example/x"` is a `web_read` of `evil.example`, and a `deny: [web_read]`
still denies it). With a message catalog and a non-C locale bash runs a translation instead, and
expands `$( )` in it, so the line also carries the `indirection` signal and asks unless
`opaque_exec` is named. It is decided a second time as `opaque_exec`, and the stricter outcome
stands, so a deny, reservation or rule on `opaque_exec` still holds for it. A heredoc whose
delimiter is spelled `<<$"EOF"` or `<<$'EOF'` ends on different lines in bash and in dash, which
runs commands when bash is missing, so its body is read as commands too.
Mapping entries use the same matching. A rule with decision `deny` or `ask` whose match has a
`command` regex sees the view too, in a second pass that only raises: the first matching rule
is still chosen on the original text, and the pass lifts the outcome to the rule's decision only
when the view matches and the original text did not, so an ordered list such as an allow for one
push target before an ask on `^git push` keeps working for plainly spelled commands. Allow and
lower rules, and grants, match the original text only. The view keeps operators and word
boundaries; whitespace, control characters and shell syntax inside a word are escaped, and
dynamic words and heredoc bodies become placeholders made of control characters, which no
literal word renders to and no regex naming letters or operators matches, so a veto on `>` or
`exo` does not fire on `ls $HOME`. It does not expand variables or run a shell. A `$"..."` inside
`${...}` is left to the parameter-expansion work (#143, #158). Nested views come from the text the
classifier already reads: command substitutions and backticks, `bash -c`, `eval`, shell stdin and
every string literal of code (`python -c`, `execute_code`), with the existing depth limit of
four. The gate can only guess which literals in code are commands, so each literal is matched as
a command even when the code merely prints it: `python3 -c "print('rm -rf build')"` is denied under
`command:^rm -rf`, fail closed, since the same path catches `os.system('rm -rf build')`. The
reason names only the policy entry, never the matched words: decisions go to the ledger, which
holds no command text. An unanchored veto regex also matches separately quoted data arguments
(`printf %s r"m" -"rf"` matches `rm -rf`), fail closed by design. These remain regex policies,
not a shell sandbox: original-text matches still apply, spellings outside literal quoting need
their own patterns, and dynamic or deeper execution asks through `opaque_exec` rather than being
caught by a command-regex veto.

The shell classifier used to name dangerous programs and let everything else through as `shell`.
Any indirection walked past it. It now works the other way round. A small lexer
(`exo.gate._Lexer`) reads quotes, escapes, `$( )`, backticks, `$VAR`, redirections and heredocs,
and splits the line into simple commands. Each simple command has one program position (after
assignments, reserved words such as `if` and `do`, and prefixes such as `sudo`, `env`, `timeout`).
A `$'..'` word is decoded with bash's ANSI-C rules (bash 5.2, UTF-8 locale; issue #167): `\n`, `\e`
and the other letter escapes, octal and `\x` values (also `\x{..}`) cut to 8 bits, `\u` with 1 to 4
and `\U` with 1 to 8 hex digits, `\cX` (`\c\\` takes one backslash), and an unknown escape kept with
its backslash. A NUL ends the quoted part, so `$'--forc\0x'e` is `--force`. A `\u` or `\U` value that
is no character is written as bash writes it: a few bytes that are not ASCII, or nothing at all from
`\U80000000` on, so `$'--forc\UFFFFFFFFe'` is `--force` too.

A command is `allow` only when every program in it is known and nothing the gate reads is hidden
(the known forms it does not read yet are listed under "Known limits" below):

- Known programs: `READ_PROGRAMS` (cat, ls, rg, head...), `SHELL_PROGRAMS` (mkdir, cp, pytest,
  ruff...), the tools with their own rules (git, gh, curl, vercel, stripe, ssh, exo...), and
  `TOOL_SUBCOMMANDS` (npm install, cargo test, go build, uv sync...). A program given by path
  runs as itself only from a system bin folder or a virtualenv or `node_modules/.bin` folder.
- Everything else is `opaque_exec`, which asks: a variable, `$( )` or a glob in the program
  position; a script file; `bash`/`sh`/`zsh` reading `-c`, a file, a pipe or a heredoc;
  `python -c`, `node -e`, `perl -e`, `ruby -e`, `osascript`; `make`, `npm run`, `npm test`,
  `npx`, `uvx`; `xargs`; `find -exec`; `eval`, `source`, `.`, `alias`, `trap`, shell functions;
  git aliases and command-running git config; gh aliases and extensions; an unknown program.
- Where the gate can see inside (a `bash -c` string, a heredoc or pipe fed to a shell, the code
  of `python -c`, a `$( )`, the command after `xargs` or `find -exec`, the program after
  `uv run`) it classifies the inner text too, within a bounded inspection depth (four levels,
  see "Known limits"). The result is the most severe part, and it keeps the `indirection`
  signal, so `decide` asks even when the inner part alone would only log.
- Nesting is read four levels deep. Past that the text is `opaque_exec` and asks. An `ssh` or
  `mosh` chain with more hops than that is not read either: the hops that were read keep the
  `remote host` exposure and the first host as target (so a `target:` deny on it still
  matches), and the rest asks as `opaque_exec` (#153). A chain the gate can read to the end is
  classified by what runs at the end.
- Options of trusted programs whose value is a command are followed as a nested command, like
  `find -exec` (#154): `tar --to-command`, `-I`, `--use-compress-program`, `--rsh-command`,
  `--rmt-command`, `--set-mtime-command`, `--checkpoint-action=exec=`; `split --filter`; `sort
  --compress-program`; `man -P`, `--pager`; `rsync -e`, `--rsh`, `--rsync-path`; `scp` and
  `sftp -S`, `-D`; `ssh`/`scp`/`sftp -o ProxyCommand`, `LocalCommand`, `RemoteCommand` (with the
  `remote host` exposure) and `KnownHostsCommand`; `git rebase -x`/`--exec`, `git bisect run`,
  `git submodule foreach` (also after `-q`, `--quiet`), `--upload-pack` (`clone`, `fetch`,
  `pull`, `ls-remote`), `git push --receive-pack`/`--exec`, `git archive --exec`; `rg --pre`;
  `zip -TT`; `less +cmd` and `more +cmd` with a `!` (macOS `more` is `less`); the `.shell` and
  `.system` dot-commands of `sqlite3` and `duckdb`, and `.once`, `.output`, `.import` and `.read`
  with an argument starting with `|`, also after options (as an argument, via `-cmd`, or on
  stdin); `psql -o '|cmd'`/`--output`, and `\!`, backquotes in a meta-command's arguments, and a
  `|command` after `\o`, `\out`, `\g`, `\gx` (also after `(options)`), `\w` or `\write`, quoted
  or not, anywhere on a `psql` line; `\!` and `system` in `mysql`; and a command set in
  `GIT_SSH_COMMAND`, `GIT_SSH`, `GIT_PAGER`, `GIT_EDITOR`, `GIT_SEQUENCE_EDITOR`,
  `GIT_EXTERNAL_DIFF`, `GIT_ASKPASS`, `SSH_ASKPASS`, `RSYNC_RSH`, `PAGER`, `MANPAGER`, `EDITOR`,
  `VISUAL`, `LESSOPEN` or `LESSCLOSE` before the program (also through `env`; `export NAME=value`
  counts when the value is a known hazard).
- Options are read as the program's `getopt` reads them: an unambiguous abbreviation of the long
  name, one letter or more (`--to-com=`, `psql --o`), or the value attached to the short letter or in the next word. In a
  cluster the first letter that takes a value ends the word (`-xIcmd`, `-oProxyCommand=cmd`,
  `-avze ssh`), and the gate knows which letters and long options take a value for `ssh`,
  `scp`, `sftp`, `tar` (also its old style, `tar xIf cmd a.tar`), `rsync`, `man`, `rg`, `psql`
  and the `git` subcommands, so a value is not read as options (`ssh -oServerAliveInterval=60`
  is not `-I`). A letter it does not know only makes it read more words. Options end at `--`;
  `ssh` reads them before and after its destination and stops at the remote command, so `ssh
  host curl -I URL` is the `curl` line; when a word before that may expand to no word or several
  (`$A`, `"$@"`, a glob), the destination cannot be counted and every word is read. An `ssh -o` setting is split as ssh's config reader
  splits it: leading whitespace, a quoted keyword, any case, and `Keyword value`,
  `Keyword=value` or `Keyword = value`. `less` reads `+cmd` as keystrokes: `!` runs a shell
  command after any of them (`+G!cmd`, `+:n!cmd`), while a search (`+/pattern`) takes the rest
  as its pattern unless a newline or another control key ends it.
- A known program in an option's value (`tar -I zstd`, `rsync -e ssh`, `PAGER=cat`) stays
  `allow`; a program the gate does not know asks, as it would on its own. A value the gate cannot
  read is `opaque_exec`: a variable, a script file (`tar --info-script`, `sqlite3 -init` or
  `--init`, `.read`, `.load`), `.once`/`.output FILE`, `.read '|cmd'` (what the command prints is
  run), `less +!` with no command or `+!!`, `psql ... PROGRAM`, `TAR_OPTIONS`, and a library
  `ssh` loads (`-I`, `-o PKCS11Provider`, `-o SecurityKeyProvider`). `sed` scripts with `e` or
  `s///e` and `awk` programs calling `system()` were already opaque. An argument that merely
  looks like an assignment (`grep EDITOR=vim`) and a data load (`sqlite3 '.import f.csv t'`) are
  not commands.
- Environment dumps count as `credentials` wherever a prefix or runner is stripped (#155):
  `printenv`, a bare `env`, `set`, `export -p`, `declare -x` and the like behind `sudo`, `nice`,
  `timeout`, `env`, `ionice`, `stdbuf`, `coproc`, `uv run`, `poetry run`, `pdm run`, `pipenv run`
  or `hatch run`, or inside an `if`, `for` or `while` body, classify as `credentials` just as the
  bare command does. A runner followed by ordinary work (`uv run pytest -q`) is unchanged.
  `uvx`, `pipx run` and `npx` stay `opaque_exec`.
- `coproc` inspects the inner command's original words, preserving quotes (and, in the simple
  form `coproc bash <<EOF`, the heredoc), while retaining `indirection`. As in bash, a name
  before a compound command (`coproc w { ...; }`, `coproc w while ...`, `coproc w if ...`) is
  skipped; before a simple command (`coproc w echo hi`) the first word is the program.
  `function NAME { ...; }` is read the same way. An environment dump inside (`coproc env`,
  `coproc w { printenv GITHUB_TOKEN; }`) is `credentials`. Until D-075 lands, a coproc is denied
  only when its inner command outranks `opaque_exec` (`delete`, `vcs_push`, `credentials`); a
  category that ties with it or ranks below it (`schedule`; `web_read`, `local_write`) still
  asks. A heredoc after the closing `}` of a block (`coproc W { bash; } <<EOF`) attaches to the
  `}` and is not fed to the inner command, which asks as `opaque_exec`; the same limit applies
  to `{ ...; } <<EOF` on its own.
- Code (`python -c`, `execute_code`, a heredoc fed to an interpreter) is read for known calls
  and for string literals and lists of strings that parse as a command
  (`subprocess.run(['gh','pr','comment',...])` is a `github_post`).
- The principal can lower `opaque_exec` only by naming it below ask: `categories: {opaque_exec: act}`
  or a rule whose match has `category: opaque_exec` and whose decision is `act` or `report` (for
  example with a `command` regex for `make`). Naming it at `ask` or `deny` keeps the indirection
  floor: a command the gate reads inside a wrapper still asks at least, whatever its own category
  says (#178).

Known limits. Each one is true of main today; where a fix is open, its PR is named.

- **Inspection depth (#137).** Nesting is read four levels deep (`_DEPTH = 4` in `exo.gate`).
  The bound holds for a `bash -c`, `sh -c` or `eval` chain called from the bash tool:
  `gh pr comment 3 --body hi` inside up to four such wrappers is still `github_post`; inside
  five, the innermost text is not read and the line is `opaque_exec` with the `indirection`
  signal, so the default policy asks. The card names the outermost wrapper (`bash runs shell
  commands`, `eval runs text as a command`), not the cap or the hidden command, so it is the
  same card as `bash -c 'foo'`: a compound classification keeps the first of equally severe
  parts, and the `nested too deep to read` part that `xargs`, `find -exec`, `uv run` and `$( )`
  add at the cap reaches the card only when the outer part is benign (`uv run`, `echo $( )`).
  A command found inside a code string (`python -c`, `node -e`, `execute_code`, a heredoc fed
  to an interpreter) gets one more level wherever the code sits, because the code reader starts
  again at the last level: one `bash -c` inside `os.system(...)` is read; a second usually is
  not (it depends on how the literals are quoted) and the line asks, as `opaque_exec` or, through
  `execute_code`, as `code`. A chain that mixes wrappers and code strings can therefore be read
  deeper than four levels. Nested `$( )` is flattened by the lexer and not capped; nested
  backticks are: four levels are read, and at five the line is `opaque_exec`, `nested too deep to
  read`. An `ssh` or `mosh` chain with more hops than the cap asks as `opaque_exec` too, with the
  first host as its target, so a `target:` deny on that host still denies it (#153, fixed by
  #164). Past the cap the line asks unless the principal has set a policy for `opaque_exec` (a
  `command:` deny entry or a deny rule that matches the text still denies it).
- **A category deny fires on the category the line ends up with.** `classify_shell` keeps the
  most severe part of a compound line, and `categories:` applies to that category only. With
  `github_post: deny`, the four-wrapper example is denied and the five-wrapper example asks as
  `opaque_exec`. On a compound line a more severe part can take over and the line asks:
  `gh pr comment 3 --body hi && rm -rf build` is fully inspected, but it is `delete`, the card
  reads `rm build`, and approving it sends the denied post. A category name in the `deny:` list
  behaves the same today (a `command:` regex entry is matched against the whole line). D-075
  (#126, in review) makes a `deny:` or `reserved:` entry match every part of the line within the
  inspection depth; `categories:` keeps matching the dominant part (#191). So approving an opaque
  card or a compound line can run an action that would be denied on its own. Read the whole
  command before approving it: an approval does not vouch for what the gate did not classify.
- **Hidden text that is allowed outright.** Some unread execution does not even ask today. An
  entry for the hidden action's category (`github_post`, `delete`...) does not reach it, in
  `categories:`, `deny:` or `reserved:`. An entry that matches the command text does (a
  `command:` regex, or a rule with a `command` match). Since #136 that regex also sees the
  literal-word view of the text the gate reads, so quoting no longer evades it there. Inside a
  `${...}` body, a quoted subscript or value, stdin sent to `ssh`, or a command-valued option the
  gate does not read (the forms below), the regex sees only the raw text, so quoting still evades
  it there until each form's fix lands:
  `echo ${X:-$(g'h' pr comment 3 --body hi)}` is allowed under `command:gh pr comment`. The
  command-valued options listed above have been read since #164, so their values are in the view
  (`tar --to-command='g"h" pr comment 3 --body hi'` is denied); the ones #164 left unread are in
  #230 and #232. The known forms:
  - `${...}` bodies are skipped by the lexer, so `echo ${X:-$(gh pr comment 3 --body hi)}` is
    `read` and allowed (#143; fix in review in #158).
  - A `$( )` inside a quoted array subscript given to a builtin that takes a variable name
    (`printf -v`, `read`, `declare`/`typeset`, `unset`, `test -v` and `[ -v`, `wait -p`) is run
    by bash, but the gate reads the quoted word as plain text, so
    `printf -v 'a[$(gh pr comment 3 --body hi)]' %s x` is `read` and allowed (#179; fix in review
    in #189).
  - A quoted array, `-i` or `-n` value given to `declare` or `typeset` is expanded again by bash,
    so its `$( )` runs: `declare -a 'b=($(gh pr comment 3 --body hi))'` is `read` and allowed
    (#188; fix in review in #194, stacked on #189).
  - Text piped or fed by heredoc to `ssh` with no remote command runs in the remote login shell,
    but the line reads as a plain `ssh`: `echo 'gh pr comment 3 --body hi' | ssh host` and
    `ssh host <<EOF` are `shell` and allowed under every category deny; a `target:` entry for the
    host still denies them (#240).
  - Forms next to #164's coverage: a dynamic `ssh -o "$OPT"`, `git ls-remote --exec=...`, and an
    environment dump behind a `NAME+=value` prefix (#230); the rest are listed in #232 (part of
    which, `git clone -c`, is in review in #231).
- **Other deny-to-ask paths.** Where the gate cannot read the command it files the line as
  `opaque_exec`, which asks, and the card names the wrapper rather than the action: `eval $(...)`
  and `eval "$(...)"`; a command arriving on stdin, `echo '...' | xargs -I{} sh -c '{}'` (while
  `echo 3 | xargs -I{} sh -c 'gh pr comment {} --body hi'` is read and denied); the wrappers
  `watch`, `parallel`, `tmux`, `screen` and `setsid` (#160; fix in review in #196); a `coproc` or
  `function NAME {` whose inner command ties with `opaque_exec` or ranks below it, such as
  `coproc w { curl https://example.com; }` under a `web_read` deny (the inner command is read, see
  above, and its category deny applies when it outranks `opaque_exec`, as `github_post` does;
  the rest is tracked in #126 for the `deny:` list and #191 for `categories:`); a heredoc after a
  compound command's closing token, `{ bash; } <<EOF` (#161; fix in review in #198).
- **Project code.** A trusted program that runs project code (pytest runs conftest.py, git runs
  hooks, `npm install` runs package scripts) or reads a startup file (`~/.sqliterc`, `~/.psqlrc`,
  `~/.ssh/config` with a `ProxyCommand`) can run code the agent wrote earlier with a local write.
  The same goes for options that read more options from a file (`curl --config`, `gcc -wrapper`,
  `-specs`) and for `git grep -O`, whose pager takes the matching files as arguments. The gate
  does not claim to stop that; it stops the one-step forms. A sandbox is the real fix.

The working folder (#192). A line that enters a folder that can hold a secret store, and reads a
file there by a relative name, is `credentials`: `cd ~/.config/gh && cat hosts.yml`,
`cd ~/.kube/cache && cat ../config`, `time cd ~/.kube && cat config`,
`cd ~/.kube && tar czf - .` and `git -C ~/.kube diff --no-index config /dev/null` ask like
`cat ~/.kube/config`. The gate does not follow the folder from command to command; it reads the
whole line in one pass:

- Store folders and store file names come from the sample store paths the path matchers use, per
  call so that `HOME` and `EXO_HOME` apply. A dot folder along a sample below home is a store
  folder by its name (`.kube`, `.docker`, `.config`, `.ssh`, `.aws`, `.exo`, `.vercel`, also
  `Netlify`). A plain folder is one only below its parent in the sample (`.config/gh`,
  `.config/gcloud`, `.exo/keys`, `.hermes/p`, `/proc`, `EXO_HOME` and its `keys` when `EXO_HOME`
  is outside home), so a checkout called `exo` or `gh` is not. The store file names are the
  samples' file names (`config`, `config.json`, `hosts.yml`, `environ`, `token`, ...).
- The targets are those of `cd`, `pushd`, `git -C`, `env -C`/`--chdir`, `sudo -D`/`--chdir` and
  `tar -C`/`--directory`, wherever they stand (after `&&`, `||`, `;`, `|` or `&`, behind `time`,
  `command`, `builtin` or `!`, in a loop, `( )`, `{ }`, `$( )`, `<( )` or the text a shell
  runs), however the command is spelt (`c\d`, `"cd"`, `$'\x63d'`) and with the option attached,
  in a cluster, old-style or cut to a unique prefix (`env -C~/.kube`, `sudo -iD ~/.kube`,
  `tar czfC - ~/.kube`, `env --c=~/.kube`, `tar --dir ~/.kube`). A brace set is the words it
  spells (`~/.{ssh,x}`, a range `{1..3}`, `{007..010}` or `{a..c}`, padded as bash pads), up to
  16 in a word of up to 4096 characters, and one glob past that or for a stepped range
  (`{1..9..2}`).
- A line enters a store folder when a target has one among its parts (`~user/` and folders
  outside home count by their parts: `/proc/self`, `/srv/app/.kube`), or goes by a relative name
  into a plain one below another target: `cd ~/.config && cd ./gcloud`, `cd / && cd proc/self`,
  `cd ~/.config/nvim && cd ../gcloud`, `cd ~/.config/nvim/lua && cd .. && cd .. && cd gcloud`.
  The other target names a dot parent by its name (any `.config`) and a plain one by its path
  (`/`, `EXO_HOME`); a folder entered this way is a parent in turn. `..` is resolved as bash's
  `cd` does and stops at `/`. `~+`, `~0`, `$PWD`, `${PWD}`, `${PWD:-x}` and `$(pwd)` are the
  current folder, and `${PWD%/*}` is its parent.
- On such a line a relative name counts when its file name is a store file name, when it goes
  into a store folder (`gh/hosts.yml` after `cd ~/.config`, `../.docker/config.json`,
  `lua/../../gcloud/x` after `cd ~/.config/nvim`), and when it is a glob or brace set that could
  match one. The folder itself (`.`, `./`, `$PWD`) counts when a target is a store folder
  (`cd ~/.kube && tar czf - .`), and any relative name counts when a target is itself a store
  (`~/.ssh`, `~/.s?h`). A name that goes into a plain store folder from its parent counts on any
  line when it names a store (`cd /srv && tar cf - exo-home/keys` with
  `EXO_HOME=/srv/exo-home`).
- Globs are read as bash reads them: `[^x]` is `[!x]`, a `]` first in a set is a member, a class
  is spelt out (`e[[:alpha:]]viron`), a `[` that opens no set is a literal, and only a glob that
  spells a leading dot matches one (`*` is not `.ssh`). A glob with an unknown class or many `[`
  could match anything. A glob part with no character of its own (`cd */`, `cd node_modules/*/`)
  is not a store folder by its name. A quoted glob counts too, for the tools that match it
  themselves (`find -name 'conf*'`), so a quoted regex, `tr` set or jq slice names a store only
  when its pattern could match a store name.
- Names are read from the words and their pieces (`--kubeconfig=config`, `-d@config`,
  `f:=@config`), redirection targets and the bodies of `$( )`, `<( )` and the text a shell runs
  (`bash -c`, a heredoc fed to `bash`). They are not read from the program, the words before it
  (`sudo -u root`) except assignments (`KUBECONFIG=config kubectl` counts), the targets' own words
  (`mkdir -p .kube && cd .kube && ls` keeps its class), a `git` subcommand, a jq filter (`jq .`;
  not one read from a file with `-f`), other heredoc text or a word with a space in it (a commit
  message, an `echo`, `awk '{print $1, $2}'`) unless it holds an unquoted brace set.

A line that enters no store folder, and names none, is classified as before. The work is linear
in the line: set lookups per word, at most two small `fnmatch` matches per glob, at most 64
store tests per store folder (one per glob candidate), and a parent path kept only as deep as
the deepest sample store folder. The trade-off, which fails closed: neither the order nor the
success of the commands is followed, so on a line that enters a store folder a word named like a
store file asks even when it is elsewhere or is not a file
(`cd ~/.kube && cd ~/project && cat config`, `cat config; cd ~/.kube`,
`cd ~/.config/fish && cat config`, `cd ~/.config/nvim && ls *`,
`cd ~/.config/nvim && npm config get prefix`), and `.` asks in a store folder even for a listing
(`cd ~/.config && find . -name '*.toml'`). A line that is already `store_write` stays
`store_write` (the principal's alone). Known limits: a target in a variable or made by a command
(`X=~/.kube; cd $X`, `cd $(...)`); a name made by a command or with a space in it (`cat $(ls)`,
`"$(pwd -P)/config"`); a dot store folder named last in an operand
(`cd ~/.config && tar czf x ../.kube`, as for `tar czf x ~/.kube`); a relative target taken from
another relative one (`cd ~/.config && cd nvim && cd ../gcloud`); a glob in the parent of a
plain store folder (`cd /sr?/exo-home && cd keys`); a relative path that reaches a plain store
folder from more than one level above its parent (`cd / && cd srv/exo-home/keys`); other
`${PWD...}` forms; the text `eval` runs (it asks as `opaque_exec`); `cd -` and `OLDPWD`,
`CDPATH`, `HOME` set on the line and `shopt -s dotglob`; and a store reached through a folder
link whose name does not say so.

## The store is credentials (D-038)

The gate's own inputs are on the protected list (`is_store_path`): `policies/`, `state/`
(including `approval-grants.json`), `ledger.sqlite*` and `keys/` of the principal store and of
`$EXO_HOME`, `$EXO_HOME/run` (the core and agent sockets) and `$EXO_HOME/seats`, any
`seat.toml`, and `$EXO_PRINCIPAL_STORE`. Distinctive names match anywhere, so a relative
`policies/gates.yaml` or `state/approval-grants.json` counts too.

Absolute paths are checked in both their lexical form and their resolved symlink form, including
missing final components, and so is the target of `cd`, `pushd` and `git -C`. This catches file and
directory aliases into protected stores, aliases used to delete a store or its ancestors, a `cd` into
an alias of the store, and `//` or `/proc/self/root` spellings. Resolution preserves `symlink/..`
filesystem order; lexical protection still applies when a protected path links outside the store. A
path that cannot be resolved (a NUL byte) keeps its lexical check, so the floor never depends on
resolution working. `cp -s` and `cp -l` count as writes to whatever they alias, and a relative `ln -s`
target is read against the link's folder in the two-operand form (`ln -s TARGET LINK`). Relative paths only use the distinctive-name checks because
the tool's working directory is unknown here.

Brace sets are expanded first, as bash does and as for secret-store paths (issue #224). A shell word is
expanded from its source text, so only unquoted braces expand. A file tool's path is expanded as written. The
word and every word it expands to are checked, and a hit on any counts, so
`echo x > ~/.exo/polic{i..i}es/default.yaml`, `cp x ~/.exo/{state,zz}/approval-grants.json` and
`cd ~/.e{x,}o` reach the store. A word is split at `=`, `:` and `,` only after its braces expand, so
`dd of=~/.exo/polic{i,x}es/default.yaml` counts too. A word past the brace limits counts as the store. The store
checks have their own budget of 4,096 expanded words per tool call, so they never use up the secret-store
checks' budget, and each spelling is expanded once per line, however many commands use it.

Known limits. These are classification checks, not a filesystem sandbox: a symlink can change
between classification and execution (TOCTOU). A hardlink made earlier (`cp -al`) shares the file
without a link to resolve, so only distinctive names catch a write through it. Only the store roots
are resolved: when a protected entry itself links out of the store (`policies -> ~/dotfiles/x`), a
write to the outside spelling is a local write. Name and prefix checks are case-sensitive, so a
case-insensitive filesystem (APFS) accepts `STATE/` for `state/`. A glob spelling
(`sta*/gate-grants.json`) is expanded by the shell, not seen here. A link planted
and used through relative paths in one command (`cd ~ && ln -s .exo e && cd e && ...`) is not
followed, and the bash tool's `cwd` argument is not read.

Any write is `store_write`: `write_file`, `edit_file`, `patch`, `apply_patch`, a redirection, a
non-read program with a store path argument (`rm`, `mv`, `cp`, `tee`, `sed -i`, `chmod`,
`sqlite3`, `touch`, `ln`), a connection to the socket (`nc -U`, `curl --unix-socket`, `socat`),
`exo log append`, `exo keygen`, `exo init`, `git -C <store>` with a writing subcommand, and any
writing command after `cd` into the store. `rm -rf ~/.exo` counts (an ancestor). A
`store_write` always asks, no policy lowers it, no standing grant covers it, and only the
principal may answer (`PRINCIPAL_ONLY`, whatever `approvals.yaml` says). It is enforced in
observe mode too. Reads of the store stay `read`, except `keys/`, which stays `credentials`.

## Policy changes during a session (D-038)

The plugin pins the policy it loaded first. When a policy file (or a scrub source) changes
later, it loads the new one, writes a `policy_change` ledger row with the files' hashes, and
decides each call under both. The stricter outcome wins. So a change can tighten at once and
can never loosen until the principal runs `/gate accept` (ledger row `policy_accept`), or the
core restarts. `/gate` says whether a change is waiting.

## Principal authority for policy and settings changes

`/gate accept` requires the live principal caller stamped by the transport. The check lives in the
policy adoption method, so command aliases, whitespace and direct plugin calls cannot bypass it.
An unstamped call is refused before reading changed policy files or recording acceptance. The ledger
records the authenticated caller, including its client label. `/gate status` stays available locally.

`config.set` and `models.set` also require principal authority. The web default-model route returns
403 when the core refuses authority. It does not rewrite config through its offline fallback.
TCP and stdio callers remain local even when they possess a transport bearer token.

Two other body fields cannot claim the principal. `peers send` with `via="human"` from a non-principal caller
goes out as `via="agent"`, so the receiving daemon gives it `same-principal` trust, not `principal`. `unsteer`
from a non-principal caller can pull back only steers that the principal did not write.
