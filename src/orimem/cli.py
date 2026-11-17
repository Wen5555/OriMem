"""``orimem`` command line."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from orimem import config


def _utf8_streams() -> None:
    """Pipes/redirects on Windows default to the ANSI code page (e.g. cp1252), which cannot encode
    note titles or paths in other scripts. Use UTF-8 there (Windows consoles already report UTF-8)."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        encoding = (getattr(stream, 'encoding', '') or '').lower().replace('-', '')
        if encoding != 'utf8' and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


ASSUME_DEFAULTS = False


def _ask(question: str, default: str) -> str:
    if ASSUME_DEFAULTS or not sys.stdin.isatty():
        return default
    try:
        answer = input(f'{question} [{default}]: ').strip()
    except EOFError:            # e.g. stdin is NUL on Windows, which reports itself as a terminal
        return default
    return answer or default


def _yes(text: str) -> bool:
    return text.strip().lower() in ('y', 'yes', '是', 'true', '1', 'on')


def _agents(value: str | None) -> list[str]:
    from orimem import adapters
    if value == 'none':
        return []
    if not value or value == 'auto':
        found = adapters.detect()
        return [a for a in adapters.AGENTS if found.get(a)]
    agents = [a.strip() for a in value.split(',') if a.strip()]
    bad = [a for a in agents if a not in adapters.AGENTS]
    if bad:
        raise SystemExit('Unknown agent(s): ' + ', '.join(bad) + ' (choose from claude, codex, dsh)')
    return agents


def scaffold_vault(vault: Path, obsidian_setup: bool = True) -> list[str]:
    """Create the layout's folders; on a fresh vault also AGENTS.md and the Obsidian first-run files."""
    from orimem import obsidian
    lay = config.layout()
    created = []
    vault.mkdir(parents=True, exist_ok=True)
    for folder in config.folders():
        path = vault / folder
        if not path.exists():
            path.mkdir(parents=True)
            created.append(folder)
    rules = vault / 'AGENTS.md'
    if not rules.exists():
        templates = Path(__file__).resolve().parent / 'templates' / 'vault'
        template = templates / f"AGENTS.{lay['language']}.md"
        if not template.exists():
            template = templates / 'AGENTS.en.md'
        rules.write_text(template.read_text(encoding='utf-8').format(**lay), encoding='utf-8')
        created.append('AGENTS.md')
        if obsidian_setup:
            created += obsidian.first_run(vault, lay)
    profile = vault / (lay['profile'] + '.md')
    if not profile.exists():
        # Also added to vaults set up before profiles existed; the untouched template is never shared.
        templates = Path(__file__).resolve().parent / 'templates' / 'vault'
        template = templates / f"profile.{lay['language']}.md"
        if not template.exists():
            template = templates / 'profile.en.md'
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
        created.append(profile.relative_to(vault).as_posix())
    return created


def _resolve_language(a, data: dict, existing: bool) -> None:
    layout = data.setdefault('layout', {})
    if a.lang:
        layout['language'] = a.lang
    elif not layout.get('language'):
        if existing and data.get('journal_from'):
            # Set up by 0.1.x: keep its Chinese layout and transcript folder.
            layout['language'] = 'zh'
            if not layout.get('evidence'):
                layout['evidence'] = config.LEGACY_EVIDENCE
        else:
            detected = config.detect_language()
            answer = _ask('库目录语言 / vault folder language (zh/en)', detected).lower()
            layout['language'] = answer if answer in config.LAYOUTS else detected


