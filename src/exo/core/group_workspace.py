"""G10 workspace preflight: a bounded, pure check that a group's members do not share a workspace.

Opt-in host preflight only. It answers one question — *would two members write into the same folder or the
same git working tree?* — and nothing else. It is **not** a sandbox, it does **not** take ownership of any
folder, and it does **not** lock anything against another process. It is a pure function: it composes no group
itself, it is the host that calls it — directly, or by opting in with
``GroupComposition(..., workspace_check=True)``, which runs it before any engine is constructed. See
``docs/SWARM-WORKSPACES.md``.

No git subprocess is run: a working tree is identified by the nearest ``.git`` marker (a folder in a normal
checkout, a file in a linked worktree) walking up from the resolved path, stopping at the first marker. That
marker is only ever ``lstat``-ed — never opened, parsed or walked — so no config, hooks or other contents of an
unknown ``.git`` are read. Because there is no subprocess there is no timeout to hit; the one failure mode left
is a probe we are not allowed to make (a permission error while looking for the marker), and that fails closed.

Pure over paths: no mkdir, no git writes, no logging, no global state.

A path string is not a folder identity. Two spellings of one folder (a case alias on a case-insensitive
filesystem, a bind mount, a second directory entry) compare unequal as strings while naming one folder. So every
same-folder question is answered twice: by the canonical path comparison, then by ``stat`` identity
(``os.path.samefile``). An identity probe that raises fails closed — "I could not compare these" is never read as
"they differ". The same asymmetry applies to *nesting*: canonicalising preserves the case the caller spelled, so
``/Repo`` and ``/repo/sub`` stay different strings while one really is inside the other. Nesting is therefore also
answered twice: by ``Path.is_relative_to`` on the canonical paths, then by comparing each member's ``(st_dev,
st_ino)`` identity against the identities of the other member's folder *and every ancestor of it*, both ways
round. A ``stat`` we are not allowed to make fails closed there too.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

__all__ = ["WorkspaceError", "git_worktree_root", "validate_workspaces"]


class WorkspaceError(ValueError):
    """A member's workspace is unusable, or two members would share one. The message is for the host/user."""


def git_worktree_root(path: str | Path) -> Path | None:
    """The nearest folder at or above ``path`` holding a ``.git`` marker (folder *or* file), else None.

    The marker is ``lstat``-ed only. A permission error while probing fails closed with ``WorkspaceError``:
    saying "not a repo" when we were not allowed to look would silently permit an overlap.
    """
    p = Path(path)
    for d in (p, *p.parents):
        marker = d / ".git"
        try:
            os.lstat(marker)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as e:  # e.g. EACCES on a parent we cannot inspect: we do not know, so refuse
            raise WorkspaceError(
                f"cannot inspect {d} for a git marker ({e.strerror or e}); refusing to guess whether "
                f"{p} shares a working tree") from None
        return d
    return None


def _canonical_dir(member: str, cwd: object) -> Path:
    """The resolved, existing directory for one member's cwd, or a ``WorkspaceError`` naming the member."""
    if isinstance(cwd, bool) or not isinstance(cwd, str):
        raise WorkspaceError(
            f"member {member!r}: cwd must be an absolute path string, not {type(cwd).__name__}")
    if not cwd:
        raise WorkspaceError(f"member {member!r}: cwd is empty")
    p = Path(cwd).expanduser()
    if not p.is_absolute():
        raise WorkspaceError(f"member {member!r}: cwd {cwd!r} is not absolute")
    try:
        canon = p.resolve(strict=True)
    except FileNotFoundError:
        raise WorkspaceError(f"member {member!r}: cwd {cwd!r} does not exist") from None
    except OSError as e:
        raise WorkspaceError(
            f"member {member!r}: cwd {cwd!r} cannot be resolved ({e.strerror or e})") from None
    except RuntimeError:  # symlink loop
        raise WorkspaceError(f"member {member!r}: cwd {cwd!r} has a symlink loop") from None
    if not canon.is_dir():
        raise WorkspaceError(f"member {member!r}: cwd {cwd!r} is not a directory")
    return canon


