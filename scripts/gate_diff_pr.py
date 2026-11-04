#!/usr/bin/env python3
"""Merge-aware gate preflight for pull requests (#1147).

Evaluates four gate checkouts on one corpus: PR base, ``merge-base(PR base, origin/main)``, the newest ``origin/main`` commit
ancestor of head (the main revision the PR carries), and head. Fails on any pair lowered vs main. A pair lowered vs the PR base is allowed only when head matches main,
the PR base matches merge-base on that pair (the bridge never touched it), and main differs from merge-base
(main changed it after divergence). Otherwise the lowering is forbidden; bridge-stricter vs main-loosened
conflicts are counted separately.

    python scripts/gate_diff_pr.py --pr-base ../base --head . [--json out.json]

Exit 0 when checks pass, 1 when a forbidden lowering is found, 2 on script or tree errors.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts import gate_diff as gd


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)


def merge_base_with_main(repo: Path, pr_base_sha: str) -> str:
    r = _git(repo, "merge-base", pr_base_sha, "origin/main")
    if r.returncode != 0:
        raise gd.Failed(f"merge-base({pr_base_sha}, origin/main): {r.stderr.strip()}")
    return r.stdout.strip()


def main_tip_for_head(repo: Path, head_sha: str) -> str:
    """Newest ``origin/main`` commit that is an ancestor of ``head`` (the main revision the PR carries)."""
    r = _git(repo, "merge-base", head_sha, "origin/main")
    if r.returncode != 0:
        raise gd.Failed(f"merge-base({head_sha}, origin/main): {r.stderr.strip()}")
    return r.stdout.strip()


def _policy_rows(path: Path) -> tuple[list[str], Iterator[Any]]:
    stream = gd._rows(path)
    header = next(stream)["header"]
    return header["policies"], stream


def _lowered_kinds(db: tuple, dh: tuple, nb: list, nh: list) -> list[str]:
    rkb, rkh = gd._ranks(db[0], dh[0])
    kinds = ["outcome"] if rkh < rkb else (gd._lost(db, dh, nb, nh) if rkh == rkb and db[0] != "deny" else [])
    if "reworded" in kinds:
        kinds.remove("reworded")
    return kinds


def _outcome_rank(outcome: str) -> int:
    return gd.RANK.get(outcome, gd.RANK["ask"])


def _pair_item(action: dict, policy: str, labels: dict[str, tuple], kinds: list[str]) -> dict:
    item = {"action": action, "policy": policy, "kinds": kinds}
    for label, (cat, tgt, dec) in labels.items():
        item[label] = _decision_dict(cat, tgt, dec)
    return item


def _decision_dict(cat, tgt, dec: tuple) -> dict:
    return {"category": cat, "target": tgt, "outcome": dec[0], "rule": dec[1], "reason": dec[2],
            "reserved": dec[3], "violations": dec[4], "flags": dec[5] if len(dec) > 5 else []}


def merge_aware_scan(
    pr_base_out: Path,
    mb_out: Path,
    main_out: Path,
    head_out: Path,
    actions: list[dict],
    json_limit: int,
) -> dict[str, Any]:
    names_b, rb = _policy_rows(pr_base_out)
    names_mb, rmb = _policy_rows(mb_out)
    names_m, rm = _policy_rows(main_out)
    names_h, rh = _policy_rows(head_out)
    if not (names_b == names_mb == names_m == names_h):
        raise RuntimeError("the four trees did not run the same policies")
    counts = Counter(
        lowered_vs_main=0,
        lowered_vs_base=0,
        allowed_because_main=0,
        forbidden_vs_base=0,
        conflict_bridge_stricter=0,
        pairs_same=0,
    )
    by_transition_main: Counter[str] = Counter()
    by_transition_forbidden: Counter[str] = Counter()
    allowed: list[dict] = []
    forbidden: list[dict] = []
    conflict: list[dict] = []
    lowered_main: list[dict] = []
    seen = 0
    for i, (row_b, row_mb, row_m, row_h) in enumerate(zip(rb, rmb, rm, rh)):
        if any(isinstance(row, dict) for row in (row_b, row_mb, row_m, row_h)):
            break
        seen += 1
        cb, tb, _, ub, pb, _ = row_b[:6]
        cmb, tmb, _, umb, pmb, _ = row_mb[:6]
        ch, th, _, uh, ph, _ = row_h[:6]
        _, _, _, um, pm, _ = row_m[:6]
        nb = row_b[6] if len(row_b) > 6 else []
        nmb = row_mb[6] if len(row_mb) > 6 else []
        nh = row_h[6] if len(row_h) > 6 else []
        nm = row_m[6] if len(row_m) > 6 else []
        for j, name in enumerate(names_b):
            db, dmb, dm, dh = ub[pb[j]], umb[pmb[j]], um[pm[j]], uh[ph[j]]
            if db == dmb == dm == dh:
                counts["pairs_same"] += 1
                continue
            kinds_main = _lowered_kinds(dm, dh, nm, nh)
            if kinds_main:
                counts["lowered_vs_main"] += 1
                by_transition_main[f"main {dm[0]} -> head {dh[0]}"] += 1
                if len(lowered_main) < json_limit:
                    lowered_main.append(_pair_item(
                        actions[i], name,
                        {"pr_base": (cb, tb, db), "merge_base": (cmb, tmb, dmb), "main": (cb, tb, dm), "head": (ch, th, dh)},
                        kinds_main))
            kinds_base = _lowered_kinds(db, dh, nb, nh)
            if not kinds_base:
                continue
            counts["lowered_vs_base"] += 1
            action = actions[i]
            labels = {"pr_base": (cb, tb, db), "merge_base": (cmb, tmb, dmb), "main": (cb, tb, dm), "head": (ch, th, dh)}
            bridge_stricter = _outcome_rank(db[0]) > _outcome_rank(dmb[0])
            main_looser_than_base = _outcome_rank(dm[0]) < _outcome_rank(db[0])
            is_conflict = db != dmb and bridge_stricter and main_looser_than_base
            if dh == dm and db == dmb and dm != dmb:
                counts["allowed_because_main"] += 1
                if len(allowed) < json_limit:
                    allowed.append(_pair_item(action, name, labels, kinds_base))
            else:
                counts["forbidden_vs_base"] += 1
                if is_conflict:
                    counts["conflict_bridge_stricter"] += 1
                    if len(conflict) < json_limit:
                        conflict.append(_pair_item(action, name, labels, kinds_base))
                by_transition_forbidden[f"pr_base {db[0]} -> head {dh[0]}"] += 1
                if len(forbidden) < json_limit:
                    forbidden.append(_pair_item(action, name, labels, kinds_base))
    if seen != len(actions):
        raise RuntimeError("a result stream ended early")
    return {
        "counts": dict(counts),
        "lowered_vs_main_by_transition": dict(by_transition_main.most_common()),
        "forbidden_vs_base_by_transition": dict(by_transition_forbidden.most_common()),
        "allowed_because_main": allowed,
        "forbidden_vs_base": forbidden,
        "conflict_bridge_stricter": conflict,
        "lowered_vs_main": lowered_main,
        "allowed_truncated": counts["allowed_because_main"] > len(allowed),
        "forbidden_truncated": counts["forbidden_vs_base"] > len(forbidden),
        "conflict_truncated": counts["conflict_bridge_stricter"] > len(conflict),
        "lowered_main_truncated": counts["lowered_vs_main"] > len(lowered_main),
    }


def run_four_trees(
    a: argparse.Namespace,
    pr_base: Path,
    merge_base: Path,
    main: Path,
    head: Path,
) -> tuple[list[dict], dict[str, Path], dict, Path | None]:
    work: Path | None = None
    procs: dict[str, subprocess.Popen] = {}
    unread: dict[str, dict[str, str]] = {}
    t0 = time.monotonic()
    try:
        policies = {} if a.only_policies else {k: gd._entry(v) for k, v in gd.BUILTIN_POLICIES.items()}
        if a.pick:
            keep = [x.strip() for x in a.pick.split(",") if x.strip()]
            missing = [k for k in keep if k not in policies]
            if missing:
                raise gd.Failed(f"unknown built-in policies: {', '.join(missing)}")
            policies = {k: policies[k] for k in keep}
        for f in a.policies:
            try:
                loaded = gd.load_policy_file(f)
            except Exception as exc:
                raise gd.Failed(f"could not load policies from {f}: {type(exc).__name__}: {exc}") from None
            for name, entry in loaded.items():
                policies[name if name not in policies else f"{name}@{f.name}"] = entry
        trees = (head, pr_base, merge_base, main)
        actions, corpus_counts = gd.build_corpus(trees, a.corpus, builtin=not a.only_corpus,
                                                 generate=not a.no_variants, policies=policies.values())
        unread = gd.unread_tables({"head": head, "pr_base": pr_base, "merge_base": merge_base, "main": main}) if (
            not a.only_corpus and not a.no_variants) else {}
        if not actions or not policies:
            raise gd.Failed("the corpus or the policy set is empty")
        work = gd.work_dir()
        home = a.home.expanduser().resolve() if a.home else gd.make_home(work)
        corpus_path, pol_path = work / "corpus.json", work / "policies.json"
        corpus_path.write_text(json.dumps(actions), encoding="utf-8")
        pol_path.write_text(json.dumps(policies), encoding="utf-8")
        labels = ("pr_base", "merge_base", "main", "head")
        outs = {label: work / f"{label}.jsonl.gz" for label in labels}
        for label, tree in zip(labels, (pr_base, merge_base, main, head)):
            procs[label] = gd.start_tree(label, tree, corpus_path, pol_path, outs[label], max(1, a.jobs), home)
        _, why = gd.wait_trees(procs, a.timeout)
        if why:
            raise gd.Failed(why)
    finally:
        gd._ignore_stops()
        for p in procs.values():
            gd.stop_tree(p)
    return actions, outs, {
        "corpus_counts": corpus_counts,
        "unread_tables": unread,
        "wall_secs": round(time.monotonic() - t0, 1),
        "tree_wall_secs": round(time.monotonic() - t0, 1),
    }, work


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Merge-aware gate preflight for a pull request.")
    p.add_argument("--pr-base", required=True, type=gd._tree_arg)
    p.add_argument("--head", required=True, type=gd._tree_arg)
    p.add_argument("--merge-base", type=gd._tree_arg, help="merge-base gate checkout (default: git worktree at MB)")
    p.add_argument("--main", type=gd._tree_arg, help="main tip checkout (default: origin/main worktree)")
    p.add_argument("--git-dir", type=Path)
    p.add_argument("--pr-base-ref", help="PR base commit sha")
    p.add_argument("--head-ref", help="PR head commit sha")
    p.add_argument("--merge-base-ref", help="merge-base sha for the report")
    p.add_argument("--main-ref", help="main tip sha for the report")
    p.add_argument("--corpus", nargs="+", action="extend", default=[], type=Path)
    p.add_argument("--only-corpus", action="store_true")
    p.add_argument("--no-variants", action="store_true")
    p.add_argument("--policies", nargs="+", action="extend", default=[], type=Path)
    p.add_argument("--only-policies", action="store_true")
    p.add_argument("--pick", default="")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    p.add_argument("--json", type=Path)
    p.add_argument("--json-limit", type=int, default=5000)
    p.add_argument("--max-examples", type=int, default=3)
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--home", type=Path)
    p.add_argument("--accept-unread", default="")
    return p.parse_args(argv)


def _sha(tree: Path) -> str:
    r = _git(tree, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _add_worktree(git_dir: Path, path: Path, ref: str) -> None:
    r = _git(git_dir, "worktree", "add", "--detach", str(path), ref)
    if r.returncode != 0:
        raise gd.Failed(f"could not add worktree at {ref[:7]}: {r.stderr.strip()}")


def _remove_worktree(git_dir: Path, path: Path) -> None:
    _git(git_dir, "worktree", "remove", "--force", str(path))
    shutil.rmtree(path, ignore_errors=True)


def summary_text(rep: dict, max_examples: int = 3) -> str:
    c = rep["merge_aware"]["counts"]
    lines = [
        f"merge-aware gate preflight: pr_base {rep['pr_base']}  merge_base {rep['merge_base']}  "
        f"main {rep['main']}  head {rep['head']}",
        f"  wall_secs (four trees): {rep.get('wall_secs', '?')}",
        f"  lowered vs main: {c['lowered_vs_main']} pairs (must be 0)",
        f"  lowered vs pr_base: {c['lowered_vs_base']} pairs",
        f"  allowed (head=main, base=MB, main!=MB): {c['allowed_because_main']} pairs",
        f"  forbidden vs pr_base: {c['forbidden_vs_base']} pairs",
        f"  conflict_bridge_stricter: {c['conflict_bridge_stricter']} pairs",
    ]
    if c["lowered_vs_main"]:
        lines.append("Lowered vs main (forbidden):")
        for tag, n in list(rep["merge_aware"]["lowered_vs_main_by_transition"].items())[:max_examples]:
            lines.append(f"  {n:7d}  {tag}")
    if c["conflict_bridge_stricter"]:
        lines.append("conflict_bridge_stricter (forbidden):")
        for tag, n in list(rep["merge_aware"]["forbidden_vs_base_by_transition"].items())[:max_examples]:
            lines.append(f"  {n:7d}  {tag}")
    lines.append("result: FORBIDDEN LOWERING (exit 1)" if c["lowered_vs_main"] or c["forbidden_vs_base"]
                   else "result: ok (exit 0)")
    return "\n".join(lines)


def _git_dir(explicit: Path | None, head: Path) -> Path:
    if explicit is not None:
        return explicit.resolve()
    path = head.resolve()
    for _ in range(20):
        if (path / ".git").exists():
            return path
        if path.parent == path:
            break
        path = path.parent
    raise gd.Failed("could not find a git directory; pass --git-dir")


def run(a: argparse.Namespace) -> int:
    git_dir = _git_dir(a.git_dir, a.head)
    _git(git_dir, "fetch", "origin", "main")
    pr_base_sha = a.pr_base_ref or _sha(a.pr_base)
    head_sha = a.head_ref or _sha(a.head)
    if not pr_base_sha or not head_sha:
        raise gd.Failed("could not resolve PR base or head sha; pass --pr-base-ref and --head-ref")
    mb_sha = a.merge_base_ref or merge_base_with_main(git_dir, pr_base_sha)
    main_sha = a.main_ref or main_tip_for_head(git_dir, head_sha)
    mb_work = mb_tree = a.merge_base
    main_work = main_tree = a.main
    work: Path | None = None
    extra_worktrees: list[Path] = []
    try:
        if mb_tree is None:
            mb_work = Path(tempfile.mkdtemp(prefix="gate-diff-mb-", dir="/var/tmp"))
            _add_worktree(git_dir, mb_work, mb_sha)
            mb_tree = mb_work
            extra_worktrees.append(mb_work)
        if main_tree is None:
            main_work = Path(tempfile.mkdtemp(prefix="gate-diff-main-", dir="/var/tmp"))
            _add_worktree(git_dir, main_work, main_sha)
            main_tree = main_work
            extra_worktrees.append(main_work)
        gd_a = argparse.Namespace(
            corpus=a.corpus, only_corpus=a.only_corpus, no_variants=a.no_variants, policies=a.policies,
            only_policies=a.only_policies, pick=a.pick, jobs=a.jobs, json=None, json_limit=a.json_limit,
            max_examples=a.max_examples, timeout=a.timeout, home=a.home, accept_unread=a.accept_unread,
            dump_corpus=None, base=a.pr_base, head=a.head,
        )
        actions, outs, meta, work = run_four_trees(gd_a, a.pr_base, mb_tree, main_tree, a.head)
        rep_bh = gd.compare(outs["pr_base"], outs["head"], actions, a.max_examples, a.json_limit)
        rep_mh = gd.compare(outs["main"], outs["head"], actions, a.max_examples, a.json_limit)
        merge = merge_aware_scan(outs["pr_base"], outs["merge_base"], outs["main"], outs["head"], actions, a.json_limit)
        if merge["counts"]["lowered_vs_main"] != rep_mh["counts"]["lowered_pairs"]:
            raise gd.Failed("internal: merge scan lowered_vs_main != gate_diff(main, head)")
        if merge["counts"]["lowered_vs_base"] != rep_bh["counts"]["lowered_pairs"]:
            raise gd.Failed("internal: merge scan lowered_vs_base != gate_diff(pr_base, head)")
        accepted = {x.strip() for x in a.accept_unread.split(",") if x.strip()}
        unread = meta.get("unread_tables") or {}
        rep = {
            "pr_base": f"{a.pr_base} {gd._commit(a.pr_base)}".strip(),
            "merge_base": f"{mb_tree} {mb_sha[:7]}".strip(),
            "main": f"{main_tree} {main_sha[:7]}".strip(),
            "head": f"{a.head} {gd._commit(a.head)}".strip(),
            "merge_base_sha": mb_sha,
            "main_tip_sha": main_sha,
            "origin_main_tip": _git(git_dir, "rev-parse", "origin/main").stdout.strip(),
            "pr_base_sha": pr_base_sha,
            "corpus_counts": meta["corpus_counts"],
            "wall_secs": meta["wall_secs"],
            "vs_main": {"counts": rep_mh["counts"], "lowered_by_transition": rep_mh["lowered_by_transition"]},
            "vs_pr_base": {"counts": rep_bh["counts"], "lowered_by_transition": rep_bh["lowered_by_transition"]},
            "merge_aware": merge,
            "unread_tables": unread,
            "incomplete": sorted({k for tabs in unread.values() for k in tabs} - accepted),
        }
        json_path = gd._out_path(a.json, "--json")
        if json_path:
            json_path.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
        print(summary_text(rep, a.max_examples))
        if rep["merge_aware"]["counts"]["lowered_vs_main"] or rep["merge_aware"]["counts"]["forbidden_vs_base"]:
            return 1
        return 2 if rep["incomplete"] else 0
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        for wt in extra_worktrees:
            _remove_worktree(git_dir, wt)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return run(parse_args(argv))
    except gd.Failed as exc:
        print(f"gate_diff_pr: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
