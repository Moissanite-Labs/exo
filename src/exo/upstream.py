"""Provenance of lifted Hermes code and the ``exo upstream`` tool (docs/STANDALONE.md 3.1, 3.2).

``PROVENANCE.toml`` at the repo root has one ``[[module]]`` per Exo file lifted or ported from an
upstream repo, ``[[watch]]`` entries for upstream areas Exo wants to hear about, and one
``[upstream.<source>]`` table per upstream. Agents may write ``PROVENANCE*.part.toml``
files with the same shape; ``merge_parts`` folds them into the main file.

Commands: ``exo upstream report`` (ranked upstream commits since each recorded commit),
``exo upstream mark`` (move a module's commit after a port or a recorded refusal),
``exo upstream check`` (schema and consistency) and ``exo upstream merge`` (fold in parts).
Stdlib only; reads TOML with tomllib and edits the file as text so comments survive.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROVENANCE = "PROVENANCE.toml"
PART_GLOB = "PROVENANCE*.part.toml"  # anywhere in the tree, e.g. src/exo/core/models/PROVENANCE.part.toml
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
MODES = ("lift", "port", "watch")
MODULE_KEYS = {"exo", "mode", "upstream", "commit", "reconciled", "changes", "source", "outcome"}
MODULE_REQUIRED = ("exo", "mode", "upstream", "commit")
WATCH_KEYS = {"upstream", "why", "commit", "source"}
SOURCE_KEYS = {"repo", "license", "notice", "commit"}
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
# A lifted or ported file says so in its first lines, for example
# "# Lifted from hermes-agent: agent/memory_provider.py" (any comment or docstring style).
HEADER_RE = re.compile(r"(?im)^\W{0,4}(lifted|ported)\s+from\s+(hermes|nousresearch)")
HEADER_LINES = 40


class ProvenanceError(ValueError):
    """PROVENANCE.toml (or a part) breaks the schema."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Module:
    exo: str
    mode: str
    upstream: list[str]
    commit: str
    reconciled: str = ""
    changes: str = ""
    source: str = "hermes"
    outcome: str = ""


@dataclass
class Watch:
    upstream: list[str]
    why: str = ""
    commit: str = ""
    source: str = "hermes"


@dataclass
class Provenance:
    sources: dict[str, dict[str, Any]]
    modules: list[Module]
    watch: list[Watch]
    path: Path | None = None

    def module(self, exo: str) -> Module | None:
        return next((m for m in self.modules if m.exo == exo), None)


# ---------------------------------------------------------------- schema


def _check_entry(d: Any, where: str, keys: set[str], required: tuple[str, ...]) -> list[str]:
    if not isinstance(d, dict):
        return [f"{where}: not a table"]
    probs = [f"{where}: unknown key {k!r}" for k in sorted(set(d) - keys)]
    probs += [f"{where}: missing {k!r}" for k in required if k not in d]
    up = d.get("upstream")
    if "upstream" in d and (not isinstance(up, list) or not up or not all(isinstance(u, str) and u for u in up)):
        probs.append(f"{where}: upstream must be a non-empty list of paths")
    for k in keys - {"upstream"}:
        if k in d and not isinstance(d[k], str):
            probs.append(f"{where}: {k} must be a string")
    if isinstance(d.get("commit"), str) and d["commit"] and not COMMIT_RE.match(d["commit"]):
        probs.append(f"{where}: commit {d['commit']!r} is not a hex sha (7 to 40 chars)")
    if "mode" in d and d["mode"] not in MODES:
        probs.append(f"{where}: mode {d['mode']!r} not one of {MODES}")
    if isinstance(d.get("reconciled"), str) and d["reconciled"]:
        try:
            dt.date.fromisoformat(d["reconciled"])
        except ValueError:
            probs.append(f"{where}: reconciled {d['reconciled']!r} is not YYYY-MM-DD")
    return probs


