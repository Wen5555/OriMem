from conftest import (CLAUDE_UUID, CODEX_ID, DSH_ID, claude_records, codex_records, dsh_records,
                      write_jsonl, write_zstd_jsonl)


def test_claude_parse_filters_injected_and_thinking(env):
    from orimem.capture import claude
    path = write_jsonl(env['home'] / '.claude' / 'projects' / 'D--proj' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/proj'))
    session, events, stats = claude.parse(path)
    assert [e['role'] for e in events] == ['user', 'assistant']
    assert events[1]['content'] == '方案：MCP 为核心。'
    assert 'secret' not in events[1]['content']
    assert session['first_user_message'] == '帮我整理 OriMem 的打包方案'


def test_codex_parse_and_automation_exclusion(env):
    from orimem.capture import codex
    path = write_jsonl(env['home'] / '.codex' / 'sessions' / '2026' / '11' / '17' / f'rollout-2026-11-17T02-00-00-{CODEX_ID}.jsonl',
                       codex_records('D:/proj'))
    session, events, stats = codex.parse(path)
    assert [e['content'] for e in events] == ['给 Codex 加采集适配', '已完成 Codex 适配。']
    assert events[1]['metadata']['phase'] == 'final'
    result = codex.capture_file(path)
    assert result['captured'] and result['session_id'] == CODEX_ID
    auto = write_jsonl(path, codex_records('D:/proj', thread_source='automation'))
    assert codex.capture_file(auto)['reason'] == 'automation thread excluded'


def test_dsh_parse_applies_replace_and_skips_tools(env):
    from orimem.capture import dsh
    path = write_zstd_jsonl(env['home'] / '.dsh' / 'sessions' / '--D-proj--' / DSH_ID / 'session.v4.jsonl.zstd',
                            dsh_records('D:/proj'))
    session, events, stats = dsh.parse(path)
    assert [(e['role'], e['content']) for e in events] == [('user', 'dsh 第一问'), ('assistant', 'dsh 最终回答')]
    result = dsh.capture_file(path)
    assert result['captured'] and result['session_id'] == 'dsh-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def test_exclude_cwd(env):
    from orimem import config
    from orimem.capture import claude
    config.set_value('capture.exclude_cwd', ['D:/private'])
    path = write_jsonl(env['home'] / '.claude' / 'projects' / 'D--private' / f'{CLAUDE_UUID}.jsonl',
                       claude_records('D:\\private\\sub'))
    assert claude.capture_file(path)['reason'] == 'cwd excluded by capture.exclude_cwd'


def test_collect_end_to_end_projects_into_vault(env):
    from orimem.capture import collect
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--proj' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/proj'))
    write_jsonl(env['home'] / '.codex' / 'sessions' / '2026' / '11' / '17' / f'rollout-x-{CODEX_ID}.jsonl', codex_records('D:/proj'))
    write_zstd_jsonl(env['home'] / '.dsh' / 'sessions' / 'w' / DSH_ID / 'session.v4.jsonl.zstd', dsh_records('D:/proj'))
    result = collect(backfill=True)
    for name in ('claude', 'codex', 'dsh'):
        assert result['sources'][name]['captured'] == 1, result
        assert result['sources'][name]['stage']['staged'], result
    assert 'error' not in result.get('projection', {}), result
    notes = list((env['vault'] / '90 归档' / 'Codex佐证').rglob('*.md'))
    text = '\n'.join(p.read_text(encoding='utf-8') for p in notes)
    assert '给 Codex 加采集适配' in text and 'dsh 最终回答' in text and '方案：MCP 为核心。' in text
    # unchanged files are not re-captured
    again = collect(backfill=True)
    assert all(r['captured'] == 0 for r in again['sources'].values())


def test_collect_respects_disabled_and_since(env):
    from orimem import config
    from orimem.capture import collect
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--proj' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/proj'))
    config.set_value('capture.since', '2999-01-01T00:00:00+00:00')
    assert collect()['sources']['claude']['captured'] == 0
    config.set_value('capture.enabled', False)
    assert collect()['enabled'] is False


def test_find_session_by_id_and_capture_now_tool(env):
    from orimem.capture import claude, codex, dsh, resolve
    from orimem.core.mcp_server import local_call
    c = write_jsonl(env['home'] / '.claude' / 'projects' / 'D--proj' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/proj'))
    x = write_jsonl(env['home'] / '.codex' / 'sessions' / '2026' / '11' / '17' / f'rollout-2026-11-17T02-00-00-{CODEX_ID}.jsonl', codex_records('D:/proj'))
    d = write_zstd_jsonl(env['home'] / '.dsh' / 'sessions' / 'w' / DSH_ID / 'session.v4.jsonl.zstd', dsh_records('D:/proj'))
    assert claude.find(CLAUDE_UUID) == c and claude.find('claude-' + CLAUDE_UUID) == c
    assert codex.find(CODEX_ID) == x and codex.find('*') is None
    assert dsh.find(DSH_ID) == d and dsh.find(DSH_ID.removeprefix('session-')) == d
    assert resolve('claude', path=str(env['tmp'] / 'elsewhere.jsonl')) is None        # outside the source root
    out = local_call('capture_now', dict(source='dsh', session=DSH_ID))
    assert out['sources']['dsh']['captured'] == 1 and out['sources']['dsh']['scanned'] == 1
    assert local_call('capture_now', dict(source='dsh', session='session-missing'))['captured'] is False
