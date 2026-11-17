"""Projection race regressions: a source observation arriving while the writer commits."""
from unittest.mock import patch

SID = 'claude-race-0000'


def _write(db, text):
    from orimem.capture.common import write_session
    events = [dict(ts='2026-11-17T01:00:00Z', role='user', content='第一条消息', metadata={}),
              dict(ts='2026-11-17T01:00:05Z', role='assistant', content=text, metadata={'phase': 'final_answer'})]
    write_session(db, SID, db.parent / 'x.jsonl', dict(cwd='D:/proj', title='race', first_user_message='第一条消息'), events, {}, 'claude', 'anthropic')


def _setup(env):
    from orimem.capture.common import adapter_db, capture_backend
    from orimem.core.source_stage import stage_sources
    from orimem.core.continuous import project
    backend, db = capture_backend(), adapter_db('claude')
    _write(db, '初始版本')
    stage_sources(backend, db, namespace='claude')
    assert not project(backend)['errors']
    with backend.connect() as c:
        rel = c.execute('SELECT path FROM capture_sessions WHERE session_id=?', (SID,)).fetchone()[0]
    return backend, db, env['vault'] / rel


def _row(backend, table):
    with backend.connect() as c:
        return dict(c.execute(f'SELECT * FROM {table} WHERE session_id=?', (SID,)).fetchone())


def test_observation_during_writer_keeps_verified_projection_registered(env):
    from orimem import journal_writer
    from orimem.core.source_stage import stage_sources
    from orimem.core.continuous import project
    from orimem.core.backend import digest
    backend, db, note = _setup(env)
    _write(db, '写入中的版本')
    stage_sources(backend, db, namespace='claude')
    note.write_text(note.read_text(encoding='utf-8') + '人工注释必须保留。\n', encoding='utf-8')
    real, fired = journal_writer.apply_plan, []

    def racing(plan, **kw):
        result = real(plan, **kw)
        if not fired:
            fired.append(1)
            _write(db, '写入后到达')
            stage_sources(backend, db, namespace='claude')
        return result

    with patch.object(journal_writer, 'apply_plan', side_effect=racing):
        result = project(backend)
    assert not result['errors'], result
    with backend.connect() as c:
        states = [r[0] for r in c.execute("SELECT state FROM lifecycle_jobs WHERE kind='projection' AND subject=?", (SID,))]
    assert 'failed' not in states and 'retry' not in states
    text = note.read_text(encoding='utf-8')
    assert '写入后到达' in text and '人工注释必须保留。' in text
    projection = _row(backend, 'source_projections')
    assert projection['snapshot_sha'] == _row(backend, 'capture_sessions')['snapshot_sha']
    assert projection['output_sha'] == digest(note.read_bytes())


def test_output_replaced_before_registration_is_not_adopted(env):
    from orimem import journal_writer
    from orimem.core.source_stage import stage_sources
    from orimem.core.continuous import project
    backend, db, note = _setup(env)
    _write(db, '写入中的版本')
    stage_sources(backend, db, namespace='claude')
    before = _row(backend, 'source_projections')
    real, edited = journal_writer.apply_plan, []

    def racing(plan, **kw):
        result = real(plan, **kw)
        if not edited:
            _write(db, '写入后到达')
            stage_sources(backend, db, namespace='claude')
            note.write_text(note.read_text(encoding='utf-8').replace('## 程序转录', '## 并发改动程序转录'), encoding='utf-8')
            edited.append(note.read_bytes())
        return result

    with patch.object(journal_writer, 'apply_plan', side_effect=racing):
        result = project(backend)
    assert result['errors']
    assert note.read_bytes() == edited[0]
    assert _row(backend, 'source_projections') == before
