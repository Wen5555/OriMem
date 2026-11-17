"""Versioned ordinary source observations and deterministic writer projections.

Source commit, Markdown projection and embedding are separate transactions.
The adapter's event index is a locator, NOT an upstream message identity.
"""
from datetime import datetime
import json, re, uuid
from orimem import journal_state, journal_writer
from .backend import digest, now
from .migration import archive_path, quote

SCHEMA = '''
CREATE TABLE IF NOT EXISTS capture_sessions(
 session_id TEXT PRIMARY KEY,title TEXT NOT NULL,cwd TEXT NOT NULL,
 snapshot_sha TEXT NOT NULL,path TEXT NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS capture_versions(
 session_id TEXT NOT NULL,event_index INTEGER NOT NULL,version TEXT NOT NULL,
 ts TEXT,role TEXT NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT NULL,
 metadata TEXT NOT NULL,identity_basis TEXT NOT NULL,completion TEXT NOT NULL,
 first_observed_at TEXT NOT NULL,PRIMARY KEY(session_id,event_index,version));
CREATE TABLE IF NOT EXISTS capture_snapshots(
 session_id TEXT NOT NULL,version TEXT NOT NULL,records TEXT NOT NULL,
 observed_at TEXT NOT NULL,PRIMARY KEY(session_id,version));
CREATE TABLE IF NOT EXISTS capture_checkpoints(
 namespace TEXT PRIMARY KEY,snapshot_sha TEXT NOT NULL,observed_at TEXT NOT NULL,
 session_count INTEGER NOT NULL,message_count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_projections(
 session_id TEXT PRIMARY KEY,path TEXT NOT NULL,snapshot_sha TEXT NOT NULL,
 managed_sha TEXT NOT NULL,output_sha TEXT NOT NULL,receipt TEXT NOT NULL,
 projected_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projection_intents(
 session_id TEXT PRIMARY KEY,managed_sha TEXT NOT NULL,at TEXT NOT NULL);
'''
# Language-neutral markers delimit the generated region; headings are presentation only and
# follow the language the note was created in (existing notes keep theirs).
BEGIN = '<!-- orion:continuous:begin -->'
END = '<!-- orion:continuous:end -->'
FORMAT = 'continuous-v1'
TEXT = {
    'zh': dict(title='持续佐证', transcript='程序转录', notes='人工注释',
               intro=['本页保留实际观察到的普通消息及修订；保存不表示完成知识提炼或事实核验。',
                      '适配器位置不是稳定上游身份；身份不确定、完成状态未知和上游既有截断均保留，未观察内容不补写。',
                      '程序区域内的编辑会产生冲突；人工注释放在此区域之外。'],
               old='旧佐证快照：[[{0}]]。旧快照保留其历史含义。', current='当前观察',
               earlier='历史观察（已修订或本次不可见；不自动认定撤回）', event='事件',
               line1='角色：{role}；实际时间：{ts}；状态：{status}。',
               line2='身份依据：{basis}；完成状态：{completion}；原文哈希：{hash}。'),
    'en': dict(title='Conversation evidence', transcript='Transcript', notes='Notes',
               intro=['This page keeps the ordinary messages actually observed, with their revisions; saving it does not '
                      'mean the content was distilled into knowledge or fact-checked.',
                      'Adapter positions are not stable upstream identities; uncertain identity, unknown completion and '
                      'upstream truncation are kept as observed; nothing unobserved is filled in.',
                      'Edits inside the generated region cause a conflict; put your own notes outside it.'],
               old='Earlier evidence snapshot: [[{0}]]. It keeps its historical meaning.', current='current observation',
               earlier='earlier observation (revised or not visible now; not assumed withdrawn)', event='Event',
               line1='Role: {role}; time: {ts}; status: {status}.',
               line2='Identity basis: {basis}; completion: {completion}; source hash: {hash}.'),
}
PLATFORM = {'claude-': ('claude-code', 'claude-code-adapter-v1'), 'dsh-': ('dsh', 'dsh-session-adapter-v1')}


def note_language(text=None):
    """Language of an existing evidence note (by its title), else the vault layout's."""
    if text is not None:
        for lang, words in TEXT.items():
            if re.search('(?m)^# ' + re.escape(words['title']) + ' ', text):
                return lang
    from orimem import config
    return 'zh' if config.layout()['language'] == 'zh' else 'en'


