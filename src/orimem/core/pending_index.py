"""Derived, opt-in retrieval for staged messages; no Markdown export or synthesis."""

import json
import uuid
import numpy as np

from .backend import digest, now
from .markdown import chunks, lexical
from .task_context import resolve_task_context


SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_index_sessions(
 session_id TEXT PRIMARY KEY, snapshot_sha TEXT NOT NULL, version TEXT NOT NULL,
 entry_path TEXT NOT NULL, entry_sha TEXT NOT NULL, model TEXT NOT NULL, archive_sha TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pending_chunks(
 id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES pending_index_sessions(session_id) ON DELETE CASCADE,
 event_index INTEGER NOT NULL,ts TEXT,role TEXT NOT NULL,heading TEXT NOT NULL,
 text TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,vector_key TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pending_chunks_session ON pending_chunks(session_id);
CREATE VIRTUAL TABLE IF NOT EXISTS pending_fts USING fts5(id UNINDEXED,path UNINDEXED,tokens,tokenize='unicode61');
"""


def initialize(backend):
    with backend.connect() as c:
        c.executescript(SCHEMA)
        if 'archive_sha' not in {r['name'] for r in c.execute('PRAGMA table_info(pending_index_sessions)')}:
            c.execute("ALTER TABLE pending_index_sessions ADD COLUMN archive_sha TEXT NOT NULL DEFAULT ''")


def binding(backend, cwd):
    # Only workspace binding is evidence of ownership; a title/keyword match
    # must never place a different project's messages into an allowed scope.
    result = resolve_task_context(backend.db, backend.vault, cwd=cwd or '',
                                  task_query='', authorized_scope=__import__('orimem.config', fromlist=['x']).project_scopes())
    if result['status'] != 'matched' or result['version']['freshness'] != 'current':
        return None
    return result['entry']['path'], result['version']['current_sha256']


def snapshot(c, sid):
    session = c.execute('SELECT * FROM pending_sessions WHERE session_id=?', (sid,)).fetchone()
    if session is None:
        raise ValueError('Pending source is no longer available')
    messages = list(c.execute('SELECT event_index,ts,role,content FROM pending_messages WHERE session_id=? ORDER BY event_index', (sid,)))
    text = '# 暂存佐证 '+sid+'\n\n'+'\n\n'.join(
        '## 事件 '+str(r['event_index'])+' | '+str(r['ts'])+' | '+str(r['role'])+'\n\n'+r['content'] for r in messages)
    return session, messages, digest(text)


def _delete(c, sid):
    c.execute('DELETE FROM pending_fts WHERE id IN (SELECT id FROM pending_chunks WHERE session_id=?)', (sid,))
    c.execute('DELETE FROM pending_index_sessions WHERE session_id=?', (sid,))


def sync(backend, limit=8):
    """Durable finite retries, atomic publication, bounded sessions per pass."""
    model = backend.embedder.model_id
    with backend.connect() as c:
        sessions = [dict(r) for r in c.execute('SELECT * FROM pending_sessions ORDER BY session_id')]
        indexed = {r['session_id']: dict(r) for r in c.execute('SELECT * FROM pending_index_sessions')}
        archives = {r['session_id']: r['source_sha'] or '' for r in c.execute('SELECT session_id,source_sha FROM history_sessions')}
    desired = {}
    for session in sessions:
        bound = binding(backend, session['cwd'])
        if bound is None:
            continue
        path, sha = bound
        archive_sha = archives.get(session['session_id'], '')
        version = digest(json.dumps([session['source_sha'], path, sha, model, archive_sha]))
        desired[session['session_id']] = version
        job = backend.lifecycle.enqueue('pending-index', session['session_id'], version,
                                      dict(snapshot_sha=session['source_sha'], entry_path=path, entry_sha=sha, model=model, archive_sha=archive_sha))
        old = indexed.get(session['session_id'])
        if old is None or (old['snapshot_sha'], old['entry_path'], old['entry_sha'], old['model'], old['archive_sha']) != (session['source_sha'], path, sha, model, archive_sha):
            with backend.connect() as c:
                c.execute("UPDATE lifecycle_jobs SET state='pending',attempts=0,error=NULL,available_at=?,updated_at=? WHERE id=? AND state IN ('complete','superseded')", (now(), now(), job))
    deleted = 0
    with backend.connect() as c:
        for sid in set(indexed) - desired.keys():
            _delete(c, sid)
            deleted += 1
        for job in list(c.execute("SELECT id,subject,version FROM lifecycle_jobs WHERE kind='pending-index' AND state IN ('pending','retry','running','failed')")):
            if desired.get(job['subject']) != job['version']:
                c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='Pending source or binding changed',updated_at=? WHERE id=?", (now(), job['id']))
    embedded = changed = 0
    errors = []
    owner = 'orion-pending-index-' + uuid.uuid4().hex
    for _ in range(limit):
        lease = backend.lifecycle.claim('pending-index', owner, 1800)['job']
        if lease is None:
            break
        try:
            payload = json.loads(lease['payload'])
            sid = lease['subject']
            with backend.connect() as c:
                c.execute('BEGIN')
                session, messages, version = snapshot(c, sid)
                archived = {}
                for message in c.execute('SELECT * FROM history_messages WHERE session_id=?', (sid,)):
                    content = message['content']
                    if message['duplicate_of']:
                        other, event = message['duplicate_of'].rsplit(':', 1)
                        prior = c.execute('SELECT content FROM history_messages WHERE session_id=? AND event_index=?', (other, int(event))).fetchone()
                        content = prior[0] if prior else content
                    archived[message['event_index']] = (message['ts'], message['role'], content)
                delta = [m for m in messages if archived.get(m['event_index']) != (m['ts'], m['role'], m['content'])]
            if session['source_sha'] != payload['snapshot_sha'] or binding(backend, session['cwd']) != (payload['entry_path'], payload['entry_sha']):
                raise ValueError('Pending source or binding changed before indexing')
            rows, passages = [], {}
            for message in delta:
                # Prefix prevents message frontmatter from being consumed as
                # metadata. Stored offsets address the original message only.
                prefix = '# 暂存消息\n\n'
                _, spans = chunks(prefix + message['content'], backend.embedder.tokenizer)
                for span in spans:
                    start, end = span['start'] - len(prefix), span['end'] - len(prefix)
                    context = span['heading'] + '\n' + span['text']
                    key = digest(context)
                    ident = 'pending-' + digest('|'.join(map(str, [sid, version, model, payload['entry_path'], payload['entry_sha'], message['event_index'], start, end])))
                    rows.append((ident, sid, message['event_index'], message['ts'], message['role'], span['heading'], span['text'], start, end, key))
                    passages[key] = context
            with backend.connect() as c:
                missing = {k: v for k, v in passages.items() if c.execute('SELECT 1 FROM vectors WHERE key=? AND model=?', (k, model)).fetchone() is None}
            keys = list(missing)
            vectors = backend.embedder.encode([missing[k] for k in keys]) if keys else []
            # BEGIN IMMEDIATE and second snapshot check prevent concurrent
            # capture from publishing stale rows after expensive encoding.
            with backend.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                current_lease = c.execute('SELECT * FROM lifecycle_jobs WHERE id=?', (lease['id'],)).fetchone()
                if current_lease['state'] != 'running' or current_lease['owner'] != owner or current_lease['lease_until'] < now():
                    raise ValueError('Pending index lease is no longer current')
                current, _, current_version = snapshot(c, sid)
                current_archive = c.execute('SELECT source_sha FROM history_sessions WHERE session_id=?', (sid,)).fetchone()
                if current['source_sha'] != payload['snapshot_sha'] or current_version != version or (current_archive[0] or '' if current_archive else '') != payload['archive_sha'] or binding(backend, current['cwd']) != (payload['entry_path'], payload['entry_sha']):
                    raise ValueError('Pending source changed during indexing')
                c.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?)', [(k, model, np.asarray(v, dtype='<f4').tobytes()) for k, v in zip(keys, vectors)])
                _delete(c, sid)
                c.execute('INSERT INTO pending_index_sessions VALUES(?,?,?,?,?,?,?)', (sid, payload['snapshot_sha'], version, payload['entry_path'], payload['entry_sha'], model, payload['archive_sha']))
                c.executemany('INSERT INTO pending_chunks VALUES(?,?,?,?,?,?,?,?,?,?)', rows)
                c.executemany('INSERT INTO pending_fts VALUES(?,?,?)', [(r[0], payload['entry_path'], lexical(r[5]+' '+r[6])) for r in rows])
                c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=? AND owner=?", (now(), lease['id'], lease['owner']))
            changed += 1
            embedded += len(keys)
        except Exception as error:
            try:
                backend.lifecycle.finish_job(lease['id'], lease['owner'], error=str(error))
            except ValueError:
                # A reclaimed/expired lease is handled by the persistent queue;
                # its former worker cannot change the new owner's task state.
                pass
            errors.append(str(error))
    with backend.connect() as c:
        outstanding = c.execute("SELECT count(*) FROM lifecycle_jobs WHERE kind='pending-index' AND state IN ('pending','retry','running','failed')").fetchone()[0]
        failures = [dict(r) for r in c.execute("SELECT id,subject,state,attempts,error FROM lifecycle_jobs WHERE kind='pending-index' AND state IN ('retry','failed')")]
        result = dict(changed=changed, deleted=deleted, embedded=embedded, outstanding=outstanding,
                      unbound=len(sessions)-len(desired), errors=errors, failures=failures,
                      state='error' if errors or failures else ('pending' if outstanding else 'complete'), vault_writes=0, at=now())
        c.execute("INSERT OR REPLACE INTO meta VALUES('last_pending_index',?)", (json.dumps(result),))
    if changed or deleted:
        backend._clear_recall_caches()
    backend.lifecycle.health_stage('pending-index', (errors[0] if errors else failures[0]['error']) if errors or failures else None)
    return result


def candidates(backend, c, scope):
    where, values = backend._scope_predicate('s.entry_path', scope)
    return [dict(r) for r in c.execute(
        'SELECT ch.*,s.entry_path AS path,s.entry_sha AS sha,p.title,\'pending\' AS kind,'
        's.snapshot_sha,s.version AS source_version,s.model,p.cwd,ch.start AS line_start '
        'FROM pending_chunks ch JOIN pending_index_sessions s ON s.session_id=ch.session_id '
        'JOIN pending_sessions p ON p.session_id=s.session_id AND p.source_sha=s.snapshot_sha '
        'LEFT JOIN history_sessions h ON h.session_id=s.session_id '
        'JOIN notes n ON n.path=s.entry_path AND n.sha=s.entry_sha '
        "WHERE "+where+" AND s.model=? AND COALESCE(h.source_sha,'')=s.archive_sha ORDER BY ch.rowid", [*values, backend.embedder.model_id])]


def fresh(backend, rows):
    bindings = {}
    return_rows = []
    for row in rows:
        if row['cwd'] not in bindings:
            bindings[row['cwd']] = binding(backend, row['cwd'])
        if bindings[row['cwd']] == (row['path'], row['sha']):
            return_rows.append(row)
    return return_rows


def evidence(backend, c, ident, scope, remaining):
    row = c.execute(
        'SELECT ch.*,s.entry_path,s.entry_sha,s.version,s.snapshot_sha,s.model,s.archive_sha,p.cwd '
        'FROM pending_chunks ch JOIN pending_index_sessions s ON s.session_id=ch.session_id '
        'JOIN pending_sessions p ON p.session_id=s.session_id WHERE ch.id=?', (ident,)).fetchone()
    if row is None or not backend.in_scope(row['entry_path'], scope):
        raise ValueError('Unknown or out-of-scope pending evidence')
    _, messages, version = snapshot(c, row['session_id'])
    archive = c.execute('SELECT source_sha FROM history_sessions WHERE session_id=?', (row['session_id'],)).fetchone()
    current = next((m for m in messages if m['event_index'] == row['event_index']), None)
    if (row['model'] != backend.embedder.model_id or version != row['version']
        or (archive[0] or '' if archive else '') != row['archive_sha']
        or binding(backend, row['cwd']) != (row['entry_path'], row['entry_sha'])
        or current is None or current['content'][row['start']:row['end']] != row['text']):
        raise ValueError('Stale pending evidence; sync and recall again')
    take = min(remaining, len(row['text']))
    return dict(evidence_id=ident, path=row['entry_path'], heading=row['heading'],
                source_key='pending:'+row['session_id'], source_sha256=version,
                event_index=row['event_index'], event_time=row['ts'], role=row['role'],
                start=row['start'], end=row['end'], text=row['text'][:take],
                truncated=take < len(row['text']), kind='pending', source_state='pending',
                temporary=True, notice='暂存消息尚未归纳；助手报告不代表已验证成果。')
