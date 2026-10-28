# Read-only setup import preview prototype

Refs #502. `exo.setup_preview` maps explicitly selected snapshots into inert candidates. It does not discover
installations, modify settings, start MCP servers, load skills or apply imported authority. The API is not
registered as a runtime command. Current Exo configuration and skill loading remain unchanged.

```python
from pathlib import Path
from exo.setup_preview import read_source, preview

source = read_source(Path("/host-selected/project/.mcp.json"))
plan = preview(source, adapter="claude_mcp_json", scope="project", name="selected-project")
```

Supported fixture-format subsets are Claude-style JSON `mcpServers`, Codex-style TOML `mcp_servers`, and
explicit UTF-8 instruction/skill documents (`instruction` / `skill` adapters). MCP candidates support only
stdio `command`, string-list `args`, and environment placeholders of the exact form `${NAME}`. HTTP servers,
unknown server fields, all literal environment values, credential-shaped arguments and recognized secret
patterns are excluded without echoing their payload. Other settings, including foreign approval policies,
are reported unsupported rather than mapped into permission rules. This is a conservative subset, not a
complete compatibility claim for every current agent release or a general secret detector. Arbitrary
instruction text may contain sensitive material not recognizable by these checks and still needs review.

The host explicitly chooses `user` or `project` scope and a bounded name. Every item retains that scope in
its stable key and has `authority: reference_only`, including imperative instruction text. A skill is an
inert text candidate, never executable installation. No foreign rule grants an executor permission.

`read_source` reads a maximum of 64 KiB from a regular file and opens each path component with no symlink
following. Nothing walks an agent home or skill tree. Sources remain untouched. A source SHA-256 binds the
snapshot, and a canonical payload SHA-256 identifies each candidate. The host may provide `current` and
`imported` dictionaries of stable keys to payload hashes: an identical candidate is `unchanged`; a new key
is `add`; a source refresh whose target still equals the prior imported hash is `update`; local edits or
preexisting items are `conflict`; a previously imported item removed locally is `local_deleted_conflict`.
User and project keys cannot silently overwrite each other. Repeating the same snapshot and target state
produces the same plan. These are proposed operations, never writes.

Remaining #502 work includes versioned adapter discovery, instruction/skill directory scope mapping,
comprehensive credential policy and review UI, a reviewed-plan apply command, target/source stale checks
at apply time, backups/rollback, partial-failure recovery and optional command packaging. No apply or
rollback operation exists in this draft, and `apply_supported` is always false.
