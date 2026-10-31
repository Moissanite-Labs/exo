"""Command completion and guidance (D-037), shared by the terminal client and the web client's server.

Pure functions and small classes, no UI and no model calls. A client feeds it the composer text and some cached
data (models, threads, grant ids) and draws what comes back: a dropdown of items, a ghost hint, a one-line tip.

The idea of a menu that opens on ``/`` and ghost text from history comes from the Hermes CLI. This is our own build.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .core.private import atomic_write

# ---------------------------------------------------------------- the command table
# name -> argument shape shown after the name. Descriptions live with each client's own table (the TUI COMMANDS
# dict) and in commands.list; this table only adds the shape and the group for commands every client may meet.
ARG_HINTS: dict[str, str] = {
    "rounds": "[default|off|number]", "new": "[private]", "tab": "[directory]", "sessions": "", "resume": "[thread]", "fork": "[title]", "rename": "<title>", "undo": "", "clear": "",
    "cwd": "", "context": "", "compact": "", "restart": "", "stop": "[task]", "tasks": "", "open": "<task>", "steer": "<task> <text>", "unsteer": "", "exit": "", "quit": "",
    "model": "[default] <model>", "default": "[model]", "providers": "[add] [name]", "usage": "",
    "panel": "", "peers": "", "send": "<peer> <text>", "handoff": "[path]", "intro": "",
    "tutorial": "[on|off|reset]", "copy": "[last|error|all]", "help": "",
    "compose": "[task|edit|close]", "layout": "[inverse|classic]",
    "trace": "[last N turns]", "findings": "[count]",
    "grants": "[list | revoke <id>]", "exo": "<subcommand> [args]", "orchestra": "[seats|skills|seat|check]",
    "peer-release": "<id>", "peer-deny": "<id>", "exo-command": "", "permissions": "[status|bypass|full [all]|ask]",
}


@dataclass(frozen=True)
class Item:
    """One row in the dropdown. ``value`` is what goes into the text; ``kind`` is command, arg or file."""
    value: str
    label: str = ""
    meta: str = ""
    kind: str = "arg"
    args: str = ""                       # argument shape, for commands

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.value)


@dataclass(frozen=True)
class Slot:
    """What one argument position accepts: a data kind to look up in the context, fixed choices, and a hint."""
    hint: str
    kind: str = ""
    choices: tuple[tuple[str, str], ...] = ()


def _c(*pairs: str) -> tuple[tuple[str, str], ...]:
    return tuple((a, b) for a, _, b in (p.partition("|") for p in pairs))


def plan(cmd: str, done: Sequence[str]) -> Slot | None:
    """The slot for the next argument of ``/cmd`` given the whole words already typed after it."""
    n = len(done)
    if cmd == "model":
        if n == 0:
            return Slot("[default] <model>", "models", _c("default|set the default for new sessions"))
        if n == 1 and done[0] == "default":
            return Slot("<model>", "models")
    elif cmd == "default":
        return Slot("[model]", "models") if n == 0 else None
    elif cmd in ("resume", "sessions"):
        return Slot("[thread]", "threads") if n == 0 else None
    elif cmd == "tutorial":
        return Slot("[on|off|reset]", "", _c("on|short tips the first time something comes up",
                                              "off|no tips, no guidance line", "reset|show every tip again")) if n == 0 else None
    elif cmd == "grants":
        if n == 0:
            return Slot("[list | revoke <id>]", "", _c("list|standing approval grants", "revoke|remove one grant"))
        if n == 1 and done[0] == "revoke":
            return Slot("<id>", "grants")
    elif cmd == "providers":
        if n == 0:
            return Slot("[add] [name]", "providers", _c("add|add or replace a key"))
        if n == 1 and done[0] == "add":
            return Slot("<name>", "providers")
    elif cmd == "handoff":
        return Slot("[path]", "handoff_path") if n == 0 else None
    elif cmd == "new":
        return Slot("[private]", "", _c("private|a thread that is not remembered")) if n == 0 else None
    elif cmd == "copy":
        return Slot("[last|error|all]", "", _c("last|the last answer", "error|the last error",
                                                "all|the whole transcript")) if n == 0 else None
    elif cmd == "compose":
        return Slot("[task|edit|close]", "", _c("task|open the editor with Goal, Context, Constraints, Done when",
                                                 "edit|edit the draft in $EDITOR", "close|close the editor, keep the draft")) if n == 0 else None
    elif cmd == "layout":
        return Slot("[inverse|classic]", "", _c("inverse|input on top, newest message under it",
                                                 "classic|input at the bottom, newest at the bottom")) if n == 0 else None
    elif cmd == "trace":
        return Slot("[last N turns]", "", _c("5|last 5 turns", "10|last 10 turns", "25|last 25 turns")) if n == 0 else None
    elif cmd == "permissions":
        return Slot("[status|bypass|full [all]|ask]", "", _c("status|the mode and how many calls bypass auto-approved",
                                                   "bypass|stop asking, except Exo's own records and secrets",
                                                   "full|skip all gate checks for all tool calls in the selected thread",
                                                   "ask|ask before risky calls again")) if n == 0 else None
    elif cmd == "findings":
        return Slot("[count]", "", _c("count|how many are open")) if n == 0 else None
    elif cmd == "send":
        return Slot("<peer> <text>", "peers") if n == 0 else (Slot("<text>") if n == 1 else None)
    elif cmd == "fork":
        return Slot("[title]") if n == 0 else None
    elif cmd == "rename":
        return Slot("<title>") if n == 0 else None
    elif cmd == "exo":
        if n == 0:
            return Slot("<subcommand>", "", _c("help|list the subcommands", "branch|write a handoff and a fresh-session line",
                                               "handoff|write a handoff file", "resume|load the newest handoff"))
    elif cmd == "orchestra":
        if n == 0:
            return Slot("[seats|skills|seat|check]", "", _c("seats|list seat templates", "skills|list skills",
                                                             "seat|render a seat file", "check|check a seat file"))
    return None


# ---------------------------------------------------------------- fuzzy matching
def score(query: str, text: str) -> int | None:
    """Lower is better, None means no match. Exact, then prefix, then a word start, then contains, then the
    letters in order (``cmp`` finds ``compact``)."""
    q, t = query.lower(), text.lower()
    if not q:
        return 0
    if t == q:
        return 0
    if t.startswith(q):
        return 1 + len(t) - len(q)
    for m in re.finditer(r"[-_./: ]", t):
        if t.startswith(q, m.end()):
            return 200 + m.end()
    pos = t.find(q)
    if pos >= 0:
        return 400 + pos
    at, gaps = -1, 0
    for ch in q:
        nxt = t.find(ch, at + 1)
        if nxt < 0:
            return None
        gaps += nxt - at - 1 if at >= 0 else nxt
        at = nxt
    return 1000 + gaps


def rank(query: str, items: Iterable[Item], limit: int = 40) -> list[Item]:
    scored = []
    for i, it in enumerate(items):
        s = score(query, it.label)
        if s is None and it.meta and len(query) > 2:  # a word of the description can find a command too
            ms = score(query, it.meta)
            s = None if ms is None or ms >= 1000 else 2000 + ms
        if s is not None:
            scored.append((s, i, it))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [it for _, _, it in scored[:limit]]


def closest(name: str, names: Iterable[str]) -> str | None:
    """The command a mistyped ``/name`` most likely meant, or None when nothing is close."""
    pool = sorted(set(names))
    name = name.lower()
    if not name or name in pool:
        return None
    pre = [n for n in pool if n.startswith(name)]
    if pre:
        return min(pre, key=len)
    hit = difflib.get_close_matches(name, pool, n=1, cutoff=0.6)
    if hit:
        return hit[0]
    sub = [n for n in pool if len(name) >= 3 and score(name, n) is not None and score(name, n) < 1000]
    return min(sub, key=len) if len(sub) == 1 else None


# ---------------------------------------------------------------- files for @
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", "dist", "build", "target"}


class FileIndex:
    """Files under ``cwd`` for ``@``: git's own list (tracked plus untracked, minus .gitignore) when the folder is a
    repo, else a bounded walk. Secret stores are left out with ``exo.gate.is_sensitive_path``. Never reads a file."""

    def __init__(self, cwd: str | os.PathLike, ttl: float = 5.0, cap: int = 6000, *,
                 executor_required: bool = False) -> None:
        self.cwd = Path(cwd)
        self.ttl, self.cap = ttl, cap
        self.executor_required = executor_required or os.environ.get("EXO_TOOL_EXECUTOR_REQUIRED") == "1"
        self._at = 0.0
        self._files: list[str] = []
        self._dirs: list[str] = []

    def _list(self) -> list[str]:
        if not self.executor_required:
            # Git configuration in a writable workspace can execute fsmonitor helpers.
            try:
                r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                   cwd=self.cwd, capture_output=True, timeout=3, check=False)
                if r.returncode == 0:
                    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p][: self.cap]
            except (OSError, subprocess.SubprocessError):
                pass
        out: list[str] = []
        stack = [(self.cwd, 0)]
        while stack and len(out) < self.cap:
            d, depth = stack.pop()
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                if e.name.startswith(".") or e.name in _SKIP_DIRS or e.is_symlink():
                    continue
                rel = os.path.relpath(e.path, self.cwd)
                if e.is_dir():
                    if depth < 6:
                        stack.append((Path(e.path), depth + 1))
                elif e.is_file():
                    out.append(rel)
        return out

    def refresh(self, force: bool = False) -> None:
        if not force and self._files and time.monotonic() - self._at < self.ttl:
            return
        from exo.gate import is_sensitive_path
        files, dirs = [], set()
        for rel in self._list():
            if is_sensitive_path(str(self.cwd / rel)):
                continue
            files.append(rel)
            parts = rel.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]) + "/")
        self._files, self._dirs, self._at = files, sorted(dirs), time.monotonic()

    def query(self, q: str, limit: int = 30) -> list[Item]:
        """A path with a slash (or nothing) browses that folder, dirs first; a bare word is a fuzzy search."""
        self.refresh()
        q = q.lstrip("@").replace("\\", "/")
        if q.startswith("/") or ".." in q.split("/") or q.startswith("~"):
            return []  # inside the working folder only
        if "/" in q or not q:
            base, _, tail = q.rpartition("/")
            prefix = base + "/" if base else ""
            kids: dict[str, int | None] = {}
            for p in self._dirs + self._files:
                rest = p[len(prefix):] if p.startswith(prefix) else ""
                if rest:
                    first, slash, _ = rest.partition("/")
                    s = score(tail, first)
                    if s is not None and s < 1000:
                        kids[prefix + first + slash] = s
            names = sorted(kids, key=lambda k: (not k.endswith("/"), kids[k], k))
            return [Item("@" + k, k, "dir" if k.endswith("/") else "", "file") for k in names[:limit]]
        pool = [(p, True) for p in self._dirs] + [(p, False) for p in self._files]
        hits = []
        for p, is_dir in pool:
            base = os.path.basename(p.rstrip("/"))
            s = score(q, base)
            if s is None:
                s2 = score(q, p)
                s = None if s2 is None else 3000 + s2
            if s is not None:
                hits.append((s, 0 if is_dir else 1, len(p), p, is_dir))
        hits.sort()
        return [Item("@" + p, p, "dir" if d else "", "file") for _, _, _, p, d in hits[:limit]]


# ---------------------------------------------------------------- suggestions for the composer
@dataclass
class Suggestion:
    mode: str = ""                       # "command", "arg", "file", "shell" or "" for nothing to show
    items: list[Item] = field(default_factory=list)
    start: int = 0                       # where in the text the token being completed begins
    hint: str = ""                       # the argument shape, or the shell note
    need: str = ""                       # a data kind the client should fetch, then ask again


SHELL_HINT = "! asks the agent to run this in its shell tool and show the output; the gate may ask first"


def complete_text(text: str, sug: Suggestion, item: Item) -> str:
    """The composer text after taking ``item``. A command gets a trailing space; an argument gets one only when
    another argument can follow."""
    head = text[: sug.start]
    if sug.mode == "command":
        return head + "/" + item.value + " "
    if sug.mode == "file":
        return head + item.value + ("" if item.value.endswith("/") else " ")
    words = text[: sug.start].split()
    cmd = words[0][1:].lower() if words else ""
    more = plan(cmd, [*words[1:], item.value]) is not None
    return head + item.value + (" " if more else "")


def suggest(text: str, catalog: Sequence[Item], ctx: dict[str, list[Item]] | None = None,
            files: FileIndex | None = None) -> Suggestion:
    """What to show for composer text with the cursor at its end. ``catalog`` holds one Item per command
    (value = name, label = ``/name``, meta = description + args)."""
    ctx = ctx or {}
    if "\n" in text:
        return Suggestion()
    if text.startswith("!"):
        return Suggestion("shell", hint=SHELL_HINT)
    m = re.search(r"(?:^|\s)(@\S*)$", text)
    if m and files is not None and not text.startswith("/"):
        tok = m.group(1)
        return Suggestion("file", files.query(tok), m.start(1))
    if not text.startswith("/"):
        return Suggestion()
    head, sp, rest = text[1:].partition(" ")
    if not sp:
        return Suggestion("command", rank(head, [Item(c.value, "/" + c.value, c.meta, "command", c.args) for c in catalog]), 0)
    cmd = head.lower()
    words = rest.split(" ")
    done, partial = [w for w in words[:-1] if w], words[-1]
    slot = plan(cmd, done)
    start = len(text) - len(partial)
    if slot is None:
        spec = next((c for c in catalog if c.value == cmd), None)
        return Suggestion("arg", [], start, "" if done or partial or not spec else spec.args)
    pool = [Item(v, v, d) for v, d in slot.choices] + list(ctx.get(slot.kind, [])) if slot.kind or slot.choices else []
    need = slot.kind if slot.kind and slot.kind not in ctx else ""
    return Suggestion("arg", rank(partial, pool), start, "" if partial else slot.hint, need)


def build_catalog(local: dict[str, str], core: Sequence[dict[str, Any]] = ()) -> list[Item]:
    """One Item per command from a client's own table plus the core's ``commands.list`` rows (name, description,
    args). Meta reads ``description`` and, for the argument shape, the label gets it in the dropdown."""
    seen: dict[str, Item] = {}
    for name, desc in local.items():
        seen[name] = Item(name, name, desc, "command", args_hint(name, core))
    for c in core:
        name = str(c.get("name") or "")
        if name and name not in seen:
            seen[name] = Item(name, name, str(c.get("description") or ""), "command", args_hint(name, core))
    return list(seen.values())


def args_hint(name: str, core: Sequence[dict[str, Any]] = ()) -> str:
    if name in ARG_HINTS:
        return ARG_HINTS[name]
    return next((str(c.get("args") or "") for c in core if c.get("name") == name), "")


def short_desc(desc: str) -> str:
    """The first clause of a description, for one dropdown row."""
    d = re.split(r"[:;]\s|\.\s", desc, maxsplit=1)[0].strip()
    return d if len(d) <= 64 else d[:63].rsplit(" ", 1)[0] + "\u2026"


def ghost(text: str, sug: Suggestion, hist: "History | None") -> tuple[str, bool]:
    """Dim text after the cursor and whether right-arrow may accept it. History first, then the top menu item
    when it continues what was typed, then the argument shape (not acceptable)."""
    if text and "\n" not in text and hist is not None:
        rest = hist.ghost(text)
        if rest:
            return rest, True
    if sug.mode == "command" and sug.items and text.startswith("/") and " " not in text:
        top = sug.items[0].value
        if top.startswith(text[1:].lower()) and top != text[1:].lower():
            return top[len(text) - 1:], True
    if sug.hint and sug.mode == "arg" and text.endswith(" "):
        return sug.hint, False
    return "", False


# ---------------------------------------------------------------- history
_SECRETISH = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]|\b(sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9_-]{16,}")


class History:
    """Sent lines for one seat: up-arrow recall and shell-style ghost text. One JSON string per line in
    ``$EXO_HOME/history/<seat>.jsonl`` (mode 0600), the newest 500. Lines that look like keys are never kept."""

    def __init__(self, path: str | os.PathLike | None = None, limit: int = 500) -> None:
        self.path = Path(path) if path else None
        self.limit = limit
        self.entries: list[str] = []
        self._i: int | None = None
        self._draft = ""
        if self.path and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
                try:
                    v = json.loads(line)
                except ValueError:
                    continue
                if isinstance(v, str) and v:
                    self.entries.append(v)

    @classmethod
    def for_seat(cls, home: str | os.PathLike, seat: str | None) -> "History":
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", seat or "default") or "default"
        return cls(Path(home).expanduser() / "history" / f"{name}.jsonl")

    def add(self, text: str) -> None:
        text = text.strip()
        self._i = None
        if not text or len(text) > 2000 or _SECRETISH.search(text) or (self.entries and self.entries[-1] == text):
            return
        self.entries.append(text)
        self.entries = self.entries[-self.limit:]
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.path, "".join(json.dumps(e) + "\n" for e in self.entries))
            except OSError:
                pass  # history is a convenience; never break a send over it

    def prev(self, current: str) -> str | None:
        if not self.entries:
            return None
        if self._i is None:
            self._i, self._draft = len(self.entries), current
        if self._i == 0:
            return None
        self._i -= 1
        return self.entries[self._i]

    def next(self) -> str | None:
        if self._i is None:
            return None
        self._i += 1
        if self._i >= len(self.entries):
            self._i = None
            return self._draft
        return self.entries[self._i]

    def reset(self) -> None:
        self._i = None

    @property
    def browsing(self) -> bool:
        return self._i is not None

    def ghost(self, text: str) -> str:
        if not text.strip():
            return ""
        for e in reversed(self.entries):
            if len(e) > len(text) and e.startswith(text) and "\n" not in e:
                return e[len(text):]
        return ""


# ---------------------------------------------------------------- guidance: one dim line, local rules only
@dataclass
class State:
    """What the rules look at. The client fills it from events it already sees."""
    first_run: bool = False
    provider_error: bool = False
    turns: int = 0
    findings: int = 0
    long_turn: bool = False
    job_done: str = ""          # id of a background job that finished and was not looked at
    busy: bool = False


@dataclass(frozen=True)
class Tip:
    key: str
    text: str                   # the one line shown, dim
    fill: str = ""              # what Tab puts in the composer ("" = nothing to fill)


class Guide:
    """Picks at most one tip. At most one new tip per ``gap`` seconds, each rule at most twice a session, never the same
    tip twice in a row, nothing while a turn runs, and a rule retires for good once the user types its command."""

    def __init__(self, enabled: bool = True, gap: float = 25.0, per_rule: int = 2,
                 many_turns: int = 12, clock: Callable[[], float] = time.monotonic) -> None:
        self.enabled, self.gap, self.per_rule, self.many_turns, self.clock = enabled, gap, per_rule, many_turns, clock
        self.shown: dict[str, int] = {}
        self.retired: set[str] = set()
        self.last_at: float | None = None
        self.last_key = ""

    def rules(self, st: State) -> list[Tip]:
        out: list[Tip] = []
        if st.first_run:
            out.append(Tip("intro", "first time here? /intro explains what Exo is and how to use it", "/intro"))
        if st.provider_error:
            out.append(Tip("providers", "that model call failed: /providers checks keys, servers and logins",
                           "/providers"))
        if st.job_done:
            out.append(Tip("job:" + st.job_done, f"job {st.job_done} finished: ask for its job_output",
                           f"show me the job_output for {st.job_done}"))
        if st.findings:
            out.append(Tip("findings", f"{st.findings} open finding(s): /findings", "/findings"))
        if st.long_turn:
            out.append(Tip("trace", "that turn was slow: /trace shows where the time went", "/trace"))
        if st.turns >= self.many_turns:
            out.append(Tip("context", "a long thread: /context shows what the next call carries", "/context"))
        return out

    def used(self, command: str) -> None:
        """The user ran ``/command``: stop suggesting it."""
        for k in ("intro", "providers", "findings", "trace", "context"):
            if command == k:
                self.retired.add(k)

    def pick(self, st: State) -> Tip | None:
        if not self.enabled or st.busy:
            return None
        now = self.clock()
        if self.last_at is not None and now - self.last_at < self.gap:
            return None
        for tip in self.rules(st):
            if tip.key in self.retired or tip.key == self.last_key:
                continue
            rule = tip.key.split(":")[0]
            if self.shown.get(rule, 0) >= self.per_rule:
                continue
            self.shown[rule] = self.shown.get(rule, 0) + 1
            self.last_at, self.last_key = now, tip.key
            return tip
        return None
