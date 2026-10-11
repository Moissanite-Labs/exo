"""Claude Code project transcripts (`<project dir>/*.jsonl`, one JSON record per line).

Kept: the person's typed messages (`type == user`, string or text blocks), messages typed
while tools ran (`attachment.type == queued_command` with a human origin; easy to miss), and
assistant text blocks. Compact summaries (`isCompactSummary`) are kept with `secondary=true`.
Dropped: tool calls and results, thinking, task notifications, peer agent messages, local
command echoes, meta rows and sidechain (subagent) rows.

Provenance: `src = {adapter, key, path, line, uuid, block?}`; session = `claude_code:<sessionId>`.
Watermark: {file name: number of complete lines read}.
"""
from __future__ import annotations

import json
from pathlib import Path

from .common import Run

ADAPTER = 'claude_code'
ASSISTANT = 'claude-code'
SKIP_PREFIX = ('<task-notification', '<local-command', '<command-name', '<command-message', '<command-args',
               '<agent-message', '<system-reminder', '<bash-input', '<bash-stdout', '<bash-stderr',
               '<user-memory-input', 'Caveat: The messages below')


def _files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(p for p in source.glob('*.jsonl') if p.is_file())


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text')
    return ''


def _human(origin) -> bool:
    """No origin (older transcripts) or origin.kind == human."""
    return not isinstance(origin, dict) or origin.get('kind') in (None, 'human')


def ingest(source_path, ledger, principal_author: str, *, since: str | None = None) -> dict:
    source = Path(source_path).expanduser()
    run = Run(ledger, ADAPTER, source, principal_author, since)
    wm = dict(run.watermark)
    files = _files(source)
    for f in files:
        start = int(wm.get(f.name, 0))
        done = start
        with f.open('r', encoding='utf-8', errors='replace') as fh:
            for n, line in enumerate(fh, 1):
                if n <= start:
                    continue
                if not line.endswith('\n'):
                    break  # partial line still being written; read it next time
                done = n
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    run.stats['bad_json'] += 1
                    continue
                if not isinstance(d, dict) or d.get('isSidechain'):
                    continue
                _row(run, d, f, n, principal_author)
        wm[f.name] = done
    run.stats['files'] = len(files)
    return run.finish(wm)


def _row(run: Run, d: dict, f: Path, n: int, principal: str) -> None:
    sid = d.get('sessionId') or d.get('session_id') or f.stem
    session = f'{ADAPTER}:{sid}'
    uid = d.get('uuid') or f'line{n}'
    src = {'path': str(f.resolve()), 'line': n, 'uuid': d.get('uuid')}
    ts = d.get('timestamp')
    kind = d.get('type')

    if kind == 'user' and not d.get('toolUseResult') and not d.get('isMeta'):
        msg = d.get('message') or {}
        text = _text(msg.get('content'))
        if d.get('isCompactSummary'):
            run.emit(key=f'{sid}:{uid}:summary', role='assistant', text=text, author=ASSISTANT, src=src,
                     source_ts=ts, session=session, secondary=True, summary='compact')
        elif _human(d.get('origin')) and text.strip() and not text.lstrip().startswith(SKIP_PREFIX):
            run.emit(key=f'{sid}:{uid}:user', role='principal', text=text, author=principal, src=src,
                     source_ts=ts, session=session)

    att = d.get('attachment')
    if isinstance(att, dict) and att.get('type') == 'queued_command':
        prompt = att.get('prompt') if isinstance(att.get('prompt'), str) else ''
        if (_human(att.get('origin')) and not att.get('isMeta') and att.get('commandMode') in (None, 'prompt')
                and prompt.strip() and not prompt.lstrip().startswith(SKIP_PREFIX)):
            run.emit(key=f'{sid}:{uid}:queued', role='principal', text=prompt, author=principal, src=src,
                     source_ts=att.get('timestamp') or ts, session=session, queued=True)

    if kind == 'assistant':
        msg = d.get('message') or {}
        content = msg.get('content')
        blocks = content if isinstance(content, list) else [{'type': 'text', 'text': content or ''}]
        for i, x in enumerate(blocks):
            if isinstance(x, dict) and x.get('type') == 'text' and (x.get('text') or '').strip():
                run.emit(key=f'{sid}:{uid}:a{i}', role='assistant', text=x['text'], author=ASSISTANT,
                         src={**src, 'block': i}, source_ts=ts, session=session, model=msg.get('model'))