def initialize(backend):
    with backend.connect() as c:
        c.executescript(SCHEMA)


def observe(c, session, events, model, project_required=True):
    """Called inside the source staging transaction. No file/model access."""
    sid = session['session_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
        raise ValueError('Unsafe source session locator')
    records = []
    stamp = now()
    for event in events:
        metadata = json.loads(event['metadata_json'] or '{}')
        completion = metadata.get('completion_status') or ('final_observed' if metadata.get('phase') == 'final' else 'unknown')
        upstream = metadata.get('message_id') or metadata.get('upstream_id')
        basis = 'upstream_id:'+str(upstream) if upstream else 'adapter_position_identity_uncertain'
        version = digest(json.dumps([event['ts'], event['role'], event['content'], metadata], ensure_ascii=False, sort_keys=True))
        records.append([event['event_index'], version])
        c.execute('INSERT OR IGNORE INTO capture_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (sid,event['event_index'],version,event['ts'],event['role'],event['content'],digest(event['content']),
                   json.dumps(metadata,ensure_ascii=False,sort_keys=True),basis,str(completion),stamp))
    retained = [list(r) for r in c.execute('SELECT event_index,version FROM capture_versions WHERE session_id=? ORDER BY event_index,version',(sid,))]
    # Render includes retained revisions, so returning to an old snapshot must
    # not reuse a writer batch that lacked the newly retained observations.
    signature = digest(json.dumps([records,retained]))
    prior = c.execute('SELECT * FROM capture_sessions WHERE session_id=?',(sid,)).fetchone()
    if prior and prior['snapshot_sha'] == signature:
        return signature, False
    # Stable path chosen once, independent of later titles and timestamps.
    path = prior['path'] if prior else archive_path(sid+'-continuous', events[0]['ts'] if events and events[0]['ts'] else session['created_at'])
    c.execute('INSERT OR REPLACE INTO capture_sessions VALUES(?,?,?,?,?,?)',
              (sid,session.get('title') or sid,session.get('cwd') or '',signature,path,stamp))
    c.execute('INSERT OR IGNORE INTO capture_snapshots VALUES(?,?,?,?)',(sid,signature,json.dumps(records),stamp))
    if not project_required and not c.execute('SELECT 1 FROM source_projections WHERE session_id=?',(sid,)).fetchone():
        # An unchanged legacy snapshot needs no duplicate continuous document.
        # Preserve observed raw records; its existing Markdown remains visible.
        return signature, True
    ident = digest('projection|'+sid+'|'+signature)
    payload = json.dumps(dict(path=path,processor='continuous-v1'),ensure_ascii=False)
    c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='New source observation',updated_at=? WHERE kind='projection' AND subject=? AND version<>? AND state IN ('pending','retry','running')",(stamp,sid,signature))
    c.execute('INSERT OR IGNORE INTO lifecycle_jobs(id,kind,subject,version,state,payload,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
              (ident,'projection',sid,signature,'pending',payload,stamp,stamp,stamp))
    # Returning to a previously observed snapshot still needs a current projection.
    c.execute("UPDATE lifecycle_jobs SET state='pending',attempts=0,error=NULL,available_at=?,updated_at=? WHERE id=? AND state IN ('complete','superseded')",(stamp,stamp,ident))
    index_version=digest(signature+'|'+model)
    index_id=digest('source-index|'+sid+'|'+index_version)
    c.execute('INSERT OR IGNORE INTO lifecycle_jobs(id,kind,subject,version,state,payload,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
              (index_id,'source-index',sid,index_version,'pending',json.dumps(dict(snapshot_sha=signature,model=model)),stamp,stamp,stamp))
    return signature, True


def checkpoint(c, namespace, manifest):
    """Source inventory denominator: includes eligible unchanged/empty sessions."""
    c.execute('INSERT OR REPLACE INTO capture_checkpoints VALUES(?,?,?,?,?)',
              (namespace,digest(json.dumps(manifest,sort_keys=True)),now(),len(manifest),sum(r['messages'] for r in manifest)))


