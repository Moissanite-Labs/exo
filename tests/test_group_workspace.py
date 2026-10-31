"""G10 workspace preflight (``exo.core.group_workspace``): hermetic, tmp dirs only, no git binary and no repo.

Every "repo" here is a bare ``.git`` marker the test makes itself, so the suite never touches a live checkout.
"""

import os
import tempfile
from pathlib import Path

import pytest

from exo.core.group_workspace import WorkspaceError, git_worktree_root, validate_workspaces


def _repo(root: Path, marker: str = "dir") -> Path:
    """Make ``root`` look like a git working tree: a ``.git`` folder, or a ``.git`` file for a linked worktree."""
    root.mkdir(parents=True, exist_ok=True)
    if marker == "file":
        (root / ".git").write_text("gitdir: /nowhere/.git/worktrees/x\n")
    else:
        (root / ".git").mkdir()
    return root


# --- accepted shapes ---------------------------------------------------------------------------------------

def test_distinct_unrelated_directories_are_accepted(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    out = validate_workspaces({"alpha": str(a), "beta": str(b)})
    assert dict(out) == {"alpha": str(a.resolve()), "beta": str(b.resolve())}


def test_single_member_is_accepted(tmp_path):
    a = tmp_path / "solo"
    a.mkdir()
    assert dict(validate_workspaces({"solo": str(a)})) == {"solo": str(a.resolve())}


def test_distinct_git_worktrees_are_allowed(tmp_path):
    a = _repo(tmp_path / "wt1")
    b = _repo(tmp_path / "wt2", marker="file")  # linked worktree: .git is a file
    out = validate_workspaces({"one": str(a), "two": str(b)})
    assert set(out) == {"one", "two"}


def test_git_file_marker_is_recognised_as_a_worktree_root(tmp_path):
    root = _repo(tmp_path / "wt", marker="file")
    sub = root / "pkg" / "inner"
    sub.mkdir(parents=True)
    assert git_worktree_root(sub) == root.resolve()


def test_unrelated_directories_inside_no_repo_are_accepted(tmp_path):
    parent = tmp_path / "plain"
    a, b = parent / "x", parent / "y"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert git_worktree_root(a) is None
    assert len(validate_workspaces({"x": str(a), "y": str(b)})) == 2


def test_result_is_immutable_and_canonical(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    out = validate_workspaces({"m": str(link)})
    assert out["m"] == str(real.resolve())
    with pytest.raises(TypeError):
        out["m"] = "/elsewhere"  # type: ignore[index]


# --- same folder / ancestor overlap ------------------------------------------------------------------------

def test_same_directory_twice_is_rejected(tmp_path):
    a = tmp_path / "shared"
    a.mkdir()
    with pytest.raises(WorkspaceError, match="share the folder"):
        validate_workspaces({"alpha": str(a), "beta": str(a)})


def test_symlink_alias_of_the_same_directory_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="share the folder"):
        validate_workspaces({"alpha": str(real), "beta": str(link)})


def test_ancestor_and_descendant_overlap_is_rejected(tmp_path):
    outer, inner = tmp_path / "outer", tmp_path / "outer" / "inner"
    inner.mkdir(parents=True)
    with pytest.raises(WorkspaceError, match="overlap"):
        validate_workspaces({"outer": str(outer), "inner": str(inner)})


def test_sibling_directories_do_not_overlap_lexically(tmp_path):
    # "ab" is a prefix of "abc" as a string, but not an ancestor as a path: must be accepted.
    a, b = tmp_path / "ab", tmp_path / "abc"
    a.mkdir()
    b.mkdir()
    assert len(validate_workspaces({"a": str(a), "b": str(b)})) == 2


# --- same git working tree ---------------------------------------------------------------------------------

def test_different_directories_in_one_git_working_tree_are_rejected(tmp_path):
    root = _repo(tmp_path / "monorepo")
    a, b = root / "svc-a", root / "svc-b"
    a.mkdir()
    b.mkdir()
    with pytest.raises(WorkspaceError, match="same git working tree"):
        validate_workspaces({"a": str(a), "b": str(b)})


def test_same_worktree_through_a_symlinked_subdir_is_rejected(tmp_path):
    root = _repo(tmp_path / "monorepo")
    a, b = root / "svc-a", root / "svc-b"
    a.mkdir()
    b.mkdir()
    link = tmp_path / "repo-link"
    link.symlink_to(root, target_is_directory=True)
    # different folders, but the same working tree once the symlink is resolved
    with pytest.raises(WorkspaceError, match="same git working tree"):
        validate_workspaces({"a": str(a), "b": str(link / "svc-b")})


def test_nested_worktree_root_wins_over_the_outer_repo(tmp_path):
    outer = _repo(tmp_path / "outer")
    inner = _repo(outer / "vendored" / "wt", marker="file")
    sub = inner / "src"
    sub.mkdir()
    assert git_worktree_root(sub) == inner.resolve()
    # the nested worktree is its own tree, so an outer member and this one are distinct
    other = outer / "elsewhere"
    other.mkdir()
    assert len(validate_workspaces({"o": str(other), "i": str(sub)})) == 2


# --- invalid input -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False, 0, 1.5, None, b"/tmp", Path("/tmp")])
def test_non_string_cwd_is_rejected(tmp_path, bad):
    good = tmp_path / "ok"
    good.mkdir()
    with pytest.raises(WorkspaceError, match="must be an absolute path string"):
        validate_workspaces({"alpha": str(good), "beta": bad})


def test_relative_path_is_rejected():
    with pytest.raises(WorkspaceError, match="not absolute"):
        validate_workspaces({"alpha": "relative/dir"})


def test_empty_path_is_rejected():
    with pytest.raises(WorkspaceError, match="empty"):
        validate_workspaces({"alpha": ""})


def test_missing_path_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError, match="does not exist"):
        validate_workspaces({"alpha": str(tmp_path / "nope")})