def cmd_init(a) -> None:
    from orimem import adapters, daily, model
    global ASSUME_DEFAULTS
    ASSUME_DEFAULTS = a.yes
    existing = config.CONFIG_PATH.exists()
    data = {}
    if existing:
        import tomllib
        data = tomllib.loads(config.CONFIG_PATH.read_text(encoding='utf-8'))
    vault = a.vault or data.get('vault') or _ask('Obsidian 库路径 / vault folder (created if missing)', str(config.home() / 'vault'))
    data['vault'] = str(Path(vault).expanduser().resolve())
    _resolve_language(a, data, existing)
    recall = data.setdefault('recall', {})
    if a.lexical:
        recall['mode'] = 'lexical'

    capture = data.setdefault('capture', {})
    if a.capture is not None:
        capture['enabled'] = a.capture == 'on'
    elif 'enabled' not in capture:
        capture['enabled'] = _yes(_ask('开启对话采集建库？(y/n)', 'y'))
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    capture.setdefault('since', stamp)
    data.setdefault('journal_from', stamp)
    summary = data.setdefault('daily_summary', {})
    if a.daily is not None:
        summary['enabled'] = {'on': True, 'off': False, 'ask': 'ask'}[a.daily]
    elif 'enabled' not in summary:
        answer = _ask('开启每日自动总结？(y/n/later)', 'later').lower()
        summary['enabled'] = True if _yes(answer) else ('ask' if answer.startswith('l') else False)
    config.save(data)
    config.reload()
    out: dict = dict(config=str(config.CONFIG_PATH), vault=data['vault'], language=config.layout()['language'],
                     capture=capture['enabled'], daily_summary=summary['enabled'], recall_mode=config.get('recall.mode'))
    out['vault_created'] = scaffold_vault(config.vault(), obsidian_setup=not a.no_obsidian_setup)
    if config.get('recall.mode') == 'lexical':
        out['model'] = 'skipped: recall.mode = lexical (full-text only; `orimem config set recall.mode hybrid` + `orimem model download` enables vectors)'
    elif not a.skip_model:
        try:
            out['model'] = model.import_from(Path(a.model_from)) if a.model_from else model.download(a.hf_endpoint, torch=a.torch)
        except Exception as error:
            out['model'] = dict(error=str(error), hint='稍后运行 orimem model download 或 orimem model import <目录>')
    agents = _agents(a.agents)
    out['agents'] = adapters.install(agents, hooks=not a.no_hooks, dsh_profiles=a.dsh_profile) if agents else 'none'
    if summary['enabled'] is True:
        try:
            out['daily_schedule'] = daily.apply_schedule()
        except Exception as error:
            out['daily_schedule'] = dict(error=str(error))
    from orimem import obsidian, upgrade
    out['obsidian'] = obsidian.status(config.vault(), config.layout()['language'])
    if config.get('recall.mode') == 'lexical' or (isinstance(out.get('model'), dict) and out['model'].get('ready')):
        try:
            # Build the index now, so the first question an agent asks already has an answer.
            result = _backend().sync()
            out['index'] = dict(notes=result.get('changed', 0))
        except Exception as error:
            out['index'] = dict(error=str(error)[:300], hint='orimem sync')
    upgrade.mark()
    if a.json:
        _print(out)
    else:
        print(_init_summary(out))


def _init_summary(out: dict) -> str:
    zh = out.get('language') == 'zh'
    ok, bad = '✓', '✗'
    lines = ['OriMem 已配置完成。' if zh else 'OriMem is set up.']
    lines.append(('  库：      ' if zh else '  Vault:    ') + out['vault'] + f"  ({out.get('language')})")
    model = out.get('model')
    if out.get('recall_mode') == 'lexical':
        search = '全文检索（不使用模型）' if zh else 'full text only (no model)'
    elif isinstance(model, dict) and model.get('ready'):
        search = ('混合检索（模型已就绪）' if zh else 'hybrid (model ready)')
    else:
        error = model.get('error') if isinstance(model, dict) else model
        search = (f'{bad} 模型未就绪：{error}；稍后运行 orimem model download' if zh
                  else f'{bad} model not ready: {error}; run `orimem model download` later')
    lines.append(('  检索：    ' if zh else '  Search:   ') + search)
    capture = (('开启' if zh else 'on') if out.get('capture') else ('关闭' if zh else 'off'))
    daily = {True: '开启' if zh else 'on', False: '关闭' if zh else 'off'}.get(out.get('daily_summary'), '稍后询问' if zh else 'ask later')
    lines.append(('  采集：    ' if zh else '  Capture:  ') + capture + ('    每日总结：' if zh else '    Daily summary: ') + daily)
    agents = out.get('agents')
    if isinstance(agents, dict):
        parts = []
        for name in ('claude', 'codex', 'dsh'):
            if name in agents:
                info = agents[name] or {}
                parts.append(f"{name} {bad} {info['error'][:80]}" if 'error' in info else f'{name} {ok}')
        lines.append(('  Agent：   ' if zh else '  Agents:   ') + ('  '.join(parts) or '-'))
    else:
        lines.append(('  Agent：   ' if zh else '  Agents:   ') + ('未接入（orimem install 可稍后接入）' if zh else 'none (connect later with `orimem install`)'))
    obs = out.get('obsidian') or {}
    lines.append(('  Obsidian：' if zh else '  Obsidian: ') + (obs.get('next_step') or ''))
    index = out.get('index') or {}
    if 'error' in index:
        lines.append(('  索引：    ' if zh else '  Index:    ') + f"{bad} {index['error']}")
    profile = config.layout()['profile'] + '.md'
    lines.append('')
    lines.append(('下一步：重启各个 agent；在库根目录的 “' + profile + '” 里写下希望所有 agent 记住的个人信息。') if zh else
                 ('Next: restart your agents; write what every agent should know about you in "' + profile + '" at the vault root.'))
    return '\n'.join(lines)


