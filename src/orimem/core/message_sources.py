"""Select a canonical message for existing read/ack/review lifecycle APIs.

Register only explicitly selected messages, not a second source ledger or one
automatic synthesis task for every captured message.
"""
import json
from .lifecycle import stamp

def current(backend,source_key):
    try:
        sid,ident=source_key.removeprefix('message:').rsplit(':',1)
        index=int(ident)
        if not source_key.startswith('message:') or index<0:raise ValueError()
    except (ValueError,AttributeError):raise ValueError('Invalid canonical message locator') from None
    with backend.connect() as c:
        session=c.execute('SELECT * FROM capture_sessions WHERE session_id=?',(sid,)).fetchone()
        if not session:raise ValueError('Canonical source not captured')
        snap=c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(sid,session['snapshot_sha'])).fetchone()
        version=next((v for i,v in json.loads(snap[0]) if i==index),None)
        row=c.execute('SELECT * FROM capture_versions WHERE session_id=? AND event_index=? AND version=?',(sid,index,version)).fetchone() if version else None
    if not row:raise ValueError('Canonical message is not in current observation')
    return dict(row),session['path']

def select(lifecycle,session_id,event_index,scope):
    backend=lifecycle.backend;scope=backend.scope(scope)
    key='message:'+session_id+':'+str(event_index)
    message,path=current(backend,key)
    if not backend.in_scope(path,scope):raise ValueError('Canonical message source is out of scope')
    with backend.connect() as c:
        prior=c.execute('SELECT version FROM source_versions WHERE source_key=? AND current=1',(key,)).fetchone()
        c.execute('UPDATE source_versions SET current=0 WHERE source_key=? AND version<>?',(key,message['version']))
        c.execute('INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?,NULL,NULL)',
                  (key,message['version'],path,'message',len(message['content']),1,stamp()))
        c.execute("UPDATE source_versions SET current=1,state='message',path=? WHERE source_key=? AND version=?",(path,key,message['version']))
    if prior and prior[0]!=message['version']:lifecycle.invalidate(key,'canonical message revised',except_version=message['version'])
    return dict(source_key=key,version=message['version'],path=path,chars=len(message['content']),
                role=message['role'],event_time=message['ts'],identity_basis=message['identity_basis'],completion=message['completion'],
                upstream_truncated=bool(json.loads(message['metadata']).get('content_filter',{}).get('truncated')),
                contains_truncation_marker='[TRUNCATED' in message['content'],completeness='observed-interface-content')

def reconcile(lifecycle):
    with lifecycle.backend.connect() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM source_versions WHERE current=1 AND state='message'")]
    changed=0
    for r in rows:
        try:
            m,path=current(lifecycle.backend,r['source_key'])
            if m['version']==r['version']:continue
            with lifecycle.backend.connect() as c:c.execute("UPDATE source_versions SET current=0,state='revised' WHERE source_key=? AND version=?",(r['source_key'],r['version']))
            lifecycle.invalidate(r['source_key'],'canonical message revised',except_version=m['version'])
        except ValueError:
            lifecycle.invalidate(r['source_key'],'canonical message missing from current observation')
        changed+=1
    return dict(canonical_sources_changed=changed)