def _same_dir(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` name the same folder.

    The path comparison settles the ordinary case; ``os.path.samefile`` (``stat`` identity) settles what a string
    comparison cannot: a case alias on a case-insensitive filesystem, a bind mount, or a second directory entry for
    one folder. A probe that raises fails closed with ``WorkspaceError``: not being allowed to compare two folders
    must never be reported as "they are different".
    """
    if a == b:
        return True
    try:
        return os.path.samefile(a, b)
    except OSError as e:
        raise WorkspaceError(
            f"cannot tell whether {a} and {b} are the same folder ({e.strerror or e}); refusing to guess") from None


def _stat_identity(path: Path) -> tuple[int, int]:
    """The ``(st_dev, st_ino)`` identity of ``path``, or a ``WorkspaceError`` when the probe is refused.

    Kept as its own function so the identity probe has one definition and can be mocked in tests on every
    platform (a case alias exists only on a case-insensitive filesystem).
    """
    try:
        st = os.stat(path)
    except OSError as e:
        raise WorkspaceError(
            f"cannot establish the filesystem identity of {path} ({e.strerror or e}); refusing to guess whether "
            f"workspaces overlap") from None
    return (st.st_dev, st.st_ino)


def _identity_chain(path: Path) -> tuple[tuple[int, int], frozenset[tuple[int, int]]]:
    """``(identity of path, identities of path and all its ancestors)`` as ``(st_dev, st_ino)`` pairs.

    Names are not identities: a case alias of an ancestor folder, a bind mount, or a second directory entry makes
    two different-looking paths name one folder tree. Comparing ``(st_dev, st_ino)`` answers the question the
    strings cannot. A ``stat`` that raises fails closed with ``WorkspaceError``: a probe we were not allowed to
    make must never be reported as "these are unrelated".
    """
    ids: list[tuple[int, int]] = []
    for d in (path, *path.parents):
        ids.append(_stat_identity(d))
    return ids[0], frozenset(ids)


def validate_workspaces(members: Mapping[str, str]) -> Mapping[str, str]:
    """Check that no two members share a cwd or a git working tree; return their canonical cwds.

    ``members`` maps a member name (a non-empty ``str``) to that member's working directory (an absolute path
    string to an existing directory). At least one member is required. Every input is validated before
    anything is returned or raised, and problems are reported together in one ``WorkspaceError``.

    Rejected:

    * a path that is not an absolute path string to an existing directory (booleans and other types included);
    * two members whose resolved cwds are the same folder, or one of which is an ancestor of the other — where
      "ancestor" means either a path ancestor of the canonical paths or a folder whose filesystem identity is an
      ancestor identity of the other cwd (so a case alias of an ancestor on a case-insensitive filesystem, a bind
      mount or a second directory entry cannot slip through);
    * two members in the *same* git working tree (same nearest ``.git`` marker) but different folders — two
      agents editing one checkout race on the index, ``.git`` locks and each other's uncommitted work.

    "Same folder" and "same worktree" are checked by path comparison *and* by ``stat`` identity, so a case alias
    or another second name for one folder is caught even when the two canonical strings differ. A pair whose
    identity cannot be probed is rejected (fail closed), never assumed distinct. Nesting is checked the same way,
    by identity of each cwd against the ancestor identities of the other (in both directions).

    Allowed: members in unrelated folders, and members holding *distinct* git worktrees (each worktree has its
    own ``.git`` marker, so the trees are separate checkouts with separate indexes).

    The result is an immutable mapping from member name to canonical (symlink-resolved) path string, so callers
    cannot mutate what was agreed. Pure: nothing is created, moved or locked.
    """
    if not isinstance(members, Mapping):
        raise WorkspaceError(f"members must be a mapping of member name to cwd, not {type(members).__name__}")
    if not members:
        raise WorkspaceError("at least one member with a cwd is required")

    names = list(members)
    problems: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            problems.append(f"member name {name!r} must be a non-empty string")
    if problems:
        raise WorkspaceError("; ".join(problems))

    canonical: dict[str, Path] = {}
    for name in names:  # validate every input first, then judge the set
        try:
            canonical[name] = _canonical_dir(name, members[name])
        except WorkspaceError as e:
            problems.append(str(e))
    if problems:
        raise WorkspaceError("; ".join(problems))

    roots: dict[str, Path | None] = {}
    for name, canon in canonical.items():
        try:
            roots[name] = git_worktree_root(canon)
        except WorkspaceError as e:
            problems.append(f"member {name!r}: {e}")
    if problems:
        raise WorkspaceError("; ".join(problems))

    chains: dict[str, tuple[tuple[int, int], frozenset[tuple[int, int]]]] = {}
    if len(names) > 1:  # a lone member has nothing to overlap with, so nothing to probe
        for name, canon in canonical.items():
            try:
                chains[name] = _identity_chain(canon)
            except WorkspaceError as e:
                problems.append(f"member {name!r}: {e}")
        if problems:
            raise WorkspaceError("; ".join(problems))

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ca, cb = canonical[a], canonical[b]
            ra, rb = roots[a], roots[b]
            try:
                same_folder = _same_dir(ca, cb)
                same_root = ra is not None and rb is not None and _same_dir(ra, rb)
            except WorkspaceError as e:
                problems.append(f"members {a!r} and {b!r}: {e}")
                continue
            if same_folder:
                problems.append(f"members {a!r} and {b!r} share the folder {ca}")
                continue
            (ida, chaina), (idb, chainb) = chains[a], chains[b]
            nested_by_path = ca.is_relative_to(cb) or cb.is_relative_to(ca)
            nested_by_identity = ida in chainb or idb in chaina
            if nested_by_path or nested_by_identity:
                problems.append(
                    f"members {a!r} ({ca}) and {b!r} ({cb}) overlap: one folder is inside the other"
                    + ("" if nested_by_path else " (compared by filesystem identity)"))
                continue
            if same_root:
                problems.append(
                    f"members {a!r} ({ca}) and {b!r} ({cb}) are in the same git working tree {ra}; "
                    f"give each its own worktree")
    if problems:
        raise WorkspaceError("; ".join(problems))

    return MappingProxyType({name: str(canonical[name]) for name in names})
