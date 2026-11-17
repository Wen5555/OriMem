"""Deterministic section-level knowledge representations; candidate discovery only."""
import json,uuid,re
import numpy as np
from .backend import digest,now
from .retrieval_policy import Policy

PIPELINE='knowledge-sections-v4'
SCHEMA='''
CREATE TABLE IF NOT EXISTS knowledge_units(
 id TEXT PRIMARY KEY,path TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
 heading TEXT NOT NULL,source_sha TEXT NOT NULL,input TEXT NOT NULL,vector_key TEXT NOT NULL,
 model TEXT NOT NULL,pipeline TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS knowledge_units_path ON knowledge_units(path);
CREATE TABLE IF NOT EXISTS knowledge_index_notes(
 path TEXT PRIMARY KEY REFERENCES notes(path) ON DELETE CASCADE,source_sha TEXT NOT NULL,
 model TEXT NOT NULL,pipeline TEXT NOT NULL,units INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_enrollment(path TEXT PRIMARY KEY,enrolled_at TEXT NOT NULL);
'''
EXCLUDE={'来源','来源与产物','主张来源定位','相关方法','相关入口','阅读路径','返回导航','内容归位'}

def noise_ranges(original):
    """Mark original line intervals BEFORE consulting overlapping child chunks.

    A child can start halfway through a source/hash line; inspecting only its
    visible prefix would accidentally encode the tail as topic semantics.
    """
    ranges=[]
    from orimem import config
    lay=config.layout(); archive='[['+lay['archive']
    prefixes=('- '+archive,'来源：'+archive,'Source: '+archive,'来源定位：','[['+lay['knowledge_index'],'- 基线结果：','- 方法与论文区别：','- 测试分支审查依据：')
    for line in re.finditer(r'[^\n]+(?:\n|$)',original):
        text=line.group(0)
        if text.lstrip().startswith(prefixes):ranges.append((line.start(),line.end()))
        elif '来源：'+archive in text or 'Source: '+archive in text:
            at=min(text.find(m) for m in ('来源：'+archive,'Source: '+archive) if m in text)
            ranges.append((line.start()+at,line.end()))
    return ranges

def without_noise(original,start,end,ranges):
    at=start;parts=[]
    for left,right in ranges:
        if right<=at or left>=end:continue
        if left>at:parts.append(original[at:min(left,end)])
        at=max(at,min(right,end))
    if at<end:parts.append(original[at:end])
    return ''.join(parts)

def initialize(backend):
    with backend.connect() as c:
        c.executescript(SCHEMA)
        if 'input_sha' not in {r['name'] for r in c.execute('PRAGMA table_info(knowledge_index_notes)')}:
            c.execute("ALTER TABLE knowledge_index_notes ADD COLUMN input_sha TEXT NOT NULL DEFAULT ''")

def inputs(backend,c,path):
    note=c.execute('SELECT * FROM notes WHERE path=?',(path,)).fetchone()
    if not note or note['kind'] not in {'concept','knowledge'}:return None,[]
    raw_note=backend.path(path).read_bytes()
    if digest(raw_note)!=note['sha']:raise ValueError('Knowledge source changed; sync first')
    original=raw_note.decode('utf-8-sig').replace('\r\n','\n');noise=noise_ranges(original)
    policy=Policy(backend,c,'current');groups={}
    for raw in c.execute('SELECT ch.*,n.sha,n.title,n.kind FROM chunks ch JOIN notes n ON n.path=ch.path WHERE ch.path=? ORDER BY ch.start',(path,)):
        leaf=raw['heading'].split(' / ')[-1]
        if leaf in EXCLUDE:continue
        for view in policy.views(dict(raw)):
            # Source-link lines and hash boilerplate are not topic semantics.
            text=without_noise(original,view['start'],view['end'],noise)
            if text.strip():groups.setdefault(view['heading'],[]).append(text)
    rows=[]
    for heading,parts in groups.items():
        # Deterministic fields only. No LLM summary or inferred project owner.
        passage=note['title']+'\n'+heading+'\n'+'\n'.join(parts)
        tokens=backend.embedder.tokenizer(passage,add_special_tokens=False,truncation=True,max_length=384)['input_ids']
        encoded=backend.embedder.tokenizer.decode(tokens)
        rows.append(dict(heading=heading,input=encoded,vector_key=digest(encoded)))
    return dict(note),rows

def sync(backend,scope,limit=12):
    # Reuse the backend's reentrant in-process guard. Watcher reconciliation
    # and a native MCP request must not replace each other's derived rows.
    with backend.lock:
        return _sync(backend,scope,limit)