def render(c, session, lang='zh'):
    words = TEXT.get(lang, TEXT['en'])
    sid = session['session_id']
    snap = c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(sid,session['snapshot_sha'])).fetchone()
    current = {int(i): v for i,v in json.loads(snap[0])}
    rows = list(c.execute('SELECT * FROM capture_versions WHERE session_id=? ORDER BY event_index,first_observed_at,version',(sid,)))
    body = [BEGIN, '## ' + words['transcript'], '', *words['intro'], '']
    old = c.execute('SELECT note_path FROM history_sessions WHERE session_id=?',(sid,)).fetchone()
    if old and old[0]:
        body += [words['old'].format(old[0].removesuffix('.md')), '']
    for r in rows:
        status = words['current'] if current.get(r['event_index']) == r['version'] else words['earlier']
        body += ['### '+words['event']+' '+str(r['event_index'])+' / '+r['version'][:12], '',
                 words['line1'].format(role=r['role'], ts=r['ts'], status=status),
                 words['line2'].format(basis=r['identity_basis'], completion=r['completion'], hash=r['content_hash']),
                 '', quote(r['content']), '']
    body += [END]
    return '\n'.join(body)


def managed(text):
    starts=list(re.finditer('(?m)^'+re.escape(BEGIN)+r'\r?$',text))
    ends=list(re.finditer('(?m)^'+re.escape(END)+r'\r?$',text))
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError('Missing or ambiguous managed projection boundary')
    start, end = starts[0].start(), ends[0].start()+len(END)
    if end < start:
        raise ValueError('Invalid managed projection boundary')
    return start,end,text[start:end]


