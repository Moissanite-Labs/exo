# Lifted from NousResearch/hermes-agent tools/environments/local.py and tools/environments/base.py @ dce1e9b37581dd62e480a9064dc04a709c2940d3 (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.
"""bash tool: run a command with a timeout, a working folder and an output cap.

The child runs in its own session (``start_new_session``), so on timeout or
cancel the whole process group is torn down: TERM, a short grace wait on the
group (not just the wrapper, which can exit before grandchildren), then KILL.
Lifted from Hermes's ``_kill_process_group_posix`` / ``_wait_for_group_exit``
and the adaptive poll loop of ``_wait_for_process``; Exo drops psutil, the
PID-reuse fingerprint guard, snapshots and the Windows path.
"""

from __future__ import annotations

import codecs
import contextlib
import os
import shutil
import signal
import subprocess
import threading
import tempfile
import time
from typing import Any, Callable, Iterable

from exo.core.tools.registry import Registry, ToolContext, ToolError

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
OUTPUT_CAP = 30_000  # chars kept: head and tail halves around a cut marker
TERM_GRACE = 1.0


class _Capped:
    """Head and tail window over a stream so a chatty command cannot exhaust memory."""

    def __init__(self, cap: int) -> None:
        self.cap, self.head, self.tail, self.total = cap, "", "", 0

    def add(self, text: str) -> None:
        self.total += len(text)
        half = self.cap // 2
        if len(self.head) < half:
            take = half - len(self.head)
            self.head += text[:take]
            text = text[take:]
        if text:
            self.tail = (self.tail + text)[-half:]

    def render(self) -> str:
        dropped = self.total - len(self.head) - len(self.tail)
        if dropped <= 0:
            return self.head + self.tail
        return f"{self.head}\n[... {dropped} chars cut ...]\n{self.tail}"


def _drain(proc: subprocess.Popen, out: _Capped, emit: Callable[[str], None] | None) -> None:
    """Read the output as one UTF-8 stream, so a character split across reads stays whole. ``emit`` gets it with
    known key values cut out across reads too: a tail that could start a key waits for the next read or the end."""
    from exo.core.models.keys import known_secret_values, redact_secrets
    assert proc.stdout is not None
    vals: list[str] = []
    with contextlib.suppress(Exception):  # redaction never stops the drain: an unread pipe would stall the command
        vals = known_secret_values() if emit else []
    dec, held, eof = codecs.getincrementaldecoder("utf-8")("replace"), "", False
    while not eof:
        chunk = proc.stdout.read1(65536)  # type: ignore[union-attr]
        eof = not chunk
        text = dec.decode(chunk, final=eof)
        out.add(text)
        if emit:  # cut the raw text, then redact what goes out: a key inside a longer key is not cut out first
            raw = held + text
            cut = len(raw) if eof else _safe_cut(raw, vals)
            held, text = raw[cut:], redact_secrets(raw[:cut], vals)
            if text:
                with contextlib.suppress(Exception):
                    emit(text)


def _safe_cut(raw: str, vals: list[str]) -> int:
    """Where ``raw`` may split so the stream equals whole-output redaction, whatever output follows.

    The stream and the whole output take their spans from the same ``secret_spans``, which finds each value's
    matches in the output itself, never across a ``[redacted]`` marker. When no span of the whole output crosses
    the cut, the text before the cut gives the same spans alone, and so does the text after it. So the cut must be
    a point that no span can cross, whatever output follows.

    The cut starts before a tail that could start a key, so every match that later output adds starts at or after
    it. Then ``secret_spans`` runs with the cut as ``end``, value by value in redaction order. Before each value,
    the earlier values' spans in the whole output that start before the cut are the ones taken here, and they stop
    at or before it. So a match that stops at or before the cut is taken or skipped as in the whole output. A
    value's first match that stops after the cut moves the cut back to its start, if it starts before it, and the
    value's later matches start after that. That keeps the rule true for the next value. Each value moves the cut at
    most once, by less than its length, so the held text stays under twice the length of all values together."""
    from exo.core.models.keys import secret_spans
    return secret_spans(raw, vals, len(raw) - _key_start(raw, vals))[1]