def _sync(backend,scope,limit=12):
    scope=backend.scope(scope)
    if not 1<=limit<=50:raise ValueError('Bounded knowledge batch required')
    model=backend.embedder.model_id
    with backend.connect() as c:
        where,args=backend._scope_predicate('path',scope)
        notes=[dict(r) for r in c.execute("SELECT path,sha FROM notes WHERE kind IN ('concept','knowledge') AND "+where,args)]
    for note in notes:
        with backend.connect() as c:_,planned=inputs(backend,c,note['path'])
        note['input_sha']=digest(json.dumps([(r['heading'],r['vector_key']) for r in planned]))
        version=digest(json.dumps([note['sha'],model,PIPELINE,note['input_sha']]))
        note['desired_version']=version
        ident=backend.lifecycle.enqueue('knowledge-index',note['path'],version,dict(source_sha=note['sha'],model=model,pipeline=PIPELINE,input_sha=note['input_sha']))
        with backend.connect() as c:
            c.execute('INSERT OR IGNORE INTO knowledge_enrollment VALUES(?,?)',(note['path'],now()))
            old=c.execute('SELECT * FROM knowledge_index_notes WHERE path=?',(note['path'],)).fetchone()
            actual=c.execute('SELECT count(*) FROM knowledge_units WHERE path=?',(note['path'],)).fetchone()[0]
            if not old or (old['source_sha'],old['model'],old['pipeline'],old['units'],old['input_sha'])!=(note['sha'],model,PIPELINE,actual,note['input_sha']):
                c.execute("UPDATE lifecycle_jobs SET state='pending',attempts=0,error=NULL,available_at=?,updated_at=? WHERE id=? AND state IN ('complete','superseded')",(now(),now(),ident))
    with backend.connect() as c:
        desired={n['path']:n['desired_version'] for n in notes}
        where,args=backend._scope_predicate('subject',scope)
        for job in list(c.execute("SELECT id,subject,version FROM lifecycle_jobs WHERE kind='knowledge-index' AND state IN ('pending','retry','running','failed') AND "+where,args)):
            if desired.get(job['subject'])!=job['version']:
                c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='Knowledge input generation changed',updated_at=? WHERE id=?",(now(),job['id']))
    owner='knowledge-index-'+uuid.uuid4().hex;changed=encoded_count=0;errors=[]
    for _ in range(limit):
        job=backend.lifecycle.claim('knowledge-index',owner,1800,scope=scope)['job']
        if job is None:break
        try:
            with backend.connect() as c:
                note,units=inputs(backend,c,job['subject'])
                payload=json.loads(job['payload'])
                if not note or note['sha']!=payload['source_sha']:raise ValueError('Knowledge job version changed')
                if digest(json.dumps([(r['heading'],r['vector_key']) for r in units]))!=payload['input_sha']:raise ValueError('Knowledge correction/temporal policy changed')
                missing={r['vector_key']:r['input'] for r in units if not c.execute('SELECT 1 FROM vectors WHERE key=? AND model=?',(r['vector_key'],model)).fetchone()}
            keys=list(missing);vectors=backend.embedder.encode([missing[k] for k in keys]) if keys else []
            with backend.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                live=c.execute('SELECT state,owner,lease_until FROM lifecycle_jobs WHERE id=?',(job['id'],)).fetchone()
                if live['state']!='running' or live['owner']!=owner or live['lease_until']<now() or digest(backend.path(note['path']).read_bytes())!=note['sha']:raise ValueError('Knowledge source or lease changed during encoding')
                c.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?)',[(k,model,np.asarray(v,dtype='<f4').tobytes()) for k,v in zip(keys,vectors)])
                c.execute('DELETE FROM knowledge_units WHERE path=?',(note['path'],))
                c.executemany('INSERT INTO knowledge_units VALUES(?,?,?,?,?,?,?,?)',[(digest(note['path']+'|'+r['heading']+'|'+r['vector_key']),note['path'],r['heading'],note['sha'],r['input'],r['vector_key'],model,PIPELINE) for r in units])
                _,latest=inputs(backend,c,note['path'])
                if digest(json.dumps([(r['heading'],r['vector_key']) for r in latest]))!=payload['input_sha']:raise ValueError('Knowledge policy changed during encoding')
                c.execute('INSERT OR REPLACE INTO knowledge_index_notes VALUES(?,?,?,?,?,?)',(note['path'],note['sha'],model,PIPELINE,len(units),payload['input_sha']))
                c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?",(now(),job['id']))
            changed+=1;encoded_count+=len(keys)
        except Exception as error:
            try:backend.lifecycle.finish_job(job['id'],owner,error=str(error))
            except ValueError:pass
            errors.append(str(error))
    incomplete=[]
    with backend.connect() as c:
        for note in notes:
            marker=c.execute('SELECT * FROM knowledge_index_notes WHERE path=?',(note['path'],)).fetchone()
            if not marker or (marker['source_sha'],marker['model'],marker['pipeline'],marker['input_sha'])!=(note['sha'],model,PIPELINE,note['input_sha']):
                job=c.execute("SELECT state,error FROM lifecycle_jobs WHERE kind='knowledge-index' AND subject=? AND version=?",(note['path'],note['desired_version'])).fetchone()
                incomplete.append(dict(path=note['path'],state=job['state'] if job else 'missing-task',error=job['error'] if job else None))
    return dict(changed=changed,embedded=encoded_count,errors=errors,complete=not incomplete,incomplete_notes=incomplete,pipeline=PIPELINE,scope=scope,
                notice='Knowledge representations aid candidate discovery; not fact relations, auto merges or project ownership.')

