"""``orimem`` command line."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from orimem import config


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _ask(question: str, default: str) -> str:
    if not sys.stdin.isatty():
        return default
    answer = input(f'{question} [{default}]: ').strip()
    return answer or default


def _yes(text: str) -> bool:
    return text.strip().lower() in ('y', 'yes', '是', 'true', '1', 'on')


def _agents(value: str | None) -> list[str]:
    from orimem import adapters
    if not value or value == 'auto':
        found = adapters.detect()
        return [a for a in adapters.AGENTS if found.get(a)]
    agents = [a.strip() for a in value.split(',') if a.strip()]
    bad = [a for a in agents if a not in adapters.AGENTS]
    if bad:
        raise SystemExit('Unknown agent(s): ' + ', '.join(bad) + ' (choose from claude, codex, dsh)')
    return agents


def scaffold_vault(vault: Path) -> list[str]:
    created = []
    for folder in sorted(config.folders()) + ['50 日记/每日', '50 日记/每周', '90 归档/Codex佐证']:
        path = vault / folder
        if not path.exists():
            path.mkdir(parents=True)
            created.append(folder)
    rules = vault / 'AGENTS.md'
    if not rules.exists():
        template = Path(__file__).resolve().parent / 'templates' / 'vault' / 'AGENTS.md'
        rules.write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
        created.append('AGENTS.md')
    return created


def cmd_init(a) -> None:
    from orimem import adapters, daily, model
    existing = config.CONFIG_PATH.exists()
    data = {}
    if existing:
        import tomllib
        data = tomllib.loads(config.CONFIG_PATH.read_text(encoding='utf-8'))
    vault = a.vault or data.get('vault') or _ask('Obsidian 库路径（不存在会新建）', str(config.home() / 'vault'))
    data['vault'] = str(Path(vault).expanduser().resolve())
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
    out: dict = dict(config=str(config.CONFIG_PATH), vault=data['vault'],
                     capture=capture['enabled'], daily_summary=summary['enabled'])
    out['vault_created'] = scaffold_vault(config.vault())
    if not a.skip_model:
        try:
            out['model'] = model.import_from(Path(a.model_from)) if a.model_from else model.download(a.hf_endpoint)
        except Exception as error:
            out['model'] = dict(error=str(error), hint='稍后运行 orimem model download 或 orimem model import <目录>')
    agents = _agents(a.agents)
    out['agents'] = adapters.install(agents, hooks=not a.no_hooks) if agents else 'none detected'
    if summary['enabled'] is True:
        try:
            out['daily_schedule'] = daily.apply_schedule()
        except Exception as error:
            out['daily_schedule'] = dict(error=str(error))
    _print(out)


def cmd_install(a) -> None:
    from orimem import adapters
    _print(adapters.install(_agents(a.agent), hooks=not a.no_hooks))


def cmd_uninstall(a) -> None:
    from orimem import adapters
    _print(adapters.uninstall(_agents(a.agent)))


def cmd_snippet(a) -> None:
    from orimem import adapters
    _print(adapters.generic_snippets())


def cmd_capture(a) -> None:
    from orimem.capture import collect, module
    time.sleep(min(max(a.delay, 0), 30))
    only = None
    if a.path:
        if not a.source:
            raise SystemExit('--path requires --source')
        root = module(a.source).root().resolve()
        candidate = Path(a.path).resolve()
        only = str(candidate) if candidate.is_relative_to(root) else None
    _print(collect([a.source] if a.source else None, backfill=a.backfill, only_path=only))


def cmd_config(a) -> None:
    if a.action == 'path':
        print(config.CONFIG_PATH)
    elif a.action == 'get':
        _print(config.get(a.key) if a.key else config.load())
    else:
        if not a.key or a.value is None:
            raise SystemExit('usage: orimem config set <key> <value>')
        config.set_value(a.key, config.parse_value(a.value))
        result = dict(key=a.key, value=config.get(a.key))
        if a.key.startswith('daily_summary.'):
            from orimem import daily
            result['schedule'] = daily.apply_schedule()
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
        _print(model.download(a.endpoint))
    elif a.action == 'import':
        if not a.path:
            raise SystemExit('usage: orimem model import <dir>')
        _print(model.import_from(Path(a.path)))
    else:
        _print(model.status())


def cmd_doctor(a) -> None:
    from orimem import adapters, daily, model
    import importlib.util
    deps = {m: importlib.util.find_spec(m) is not None
            for m in ('numpy', 'torch', 'transformers', 'watchdog', 'jsonschema', 'yaml', 'zstandard', 'requests')}
    out = dict(home=str(config.home()), config_exists=config.CONFIG_PATH.exists(), vault=str(config.vault()),
               vault_exists=config.vault().is_dir(), db_exists=config.db().exists(), model=model.status(),
               dependencies=deps, agents_detected=adapters.detect(), capture=config.get('capture'),
               daily_summary=daily.status())
    _print(out)


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
                                epilog='Low-level passthrough: orimem journal|write|lifecycle --help')
    sub = p.add_subparsers(dest='command', required=True)

    s = sub.add_parser('init', help='first-time setup: config, vault, model, agent integration')
    s.add_argument('--vault')
    s.add_argument('--capture', choices=['on', 'off'])
    s.add_argument('--daily', choices=['on', 'off', 'ask'])
    s.add_argument('--agents', help='comma list of claude,codex,dsh; default: auto-detect')
    s.add_argument('--no-hooks', action='store_true')
    s.add_argument('--skip-model', action='store_true')
    s.add_argument('--model-from', help='import an existing local copy of the pinned model')
    s.add_argument('--hf-endpoint', help='e.g. https://hf-mirror.com')
    s.set_defaults(fn=cmd_init)

    for name, fn in (('install', cmd_install), ('uninstall', cmd_uninstall)):
        s = sub.add_parser(name, help=f'{name} MCP server, skill and hooks for agents')
        s.add_argument('--agent', help='comma list of claude,codex,dsh; default: auto-detect')
        if name == 'install':
            s.add_argument('--no-hooks', action='store_true')
        s.set_defaults(fn=fn)

    s = sub.add_parser('snippet', help='print MCP JSON / AGENTS.md snippets for other agents')
    s.set_defaults(fn=cmd_snippet)

    s = sub.add_parser('capture', help='incrementally capture conversations into the vault')
    s.add_argument('--source', choices=['claude', 'codex', 'dsh'])
    s.add_argument('--path', help='one transcript (must live under that source folder)')
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
    s.set_defaults(fn=cmd_model)

    s = sub.add_parser('doctor', help='show installation status')
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
    argv = list(sys.argv[1:] if argv is None else argv)
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
