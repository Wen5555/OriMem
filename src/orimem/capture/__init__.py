"""Conversation capture: per-agent transcript parsers feeding one shared pipeline."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from orimem import config
from orimem.capture import common, formats


def format_report() -> dict:
    return formats.report()

SOURCES = ('claude', 'codex', 'dsh')


def module(name: str):
    if name not in SOURCES:
        raise ValueError('Unknown capture source: ' + name)
    from importlib import import_module
    return import_module('orimem.capture.' + name)


def _since() -> float | None:
    value = config.get('capture.since')
    if not value:
        # Never backfill implicitly: older history is imported only on request (`orimem onboard`).
        from datetime import timezone
        now = datetime.now(timezone.utc)
        if config.configured():
            config.set_value('capture.since', now.isoformat(timespec='seconds'))
        return now.timestamp()
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def _state_path() -> Path:
    return common.sources_dir() / 'scan-state.json'


def _load_state() -> dict:
    path = _state_path()
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _save_state(state: dict) -> None:
    path = _state_path()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def resolve(source: str, session: str | None = None, path: str | None = None) -> str | None:
    """One transcript of ``source``, by explicit path (must live under that source's folder) or session id."""
    mod = module(source)
    root = mod.root().resolve()
    candidate = None
    if path:
        p = Path(path).resolve()
        candidate = p if p.is_file() and p.is_relative_to(root) else None
    if candidate is None and session:
        candidate = mod.find(session)
    return str(candidate) if candidate else None


def collect(sources=None, backfill: bool = False, project: bool = True, only_path: str | None = None, backend=None,
            only_cwd=None, since: float | None = None) -> dict:
    """Incrementally mirror changed transcripts, stage them, then project into the vault.

    Sessions whose files were last modified before ``capture.since`` are not
    backfilled unless ``backfill`` is set. Disabled capture is a no-op.
    """
    if not config.get('capture.enabled', True):
        return dict(enabled=False, reason='capture.enabled is false')
    wanted = [s for s in (sources or config.get('capture.sources') or SOURCES) if s in SOURCES]
    if since is None:
        # An explicitly named transcript (hook, capture_now) is captured whatever its age.
        since = None if (backfill or only_path) else _since()
    out: dict = dict(enabled=True, sources={})
    with common.file_lock(common.sources_dir() / 'collect.lock') as acquired:
        if not acquired:
            return dict(enabled=True, skipped='another capture run is active')
        state = _load_state()
        backend = None
        for name in wanted:
            mod = module(name)
            seen = state.setdefault(name, {})
            report = dict(scanned=0, captured=0, excluded=0, errors=0)
            paths = [Path(only_path)] if only_path else list(mod.discover())
            for path in paths:
                try:
                    mtime = path.stat().st_mtime_ns
                except OSError:
                    continue
                report['scanned'] += 1
                key = str(path)
                if since is not None and mtime / 1e9 < since:
                    continue
                if seen.get(key) == mtime and only_cwd is None:
                    continue
                try:
                    result = mod.capture_file(path, only_cwd=only_cwd) if only_cwd is not None else mod.capture_file(path)
                except Exception as error:      # fail closed for this file; next run retries
                    report['errors'] += 1
                    common.log(dict(op='capture', source=name, path=key, error=type(error).__name__ + ': ' + str(error)[:300]))
                    continue
                if result.get('skipped'):
                    continue                # outside an onboarding selection: not processed, not marked seen
                seen[key] = mtime
                if only_cwd is not None and result.get('captured'):
                    report.setdefault('sessions', []).append(result['session_id'])
                formats.record(name, key, result.get('format'))
                if (result.get('format') or {}).get('warnings'):
                    report['format_warnings'] = report.get('format_warnings', 0) + 1
                report['captured' if result.get('captured') else 'excluded'] += 1
            if report['captured'] or report['excluded']:
                backend = backend or common.capture_backend()
                report['stage'] = common.stage(name, mod.NAMESPACE, backend)
            out['sources'][name] = report
        _save_state(state)
        if project and any(r.get('stage') for r in out['sources'].values()):
            from orimem.core.continuous import project as project_sessions
            backend = backend or common.capture_backend()
            try:
                out['projection'] = project_sessions(backend)
            except Exception as error:
                out['projection'] = dict(error=type(error).__name__ + ': ' + str(error)[:300])
    common.log(dict(op='collect', summary={k: {kk: vv for kk, vv in v.items() if kk != 'stage'} for k, v in out['sources'].items()}))
    return out
