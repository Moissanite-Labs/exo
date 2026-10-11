"""Principal store on disk: router, principal pack, belief nodes, policies, private corpus.

Layout (see templates/principal-store/):

    INDEX.md            router: load rules plus a generated node table
    PRINCIPAL.md        one-page pack
    nodes/<domain>/*.md belief nodes, frontmatter schema in method/SCHEMA.md
    policies/           gates, posting rules, budget, interruptions
    timeline/ training/ agents/
    corpus/private/     raw record, git-ignored, mode 0700
    ledger.sqlite       the event log (exo.ledger)

The store is principal-agnostic. Its root is a parameter; the default for a principal id
is `$EXO_HOME/principals/<id>`, with EXO_HOME defaulting to `~/.exo`.
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


def principal_root(pid: str) -> Path:
    if not ID_RE.match(pid):
        raise ValueError(f'bad principal id {pid!r}: use lowercase letters, digits, - and _')
    return exo_home() / 'principals' / pid


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

    def reload(self) -> dict[str, dict]:
        nodes: dict[str, dict] = {}
        if self.nodes_dir.is_dir():
            for d, _, files in sorted(os.walk(self.nodes_dir)):
                for f in sorted(files):
                    if f.endswith('.md'):
                        n = self.parse_node(Path(d) / f)
                        if n and n.get('id'):
                            nodes[n['id']] = n
        self._nodes = nodes
        return nodes

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
        """BM25 over nodes. Title counts three times, summary and load_when twice."""
        q = tokens(query if isinstance(query, str) else ' '.join(query))
        if not q:
            return []
        docs = {}
        for i, node in self.nodes.items():
            tags = node.get('tags') or []
            weighted = ' '.join([
                ' '.join([node.get('title') or ''] * 3),
                ' '.join([node.get('summary') or ''] * 2),
                ' '.join([node.get('load_when') or ''] * 2),
                ' '.join(tags) if isinstance(tags, list) else str(tags),
                i.replace('/', ' '),
                node['_body'],
            ])
            docs[i] = Counter(tokens(weighted))
        N = len(docs)
        df: Counter = Counter()
        for c in docs.values():
            for w in set(c):
                df[w] += 1
        avg = sum(sum(c.values()) for c in docs.values()) / max(N, 1)
        scores = {}
        for i, c in docs.items():
            L = sum(c.values())
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