def parse(data: dict[str, Any], label: str = PROVENANCE, require_sources: bool = True) -> Provenance:
    """Build a Provenance from loaded TOML data, raising ProvenanceError on schema problems."""
    probs = [f"{label}: unknown top-level key {k!r}" for k in sorted(set(data) - {"upstream", "module", "watch"})]
    sources = data.get("upstream", {})
    if not isinstance(sources, dict):
        probs.append(f"{label}: [upstream] must be tables")
        sources = {}
    for name, s in sources.items():
        if not isinstance(s, dict) or "repo" not in s:
            probs.append(f"{label}: [upstream.{name}] needs repo")
        elif set(s) - SOURCE_KEYS:
            probs.append(f"{label}: [upstream.{name}] unknown keys {sorted(set(s) - SOURCE_KEYS)}")
    mods, watches = data.get("module", []), data.get("watch", [])
    for i, d in enumerate(mods):
        probs += _check_entry(d, f"{label} module[{i}]", MODULE_KEYS, MODULE_REQUIRED)
    for i, d in enumerate(watches):
        probs += _check_entry(d, f"{label} watch[{i}]", WATCH_KEYS, ("upstream",))
    if not probs and require_sources:
        for i, d in enumerate([*mods, *watches]):
            if d.get("source", "hermes") not in sources:
                probs.append(f"{label} entry {d.get('exo', i)}: source {d.get('source', 'hermes')!r} "
                             "has no [upstream.*] table")
    seen: set[str] = set()
    for d in mods:
        if isinstance(d, dict) and d.get("exo") in seen:
            probs.append(f"{label}: duplicate module {d['exo']!r}")
        elif isinstance(d, dict):
            seen.add(d.get("exo"))
    if probs:
        raise ProvenanceError(probs)
    return Provenance(sources, [Module(**d) for d in mods], [Watch(**d) for d in watches])


def load(path: str | Path) -> Provenance:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ProvenanceError([f"{path.name}: {e}"]) from None
    prov = parse(data, path.name)
    prov.path = path
    return prov


def find_root(start: str | Path | None = None) -> Path:
    """Nearest directory at or above ``start`` (default cwd) holding PROVENANCE.toml; else the repo."""
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / PROVENANCE).is_file():
            return d
    return Path(__file__).resolve().parents[2]


def lifted_files(root: Path, scan: tuple[str, ...] = ("src", "plugins")) -> list[str]:
    """Repo-relative paths of .py files whose header says they were lifted or ported."""
    out = []
    for top in scan:
        for f in sorted((root / top).rglob("*.py")) if (root / top).is_dir() else []:
            if "__pycache__" in f.parts:
                continue
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    head = "".join(next(fh, "") for _ in range(HEADER_LINES))
            except OSError:
                continue
            if HEADER_RE.search(head):
                out.append(f.relative_to(root).as_posix())
    return out


def find_parts(root: str | Path) -> list[Path]:
    """Every PROVENANCE*.part.toml under ``root``, skipping virtualenvs and VCS dirs."""
    root = Path(root)
    return sorted(p for p in root.rglob(PART_GLOB)
                  if not _SKIP_DIRS.intersection(p.relative_to(root).parts[:-1]))


