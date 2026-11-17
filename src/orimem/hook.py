"""One hook entry for every agent: ``orimem hook --agent claude|codex|dsh``.

Reads the hook event JSON on stdin (Claude Code / Codex hook format; dsh runs
the same file through its Claude Code hook bridge) and:

* SessionStart / UserPromptSubmit: injects the bounded project-entry context
  (``hook_entry.handle``) and, while ``daily_summary.enabled`` is still "ask",
  a one-line reminder for the agent to ask the user once;
* Stop (and SessionStart resume/compact): asks the running OriMem service to
  capture this session in the background. The hook itself writes nothing, so
  it also works inside sandboxed hook runners (dsh). Only when no service is
  running does it start a detached ``orimem capture`` process instead.

Hooks always fail open: any error yields ``{}`` so the agent keeps working.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from orimem import config

ASK_DAILY = ('OriMem：每日总结开关尚未设置（daily_summary.enabled = "ask"）。请在本次会话的合适时机简短询问用户一次'
             '是否开启每日自动总结（默认关闭，可设定时间），按回答调用 `config_set`（true 或 false）记录；'
             '用户未回答前视为关闭，不要反复追问。')
SESSION_PREFIX = {'claude': 'claude-', 'dsh': 'dsh-'}


def normalized_session(agent: str, session: str) -> str:
    """Same session id the capture pipeline uses (claude-<uuid>, dsh-<uuid>, bare Codex id)."""
    if agent == 'dsh':
        session = session.removeprefix('dsh-').removeprefix('session-')
    prefix = SESSION_PREFIX.get(agent, '')
    return session if session.startswith(prefix) else prefix + session


def _notify_service(args: dict) -> bool:
    """POST capture_now(background) to the local OriMem service; stdlib only, ~ms when it runs."""
    import urllib.request
    try:
        state = json.loads((config.home() / 'service-state.json').read_text(encoding='utf-8'))
        request = urllib.request.Request(
            f'http://127.0.0.1:{int(state["port"])}/rpc',
            data=json.dumps(dict(name='capture_now', arguments=args)).encode('utf-8'),
            headers={'X-OriMem-Key': state['key'], 'Content-Type': 'application/json'}, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a proxy
        with opener.open(request, timeout=3) as response:
            value = json.loads(response.read().decode('utf-8'))
        return 'error' not in value
    except Exception:
        return False


def _spawn_capture(agent: str, session: str | None, transcript: str | None) -> None:
    exe = sys.executable
    if os.name == 'nt':
        windowless = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        exe = windowless if os.path.exists(windowless) else exe
    args = [exe, '-X', 'utf8', '-m', 'orimem', '--home', str(config.home()), 'capture', '--source', agent, '--delay', '3']
    if transcript:
        args += ['--path', transcript]
    if session:
        args += ['--session', session]
    flags = 0
    if os.name == 'nt':
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    for extra in ((getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0),) if os.name == 'nt' else ()) + (0,):
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags | extra, close_fds=True,
                             start_new_session=(os.name != 'nt'))
            return
        except OSError:
            continue


def request_capture(agent: str, session: str | None, transcript: str | None) -> str:
    args = dict(source=agent, background=True)
    if session:
        args['session'] = session
    if transcript:
        args['path'] = transcript
    if _notify_service(args):
        return 'service'
    _spawn_capture(agent, session, transcript)
    return 'spawned'


def handle(event: dict, agent: str) -> dict:
    name = event.get('hook_event_name')
    output: dict = {}
    session = event.get('session_id') if isinstance(event.get('session_id'), str) and event.get('session_id') else None
    if name in ('SessionStart', 'UserPromptSubmit', 'Stop') and session:
        from orimem import hook_entry
        shared = {k: v for k, v in event.items() if k not in ('transcript_path', 'last_assistant_message')}
        shared['session_id'] = normalized_session(agent, session)
        try:
            output = hook_entry.handle(shared, scope=config.project_scopes()) or {}
        except Exception:
            output = {}
    if name == 'SessionStart' and config.get('daily_summary.enabled') == 'ask':
        spec = output.setdefault('hookSpecificOutput', dict(hookEventName=name))
        spec['additionalContext'] = (spec.get('additionalContext', '') + '\n' + ASK_DAILY).strip()
    source = event.get('source')
    if config.get('capture.enabled', True) and agent in (config.get('capture.sources') or []):
        if name == 'Stop' or (name == 'SessionStart' and source in ('resume', 'compact')):
            transcript = event.get('transcript_path')
            request_capture(agent, session, transcript if isinstance(transcript, str) and transcript else None)
    return output


def main(agent: str) -> None:
    try:
        raw = sys.stdin.buffer.read(1_000_001)
        event = json.loads(raw.decode('utf-8')) if raw.strip() else {}
        result = handle(event, agent) if isinstance(event, dict) else {}
    except Exception:
        print('OriMem hook unavailable; use the OriMem skill or CLI.', file=sys.stderr)
        result = {}
    print(json.dumps(result, ensure_ascii=False))
