"""Debounced Markdown updates and reconciled legacy-source delta import."""

from pathlib import Path
import threading, time, subprocess, sys, json
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .source_stage import stage_sources
from .continuous import project
from .privacy import sanitize

def adapter_db_for(session_id):
    """Adapter DB that mirrors a captured session (by session-id prefix)."""
    from orimem.capture import common
    sid = str(session_id)
    name = 'claude' if sid.startswith('claude-') else 'dsh' if sid.startswith('dsh-') else 'codex'
    return common.adapter_db(name)


class Watcher:
    def __init__(self, backend, collect=False):
        self.backend = backend
        self.collect = collect
        policy=backend.db.parent/'capture-runtime.json'
        if collect and policy.exists():
            configured=json.loads(policy.read_text(encoding='utf-8'))
            if configured.get('version')!=1 or configured.get('mode') not in {'on-demand','independent-worker'}:
                raise ValueError('Unknown capture runtime policy; preserve configuration')
            self.collect=configured['mode']=='on-demand'
        self.stop_event = threading.Event()
        self.dirty = threading.Event()
        self.dirty.set()
        self.last_event = 0
        self.last_import = time.monotonic() - 110   # first transcript scan ~10 s after the first index sync
        self.last_prune = time.monotonic() - 86400 + 600     # first housekeeping 10 minutes after start, then daily
        self.signature = None
        self.process = None
        self.observer = Observer()
        parent = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                paths = [event.src_path, getattr(event, "dest_path", "")]
                if event.event_type in {"created", "modified", "deleted", "moved"} and any(
                    p.endswith(".md") and ".obsidian" not in Path(p).parts for p in paths
                ):
                    parent.last_event = time.monotonic()
                    parent.dirty.set()

        self.observer.schedule(Handler(), str(backend.vault), recursive=True)

    def start(self):
        self.observer.start()
        self.thread = threading.Thread(target=self.run, daemon=True, name="orion-index-watch")
        self.thread.start()
        return self

    def run(self):
        last_sync = 0
        while not self.stop_event.wait(1):
            at = time.monotonic()
            try:
                if self.collect and at - self.last_import >= 120:
                    self.last_import = at
                    from orimem.capture import collect
                    result = collect(project=False, backend=self.backend)
                    if any(r.get('captured') or r.get('excluded') for r in result.get('sources', {}).values()):
                        self.dirty.set()
                    self.backend.lifecycle.health_stage('capture')
            except Exception as error:
                self.backend.lifecycle.health_stage('capture', error)
            if at - self.last_prune >= 86400:
                self.last_prune = at
                try:
                    from orimem.maintenance import prune
                    prune()
                except Exception:
                    pass
            # Source projection has its own durable jobs and error state. It
            # must run before, and independently of, embedding or synthesis.
            if self.collect:
                try:
                    project(self.backend)
                except Exception as error:
                    self.backend.lifecycle.health_stage('projection', error)
            try:
                if (self.dirty.is_set() and at - self.last_event >= 3) or at - last_sync >= 60:
                    self.dirty.clear()
                    self.backend.sync()
                    last_sync = at
            except Exception as error:
                self.backend.last_error = sanitize(str(error))
                self.backend.lifecycle.health_stage('index', error)
                # Retry once at the next reconciliation, not a tight failure loop.
                last_sync = at
                self.dirty.clear()

    def close(self):
        self.stop_event.set()
        self.observer.stop()
        self.observer.join(5)
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
