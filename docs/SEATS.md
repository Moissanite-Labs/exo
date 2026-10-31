# Seats (core loader format)

A seat is one named configuration of an Exo agent: who it serves, which models, which tools, which
skills, which MCP servers, which plugins and channels. Run one with `exo serve --seat <name>`
(sets `EXO_SEAT`; the engine factory reads it). `seat.list` shows the files and the active seat.
A second seat beside a running `exo serve` needs its own `--socket`, for example
`exo serve --seat scout --socket $EXO_HOME/run/scout.sock`, and its clients pass the same `--socket`. `exo serve`
refuses a socket that another core answers on, or is starting on at the same moment (an `exod.sock.lock` file
beside the socket, held for the core's life), and exits 1. Only the core that holds `run/exod.sock` writes `run/exod.pid`, so `exo stop` stops that core and never a seat on its own socket.

This is the one seat format. The role templates in `plugins/exo-orchestra/seats/*/seat.toml` are written in
it (see `plugins/exo-orchestra/SEATS.md`), and `/orchestra check <file>` validates a file against it.

Where: `$EXO_HOME/seats/<name>.toml`, `<name>.json`, or `<name>/seat.toml` with a `SOUL.md` beside it
(its text is added to the system prompt). The file is checked at the start of every turn (one stat, by
mtime and size) and re-read when it changed, so an edit applies to the next turn with no restart. A file that cannot be
read keeps the last good text. A few shorter spellings are accepted (top-level `name`, `role`,
`toolsets`, `principal = "ada"`, `[plugins]` as a list); the examples below use the canonical ones. Keys the loader
does not read are ignored.

Three keys must be lists when they are set: `toolsets` (in `[tools]` or at the top level), `[tools] env_deny` and
`[skills] load`. For each, no list means more: every toolset, nothing more hidden, every skill. So any other value,
such as `toolsets = "fs"`, is refused, and the seat does not load (`exo serve --seat` exits with the reason). It
used to read as no list, and a seat meant to have one toolset got all of them (exo-desktop #52).

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
env_allow = ["GH_TOKEN"]     # variables the scrub would drop (secrets, shell startup hooks) that bash and jobs keep
                             # (default none; docs/TOOLS.md "Environment of bash and jobs" has the startup-code caveat)
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

[permissions]                 # D-065: read once by the exo-gate plugin when the core starts
mode = "bypass"               # start in permissions bypass (docs/GATE.md "Bypass mode"); omit or "ask" to ask
```

The `[models]` tables (`roles`, `providers`, `models` for prices, `fake`) merge over config.json's `models`
entry by entry. A role or a `provider:model` price entry the seat names replaces config.json's entry of that name
whole. Every other entry stays as config.json set it. So the example above sets only `fast` (which `tier` then
makes the seat's main model) and keeps config.json's `aux` and `decision` routes, and `roles = { decision = [] }`
empties that one role. A price field the seat's entry leaves out comes from the catalog, not from config.json.

A provider entry merges one level deeper, key by key over config.json's entry of that name, by the same rule as
a provider table over the catalog (`docs/PROVIDERS.md`). The keys the seat sets win and the rest stay. So a seat
that sets only `read_timeout = 300` under `[models.providers.openai]` keeps the `base_url` config.json gave that
provider, a LAN server for example, and sends no key there unless config.json named one. A seat entry that sets
`base_url` drops config.json's `key_env` and `headers`: no key or header token follows a new URL unless the seat names it.

A seat file is on the gate's store list, so the agent cannot write `[permissions]` without asking you, even in
bypass. A required tool executor (Exo OS) ignores `mode = "bypass"`.

When a thread starts, or a shell call works (its `cwd` argument or a leading `cd`) in a folder that is not inside
a git repo, the user gets one warning line, once per folder per thread. It is not shown to the model.

## Truthful seat attribution

On a core running a seat, `session.create` attributes an omitted or empty `seat` to the
loaded seat. An explicit matching name is accepted. A different name returns `bad_request`
without creating a session. Selecting a label does not load another profile or change the
core's identity, permissions or memory scope. Use the other seat's core to create its session.

An unseated core preserves the client's seat label for compatibility. That label alone is
not proof of profile execution or a memory access boundary. This check governs new sessions;
it does not isolate access to existing sessions.

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

`config.set` writes `$EXO_HOME/config.json` and refuses secrets: literals under names such as `api_key` or
`token` (names ending `_env` are fine, they hold an environment variable name), literals under `env` or `headers`
(use `"${VAR}"`), and values shaped like API keys, even beside a `${VAR}`. A secret-shaped name guards everything
beneath it, as `env` and `headers` do, so `{"token": {"a": "x"}}` is refused as `{"token": "x"}` is. Changes apply on
the next start.

`config.get` returns the effective config with each value `config.set` would refuse printed as `***`, so a file
written by hand, or before a check existed, shows no secret either. `exo config show` and `exo config get` print the
same values. All three judge a value by every part of its key path, so
`exo config get plugin_config.exo-bus.headers.X-Auth` is masked as `show` masks it. A container whose own name looks
like a secret, such as an MCP server named `github-token`, is masked whole and cannot be set with literals: rename it.

## Channels

A plugin calls `register_channel(name, factory)`. For each channel a seat (or `channels` in config.json)
enables, `exo serve` calls `factory(link, **config)`, runs `start()` after the server listens, and `stop()` on
shutdown. The link is exod's in-process `ChannelLink`: it runs commands as the caller `channel` (`docs/GATE.md`
"Who answered"), the only caller the core takes a bus message from. A channel run through a protocol `Client` on
the socket cannot hand in bus messages.

## Several seats in one home

Seats whose files sit in one `$EXO_HOME` run side by side. With no `exo serve` on the home's `run/exod.sock`,
`exo tui --seat scout` and `exo tui --seat forge` are two cores, one per seat. So are two `exo serve --seat NAME`,
each with its own `--socket`. A TUI window joins a server on `run/exod.sock` whatever its `--seat`, so it then talks
to that server's seat. Each core holds its own writer lock (`run/agent-<seat>.lock`, see docs/MEMORY-MODEL.md), and
all of them use the home's `sessions.sqlite` and `schedule.sqlite`.

- **Events.** Each core numbers its own events, and the database raises a number past the table's highest inside
  the write transaction, so two cores never reuse one. One core's stream still rises, with gaps where the other
  seat wrote. A core sends only its own events live. Replay goes by number, not by seat, so it returns the other
  seat's events too. A session filter still lets through the ones with no session, such as a scheduled job's card.
  `hello`, `events.subscribe` and `/health` report the table's highest number, so a client that starts from there
  replays none of the other seat's past events.
- **Delegate tasks.** A core fails the queued or running tasks a dead process left behind only for its own agent,
  and only once it holds the writer lock. Another seat's core, or a second core of the same seat that fails the
  lock, leaves live tasks alone.
- **Scheduled jobs.** A job with a `seat` runs only in that seat's core, and each due slot runs once
  (docs/SCHEDULE.md).
