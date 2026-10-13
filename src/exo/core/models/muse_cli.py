"""Muse Code subscription through the ``muse`` CLI: one ``muse exec`` subprocess per call, no key in Exo.

Why a subprocess: the Muse login keeps its OAuth token in the OS keychain, owned by the ``muse`` binary. Exo
never reads, copies or stores it. Exo only starts ``muse exec`` and reads what it prints.

What this provider is not, said plainly:

- Text only. ``muse exec`` runs its own agent loop. Exo cannot hand it Exo's tools or stream tool calls out of
  it, so a call that carries tools is refused (status 501) and the router falls back to the next route.
- One process per turn, so every turn pays process start-up and a fresh session. History is flattened into one
  prompt. Output arrives when the run ends, not token by token.
- On some macOS setups an unsigned native ``muse`` cannot write the keychain at login (OSStatus -67068). A
  working unsigned binary or launcher is fine and is never checked or flagged up front. Only when a real call
  fails with that error does Exo report ``unsigned`` and suggest ad-hoc signing of the native binary
  (``codesign --force --sign - <path>``), not of a launcher script.
- ``META_API_KEY`` is removed from the child's environment. It would override the account login and bill an API
  account instead of the subscription.

The only file read is ``~/.config/muse/auth.json``, and only two non-secret fields from it (``mechanism`` and
``subs_active``, see ``login_state``). Nothing else from that file is kept, logged or returned.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

import httpx

from exo.core.models.base import StreamState, error_event
from exo.core.types import Message, ProviderEvent, ToolSpec

# Credentials and backend switches that would make the CLI bill an API account instead of the login.
DROP_ENV = ("META_API_KEY",)
PROBE_PROMPT = "Reply with the single word: ok"
NOT_SIGNED = "not signed at all"
DEFAULT_MODEL = "muse-spark-1.3"
# The CLI's own scale (``muse exec --help``): none|minimal|low|medium|high|xhigh|max|ultra, its default is high.
# Exo's levels use the same names, so they pass straight through. A one-shot call that does not ask for a level
# gets "low": quiet and quick, and Exo does not pay for deep thinking nobody requested.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_EFFORT = "low"
SIGN_FIX = "codesign --force --sign - "
CLEANUP_TIMEOUT = 2.0
MACHO_MAGICS = frozenset(bytes.fromhex(m) for m in (
    "feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca"))


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The CLI's environment (D-039 style): the minimal allowlisted set, never an API key. HOME stays so the
    CLI finds its own config and keychain."""
    from exo.envscrub import minimal_env

    return {k: v for k, v in minimal_env(os.environ if env is None else env).items() if k not in DROP_ENV}


def auth_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return Path(base) / "muse" / "auth.json"


