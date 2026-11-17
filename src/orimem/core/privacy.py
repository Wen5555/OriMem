"""Native output/import redaction; no dependency on the legacy runtime.

Recognized secret patterns are masked. This cannot detect every free-form secret.
Derived from the legacy filter, extended for PEM, cookies, Chinese assignments.
"""

import re, hashlib

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "password",
    "passwd",
    "pwd",
    "pw",
    "secret",
    "authorization",
    "private_key",
    "client_secret",
    "cookie",
    "set_cookie",
    "密码",
    "口令",
    "密钥",
}
PREFIXES = (
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
    "<environment_context>",
    "<external_codex_apps_open_page>",
    "<recommended_plugins>",
    "<in-app-browser-context",
    "<codex_internal_context",
    "<turn_aborted>",
    "<subagent_notification>",
)
KEYS = r"api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|password|passwd|pwd|pw|secret|authorization|private[_-]?key|client[_-]?secret|cookie|密码|口令|密钥"
PATTERNS = [
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.I | re.S,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        re.compile(r"(?i)(\b(?:authorization\s*:\s*)?bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"""(?i)((?<![\w-])["']?(?:"""
            + KEYS
            + r""")["']?\s*[:=：]\s*)(["'])((?:\\.|(?!\2)[^\\\r\n])*)\2"""
        ),
        lambda m: m[1] + m[2] + "[REDACTED]" + m[2],
    ),
    (
        re.compile(
            r"""(?i)((?<![\w-])["']?(?:"""
            + KEYS
            + r""")["']?\s*[:=：]\s*)(["']?)([^\s"',;，；}]+)(["']?)"""
        ),
        lambda m: m[1] + m[2] + "[REDACTED]" + m[4],
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"
        ),
        "[REDACTED TOKEN]",
    ),
    (
        re.compile(
            r"(?i)(\bssh(?:连接|链接|访问)?\s*(?:这台[^\r\n:：]{1,40}[:：]\s*)?(?:[\w.-]+|\[[0-9a-f:]+\])(?::\d{1,5})?\s+(?:root|[a-z_][a-z0-9_-]{0,31})\s+)([^\s,，;；]+)"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(https?://[^\s/:@]+:)([^\s/@]+)(@)"), r"\1[REDACTED]\3"),
    (
        re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{4096,}={0,2}(?![A-Za-z0-9+/])"),
        lambda m: "[REDACTED LARGE BLOB sha256="
        + hashlib.sha256(m[0].encode()).hexdigest()[:16]
        + "]",
    ),
]


def sensitive_key(key):
    k = str(key).casefold().replace("-", "_")
    return k in SENSITIVE_KEYS or k.endswith(
        ("_api_key", "_auth_token", "_access_token", "_refresh_token", "_password", "_secret")
    )


def redact(text):
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
    text = re.sub("[\ud800-\udfff\x00]", "", text).replace("\ufffd", "[INVALID SOURCE CHARACTER]")
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [sanitize(x) for x in value]
    if isinstance(value, dict):
        return {
            redact(str(k)): "[REDACTED]" if sensitive_key(k) else sanitize(v)
            for k, v in value.items()
        }
    return value


def contains_roster(text):
    # Explicit personal rosters are preserved only in the frozen source. Avoid
    # inventing anonymized identities or importing third-party contact lists.
    return bool(
        re.search(
            r"(?:姓名|学生名单|志愿者名单).{0,80}(?:学号|手机号|电话号码|联系方式|身份证)|(?:手机号|身份证号).{0,80}姓名",
            text,
            re.S,
        )
    )
