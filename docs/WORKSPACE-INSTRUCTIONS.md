# Workspace instructions

**Status:** In progress (D-088, [#496](https://github.com/aska-digital/exo/issues/496)). Implemented on the feature branch and awaiting review.

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
repository marker is present, Exo reads only the session's directory. A session
with no stored working directory uses the core's directory, where its tools run.

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

The byte budget applies to the instruction files' content. Files near the root
consume it first. A partially included file and files omitted after the limit
are reported. Invalid UTF-8, including a character split at the byte limit, is
replaced and reported. The headings, paths and warnings Exo adds get 8 KiB more,
so the whole block stays near `max_bytes` plus 8,192 characters. Past that it is
cut with a note, so the warnings at its end go first. At most 16 warnings are
listed, then a count of the rest.

`filenames` cannot name a secret store, such as `.env`, `.npmrc`, `.netrc` or an
SSH key. Exo checks each name with the gate's secret-store test when the core
starts. It checks each file's full path again before it reads it.

## Trust and execution boundaries

Project guidance is local text. It does not grant tool permissions, authorize
external actions, or count as a principal approval. Exo labels this distinction
in the prompt and escapes tags that could impersonate its pinned principal
instructions. The gate and executor remain authoritative for tool execution.

The loader reads regular files only. It skips instruction symlinks, including
links to another file inside the repository, and skips special files such as
FIFOs.

It follows Git's ownership rule (`safe.directory`), eased for a repository that
a container mounts from the host. A `.git` marker or instruction file is not
trusted when another user owns it and other users can write it or its folder.
The upward search also stops at a folder that other users can write and the
user does not own, such as a shared `/tmp`. Exo then reads only the session's
directory, and skips files there that another user could have planted. The
user's own files always count. A repository owned by another user, in folders
only that user can write, still loads. Exo checks ownership again on the open
file. Directory descent refuses symlinks introduced after discovery. It does
not run project code, import plugins, scan project skills, or follow the target
named inside a worktree's `.git` file.

Required executor mode currently rejects enabled workspace instructions: this
loader reads the host filesystem, and an executor-mediated instruction reader
has not been implemented. Disable the capability or set
`workspace_instructions.enabled` to `false` for that mode.

## Developer interface

`exo.workspace_context.attach(engine, settings)` installs a
`WorkspaceInstructions` object on `engine.workspace_instructions` and a
`workspace_extra(session, cwd)` callback on the loop. `settings` is the
`workspace_instructions` object, or a `WorkspaceInstructions` that the caller
already built. `exo.compose` builds and checks one before the engine exists,
then attaches it the same way. `attach` refuses enabled instructions in required
executor mode, as `exo.compose` does. The core owns no discovery logic. It calls
the injected callback when it builds system text. A child loop inherits that
callback.

`WorkspaceInstructions.render(cwd)` returns the labeled prompt block, or an
empty string when nothing applies. `inspect(cwd)` returns source paths, scopes,
byte counts, truncation flags, and warnings without returning file contents.
Both operations reread the current files. Disabled discovery and an empty
filename list perform no filesystem reads.

Tests: `tests/test_workspace_context.py`, including actual simultaneous session
prompts, delegated children, repository boundaries, byte and rendered-size limits,
ownership in a shared folder, secret-store names, and symlink replacement during
discovery. `tests/test_harness_composition.py` checks the same scoping through
`exo.compose.create()`, with sessions and a child outside the configured cwd.
