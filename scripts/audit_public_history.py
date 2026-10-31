#!/usr/bin/env python3
"""Read-only, content-free audit of the historical paths named in issue #49."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

TARGETS = ("inventory/", "docs/BRIEF.md", "docs/STORE-V2.md", "scripts/deploy-site.sh")
PATTERNS = {
    "email": re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "home_path": re.compile(rb"/(?:Users|home)/[^/\s\"'`<>]+"),
}
MAX_BLOB_BYTES = 16 * 1024 * 1024
REDIRECT_ENV = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_INDEX_FILE",
    "GIT_SHALLOW_FILE", "GIT_REPLACE_REF_BASE", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
}
TRANSPORT_ENV = {"GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND", "GIT_EXEC_PATH"}


def redirects_repository(key: str) -> bool:
    return key in REDIRECT_ENV or key.startswith("GIT_CONFIG")


def git_command(repo: Path, *args: str) -> list[str]:
    return ["git", "--no-pager", "--no-replace-objects", "-C", str(repo), *args]


def git_environment() -> dict[str, str]:
    return {**{key: value for key, value in os.environ.items() if not redirects_repository(key)},
            "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_GRAFT_FILE": os.devnull}


def head_state(repo: Path) -> tuple[bytes, bytes]:
    return (git(repo, "rev-parse", "--verify", "HEAD").strip(),
            git(repo, "rev-parse", "--symbolic-full-name", "HEAD").strip())


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(git_command(repo, *args), env=git_environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError("Git history audit failed; inspect repository access locally")
    return result.stdout


def namespace_counts(refs: list[bytes]) -> dict[str, int]:
    result = {"heads": 0, "tags": 0, "remotes": 0, "pull": 0, "other": 0}
    for line in refs:
        name = line.split()[0]
        namespace = next((key for key in result if name.startswith(b"refs/" + key.encode() + b"/")), "other")
        result[namespace] += 1
    return result


def advertised_refs(repo: Path, remote: str) -> list[bytes]:
    # A named remote only: a URL or option is not accepted or echoed.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", remote):
        raise ValueError("invalid remote name")
    return sorted(name + b" " + oid for oid, name in
                  (line.split() for line in git(repo, "ls-remote", "--refs", remote).splitlines()))


def remote_location(repo: Path, remote: str) -> str:
    """Classify the effective fetch URL, without returning or reporting it.

    get-url expands insteadOf aliases. A mirror of a local ordinary clone cannot
    establish hosting coverage, even when its local source matches perfectly.
    Unknown remote-helper transports are not executed for verification.
    """
    urls = git(repo, "remote", "get-url", "--all", remote).splitlines()
    if len(urls) != 1:
        return "unsupported"
    url = os.fsdecode(urls[0])
    if url.lower().startswith("file:") or url.startswith(("/", ".", "~", "\\")):
        return "local"
    if re.match(r"^[A-Za-z]:[\\/]", url):
        return "local"
    try:
        parsed = urlsplit(url)
        if parsed.scheme in ("https", "http", "ssh", "git") and parsed.hostname:
            return "network"
    except ValueError:
        return "unsupported"
    if "://" in url or "::" in url:
        return "unsupported"
    if re.fullmatch(r"(?:[^/@:\s]+@)?[^/:\s]+:.+", url):
        return "network"  # Git's scp-style SSH syntax.
    return "local"


def transport_overridden(repo: Path, remote: str) -> bool:
    """Known custom commands/helpers can serve a different repository than the URL.

    Refuse their presence conservatively, even if a particular URL scheme would
    not use them. Never execute a custom transport to decide whether it is safe.
    """
    if any(key in os.environ for key in TRANSPORT_ENV):
        return True
    keys = {b"core.sshcommand", b"core.gitproxy", f"remote.{remote}.uploadpack".lower().encode(),
            f"remote.{remote}.vcs".lower().encode()}
    config = git(repo, "config", "--includes", "--list", "--null").split(b"\0")
    return any(item.split(b"\n", 1)[0].lower() in keys for item in config)


def audit(repo: Path, literals: tuple[bytes, ...] = (), verify_remote: str | None = None) -> dict:
    report = {"schema": 3, "read_only": True, "ref_count": 0, "targets": [],
              "complete": False, "local_complete": False, "remote_verified": False,
              "coverage": "local refs only; non-mirror clones can omit advertised branches and refs/pull/*; "
                          "remote coverage requires --verify-remote on a full mirror; excludes reflogs, "
                          "unreachable and unadvertised objects, caches, LFS payloads, forks and other clones",
              "literal_scope": "reachable blob, commit, tag and tree objects, historical paths and ref names"
                               if literals else "not requested",
              "environment_redirected": any(redirects_repository(key) for key in os.environ)}
    if report["environment_redirected"]:
        return report
    # Refuse before object traversal: older Git versions may ignore NO_LAZY_FETCH.
    shallow = git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true"
    config = git(repo, "config", "--includes", "--list", "--null").lower().split(b"\0")
    partial = any(item.split(b"\n", 1)[0] == b"extensions.partialclone" or
                  (item.split(b"\n", 1)[0].startswith(b"remote.") and
                   item.split(b"\n", 1)[0].endswith(b".promisor")) for item in config)
    # --git-path info/grafts follows GIT_GRAFT_FILE, which we disable for reads.
    # Resolve the common directory to inspect the repository's default file too.
    graft_path = Path(os.fsdecode(git(repo, "rev-parse", "--git-common-dir").strip()))
    if not graft_path.is_absolute():
        graft_path = repo / graft_path
    grafts = "GIT_GRAFT_FILE" in os.environ or (graft_path / "info/grafts").exists()
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    worktrees = git(repo, "worktree", "list", "--porcelain", "-z").split(b"\0")
    linked_worktrees = sum(item.startswith(b"worktree ") for item in worktrees)
    report.update({"ref_count": len(refs), "ref_namespaces": namespace_counts(refs),
                   "shallow": shallow, "partial": partial, "grafts": grafts,
                   "worktree_count": linked_worktrees})
    if shallow or partial or grafts or linked_worktrees > 1:
        return report
    remote_refs = None
    if verify_remote is not None:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", verify_remote):
            raise ValueError("invalid remote name")
        fetch = git(repo, "config", "--get-all", f"remote.{verify_remote}.fetch").splitlines()
        report["full_mirror"] = (git(repo, "rev-parse", "--is-bare-repository").strip() == b"true"
                                 and fetch == [b"+refs/*:refs/*"])
        if not report["full_mirror"]:
            return report
        location = remote_location(repo, verify_remote)
        report["remote_local"] = location == "local"
        report["remote_network"] = location == "network"
        if report["remote_network"]:
            report["transport_overridden"] = transport_overridden(repo, verify_remote)
            if not report["transport_overridden"]:
                remote_refs = advertised_refs(repo, verify_remote)
                report["remote_ref_namespaces"] = namespace_counts(remote_refs)
                report["mirror_refs_match"] = sorted(refs) == remote_refs
                if not report["mirror_refs_match"]:
                    return report
    head = head_state(repo)
    roots = tuple(sorted({line.split()[-1].decode("ascii") for line in refs} |
                         {head[0].decode("ascii")}))
    inventory = object_inventory(repo, roots)
    for target in TARGETS:
        objects = git(repo, "rev-list", "--objects", *roots, "--full-history", "--",
                      ":(top,literal)" + target).splitlines()
        candidates = {line.split(b" ", 1)[0].decode("ascii") for line in objects
                      if b" " in line and line.split(b" ", 1)[1]}
        blobs = set()
        for oid in candidates:
            if git(repo, "cat-file", "-t", oid).strip() == b"blob":
                blobs.add(oid)
        row = {"target": target, "historical_blobs": len(blobs), "scanned_blobs": 0,
               "oversized_blobs": 0, "pattern_matches": {name: 0 for name in PATTERNS},
               "literal_matches": 0, "removal_candidate": target in TARGETS[:2] and bool(blobs)}
        for oid in blobs:
            if int(git(repo, "cat-file", "-s", oid)) > MAX_BLOB_BYTES:
                row["oversized_blobs"] += 1
                continue
            content = git(repo, "cat-file", "blob", oid)
            row["scanned_blobs"] += 1
            for name, pattern in PATTERNS.items():
                row["pattern_matches"][name] += len(pattern.findall(content))
            row["literal_matches"] += sum(content.count(value) for value in literals)
        report["targets"].append(row)
    if literals:
        report["all_objects_literals"] = scan_literals(repo, literals, inventory)
        report["historical_paths_literals"] = scan_path_literals(repo, literals, roots, inventory)
        report["ref_names_literals"] = {
            "scanned_ref_names": len(refs),
            "literal_matches": sum(line.split()[0].count(value) for line in refs for value in literals),
        }
    # A concurrent ref update makes this snapshot unsuitable for verification.
    report["refs_unchanged"] = refs == git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    report["head_unchanged"] = head == head_state(repo)
    report["local_complete"] = (report["refs_unchanged"] and report["head_unchanged"]
                                and not any(row["oversized_blobs"] for row in report["targets"])
                                and not report.get("all_objects_literals", {}).get("oversized_objects", 0)
                                and not report.get("historical_paths_literals", {}).get("oversized_trees", 0))
    if remote_refs is not None:
        report["remote_refs_unchanged"] = remote_refs == advertised_refs(repo, verify_remote)
        report["remote_verified"] = report["remote_refs_unchanged"] and report["mirror_refs_match"]
    report["complete"] = report["local_complete"] and report["remote_verified"]
    return report


def object_inventory(repo: Path, roots: tuple[str, ...] | None = None) -> list[tuple[bytes, bytes, int]]:
    if roots is None:
        refs = git(repo, "for-each-ref", "--format=%(objectname)").splitlines()
        roots = tuple(sorted({x.decode("ascii") for x in refs} |
                             {head_state(repo)[0].decode("ascii")}))
    objects = git(repo, "rev-list", "--objects", *roots).splitlines()
    ids = sorted({line.split(b" ", 1)[0] for line in objects})
    inventory = subprocess.run(git_command(repo, "cat-file", "--batch-check"),
                               input=b"\n".join(ids) + b"\n", env=git_environment(),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if inventory.returncode:
        raise ValueError("history object inventory failed")
    rows = []
    for line in inventory.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise ValueError("history object unavailable")
        rows.append((fields[0], fields[1], int(fields[2])))
    return rows


def scan_literals(repo: Path, literals: tuple[bytes, ...], inventory: list) -> dict:
    result = {"scanned_blobs": 0, "scanned_commits": 0, "scanned_tags": 0,
              "scanned_trees": 0,
              "oversized_objects": 0, "literal_matches": 0}
    # Request one object at a time to bound memory and avoid pipe deadlock.
    with subprocess.Popen(git_command(repo, "cat-file", "--batch"), env=git_environment(),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            for oid, kind, size in inventory:
                if kind not in (b"blob", b"commit", b"tag", b"tree"):
                    continue
                if int(size) > MAX_BLOB_BYTES:
                    result["oversized_objects"] += 1
                    continue
                process.stdin.write(oid + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3 or header[0] != oid or header[1] != kind:
                    raise ValueError("history object unavailable")
                content = process.stdout.read(int(header[2]))
                if len(content) != int(header[2]) or process.stdout.read(1) != b"\n":
                    raise ValueError("history object truncated")
                result["scanned_" + kind.decode() + "s"] += 1
                result["literal_matches"] += sum(content.count(value) for value in literals)
        finally:
            process.stdin.close()
            process.wait()
        if process.returncode:
            raise ValueError("history object scan failed")
    return result


def scan_path_literals(repo: Path, literals: tuple[bytes, ...], roots: tuple[str, ...], inventory: list) -> dict:
    """Count full paths per distinct historical root tree, including tree-only refs.

    Raw tree-object matches alone miss a literal spanning multiple path components.
    Read entries once and walk them with their prefixes; never print any path.
    """
    root_trees = set(git(repo, "rev-list", "--format=%T", "--no-commit-header", *roots).splitlines())
    for oid in roots:
        if git(repo, "cat-file", "-t", oid + "^{}").strip() == b"tree":
            root_trees.add(git(repo, "rev-parse", oid + "^{}").strip())
    sizes = {oid: size for oid, kind, size in inventory if kind == b"tree"}
    oid_size = 32 if git(repo, "rev-parse", "--show-object-format").strip() == b"sha256" else 20
    result = {"scanned_root_trees": len(root_trees), "scanned_paths": 0,
              "literal_matches": 0, "oversized_trees": 0}
    entries = {}
    with subprocess.Popen(git_command(repo, "cat-file", "--batch"), env=git_environment(),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            for root in root_trees:
                stack = [(b"", root)]
                while stack:
                    prefix, oid = stack.pop()
                    if oid not in sizes:
                        raise ValueError("history tree unavailable")
                    if sizes[oid] > MAX_BLOB_BYTES:
                        result["oversized_trees"] += 1
                        continue
                    if oid not in entries:
                        process.stdin.write(oid + b"\n")
                        process.stdin.flush()
                        header = process.stdout.readline().split()
                        if len(header) != 3 or header[:2] != [oid, b"tree"]:
                            raise ValueError("history tree unavailable")
                        content = process.stdout.read(int(header[2]))
                        if len(content) != int(header[2]) or process.stdout.read(1) != b"\n":
                            raise ValueError("history tree truncated")
                        parsed, offset = [], 0
                        while offset < len(content):
                            space = content.index(b" ", offset)
                            end = content.index(b"\0", space)
                            child = content[end + 1:end + 1 + oid_size]
                            if len(child) != oid_size:
                                raise ValueError("history tree invalid")
                            parsed.append((content[offset:space], content[space + 1:end], child.hex().encode()))
                            offset = end + 1 + oid_size
                        entries[oid] = parsed
                    for mode, name, child in entries[oid]:
                        path = prefix + name
                        result["scanned_paths"] += 1
                        result["literal_matches"] += sum(path.count(value) for value in literals)
                        if mode == b"40000":
                            stack.append((path + b"/", child))
        finally:
            process.stdin.close()
            process.wait()
        if process.returncode:
            raise ValueError("history path scan failed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--literal-file", type=Path,
                        help="private newline-delimited bytes to count; values are never printed")
    parser.add_argument("--verify-remote", help="verify a named full-mirror remote's advertised refs before and after scanning")
    args = parser.parse_args(argv)
    try:
        literals = tuple(x for x in args.literal_file.read_bytes().splitlines() if x) if args.literal_file else ()
        if args.literal_file and not literals:
            raise ValueError("empty literal file")
        report = audit(args.repo, literals, args.verify_remote)
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({"error": "history audit failed; inspect repository and private pattern file locally"}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
