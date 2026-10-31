"""History preparation audits are read-only and never print matched content (#49)."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_public_history.py"
spec = importlib.util.spec_from_file_location("history_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)


def test_deleted_history_and_secondary_refs_are_checked_without_content_disclosure(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "docs").mkdir()
    brief = repo / "docs/BRIEF.md"
    brief.write_text("fixture-person@example.invalid /Users/fixture-seat private-client-fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic sensitive history")
    git(repo, "tag", "old-fixture")
    brief.unlink()
    git(repo, "add", "-u")
    git(repo, "commit", "-qm", "remove fixture")
    before = git(repo, "show-ref")
    patterns = tmp_path / "private-patterns"
    patterns.write_text("private-client-fixture\n")
    assert audit.main(["--repo", str(repo), "--literal-file", str(patterns)]) == 2
    output = capsys.readouterr().out
    for private in ["fixture-person", "/Users/fixture-seat", "private-client-fixture", str(patterns), str(repo)]:
        assert private not in output
    report = json.loads(output)
    row = next(x for x in report["targets"] if x["target"] == "docs/BRIEF.md")
    assert row["historical_blobs"] == row["scanned_blobs"] == 1
    assert row["pattern_matches"] == {"email": 1, "home_path": 1}
    assert row["literal_matches"] == 1 and row["removal_candidate"]
    assert report["read_only"] and report["local_complete"] and not report["complete"] and report["ref_count"] == 2
    assert git(repo, "show-ref") == before
    assert git(repo, "status", "--porcelain") == b""


def test_errors_do_not_echo_repository_or_pattern_paths(tmp_path, capsys):
    assert audit.main(["--repo", str(tmp_path / "private-repository")]) == 2
    output = capsys.readouterr().out
    assert "private-repository" not in output and str(tmp_path) not in output
    assert json.loads(output)["error"]


def test_oversized_blob_marks_the_report_incomplete(tmp_path, monkeypatch):
    def fake_git(repo, *args):
        if args[0] == "rev-parse":
            return b"absent-graft-file" if "--git-path" in args else b"false\n"
        if args[0] == "config":
            return b""
        if args[0] == "for-each-ref":
            return b"a\n"
        if args[0] == "worktree":
            return b"worktree fixture\0\0"
        if args[0] == "rev-list":
            return b"a docs/BRIEF.md\n"
        if args[:2] == ("cat-file", "-t"):
            return b"blob\n"
        if args[:2] == ("cat-file", "-s"):
            return str(audit.MAX_BLOB_BYTES + 1).encode()
        raise AssertionError("oversized content must never be loaded")
    monkeypatch.setattr(audit, "git", fake_git)
    monkeypatch.setattr(audit, "object_inventory", lambda repo, roots=None: [])
    report = audit.audit(tmp_path)
    assert not report["complete"] and not report["local_complete"]
    assert all(x["oversized_blobs"] == 1 and x["scanned_blobs"] == 0 for x in report["targets"])


def fixture_repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'safe').write_text('safe')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'safe root')
    return repo


def add_brief(repo):
    (repo / 'docs').mkdir(exist_ok=True)
    (repo / 'docs/BRIEF.md').write_text('synthetic-private')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'fixture')


def brief_count(report):
    return next(x['historical_blobs'] for x in report['targets'] if x['target'] == 'docs/BRIEF.md')


def test_subdirectory_and_default_cwd(tmp_path, monkeypatch, capsys):
    repo = fixture_repo(tmp_path)
    add_brief(repo)
    assert brief_count(audit.audit(repo / 'docs')) == 1
    monkeypatch.chdir(repo / 'docs')
    assert audit.main([]) == 2
    assert brief_count(json.loads(capsys.readouterr().out)) == 1


def test_disconnected_branch_tag_and_stash(tmp_path):
    repo = fixture_repo(tmp_path)
    root = git(repo, 'rev-parse', 'HEAD').strip().decode()
    git(repo, 'checkout', '-qb', 'side')
    add_brief(repo)
    git(repo, 'tag', 'side-tag')
    git(repo, 'checkout', '--detach', root)
    assert brief_count(audit.audit(repo)) == 1
    git(repo, 'branch', '-D', 'side')
    assert brief_count(audit.audit(repo)) == 1
    git(repo, 'tag', '-d', 'side-tag')
    assert brief_count(audit.audit(repo)) == 0
    (repo / 'docs').mkdir(exist_ok=True)
    (repo / 'docs/BRIEF.md').write_text('stash only')
    git(repo, 'stash', 'push', '-u')
    assert brief_count(audit.audit(repo)) == 1


def test_replacement_cannot_hide_original(tmp_path):
    repo = fixture_repo(tmp_path)
    safe = git(repo, 'rev-parse', 'HEAD').strip().decode()
    add_brief(repo)
    original = git(repo, 'rev-parse', 'HEAD').strip().decode()
    git(repo, 'replace', original, safe)
    assert brief_count(audit.audit(repo)) == 1


def test_shallow_clone_refused(tmp_path, capsys):
    repo = fixture_repo(tmp_path)
    add_brief(repo)
    (repo / 'docs/BRIEF.md').unlink()
    git(repo, 'add', '-u')
    git(repo, 'commit', '-qm', 'delete')
    clone = tmp_path / 'shallow'
    subprocess.check_call(['git', 'clone', '-q', '--depth=1', repo.as_uri(), str(clone)])
    assert audit.main(['--repo', str(clone)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['shallow'] and not report['complete'] and report['targets'] == []


def test_partial_config_refused_before_traversal(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)
    for key, value in [('remote.origin.promisor', 'true'), ('extensions.partialClone', 'origin')]:
        git(repo, 'config', key, value)
        before = sorted((str(p.relative_to(repo)), p.read_bytes()) for p in (repo / '.git/objects').rglob('*') if p.is_file())
        real_git = audit.git
        def guarded(repo, *args):
            assert args[0] not in ('rev-list', 'cat-file')
            return real_git(repo, *args)
        with monkeypatch.context() as patch:
            patch.setattr(audit, 'git', guarded)
            report = audit.audit(repo)
        assert report['partial'] and not report['complete']
        assert before == sorted((str(p.relative_to(repo)), p.read_bytes()) for p in (repo / '.git/objects').rglob('*') if p.is_file())
        git(repo, 'config', '--unset', key)
    assert audit.git_environment()['GIT_NO_LAZY_FETCH'] == '1'


def test_whole_object_literals_and_blob_tag(tmp_path):
    repo = fixture_repo(tmp_path)
    (repo / 'safe').write_text('outside-literal')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'message-literal')
    git(repo, 'tag', '-a', 'annotated', '-m', 'tag-literal')
    blob = git(repo, 'rev-parse', 'HEAD:safe').strip().decode()
    git(repo, 'tag', 'blob-only', blob)
    report = audit.audit(repo, (b'outside-literal', b'message-literal', b'tag-literal'))
    assert all(row['historical_blobs'] == 0 for row in report['targets'])
    assert report['all_objects_literals']['literal_matches'] == 3
    assert report['all_objects_literals']['scanned_tags'] == 1
    assert report['local_complete'] and not report['complete']


def test_aggregate_oversize_and_missing_object(tmp_path, monkeypatch, capsys):
    repo = fixture_repo(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(audit, 'MAX_BLOB_BYTES', 1)
        report = audit.audit(repo, (b'fixture',))
    assert not report['complete'] and not report['local_complete'] and report['all_objects_literals']['oversized_objects']
    oid = git(repo, 'rev-parse', 'HEAD:safe').strip().decode()
    (repo / '.git/objects' / oid[:2] / oid[2:]).unlink()
    assert audit.main(['--repo', str(repo)]) == 2
    assert 'error' in json.loads(capsys.readouterr().out)


def test_actual_partial_clone_does_not_fetch_objects(tmp_path, capsys):
    repo = fixture_repo(tmp_path)
    add_brief(repo)
    git(repo, 'config', 'uploadpack.allowFilter', 'true')
    clone = tmp_path / 'partial'
    subprocess.check_call(['git', 'clone', '-q', '--filter=blob:none', '--no-checkout', repo.as_uri(), str(clone)])
    objects = clone / '.git/objects'
    before = {str(p.relative_to(objects)): p.read_bytes() for p in objects.rglob('*') if p.is_file()}
    assert audit.main(['--repo', str(clone)]) == 2
    assert json.loads(capsys.readouterr().out)['partial']
    assert before == {str(p.relative_to(objects)): p.read_bytes() for p in objects.rglob('*') if p.is_file()}


def test_ref_change_marks_coverage_incomplete(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)
    real_git = audit.git
    reads = 0
    def changing(repo, *args):
        nonlocal reads
        result = real_git(repo, *args)
        if args[0] == 'for-each-ref':
            reads += 1
            if reads == 2:
                return result + b'refs/heads/new abc\n'
        return result
    monkeypatch.setattr(audit, 'git', changing)
    report = audit.audit(repo)
    assert not report['refs_unchanged'] and not report['local_complete'] and not report['complete']


def test_runbook_requires_principal_cutover_and_states_coverage():
    text = (SCRIPT.parents[1] / 'docs/PUBLIC-HISTORY-CLEANUP.md').read_text()
    assert 'Only the principal can grant action-time approval' in text
    assert 'never\nprincipal cutover authorization' in text
    assert 'all_objects_literals' in text and 'Shallow and partial/promisor' in text
    assert 'refs/pull/*' in text and 'conditional' in text
    assert 'fresh private `--mirror` clone' in text
    assert '--verify-remote origin' in text and 'local_complete' in text
    assert 'historical_paths_literals' in text and 'ref_names_literals' in text


def test_graft_file_and_environment_are_refused_before_traversal(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)
    add_brief(repo)
    tip = git(repo, 'rev-parse', 'HEAD').strip()
    graft = repo / '.git/info/grafts'
    graft.write_bytes(tip + b'\n')
    real_git = audit.git
    def guarded(repo, *args):
        assert args[0] not in ('rev-list', 'cat-file')
        return real_git(repo, *args)
    with monkeypatch.context() as patch:
        patch.setattr(audit, 'git', guarded)
        report = audit.audit(repo)
        assert report['grafts'] and not report['complete'] and report['targets'] == []
    graft.unlink()
    external = tmp_path / 'external-grafts'
    external.write_bytes(tip + b'\n')
    with monkeypatch.context() as patch:
        patch.setenv('GIT_GRAFT_FILE', str(external))
        patch.setattr(audit, 'git', guarded)
        report = audit.audit(repo)
        assert report['grafts'] and not report['complete']
    assert audit.git_environment()['GIT_GRAFT_FILE'] == __import__('os').devnull
    assert brief_count(audit.audit(repo)) == 1


def test_detached_head_roots_are_pinned_and_motion_is_incomplete(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)
    safe = git(repo, 'rev-parse', 'HEAD').strip().decode()
    add_brief(repo)
    old = git(repo, 'rev-parse', 'HEAD').strip().decode()
    git(repo, 'checkout', '--detach', old)
    for branch in git(repo, 'for-each-ref', '--format=%(refname)', 'refs/heads/').decode().splitlines():
        git(repo, 'update-ref', branch, safe)
    real_inventory = audit.object_inventory
    def moving(repo, roots=None):
        assert old in roots
        inventory = real_inventory(repo, roots)
        git(repo, 'checkout', '--detach', safe)
        return inventory
    monkeypatch.setattr(audit, 'object_inventory', moving)
    report = audit.audit(repo)
    assert brief_count(report) == 1
    assert report['refs_unchanged'] and not report['head_unchanged'] and not report['local_complete'] and not report['complete']


def clone(repo, destination, *options):
    subprocess.check_call(['git', 'clone', '-q', *options, repo.as_uri(), str(destination)])
    return destination


def test_single_branch_and_plain_clone_cannot_claim_remote_completeness(tmp_path):
    repo = fixture_repo(tmp_path)
    default = git(repo, 'symbolic-ref', '--short', 'HEAD').strip().decode()
    git(repo, 'checkout', '-qb', 'side')
    add_brief(repo)
    git(repo, 'checkout', default)
    single = clone(repo, tmp_path / 'single', '--single-branch')
    report = audit.audit(single)
    assert report['local_complete'] and not report['complete'] and brief_count(report) == 0
    assert 'non-mirror' in report['coverage'] and 'refs/pull/*' in report['coverage']
    assert report['ref_namespaces']['remotes'] == 2  # origin/HEAD and the one branch
    report = audit.audit(single, verify_remote='origin')
    assert not report['complete'] and not report['full_mirror'] and not report['targets']
    plain = clone(repo, tmp_path / 'plain')
    report = audit.audit(plain)
    assert brief_count(report) == 1 and not report['complete']
    mirror = clone(repo, tmp_path / 'mirror.git', '--mirror')
    report = audit.audit(mirror, verify_remote='origin')
    assert report['complete'] and report['local_complete'] and report['remote_verified']
    assert brief_count(report) == 1 and report['ref_namespaces']['heads'] == 2
    assert report['remote_ref_namespaces'] == report['ref_namespaces']


def test_advertised_pr_only_history_requires_verified_mirror(tmp_path, capsys):
    repo = fixture_repo(tmp_path)
    safe = git(repo, 'rev-parse', 'HEAD').strip().decode()
    add_brief(repo)
    private = git(repo, 'rev-parse', 'HEAD').strip().decode()
    git(repo, 'update-ref', 'refs/pull/1/head', private)
    git(repo, 'reset', '--hard', safe)
    plain = clone(repo, tmp_path / 'plain')
    report = audit.audit(plain)
    assert report['local_complete'] and not report['complete'] and brief_count(report) == 0
    assert report['ref_namespaces']['pull'] == 0
    mirror = clone(repo, tmp_path / 'mirror.git', '--mirror')
    assert audit.main(['--repo', str(mirror), '--verify-remote', 'origin']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['complete'] and brief_count(report) == 1 and report['ref_namespaces']['pull'] == 1
    git(repo, 'update-ref', 'refs/heads/new', safe)
    report = audit.audit(mirror, verify_remote='origin')
    assert not report['complete'] and not report['mirror_refs_match']


def test_remote_motion_during_scan_is_incomplete(tmp_path, monkeypatch):
    source = fixture_repo(tmp_path)
    mirror = clone(source, tmp_path / 'mirror.git', '--mirror')
    original = audit.scan_literals
    def moving(repo, literals, inventory):
        result = original(repo, literals, inventory)
        git(source, 'branch', 'new')
        return result
    monkeypatch.setattr(audit, 'scan_literals', moving)
    report = audit.audit(mirror, (b'fixture',), verify_remote='origin')
    assert report['local_complete'] and not report['remote_refs_unchanged'] and not report['complete']


def test_full_paths_tree_only_refs_and_ref_names_are_counted_without_disclosure(tmp_path, capsys):
    repo = fixture_repo(tmp_path)
    (repo / 'notes/private-component').mkdir(parents=True)
    (repo / 'notes/private-component/client-fixture.md').write_text('safe content')
    git(repo, 'add', '.')
    private_tree = git(repo, 'write-tree').strip().decode()
    git(repo, 'reset', '--hard', 'HEAD')
    git(repo, 'branch', 'private-component/client-fixture')
    git(repo, 'tag', '-a', 'tree-only', private_tree, '-m', 'safe')
    literals = tmp_path / 'patterns'
    literals.write_text('private-component\nprivate-component/client-fixture\n')
    assert audit.main(['--repo', str(repo), '--literal-file', str(literals)]) == 2
    output = capsys.readouterr().out
    assert 'private-component' not in output and 'client-fixture' not in output
    report = json.loads(output)
    assert report['local_complete']
    assert report['all_objects_literals']['scanned_trees'] > 0
    assert report['all_objects_literals']['literal_matches'] == 1  # one raw tree entry
    assert report['historical_paths_literals']['literal_matches'] == 3  # directory + file, plus cross-component literal
    assert report['ref_names_literals']['literal_matches'] == 2


@pytest.mark.parametrize('key', sorted(audit.REDIRECT_ENV | {'GIT_CONFIG_COUNT', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_PARAMETERS'}))
def test_git_environment_redirection_refused_before_git(tmp_path, monkeypatch, capsys, key):
    monkeypatch.setenv(key, 'private-redirection-fixture')
    def forbidden(*args):
        raise AssertionError('Git must not run under redirection')
    monkeypatch.setattr(audit, 'git', forbidden)
    assert audit.main(['--repo', str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert 'private-redirection-fixture' not in output
    report = json.loads(output)
    assert report['environment_redirected'] and not report['local_complete'] and not report['complete']
    assert key not in audit.git_environment()


def test_actual_git_dir_cannot_redirect_repository(tmp_path, monkeypatch):
    parent = tmp_path / 'a';parent.mkdir()
    private = fixture_repo(parent)
    add_brief(private)
    other = tmp_path / 'b';other.mkdir()
    safe = fixture_repo(other)
    monkeypatch.setenv('GIT_DIR', str(safe / '.git'))
    report = audit.audit(private)
    assert report['environment_redirected'] and not report['complete']


@pytest.mark.parametrize('content', [b'', b'\n\n'])
def test_empty_supplied_literal_file_is_error(tmp_path, capsys, content):
    repo = fixture_repo(tmp_path)
    patterns = tmp_path / 'private-pattern-file'
    patterns.write_bytes(content)
    assert audit.main(['--repo', str(repo), '--literal-file', str(patterns)]) == 2
    output = capsys.readouterr().out
    assert 'private-pattern-file' not in output and json.loads(output)['error']


def test_linked_detached_head_refused_before_traversal(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)
    safe = git(repo, 'rev-parse', 'HEAD').strip().decode()
    add_brief(repo)
    private = git(repo, 'rev-parse', 'HEAD').strip().decode()
    git(repo, 'reset', '--hard', safe)
    linked = tmp_path / 'linked'
    git(repo, 'worktree', 'add', '--detach', str(linked), private)
    original = audit.git
    def guarded(repo, *args):
        assert args[0] not in ('rev-list', 'cat-file')
        return original(repo, *args)
    monkeypatch.setattr(audit, 'git', guarded)
    for path in [repo, linked]:
        report = audit.audit(path)
        assert report['worktree_count'] == 2 and not report['local_complete'] and not report['complete']
