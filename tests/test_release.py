"""Release metadata stays consistent: one version everywhere, registry ownership marker present."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def versions():
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    init = (ROOT / 'src' / 'orimem' / '__init__.py').read_text(encoding='utf-8')
    server = json.loads((ROOT / 'server.json').read_text(encoding='utf-8'))
    return {
        'pyproject': pyproject['project']['version'],
        '__init__': init.split('"')[1],
        'server.json': server['version'],
        'server.json package': server['packages'][0]['version'],
        'claude plugin': json.loads((ROOT / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))['version'],
        'dsh bundle': json.loads((ROOT / 'integrations' / 'dsh' / 'package.json').read_text(encoding='utf-8'))['version'],
    }


def test_single_version():
    found = versions()
    assert len(set(found.values())) == 1, found


def test_registry_identity():
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    server = json.loads((ROOT / 'server.json').read_text(encoding='utf-8'))
    assert server['packages'][0]['identifier'] == pyproject['project']['name'] == 'orimem-mcp'
    assert pyproject['project']['scripts']['orimem'] == 'orimem.cli:main'
    readme = (ROOT / pyproject['project']['readme']).read_text(encoding='utf-8')
    assert f'mcp-name: {server["name"]} ' in readme or f'mcp-name: {server["name"]}-->' in readme \
        or f'mcp-name: {server["name"]}\n' in readme
