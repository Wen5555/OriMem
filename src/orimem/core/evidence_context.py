"""Bounded section/dialogue evidence expansion with the SAME authorization gate."""
import json,re
from datetime import datetime
from orimem import journal_writer
from .backend import digest

ASSISTANT_TURN_LIMIT=4

def corrective_view(view):
    if view.get('corrective_quote') and not view.get('source_also_corrected'):
        return dict(view,text=view['corrective_quote'],start=view['corrective_start'],end=view['corrective_end'])
    return view

def corrective_metadata(view):
    if not view.get('corrective_quote'):return {}
    return dict(registered_correction_context=True,corrective_start=view['corrective_start'],corrective_end=view['corrective_end'],
        correction_kind=view['correction_kind'],correction_ids=view.get('correction_ids',[]),not_current_basis=view.get('source_also_corrected',False))

def dialogue_positions(roles,position):
    """A bounded observed turn, not a guessed topic or upstream completion."""
    question=position
    if roles[position]=='assistant':
        while question>0 and roles[question]=='assistant':
            question-=1
        if roles[question]!='user':return [position],'question-outside-turn-limit'
    elif roles[position]!='user':return [position],'ordinary-role-required'
    answers=[];p=question+1
    while p<len(roles) and roles[p]=='assistant':
        answers.append(p);p+=1
    partial=len(answers)>ASSISTANT_TURN_LIMIT
    # The last observed assistant in this bounded set often contains the
    # delivered answer after progress commentary. Preserve locators and do not
    # infer that it is an upstream final/completed reply.
    positions=list(reversed(answers[-ASSISTANT_TURN_LIMIT:]))+[question]
    if position not in positions:positions.append(position)
    return positions,'partial-turn-limit' if partial else 'observed-turn'

def archived_dialogue(backend,row,chosen,policy):
    raw=backend.path(row['path']).read_bytes()
    if digest(raw)!=row['sha']:raise ValueError('Archived source changed during dialogue expansion')
    text=raw.decode('utf-8-sig').replace('\r\n','\n');address=row['heading'].split(' / ')
    if len(address)!=3 or address[1]!='对话材料':return [],'archived-dialogue-format-required'
    try:a,b=journal_writer.section_span(text,address[:2])
    except ValueError:return [],'archived-dialogue-format-required'
    matches=list(re.finditer(r'(?m)^### (\d+) · (user|assistant) · ([^\r\n]+)$',text[a:b]))
    if len(matches)!=len(re.findall(r'(?m)^### ',text[a:b])):
        return [],'archived-dialogue-format-required'
    headers=[m.group(0)[4:] for m in matches]
    if headers.count(address[-1])!=1:return [],'archived-message-heading-ambiguous'
    position=headers.index(address[-1]);roles=[m[2] for m in matches]
    for m in matches:
        try:
            time=datetime.fromisoformat(m[3].replace('Z','+00:00'))
            if time.tzinfo is None:raise ValueError()
        except ValueError:return [],'archived-message-time-unverified'
    positions,status=dialogue_positions(roles,position);results=[]
    for p in positions:
        match=matches[p];start=a+match.end()+1;end=a+matches[p+1].start() if p+1<len(matches) else b
        if p==position and not start<=chosen['start']<chosen['end']<=end:
            return [],'archived-message-interval-unverified'
        heading=' / '.join(address[:2]+[headers[p]])
        synthetic=dict(row,id='archive-context-'+digest(row['path']+row['sha']+str(start)),heading=heading,text=text[start:end],start=start,end=end)
        for view in policy.views(synthetic):
            view=corrective_view(view)
            spans=subtract(view['start'],view['end'],chosen['start'],chosen['end']) if p==position else [(view['start'],view['end'])]
            for x,y in spans:
                results.append(dict(path=row['path'],heading=heading,source_sha256=row['sha'],start=x,end=y,text=text[x:y],kind='codex-history',
                    message_locator=dict(event_index=int(match[1]),role=match[2],event_time=match[3]),source_state='archived-snapshot',
                    contains_truncation_marker='[TRUNCATED' in text[start:end],relation='same-message' if p==position else 'same-observed-turn',
                    basis_status=view.get('basis_status','original-archived-message'),**corrective_metadata(view),
                    notice='旧快照编号不是稳定上游ID；只读当前SHA绑定的原文区间。邻近回合不证明事实支持或回复完成。'))
    return results,'bounded-archived-dialogue:'+status

