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

import fnmatch
import hashlib
import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
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

    def as_dict(self) -> dict:
        """Ledger-safe view: no outbound text or command, only their digests and sizes."""
        d = asdict(self)
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

    def as_dict(self) -> dict:
        return asdict(self)


# --- helpers -------------------------------------------------------------------------------

def _make(category: str, tool: str = "", **kw) -> Classification:
    rev, exp, third = CATEGORIES.get(category, CATEGORIES["unknown"])
    kw.setdefault("reversibility", rev)
    kw.setdefault("exposure", exp)
    kw.setdefault("third_party", third)
    return Classification(category=category, tool=tool, **kw)


def _severity(c: Classification) -> tuple:
    return (REVERSIBILITY.index(c.reversibility), EXPOSURE.index(c.exposure), c.third_party,
            c.category not in ("shell", "read", "code", "unknown"))


def _most_severe(cs: Iterable[Classification]) -> Classification | None:
    cs = list(cs)
    return max(cs, key=_severity) if cs else None


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")


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
_WRITE_REDIRS = (">", ">>", ">|", "&>", "&>>", "<>", ">&")
_REDIR_OPS = ("&>>", "&>", "<<<", "<<-", "<<", "<>", ">>", ">|", ">&", "<&", ">", "<")
_CTRL_OPS = (";;&", ";;", ";&", "&&", "||", "|&", "|", "&", ";", "(", ")")
_ASSIGN = re.compile(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=.*", re.S)


@dataclass
class _Word:
    text: str = ""
    dynamic: bool = False      # holds $var, ${..}, $(..) or `..` outside single quotes
    quoted: bool = False
    glob: bool = False         # an unquoted * or ?, or a [ or { after the first character
    started: bool = False


@dataclass
class _Heredoc:
    delim: str
    strip: bool
    quoted: bool
    body: str = ""


@dataclass
class _Cmd:
    words: list = field(default_factory=list)
    redirs: list = field(default_factory=list)       # (operator, _Word)
    heredocs: list = field(default_factory=list)     # _Heredoc
    herestrings: list = field(default_factory=list)  # _Word
    piped_in: bool = False


class _Lexer:
    """Shell words, operators, redirections and heredocs. Never raises; odd input gives odd words."""

    def __init__(self, src: str):
        self.s = src
        self.i = 0
        self.subs: list[str] = []          # sources of $( ), ` ` and <( ) met at this level
        self.pending: list[_Heredoc] = []

    def run(self, closer: bool = False) -> list:
        s, toks = self.s, []
        state = {"w": _Word(), "delim": None}
        depth = 0

        def flush():
            w = state["w"]
            if w.started:
                if state["delim"] is not None:
                    hd = _Heredoc(w.text, state["delim"], w.quoted)
                    self.pending.append(hd)
                    toks.append(("hd", hd))
                    state["delim"] = None
                else:
                    toks.append(("w", w))
            state["w"] = _Word()

        while self.i < len(s):
            w = state["w"]
            ch, nxt = s[self.i], s[self.i + 1:self.i + 2]
            if ch in " \t\r":
                flush()
                self.i += 1
            elif ch == "\n":
                flush()
                toks.append(("op", ";"))
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
                self.run(closer=True)
                self.subs.append(s[start:max(start, self.i - 1)])
                w.text += "<(..)"
                w.dynamic = w.started = True
            elif ch in "<>" or (ch == "&" and nxt == ">"):
                if w.started and w.text.isdigit() and not w.quoted:
                    state["w"] = _Word()       # an fd number belongs to the operator
                else:
                    flush()
                op = next(o for o in _REDIR_OPS if s.startswith(o, self.i))
                self.i += len(op)
                if op in ("<<", "<<-"):
                    state["delim"] = op == "<<-"
                else:
                    toks.append(("redir", op))
            elif ch in ";&|()":
                flush()
                if ch == ")" and closer and depth == 0:
                    self.i += 1
                    return toks
                op = next(o for o in _CTRL_OPS if s.startswith(o, self.i))
                depth += {"(": 1, ")": -1}.get(op, 0)
                toks.append(("op", op))
                self.i += len(op)
            else:
                if ch in "*?" or (ch in "[{" and w.started):
                    w.glob = True
                w.text += ch
                w.started = True
                self.i += 1
        flush()
        return toks

    def _read_heredocs(self) -> None:
        s = self.s
        while self.pending:
            hd = self.pending.pop(0)
            lines: list[str] = []
            while self.i < len(s):
                j = s.find("\n", self.i)
                end = len(s) if j < 0 else j
                line, start = s[self.i:end], self.i
                self.i = len(s) if j < 0 else j + 1
                bare = line.lstrip("\t" if hd.strip else "").strip()
                if bare == hd.delim:
                    break
                if bare.startswith(hd.delim) and bare[len(hd.delim):].strip(" )") == "":
                    self.i = start + line.index(")", line.index(hd.delim))  # `EOF)` closes a $( ) too
                    break
                lines.append(line.lstrip("\t") if hd.strip else line)
            hd.body = "\n".join(lines)
            if not hd.quoted:
                sub = _Lexer(hd.body)
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs

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
                self._dollar(w)
            elif ch == "`":
                self._backtick(w)
            else:
                w.text += ch
                self.i += 1

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

    def _dollar(self, w: _Word) -> None:
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
            if "$(" in body[3:] or "`" in body:
                sub = _Lexer(body[3:-2])
                sub._dquote(_Word(), until_end=True)
                self.subs += sub.subs
            w.text += body
            w.dynamic = True
            self.i = j
        elif nxt == "(":
            self.i = i + 2
            start = self.i
            toks = self.run(closer=True)
            inner = s[start:max(start, self.i - 1)]
            lit = _literal_heredoc(toks)
            if lit is not None:
                w.text += lit          # $(cat <<'EOF' ... EOF) is plain text
            else:
                self.subs.append(inner)
                w.text += "$(" + inner + ")"
                w.dynamic = True
        elif nxt == "{":
            j = s.find("}", i)
            j = len(s) - 1 if j < 0 else j
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
                w.text += raw.encode("latin-1", "backslashreplace").decode("unicode_escape")
            except (UnicodeError, ValueError):
                w.text += raw
                w.dynamic = True
            w.quoted = True
            self.i = j + 1
        else:
            m = re.match(r"\$([A-Za-z_]\w*|[0-9@*#?$!-])", s[i:])
            if m:
                w.text += m.group(0)
                w.dynamic = True
                self.i = i + len(m.group(0))
            else:
                w.text += "$"
                self.i = i + 1


def _literal_heredoc(toks: list) -> str | None:
    """`cat <<'EOF' ... EOF` inside $( ) is the heredoc text, not a command the gate must classify."""
    toks = [t for t in toks if t != ("op", ";")]
    if len(toks) == 2 and toks[0][0] == "w" and toks[0][1].text == "cat" and not toks[0][1].dynamic \
            and toks[1][0] == "hd":
        hd = toks[1][1]
        if hd.quoted or not re.search(r"\$|`", hd.body):
            return hd.body
    return None


def _parse(src: str) -> tuple[list[_Cmd], list[str], bool]:
    """(simple commands, substitution sources, defines a function)."""
    lx = _Lexer(src or "")
    toks = lx.run()
    cmds: list[_Cmd] = []
    cur = _Cmd()
    func = False
    i = 0

    def end(piped: bool = False) -> None:
        nonlocal cur
        if cur.words or cur.redirs or cur.heredocs or cur.herestrings:
            cmds.append(cur)
        cur = _Cmd(piped_in=piped)

    while i < len(toks):
        kind, v = toks[i]
        if kind == "op":
            if v == "(" and cur.words and toks[i + 1:i + 2] == [("op", ")")]:
                func = True
                i += 2
                end()
                continue
            end(piped=v in ("|", "|&"))
        elif kind == "redir":
            if i + 1 < len(toks) and toks[i + 1][0] == "w":
                if v == "<<<":
                    cur.herestrings.append(toks[i + 1][1])
                else:
                    cur.redirs.append((v, toks[i + 1][1]))
                i += 2
                continue
        elif kind == "hd":
            cur.heredocs.append(v)
        else:
            cur.words.append(v)
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
_GIT_SUBS = frozenset("""
add commit checkout switch branch merge rebase stash tag fetch pull push status log diff show reset restore
clean rm mv init clone remote config rev-parse blame ls-files grep cherry-pick revert worktree submodule
describe shortlog reflog bisect apply am format-patch archive gc fsck notes rev-list cat-file for-each-ref
symbolic-ref update-ref ls-remote ls-tree show-ref version help range-diff whatchanged sparse-checkout
maintenance count-objects verify-commit verify-tag merge-base name-rev check-ignore check-attr var
""".split())
_GIT_EXEC_KEY = re.compile(r"^(alias\..+|core\.(pager|editor|sshcommand|hookspath|fsmonitor|askpass|gitproxy)|"
                           r"sequence\.editor|diff\.external|.+\.(command|cmd|textconv|clean|smudge|process|"
                           r"driver|program|helper)|credential\..*|pager\..+|filter\..+|uploadpack\..+)$", re.I)
_GH_GROUPS = frozenset("""
pr issue repo api auth release gist workflow run label secret variable ssh-key gpg-key search browse status
project cache ruleset org config attestation completion discussion help version --version --help
""".split())
_DATA_FLAGS = ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form",
               "--json", "-T", "--upload-file", "--form-string")
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


