"""Shared adapter store for all agent sources.

Every source parser (Claude Code, Codex, dsh, ...) mirrors one main-session
transcript into its own adapter DB with the same two tables, then the shared
``stage_sources`` path moves eligible sessions into the OriMem database under a
per-source checkpoint namespace. Projection into the vault stays with
``core.continuous.project``.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from orimem import config

SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_sessions(
 session_id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL, cwd TEXT NOT NULL, source TEXT NOT NULL, model_provider TEXT NOT NULL,
 cli_version TEXT NOT NULL, title TEXT NOT NULL, first_user_message TEXT NOT NULL,
 thread_source TEXT NOT NULL DEFAULT 'user', session_kind TEXT NOT NULL DEFAULT 'root',
 parent_thread_id TEXT NOT NULL DEFAULT '', forked_from_id TEXT NOT NULL DEFAULT '',
 agent_path TEXT NOT NULL DEFAULT '', archive_kind TEXT NOT NULL DEFAULT 'active',
 source_line_start INTEGER NOT NULL DEFAULT 1, source_line_end INTEGER NOT NULL DEFAULT 1,
 event_records_seen INTEGER NOT NULL DEFAULT 0, duplicate_events INTEGER NOT NULL DEFAULT 0,
 inherited_records INTEGER NOT NULL DEFAULT 0, invalid_records INTEGER NOT NULL DEFAULT 0,
 redacted_values INTEGER NOT NULL DEFAULT 0, truncated_values INTEGER NOT NULL DEFAULT 0,
 ingested_at INTEGER NOT NULL DEFAULT (strftime('%s','now')));
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, event_index INTEGER NOT NULL,
 ts TEXT NOT NULL, event_type TEXT NOT NULL, role TEXT, name TEXT, call_id TEXT,
 content TEXT NOT NULL, metadata_json TEXT NOT NULL, UNIQUE(session_id, event_index),
 FOREIGN KEY(session_id) REFERENCES source_sessions(session_id) ON DELETE CASCADE);
'''


def sources_dir() -> Path:
    path = config.home() / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def adapter_db(name: str) -> Path:
    return sources_dir() / f"{name}.db"


def epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp())


def excluded_cwd(cwd: str) -> bool:
    norm = (cwd or '').replace('\\', '/').rstrip('/').casefold()
    for prefix in config.get('capture.exclude_cwd') or []:
        p = str(prefix).replace('\\', '/').rstrip('/').casefold()
        if p and (norm == p or norm.startswith(p + '/')):
            return True
    return False


def write_session(db: Path, sid: str, path: Path, session: dict, events: list, stats: dict,
                  source: str, provider: str) -> dict:
    """Replace one session's mirror. ``events``: dicts with ts, role, content, metadata."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=30)) as c, c:
        c.executescript(SCHEMA)
        c.execute('DELETE FROM events WHERE session_id=?', (sid,))
        c.execute('INSERT OR REPLACE INTO source_sessions(session_id,rollout_path,created_at,updated_at,cwd,source,model_provider,'
                  'cli_version,title,first_user_message,thread_source,session_kind,source_line_end,event_records_seen,invalid_records) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (sid, str(path), epoch(events[0]['ts']), epoch(events[-1]['ts']), session.get('cwd') or '', source,
                   provider, session.get('cli_version') or '', (session.get('title') or '')[:180],
                   session.get('first_user_message') or '', session.get('thread_source') or 'user',
                   session.get('session_kind') or 'root', stats.get('lines', 0), stats.get('lines', 0),
                   stats.get('invalid', 0)))
        c.executemany('INSERT INTO events(session_id,event_index,ts,event_type,role,content,metadata_json) VALUES(?,?,?,?,?,?,?)',
                      [(sid, i, e['ts'], 'message', e['role'], e['content'], json.dumps(e['metadata'], ensure_ascii=False))
                       for i, e in enumerate(events)])
    return dict(captured=True, session_id=sid, messages=len(events))


def forget_session(db: Path, sid: str) -> None:
    if not db.exists():
        return
    with closing(sqlite3.connect(db, timeout=30)) as c, c:
        c.executescript(SCHEMA)
        c.execute('DELETE FROM events WHERE session_id=?', (sid,))
        c.execute('DELETE FROM source_sessions WHERE session_id=?', (sid,))


@contextmanager
def file_lock(path: Path, blocking: bool = False):
    """Cross-platform single-byte advisory lock. Yields False when busy (non-blocking)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+b') as handle:
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            acquired = True
        except OSError:
            acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def capture_backend():
    """Backend that stages and projects but never loads an embedding model."""
    from orimem.core.backend import Backend
    from orimem.capture.worker import CaptureOnly
    return Backend(config.vault(), config.db(), CaptureOnly(config.db().resolve()))


def stage(name: str, namespace: str, backend=None) -> dict:
    from orimem.core.source_stage import stage_sources
    db = adapter_db(name)
    if not db.exists():
        return dict(staged=False, reason='no captured sessions yet')
    with file_lock(sources_dir() / f'{name}.stage.lock', blocking=True):
        backend = backend or capture_backend()
        result = stage_sources(backend, db, namespace=namespace)
    return dict(staged=True, result={k: result.get(k) for k in ('pending_sessions', 'changed_sessions', 'at') if isinstance(result, dict)})


def log(entry: dict) -> None:
    with open(sources_dir() / 'capture-log.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(dict(at=datetime.now().astimezone().isoformat(), **entry), ensure_ascii=False) + '\n')
