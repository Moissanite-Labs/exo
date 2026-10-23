# Workspace instructions

**Status:** In progress (D-082, [#496](https://github.com/aska-digital/exo/issues/496)). Implemented on the feature branch and awaiting review.

Exo can read a project's `AGENTS.md` files into the model's system text. The
instructions capability is included in the `coding` and `full` harness profiles.
It is off in `lean`, and existing installations without a harness profile keep
their current behavior. To add it to a lean setup:

```sh
exo harness enable instructions
exo stop
exo
```

Configuration is read when the core starts. To enable it without selecting a
profile, set this object in `$EXO_HOME/config.json`:

```json
{
  "workspace_instructions": {
    "enabled": true,
    "claude_fallback": false,
    "max_bytes": 32768
  }
}
```

An explicit profile that disables the instructions capability takes precedence
over `workspace_instructions.enabled`. Within an enabled capability, setting
`workspace_instructions.enabled` to `false` switches discovery off.

## What a session receives

For each prompt, Exo starts at the session's current working directory and finds
the nearest ancestor containing a `.git` directory or regular `.git` file. The
file form supports Git worktrees. It reads guidance from that root down to the
session's directory, in order. A nested repository starts its own scope. When no
repository marker is present, Exo reads only the session's directory.

At each directory it considers these files in priority order and uses only the
first one present:

1. `AGENTS.override.md`
2. `AGENTS.md`
3. `CLAUDE.md`, only when `claude_fallback` is `true`

A skipped or unreadable higher-priority file produces a warning. It does not
fall through to a lower-priority file. Descendant directories below the current
working directory are not scanned. Guidance in a directory applies within that
directory. The prompt tells the model that deeper guidance takes precedence over
broader guidance when they conflict.

Every source is labeled with its path and scope. The loader reads again when a
prompt is built, so file changes, deletions, and each session's current directory
take effect without sharing one session's rendered guidance with another.
Delegated children inherit the loader and resolve guidance against their own
working directory.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` without an enabled harness capability | Enable instruction discovery. |
| `filenames` | `["AGENTS.override.md", "AGENTS.md"]` | Ordered candidate names, at most 16 plain filenames. An empty list reads no files. |
| `claude_fallback` | `false` | Append `CLAUDE.md` to a nonempty candidate list if not already present. |
| `max_bytes` | `32768` | Total source bytes read across all selected files, from 1 to 32768. |

Settings must have the types shown. Unknown settings, invalid filenames, and
invalid limits are rejected before the runtime opens its stores or imports
plugins. Custom `filenames` replaces the default list and uses the specified
order. Filenames cannot contain path separators or control characters.

The byte budget applies to the instruction files' content, not the headings Exo
adds. Files near the root consume the budget first. A partially included file
and files omitted after the limit are reported. Invalid UTF-8, including a
character split at the byte limit, is replaced and reported.

## Trust and execution boundaries

Project guidance is local text. It does not grant tool permissions, authorize
external actions, or count as a principal approval. Exo labels this distinction
in the prompt and escapes tags that could impersonate its pinned principal
instructions. The gate and executor remain authoritative for tool execution.

The loader reads regular files only. It skips instruction symlinks, including
links to another file inside the repository, and skips special files such as
FIFOs. Directory descent refuses symlinks introduced after discovery. It does
not run project code, import plugins, scan project skills, or follow the target
named inside a worktree's `.git` file.

Required executor mode currently rejects enabled workspace instructions: this
loader reads the host filesystem, and an executor-mediated instruction reader
has not been implemented. Disable the capability or set
`workspace_instructions.enabled` to `false` for that mode.

## Developer interface

`exo.workspace_context.attach(engine, settings)` installs a
`WorkspaceInstructions` object on `engine.workspace_instructions` and a
`workspace_extra(session, cwd)` callback on the loop. The core owns no discovery
logic. It calls the injected callback when it builds system text. A child loop
inherits that callback.

`WorkspaceInstructions.render(cwd)` returns the labeled prompt block, or an
empty string when nothing applies. `inspect(cwd)` returns source paths, scopes,
byte counts, truncation flags, and warnings without returning file contents.
Both operations reread the current files. Disabled discovery and an empty
filename list perform no filesystem reads.

Tests: `tests/test_workspace_context.py`, including actual simultaneous session
prompts, delegated children, repository boundaries, byte limits, and symlink
replacement during discovery.