def _opaque(summary: str, cmd: str, target: str = "", signal: str = "indirection") -> Classification:
    c = _make("opaque_exec", summary=summary[:120], target=target, command=cmd)
    c.signals.append(signal)
    return c


def _classify_git(argv: list[str], cmd: str) -> Classification:
    i, config = 1, []
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"):
            if argv[i] in ("-c", "--config-env") and i + 1 < len(argv):
                config.append(argv[i + 1])
            i += 2
            continue
        i += 1
    for kv in config:
        k, _, v = kv.partition("=")
        if _GIT_EXEC_KEY.match(k) or v.startswith("!"):
            return _opaque(f"git -c {k} runs a configured command", cmd)
    sub, rest = (argv[i] if i < len(argv) else ""), argv[i + 1:]
    if sub and sub not in _GIT_SUBS:
        return _opaque(f"git {sub} (an alias or extension the gate cannot see)", cmd)
    if sub == "config":
        names = [a for a in rest if not a.startswith("-")]
        if names and _GIT_EXEC_KEY.match(names[0]) and (len(names) > 1 or _has(rest, "--add", "--replace-all")):
            return _opaque(f"git config {names[0]} sets a command git will run", cmd)
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


def _classify_gh(argv: list[str], cmd: str, bodies: list[str]) -> Classification:
    group = argv[1] if len(argv) > 1 else ""
    verb = argv[2] if len(argv) > 2 else ""
    if group and group not in _GH_GROUPS:
        return _opaque(f"gh {group} (an alias or extension the gate cannot see)", cmd)
    target, link = _gh_target(argv)
    body = _opt(argv, "--body", "-b") or ""
    title = _opt(argv, "--title", "-t") or ""
    body_file = (_opt(argv, "--body-file", "-F") or "") if group != "api" else ""
    if body_file == "-":
        body_file = ""
        if bodies and not body:
            body = bodies[0]
    kw = dict(target=target, link=link, text=body, title=title, text_file=body_file, command=cmd)
    where = f" on {target}" if target else ""
    if group == "auth" or group == "secret" or (group == "ssh-key" and verb in ("add", "delete")):
        return _make("credentials", summary=f"gh {group} {verb}", **kw)
    if group == "api":
        method = (_opt(argv, "-X", "--method") or "").upper()
        fields = _opts(argv, "-f", "--raw-field", "-F", "--field")
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