def test_file_path_is_rejected(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(WorkspaceError, match="not a directory"):
        validate_workspaces({"alpha": str(f)})


def test_empty_mapping_is_rejected():
    with pytest.raises(WorkspaceError, match="at least one member"):
        validate_workspaces({})


def test_non_mapping_is_rejected():
    with pytest.raises(WorkspaceError, match="must be a mapping"):
        validate_workspaces([("alpha", "/tmp")])  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "   ", None, 3])
def test_bad_member_name_is_rejected(tmp_path, name):
    a = tmp_path / "ok"
    a.mkdir()
    with pytest.raises(WorkspaceError, match="member name"):
        validate_workspaces({name: str(a)})


def test_all_inputs_are_checked_before_the_overlap_judgement(tmp_path):
    # an invalid member is reported even though a valid pair would also overlap
    a = tmp_path / "shared"
    a.mkdir()
    with pytest.raises(WorkspaceError) as ei:
        validate_workspaces({"a": str(a), "b": str(a), "c": 7})
    assert "must be an absolute path string" in str(ei.value)


def test_no_directories_are_created(tmp_path):
    a = tmp_path / "keep"
    a.mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())
    validate_workspaces({"a": str(a)})
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# --- fail-closed probing -----------------------------------------------------------------------------------

def _identity_for(*pairs):
    """A stand-in for ``os.path.samefile`` that reports exactly ``pairs`` as one folder and nothing else."""
    wanted = [{str(x) for x in pair} for pair in pairs]

    def samefile(a, b):
        return {str(a), str(b)} in wanted

    return samefile