def part_modules(root: str | Path) -> dict[str, str]:
    """Exo paths recorded in pending part files, mapped to the part's name."""
    out = {}
    for part in find_parts(root):
        try:
            data = tomllib.loads(part.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        for m in data.get("module", []):
            if isinstance(m, dict) and isinstance(m.get("exo"), str):
                out[m["exo"]] = part.as_posix()
    return out


def validate(prov: Provenance, root: str | Path, scan: tuple[str, ...] = ("src", "plugins"),
             include_parts: bool = True) -> list[str]:
    """Consistency problems: missing exo paths, lifted headers without entries. [] means clean.

    With ``include_parts`` a lifted file recorded only in a pending part file counts as known.
    """
    root = Path(root)
    probs = [f"module {m.exo}: path does not exist" for m in prov.modules if not (root / m.exo).exists()]
    known = {m.exo for m in prov.modules} | (set(part_modules(root)) if include_parts else set())
    probs += [f"{f}: has a lifted header but no [[module]] entry" for f in lifted_files(root, scan)
              if f not in known]
    for name, s in prov.sources.items():
        if s.get("notice") and not (root / s["notice"]).is_file():
            probs.append(f"[upstream.{name}] notice {s['notice']} missing")
    return probs


# ---------------------------------------------------------------- writing


def _s(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_s(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def render_entry(kind: str, d: dict[str, Any], order: tuple[str, ...]) -> str:
    keys = [k for k in order if k in d and d[k] != ""] + sorted(set(d) - set(order))
    return f"\n[[{kind}]]\n" + "".join(f"{k} = {_s(d[k])}\n" for k in keys)


_MODULE_ORDER = ("exo", "mode", "source", "upstream", "commit", "reconciled", "changes", "outcome")


def merge_parts(root: str | Path, remove: bool = False) -> list[Path]:
    """Fold every PROVENANCE*.part.toml in the tree into PROVENANCE.toml. All or nothing."""
    root = Path(root)
    main_path = root / PROVENANCE
    main_text = main_path.read_text(encoding="utf-8")
    data = tomllib.loads(main_text)
    parse(data)
    exos = {m["exo"] for m in data.get("module", [])}
    sources = dict(data.get("upstream", {}))
    parts = find_parts(root)
    add: list[str] = []
    probs: list[str] = []
    for part in parts:
        label = part.relative_to(root).as_posix()
        try:
            pd = tomllib.loads(part.read_text(encoding="utf-8"))
            parse(pd, label, require_sources=False)
        except (tomllib.TOMLDecodeError, ProvenanceError) as e:
            probs.append(f"{label}: {e}")
            continue
        for name, s in pd.get("upstream", {}).items():
            if name in sources and sources[name] != s:
                probs.append(f"{label}: [upstream.{name}] differs from the main file")
            elif name not in sources:
                sources[name] = s
                add.append(f"\n[upstream.{name}]\n" + "".join(f"{k} = {_s(v)}\n" for k, v in s.items()))
        for m in pd.get("module", []):
            if m["exo"] in exos:
                probs.append(f"{label}: module {m['exo']!r} already recorded")
                continue
            exos.add(m["exo"])
            add.append(render_entry("module", m, _MODULE_ORDER))
        for w in pd.get("watch", []):
            add.append(render_entry("watch", w, ("upstream", "why", "source", "commit")))
    new_text = main_text.rstrip("\n") + "\n" + "".join(add)
    if not probs:
        try:
            parse(tomllib.loads(new_text))
        except (tomllib.TOMLDecodeError, ProvenanceError) as e:
            probs.append(f"merged result invalid: {e}")
    if probs:
        raise ProvenanceError(probs)
    if add:
        main_path.write_text(new_text, encoding="utf-8")
    if remove:
        for p in parts:
            p.unlink()
    return parts


def _blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = [[]]
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*\[", line):
            blocks.append([])
        blocks[-1].append(line)
    return blocks


def _set_key(block: list[str], key: str, value: str) -> list[str]:
    pat = re.compile(rf"^{key}\s*=")
    line = f"{key} = {_s(value)}\n"
    for i, ln in enumerate(block):
        if pat.match(ln):
            return block[:i] + [line] + block[i + 1:]
    end = len(block)
    while end > 1 and not block[end - 1].strip():
        end -= 1
    if end and not block[end - 1].endswith("\n"):
        block = block[:end - 1] + [block[end - 1] + "\n"] + block[end:]
    return block[:end] + [line] + block[end:]


def mark(root: str | Path, exo: str, commit: str, rejected: str | None = None, note: str = "",
         repo: str | Path | None = None, force: bool = False, today: str | None = None) -> dict[str, Any]:
    """Move ``exo``'s recorded commit to ``commit`` after a port (or a refusal, with a reason).

    With ``repo``, the new commit must exist and descend from the old one unless ``force``.
    Appends the outcome to ``upstream/MARKS.jsonl`` so what was taken and refused is kept.
    """
    root = Path(root)
    path = root / PROVENANCE
    prov = load(path)
    m = prov.module(exo)
    if m is None:
        raise ProvenanceError([f"no module {exo!r} in {PROVENANCE}"])
    if not COMMIT_RE.match(commit):
        raise ProvenanceError([f"commit {commit!r} is not a hex sha"])
    if repo is not None:
        full = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
        if not force and not _is_ancestor(repo, m.commit, full):
            raise ProvenanceError([f"{commit} does not descend from recorded {m.commit}; use force"])
        commit = full
    today = today or dt.date.today().isoformat()
    outcome = f"refused: {rejected}" if rejected else "ported"
    if note:
        outcome += f" ({note})"
    blocks = _blocks(path.read_text(encoding="utf-8"))
    for i, b in enumerate(blocks):
        if b and re.match(r"^\s*\[\[module\]\]", b[0]):
            body = tomllib.loads("".join(b[1:]))
            if body.get("exo") == exo:
                for k, v in (("commit", commit), ("reconciled", today), ("outcome", outcome)):
                    b = _set_key(b, k, v)
                blocks[i] = b
                break
    text = "".join("".join(b) for b in blocks)
    parse(tomllib.loads(text))
    path.write_text(text, encoding="utf-8")
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "exo": exo,
           "from": m.commit, "to": commit, "outcome": outcome}
    log = root / "upstream" / "MARKS.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


# ---------------------------------------------------------------- git and report


def _git(repo: str | Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def _is_ancestor(repo: str | Path, a: str, b: str) -> bool:
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b], capture_output=True)
    return r.returncode == 0


def _has_commit(repo: str | Path, sha: str) -> bool:
    r = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True)
    return r.returncode == 0


def ensure_mirror(url: str, path: str | Path) -> Path:
    """Bare mirror at ``path``: clone once, then fetch. Never checks anything out."""
    path = Path(path)
    if (path / "HEAD").is_file():
        _git(path, "fetch", "--prune", "--tags", "origin")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "--mirror", "--quiet", url, str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"git clone {url}: {r.stderr.strip()}")
    return path


