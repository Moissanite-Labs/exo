#!/usr/bin/env python3
"""Reproducible, synthetic measurements before changing the optimization hot paths.

This reports observations, not performance thresholds or completed optimizations.
No live Exo state, provider, relay, or deployment is opened.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("src/exo/gate.py", "src/exo/consolidate.py", "src/exo/core/context.py",
           "src/exo/core/sessions.py", "clients/tui/exo_tui/app.py")


def source_measurements(root: Path) -> dict:
    """AST measurements are source sites, never runtime query/call counts."""
    modules = {}
    for name in MODULES:
        source = (root / name).read_text()
        tree = ast.parse(source)
        public = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                public.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    continue
                public.update(a.asname or (a.name.split('.')[0] if isinstance(node, ast.Import) else a.name)
                              for a in node.names if a.name != '*')
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                public.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        lengths = [n.end_lineno - n.lineno + 1 for n in functions]
        modules[name] = {"lines": len(source.splitlines()), "functions": len(functions),
                         "functions_over_100_lines": sum(n > 100 for n in lengths),
                         "largest_function_lines": max(lengths, default=0),
                         "if_nodes": sum(isinstance(n, ast.If) for n in ast.walk(tree)),
                         "public_names": sorted(n for n in public if not n.startswith('_'))}
    context = ast.parse((root / "src/exo/core/context.py").read_text())
    models = ast.parse((root / "src/exo/core/models/base.py").read_text())
    return {"modules": modules,
            "context_summaries_call_sites": sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                                and n.func.attr == "summaries" for n in ast.walk(context)),
            "transport_async_client_construction_sites": sum(isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "AsyncClient" for n in ast.walk(models))}


def removed_names(before: dict, after: dict) -> dict:
    old, new = before["source"]["modules"], after["source"]["modules"]
    return {name: missing for name, row in old.items()
            if (missing := sorted(set(row["public_names"]) - set(new.get(name, {}).get("public_names", []))))}


def asset_measurements(root: Path) -> dict:
    # Tracked assets only: never scan a home directory or follow a symlink.
    names = subprocess.check_output(["git", "ls-files", "-z", "site", "docs/media"], cwd=root).split(b"\0")
    groups = defaultdict(list)
    total = 0
    for raw in names:
        if not raw:
            continue
        name = raw.decode()
        path = root / name
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg"}:
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            continue
        data = path.read_bytes()
        total += len(data)
        groups[(hashlib.sha256(data).hexdigest(), len(data))].append(name)
    duplicates = [{"sha256": digest, "bytes_each": size, "paths": sorted(paths)}
                  for (digest, size), paths in groups.items() if len(paths) > 1]
    return {"tracked_image_bytes": total,
            "duplicate_excess_bytes": sum(x["bytes_each"] * (len(x["paths"]) - 1) for x in duplicates),
            "duplicate_groups": sorted(duplicates, key=lambda x: x["paths"]),
            "cache_config_present": (root / "site/vercel.json").is_file() or (root / "vercel.json").is_file()}


def observe(db, call) -> tuple[dict, object]:
    statements = []
    db.set_trace_callback(statements.append)
    start = time.perf_counter()
    try:
        result = call()
    finally:
        elapsed = time.perf_counter() - start
        db.set_trace_callback(None)
    kinds = Counter(s.lstrip().split(None, 1)[0].upper() for s in statements if s.strip())
    return {"seconds": elapsed, "sql_statements": len(statements), "sql_kinds": dict(kinds)}, result


def session_measurements(folder: Path, messages: int, events: int) -> dict:
    from exo.core.sessions import SessionStore
    from exo.core.types import Message
    from exo.protocol.schema import Notice
    store = SessionStore(folder / "sessions.sqlite")
    try:
        session = store.create_session("synthetic benchmark")
        for i in range(messages):
            store.append_message(session.id, Message.user(f"synthetic {i}: " + "x" * 64))
        for i in range(events):
            store.append_event(Notice(text=f"synthetic event {i}"), session=session.id)
        full, all_messages = observe(store._db, lambda: store.messages(session.id))
        tail, tail_messages = observe(store._db, lambda: store.messages(session.id, since=max(0, messages - 40)))
        listing, sessions = observe(store._db, lambda: store.list_sessions())
        fork, child = observe(store._db, lambda: store.fork(session.id))
        event_read, all_events = observe(store._db, lambda: store.events_since(limit=events + 1))
        page, page_events = observe(store._db, lambda: store.events_since(limit=40))
        return {"fixture_messages": messages, "fixture_events": events,
                "full_messages": {**full, "rows_materialized": len(all_messages)},
                "bounded_tail": {**tail, "rows_materialized": len(tail_messages)},
                "tail_matches_reference": [m.id for m in tail_messages] == [m.id for m in all_messages[-40:]],
                "list_sessions": {**listing, "rows_returned": len(sessions)},
                "fork": {**fork, "rows_copied": len(store.messages(child.id))},
                "all_events": {**event_read, "rows_materialized": len(all_events)},
                "event_page": {**page, "rows_materialized": len(page_events)}}
    finally:
        store.close()


def bus_measurements(folder: Path, rows: int) -> dict:
    from exo.bus.store import Store
    store = Store(folder / "bus.sqlite")
    try:
        # Query-only fixtures deliberately bypass signatures; these rows never leave this database.
        store.db.executemany("INSERT INTO log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (i + 1, f"synthetic-{i}", "", "", "synthetic", '["group"]', "bench", "note",
             "synthetic searchable text", None, "[]", "", "", "") for i in range(rows)])
        inbox, found = observe(store.db, lambda: store.inbox(["group"], limit=40))
        search, matches = observe(store.db, lambda: store.search("searchable", limit=40))
        queries = {
            "inbox": ("SELECT seq FROM log WHERE seq > ? AND EXISTS "
                      "(SELECT 1 FROM json_each(log.to_json) WHERE value = ?) ORDER BY seq LIMIT ?", (0, "group", 40)),
            "search": ("SELECT seq FROM log WHERE enc_json IS NULL AND "
                       "(body LIKE ? OR thread LIKE ? OR from_seat LIKE ?) ORDER BY seq DESC LIMIT ?",
                       ("%searchable%", "%searchable%", "%searchable%", 40))}
        plans = {key: [r[3] for r in store.db.execute("EXPLAIN QUERY PLAN " + query, args)]
                 for key, (query, args) in queries.items()}
        return {"fixture_rows": rows, "inbox": {**inbox, "rows_returned": len(found)},
                "search": {**search, "rows_returned": len(matches)}, "query_plans": plans,
                "query_plan_projection": "seq only; timed calls use the production methods"}
    finally:
        store.db.close()


def ledger_child(folder: Path, mode: str) -> dict:
    # Only used by this script's disposable subprocesses; never use a default Exo home.
    if mode not in {"prepare", "1", "2", "check"}:
        raise ValueError("unknown ledger fixture operation")
    os.environ["EXO_HOME"] = str(folder / "home")
    from exo import crypto, ledger
    signer = crypto.Signer("synthetic-benchmark", crypto.keygen())
    led = ledger.Ledger(folder / "ledger.sqlite", signer)
    try:
        if mode == "prepare":
            led.append("note", {"text": "synthetic baseline"})
            return {"prepared": True}
        if mode in {"1", "2"}:
            count, write = 0, ledger._atomic_write

            def crash_at_anchor(*args, **kwargs):
                nonlocal count
                count += 1
                if count == int(mode):
                    os._exit(73)  # abrupt own-process exit, no cleanup or fixture deletion
                return write(*args, **kwargs)

            ledger._atomic_write = crash_at_anchor
            led.append("note", {"text": "synthetic interrupted append"})
            raise AssertionError("anchor crash point was not reached")
        before = led.verify()
        led.append("note", {"text": "synthetic recovery"})
        after = led.verify()
        return {"before": {k: before[k] for k in ("ok", "checked", "anchored", "anchored_seq")},
                "after": {k: after[k] for k in ("ok", "checked", "anchored", "anchored_seq")}}
    finally:
        led.close()


def ledger_measurements() -> list[dict]:
    results = []
    for anchor in (1, 2):
        with tempfile.TemporaryDirectory(prefix="exo-ledger-benchmark-") as tmp:
            env = {**os.environ, "EXO_HOME": str(Path(tmp) / "home"), "PYTHONPATH": str(ROOT / "src")}
            command = [sys.executable, str(Path(__file__).resolve()), "--ledger-child", tmp]
            subprocess.run([*command, "prepare"], env=env, check=True, capture_output=True, timeout=30)
            killed = subprocess.run([*command, str(anchor)], env=env, capture_output=True, timeout=30)
            if killed.returncode != 73:
                raise RuntimeError(f"anchor {anchor}: crash point was not reached")
            checked = subprocess.run([*command, "check"], env=env, check=True, capture_output=True, timeout=30)
            results.append({"exit_before_anchor_write": anchor, "exit_code": killed.returncode,
                            **json.loads(checked.stdout)})
    return results


def snapshot(messages: int, events: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="exo-optimization-") as tmp:
        folder = Path(tmp)
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        return {"schema": 1,
                "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "python": platform.python_version(), "platform": sys.platform,
                "scope": "synthetic local baseline; no optimization or power-loss claim",
                "source": source_measurements(ROOT), "assets": asset_measurements(ROOT),
                "ci_observations": {"pytest_command_sites": sum("pytest" in x and not x.lstrip().startswith('#')
                                                                 for x in ci.splitlines()),
                                    "enable_cache_mentioned": "enable-cache" in ci,
                                    "durations_mentioned": "--durations" in ci},
                "sessions": session_measurements(folder, messages, events),
                "bus": bus_measurements(folder, events), "ledger_crash_boundaries": ledger_measurements()}


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--ledger-child":
        print(json.dumps(ledger_child(Path(sys.argv[2]), sys.argv[3])))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=200)
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--compare", type=Path, help="fail if a baseline public name disappears")
    args = parser.parse_args()
    if not (1 <= args.messages <= 10000 and 1 <= args.events <= 100000):
        parser.error("messages must be 1..10000 and events 1..100000")
    report = snapshot(args.messages, args.events)
    if args.compare:
        report["removed_public_names"] = removed_names(json.loads(args.compare.read_text()), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(bool(report.get("removed_public_names")))


if __name__ == "__main__":
    raise SystemExit(main())
