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
    monkeypatch.setattr(hook, 'request_capture', lambda agent, session, transcript: spawned.append((agent, transcript)))
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
    assert data['hooks']['Stop'][-1]['hooks'][0]['async'] is True          # capture never blocks the turn
    assert 'async' not in data['hooks']['UserPromptSubmit'][-1]['hooks'][0]  # context injection stays synchronous
    state = json.loads((env['home'] / '.claude.json').read_text(encoding='utf-8'))
    assert state['mcpServers']['orimem']['args'][-1] == 'mcp'
    assert (env['home'] / '.claude' / 'skills' / 'orimem' / 'SKILL.md').exists()
    adapters.install(['claude'])          # idempotent
    assert len(json.loads(settings.read_text(encoding='utf-8'))['hooks']['Stop']) == 2
    adapters.uninstall(['claude'])
    assert json.loads(settings.read_text(encoding='utf-8')) == original
    assert 'orimem' not in json.loads((env['home'] / '.claude.json').read_text(encoding='utf-8'))['mcpServers']
    assert not (env['home'] / '.claude' / 'skills' / 'orimem').exists()


def test_codex_install_uninstall_roundtrip(env):
    import tomllib
    from orimem import adapters
    cfg = env['home'] / '.codex' / 'config.toml'
    cfg.parent.mkdir(parents=True)
    original = 'model = "gpt"\n\n[mcp_servers.other]\ncommand = "x"\n'
    cfg.write_bytes(original.encode('utf-8'))
    out = adapters.install(['codex'])
    assert 'error' not in out['codex'], out
    parsed = tomllib.loads(cfg.read_text(encoding='utf-8'))
    assert parsed['mcp_servers']['orimem']['args'][-1] == 'mcp' and 'other' in parsed['mcp_servers']
    hooks = json.loads((env['home'] / '.codex' / 'hooks.json').read_text(encoding='utf-8'))
    assert 'commandWindows' in hooks['hooks']['Stop'][0]['hooks'][0]
    assert hooks['hooks']['Stop'][0]['hooks'][0]['async'] is True
    adapters.install(['codex'])
    assert cfg.read_text(encoding='utf-8').count('[mcp_servers.orimem]') == 1
    adapters.uninstall(['codex'])
    assert cfg.read_bytes() == original.encode('utf-8')


def test_dsh_install_uninstall_roundtrip(env):
    import yaml
    from orimem import adapters
    profile = env['home'] / '.dsh' / 'profiles' / 'desktop'
    profile.mkdir(parents=True)
    original = '# my patch\n- id: ui-chat\n  name: "@deepseek-ai/dsh-client-ui-chat"\n  config:\n    x: !!js process.env.X\n'
    (profile / 'cordis.patch.yml').write_bytes(original.encode('utf-8'))
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
    assert 'async' not in hooks_file['hooks']['Stop'][0]['hooks'][0]   # dsh runs hooks synchronously
    assert (env['home'] / '.dsh' / 'skills' / 'orimem' / 'SKILL.md').exists()
    adapters.uninstall(['dsh'])
    assert (profile / 'cordis.patch.yml').read_bytes() == original.encode('utf-8')   # byte-identical, LF kept
    assert yaml.safe_load((empty / 'cordis.patch.yml').read_text(encoding='utf-8')) == []


def test_mcp_instructions_carry_daily_question_only_while_ask(env):
    from orimem import config
    from orimem.core.mcp_server import server_instructions
    assert 'config_set' in server_instructions()
    config.set_value('daily_summary.enabled', False)
    assert 'daily_summary' not in server_instructions()


def test_hook_session_ids_and_service_fallback(env, monkeypatch):
    from orimem import hook
    assert hook.normalized_session('dsh', 'session-abc') == 'dsh-abc'
    assert hook.normalized_session('claude', 'u1') == 'claude-u1'
    assert hook.normalized_session('codex', 'x1') == 'x1'
    spawned = []
    monkeypatch.setattr(hook, '_spawn_capture', lambda *a: spawned.append(a))
    assert hook.request_capture('dsh', 'session-abc', None) == 'spawned'      # no service running
    assert spawned == [('dsh', 'session-abc', None)]
    monkeypatch.setattr(hook, '_notify_service', lambda args: True)
    assert hook.request_capture('dsh', 'session-abc', None) == 'service'


def test_cli_output_survives_non_utf8_pipe(env, tmp_path):
    import os, subprocess, sys
    environment = dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0',
                       PYTHONPATH=str(__import__('conftest').SRC))
    result = subprocess.run([sys.executable, '-m', 'orimem', 'config', 'get', 'layout.inbox'],
                            capture_output=True, env=environment)
    assert result.returncode == 0, result.stderr
    assert '收件箱' in result.stdout.decode('utf-8')