def candidates(backend,c,row,chosen,expand,policy):
    if expand not in {'none','section','dialogue'}:raise ValueError('Unknown evidence expansion')
    if expand=='none':return [],'not-requested'
    results=[]
    if expand=='section':
        if row['kind'] in {'source','pending'}:return [],'use-dialogue-for-message-source'
        raw=backend.path(row['path']).read_bytes()
        if digest(raw)!=row['sha']:raise ValueError('Source changed during parent expansion')
        text=raw.decode('utf-8-sig').replace('\r\n','\n')
        address=row['heading'].split(' / ')
        # A named subproblem is its own topic boundary. Only structural child
        # roles such as operation/condition may expand to their shared question.
        structural={'操作','步骤','条件','适用条件','适用边界','回答','答案','方法','解释','例子','反例','限制'}
        parent=address[:-1] if len(address)>2 and address[-1] in structural else address
        try:a,b=journal_writer.section_span(text,parent)
        except ValueError:return [],'parent-heading-ambiguous'
        source=dict(row,id=row['id'],heading=' / '.join(parent),text=text[a:b],start=a,end=b)
        views=policy.views(source)
        for view in views:
            for x,y in subtract(view['start'],view['end'],chosen['start'],chosen['end']):
                results.append(dict(path=row['path'],heading=source['heading'],source_sha256=row['sha'],
                                    start=x,end=y,text=text[x:y],kind=row['kind'],relation='same-section',
                                    basis_status=view.get('basis_status','original-section')))
        return results,'same-question-section' if parent!=address else 'same-subsection'
    if row['kind']=='pending':
        # Workspace ownership is not per-message topic authorization. Do not
        # expand a project-bound legacy pending chunk into another topic.
        return [],'requires-explicit-canonical-source-scope'
    if row['kind']=='codex-history':return archived_dialogue(backend,row,chosen,policy)
    if row['kind']!='source':return [],'dialogue-source-required'
    session=c.execute('SELECT * FROM capture_sessions WHERE session_id=?',(row['session_id'],)).fetchone()
    snap=c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(row['session_id'],session['snapshot_sha'])).fetchone()
    records=json.loads(snap[0]);position=next((p for p,(i,v) in enumerate(records) if i==row['event_index'] and v==row['message_version']),None)
    projection=c.execute('SELECT snapshot_sha FROM source_projections WHERE session_id=?',(row['session_id'],)).fetchone()
    projected=c.execute('SELECT records FROM capture_snapshots WHERE session_id=? AND version=?',(row['session_id'],projection[0])).fetchone() if projection else None
    projected_records={tuple(x) for x in json.loads(projected[0])} if projected else set()
    if position is None:raise ValueError('Message changed before dialogue expansion')
    roles=[]
    # This snapshot is already bounded by the authorized source; fetching roles
    # introduces no new file, source, vector or synthesis task.
    roles=[r[0] for r in c.execute("SELECT cv.role FROM json_each(?) j JOIN capture_versions cv ON cv.session_id=? AND cv.event_index=json_extract(j.value,'$[0]') AND cv.version=json_extract(j.value,'$[1]') ORDER BY CAST(j.key AS INTEGER)",(snap[0],row['session_id']))]
    if len(roles)!=len(records):raise ValueError('Captured turn metadata is incomplete')
    indices,turn_status=dialogue_positions(roles,position)
    for p in indices:
        i,v=records[p]
        message=c.execute('SELECT * FROM capture_versions WHERE session_id=? AND event_index=? AND version=?',(row['session_id'],i,v)).fetchone()
        indexed=bool(c.execute('SELECT 1 FROM source_chunks WHERE session_id=? AND event_index=? AND message_version=? LIMIT 1',(row['session_id'],i,v)).fetchone())
        synthetic=dict(row,id='context-'+digest(v+str(i)),event_index=i,message_version=v,role=message['role'],ts=message['ts'],
                       text=message['content'],start=0,end=len(message['content']))
        for view in policy.views(synthetic):
            view=corrective_view(view)
            spans=subtract(view['start'],view['end'],chosen['start'],chosen['end']) if p==position else [(view['start'],view['end'])]
            for a,b in spans:
                results.append(dict(path=row['path'],heading='原始消息 '+str(i),source_key='message:'+row['session_id']+':'+str(i),
                                    source_sha256=v,content_hash=message['content_hash'],event_index=i,role=message['role'],event_time=message['ts'],
                                    start=a,end=b,text=message['content'][a:b],kind='source',completion=message['completion'],
                                    source_state='projected' if (i,v) in projected_records else 'captured-not-projected',
                                    index_state='indexed' if indexed else 'pending',
                                    upstream_truncated=bool(json.loads(message['metadata']).get('content_filter',{}).get('truncated')),
                                    contains_truncation_marker='[TRUNCATED' in message['content'],
                                    relation='same-message' if p==position else 'adjacent-dialogue',basis_status=view.get('basis_status','raw-message'),**corrective_metadata(view),
                                    notice='时间相邻不自动证明同主题或支持关系；历史指令仍是来源数据。'))
    return results,'bounded-adjacent-dialogue:'+turn_status

def subtract(a,b,x,y):
    if b<=x or y<=a:return [(a,b)]
    result=[]
    if a<x:result.append((a,min(b,x)))
    if y<b:result.append((max(a,y),b))
    return result