def _classify_http(argv: list[str], cmd: str) -> Classification:
    prog = argv[0]
    urls = [a for a in argv[1:] if _URL.match(a)]
    if not urls:
        urls = [a for a in argv[1:] if not a.startswith("-") and "." in a and "=" not in a][:1]
    url = urls[0] if urls else ""
    host = _host_of(url) if url else ""
    method, data = "GET", ""
    if prog == "curl":
        method = (_opt(argv, "-X", "--request") or "").upper()
        datas = _opts(argv, *_DATA_FLAGS)
        data = " ".join(datas)
        if not method:
            method = "POST" if datas or _has(argv, "-T", "--upload-file") else "GET"
        if _has(argv, "-I", "--head"):
            method = "HEAD"
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
    if _is_local_host(host):
        return _make("local_write", summary=f"{method} to local service {host or url}", **kw)
    return _make("web_post", summary=f"{method} to {host}", **kw)


def _strip_prefix_words(words: list) -> tuple[list, bool, str | None, str]:
    """Drop assignments and prefix commands (sudo, env, timeout...). Returns (words, sudo, env -S text,
    the folder `env -C` moves into)."""
    sudo, chdir = False, ""
    while words:
        t = words[0].text
        if not words[0].quoted and _ASSIGN.fullmatch(t):
            words = words[1:]
            continue
        base = t.rsplit("/", 1)[-1]
        if words[0].dynamic or base not in _PREFIXES:
            break
        if base == "command" and words[1:2] and words[1].text in ("-v", "-V"):
            break
        sudo = sudo or base in ("sudo", "doas")
        takes, words = _PREFIXES[base], words[1:]
        while words and words[0].text.startswith("-") and words[0].text != "-":
            f = words[0].text
            words = words[1:]
            m = base == "env" and re.fullmatch(r"-[a-zA-Z]*?([CSu])(.*)", f)
            if m:
                f = "-" + m.group(1) + m.group(2)      # `-iC DIR`, `-iCDIR`: the first value-taking letter ends the cluster
            if base == "env" and (f in ("-S", "--split-string") or f.startswith(("--split-string=", "-S"))):
                inline = f.split("=", 1)[1] if "=" in f else (f[2:] if f.startswith("-S") and len(f) > 2 else "")
                return [], sudo, inline or " ".join(w.text for w in words), chdir
            if base == "env" and (f.startswith("--chdir=") or (f.startswith("-C") and len(f) > 2)):
                chdir = f[8:] if f.startswith("--chdir=") else f[2:]           # `--chdir=DIR`, `-CDIR`
            if f in takes and words:
                if base == "env" and f in ("-C", "--chdir"):
                    chdir = words[0].text
                words = words[1:]
        if base == "timeout" and words and re.fullmatch(r"[\d.]+[smhd]?", words[0].text):
            words = words[1:]
    return words, sudo, None, chdir


def _trusted_path(prog: str) -> bool:
    """A program named by path runs as itself only from a system bin folder or a virtualenv/node bin."""
    p = _expand_text(prog)
    if _ENV_BIN.search(p):
        return True
    return p.startswith("/") and os.path.dirname(os.path.normpath(p)) in _SYSTEM_BIN


def _stdin_texts(k: _Cmd, piped: str | None) -> list[str]:
    out = [h.body for h in k.heredocs] + [w.text for w in k.herestrings]
    if piped is not None:
        out.append(piped)
    return out


