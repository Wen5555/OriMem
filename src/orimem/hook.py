"""One hook entry for every agent: ``orimem hook --agent claude|codex|dsh``.

Reads the hook event JSON on stdin (Claude Code / Codex hook format; dsh runs
the same file through its Claude Code hook bridge) and:

* SessionStart / UserPromptSubmit: injects the bounded project-entry context
  (``hook_entry.handle``) and, while ``daily_summary.enabled`` is still "ask",
  a one-line reminder for the agent to ask the user once;
* Stop (and SessionStart resume/compact): starts a detached, incremental
  capture of that agent's transcripts when ``capture.enabled`` is true.

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


def _spawn_capture(agent: str, transcript: str | None) -> None:
    exe = sys.executable
    if os.name == 'nt':
        windowless = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        exe = windowless if os.path.exists(windowless) else exe
    args = [exe, '-X', 'utf8', '-m', 'orimem', '--home', str(config.home()), 'capture', '--source', agent, '--delay', '3']
    if transcript:
        args += ['--path', transcript]
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


def handle(event: dict, agent: str) -> dict:
    name = event.get('hook_event_name')
    output: dict = {}
    session = event.get('session_id')
    if name in ('SessionStart', 'UserPromptSubmit', 'Stop') and isinstance(session, str):
        from orimem import hook_entry
        shared = {k: v for k, v in event.items() if k not in ('transcript_path', 'last_assistant_message')}
        prefix = {'claude': 'claude-', 'dsh': 'dsh-'}.get(agent, '')
        shared['session_id'] = prefix + session if not session.startswith(prefix) else session
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
            _spawn_capture(agent, transcript if isinstance(transcript, str) and transcript else None)
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
