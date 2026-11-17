"""0.1.3: language layouts, lexical-only recall, transcript format checks."""
import json

import pytest
from conftest import CLAUDE_UUID, DSH_ID, claude_records, dsh_records, write_jsonl, write_zstd_jsonl


def _init(env, *args):
    from orimem import cli, config
    cli.main(['init', '--yes', '--vault', str(env['vault']), '--agents', 'none', '--skip-model', *args])
    config.reload()


def test_english_layout_scaffolds_and_routes_everything(env, capsys):
    from orimem import config
    (env['tmp'] / 'orimem' / 'config.toml').unlink()      # a brand-new install, not a 0.1.x upgrade
    config.reload()
    _init(env, '--lang', 'en')
    capsys.readouterr()
    lay = config.layout()
    assert lay['language'] == 'en' and lay['daily'] == '50 Journal/Daily' and lay['evidence'] == '90 Archive/Conversations'
    for folder in ('10 Projects', '50 Journal/Daily', '90 Archive/Conversations', '00 Inbox/AI Drafts'):
        assert (env['vault'] / folder).is_dir(), folder
    rules = (env['vault'] / 'AGENTS.md').read_text(encoding='utf-8')
    assert '`50 Journal/Daily/YYYY-MM-DD.md`' in rules and '{' not in rules
    assert (env['vault'] / 'Welcome to OriMem.md').exists()
    assert json.loads((env['vault'] / '.obsidian' / 'daily-notes.json').read_text(encoding='utf-8'))['folder'] == '50 Journal/Daily'
    assert config.daily_note('2026-11-17') == '50 Journal/Daily/2026-11-17.md'
    assert config.weekly_note('2026-W47') == '50 Journal/Weekly/2026-W47 Weekly Review.md'
    assert config.project_scopes() == ['10 Projects', '15 Courses']
    # capture projects into the English evidence folder
    from orimem.capture import collect
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--p' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/p'))
    assert 'error' not in collect(backfill=True).get('projection', {})
    assert list((env['vault'] / '90 Archive' / 'Conversations').rglob('*-continuous.md'))


def test_custom_folder_and_writer_boundaries(env):
    from orimem import config, journal_writer
    config.set_value('layout.language', 'en')
    config.set_value('layout.daily', 'Journal/Days')
    with pytest.raises(ValueError):
        config.set_value('layout.projects', '../outside')
    root = env['vault']

    def plan(path):
        return dict(batch_id='t-' + __import__('hashlib').sha1(path.encode()).hexdigest()[:12], through=__import__('datetime').datetime.now().astimezone().isoformat(), events=[],
                    changes=[dict(path=path, expected_sha256=None, content='# Note\n\nA fact.\n',
                                  checks=[dict(section=['Note'], text='A fact.', events=[])])])
    journal_writer.validate_plan(plan('Journal/Days/2026-11-17.md'), root)
    journal_writer.validate_plan(plan('10 Projects/Demo.md'), root)
    journal_writer.validate_plan(plan('15 Courses/Math/Course Home.md'), root)
    for bad in ('50 Journal/Daily/2026-11-17.md', '15 Courses/Math/lecture.md', '90 Archive/x.md', 'Random/x.md'):
        with pytest.raises(ValueError):
            journal_writer.validate_plan(plan(bad), root)


def test_upgrade_from_0_1_keeps_chinese_layout_and_transcript_folder(env, capsys):
    from orimem import config
    data = json.loads(json.dumps(config.load()))
    data['journal_from'] = '2026-11-01T00:00:00+00:00'      # what 0.1.x init wrote
    config.save(data)
    config.reload()
    _init(env)
    capsys.readouterr()
    assert config.layout()['language'] == 'zh' and config.layout()['evidence'] == '90 归档/Codex佐证'


def test_lexical_mode_indexes_and_recalls_without_model(env, monkeypatch):
    from orimem import config
    from orimem.core.backend import Backend
    from orimem.core.embedding import LexicalOnly
    monkeypatch.setenv('ORIMEM_EMBEDDER', 'none')
    config.set_value('recall.mode', 'lexical')
    note = env['vault'] / '10 项目' / 'Demo.md'
    note.parent.mkdir(parents=True)
    note.write_text('---\ntype: project\n---\n\n# Demo\n\n部署使用 WireGuard 隧道，端口 51820。\n', encoding='utf-8')
    backend = Backend(config.vault(), config.db())
    assert isinstance(backend.embedder, LexicalOnly)
    result = backend.sync()
    assert result['changed'] == 1 and result['embedded'] == 0      # nothing is embedded without a model
    found = backend.recall('WireGuard 隧道', ['10 项目'], mode='hybrid')
    assert found['mode'] == 'lexical' and found['results'][0]['path'] == '10 项目/Demo.md'
    with pytest.raises(ValueError):
        config.set_value('recall.mode', 'vectors')


def test_vector_mode_without_model_names_the_lexical_fallback(env, monkeypatch):
    from orimem.core.embedding import choose_backend
    with pytest.raises(RuntimeError, match='recall.mode lexical'):
        choose_backend(env['tmp'] / 'no-model')


def test_format_checks(env):
    from orimem.capture import claude, dsh, formats, collect
    good = write_zstd_jsonl(env['home'] / '.dsh' / 'sessions' / 'w' / DSH_ID / 'session.v4.jsonl.zstd', dsh_records('D:/p'))
    assert dsh.parse(good)[0]['format']['status'] == 'ok'
    future = [dict(r, version=5) if r.get('type') == 'session' else r for r in dsh_records('D:/p')]
    newer = write_zstd_jsonl(env['home'] / '.dsh' / 'sessions' / 'w' / 'session-ffffffff-bbbb-4ccc-8ddd-eeeeeeeeeeee' / 'session.v5.jsonl.zstd', future)
    result = dsh.capture_file(newer)
    assert not result['captured'] and 'not supported' in result['reason']
    records = [dict(r, version='3.0.1') for r in claude_records('D:/p')]
    records[0] = dict(records[0], message='not a message object')
    odd = write_jsonl(env['home'] / '.claude' / 'projects' / 'D--p' / f'{CLAUDE_UUID}.jsonl', records)
    fmt = claude.parse(odd)[0]['format']
    assert fmt['status'] == 'drift' and any('3.0.1' in w for w in fmt['warnings'])
    collect(['claude'], backfill=True)
    report = formats.report()
    assert report['claude']['last_status'] == 'drift' and report['claude']['recent_warnings']
