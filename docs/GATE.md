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
  credential is a random token in a launch record the core writes beside its socket when it starts
  (`$EXO_HOME/run/exod.principal.key` or `run/agent-<seat>.principal.key`, mode 0600) and deletes
  when it stops. A credential sent from a process that is a child, grandchild and so on of the
  core (a bash tool call or a background job) does not count, and neither does one from another
  user or from a process the kernel cannot name (see "Unix peer authentication" below).
- **local**: everything else on the socket (a tool subprocess, a channel, another daemon), and
  every stdio or TCP connection.

A principal connection answers as the principal; `client` is only a label (`principal:tui`,
`principal:web`). A local connection can answer only as a seat, with the signature above. An
empty `client`, `principal`, `exo-tui` or any other name from a local connection is refused as
`forbidden`, so a tool that speaks the protocol cannot answer its own approval. The answerer shows
in `approval.resolved` as `by`.

### Unix peer authentication (D-060)

Both ends of a principal connection on the Unix socket ask the kernel who is on the other end of that
one connection before the credential moves. Code: `src/exo/principal.py` and `src/exo/peerauth.py`.

| Platform | Peer user | Peer process | Process start and parent |
|---|---|---|---|
| Linux | `SO_PEERCRED` | `SO_PEERCRED` | `/proc/<pid>/stat` (start ticks, with the boot id) |
| macOS | `LOCAL_PEERCRED` (the uid `getpeereid` reports) | `LOCAL_PEERPID` | `proc_pidinfo(PROC_PIDTBSDINFO)` |
| anything else | none | none | none: principal mode is refused |

The launch record is one JSON object: `v`, `token`, `pid`, `uid`, `start` (the core's process start
identity) and `instance` (the launch id `hello` also reports). It is written atomically, before the
socket accepts, so a client never reads a missing record or a crashed core's. A core that cannot read its
own process entry warns in its log and writes an empty `start`, which clients refuse with that reason.

**The client, before sending.** A principal client (`exo`, a joined window, `exo chat`, the web)
connects, then checks that connection: the kernel's peer uid is this user, its pid is the record's
pid, and that process's start identity equals the record's. The record is read through one
descriptor with `O_NOFOLLOW`; it must be a regular file with one link, owned by this user, with no
group or other access, at most 4 KiB, and a v1 record. Only then does the token from that record go
on that same connection, and only on `POST /command`. Any failure is a connection error
(`PrincipalRefused`, a `ConnectError`) that names the reason, with no byte written: a replaced socket, a
stale record (a dead core, or a reused pid), a symlink, a shared file, a record from a core that predates
D-060, a core in another PID namespace (the kernel names pid 0), or a platform with no peer call. The web
page shows that reason with a restart hint (`exo stop`, then reopen the page) instead of "not reachable".
There is no fallback to a credential-less or unchecked principal mode.

**The core, before accepting.** The core takes the kernel's peer pid and uid, and that pid's process
start identity, when it accepts the connection, before reading a byte (one request per connection, so
this is per request too). The caller is the principal only when the token matches, the uid is this
user, that pid still names the same process (the start identity taken at accept, and a start before
the accept, so a pid reused after the accept fails), and its whole parent chain reads cleanly up to pid 1 without
passing the core. A chain that cannot be read is treated as the core's own tool tree. The tree check
runs when the request is read. The dead-peer check, not the parent walk, closes one detach route on
Linux: a tool child that connects, then exits and leaves the connection to an orphan, is local,
because the kernel's peer pid then names a dead process. On macOS `LOCAL_PEERPID` reports the last
process to use the socket, so an orphan that writes before the core's accept is the process named; the
claim holds reliably on Linux only. When a request with the right token is taken as local, the core
says why once per process in its log (`exod: the principal credential from pid N is taken as local:
...`), and that connection's refusals (approvals, principal-only commands) name the reason.