def _classify_simple(k: _Cmd, cmd: str, depth: int, ctx: dict, piped: str | None = None) -> list[Classification]:
    """One simple command into its parts: the program's own category, the redirections, anything run
    inside it, and a store write when it touches the store."""
    words = list(k.words)
    while words and not words[0].quoted and words[0].text in _OPENERS:
        words = words[1:]
    if words and not words[0].quoted and words[0].text == "for":
        words = []                                   # a for header lists words, not programs
    words, sudo, split, chdir = _strip_prefix_words(words)
    if chdir and _in_store_dir(chdir):
        ctx = {**ctx, "store_cwd": True}             # `env -C <store> cmd` runs this one command inside the store
    parts: list[Classification] = []
    if split is not None:
        parts.append(_opaque("env -S runs a command line", cmd))
        if depth < _DEPTH:
            parts.append(classify_shell(split, depth + 1))
    elif words:
        parts += _classify_program(words, k, cmd, depth, ctx, piped)
    else:
        parts.append(_make("read", summary="shell assignment", command=cmd))
    if sudo:
        parts[0].signals.append("sudo")
        if parts[0].reversibility == "reversible":
            parts[0].reversibility = "costly"
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
        c = _make("store_write", summary=f"write to the Exo store: {hit}"[:120], target=hit, link=hit, command=cmd)
        c.signals.append("exo store")
        parts.append(c)
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
            return [_opaque("nested too deep to read", cmd)]
        return _classify_simple(_Cmd(words=ws, heredocs=k.heredocs, herestrings=k.herestrings), cmd,
                                inner_depth, ctx, piped)

    if prog in _OPAQUE_PROGRAMS:
        parts = [_opaque(f"{prog}: {_OPAQUE_PROGRAMS[prog]}", cmd)]
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
        for text in _stdin_texts(k, piped):
            if depth < _DEPTH:
                parts.append(classify_shell(text, inner_depth))
        return parts
    if prog in _INTERPRETERS:
        return _classify_interpreter(prog, argv, k, cmd, depth, ctx, piped)
    if prog == "xargs":
        i = 1
        while i < len(words) and words[i].text.startswith("-"):
            i += 2 if words[i].text in ("-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a") else 1
        return [_opaque("xargs builds a command from its input", cmd)] + inner_words(words[i:])
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
        dest = next((a for a in argv[1:] if not a.startswith("-")), "")
        if dest and _in_store_dir(dest):
            ctx["store_cwd"] = True
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
        return [_classify_gh(argv, cmd, [h.body for h in k.heredocs])]
    if prog in ("curl", "wget", "http", "https", "xh", "httpie"):
        return [_classify_http(argv, cmd)]
    if prog in ("ssh", "mosh"):
        rest, i = argv[1:], 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-m",
                                  "-O", "-Q", "-S", "-w", "-e") else 1
        host = rest[i] if i < len(rest) else ""
        inner = " ".join(rest[i + 1:])
        c = (classify_shell(inner, inner_depth) if inner and depth < _DEPTH
             else _make("shell", summary=f"ssh {host}", command=cmd))
        if EXPOSURE.index(c.exposure) < 1:
            c.exposure = "internal"
        c.target = c.target or host
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
    if prog in READ_PROGRAMS:
        return [_make("read", summary=prog, command=cmd)]
    if prog in SHELL_PROGRAMS:
        return [_make("shell", summary=" ".join(argv[:3])[:80], command=cmd)]
    return [_opaque(f"runs {prog}, a program the gate does not know", cmd, signal="unknown program")]


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
            if mod == "exo.config":
                return [_classify_exo(["exo", "config"] + args[i + 2:], cmd)]
            return [_opaque(f"{prog} -m {mod} runs a module", cmd)]
        if a == "-" or not a.startswith("-"):
            if a != "-":
                return [_opaque(f"{prog} runs the script {a}"[:120], cmd)]
            break
        i += 2 if a in _INTERP_VALUE_FLAGS.get(prog, ()) else 1
    parts = [_opaque(f"{prog} reads code from its input", cmd)]
    parts += [classify_code(t) for t in _stdin_texts(k, piped)]
    return parts


def _classify_exo(argv: list[str], cmd: str) -> Classification:
    sub = argv[1] if len(argv) > 1 else ""
    verb = argv[2] if len(argv) > 2 else ""
    if sub == "providers" and verb in ("add", "remove", "test", "default"):
        return _make("credentials", summary=f"exo providers {verb}", command=cmd)  # D-034: key setup is the user's
    if sub in ("keygen", "init") or (sub == "log" and verb == "append") or \
            (sub == "config" and verb in ("set", "unset")) or (sub == "update" and "--pin-trust" in argv[2:]):
        c = _make("store_write", summary=f"exo {sub} {verb}".strip(), command=cmd)  # writes config.json or update/
        c.signals.append("exo store")
        return c
    if sub in ("find", "get", "ctx", "links", "lint", "graph", "wire", "providers", "--version", "--help", "") or \
            (sub == "log" and verb in ("tail", "verify")):
        return _make("read", summary=f"exo {sub}".strip(), command=cmd)
    return _opaque(f"exo {sub} starts, stops or changes Exo itself", cmd)


def _store_touch(k: _Cmd, words: list, parts: list[Classification], ctx: dict) -> str:
    """What in a simple command writes to the Exo store, or ''. Reads of the store are fine."""
    for op, t in k.redirs:
        if op in _WRITE_REDIRS and is_store_path(t.text):
            return t.text
    if all(p.category == "read" for p in parts):
        return ""
    prog = words[0].text.rsplit("/", 1)[-1] if words else ""
    wide = prog in ("rm", "rmdir", "mv", "chmod", "chown", "chflags", "rsync", "shred", "unlink", "ln") or any(
        p.category == "delete" for p in parts)
    for i, w in enumerate(words):
        for piece in dict.fromkeys([w.text] + re.split(r"[:=,]", w.text)):
            if piece and is_store_path(piece, ancestors=wide):
                return piece
        if prog == "git" and w.text in ("-C", "--work-tree", "--git-dir") and i + 1 < len(words) and \
                _in_store_dir(words[i + 1].text):
            return words[i + 1].text            # git checkout, reset or commit inside the store
    argv = [w.text for w in words]
    for d in _landings(prog, argv, _dir_target(prog, argv)):  # `cp x ~/.exo/`, `tar -C ~/.exo`, `cp -r /tmp/.exo ~/`
        if _is_store_dir(d):
            return d                            # the file, or the folder copied, lands on a protected folder
    if ctx.get("store_cwd"):
        return "a path inside the store (after cd)"
    return ""


