import json

import pytest


def test_config_defaults_and_set(env):
    from orimem import config
    assert config.get('daily_summary.enabled') == 'ask'
    assert config.get('capture.enabled') is True
    config.set_value('daily_summary.enabled', True)
    assert config.get('daily_summary.enabled') is True
    with pytest.raises(ValueError):
        config.set_value('daily_summary.enabled', 'maybe')
    with pytest.raises(KeyError):
        config.set_value('nope.key', 1)
    assert config.parse_value('false') is False and config.parse_value('22:00') == '22:00'


def test_hook_asks_daily_once_and_spawns_capture(env, monkeypatch):
    from orimem import config, hook
    spawned = []
    monkeypatch.setattr(hook, '_spawn_capture', lambda agent, transcript: spawned.append((agent, transcript)))
    out = hook.handle(dict(hook_event_name='SessionStart', session_id='abc', cwd='D:/x', source='startup'), 'claude')
    assert 'config_set' in out['hookSpecificOutput']['additionalContext']
    config.set_value('daily_summary.enabled', False)
    out = hook.handle(dict(hook_event_name='SessionStart', session_id='abc', cwd='D:/x', source='startup'), 'claude')
    assert out == {}
    hook.handle(dict(hook_event_name='Stop', session_id='abc', cwd='D:/x', transcript_path='T'), 'codex')
    assert spawned == [('codex', 'T')]
    config.set_value('capture.enabled', False)
    hook.handle(dict(hook_event_name='Stop', session_id='abc', cwd='D:/x'), 'dsh')
    assert spawned == [('codex', 'T')]


def test_claude_install_uninstall_roundtrip(env):
    from orimem import adapters
    settings = env['home'] / '.claude' / 'settings.json'
    settings.parent.mkdir(parents=True)
    original = dict(model='x', hooks=dict(Stop=[dict(hooks=[dict(type='command', command='other-tool')])]))
    settings.write_text(json.dumps(original), encoding='utf-8')
    out = adapters.install(['claude'])
    assert 'error' not in out['claude'], out
    data = json.loads(settings.read_text(encoding='utf-8'))
    assert len(data['hooks']['Stop']) == 2 and 'SessionStart' in data['hooks']
    state = json.loads((env['home'] / '.claude.json').read_text(encoding='utf-8'))
    assert state['mcpServers']['orimem']['args'][-1] == 'mcp'
    assert (env['home'] / '.claude' / 'skills' / 'OriMem' / 'SKILL.md').exists()
    adapters.install(['claude'])          # idempotent
    assert len(json.loads(settings.read_text(encoding='utf-8'))['hooks']['Stop']) == 2
    adapters.uninstall(['claude'])
    assert json.loads(settings.read_text(encoding='utf-8')) == original
    assert 'orimem' not in json.loads((env['home'] / '.claude.json').read_text(encoding='utf-8'))['mcpServers']
    assert not (env['home'] / '.claude' / 'skills' / 'OriMem').exists()


def test_codex_install_uninstall_roundtrip(env):
    import tomllib
    from orimem import adapters
    cfg = env['home'] / '.codex' / 'config.toml'
    cfg.parent.mkdir(parents=True)
    original = 'model = "gpt"\n\n[mcp_servers.other]\ncommand = "x"\n'
    cfg.write_text(original, encoding='utf-8')
    out = adapters.install(['codex'])
    assert 'error' not in out['codex'], out
    parsed = tomllib.loads(cfg.read_text(encoding='utf-8'))
    assert parsed['mcp_servers']['orimem']['args'][-1] == 'mcp' and 'other' in parsed['mcp_servers']
    hooks = json.loads((env['home'] / '.codex' / 'hooks.json').read_text(encoding='utf-8'))
    assert 'commandWindows' in hooks['hooks']['Stop'][0]['hooks'][0]
    adapters.install(['codex'])
    assert cfg.read_text(encoding='utf-8').count('[mcp_servers.orimem]') == 1
    adapters.uninstall(['codex'])
    assert tomllib.loads(cfg.read_text(encoding='utf-8')) == tomllib.loads(original)


def test_dsh_install_uninstall_roundtrip(env):
    import yaml
    from orimem import adapters
    profile = env['home'] / '.dsh' / 'profiles' / 'desktop'
    profile.mkdir(parents=True)
    original = '# my patch\n- id: ui-chat\n  name: "@deepseek-ai/dsh-client-ui-chat"\n  config:\n    x: !!js process.env.X\n'
    (profile / 'cordis.patch.yml').write_text(original, encoding='utf-8')
    empty = env['home'] / '.dsh' / 'profiles' / 'web'
    empty.mkdir()
    (empty / 'cordis.patch.yml').write_text('# empty layer\n[]\n', encoding='utf-8')
    out = adapters.install(['dsh'])
    assert 'error' not in out['dsh'], out
    for patch in (profile / 'cordis.patch.yml', empty / 'cordis.patch.yml'):
        data = yaml.load(patch.read_text(encoding='utf-8'), Loader=adapters._DshLoader)
        inserted = [e for e in data if 'insert' in e][0]['insert']
        assert [e['id'] for e in inserted] == ['orimem-mcp', 'orimem-hooks']
        assert inserted[0]['config']['args'][-1] == 'mcp'
    hooks_file = json.loads((env['tmp'] / 'orimem' / 'dsh-hooks.json').read_text(encoding='utf-8'))
    assert '--agent dsh' in hooks_file['hooks']['Stop'][0]['hooks'][0]['command']
    assert (env['home'] / '.dsh' / 'skills' / 'OriMem' / 'SKILL.md').exists()
    adapters.uninstall(['dsh'])
    assert (profile / 'cordis.patch.yml').read_text(encoding='utf-8').strip() == original.strip()
    assert yaml.safe_load((empty / 'cordis.patch.yml').read_text(encoding='utf-8')) == []
