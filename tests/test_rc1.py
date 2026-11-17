"""1.0.0-rc.1 fixes: safe defaults, config handling, first run, hooks, upgrades, retention, recovery."""
import json
import os
import sqlite3
import time
from contextlib import closing

import pytest
from conftest import CLAUDE_UUID, claude_records, write_jsonl


def _lexical_backend():
    from orimem import config
    from orimem.core.backend import Backend
    from orimem.core.embedding import LexicalOnly
    return Backend(config.vault(), config.db(), LexicalOnly())


def test_unconfigured_never_captures_and_detects_language(env):
    from orimem import config
    from orimem.capture import collect
    (env['tmp'] / 'orimem' / 'config.toml').unlink()
    config.reload()
    assert not config.configured() and config.get('capture.enabled') is False
    assert config.layout()['evidence'] != config.LEGACY_EVIDENCE        # not the 0.1.x layout
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--p' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/p'))
    assert collect()['enabled'] is False
    from orimem.core.mcp_server import server_instructions
    assert 'orimem init' in server_instructions()


def test_empty_since_never_backfills(env):
    from orimem import config
    from orimem.capture import collect
    old = write_jsonl(env['home'] / '.claude' / 'projects' / 'D--p' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/p'))
    os.utime(old, (time.time() - 86400, time.time() - 86400))
    assert config.get('capture.since') == ''
    assert collect()['sources']['claude']['captured'] == 0
    assert config.get('capture.since')                                   # pinned to the first collection


def test_settings_are_typed_reloaded_and_guarded(env):
    from orimem import config
    config.set_value('capture.enabled', 'false')
    assert config.get('capture.enabled') is False
    with pytest.raises(ValueError):
        config.set_value('capture.sources', 'claude')
    with pytest.raises(ValueError):
        config.set_value('capture.sources', ['claude', 'cursor'])
    with pytest.raises(ValueError):
        config.set_value('daily_summary.time', '25:00')
    with pytest.raises(ValueError):
        config.set_value('journal_from', '2020-01-01T00:00:00+00:00')
    config.set_value('maintenance.backup_days', '7')
    assert config.get('maintenance.backup_days') == 7
    # another process edits the file: the next read sees it (no restart needed)
    text = config.CONFIG_PATH.read_text(encoding='utf-8').replace('enabled = false', 'enabled = true')
    time.sleep(0.01)
    config.CONFIG_PATH.write_text(text + '\n', encoding='utf-8')
    assert config.get('capture.enabled') is True


def test_hook_command_with_spaces_uses_powershell_for_claude(env, monkeypatch):
    from orimem import adapters
    if os.name != 'nt':
        monkeypatch.setattr(adapters, 'python_exe', lambda: "/home/a b/it's/python")
        assert adapters.hook_command('claude').startswith("'/home/a b/it'\"'\"'s/python'")
        return
    monkeypatch.setattr(adapters, 'python_exe', lambda: r'C:\No Such\Dir\python.exe')
    spec = adapters.hook_spec('claude')
    assert spec['shell'] == 'powershell' and spec['command'].startswith("& 'C:/No Such/Dir/python.exe'")
    assert 'shell' not in adapters.hook_spec('dsh')        # dsh already runs the user's shell (PowerShell)
    monkeypatch.setattr(adapters, 'python_exe', lambda: r'C:\Tools\python.exe')
    assert adapters.hook_spec('claude') == dict(command=adapters.hook_command('claude'))
    assert not adapters.hook_command('claude').startswith('&')


def test_first_recall_builds_the_index(env):
    from orimem import config
    config.set_value('recall.mode', 'lexical')
    note = env['vault'] / '10 项目' / 'Alpha.md'
    note.parent.mkdir(parents=True)
    note.write_text('# Alpha\n\nThe deploy uses WireGuard on port 51820.\n', encoding='utf-8')
    found = _lexical_backend().recall('WireGuard', ['10 项目'])          # no explicit sync
    assert found['results'] and found['results'][0]['path'] == '10 项目/Alpha.md'
    with closing(sqlite3.connect(config.db())) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == 1


def test_prune_keeps_recent_and_install_backups(env):
    from orimem import config, maintenance
    root = config.home() / 'backups'
    old = time.time() - 10 * 86400
    for name, age in [('20260101-projection-abc', old), ('20260101-orion-init-x', old),
                      ('20260101-projection-new', time.time()), ('install-20260101-000000', old)]:
        (root / name).mkdir(parents=True)
        (root / name / 'manifest.json').write_text('{}', encoding='utf-8')
        os.utime(root / name, (age, age))
    result = maintenance.prune()
    left = {p.name for p in root.iterdir()}
    assert left == {'20260101-orion-init-x', '20260101-projection-new', 'install-20260101-000000'}
    assert result['backups'] == 1


def test_projection_is_adopted_after_database_loss(env):
    from orimem import config
    from orimem.capture import collect
    path = write_jsonl(env['home'] / '.claude' / 'projects' / 'D--p' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/p'))
    assert 'error' not in collect(backfill=True).get('projection', {})
    note = next(env['vault'].rglob('*-continuous.md'))
    note.write_text(note.read_text(encoding='utf-8') + 'My own remark.\n', encoding='utf-8')
    # ~/.orimem lost: database and adapter mirrors are gone, the vault stays
    for item in (config.db(), config.home() / 'sources'):
        if item.is_dir():
            import shutil
            shutil.rmtree(item)
        else:
            item.unlink()
    records = claude_records('D:/p')
    records[-1]['message']['content'][-1]['text'] = 'Updated answer.'
    write_jsonl(path, records)
    result = collect(backfill=True)
    assert 'error' not in result.get('projection', {}) and not result['projection']['errors'], result
    text = note.read_text(encoding='utf-8')
    assert 'Updated answer.' in text and 'My own remark.' in text


def test_english_vault_gets_english_evidence_notes(env):
    from orimem import config
    from orimem.capture import collect
    config.set_value('layout.language', 'en')
    write_jsonl(env['home'] / '.claude' / 'projects' / 'D--p' / f'{CLAUDE_UUID}.jsonl', claude_records('D:/p'))
    collect(backfill=True)
    text = next((env['vault'] / '90 Archive' / 'Conversations').rglob('*-continuous.md')).read_text(encoding='utf-8')
    assert '# Conversation evidence claude-' in text and '## Transcript' in text and 'source_platform: claude-code' in text
    assert '持续佐证' not in text


def test_upgrade_refreshes_hooks_of_installed_agents(env):
    from orimem import adapters, upgrade
    adapters.install(['claude'])
    settings = env['home'] / '.claude' / 'settings.json'
    data = json.loads(settings.read_text(encoding='utf-8'))
    stop = data['hooks']['Stop'][-1]['hooks'][0]
    stop.pop('async')                                            # what 0.1.2 wrote
    settings.write_text(json.dumps(data), encoding='utf-8')
    assert upgrade.run_if_needed()['claude']
    assert json.loads(settings.read_text(encoding='utf-8'))['hooks']['Stop'][-1]['hooks'][0]['async'] is True
    assert upgrade.run_if_needed() is None                       # once per version
    assert 'codex' not in adapters.installed()                   # never installs into new agents


def test_init_survives_eof_on_a_fake_terminal(env, monkeypatch):
    from orimem import cli

    class FakeStdin:
        def isatty(self):
            return True
    monkeypatch.setattr(cli.sys, 'stdin', FakeStdin())
    monkeypatch.setattr('builtins.input', lambda *_: (_ for _ in ()).throw(EOFError()))
    cli.ASSUME_DEFAULTS = False
    assert cli._ask('question', 'default') == 'default'


def test_agent_installed_later_is_connected_once(env, monkeypatch):
    from orimem import adapters, config, upgrade
    monkeypatch.setattr(adapters, 'detect', lambda: dict(claude=True, codex=False, dsh=False))
    adapters.install(['claude'])
    (env['home'] / '.codex').mkdir(exist_ok=True)
    monkeypatch.setattr(adapters, 'detect', lambda: dict(claude=True, codex=True, dsh=False))
    result = upgrade.connect_new_agents()                 # Codex appeared after OriMem was set up
    assert set(result) >= {'codex'} and 'claude' not in result
    assert 'codex' in adapters.installed()
    assert upgrade.connect_new_agents() is None           # nothing new
    adapters.uninstall(['codex'])                         # removed on purpose
    assert config.get('agents.skip') == ['codex']
    assert upgrade.connect_new_agents() is None and 'codex' not in adapters.installed()
    adapters.install(['codex'])                           # explicit install lifts the skip
    assert config.get('agents.skip') == []


def test_codex_hook_trust_detection(env):
    import tomllib
    from orimem import adapters
    adapters.install(['codex'])
    assert adapters.codex_hook_trust() == 'needs approval'
    hooks_path = env['home'] / '.codex' / 'hooks.json'
    data = json.loads(hooks_path.read_text(encoding='utf-8'))
    lines = []
    for event, groups in data['hooks'].items():
        label = __import__('re').sub(r'(?<!^)(?=[A-Z])', '_', event).lower()
        for gi, _ in enumerate(groups):
            lines.append(f'[hooks.state."file:{hooks_path}:{label}:{gi}:0"]\ntrusted_hash = "sha256:abc"\n')
    cfg = env['home'] / '.codex' / 'config.toml'
    cfg.write_text(cfg.read_text(encoding='utf-8') + chr(10) + chr(10).join(l.replace(chr(92), chr(92) * 2) for l in lines), encoding='utf-8')
    tomllib.loads(cfg.read_text(encoding='utf-8'))
    assert adapters.codex_hook_trust() == 'trusted'


def test_dsh_hook_bridge_states(env, monkeypatch, tmp_path):
    from orimem import adapters
    profile = tmp_path / 'profiles' / 'headless'
    profile.mkdir(parents=True)
    monkeypatch.setattr(adapters, '_dsh_cli', lambda: None)
    assert adapters.dsh_hook_bridge(profile) == 'unknown'
    npm = tmp_path / 'npm' / 'lib' / 'node_modules' / '@deepseek-ai' / 'dsh'
    (npm / 'lib').mkdir(parents=True)
    cli = npm / 'lib' / 'bin.js'
    cli.write_text('', encoding='utf-8')
    monkeypatch.setattr(adapters, '_dsh_cli', lambda: str(cli))
    assert adapters.dsh_hook_bridge(profile) == 'missing'
    monkeypatch.setattr(adapters.shutil, 'which', lambda name: None)
    assert adapters.ensure_dsh_hook_bridge(profile)['hint'].startswith('dsh installs plugins with pnpm')
    (profile / 'node_modules' / '@deepseek-ai' / 'dsh-hooks-claude-code').mkdir(parents=True)
    assert adapters.dsh_hook_bridge(profile) == 'profile'