_DIR_OPTS = {"cp": ("-t", "--target-directory"), "install": ("-t", "--target-directory"),
             "tar": ("-C", "--directory"), "unzip": ("-d",)}


def _dir_target(prog: str, argv: list[str]) -> str:
    """The folder a copying or extracting program writes into, or ''. `cp -t DIR`, `install --target-directory=DIR`,
    `tar -C DIR` (extracting; creating or listing reads there), `unzip -d DIR`, or a `cp`/`install` destination
    that is a folder (a trailing slash, one that exists, or a relative `.exo` by name). What lands there is
    `DIR/<name of each source>`, so the folder decides (see `_landings`). A short option may end a cluster or
    carry the folder: `-rt DIR`, `-tDIR`, `-xC DIR`."""
    opts = _DIR_OPTS.get(prog)
    if not opts:
        return ""
    if prog == "tar":
        old = argv[1] if argv[1:] and not argv[1].startswith("-") else ""      # old style: `tar xCf DIR FILE`
        if "x" not in old and not any(a in ("--extract", "--get") or re.match(r"-[a-zA-Z]*x", a) for a in argv[1:]):
            return ""
        if "C" in old:
            takers = [ch for ch in old if ch in "CfT"]                      # each takes the next operand, in order
            return argv[2 + takers.index("C")] if 2 + takers.index("C") < len(argv) else ""
    for i, a in enumerate(argv[1:], 1):
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        for o in opts:
            if a == o:
                return nxt
            if a.startswith(o + "="):
                return a.split("=", 1)[1]
            m = len(o) == 2 and re.fullmatch(rf"-[a-zA-Z]*?{o[1]}(.*)", a)   # the first such letter ends the cluster
            if m:
                return m.group(1) or nxt
    if prog in ("cp", "install"):
        operands = [a for a in argv[1:] if not a.startswith("-")]
        dest = operands[-1] if len(operands) > 1 else ""
        p = _expand_text(dest)
        if dest.endswith("/") or os.path.isdir(p) or (not p.startswith("/") and re.search(r"(^|/)\.exo$", p)):
            return dest                  # a relative `.exo` is the home by name; isdir() would ask the gate's own cwd
    return ""


def _landings(prog: str, argv: list[str], dest: str) -> list[str]:
    """The folders a copy into ``dest`` writes: ``dest`` itself and, for `cp`/`install`, `dest/<name of each
    source>`: `cp -r /tmp/.exo ~/` merges into the home and overwrites `config.json` without naming either."""
    if not dest:
        return []
    names = [os.path.basename(a.rstrip("/")) for a in argv[1:] if not a.startswith("-") and a != dest] \
        if prog in ("cp", "install") else []
    return [dest] + [os.path.join(dest, n) for n in names if n]


def _combine(parts: list[Classification], cmd: str) -> Classification:
    """The most severe part, with every part's signals. A store write outranks everything but credentials."""
    signals = [s for p in parts for s in p.signals]
    credentials = [p for p in parts if p.category == "credentials"]
    store = [p for p in parts if p.category == "store_write"]
    c = credentials[0] if credentials else (store[0] if store else (_most_severe(parts) or _make("shell", command=cmd)))
    c.signals = list(dict.fromkeys(c.signals + signals))
    return c


def _piped_text(k: _Cmd) -> str | None:
    """What a simple command writes to a pipe, when the gate can read it: `cat <<EOF`, `echo`, `printf`."""
    words = [w for w in k.words]
    if not words or any(w.dynamic for w in words):
        return None
    prog = words[0].text
    if prog == "cat" and len(words) == 1 and (k.heredocs or k.herestrings):
        return "\n".join([h.body for h in k.heredocs] + [w.text for w in k.herestrings])
    if prog in ("echo", "printf"):
        return " ".join(w.text for w in words[1:] if not (prog == "echo" and w.text in ("-n", "-e", "-E")))
    return None


