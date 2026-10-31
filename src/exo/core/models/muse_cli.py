"""Muse Code subscription through the ``muse`` CLI: one ``muse exec`` subprocess per call, no key in Exo.

Why a subprocess: the Muse login keeps its OAuth token in the OS keychain, owned by the ``muse`` binary. Exo
never reads, copies or stores it. Exo only starts ``muse exec`` and reads what it prints.

What this provider is not, said plainly:

- Text only. ``muse exec`` runs its own agent loop. Exo cannot hand it Exo's tools or stream tool calls out of
  it, so a call that carries tools is refused (status 501) and the router falls back to the next route.
- One process per turn, so every turn pays process start-up and a fresh session. History is flattened into one
  prompt, earlier tool calls and results as text. Output arrives when the run ends, not token by token.
- On macOS the native Muse binary must be code-signed for keychain access. ``signature_state`` checks a native
  executable directly; a shell launcher is ``unknown``, because its own signature says nothing about the binary
  it starts. Launchers may run, and an actual signing failure is classified from the CLI's error.
- ``META_API_KEY`` is removed from the child's environment. It would override the account login and bill an API
  account instead of the subscription.

The only file read is ``~/.config/muse/auth.json``, and only two non-secret fields from it (``mechanism`` and
``subs_active``, see ``login_state``). Nothing else from that file is kept, logged or returned.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from exo.core.models import claude_cli
from exo.core.models.base import StreamState, error_event
from exo.core.types import Message, ProviderEvent, ToolSpec

# Credentials and backend switches that would make the CLI bill an API account instead of the login.
DROP_ENV = ("META_API_KEY",)
PROBE_PROMPT = "Reply with the single word: ok"
NOT_SIGNED = "not signed at all"
MACHO_MAGICS = frozenset(bytes.fromhex(m) for m in (
    "feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca"))


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The CLI's environment (D-039 style): the minimal allowlisted set, never an API key. HOME stays so the
    CLI finds its own config and keychain."""
    return claude_cli.child_env(env, drop=DROP_ENV, extra={})


