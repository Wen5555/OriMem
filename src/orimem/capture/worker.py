"""Capture-only backend helper: stages and projects without loading an embedding model."""
from pathlib import Path
import sqlite3
from contextlib import closing


class CaptureOnly:
    def __init__(self, db):
        self.model_id = 'unconfigured-local-model'
        if Path(db).exists():
            with closing(sqlite3.connect(Path(db).as_uri() + '?mode=ro', uri=True)) as c:
                try:
                    row = c.execute("SELECT value FROM meta WHERE key='model'").fetchone()
                except sqlite3.Error:
                    row = None
                if row:
                    self.model_id = row[0]

    @property
    def tokenizer(self):
        raise RuntimeError('Capture worker never loads a tokenizer')

    def encode(self, *args, **kwargs):
        raise RuntimeError('Capture worker never loads an embedding model')