def _key_start(text: str, vals: list[str]) -> int:
    """Length of the longest tail of ``text`` that is the start of a value in ``vals``, and shorter than it.

    Only a place that holds a value's first character can start such a tail. So ``find`` steps through those places
    alone, and only through the ones that would give a longer tail than the best so far. A read then costs a few
    ``find`` calls per value, not one test per place in the last ``len(v)`` characters."""
    n, best = len(text), 0
    for v in vals:
        i = text.find(v[:1], max(n - len(v) + 1, 0), n - best)
        while i >= 0 and not v.startswith(text[i:]):
            i = text.find(v[:1], i + 1, n - best)
        best = n - i if i >= 0 else best
    return best


def _wait_for_group_exit(proc: subprocess.Popen, pgid: int, timeout: float) -> bool:
    """Wait until the group is gone, reaping the wrapper (an unreaped leader keeps it alive)."""
    deadline = time.monotonic() + timeout
    while True:
        with contextlib.suppress(Exception):
            proc.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # exists, or only zombies remain (macOS answers EPERM there)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def kill_process_group(proc: subprocess.Popen, grace: float = TERM_GRACE) -> None:
    """TERM the child's process group, wait ``grace`` seconds, then KILL it."""
    pgid = proc.pid  # every spawner uses start_new_session, so the child leads its own group
    try:
        os.killpg(pgid, signal.SIGTERM)
        if not _wait_for_group_exit(proc, pgid, grace):
            os.killpg(pgid, signal.SIGKILL)
            _wait_for_group_exit(proc, pgid, 2.0)
    except ProcessLookupError:
        pass
    except PermissionError:
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        proc.wait(timeout=0.5)


def tool_env(base: dict[str, str] | None = None, allow: Iterable[str] = (), deny: Iterable[str] = ()) -> dict[str, str]:
    """The environment a tool subprocess gets (bash and background jobs): ``exo.envscrub.scrub_env`` (D-039),
    so no provider key, token, cloud credential, bus identity or core socket/credential, and none of the shell startup
    hooks ``envscrub.EXEC_HOOKS`` names (``BASH_ENV``, ``ENV``, exported functions, ``SHELLOPTS``, ``BASHOPTS``, ``PS4``);
    loader and interpreter hooks pass (docs/TOOLS.md). ``allow`` and ``deny`` are the seat's ``[tools] env_allow`` and
    ``env_deny``. Only a copy is filtered: model providers read the real environment and keep their keys."""
    from exo.envscrub import scrub_env

    out = scrub_env(base, allow, deny)
    out.update({"PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb"})
    return out


# Exit codes of a pipeline. bash reports only the last stage, so `false | head -1` looks like success. We do not
# turn on pipefail (it would make `grep | head` fail on SIGPIPE). Instead an EXIT trap writes the final pipeline's
# PIPESTATUS to a side file, and the tool result gets one note line when an earlier stage failed while the
# last one succeeded. Only the command's final pipeline is seen: a DEBUG trap would see more but resets
# PIPESTATUS for the user's own commands.
MASKED_PREFIX = "note: pipeline stage"
_TRAP = ('case ${#PIPESTATUS[@]} in 1) ;; *) printf "%s\\n" "${PIPESTATUS[*]}" 2>/dev/null >&FD ;; esac')


def wrap_pipestatus(command: str, fd: int, env: dict[str, str]) -> list[str]:
    """The argv that runs ``command`` under bash with an EXIT trap writing PIPESTATUS to ``fd`` (pass it in
    pass_fds). The trap text travels in ``env`` (EXO_PIPE_TRAP) so the prefix stays short in bash's own syntax-error
    messages. (An ``eval`` wrapper would hide the command's PIPESTATUS from the trap.) bash gets ``--norc``: with
    SSH_CLIENT or SSH2_CLIENT set and SHLVL unset or 0, ``bash -c`` would source ~/.bashrc and skip an allowed
    BASH_ENV (#135); ``--norc`` still honours BASH_ENV. The /bin/sh fallback reads no rc file."""
    env["EXO_PIPE_TRAP"] = _TRAP.replace("FD", str(fd))
    bash = shutil.which("bash")
    return [*([bash, "--norc"] if bash else ["/bin/sh"]), "-c", 'trap "$EXO_PIPE_TRAP" EXIT; ' + command]


