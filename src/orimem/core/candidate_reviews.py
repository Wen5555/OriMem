"""Small durable candidate watches; lexical triggers request judgment, not truth."""
import json
from orimem import journal_writer
from .lifecycle import sha, stamp

def register(lifecycle, source_key, version, candidate_path, topics, scope, reason, include_pending=False):
    b=lifecycle.backend
    lifecycle._source(source_key,version)
    current=lifecycle.assessment(source_key,version)['assessment']
    if current['disposition']!='candidate' or not lifecycle.coverage(source_key,version)['complete']:
        raise ValueError('Only a fully read current candidate assessment can be watched')
    b.path(candidate_path)
    if not isinstance(topics,list) or not 1<=len(topics)<=8 or any(not isinstance(t,str) or not 2<=len(t.strip())<=100 for t in topics):
        raise ValueError('Bounded explicit topic terms required')
    if not isinstance(scope,list) or not 1<=len(scope)<=12 or type(include_pending) is not bool:
        raise ValueError('Explicit bounded scopes and pending opt-in required')
    if not isinstance(reason,str) or len(reason.strip())<8:
        raise ValueError('Concrete re-evaluation reason required')
    normalized=[]
    for prefix in scope:
        if not isinstance(prefix,str) or not prefix.strip() or prefix!=prefix.replace('\\','/').strip('/') or any(p in {'.','..'} for p in prefix.split('/')):
            raise ValueError('Scope must be a visible relative vault path')
        b.path(prefix if prefix.endswith('.md') else prefix+'/scope-check.md')
        normalized.append(prefix)
    with b.connect() as c:
        verified=c.execute("SELECT * FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND path=? AND status='current' AND receipt IS NOT NULL",(source_key,version,candidate_path)).fetchone()
        indexed=c.execute('SELECT sha FROM notes WHERE path=?',(candidate_path,)).fetchone()
    raw=b.path(candidate_path).read_bytes()
    if not verified or not indexed or indexed[0]!=sha(raw) or verified['claim'] not in journal_writer.section_text(raw.decode('utf-8-sig'),json.loads(verified['heading'])):
        raise ValueError('Candidate must already have a current writer/index dependency')
    topics=sorted(set(t.strip().casefold() for t in topics))
    config=dict(source_key=source_key,version=version,candidate_path=candidate_path,topics=topics,scope=normalized,include_pending=include_pending,reason=reason)
    watch_id=sha(json.dumps(config,ensure_ascii=False,sort_keys=True))
    with b.connect() as c:
        c.execute("UPDATE candidate_watches SET state='superseded' WHERE source_key=? AND source_version=? AND candidate_path=? AND id<>?",(source_key,version,candidate_path,watch_id))
        c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='Candidate watch configuration changed',updated_at=? WHERE kind='candidate' AND state IN ('pending','retry','running') AND subject IN (SELECT id FROM candidate_watches WHERE state='superseded')",(stamp(),))
        c.execute('INSERT OR IGNORE INTO candidate_watches VALUES(?,?,?,?,?,?,?,?,?,?,?)',(watch_id,source_key,version,candidate_path,json.dumps(topics,ensure_ascii=False),json.dumps(normalized,ensure_ascii=False),int(include_pending),reason,'active',stamp(),stamp()))
    return dict(watch_id=watch_id,**notify(lifecycle))

def _like(term):
    return '%'+term.replace('!','!!').replace('%','!%').replace('_','!_')+'%'

