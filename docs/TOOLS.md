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
- anything the seat lists in `[tools] env_deny` (exact names or patterns like `COMPANY_*`).

A seat can keep named variables with `[tools] env_allow = ["GH_TOKEN"]`; `env_deny` wins over `env_allow`. This is
a heuristic: a secret under an innocent name still passes, so keep keys in the key store. `EXO_HOME` stays: tools
run `exo` commands, and the default path is no secret.

Model providers read the real environment. The claude-cli and muse-cli providers do not pass it on: the child gets an
explicit minimal set (`exo.envscrub.minimal_env`: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_*`,
`TERM`, `TMPDIR`, `TZ`, `CLAUDE_CONFIG_DIR`, `XDG_*_HOME`, proxy variables and CA bundle paths) plus its fixed
switches, and never an API key (muse-cli also drops `META_API_KEY`, and its serve route registers Exo's tools as an inert MCP server
that never runs one). MCP servers get the environment their own `env` table asks for (`${VAR}`
expansion), not this filter.

## Pipeline exit codes

bash reports the exit code of a pipeline's last stage, so `false | head -1` looks like success. Exo does not turn on
`pipefail` (`grep | head` would then fail on SIGPIPE). An EXIT trap records the final pipeline's `PIPESTATUS`; when an
earlier stage failed with a code other than 141 (SIGPIPE) and the last stage succeeded, the result gets one line, e.g.
`note: pipeline stage 1 exited 1 (only the last stage's code is reported as the result)`. Background jobs put the same
line in the job's end notice. Only the command's last pipeline is checked, and a command that sets its own EXIT trap
turns the note off. The trace rule `shell.masked_exit` (docs/TRACE.md) flags it.

## Result cap (D-041)

Every tool result is cut at 100,000 characters (`MAX_RESULT_CHARS`) with a `[truncated N chars]` note. This now holds
for results a handler returns as a `ToolResult` (the MCP client does) as well as plain strings: the cap is shared
across the result's text parts.

## code_find (D-029)

`code_find(question, path=".", page=1, timeout=120)` runs [jevgrep](https://github.com/dzhng/jevgrep) (`jg`, MIT)
as a subprocess. Jevgrep uses the TypeSafe Jev decision model to pick relevant files and returns a summary, a
ranked file list and verbatim excerpts. Use it for unfamiliar code and "where or how" questions; use `grep` or
`read_file` when the symbol or path is known.

- **Runner:** an installed `jg`, else `npx -y @dzhng/jevgrep@0.8.0`. Needs Node.js 22+. Checked once at
  startup. If Node 22+ or both runners are missing the tool is hidden, and `exo models` says why on its
  `code_find:` line.
- **Backend:** TypeSafe Jev with the key Exo's jev provider uses (`TYPESAFE_API_KEY`: environment, macOS Keychain
  service `exo`, or `~/.config/exo/keys.env`). If Jev fails or no key exists, local Laya through jg's custom
  endpoint (the same `/v1/systemone` API), when `providers.laya` is configured, on loopback, and listening.
  The first line of the result names the backend: `[code_find backend=jev duration_ms=15010]`. A fallback adds
  `fallback_from=...`; a search jg marks incomplete adds `incomplete`.
- **Key handling:** `jg` ignores key environment variables and reads only a saved config. Each call makes a
  0700 temp dir as `XDG_CONFIG_HOME`, pipes the key to `jg auth --stdin` (never argv or the environment),
  searches, and deletes the dir. Secret-looking variables are dropped from the child environment, and the key
  is scrubbed from any error text. Nothing is logged.
- **Limits:** 120 s default timeout (max 600; the process group is killed), output paged at 20,000 characters
  (`page=2` serves the rest from memory), jg itself capped at 256 KB. Read-only, so risk low, but it sends
  eligible source files to the backend; jg skips ignored and known sensitive files.
- **Trace:** a normal tool call in `exo trace` (`tool.completed` with `duration_ms`); backend and duration are the
  first line of the result.
- Skill: `skills/jevgrep` (adapted from dzhng/jevgrep's skill, MIT).
