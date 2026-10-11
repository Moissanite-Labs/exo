"""Shared pieces for ingest adapters: redaction, timestamps, authorship confidence, and the
`Run` that dedupes rows, applies `since`, writes events and keeps the watermark.

Every adapter writes `utterance` events. The body always has:

    text        the message text, secrets redacted
    role        "principal", "assistant" or "user" (a user row of doubtful authorship)
    src         provenance: {"adapter", "path", "key", ...locators such as line, uuid, rowid}
    source_ts   the source row's own time, UTC ISO 8601, or null

and optionally `redacted` (count of secrets replaced), `secondary` (true for summaries
that are not anyone's own words) and adapter fields (model, profile, title, ...).

After a run that added events or moved the watermark, one `ingest` event records
{"adapter", "source", "watermark", "since", "stats"}. `src.key` is unique per adapter, so a
rerun adds nothing even if the watermark was lost.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

# --- redaction ----------------------------------------------------------------

_SECRETS: list[tuple[re.Pattern, str]] = [(re.compile(p), label) for p, label in (
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', 'PRIVATE_KEY'),
    (r'\bsk-ant-[A-Za-z0-9_\-]{20,}', 'API_KEY'),
    (r'\bsk-(?:proj-|live-|test-|or-v1-)?[A-Za-z0-9_\-]{20,}', 'API_KEY'),
    (r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}', 'GITHUB_TOKEN'),
    (r'\bgithub_pat_[A-Za-z0-9_]{40,}', 'GITHUB_TOKEN'),
    (r'\bglpat-[A-Za-z0-9_\-]{20,}', 'GITLAB_TOKEN'),
    (r'\bxox[abprs]-[A-Za-z0-9\-]{10,}', 'SLACK_TOKEN'),
    (r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b', 'AWS_KEY'),
    (r'\bAIza[0-9A-Za-z_\-]{35}', 'GOOGLE_KEY'),
    (r'\bhf_[A-Za-z0-9]{30,}', 'HF_TOKEN'),
    (r'\b(?:pk|sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}', 'STRIPE_KEY'),
    (r'\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}', 'JWT'),
    (r'(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{20,}=*', 'BEARER'),
)]
# key = value assignments: keep the name, drop the value.
_ASSIGN = re.compile(r'(?i)\b([A-Za-z0-9_\-]*(?:api[_-]?key|secret|token|password|passwd|pwd)[A-Za-z0-9_\-]*)'
                     r'(["\']?\s*[:=]\s*["\']?)([^\s"\'`,;]{8,})')


def redact(text: str) -> tuple[str, int]:
    """Replace obvious secrets with `[REDACTED:<TYPE>]`. Returns (text, count)."""
    if not text:
        return text, 0
    n = 0
    for pat, label in _SECRETS:
        text, k = pat.subn(f'[REDACTED:{label}]', text)
        n += k

    def _assign(m: re.Match) -> str:
        nonlocal n
        if m.group(3).startswith('[REDACTED:'):
            return m.group(0)
        n += 1
        return f'{m.group(1)}{m.group(2)}[REDACTED:SECRET]'

    text = _ASSIGN.sub(_assign, text)
    return text, n


# --- time ---------------------------------------------------------------------

TS_FMT = '%Y-%m-%dT%H:%M:%S.%fZ'


def to_ts(value) -> str | None:
    """Normalise epoch seconds or milliseconds, or an ISO string, to UTC `TS_FMT`."""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r'\d+(\.\d+)?', value)):
            v = float(value)
            if v > 1e11:  # milliseconds
                v /= 1000.0
            return datetime.fromtimestamp(v, timezone.utc).strftime(TS_FMT)
        s = str(value).strip().replace('Z', '+00:00')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
            s += 'T00:00:00+00:00'
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime(TS_FMT)
    except (ValueError, OverflowError, OSError):
        return None


# --- authorship ---------------------------------------------------------------

def author_conf(text: str) -> str:
    """How likely a "user" row was typed by a person rather than written by an agent as a
    brief. Heuristic: casual lowercase and short favour a person; imperative brief openers,
    long bullet lists and heavy markdown favour an agent. Returns high, medium or low."""
    t = (text or '').strip()
    a = 0
    first = next((c for c in t if c.isalpha()), '')
    if first.islower():
        a += 2
    if re.search(r"\b(i|im|ive|ill|idk|lmk|dont|cant|wont|thats|u)\b", t):
        a += 2
    if len(t) < 160:
        a += 1
    if re.search(r"(?i)^(you are|produce|research|write|reply with|accepted as|the user authorized|read-only|"
                 r"your task|task:|context:|instructions:)", t):
        a -= 4
    if t.count('\n- ') > 4 or t.count('**') > 3:
        a -= 2
    if len(t) > 6000:
        a -= 2
    return 'high' if a >= 3 else 'medium' if a >= 1 else 'low'


# --- export files -------------------------------------------------------------

def load_export_json(path: str | Path, member_suffixes: tuple[str, ...]):
    """Load JSON from a file, a directory or a zip without extracting anything to disk.
    Returns (data, member) where member is the path inside a zip or directory, or None."""
    p = Path(path).expanduser()
    if p.is_dir():
        for suf in member_suffixes:
            hits = sorted(x for x in p.rglob('*') if x.is_file() and x.as_posix().endswith(suf))
            if hits:
                return json.loads(hits[0].read_text(encoding='utf-8')), hits[0].relative_to(p).as_posix()
        raise FileNotFoundError(f'no {" or ".join(member_suffixes)} under {p}')
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            for suf in member_suffixes:
                names = sorted(n for n in z.namelist() if n.endswith(suf))
                if names:
                    with z.open(names[0]) as fh:
                        return json.load(io.TextIOWrapper(fh, encoding='utf-8')), names[0]
        raise FileNotFoundError(f'no {" or ".join(member_suffixes)} in {p}')
    return json.loads(p.read_text(encoding='utf-8')), None


# --- the run ------------------------------------------------------------------

class Run:
    """One adapter pass over one source. Use `emit()` per row, then `finish(watermark)`."""

    def __init__(self, ledger, adapter: str, source_path: str | Path, principal_author: str,
                 since: str | None = None):
        if not principal_author:
            raise ValueError('principal_author is required')
        self.ledger = ledger
        self.adapter = adapter
        self.source = str(Path(source_path).expanduser().resolve())
        self.principal = principal_author
        self.since_raw = since
        self.since = to_ts(since) if since else None
        if since and not self.since:
            raise ValueError(f'cannot parse since={since!r}')
        self.watermark: dict = {}
        self.keys: set[str] = set()
        for ev in ledger.iter(kind=('ingest', 'utterance')):
            b = ev['body']
            if ev['kind'] == 'ingest':
                if b.get('adapter') == adapter and b.get('source') == self.source and b.get('watermark') is not None:
                    self.watermark = b['watermark']
            else:
                src = b.get('src') or {}
                if src.get('adapter') == adapter and 'key' in src:
                    self.keys.add(src['key'])
        self.stats: Counter = Counter()

    def emit(self, *, key: str, role: str, text: str, author: str, src: Mapping,
             source_ts=None, session: str | None = None, **extra) -> bool:
        """Append one utterance unless it is empty, a duplicate, or older than `since`."""
        self.stats['seen'] += 1
        if not text or not text.strip():
            self.stats['empty'] += 1
            return False
        ts = to_ts(source_ts) if source_ts is not None else None
        if self.since and (ts is None or ts < self.since):
            self.stats['before_since'] += 1
            return False
        if key in self.keys:
            self.stats['duplicate'] += 1
            return False
        clean, n = redact(text)
        body = {'text': clean, 'role': role, 'source_ts': ts,
                'src': {'adapter': self.adapter, 'key': key, **dict(src)}}
        if n:
            body['redacted'] = n
            self.stats['redacted'] += n
        for k, v in extra.items():
            if v is None:
                continue
            body[k] = redact(v)[0] if isinstance(v, str) else v
        self.ledger.append('utterance', body, author, session=session)
        self.keys.add(key)
        self.stats['added'] += 1
        self.stats[f'added_{role}'] += 1
        if extra.get('secondary'):
            self.stats['added_secondary'] += 1
        return True

    def finish(self, watermark: dict) -> dict:
        """Record an `ingest` event when anything changed. With `since`, the stored watermark
        is left as it was, so rows skipped by `since` stay reachable for a later full run."""
        new_wm = self.watermark if self.since else watermark
        stats = {k: v for k, v in sorted(self.stats.items())}
        stats.setdefault('added', 0)
        if stats['added'] or new_wm != self.watermark:
            self.ledger.append('ingest', {'adapter': self.adapter, 'source': self.source, 'watermark': new_wm,
                                          'since': self.since, 'stats': stats}, self.ledger.signer.author or 'exo-ingest')
        stats['adapter'] = self.adapter
        return stats


class TsCursor:
    """Watermark for snapshot exports: the newest source time already ingested. Rows strictly
    older are skipped fast; rows at or after it go through key dedupe."""

    def __init__(self, run: Run):
        self.run = run
        self.floor = run.watermark.get('max_ts')
        self.top = self.floor

    def old(self, ts) -> bool:
        t = to_ts(ts)
        if t and (self.top is None or t > self.top):
            self.top = t
        if self.floor and t and t < self.floor:
            self.run.stats['below_watermark'] += 1
            return True
        return False

    def watermark(self) -> dict:
        return {'max_ts': self.top}
