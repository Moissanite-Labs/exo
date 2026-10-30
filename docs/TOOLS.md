# Built-in tools

Tools register in `exo.core.tools.builtin`. Each has a risk (low, medium, high) that exo-gate reads and a
toolset. Subagents get every low-risk tool the parent has (scout and critic) or all but the blocked ones
(builder, verifier); see `exo.core.agents.delegate`.

| Tool | Toolset | Risk | Job |
|---|---|---|---|
| `read_file`, `write_file`, `edit_file`, `apply_patch` | fs | low, medium | Files |
| `glob`, `grep` | search | low | Find by name or regex |
| `code_find` | search | low | Ask what code does; get files and verbatim excerpts |
| `bash` | shell | high | Shell with timeout and output cap; `background: true` starts a job (docs/JOBS.md) |
| `jobs`, `job_output`, `job_kill` | shell | low, low, medium | List, read and stop background jobs (D-032) |

`glob` and `grep` skip `.git`, `node_modules`, virtualenvs and caches, and every secret store below the folder
they search (`~/.ssh`, `~/.aws`, `.env`, `$EXO_HOME/keys`, `*.key`...; the list is `exo.gate.is_sensitive_path`),
also through a link. When they skip a store they end with `[skipped N secret-store paths; read one by name to be
asked]`. A store named as `path` is read, after the gate asks (#241, `docs/GATE.md` "What the search tools
read"). In a repo's own `.claude` folder only `worktrees`, `commands`, `agents` and `skills` are searched. A
search of the whole file system (`/`, or a path that resolves to it) is refused; name a folder below it.

Each registered `grep` or `glob` call runs its walk and pattern matching in an owned Python process, with a
30-second wall-clock limit (D-057 stage 0.5). A pattern that backtracks cannot stop the core's event loop.
At the limit the result says `Search stopped after 30 s; narrow the pattern`. Stopping the turn kills and
reaps the worker too. File limits, skipped-store notes, result ordering and the registry's output cap stay the same.
The worker gets only the paths needed to recognize configured secret stores, without provider keys or Python
startup hooks. The gate and selected executor run first: an executor may perform the search itself, and only
its explicit trusted-local callback starts this worker. A missing required executor never starts it.

The process is a time bound, not a filesystem sandbox or a memory limit. Large trees still use memory for
the matching file list. Process startup adds overhead to small searches. `src/exo/search_worker.py` owns the
process, while `core/tools/builtin/search.py` keeps the synchronous search implementation and tool schemas.

## Edit matching

`edit_file` finds `old_string`, and `apply_patch` finds each hunk's context and `-` lines, with the fuzzy matcher
lifted from Hermes (`exo.core.tools.builtin.fuzzy`). Its strategies run in order and the first that finds the text
wins: exact, trimmed lines, collapsed whitespace, unescaped `\n` and `\t`, Unicode punctuation read as ASCII, then
two similarity strategies. `block_anchor` needs the first and last lines to match and scores the lines between them.
`context_aware` takes a block whose every non-blank line is at least 80% similar. Without `replace_all` the match
must be unique, and `replace_all` refuses several similarity matches. `edit_file` names a non-exact strategy in its
result.

The similarity strategies need two or more non-blank lines. A one-line `old_string` (blank lines around it do not
count) must match up to whitespace, escaping or Unicode. When the file's line has other text, for example
`TIMEOUT = 45` where `old_string` says `TIMEOUT = 30`, the call fails and shows the closest lines. Before, such a
stale edit overwrote the newer line. In `apply_patch` a hunk's context lines count toward the two, but its `-` lines
must match the file up to the same whitespace and Unicode tolerance (a literal `\t` too). Otherwise the hunk is
refused, naming the hunk's line and the file's line, and no file changes (#236). Similarity places only the context
lines, and a similarity match takes the hunk's text as sent.

In `edit_file` the rule counts lines, not exact lines. So one more line, even a lone `}` or the `def` line, lets a
stale line match again. On a file that holds `def f():`, `    TIMEOUT = 45` and `}`, the `old_string`
`    TIMEOUT = 30` plus a line `}` matches through `context_aware`, and so does `def f():` plus `    TIMEOUT = 30`.
Each one replaces the line that now says 45, and the result ends in `(matched via context_aware)`. More exact lines
do not stop this. With all three lines, `block_anchor` matches. Exact lines only make sure the match lands where you
meant. So copy `old_string` from a fresh read of the file, with every line exact and enough of them to be unique.
When the result names `context_aware` or `block_anchor`, the file did not hold `old_string` as written. Read the
lines it replaced before you go on.

## Environment of bash and jobs

`bash`, background jobs and `code_find` run with a filtered copy of the environment, all through one function,
`exo.envscrub.scrub_env` (D-039). Removed:

- shell startup hooks. bash itself acts on `BASH_ENV`, exported functions (`BASH_FUNC_<name>%%`, or
  `BASH_FUNC_<name>()` in Red Hat-patched bash, which shadow builtins and commands), `SHELLOPTS` and `BASHOPTS`
  when it starts. `ENV` is read by interactive POSIX shells a command starts (`sh -i`). `PS4` matters once xtrace
  is on: bash expands it before every traced line, so a `$(...)` in it runs a command each time. These match by
  exact name only (`ENV=dev` is dropped; `env`, `DEPLOY_ENV` and a `?env=` URL query are not secrets and stay);
- every provider key named by `key_env` in the model catalog;
- names ending `_API_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD`, `_CREDENTIALS`, `_KEY`, `_KEYS`, `_KEYS_DIR`,
  `_KEYS_FILE` or `_DSN`, and names containing `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `APIKEY`,
  `API_KEY`, `PRIVATE_KEY`, `ACCESS_KEY` or `_KEY_`;
- `AWS_*`, `GH_*`, `GITHUB_TOKEN`, names ending `DATABASE_URL`;
- the bus identity (`EXO_BUS_*`) and the core's socket and credential (`EXOD_*`, `EXO_PRINCIPAL*`, `EXO_*SOCK*`);
- known credential/configuration names such as `MYSQL_PWD`, `DB_PASS`, `STRIPE_SK`, `TWILIO_AUTH`,
  `NPM_CONFIG__AUTH`, `DOCKER_AUTH_CONFIG`, `KUBECONFIG`, and PostgreSQL credential/key file fields;
- recognized token/private-key value formats, and URL/URI/proxy fields with userinfo or credential query/fragment fields;
- git's environment config (values can carry credentials, e.g. `http.extraHeader`): `GIT_CONFIG_PARAMETERS`, and
  `GIT_CONFIG_COUNT` with its `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` as one unit, kept whole only by
  `env_allow = ["GIT_CONFIG_COUNT"]` and dropped whole if any member is denied or has a value in a recognized
  token format; a bearer header is not one, so allow the count only for values tools may see (#159);
- anything the seat lists in `[tools] env_deny` (exact names or patterns like `COMPANY_*`).

A seat can keep named variables with `[tools] env_allow = ["GH_TOKEN"]`; `env_deny` wins over `env_allow`.
Allowing `BASH_ENV`, `ENV` or an exported function (`env_allow = ["BASH_FUNC_module%%"]` for Lmod, for example)
also allows shell startup code; only allow a startup file or function you trust. The bash tool and background
jobs start bash as `bash --norc -c`: with `SSH_CLIENT` or `SSH2_CLIENT` set and `SHLVL` unset or 0, a plain
`bash -c` (in many builds) sources `~/.bashrc` and skips an allowed `BASH_ENV`. The `/bin/sh`
fallback, used only when no bash is on `PATH`, reads no startup file. This is
a heuristic: an unrecognized secret under an innocent name still passes, so keep keys in the key store. `EXO_HOME` stays: tools
run `exo` commands, and the default path is no secret.

Not scrubbed, on purpose: loader and interpreter hooks such as `LD_PRELOAD`, `PYTHONPATH` (`sitecustomize`),
`NODE_OPTIONS`, `PERL5OPT`, `RUBYOPT`, `GIT_SSH_COMMAND` and `GIT_EXTERNAL_DIFF`. They
can run code in the programs a command starts, but they are general process hooks with everyday development
uses (virtualenvs, debuggers, CA bundles, git over a jump host), and `PYTHONPATH` is deliberately on the
claude-cli allowlist. The scrub's startup-hook list is limited to the shell's own hooks above;
whoever controls the core's launch environment as the same OS user controls these too (D-039 Limits). A seat
that wants them gone lists them in `env_deny`.

Model providers read the real environment. The claude-cli and muse-cli providers do not pass it on: the child gets an
explicit minimal set (`exo.envscrub.minimal_env`: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_*`,
`TERM`, `TMPDIR`, `TZ`, `CLAUDE_CONFIG_DIR`, `XDG_*_HOME`, proxy variables and CA bundle paths) plus its fixed
switches, and never an API key (muse-cli also drops `META_API_KEY`). MCP servers get the environment their own `env` table asks for (`${VAR}`
expansion), not this filter.

## Shell results and pipeline exit codes

Foreground `bash` returns its captured output and `[exit N]` as before, with `ToolResult.is_error` true when the
command exits nonzero, times out or is interrupted. The model, tool completion event and turn error count share
that status. A failed write-shaped command does not count as successful file progress for repeat detection.
Output remains available on failure, including capped head and tail output. A successful background job launch
still returns its job receipt; job completion is reported separately.

bash reports the exit code of a pipeline's last stage, so `false | head -1` looks like success. Exo does not turn on
`pipefail` (`grep | head` would then fail on SIGPIPE). An EXIT trap records the final pipeline's `PIPESTATUS`; when an
earlier stage failed with a code other than 141 (SIGPIPE) and the last stage succeeded, the result gets one line, e.g.
`note: pipeline stage 1 exited 1 (only the last stage's code is reported as the result)`. Background jobs put the same
line in the job's end notice. Only the command's last pipeline is checked, and a command that sets its own EXIT trap
turns the note off. The trace rule `shell.masked_exit` (docs/TRACE.md) flags it.

## Streamed output

While a foreground `bash` command runs, its output also goes out in pieces as `tool.output` events: clients show
them live and sessions.sqlite keeps them. The output is read as one UTF-8 stream, so a character split between two
reads stays whole in the stream and in the result. Invalid bytes, and a sequence cut off by the end of the output,
become U+FFFD. Known key values are cut out of the stream as `[redacted]`, even when a key is split between two
reads. The end of the unsent output that could be the start of a key waits for the next read or for the end of the
command. Other output is not held back. Redaction finds the keys in the output itself, longest key first. Each key
is found in the output with the spans of the keys before it blanked out, so no match overlaps one already taken. The
markers go in only after that, so no key can match across a `[redacted]` marker. The stream is cut only where no
match that redaction could take crosses the cut, whatever output follows. So the stream equals redacting the whole
output with the key values known when the command started, for any set of them. That holds when one key holds
another (a URL with a password in it), when keys overlap in a chain and when a key holds `[` or `]`. The held text
stays under twice the length of all key values together. Each piece is redacted once more on its way to clients.
That pass keeps the markers already in the piece, so it does not change what the stream redaction did. It cuts out a
key value learned after the command started, but only inside one piece, so such a key split between two pieces still
goes out in two parts. The result is redacted as a whole, with every key value known by then, when the call returns.
But every cut made to a result comes before that: any cut a tool makes in its own output, and the 100,000-character
cap on every result ("Result cap"). For example, `bash` keeps 15,000 characters at each end of a longer output,
`job_output` keeps the last 20,000, `read_file` cuts a line at 2,000 characters, `grep` cuts a line at 300 and
`jobs` cuts a command at 100. So a known key value that such a cut splits leaves a part in the result. The stream
pieces are redacted before the cut, so they hold no such part.

## Execution brokers (D-020)

An installed broker can mediate every registered tool after pre-hooks and
validation, including foreground/background bash and delegated or scheduled
turns. Required mode refuses missing executors and unsupported MCP startup.
The immutable request and trusted host-code boundary are in `docs/EXECUTORS.md`.

## Result cap (D-041)

Every tool result is cut at 100,000 characters (`MAX_RESULT_CHARS`) with a `[truncated N chars]` note. This now holds
for results a handler returns as a `ToolResult` (the MCP client does) as well as plain strings: the cap is shared
across the result's text parts.

Image results have a separate 4 MiB aggregate cap on UTF-8 `data` and `url` field payload. Over-budget parts are
omitted whole with a visible marker, even when the text budget is exhausted. Base64 is never truncated. This
caps inline/URL payload, not remote asset download sizes; metadata and serialization overhead are separate.

## File safety

- **Writes** (`write_file`, `edit_file`, `apply_patch`) go through a fresh temp file beside the target (a random
  name from `mkstemp`, created exclusively, through `exo.core.private.atomic_write`, #273) and a rename, so a link
  planted in the folder is never followed. A write to a symlinked file is refused, and the error names the link's
  target: write that path instead, and the link stays. Before, the rename replaced the link with a regular file and
  left the target unchanged. `apply_patch` checks this before it writes anything, and its Delete File removes only
  the link, as `rm` does. A hard-linked file becomes its own copy, as with any atomic rename. A symlinked parent
  folder is still followed.
- **Reads** (`read_file`, `edit_file`, `apply_patch`, `grep`) open regular files only. A FIFO, socket or device, or
  a link to one, is refused before it is opened (`grep` skips it), so a FIFO cannot block the core and `/dev/zero`
  cannot fill its memory. `read_file`, `edit_file` and `apply_patch` refuse files over 50 MB (`MAX_READ_BYTES`),
  also a file that grows past that while it is read, so an edit never writes back a cut copy. Use `grep`, or
  `sed -n` in `bash`, for those, and `bash` to move or delete them. `grep` skips files over 2 MB.

## code_find (D-029)

`code_find(question, path=".", page=1, timeout=120)` runs [jevgrep](https://github.com/dzhng/jevgrep) (`jg`, MIT)
as a subprocess. Jevgrep uses the TypeSafe Jev decision model to pick relevant files and returns a summary, a
ranked file list and verbatim excerpts. Use it for unfamiliar code and "where or how" questions; use `grep` or
`read_file` when the symbol or path is known.

- **Runner:** an installed `jg`, else `npx -y @dzhng/jevgrep@0.8.0`. Needs Node.js 22+. Checked once at
  startup. If Node 22+ or both runners are missing the tool is hidden, and `exo models` says why on its
  `code_find:` line.
- **Backend:** TypeSafe Jev with the key Exo's jev provider uses (`TYPESAFE_API_KEY`: environment, macOS Keychain
  service `exo`, Linux Secret Service when `secret-tool` is available, or `~/.config/exo/keys.env` mode 0600
  with `EXO_KEYS_FILE` override). If Jev fails or no key exists, local Laya through jg's custom
  endpoint (the same `/v1/systemone` API), when `providers.laya` is configured, on loopback, and listening.
  A Laya base URL Exo cannot read, such as a port that is not a number, counts as not listening.
  The first line of the result names the backend: `[code_find backend=jev duration_ms=15010]`. A fallback adds
  `fallback_from=...`; a search jg marks incomplete adds `incomplete`.
- **Key handling:** `jg` ignores key environment variables and reads only a saved config. Each call makes a
  0700 temp dir as `XDG_CONFIG_HOME`, pipes the key to `jg auth --stdin` (never argv or the environment),
  searches, and deletes the dir. Secret-looking variables are dropped from the child environment, and the key
  is scrubbed from any error text. Nothing is logged.
- **Limits:** 120 s default timeout (max 600; the process group is killed), output paged at 20,000 characters
  (`page=2` serves the rest from memory), jg itself capped at 256 KB. Read-only, so risk low, but it sends
  eligible source files to the backend; jg skips ignored and known sensitive files. It refuses a folder that is
  or holds one of Exo's known secret stores (the home folder, `$EXO_HOME`, `~/.config`, `/`), since jg cannot
  leave those out (#241). The question goes to jg after `--`, so it can never act as a jg flag such as
  `--include-sensitive`.
- **Trace:** a normal tool call in `exo trace` (`tool.completed` with `duration_ms`); backend and duration are the
  first line of the result.
- Skill: `skills/jevgrep` (adapted from dzhng/jevgrep's skill, MIT).
