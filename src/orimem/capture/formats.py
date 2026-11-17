"""Transcript format checks: which client/format version produced a file, and does it still look
like the format each parser was built against?

Agent transcripts are internal formats that change without notice. A silent change would make
capture quietly keep nothing, so every parse reports:

* ``version``: the client or format version recorded in the file;
* ``status``: ``ok`` | ``unverified`` (version outside the tested range; captured, check output)
  | ``drift`` (message records no longer have the expected shape, header missing, many invalid
  lines; captured what parsed) | ``unsupported`` (format known to be incompatible; not captured);
* ``new_record_types``: record types this parser has never seen (informational only - clients
  add bookkeeping records all the time; message records are what matter).

The latest result per source is kept in ``<home>/sources/formats.json`` for ``orimem doctor``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from orimem.capture import common

# Versions the parsers were verified against; major versions outside these sets are "unverified".
TESTED = {
    'claude': dict(majors={1, 2}, verified='2.1.325'),
    'codex': dict(majors={0}, verified='0.164.0'),
    'dsh': dict(majors={4}, verified='session v4', strict=True),   # other versions: unsupported
}
KNOWN_TYPES = {
    'claude': {'user', 'assistant', 'system', 'summary', 'custom-title', 'ai-title', 'agent-name', 'attachment',
               'queue-operation', 'last-prompt', 'file-history-snapshot', 'file-history-delta', 'atis-latch',
               'api-request', 'api-request-blob', 'api-request-shape', 'cost-state', 'mode', 'permission-mode',
               'pr-link', 'tools-used', 'progress', 'tag', 'worktree-state'},
    'codex': {'session_meta', 'response_item', 'event_msg', 'turn_context', 'compacted', 'world_state',
              'token_usage_record', 'inter_agent_communication_metadata'},
    'dsh': {'session', 'user/message', 'assistant/message', 'assistant/attempt', 'system/message', 'tool/call',
            'tool/result', 'turn/start', 'turn/end', 'step/start', 'step/end', 'permission/preset', 'sandbox/mode',
            'approval/policy', 'approval/asked', 'approval/decided', 'agent/inbox/spliced', 'hook/invoked',
            'hook/result', 'request/header', 'request/context', 'session/title', 'session/title-llm-request',
            'session/end-seed', 'session-log-deepseek/delivery-accepted', 'command/run', 'command/done',
            'web/deepseek-search-llm-request', 'workspace/changes'},
}
MAX_WARNINGS = 20


def _major(version) -> int | None:
    match = re.match(r'\D*(\d+)', str(version or ''))
    return int(match.group(1)) if match else None


def assess(source: str, version, *, header_ok: bool, messages: int, malformed: int, invalid: int,
           lines: int, types) -> dict:
    tested = TESTED[source]
    warnings, status = [], 'ok'
    major = _major(version)
    if major is not None and major not in tested['majors']:
        if tested.get('strict'):
            status = 'unsupported'
            warnings.append(f'{source} format version {version} is not supported (expects {tested["verified"]}); '
                            'not captured - upgrade OriMem')
        else:
            status = 'unverified'
            warnings.append(f'{source} version {version} is outside the tested range (verified on {tested["verified"]}); '
                            'captured, please check the transcripts')
    drift = []
    if not header_ok:
        drift.append('session header missing')
    if malformed:
        drift.append(f'{malformed} of {messages} message records do not have the expected shape')
    if lines and invalid > max(2, lines // 20):
        drift.append(f'{invalid} of {lines} lines are not valid JSON')
    if drift and status != 'unsupported':
        status = 'drift'
        warnings.append(f'{source} transcript format may have changed: ' + '; '.join(drift))
    new_types = sorted({str(t) for t in types if t is not None} - KNOWN_TYPES[source])[:10]
    return dict(status=status, version=str(version or ''), warnings=warnings, new_record_types=new_types)


def _path():
    return common.sources_dir() / 'formats.json'


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def record(source: str, path, fmt: dict | None) -> None:
    """Keep the latest version/status per source plus recent warnings (called under collect.lock)."""
    if not fmt:
        return
    data = load()
    entry = data.setdefault(source, dict(warnings=[], new_record_types=[]))
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    entry.update(last_version=fmt['version'] or entry.get('last_version', ''), last_status=fmt['status'], checked_at=stamp)
    for warning in fmt['warnings']:
        entry['warnings'] = ([w for w in entry['warnings'] if w['warning'] != warning]
                             + [dict(at=stamp, path=str(path), warning=warning)])[-MAX_WARNINGS:]
        common.log(dict(op='format', source=source, path=str(path), status=fmt['status'], warning=warning))
    entry['new_record_types'] = sorted(set(entry['new_record_types']) | set(fmt['new_record_types']))[:30]
    tmp = _path().with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(_path())


def report() -> dict:
    data = load()
    out = {}
    for source, tested in TESTED.items():
        entry = data.get(source) or {}
        out[source] = dict(verified_on=tested['verified'], last_version=entry.get('last_version'),
                           last_status=entry.get('last_status', 'not checked yet'),
                           recent_warnings=[w['warning'] for w in entry.get('warnings', [])[-3:]],
                           new_record_types=entry.get('new_record_types', []))
    return out