**Limits.** Same-user code running outside the core's tree is trusted: it can read the record, as it
could read the old token file. A tool child whose parent exits before it sends its request is not
caught by this check (issue #31); an OS sandbox for tools is still the fix (D-038, D-039). Under
`hidepid` or systemd `ProtectProc` (or `ProcSubset=pid`), root-owned ancestors such as `sshd`, `login`,
`su` or `sudo` cannot be read, so every principal client started below them is local; the refusal and
the core's log say so. This fails closed on purpose: stopping the walk at the first unreadable
ancestor would let `tool -> sudo -u $USER exo` pass. A client in another PID namespace than the core
is refused too. An old client against a new core sends the whole record as its token and is local
without a message; restart `exo`, `exo web` and `exo chat` after an update. The browser-to-web hop (the
`exo web` page to its own server, over TCP) is not peer-checked; the page token protects it and, once
#177 lands, the one-time launch code (D-039, #166).

**Snapshot and liveness contract.** `hello` returns `instance` (this launch) and `heartbeat` (seconds
between `: ping` comments on `GET /events`; the capability list has `heartbeat`). The event stream
opens with `: exod instance=<id> heartbeat=<s>`. `approvals.list` returns
`{approvals, cursor: {instance, seq}}`, copied in one await-free step: replace pending cards with it,
then apply events above `seq`; a different `instance` means the core restarted and its pending asks
are gone. The Python client ends a stream that brings no complete frame (an event or a heartbeat) for
`idle` seconds (45 by default); partial bytes do not count. A frame over 16 Mi characters is skipped: the client
resumes after that frame's seq, since resuming at it would replay it forever, with or without `reconnect`, so a
caller that keeps its own `since` (the exo-bus channel) gets the next event. The core writes frames
as raw UTF-8 (escaping U+0085, U+2028, U+2029 and lone surrogates, which line readers split on or
cannot encode), so a frame stays near the size of the request that made it; text made of those
escaped characters can still grow up to three times. A joined terminal window resends a command
after a lost connection only when nothing was sent (the connect failed) or the command is a read; a
lost reply to a turn or an approval answer is never repeated. The window says it may not have been
delivered, puts the typed text back in an empty composer, and re-reads `approvals.list` to put back a
card the core still holds.

A seat may approve one exact text with `allow`. Only a principal connection can answer
`allow_always`; anything else is refused.

Limits. The credential file is guarded by the gate: its name ends in `.key`, so reading it is a
`credentials` act that always asks. No environment variable carries it, and `tool_env` drops
`EXOD_*`, `EXO_PRINCIPAL*` and `EXO_*SOCK*`. A tool process is the same OS user, so code that
both reads the file without the gate seeing it and detaches from the core's process tree could
still pass. The full fix is an OS sandbox for tools; until then the gate and the process-tree
check are the guard.

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

A command is `allow` only when every program in it is known and nothing is hidden:

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
  `uv run`) it classifies the inner text too. The result is the most severe part, and it keeps
  the `indirection` signal, so `decide` asks even when the inner part alone would only log.
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
- The principal can lower `opaque_exec` only by naming it: `categories: {opaque_exec: act}` or a
  rule whose match has `category: opaque_exec` (for example with a `command` regex for `make`).

Known limit. A trusted program that runs project code (pytest runs conftest.py, git runs hooks,
`npm install` runs package scripts) can run code the agent wrote earlier with a local write.
The gate does not claim to stop that; it stops the one-step forms. A sandbox is the real fix.

## The store is credentials (D-038)

The gate's own inputs are on the protected list (`is_store_path`): `policies/`, `state/`
(including `approval-grants.json`), `ledger.sqlite*` and `keys/` of the principal store and of
`$EXO_HOME`, `$EXO_HOME/run` (the core and agent sockets) and `$EXO_HOME/seats`, any
`seat.toml`, and `$EXO_PRINCIPAL_STORE`. Distinctive names match anywhere, so a relative
`policies/gates.yaml` or `state/approval-grants.json` counts too.

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