def login_state(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``{"logged_in": bool, "subs_active": bool | None}`` from the non-secret fields of the Muse auth file.

    Reads ``providers.meta.mechanism`` and ``providers.meta.subs_active`` and nothing else; the rest of the file
    (which holds personal metadata) is dropped at once. Never touches the keychain, so a stale file can say
    "logged in" when the keychain entry is gone: the test call is what proves the login works."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    try:
        meta = (json.loads((Path(base) / "muse" / "auth.json").read_text(encoding="utf-8")).get("providers") or {}).get("meta") or {}
        mechanism, subs = meta.get("mechanism"), meta.get("subs_active")
    except (OSError, ValueError, AttributeError):
        return {"logged_in": False, "subs_active": None}
    return {"logged_in": mechanism == "oauth", "subs_active": subs if isinstance(subs, bool) else None}


def _native_binary(exe: str) -> bool:
    try:
        with open(exe, "rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def signature_state(exe: str) -> str:
    """Signing state of a directly configured native macOS binary. Scripts and launchers are ``unknown``:
    they cannot be code-signed, and this does not resolve or verify the executable they eventually start."""
    if sys.platform != "darwin" or not _native_binary(exe) or not shutil.which("codesign"):
        return "unknown"
    try:
        r = subprocess.run(["codesign", "-dv", exe], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if r.returncode == 0:
        return "signed"
    return "unsigned" if NOT_SIGNED in (r.stderr or "") else "unknown"


def unsigned_detail(exe: str) -> str:
    if _native_binary(exe):
        return f"the native Muse binary is not code-signed; run: codesign -s - {exe}"
    return "Muse keychain access failed (unsigned binary); run codesign -s - on the native Muse binary, not its launcher"


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
    if signature_state(exe) == "unsigned":
        return {"error_class": "unsigned", "detail": unsigned_detail(exe)}
    with tempfile.TemporaryDirectory(prefix="muse-cli-") as tmp:
        argv = _exec_argv(exe, tmp, PROBE_PROMPT)
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


def _exec_argv(exe: str, tmp: str, prompt: str) -> list[str]:
    """``muse exec`` with every tool and personal-context switch off, in the private workspace ``tmp``."""
    return [exe, "exec", "--no-session-log", "--disable-shell", "--disable-write", "--disable-web-tools",
            "--disable-reminders", "--no-foreign-personal-context", "--approval-mode", "never",
            "--workspace", tmp, "--prompt-file", _write(tmp, prompt)]


def _write(tmp: str, text: str) -> str:
    path = os.path.join(tmp, "prompt.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _blocks(m: Message, names: dict[str, str]) -> list[str]:
    """One message as labelled blocks. The CLI cannot take tool turns, so earlier tool calls and results go in as
    text: dropping them would hide from the model what was done."""
    if m.role == "tool":
        return [f"[tool result {names.get(r.call_id, r.call_id)}{', error' if r.is_error else ''}]\n"
                f"{r.text() or '(no output)'}" for r in m.tool_results]
    lines = [m.text()] if m.text() else []
    lines += [f"[tool call {c.name}] {json.dumps(c.arguments, ensure_ascii=False)}" for c in m.tool_calls]
    return [f"[{m.role}]\n" + "\n".join(lines)] if lines else []


def build_prompt(messages: list[Message]) -> str:
    """Flatten the history to one prompt: system text first, then the turns with role labels, tool calls and
    results included. A lone user message goes through as it is."""
    system = [m.text() for m in messages if m.role == "system" and m.text()]
    names = {c.id: c.name for m in messages for c in m.tool_calls}
    turns = [(m, b) for m in messages if m.role != "system" for b in _blocks(m, names)]
    if not turns:
        raise ValueError("history must contain at least one message")
    lone = len(turns) == 1 and turns[0][0].role == "user"
    body = turns[0][0].text() if lone else "\n\n".join(b for _, b in turns)
    return ("\n\n".join(system) + "\n\n" + body) if system else body


def stop_sync(proc: subprocess.Popen) -> None:
    """Stop descendants too, even if the leader has exited; bound both draining and reaping."""
    claude_cli.kill_group(proc)
    try:
        proc.communicate(timeout=claude_cli.CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        # A descendant that detached from the group may still hold the captured pipes. Do not wait for it.
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()
        try:
            proc.wait(timeout=claude_cli.CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass


class MuseCliProvider(claude_cli.CliProvider):
    """Provider over a ``muse`` executable. ``model`` is optional; unset leaves the CLI's own default."""

    name = "muse-cli"
    default_command = "muse"
    default_read = 300.0

    async def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        if tools:  # 501 is not in NO_FALLBACK_STATUS: the router moves to the next route
            yield error_event("muse-cli is text only: it cannot carry Exo tools", 501, retryable=False)
            return
        try:
            prompt = build_prompt(messages)
        except ValueError as exc:
            yield error_event(f"muse-cli: {exc}", 400, retryable=False)
            return
        if signature_state(self.command) == "unsigned":
            yield error_event(f"muse-cli: {unsigned_detail(self.command)}", None, retryable=True)
            return
        model = opts.get("model") or self.model
        effort = opts.get("reasoning")
        st = StreamState()
        with tempfile.TemporaryDirectory(prefix="muse-cli-") as tmp:
            # An empty private workspace: the run has no files of Exo's or the user's to read.
            argv = _exec_argv(self.command, tmp, prompt)
            if model and model != "default":
                argv += ["--model", str(model)]
            if effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
                argv += ["--reasoning-effort", effort]
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
                await asyncio.shield(claude_cli.stop_group(proc))
            if timed_out:
                yield error_event(f"ReadTimeout: muse sent nothing for {self._read:.0f} s", None, retryable=True)
                return
        text = out.decode("utf-8", "replace").strip()
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