def open_side() -> tuple[int, str]:
    """A private temp file for the trap to append to; returns (fd, path). The caller closes the fd."""
    return tempfile.mkstemp(prefix="exo-pipe-")


def masked_note(path: str) -> str:
    """One note line when the recorded pipeline had a failed earlier stage (not 141, SIGPIPE) and a successful
    last stage; otherwise an empty string. Removes the side file."""
    try:
        with open(path, "rb") as f:
            raw = f.read(4096).decode("ascii", "replace")
    except OSError:
        return ""
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    for line in raw.splitlines():
        try:
            codes = [int(x) for x in line.split()]
        except ValueError:
            continue
        if len(codes) > 1 and codes[-1] == 0:
            for i, c in enumerate(codes[:-1], 1):
                if c not in (0, 141):
                    return (f"{MASKED_PREFIX} {i} exited {c} (only the last stage's code is reported as the "
                            "result)")
    return ""


def raise_nofile_limit(cap: int = 10240) -> int | None:
    """Raise the soft open-files limit toward the hard limit (at most ``cap``) so child tools inherit it.
    macOS starts at 256. Returns the new soft limit, or None when nothing changed or it is not possible."""
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = cap if hard == resource.RLIM_INFINITY else min(hard, cap)
        if soft != resource.RLIM_INFINITY and soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            return want
    except (ImportError, ValueError, OSError):
        pass
    return None


def run_command(command: str, cwd: str, timeout: float = DEFAULT_TIMEOUT, cap: int = OUTPUT_CAP,
                cancel: threading.Event | None = None,
                emit: Callable[[str], None] | None = None, env_allow: Iterable[str] = (),
                env_deny: Iterable[str] = (), github_auth: tuple[str, str] | None = None,
                *, redact_output: bool = True) -> tuple[int, str, str]:
    """Run ``command`` under bash. Returns ``(exit_code, output, status)``.

    status is ``ok``, ``timeout`` (exit 124) or ``interrupted`` (exit 130).
    stdout and stderr are merged in arrival order. Bound credentials are redacted by default;
    the Registry-owned handler defers final-output redaction to its strict result boundary.
    """
    side_fd, side_path = open_side()
    try:
        env = tool_env(allow=env_allow, deny=env_deny)
        if github_auth:
            from exo.core.github_auth import child_env
            env = child_env(env, *github_auth, deny=env_deny, cwd=str(cwd))
            from exo.core.credentials import child_env as credential_env
            env = credential_env(env, *github_auth, consumer="bash", deny=env_deny)
        proc = subprocess.Popen(
            wrap_pipestatus(command, side_fd, env), cwd=cwd, stdin=subprocess.DEVNULL, pass_fds=(side_fd,),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, env=env)
    except BaseException:
        os.close(side_fd)
        with contextlib.suppress(OSError):
            os.unlink(side_path)
        raise
    os.close(side_fd)
    out = _Capped(cap)
    drain = threading.Thread(target=_drain, args=(proc, out, emit), daemon=True)
    drain.start()
    deadline = time.monotonic() + timeout
    status = "ok"
    sleep = 0.005  # adaptive poll: fast commands return fast, long ones cost little CPU
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                status = "interrupted"
                break
            if time.monotonic() > deadline:
                status = "timeout"
                break
            time.sleep(sleep)
            sleep = min(sleep * 1.5, 0.2)
    except BaseException:
        kill_process_group(proc)
        raise
    if status != "ok":
        kill_process_group(proc)
    else:
        # The wrapper exited; reap any background stragglers still holding the pipe open.
        drain.join(timeout=2)
        if drain.is_alive():
            kill_process_group(proc, grace=0.2)
    drain.join(timeout=2)
    with contextlib.suppress(Exception):
        proc.stdout.close()  # type: ignore[union-attr]
    code = {"timeout": 124, "interrupted": 130}.get(status, proc.returncode or 0)
    note = masked_note(side_path)
    if note and status == "ok":
        out.add("\n" + note)
    rendered = out.render()
    if redact_output and github_auth:
        from exo.core.models.keys import redact_secrets
        rendered = redact_secrets(rendered)
    return code, rendered, status


