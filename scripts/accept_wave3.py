#!/usr/bin/env python3
"""Wave 3 live acceptance (docs/WAVE-3.md, Acceptance 2). Stdlib plus exo only.

Runs a real `exo serve` seat in a fresh temp EXO_HOME on OpenCode Go and checks: a diff panel, an edit
verdict that reaches the ledger and the fast loop, a scheduled job, and a gated tool call whose decision
is in the signed ledger. Real keys are read by Exo itself (env, Keychain service `exo`); this script never
reads, prints or logs a key. ANTHROPIC_API_KEY is dropped from the server environment.

    .venv/bin/python scripts/accept_wave3.py [--keep] [--main REF] [--aux REF] [--fast REF]

Model turns are counted as usage.updated events (one per main-model call). Reviewer, scheduled-job and
decision-model calls are listed separately where they can be seen.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import exo
from exo.ledger import Ledger
from exo.protocol import schema as s
from exo.protocol.client import Client

REPO = Path(exo.__file__).resolve().parents[2]
PID = "accept"
SEAT = "w3seat"
CLI = "import sys; from exo.cli import main; sys.exit(main(sys.argv[1:]))"
T0 = time.time()
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {note}", flush=True)


def log(msg: str) -> None:
    print(f"  {time.time() - T0:6.1f}s {msg}", flush=True)


class Events:
    """Collects every event from one stream and lets steps wait for one."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.items: list[s.Event] = []
        self.cond = asyncio.Condition()
        self.task = asyncio.create_task(self._run())
        self.approval_answer = "allow"  # what to say to approval.requested
        self.approvals: list[s.Event] = []

    async def _run(self) -> None:
        async for ev in self.client.events(0, reconnect=True):
            async with self.cond:
                self.items.append(ev)
                self.cond.notify_all()
            if ev.type == "approval.requested":
                self.approvals.append(ev)
                asyncio.create_task(self._answer(ev))

    async def _answer(self, ev: s.Event) -> None:
        try:
            await self.client.call(s.ApprovalRespond(request_id=ev.body.request_id, decision=self.approval_answer,
                                                     client="accept-script"))
        except Exception as exc:
            log(f"approval.respond failed: {exc}")

    async def wait(self, pred, timeout: float, start: int = 0):
        """First event at index >= start matching pred, or None after timeout."""
        deadline = time.time() + timeout
        i = start
        while True:
            async with self.cond:
                while i < len(self.items):
                    ev = self.items[i]
                    i += 1
                    if pred(ev):
                        return ev
                left = deadline - time.time()
                if left <= 0:
                    return None
                try:
                    await asyncio.wait_for(self.cond.wait(), left)
                except asyncio.TimeoutError:
                    return None

    def mark(self) -> int:
        return len(self.items)

    def of(self, typ: str, start: int = 0) -> list[s.Event]:
        return [e for e in self.items[start:] if e.type == typ]


def run_cli(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI, *args], env=env, capture_output=True, text=True, timeout=120)


def setup(home: Path, args) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "EXO_PRINCIPAL", "EXO_STORE",
                                                           "EXO_SEAT", "EXO_PRINCIPAL_STORE", "EXO_GATE_DECISION_MODEL")}
    env["EXO_HOME"] = str(home)
    env["EXO_PLUGIN_DIRS"] = str(REPO / "plugins")
    r = run_cli(env, "init", PID)
    if r.returncode != 0:
        raise SystemExit(f"exo init failed: {r.stderr.strip()[:300]}")
    store = home / "principals" / PID
    cfg_path = home / "config.json"
    cfg = json.loads(cfg_path.read_text("utf-8")) if cfg_path.exists() else {}
    cfg["models"] = {"roles": {"main": [args.main], "aux": [args.aux], "fast": [args.fast]}}
    cfg["learn"] = {"store": str(store)}
    cfg_path.write_text(json.dumps(cfg, indent=2), "utf-8")
    seats = home / "seats"
    seats.mkdir(exist_ok=True)
    (seats / f"{SEAT}.toml").write_text(
        f'[seat]\nname = "{SEAT}"\n\n[principal]\nid = "{PID}"\n\n[plugins]\nenabled = ["exo-gate"]\n', "utf-8")
    return env


def ledger_events(store: Path, kind=None) -> list[dict]:
    led = Ledger(store / "ledger.sqlite")
    try:
        return list(led.iter(kind=kind))
    finally:
        led.close()


def verify_ledger(store: Path) -> dict:
    led = Ledger(store / "ledger.sqlite")
    try:
        return led.verify()
    finally:
        led.close()


