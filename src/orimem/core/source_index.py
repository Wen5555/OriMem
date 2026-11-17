"""Canonical message index behind explicitly scoped continuous Markdown views.

One message/version/interval identity, independent of the session projection
hash. Generated Markdown is a view, not another vector corpus.
"""
import json, uuid
import numpy as np
from .backend import digest, now
from .markdown import chunks, lexical
from .continuous import managed

SCHEMA='''
CREATE TABLE IF NOT EXISTS source_index_sessions(session_id TEXT PRIMARY KEY,snapshot_sha TEXT NOT NULL,model TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_chunks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES source_index_sessions(session_id) ON DELETE CASCADE,event_index INTEGER NOT NULL,message_version TEXT NOT NULL,ts TEXT,role TEXT NOT NULL,heading TEXT NOT NULL,text TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,vector_key TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(id UNINDEXED,path UNINDEXED,tokens,tokenize='unicode61');
'''

def initialize(backend):
    with backend.connect() as c: c.executescript(SCHEMA)

def sync(backend,limit=8):
    model=backend.embedder.model_id
    with backend.connect() as c:
        sessions=[dict(r) for r in c.execute("SELECT s.* FROM capture_sessions s WHERE EXISTS(SELECT 1 FROM source_projections p WHERE p.session_id=s.session_id) OR EXISTS(SELECT 1 FROM lifecycle_jobs j WHERE j.kind='projection' AND j.subject=s.session_id AND j.state IN ('pending','retry','running'))")]
    for s in sessions:
        ident=backend.lifecycle.enqueue('source-index',s['session_id'],digest(s['snapshot_sha']+'|'+model),dict(snapshot_sha=s['snapshot_sha'],model=model))
        with backend.connect() as c:
            indexed=c.execute('SELECT * FROM source_index_sessions WHERE session_id=?',(s['session_id'],)).fetchone()
            if not indexed or (indexed['snapshot_sha'],indexed['model'])!=(s['snapshot_sha'],model):
                c.execute("UPDATE lifecycle_jobs SET state='pending',attempts=0,error=NULL,available_at=?,updated_at=? WHERE id=? AND state IN ('complete','superseded')",(now(),now(),ident))
    with backend.connect() as c:
        for job in list(c.execute("SELECT * FROM lifecycle_jobs WHERE kind='source-index' AND state IN ('pending','retry','running','failed')")):
            latest=next((s for s in sessions if s['session_id']==job['subject']),None)
            if not latest or digest(latest['snapshot_sha']+'|'+model)!=job['version']:
                c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(now(),job['id']))
    owner='orion-source-index-'+uuid.uuid4().hex
    embedded=changed=0; errors=[]
    for _ in range(limit):
        job=backend.lifecycle.claim('source-index',owner,1800)['job']
        if job is None: break
        payload=json.loads(job['payload']); sid=job['subject']
        try:
            with backend.connect() as c:
                c.execute('BEGIN')
                session=c.execute('SELECT * FROM capture_sessions WHERE session_id=?',(sid,)).fetchone()
                if not session or session['snapshot_sha']!=payload['snapshot_sha']: raise ValueError('Source changed before indexing')
                snap=c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(sid,session['snapshot_sha'])).fetchone()
                messages=[c.execute('SELECT * FROM capture_versions WHERE session_id=? AND event_index=? AND version=?',(sid,i,v)).fetchone() for i,v in json.loads(snap[0])]
                rows=[]; passages={}
                for m in messages:
                    prefix='# 暂存消息\n\n'
                    _,spans=chunks(prefix+m['content'],backend.embedder.tokenizer)
                    for span in spans:
                        a,b=span['start']-len(prefix),span['end']-len(prefix)
                        context=span['heading']+'\n'+span['text']; key=digest(context)
                        ident='source-'+digest(json.dumps([sid,m['event_index'],m['version'],model,a,b]))
                        rows.append((ident,sid,m['event_index'],m['version'],m['ts'],m['role'],span['heading'],span['text'],a,b,key))
                        if not c.execute('SELECT 1 FROM vectors WHERE key=? AND model=?',(key,model)).fetchone(): passages[key]=context
            keys=list(passages)
            encoded=backend.embedder.encode([passages[k] for k in keys]) if keys else []
            with backend.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                live=c.execute('SELECT * FROM lifecycle_jobs WHERE id=?',(job['id'],)).fetchone()
                current=c.execute('SELECT snapshot_sha FROM capture_sessions WHERE session_id=?',(sid,)).fetchone()
                if live['state']!='running' or live['owner']!=owner or live['lease_until']<now() or current[0]!=payload['snapshot_sha']: raise ValueError('Source or index lease changed during encoding')
                c.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?)',[(k,model,np.asarray(v,dtype='<f4').tobytes()) for k,v in zip(keys,encoded)])
                c.execute('DELETE FROM source_fts WHERE id IN (SELECT id FROM source_chunks WHERE session_id=?)',(sid,))
                c.execute('DELETE FROM source_index_sessions WHERE session_id=?',(sid,))
                c.execute('INSERT INTO source_index_sessions VALUES(?,?,?)',(sid,payload['snapshot_sha'],model))
                c.executemany('INSERT INTO source_chunks VALUES(?,?,?,?,?,?,?,?,?,?,?)',rows)
                c.executemany('INSERT INTO source_fts VALUES(?,?,?)',[(r[0],session['path'],lexical(r[6]+' '+r[7])) for r in rows])
                c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?",(now(),job['id']))
            changed+=1; embedded+=len(keys)
        except Exception as error:
            try: backend.lifecycle.finish_job(job['id'],owner,error=str(error))
            except ValueError: pass
            errors.append(str(error))
    if changed: backend._clear_recall_caches()
    result=dict(changed=changed,embedded=embedded,errors=errors,at=now())
    with backend.connect() as c: c.execute("INSERT OR REPLACE INTO meta VALUES('last_source_index',?)",(json.dumps(result),))
    backend.lifecycle.health_stage('source-index',str(errors) if errors else None)
    return result

