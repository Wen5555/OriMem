import io
import json
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated ORIMEM_HOME, user home and agent homes; no real agent CLI on PATH."""
    home = tmp_path / 'user'
    home.mkdir()
    monkeypatch.setenv('ORIMEM_HOME', str(tmp_path / 'orimem'))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CODEX_HOME', str(home / '.codex'))
    monkeypatch.setenv('DSH_HOME', str(home / '.dsh'))
    monkeypatch.setenv('PATH', os.path.dirname(sys.executable))
    from orimem import config
    config.reload()
    data = dict(vault=str(tmp_path / 'vault'),
                capture=dict(enabled=True, sources=['claude', 'codex', 'dsh'], since=''),
                sources=dict(claude=str(home / '.claude' / 'projects'), codex=str(home / '.codex' / 'sessions'),
                             dsh=str(home / '.dsh' / 'sessions')))
    config.save(data)
    config.reload()
    (tmp_path / 'vault').mkdir()
    yield dict(tmp=tmp_path, home=home, vault=tmp_path / 'vault')
    config.reload()


def write_jsonl(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n', encoding='utf-8')
    return path


def write_zstd_jsonl(path: Path, records) -> Path:
    import zstandard
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n').encode('utf-8')
    path.write_bytes(zstandard.ZstdCompressor().compress(raw))
    return path


CLAUDE_UUID = '11111111-2222-4333-8444-555555555555'
CODEX_ID = '019a0000-0000-7000-8000-00000000c0de'
DSH_ID = 'session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def claude_records(cwd):
    base = dict(cwd=cwd, version='2.1.0', entrypoint='claude-desktop', isSidechain=False)
    return [
        dict(type='user', uuid='u1', timestamp='2026-11-17T01:00:00Z', origin=dict(kind='human'),
             message=dict(role='user', content='帮我整理 OriMem 的打包方案'), **base),
        dict(type='user', uuid='u-inj', timestamp='2026-11-17T01:00:01Z', origin=dict(kind='human'),
             message=dict(role='user', content=[dict(type='text', text='<system-reminder>ignore</system-reminder>')]), **base),
        dict(type='assistant', uuid='a1', timestamp='2026-11-17T01:00:05Z',
             message=dict(role='assistant', id='msg1', model='claude-x', stop_reason='end_turn',
                          content=[dict(type='thinking', thinking='secret'), dict(type='text', text='方案：MCP 为核心。')]), **base),
    ]


def codex_records(cwd, thread_source='user'):
    return [
        dict(timestamp='2026-11-17T02:00:00Z', type='session_meta',
             payload=dict(id=CODEX_ID, cwd=cwd, cli_version='0.99', source='vscode', thread_source=thread_source)),
        dict(timestamp='2026-11-17T02:00:01Z', type='response_item',
             payload=dict(type='message', role='user', content=[dict(type='input_text', text='# AGENTS.md instructions\nrules')])),
        dict(timestamp='2026-11-17T02:00:02Z', type='response_item',
             payload=dict(type='message', role='user', content=[dict(type='input_text', text='给 Codex 加采集适配')])),
        dict(timestamp='2026-11-17T02:00:03Z', type='response_item', payload=dict(type='reasoning', summary=[])),
        dict(timestamp='2026-11-17T02:00:04Z', type='response_item',
             payload=dict(type='message', role='assistant', phase='final_answer', id='m2',
                          content=[dict(type='output_text', text='已完成 Codex 适配。')])),
    ]


def dsh_records(cwd):
    return [
        dict(type='session', id=DSH_ID, version=4, createdAt=1763341200000, cwd=cwd, delegationDepth=0, isSeeded=False),
        dict(seq=1, time=1763341201000, type='permission/preset', data={}),
        dict(seq=2, time=1763341202000, type='user/message', surfaceOp='append',
             data=dict(id='m1', role='user', source=dict(kind='user'), content=[dict(type='text', text='dsh 第一问')])),
        dict(seq=3, time=1763341203000, type='assistant/message', surfaceOp='append',
             data=dict(message=dict(id='m2', role='assistant', source=dict(kind='model', provider='deepseek', model='deepseek-flash'),
                                    content=[dict(type='text', text='旧回答')]))),
        dict(seq=4, time=1763341204000, type='assistant/message', surfaceOp=dict(op='replace', startSeq=3, endSeq=3),
             data=dict(message=dict(id='m3', role='assistant', source=dict(kind='model', provider='deepseek', model='deepseek-flash'),
                                    content=[dict(type='text', text='dsh 最终回答')]))),
        dict(seq=5, time=1763341205000, type='tool/result', surfaceOp='append',
             data=dict(message=dict(id='t1', role='tool', source=dict(kind='tool', callId='c1'), toolCallId='c1',
                                    content=[dict(type='text', text='tool output')]))),
    ]
