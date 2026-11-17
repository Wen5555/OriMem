"""Pinned embedding model (intfloat/multilingual-e5-small): download / import with checksums.

Default files are the ONNX export of the pinned revision (used through onnxruntime);
``--torch`` additionally fetches the safetensors weights for the optional PyTorch backend.
Downloads pick the first reachable endpoint: recall.hf_endpoint, $HF_ENDPOINT,
huggingface.co, then the hf-mirror.com mirror.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from orimem import config

REPOSITORY = 'intfloat/multilingual-e5-small'
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
COMMON = {
    'config.json': '69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959',
    'tokenizer.json': '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39',
    'tokenizer_config.json': 'a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b',
}
ONNX = {'onnx/model.onnx': 'ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665'}
TORCH = {'model.safetensors': '1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477'}
MIRRORS = ('https://huggingface.co', 'https://hf-mirror.com')


def model_dir() -> Path:
    return config.home() / 'models' / 'multilingual-e5-small'


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _present(dest: Path, files: dict) -> bool:
    return all((dest / name).exists() for name in files)


def _write_manifest(dest: Path, source: str) -> None:
    files = [dict(file=n, sha256=s, bytes=(dest / n).stat().st_size)
             for n, s in {**COMMON, **ONNX, **TORCH}.items() if (dest / n).exists()]
    (dest / 'manifest.json').write_text(json.dumps(dict(repository=REPOSITORY, revision=REVISION, source=source,
                                                        files=files), indent=2), encoding='utf-8')


def status() -> dict:
    dest = model_dir()
    onnx = _present(dest, {**COMMON, **ONNX})
    torch = _present(dest, {**COMMON, **TORCH})
    return dict(path=str(dest), ready=(onnx or torch) and (dest / 'manifest.json').exists(),
                onnx=onnx, torch=torch)


def import_from(source: Path) -> dict:
    """Copy an already downloaded copy of the pinned revision (any of its files; checksums verified)."""
    source = Path(source)
    dest = model_dir()
    copied = 0
    for name, expected in {**COMMON, **ONNX, **TORCH}.items():
        if not (source / name).exists():
            continue
        if _sha256(source / name) != expected:
            raise ValueError('Checksum mismatch, not the pinned revision: ' + name)
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, dest / name)
        copied += 1
    if not (_present(dest, {**COMMON, **ONNX}) or _present(dest, {**COMMON, **TORCH})):
        raise ValueError('Source has neither the ONNX nor the PyTorch file set of the pinned revision')
    _write_manifest(dest, 'import:' + str(source))
    return dict(status(), copied=copied)


def endpoints(explicit: str | None = None) -> list[str]:
    first = [e for e in (explicit, config.get('recall.hf_endpoint'), os.environ.get('HF_ENDPOINT')) if e]
    return [e.rstrip('/') for e in first] + [m for m in MIRRORS if m not in first]


def _reachable(endpoint: str) -> bool:
    import requests
    try:
        url = f'{endpoint}/{REPOSITORY}/resolve/{REVISION}/config.json'
        return requests.head(url, timeout=6, allow_redirects=True).status_code < 400
    except requests.RequestException:
        return False


def _fetch(endpoint: str, name: str, expected: str, dest: Path, quiet: bool) -> None:
    import requests
    final = dest / name
    if final.exists() and _sha256(final) == expected:
        return
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(final.name + '.part')
    url = f'{endpoint}/{REPOSITORY}/resolve/{REVISION}/{name}?download=true'
    with requests.get(url, stream=True, timeout=(20, 120)) as r:
        r.raise_for_status()
        total = int(r.headers.get('content-length') or 0)
        done = 0
        with open(tmp, 'wb') as f:
            for block in r.iter_content(1024 * 1024):
                f.write(block)
                done += len(block)
                if not quiet and total > 50 * 1024 * 1024:
                    print(f'\r  {name}: {done * 100 // total:3d}%  {done >> 20}/{total >> 20} MB', end='',
                          file=sys.stderr, flush=True)
    if not quiet and total > 50 * 1024 * 1024:
        print(file=sys.stderr)
    if _sha256(tmp) != expected:
        tmp.unlink()
        raise ValueError('Checksum mismatch after download: ' + name)
    os.replace(tmp, final)


def download(endpoint: str | None = None, torch: bool = False, quiet: bool = False) -> dict:
    dest = model_dir()
    files = {**COMMON, **ONNX, **(TORCH if torch else {})}
    errors = []
    for candidate in endpoints(endpoint):
        if not _reachable(candidate):
            errors.append(candidate + ': unreachable')
            continue
        if not quiet:
            print(f'Downloading {REPOSITORY} ({REVISION[:7]}) from {candidate}', file=sys.stderr)
        try:
            for name, expected in files.items():
                _fetch(candidate, name, expected, dest, quiet)
        except Exception as error:      # try the next endpoint; completed files are kept
            errors.append(f'{candidate}: {error}')
            continue
        _write_manifest(dest, candidate)
        return dict(status(), endpoint=candidate)
    raise RuntimeError('Model download failed: ' + '; '.join(errors))
