"""Principal store on disk: router, principal pack, belief nodes, policies, private corpus.

Layout (see templates/principal-store/):

    INDEX.md            router: load rules plus a generated node table
    PRINCIPAL.md        one-page pack
    nodes/<domain>/*.md belief nodes, frontmatter schema in method/SCHEMA.md
    policies/           gates, posting rules, budget, interruptions
    timeline/ training/ agents/
    corpus/private/     raw record, git-ignored, mode 0700
    ledger.sqlite       the event log (exo.ledger)

The store is principal-agnostic. Its root is a parameter; for a principal id it is the root
recorded in config `principals.<id>`, else `$EXO_HOME/principals/<id>`, with EXO_HOME defaulting
to `~/.exo` (`principal_root`).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ['id', 'title', 'type', 'domain', 'summary', 'load_when', 'confidence', 'stability',
            'valid_from', 'sources', 'links']
TYPES = {'trait', 'value', 'fact', 'preference', 'rule', 'episode', 'entity', 'procedure', 'question'}
RELS = {'part-of', 'explains', 'evidence-for', 'contradicts', 'supersedes', 'applies-to', 'instance-of',
        'see-also'}
CONFIDENCE = {'high', 'medium', 'low'}
STABILITY = {'core', 'durable', 'volatile'}
# Patterns that must never appear outside corpus/private (see method/PRIVACY.md in a store).
LEAKS = [re.compile(p) for p in (
    r'\+?\d[\d \-()]{9,}\d',
    r'(?i)(api[_-]?key|secret|token)\s*[:=]\s*\S{12,}',
    r'sk-[A-Za-z0-9]{16,}',
    r'ghp_[A-Za-z0-9]{20,}',
    r'(?i)password\s*[:=]',
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
)]
STOP = set("the a an and or of to in on for is are be with as at by it this that from "
           "he him his she her their they them its".split())
NODES_BEGIN, NODES_END = '<!-- NODES:BEGIN -->', '<!-- NODES:END -->'
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*$')


# --- locations ----------------------------------------------------------------

def exo_home() -> Path:
    return Path(os.environ.get('EXO_HOME') or Path.home() / '.exo').expanduser()


def _root_path(root: object) -> Path | None:
    """A recorded store root as an absolute path, or None. A root that is not a string, is blank, holds a NUL byte,
    names an unknown user (`~nosuchuser/x`) or is relative after `~` expansion is not a store root."""
    if not isinstance(root, str) or not root.strip() or '\0' in root:
        return None
    try:
        path = Path(root).expanduser()
    except (RuntimeError, ValueError, OSError):
        return None
    return path if path.is_absolute() else None


def explicit_roots(home: str | os.PathLike | None = None) -> dict[str, Path]:
    """Store roots by principal id from `principals` in `<home>/config.json` (`exo init --root` and
    `exo import-store --root` record them). A missing or unreadable file, or a bad entry, adds nothing."""
    try:
        data = json.loads((Path(home or exo_home()).expanduser() / 'config.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    roots = data.get('principals') if isinstance(data, dict) else None
    out = {}
    for pid, root in (roots.items() if isinstance(roots, dict) else ()):
        path = _root_path(root)
        if isinstance(pid, str) and ID_RE.match(pid) and path is not None:
            out[pid] = path
    return out


def principal_root(pid: str, home: str | os.PathLike | None = None) -> Path:
    """The store of a principal id: its explicit root (`explicit_roots`), else `<home>/principals/<id>`.
    `home` defaults to `$EXO_HOME`. Every reader that turns an id into a store goes through here."""
    if not ID_RE.match(pid):
        raise ValueError(f'bad principal id {pid!r}: use lowercase letters, digits, - and _')
    home = Path(home).expanduser() if home else exo_home()
    return explicit_roots(home).get(pid) or home / 'principals' / pid


def template_dir() -> Path:
    """The bundled empty store. Installed wheels carry it inside the package; a source
    checkout keeps it at the repo root."""
    here = Path(__file__).resolve().parent
    for cand in (here / 'templates' / 'principal-store', here.parents[1] / 'templates' / 'principal-store'):
        if cand.is_dir():
            return cand
    raise FileNotFoundError('principal-store template not found')


# --- frontmatter parser (a strict subset of YAML; stdlib only) ----------------

def parse_flow(s: str) -> dict:
    """Parse '{k: v, k: "v, w"}' into a dict."""
    s = s.strip()[1:-1]
    out, buf, q, parts = {}, '', False, []
    for ch in s:
        if ch == '"':
            q = not q
            continue
        if ch == ',' and not q:
            parts.append(buf)
            buf = ''
            continue
        buf += ch
    if buf.strip():
        parts.append(buf)
    for p in parts:
        if ':' in p:
            k, v = p.split(':', 1)
            out[k.strip()] = v.strip()
    return out


def parse_text(text: str) -> dict | None:
    """Parse a node file's text into a dict of frontmatter keys plus `_body`."""
    if not text.startswith('---'):
        return None
    try:
        _, fm, body = text.split('---', 2)
    except ValueError:
        return None
    meta: dict = {}
    key = None
    item_dict: dict | None = None
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        stripped = line.strip()
        if stripped.startswith('- '):
            item = stripped[2:].strip()
            if not isinstance(meta.get(key), list):
                meta[key] = []
            if item.startswith('{'):
                meta[key].append(parse_flow(item))
                item_dict = None
            elif re.match(r'^[A-Za-z_]+:\s', item + ' ') and not item.startswith('"'):
                # block-style list item: "- kind: x" followed by indented "ref: y" lines
                k, v = item.split(':', 1)
                item_dict = {k.strip(): v.strip().strip('"')}
                meta[key].append(item_dict)
            else:
                meta[key].append(item.strip('"'))
                item_dict = None
        elif line.startswith((' ', '\t')) and item_dict is not None and ':' in stripped:
            k, v = stripped.split(':', 1)
            item_dict[k.strip()] = v.strip().strip('"')
        elif re.match(r'^[A-Za-z_]+:', line):
            item_dict = None
            key, val = line.split(':', 1)
            val = val.strip()
            if val.startswith('[') and val.endswith(']'):
                meta[key] = [v.strip().strip('"') for v in val[1:-1].split(',') if v.strip()]
            elif val == '':
                meta[key] = None
            else:
                meta[key] = val.strip('"')
    meta['_body'] = body.strip()
    return meta


def tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 1]


# --- results ------------------------------------------------------------------

@dataclass
class Context:
    text: str
    ids: list[str]                                   # every node included, full or summary
    full: list[str] = field(default_factory=list)    # nodes included with their body
    tokens: int = 0


@dataclass
class LintReport:
    nodes: int
    edges: int
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f'ERROR {e}' for e in self.errors] + [f'warn  {w}' for w in self.warnings]
        lines.append(f'lint: {self.nodes} nodes, {self.edges} edges, {len(self.errors)} errors, '
                     f'{len(self.warnings)} warnings')
        return '\n'.join(lines)


# --- the store ----------------------------------------------------------------

class PrincipalStore:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()
        self._nodes: dict[str, dict] | None = None
        self._seen: tuple = ()  # the node files as the last reload saw them (`refresh`)
        self._bm25: tuple | None = None  # (node set, tables) for `find`, rebuilt when a reload replaces the set

    def __repr__(self) -> str:
        return f'PrincipalStore({str(self.root)!r})'

    @classmethod
    def for_principal(cls, pid: str) -> 'PrincipalStore':
        return cls(principal_root(pid))

    @classmethod
    def create(cls, root: str | os.PathLike, template: str | os.PathLike | None = None) -> 'PrincipalStore':
        """Copy the template into an empty or missing directory and set private modes."""
        root = Path(root).expanduser()
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f'{root} exists and is not empty')
        src = Path(template) if template else template_dir()
        shutil.copytree(src, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '.DS_Store'))
        private = root / 'corpus' / 'private'
        private.mkdir(parents=True, exist_ok=True)
        os.chmod(private, 0o700)
        store = cls(root)
        store.index()
        return store

    @property
    def nodes_dir(self) -> Path:
        return self.root / 'nodes'

    @property
    def ledger_path(self) -> Path:
        return self.root / 'ledger.sqlite'

    def exists(self) -> bool:
        return self.nodes_dir.is_dir()

    # --- loading -------------------------------------------------------------

    def parse_node(self, path: Path) -> dict | None:
        meta = parse_text(path.read_text(encoding='utf-8'))
        if meta is None:
            return None
        meta['_path'] = path.relative_to(self.root).as_posix()
        return meta

    def _node_files(self) -> tuple:
        """(path, mtime, size) of every node file: it changes when a node is added, edited or removed."""
        seen = []
        for d, _, files in os.walk(self.nodes_dir):
            for f in files:
                if f.endswith('.md'):
                    try:
                        st = os.stat(os.path.join(d, f))
                    except OSError:
                        continue
                    seen.append((os.path.join(d, f), st.st_mtime_ns, st.st_size))
        return tuple(sorted(seen))

    def reload(self) -> dict[str, dict]:
        self._seen = self._node_files()  # before reading, so a write during the walk shows at the next refresh
        nodes: dict[str, dict] = {}
        if self.nodes_dir.is_dir():
            for d, _, files in sorted(os.walk(self.nodes_dir)):
                for f in sorted(files):
                    if f.endswith('.md'):
                        n = self.parse_node(Path(d) / f)
                        if n and n.get('id'):
                            nodes[n['id']] = n
        self._nodes, self._bm25 = nodes, None  # free the old set's tables now; the next `find` builds new ones
        return nodes

    def refresh(self) -> bool:
        """Reload when a node file was added, edited or removed since the last read: another process or another
        store object (the CLI's `exo proposals accept`, the learner's) wrote it. True when it reloaded. `nodes`
        itself stays a plain cached read, so a long-lived reader calls this once per use."""
        if self._nodes is not None and self._node_files() == self._seen:
            return False
        self.reload()
        return True

    @property
    def nodes(self) -> dict[str, dict]:
        return self._nodes if self._nodes is not None else self.reload()

    def get(self, nid: str) -> dict | None:
        return self.nodes.get(nid)

    def render(self, nid: str, body_only: bool = False) -> str:
        n = self.nodes[nid]
        return n['_body'] if body_only else (self.root / n['_path']).read_text(encoding='utf-8')

    # --- search --------------------------------------------------------------

    def find(self, query: str | list[str], n: int | None = None) -> list[tuple[str, float]]:
        """BM25 over nodes. Title counts three times, summary and load_when twice. The node tables are built once
        per node set: every reload makes a new set, so only a reload (or a refresh that saw a change) rebuilds
        them, and a repeat query tokenizes just itself."""
        q = tokens(query if isinstance(query, str) else ' '.join(query))
        if not q:
            return []
        nodes, cached = self.nodes, self._bm25
        if cached is None or cached[0] is not nodes:
            cached = self._bm25 = (nodes, *self._bm25_tables(nodes))
        _, docs, df, avg = cached
        N = len(docs)
        scores = {}
        for i, (c, L) in docs.items():
            s = 0.0
            for w in q:
                if w not in c:
                    continue
                idf = math.log(1 + (N - df[w] + .5) / (df[w] + .5))
                s += idf * c[w] * 2.2 / (c[w] + 1.2 * (.25 + .75 * L / avg))
            if s:
                scores[i] = s
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:n] if n else ranked

    @staticmethod
    def _bm25_tables(nodes: dict[str, dict]) -> tuple[dict[str, tuple[Counter, int]], Counter, float]:
        """Each node's weighted token counts and length, each token's document frequency, the average length."""
        docs = {}
        for i, node in nodes.items():
            tags = node.get('tags') or []
            weighted = ' '.join([
                ' '.join([node.get('title') or ''] * 3),
                ' '.join([node.get('summary') or ''] * 2),
                ' '.join([node.get('load_when') or ''] * 2),
                ' '.join(tags) if isinstance(tags, list) else str(tags),
                i.replace('/', ' '),
                node['_body'],
            ])
            c = Counter(tokens(weighted))
            docs[i] = (c, sum(c.values()))
        df: Counter = Counter()
        for c, _ in docs.values():
            df.update(set(c))
        return docs, df, sum(L for _, L in docs.values()) / max(len(docs), 1)

    def ctx(self, words: str | list[str], budget: int = 1500,
            ranked: list[tuple[str, float]] | None = None) -> Context:
        """Pack the best nodes under a token budget (tokens estimated as chars / 4). The header
        line counts against the budget. A node that does not fit is listed by summary."""
        query = words if isinstance(words, str) else ' '.join(words)
        limit = budget * 4
        ranked = self.find(query) if ranked is None else ranked
        header_reserve = len(f'<!-- exo ctx: 9999 nodes, ~{budget} tokens, query={query} -->\n')
        used, picked, ids, full = header_reserve, [], [], []
        for i, _ in ranked:
            body = f"## {i}\n{self.nodes[i]['_body']}\n"
            if used + len(body) + 1 > limit:
                line = f"## {i} (summary only; exo get {i})\n{self.nodes[i].get('summary') or ''}\n"
                if used + len(line) + 1 <= limit:
                    picked.append(line)
                    used += len(line) + 1
                    ids.append(i)
                continue
            picked.append(body)
            used += len(body) + 1
            ids.append(i)
            full.append(i)
        content = '\n'.join(picked)
        header = f'<!-- exo ctx: {len(ids)} nodes, ~{(len(content) + header_reserve) // 4} tokens, query={query} -->'
        text = header + '\n' + content if picked else header
        return Context(text=text, ids=ids, full=full, tokens=len(text) // 4)

    # --- graph ---------------------------------------------------------------

    def edges(self) -> list[tuple[str, str, str]]:
        out = []
        for i, n in self.nodes.items():
            for link in n.get('links') or []:
                if isinstance(link, dict) and link.get('to'):
                    out.append((i, link['to'], link.get('rel', 'see-also')))
        return out

    def links(self, nid: str) -> dict[str, list[tuple[str, str]]]:
        E = self.edges()
        return {'out': [(r, t) for s, t, r in E if s == nid], 'in': [(r, s) for s, t, r in E if t == nid]}

    def graph(self, write: bool = True) -> dict:
        keys = ('id', 'title', 'type', 'domain', 'summary', 'confidence', 'stability', 'valid_from', 'valid_until')
        g = {'nodes': [{k: n.get(k) for k in keys} for n in self.nodes.values()],
             'edges': [{'from': s, 'to': t, 'rel': r} for s, t, r in self.edges()]}
        if write:
            (self.root / 'graph').mkdir(exist_ok=True)
            (self.root / 'graph' / 'graph.json').write_text(json.dumps(g, indent=1, ensure_ascii=False),
                                                            encoding='utf-8')
        return g

    # --- lint ----------------------------------------------------------------

    def lint(self) -> LintReport:
        nodes = self.nodes
        errs, warns = [], []
        for i, n in nodes.items():
            for k in REQUIRED:
                if k not in n:
                    errs.append(f'{i}: missing {k}')
            if n.get('type') not in TYPES:
                errs.append(f"{i}: bad type {n.get('type')}")
            if n.get('confidence') not in CONFIDENCE:
                warns.append(f"{i}: confidence {n.get('confidence')!r} not in {sorted(CONFIDENCE)}")
            if n.get('stability') not in STABILITY:
                warns.append(f"{i}: stability {n.get('stability')!r} not in {sorted(STABILITY)}")
            exp = n['_path'][len('nodes/'):-3]
            if exp != i:
                errs.append(f'{i}: id does not match path {exp}')
            if n.get('domain') and i.split('/')[0] != n.get('domain'):
                warns.append(f"{i}: domain {n.get('domain')} differs from folder")
            if len(n.get('summary') or '') > 200:
                warns.append(f'{i}: summary over 200 chars')
            words = len(n['_body'].split())
            if words > 600:
                warns.append(f'{i}: body {words} words (split?)')
            if not n.get('sources'):
                warns.append(f'{i}: no sources')
            for link in n.get('links') or []:
                if isinstance(link, dict):
                    if link.get('rel') not in RELS:
                        errs.append(f"{i}: bad rel {link.get('rel')}")
                    if link.get('to') not in nodes:
                        errs.append(f"{i}: dangling link {link.get('to')}")
            errs += [f'{i}: {m}' for m in self._leaks(self.render(i))]
        for rel in ('INDEX.md', 'PRINCIPAL.md'):
            p = self.root / rel
            if p.exists():
                errs += [f'{rel}: {m}' for m in self._leaks(p.read_text(encoding='utf-8'))]
            else:
                errs.append(f'{rel}: missing')
        E = self.edges()
        touched = {s for s, _, _ in E} | {t for _, t, _ in E}
        warns += [f'{i}: orphan' for i in nodes if i not in touched]
        return LintReport(len(nodes), len(E), errs, warns)

    @staticmethod
    def _leaks(text: str) -> list[str]:
        out = []
        for p in LEAKS:
            m = p.search(text)
            if m:
                out.append(f'possible leak "{m.group(0)[:20]}"')
        return out

    # --- index ---------------------------------------------------------------

    def index(self) -> int:
        """Rewrite the node table between the NODES markers in INDEX.md."""
        path = self.root / 'INDEX.md'
        text = path.read_text(encoding='utf-8')
        if NODES_BEGIN not in text or NODES_END not in text:
            text = text.rstrip() + f'\n\n## Nodes\n\n{NODES_BEGIN}\n{NODES_END}\n'
        rows = defaultdict(list)
        for i, n in sorted(self.nodes.items()):
            rows[n.get('domain') or i.split('/')[0]].append(f"- `{i}`: {n.get('summary') or ''}")
        table = '\n'.join(f'\n**{d}**\n' + '\n'.join(r) for d, r in sorted(rows.items()))
        new = re.sub(re.escape(NODES_BEGIN) + '.*' + re.escape(NODES_END),
                     lambda m: NODES_BEGIN + table + '\n' + NODES_END, text, flags=re.S)
        path.write_text(new, encoding='utf-8')
        return len(self.nodes)

    def stats(self) -> dict[str, dict]:
        return {f: dict(Counter(n.get(f) for n in self.nodes.values()))
                for f in ('domain', 'type', 'confidence', 'stability')}
