"""UI localisation (English).

Translation keys are English source strings, so English is the zero-cost
fallback. Source names, native language names and the translation-language
list are intentionally not localised.
"""

from __future__ import annotations

import threading

DEFAULT_LANGUAGE = "en"
LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
]

_lock = threading.Lock()
_language = DEFAULT_LANGUAGE

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {},
}

_language = DEFAULT_LANGUAGE


def language() -> str:
    return _language


def set_language(code: str) -> str:
    global _language
    with _lock:
        _language = "en"
    return _language


def tr(text: str, **kwargs: object) -> str:
    value = TRANSLATIONS.get(_language, {}).get(text, text)
    if kwargs:
        try:
            value = value.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            pass
    return value


def content_rating_title(code: str) -> str:
    titles = {
        "safe": "Safe",
        "suggestive": "Suggestive",
        "erotica": "Erotica",
        "pornographic": "Pornographic",
    }
    return titles.get(code, code.title())


def tag_group_title(group: str) -> str:
    titles = {
        "genre": "Genre",
        "theme": "Theme",
        "format": "Format",
        "content": "Content",
    }
    return titles.get(group, group.title())


def tag_tooltip(name: str, group: str = "", description: str = "") -> str:
    if description:
        return description
    return ""


def demographic_title(code: str) -> str:
    titles = {
        "shounen": "Shounen",
        "shoujo": "Shoujo",
        "seinen": "Seinen",
        "josei": "Josei",
    }
    return titles.get(code, code.title() if code else "")


def status_title(code: str) -> str:
    titles = {
        "ongoing": "Ongoing",
        "completed": "Completed",
        "hiatus": "Hiatus",
        "cancelled": "Cancelled",
    }
    return titles.get(code, code.title() if code else "")


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "TRANSLATIONS",
    "content_rating_title",
    "demographic_title",
    "language",
    "set_language",
    "status_title",
    "tag_group_title",
    "tag_tooltip",
    "tr",
]