def mirror_path(source: str = "hermes") -> Path:
    home = Path(os.environ.get("EXO_HOME") or Path.home() / ".exo")
    return home / "upstream" / f"{source}.git"


_STABLE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def pick_target(repo: str | Path, to: str | None = None, baselines: list[str] = ()) -> str:
    """``to`` if given; else the newest stable tag that contains every baseline; else HEAD."""
    if to:
        return _git(repo, "rev-parse", "--verify", f"{to}^{{commit}}").strip()
    tags = [t for t in _git(repo, "tag", "--list", "v*").split() if _STABLE.match(t)]
    tags.sort(key=lambda t: tuple(int(x) for x in _STABLE.match(t).groups()), reverse=True)
    present = [b for b in baselines if _has_commit(repo, b)]
    for t in tags[:5]:
        if all(_is_ancestor(repo, b, t) for b in present):
            return _git(repo, "rev-parse", f"{t}^{{commit}}").strip()
    return _git(repo, "rev-parse", "HEAD").strip()


_NOISE_PATH = re.compile(r"(^|/)(tests?|docs?|website|catalogs?)/|(^|/)test_[^/]*$|\.(md|rst|txt)$")
_NOISE_SUBJ = re.compile(r"^(docs|test|tests|ci|style|chore\(ci\)|chore\(deps\))(\(|:|!)", re.I)
_TYPE_RE = re.compile(r"^(\w+)(\([^)]*\))?!?:")
WEIGHTS = {"security": 100, "fix": 80, "perf": 60, "revert": 40, "feat": 50, "refactor": 30}


@dataclass
class UpstreamCommit:
    sha: str
    subject: str
    author: str
    date: str
    kind: str
    score: int
    lines: int = 0
    files: list[str] = field(default_factory=list)
    touches: list[str] = field(default_factory=list)


@dataclass
class ModuleReport:
    exo: str
    mode: str
    commit: str
    upstream: list[str]
    commits: list[UpstreamCommit] = field(default_factory=list)
    noise: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class Report:
    source: str
    repo_url: str
    target: str
    generated: str
    modules: list[ModuleReport] = field(default_factory=list)


def classify(subject: str) -> tuple[str, int]:
    low = subject.lower()
    if any(w in low for w in ("security", "cve-", "vuln", "injection", "leak")):
        return "security", WEIGHTS["security"]
    m = _TYPE_RE.match(subject)
    kind = m.group(1).lower() if m else "other"
    return kind, WEIGHTS.get(kind, 20)


def _pathspecs(paths: list[str]) -> list[str]:
    return [f":(glob){p}" if any(c in p for c in "*?[") else p for p in paths]


def lifted_names(file: Path) -> set[str]:
    """Function and class names defined in an Exo file (to spot upstream hunks that hit them)."""
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(re.findall(r"^\s*(?:async\s+def|def|class)\s+(\w+)", text, re.M))


def _renames(repo: str | Path, base: str, target: str, path: str) -> tuple[str | None, bool]:
    """(new path, deleted) for a plain path missing at target. Follows chains of renames."""
    out = _git(repo, "log", "-M", "--reverse", "--diff-filter=R", "--name-status", "--format=", f"{base}..{target}")
    new = path
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) == 3 and cols[0].startswith("R") and cols[1] == new:
            new = cols[2]
    return (new, False) if new != path else (None, True)