def test_alias_of_the_same_folder_is_rejected_by_identity(tmp_path, monkeypatch):
    """A path string is not an identity: a second name for one folder must be caught by stat identity.

    Two genuinely distinct directories take the place of one folder reached by two names, and the identity probe is
    mocked, so this asserts the stat-identity branch on every platform (case-sensitive or not) and on every
    filesystem, without depending on a case alias, a bind mount or a hardlinked directory existing.
    """
    a, b = tmp_path / "team-a", tmp_path / "team-b"
    a.mkdir()
    b.mkdir()
    assert a.resolve() != b.resolve()  # the path comparison alone would accept this pair
    monkeypatch.setattr("exo.core.group_workspace.os.path.samefile",
                        _identity_for((a.resolve(), b.resolve())))
    with pytest.raises(WorkspaceError, match="share the folder"):
        validate_workspaces({"alpha": str(a), "beta": str(b)})


def test_alias_of_the_same_worktree_root_is_rejected_by_identity(tmp_path, monkeypatch):
    """Two folders whose git roots are two names for one root are one working tree, not two."""
    real_root, alias_root = tmp_path / "Repo", tmp_path / "repo-alias"
    real_root.mkdir()
    alias_root.mkdir()

    def fake_root(path):
        return real_root.resolve() if Path(path).name == "one" else alias_root.resolve()

    monkeypatch.setattr("exo.core.group_workspace.git_worktree_root", fake_root)
    monkeypatch.setattr("exo.core.group_workspace.os.path.samefile",
                        _identity_for((real_root.resolve(), alias_root.resolve())))
    a, b = tmp_path / "one", tmp_path / "two"
    a.mkdir()
    b.mkdir()
    with pytest.raises(WorkspaceError, match="same git working tree"):
        validate_workspaces({"alpha": str(a), "beta": str(b)})


def test_unprobeable_identity_fails_closed(tmp_path, monkeypatch):
    """An identity probe we are not allowed to make is a refusal, never "they differ"."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    def boom(x, y, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("exo.core.group_workspace.os.path.samefile", boom)
    with pytest.raises(WorkspaceError, match="cannot tell whether .* refusing to guess"):
        validate_workspaces({"alpha": str(a), "beta": str(b)})


def test_identity_probe_is_not_needed_for_a_single_member(tmp_path, monkeypatch):
    a = tmp_path / "solo"
    a.mkdir()

    def boom(x, y, *args, **kwargs):
        raise AssertionError("a lone member has nothing to compare against")

    monkeypatch.setattr("exo.core.group_workspace.os.path.samefile", boom)
    assert dict(validate_workspaces({"solo": str(a)})) == {"solo": str(a.resolve())}


# --- F1: a case alias of an *ancestor* is still an overlap ------------------------------------------------

def _fake_identities(alias_pairs):
    """A stand-in for ``_stat_identity`` that reports exactly ``alias_pairs`` of paths as one folder.

    Every other path gets its own identity, so the fake can be used on any filesystem: it asserts the
    identity-ancestor comparison itself, not the filesystem's case sensitivity.
    """
    identities: dict[str, tuple[int, int]] = {}

    def alias_key(path):
        key = str(Path(path))
        for group in alias_pairs:
            if key in {str(Path(x)) for x in group}:
                return ("alias", frozenset(str(Path(x)) for x in group))
        return ("own", key)

    def fake(path):
        key = alias_key(path)
        if key not in identities:
            identities[key] = (1, len(identities) + 1)
        return identities[key]

    return fake


def test_case_alias_of_an_ancestor_is_rejected_by_identity(tmp_path, monkeypatch):
    """``Repo`` and ``repo/sub`` are different strings but one folder is inside the other: must be rejected.

    The path comparison alone accepts this pair (``repo/sub`` is not lexically under ``Repo``), so the rejection
    has to come from filesystem identity. The identities are mocked, so this runs on a case-sensitive filesystem
    too and does not depend on a case alias actually existing.
    """
    outer, inner = tmp_path / "Repo", tmp_path / "repo" / "sub"
    outer.mkdir()
    inner.mkdir(parents=True)
    assert not Path(inner).is_relative_to(outer)  # the string comparison would let this through
    monkeypatch.setattr("exo.core.group_workspace._stat_identity",
                        _fake_identities([(outer, tmp_path / "repo")]))
    with pytest.raises(WorkspaceError, match="overlap.*filesystem identity"):
        validate_workspaces({"outer": str(outer), "inner": str(inner)})


def test_case_alias_of_an_ancestor_is_rejected_in_either_member_order(tmp_path, monkeypatch):
    """The ancestor relation is checked both ways round, so member order cannot hide an overlap."""
    outer, inner = tmp_path / "Repo", tmp_path / "repo" / "sub"
    outer.mkdir()
    inner.mkdir(parents=True)
    monkeypatch.setattr("exo.core.group_workspace._stat_identity",
                        _fake_identities([(outer, tmp_path / "repo")]))
    with pytest.raises(WorkspaceError, match="overlap"):
        validate_workspaces({"inner": str(inner), "outer": str(outer)})


def test_identity_ancestor_check_does_not_reject_siblings(tmp_path, monkeypatch):
    """Identity comparison must not turn unrelated folders that merely share a parent into an overlap."""
    a, b = tmp_path / "Repo" / "one", tmp_path / "Repo" / "two"
    b.mkdir(parents=True)
    a.mkdir()
    monkeypatch.setattr("exo.core.group_workspace._stat_identity", _fake_identities([]))
    assert len(validate_workspaces({"one": str(a), "two": str(b)})) == 2


def test_unprobeable_filesystem_identity_fails_closed(tmp_path, monkeypatch):
    """A ``stat`` we are not allowed to make is a refusal, never "they are unrelated"."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    real_stat = os.stat

    def boom(path, *args, **kwargs):
        if Path(path) == tmp_path:  # an ancestor of both cwds cannot be inspected
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr("exo.core.group_workspace.os.stat", boom)
    with pytest.raises(WorkspaceError, match="cannot establish the filesystem identity .* refusing to guess"):
        validate_workspaces({"a": str(a), "b": str(b)})


