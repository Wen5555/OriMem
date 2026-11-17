"""After `uv tool upgrade orimem-mcp`: bring an existing setup up to the installed version, once.

Runs when the shared service starts (every agent session starts it) and from `orimem init/install`.
Steps are idempotent and only touch what OriMem already owns:

* scaffold what newer versions add to the vault (e.g. the profile note), never overwriting a file;
* re-install OriMem's own MCP/skill/hook entries for the agents it is already installed into
  (keeping the earlier hooks on/off choice), so hook changes (async capture, quoting) take effect.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import orimem
from orimem import config


def _marker():
    return config.home() / 'installed-version.json'


def recorded() -> str | None:
    try:
        return json.loads(_marker().read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError):
        return None


def mark() -> None:
    config.ensure_home()
    _marker().write_text(json.dumps(dict(version=orimem.__version__,
                                         at=datetime.now(timezone.utc).isoformat(timespec='seconds'))), encoding='utf-8')


def run_if_needed(force: bool = False) -> dict | None:
    if not config.configured():
        return None
    if not force and recorded() == orimem.__version__:
        return None
    from orimem import adapters
    from orimem.cli import scaffold_vault
    result = dict(from_version=recorded(), to_version=orimem.__version__)
    result['vault'] = scaffold_vault(config.vault(), obsidian_setup=False)
    present = adapters.installed()
    for agent, info in present.items():
        result[agent] = adapters.install([agent], hooks=info.get('hooks', True),
                                         dsh_profiles=info.get('profiles')).get(agent)
    mark()
    return result


def connect_new_agents() -> dict | None:
    """Connect agents installed after OriMem (e.g. OriMem set up from Codex, dsh installed later).

    They share the one OriMem home - vault, model, index, settings - so nothing is downloaded again.
    Agents the user removed (`agents.skip`) and setups with `agents.auto_connect = false` are left alone.
    """
    if not config.configured() or not config.get('agents.auto_connect', True):
        return None
    from orimem import adapters
    found = [a for a, present in adapters.detect().items() if present]
    new = [a for a in found if a not in adapters.connected() and a not in (config.get('agents.skip') or [])]
    if not new:
        return None
    result = adapters.install(new)
    record = dict(at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  connected={a: ('error' not in (result.get(a) or {})) for a in new})
    config.ensure_home()
    (config.home() / 'agents-auto.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    return result
