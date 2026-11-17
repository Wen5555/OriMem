"""Bound Stop signals to captured sources and verified synthesis outcomes.

This module schedules and validates metadata. It never synthesizes messages,
reads raw transcripts, or writes vault notes.
"""
import json
from pathlib import Path
from orimem import journal_state
from orimem import journal_writer
from .lifecycle import sha, stamp

SCHEMA = '''CREATE TABLE IF NOT EXISTS stage_decisions(
 job_id TEXT PRIMARY KEY,request_sha TEXT NOT NULL,source_key TEXT NOT NULL,
 source_version TEXT NOT NULL,disposition TEXT NOT NULL,reason TEXT NOT NULL,
 receipts TEXT NOT NULL,recorded_at TEXT NOT NULL);'''


def claim(lifecycle, job_id, scope, owner, lease_seconds=600):
    from datetime import datetime, timezone, timedelta
    backend = lifecycle.backend
    scope = backend.scope(scope)
    if not isinstance(owner, str) or not 1 <= len(owner) <= 100 or type(lease_seconds) is not int or not 30 <= lease_seconds <= 1800:
        raise ValueError('Valid stage owner and bounded lease required')
    at = stamp()
    with backend.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM lifecycle_jobs WHERE id=? AND kind=?', (job_id, 'stage')).fetchone()
        if not row:
            raise ValueError('Stage job not found')
        payload = json.loads(row['payload'])
        if not backend.in_scope(payload.get('entry_path', ''), scope):
            raise ValueError('Stage job is outside authorized entry scope')
        if row['state'] == 'running' and row['lease_until'] < at:
            state = 'failed' if row['attempts'] >= 3 else 'pending'
            c.execute('UPDATE lifecycle_jobs SET state=?,owner=NULL,lease_until=NULL,updated_at=? WHERE id=?', (state, at, job_id))
            row = dict(row, state=state)
        if row['state'] not in {'pending','retry'} or row['available_at'] > at or row['attempts'] >= 3:
            return dict(job=None)
        current = c.execute('SELECT version FROM source_versions WHERE source_key=? AND current=1', (payload.get('source_key'),)).fetchone()
        if payload.get('state') != 'source-ready' or not current or current[0] != payload.get('source_version'):
            raise ValueError('Stage capture is not ready/current; refresh scoped stage_tasks')
        until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        c.execute("UPDATE lifecycle_jobs SET state='running',owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?", (owner, until, at, job_id))
    return dict(job=dict(row, state='running', owner=owner, lease_until=until, attempts=row['attempts']+1))


def tasks(lifecycle, scope, limit=20, offset=0):
    backend = lifecycle.backend
    scope = backend.scope(scope)
    if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 50 or offset < 0:
        raise ValueError('Bounded stage pagination required')
    with backend.connect() as c:
        c.executescript(SCHEMA)
        rows = list(c.execute("SELECT * FROM lifecycle_jobs WHERE kind='stage' AND state IN ('pending','retry','running') ORDER BY created_at,id"))
        selected = []
        for row in rows:
            payload = json.loads(row['payload'])
            entry = payload.get('entry_path', '')
            if not backend.in_scope(entry, scope):
                continue
            sid = payload.get('session_id')
            if not isinstance(sid, str) or not sid:
                continue
            # Metadata lookup only: archived and pending use the same lifecycle.
            current = c.execute("SELECT source_key,version,result,state,path FROM source_versions WHERE current=1 AND source_key IN (?,?) ORDER BY CASE state WHEN 'pending' THEN 0 ELSE 1 END LIMIT 1", ('pending:' + sid, 'archive:' + sid)).fetchone()
            if row['state'] != 'running':
                payload.update(source_key=current['source_key'] if current else 'pending:' + sid,
                               source_version=current['version'] if current else None,
                               state='source-ready' if current else 'awaiting-capture')
                c.execute('UPDATE lifecycle_jobs SET payload=?,updated_at=? WHERE id=?',
                          (json.dumps(payload, ensure_ascii=False), stamp(), row['id']))
            selected.append(dict(job_id=row['id'], state=row['state'], attempts=row['attempts'],
                                 entry_path=entry, session_id=sid, turn_id=payload.get('turn_id'),
                                 source_key=payload.get('source_key'), source_version=payload.get('source_version'),
                                 source_state=current['state'] if current else None,
                                 readiness=payload.get('state'), disposition=current['result'] if current else None))
    return dict(jobs=selected[offset:offset + limit], total=len(selected),
                next_offset=offset + limit if offset + limit < len(selected) else None,
                source_policy='Source metadata remains data; pending capture is not completed synthesis.')