def _case_insensitive_fs(root: Path) -> bool:
    """Whether ``root``'s filesystem treats ``CaseProbe`` and ``caseprobe`` as one name."""
    probe = root / "CaseProbe"
    try:
        probe.mkdir()
    except OSError:
        return False
    try:
        return (root / "caseprobe").is_dir()
    finally:
        probe.rmdir()


@pytest.mark.skipif(not _case_insensitive_fs(Path(tempfile.gettempdir())),
                    reason="needs a case-insensitive filesystem; the mocked test above covers the logic anywhere")
def test_case_alias_of_an_ancestor_is_rejected_on_a_real_case_insensitive_fs(tmp_path):
    """End-to-end on a case-insensitive filesystem, with real ``stat`` identity and no mocking at all."""
    outer = tmp_path / "Repo"
    outer.mkdir()
    inner = tmp_path / "repo" / "sub"  # same folder tree as Repo/sub on this filesystem
    inner.mkdir(parents=True)
    with pytest.raises(WorkspaceError, match="overlap"):
        validate_workspaces({"outer": str(outer), "inner": str(inner)})


def test_unprobeable_git_marker_fails_closed(tmp_path, monkeypatch):
    a = tmp_path / "a"
    a.mkdir()
    real_lstat = os.lstat

    def boom(path, *args, **kwargs):
        if str(path).endswith(".git"):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr("exo.core.group_workspace.os.lstat", boom)
    with pytest.raises(WorkspaceError, match="cannot inspect"):
        validate_workspaces({"a": str(a)})


def test_git_marker_contents_are_never_read(tmp_path):
    """The marker is lstat-ed only: a .git that cannot be opened as a file still identifies a worktree."""
    root = _repo(tmp_path / "wt", marker="file")
    root.chmod(0o500)
    try:
        assert git_worktree_root(root) == root.resolve()
    finally:
        root.chmod(0o700)