def project(backend, limit=8):
    """LLM/embedding-free worker. Shared lease/retry queue and writer receipts."""
    owner = 'orion-projection-'+uuid.uuid4().hex
    results, errors = [], []
    for _ in range(limit):
        job = backend.lifecycle.claim('projection',owner,1800)['job']
        if job is None:
            break
        try:
            with backend.lock:
                with backend.connect() as c:
                    c.execute('BEGIN')
                    session = c.execute('SELECT * FROM capture_sessions WHERE session_id=?',(job['subject'],)).fetchone()
                    if not session or session['snapshot_sha'] != job['version']:
                        raise ValueError('Projection source changed; lease obsolete')
                    projection = c.execute('SELECT * FROM source_projections WHERE session_id=?',(job['subject'],)).fetchone()
                    intent = c.execute('SELECT managed_sha FROM projection_intents WHERE session_id=?',(job['subject'],)).fetchone()
                    path = backend.path(session['path'])
                    old = path.read_bytes() if path.exists() else None
                    lang = note_language(old.decode('utf-8', 'replace') if old is not None else None)
                    block = render(c,session,lang)
                words = TEXT[lang]
                batch = 'projection-'+job['id'][:40]
                queued = backend.state_path.parent/'queue'/(batch+'.json')
                if queued.exists():
                    plan = json.loads(queued.read_text(encoding='utf-8'))['plan']
                else:
                    sid = session['session_id']
                    if old is None:
                        # `type: codex-history` is the stable kind id of every captured conversation (any agent).
                        platform, adapter = next((v for k, v in PLATFORM.items() if sid.startswith(k)),
                                                 ('codex', 'codex-rollout-adapter-v1'))
                        text = ('---\ntype: codex-history\nprojection: '+FORMAT+'\nsession_id: '+json.dumps(sid)+
                                '\nsource_platform: '+platform+'\nsource_adapter: '+adapter+'\nreview: pending\n---\n\n# '+
                                words['title']+' '+sid+'\n\n'+block+'\n\n## '+words['notes']+'\n\n')
                    else:
                        text = old.decode('utf-8')
                        a,b,previous = managed(text)
                        previous_sha = digest(previous.replace('\r\n','\n'))
                        ours = ((projection and previous_sha == projection['managed_sha'])
                                # written by us but not registered (crash between write and registration)
                                or (intent and previous_sha == intent['managed_sha'])
                                # the database was lost or rebuilt: adopt the note this session generated earlier
                                or (projection is None and journal_writer.properties(text).get('session_id') == sid))
                        if not ours:
                            raise ValueError('Managed projection edited; preserve both versions and review conflict')
                        text = text[:a]+block+text[b:]
                    heading = next((line[2:].strip() for line in text.splitlines() if line.startswith('# ')), words['title']+' '+sid)
                    title = journal_writer.section_text(text,[heading,words['transcript']])
                    # Check the whole generated source region, not a placeholder.
                    plan = dict(batch_id=batch,through=datetime.now(journal_state.ZONE).isoformat(),events=[],
                                changes=[dict(path=session['path'],expected_sha256=digest(old) if old is not None else None,
                                              content=text,checks=[dict(section=[heading,words['transcript']],text=title.strip(),events=[])])])
                with backend.connect() as c:
                    # Write-ahead: remember what we are about to write, so a crash before registration
                    # is recognised as our own output instead of a manual edit.
                    planned = managed(plan['changes'][0]['content'])[2].replace('\r\n', '\n')
                    c.execute('INSERT OR REPLACE INTO projection_intents VALUES(?,?,?)',(job['subject'],digest(planned),now()))
                result = journal_writer.apply_plan(plan,state_path=backend.state_path,root=backend.vault,internal=True)
                receipt_path = backend.state_path.parent/'receipts'/(batch+'.json')
                receipt = journal_state.verify_receipt(receipt_path,backend.state_path,backend.vault)
                output = path.read_bytes()
                if output != plan['changes'][0]['content'].encode('utf-8'):
                    raise ValueError('Projection differs after receipt; preserve concurrent edit')
                _,_,actual = managed(output.decode('utf-8'))
                if actual.replace('\r\n','\n') != block:
                    raise ValueError('Projection receipt does not cover requested source version')
                with backend.connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    live = c.execute('SELECT * FROM lifecycle_jobs WHERE id=?',(job['id'],)).fetchone()
                    latest = c.execute('SELECT snapshot_sha FROM capture_sessions WHERE session_id=?',(job['subject'],)).fetchone()
                    current = live['state'] == 'running' and live['owner'] == owner and live['lease_until'] >= now() and latest[0] == job['version']
                    if not current:
                        # The writer already replaced the file and verified its
                        # receipt. A newer observation (or lease loss) arriving
                        # now must not leave that output unregistered, or every
                        # later projection and vault sync reads our own write
                        # as a manual edit. Register it only while the file
                        # still holds exactly these bytes (checked under the
                        # write lock); anything else stays a preserved conflict.
                        if not path.exists() or digest(path.read_bytes()) != digest(output):
                            raise ValueError('Source or projection lease changed during writer commit')
                    c.execute('DELETE FROM projection_intents WHERE session_id=?',(job['subject'],))
                    c.execute('INSERT OR REPLACE INTO source_projections VALUES(?,?,?,?,?,?,?)',
                              (job['subject'],session['path'],job['version'],digest(block),digest(output),str(receipt_path),now()))
                    # A newer verified projection replaces an older derived
                    # index intent for this managed view. Keep both receipts
                    # and all raw versions; never fail the old intent merely
                    # because its full-file hash is no longer the latest view.
                    for old_job in list(c.execute("SELECT id,subject,payload FROM lifecycle_jobs WHERE kind='index' AND state IN ('pending','retry','running','failed') AND subject LIKE 'projection-%'")):
                        expected=json.loads(old_job['payload']).get('notes',{})
                        if old_job['subject']!=batch and set(expected)=={session['path']}:
                            c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='Newer verified continuous projection',updated_at=? WHERE id=?",(now(),old_job['id']))
                    if current or (live['state'] == 'running' and live['owner'] == owner and latest[0] == job['version']):
                        c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?",(now(),job['id']))
                    elif live['state'] == 'running' and live['owner'] == owner:
                        c.execute("UPDATE lifecycle_jobs SET state='superseded',owner=NULL,lease_until=NULL,error='New source observation after verified write',updated_at=? WHERE id=?",(now(),job['id']))
                    # Otherwise observe() already superseded it, or another
                    # owner holds it; leave that job state untouched.
                results.append(dict(session_id=job['subject'],version=job['version'],path=session['path'],receipt=str(receipt_path),written=result['written'],
                                    superseded_after_write=not current))
        except Exception as error:
            try:
                backend.lifecycle.finish_job(job['id'],owner,error=str(error))
            except ValueError:
                pass
            errors.append(dict(session_id=job['subject'],error=str(error)))
    with backend.connect() as c:
        outstanding = c.execute("SELECT count(*) FROM lifecycle_jobs WHERE kind='projection' AND state IN ('pending','retry','running','failed')").fetchone()[0]
        result = dict(projected=len(results),written=sum(len(r['written']) for r in results),results=results,errors=errors,outstanding=outstanding,at=now())
        c.execute("INSERT OR REPLACE INTO meta VALUES('last_projection',?)",(json.dumps(result,ensure_ascii=False),))
    backend.lifecycle.health_stage('projection',str(errors) if errors else None)
    return result
