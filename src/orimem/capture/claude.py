"""Claude Code source: ~/.claude/projects/<project>/<uuid>.jsonl main transcripts.

Subagent/sidechain files, tool payloads, thinking, injected/meta records,
scheduled-task runs and `claude -p` (sdk) sessions are excluded. The
transcript format is internal to Claude Code; unknown records are skipped and
counted, never guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from orimem import config
from orimem.capture import common, formats

NAME = 'claude'
NAMESPACE = 'claude-code-adapter-v1'
PARSER = 'claude-transcript-v1'
PREFIX = 'claude-'
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
SYNTHETIC = ('<command-name>', '<command-message>', '<command-args>', '<local-command-stdout>',
             '<local-command-stderr>', '<local-command-caveat>', 'Caveat: The messages below were generated',
             '[Request interrupted by user', '<system-reminder>', '<task-notification>', '<agent-message',
             '<side-sessions-event>', '<ci-monitor-event>', '<cross-session-message', '<user-prompt-submit-hook>')


def root() -> Path:
    return config.source_dir(NAME)


def discover():
    """Main-session transcripts only; subagent transcripts live one level deeper."""
    base = root()
    if not base.is_dir():
        return
    for path in base.glob('*/*.jsonl'):
        if UUID.fullmatch(path.stem):
            yield path


def find(session_id: str):
    sid = str(session_id).removeprefix(PREFIX)
    if not UUID.fullmatch(sid):
        return None
    return next(iter(root().glob(f'*/{sid}.jsonl')), None) if root().is_dir() else None


def transcript_for(path, session_uuid, projects=None):
    path = Path(path).resolve()
    if not UUID.fullmatch(session_uuid or '') or path.suffix != '.jsonl' or path.stem != session_uuid:
        raise ValueError('Not a Claude main-session transcript')
    if path.parent.parent != Path(projects or root()).resolve():
        raise ValueError('Transcript outside the configured Claude projects folder')
    return path


def texts(content, keep_block):
    if isinstance(content, str):
        return [content]
    return [b.get('text', '') for b in content or [] if isinstance(b, dict) and b.get('type') == 'text' and keep_block(b)]


def human(record):
    # The desktop app sends typed prompts through the SDK (promptSource "sdk"), so only
    # origin.kind decides; `claude -p` automation is excluded per session by entrypoint.
    origin = record.get('origin')
    if 'origin' in record:
        return isinstance(origin, dict) and origin.get('kind') == 'human'
    return True   # older clients without origin markers: rely on the synthetic-text filter


def parse(path):
    """Return (session_fields, events, stats); events follow transcript order."""
    stats = dict(lines=0, invalid=0, kept=0, skipped=0, messages=0, malformed=0)
    types = set()
    title = cwd = None
    scheduled = False
    versions, entrypoints, events = [], set(), []
    with open(path, encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            stats['lines'] += 1
            try:
                r = json.loads(line)
            except ValueError:
                stats['invalid'] += 1
                continue
            kind = r.get('type')
            types.add(kind)
            if kind == 'custom-title' and isinstance(r.get('customTitle'), str):
                title = r['customTitle']
            if kind not in ('user', 'assistant'):
                continue
            stats['messages'] += 1
            content = r.get('message', {}).get('content') if isinstance(r.get('message'), dict) else None
            if not isinstance(content, (str, list)) or not isinstance(r.get('timestamp'), str):
                stats['malformed'] += 1
            if r.get('turnOrigin') == 'scheduled':
                scheduled = True   # Claude scheduled-task run: an automation echo, not an ordinary session
            if r.get('isSidechain') or r.get('isMeta') or r.get('isCompactSummary') or r.get('isVisibleInTranscriptOnly'):
                stats['skipped'] += 1
                continue
            if r.get('entrypoint'):
                entrypoints.add(r['entrypoint'])
            if r.get('version') and r['version'] not in versions:
                versions.append(r['version'])
            cwd = r.get('cwd') or cwd
            message = r.get('message') if isinstance(r.get('message'), dict) else {}
            if kind == 'user':
                if r.get('toolUseResult') is not None or not human(r):
                    stats['skipped'] += 1
                    continue
                parts = texts(message.get('content'), lambda b: not b.get('text', '').lstrip().startswith(SYNTHETIC))
                parts = [p for p in parts if not p.lstrip().startswith(SYNTHETIC)]
            else:
                parts = texts(message.get('content'), lambda b: True)
            body = '\n\n'.join(p for p in parts if p and p.strip())
            if not body.strip() or not isinstance(r.get('uuid'), str) or not isinstance(r.get('timestamp'), str):
                stats['skipped'] += 1
                continue
            meta = dict(platform='claude-code', adapter=NAMESPACE, parser=PARSER, message_id=r['uuid'],
                        parent_uuid=r.get('parentUuid'), client_version=r.get('version'),
                        entrypoint=r.get('entrypoint'), raw_source=dict(line=line_no, transcript_path=str(path)))
            if kind == 'assistant':
                final = message.get('stop_reason') == 'end_turn'
                meta.update(api_message_id=message.get('id'), model=message.get('model'),
                            phase='final' if final else 'commentary',
                            completion_status='final_observed' if final else 'interim_observed')
            events.append(dict(ts=r['timestamp'], role=kind, content=body, metadata=meta))
            stats['kept'] += 1
    first = next((e['content'] for e in events if e['role'] == 'user'), '')
    session = dict(title=(title or first)[:180], cwd=cwd or '', first_user_message=first,
                   cli_version=versions[-1] if versions else '', entrypoints=sorted(entrypoints), scheduled=scheduled)
    session['format'] = formats.assess(NAME, session['cli_version'], header_ok=True, messages=stats['messages'],
                                       malformed=stats['malformed'], invalid=stats['invalid'],
                                       lines=stats['lines'], types=types)
    return session, events, stats


def capture_file(path: Path) -> dict:
    path = Path(path)
    session_uuid = path.stem
    sid = PREFIX + session_uuid
    session, events, stats = parse(path)
    db = common.adapter_db(NAME)
    reason = None
    if any(e.startswith('sdk') for e in session['entrypoints']):
        reason = 'non-interactive sdk session excluded'
    elif session['scheduled']:
        reason = 'scheduled-task session excluded'
    elif common.excluded_cwd(session['cwd']):
        reason = 'cwd excluded by capture.exclude_cwd'
    elif not events:
        reason = 'no ordinary messages observed yet'
    if reason:
        common.forget_session(db, sid)
        return dict(captured=False, session_id=sid, reason=reason, format=session['format'])
    result = common.write_session(db, sid, path, session, events, stats, 'claude-code', 'anthropic-compatible')
    result['format'] = session['format']
    return result
