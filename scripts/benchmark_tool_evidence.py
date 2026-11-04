#!/usr/bin/env python3
"""Offline retained-output fixtures and enabled/disabled composition cost.

Run with the project's Python: scripts/benchmark_tool_evidence.py --samples 5.
No providers, external services or tools are executed. Output is JSON.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

from benchmark_harness import git_sha, offline_guard, source_digest, worker_environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def fixtures() -> dict[str, str]:
    return {
        "build_log": "[resource build.log]\n" + "step completed\n" * 4000 + "error: bad target\n[exit 1]\n",
        "json": json.dumps({"rows": [{"id": n, "value": f"sample {n}"} for n in range(1000)]}),
        "diff": "diff --git a/file b/file\n" + "@@ -1 +1 @@\n-before\n+after\n" * 1000,
        "malformed_text": "{incomplete\x00\ufffd🙂\n" * 1000,
        "distinct_errors": "\n".join(f"error {n}: failed distinct item" for n in range(1000)),
    }


def fixture_metrics() -> list[dict]:
    from exo.core.sessions import SessionStore
    from exo.core.types import Message, ToolCall, ToolResult
    from exo.tool_evidence import Evidence, Options
    out = []
    for name, text in fixtures().items():
        with tempfile.TemporaryDirectory(prefix="exo-evidence-fixture-") as folder:
            path = Path(folder) / "sessions.sqlite"
            store = SessionStore(path)
            sid = store.create_session(agent="default").id
            evidence = Evidence(store, Options(enabled=True), "benchmark", "default")
            evidence.begin(sid)
            metadata_bytes = len(json.dumps(store.get_meta(sid), ensure_ascii=False).encode())
            a = store.append_message(sid, Message.assistant(tool_calls=[ToolCall("call", "build", {})]))
            b = store.append_message(sid, Message.tool([ToolResult.of_text("call", text)]))
            before = sum(p.stat().st_size for p in Path(folder).iterdir())
            times, cpu_times, rendered = [], [], []
            for _ in range(100):
                started, cpu = time.perf_counter(), time.process_time()
                rendered = evidence.render(sid, [a, b])
                times.append((time.perf_counter() - started) * 1000)
                cpu_times.append((time.process_time() - cpu) * 1000)
            after = sum(p.stat().st_size for p in Path(folder).iterdir())
            short = rendered[-1].tool_results[0].text()
            assert store.messages(sid)[-1].tool_results[0].text() == text
            out.append({"fixture": name, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "source_chars": len(text), "view_chars": len(short),
                        "saved_percent": round(100 * (1 - len(short) / len(text)), 2),
                        "render_median_ms": statistics.median(times), "render_max_ms": max(times),
                        "render_cpu_median_ms": statistics.median(cpu_times),
                        "render_storage_growth_bytes": after - before, "session_metadata_bytes": metadata_bytes})
            store.close()
    return out


def startup(enabled: bool) -> dict:
    import os
    if os.environ.get("EXO_BENCHMARK_WORKER") != "1":
        raise RuntimeError("startup mode requires a disposable benchmark home")
    sys.addaudithook(offline_guard)
    from exo.compose import create
    from exo.config import Config
    home = Path(os.environ["EXO_HOME"])
    Config({"tool_output": {"enabled": enabled}, "core_plugins": [],
            "learn": {"enabled": False}, "jobs": {"enabled": False}, "delegation": {"enabled": False},
            "peers": {"enabled": False}, "schedule": False,
            "models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}}, home).save()
    started = time.perf_counter()
    engine = create()
    composed = time.perf_counter()
    tools = len(engine.registry.names())
    imported = "exo.tool_evidence" in sys.modules
    asyncio.run(engine.close())
    return {"enabled": enabled, "compose_ms": (composed - started) * 1000,
            "compose_close_ms": (time.perf_counter() - started) * 1000,
            "registered_tools": tools, "feature_imported": imported}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--startup", choices=("on", "off"), help=argparse.SUPPRESS)
    parser.add_argument("--fixtures", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.startup:
        print(json.dumps(startup(args.startup == "on")))
        return
    if args.fixtures:
        sys.addaudithook(offline_guard)
        print(json.dumps(fixture_metrics()))
        return
    if not 1 <= args.samples <= 30:
        parser.error("samples must be between 1 and 30")
    rows = []
    for number in range(args.samples):
        for mode in (("on", "off") if number % 2 else ("off", "on")):
            with tempfile.TemporaryDirectory(prefix="exo-evidence-startup-") as folder:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--startup", mode],
                    env=worker_environment(Path(folder)), capture_output=True, text=True, timeout=30, check=True)
                rows.append(json.loads(result.stdout))
    with tempfile.TemporaryDirectory(prefix="exo-evidence-fixtures-") as folder:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--fixtures"],
            env=worker_environment(Path(folder)), capture_output=True, text=True, timeout=60, check=True)
        measured_fixtures = json.loads(result.stdout)
    module = ROOT / "src" / "exo" / "tool_evidence.py"
    print(json.dumps({"measured_at": datetime.now(timezone.utc).isoformat(), "base_commit": git_sha(),
        "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(), "runtime_sha256": source_digest(),
        "python": platform.python_version(), "platform": platform.platform(), "samples": rows, "fixtures": measured_fixtures,
        "scope": "Offline synthetic fixtures; composition without provider calls or service start. No task-quality claim."},
        indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
