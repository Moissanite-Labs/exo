# Swarm workspaces: the opt-in preflight (G10)

A group of agents that share one folder race each other: two writers, one git index, one set of `.git` locks,
and each other's uncommitted work. G10 answers that one question before a group starts.

`exo.core.group_workspace.validate_workspaces(members)` is a **pure, host-callable check** over a mapping of
member name → working directory. It either returns the canonical cwds of the members or raises `WorkspaceError`
naming every problem it found.

## What it is not

Being explicit, because these are the easy things to assume:

* **Not a sandbox.** It does not confine, restrict or intercept anything a member does at run time.
* **Not filesystem ownership.** It creates nothing, takes no ownership of a directory, and asserts no exclusive
  right to one. Another process can use the same folder happily; the preflight will not stop it.
* **Not a cross-process lock.** It is a check at one moment in one process. Two hosts running it concurrently
  both see "fine" for the same folder.
* **Opt-in, never automatic.** The host calls it and decides what to do with the answer. `GroupComposition` takes
  `workspace_check=True` (default `False`) and then runs the preflight before constructing any engine; with the
  default it is not called at all. It is a check, not a lifecycle stage: it creates nothing and owns nothing.

Use it as a **preflight**: cheap, fail-closed, and a better error than the corrupt index you would find later.

## Usage

```python
from exo.core.group_workspace import WorkspaceError, validate_workspaces

try:
    cwds = validate_workspaces({"planner": "/work/plan", "coder": "/work/code"})
except WorkspaceError as e:
    raise SystemExit(f"workspace preflight failed: {e}")
# cwds is an immutable mapping: member -> canonical (symlink-resolved) absolute path
```

Accepted and rejected:

| Situation | Result |
| --- | --- |
| Unrelated folders | accepted |
| Same folder, twice | rejected: `share the folder` |
| Symlink alias of the same folder | rejected (paths are canonicalised first) |
| One folder inside the other | rejected: `overlap` |
| A case alias of an ancestor (`/Repo` vs `/repo/sub`) | rejected: `overlap` (filesystem identity) |
| Two folders in the **same** git working tree | rejected: `same git working tree` |
| Two **distinct** git worktrees | accepted |
| Missing / relative / non-directory / non-`str` cwd | rejected |

Every input is validated before the pairwise judgement, so one bad member is reported even when a valid pair
would also overlap. At least one member is required and member names must be non-empty strings.

The result is a `MappingProxyType` of member → canonical path string, so a caller cannot mutate the agreement it
was handed.

## Why identity, not just strings

Canonicalising a path preserves the case the caller spelled, so `/work/Repo` and `/work/repo/sub` stay *different
strings* on a case-insensitive filesystem even though one folder really is inside the other. A path comparison
alone would accept that pair.

So each question is answered twice: by the canonical-path comparison (`is_relative_to`), and by `stat` identity —
each member's `(st_dev, st_ino)` compared against the identity of the other member's cwd *and every ancestor of it*,
both ways round. A `stat` that is refused (permission error) **fails closed** with `WorkspaceError`; "I could not
compare these" is never read as "they differ". The same applies to same-folder checks (`os.path.samefile`) and to
the git-root comparison, which keep working as before.

## How a working tree is identified

The nearest `.git` **marker** at or above the resolved path — a folder in a normal checkout, a file in a linked
worktree. Walking stops at that first marker, so a nested worktree (a linked worktree or a vendored checkout
inside a bigger repo) counts as its own tree and is *not* confused with the outer one.

There is deliberately **no git subprocess**: no `git rev-parse`, so no timeout to tune and no git config, hooks
or other contents read from a `.git` we do not own. The marker is `lstat`-ed and never opened. A marker we are
not allowed to inspect (a permission error) **fails closed** with `WorkspaceError` rather than being read as
"not a repo" — guessing would silently permit an overlap.

Distinct worktrees are allowed on purpose: each has its own `.git` marker, its own index and its own locks, so
they are genuinely separate checkouts.

## Relationship to the engine's seat cwd

The engine already resolves a seat's own folder and falls back to the shared default cwd
(`Engine._seat_cwd`, then `cwd or os.getcwd()`). G10 does not change that and does not re-implement it: it is a
separate check a host runs over the cwds it has already decided on. If you want a group to refuse a shared
checkout, call the preflight and act on the `WorkspaceError`; nothing else changes.
