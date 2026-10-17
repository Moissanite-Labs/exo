"""Onboarding: build a principal store from zero, or adopt an existing one.

    exo init <id> --interview [--answers FILE] [--max-questions 12] [--generator MOD:ATTR]
    exo interview [--principal ID | --root DIR] [--answers FILE] ...
    exo import-store <path> [--id ID] [--root DIR] [--include-private] [--activate]

The interview follows principle 13. It drafts candidate questions from the question bank
(`templates/question-bank/*.yaml`, plus `<store>/method/question-bank/*.yaml` when present),
scores each by value of information given what the store already knows, asks the top few, and
writes each answer twice: as a signed `answer` event in the ledger (the source of truth), and
into a low-confidence belief node marked `kind: a-said` (a view rebuilt from the ledger).

It needs no model. The order is deterministic by default. A `Generator` is the seam for a
model to draft extra questions, imagine answers, and propose follow-ups.

Run standalone with `python -m exo.onboard ...` when `exo.cli` is not wired yet.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import crypto
from .consolidate import event_ref
from .gate import parse_yaml
from .ledger import Ledger
from .store import ID_RE, PrincipalStore, exo_home, principal_root, template_dir

INTERVIEWER = 'exo-onboard'
DEFAULT_MAX = 12
A_SAID = 'a-said'
STOP_WORDS = {'q', 'quit', ':q', 'stop'}
SKIP_WORDS = {'', 'skip', 'pass', '-'}


# --- question bank ----------------------------------------------------------------

@dataclass(frozen=True)
class Question:
    id: str
    section: str
    ask: str
    why: str = ''
    covers: tuple[str, ...] = ()
    weight: float = 3.0
    imagined: tuple[str, ...] = ()
    node: str = ''
    followups: tuple['Question', ...] = ()
    when: str = 'answered'          # follow-up condition: 'answered' or 'always'
    when_matches: str = ''          # follow-up condition: regex the parent answer must match
    parent: str = ''
    rank: int = 0                   # bank order, the deterministic tie-break

    @property
    def facets(self) -> tuple[str, ...]:
        return self.covers or (self.id,)


@dataclass
class NodeSpec:
    id: str
    title: str
    type: str
    summary: str
    load_when: str
    stability: str = 'durable'


@dataclass
class Bank:
    questions: list[Question] = field(default_factory=list)
    nodes: dict[str, NodeSpec] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)   # section -> default node id

    def node_for(self, q: Question) -> str:
        return q.node or self.sections.get(q.section) or f'person/{q.section}'


def bank_dir() -> Path:
    """The bundled question bank, next to the principal-store template."""
    return template_dir().parent / 'question-bank'


def _q(raw: dict, section: str, node: str, rank: int, parent: str = '') -> Question:
    if not raw.get('id') or not raw.get('ask'):
        raise ValueError(f'question in section {section!r} needs id and ask: {raw!r}')
    ups = tuple(_q(f, section, f.get('node') or node, rank * 100 + i + 1, parent=raw['id'])
                for i, f in enumerate(raw.get('followups') or []))
    return Question(
        id=str(raw['id']), section=section, ask=str(raw['ask']).strip(), why=str(raw.get('why') or ''),
        covers=tuple(str(c) for c in (raw.get('covers') or [])), weight=float(raw.get('weight', 3)),
        imagined=tuple(str(x) for x in (raw.get('imagined') or [])), node=str(raw.get('node') or node),
        followups=ups, when=str(raw.get('when') or 'answered'), when_matches=str(raw.get('when_matches') or ''),
        parent=parent, rank=rank)


def load_bank(dirs: Iterable[str | os.PathLike] | None = None) -> Bank:
    """Read every `*.yaml` in the given directories (default: the bundled bank). Later
    directories add questions; a repeated question id replaces the earlier one."""
    bank = Bank()
    files = []
    for d in (list(dirs) if dirs is not None else [bank_dir()]):
        d = Path(d)
        if d.is_dir():
            files += sorted(d.glob('*.yaml'))
    by_id: dict[str, Question] = {}
    parsed = []
    for f in files:
        doc = parse_yaml(f.read_text(encoding='utf-8')) or {}
        if not isinstance(doc, dict) or not doc.get('section'):
            raise ValueError(f'{f}: a bank file needs a top-level `section`')
        parsed.append((float(doc.get('order', 100)), f.name, doc))
    for si, (_, _, doc) in enumerate(sorted(parsed, key=lambda x: (x[0], x[1]))):
        section = str(doc['section'])
        node = str(doc.get('node') or f'person/{section}')
        bank.sections.setdefault(section, node)
        specs = [{'id': node, **{k: doc.get(k) for k in ('title', 'type', 'summary', 'load_when', 'stability')}}]
        specs += list(doc.get('extra_nodes') or [])
        for s in specs:
            nid = str(s['id'])
            bank.nodes[nid] = NodeSpec(
                id=nid, title=str(s.get('title') or f'Interview answers: {nid}'),
                type=str(s.get('type') or doc.get('type') or 'preference'),
                summary=str(s.get('summary') or f'What the principal said about {section} in the interview.'),
                load_when=str(s.get('load_when') or f'Tasks that touch {section}.'),
                stability=str(s.get('stability') or doc.get('stability') or 'durable'))
        for qi, raw in enumerate(doc.get('questions') or []):
            q = _q(raw, section, str(raw.get('node') or ''), (si + 1) * 1000 + qi)
            by_id[q.id] = q
    bank.questions = sorted(by_id.values(), key=lambda q: q.rank)
    return bank


# --- the generator seam -------------------------------------------------------------

class Generator:
    """Deterministic default. Subclass (or duck-type) to plug in a model:

    draft(bank, known)            extra candidate questions (principle 13: draft 20+)
    imagine(question, known)      plausible answers; their spread is the uncertainty term
    follow_ups(q, answer, known)  questions to add after an answer
    """

    def draft(self, bank: Bank, known: set[str]) -> list[Question]:
        return []

    def imagine(self, q: Question, known: set[str]) -> list[str]:
        return list(q.imagined)

    def follow_ups(self, q: Question, answer: str, known: set[str]) -> list[Question]:
        out = []
        for f in q.followups:
            if f.when_matches:
                if re.search(f.when_matches, answer or ''):
                    out.append(f)
            elif f.when == 'always' or answer.strip():
                out.append(f)
        return out


def load_generator(spec: str | None) -> Generator:
    if not spec:
        return Generator()
    mod, _, attr = spec.partition(':')
    obj = getattr(importlib.import_module(mod), attr or 'Generator')
    return obj() if isinstance(obj, type) else obj


# --- value of information -------------------------------------------------------------

def score(q: Question, known: set[str], asked_in_section: Counter | None = None,
          imagined: list[str] | None = None) -> float:
    """weight x unknown share of its facets x spread of imagined answers x section spread.
    A question whose facets are all known scores 0: never ask what the store already holds."""
    facets = q.facets
    unknown = sum(1 for f in facets if f not in known) / len(facets)
    if unknown == 0:
        return 0.0
    k = len(set(imagined if imagined is not None else q.imagined))
    spread = 1 - 1 / k if k >= 2 else 0.5
    diversity = 1 / (1 + (asked_in_section or Counter())[q.section])
    return round(min(q.weight, 5) / 5 * unknown * spread * diversity, 6)


def known_facets(store: PrincipalStore) -> set[str]:
    """Facets already known: from `facets:` on nodes and from answer events in the ledger."""
    known: set[str] = set()
    for n in store.nodes.values():
        f = n.get('facets')
        if isinstance(f, list):
            known.update(str(x) for x in f)
    if store.ledger_path.exists():
        led = Ledger(store.ledger_path)
        try:
            for ev in led.iter(kind='answer'):
                known.update(ev['body'].get('facets') or [])
        finally:
            led.close()
    return known


# --- the interview ------------------------------------------------------------------

@dataclass
class Turn:
    question: Question
    score: float
    answer: Optional[str]           # None = skipped


AskFn = Callable[[Question, int, int], Optional[str]]   # (question, index, max) -> answer, None skip
STOP = object()


def plan(bank: Bank, known: set[str], n: int = DEFAULT_MAX, generator: Generator | None = None) -> list[tuple[Question, float]]:
    """The first `n` questions in the order they would be asked if every answer were skipped."""
    gen = generator or Generator()
    pool = list(bank.questions) + list(gen.draft(bank, known))
    asked: Counter = Counter()
    out = []
    while pool and len(out) < n:
        best = _best(pool, known, asked, gen)
        if best is None:
            break
        q, s = best
        pool.remove(q)
        asked[q.section] += 1
        out.append((q, s))
    return out


def _best(pool, known, asked, gen) -> tuple[Question, float] | None:
    scored = [(score(q, known, asked, gen.imagine(q, known)), q) for q in pool]
    scored = [(s, q) for s, q in scored if s > 0]
    if not scored:
        return None
    s, q = min(scored, key=lambda x: (-x[0], x[1].rank, x[1].id))
    return q, s


def interview(bank: Bank, known: set[str], ask: AskFn, max_questions: int = DEFAULT_MAX,
              generator: Generator | None = None, count_skips: bool = True) -> list[Turn]:
    """Ask greedily by value of information, recomputing after each answer. `ask` returns the
    answer, None to skip, or `STOP` to end. With `count_skips=False` (answers from a file)
    a skipped question costs nothing and does not count toward `max_questions`."""
    gen = generator or Generator()
    known = set(known)
    pool = list(bank.questions) + list(gen.draft(bank, known))
    asked: Counter = Counter()
    turns: list[Turn] = []
    spent = 0
    while pool and (max_questions <= 0 or spent < max_questions):
        best = _best(pool, known, asked, gen)
        if best is None:
            break
        q, s = best
        pool.remove(q)
        reply = ask(q, spent + 1, max_questions)
        if reply is STOP:
            break
        answer = reply.strip() if isinstance(reply, str) else None
        if answer is not None and answer.lower() in SKIP_WORDS:
            answer = None
        turns.append(Turn(q, s, answer))
        if answer is None and not count_skips:
            continue
        spent += 1
        asked[q.section] += 1
        if answer is not None:
            known.update(q.facets)
            pool += [f for f in gen.follow_ups(q, answer, known) if f not in pool]
    return turns


# --- writing answers ------------------------------------------------------------------

def _writable_ledger(path, name):
    from .ledger_authority.factory import open_writable_ledger
    return open_writable_ledger(path, name, exo_home() / 'keys' / f'{name}.key', auto_keygen=True)


def record(store: PrincipalStore, principal: str, bank: Bank, turns: list[Turn],
           session: str | None = None) -> list[dict]:
    """Append a `question` and an `answer` event per answered turn. Returns the answer events."""
    out = []
    session = session or f'onboard-{crypto.now_ts()[:19].replace(":", "")}'
    with _writable_ledger(store.ledger_path, INTERVIEWER) as asker, \
            _writable_ledger(store.ledger_path, principal) as said:
        for t in turns:
            if t.answer is None:
                continue
            q = t.question
            qev = asker.append('question', {'question_id': q.id, 'text': q.ask, 'section': q.section,
                                          'why': q.why, 'voi': t.score, 'source': 'onboard'},
                             INTERVIEWER, session=session)
            out.append(said.append('answer', {'question_id': q.id, 'question': q.ask, 'answer': t.answer,
                                             'section': q.section, 'facets': list(q.facets),
                                             'node': bank.node_for(q), 'source': 'onboard'},
                                  principal, session=session, refs=[qev['id']]))
    os.chmod(store.ledger_path, 0o600)
    return out


def _quote(text: str) -> str:
    return '"' + ' '.join(text.split()).replace('"', "'") + '"'


def _fm_text(s: str) -> str:
    return ' '.join(str(s).split()).replace('"', "'")


def render_nodes(store: PrincipalStore, bank: Bank | None = None) -> list[str]:
    """Rebuild every `a-said` node from the ledger's onboarding answers (newest answer per
    question wins). A curated node already at the target id is left alone; the answers go to
    `<id>-said` beside it. Returns the node ids written."""
    bank = bank or load_bank(_bank_dirs(store))
    if not store.ledger_path.exists():
        return []
    latest: dict[str, dict] = {}
    led = Ledger(store.ledger_path)
    try:
        for ev in led.iter(kind='answer'):
            if ev['body'].get('source') == 'onboard' and ev['body'].get('question_id'):
                latest[ev['body']['question_id']] = ev
    finally:
        led.close()
    groups: dict[str, list[dict]] = defaultdict(list)
    for ev in latest.values():
        groups[ev['body'].get('node') or 'person/interview'].append(ev)
    store.reload()
    target: dict[str, str] = {}
    for nid in groups:
        cur = store.get(nid)
        target[nid] = nid if cur is None or cur.get('kind') == A_SAID or _is_example(cur) else f'{nid}-said'
    ids = sorted(target.values())
    hub = 'person/identity' if 'person/identity' in ids else (ids[0] if ids else '')
    written = []
    for nid, events in groups.items():
        tid = target[nid]
        events.sort(key=lambda e: e['seq'])
        spec = bank.nodes.get(nid) or NodeSpec(nid, f'Interview answers: {nid}', 'preference',
                                               f'What the principal said in the interview ({nid}).',
                                               'Tasks that touch this topic.')
        links = [{'to': x, 'rel': 'see-also'} for x in ids if x != tid] if tid == hub else \
                ([{'to': hub, 'rel': 'see-also'}] if hub and hub != tid else [])
        if tid != nid:
            links.append({'to': nid, 'rel': 'evidence-for'})
        facets = sorted({f for e in events for f in (e['body'].get('facets') or [])})
        lines = ['---', f'id: {tid}', f'title: {_fm_text(spec.title)}', f'type: {spec.type}',
                 f'domain: {tid.split("/")[0]}', f'summary: {_fm_text(spec.summary)[:190]}',
                 f'load_when: {_fm_text(spec.load_when)}', 'confidence: low', f'stability: {spec.stability}',
                 f'valid_from: {events[0]["ts"][:10]}', f'kind: {A_SAID}', 'sources:']
        for e in events:
            lines += [f'  - kind: {A_SAID}', f'    ref: {event_ref(e["id"])}', f'    note: onboarding answer to {e["body"]["question_id"]}']
        lines.append('links:')
        lines += [f'  - {{to: {l["to"]}, rel: {l["rel"]}}}' for l in links]
        lines += [f'facets: [{", ".join(facets)}]', 'tags: [onboarding, a-said]', '---', '']
        body = ['What the principal said in the onboarding interview. Low confidence: first answers, '
                'not yet confirmed in use. A newer answer to the same question replaces an older one.', '']
        for e in events:
            b = e['body']
            ans = b['answer']
            if PrincipalStore._leaks(ans):
                ans_line = f'(withheld from this node: it looked like a secret or contact detail; see {event_ref(e["id"])})'
            else:
                ans_line = _quote(ans)
            body += [f'**Q:** {" ".join(b["question"].split())}', f'**Said ({e["ts"][:10]}):** {ans_line}', '']
        body += ['How to apply: treat these as the principal\'s stated view. Confirm before relying on '
                 'one for anything costly, public or irreversible, and record the confirmation.']
        path = store.nodes_dir / f'{tid}.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(lines + body) + '\n', encoding='utf-8')
        written.append(tid)
    store.reload()
    store.index()
    return sorted(written)


def _is_example(node: dict) -> bool:
    """A placeholder node shipped with the empty store template: safe to replace."""
    tags = node.get('tags') or []
    srcs = node.get('sources') or []
    return 'example' in tags or any(isinstance(x, dict) and str(x.get('ref', '')).startswith('template:')
                                    for x in srcs)


def seed_queue(store: PrincipalStore, bank: Bank, limit: int = 8, generator: Generator | None = None) -> list[str]:
    """Hand the best unasked bank questions to the ongoing ask loop (`exo.ask` queue), so they
    reach the principal later inside the interruption budget. Returns the queue ids added."""
    if limit <= 0:
        return []
    from .ask import QuestionQueue
    gen = generator or Generator()
    queue = QuestionQueue(store.root)
    have = set(queue.state())
    added = []
    known = known_facets(store)
    for q, s in plan(bank, known, limit + len(have), gen):
        qid = f'onboard-{q.id}'
        if qid in have:
            continue
        k = len(set(gen.imagine(q, known)))
        queue.propose(q.ask, bank.node_for(q), q.why, impact=min(q.weight, 5) / 5,
                      uncertainty=(1 - 1 / k) if k >= 2 else 0.5, source='onboard', qid=qid)
        added.append(qid)
        if len(added) >= limit:
            break
    return added


def _bank_dirs(store: PrincipalStore | None) -> list[Path]:
    dirs = [bank_dir()]
    if store is not None and (store.root / 'method' / 'question-bank').is_dir():
        dirs.append(store.root / 'method' / 'question-bank')
    return dirs


# --- answer sources -----------------------------------------------------------------

def load_answers(path: str | os.PathLike) -> dict[str, str]:
    """A YAML map of question id to answer, either top-level or under `answers:`."""
    doc = parse_yaml(Path(path).read_text(encoding='utf-8')) or {}
    if not isinstance(doc, dict):
        raise ValueError(f'{path}: expected a map of question id to answer')
    doc = doc.get('answers', doc) if isinstance(doc.get('answers'), dict) else doc
    return {str(k): ('' if v is None else str(v)) for k, v in doc.items()}


def file_asker(answers: dict[str, str]) -> AskFn:
    return lambda q, i, n: answers.get(q.id)


def tty_asker(inp: Callable[[str], str] = input, out=sys.stdout) -> AskFn:
    def ask(q: Question, i: int, n: int):
        total = f'/{n}' if n > 0 else ''
        print(f'\n[{i}{total}] ({q.section}) {q.ask}', file=out)
        if q.why:
            print(f'  why: {q.why}', file=out)
        try:
            reply = inp('> ')
        except EOFError:
            return STOP
        if reply.strip().lower() in STOP_WORDS:
            return STOP
        return reply
    return ask


# --- high-level flows -----------------------------------------------------------------

def run_interview(store: PrincipalStore, principal: str, ask: AskFn, max_questions: int = DEFAULT_MAX,
                  generator: Generator | None = None, count_skips: bool = True, seed: int = 8) -> dict:
    bank = load_bank(_bank_dirs(store))
    turns = interview(bank, known_facets(store), ask, max_questions, generator, count_skips)
    events = record(store, principal, bank, turns)
    nodes = render_nodes(store, bank) if events else []
    queued = seed_queue(store, bank, seed, generator)
    return {'asked': len(turns), 'answered': len(events), 'nodes': nodes, 'queued': queued, 'lint': store.lint()}


def create_store(pid: str, root: str | os.PathLike | None = None,
                 template: str | os.PathLike | None = None) -> PrincipalStore:
    root = Path(root) if root else principal_root(pid)
    store = PrincipalStore.create(root, template=template)
    led = Ledger(store.ledger_path)
    led.clear_anchors()  # a new ledger: an anchor left by a deleted store at this path is stale (D-040)
    led.close()
    os.chmod(store.ledger_path, 0o600)
    from .keyring import KeyRegistry
    KeyRegistry.load().add_principal(pid)  # D-040: ledger events by this id are the principal's voice
    return store


def _activate(pid: str, root: Path | None = None, force: bool = False) -> bool:
    from .config import Config
    cfg = Config.load()
    changed = False
    if force or not cfg.data.get('principal'):
        cfg.set('principal', pid)
        changed = True
    if root is not None and Path(root).resolve() != (cfg.home / 'principals' / pid).resolve():
        cfg.set(f'principals.{pid}', str(Path(root).resolve()))
        changed = True
    if changed:
        cfg.save()
    return changed


# --- import-store ---------------------------------------------------------------------

ALWAYS_SKIP = {'.git', '__pycache__', '.DS_Store', '_drafts', '.pytest_cache'}
PRIVATE_PATHS = {'corpus/private', 'ledger.sqlite', 'ledger.sqlite-wal', 'ledger.sqlite-shm', 'ledger.sqlite.head'}


def import_store(src: str | os.PathLike, dest: str | os.PathLike, include_private: bool = False) -> dict:
    """Copy a hafiz-format store into `dest`. Never moves or edits the source. Skips `.git`,
    caches, drafts, key files, symlinks, and (unless `include_private`) `corpus/private` and the
    ledger with its head anchor. Fills in template parts the source lacks (policies, agents, ...)."""
    src = Path(src).expanduser().resolve()
    dest = Path(dest).expanduser()
    if not (src / 'INDEX.md').is_file() or not (src / 'nodes').is_dir():
        raise ValueError(f'{src} is not a principal store: it needs INDEX.md and nodes/')
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f'{dest} exists and is not empty')
    if dest.resolve() == src or src in dest.resolve().parents:
        raise ValueError('destination must be outside the source store')
    stats = {'files': 0, 'bytes': 0, 'skipped': [], 'symlinks': [], 'filled': []}

    def walk(rel: Path):
        for entry in sorted((src / rel).iterdir()):
            r = rel / entry.name
            rs = r.as_posix()
            if entry.name in ALWAYS_SKIP or entry.name.endswith('.key'):
                stats['skipped'].append(rs)
                continue
            if rs in PRIVATE_PATHS and not include_private:
                stats['skipped'].append(rs)
                continue
            if entry.is_symlink():
                stats['symlinks'].append(rs)
                continue
            if entry.is_dir():
                (dest / r).mkdir(parents=True, exist_ok=True)
                shutil.copystat(entry, dest / r)
                walk(r)
            elif entry.is_file():
                (dest / r).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, dest / r)
                stats['files'] += 1
                stats['bytes'] += entry.stat().st_size

    dest.mkdir(parents=True, exist_ok=True)
    walk(Path('.'))
    tpl = template_dir()
    for f in sorted(tpl.rglob('*')):
        if f.is_dir() or '__pycache__' in f.parts or f.name == '.DS_Store':
            continue
        rel = f.relative_to(tpl)
        out = dest / rel
        if rel.as_posix() == '.gitignore' and out.exists():
            have = out.read_text(encoding='utf-8').splitlines()
            add = [l for l in f.read_text(encoding='utf-8').splitlines() if l.strip() and l not in have]
            if add:
                out.write_text('\n'.join(have + ['# added by exo import-store'] + add) + '\n', encoding='utf-8')
                stats['filled'].append('.gitignore (merged)')
            continue
        if not out.exists() and not (rel.name == '.gitkeep' and out.parent.exists()):
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            stats['filled'].append(rel.as_posix())
    private = dest / 'corpus' / 'private'
    private.mkdir(parents=True, exist_ok=True)
    os.chmod(private, 0o700)
    store = PrincipalStore(dest)
    led_new = not store.ledger_path.exists()
    with _writable_ledger(store.ledger_path, INTERVIEWER) as led:
        led.clear_anchors(beside=False)  # dest was empty: a keys/heads anchor for it is a deleted store's (D-040)
        led.append('ingest', {'adapter': 'import-store', 'source': str(src), 'files': stats['files'],
                              'bytes': stats['bytes'], 'include_private': include_private,
                              'skipped': stats['skipped'], 'symlinks_skipped': stats['symlinks'],
                              'filled_from_template': stats['filled']}, INTERVIEWER)
    os.chmod(store.ledger_path, 0o600)
    stats['ledger_created'] = led_new
    stats['lint'] = store.lint()
    stats['root'] = store.root
    return stats


# --- command line -----------------------------------------------------------------------

def add_init_args(p: argparse.ArgumentParser) -> None:
    """Extra flags for `exo init` (exo.cli calls this on its init parser)."""
    p.add_argument('--interview', action='store_true', help='run the onboarding interview after creating the store')
    _interview_args(p)


def _interview_args(p: argparse.ArgumentParser) -> None:
    p.add_argument('--answers', metavar='FILE', help='read answers from a YAML file instead of asking')
    p.add_argument('--max-questions', type=int, default=DEFAULT_MAX, help=f'questions to ask (default {DEFAULT_MAX}; 0 = all)')
    p.add_argument('--generator', metavar='MOD:ATTR', help='a Generator for model-drafted questions (default: none)')
    p.add_argument('--plan', action='store_true', help='print the questions in order and exit')
    p.add_argument('--seed-queue', type=int, default=8, metavar='N',
                   help='hand the next N unasked questions to the ask loop (default 8; 0 = none)')


def register(sp, common) -> None:
    """Add `interview`, `import-store` and `config` to the exo parser."""
    p = sp.add_parser('interview', parents=[common], help='ask the onboarding questions for an existing store')
    _interview_args(p)
    p = sp.add_parser('import-store', help='adopt an existing hafiz-format store as a principal (copy)')
    p.add_argument('path')
    p.add_argument('--id', help='principal id (default: the source folder name)')
    p.add_argument('--root', help='destination (default: $EXO_HOME/principals/<id>)')
    p.add_argument('--include-private', action='store_true', help='also copy corpus/private and the ledger')
    p.add_argument('--activate', action='store_true', help='make it the active principal in config.json')
    p = sp.add_parser('config', help='read and write $EXO_HOME/config.json')
    p.add_argument('config_args', nargs=argparse.REMAINDER)


NO_STORE_CMDS = ('init', 'import-store', 'config')


def _print_result(res: dict, pid: str) -> None:
    print(f'interview: {res["answered"]} answered, {res["asked"] - res["answered"]} skipped; '
          f'nodes: {", ".join(res["nodes"]) or "none"}')
    if res.get('queued'):
        print(f'{len(res["queued"])} more question(s) queued for the ask loop (exo ask list).')
    print(res['lint'].render().splitlines()[-1])
    if res['answered']:
        print(f'Answers are low-confidence a-said beliefs. Review them with: exo find onboarding --principal {pid}')


def _run_from_args(a, store: PrincipalStore, pid: str) -> int:
    gen = load_generator(getattr(a, 'generator', None))
    if getattr(a, 'plan', False):
        bank = load_bank(_bank_dirs(store))
        for i, (q, s) in enumerate(plan(bank, known_facets(store), a.max_questions or 10**6, gen), 1):
            print(f'{i:>3}. [{s:.3f}] ({q.section}) {q.id}: {q.ask}')
        return 0
    if a.answers:
        ask, count_skips = file_asker(load_answers(a.answers)), False
    else:
        if not sys.stdin.isatty():
            sys.exit('no terminal to ask on: pass --answers FILE')
        print('Onboarding interview. Press Enter to skip a question, q to stop. Do not type passwords or keys.')
        ask, count_skips = tty_asker(), True
    res = run_interview(store, pid, ask, a.max_questions, gen, count_skips, a.seed_queue)
    _print_result(res, pid)
    return 0 if res['lint'].ok else 1


def cmd_init(a) -> int:
    """`exo init <id> [--interview ...]`: create the store, then optionally interview."""
    if not ID_RE.match(a.id):
        sys.exit(f'bad principal id {a.id!r}: use lowercase letters, digits, - and _')
    root = Path(a.root) if getattr(a, 'root', None) else None
    try:
        store = create_store(a.id, root, getattr(a, 'from_template', None) or None)
    except (FileExistsError, FileNotFoundError) as e:
        sys.exit(str(e))
    _activate(a.id, root)
    print(f'created principal store {a.id} at {store.root}')
    if not getattr(a, 'interview', False):
        print(store.lint().render().splitlines()[-1])
        return 0
    return _run_from_args(a, store, a.id)


def cmd_interview(a, store: PrincipalStore) -> int:
    pid = a.principal or os.environ.get('EXO_PRINCIPAL') or _config_principal() or store.root.name
    return _run_from_args(a, store, pid)


def cmd_import_store(a) -> int:
    src = Path(a.path).expanduser()
    pid = a.id or re.sub(r'[^a-z0-9_-]+', '-', src.resolve().name.lower()).strip('-')
    if not ID_RE.match(pid or ''):
        sys.exit(f'bad principal id {pid!r}: pass --id')
    dest = Path(a.root) if a.root else principal_root(pid)
    try:
        st = import_store(src, dest, include_private=a.include_private)
    except (ValueError, FileExistsError) as e:
        sys.exit(str(e))
    from .keyring import KeyRegistry
    KeyRegistry.load().add_principal(pid)  # D-040: ledger events by this id are the principal's voice
    activated = _activate(pid, Path(a.root) if a.root else None, force=a.activate)
    print(f'imported {st["files"]} files ({st["bytes"]} bytes) from {src} as principal {pid} at {st["root"]}')
    if st['skipped']:
        print('skipped: ' + ', '.join(st['skipped']))
    if st['symlinks']:
        print('symlinks not followed: ' + ', '.join(st['symlinks']))
    if st['filled']:
        print('added from template: ' + ', '.join(st['filled']))
    if not a.include_private:
        print('corpus/private was not copied. Re-run with --include-private into a fresh root to bring it.')
    if activated:
        print(f'active principal: {pid}')
    print(st['lint'].render().splitlines()[-1])
    return 0


def cmd_config(a) -> int:
    from .config import main as config_main
    return config_main(a.config_args)


def _config_principal() -> str | None:
    try:
        from .config import Config
        return Config.load().principal
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    """Standalone entry: `python -m exo.onboard init|interview|import-store|config ...`."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--principal', help='principal id (default: $EXO_PRINCIPAL, then config.json)')
    common.add_argument('--root', help='store directory; overrides --principal')
    ap = argparse.ArgumentParser(prog='exo', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('init', parents=[common], help='create an empty principal store')
    p.add_argument('id')
    p.add_argument('--from-template', nargs='?', const='', default='', metavar='PATH')
    add_init_args(p)
    register(sp, common)
    a = ap.parse_args(argv)
    if a.cmd == 'init':
        return cmd_init(a)
    if a.cmd == 'import-store':
        return cmd_import_store(a)
    if a.cmd == 'config':
        return cmd_config(a)
    if a.root:
        store = PrincipalStore(a.root)
    else:
        pid = a.principal or os.environ.get('EXO_PRINCIPAL') or _config_principal()
        if not pid:
            sys.exit('no active principal: pass --principal <id> or --root <dir>')
        store = PrincipalStore.for_principal(pid)
    if not store.exists():
        sys.exit(f'no principal store at {store.root}; run: exo init <id>')
    return cmd_interview(a, store)


if __name__ == '__main__':
    sys.exit(main())
