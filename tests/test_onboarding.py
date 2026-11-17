"""Project onboarding: linking a new folder, multiple folders/worktrees, backlog import."""
import json

from conftest import CLAUDE_UUID, CODEX_ID, claude_records, codex_records, write_jsonl

OTHER_UUID = '99999999-2222-4333-8444-555555555555'


def _backend():
    from orimem import config
    from orimem.core.backend import Backend
    from orimem.core.embedding import LexicalOnly
    return Backend(config.vault(), config.db(), LexicalOnly())


def test_initialize_dedupes_and_binds_several_folders(env, tmp_path):
    from orimem import config
    from orimem.core.records import initialize
    from orimem.core.task_context import resolve_task_context
    config.set_value('recall.mode', 'lexical')
    backend = _backend()
    first = initialize(backend, '星图编辑器', 'project', workspace='D:/code/stargraph')
    assert first['path'] == '10 项目/星图编辑器.md'
    # same name again from another folder: no silent rebinding
    again = initialize(backend, '星图编辑器', 'project', workspace='D:/code/stargraph-v2')
    assert again['binding_conflict'] and 'also_bind' in again['hint']
    bound = initialize(backend, '星图编辑器', 'project', workspace='D:/code/stargraph-v2', also_bind=True)
    assert 'binding_conflict' not in bound
    for folder in ('D:/code/stargraph/src', 'D:/code/stargraph-v2'):
        found = resolve_task_context(config.db(), config.vault(), folder, '', authorized_scope=['10 项目'])
        assert found['status'] == 'matched' and found['entry']['path'] == '10 项目/星图编辑器.md', folder
    # an alias-like duplicate under a different name is reported, not created
    note = config.vault() / '10 项目' / '星图编辑器.md'
    note.write_text(note.read_text(encoding='utf-8').replace('title:', 'aliases: ["stargraph"]\ntitle:'), encoding='utf-8')
    backend.sync()
    similar = initialize(backend, 'stargraph', 'project', workspace='D:/other')
    assert similar['created'] is False and similar['similar_entries'][0]['path'] == '10 项目/星图编辑器.md'
    assert not (config.vault() / '10 项目' / 'stargraph.md').exists()
    assert 'path' in initialize(backend, 'stargraph', 'project', force_new=True)


def test_worktree_resolves_to_main_checkout(env, tmp_path):
    from orimem.core.task_context import workspace_variants
    main = tmp_path / 'repo'
    (main / '.git' / 'worktrees' / 'feature').mkdir(parents=True)
    tree = tmp_path / 'repo-feature'
    (tree / 'src').mkdir(parents=True)
    (tree / '.git').write_text(f'gitdir: {main / ".git" / "worktrees" / "feature"}\n', encoding='utf-8')
    assert workspace_variants(str(tree / 'src'))[-1] == str(main)
    assert workspace_variants(str(main)) == [str(main)]


def test_unlinked_folder_is_offered_once(env, tmp_path):
    from orimem import config, hook_entry
    config.set_value('recall.mode', 'lexical')
    _backend().sync()                                   # creates the database the hook reads
    event = dict(hook_event_name='SessionStart', session_id='s1', cwd='D:/code/newproj')   # temp folders are never offered
    first = hook_entry.handle(event, db=config.db(), vault=config.vault(), scope=config.project_scopes())
    assert 'initialize' in first['hookSpecificOutput']['additionalContext']
    assert hook_entry.handle(dict(event, session_id='s2'), db=config.db(), vault=config.vault(),
                             scope=config.project_scopes()) == {}
    vault_event = dict(event, session_id='s3', cwd=str(config.vault()))
    assert hook_entry.handle(vault_event, db=config.db(), vault=config.vault(), scope=config.project_scopes()) == {}


def test_onboard_scan_import_status(env):
    from orimem import config, onboard
    config.set_value('recall.mode', 'lexical')
    config.set_value('capture.since', '2999-01-01T00:00:00+00:00')     # everything predates the install
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--alpha' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/alpha'))
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--beta' / f'{OTHER_UUID}.jsonl', claude_records('D:/beta/sub'))
    write_jsonl(env['home'] / '.codex' / 'sessions' / '2026' / '11' / '17' / f'rollout-x-{CODEX_ID}.jsonl',
                codex_records('D:/alpha/app'))
    found = onboard.scan(days=0)
    by_cwd = {w['cwd']: w for w in found['workspaces']}
    assert set(by_cwd) == {'D:/alpha', 'D:/beta/sub', 'D:/alpha/app'}
    assert by_cwd['D:/alpha']['not_captured'] == 1 and not list((env['vault']).rglob('*-continuous.md'))
    result = onboard.import_workspaces(['D:/alpha'])
    assert result['imported_sessions'] == 2                      # D:/alpha and its subfolder, not D:/beta
    notes = '\n'.join(p.name for p in env['vault'].rglob('*-continuous.md'))
    assert CLAUDE_UUID in notes and CODEX_ID in notes and OTHER_UUID not in notes
    state = onboard.status()
    assert state['pending'] == ['D:/alpha'] and state['imported'][0]['sessions'] == 2
    # the skipped D:/beta transcript is still eligible for a later import
    assert onboard.import_workspaces(['D:/beta'])['imported_sessions'] == 1


