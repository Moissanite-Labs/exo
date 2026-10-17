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

## Environment of bash and jobs

`bash`, background jobs and `code_find` run with a filtered copy of the environment, all through one function,
`exo.envscrub.scrub_env` (D-039). Removed:

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

A seat can keep named variables with `[tools] env_allow = ["GH_TOKEN"]`; `env_deny` wins over `env_allow`. This is
a heuristic: an unrecognized secret under an innocent name still passes, so keep keys in the key store. `EXO_HOME` stays: tools
run `exo` commands, and the default path is no secret.

Model providers read the real environment. The claude-cli and muse-cli providers do not pass it on: the child gets an
explicit minimal set (`exo.envscrub.minimal_env`: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_*`,
`TERM`, `TMPDIR`, `TZ`, `CLAUDE_CONFIG_DIR`, `XDG_*_HOME`, proxy variables and CA bundle paths) plus its fixed
switches, and never an API key (muse-cli also drops `META_API_KEY`). MCP servers get the environment their own `env` table asks for (`${VAR}`
expansion), not this filter.

## Pipeline exit codes

bash reports the exit code of a pipeline's last stage, so `false | head -1` looks like success. Exo does not turn on
`pipefail` (`grep | head` would then fail on SIGPIPE). An EXIT trap records the final pipeline's `PIPESTATUS`; when an
earlier stage failed with a code other than 141 (SIGPIPE) and the last stage succeeded, the result gets one line, e.g.
`note: pipeline stage 1 exited 1 (only the last stage's code is reported as the result)`. Background jobs put the same
line in the job's end notice. Only the command's last pipeline is checked, and a command that sets its own EXIT trap
turns the note off. The trace rule `shell.masked_exit` (docs/TRACE.md) flags it.

## Streamed output

While a foreground `bash` command runs, its output also goes out in pieces as `tool.output` events: clients show
them live and sessions.sqlite keeps them. The output is read as one UTF-8 stream, so a character split between two
reads stays whole in the stream and in the result. Only a sequence cut off by the end of the output becomes U+FFFD.
Known key values are cut out of the stream as `[redacted]`, even when a key is split between two reads. The end of
a read that could be the start of a key waits for the next read or for the end of the command. Other output is not
held back. The result is redacted as a whole when the call returns.

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

- **Writes** (`write_file`, `edit_file`, `apply_patch`) go through a fresh temp file beside the target
  (`.<name>.exo-tmp`, created exclusively) and a rename, so a link already sitting at the temp name is never
  followed. A write to a symlinked file is refused, and the error names the link's target: write that path instead,
  and the link stays. Before, the rename replaced the link with a regular file and left the target unchanged.
  `apply_patch` checks this before it writes anything, and its Delete File removes only the link, as `rm` does.
  A hard-linked file becomes its own copy, as with any atomic rename. A symlinked parent folder is still followed.
- **Reads** (`read_file`, `edit_file`, `grep`) open regular files only. A FIFO, socket or device, or a link to one,
  is refused before it is opened (`grep` skips it), so a FIFO cannot block the core and `/dev/zero` cannot fill
  its memory. `read_file` and `edit_file` refuse files over 50 MB (`MAX_READ_BYTES`). Use `grep`, or `sed -n` in
  `bash`, for those. `grep` skips files over 2 MB.

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
  The first line of the result names the backend: `[code_find backend=jev duration_ms=15010]`. A fallback adds
  `fallback_from=...`; a search jg marks incomplete adds `incomplete`.
- **Key handling:** `jg` ignores key environment variables and reads only a saved config. Each call makes a
  0700 temp dir as `XDG_CONFIG_HOME`, pipes the key to `jg auth --stdin` (never argv or the environment),
  searches, and deletes the dir. Secret-looking variables are dropped from the child environment, and the key
  is scrubbed from any error text. Nothing is logged.
- **Limits:** 120 s default timeout (max 600; the process group is killed), output paged at 20,000 characters
  (`page=2` serves the rest from memory), jg itself capped at 256 KB. Read-only, so risk low, but it sends
  eligible source files to the backend; jg skips ignored and known sensitive files. The question goes to jg after
  `--`, so it can never act as a jg flag such as `--include-sensitive`.
- **Trace:** a normal tool call in `exo trace` (`tool.completed` with `duration_ms`); backend and duration are the
  first line of the result.
- Skill: `skills/jevgrep` (adapted from dzhng/jevgrep's skill, MIT).