def classify_shell(cmd: str, depth: int = 0, cwd: str = "") -> Classification:
    """Classify a shell command line; a compound line takes its most severe part. ``cwd`` is the folder the
    tool runs it in, when the call names one: inside the store it counts like a leading `cd` there."""
    cmds, subs, func = _parse(cmd or "")
    ctx: dict = {"store_cwd": True} if cwd and _in_store_dir(cwd) else {}
    parts: list[Classification] = []
    prev: _Cmd | None = None
    for k in cmds:
        piped = _piped_text(prev) if (k.piped_in and prev is not None) else None
        parts += _classify_simple(k, cmd, depth, ctx, piped)
        prev = k
    for sub in subs:
        parts.append(classify_shell(sub, depth + 1) if depth < _DEPTH else _opaque("nested too deep to read", cmd))
    if func:
        parts.append(_opaque("defines a shell function", cmd))
    c = _combine(parts, cmd)
    segs = [[w.text for w in k.words] for k in cmds if k.words]
    hit = _shell_credential_hit(cmd, segs)
    if hit and c.category != "credentials":
        rev = max((p.reversibility for p in parts), key=REVERSIBILITY.index, default="costly")
        signals = c.signals
        c = _make("credentials", summary=f"shell touching {hit}"[:120], target=hit if "/" in hit or "." in hit else "",
                  command=cmd, reversibility=max(rev, "costly", key=REVERSIBILITY.index))
        c.signals = signals + ["secret store"]
    c.command = cmd
    # Outbound text: if several gh segments post text, keep the most severe one's text.
    bodies = [h.body for k in cmds for h in k.heredocs]
    if not c.text and c.category in OUTBOUND and bodies and c.category != "web_post":
        c.text = bodies[0]
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


def classify_code(code: str) -> Classification:
    """Code the gate cannot run: look for known calls, and read each string literal (and each list of
    string literals, as in subprocess.run([...])) as a shell command. Only results that matter count."""
    code = code or ""
    parts = [_make("code", summary="run code")]
    for rx, cat in _CODE_PATTERNS:
        m = rx.search(code)
        if m:
            parts.append(_make(cat, summary=f"code calls {m.group(0)[:40]}"))
    texts = [m.group(2) for m in _CODE_STRINGS.finditer(code)]
    texts += [" ".join(shlex.quote(s) for _, s in re.findall(r"(['\"])((?:(?!\1).)*)\1", m.group(1)))
              for m in _CODE_LISTS.finditer(code)]
    for s in texts[:200]:
        c = classify_shell(s, depth=_DEPTH - 1)
        if c.category not in _BENIGN:
            parts.append(c)
    c = _combine(parts, code)
    c.tool = "execute_code"
    c.command = code
    c.signals = list(dict.fromkeys(c.signals + ["indirection"]))
    return c


# --- tool table ----------------------------------------------------------------------------

TOOLS: dict[str, str] = {
    **{t: "read" for t in (
        "read_file", "search_files", "web_search", "session_search", "skills_list", "skill_view",
        "vision_analyze", "video_analyze", "read_terminal", "read_window_below", "x_search", "clarify",
        "show_tip", "gui_tour", "focus_pane", "desktop_preview", "drive_preview", "feishu_doc_read",
        "setup_choose", "browser_vault_list", "todo_list", "list_dir", "grep", "glob")},
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
    r"proc/[^/]+/environ)(/|$)")

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


def _expand_text(text: str) -> str:
    """~, $HOME and $EXO_HOME (also ${...}) spelled out; nothing else is evaluated."""
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


def is_sensitive_path(path: str) -> bool:
    """True when ``path`` names one of the secret stores (ssh, cloud and netrc credentials, Exo's key
    stores, signing keys, token and client-secret files, .env files). Never reads the file."""
    path = _expand_text(str(path or "")).strip()
    if not path:
        return False
    if path.endswith(".pub"):
        return False  # public keys are meant to be shared
    if _SENSITIVE_PATH.search(path.replace("\\", "/")):
        return True
    try:
        cands = {os.path.abspath(path), os.path.realpath(path)}
    except (OSError, ValueError):
        cands = {path}
    for root in _dynamic_secret_paths():
        roots = {os.path.abspath(root), os.path.realpath(root)}
        if any(c == r or c.startswith(r + os.sep) for c in cands for r in roots):
            return True
    return False


def _glob_hits_secret(tok: str) -> bool:
    """A shell glob that could expand to a secret store. Bare `*` or `*.json` do not count; the last
    component needs three literal characters, or the directory must itself be a secret store."""
    tok = _expand_text(tok)
    if not re.search(r"[*?\[]", tok):
        return False
    d, _, base = tok.rpartition("/")
    stem = re.sub(r"[*?\[\]]", "", base.rsplit(".", 1)[0] if "." in base[1:] else base)
    home = _home()
    samples = [os.path.join(home, x) for x in _SENSITIVE_SAMPLES] + [os.path.join(_exo_home(), "keys", "seat.key"),
                                                                       _dynamic_secret_paths()[1]]
    d_abs = os.path.normpath(d if d.startswith("/") else os.path.join(home, d)) if d and not re.search(r"[*?\[]", d) else ""
    in_store_dir = bool(d_abs) and d_abs != home and any(x.startswith(d_abs + "/") for x in samples)
    if len(stem) < 3 and not in_store_dir:
        return False
    pats = [tok] if tok.startswith("/") else [tok, "*/" + tok]
    samples += list(_SENSITIVE_SAMPLES)
    return any(fnmatch.fnmatch(x, pat) for x in samples for pat in pats)


