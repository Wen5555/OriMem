"""Unique source intervals within one current-policy evidence budget."""
from .evidence_context import subtract

def identity(piece):
    return (piece['path'],piece['source_sha256'],piece.get('source_key'),piece.get('event_index'))

def unseen(piece,seen):
    key=identity(piece);intervals=[(piece['start'],piece['end'])]
    for left,right in seen.get(key,[]):
        intervals=[part for a,b in intervals for part in subtract(a,b,left,right)]
    return intervals

def novel(piece,seen,budget):
    key=identity(piece);intervals=unseen(piece,seen)
    result=[];removed=len(piece['text'])-sum(b-a for a,b in intervals)
    for a,b in intervals:
        if budget<=0:break
        size=min(b-a,budget);item=dict(piece,start=a,end=a+size,text=piece['text'][a-piece['start']:a-piece['start']+size],truncated=piece.get('truncated',False) or size<b-a)
        result.append(item);budget-=size;seen.setdefault(key,[]).append((a,a+size))
    return result,removed

def pack(backend,ids,scope,max_chars,time_intent,expand):
    if not isinstance(ids,list) or not 1<=len(ids)<=8 or not 500<=max_chars<=24000:raise ValueError('Invalid evidence budget')
    # Validate the whole authorization once; native reads independently enforce
    # source SHA, current/historical policy and the same exact scope.
    scope=backend.scope(scope);remaining=max_chars;seen={};items=[];skipped=[];removed=0;incomplete=[]
    for ident in ids:
        if remaining<=0:break
        value=backend._evidence_raw([ident],scope,max_chars=24000,time_intent=time_intent,expand=expand)['evidence'][0]
        if value.get('expansion')=='registered-correction-quote' and sum(b-a for a,b in unseen(value,seen))>remaining:
            incomplete.append(dict(evidence_id=ident,requires_context=True,not_current_basis=True,reason='Registered correction quote cannot fit as a whole'));continue
        primary=dict(value);primary.pop('context',None)
        primary_parts,count=novel(primary,seen,remaining);removed+=count
        partial=sum(len(x['text']) for x in primary_parts)<len(primary['text'])-count
        contexts=[]
        for part in primary_parts:remaining-=len(part['text'])
        for context in value.get('context',[]):
            context=dict(context,path=context.get('path',value['path']),source_sha256=context.get('source_sha256',value['source_sha256']))
            # Canonical message offsets belong to that message, not the file.
            if value.get('kind') in {'source','pending'}:
                context.setdefault('source_key',value.get('source_key'))
            if context.get('registered_correction_context') and (context.get('requires_context') or sum(b-a for a,b in unseen(context,seen))>remaining):
                incomplete.append(dict(evidence_id=ident,requires_context=True,not_current_basis=True,reason='Registered correction context cannot fit as a whole'))
                partial=True
                continue
            fresh,count=novel(context,seen,remaining);removed+=count
            partial=partial or sum(len(x['text']) for x in fresh)<len(context['text'])-count
            contexts.extend(fresh);remaining-=sum(len(x['text']) for x in fresh)
        if not primary_parts and not contexts:skipped.append(ident);continue
        item=dict(value,text=primary_parts[0]['text'] if primary_parts else '',start=primary_parts[0]['start'] if primary_parts else value['start'],end=primary_parts[0]['end'] if primary_parts else value['start'])
        item['truncated']=bool(primary_parts and primary_parts[0]['truncated'])
        item['context']=[*primary_parts[1:],*contexts]
        if partial:item['context_status']='packed:partial-budget'
        items.append(item)
    return dict(evidence=items,omitted=len(ids)-len(items),deduplicated_ids=skipped,deduplicated_chars=removed,
        evidence_chars=max_chars-remaining,packing='unique-source-intervals-v1',incomplete=incomplete)
