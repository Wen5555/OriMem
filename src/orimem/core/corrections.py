"""Explicit evidence-backed corrections in the existing lifecycle ledger.

Registration blocks affected assertions before body/index rebuilding. Semantic
decisions are supplied by the caller, never inferred from vector similarity.
"""
from datetime import datetime,timezone
import json,re
from orimem import journal_writer, journal_state
from .lifecycle import sha,stamp
from .history import evidence_roots
from orimem import config as _config

SCHEMA='''
CREATE TABLE IF NOT EXISTS corrections(
 id TEXT PRIMARY KEY,request_sha TEXT NOT NULL,kind TEXT NOT NULL,
 source_key TEXT NOT NULL,source_version TEXT NOT NULL,quote TEXT NOT NULL,
 start INTEGER NOT NULL,end INTEGER NOT NULL,effective_at TEXT,
 reason TEXT NOT NULL,scope TEXT NOT NULL,recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS correction_targets(
 correction_id TEXT NOT NULL REFERENCES corrections(id),dependency_id TEXT NOT NULL,
 path TEXT NOT NULL,heading TEXT NOT NULL,claim TEXT NOT NULL,impact TEXT NOT NULL,
 state TEXT NOT NULL,receipt TEXT,source_key TEXT,source_version TEXT,
 source_quote TEXT,source_start INTEGER,source_end INTEGER,
 PRIMARY KEY(correction_id,dependency_id));
CREATE INDEX IF NOT EXISTS correction_source ON correction_targets(source_key,source_version);
CREATE TABLE IF NOT EXISTS correction_history_sections(
 correction_id TEXT NOT NULL,path TEXT NOT NULL,heading TEXT NOT NULL,
 receipt TEXT NOT NULL,PRIMARY KEY(correction_id,path,heading));
'''

def initialize(backend):
    with backend.connect() as c:
        # Additive migration of an already initialized empty/test registry.
        c.execute('CREATE TABLE IF NOT EXISTS correction_targets(correction_id TEXT NOT NULL REFERENCES corrections(id),dependency_id TEXT NOT NULL,path TEXT NOT NULL,heading TEXT NOT NULL,claim TEXT NOT NULL,impact TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,PRIMARY KEY(correction_id,dependency_id))')
        columns={r['name'] for r in c.execute('PRAGMA table_info(correction_targets)')}
        for name,type_ in [('source_key','TEXT'),('source_version','TEXT'),('source_quote','TEXT'),('source_start','INTEGER'),('source_end','INTEGER')]:
            if name not in columns:c.execute('ALTER TABLE correction_targets ADD COLUMN '+name+' '+type_)
        c.executescript(SCHEMA)

def active(c,path,at=None):
    at=at or stamp()
    return [dict(r) for r in c.execute("SELECT t.*,r.kind,r.effective_at,r.reason FROM correction_targets t JOIN corrections r ON r.id=t.correction_id WHERE t.path=? AND t.state='open' AND (r.effective_at IS NULL OR r.effective_at<=?)",(path,at))]

def checked_origin(lifecycle,origin,claim,scope):
    if not isinstance(origin,dict) or set(origin)!={'source_key','version','quote','start','end'}:
        raise ValueError('Origin requires an explicit source/version/quote/interval')
    source,text=lifecycle._source(origin['source_key'],origin['version'])
    if not lifecycle.backend.in_scope(source['path'],scope):raise ValueError('Origin is out of scope')
    a,b=origin['start'],origin['end']
    if type(a) is not int or type(b) is not int or not 0<=a<b<=len(text) or text[a:b]!=origin['quote'] or origin['quote']!=claim:
        raise ValueError('Origin must bind the exact same assertion, not similar text')
    if not lifecycle.coverage(origin['source_key'],origin['version'])['complete']:
        raise ValueError('Read and acknowledge the full origin before binding')
    return dict(quote=origin['quote'],start=a,end=b)

