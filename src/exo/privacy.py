"""Privacy checks for tests (D-004, D-042): nothing in the package names the principal.

The names to refuse are not stored in the repo. A test loads them at run time from an untracked file:
$EXO_PRIVACY_DENYLIST, else ~/.config/exo/privacy-denylist.txt (one word or phrase per line, # for comments).
When the file is absent, tests that need it skip. The generic checks (home paths, email addresses) always run.

Read the list at import or collection time: the repo conftest points HOME and EXO_* at a temp dir for each test.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid", "localhost",
                         "higherorderco.com", "anthropic.com"}
ALLOWED_HOME_NAMES = {"<name>", "name", "user", "you", "me", "sam", "someone", "..."}

_EMAIL = re.compile(r"(?<![\w.+@-])[A-Za-z0-9][A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
_HOME = re.compile(r"/Users/([A-Za-z0-9._<>-]+(?:\.\.\.)?)")


def _real_home() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        return os.path.expanduser("~")


def denylist_path() -> Path:
    env = os.environ.get("EXO_PRIVACY_DENYLIST")
    return Path(env) if env else Path(_real_home()) / ".config" / "exo" / "privacy-denylist.txt"


def load_denylist() -> list[str]:
    """Words to refuse, lowercased. Empty when the file is absent."""
    p = denylist_path()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [w.strip().lower() for w in lines if w.strip() and not w.lstrip().startswith("#")]


def deny_regex(words: list[str] | None = None, *, whole_word: bool = False) -> re.Pattern | None:
    """One case-insensitive pattern over the words, or None when there are none."""
    words = load_denylist() if words is None else words
    if not words:
        return None
    body = "|".join(re.escape(w) for w in words)
    return re.compile(r"(?<!\w)(" + body + r")(?![\w])" if whole_word else body, re.I)


def generic_findings(text: str) -> list[str]:
    """Home paths with a real-looking user name and email addresses outside the allowlist."""
    out = []
    for m in _HOME.finditer(text):
        if m.group(1).lower() not in ALLOWED_HOME_NAMES:
            out.append("home path: /Users/" + m.group(1))
    for m in _EMAIL.finditer(text):
        if m.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
            out.append("email address: " + m.group(0))
    return out