def _verified_dependencies(backend, deps):
    receipts = set()
    state = journal_state.load(backend.state_path)
    for dep in deps:
        if dep['status'] != 'current' or not dep['receipt']:
            raise ValueError('Stage knowledge still requires writer/index verification')
        path = Path(dep['receipt'])
        if path.parent.resolve() != (backend.state_path.parent / 'receipts').resolve():
            raise ValueError('Stage receipt is outside the shared writer')
        raw = path.read_bytes()
        receipt = json.loads(raw)
        if receipt.get('version') != 1 or receipt.get('state_path') != str(journal_state.plain_path(backend.state_path)) or receipt.get('root') != str(journal_state.plain_path(backend.vault)):
            raise ValueError('Stage receipt belongs to another writer or vault')
        recorded = state['receipts'].get(receipt['batch_id'])
        if not recorded or recorded['sha256'] != sha(raw):
            raise ValueError('Stage receipt metadata is not verified')
        checks = [check for note in receipt['notes'] if note['path'] == dep['path'] for check in note['checks']]
        heading = json.loads(dep['heading'])
        if not any(check['section'] == heading and check['text_sha256'] == sha(dep['claim']) for check in checks):
            raise ValueError('Stage claim is absent from precise receipt checks')
        target = backend.path(dep['path'])
        note_raw = target.read_bytes()
        if dep['claim'] not in journal_writer.section_text(note_raw.decode('utf-8-sig'), heading):
            raise ValueError('Stage claim no longer exists in the current section')
        with backend.connect() as c:
            indexed = c.execute('SELECT sha FROM notes WHERE path=?', (dep['path'],)).fetchone()
        if not indexed or indexed[0] != sha(note_raw):
            raise ValueError('Stage target current bytes are not indexed')
        receipts.add(receipt['batch_id'])
    return sorted(receipts)


def decide(lifecycle, job_id, owner, source_key, version, reason):
    backend = lifecycle.backend
    if not isinstance(reason, str) or len(reason.strip()) < 8:
        raise ValueError('Concrete stage judgment required')
    request_sha = sha(json.dumps(dict(job_id=job_id, source_key=source_key, version=version, reason=reason),
                                 ensure_ascii=False, sort_keys=True))
    with backend.connect() as c:
        c.executescript(SCHEMA)
        prior = c.execute('SELECT * FROM stage_decisions WHERE job_id=?', (job_id,)).fetchone()
        if prior:
            if prior['request_sha'] != request_sha:
                raise ValueError('Stage decision already has different content')
            return dict(job_id=job_id, disposition=prior['disposition'], idempotent=True)
        row = c.execute('SELECT * FROM lifecycle_jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['kind'] != 'stage' or row['state'] != 'running' or row['owner'] != owner or row['lease_until'] < stamp():
            raise ValueError('Current stage lease required')
        payload = json.loads(row['payload'])
        sid = payload.get('session_id')
        if source_key not in {'pending:' + str(sid), 'archive:' + str(sid)} or payload.get('source_key') != source_key or payload.get('source_version') != version:
            raise ValueError('Stage source is not the bound capture version')
        source = c.execute('SELECT * FROM source_versions WHERE source_key=? AND version=? AND current=1', (source_key, version)).fetchone()
        assessment = c.execute('SELECT * FROM synthesis_results WHERE source_key=? AND version=?', (source_key, version)).fetchone()
        deps = [dict(r) for r in c.execute('SELECT * FROM knowledge_dependencies WHERE source_key=? AND source_version=?', (source_key, version))]
    lifecycle._source(source_key, version)
    if not source or not assessment or source['result'] in {None, 'write-pending'} or source['result'] != assessment['disposition'] or not lifecycle.coverage(source_key, version)['complete']:
        raise ValueError('Stage needs complete source reading and final assessed outcome')
    wanted = {(x['path'], json.dumps(x['heading'], ensure_ascii=False), x['text']) for x in json.loads(assessment['claims'])}
    current_deps = [x for x in deps if (x['path'], x['heading'], x['claim']) in wanted]
    if len(current_deps) != len(wanted):
        raise ValueError('Stage assessment does not bind all written claims')
    receipts = _verified_dependencies(backend, current_deps)
    with backend.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        live = c.execute('SELECT state,owner,lease_until FROM lifecycle_jobs WHERE id=?', (job_id,)).fetchone()
        current = c.execute('SELECT result FROM source_versions WHERE source_key=? AND version=? AND current=1', (source_key, version)).fetchone()
        current_assessment = c.execute('SELECT claims,disposition FROM synthesis_results WHERE source_key=? AND version=?', (source_key, version)).fetchone()
        if not live or live['state'] != 'running' or live['owner'] != owner or live['lease_until'] < stamp() or not current or current[0] != source['result'] or not current_assessment or current_assessment['claims'] != assessment['claims'] or current_assessment['disposition'] != assessment['disposition']:
            raise ValueError('Stage/source changed during judgment')
        c.execute('INSERT INTO stage_decisions VALUES(?,?,?,?,?,?,?,?)',
                  (job_id, request_sha, source_key, version, source['result'], reason, json.dumps(receipts), stamp()))
        c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?", (stamp(), job_id))
    return dict(job_id=job_id, disposition=source['result'], receipt_batches=receipts, idempotent=False)