def register(lifecycle,correction_id,kind,source_key,version,quote,start,end,targets,reason,scope,effective_at=None):
    backend=lifecycle.backend;scope=backend.scope(scope)
    if not isinstance(correction_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',correction_id):raise ValueError('Immutable correction ID required')
    if kind not in {'retraction','supersession','dispute'}:raise ValueError('Explicit correction kind required')
    if not isinstance(reason,str) or len(reason.strip())<8:raise ValueError('A concrete correction reason is required')
    if not isinstance(targets,list) or not 1<=len(targets)<=30:raise ValueError('Explicit bounded dependency targets required')
    if kind!='supersession' and effective_at is not None:raise ValueError('Retraction/dispute must not fabricate a validity end time')
    if effective_at is not None:
        dt=datetime.fromisoformat(effective_at.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError('Effective time needs explicit timezone')
        effective_at=dt.astimezone(timezone.utc).isoformat()
    request=dict(kind=kind,source_key=source_key,version=version,quote=quote,start=start,end=end,targets=targets,reason=reason,scope=scope,effective_at=effective_at)
    signature=sha(json.dumps(request,ensure_ascii=False,sort_keys=True))
    with backend.connect() as c:
        old=c.execute('SELECT request_sha FROM corrections WHERE id=?',(correction_id,)).fetchone()
        if old:
            if old[0]!=signature:raise ValueError('Correction ID already binds different content')
            return dict(correction_id=correction_id,idempotent=True)
    source,text=lifecycle._source(source_key,version)
    if not backend.in_scope(source['path'],scope):raise ValueError('Correction source is out of scope')
    if type(start) is not int or type(end) is not int or not isinstance(quote,str) or not 0<=start<end<=len(text) or text[start:end]!=quote:raise ValueError('Exact correction quote/interval required')
    if not lifecycle.coverage(source_key,version)['complete']:raise ValueError('Read and acknowledge the full correction source first')
    with backend.connect() as c:
        deps={lifecycle.dependency_id(dict(r)):dict(r) for r in c.execute('SELECT * FROM knowledge_dependencies')}
    selected=[];seen=set()
    for target in targets:
        explicit_origin=None
        if target.get('impact') not in {'assertion','example'}:raise ValueError('Classify exact assertion/example targets, not a whole source by similarity')
        if set(target)=={'dependency_id','impact'}:
            ident=target['dependency_id'];dep=deps.get(ident)
        elif set(target) in ({'path','heading','claim','impact'},{'path','heading','claim','impact','origin','expected_sha256'}):
            # Existing manually authored/project paragraphs may have no
            # synthesis dependency. They remain Markdown primary data; record
            # a version-bound correction target, not an invented source fact.
            path=backend.path(target['path']).relative_to(backend.vault).as_posix()
            if path!=target['path'] or not backend.in_scope(path,scope):raise ValueError('Direct assertion path is out of scope or not canonical')
            raw=backend.path(path).read_bytes()
            if not isinstance(target['claim'],str) or not target['claim'].strip() or target['claim'] not in journal_writer.section_text(raw.decode('utf-8-sig'),target['heading']):raise ValueError('Exact current primary-note assertion required')
            dep=dict(source_key=None,source_version=sha(raw),path=path,heading=json.dumps(target['heading'],ensure_ascii=False),claim=target['claim'],status='current')
            if 'origin' in target:
                if target['expected_sha256']!=sha(raw):raise ValueError('Origin-bound target changed since reading')
                explicit_origin=checked_origin(lifecycle,target['origin'],target['claim'],scope)
                dep.update(source_key=target['origin']['source_key'],source_version=target['origin']['version'],target_sha=sha(raw))
            ident='assertion:'+sha(json.dumps(dep,ensure_ascii=False,sort_keys=True))
        else:raise ValueError('Use an exact dependency or a scoped primary-note assertion')
        if ident in seen or not dep or dep['status'] not in {'current','review-required'}:raise ValueError('Unknown, duplicate or retired target dependency')
        seen.add(ident)
        if not backend.in_scope(dep['path'],scope):raise ValueError('Correction target is out of scope')
        raw=backend.path(dep['path']).read_bytes()
        with backend.connect() as c:indexed=c.execute('SELECT sha FROM notes WHERE path=?',(dep['path'],)).fetchone()
        if not indexed or indexed[0]!=sha(raw):raise ValueError('Target changed or has not been indexed')
        with backend.connect() as c:
            prior=c.execute('SELECT claims FROM synthesis_results WHERE source_key=? AND version=?',(dep['source_key'],dep['source_version'])).fetchone() if dep['source_key'] else None
        supported=explicit_origin or (next((x for x in json.loads(prior[0]) if (x['path'],json.dumps(x['heading'],ensure_ascii=False),x['text'])==(dep['path'],dep['heading'],dep['claim'])),None) if prior else None)
        selected.append((dep,target['impact'],sha(raw),supported,ident))
    with backend.connect() as c:
        c.execute('BEGIN IMMEDIATE');lifecycle._source(source_key,version)
        for dep,_,target_sha,_,_ in selected:
            if sha(backend.path(dep['path']).read_bytes())!=target_sha:raise ValueError('Target changed during correction registration')
            if dep.get('target_sha'):lifecycle._source(dep['source_key'],dep['source_version'])
        c.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (correction_id,signature,kind,source_key,version,quote,start,end,effective_at,reason,json.dumps(scope),stamp()))
        for dep,impact,_,supported,ident in selected:
            c.execute('INSERT INTO correction_targets VALUES(?,?,?,?,?,?,?,NULL,?,?,?,?,?)',
                      (correction_id,ident,dep['path'],dep['heading'],dep['claim'],impact,'open',dep['source_key'],dep['source_version'],
                       supported['quote'] if supported else None,supported['start'] if supported else None,supported['end'] if supported else None))
            if dep['source_key'] and (effective_at is None or effective_at<=stamp()):
                c.execute("UPDATE knowledge_dependencies SET status='review-required' WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?",tuple(dep[k] for k in ['source_key','source_version','path','heading','claim']))
            job=sha('review|'+dep['path']+'|'+correction_id)
            c.execute('INSERT OR IGNORE INTO lifecycle_jobs(id,kind,subject,version,state,payload,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      (job,'review',dep['path'],correction_id,'pending',json.dumps(dict(correction_id=correction_id,dependency_id=ident,kind=kind)),stamp(),stamp(),stamp()))
    backend._clear_recall_caches()
    return dict(correction_id=correction_id,idempotent=False,targets=len(selected),kind=kind,effective_at=effective_at,body_writes=0,
                notice='Affected current assertions are withheld before rebuilding. Original sources remain intact.')

def list_corrections(lifecycle,scope,limit=20):
    scope=lifecycle.backend.scope(scope)
    if not 1<=limit<=50:raise ValueError('Bounded correction page required')
    with lifecycle.backend.connect() as c:
        where,args=lifecycle.backend._scope_predicate('t.path',scope)
        rows=[dict(r) for r in c.execute('SELECT r.id,r.kind,r.effective_at,r.recorded_at,t.path,t.heading,t.impact,t.state,t.dependency_id FROM corrections r JOIN correction_targets t ON t.correction_id=r.id WHERE '+where+' ORDER BY r.recorded_at DESC LIMIT ?',[*args,limit])]
    return dict(corrections=rows)

def finish(lifecycle,correction_id,receipt_batch,scope,historical_sections=None,direct_resolutions=None):
    """Existing dependency resolutions settle meaning; this closes their gate."""
    backend=lifecycle.backend;scope=backend.scope(scope);historical_sections=historical_sections or []
    direct_resolutions=direct_resolutions or []
    if not isinstance(direct_resolutions,list) or len(direct_resolutions)>30:raise ValueError('Bounded primary assertion resolutions required')
    direct_by_id={r.get('dependency_id'):r for r in direct_resolutions}
    if len(direct_by_id)!=len(direct_resolutions):raise ValueError('Duplicate direct assertion resolution')
    if not isinstance(receipt_batch,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',receipt_batch):raise ValueError('Exact immutable receipt batch required')
    receipt_path=backend.state_path.parent/'receipts'/(receipt_batch+'.json')
    with backend.connect() as c:
        correction=c.execute('SELECT * FROM corrections WHERE id=?',(correction_id,)).fetchone()
        targets=[dict(r) for r in c.execute('SELECT * FROM correction_targets WHERE correction_id=?',(correction_id,))]
        deps={lifecycle.dependency_id(dict(r)):dict(r) for r in c.execute('SELECT * FROM knowledge_dependencies')}
    if not correction or not targets:raise ValueError('Unknown correction')
    if set(direct_by_id)!={t['dependency_id'] for t in targets if t['dependency_id'].startswith('assertion:')}:raise ValueError('Resolve every primary-note target exactly once')
    replay=(not historical_sections and all(t['dependency_id'].startswith('assertion:') and t['state']=='resolved' and t['receipt']==str(receipt_path) and direct_by_id[t['dependency_id']].get('decision')=='source-retained' for t in targets))
    receipt=(journal_state.verify_committed_receipt if replay else journal_state.verify_receipt)(receipt_path,backend.state_path,backend.vault)
    source,_=lifecycle._source(correction['source_key'],correction['source_version'])
    if not backend.in_scope(source['path'],scope):raise ValueError('Correction source is out of scope')
    snapshots={}
    for t in targets:
        dep=deps.get(t['dependency_id'])
        if not backend.in_scope(t['path'],scope):raise ValueError('Correction target is out of scope')
        if t['dependency_id'].startswith('assertion:'):
            resolution=direct_by_id[t['dependency_id']]
            raw=backend.path(t['path']).read_bytes();body=raw.decode('utf-8-sig')
            retained=resolution.get('decision')=='source-retained'
            required={'dependency_id','path','heading','text','decision'} | ({'proof_path','expected_sha256'} if retained else set())
            if set(resolution)!=required or resolution['path']!=t['path'] or resolution['decision'] not in {'withdrawn','replaced','source-retained'}:raise ValueError('Explicit receipt-bound primary assertion disposition required')
            proof_path=t['path']
            if retained:
                if not any(_config.under(t['path'], r) for r in evidence_roots()) or journal_writer.properties(body).get('type')!='codex-history':raise ValueError('Retained-source closure is only for immutable original history')
                expected=resolution['expected_sha256']
                bound=dict(source_key=t['source_key'],source_version=t['source_version'],path=t['path'],heading=t['heading'],claim=t['claim'],status='current',target_sha=expected)
                if expected!=sha(raw) or t['dependency_id']!='assertion:'+sha(json.dumps(bound,ensure_ascii=False,sort_keys=True)):raise ValueError('Retained original must be byte-identical to the registered target')
                origin=dict(source_key=t['source_key'],version=t['source_version'],quote=t['source_quote'],start=t['source_start'],end=t['source_end'])
                checked_origin(lifecycle,origin,t['claim'],scope)
                if t['claim'] not in journal_writer.section_text(body,json.loads(t['heading'])):raise ValueError('Retained source assertion no longer matches')
                archive_alias=False
                for alias in targets:
                    if alias['path']==t['path'] and alias['claim']==t['claim'] and alias['source_version']==sha(raw) and alias['source_key']:
                        alias_source,_=lifecycle._source(alias['source_key'],alias['source_version'])
                        if alias_source['state']=='archived' and alias_source['path']==t['path']:
                            archive_alias=True
                if not archive_alias:raise ValueError('Retained source needs its explicit archived-file origin gate')
                proof_path=resolution['proof_path']
                if not backend.in_scope(proof_path,scope):raise ValueError('Retained-source proof is out of scope')
            elif t['claim'] in journal_writer.section_text(body,json.loads(t['heading'])):raise ValueError('Old primary assertion remains in its current section')
            proof_raw=backend.path(proof_path).read_bytes();proof_body=proof_raw.decode('utf-8-sig')
            note=next((n for n in receipt['notes'] if n['path']==proof_path),None)
            if not note or (not replay and note['sha256']!=sha(proof_raw)) or not any(ch['section']==resolution['heading'] and ch['text_sha256']==sha(resolution['text']) for ch in note['checks']) or resolution['text'] not in journal_writer.section_text(proof_body,resolution['heading']):raise ValueError('Primary assertion disposition is not exactly verified by this receipt')
            with backend.connect() as c:proof_index=c.execute('SELECT sha FROM notes WHERE path=?',(proof_path,)).fetchone()
            if not proof_index or proof_index[0]!=sha(proof_raw):raise ValueError('Resolution proof index is not current')
            snapshots[proof_path]=sha(proof_raw)
            resolved=dict(receipt=str(receipt_path),decision=resolution['decision'])
            if replay:
                with backend.connect() as c:previous=c.execute('SELECT * FROM dependency_resolutions WHERE id=?',(sha('primary-correction|'+correction_id+'|'+t['dependency_id']),)).fetchone()
                if not previous or previous['receipt']!=str(receipt_path) or previous['decision']!='source-retained' or previous['note_sha']!=sha(raw):raise ValueError('Replay needs the exact already recorded immutable source disposition')
                recorded=json.loads(previous['prior'])
                if any(recorded.get(k)!=v for k,v in t.items() if k not in {'state','receipt'}):raise ValueError('Registered correction target changed since its recorded resolution')
        else:
            if not dep or dep['status'] not in {'withdrawn','superseded'}:raise ValueError('Resolve every exact dependency using the existing receipt-backed review first')
            with backend.connect() as c:
                resolved=c.execute('SELECT receipt,decision,replacements FROM dependency_resolutions WHERE dependency_id=? ORDER BY recorded_at DESC LIMIT 1',(t['dependency_id'],)).fetchone()
        if not resolved or resolved['receipt']!=str(receipt_path):raise ValueError('Correction closure needs the exact resolution receipt')
        t['resolution_decision']=resolved['decision']
        if correction['kind']=='supersession' and correction['effective_at'] and dep:
            from .retrieval_policy import instant
            replacement_ids=json.loads(resolved['replacements'])
            for ident in replacement_ids:
                replacement=deps.get(ident)
                if not replacement or not replacement['valid_from'] or instant(replacement['valid_from'])!=instant(correction['effective_at']):
                    raise ValueError('Replacement rule must carry the explicitly evidenced effective time')
        if resolved['decision']=='retained' and correction['kind']!='dispute':
            raise ValueError('Retaining a disputed assertion differs from cancelling a retraction/time change; register the new explicit evidence/decision')
        raw=backend.path(t['path']).read_bytes()
        note=next((n for n in receipt['notes'] if n['path']==t['path']),None)
        with backend.connect() as c:indexed=c.execute('SELECT sha FROM notes WHERE path=?',(t['path'],)).fetchone()
        if (t['resolution_decision']!='source-retained' and (not note or note['sha256']!=sha(raw))) or not indexed or indexed[0]!=sha(raw):raise ValueError('Corrected body/index/receipt are not current')
        snapshots[t['path']]=sha(raw)
    history=[]
    for item in historical_sections:
        if set(item)!={'path','heading','text'} or not backend.in_scope(item['path'],scope):raise ValueError('Scoped exact retained historical section required')
        raw=backend.path(item['path']).read_bytes();note=next((n for n in receipt['notes'] if n['path']==item['path']),None)
        if not note or note['sha256']!=sha(raw) or not any(ch['section']==item['heading'] and ch['text_sha256']==sha(item['text']) for ch in note['checks']) or item['text'] not in journal_writer.section_text(raw.decode('utf-8-sig'),item['heading']):raise ValueError('Retained history must be exactly verified by the same writer receipt')
        history.append((correction_id,item['path'],json.dumps(item['heading'],ensure_ascii=False),str(receipt_path)))
        historical_body=journal_writer.section_text(raw.decode('utf-8-sig'),item['heading'])
        with backend.connect() as c:
            current_claims=[r[0] for r in c.execute("SELECT claim FROM knowledge_dependencies WHERE path=? AND status='current'",(item['path'],))]
        if any(claim in historical_body for claim in current_claims):
            raise ValueError('A historical section must not swallow unrelated current supported assertions')
    # Retained old assertions cannot re-enter current retrieval from an
    # undeclared historical paragraph after the gate closes.
    for t in targets:
        if t['resolution_decision'] in {'retained','source-retained'}:continue
        body=backend.path(t['path']).read_text(encoding='utf-8')
        allowed=[]
        for item in historical_sections:
            if item['path']==t['path']:
                allowed.append(journal_writer.section_span(body,item['heading']))
        for match in re.finditer(re.escape(t['claim']),body):
            if not any(a<=match.start() and match.end()<=b for a,b in allowed):
                raise ValueError('Every retained old assertion must be in an explicitly receipt-verified historical section')
    if replay:
        for path,expected in snapshots.items():
            if sha(backend.path(path).read_bytes())!=expected:raise ValueError('Source/proof changed during replay')
        # Re-check observed versions too: a canonical revision may leave the
        # old archival file unchanged, but cannot silently close the new origin.
        lifecycle._source(correction['source_key'],correction['source_version'])
        for t in targets:checked_origin(lifecycle,dict(source_key=t['source_key'],version=t['source_version'],quote=t['source_quote'],start=t['source_start'],end=t['source_end']),t['claim'],scope)
        return dict(correction_id=correction_id,state='resolved',targets=len(targets),retained_history=0,idempotent=True,body_writes=0,resolution_writes=0)
    with backend.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for path,expected in snapshots.items():
            if sha(backend.path(path).read_bytes())!=expected:raise ValueError('Target changed during closure')
        for t in targets:
            if correction['kind']=='supersession' and correction['effective_at'] and t['dependency_id'] in deps:
                old=deps[t['dependency_id']]
                c.execute('UPDATE knowledge_dependencies SET valid_to=? WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?',
                          (correction['effective_at'],*tuple(old[k] for k in ['source_key','source_version','path','heading','claim'])))
            if t['dependency_id'].startswith('assertion:'):
                ident=sha('primary-correction|'+correction_id+'|'+t['dependency_id'])
                c.execute('INSERT OR IGNORE INTO dependency_resolutions VALUES(?,?,?,?,?,?,?,?,?)',
                          (ident,t['dependency_id'],t['resolution_decision'],correction['reason'],json.dumps(t,ensure_ascii=False),json.dumps([]),str(receipt_path),snapshots[t['path']],stamp()))
            c.execute('UPDATE correction_targets SET state=?,receipt=? WHERE correction_id=? AND dependency_id=?',
                      ('retained' if t['resolution_decision']=='retained' else 'resolved',str(receipt_path),correction_id,t['dependency_id']))
        c.executemany('INSERT OR IGNORE INTO correction_history_sections VALUES(?,?,?,?)',history)
    backend._clear_recall_caches()
    return dict(correction_id=correction_id,state='resolved',targets=len(targets),retained_history=len(history))
