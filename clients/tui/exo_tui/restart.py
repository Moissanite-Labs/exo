"""Restart the TUI in a new terminal tab, resuming the current session.

``open_tab`` is the one place that knows terminals; it takes an injectable ``runner``
(default ``subprocess.run``) so tests need no real terminal. ``schedule_restart`` starts a
detached helper that waits for this process to exit (so the memory writer lock is released)
and then opens the tab.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping
from typing import Any, Callable
from pathlib import Path

Runner = Callable[[list[str]], Any]

PERMISSION_FLAGS = ("--skip-permissions", "--dangerously-skip-permissions")


def _matching(arg: str, flags: tuple[str, ...]) -> list[str]:
    """The flags ``arg`` names, argparse abbreviation included (``--skip-perm`` names ``--skip-permissions``)."""
    key = arg.split("=", 1)[0]
    return [flag for flag in flags if key.startswith("--") and flag.startswith(key)]


def _launch_command(env: Mapping[str, str], argv: list[str] | None) -> list[str]:
    base = shlex.split(env.get("EXO_RESTART_CMD", ""))
    if base:
        return base
    carry = [f"{k}={v}" for k, v in sorted(env.items())
             if k.startswith("EXO_") and not k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))]
    base = [sys.executable, *(sys.argv if argv is None else argv)]
    return ["env", *carry, *base] if carry else base


def _without_options(base: list[str], valued: tuple[str, ...], switches: tuple[str, ...]) -> list[str]:
    """Remove old options, including argparse's unambiguous long-option abbreviations."""
    out, skip = [], False
    for arg in base:
        if skip:
            skip = False
            continue
        matches = _matching(arg, (*valued, *switches))
        if len(matches) == 1:
            skip = matches[0] in valued and "=" not in arg
        else:
            out.append(arg)
    return out


def dropped_permission_flags(env: Mapping[str, str] | None = None, argv: list[str] | None = None) -> list[str]:
    """Permission flags in the command ``new_command`` starts from, so these are the ones it strips.

    The source is ``_launch_command``, the same one ``new_command`` uses: with ``EXO_RESTART_CMD`` set the
    restart command is the source, not this process's ``sys.argv``.
    """
    base = _launch_command(os.environ if env is None else env, argv)
    dropped: list[str] = []
    for arg in base:
        matches = _matching(arg, PERMISSION_FLAGS)
        if len(matches) == 1 and matches[0] not in dropped:
            dropped.append(matches[0])
    return dropped


def restart_command(session: str | None, env: Mapping[str, str] | None = None,
                    argv: list[str] | None = None) -> list[str]:
    """The command line that resumes ``session``: EXO_RESTART_CMD, else re-exec of argv."""
    effective_env = os.environ if env is None else env
    base = _without_options(_launch_command(effective_env, argv), ("--session",), ("--resume",))
    return [*base, "--session", session] if session else [*base, "--resume"]


def new_command(cwd: str, env: Mapping[str, str] | None = None, argv: list[str] | None = None) -> list[str]:
    """Open a fresh session without inheriting a title, resume or permission flag."""
    effective_env = os.environ if env is None else env
    base = _without_options(_launch_command(effective_env, argv), ("--session", "--cwd", "--title"),
                            ("--resume", "--skip-permissions", "--dangerously-skip-permissions"))
    # The machine launchers and ``exo resume`` also accept a positional resume subcommand.
    for i, arg in enumerate(base[:-1]):
        if Path(arg).name in ("exo", "exo-canary", "exo-projects") and base[i + 1] == "resume":
            del base[i + 1]
            break
    return [*base, "--cwd", cwd]


def _applescript_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def has_opener(env: Mapping[str, str] | None = None, platform: str | None = None,
               which: Callable[[str], str | None] = shutil.which) -> bool:
    effective_env = os.environ if env is None else env
    if (platform or sys.platform) == "darwin":
        return True
    return bool((which("kitty") and effective_env.get("KITTY_WINDOW_ID")) or (which("wezterm") and effective_env.get("WEZTERM_PANE"))
                or which("gnome-terminal"))


def open_tab(cmd: list[str], env: Mapping[str, str] | None = None, platform: str | None = None,
             runner: Runner | None = None, which: Callable[[str], str | None] = shutil.which) -> str:
    """Open ``cmd`` in a new terminal tab. Returns "opened" or the exact command to run by hand."""
    effective_env = os.environ if env is None else env
    platform = platform or sys.platform
    run = runner or (lambda a: subprocess.run(a, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15))
    line = shlex.join(cmd)
    here = shlex.quote(os.getcwd())
    shell_line = f"cd {here} && {line}"
    try:
        if platform == "darwin":
            q = _applescript_quote(shell_line)
            # Never target an existing tab and never send keystrokes: both can type into another program.
            # Each script makes a NEW window that runs the command itself.
            if effective_env.get("TERM_PROGRAM") == "iTerm.app":
                script = ('tell application "iTerm2"\nactivate\n'
                          f'create window with default profile command "{q}"\nend tell')
            else:
                script = f'tell application "Terminal"\nactivate\ndo script "{q}"\nend tell'
            run(["osascript", "-e", script])
            return "opened"
        if which("kitty") and effective_env.get("KITTY_WINDOW_ID"):
            run(["kitty", "@", "launch", "--type=tab", "--cwd=current", *cmd])
            return "opened"
        if which("wezterm") and effective_env.get("WEZTERM_PANE"):
            run(["wezterm", "cli", "spawn", "--cwd", os.getcwd(), "--", *cmd])
            return "opened"
        if which("gnome-terminal"):
            run(["gnome-terminal", "--tab", "--", "bash", "-c", shell_line])
            return "opened"
    except Exception:
        pass
    return shell_line


def schedule_restart(cmd: list[str], pid: int | None = None) -> None:
    """Detached helper: wait for ``pid`` (default this process) to exit, then open the tab."""
    code = ("import os,sys,time,json\n"
            "pid,cmd=int(sys.argv[1]),json.loads(sys.argv[2])\n"
            "for _ in range(300):\n"
            "    try: os.kill(pid,0)\n"
            "    except OSError: break\n"
            "    time.sleep(0.1)\n"
            "from exo_tui.restart import open_tab\n"
            "line=open_tab(cmd)\n"
            "if line!='opened': print('exo: open a new tab and run: '+line)\n")
    subprocess.Popen([sys.executable, "-c", code, str(pid or os.getpid()), json.dumps(cmd)],
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