def cmd_link(a) -> None:
    from orimem import projects
    _print(projects.link_workspace(_backend(), a.cwd or str(Path.cwd())))


def cmd_onboard(a) -> None:
    from orimem import onboard
    sources = [a.source] if a.source else None
    if a.action == 'scan':
        _print(onboard.scan(days=a.days if a.days is not None else 90, sources=sources))
    elif a.action == 'import':
        _print(onboard.import_workspaces(a.cwd or [], days=a.days, sources=sources))
    else:
        _print(onboard.status())


def cmd_open(a) -> None:
    from orimem import obsidian
    _print(obsidian.open_vault(config.vault(), config.layout()['language']))


def cmd_install(a) -> None:
    from orimem import adapters, upgrade
    _print(adapters.install(_agents(a.agent), hooks=not a.no_hooks, dsh_profiles=a.dsh_profile))
    upgrade.mark()


def cmd_uninstall(a) -> None:
    from orimem import adapters, daily
    out = adapters.uninstall(_agents(a.agent), dsh_profiles=a.dsh_profile)
    if not a.agent:
        # Removing OriMem from every agent also removes its scheduled daily job.
        try:
            out['daily_schedule'] = daily.apply_schedule(enabled=False)
        except Exception as error:
            out['daily_schedule'] = dict(error=str(error)[:200])
    _print(out)


def cmd_snippet(a) -> None:
    from orimem import adapters
    _print(adapters.generic_snippets())


def cmd_capture(a) -> None:
    from orimem.capture import collect, module
    time.sleep(min(max(a.delay, 0), 30))
    from orimem.capture import resolve
    only = None
    if a.path or a.session:
        if not a.source:
            raise SystemExit('--path/--session require --source')
        only = resolve(a.source, a.session, a.path)
        if only is None:
            _print(dict(captured=False, reason='transcript not found under the configured source folder'))
            return
    _print(collect([a.source] if a.source else None, backfill=a.backfill, only_path=only))


def cmd_config(a) -> None:
    if a.action == 'path':
        print(config.CONFIG_PATH)
    elif a.action == 'get':
        if a.key in ('layout', *('layout.' + r for r in (*config.ROLES, 'language'))):
            # Resolved values: presets fill the roles left empty in config.toml.
            lay = config.layout()
            _print(lay if a.key == 'layout' else lay[a.key.split('.', 1)[1]])
        else:
            _print(config.get(a.key) if a.key else config.load())
    else:
        if not a.key or a.value is None:
            raise SystemExit('usage: orimem config set <key> <value>')
        config.set_value(a.key, config.parse_value(a.value))
        result = dict(key=a.key, value=config.get(a.key))
        if a.key.startswith('daily_summary.'):
            from orimem import daily
            result['schedule'] = daily.apply_schedule()
        if a.key.startswith('layout.'):
            result['created'] = scaffold_vault(config.vault(), obsidian_setup=False)
            result['note'] = 'Existing notes are not moved; move them yourself if you renamed a folder.'
        if (a.key.startswith(('recall.', 'layout.', 'sources.')) or a.key in ('mcp.tools', 'vault', 'timezone')):
            from orimem.core.service import stop_service
            stop_service()
            result['service'] = 'restarted on next use'
            if a.key == 'recall.mode':
                result['next'] = 'run `orimem sync` to rebuild the index for this mode'
        _print(result)


def cmd_daily(a) -> None:
    from orimem import daily
    if a.action == 'run':
        _print(daily.run(dry_run=a.dry_run))
    elif a.action == 'apply':
        _print(daily.apply_schedule())
    else:
        _print(daily.status())