def notify(lifecycle):
    b=lifecycle.backend
    with b.connect() as c:
        c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='Candidate evidence version changed or missing',updated_at=? WHERE kind='candidate' AND state IN ('pending','retry','running') AND NOT EXISTS (SELECT 1 FROM source_versions s WHERE s.source_key=json_extract(lifecycle_jobs.payload,'$.evidence_source_key') AND s.version=json_extract(lifecycle_jobs.payload,'$.evidence_version') AND s.current=1)",(stamp(),))
        watches=[dict(r) for r in c.execute("SELECT * FROM candidate_watches WHERE state='active'")]
        sources=[dict(r) for r in c.execute("SELECT source_key,version,path,state FROM source_versions WHERE current=1")]
    added=queued=0
    for watch in watches:
        with b.connect() as c:
            candidate=c.execute("SELECT s.current,s.result,r.disposition FROM source_versions s LEFT JOIN synthesis_results r ON r.source_key=s.source_key AND r.version=s.version WHERE s.source_key=? AND s.version=?",(watch['source_key'],watch['source_version'])).fetchone()
        if not candidate or not candidate['current'] or candidate['result']!='candidate' or candidate['disposition']!='candidate':
            # Closing a watch is a metadata consequence of a reviewed result,
            # not a promotion or a promise that queued evidence was processed.
            with b.connect() as c:
                c.execute("UPDATE candidate_watches SET state='inactive',updated_at=? WHERE id=?",(stamp(),watch['id']))
                c.execute("UPDATE lifecycle_jobs SET state='superseded',error='Candidate no longer awaits this review',owner=NULL,lease_until=NULL,updated_at=? WHERE kind='candidate' AND subject=? AND state IN ('pending','retry','running')",(stamp(),watch['id']))
            continue
        topics=json.loads(watch['topics'])
        scope=json.loads(watch['scope'])
        with b.connect() as c:
            prior_signals={(r['source_key'],r['source_version']) for r in c.execute('SELECT source_key,source_version FROM candidate_signals WHERE watch_id=?',(watch['id'],))}
        for source in sources:
            if source['source_key']==watch['source_key']:
                continue
            if (source['source_key'],source['version']) in prior_signals:
                continue
            if source['state']=='pending':
                if not watch['include_pending']:
                    continue
            elif source['state']=='archived':
                if not any(source['path']==p or source['path'].startswith(p+'/') for p in scope):
                    continue
                with b.connect() as c:
                    indexed=c.execute('SELECT sha FROM notes WHERE path=?',(source['path'],)).fetchone()
                if not indexed or indexed[0]!=source['version']:
                    continue
            else:
                continue
            matched=[]
            with b.connect() as c:
                for term in topics:
                    if source['state']=='pending':
                        found=c.execute("SELECT 1 FROM pending_messages WHERE session_id=? AND lower(content) LIKE ? ESCAPE '!' LIMIT 1",(source['source_key'].removeprefix('pending:'),_like(term))).fetchone()
                    else:
                        found=c.execute("SELECT 1 FROM chunks WHERE path=? AND lower(text) LIKE ? ESCAPE '!' LIMIT 1",(source['path'],_like(term))).fetchone()
                    if found:
                        matched.append(term)
            if matched:
                signature=sha(source['source_key']+'|'+source['version'])
                lifecycle.enqueue('candidate',watch['id'],signature,dict(watch_id=watch['id'],candidate_path=watch['candidate_path'],candidate_source_key=watch['source_key'],candidate_source_version=watch['source_version'],evidence_source_key=source['source_key'],evidence_version=source['version'],matched_topics=matched,reason='Related source changed; read and judge evidence before any promotion'))
                queued+=1
            # Queue before acknowledging a signal: a crash cannot lose work.
            # Nonmatches are pinned too; a changed source gets a new version.
            with b.connect() as c:
                c.execute('INSERT OR IGNORE INTO candidate_signals VALUES(?,?,?,?,?,?)',(watch['id'],source['source_key'],source['version'],source['state'],json.dumps(matched,ensure_ascii=False),stamp()))
            added+=bool(matched)
    return dict(candidate_signals_added=added,candidate_jobs_enqueued=queued)

