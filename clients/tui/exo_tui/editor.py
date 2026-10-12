"""Open the draft in $VISUAL or $EDITOR (D-046). Pure: no terminal handling; the app gives the terminal away around it."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile


def editor_command() -> list[str]:
    cmd = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    if not cmd.strip():
        raise FileNotFoundError("no $VISUAL or $EDITOR")
    return shlex.split(cmd)


def run_editor(text: str) -> str | None:
    """Write ``text`` to a temp .md file, run the editor on it, return the saved text; None if the editor failed."""
    argv = editor_command()
    fd, path = tempfile.mkstemp(prefix="exo-compose-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        done = subprocess.run([*argv, path], check=False)
        if done.returncode != 0:
            return None
        with open(path, encoding="utf-8") as f:
            return f.read().rstrip("\n")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
