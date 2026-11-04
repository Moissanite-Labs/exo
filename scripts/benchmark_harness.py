#!/usr/bin/env python3
"""Offline composition footprint; use the project's installed Python environment.

    .venv/bin/python scripts/benchmark_harness.py --samples 3 --json

Each sample uses a fresh interpreter and disposable Exo home. No model turns or
services are started. This measures internal footprint, not task quality or a
competitive benchmark. OS filesystem caches are not flushed between samples.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("lean", "coding", "full")


def git_sha() -> str | None:
    """Read local Git metadata without running commands, including worktrees."""
    try:
        folder = ROOT / ".git"
        if folder.is_file():
            folder = (ROOT / folder.read_text().strip().removeprefix("gitdir: ")).resolve()
        head = (folder / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head if len(head) == 40 else None
        ref = head.removeprefix("ref: ")
        common = folder
        if (folder / "commondir").exists():
            common = (folder / (folder / "commondir").read_text().strip()).resolve()
        for base in (folder, common):
            path = base / ref
            if path.is_file():
                return path.read_text().strip()
        for line in (common / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    except OSError:
        pass
    return None


def source_digest() -> str:
    """Fingerprint live runtime sources because a checkout may have uncommitted edits."""
    digest = hashlib.sha256()
    paths = [Path(__file__).resolve()]
    for folder in (ROOT / "src", ROOT / "plugins"):
        paths.extend(p for p in folder.rglob("*") if p.is_file()
                     and p.suffix in {".py", ".yaml", ".toml"} and "__pycache__" not in p.parts)
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def worker_environment(home: Path) -> dict[str, str]:
    # Preserve the user's HOME as-is, but direct every Exo data/key location to the
    # disposable home. Do not inherit provider credentials, EXO_* policies or seats.
    inherited = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR",
                 "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
    env = {key: os.environ[key] for key in inherited if key in os.environ}
    (home / "bin").mkdir()
    env.update(EXO_HOME=str(home), EXO_PRINCIPAL="benchmark", EXO_STORE=str(home / "principal"),
               EXO_PLUGIN_DIRS=str(ROOT / "plugins"), EXO_KEYS_FILE=str(home / "keys.env"),
               EXO_PEERS_DIR=str(home / "peers"), EXO_NO_KEYCHAIN="1", EXO_NO_SECRET_SERVICE="1",
               PYTHONHASHSEED="0", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
               TMPDIR=str(home), PATH=str(home / "bin"), EXO_BENCHMARK_WORKER="1")
    return env


def offline_guard(event: str, args: tuple) -> None:
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen",
                 "os.system", "os.posix_spawn", "os.exec", "os.fork"}:
        raise RuntimeError(f"offline benchmark forbids {event}")


def worker(profile: str) -> dict:
    if os.environ.get("EXO_BENCHMARK_WORKER") != "1":
        raise RuntimeError("worker mode is internal; run without --worker to allocate a disposable home")
    home = Path(os.environ["EXO_HOME"])
    if (home / "config.json").exists():
        raise RuntimeError("benchmark worker refuses an existing Exo configuration")
    sys.addaudithook(offline_guard)
    sys.path.insert(0, str(ROOT / "src"))
    from exo.compose import create
    from exo.store import PrincipalStore

    store = home / "principal"
    PrincipalStore.create(store)  # stock template, no user memory; excluded from compose timer
    config = {
        "principal": "benchmark", "principals": {"benchmark": str(store)},
        "cwd": str(home), "harness": {"profile": profile},
        "models": {"fake": {"reply": "offline"}, "roles": {
            "main": ["fake:scripted"], "aux": ["fake:scripted"], "decision": []}},
        "plugin_config": {"exo-gate": {"principal_store": str(store),
                                         "key_path": str(home / "gate.key")}},
    }
    (home / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (home / "keys.env").touch(mode=0o600)
    started = time.perf_counter()
    engine = create()
    composed = time.perf_counter()
    try:
        failures = {name: plugin.error for name, plugin in engine.plugins.plugins.items() if not plugin.ok}
        if failures:
            raise RuntimeError(f"plugin load failed: {failures}")
        # Registered schemas, including unavailable tools, are stable without
        # probing installed CLIs. They are not a provider's exact request payload.
        schemas = engine.registry.schemas(available_only=False)
        schema_chars = len(json.dumps(schemas, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        result = {"profile": profile, "compose_ms": (composed - started) * 1000,
                  "registered_tools": len(schemas), "schema_chars": schema_chars,
                  "schema_tokens_approx": math.ceil(schema_chars / 4),
                  "loaded_plugins": sorted(engine.plugins.plugins)}
    finally:
        close_started = time.perf_counter()
        asyncio.run(engine.close())
    result["close_ms"] = (time.perf_counter() - close_started) * 1000
    result["compose_close_ms"] = result["compose_ms"] + result["close_ms"]
    if sys.platform in ("linux", "darwin"):
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_rss_mib"] = peak / (1024 if sys.platform == "linux" else 1024 * 1024)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=3, help="fresh processes per profile (default: 3)")
    parser.add_argument("--json", action="store_true", help="emit metadata, all samples and median summaries as JSON")
    parser.add_argument("--worker", choices=PROFILES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        # Incidental plugin stdout must not corrupt the machine-readable sample.
        captured = io.StringIO()
        with redirect_stdout(captured):
            result = worker(args.worker)
        print(json.dumps(result, sort_keys=True))
        return 0
    if not 1 <= args.samples <= 100:
        parser.error("--samples must be between 1 and 100")
    fingerprint = source_digest()
    rows = []
    for index in range(args.samples):
        # Rotate order to reduce systematic filesystem-cache/order advantage.
        order = PROFILES[index % len(PROFILES):] + PROFILES[:index % len(PROFILES)]
        for profile in order:
            with tempfile.TemporaryDirectory(prefix="exo-footprint-") as directory:
                home = Path(directory)
                start = time.perf_counter()
                try:
                    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", profile],
                                          env=worker_environment(home), cwd=home, capture_output=True,
                                          text=True, timeout=60, check=True)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    print(f"benchmark failed for {profile}: {exc}\n{exc.stderr or ''}", file=sys.stderr)
                    return 1
                row = json.loads(done.stdout)
                row.update(sample=index + 1, process_ms=(time.perf_counter() - start) * 1000)
                rows.append(row)
    if source_digest() != fingerprint:
        print("Runtime sources changed during measurement; rerun against a stable checkout.", file=sys.stderr)
        return 1
    summaries = {}
    for profile in PROFILES:
        selected = [r for r in rows if r["profile"] == profile]
        metrics = ("compose_ms", "close_ms", "compose_close_ms", "process_ms", "registered_tools",
                   "schema_chars", "schema_tokens_approx", "peak_rss_mib")
        summaries[profile] = {key: statistics.median(r[key] for r in selected)
                              for key in metrics if key in selected[0]}
    report = {"schema_version": 1, "measured_at": datetime.now(timezone.utc).isoformat(),
              "git_sha": git_sha(), "source_sha256": fingerprint, "python": sys.version,
              "platform": platform.platform(), "samples_per_profile": args.samples,
              "scope": "Offline compose/close only; no model turns, tools, service startup or external connections.",
              "notes": ["Fresh interpreter and template-only principal store per sample; OS caches are not flushed.",
                        "Compose excludes imports/store setup; process wall time includes imports/setup/measurement.",
                        "PATH is an empty temporary directory: no installed CLI detection/probes run.",
                        "Registered schemas include unavailable tools; tokens approximate schema characters / 4.",
                        "Peak RSS covers the entire child process; source digest identifies live uncommitted code."],
              "medians": summaries, "samples": rows}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Offline harness footprint | {report['platform']} | Python {platform.python_version()}")
        print(f"Git {report['git_sha']} | source {report['source_sha256'][:12]} | {args.samples} samples/profile")
        print("profile  compose+close ms  process ms  tools  schema chars  approx tokens  peak RSS MiB")
        for profile, row in summaries.items():
            rss = f"{row['peak_rss_mib']:.1f}" if "peak_rss_mib" in row else "n/a"
            print(f"{profile:<7}  {row['compose_close_ms']:>16.1f}  {row['process_ms']:>10.1f}  "
                  f"{row['registered_tools']:>5g}  {row['schema_chars']:>12g}  "
                  f"{row['schema_tokens_approx']:>13g}  {rss:>12}")
        print("Medians only; use --json for samples and metadata. No task-quality or full-startup claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