def candidates(backend,c,scope):
    where,values=backend._scope_predicate('p.path',scope)
    return [dict(r) for r in c.execute("SELECT ch.*,p.path,p.output_sha AS sha,('持续佐证 ' || s.session_id) AS title,'source' AS kind,ch.start AS line_start,p.managed_sha FROM source_chunks ch JOIN source_index_sessions ix ON ix.session_id=ch.session_id JOIN capture_sessions s ON s.session_id=ix.session_id JOIN capture_snapshots snap ON snap.session_id=s.session_id AND snap.version=s.snapshot_sha JOIN source_projections p ON p.session_id=s.session_id WHERE "+where+" AND ix.model=? AND EXISTS(SELECT 1 FROM json_each(snap.records) r WHERE json_extract(r.value,'$[0]')=ch.event_index AND json_extract(r.value,'$[1]')=ch.message_version)",[*values,backend.embedder.model_id])]

def fresh(backend,rows):
    checked={}; result=[]
    for r in rows:
        if r['path'] not in checked:
            try:
                _,_,body=managed(backend.path(r['path']).read_text(encoding='utf-8'))
                checked[r['path']]=digest(body)==r['managed_sha']
            except (OSError,ValueError): checked[r['path']]=False
        if checked[r['path']]: result.append(r)
    return result

def evidence(backend,c,ident,scope,remaining):
    # SQL authorization before resolving a source version or reading a file.
    permitted={r['id']:r for r in candidates(backend,c,scope)}
    r=permitted.get(ident)
    if r is None: raise ValueError('Unknown or out-of-scope source evidence')
    if not fresh(backend,[r]): raise ValueError('Source projection changed; reconcile before evidence')
    m=c.execute('SELECT * FROM capture_versions WHERE session_id=? AND event_index=? AND version=?',(r['session_id'],r['event_index'],r['message_version'])).fetchone()
    if m['content'][r['start']:r['end']]!=r['text']: raise ValueError('Source interval mismatch')
    return dict(evidence_id=ident,path=r['path'],heading=r['heading'],source_key='message:'+r['session_id']+':'+str(r['event_index']),
                source_sha256=r['message_version'],content_hash=m['content_hash'],event_index=r['event_index'],event_time=r['ts'],role=r['role'],
                start=r['start'],end=r['start']+min(len(r['text']),remaining),chunk_end=r['end'],
                text=r['text'][:remaining],truncated=len(r['text'])>remaining,kind='source',source_state='projected',
                identity_basis=m['identity_basis'],completion=m['completion'],
                upstream_truncated=bool(json.loads(m['metadata']).get('content_filter',{}).get('truncated')),
                contains_truncation_marker='[TRUNCATED' in m['content'],notice='原文保存与事实核验、知识提炼分开。')
