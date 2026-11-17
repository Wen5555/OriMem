"""Project folders: recognise them, and link a new one to a project page automatically.

``projects.link`` (default ``"auto"``): the first time an agent session starts in a folder that is
not linked to any entry, OriMem

* finds the project root (git checkout - a worktree counts as its main checkout - or the nearest
  folder with a build/manifest file such as package.json or pyproject.toml);
* if an entry with the same name or alias already exists, lets the agent ask the user whether to
  bind the folder to it (never guesses);
* otherwise creates ``<projects>/<folder name>.md`` bound to that root, imports the folder's
  conversations from before the install as evidence (``projects.import_history``), and tells the
  agent, which fills in an overview during the session.

Folders without a project marker (and with ``projects.link = "offer"``) only get a one-time
suggestion; home, temp, drive roots and the vault itself are never touched. ``"off"`` disables both.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from orimem import config

MARKERS = ('package.json', 'pyproject.toml', 'setup.py', 'Cargo.toml', 'go.mod', 'pom.xml', 'build.gradle',
           'build.gradle.kts', 'CMakeLists.txt', 'composer.json', 'Gemfile', 'deno.json', 'requirements.txt',
           'pubspec.yaml', 'Package.swift', 'mix.exs')
SUFFIX_MARKERS = ('.sln', '.csproj', '.xcodeproj')


def mode() -> str:
    value = str(config.get('projects.link') or 'auto').lower()
    return value if value in ('auto', 'offer', 'off') else 'auto'


def trivial(cwd, vault=None) -> bool:
    """Home, OriMem's home, temp folders, drive roots and the vault are never projects."""
    try:
        here = Path(cwd).resolve()
        homes = {Path.home().resolve(), config.home().resolve()}
        temps = {Path(tempfile.gettempdir()).resolve()}
        vault_path = Path(vault or config.vault()).resolve()
    except (OSError, ValueError):
        return True
    return (here in homes or here.parent == here or here == vault_path or vault_path in here.parents
            or any(here == t or t in here.parents for t in temps))


def _has_marker(folder: Path) -> bool:
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return any(n in MARKERS for n in names) or any(n.endswith(SUFFIX_MARKERS) for n in names)


def project_root(cwd) -> Path | None:
    """The project folder containing ``cwd``: git checkout first, else the nearest marker folder."""
    from orimem.core.task_context import workspace_variants
    try:
        here = Path(cwd).resolve()
    except (OSError, ValueError):
        return None
    stop = {Path.home().resolve()}
    chain = [here, *list(here.parents)[:8]]
    marker_root = None
    for folder in chain:
        if folder in stop or folder.parent == folder:
            break
        git = folder / '.git'
        if git.is_dir() or git.is_file():
            variants = workspace_variants(str(folder))
            return Path(variants[-1])           # a linked worktree resolves to its main checkout
        if marker_root is None and _has_marker(folder):
            marker_root = folder
    return marker_root


def entry_name(root: Path) -> str:
    """A valid note name from the project folder name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', root.name).strip().rstrip('. ')
    return (name or 'project')[:100]


def older_conversations(cwd) -> int:
    """Rough count of Claude Code / dsh sessions started in ``cwd`` before ``capture.since`` (folder names only)."""
    from datetime import datetime
    since = config.get('capture.since')
    try:
        limit = datetime.fromisoformat(str(since).replace('Z', '+00:00')).timestamp() if since else None
    except ValueError:
        limit = None
    encoded = re.sub(r'[^A-Za-z0-9]', '-', str(cwd))
    folders = [config.source_dir('claude') / encoded,
               config.source_dir('dsh') / ('--' + re.sub(r'[^A-Za-z0-9]+', '-', str(cwd)).strip('-') + '--')]
    count = 0
    for folder in folders:
        try:
            for item in folder.iterdir():
                if limit is None or item.stat().st_mtime < limit:
                    count += 1
        except OSError:
            continue
    return count


def link_workspace(backend, cwd) -> dict:
    """Create (or bind) the entry for ``cwd``'s project; import its older conversations. Idempotent."""
    from orimem.core.records import initialize
    root = project_root(cwd)
    if root is None or trivial(root, backend.vault):
        return dict(linked=False, reason='not a project folder')
    result = initialize(backend, entry_name(root), 'project', workspace=str(root), origin='auto')
    out = dict(linked='path' in result and not result.get('binding_conflict') and not result.get('similar_entries'),
               root=str(root), **{k: v for k, v in result.items() if k in ('path', 'similar_entries', 'binding_conflict')})
    if out['linked'] and config.get('projects.import_history', True) and config.get('capture.enabled', True):
        from orimem import onboard
        try:
            out['history'] = onboard.import_workspaces([str(root)]).get('imported_sessions')
        except Exception as error:             # history is a bonus; the entry itself is done
            out['history_error'] = str(error)[:200]
    return out