def test_new_project_folder_is_linked_automatically(env, tmp_path, monkeypatch):
    import tempfile
    from orimem import config, hook_entry, projects
    from orimem.core.task_context import resolve_task_context
    config.set_value('recall.mode', 'lexical')
    _backend().sync()
    work = env['home'] / 'code' / 'lumen'              # not under the temp folder (which is never a project)
    (work / 'src').mkdir(parents=True)
    (work / 'Cargo.toml').write_text('[package]\nname = "lumen"\n', encoding='utf-8')
    monkeypatch.setattr(tempfile, 'tempdir', str(env['home'] / 'no-temp'))
    assert projects.project_root(work / 'src') == work.resolve()
    event = dict(hook_event_name='SessionStart', session_id='s1', cwd=str(work / 'src'))
    out = hook_entry.handle(event, db=config.db(), vault=config.vault(), scope=config.project_scopes())
    assert out['_orimem_link'] == dict(cwd=str(work.resolve()))
    assert '10 项目/lumen.md' in out['hookSpecificOutput']['additionalContext']
    # what the service does with that request
    linked = projects.link_workspace(_backend(), str(work / 'src'))
    assert linked['linked'] and linked['path'] == '10 项目/lumen.md'
    page = (config.vault() / '10 项目' / 'lumen.md').read_text(encoding='utf-8')
    assert 'OriMem自动创建' in page
    found = resolve_task_context(config.db(), config.vault(), str(work / 'src'), '', authorized_scope=['10 项目'])
    assert found['status'] == 'matched'
    # linked folder: context plus the record-keeping duty, and no second creation request
    again = hook_entry.handle(dict(event, session_id='s2'), db=config.db(), vault=config.vault(), scope=config.project_scopes())
    assert '_orimem_link' not in again and '记录约定' in again['hookSpecificOutput']['additionalContext']
    config.set_value('projects.link', 'off')
    other = env['home'] / 'code' / 'other'
    other.mkdir()
    (other / 'package.json').write_text('{}', encoding='utf-8')
    assert hook_entry.handle(dict(event, session_id='s3', cwd=str(other)), db=config.db(), vault=config.vault(),
                             scope=config.project_scopes()) == {}


def test_similar_entry_asks_instead_of_creating(env, monkeypatch):
    import tempfile
    from orimem import config, hook_entry
    from orimem.core.records import initialize
    config.set_value('recall.mode', 'lexical')
    initialize(_backend(), 'Lumen', 'project')                   # an unbound entry already exists
    work = env['home'] / 'repos' / 'lumen'
    (work / '.git').mkdir(parents=True)
    monkeypatch.setattr(tempfile, 'tempdir', str(env['home'] / 'no-temp'))
    out = hook_entry.handle(dict(hook_event_name='SessionStart', session_id='s1', cwd=str(work)),
                            db=config.db(), vault=config.vault(), scope=config.project_scopes())
    assert '_orimem_link' not in out and '10 项目/Lumen.md' in out['hookSpecificOutput']['additionalContext']


def test_profile_is_shared_only_when_filled(env, capsys):
    from orimem import cli, config, profile
    from orimem.core.mcp_server import server_instructions
    cli.main(['init', '--yes', '--vault', str(env['vault']), '--agents', 'none', '--skip-model'])
    capsys.readouterr()
    config.reload()
    note = env['vault'] / '个人画像.md'
    assert note.exists() and profile.text() == ''                 # untouched template is not shared
    note.write_text(note.read_text(encoding='utf-8').replace('## 沟通偏好\n', '## 沟通偏好\n\n默认中文，先给结果。\n'),
                    encoding='utf-8')
    shared = profile.text()
    assert '默认中文，先给结果。' in shared and '基本信息' not in shared and '<!--' not in shared
    assert '默认中文' in server_instructions()
    config.set_value('profile.share', False)
    assert profile.text() == '' and '默认中文' not in server_instructions()
    import pytest
    from datetime import datetime
    from orimem import journal_writer

    def plan(path, **extra):
        return dict(batch_id='p1', through=datetime.now().astimezone().isoformat(), events=[],
                    changes=[dict(path=path, expected_sha256=None, content='# T' + chr(10) * 2 + 'A fact.' + chr(10),
                                  checks=[dict(section=['T'], text='A fact.', events=[])])], **extra)
    with pytest.raises(ValueError, match='explicitly asks'):
        journal_writer.validate_plan(plan('个人画像.md'), env['vault'])
    journal_writer.validate_plan(plan('个人画像.md', profile_update=True), env['vault'])
    with pytest.raises(ValueError, match='capture'):
        journal_writer.validate_plan(plan('90 归档/Codex佐证/2026-11/x.md'), env['vault'])
    journal_writer.validate_plan(plan('90 归档/Codex佐证/2026-11/x.md'), env['vault'], internal=True)
