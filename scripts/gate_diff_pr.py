#!/usr/bin/env python3
"""Merge-aware gate preflight for pull requests (#1147).

Runs the gate corpus on three checkouts: the PR base, a resolved ``main`` reference, and the PR head. Fails when the
head is lowered vs ``main`` on any pair. When the head is lowered vs the PR base, allows the pair only when the
head's decision equals ``main``'s on that (action, policy) — an inherited, reviewed main change. All other base
lowerings fail (including dropped bridge-only rules such as ``tool_output``).

    python scripts/gate_diff_pr.py --pr-base ../base --head . [--main ../main] [--json out.json]

Exit 0 when checks pass, 1 when a forbidden lowering is found, 2 on script or tree errors (same as gate_diff.py).
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

_TOOL_OUTPUT_RULE = 'elif (tool == "tool_output" and set(args) <= {"reference", "offset", "limit"}'


def _gate_file(tree: Path) -> Path:
    return tree / "src" / "exo" / "gate.py"


def _tool_output_rule_present(tree: Path) -> bool:
    return _TOOL_OUTPUT_RULE in _gate_file(tree).read_text(encoding="utf-8")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)


def resolve_main_commit(repo: Path, pr_base_sha: str, head_sha: str, explicit: str | None) -> str:
    """Pick the ``main`` gate checkout: explicit ref, else newest merge second-parent on MB..HEAD, else origin/main."""
    if explicit:
        r = _git(repo, "rev-parse", "--verify", explicit)
        if r.returncode != 0:
            raise gd.Failed(f"--main {explicit!r}: {r.stderr.strip() or 'not a valid ref'}")
        return r.stdout.strip()
    mb = _git(repo, "merge-base", pr_base_sha, head_sha)
    if mb.returncode != 0:
        raise gd.Failed(f"could not merge-base {pr_base_sha} and {head_sha}: {mb.stderr.strip()}")
    mb_sha = mb.stdout.strip()
    merged: str | None = None
    log = _git(repo, "log", f"{mb_sha}..{head_sha}", "--merges", "--format=%H %P")
    if log.returncode == 0:
        for line in log.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            second = parts[2]
            anc = _git(repo, "merge-base", "--is-ancestor", second, "origin/main")
            if anc.returncode == 0:
                merged = second
                break
    if merged:
        return merged
    tip = _git(repo, "rev-parse", "origin/main")
    if tip.returncode != 0:
        raise gd.Failed(f"could not resolve origin/main: {tip.stderr.strip()}")
    return tip.stdout.strip()


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


def _pair_item(action: dict, policy: str, ref_label: str, ref_d: tuple, head_d: tuple, cb, tb, ch, th,
               kinds: list[str]) -> dict:
    return {
        "action": action,
        "policy": policy,
        "kinds": kinds,
        ref_label: {"category": cb, "target": tb, "outcome": ref_d[0], "rule": ref_d[1], "reason": ref_d[2],
                    "reserved": ref_d[3], "violations": ref_d[4], "flags": ref_d[5] if len(ref_d) > 5 else []},
        "head": {"category": ch, "target": th, "outcome": head_d[0], "rule": head_d[1], "reason": head_d[2],
                 "reserved": head_d[3], "violations": head_d[4], "flags": head_d[5] if len(head_d) > 5 else []},
    }


def merge_aware_scan(
    pr_base_out: Path,
    main_out: Path,
    head_out: Path,
    actions: list[dict],
    json_limit: int,
    *,
    pr_base_tree: Path,
    head_tree: Path,
) -> dict[str, Any]:
    names_b, rb = _policy_rows(pr_base_out)
    names_m, rm = _policy_rows(main_out)
    names_h, rh = _policy_rows(head_out)
    if names_b != names_m or names_b != names_h:
        raise RuntimeError("the three trees did not run the same policies")
    counts = Counter(
        lowered_vs_main=0,
        lowered_vs_base=0,
        allowed_because_main=0,
        forbidden_vs_base=0,
        pairs_same=0,
    )
    by_transition_main: Counter[str] = Counter()
    by_transition_forbidden: Counter[str] = Counter()
    allowed: list[dict] = []
    forbidden: list[dict] = []
    lowered_main: list[dict] = []
    bridge_tool_output_removed = _tool_output_rule_present(pr_base_tree) and not _tool_output_rule_present(head_tree)
    seen = 0
    for i, (row_b, row_m, row_h) in enumerate(zip(rb, rm, rh)):
        if isinstance(row_b, dict) or isinstance(row_m, dict) or isinstance(row_h, dict):
            break
        seen += 1
        cb, tb, _, ub, pb, _ = row_b[:6]
        ch, th, _, uh, ph, _ = row_h[:6]
        _, _, _, um, pm, _ = row_m[:6]
        nb = row_b[6] if len(row_b) > 6 else []
        nh = row_h[6] if len(row_h) > 6 else []
        nm = row_m[6] if len(row_m) > 6 else []
        for j, name in enumerate(names_b):
            db, dh, dm = ub[pb[j]], uh[ph[j]], um[pm[j]]
            if db == dh == dm:
                counts["pairs_same"] += 1
                continue
            kinds_main = _lowered_kinds(dm, dh, nm, nh)
            if kinds_main:
                counts["lowered_vs_main"] += 1
                by_transition_main[f"main {dm[0]} -> head {dh[0]}"] += 1
                if len(lowered_main) < json_limit:
                    lowered_main.append(_pair_item(actions[i], name, "main", dm, dh, cb, tb, ch, th, kinds_main))
            kinds_base = _lowered_kinds(db, dh, nb, nh)
            if not kinds_base:
                continue
            counts["lowered_vs_base"] += 1
            action = actions[i]
            bridge_drop = bridge_tool_output_removed and action.get("tool") == "tool_output"
            if dm == dh and _lowered_kinds(db, dm, nb, nm) and not bridge_drop:
                counts["allowed_because_main"] += 1
                if len(allowed) < json_limit:
                    allowed.append(_pair_item(action, name, "pr_base", db, dh, cb, tb, ch, th, kinds_base))
            else:
                counts["forbidden_vs_base"] += 1
                by_transition_forbidden[f"pr_base {db[0]} -> head {dh[0]}"] += 1
                if len(forbidden) < json_limit:
                    forbidden.append(_pair_item(action, name, "pr_base", db, dh, cb, tb, ch, th, kinds_base))
    if seen != len(actions):
        raise RuntimeError("a result stream ended early")
    if bridge_tool_output_removed and any(a.get("tool") == "tool_output" for a in actions):
        counts["forbidden_vs_base"] = max(counts["forbidden_vs_base"], 1)
        counts["bridge_tool_output_regression"] = 1
    return {
        "counts": dict(counts),
        "lowered_vs_main_by_transition": dict(by_transition_main.most_common()),
        "forbidden_vs_base_by_transition": dict(by_transition_forbidden.most_common()),
        "allowed_because_main": allowed,
        "forbidden_vs_base": forbidden,
        "lowered_vs_main": lowered_main,
        "allowed_truncated": counts["allowed_because_main"] > len(allowed),
        "forbidden_truncated": counts["forbidden_vs_base"] > len(forbidden),
        "lowered_main_truncated": counts["lowered_vs_main"] > len(lowered_main),
        "bridge_tool_output_rule_removed": bridge_tool_output_removed,
        "bridge_tool_output_regression": counts.get("bridge_tool_output_regression", 0),
    }


def run_three_trees(a: argparse.Namespace, pr_base: Path, main: Path, head: Path
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
        trees = (head, pr_base, main)
        actions, corpus_counts = gd.build_corpus(trees, a.corpus, builtin=not a.only_corpus,
                                                 generate=not a.no_variants, policies=policies.values())
        unread = gd.unread_tables({"head": head, "pr_base": pr_base, "main": main}) if not a.only_corpus and not a.no_variants else {}
        if not actions or not policies:
            raise gd.Failed("the corpus or the policy set is empty")
        work = gd.work_dir()
        home = a.home.expanduser().resolve() if a.home else gd.make_home(work)
        corpus_path, pol_path = work / "corpus.json", work / "policies.json"
        corpus_path.write_text(json.dumps(actions), encoding="utf-8")
        pol_path.write_text(json.dumps(policies), encoding="utf-8")
        outs = {label: work / f"{label}.jsonl.gz" for label in ("pr_base", "main", "head")}
        for label, tree in (("pr_base", pr_base), ("main", main), ("head", head)):
            procs[label] = gd.start_tree(label, tree, corpus_path, pol_path, outs[label], max(1, a.jobs), home)
        _, why = gd.wait_trees(procs, a.timeout)
        if why:
            raise gd.Failed(why)
    finally:
        gd._ignore_stops()
        for p in procs.values():
            gd.stop_tree(p)
    return actions, outs, {"corpus_counts": corpus_counts, "unread_tables": unread, "wall_secs": round(time.monotonic() - t0, 1)}, work


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Merge-aware gate preflight for a pull request.")
    p.add_argument("--pr-base", required=True, type=gd._tree_arg)
    p.add_argument("--head", required=True, type=gd._tree_arg)
    p.add_argument("--main", type=gd._tree_arg, help="main gate checkout (default: resolve via git and add a worktree)")
    p.add_argument("--main-ref", help="main commit sha for the report when --main is not a git checkout")
    p.add_argument("--git-dir", type=Path, help="repo for merge-base and main resolution (default: parent of --head)")
    p.add_argument("--pr-base-ref", help="PR base commit sha (default: git HEAD of --pr-base worktree)")
    p.add_argument("--head-ref", help="PR head commit sha (default: git HEAD of --head)")
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


def summary_text(rep: dict, max_examples: int = 3) -> str:
    c = rep["merge_aware"]["counts"]
    lines = [
        f"merge-aware gate preflight: pr_base {rep['pr_base']}  main {rep['main']}  head {rep['head']}",
        f"  lowered vs main: {c['lowered_vs_main']} pairs (must be 0)",
        f"  lowered vs pr_base: {c['lowered_vs_base']} pairs",
        f"  allowed because head matches main: {c['allowed_because_main']} pairs",
        f"  forbidden vs pr_base (lowered and head != main): {c['forbidden_vs_base']} pairs",
    ]
    if c["lowered_vs_main"]:
        lines.append("Lowered vs main (forbidden):")
        for tag, n in list(rep["merge_aware"]["lowered_vs_main_by_transition"].items())[:max_examples]:
            lines.append(f"  {n:7d}  {tag}")
    if c["forbidden_vs_base"]:
        lines.append("Forbidden vs pr_base:")
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
    pr_base_sha = a.pr_base_ref or _sha(a.pr_base)
    head_sha = a.head_ref or _sha(a.head)
    if not pr_base_sha or not head_sha:
        raise gd.Failed("could not resolve PR base or head sha; pass --pr-base-ref and --head-ref")
    main_tree = a.main
    main_work: Path | None = None
    main_sha: str
    work: Path | None = None
    if main_tree is not None:
        main_sha = a.main_ref or _sha(main_tree) or "0" * 40
    else:
        main_sha = resolve_main_commit(git_dir, pr_base_sha, head_sha, None)
        main_work = Path(tempfile.mkdtemp(prefix="gate-diff-main-", dir="/var/tmp"))
        r = _git(git_dir, "worktree", "add", "--detach", str(main_work), main_sha)
        if r.returncode != 0:
            shutil.rmtree(main_work, ignore_errors=True)
            raise gd.Failed(f"could not add worktree for main {main_sha}: {r.stderr.strip()}")
        main_tree = main_work
    try:
        gd_a = argparse.Namespace(
            corpus=a.corpus, only_corpus=a.only_corpus, no_variants=a.no_variants, policies=a.policies,
            only_policies=a.only_policies, pick=a.pick, jobs=a.jobs, json=None, json_limit=a.json_limit,
            max_examples=a.max_examples, timeout=a.timeout, home=a.home, accept_unread=a.accept_unread,
            dump_corpus=None, base=a.pr_base, head=a.head,
        )
        actions, outs, meta, work = run_three_trees(gd_a, a.pr_base, main_tree, a.head)
        rep_bh = gd.compare(outs["pr_base"], outs["head"], actions, a.max_examples, a.json_limit)
        rep_mh = gd.compare(outs["main"], outs["head"], actions, a.max_examples, a.json_limit)
        merge = merge_aware_scan(outs["pr_base"], outs["main"], outs["head"], actions, a.json_limit,
                                   pr_base_tree=a.pr_base, head_tree=a.head)
        if merge["counts"]["lowered_vs_main"] != rep_mh["counts"]["lowered_pairs"]:
            raise gd.Failed("internal: merge scan lowered_vs_main != gate_diff(main, head)")
        if merge["counts"]["lowered_vs_base"] != rep_bh["counts"]["lowered_pairs"]:
            raise gd.Failed("internal: merge scan lowered_vs_base != gate_diff(pr_base, head)")
        accepted = {x.strip() for x in a.accept_unread.split(",") if x.strip()}
        unread = meta.get("unread_tables") or {}
        rep = {
            "pr_base": f"{a.pr_base} {gd._commit(a.pr_base)}".strip(),
            "main": f"{main_tree} {main_sha[:7]}".strip(),
            "head": f"{a.head} {gd._commit(a.head)}".strip(),
            "merge_base": pr_base_sha,
            "main_resolved": main_sha,
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
        if main_work is not None:
            _git(git_dir, "worktree", "remove", "--force", str(main_work))
            shutil.rmtree(main_work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return run(parse_args(argv))
    except gd.Failed as exc:
        print(f"gate_diff_pr: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