def reconcile(backend,limit=8):
    # Enroll only explicitly selected notes. A file replace/delete can drop
    # derived FK rows, while this bounded authorization persists for recovery.
    with backend.connect() as c:
        paths=[r[0] for r in c.execute('SELECT e.path FROM knowledge_enrollment e JOIN notes n ON n.path=e.path ORDER BY e.path')]
    if not paths:return dict(changed=0,embedded=0,errors=[],enrolled=0)
    # Public scope is deliberately capped at 12 exact paths. Persistent
    # enrollment can exceed it; reconcile in bounded batches without widening
    # to a folder prefix or turning routine sync into an unbounded encoder.
    remaining=limit;result=dict(changed=0,embedded=0,errors=[],enrolled=len(paths),pipeline=PIPELINE)
    for start in range(0,len(paths),12):
        if remaining<=0:break
        batch=sync(backend,paths[start:start+12],limit=remaining)
        result['changed']+=batch['changed'];result['embedded']+=batch['embedded'];result['errors'].extend(batch['errors'])
        remaining-=batch['changed']+len(batch['errors'])
    return result

def neighbors(backend,path,scope,limit=8):
    scope=backend.scope(scope)
    if not backend.in_scope(path,scope):raise ValueError('Origin knowledge is out of scope')
    if not 1<=limit<=12:raise ValueError('Bounded neighbor count required')
    with backend.connect() as c:
        where,args=backend._scope_predicate('u.path',scope)
        units=[dict(r) for r in c.execute('SELECT u.*,v.vector FROM knowledge_units u JOIN vectors v ON v.key=u.vector_key AND v.model=u.model JOIN notes n ON n.path=u.path AND n.sha=u.source_sha WHERE '+where+' AND u.model=? AND u.pipeline=?',[*args,backend.embedder.model_id,PIPELINE])]
    fresh=[]
    checked={}
    for r in units:
        if r['path'] not in checked:
            with backend.connect() as c:
                try:
                    _,current=inputs(backend,c,r['path'])
                    marker=c.execute('SELECT input_sha FROM knowledge_index_notes WHERE path=?',(r['path'],)).fetchone()
                    checked[r['path']]=bool(marker and digest(json.dumps([(u['heading'],u['vector_key']) for u in current]))==marker[0])
                except ValueError:checked[r['path']]=False
        if checked[r['path']] and digest(backend.path(r['path']).read_bytes())==r['source_sha']:fresh.append(r)
    origin=[r for r in fresh if r['path']==path]
    if not origin:raise ValueError('Knowledge representation unavailable; scoped sync required')
    result=[]
    for other in fresh:
        if other['path']==path:continue
        score=max(float(np.dot(np.frombuffer(r['vector'],dtype='<f4'),np.frombuffer(other['vector'],dtype='<f4'))) for r in origin)
        result.append(dict(path=other['path'],heading=other['heading'],source_sha256=other['source_sha'],cosine=round(score,6),relation='semantic_neighbor',state='candidate',requires_review=True))
    result.sort(key=lambda r:r['cosine'],reverse=True)
    return dict(origin=path,neighbors=result[:limit],named_sections=len(origin),
                notice='Similarity is not correctness, duplicate equivalence, a merge instruction or project attribution.')
