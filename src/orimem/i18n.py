"""Tiny bilingual helper: agent- and user-facing text follows the vault language (zh, else English)."""
from orimem import config


def lang() -> str:
    return 'zh' if config.layout()['language'] == 'zh' else 'en'


def t(zh: str, en: str) -> str:
    return zh if lang() == 'zh' else en
