"""Current/historical intent and exact safe views of mixed evidence chunks.

Withhold an affected paragraph, not every unrelated assertion under a shared
heading. Safe views address original bytes/characters; no invented summaries or
new source/vector copies are introduced.
"""
import json,re
from datetime import datetime,timezone
from orimem import journal_writer
from .corrections import active
from .lifecycle import stamp

class Policy:
    def __init__(self,backend,c,intent='current'):
        if intent not in {'current','historical'}:raise ValueError('Explicit current/historical time intent required')
        self.backend=backend;self.c=c;self.intent=intent;self.paths={};self.source_effects={};self.note_basis={};self.archive_revisions={}
        self.has_reviews=bool(c.execute("SELECT 1 FROM knowledge_dependencies WHERE status='review-required' LIMIT 1").fetchone())
        self.has_corrections=bool(c.execute('SELECT 1 FROM corrections LIMIT 1').fetchone())
        self.has_temporal=bool(c.execute("SELECT 1 FROM knowledge_dependencies WHERE status='current' AND (valid_from IS NOT NULL OR valid_to IS NOT NULL) LIMIT 1").fetchone())

    def details(self,path):
        if path not in self.paths:
            reviewed=[dict(r) for r in self.c.execute("SELECT * FROM knowledge_dependencies WHERE path=? AND status='review-required'",(path,))]
            corrections=active(self.c,path)
            historical=[dict(r,heading=json.loads(r['heading'])) for r in self.c.execute('SELECT h.heading,r.kind,r.effective_at FROM correction_history_sections h JOIN corrections r ON r.id=h.correction_id WHERE h.path=?',(path,))]
            timed=[dict(r) for r in self.c.execute("SELECT * FROM knowledge_dependencies WHERE path=? AND status='current' AND (valid_from IS NOT NULL OR valid_to IS NOT NULL)",(path,))]
            self.paths[path]=(reviewed,corrections,historical,timed)
        return self.paths[path]

    def source_quotes(self,key,version):
        """Only effects referring to the already scoped original source."""
        cache=(key,version)
        if cache not in self.source_effects:
            self.source_effects[cache]=[(r['source_quote'],r['source_start'],r['source_end'],r['correction_id']) for r in self.c.execute("SELECT t.source_quote,t.source_start,t.source_end,t.correction_id FROM correction_targets t JOIN corrections r ON r.id=t.correction_id WHERE t.source_key=? AND t.source_version=? AND t.source_quote IS NOT NULL AND t.state IN ('open','resolved') AND (r.effective_at IS NULL OR r.effective_at<=?)",(key,version,stamp()))]
        return self.source_effects[cache]

    def revised_archive_origins(self,row):
        """Pinned archived origins do not vanish when their file SHA changes."""
        key=(row['path'],row['sha'])
        if key not in self.archive_revisions:
            origins=[dict(r) for r in self.c.execute("SELECT DISTINCT t.heading,t.correction_id FROM correction_targets t JOIN corrections r ON r.id=t.correction_id JOIN source_versions s ON s.source_key=t.source_key AND s.version=t.source_version WHERE t.path=? AND s.path=t.path AND s.state='archived' AND t.source_quote IS NOT NULL AND t.source_version<>? AND t.state IN ('open','resolved') AND (r.effective_at IS NULL OR r.effective_at<=?)",(row['path'],row['sha'],stamp()))]
            spans=[]
            if origins:
                raw=self.backend.path(row['path']).read_text(encoding='utf-8')
                for origin in origins:
                    try:a,b=journal_writer.section_span(raw,json.loads(origin['heading']))
                    except ValueError:
                        # If the pinned message boundary was renamed/removed,
                        # relocation needs review rather than guessing identity.
                        a,b=0,len(raw)
                    spans.append((a,b,origin['correction_id']))
            self.archive_revisions[key]=spans
        return sorted({ident for a,b,ident in self.archive_revisions[key] if a<row['end'] and row['start']<b})

    def views(self,row):
        row=dict(row)
        # Explicit archival disposition is separate from semantic retraction:
        # retain the exact old text, but never use this note as current basis.
        # Canonical messages do not inherit a project/summary note's policy.
        if row['kind'] not in {'pending','source'}:
            path=row['path']
            if path not in self.note_basis:
                note=self.c.execute('SELECT props FROM notes WHERE path=?',(path,)).fetchone()
                self.note_basis[path]=json.loads(note['props']) if note else {}
            props=self.note_basis[path]
            if props.get('retrieval_basis')=='historical-only':
                if self.intent=='current':return []
                row.update(basis_status='retired-note-history',not_current_basis=True,
                           retirement_recorded_at=props.get('legacy_retired_at'))
        if not self.has_reviews and not self.has_corrections and not self.has_temporal:return [dict(row)]
        if row['kind'] not in {'pending','source'}:
            revisions=self.revised_archive_origins(row)
            if revisions:
                if self.intent=='current':return []
                row.update(basis_status='corrected-origin-version-needs-review',correction_ids=revisions,not_current_basis=True)
        row=dict(row);reviewed,corrections,historical,timed=self.details(row['path'])
        historical_matches=[h for h in historical if row['heading']==' / '.join(h['heading']) or row['heading'].startswith(' / '.join(h['heading'])+' / ')]
        historical_heading=bool(historical_matches)
        effects=[];ranges=[];fallback=False
        if row['kind']=='source':
            key='message:'+row['session_id']+':'+str(row['event_index'])
            effects=self.source_quotes(key,row['message_version'])
            prior_origins=list(self.c.execute("SELECT DISTINCT t.correction_id FROM correction_targets t JOIN corrections r ON r.id=t.correction_id WHERE t.source_key=? AND t.source_version<>? AND t.source_quote IS NOT NULL AND t.state IN ('open','resolved') AND (r.effective_at IS NULL OR r.effective_at<=?)",(key,row['message_version'],stamp())))
            if prior_origins and not effects:
                if self.intent=='current':return []
                row.update(basis_status='corrected-origin-version-needs-review',correction_ids=[r[0] for r in prior_origins],not_current_basis=True)
            corrective=self.c.execute('SELECT id,kind,quote,start,end,effective_at FROM corrections WHERE source_key=? AND source_version=? ORDER BY recorded_at DESC LIMIT 1',(key,row['message_version'])).fetchone()
            if corrective:
                row.update(basis_status='correction-source',correction_ids=[corrective['id']],correction_kind=corrective['kind'],
                           corrective_quote=corrective['quote'],corrective_start=corrective['start'],corrective_end=corrective['end'],
                           requires_context=not (row['start']<=corrective['start'] and corrective['end']<=row['end']),
                           source_also_corrected=bool(effects))
        elif row['kind']=='codex-history':
            for s in self.c.execute("SELECT source_key,version FROM source_versions WHERE path=? AND state='archived' AND version=?",(row['path'],row['sha'])):
                effects.extend(self.source_quotes(s['source_key'],s['version']))
                corrective=self.c.execute('SELECT id,kind,quote,start,end,effective_at FROM corrections WHERE source_key=? AND source_version=? AND start<? AND ?<end ORDER BY recorded_at DESC LIMIT 1',
                    (s['source_key'],s['version'],row['end'],row['start'])).fetchone()
                if corrective:
                    row.update(basis_status='correction-source',correction_ids=[corrective['id']],correction_kind=corrective['kind'],
                        corrective_quote=corrective['quote'],corrective_start=corrective['start'],corrective_end=corrective['end'],
                        requires_context=not (row['start']<=corrective['start'] and corrective['end']<=row['end']),source_also_corrected=bool(effects))
        if self.intent=='historical':
            if reviewed or corrections or historical_heading or effects:
                row['basis_status']='historical-or-under-review'
                row['correction_ids']=sorted(set(row.get('correction_ids',[]))|{t['correction_id'] for t in corrections}|{e[3] for e in effects})
            return [row]
        now=instant(stamp())
        constraints=[dict(claim=d['claim'],valid_from=d['valid_from'],valid_to=d['valid_to']) for d in timed if d['claim'] in row['text']]
        if constraints:row['temporal_constraints']=constraints
        if historical_heading:
            if all(h['kind']=='supersession' and h['effective_at'] and now<instant(h['effective_at']) for h in historical_matches):
                row.update(basis_status='valid-before-effective-change',valid_to=min(h['effective_at'] for h in historical_matches))
            else:return []
        if row['kind'] not in {'pending','source'}:
            explicit_ids={t['dependency_id'] for t in corrections}
            claims=[(t['claim'],None,t['correction_id']) for t in corrections]
            for dep in reviewed:
                if self.backend.lifecycle.dependency_id(dep) not in explicit_ids:
                    claims.append((dep['claim'],json.loads(dep['heading']),None))
            for dep in timed:
                if (dep['valid_from'] and now<instant(dep['valid_from'])) or (dep['valid_to'] and now>=instant(dep['valid_to'])):
                    claims.append((dep['claim'],json.loads(dep['heading']),None))
            if claims:
                raw=self.backend.path(row['path']).read_text(encoding='utf-8')
                for claim,heading,ident in claims:
                    if heading:
                        address=' / '.join(heading)
                        if row['heading']!=address and not row['heading'].startswith(address+' / '):continue
                        try:a,b=journal_writer.section_span(raw,heading)
                        except ValueError:
                            fallback=True;continue
                        positions=[(a+m.start(),a+m.end()) for m in re.finditer(re.escape(claim),raw[a:b])]
                        if not positions:fallback=True
                    else:
                        # Registered corrections follow retained copies in the
                        # same target, even if moved to an undeclared heading.
                        positions=[(m.start(),m.end()) for m in re.finditer(re.escape(claim),raw)]
                    ranges.extend((a,b,ident) for a,b in positions)
        for quote,a,b,ident in effects:
            if row['kind']=='source':
                lo,hi=max(row['start'],a),min(row['end'],b)
                if lo<hi and row['text'][lo-row['start']:hi-row['start']]==quote[lo-a:hi-a]:
                    ranges.append((a,b,ident))
                elif quote in row['text']:
                    # A fragment not at its pinned source interval is never
                    # treated as that message merely because text is similar.
                    continue
            else:
                ranges.append((a,b,ident))
        hit=[(a,b,ident) for a,b,ident in ranges if a<row['end'] and row['start']<b]
        if fallback:return []
        if not hit:return [row]
        # Paragraph boundaries retain conditions as a unit. Mixed rows expose
        # only unaffected original paragraphs under deterministic safe IDs.
        pieces=[]
        # A blank line inside a quoted archive is `> `, not an unquoted blank.
        # Keep its original offsets while preserving unrelated paragraphs.
        for match in re.finditer(r'[^\n]*(?:\n(?![ \t]*(?:>[ \t]*)*(?:\n|$))[^\n]*)*',row['text']):
            if not match.group(0).strip():continue
            if re.fullmatch(r'[ \t]*(?:>[ \t]*)+',match.group(0)):continue
            a=row['start']+match.start();b=row['start']+match.end()
            if any(x<b and a<y for x,y,_ in hit):continue
            text=match.group(0)
            r=dict(row,id='safe:'+row['id']+':'+str(a)+':'+str(b),origin_id=row['id'],text=text,start=a,end=b,
                   basis_status='unaffected-paragraph',correction_ids=sorted({i for _,_,i in hit if i}))
            pieces.append(r)
        return pieces

def safe_locator(ident):
    match=re.fullmatch(r'safe:([A-Za-z0-9_-]{1,100}):(\d+):(\d+)',ident)
    if not match:raise ValueError('Invalid safe evidence locator')
    return match[1],int(match[2]),int(match[3])

def instant(value):
    time=datetime.fromisoformat(value.replace('Z','+00:00'))
    if time.tzinfo is None:raise ValueError('Dependency time requires explicit timezone; review metadata')
    return time.astimezone(timezone.utc)