def login_state(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``{"logged_in": bool, "subs_active": bool | None}`` from the non-secret fields of the Muse auth file.

    Reads ``providers.meta.mechanism`` and ``providers.meta.subs_active`` and nothing else; the rest of the file
    (which holds personal metadata) is dropped at once. Never touches the keychain, so a stale file can say
    "logged in" when the keychain entry is gone: the test call is what proves the login works."""
    try:
        meta = (json.loads(auth_path(env).read_text(encoding="utf-8")).get("providers") or {}).get("meta") or {}
        mechanism, subs = meta.get("mechanism"), meta.get("subs_active")
    except (OSError, ValueError, AttributeError):
        return {"logged_in": False, "subs_active": None}
    return {"logged_in": mechanism == "oauth", "subs_active": subs if isinstance(subs, bool) else None}


def unsigned_detail(exe: str) -> str:
    return (f"keychain write refused (unsigned binary); try ad-hoc signing of the native Muse binary, not a "
            f"launcher script: {SIGN_FIX}{exe}")


def classify_failure(returncode: int, stderr: str) -> str:
    """Map a failed ``muse exec`` to one of Exo's error classes. Looks at the text, never returns it."""
    low = (stderr or "").lower()
    if "-67068" in low or NOT_SIGNED in low:
        return "unsigned"
    if any(w in low for w in ("not logged in", "no credentials", "login required", "muse login")):
        return "no_key"
    if any(w in low for w in ("401", "403", "unauthor", "forbidden", "expired", "invalid token")):
        return "auth"
    if "429" in low or "rate limit" in low:
        return "rate_limited"
    if "402" in low or "subscription" in low or "quota" in low:
        return "billing"
    if any(w in low for w in ("connection", "network", "dns", "unreachable")):
        return "network"
    return "error"


def probe(exe: str, env: Mapping[str, str] | None = None, timeout: float = 60.0) -> dict[str, str]:
    """One tiny ``muse exec`` call for the provider test. Returns ``{"error_class", "detail"}``; an empty class
    means it worked. Details are fixed phrases, never CLI output."""
    with tempfile.TemporaryDirectory(prefix="muse-cli-") as tmp:
        argv = [exe, "exec", "--no-session-log", "--disable-shell", "--disable-write", "--disable-web-tools",
                "--disable-reminders", "--no-foreign-personal-context", "--approval-mode", "never",
                "--model", DEFAULT_MODEL, "--reasoning-effort", DEFAULT_EFFORT,
                "--workspace", tmp, "--prompt-file", _write(tmp, PROBE_PROMPT)]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", cwd=tmp, env=child_env(env),
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return {"error_class": "not_installed", "detail": "the muse CLI could not be run"}
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error_class": "timeout", "detail": f"no answer in {timeout:.0f} s"}
        finally:
            stop_sync(proc)
    if proc.returncode != 0:
        cls = classify_failure(proc.returncode, err)
        return {"error_class": cls, "detail": {
            "unsigned": unsigned_detail(exe),
            "no_key": "not logged in; run: muse login", "auth": "the Muse login was rejected; run: muse login",
            "billing": "the Muse subscription is not active", "rate_limited": "rate limited",
            "network": "could not connect"}.get(cls, f"muse exec exited {proc.returncode}")}
    if not (out or "").strip():
        return {"error_class": "bad_response", "detail": "muse exec printed nothing"}
    return {"error_class": "", "detail": "muse exec answered (its own login is the credential)"}


def _write(tmp: str, text: str) -> str:
    path = os.path.join(tmp, "prompt.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def clean_output(text: str) -> str:
    """The answer from stdout. The CLI's own notices (``muse: ...``, for example "local session messaging
    disabled", printed because ``--no-session-log`` is set) go to stderr and are harmless; a stray one on stdout
    is dropped too."""
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("muse: ")).strip()


def build_prompt(messages: list[Message]) -> str:
    """Flatten the history to one prompt: system text first, then the turns with role labels. A lone user
    message goes through as it is."""
    system = [m.text() for m in messages if m.role == "system" and m.text()]
    turns = [m for m in messages if m.role in ("user", "assistant") and m.text()]
    if not turns:
        raise ValueError("history must contain at least one message")
    body = turns[0].text() if len(turns) == 1 and turns[0].role == "user" else "\n\n".join(
        f"[{m.role}]\n{m.text()}" for m in turns)
    return ("\n\n".join(system) + "\n\n" + body) if system else body


def kill_group(proc: asyncio.subprocess.Process | subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop_sync(proc: subprocess.Popen) -> None:
    """Stop descendants too, even if the leader has exited; bound both draining and reaping."""
    kill_group(proc)
    try:
        proc.communicate(timeout=CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        # A descendant that detached from the group may still hold the captured pipes. Do not wait for it.
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()
        try:
            proc.wait(timeout=CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass


async def stop_async(proc: asyncio.subprocess.Process) -> None:
    kill_group(proc)
    try:
        await asyncio.wait_for(proc.wait(), CLEANUP_TIMEOUT)
    except asyncio.TimeoutError:
        # asyncio exposes no public close for subprocess output pipes; release them if a detached child holds
        # them open. The transport also kills a still-running leader. Waiting for EOF must stay bounded.
        proc._transport.close()


class MuseCliProvider:
    """Provider over a ``muse`` executable. ``model`` is optional; unset leaves the CLI's own default."""

    name = "muse-cli"

    def __init__(self, command: str = "muse", *, name: str | None = None, model: str | None = None,
                 env: Mapping[str, str] | None = None, read_timeout: float | None = 300.0,
                 timeout: float = 600.0) -> None:
        self.command = command
        self.model = model
        self.env = env
        self._timeout = httpx.Timeout(timeout, read=read_timeout or timeout)  # the router quotes ``_timeout.read``
        self._read = read_timeout or timeout
        if name:
            self.name = name

    async def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        if tools:  # 501 is not in NO_FALLBACK_STATUS: the router moves to the next route
            yield error_event("muse-cli is text only: it cannot carry Exo tools", 501, retryable=False)
            return
        try:
            prompt = build_prompt(messages)
        except ValueError as exc:
            yield error_event(f"muse-cli: {exc}", 400, retryable=False)
            return
        model = opts.get("model") or self.model
        if not model or model == "default":
            model = DEFAULT_MODEL
        effort = opts.get("reasoning") if opts.get("reasoning") in EFFORTS else DEFAULT_EFFORT
        st = StreamState()
        with tempfile.TemporaryDirectory(prefix="muse-cli-") as tmp:
            # An empty private workspace: the run has no files of Exo's or the user's to read.
            argv = [self.command, "exec", "--no-session-log", "--disable-shell", "--disable-write",
                    "--disable-web-tools", "--disable-reminders", "--no-foreign-personal-context",
                    "--approval-mode", "never", "--workspace", tmp,
                    "--prompt-file", _write(tmp, prompt)]
            argv += ["--model", str(model), "--reasoning-effort", effort]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=child_env(self.env), cwd=tmp, start_new_session=True)
            except OSError as exc:
                yield error_event(f"cannot run {self.command}: {exc.strerror or type(exc).__name__}", None, retryable=True)
                return
            timed_out = False
            try:
                out, err = await asyncio.wait_for(proc.communicate(), self._read)
            except asyncio.TimeoutError:
                timed_out = True
            finally:  # also runs on cancel (a stop), and after an exited leader with live descendants
                await asyncio.shield(stop_async(proc))
            if timed_out:
                yield error_event(f"ReadTimeout: muse sent nothing for {self._read:.0f} s", None, retryable=True)
                return
        text = clean_output(out.decode("utf-8", "replace"))
        if proc.returncode != 0:
            cls = classify_failure(proc.returncode, err.decode("utf-8", "replace"))
            status = {"auth": 401, "no_key": 401, "rate_limited": 429, "billing": 402}.get(cls)
            hint = " (run `muse login`)" if cls in ("auth", "no_key") else (
                f" ({unsigned_detail(self.command)})" if cls == "unsigned" else "")
            yield error_event(f"muse exec failed ({cls}, exit {proc.returncode}){hint}", status, retryable=True)
            return
        if not text:
            yield error_event("muse exec printed nothing", None, retryable=True)
            return
        if ev := st.add_text("text", text):
            yield ev
        done = st.done()
        done.usage = None  # the CLI reports no token counts in plain mode: unknown, never 0
        yield done
