"""OriMem configuration: one TOML file under ORIMEM_HOME (default ~/.orimem).

Every path the backend uses is derived from here; nothing is hard-coded to a
particular machine. Values are read once per process (call ``reload()`` in
tests after changing ORIMEM_HOME).
"""
from __future__ import annotations

import copy
import json
import os
import tomllib
from datetime import timezone, timedelta, datetime
from pathlib import Path

DEFAULTS: dict = {
    "vault": "",                       # empty -> <home>/vault
    "journal_from": "",                # set once by `orimem init`; do not change afterwards
    "timezone": "",                    # IANA name; empty -> system local
    "capture": {
        "enabled": True,               # conversation capture into the vault (default on)
        "sources": ["claude", "codex", "dsh"],
        "since": "",                   # ISO time; sessions older than this are not backfilled
        "exclude_cwd": [],             # path prefixes whose sessions are never captured
    },
    "daily_summary": {
        "enabled": "ask",              # "ask" | true | false  (asked once; default off)
        "time": "22:30",
        "agent": "",                   # claude | codex | "" (auto: first installed)
    },
    "layout": {
        "project_scopes": ["10 项目", "15 课程"],
        "folders": ["00 收件箱", "10 项目", "15 课程", "30 知识", "50 日记", "90 归档", "95 AI 工作区"],
    },
    "recall": {
        "model": "intfloat/multilingual-e5-small",
        "hf_endpoint": "",             # e.g. https://hf-mirror.com
    },
    "sources": {                       # where each agent keeps its transcripts
        "claude": "~/.claude/projects",
        "codex": "~/.codex/sessions",
        "dsh": "~/.dsh/sessions",
    },
}

HOME = Path(os.environ.get("ORIMEM_HOME") or Path.home() / ".orimem").expanduser()
CONFIG_PATH = HOME / "config.toml"
_cache: dict | None = None


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    global _cache
    if _cache is None:
        data = {}
        if CONFIG_PATH.exists():
            data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        _cache = _merge(DEFAULTS, data)
    return _cache


def reload() -> dict:
    global _cache, HOME, CONFIG_PATH
    HOME = Path(os.environ.get("ORIMEM_HOME") or Path.home() / ".orimem").expanduser()
    CONFIG_PATH = HOME / "config.toml"
    _cache = None
    return load()


def get(key: str, default=None):
    node = load()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def parse_value(text: str):
    """CLI values: JSON when it parses (true, 3, ["a"]), else the raw string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def set_value(key: str, value) -> dict:
    data = {}
    if CONFIG_PATH.exists():
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    parts = key.split(".")
    known = DEFAULTS
    for part in parts:
        if not isinstance(known, dict) or part not in known:
            raise KeyError("Unknown setting: " + key)
        known = known[part]
    if key == "daily_summary.enabled" and value not in ("ask", True, False):
        raise ValueError('daily_summary.enabled must be "ask", true or false')
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    save(data)
    return reload()


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError("Unsupported TOML value: %r" % (value,))


def dumps(data: dict) -> str:
    lines, tables = [], []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    for name, table in tables:
        lines.append("")
        lines.append(f"[{name}]")
        for key, value in table.items():
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines).lstrip("\n") + "\n"


def save(data: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(dumps(data), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


# ---- derived paths -------------------------------------------------------

def home() -> Path:
    return HOME


def vault() -> Path:
    value = get("vault") or str(HOME / "vault")
    return Path(str(value)).expanduser()


def db() -> Path:
    return HOME / "orimem.db"


def source_dir(name: str) -> Path:
    return Path(str(get("sources." + name) or "")).expanduser()


def zone():
    name = get("timezone")
    if name:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    local = datetime.now().astimezone().utcoffset() or timedelta(0)
    return timezone(local)


def zone_name() -> str:
    name = get("timezone")
    if name:
        return str(name)
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    hours = int(offset.total_seconds() // 3600)
    # Etc/GMT signs are inverted by POSIX convention.
    return "UTC" if hours == 0 else f"Etc/GMT{-hours:+d}"


def journal_from() -> str:
    return str(get("journal_from") or "2000-01-01T00:00:00+00:00")


def project_scopes() -> list[str]:
    return list(get("layout.project_scopes") or [])


def folders() -> set[str]:
    return set(get("layout.folders") or [])