def tasks(lifecycle,limit=20,offset=0):
    if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or offset<0:
        raise ValueError('Invalid candidate task page')
    with lifecycle.backend.connect() as c:
        total=c.execute("SELECT count(*) FROM lifecycle_jobs WHERE kind='candidate' AND state IN ('pending','retry','running')").fetchone()[0]
        rows=[dict(r) for r in c.execute("SELECT * FROM lifecycle_jobs WHERE kind='candidate' AND state IN ('pending','retry','running') ORDER BY created_at,id LIMIT ? OFFSET ?",(limit,offset))]
    for r in rows:
        r['payload']=json.loads(r['payload'])
    return dict(tasks=rows,total=total,next_offset=offset+len(rows) if offset+len(rows)<total else None)

def decide(lifecycle,job_id,owner,decision,reason,evidence_source_key,evidence_version,revision_id=None):
    b=lifecycle.backend
    if decision not in {'keep-candidate','promoted'} or not isinstance(reason,str) or len(reason.strip())<8:
        raise ValueError('Concrete candidate judgment required')
    request=dict(job_id=job_id,owner=owner,decision=decision,reason=reason,evidence_source_key=evidence_source_key,evidence_version=evidence_version,revision_id=revision_id)
    request_sha=sha(json.dumps(request,ensure_ascii=False,sort_keys=True))
    with b.connect() as c:
        done=c.execute('SELECT * FROM candidate_decisions WHERE job_id=?',(job_id,)).fetchone()
        job=c.execute("SELECT * FROM lifecycle_jobs WHERE id=? AND kind='candidate'",(job_id,)).fetchone()
    if done:
        if done['request_sha']!=request_sha:
            raise ValueError('Completed candidate judgment cannot change')
        return dict(job_id=job_id,decision=decision,idempotent=True)
    if not job or job['state']!='running' or job['owner']!=owner or job['lease_until']<stamp():
        raise ValueError('Current candidate job lease required')
    payload=json.loads(job['payload'])
    if evidence_source_key!=payload['evidence_source_key'] or evidence_version!=payload['evidence_version']:
        raise ValueError('Judgment must review the exact triggered evidence version')
    lifecycle._source(evidence_source_key,evidence_version)
    if not lifecycle.coverage(evidence_source_key,evidence_version)['complete']:
        raise ValueError('New candidate evidence must actually be fully read')
    candidate=lifecycle.assessment(payload['candidate_source_key'],payload['candidate_source_version'])['assessment']
    if decision=='promoted':
        with b.connect() as c:
            resolved=c.execute('SELECT * FROM assessment_resolutions WHERE id=?',(revision_id,)).fetchone()
        if not resolved or resolved['source_key']!=payload['candidate_source_key'] or resolved['version']!=payload['candidate_source_version'] or resolved['disposition'] not in {'formal-knowledge','merged'} or candidate['disposition']!=resolved['disposition']:
            raise ValueError('Promotion requires the candidate actual receipt-backed assessment revision')
    elif candidate['disposition']!='candidate':
        raise ValueError('Candidate state changed; reconcile the job')
    with b.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        live=c.execute('SELECT state,owner,lease_until FROM lifecycle_jobs WHERE id=?',(job_id,)).fetchone()
        if live['state']!='running' or live['owner']!=owner or live['lease_until']<stamp():
            raise ValueError('Candidate lease changed during judgment')
        lifecycle._source(evidence_source_key,evidence_version)
        current=c.execute('SELECT disposition FROM synthesis_results WHERE source_key=? AND version=?',(payload['candidate_source_key'],payload['candidate_source_version'])).fetchone()
        if not current or current[0]!=candidate['disposition']:
            raise ValueError('Candidate assessment changed during judgment')
        c.execute('INSERT INTO candidate_decisions VALUES(?,?,?,?,?,?,?)',(job_id,request_sha,decision,reason,json.dumps(request,ensure_ascii=False),revision_id,stamp()))
        c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?",(stamp(),job_id))
        if decision=='promoted':
            c.execute("UPDATE candidate_watches SET state='promoted',updated_at=? WHERE id=?",(stamp(),job['subject']))
    return dict(job_id=job_id,decision=decision,idempotent=False)