_ATTRS: Path | None = None


def _py_attrs() -> str:
    """A gitattributes file turning on git's python hunk headers (def/class names)."""
    global _ATTRS
    if _ATTRS is None or not _ATTRS.exists():
        import tempfile
        fd, name = tempfile.mkstemp(prefix="exo-upstream-", suffix=".gitattributes")
        os.write(fd, b"*.py diff=python\n")
        os.close(fd)
        _ATTRS = Path(name)
    return str(_ATTRS)


def report_module(repo: str | Path, m: Module | Watch, target: str, root: Path) -> ModuleReport:
    exo = getattr(m, "exo", "(watch)")
    rep = ModuleReport(exo, getattr(m, "mode", "watch"), m.commit, list(m.upstream))
    if not m.commit:
        rep.notes.append("no recorded commit; nothing to diff against")
        return rep
    if not _has_commit(repo, m.commit):
        rep.notes.append(f"recorded commit {m.commit} not found upstream")
        return rep
    if _is_ancestor(repo, target, m.commit) and target != m.commit:
        rep.notes.append("target is behind the recorded commit; pass --to")
        return rep
    paths = list(m.upstream)
    for p in m.upstream:
        if any(c in p for c in "*?["):
            continue
        if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{target}:{p}"], capture_output=True).returncode:
            new, deleted = _renames(repo, m.commit, target, p)
            if new:
                rep.notes.append(f"{p} renamed to {new} upstream")
                paths.append(new)
            elif deleted:
                rep.notes.append(f"{p} deleted upstream")
    names = lifted_names(root / exo) if hasattr(m, "exo") and m.mode == "lift" else set()
    specs = _pathspecs(paths)
    out = _git(repo, "log", "--no-merges", "-M", "--numstat", "--format=%x1e%H%x1f%s%x1f%an%x1f%aI",
               f"{m.commit}..{target}", "--", *specs)
    for chunk in out.split("\x1e")[1:]:
        head, _, stat = chunk.partition("\n")
        sha, subject, author, date = head.split("\x1f")
        files, lines = [], 0
        for row in stat.strip().splitlines():
            a, d, f = (row.split("\t") + ["", "", ""])[:3]
            files.append(f)
            lines += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
        if _NOISE_SUBJ.match(subject) or (files and all(_NOISE_PATH.search(f) for f in files)):
            rep.noise += 1
            continue
        kind, score = classify(subject)
        score += min(20, lines // 20)
        touches: list[str] = []
        if names:
            diff = _git(repo, "-c", f"core.attributesFile={_py_attrs()}", "show", "-U0", "--format=",
                        sha, "--", *specs)
            changed = " ".join(ln for ln in diff.splitlines() if ln.startswith(("@@", "+", "-")))
            touches = sorted(n for n in names if re.search(rf"\b{re.escape(n)}\b", changed))
            if touches:
                score += 15
        rep.commits.append(UpstreamCommit(sha, subject, author, date[:10], kind, score, lines, files, touches))
    rep.commits.sort(key=lambda c: (-c.score, c.date))
    return rep


def build_report(prov: Provenance, repo: str | Path, root: str | Path, to: str | None = None,
                 source: str = "hermes") -> Report:
    root = Path(root)
    entries = [m for m in prov.modules if m.source == source] + [w for w in prov.watch if w.source == source]
    target = pick_target(repo, to, [m.commit for m in entries if m.commit])
    rep = Report(source, prov.sources.get(source, {}).get("repo", ""), target,
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    rep.modules = [report_module(repo, m, target, root) for m in entries]
    rep.modules.sort(key=lambda r: -max((c.score for c in r.commits), default=0))
    return rep


def render_markdown(rep: Report, limit: int = 30) -> str:
    out = [f"# Upstream report: {rep.source}", "",
           f"Target `{rep.target[:12]}`, generated {rep.generated}. Ranked: security and fixes first, "
           "then features, then refactors; size and hits on lifted functions raise the rank.", ""]
    for m in rep.modules:
        out.append(f"## {m.exo} ({m.mode}, since `{m.commit[:12]}`)")
        out.append(f"Upstream: {', '.join(m.upstream)}. {len(m.commits)} commits, {m.noise} skipped as noise.")
        out += [f"- note: {n}" for n in m.notes]
        for c in m.commits[:limit]:
            link = f"{rep.repo_url}/commit/{c.sha}" if rep.repo_url else c.sha[:12]
            hit = f" touches {', '.join(c.touches)}." if c.touches else ""
            out.append(f"- [{c.score}] {c.kind}: {c.subject} ({c.date}, {c.lines} lines, [{c.sha[:10]}]({link})).{hit}")
        if len(m.commits) > limit:
            out.append(f"- and {len(m.commits) - limit} more")
        out.append("")
    return "\n".join(out)


def write_report(root: str | Path, rep: Report, text: str) -> Path:
    path = Path(root) / "upstream" / f"REPORT-{rep.generated[:10]}-{rep.source}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- CLI


def register(sp: Any, common: argparse.ArgumentParser) -> None:
    """Add ``exo upstream {report,mark,check,merge}``. Dispatch with ``cmd_upstream(a)``."""
    p = sp.add_parser("upstream", help="track lifted upstream code (PROVENANCE.toml)")
    up = p.add_subparsers(dest="upstream_cmd", required=True)
    r = up.add_parser("report", parents=[common], help="ranked upstream changes since each recorded commit")
    r.add_argument("--source", default="hermes")
    r.add_argument("--to", help="target ref (default: newest stable tag containing the baselines, else HEAD)")
    r.add_argument("--repo", help="use this git repo or mirror as is (no fetch)")
    r.add_argument("--no-fetch", action="store_true", help="use the existing mirror without fetching")
    r.add_argument("--no-write", action="store_true", help="print, do not write upstream/REPORT-*.md")
    r.add_argument("--json", action="store_true")
    r.add_argument("--limit", type=int, default=30)
    m = up.add_parser("mark", parents=[common], help="move a module's commit after a port or refusal")
    m.add_argument("exo", help="Exo path as recorded, e.g. src/exo/core/slots.py")
    m.add_argument("--commit", required=True)
    m.add_argument("--rejected", metavar="REASON", help="record a refusal instead of a port")
    m.add_argument("--note", default="")
    m.add_argument("--repo", help="check the commit against this repo or mirror")
    m.add_argument("--force", action="store_true", help="allow a commit that does not descend")
    up.add_parser("check", parents=[common], help="validate PROVENANCE.toml against the tree")
    g = up.add_parser("merge", parents=[common], help="fold PROVENANCE*.part.toml files into PROVENANCE.toml")
    g.add_argument("--remove", action="store_true", help="delete the parts after merging")
    for q in (r, m, up.choices["check"], g):
        q.add_argument("--provenance-root", help="directory holding PROVENANCE.toml (default: search up)")
    p.set_defaults(func=cmd_upstream)


def cmd_upstream(a: argparse.Namespace, store: Any = None) -> int:
    root = Path(a.provenance_root) if getattr(a, "provenance_root", None) else find_root()
    try:
        if a.upstream_cmd == "check":
            probs = validate(load(root / PROVENANCE), root)
            print("\n".join(probs) if probs else "PROVENANCE.toml ok")
            return 1 if probs else 0
        if a.upstream_cmd == "merge":
            parts = merge_parts(root, remove=a.remove)
            print(f"merged {len(parts)} part(s)" + "".join(f"\n  {p.relative_to(root)}" for p in parts))
            return 0
        if a.upstream_cmd == "mark":
            rec = mark(root, a.exo, a.commit, a.rejected, a.note, a.repo, a.force)
            print(f"{rec['exo']}: {rec['from'][:12]} -> {rec['to'][:12]} ({rec['outcome']})")
            return 0
        prov = load(root / PROVENANCE)
        if a.repo:
            repo = Path(a.repo)
        else:
            repo = mirror_path(a.source)
            if not a.no_fetch or not repo.exists():
                ensure_mirror(prov.sources[a.source]["repo"], repo)
        rep = build_report(prov, repo, root, a.to, a.source)
        if a.json:
            print(json.dumps(asdict(rep), indent=2))
            return 0
        text = render_markdown(rep, a.limit)
        if a.no_write:
            print(text)
        else:
            print(write_report(root, rep, text))
        return 0
    except (ProvenanceError, RuntimeError, KeyError) as e:
        print(f"exo upstream: {e}")
        return 2