def cmd_model(a) -> None:
    from orimem import model
    if a.action == 'download':
        _print(model.download(a.endpoint, torch=a.torch))
    elif a.action == 'import':
        if not a.path:
            raise SystemExit('usage: orimem model import <dir>')
        _print(model.import_from(Path(a.path)))
    else:
        _print(model.status())


def cmd_prune(a) -> None:
    from orimem import maintenance
    _print(dict(before_mb=maintenance.usage(), result=maintenance.prune(dry_run=a.dry_run)))


def cmd_doctor(a) -> None:
    from orimem import adapters, daily, model
    import importlib.util
    deps = {m: importlib.util.find_spec(m) is not None
            for m in ('numpy', 'onnxruntime', 'tokenizers', 'watchdog', 'jsonschema', 'yaml', 'zstandard', 'requests',
                      'torch', 'transformers')}
    from orimem import obsidian
    from orimem.core.embedding import choose_backend, recall_mode
    if recall_mode() == 'lexical':
        backend = 'none (recall.mode = lexical: full-text only)'
    else:
        try:
            backend = choose_backend(model.model_dir())
        except RuntimeError as error:
            backend = str(error)
    from orimem.capture import format_report
    out = dict(home=str(config.home()), config_exists=config.CONFIG_PATH.exists(), vault=str(config.vault()),
               recall_mode=recall_mode(), embedder=backend, layout=config.layout(),
               obsidian=obsidian.status(config.vault(), config.layout()['language']),
               transcript_formats=format_report(),
               vault_exists=config.vault().is_dir(), db_exists=config.db().exists(), model=model.status(),
               dependencies=deps, agents_detected=adapters.detect(), capture=config.get('capture'),
               daily_summary=daily.status())
    from orimem import maintenance, upgrade
    import orimem
    out.update(version=orimem.__version__, installed_into=adapters.installed(), index=_index_stats(),
               storage_mb=maintenance.usage(), configured=config.configured(), upgraded_to=upgrade.recorded())
    if a.json:
        _print(out)
    else:
        print(_doctor_summary(out))


def _index_stats() -> dict:
    import sqlite3
    from contextlib import closing
    db = config.db()
    if not db.exists():
        return dict(built=False)
    try:
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as c:
            notes = c.execute('SELECT count(*) FROM notes').fetchone()[0]
            last = c.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
            model = c.execute("SELECT value FROM meta WHERE key='model'").fetchone()
        return dict(built=bool(model), notes=notes, last_sync=json.loads(last[0]).get('at') if last else None)
    except (sqlite3.Error, ValueError):
        return dict(built=False)


def _doctor_summary(out: dict) -> str:
    ok, bad, warn = '✓', '✗', '!'
    lines = [f"OriMem {out['version']}  ·  home {out['home']}"]
    if not out['configured']:
        lines.append(f'  {bad} not set up: run `orimem init`')
    lines.append(f"  {ok if out['vault_exists'] else bad} vault  {out['vault']}  ({out['layout']['language']} layout)")
    index = out['index']
    lines.append(f"  {ok if index.get('built') else warn} index  " + (f"{index['notes']} notes, last sync {index.get('last_sync')}"
                                                                 if index.get('built') else 'not built yet (`orimem sync`)'))
    embedder = out['embedder']
    search_ok = out['recall_mode'] == 'lexical' or embedder in ('onnx', 'torch')
    lines.append(f"  {ok if search_ok else bad} search {out['recall_mode']}" + ('' if out['recall_mode'] == 'lexical' else f' ({embedder})'))
    agents = out['installed_into']
    detected = [a for a, found in out['agents_detected'].items() if found]
    lines.append(f"  {ok if agents else warn} agents " + ('  '.join(f"{a}{'' if info.get('hooks') else ' (no hooks)'}" for a, info in agents.items())
                                                       or 'none connected (`orimem install`)')
                 + (f"   (found but not connected: {', '.join(sorted(set(detected) - set(agents)))})" if set(detected) - set(agents) else ''))
    capture = out['capture']
    formats = out['transcript_formats']
    drift = [f"{s}: {v['last_status']}" for s, v in formats.items() if v['last_status'] not in ('ok', 'not checked yet')]
    lines.append(f"  {ok if capture.get('enabled') else warn} capture {'on' if capture.get('enabled') else 'off'}"
                 f" ({', '.join(capture.get('sources') or [])})" + (f'   formats: {"; ".join(drift)}' if drift else ''))
    daily = out['daily_summary']
    lines.append(f"  {ok} daily summary {daily.get('enabled')}" + (f" at {daily.get('time')}" if daily.get('enabled') is True else ''))
    obs = out['obsidian']
    lines.append(f"  {ok if obs.get('vault_registered') else warn} obsidian {obs.get('next_step')}")
    storage = out['storage_mb']
    total = sum(storage.values())
    lines.append(f"  {ok if total < 500 else warn} storage {total:.0f} MB in backups/receipts (`orimem prune` cleans up)")
    missing = [m for m in ('numpy', 'onnxruntime', 'tokenizers', 'watchdog', 'jsonschema', 'yaml', 'zstandard', 'requests')
               if not out['dependencies'].get(m)]
    if missing:
        lines.append(f"  {bad} missing dependencies: {', '.join(missing)}")
    lines.append('  (`orimem doctor --json` shows every detail)')
    return '\n'.join(lines)


