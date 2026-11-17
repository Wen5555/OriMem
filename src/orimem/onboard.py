"""Backlog onboarding: bring conversations from before OriMem was installed into the vault, by workspace.

``init`` sets ``capture.since`` to the install time, so older sessions are not captured
automatically. Onboarding is explicit and selective:

1. ``scan``   - read-only inventory of transcripts per working folder (sessions, dates, sources,
                sample prompts, whether a project entry is already linked). Writes nothing to the vault.
2. ``import`` - capture only the folders the user picked (optionally only the last N days) into the
                evidence folder. Transcripts are evidence, not knowledge.
3. ``status`` - imported folders and whether each is linked to a project entry yet; the agent then
                drafts project pages from that history for the user to confirm (see the OriMem skill,
                references/onboarding.md).

State lives in ``<home>/onboard.json``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from orimem import config
from orimem.capture import SOURCES, common, module


def _state_path() -> Path:
    return config.home() / 'onboard.json'


def _load() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    config.home().mkdir(parents=True, exist_ok=True)
    tmp = _state_path().with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(_state_path())


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone(config.zone()).strftime('%Y-%m-%d')


def _linked_entry(cwd: str) -> str | None:
    db = config.db()
    if not db.exists():
        return None
    from orimem.core.task_context import resolve_task_context
    try:
        found = resolve_task_context(db, config.vault(), cwd, '', authorized_scope=config.project_scopes(), budget_chars=256)
    except ValueError:
        return None
    return found['entry']['path'] if found.get('status') == 'matched' else None


def _captured_keys() -> dict:
    try:
        return json.loads((common.sources_dir() / 'scan-state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def scan(days: int | None = 90, sources=None) -> dict:
    """Group ordinary sessions by working folder. ``days``: only files modified in that window (None/0 = all)."""
    floor = time.time() - days * 86400 if days else None
    seen_state = _captured_keys()
    groups: dict = {}
    counted = dict(files=0, sessions=0, excluded=0, unreadable=0)
    for name in [s for s in (sources or config.get('capture.sources') or SOURCES) if s in SOURCES]:
        mod = module(name)
        for path in mod.discover():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if floor is not None and mtime < floor:
                continue
            counted['files'] += 1
            try:
                session, events, _ = mod.parse(path)
            except Exception:
                counted['unreadable'] += 1
                continue
            if mod.skip_reason(session, events) or not session.get('cwd'):
                counted['excluded'] += 1
                continue
            counted['sessions'] += 1
            key = session['cwd'].replace('\\', '/').rstrip('/').casefold()
            g = groups.setdefault(key, dict(cwd=session['cwd'], sessions=0, not_captured=0, first=mtime, last=mtime,
                                            sources=set(), samples=[]))
            g['sessions'] += 1
            g['first'], g['last'] = min(g['first'], mtime), max(g['last'], mtime)
            g['sources'].add(name)
            if seen_state.get(name, {}).get(str(path)) != path.stat().st_mtime_ns:
                g['not_captured'] += 1
            first = (session.get('first_user_message') or '').strip().replace('\n', ' ')
            if first and len(g['samples']) < 2:
                g['samples'].append(first[:80])
    workspaces = []
    for g in sorted(groups.values(), key=lambda x: -x['last']):
        workspaces.append(dict(cwd=g['cwd'], sessions=g['sessions'], not_captured=g['not_captured'],
                               active=f"{_iso(g['first'])} .. {_iso(g['last'])}", sources=sorted(g['sources']),
                               linked_entry=_linked_entry(g['cwd']), sample_prompts=g['samples']))
    result = dict(scanned_at=datetime.now(timezone.utc).isoformat(timespec='seconds'), window_days=days or None,
                  counts=counted, workspaces=workspaces,
                  next=('Pick the folders worth keeping, then: orimem onboard import --cwd "<folder>" [--cwd ...] '
                        '[--days N]. Nothing has been written.'))
    state = _load()
    state['last_scan'] = dict(at=result['scanned_at'], window_days=result['window_days'],
                              workspaces=[w['cwd'] for w in workspaces])
    _save(state)
    return result


def import_workspaces(cwds: list[str], days: int | None = None, sources=None) -> dict:
    """Capture the chosen folders' sessions (all history, or the last ``days``), then project them."""
    cwds = [c for c in (cwds or []) if isinstance(c, str) and c.strip()]
    if not cwds:
        raise ValueError('Choose at least one folder: orimem onboard import --cwd "<folder>"')
    if not config.get('capture.enabled', True):
        raise ValueError('capture.enabled is false; turn capture on first')
    from orimem.capture import collect
    since = time.time() - days * 86400 if days else 0.0
    result = collect(sources, backfill=True, only_cwd=cwds, since=since)
    if result.get('skipped'):
        raise RuntimeError('Another capture run is active; try again in a minute')
    sessions = sorted({sid for report in result.get('sources', {}).values() for sid in report.get('sessions', [])})
    state = _load()
    imported = state.setdefault('imported', {})
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for cwd in cwds:
        entry = imported.setdefault(cwd, dict(first_imported=stamp, sessions=[]))
        entry['last_imported'] = stamp
        entry['window_days'] = days or None
    for cwd in cwds:
        # Sessions started inside this folder (or its subfolders), looked up in the adapter mirrors.
        imported[cwd]['sessions'] = sorted(set(imported[cwd]['sessions']) | set(_sessions_under(cwd, sessions)))
    _save(state)
    projection = result.get('projection') or {}
    return dict(imported_sessions=len(sessions), folders=cwds, projection_errors=projection.get('errors'),
                sources={k: {kk: vv for kk, vv in v.items() if kk not in ('stage', 'sessions')}
                         for k, v in result.get('sources', {}).items()},
                next='Run `orimem onboard status`; for each folder without a linked entry the agent drafts a project '
                     'page from these sessions for the user to confirm.')


def _sessions_under(cwd: str, session_ids: list[str]) -> list[str]:
    """Which of ``session_ids`` were started inside ``cwd`` (looked up in the adapter mirrors)."""
    import sqlite3
    from contextlib import closing
    wanted, out = set(session_ids), []
    for name in SOURCES:
        db = common.adapter_db(name)
        if not db.exists():
            continue
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as c:
            for sid, folder in c.execute('SELECT session_id, cwd FROM source_sessions'):
                if sid in wanted and common.within(folder, [cwd]):
                    out.append(sid)
    return out


def status() -> dict:
    state = _load()
    folders = []
    for cwd, entry in sorted((state.get('imported') or {}).items()):
        linked = _linked_entry(cwd)
        folders.append(dict(cwd=cwd, sessions=len(entry.get('sessions', [])), last_imported=entry.get('last_imported'),
                            linked_entry=linked, needs=None if linked else 'draft a project page, confirm, then initialize'))
    return dict(last_scan=state.get('last_scan', {}).get('at'), imported=folders,
                pending=[f['cwd'] for f in folders if not f['linked_entry']])