# D-038: the Exo store is credentials. Its policies, state (approval grants), ledger, keys, seat files and
# the core sockets steer the gate itself, so a write to them always asks the principal. Names that are
# distinctive match anywhere (a relative `policies/gates.yaml` may sit in the tool's working folder);
# absolute paths are also checked against EXO_HOME, its principal stores and EXO_PRINCIPAL_STORE.
_STORE_NAMES = re.compile(
    r"(^|/)(\.exo/(principals/[^/]+/)?(policies|state|keys|run|seats)(/|$)|\.exo/(principals/[^/]+/)?ledger\.sqlite|"
    r"\.exo/(update(/|$)|config\.json$)|"
    r"policies/(gates|budget|interruptions|approvals|posting|brief|consolidate)\.(ya?ml|md)$|"
    r"policies/internal-names\.txt$|state/(approval|gate)-grants\.json$|update/allowed_signers$|"
    r"(ledger|schedule)\.sqlite(-wal|-shm|-journal)?$|"
    r"seat\.(toml|json)$|(exod|agent-[^/]*)\.sock$)")
# `config.json` names the plugins the core loads (the gate among them), the plugin config, the models and the
# interpreter `exo update` runs; `update/` holds the pinned trust list (D-073). Both steer the gate or the
# code that runs as the principal, so they are protected like the rest (D-074).
_HOME_PROTECTED = ("policies", "state", "keys", "run", "seats", "update")
_HOME_FILES = ("config.json",)
_STORE_PROTECTED = ("policies", "state", "keys")


def _store_roots() -> list[tuple[str, tuple, bool]]:
    """(root, protected top-level names, has principals/<name>/ stores below it)."""
    roots: list[tuple[str, tuple, bool]] = [(os.path.normpath(_exo_home()), _HOME_PROTECTED, True)]
    if os.environ.get("EXO_PRINCIPAL_STORE"):
        roots.append((os.path.normpath(os.path.expanduser(os.environ["EXO_PRINCIPAL_STORE"])), _STORE_PROTECTED,
                      False))
    return roots


def _protected_rel(parts: list[str], names: tuple, files: tuple = ()) -> bool:
    return bool(parts) and (parts[0] in names or parts[0].startswith(("ledger.sqlite", "schedule.sqlite"))
                            or (len(parts) == 1 and parts[0] in files))


def is_store_path(path: str, ancestors: bool = False) -> bool:
    """True when ``path`` is (or, with ``ancestors``, contains) a protected part of the Exo store."""
    p = _expand_text(str(path or "")).strip().replace("\\", "/")
    if not p:
        return False
    if _STORE_NAMES.search(p):
        return True
    if not p.startswith("/"):
        return False                     # relative: names only; the tool's working folder is not known here
    p = "/" + os.path.normpath(p).lstrip("/")        # normpath keeps a leading `//`
    for root, names, principals in _store_roots():
        for r in {root, os.path.realpath(root)}:
            if ancestors and (r == p or r.startswith(p.rstrip("/") + "/")):
                return True
            if not p.startswith(r + "/"):
                continue
            parts = p[len(r) + 1:].split("/")
            if _protected_rel(parts, names, _HOME_FILES if principals else ()):
                return True
            if principals and parts[0] == "principals":
                if len(parts) <= 2 and ancestors:
                    return True
                if _protected_rel(parts[2:], _STORE_PROTECTED):
                    return True
    return False


def _in_store_dir(path: str) -> bool:
    """A `cd` target inside EXO_HOME or the principal store: relative names after it may be the store."""
    p = _expand_text(path).strip()
    if not p.startswith("/"):
        return bool(re.search(r"(^|/)\.exo(/|$)", p))
    p = "/" + os.path.normpath(p).lstrip("/")
    return any(p == r or p.startswith(r + "/") for r, _, _ in _store_roots())


def _is_store_dir(path: str) -> bool:
    """A folder whose entries are protected: the home or the principal store itself (`config.json`, `update/`
    and the rest sit right under it), or a protected folder below it (`update/`, `policies/`)."""
    p = _expand_text(path).strip()
    if not p.startswith("/"):
        p = os.path.normpath(p)          # relative: names only, as in is_store_path; `.exo` is the home by name
        return bool(re.search(r"(^|/)\.exo$", p)) or is_store_path(p)
    p = "/" + os.path.normpath(p).lstrip("/")
    return any(p == os.path.normpath(r) for r, _, _ in _store_roots()) or is_store_path(p)


_PATCH_PATHS = re.compile(r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$|^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+?)\s*$",
                          re.M)


def _patch_paths(text: str) -> list[str]:
    return [p for m in _PATCH_PATHS.finditer(text or "") for p in m.groups() if p]


_PATH_ARG_KEYS = ("path", "file_path", "file", "filename", "paths", "files", "directory", "dir", "root", "source",
                  "src", "dest", "destination")


def args_sensitive(args: Mapping) -> str:
    """The first path-like tool argument that names a secret store, or ''."""
    for k in _PATH_ARG_KEYS:
        v = args.get(k)
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            if isinstance(item, str) and item and (is_sensitive_path(item) or _glob_hits_secret(item)):
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
        rest = [a for a in rest if not re.fullmatch(r"[A-Za-z_]\w*=.*", a)]
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


def _shell_credential_hit(cmd: str, segs: list[list[str]]) -> str:
    """What in a shell command touches a secret store: a path (also through ~, $HOME, a glob, a
    redirection or a quoted `bash -c` string), the Keychain, or a bare environment dump. '' if nothing."""
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
        if p and (is_sensitive_path(p) or _glob_hits_secret(p)):
            return p
    return ""


