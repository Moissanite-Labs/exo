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
}

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


# --- heredocs and shell splitting ----------------------------------------------------------

_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$|\))",
                      re.S)


def _strip_heredocs(cmd: str) -> tuple[str, list[str]]:
    bodies: list[str] = []

    def keep(m):
        bodies.append(m.group(3))
        return "__HEREDOC%d__" % (len(bodies) - 1)

    return _HEREDOC.sub(keep, cmd), bodies


def _newlines_to_semicolons(cmd: str) -> str:
    out, q = [], ""
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "\\" and q != "'" and i + 1 < len(cmd):
            if cmd[i + 1] == "\n":       # line continuation
                i += 2
                continue
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if q:
            if ch == q:
                q = ""
        elif ch in "'\"":
            q = ch
        elif ch == "\n":
            ch = ";"
        out.append(ch)
        i += 1
    return "".join(out)


_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&", "$("}


def split_commands(cmd: str) -> tuple[list[list[str]], list[str]]:
    """Split a shell command line into argv lists. Returns (segments, heredoc bodies)."""
    flat, bodies = _strip_heredocs(cmd)
    flat = _newlines_to_semicolons(flat)
    try:
        lex = shlex.shlex(flat, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = flat.replace(";", " ; ").replace("&&", " && ").replace("|", " | ").split()
    segs, cur = [], []
    for t in tokens:
        if t in _SEPARATORS or (t and set(t) <= set(";&|()")):
            if cur:
                segs.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        segs.append(cur)
    # "$(cat" style leftovers: drop leading "$" tokens
    segs = [[a for a in s if a != "$"] for s in segs]
    return [s for s in segs if s], bodies


_PREFIXES = {"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice", "ionice", "xargs",
             "caffeinate", "timeout", "stdbuf", "builtin"}


def _strip_prefixes(argv: list[str]) -> tuple[list[str], bool]:
    sudo = False
    while argv:
        a = argv[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", a):
            argv = argv[1:]
            continue
        base = a.rsplit("/", 1)[-1]
        if base in _PREFIXES:
            sudo = sudo or base in ("sudo", "doas")
            argv = argv[1:]
            # skip the prefix's own flags (and a timeout duration)
            while argv and (argv[0].startswith("-") or (base == "timeout" and re.fullmatch(r"[\d.]+[smhd]?", argv[0]))):
                argv = argv[1:]
            continue
        break
    return argv, sudo


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


def _resolve_text(value: str | None, bodies: list[str]) -> str:
    """Turn a flag value into text, replacing heredoc placeholders with their bodies."""
    if not value:
        return ""
    if "__HEREDOC" not in value:
        return value
    bare = re.sub(r"^\s*(\$\(|`)\s*(cat\s*)?|\s*(\)|`)\s*$", "", value).strip()
    m = re.fullmatch(r"__HEREDOC(\d+)__", bare)
    if m:
        return bodies[int(m.group(1))]
    return re.sub(r"__HEREDOC(\d+)__", lambda mm: bodies[int(mm.group(1))], value)


# --- shell rules (table driven) ------------------------------------------------------------

# program -> category, when the program alone decides it
PROGRAMS = {
    "rm": "delete", "rmdir": "delete", "shred": "delete", "unlink": "delete", "srm": "delete",
    "mkfs": "delete", "wipefs": "delete", "dd": "delete",
    "sendmail": "email_send", "mail": "email_send", "mailx": "email_send", "mutt": "email_send",
    "msmtp": "email_send", "swaks": "email_send",
    "ssh-keygen": "credentials", "ssh-add": "credentials", "passwd": "credentials",
    "crontab": "schedule",
    "cat": "read", "ls": "read", "grep": "read", "rg": "read", "head": "read", "tail": "read",
    "find": "read", "pwd": "read", "echo": "read", "wc": "read", "diff": "read", "less": "read",
    "which": "read", "stat": "read", "jq": "read", "sed": "shell", "awk": "read", "sort": "read",
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
    ("az", "login"): "credentials", ("vercel", "login"): "credentials", ("npm", "login"): "credentials",
    ("security", "add-generic-password"): "credentials", ("security", "delete-generic-password"): "credentials",
    ("launchctl", "load"): "schedule", ("launchctl", "bootstrap"): "schedule", ("systemctl", "enable"): "schedule",
}

_DATA_FLAGS = ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form",
               "--json", "-T", "--upload-file", "--form-string")
_SQL_DESTRUCTIVE = re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+|delete\s+from)\b", re.I)
_URL = re.compile(r"https?://[^\s'\"<>]+")
_GH_URL = re.compile(r"https?://github\.com/([\w.-]+/[\w.-]+)(?:/(?:pull|issues|discussions)/(\d+))?")


def _classify_git(argv: list[str], cmd: str) -> Classification:
    sub = argv[1] if len(argv) > 1 else ""
    rest = argv[2:]
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
    if sub == "filter-branch" or sub == "filter-repo":
        return _make("history_rewrite", summary=f"git {sub}", command=cmd)
    if sub in ("status", "log", "diff", "show", "fetch", "branch", "remote", "rev-parse", "blame", "ls-files"):
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
    target, link = _gh_target(argv)
    body = _resolve_text(_opt(argv, "--body", "-b"), bodies)
    title = _resolve_text(_opt(argv, "--title", "-t"), bodies)
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


def _classify_argv(argv: list[str], cmd: str, bodies: list[str], depth: int = 0) -> Classification:
    argv, sudo = _strip_prefixes(argv)
    if not argv:
        return _make("shell", command=cmd)
    prog = argv[0].rsplit("/", 1)[-1]
    sub = argv[1] if len(argv) > 1 else ""
    c: Classification | None = None
    if prog in ("bash", "sh", "zsh", "fish", "dash") and "-c" in argv:
        inner = argv[argv.index("-c") + 1] if argv.index("-c") + 1 < len(argv) else ""
        c = classify_shell(inner, depth + 1) if depth < 3 else None
    elif prog == "eval" and depth < 3:
        c = classify_shell(" ".join(argv[1:]), depth + 1)
    elif prog in ("ssh", "mosh") and depth < 3:
        rest = [a for a in argv[1:]]
        # ssh [opts] host cmd...
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D") else 1
        host = rest[i] if i < len(rest) else ""
        inner = " ".join(rest[i + 1:])
        c = classify_shell(inner, depth + 1) if inner else _make("shell", summary=f"ssh {host}")
        if EXPOSURE.index(c.exposure) < 1:
            c.exposure = "internal"
        c.target = c.target or host
        c.signals.append("remote host")
    elif prog == "git":
        c = _classify_git(argv, cmd)
    elif prog == "gh":
        c = _classify_gh(argv, cmd, bodies)
    elif prog in ("curl", "wget", "http", "https", "xh", "httpie"):
        c = _classify_http(argv, cmd)
    elif prog in ("vercel", "netlify"):
        if "login" in argv:
            c = _make("credentials", summary=f"{prog} login", command=cmd)
        elif "--prod" in argv or "--production" in argv or sub in ("promote", "alias"):
            c = _make("deploy_production", summary=f"{prog} production deploy", command=cmd)
        elif sub in ("", "deploy") or sub.startswith("-"):
            c = _make("deploy_preview", summary=f"{prog} preview deploy", command=cmd)
        elif sub in ("remove", "rm"):
            c = _make("delete", exposure="public", summary=f"{prog} remove", command=cmd)
        else:
            c = _make("shell", summary=f"{prog} {sub}", command=cmd)
    elif prog == "stripe":
        joined = " ".join(argv[1:]).lower()
        if re.search(r"\b(create|confirm|capture|pay)\b", joined) and re.search(
                r"(charge|payment|payout|transfer|invoice|refund|subscription|checkout)", joined):
            amt = _opt(argv, "--amount", "-d")
            try:
                cost = float(re.sub(r"[^\d.]", "", amt.split("=")[-1])) if amt else None
            except ValueError:
                cost = None
            c = _make("payment", summary="Stripe payment", cost=cost, command=cmd)
        else:
            c = _make("read", summary=f"stripe {sub}", command=cmd)
    elif prog in ("psql", "mysql", "sqlite3", "duckdb") and _SQL_DESTRUCTIVE.search(cmd):
        c = _make("delete", summary=f"destructive SQL via {prog}", command=cmd)
    elif prog == "rsync" and _has(argv, "--delete", "--delete-after", "--delete-before", "--remove-source-files"):
        c = _make("delete", summary="rsync with delete", command=cmd)
    elif prog == "mv" and len(argv) > 2 and argv[-1] in ("/dev/null",):
        c = _make("delete", summary="move to /dev/null", command=cmd)
    elif prog == "chmod" or prog == "chown":
        c = _make("shell", summary=f"{prog}", command=cmd)
        if any(".ssh" in a or a.endswith((".pem", ".key")) for a in argv):
            c = _make("credentials", summary=f"{prog} on key material", command=cmd)
    elif (prog, sub) in SUBCOMMANDS:
        c = _make(SUBCOMMANDS[(prog, sub)], summary=f"{prog} {sub}", command=cmd)
    elif prog in PROGRAMS:
        cat = PROGRAMS[prog]
        target = next((a for a in reversed(argv[1:]) if not a.startswith("-")), "")
        c = _make(cat, summary=f"{prog} {target}".strip() if cat == "delete" else prog, target=target if cat == "delete" else "",
                  link=target if cat == "delete" else "", command=cmd)
        if prog == "rm" and _has(argv, "-r", "-R", "--recursive") and _has(argv, "-f", "--force"):
            c.signals.append("recursive force delete")
        if prog == "crontab" and _has(argv, "-l"):
            c = _make("read", summary="crontab -l", command=cmd)
        if prog == "sed" and _has(argv, "-i"):
            c = _make("local_write", summary="sed -i", command=cmd)
    if c is None:
        c = _make("shell", summary=" ".join(argv[:3])[:80], command=cmd)
    if sudo:
        c.signals.append("sudo")
        if c.reversibility == "reversible":
            c.reversibility = "costly"
    return c


def classify_shell(cmd: str, depth: int = 0) -> Classification:
    """Classify a shell command line; a compound line takes its most severe part."""
    segs, bodies = split_commands(cmd or "")
    parts = [_classify_argv(s, cmd, bodies, depth) for s in segs]
    c = _most_severe(parts) or _make("shell", command=cmd)
    hit = _shell_credential_hit(cmd, segs)
    if hit and c.category != "credentials":
        rev = max((p.reversibility for p in parts), key=REVERSIBILITY.index, default="costly")
        c = _make("credentials", summary=f"shell touching {hit}"[:120], target=hit if "/" in hit or "." in hit else "",
                  command=cmd, reversibility=max(rev, "costly", key=REVERSIBILITY.index))
        c.signals.append("secret store")
    c.command = cmd
    # Outbound text: if several gh segments post text, keep the most severe one's text.
    if not c.text and c.category in OUTBOUND and bodies and c.category != "web_post":
        c.text = bodies[0]
    return c


# --- code (execute_code) -------------------------------------------------------------------

_CODE_PATTERNS = [
    (re.compile(r"\b(shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|Path\([^)]*\)\.unlink|\.rmtree\()"), "delete"),
    (re.compile(r"\b(requests|httpx|session|client|aiohttp\.ClientSession\(\))\.(post|put|patch|delete)\("), "web_post"),
    (re.compile(r"\burllib\.request\.urlopen\([^)]*data="), "web_post"),
    (re.compile(r"\bsmtplib\.SMTP"), "email_send"),
    (re.compile(r"\bstripe\.\w+\.create\("), "payment"),
]
_CODE_STRINGS = re.compile(r"(['\"])((?:(?!\1).){3,400})\1")


def classify_code(code: str) -> Classification:
    parts = [_make("code", summary="run code")]
    for rx, cat in _CODE_PATTERNS:
        m = rx.search(code or "")
        if m:
            parts.append(_make(cat, summary=f"code calls {m.group(0)[:40]}"))
    for m in _CODE_STRINGS.finditer(code or ""):
        s = m.group(2)
        first = s.split()[0] if s.split() else ""
        if first in ("rm", "git", "gh", "curl", "wget", "npm", "vercel", "kubectl", "terraform", "docker", "sendmail"):
            parts.append(classify_shell(s, depth=1))
    c = _most_severe(parts)
    c.tool = "execute_code"
    c.command = code or ""
    return c


# --- tool table ----------------------------------------------------------------------------

TOOLS: dict[str, str] = {
    **{t: "read" for t in (
        "read_file", "search_files", "web_search", "session_search", "skills_list", "skill_view",
        "vision_analyze", "video_analyze", "read_terminal", "read_window_below", "x_search", "clarify",
        "show_tip", "gui_tour", "focus_pane", "desktop_preview", "drive_preview", "feishu_doc_read",
        "setup_choose", "browser_vault_list", "todo_list", "list_dir", "grep", "glob")},
    **{t: "local_write" for t in ("write_file", "patch", "memory", "skill_manage", "apply_layout",
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
    r"(^|/)(\.ssh|\.gnupg|\.aws|\.config/gcloud|\.netrc|\.npmrc|\.pypirc|\.env(\.[\w-]+)?|"
    r"credentials(\.json)?|id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]+\.pem|[^/]+\.key|"
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
    text = re.sub(r"\$\{?EXO_HOME\}?", lambda m: exo, text)
    text = re.sub(r"\$\{?HOME\}?", lambda m: home, text)
    return re.sub(r"(^|(?<=[\s'\"=:<>]))~(?=/|$|\s|['\"])", lambda m: m.group(1) + home, text)


def _dynamic_secret_paths() -> list[str]:
    paths = [os.path.join(_exo_home(), "keys")]
    kf = os.environ.get("EXO_KEYS_FILE")
    paths.append(os.path.expanduser(kf) if kf else os.path.join(_home(), ".config", "exo", "keys.env"))
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
        c = classify_shell(str(args.get("command") or args.get("cmd") or ""))
    elif tool == "execute_code":
        c = classify_code(str(args.get("code") or ""))
    elif tool in ("write_file", "patch", "edit_file"):
        path = _first(args, ("path", "file_path"))
        cat = "credentials" if (is_sensitive_path(path) or args_sensitive(args)) else "local_write"
        c = _make(cat, summary=f"write {path}", target=path, link=path)
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
        c = _make("read" if action in ("list", "get", "status", "") else "schedule", summary=f"cron {action}")
    else:
        cat = TOOLS.get(tool) or classify_by_name(tool)
        secret = args_sensitive(args) if cat in ("read", "local_write", "unknown", "remote_write", "delete") else ""
        if secret:
            cat = "credentials"
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
    if footer and text and exposure != "private" and not text.rstrip().endswith(footer):
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
    if eff.category in gates.get("categories", {}):
        outcome, reason, rule = gates["categories"][eff.category], f"category {eff.category}", "category"
    for r in gates.get("rules") or []:
        if _rule_matches(r["match"], eff):
            outcome, reason, rule = r["decision"], r["reason"], "rule"
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
        if violations:
            bump("ask", "posting rules: " + "; ".join(violations), "posting")

    # Operator floor (P11): outside the principal model, cannot be lowered by policy.
    if eff.reversibility == "irreversible" and eff.exposure == "public" and eff.third_party:
        bump("ask", "irreversible, public and affects a third party", "floor")

    # Credentials floor: no policy, rule, scope or escalate setting lowers this. The principal answers.
    if eff.category == "credentials":
        bump("ask", "credentials always ask the principal", "floor")

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
    return [pdir / n for n in ("gates.yaml", "budget.yaml", "interruptions.yaml", "posting.md", "posting.yaml")]


def load_policies(root: str | Path | None) -> dict:
    """Read `<root>/policies/` and `<root>/agents/` into normalized policies. A missing root or
    missing files fall back to the defaults (which ask on anything irreversible or public)."""
    if not root:
        return normalize_policies({})
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
    return normalize_policies(raw)
