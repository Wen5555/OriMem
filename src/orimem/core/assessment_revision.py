"""Receipt-backed revision of a synthesis result after its body was written.

The caller supplies the semantic decision. No timestamp, embedding score or
queue transition can promote a candidate on its own.
"""
import json
import re
import uuid

from orimem import journal_state
from orimem import journal_writer
from .lifecycle import sha, stamp


def assessment(lifecycle, source_key, version):
    with lifecycle.backend.connect() as c:
        row = c.execute('SELECT * FROM synthesis_results WHERE source_key=? AND version=?',
                        (source_key, version)).fetchone()
    if not row:
        raise ValueError('No prior assessment exists')
    value = dict(row)
    return dict(assessment=value, assessment_sha256=sha(json.dumps(value, ensure_ascii=False, sort_keys=True)))


def revise(lifecycle, revision_id, source_key, version, expected_assessment_sha256,
           disposition, reason, comparison, claims, receipt_batch, retirements=None):
    """Finalize an inspected body/index change, atomically preserving its prior.

    Removed assertions require an explicitly checked replacement statement in
    the same receipt, and must be absent from their old current heading. A
    historical paragraph or candidate's promoted-link stub may retain context.
    """
    backend = lifecycle.backend
    if not isinstance(revision_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', revision_id):
        raise ValueError('Valid immutable revision ID required')
    if not isinstance(receipt_batch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', receipt_batch):
        raise ValueError('Valid writer receipt batch required')
    retirements = retirements or []
    if not isinstance(retirements, list) or len(retirements) > 60:
        raise ValueError('Retirements must be a bounded list')
    request = dict(source_key=source_key, version=version,
                   expected_assessment_sha256=expected_assessment_sha256,
                   disposition=disposition, reason=reason, comparison=comparison,
                   claims=claims, receipt_batch=receipt_batch, retirements=retirements)
    request_sha = sha(json.dumps(request, ensure_ascii=False, sort_keys=True))
    with backend.connect() as c:
        done = c.execute('SELECT * FROM assessment_resolutions WHERE id=?', (revision_id,)).fetchone()
    if done:
        if done['request_sha'] != request_sha:
            raise ValueError('Completed revision ID cannot change content')
        return dict(revision_id=revision_id, source_key=source_key, disposition=done['disposition'], idempotent=True)

    lifecycle.validate_assessment(source_key, version, disposition, reason, comparison, claims)
    prior = assessment(lifecycle, source_key, version)
    if prior['assessment_sha256'] != expected_assessment_sha256:
        raise ValueError('Prior assessment changed; compare again')
    receipt_path = backend.state_path.parent / 'receipts' / (receipt_batch + '.json')
    receipt = journal_state.verify_receipt(receipt_path, backend.state_path, backend.vault)
    notes = {n['path']: n for n in receipt['notes']}
    snapshots = {}

    def checked_statement(path, heading, text):
        if not isinstance(text, str) or not text.strip() or not isinstance(heading, list) or not heading:
            raise ValueError('An exact nonempty checked statement is required')
        raw = backend.path(path).read_bytes()
        body = raw.decode('utf-8-sig')
        note = notes.get(path)
        if not note or note['sha256'] != sha(raw) or not any(
            check['section'] == heading and check['text_sha256'] == sha(text)
            for check in note['checks']
        ) or text not in journal_writer.section_text(body, heading):
            raise ValueError('Receipt does not bind the exact current statement: ' + path)
        with backend.connect() as c:
            indexed = c.execute('SELECT sha FROM notes WHERE path=?', (path,)).fetchone()
        if not indexed or indexed[0] != sha(raw):
            raise ValueError('Revision body has not been indexed: ' + path)
        snapshots[path] = sha(raw)
        return body

    for claim in claims:
        checked_statement(claim['path'], claim['heading'], claim['text'])
    with backend.connect() as c:
        old_deps = [dict(r) for r in c.execute(
            "SELECT * FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND status IN ('current','review-required','write-pending')",
            (source_key, version))]
    new_identity = {(x['path'], json.dumps(x['heading'], ensure_ascii=False), x['text']) for x in claims}
    removed = [d for d in old_deps if (d['path'], d['heading'], d['claim']) not in new_identity]
    retirement_by_id = {}
    for item in retirements:
        ident = item.get('dependency_id')
        if not ident or ident in retirement_by_id:
            raise ValueError('Duplicate or missing retirement dependency ID')
        retirement_by_id[ident] = item
    if set(retirement_by_id) != {lifecycle.dependency_id(d) for d in removed}:
        raise ValueError('Every removed dependency requires exactly one explicit retirement')
    for old in removed:
        item = retirement_by_id[lifecycle.dependency_id(old)]
        checked_statement(item['path'], item['heading'], item['text'])
        # File removal is deliberately not accepted as an implicit correction.
        raw = backend.path(old['path']).read_bytes()
        if old['claim'] in journal_writer.section_text(raw.decode('utf-8-sig'), json.loads(old['heading'])):
            raise ValueError('Old current assertion remains; move it to history or replace its section')
        if old['path'] not in notes or notes[old['path']]['sha256'] != sha(raw):
            raise ValueError('Retired target must also be verified in this writer receipt')
        snapshots[old['path']] = sha(raw)

    new_ids = [lifecycle.dependency_id(dict(source_key=source_key, source_version=version,
                 path=x['path'], heading=json.dumps(x['heading'], ensure_ascii=False), claim=x['text'])) for x in claims]
    with backend.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT * FROM synthesis_results WHERE source_key=? AND version=?', (source_key, version)).fetchone()
        if not current or sha(json.dumps(dict(current), ensure_ascii=False, sort_keys=True)) != expected_assessment_sha256:
            raise ValueError('Assessment changed during revision')
        lifecycle._source(source_key, version)
        for path, expected in snapshots.items():
            indexed = c.execute('SELECT sha FROM notes WHERE path=?', (path,)).fetchone()
            if not indexed or indexed[0] != expected or sha(backend.path(path).read_bytes()) != expected:
                raise ValueError('Body/index changed during revision')
        live_deps = [dict(r) for r in c.execute(
            "SELECT * FROM knowledge_dependencies WHERE source_key=? AND source_version=? AND status IN ('current','review-required','write-pending')",
            (source_key, version))]
        if sorted(json.dumps(x, sort_keys=True) for x in live_deps) != sorted(json.dumps(x, sort_keys=True) for x in old_deps):
            raise ValueError('Dependencies changed during revision')
        c.execute('INSERT INTO synthesis_revisions VALUES(?,?,?,?,?,?)',
                  (uuid.uuid4().hex, source_key, version, json.dumps(dict(current), ensure_ascii=False), reason, stamp()))
        for old in removed:
            identity = tuple(old[k] for k in ['source_key','source_version','path','heading','claim'])
            c.execute("UPDATE knowledge_dependencies SET status='superseded' WHERE source_key=? AND source_version=? AND path=? AND heading=? AND claim=?", identity)
            ident = lifecycle.dependency_id(old)
            c.execute('INSERT INTO dependency_resolutions VALUES(?,?,?,?,?,?,?,?,?)',
                      (sha('assessment|' + revision_id + '|' + ident), ident, 'superseded', reason,
                       json.dumps(old, ensure_ascii=False), json.dumps(new_ids), str(receipt_path), snapshots[old['path']], stamp()))
        for x in claims:
            c.execute('INSERT OR REPLACE INTO knowledge_dependencies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (source_key, version, x['path'], json.dumps(x['heading'], ensure_ascii=False), x['text'],
                       x['strength'], x.get('valid_from'), x.get('valid_to'), stamp(), str(receipt_path), snapshots[x['path']], 'current'))
        c.execute('INSERT OR REPLACE INTO synthesis_results VALUES(?,?,?,?,?,?,?)',
                  (source_key, version, disposition, reason, json.dumps(comparison, ensure_ascii=False), json.dumps(claims, ensure_ascii=False), stamp()))
        c.execute('UPDATE source_versions SET result=?,reason=? WHERE source_key=? AND version=?', (disposition, reason, source_key, version))
        c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE kind='synthesis' AND subject=? AND version=?", (stamp(), source_key, version))
        for old in removed:
            if not c.execute("SELECT 1 FROM knowledge_dependencies WHERE path=? AND status='review-required'", (old['path'],)).fetchone():
                c.execute("UPDATE lifecycle_jobs SET state='complete',owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE kind='review' AND subject=? AND state IN ('pending','retry','running')", (stamp(), old['path']))
        c.execute('INSERT INTO assessment_resolutions VALUES(?,?,?,?,?,?,?,?)',
                  (revision_id, request_sha, source_key, version, disposition, str(receipt_path), json.dumps(dict(current), ensure_ascii=False), stamp()))
    return dict(revision_id=revision_id, source_key=source_key, disposition=disposition, idempotent=False,
                claims=len(claims), superseded_dependencies=len(removed))
