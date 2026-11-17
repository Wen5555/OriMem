"""Codex source: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

Only ordinary user/assistant ``response_item`` messages are kept. Injected
context blocks (AGENTS.md, environment context, skills, app context),
reasoning, tool calls/outputs, automation threads and subagent threads are
excluded. Unknown records are skipped and counted.
"""
from __future__ import annotations

import json
from pathlib import Path

from orimem import config
from orimem.capture import common, formats

NAME = 'codex'
NAMESPACE = 'codex-rollout-adapter-v1'
PARSER = 'codex-rollout-v1'
PREFIX = ''   # bare Codex thread ids, compatible with earlier Codex captures
INJECTED = ('# AGENTS.md instructions', '<INSTRUCTIONS>', '<environment_context>', '<user_instructions>',
            '<external_codex_apps', '<recommended_plugins>', '<in-app-browser-context', '<codex_internal_context',
            '<turn_aborted>', '<subagent_notification>', '<skill>', '# Files mentioned by the user',
            '<permissions instructions>', '<collaboration_mode>', '<user_shell_command>')


def root() -> Path:
    return config.source_dir(NAME)


def discover():
    base = root()
    if base.is_dir():
        yield from base.glob('*/*/*/rollout-*.jsonl')


def find(session_id: str):
    sid = str(session_id)
    if not sid or any(c in sid for c in '*?[]/\\') or not root().is_dir():
        return None
    return next(iter(root().glob(f'*/*/*/rollout-*-{sid}.jsonl')), None)


def _text(content, kinds):
    out = []
    for block in content or []:
        if isinstance(block, dict) and block.get('type') in kinds:
            text = block.get('text') or ''
            if text.strip() and not text.lstrip().startswith(INJECTED):
                out.append(text)
    return '\n\n'.join(out)


def parse(path: Path):
    stats = dict(lines=0, invalid=0, kept=0, skipped=0, messages=0, malformed=0)
    meta, events, types = {}, [], set()
    with open(path, encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            stats['lines'] += 1
            try:
                r = json.loads(line)
            except ValueError:
                stats['invalid'] += 1
                continue
            types.add(r.get('type'))
            payload = r.get('payload') if isinstance(r.get('payload'), dict) else {}
            if r.get('type') == 'session_meta' and not meta:
                meta = payload
                continue
            if r.get('type') != 'response_item' or payload.get('type') != 'message':
                continue
            stats['messages'] += 1
            if not isinstance(payload.get('content'), list) or not isinstance(r.get('timestamp'), str):
                stats['malformed'] += 1
            role = payload.get('role')
            if role not in ('user', 'assistant'):
                stats['skipped'] += 1
                continue
            kinds = ('input_text',) if role == 'user' else ('output_text',)
            body = _text(payload.get('content'), kinds)
            ts = r.get('timestamp')
            if not body.strip() or not isinstance(ts, str):
                stats['skipped'] += 1
                continue
            m = dict(platform='codex', adapter=NAMESPACE, parser=PARSER, message_id=payload.get('id'),
                     client_version=meta.get('cli_version'), raw_source=dict(line=line_no, transcript_path=str(path)))
            if role == 'assistant':
                phase = payload.get('phase') or ''
                final = phase == 'final_answer'
                m.update(phase='final' if final else 'commentary',
                         completion_status='final_observed' if final else 'interim_observed')
            events.append(dict(ts=ts, role=role, content=body, metadata=m))
            stats['kept'] += 1
    first = next((e['content'] for e in events if e['role'] == 'user'), '')
    source = meta.get('source')
    session = dict(title=first[:180], cwd=meta.get('cwd') or '', first_user_message=first,
                   cli_version=meta.get('cli_version') or '', id=meta.get('id') or meta.get('session_id'),
                   thread_source=meta.get('thread_source') or 'user',
                   subagent=isinstance(source, dict) and 'subagent' in source)
    session['format'] = formats.assess(NAME, session['cli_version'], header_ok=bool(meta),
                                       messages=stats['messages'], malformed=stats['malformed'],
                                       invalid=stats['invalid'], lines=stats['lines'], types=types)
    return session, events, stats


def capture_file(path: Path) -> dict:
    path = Path(path)
    session, events, stats = parse(path)
    ident = session.get('id') or '-'.join(path.stem.split('-')[-5:])
    sid = PREFIX + str(ident)
    db = common.adapter_db(NAME)
    reason = None
    if session['subagent']:
        reason = 'subagent thread excluded'
    elif session['thread_source'] != 'user':
        reason = 'automation thread excluded'
    elif common.excluded_cwd(session['cwd']):
        reason = 'cwd excluded by capture.exclude_cwd'
    elif not events:
        reason = 'no ordinary messages observed yet'
    if reason:
        common.forget_session(db, sid)
        return dict(captured=False, session_id=sid, reason=reason, format=session['format'])
    result = common.write_session(db, sid, path, session, events, stats, 'codex', 'openai-compatible')
    result['format'] = session['format']
    return result