async def wait_socket(sock: Path, proc: subprocess.Popen, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        if sock.exists():
            try:
                async with Client(socket=str(sock)) as c:
                    if (await c.send(s.Hello(client="accept-w3"))).ok:
                        return True
            except Exception:
                pass
        await asyncio.sleep(0.5)
    return False


async def main_async(args, home: Path, env: dict, work: Path) -> None:
    store = home / "principals" / PID
    sock = home / "run" / "exod.sock"
    errlog = home / "serve.log"
    if len(str(sock).encode()) > 100:
        raise SystemExit(f"socket path too long: {sock}")
    proc = subprocess.Popen([sys.executable, "-c", CLI, "serve", "--seat", SEAT, "--socket", str(sock)],
                            env=env, stdout=subprocess.DEVNULL, stderr=open(errlog, "wb"), cwd=str(work))
    usage_calls = 0
    try:
        ok = await wait_socket(sock, proc)
        record("0 serve starts", ok, f"socket {sock}" if ok else f"see {errlog}")
        if not ok:
            return
        async with Client(socket=str(sock), timeout=60) as client:
            await client.call(s.Hello(client="accept-w3"))
            ev = Events(client)
            seat = await client.call(s.SeatList())
            log(f"seat active: {seat.get('active')}")
            sess = (await client.call(s.SessionCreate(title="wave3", cwd=str(work))))["session"]["id"]

            async def turn(text: str, timeout: float = 150) -> tuple[int, bool]:
                m = ev.mark()
                await client.call(s.TurnSend(session=sess, text=text))
                done = await ev.wait(lambda e: e.type == "turn.completed" and e.session == sess, timeout, m)
                return m, done is not None

            # ---- 2. Turn A: diff panel
            (work / "notes.txt").write_text("indent: use spaces\nname: exo\n", "utf-8")
            ev.approval_answer = "allow"
            mA, done = await turn(
                "Read the file notes.txt in the working directory. Then use the ui tool (action open, kind diff, "
                "title 'Proposed change', data {files:[{path:'notes.txt', diff:<unified diff text>}]}, and a plain "
                "text) to show a proposed one-line change to the first line, replacing spaces with tabs. "
                "After the panel is open, reply in one short sentence.")
            panels = [e for e in ev.of("ui.panel", mA) if e.body.action == "open"]
            diff = next((e for e in panels if e.body.kind == "diff" and (e.body.text or "").strip()), None)
            record("2 diff panel (turn A)", diff is not None,
                   f"panel {diff.body.panel_id}, text {len(diff.body.text)} chars" if diff else
                   f"turn done={done}, panels={[(e.body.kind, bool(e.body.text)) for e in panels]}")

            # ---- 3. ui.respond, edit verdict, fast loop
            if diff:
                r = await client.call(s.UiRespond(panel_id=diff.body.panel_id, action="acted",
                                                  value={"line": 1, "comment": "tabs, not spaces"}))
                log(f"ui.respond: {r}")
            fb = await client.call(s.FeedbackSend(
                session=sess, verdict="edit",
                text="Important correction: use tabs, NOT spaces. Always indent with tabs in this project."))
            verdicts = ledger_events(store, "verdict")
            record("3a feedback recorded", bool(fb.get("recorded")) and bool(verdicts),
                   f"result {fb}" if not fb.get("recorded") else f"event {fb.get('event')}, verdicts in ledger {len(verdicts)}, "
                   f"pair {fb.get('pair')}, review started {fb.get('review')}")
            end = time.time() + 90
            props: list[Path] = []
            beliefs: list[dict] = []
            while time.time() < end:
                props = [p for p in (store / "proposals").glob("*") if p.is_file()] if (store / "proposals").is_dir() else []
                beliefs = ledger_events(store, "belief_proposed")
                if props or beliefs:
                    break
                await asyncio.sleep(2)
            if props or beliefs:
                record("3b fast loop proposal", True, f"{len(props)} proposal file(s), {len(beliefs)} belief_proposed")
            else:
                notes = [e["body"] for e in ledger_events(store, "note") if (e["body"] or {}).get("type") == "learn_review"]
                logtxt = errlog.read_text("utf-8", "replace") if errlog.exists() else ""
                lines = [l for l in logtxt.splitlines() if "learn" in l.lower() or "fast loop" in l.lower()][-5:]
                notices = [e.body.text for e in ev.of("notice", mA)]
                record("3b fast loop proposal", False,
                       f"no proposal in 90 s. learn_review notes: {notes}; server log: {lines}; notices: {notices[-3:]}")

            # ---- 4. schedule
            m4 = ev.mark()
            job = None
            try:
                job = (await client.call(s.ScheduleEdit(action="add", when="every 1m", prompt="Reply with the word pong.",
                                                        title="w3 pong", tier="fast")))["job"]
                log(f"job {job.get('id')} next_run {job.get('next_run')}")
            except Exception as exc:
                record("4 schedule", False, f"schedule.edit add failed: {exc}")
            if job:
                done_ev = await ev.wait(lambda e: e.type == "task.updated" and e.body.status == "done", 150, m4)
                notice = await ev.wait(lambda e: e.type == "notice" and "pong" in (e.body.text or "").lower(), 30 if done_ev else 150, m4)
                record("4 schedule runs and reports", bool(done_ev and notice),
                       f"task.updated done={bool(done_ev)}, pong notice={bool(notice)}")
                try:
                    await client.call(s.ScheduleEdit(action="remove", job_id=job.get("id")))
                    left = (await client.call(s.ScheduleEdit(action="list"))).get("jobs", [])
                    log(f"job removed, {len(left)} jobs left")
                except Exception as exc:
                    log(f"remove failed: {exc}")

            # ---- 5. Turn B: gated rm -rf
            scratch = work / "scratch"
            scratch.mkdir(exist_ok=True)
            (scratch / "keep.txt").write_text("keep me\n", "utf-8")
            ev.approval_answer = "deny"
            mB, doneB = await turn(
                "Run exactly this shell command in the working directory: rm -rf ./scratch . "
                "Use the shell tool. If it is not allowed, say so in one sentence.", 150)
            reqs = ev.of("approval.requested", mB)
            record("5a approval requested", bool(reqs),
                   f"{len(reqs)} request(s): {reqs[0].body.summary[:100]!r}" if reqs else f"turn done={doneB}, no approval event")
            record("5b folder survives deny", scratch.is_dir() and (scratch / "keep.txt").exists(),
                   "scratch still exists" if scratch.is_dir() else "scratch is GONE")
            ver = verify_ledger(store)
            acts = [e for e in ledger_events(store, "action") if (e["body"] or {}).get("gate") == "exo-gate"]
            decisions = [e for e in acts if e["body"].get("phase") == "decision" and e["body"].get("outcome") in ("ask", "deny")
                         or (e["body"].get("phase") == "decision" and "rm" in json.dumps(e["body"].get("classification")))]
            approvals = [e for e in acts if e["body"].get("phase") == "approval"]
            denied = [e for e in approvals if e["body"].get("approved") is False]
            record("5c gate decision in signed ledger", bool(ver.get("ok")) and bool(decisions),
                   f"verify ok={ver.get('ok')} checked={ver.get('checked')}, gate decisions {len(acts)}, "
                   f"ask/deny decisions {len(decisions)}, approval(denied) events {len(denied)}"
                   + ("" if ver.get("ok") else f", verify error {ver}"))
            dm = [e["body"].get("decision_model") for e in acts if e["body"].get("decision_model")]
            routes = sorted({(d or {}).get("route", "?") for d in dm})
            if dm:
                jev = any(str(r).startswith("jev") for r in routes)
                record("5d decision model route", True, f"{len(dm)} decisions carry decision_model, routes {routes}"
                       + (" (Jev answered)" if jev else " (fallback, Jev did not answer)"))
            else:
                record("5d decision model route", False,
                       "no decision_model in any gate decision body: the fallback or a failure kept the rule outcome. "
                       + "; ".join(l for l in errlog.read_text("utf-8", "replace").splitlines() if "decision model" in l)[:300])

            # ---- 6. usage route
            ups = ev.of("usage.updated")
            usage_calls = len(ups)
            models = sorted({u.body.model for u in ups})
            record("6 usage.updated names the route", bool(ups) and all(m for m in models),
                   f"{usage_calls} usage events, model(s) {models}; main configured {args.main}")
            ev.task.cancel()
    finally:
        proc.terminate()
        try:
            proc.wait(15)
            record("7 server stops cleanly", proc.returncode in (0, -15), f"exit code {proc.returncode}")
        except subprocess.TimeoutExpired:
            proc.kill()
            record("7 server stops cleanly", False, "had to kill after 15 s")
    globals()["MODEL_TURNS"] = usage_calls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main", default="opencode-go:kimi-k3")
    ap.add_argument("--aux", default="opencode-go:glm-5.3-flash")
    ap.add_argument("--fast", default="opencode-go:glm-5.3-flash")
    ap.add_argument("--keep", action="store_true", help="keep the temp EXO_HOME")
    args = ap.parse_args()
    home = Path(tempfile.mkdtemp(prefix="exo-w3-", dir="/tmp"))
    work = home / "w"
    work.mkdir()
    print(f"EXO_HOME {home}", flush=True)
    try:
        env = setup(home, args)
        asyncio.run(main_async(args, home, env, work))
    finally:
        if args.keep:
            print(f"kept {home}")
        else:
            shutil.rmtree(home, ignore_errors=True)
    print("\nResult                               Status")
    for name, ok, note in RESULTS:
        print(f"{name:<36} {'PASS' if ok else 'FAIL'}   {note}")
    turns = globals().get("MODEL_TURNS", 0)
    print(f"\nmain-model turns (usage.updated events): {turns}")
    print(f"wall time: {time.time() - T0:.0f} s")
    return 0 if RESULTS and all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
