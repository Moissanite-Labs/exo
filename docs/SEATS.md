# Seats (core loader format)

A seat is one named configuration of an Exo agent: who it serves, which models, which tools, which
skills, which MCP servers, which plugins and channels. Run one with `exo serve --seat <name>`
(sets `EXO_SEAT`; the engine factory reads it). `seat.list` shows the files and the active seat.

This is the one seat format. The role templates in `plugins/exo-orchestra/seats/*/seat.toml` are written in
it (see `plugins/exo-orchestra/SEATS.md`), and `/orchestra check <file>` validates a file against it.

Where: `$EXO_HOME/seats/<name>.toml`, `<name>.json`, or `<name>/seat.toml` with a `SOUL.md` beside it
(its text is added to the system prompt). A few shorter spellings are accepted (top-level `name`, `role`,
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
```

## Skills

Sources, first name wins: the seat's folders, `$EXO_HOME/skills`, the Exo pack (`skills/` in the checkout, or
`$EXO_SKILLS_PACK`), then skills plugins register with `register_skill`. Each `SKILL.md` needs `name` and
`description` frontmatter. The system prompt carries one line per skill; the `skill` tool loads one body.

## MCP

`pip install 'exo[mcp]'`. `command` is stdio, `url` is streamable HTTP (`headers` allowed). Tools appear as
`mcp__<server>__<tool>` in toolset `mcp:<server>`, risk `medium` unless set. A server that fails to start is
reported by `seat.list` and skipped.

## Config and secrets

`config.get` returns the effective config with secret-looking values masked. `config.set` writes
`$EXO_HOME/config.json` and refuses secrets: names such as `api_key` or `token` (names ending `_env` are
fine, they hold an environment variable name), literals under `env` or `headers` (use `"${VAR}"`), and
values shaped like API keys. Changes apply on the next start.

## Channels

A plugin calls `register_channel(name, factory)`. For each channel a seat (or `channels` in config.json)
enables, `exo serve` calls `factory(link, **config)` with a real protocol `Client` on the server socket, runs
`start()` after the server listens, and `stop()` on shutdown.
