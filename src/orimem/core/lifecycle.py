"""Versioned source coverage and durable jobs in the OriMem database.

The model performs semantic synthesis. This module verifies delivery, explicit
reading acknowledgement, evidence and receipts; it never manufactures knowledge.
"""
from datetime import datetime, timezone, timedelta
import hashlib, json, uuid
import sqlite3
from orimem import config as _config


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_versions(
 source_key TEXT NOT NULL,version TEXT NOT NULL,path TEXT NOT NULL,state TEXT NOT NULL,
 chars INTEGER NOT NULL,current INTEGER NOT NULL,discovered_at TEXT NOT NULL,
 result TEXT,reason TEXT,PRIMARY KEY(source_key,version));
CREATE TABLE IF NOT EXISTS source_reads(
 token TEXT PRIMARY KEY,source_key TEXT NOT NULL,version TEXT NOT NULL,
 start INTEGER NOT NULL,end INTEGER NOT NULL,content_sha TEXT NOT NULL,
 delivered_at TEXT NOT NULL,acknowledged_at TEXT);
CREATE INDEX IF NOT EXISTS reads_version ON source_reads(source_key,version);
CREATE TABLE IF NOT EXISTS lifecycle_jobs(
 id TEXT PRIMARY KEY,kind TEXT NOT NULL,subject TEXT NOT NULL,version TEXT NOT NULL,
 state TEXT NOT NULL,payload TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
 owner TEXT,lease_until TEXT,available_at TEXT NOT NULL,error TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(kind,subject,version));
CREATE TABLE IF NOT EXISTS synthesis_results(
 source_key TEXT NOT NULL,version TEXT NOT NULL,disposition TEXT NOT NULL,
 reason TEXT NOT NULL,comparison TEXT NOT NULL,claims TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(source_key,version));