def test_dsh_profile_with_market_bundle_is_not_patched(env):
    from orimem import adapters
    profile = env['home'] / '.dsh' / 'profiles' / 'desktop'
    profile.mkdir(parents=True)
    (profile / 'cordis.patch.yml').write_bytes(b'[]\n')
    (profile / 'package.json').write_text(json.dumps(
        {'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', 'dsh-orimem']}}}), encoding='utf-8')
    out = adapters.install(['dsh'])
    assert out['dsh']['mcp_and_hooks'] == [] and out['dsh']['via_bundle'] == [str(profile)]
    assert (profile / 'cordis.patch.yml').read_bytes() == b'[]\n'
    assert (env['tmp'] / 'orimem' / 'dsh-hooks.json').exists()       # the bundle reads this file
    adapters.uninstall(['dsh'])
    assert (env['tmp'] / 'orimem' / 'dsh-hooks.json').exists()       # still used by the bundle


def test_dsh_bundle_manifest_is_valid():
    import yaml
    from pathlib import Path
    from orimem import adapters
    root = Path(__file__).resolve().parents[1] / 'integrations' / 'dsh'
    manifest = json.loads((root / 'package.json').read_text(encoding='utf-8'))
    assert manifest['name'] == adapters.DSH_BUNDLE and manifest['dsh']['bundle']['patch'] == './cordis.patch.yml'
    assert 'dsh-plugin' in manifest['keywords']
    patch = yaml.load((root / 'cordis.patch.yml').read_text(encoding='utf-8'), Loader=adapters._DshLoader)
    ids = [e['id'] for e in patch[0]['insert']]
    assert ids == ['orimem-mcp', 'orimem-hooks']


def test_claude_plugin_installed_skips_user_level_registration(env):
    from orimem import adapters
    plugins = env['home'] / '.claude' / 'plugins'
    plugins.mkdir(parents=True)
    (plugins / 'installed_plugins.json').write_text(json.dumps(
        {'version': 2, 'plugins': {'orimem@orimem': [{'scope': 'user'}]}}), encoding='utf-8')
    out = adapters.install(['claude'])
    assert out['claude']['via_plugin'] is True
    assert not (env['home'] / '.claude.json').exists() and not (env['home'] / '.claude' / 'settings.json').exists()


def test_claude_plugin_manifests_are_consistent():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    market = json.loads((root / '.claude-plugin' / 'marketplace.json').read_text(encoding='utf-8'))
    plugin = json.loads((root / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))
    assert market['plugins'][0]['name'] == plugin['name'] == 'orimem'
    for rel in plugin['skills'] + [plugin['hooks'], plugin['icon']]:
        assert (root / rel).exists(), rel
    assert (root / plugin['skills'][0] / 'orimem' / 'SKILL.md').exists()
    hooks = json.loads((root / plugin['hooks']).read_text(encoding='utf-8'))['hooks']
    assert set(hooks) == {'SessionStart', 'UserPromptSubmit', 'Stop'}


def test_mcp_server_starts_without_embedding_model(env):
    """A missing model must not take the server down: settings, capture and health keep working."""
    import os, subprocess, sys
    from conftest import SRC
    from orimem import config
    config.set_value('mcp.extra', ['config_get'])
    requests = [
        dict(jsonrpc='2.0', id=1, method='initialize', params={}),
        dict(jsonrpc='2.0', id=2, method='tools/list', params={}),
        dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='config_get', arguments=dict(key='capture.enabled'))),
        dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(name='recall', arguments=dict(query='x', scope=['10 项目']))),
    ]
    stdin = ''.join(json.dumps(r) + '\n' for r in requests)
    environment = dict(os.environ, PYTHONPATH=str(SRC))
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'orimem', 'mcp', '--direct'], input=stdin,
                            capture_output=True, text=True, encoding='utf-8', env=environment, timeout=120)
    replies = {r['id']: r for r in map(json.loads, result.stdout.splitlines())}
    assert replies[1]['result']['serverInfo']['name'] == 'orimem', result.stderr
    from orimem.core.mcp_server import CORE_TOOLS
    assert {t['name'] for t in replies[2]['result']['tools']} == set(CORE_TOOLS) | {'config_get'}
    assert 'capture_now' in replies[1]['result']['instructions']   # hidden advanced tools are named
    assert replies[3]['result']['content'][0]['text'] == 'true'
    assert 'model download' in replies[4]['error']['message']
