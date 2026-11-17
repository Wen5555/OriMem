"""Housekeeping for ``ORIMEM_HOME``: keep write-back backups and bookkeeping from growing forever.

Every verified write keeps a backup of the notes it replaced, a receipt and a queue entry. Evidence
projections rewrite a transcript note on every captured turn, so without retention these grow by
thousands of entries a month. ``prune()`` keeps:

* backups of evidence projections for ``maintenance.projection_backup_days`` (default 3);
* every other backup for ``maintenance.backup_days`` (default 30);
* queue/prepared entries of finished batches for ``maintenance.backup_days``;
* receipts of evidence projections for ``maintenance.backup_days``, except the receipt each
  current projection still points to. Receipts of agent writes are never deleted.

Install backups (``backups/install-*``) are left alone. The service runs this once a day;
``orimem prune`` runs it on demand.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from orimem import config, journal_state

PROJECTION = 'projection-'


def _days(key: str, default: int) -> int:
    try:
        return max(0, int(config.get(key) or default))
    except (TypeError, ValueError):
        return default


def _size(path: Path) -> int:
    total = 0
    for item in path.rglob('*') if path.is_dir() else []:
        try:
            total += item.stat().st_size if item.is_file() else 0
        except OSError:
            pass
    return total


def usage() -> dict:
    home = config.home()
    return {name: round(_size(home / name) / 1_048_576, 1) for name in ('backups', 'receipts', 'queue', 'prepared')}


def _live_receipts(db: Path) -> set[str]:
    if not db.exists():
        return set()
    try:
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as c:
            return {Path(r[0]).stem for r in c.execute('SELECT receipt FROM source_projections') if r[0]}
    except sqlite3.Error:
        return set()


def prune(dry_run: bool = False) -> dict:
    home = config.home()
    now = time.time()
    keep = _days('maintenance.backup_days', 30) * 86400
    keep_projection = _days('maintenance.projection_backup_days', 3) * 86400
    out = dict(backups=0, receipts=0, queue=0, prepared=0, freed_mb=0.0, dry_run=dry_run)
    freed = 0

    def old(path: Path, limit: float) -> bool:
        try:
            return now - path.stat().st_mtime > limit
        except OSError:
            return False

    def remove(path: Path, kind: str) -> None:
        nonlocal freed
        freed += _size(path) if path.is_dir() else (path.stat().st_size if path.exists() else 0)
        out[kind] += 1
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)

    backups = home / 'backups'
    for item in sorted(backups.iterdir()) if backups.is_dir() else []:
        if not item.is_dir() or item.name.startswith('install-'):
            continue
        ident = item.name.split('-', 1)[1] if '-' in item.name else item.name
        if old(item, keep_projection if ident.startswith(PROJECTION) else keep):
            remove(item, 'backups')

    for folder, kind in ((home / 'queue', 'queue'), (home / 'prepared', 'prepared')):
        for item in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            if not old(item, keep):
                continue
            if kind == 'queue':
                try:
                    if json.loads(item.read_text(encoding='utf-8')).get('status') not in ('committed', 'superseded'):
                        continue          # unfinished batches stay for retry
                except (OSError, ValueError):
                    continue
            remove(item, kind)

    live = _live_receipts(config.db())
    state_path = home / 'state.json'
    receipts = home / 'receipts'
    stale = [p for p in (sorted(receipts.glob(PROJECTION + '*.json')) if receipts.is_dir() else [])
             if p.stem not in live and old(p, keep)]
    if stale:
        for path in stale:
            remove(path, 'receipts')
        if not dry_run and state_path.exists():
            with journal_state.locked(state_path):
                current = journal_state.load(state_path)
                for path in stale:
                    current['receipts'].pop(path.stem, None)
                journal_state.save(state_path, current)
    out['freed_mb'] = round(freed / 1_048_576, 1)
    return out