def _strip_prefixes_keep_env(argv: list[str]) -> tuple[list[str], bool]:
    """Like _strip_prefixes but `env` itself is kept so a bare `env` can be seen as a dump."""
    while argv and (re.fullmatch(r"[A-Za-z_]\w*=.*", argv[0]) or argv[0].rsplit("/", 1)[-1] in ("sudo", "doas", "command", "builtin", "exec", "nohup", "time")):
        argv = argv[1:]
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


def classify(action: Any) -> Classification:
    """Classify one tool call from its name and arguments. Pure; never raises on odd args."""
    tool, args = _args_of(action)
    if tool in ("terminal", "shell", "bash", "run_command", "exec_command") or (
            tool not in TOOLS and isinstance(args.get("command"), str) and tool.endswith(("terminal", "shell"))):
        cwd = args.get("cwd")
        c = classify_shell(str(args.get("command") or args.get("cmd") or ""), cwd=cwd if isinstance(cwd, str) else "")
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


def _rule_matches(match: Mapping, c: Classification) -> bool:
    for k, want in match.items():
        if k == "category" and c.category not in [str(x) for x in _as_list(want)]:
            return False
        if k == "tool" and not _glob_any(c.tool, _as_list(want)):
            return False
        if k == "target" and not _glob_any(c.target, _as_list(want)):
            return False
        if k == "command" and not any(re.search(str(p), c.command or "") for p in _as_list(want)):
            return False
        if k == "exposure" and c.exposure not in _as_list(want):
            return False
        if k == "reversibility" and c.reversibility not in _as_list(want):
            return False
        if k == "third_party" and bool(want) != c.third_party:
            return False
        if k not in ("category", "tool", "target", "command", "exposure", "reversibility", "third_party"):
            return False      # unknown key: never match, so a typo cannot widen a rule
    return True


def _list_matches(entries: Iterable, c: Classification) -> str:
    """Reserved/deny entries: a category name, `tool:<glob>`, `command:<regex>`, `target:<glob>`,
    or a mapping like a rule's match. Plain prose that matches none of these is ignored."""
    for e in entries:
        if isinstance(e, Mapping):
            if e and _rule_matches(e.get("match", e), c):
                return str(e.get("reason") or e)
            continue
        s = str(e).strip()
        if s == c.category:
            return s
        kind, _, val = s.partition(":")
        if kind == "tool" and _glob_any(c.tool, [val.strip()]):
            return s
        if kind == "target" and _glob_any(c.target, [val.strip()]):
            return s
        if kind == "command" and val.strip() and re.search(val.strip(), c.command or ""):
            return s
    return ""


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
    or raise). Then only raises: reserved actions, escalations (budget, third party, listed
    categories), posting violations and the operator floor. A deny entry wins over everything.
    `context` may carry `spent_today` for the daily budget.
    """
    pol = policies if (policies and policies.get("_normalized")) else normalize_policies(policies)
    gates, budget, posting = pol["gates"], pol["budget"], pol["posting"]
    ctx = dict(context or {})
    exposure, third = apply_scopes(c, gates.get("scopes") or {})
    eff = Classification(**{**asdict(c), "exposure": exposure, "third_party": third})

    outcome = gates["matrix"][eff.reversibility][eff.exposure]
    reason, rule = f"{eff.reversibility} and {eff.exposure}", "matrix"
    # D-038: the principal may lower opaque code only by naming it (a category line or a rule on opaque_exec).
    opaque_named = "opaque_exec" in gates.get("categories", {})
    if eff.category in gates.get("categories", {}):
        outcome, reason, rule = gates["categories"][eff.category], f"category {eff.category}", "category"
    for r in gates.get("rules") or []:
        if _rule_matches(r["match"], eff):
            outcome, reason, rule = r["decision"], r["reason"], "rule"
            opaque_named = opaque_named or "opaque_exec" in [str(x) for x in _as_list(r["match"].get("category", []))]
            break

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

    if (eff.category == "opaque_exec" or "indirection" in eff.signals) and not opaque_named:
        bump("ask", "runs code the gate cannot fully see (" + (c.summary or "indirection") + ")", "opaque")
    hit = _list_matches(gates.get("reserved") or [], eff)
    if hit:
        bump("ask", f"reserved for the principal ({hit})", "reserved")
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
    if eff.category in (posting.get("applies_to") or POSTING_DEFAULT) and (eff.text or eff.title):
        violations = check_post(eff.text, posting, title=eff.title, agents=pol.get("agents") or [],
                                exposure=eff.exposure)
        violations += check_scrub(eff.text, pol.get("scrub"), title=eff.title, exposure=eff.exposure)  # D-033
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

    hit = _list_matches(gates.get("deny") or [], eff)
    if hit:
        outcome, reason, rule = "deny", f"denied by policy ({hit})", "deny"
    return Decision(outcome=outcome, reason=reason, rule=rule, violations=violations,
                    exposure=eff.exposure, third_party=eff.third_party)


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


def check_scrub(text: str, scrub: Mapping | None, *, title: str = "", exposure: str = "public") -> list[str]:
    """Internal identifiers found in outbound text, as ``"<label>: <match>"``. Empty = clean."""
    if not scrub or not scrub.get("enabled") or exposure not in (scrub.get("exposures") or []):
        return []
    both = "\n".join(x for x in (title, text) if x)
    if not both:
        return []
    allow = set(scrub.get("allow") or [])
    out: list[str] = []
    for r in scrub.get("rules") or []:
        for m in re.finditer(r["pattern"], both, re.I):
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