CREATE TABLE IF NOT EXISTS synthesis_revisions(
 id TEXT PRIMARY KEY,source_key TEXT NOT NULL,version TEXT NOT NULL,
 prior TEXT NOT NULL,reason TEXT NOT NULL,recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_dependencies(
 source_key TEXT NOT NULL,source_version TEXT NOT NULL,path TEXT NOT NULL,
 heading TEXT NOT NULL,claim TEXT NOT NULL,source_strength TEXT NOT NULL,
 valid_from TEXT,valid_to TEXT,recorded_at TEXT NOT NULL,receipt TEXT,
 note_sha TEXT,status TEXT NOT NULL,PRIMARY KEY(source_key,source_version,path,heading,claim));
CREATE TABLE IF NOT EXISTS stage_health(
 stage TEXT PRIMARY KEY,state TEXT NOT NULL,error TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS material_routes(
 fact_id TEXT NOT NULL,source_key TEXT NOT NULL,version TEXT NOT NULL,
 material_kind TEXT NOT NULL,event_time TEXT,primary_path TEXT,decision TEXT NOT NULL,
 reason TEXT NOT NULL,period_significance TEXT,recorded_at TEXT NOT NULL,
 PRIMARY KEY(fact_id,source_key,version));
CREATE TABLE IF NOT EXISTS dependency_resolutions(
 id TEXT PRIMARY KEY,dependency_id TEXT NOT NULL,decision TEXT NOT NULL,
 reason TEXT NOT NULL,prior TEXT NOT NULL,replacements TEXT NOT NULL,
 receipt TEXT,note_sha TEXT,recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assessment_resolutions(
 id TEXT PRIMARY KEY,request_sha TEXT NOT NULL,source_key TEXT NOT NULL,
 version TEXT NOT NULL,disposition TEXT NOT NULL,receipt TEXT NOT NULL,
 prior TEXT NOT NULL,recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_watches(
 id TEXT PRIMARY KEY,source_key TEXT NOT NULL,source_version TEXT NOT NULL,
 candidate_path TEXT NOT NULL,topics TEXT NOT NULL,scope TEXT NOT NULL,
 include_pending INTEGER NOT NULL,reason TEXT NOT NULL,state TEXT NOT NULL,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_signals(
 watch_id TEXT NOT NULL,source_key TEXT NOT NULL,source_version TEXT NOT NULL,
 source_state TEXT NOT NULL,matched_topics TEXT NOT NULL,recorded_at TEXT NOT NULL,
 PRIMARY KEY(watch_id,source_key,source_version));
CREATE TABLE IF NOT EXISTS candidate_decisions(
 job_id TEXT PRIMARY KEY,request_sha TEXT NOT NULL,decision TEXT NOT NULL,
 reason TEXT NOT NULL,evidence TEXT NOT NULL,revision_id TEXT,recorded_at TEXT NOT NULL);
'''


def register_write(db, plan):
    """Persist before writing: a crash after os.replace cannot lose index work."""
    if not db.exists() or not plan['changes']:
        return
    version = sha(json.dumps(plan,ensure_ascii=False,sort_keys=True))
    ident = sha('index|'+plan['batch_id']+'|'+version)
    payload = json.dumps(dict(batch_id=plan['batch_id'],notes={x['path']:sha(x['content']) for x in plan['changes']}),ensure_ascii=False)
    c = sqlite3.connect(db,timeout=60)
    try:
        with c:
            c.executescript(SCHEMA)
            c.execute('INSERT OR IGNORE INTO lifecycle_jobs(id,kind,subject,version,state,payload,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      (ident,'index',plan['batch_id'],version,'pending',payload,stamp(),stamp(),stamp()))
    finally:
        c.close()


class Lifecycle:
    def __init__(self, backend):
        self.backend = backend
        with backend.connect() as c:
            c.executescript(SCHEMA)
        from .corrections import initialize
        initialize(backend)

    def health_stage(self, stage, error=None):
        if stage not in {'capture','synthesis','write','index','pending-index','projection','source-index'}:
            raise ValueError('Unknown lifecycle stage')
        with self.backend.connect() as c:
            c.execute('INSERT OR REPLACE INTO stage_health VALUES(?,?,?,?)',
                      (stage,'error' if error else 'ok',str(error)[:500] if error else None,stamp()))

    def enqueue(self, kind, subject, version, payload=None):
        if kind not in {'synthesis','index','review','daily','weekly','stage','candidate','pending-index','projection','source-index','knowledge-index'}:
            raise ValueError('Unknown job kind')
        ident = sha(kind+'|'+subject+'|'+version)
        with self.backend.connect() as c:
            c.execute('INSERT OR IGNORE INTO lifecycle_jobs(id,kind,subject,version,state,payload,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      (ident,kind,subject,version,'pending',json.dumps(payload or {},ensure_ascii=False),stamp(),stamp(),stamp()))
        return ident

    def discover(self, paths=None):
        """Only vault source evidence; staging is added independently by its adapter."""
        with self.backend.connect() as c:
            rows = list(c.execute('SELECT session_id,note_path FROM history_sessions'))
        seen = set()
        added = 0
        for r in rows:
            if paths is not None and r['note_path'] not in paths:
                continue
            key = 'archive:'+r['session_id']
            try:
                p = self.backend.path(r['note_path'])
                raw = p.read_bytes()
            except (OSError,ValueError):
                # Stable session IDs in indexed frontmatter allow an external
                # relocation to be reconciled without moving any vault files.
                with self.backend.connect() as c:
                    matches = [n['path'] for n in c.execute("SELECT path,props FROM notes WHERE kind='codex-history'")
                               if json.loads(n['props']).get('session_id') == r['session_id']]
                if len(matches) != 1:
                    self.invalidate(key, 'source missing or ambiguous relocation')
                    continue
                try:
                    relocated = self.backend.path(matches[0])
                    raw = relocated.read_bytes()
                except (OSError,ValueError):
                    self.invalidate(key, 'source missing')
                    continue
                r = dict(r, note_path=matches[0])
                with self.backend.connect() as c:
                    c.execute('UPDATE history_sessions SET note_path=? WHERE session_id=?',(r['note_path'],r['session_id']))
                    c.execute('UPDATE history_messages SET note_path=? WHERE session_id=?',(r['note_path'],r['session_id']))
            version = sha(raw)
            text = raw.decode('utf-8-sig')
            seen.add(key)
            with self.backend.connect() as c:
                old = c.execute('SELECT version FROM source_versions WHERE source_key=? AND current=1',(key,)).fetchone()
                changed = old is not None and old[0] != version
                c.execute('UPDATE source_versions SET current=0 WHERE source_key=? AND version<>?',(key,version))
                c.execute("UPDATE lifecycle_jobs SET state='superseded',error='Source version changed',owner=NULL,lease_until=NULL,updated_at=? WHERE kind='synthesis' AND subject=? AND version<>? AND state IN ('pending','retry','running')",(stamp(),key,version))
                added += c.execute('INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?,NULL,NULL)',
                                   (key,version,r['note_path'],'archived',len(text),1,stamp())).rowcount
                c.execute('UPDATE source_versions SET current=1,state=?,path=? WHERE source_key=? AND version=?',('archived',r['note_path'],key,version))
            if changed:
                self.invalidate(key,'source revised',except_version=version)
            self.enqueue('synthesis',key,version,dict(path=r['note_path']))
        targets = self.reconcile_targets(paths)
        return dict(sources=len(seen),new_versions=added,vault_writes=0,**targets,**self.notify_candidates())

    @staticmethod
    def dependency_id(dep):
        identity = {k:dep[k] for k in ['source_key','source_version','path','heading','claim']}
        return sha(json.dumps(identity,ensure_ascii=False,sort_keys=True))

    def reconcile_targets(self, paths=None):
        """Check actual supported sections, not just a note-level timestamp."""
        from orimem import journal_writer
        with self.backend.connect() as c:
            deps = [dict(r) for r in c.execute("SELECT * FROM knowledge_dependencies WHERE status IN ('current','review-required')")]
        changed = set(paths) if paths is not None else None
        reviewed = relocated_count = retained = 0
        for dep in deps:
            if changed is not None and dep['path'] not in changed and dep['status'] == 'current':
                continue
            original_path = dep['path']
            path = self.backend.path(original_path)
            target = original_path
            raw = None
            if not path.exists() and dep['note_sha']:
                with self.backend.connect() as c:
                    matches = [r['path'] for r in c.execute('SELECT path FROM notes WHERE sha=?',(dep['note_sha'],))]
                # Ambiguous byte-identical copies do not establish a binding.
                if len(matches) == 1:
                    target = matches[0]
                    path = self.backend.path(target)
            try:
                raw = path.read_bytes()
                supported = dep['claim'] in journal_writer.section_text(raw.decode('utf-8-sig'),json.loads(dep['heading']))
                self._source(dep['source_key'],dep['source_version'])
            except (OSError,ValueError):
                supported = False
            from .corrections import active
            with self.backend.connect() as c:
                if any(t['dependency_id']==self.dependency_id(dep) for t in active(c,dep['path'])):
                    # A registered semantic correction cannot be cleared by
                    # the old quote still being present in an unchanged file.
                    supported=False
            identity = tuple(dep[k] for k in ['source_key','source_version','path','heading','claim'])
            with self.backend.connect() as c:
                # Bind only bytes still present after the comparison.
                if supported and (not path.exists() or sha(path.read_bytes()) != sha(raw)):
                    supported = False
                if supported:
                    actual_sha = sha(raw)
                    if target != original_path:
                        moved = dict(dep,path=target,note_sha=actual_sha,status='current')
                        c.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                  tuple(moved[k] for k in ['source_key','source_version','path','heading','claim','source_strength','valid_from','valid_to','recorded_at','receipt','note_sha','status']))
                        c.execute('DELETE FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?',identity)
                        ident = self.dependency_id(dep)
                        c.execute('INSERT OR IGNORE INTO dependency_resolutions VALUES(?,?,?,?,?,?,?,?,?)',
                            (sha('relocation|'+ident+'|'+target),ident,'relocated',
                             'Unique byte-identical indexed target retains the exact supported section.',
                             json.dumps(dep,ensure_ascii=False),json.dumps([self.dependency_id(moved)]),dep['receipt'],actual_sha,stamp()))
                        relocated_count += 1
                    elif dep['note_sha'] != actual_sha or dep['status'] != 'current':
                        c.execute("UPDATE knowledge_dependencies SET note_sha=?,status='current' WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?",(actual_sha,*identity))
                        retained += 1
                elif dep['status'] != 'review-required':
                    c.execute("UPDATE knowledge_dependencies SET status='review-required' WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?",identity)
                    reviewed += 1
            if not supported:
                with self.backend.connect() as c:
                    already_queued = c.execute("SELECT 1 FROM lifecycle_jobs WHERE kind='review' AND subject=? AND state IN ('pending','retry','running')",(original_path,)).fetchone()
                if not already_queued:
                    self.enqueue('review',original_path,sha('target|'+self.dependency_id(dep)+'|'+(sha(raw) if raw is not None else 'missing')),
                                 dict(dependency_id=self.dependency_id(dep),source_key=dep['source_key'],reason='Target missing, section changed, or supporting source no longer current'))
        with self.backend.connect() as c:
            c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE kind='review' AND state IN ('pending','retry','running') AND NOT EXISTS (SELECT 1 FROM knowledge_dependencies d WHERE d.path=lifecycle_jobs.subject AND d.status='review-required')",(stamp(),))
        return dict(target_dependencies_retained=retained,target_dependencies_relocated=relocated_count,target_dependencies_review_required=reviewed)

    def dependency_reviews(self, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid dependency review page')
        with self.backend.connect() as c:
            total = c.execute("SELECT count(*) FROM knowledge_dependencies WHERE status='review-required'").fetchone()[0]
            rows = [dict(r) for r in c.execute("SELECT * FROM knowledge_dependencies WHERE status='review-required' ORDER BY path,source_key,source_version,heading,claim LIMIT ? OFFSET ?",(limit,offset))]
        for row in rows:
            row['dependency_id'] = self.dependency_id(row)
        return dict(dependencies=rows,total=total,next_offset=offset+len(rows) if offset+len(rows)<total else None)

    def resolve_review(self, dependency_id, decision, reason, receipt_batch, target_path, heading, text, replacements=None):
        """Settle one reviewed fact only against current writer/index evidence.

        Semantic judgments remain the model's responsibility. This gate proves
        their supported replacement, body change and durable receipt exist.
        """
        from orimem import journal_state, journal_writer
        if decision not in {'retained','replaced','withdrawn'} or not isinstance(reason,str) or len(reason.strip()) < 8:
            raise ValueError('An explicit dependency disposition and reason are required')
        if not isinstance(receipt_batch,str) or not receipt_batch.replace('-','').replace('_','').isalnum():
            raise ValueError('A writer receipt batch is required')
        if not isinstance(replacements,list) or len(replacements)>30 or len(set(replacements))!=len(replacements):
            raise ValueError('Replacement dependency IDs must be a bounded unique list')
        receipt_path = self.backend.state_path.parent/'receipts'/(receipt_batch+'.json')
        receipt = journal_state.verify_receipt(receipt_path,self.backend.state_path,self.backend.vault)
        path = self.backend.path(target_path)
        raw = path.read_bytes()
        if not isinstance(text,str) or not text.strip() or text not in journal_writer.section_text(raw.decode('utf-8-sig'),heading):
            raise ValueError('Resolution statement is missing from the exact target section')
        notes = [n for n in receipt['notes'] if n['path']==target_path]
        if len(notes)!=1 or notes[0]['sha256']!=sha(raw) or not any(x['section']==heading and x['text_sha256']==sha(text) for x in notes[0]['checks']):
            raise ValueError('Writer receipt does not bind the resolution statement')
        with self.backend.connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM knowledge_dependencies')]
        by_id = {self.dependency_id(r):r for r in rows}
        dep = by_id.get(dependency_id)
        if not dep:
            raise ValueError('Dependency identity is unknown')
        ident = sha('resolution|'+dependency_id+'|'+decision+'|'+receipt_batch)
        with self.backend.connect() as c:
            prior_resolution = c.execute('SELECT * FROM dependency_resolutions WHERE id=?',(ident,)).fetchone()
        if prior_resolution:
            if (prior_resolution['decision']!=decision or prior_resolution['reason']!=reason or
                json.loads(prior_resolution['replacements'])!=replacements):
                raise ValueError('A completed resolution cannot change under the same identity')
            return dict(dependency_id=dependency_id,decision=decision,idempotent=True,resolution_id=ident)
        if dep['status']!='review-required':
            raise ValueError('Dependency is not awaiting review')
        if decision in {'retained','replaced'} and not replacements:
            raise ValueError('Retained or replaced conclusions require current supported dependencies')
        replacement_snapshots = []
        for replacement_id in replacements:
            new = by_id.get(replacement_id)
            if not new or new['status']!='current' or not new['receipt']:
                raise ValueError('Replacement is not current and receipt-backed')
            self._source(new['source_key'],new['source_version'])
            if not self.coverage(new['source_key'],new['source_version'])['complete']:
                raise ValueError('Replacement source has not been fully read')
            replacement_raw = self.backend.path(new['path']).read_bytes()
            with self.backend.connect() as c:
                indexed = c.execute('SELECT sha FROM notes WHERE path=?',(new['path'],)).fetchone()
            if not indexed or indexed[0]!=sha(replacement_raw) or new['claim'] not in journal_writer.section_text(replacement_raw.decode('utf-8-sig'),json.loads(new['heading'])):
                raise ValueError('Replacement target is not current in body and index')
            replacement_snapshots.append((new,sha(replacement_raw)))
        if decision=='retained' and not any(by_id[i]['claim']==dep['claim'] for i in replacements):
            raise ValueError('Retained fact requires current support for the same exact claim')
        if decision in {'replaced','withdrawn'}:
            try:
                old_body = self.backend.path(dep['path']).read_text(encoding='utf-8-sig')
                old_claim_present = dep['claim'] in journal_writer.section_text(old_body,json.loads(dep['heading']))
            except (OSError,ValueError):
                old_claim_present = False
            if old_claim_present:
                raise ValueError('The old current assertion must be removed or moved to an explicit historical section')
        with self.backend.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            indexed = c.execute('SELECT sha FROM notes WHERE path=?',(target_path,)).fetchone()
            if not indexed or indexed[0]!=sha(raw) or sha(path.read_bytes())!=sha(raw):
                raise ValueError('Resolution target changed or has not been indexed')
            for new, expected_sha in replacement_snapshots:
                self._source(new['source_key'],new['source_version'])
                replacement_identity = tuple(new[k] for k in ['source_key','source_version','path','heading','claim'])
                replacement_live = c.execute('SELECT status FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?',replacement_identity).fetchone()
                if not replacement_live or replacement_live[0]!='current' or sha(self.backend.path(new['path']).read_bytes())!=expected_sha:
                    raise ValueError('Replacement evidence changed while resolving')
            identity = tuple(dep[k] for k in ['source_key','source_version','path','heading','claim'])
            live = c.execute('SELECT status FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?',identity).fetchone()
            if not live or live[0]!='review-required':
                raise ValueError('Dependency changed while resolving')
            c.execute("UPDATE knowledge_dependencies SET status=? WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?",('withdrawn' if decision=='withdrawn' else 'superseded',*identity))
            c.execute('INSERT INTO dependency_resolutions VALUES(?,?,?,?,?,?,?,?,?)',
                      (ident,dependency_id,decision,reason,json.dumps(dep,ensure_ascii=False),json.dumps(replacements),str(receipt_path),sha(raw),stamp()))
            if not c.execute("SELECT 1 FROM knowledge_dependencies WHERE path=? AND status='review-required'",(dep['path'],)).fetchone():
                c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE kind='review' AND subject=? AND state IN ('pending','retry','running')",(stamp(),dep['path']))
        return dict(dependency_id=dependency_id,decision=decision,idempotent=False,resolution_id=ident)

    def _pending_text(self, session_id):
        with self.backend.connect() as c:
            c.execute('BEGIN')
            session = c.execute('SELECT * FROM pending_sessions WHERE session_id=?',(session_id,)).fetchone()
            if not session:
                raise ValueError('Pending snapshot no longer available')
            rows = list(c.execute('SELECT event_index,ts,role,content FROM pending_messages WHERE session_id=? ORDER BY event_index',(session_id,)))
        # Stable exact-message representation; no model summary or vault export.
        return '# 暂存佐证 '+session_id+'\n\n'+'\n\n'.join('## 事件 '+str(r['event_index'])+' | '+str(r['ts'])+' | '+str(r['role'])+'\n\n'+r['content'] for r in rows)

    def discover_pending(self):
        with self.backend.connect() as c:
            sessions = [r[0] for r in c.execute('SELECT session_id FROM pending_sessions')]
            former = [r[0] for r in c.execute("SELECT source_key FROM source_versions WHERE current=1 AND state='pending'")]
        for key in former:
            if key.removeprefix('pending:') not in sessions:
                self.invalidate(key,'pending snapshot archived or unavailable')
                with self.backend.connect() as c:
                    c.execute("UPDATE lifecycle_jobs SET state='superseded',error='Pending snapshot archived or unavailable',owner=NULL,lease_until=NULL,updated_at=? WHERE kind='synthesis' AND subject=? AND state IN ('pending','retry','running')",(stamp(),key))
        added = 0
        for sid in sessions:
            key = 'pending:'+sid
            text = self._pending_text(sid)
            version = sha(text)
            with self.backend.connect() as c:
                old = c.execute('SELECT version FROM source_versions WHERE source_key=? AND current=1',(key,)).fetchone()
                c.execute('UPDATE source_versions SET current=0 WHERE source_key=? AND version<>?',(key,version))
                c.execute("UPDATE lifecycle_jobs SET state='superseded',error='Source version changed',owner=NULL,lease_until=NULL,updated_at=? WHERE kind='synthesis' AND subject=? AND version<>? AND state IN ('pending','retry','running')",(stamp(),key,version))
                added += c.execute('INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?,NULL,NULL)',
                    (key,version,'pending:'+sid,'pending',len(text),1,stamp())).rowcount
                c.execute('UPDATE source_versions SET current=1 WHERE source_key=? AND version=?',(key,version))
                # Reuse only acknowledgements whose exact delivered bytes still match.
                if old and old[0] != version:
                    for read in list(c.execute('SELECT * FROM source_reads WHERE source_key=? AND version=? AND acknowledged_at IS NOT NULL',(key,old[0]))):
                        if sha(text[read['start']:read['end']]) == read['content_sha']:
                            c.execute('INSERT INTO source_reads VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,key,version,read['start'],read['end'],read['content_sha'],stamp(),stamp()))
            if old and old[0] != version:
                self.invalidate(key,'pending source revised',except_version=version)
            self.enqueue('synthesis',key,version,dict(source_state='pending',session_id=sid))
        return dict(sources=len(sessions),new_versions=added,vault_writes=0,**self.notify_candidates())

    def indexed_receipts(self):
        """Only settle durable intents after a verified writer receipt and index."""
        from orimem import journal_state, journal_writer
        settled = []
        with self.backend.connect() as c:
            jobs = [dict(r) for r in c.execute("SELECT * FROM lifecycle_jobs WHERE kind='index' AND state IN ('pending','retry','running') AND available_at<=?",(stamp(),))]
        for job in jobs:
            payload = json.loads(job['payload'])
            receipt_path = self.backend.state_path.parent / 'receipts' / (job['subject']+'.json')
            if not receipt_path.exists():
                continue
            try:
                receipt = journal_state.verify_receipt(receipt_path,self.backend.state_path,self.backend.vault)
                expected = payload['notes']
                with self.backend.connect() as c:
                    for note in receipt['notes']:
                        indexed = c.execute('SELECT sha FROM notes WHERE path=?',(note['path'],)).fetchone()
                        raw=self.backend.path(note['path']).read_bytes()
                        props=journal_writer.properties(raw.decode('utf-8-sig'))
                        if props.get('orion_index') is False or _config.under(note['path'], _config.layout_path('legacy')):
                            # A user-excluded manual archive is verified as
                            # absent from Markdown search, not a stuck index.
                            if indexed or sha(raw)!=note['sha256'] or expected.get(note['path'])!=note['sha256']:
                                raise ValueError('Manual archive exclusion/version not applied: '+note['path'])
                            if c.execute("SELECT 1 FROM knowledge_dependencies WHERE path=? AND status='write-pending'",(note['path'],)).fetchone():
                                raise ValueError('Unindexed archives cannot finalize knowledge claims')
                            continue
                        if not indexed or indexed[0] != note['sha256'] or expected.get(note['path']) != note['sha256']:
                            raise ValueError('Written version is not yet indexed: '+note['path'])
                    deps = list(c.execute("SELECT * FROM knowledge_dependencies WHERE status='write-pending'"))
                    for dep in deps:
                        if dep['path'] not in expected:
                            continue
                        text = self.backend.path(dep['path']).read_text(encoding='utf-8')
                        heading = json.loads(dep['heading'])
                        if dep['claim'] not in journal_writer.section_text(text,heading):
                            continue
                        self._source(dep['source_key'],dep['source_version'])
                        c.execute("UPDATE knowledge_dependencies SET receipt=?,note_sha=?,status='current' WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?",
                                  (str(receipt_path),expected[dep['path']],dep['source_key'],dep['source_version'],dep['path'],dep['heading'],dep['claim']))
                    c.execute("UPDATE lifecycle_jobs SET state='complete',error=NULL,updated_at=? WHERE id=?",(stamp(),job['id']))
                settled.append(job['id'])
            except (OSError,ValueError) as error:
                self.health_stage('index',error)
                attempt=job['attempts']+1
                state='failed' if attempt>=3 else 'retry'
                available=(datetime.now(timezone.utc)+timedelta(seconds=60*attempt)).isoformat()
                with self.backend.connect() as c:
                    c.execute('UPDATE lifecycle_jobs SET state=?,attempts=?,error=?,available_at=?,updated_at=?,owner=NULL,lease_until=NULL WHERE id=?',(state,attempt,str(error)[:500],available,stamp(),job['id']))
        with self.backend.connect() as c:
            for r in c.execute('SELECT * FROM synthesis_results'):
                count = c.execute("SELECT count(*) FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND status IN ('write-pending','review-required')",(r['source_key'],r['version'])).fetchone()[0]
                if count == 0:
                    c.execute('UPDATE source_versions SET result=? WHERE source_key=? AND version=?',(r['disposition'],r['source_key'],r['version']))
                    c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE kind='synthesis' AND subject=? AND version=?",(stamp(),r['source_key'],r['version']))
            errors=c.execute("SELECT count(*) FROM lifecycle_jobs WHERE kind='index' AND state IN ('retry','failed') AND error IS NOT NULL").fetchone()[0]
        if errors:
            self.health_stage('index',str(errors)+' index receipt jobs require retry or review')
        return dict(settled_index_jobs=len(settled))

    def invalidate(self, source_key, reason, except_version=None):
        with self.backend.connect() as c:
            deps = list(c.execute("SELECT DISTINCT path FROM knowledge_dependencies WHERE source_key=? AND source_version<>? AND status='current'",(source_key,except_version or '')))
            c.execute("UPDATE knowledge_dependencies SET status='review-required' WHERE source_key=? AND source_version<>?",(source_key,except_version or ''))
            if except_version is None:
                c.execute("UPDATE source_versions SET current=0,state='missing' WHERE source_key=?",(source_key,))
        for d in deps:
            self.enqueue('review',d['path'],sha(source_key+'|'+(except_version or reason)),dict(source_key=source_key,reason=reason))

    def _source(self, source_key, version=None):
        with self.backend.connect() as c:
            r = c.execute('SELECT * FROM source_versions WHERE source_key=? AND '+('version=?' if version else 'current=1'),
                          (source_key,version) if version else (source_key,)).fetchone()
        if not r or not r['current']:
            raise ValueError('Source version is not current; rediscover')
        if r['state']=='message':
            from .message_sources import current
            message,path=current(self.backend,source_key)
            if message['version']!=r['version'] or path!=r['path']:
                raise ValueError('Canonical message changed; reselect before reading')
            return dict(r),message['content']
        text = self._pending_text(source_key.removeprefix('pending:')) if r['state']=='pending' else None
        raw = text.encode('utf-8') if text is not None else self.backend.path(r['path']).read_bytes()
        if sha(raw) != r['version']:
            raise ValueError('Source changed; rediscover before reading or completing')
        return dict(r), text if text is not None else raw.decode('utf-8-sig')

    def route(self, source_key,version,material_kind,quote,start,end,reason,event_time=None,primary_path=None,period_significance=None):
        from orimem import journal_state
        source,text = self._source(source_key,version)
        kinds = {'progress','decision','deliverable','obstacle','correction','experience','question','chatter','suggestion','maintenance'}
        if material_kind not in kinds or not reason or type(start) is not int or type(end) is not int or not 0<=start<end<=len(text) or text[start:end]!=quote:
            raise ValueError('A classified material with exact supporting source and reason is required')
        coverage = self.coverage(source_key,version)
        if not any(a<=start and end<=b for a,b in coverage['ranges']):
            raise ValueError('Read and acknowledge the actual supporting material first')
        if primary_path:
            self.backend.path(primary_path)
        fact_id = sha(quote.strip())
        decision = []
        if material_kind not in {'question','chatter','suggestion','maintenance'}:
            if primary_path:
                decision.append('primary-record')
            if event_time:
                event = journal_state.dt(event_time)
                if event > datetime.now(timezone.utc)+timedelta(minutes=5):
                    raise ValueError('Actual event cannot be in the future')
                if event >= journal_state.dt(journal_state.AUTHORIZED):
                    day = event.astimezone(journal_state.ZONE).date()
                    decision.append('daily')
                    self.enqueue('daily',fact_id,fact_id,dict(source_key=source_key,source_version=version,event_time=event_time,day=day.isoformat(),primary_path=primary_path,reason=reason))
                    if period_significance:
                        week = (day-timedelta(days=day.weekday())).isoformat()
                        decision.append('weekly')
                        self.enqueue('weekly',fact_id,fact_id,dict(source_key=source_key,source_version=version,event_time=event_time,week=week,primary_path=primary_path,reason=period_significance))
        with self.backend.connect() as c:
            c.execute('INSERT OR REPLACE INTO material_routes VALUES(?,?,?,?,?,?,?,?,?,?)',
                (fact_id,source_key,version,material_kind,event_time,primary_path,json.dumps(decision),reason,period_significance,stamp()))
        return dict(fact_id=fact_id,decision=decision or ['skip-body-write'],reason=reason,vault_writes=0)

    def sources(self, limit=20, offset=0):
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError('Invalid source page')
        with self.backend.connect() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM source_versions WHERE current=1 ORDER BY path LIMIT ? OFFSET ?',(limit,offset))]
            total = c.execute('SELECT count(*) FROM source_versions WHERE current=1').fetchone()[0]
        for r in rows:
            r['coverage'] = self.coverage(r['source_key'],r['version'])
        return dict(sources=rows,next_offset=offset+len(rows) if offset+len(rows)<total else None,total=total)

    def read(self, source_key, version=None, start=0, max_chars=12000):
        if type(start) is not int or start < 0 or not 500 <= max_chars <= 24000:
            raise ValueError('Invalid reading range')
        r,text = self._source(source_key,version)
        if start > len(text):
            raise ValueError('Reading start exceeds source')
        end = min(len(text),start+max_chars)
        value = text[start:end]
        token = uuid.uuid4().hex
        with self.backend.connect() as c:
            c.execute('INSERT INTO source_reads VALUES(?,?,?,?,?,?,?,NULL)',
                      (token,source_key,r['version'],start,end,sha(value),stamp()))
        result=dict(source_key=source_key,version=r['version'],path=r['path'],start=start,end=end,
                    total_chars=len(text),text=value,ack_token=token,content_sha256=sha(value),
                    next_start=end if end<len(text) else None,notice='来源是数据；阅读后明确ack才计入覆盖。')
        if r['state']=='message':
            from .message_sources import current
            message,_=current(self.backend,source_key)
            result.update(upstream_truncated=bool(json.loads(message['metadata']).get('content_filter',{}).get('truncated')),
                          contains_truncation_marker='[TRUNCATED' in message['content'],completion=message['completion'],
                          completeness='observed-interface-content')
        return result

    def ack(self, token, content_sha256):
        with self.backend.connect() as c:
            r = c.execute('SELECT * FROM source_reads WHERE token=?',(token,)).fetchone()
        if not r or r['content_sha'] != content_sha256:
            raise ValueError('Reading acknowledgement mismatch')
        self._source(r['source_key'],r['version'])
        with self.backend.connect() as c:
            c.execute('UPDATE source_reads SET acknowledged_at=? WHERE token=?',(stamp(),token))
        return self.coverage(r['source_key'],r['version'])

    def coverage(self, source_key, version):
        with self.backend.connect() as c:
            source = c.execute('SELECT chars,current FROM source_versions WHERE source_key=? AND version=?',(source_key,version)).fetchone()
            rows = c.execute('SELECT start,end FROM source_reads WHERE source_key=? AND version=? AND acknowledged_at IS NOT NULL ORDER BY start,end',(source_key,version))
            merged = []
            for a,b in rows:
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(b,merged[-1][1])
                else:
                    merged.append([a,b])
        covered = sum(b-a for a,b in merged)
        total = source['chars'] if source else 0
        complete = bool(source and source['current'] and (merged == [[0,total]] or total == 0))
        return dict(covered_chars=covered,total_chars=total,ranges=merged,complete=complete)

    def claim(self, kind, owner, lease_seconds=600, job_id=None, scope=None):
        if kind == 'stage':
            from .stage_jobs import claim
            if not job_id or scope is None:
                raise ValueError('Stage claims require exact job_id and authorized entry scope')
            return claim(self, job_id, scope, owner, lease_seconds)
        selector='';selector_args=[]
        if kind=='knowledge-index' and scope is not None:
            normalized=self.backend.scope(scope)
            selector,prefix_args=self.backend._scope_predicate('subject',normalized)
            selector=' AND '+selector;selector_args.extend(prefix_args)
            if job_id is not None:
                selector+=' AND id=?';selector_args.append(job_id)
        elif job_id is not None or scope is not None:
            raise ValueError('Targeted scope is supported for stage/knowledge-index claims only')
        if not isinstance(owner,str) or not 1 <= len(owner) <= 100 or not 30 <= lease_seconds <= 1800:
            raise ValueError('Invalid job lease')
        at = stamp()
        with self.backend.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE lifecycle_jobs SET state='failed',owner=NULL,lease_until=NULL,error='Lease expired; retry budget exhausted',updated_at=? WHERE state='running' AND lease_until<? AND attempts>=3",(at,at))
            c.execute("UPDATE lifecycle_jobs SET state='pending',owner=NULL,lease_until=NULL,updated_at=? WHERE state='running' AND lease_until<? AND attempts<3",(at,at))
            r = c.execute("SELECT * FROM lifecycle_jobs WHERE kind=? AND state IN ('pending','retry') AND available_at<=? AND attempts<3"+selector+" ORDER BY created_at,id LIMIT 1",(kind,at,*selector_args)).fetchone()
            if not r:
                return dict(job=None)
            until = (datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat()
            c.execute("UPDATE lifecycle_jobs SET state='running',owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",(owner,until,at,r['id']))
        return dict(job=dict(r,owner=owner,lease_until=until,state='running',attempts=r['attempts']+1))

    def finish_job(self, job_id, owner, error=None, receipt_batch=None):
        with self.backend.connect() as c:
            r = c.execute('SELECT * FROM lifecycle_jobs WHERE id=?',(job_id,)).fetchone()
            if not r or r['state'] != 'running' or r['owner'] != owner or r['lease_until'] < stamp():
                raise ValueError('Job lease missing or expired')
            if error is None:
                if r['kind']=='synthesis':
                    source = c.execute('SELECT result FROM source_versions WHERE source_key=? AND version=? AND current=1',(r['subject'],r['version'])).fetchone()
                    if not source or not source['result'] or source['result']=='write-pending' or not self.coverage(r['subject'],r['version'])['complete']:
                        raise ValueError('Synthesis cannot finish without full reading and verified disposition')
                elif r['kind'] in {'daily','weekly'}:
                    from orimem import journal_state
                    if not receipt_batch or not isinstance(receipt_batch,str) or not receipt_batch.replace('-','').replace('_','').isalnum():
                        raise ValueError('Period job completion requires a verified writer receipt batch')
                    receipt = journal_state.verify_receipt(self.backend.state_path.parent/'receipts'/(receipt_batch+'.json'),self.backend.state_path,self.backend.vault)
                    payload = json.loads(r['payload'])
                    target = _config.daily_note(payload['day']) if r['kind']=='daily' else _config.weekly_note(payload['week'])
                    if not any(n['path']==target for n in receipt['notes']):
                        raise ValueError('Receipt does not verify the material target period')
                elif r['kind']=='index':
                    raise ValueError('Index completion is settled only against source hashes and receipts')
                else:
                    raise ValueError('Use a supported review/period result to complete this job')
            state = ('failed' if r['attempts'] >= 3 else 'retry') if error else 'complete'
            available = (datetime.now(timezone.utc)+timedelta(seconds=60*r['attempts'])).isoformat()
            c.execute('UPDATE lifecycle_jobs SET state=?,error=?,available_at=?,owner=NULL,lease_until=NULL,updated_at=? WHERE id=?',
                      (state,str(error)[:500] if error else None,available,stamp(),job_id))
        return dict(job_id=job_id,state=state)

    def validate_assessment(self, source_key, version, disposition, reason, comparison, claims=None):
        r,text = self._source(source_key,version)
        if not self.coverage(source_key,version)['complete']:
            raise ValueError('Full explicit reading coverage is required')
        allowed = {'merged','formal-knowledge','candidate','no-reusable-knowledge','conflict','needs-evidence'}
        if disposition not in allowed or not isinstance(reason,str) or len(reason.strip()) < 8:
            raise ValueError('Concrete synthesis decision and reason required')
        if not isinstance(comparison,list):
            raise ValueError('Existing knowledge comparison required')
        for p in comparison:
            self.backend.path(p).read_bytes()
        claims = claims or []
        if not isinstance(claims,list) or len(claims)>60:
            raise ValueError('Claims must be a bounded list')
        if disposition in {'formal-knowledge','merged','candidate'} and not claims:
            raise ValueError('Written dispositions require supported claims')
        for claim in claims:
            for field in ['text','path','heading','strength','boundary','quote','start','end']:
                if field not in claim:
                    raise ValueError('Claim missing '+field)
            self.backend.path(claim['path'])
            if not isinstance(claim['heading'],list) or not claim['heading']:
                raise ValueError('Exact heading address required')
            a,b = claim['start'],claim['end']
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text) or text[a:b] != claim['quote']:
                raise ValueError('Claim quotation does not match pinned source')
            if claim['strength'] not in {'user-confirmed','historical-report','inference','verified-artifact'}:
                raise ValueError('Invalid evidence strength')
            if _config.under(claim['path'], _config.layout_path('knowledge')) and (not claim['boundary'] or not claim.get('promotion_reason')):
                raise ValueError('Formal knowledge needs boundaries and promotion rationale')
        return r,text

    def assessment(self, source_key, version):
        from .assessment_revision import assessment
        return assessment(self, source_key, version)

    def revise_assessment(self, **arguments):
        from .assessment_revision import revise
        return revise(self, **arguments)

    def register_candidate(self, **arguments):
        from .candidate_reviews import register
        return register(self, **arguments)

    def notify_candidates(self):
        from .candidate_reviews import notify
        return notify(self)

    def candidate_tasks(self, limit=20, offset=0):
        from .candidate_reviews import tasks
        return tasks(self,limit,offset)

    def candidate_decision(self, **arguments):
        from .candidate_reviews import decide
        return decide(self,**arguments)

    def stage_tasks(self, **arguments):
        from .stage_jobs import tasks
        return tasks(self, **arguments)

    def stage_decision(self, **arguments):
        from .stage_jobs import decide
        return decide(self, **arguments)

    def review(self, source_key, version, disposition, reason, comparison, claims=None, revision_reason=None):
        self.validate_assessment(source_key,version,disposition,reason,comparison,claims)
        claims=claims or []
        with self.backend.connect() as c:
            previous = c.execute('SELECT * FROM synthesis_results WHERE source_key=? AND version=?',(source_key,version)).fetchone()
            changed = previous and (previous['claims'] != json.dumps(claims,ensure_ascii=False) or previous['disposition'] != disposition)
            if changed:
                written = c.execute('SELECT count(*) FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND receipt IS NOT NULL',(source_key,version)).fetchone()[0]
                if written or not isinstance(revision_reason,str) or len(revision_reason.strip())<8:
                    raise ValueError('Written assessments require a correction job; unwritten revisions need a concrete reason')
                c.execute('INSERT INTO synthesis_revisions VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,source_key,version,json.dumps(dict(previous),ensure_ascii=False),revision_reason,stamp()))
                c.execute('DELETE FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND receipt IS NULL',(source_key,version))
            c.execute('INSERT OR REPLACE INTO synthesis_results VALUES(?,?,?,?,?,?,?)',
                      (source_key,version,disposition,reason,json.dumps(comparison,ensure_ascii=False),json.dumps(claims,ensure_ascii=False),stamp()))
            for x in claims:
                c.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                          (source_key,version,x['path'],json.dumps(x['heading'],ensure_ascii=False),x['text'],x['strength'],x.get('valid_from'),x.get('valid_to'),stamp(),None,None,'write-pending'))
            finished = not claims
            c.execute('UPDATE source_versions SET result=?,reason=? WHERE source_key=? AND version=?',
                      (disposition if finished else 'write-pending',reason,source_key,version))
            if finished:
                c.execute("UPDATE lifecycle_jobs SET state='complete',error=NULL,owner=NULL,lease_until=NULL,updated_at=? WHERE kind='synthesis' AND subject=? AND version=?",(stamp(),source_key,version))
        self.health_stage('synthesis')
        return dict(source_key=source_key,assessment=disposition,state='assessed' if claims else 'complete',claims=len(claims))

    def health(self):
        with self.backend.connect() as c:
            stages = {r['stage']:dict(r) for r in c.execute('SELECT * FROM stage_health')}
            jobs = [dict(r) for r in c.execute('SELECT kind,state,count(*) count FROM lifecycle_jobs GROUP BY kind,state')]
            results = [dict(r) for r in c.execute('SELECT result,count(*) count FROM source_versions WHERE current=1 GROUP BY result')]
            deps = c.execute("SELECT count(*) FROM knowledge_dependencies WHERE status='review-required'").fetchone()[0]
        for stage in ['capture','synthesis','write','index','projection','source-index']:
            stages.setdefault(stage,dict(stage=stage,state='not-run',error=None,updated_at=None))
        return dict(stages=stages,jobs=jobs,source_results=results,review_required_dependencies=deps)

    def dispatch(self, action, arguments=None):
        methods = {'discover':self.discover,'discover_pending':self.discover_pending,'route':self.route,'sources':self.sources,'read':self.read,'ack':self.ack,
                   'coverage':self.coverage,'claim':self.claim,'finish_job':self.finish_job,'review':self.review,'health':self.health,
                   'dependency_reviews':self.dependency_reviews,'resolve_review':self.resolve_review,'reconcile_targets':self.reconcile_targets,
                   'assessment':self.assessment,'revise_assessment':self.revise_assessment,
                   'register_candidate':self.register_candidate,'candidate_tasks':self.candidate_tasks,
                   'candidate_decision':self.candidate_decision,'notify_candidates':self.notify_candidates,
                   'stage_tasks':self.stage_tasks,'stage_decision':self.stage_decision}
        from .message_sources import select
        from .corrections import register,finish,list_corrections
        methods.update(message_source=lambda **args:select(self,**args),
                       register_correction=lambda **args:register(self,**args),
                       finish_correction=lambda **args:finish(self,**args),
                       corrections=lambda **args:list_corrections(self,**args))
        from .knowledge_index import sync as knowledge_sync,neighbors
        methods.update(knowledge_sync=lambda **args:knowledge_sync(self.backend,**args),
                       knowledge_neighbors=lambda **args:neighbors(self.backend,**args))
        if action not in methods:
            raise ValueError('Unknown lifecycle action')
        return methods[action](**(arguments or {}))
