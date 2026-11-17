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
    "layout": {                        # vault folders by role; "" -> the language preset (see LAYOUTS)
        "language": "",                # zh | en; set by `orimem init`
        "inbox": "", "drafts": "", "projects": "", "courses": "", "areas": "", "knowledge": "",
        "journal": "", "daily": "", "weekly": "", "archive": "", "evidence": "", "legacy": "",
        "workspace": "", "course_home": "", "weekly_note": "", "knowledge_index": "",
        "project_scopes": [],          # [] -> [projects, courses]
        "extra_writable": [],          # further folders automatic write-back may touch
    },
    "recall": {
        "mode": "hybrid",              # hybrid (vectors + full text, needs the model) | lexical (no model)
        "model": "intfloat/multilingual-e5-small",
        "hf_endpoint": "",             # e.g. https://hf-mirror.com
    },
    "mcp": {
        "tools": "core",               # core (8 everyday tools) | all (also the advanced ones)
        "extra": [],                   # with "core": individual advanced tools to publish too
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
    if key == "mcp.tools" and value not in ("core", "all"):
        raise ValueError('mcp.tools must be "core" or "all"')
    if key == "recall.mode" and value not in ("hybrid", "lexical"):
        raise ValueError('recall.mode must be "hybrid" or "lexical"')
    if key == "layout.language" and value not in LAYOUTS:
        raise ValueError("layout.language must be one of: " + ", ".join(LAYOUTS))
    if key.startswith("layout.") and isinstance(known, str) and isinstance(value, str) and value:
        _check_rel(value, key)
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


LAYOUTS: dict = {
    "zh": {
        "inbox": "00 收件箱", "drafts": "00 收件箱/AI 草稿", "projects": "10 项目", "courses": "15 课程",
        "areas": "20 长期领域", "knowledge": "30 知识", "journal": "50 日记", "daily": "50 日记/每日",
        "weekly": "50 日记/每周", "archive": "90 归档", "evidence": "90 归档/对话佐证", "legacy": "90 归档/旧文件",
        "workspace": "95 AI 工作区", "course_home": "课程主页", "weekly_note": "{week} 周复盘",
        "knowledge_index": "30 知识/知识索引",
    },
    "en": {
        "inbox": "00 Inbox", "drafts": "00 Inbox/AI Drafts", "projects": "10 Projects", "courses": "15 Courses",
        "areas": "20 Areas", "knowledge": "30 Knowledge", "journal": "50 Journal", "daily": "50 Journal/Daily",
        "weekly": "50 Journal/Weekly", "archive": "90 Archive", "evidence": "90 Archive/Conversations",
        "legacy": "90 Archive/Legacy", "workspace": "95 AI Workspace", "course_home": "Course Home",
        "weekly_note": "{week} Weekly Review", "knowledge_index": "30 Knowledge/Knowledge Index",
    },
}
ROLES = tuple(LAYOUTS["zh"])
# Vaults set up by 0.1.x have no layout.language; their transcripts already live here.
LEGACY_EVIDENCE = "90 归档/Codex佐证"


def _check_rel(value: str, key: str = "layout") -> str:
    norm = value.replace("\\", "/").strip("/")
    parts = norm.split("/")
    if not norm or any(p in ("", ".", "..") or p.startswith(".") for p in parts) or ":" in norm:
        raise ValueError(f"{key} must be a relative folder inside the vault: {value!r}")
    return norm


def detect_language() -> str:
    """zh for Chinese locales, en otherwise (ORIMEM_LANG overrides)."""
    import locale
    hints = [os.environ.get("ORIMEM_LANG", ""), os.environ.get("LC_ALL", ""), os.environ.get("LANG", "")]
    try:
        hints.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            hints.append(locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""))
        except Exception:
            pass
    for hint in hints:
        low = hint.lower()
        if low.startswith(("zh", "chinese")):
            return "zh"
        if low.startswith(("en", "english")):
            return "en"
    return "en"


def layout() -> dict:
    """Resolved vault layout: every role -> relative folder (or note name), plus language."""
    lang = str(get("layout.language") or "")
    preset = dict(LAYOUTS.get(lang) or LAYOUTS["zh"])
    if not lang:
        preset["evidence"] = LEGACY_EVIDENCE
    out = {"language": lang or "zh"}
    for role in ROLES:
        value = str(get("layout." + role) or "") or preset[role]
        out[role] = value if role in ("course_home", "weekly_note") else _check_rel(value, "layout." + role)
    return out


def layout_path(role: str) -> str:
    return layout()[role]


def under(rel: str, folder: str) -> bool:
    rel = rel.replace("\\", "/").strip("/")
    return rel == folder or rel.startswith(folder + "/")


def daily_note(day: str) -> str:
    return f"{layout_path('daily')}/{day}.md"


def weekly_note(week: str) -> str:
    lay = layout()
    return f"{lay['weekly']}/{lay['weekly_note'].format(week=week)}.md"


def project_scopes() -> list[str]:
    lay = layout()
    return list(get("layout.project_scopes") or [lay["projects"], lay["courses"]])


def writable() -> list[str]:
    """Folders automatic write-back may touch (journal only through daily/weekly)."""
    lay = layout()
    base = [lay[r] for r in ("drafts", "projects", "courses", "knowledge", "daily", "weekly", "evidence", "workspace")]
    return base + [_check_rel(str(x)) for x in (get("layout.extra_writable") or [])]


def folders() -> list[str]:
    """Every folder the layout names (for scaffolding)."""
    lay = layout()
    found = {lay[r] for r in ROLES if r not in ("course_home", "weekly_note", "knowledge_index")}
    if "/" in lay["knowledge_index"]:
        found.add(lay["knowledge_index"].rsplit("/", 1)[0])
    return sorted(found)
