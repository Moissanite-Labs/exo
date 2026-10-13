# Seats (core loader format)

A seat is one named configuration of an Exo agent: who it serves, which models, which tools, which
skills, which MCP servers, which plugins and channels. Run one with `exo serve --seat <name>`
(sets `EXO_SEAT`; the engine factory reads it). `seat.list` shows the files and the active seat.

This is the one seat format. The role templates in `plugins/exo-orchestra/seats/*/seat.toml` are written in
it (see `plugins/exo-orchestra/SEATS.md`), and `/orchestra check <file>` validates a file against it.

Where: `$EXO_HOME/seats/<name>.toml`, `<name>.json`, or `<name>/seat.toml` with a `SOUL.md` beside it
(its text is added to the system prompt). The file is checked at the start of every turn (one stat, by
mtime and size) and re-read when it changed, so an edit applies to the next turn with no restart. A file that cannot be
read keeps the last good text. A few shorter spellings are accepted (top-level `name`, `role`,
`toolsets`, `principal = "ada"`, `[plugins]` as a list); the examples below use the canonical ones. Keys the loader
does not read are ignored.

```toml
[seat]                       # or top-level name / role / description; name defaults to the file name
name = "scout"
role = "researcher"
soul = "SOUL.md"

[principal]                  # or principal = "ada"
id = "ada"                   # exported as EXO_PRINCIPAL unless already set

[models]
tier = "fast"                # a router role (fast, standard, deep): becomes the seat's main model
roles = { fast = ["local:qwen"] }   # optional router overrides; also providers, local, fake

[tools]
toolsets = ["fs", "search", "skills", "mcp"]   # omit for all. "mcp" matches every mcp:<server>; "skills" is always kept
env_allow = ["GH_TOKEN"]     # secret-looking variables bash and jobs may keep (default none, see docs/TOOLS.md)
env_deny = ["COMPANY_*"]     # more names or patterns bash, jobs and code_find never see (D-039)

[skills]
folders = ["~/notes/skills"]  # searched first; "pack" is accepted as one more folder
load = ["choreography"]       # optional allow list: only these skills are indexed

[mcp.files]                   # see src/exo/core/mcp_client.py
command = "uvx"
args = ["some-server"]
env = { TOKEN = "${FILES_TOKEN}" }
risk = "medium"               # default for the server's tools; tool_risks = { delete = "high" }

[channels.bus]                # channels to run with the server: factory(link, **config)
seat = "scout"

[plugins]
enabled = ["exo-memory", "exo-bus"]   # omit for the config.json default

[session]                     # D-032
cwd = "~/Work/exo-live"       # where this seat's threads start; beats config.json's cwd. A folder that does not
                              # exist is ignored (and said so in the session-start notice)

[trace]                       # D-032: anomaly limits for this seat over config.json's [trace] (docs/TRACE.md)
turn_wall_s = 90              # also model_call_s, model_call_routes, turn_total_wall_s, model_wait_s, subagent_s, verify_turns, verify_days

[loop]                        # D-064: tool rounds one turn may run before it wraps up (default 64, 1 to 1000)
max_tool_rounds = 96          # EXO_MAX_TOOL_ROUNDS beats this; /rounds N beats both for one session

[permissions]                 # D-065: read once by the exo-gate plugin when the core starts
mode = "bypass"               # start in permissions bypass (docs/GATE.md "Bypass mode"); omit or "ask" to ask
```

A seat file is on the gate's store list, so the agent cannot write `[permissions]` without asking you, even in
bypass. A required tool executor (Exo OS) ignores `mode = "bypass"`.

When a thread starts, or a shell call works (its `cwd` argument or a leading `cd`) in a folder that is not inside
a git repo, the user gets one warning line, once per folder per thread. It is not shown to the model.

## Skills

Sources, first name wins: the seat's folders, `$EXO_HOME/skills`, the Exo pack (`skills/` in the checkout, or
`$EXO_SKILLS_PACK`), then skills plugins register with `register_skill`. Each `SKILL.md` needs `name` and
`description` frontmatter. The system prompt carries one line per skill; the `skill` tool loads one body.

## MCP

`pip install 'exo[mcp]'`. `command` is stdio, `url` is streamable HTTP (`headers` allowed). Tools appear as
`mcp__<server>__<tool>` in toolset `mcp:<server>`, risk `medium` unless set. A server that fails to start is
reported by `seat.list` and skipped. Names are at most 64 characters; a name that had to be shortened or cleaned ends
in an 8 character hash of the real server and tool names, so two tools never share one. A name already registered
is never replaced: the clash is refused, listed under `refused` in the server's status and recorded as a
`harness.error`. The model sees each remote description after a note that it is untrusted third-party text (cut at
1000 characters). Results get the same 100,000 character cap as any tool result.

## Config and secrets

`config.get` returns the effective config with secret-looking values masked. `config.set` writes
`$EXO_HOME/config.json` and refuses secrets: names such as `api_key` or `token` (names ending `_env` are
fine, they hold an environment variable name), literals under `env` or `headers` (use `"${VAR}"`), and
values shaped like API keys. Changes apply on the next start.

## Channels

A plugin calls `register_channel(name, factory)`. For each channel a seat (or `channels` in config.json)
enables, `exo serve` calls `factory(link, **config)` with a real protocol `Client` on the server socket, runs
`start()` after the server listens, and `stop()` on shutdown.