def _backend():
    from orimem.core.backend import Backend
    return Backend(config.vault(), config.db())


def cmd_backend(a) -> None:
    if a.command == 'service':
        from orimem.core.service import run_service
        run_service()
        return
    if a.command == 'service-stop':
        from orimem.core.service import stop_service
        _print(stop_service())
        return
    if a.command == 'mcp':
        from orimem.core.mcp_server import serve
        if a.direct:
            serve(_backend(), watch=a.watch, collect=a.watch and config.get('capture.enabled', True))
        else:
            from orimem.core.service import remote_call, ensure
            ensure()
            serve(None, dispatcher=remote_call)
        return
    backend = _backend()
    if a.command == 'sync':
        _print(backend.sync())
    elif a.command == 'health':
        _print(backend.health())
    elif a.command == 'recall':
        _print(backend.recall(a.query, a.scope, mode=a.mode, include_history=a.include_history,
                              include_pending=a.include_pending, time_intent='current'))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='orimem', description='OriMem: local Obsidian memory for coding agents',
                                epilog='Global: orimem --home <dir> <command> overrides ORIMEM_HOME.')
    sub = p.add_subparsers(dest='command', required=True)

    s = sub.add_parser('init', help='first-time setup: config, vault, model, agent integration')
    s.add_argument('--vault')
    s.add_argument('--capture', choices=['on', 'off'])
    s.add_argument('--daily', choices=['on', 'off', 'ask'])
    s.add_argument('--agents', help='comma list of claude,codex,dsh, or none; default: auto-detect')
    s.add_argument('--dsh-profile', action='append', help='only patch these dsh profiles (default: all)')
    s.add_argument('--no-hooks', action='store_true')
    s.add_argument('--skip-model', action='store_true')
    s.add_argument('--model-from', help='import an existing local copy of the pinned model')
    s.add_argument('--hf-endpoint', help='model download endpoint; default tries huggingface.co then hf-mirror.com')
    s.add_argument('--torch', action='store_true', help='also download PyTorch weights (needs orimem[torch])')
    s.add_argument('--lang', choices=sorted(config.LAYOUTS), help='vault folder language; default: system locale')
    s.add_argument('--lexical', action='store_true',
                   help='full-text search only: no embedding model download (switch later with recall.mode)')
    s.add_argument('--no-obsidian-setup', action='store_true', help='do not write .obsidian or the welcome note')
    s.add_argument('-y', '--yes', action='store_true', help='accept defaults for every question')
    s.add_argument('--json', action='store_true', help='print the full result as JSON')
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser('link', help='create or bind the project page for a folder (what the hook does automatically)')
    s.add_argument('--cwd', help='folder inside the project (default: current directory)')
    s.set_defaults(fn=cmd_link)

    s = sub.add_parser('onboard', help='bring conversations from before install into the vault: scan | import | status')
    s.add_argument('action', choices=['scan', 'import', 'status'], nargs='?', default='status')
    s.add_argument('--cwd', action='append', help='import: a working folder to bring in (repeatable)')
    s.add_argument('--days', type=int, help='scan: window in days (default 90, 0 = all); import: only the last N days')
    s.add_argument('--source', choices=['claude', 'codex', 'dsh'])
    s.set_defaults(fn=cmd_onboard)

    s = sub.add_parser('open', help='open the vault in Obsidian (or say how to install/add it)')
    s.set_defaults(fn=cmd_open)

    for name, fn in (('install', cmd_install), ('uninstall', cmd_uninstall)):
        s = sub.add_parser(name, help=f'{name} MCP server, skill and hooks for agents')
        s.add_argument('--agent', help='comma list of claude,codex,dsh; default: auto-detect')
        s.add_argument('--dsh-profile', action='append', help='only patch these dsh profiles (default: all)')
        if name == 'install':
            s.add_argument('--no-hooks', action='store_true')
        s.set_defaults(fn=fn)

    s = sub.add_parser('snippet', help='print MCP JSON / AGENTS.md snippets for other agents')
    s.set_defaults(fn=cmd_snippet)

    s = sub.add_parser('capture', help='incrementally capture conversations into the vault')
    s.add_argument('--source', choices=['claude', 'codex', 'dsh'])
    s.add_argument('--path', help='one transcript (must live under that source folder)')
    s.add_argument('--session', help='one session id of that source')
    s.add_argument('--backfill', action='store_true', help='ignore capture.since')
    s.add_argument('--delay', type=float, default=0)
    s.set_defaults(fn=cmd_capture)

    s = sub.add_parser('config', help='get/set settings')
    s.add_argument('action', choices=['get', 'set', 'path'])
    s.add_argument('key', nargs='?')
    s.add_argument('value', nargs='?')
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser('daily', help='daily summary: status | run | apply')
    s.add_argument('action', choices=['status', 'run', 'apply'], nargs='?', default='status')
    s.add_argument('--dry-run', action='store_true')
    s.set_defaults(fn=cmd_daily)

    s = sub.add_parser('model', help='embedding model: status | download | import <dir>')
    s.add_argument('action', choices=['status', 'download', 'import'], nargs='?', default='status')
    s.add_argument('path', nargs='?')
    s.add_argument('--endpoint')
    s.add_argument('--torch', action='store_true', help='also fetch PyTorch weights')
    s.set_defaults(fn=cmd_model)

    s = sub.add_parser('prune', help='delete old write-back backups and finished batch records (see maintenance.*)')
    s.add_argument('--dry-run', action='store_true')
    s.set_defaults(fn=cmd_prune)

    s = sub.add_parser('doctor', help='show installation status')
    s.add_argument('--json', action='store_true', help='print every detail as JSON')
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser('hook', help='agent hook entry (reads event JSON on stdin)')
    s.add_argument('--agent', required=True, choices=['claude', 'codex', 'dsh'])
    s.set_defaults(fn=lambda a: __import__('orimem.hook', fromlist=['main']).main(a.agent))

    for name in ('mcp', 'service', 'service-stop', 'sync', 'health', 'recall'):
        s = sub.add_parser(name)
        if name == 'mcp':
            s.add_argument('--direct', action='store_true', help='serve in-process instead of via the shared service')
            s.add_argument('--watch', action='store_true', help='with --direct: watch the vault and capture')
        if name == 'recall':
            s.add_argument('query')
            s.add_argument('--scope', action='append')
            s.add_argument('--mode', choices=['hybrid', 'lexical', 'semantic'], default='hybrid')
            s.add_argument('--include-history', action='store_true')
            s.add_argument('--include-pending', action='store_true')
        s.set_defaults(fn=cmd_backend)
    return p


PASSTHROUGH = {
    'journal': ('orimem.journal_state', 'state/receipt queries: seen, window, weekly-ready, ...'),
    'write': ('orimem.journal_writer', 'verified writer: apply, retry, supersede'),
    'lifecycle': ('orimem.lifecycle_ctl', 'lifecycle job control'),
}


def main(argv=None) -> None:
    _utf8_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == '--home':
        # Explicit home for processes started by agents/schedulers that may not inherit ORIMEM_HOME.
        import os
        os.environ['ORIMEM_HOME'] = str(Path(argv[1]).expanduser().resolve())
        config.reload()
        argv = argv[2:]
    if argv and argv[0] in PASSTHROUGH:
        from importlib import import_module
        sys.argv = ['orimem ' + argv[0]] + argv[1:]
        import_module(PASSTHROUGH[argv[0]][0]).main()
        return
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        from orimem.core.privacy import sanitize
        print(json.dumps({'error': sanitize(str(error))}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
