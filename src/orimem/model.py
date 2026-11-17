"""Pinned embedding model (intfloat/multilingual-e5-small) download / import with checksums."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from orimem import config

REPOSITORY = 'intfloat/multilingual-e5-small'
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
FILES = {
    'config.json': '69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959',
    'model.safetensors': '1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477',
    'tokenizer.json': '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39',
    'tokenizer_config.json': 'a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b',
}


def model_dir() -> Path:
    return config.home() / 'models' / 'multilingual-e5-small'


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _write_manifest(dest: Path, source: str) -> None:
    files = [dict(file=n, sha256=s, bytes=(dest / n).stat().st_size) for n, s in FILES.items()]
    (dest / 'manifest.json').write_text(json.dumps(dict(repository=REPOSITORY, revision=REVISION, source=source,
                                                        files=files), indent=2), encoding='utf-8')


def status() -> dict:
    dest = model_dir()
    present = {n: (dest / n).exists() for n in FILES}
    return dict(path=str(dest), ready=all(present.values()) and (dest / 'manifest.json').exists(), files=present)


def import_from(source: Path) -> dict:
    """Copy an already downloaded copy of the pinned revision (checksums are verified)."""
    source = Path(source)
    dest = model_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        if _sha256(source / name) != expected:
            raise ValueError('Checksum mismatch, not the pinned revision: ' + name)
        shutil.copy2(source / name, dest / name)
    _write_manifest(dest, 'import:' + str(source))
    return status()


def download(endpoint: str | None = None) -> dict:
    import requests
    endpoint = (endpoint or config.get('recall.hf_endpoint') or os.environ.get('HF_ENDPOINT')
                or 'https://huggingface.co').rstrip('/')
    dest = model_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        final = dest / name
        if final.exists() and _sha256(final) == expected:
            continue
        tmp = dest / (name + '.part')
        url = f'{endpoint}/{REPOSITORY}/resolve/{REVISION}/{name}?download=true'
        with requests.get(url, stream=True, timeout=(20, 120)) as r:
            r.raise_for_status()
            with open(tmp, 'wb') as f:
                for block in r.iter_content(1024 * 1024):
                    f.write(block)
        if _sha256(tmp) != expected:
            tmp.unlink()
            raise ValueError('Checksum mismatch after download: ' + name)
        os.replace(tmp, final)
    _write_manifest(dest, endpoint)
    return status()
