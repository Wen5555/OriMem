"""DeepSeek Harness (dsh) source: ~/.dsh/sessions/<workspace>/<session>/session.v4.jsonl.zstd.

The log is a zstd-compressed JSONL stream: one ``session`` header record, then
events ``{seq, time, type, data}``. Only ``user/message`` (data is the message)
and ``assistant/message`` (data.message) text blocks are kept; ``surfaceOp``
replace ranges (compaction/rewrites) are applied so the mirror follows the
visible transcript. Seeded/forked sessions keep only their own events.
Tool results, system/developer messages and unknown events are skipped.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from orimem import config
from orimem.capture import common

NAME = 'dsh'
NAMESPACE = 'dsh-session-adapter-v1'
PARSER = 'dsh-session-v4'
PREFIX = 'dsh-'
INJECTED = ('<system-reminder>', '<environment_context>', '# AGENTS.md instructions', '<user-prompt-submit-hook>')
SKIP_USER_SOURCES = {'hook', 'system', 'system-prompt', 'compaction', 'tool', 'reminder', 'agent'}


def root() -> Path:
    return config.source_dir(NAME)


def discover():
    base = root()
    if base.is_dir():
        yield from base.glob('*/*/session.v*.jsonl.zstd')
        yield from base.glob('*/*/session.v*.jsonl')


def _lines(path: Path):
    raw = path.read_bytes()
    if path.suffix == '.zstd':
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
            raw = reader.read()
    return raw.decode('utf-8').splitlines()


def _iso(value) -> str | None:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _text(message) -> str:
    parts = []
    for block in message.get('content') or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            text = block.get('text') or ''
            if text.strip() and not text.lstrip().startswith(INJECTED):
                parts.append(text)
    return '\n\n'.join(parts)


def parse(path: Path):
    stats = dict(lines=0, invalid=0, kept=0, skipped=0)
    header, surface = {}, []          # surface: list of (seq, event-dict or None)
    for line_no, line in enumerate(_lines(path), 1):
        stats['lines'] += 1
        try:
            r = json.loads(line)
        except ValueError:
            stats['invalid'] += 1
            continue
        if r.get('type') == 'session' and not header:
            header = r
            continue
        op = r.get('surfaceOp')
        if isinstance(op, dict) and op.get('op') == 'replace':
            start, end = op.get('startSeq'), op.get('endSeq')
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                surface = [(seq, e) for seq, e in surface if not (start <= seq <= end)]
        kind = r.get('type')
        data = r.get('data') if isinstance(r.get('data'), dict) else {}
        if kind == 'user/message':
            message, role = data, 'user'
            source = (message.get('source') or {}).get('kind') if isinstance(message.get('source'), dict) else None
            if source in SKIP_USER_SOURCES:
                stats['skipped'] += 1
                continue
        elif kind == 'assistant/message':
            message, role = data.get('message') if isinstance(data.get('message'), dict) else {}, 'assistant'
        else:
            continue
        message = message or {}
        body = _text(message)
        ts = _iso(r.get('time'))
        if not body.strip() or not ts:
            stats['skipped'] += 1
            continue
        src = message.get('source') if isinstance(message.get('source'), dict) else {}
        m = dict(platform='dsh', adapter=NAMESPACE, parser=PARSER, message_id=message.get('id'),
                 seq=r.get('seq'), raw_source=dict(line=line_no, transcript_path=str(path)))
        if role == 'assistant':
            m.update(model=src.get('model'), provider=src.get('provider'), phase='final',
                     completion_status='final_observed')
        else:
            m.update(source_kind=src.get('kind'))
        surface.append((r.get('seq') if isinstance(r.get('seq'), (int, float)) else line_no,
                        dict(ts=ts, role=role, content=body, metadata=m)))
        stats['kept'] += 1
    events = [e for _, e in surface]
    # Interim assistant chunks: only the last assistant message before the next user turn is final.
    for i, e in enumerate(events):
        if e['role'] == 'assistant' and i + 1 < len(events) and events[i + 1]['role'] == 'assistant':
            e['metadata'].update(phase='commentary', completion_status='interim_observed')
    first = next((e['content'] for e in events if e['role'] == 'user'), '')
    seeded = bool(header.get('isSeeded'))
    session = dict(title=first[:180], cwd=header.get('cwd') or '', first_user_message=first,
                   cli_version=str(header.get('version') or ''), id=header.get('id'),
                   delegated=bool(header.get('delegationDepth')), seeded=seeded)
    return session, events, stats


def capture_file(path: Path) -> dict:
    path = Path(path)
    session, events, stats = parse(path)
    ident = session.get('id') or path.parent.name
    sid = PREFIX + str(ident).removeprefix('session-')
    db = common.adapter_db(NAME)
    reason = None
    if session['delegated']:
        reason = 'subagent (delegated) session excluded'
    elif common.excluded_cwd(session['cwd']):
        reason = 'cwd excluded by capture.exclude_cwd'
    elif not events:
        reason = 'no ordinary messages observed yet'
    if reason:
        common.forget_session(db, sid)
        return dict(captured=False, session_id=sid, reason=reason)
    return common.write_session(db, sid, path, session, events, stats, 'dsh', 'deepseek-compatible')