def _start_job(args: dict[str, Any], ctx: ToolContext, cwd: Any) -> str:
    """``background: true`` (D-032): hand the command to the job manager and return at once. The gate has already
    classified the command exactly as a foreground call; only how it runs differs."""
    jobs = ctx.meta.get("jobs")
    if jobs is None:
        raise ToolError("background jobs are not available in this session; run the command in the foreground")
    job = jobs.start(str(args["command"]), str(cwd), ctx.session, float(args["timeout"]) if args.get("timeout") else None)
    return (f"Started background job {job['id']} (pid {job['pid']}). Output goes to {job['log']}. It keeps running if "
            "this turn ends or is interrupted, and is stopped only by `job_kill`; the wall limit is "
            f"{int(job['wall_s'])}s. You will get a job notice here when it ends, so do not poll or wait: carry on "
            "with other work. `jobs` lists jobs, `job_output` reads output.")


def timeout_arg(args: dict[str, Any], check: bool = True) -> float:
    """The ``timeout`` argument in seconds (default DEFAULT_TIMEOUT); rejected outside 1..MAX_TIMEOUT when ``check``."""
    timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)
    if check and not 0 < timeout <= MAX_TIMEOUT:
        raise ToolError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    return timeout


def bash(args: dict[str, Any], ctx: ToolContext) -> str:
    background = args.get("background") is True
    timeout = timeout_arg(args, check=not background)
    cwd = ctx.resolve(args.get("cwd") or ".")
    if not cwd.is_dir():
        raise ToolError(f"cwd is not a folder: {cwd}")
    if background:
        return _start_job(args, ctx, cwd)
    code, output, status = run_command(args["command"], str(cwd), timeout,
                                       cancel=ctx.cancel, emit=ctx.emit,
                                       env_allow=ctx.meta.get("env_allow") or (),
                                       env_deny=ctx.meta.get("env_deny") or (),
                                       github_auth=ctx.meta.get("github_auth"), redact_output=False)
    output = output.rstrip("\n")
    if status == "timeout":
        output += f"\n[Command timed out after {timeout:g}s; process group killed]"
    elif status == "interrupted":
        output += "\n[Command interrupted]"
    return f"{output.lstrip() if output.strip() else '(no output)'}\n[exit {code}]"


def register(reg: Registry) -> None:
    reg.tool("bash", "Run a shell command with bash -c. stdout and stderr are merged; long output "
             f"keeps the head and tail. Default timeout {DEFAULT_TIMEOUT}s (max {MAX_TIMEOUT}; larger "
             "values are rejected); on timeout the whole process group is killed. Not interactive: "
             "stdin is closed. macOS has no `timeout` command, so use the timeout parameter. For a command that "
             "takes longer than a couple of minutes (a build, a server, a long test run) pass `background: true`: "
             "it returns a job id at once, keeps running if the turn ends, and you get a notice when it exits "
             "(see the jobs, job_output and job_kill tools).",
             {"type": "object", "properties": {
                 "command": {"type": "string"},
                 "timeout": {"type": "number", "description": f"Seconds before the command is killed (max {MAX_TIMEOUT}; larger values are rejected)"},
                 "cwd": {"type": "string", "description": "Working folder (default session cwd)"},
                 "background": {"type": "boolean", "description": "Run as a background job and return a job id at "
                                "once. `timeout` then means the job's wall limit (default and maximum set by the "
                                "seat, 2 h)."}},
              "required": ["command"], "additionalProperties": False}, risk="high", toolset="shell")(bash)
