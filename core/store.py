"""Local user data: settings, reading history, favourite and excluded tags.

Everything is stored as small JSON files in the application data directory so
that preferences survive restarts and the history file can be inspected or
removed by the user.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .i18n import DEFAULT_LANGUAGE
from .models import Manga, Tag

SETTINGS_FILE = "settings.json"
HISTORY_FILE = "history.json"
MAX_HISTORY = 800

DEFAULT_SETTINGS: dict[str, Any] = {
    "interface_language": DEFAULT_LANGUAGE,
    "hide_read": False,
    "hide_read_in_search": False,
    "block_gay": False,
}


def _tag_to_data(tag: Tag) -> dict[str, Any]:
    return {
        "id": tag.id,
        "name": tag.name,
        "group": tag.group,
        "description": tag.description,
        "source_ids": dict(tag.source_ids),
    }


def _tag_from_data(data: dict[str, Any]) -> Tag | None:
    try:
        return Tag(
            id=str(data["id"]),
            name=str(data["name"]),
            group=str(data.get("group") or "theme"),
            description=str(data.get("description") or ""),
            source_ids={
                str(key): str(value)
                for key, value in (data.get("source_ids") or {}).items()
            },
        )
    except (KeyError, TypeError, ValueError):
        return None


def manga_to_data(manga: Manga) -> dict[str, Any]:
    return {
        "id": manga.id,
        "title": manga.title,
        "alt_titles": list(manga.alt_titles),
        "description": manga.description,
        "status": manga.status,
        "year": manga.year,
        "content_rating": manga.content_rating,
        "demographic": manga.demographic,
        "tags": [_tag_to_data(tag) for tag in manga.tags],
        "authors": list(manga.authors),
        "artists": list(manga.artists),
        "cover_file": manga.cover_file,
        "original_language": manga.original_language,
        "links": dict(manga.links),
        "languages": list(manga.languages),
        "source": manga.source,
        "source_name": manga.source_name,
        "url_text": manga.url_text,
        "cover_url_text": manga.cover_url_text,
        "page_count": manga.page_count,
    }


def manga_from_data(data: dict[str, Any]) -> Manga | None:
    try:
        tags = [
            tag
            for tag in (
                _tag_from_data(item) for item in data.get("tags") or []
            )
            if tag is not None
        ]
        return Manga(
            id=str(data["id"]),
            title=str(data.get("title") or ""),
            alt_titles=[str(item) for item in data.get("alt_titles") or []],
            description=str(data.get("description") or ""),
            status=str(data.get("status") or ""),
            year=int(data["year"]) if data.get("year") else None,
            content_rating=str(data.get("content_rating") or ""),
            demographic=data.get("demographic") or None,
            tags=tags,
            authors=[str(item) for item in data.get("authors") or []],
            artists=[str(item) for item in data.get("artists") or []],
            cover_file=data.get("cover_file") or None,
            original_language=str(data.get("original_language") or ""),
            links={
                str(key): str(value)
                for key, value in (data.get("links") or {}).items()
            },
            languages=[str(item) for item in data.get("languages") or []],
            source=str(data.get("source") or "mangadex"),
            source_name=str(data.get("source_name") or data.get("source") or ""),
            url_text=str(data.get("url_text") or ""),
            cover_url_text=str(data.get("cover_url_text") or ""),
            page_count=int(data["page_count"]) if data.get("page_count") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(slots=True)
class HistoryEntry:
    manga: Manga
    read_at: float = field(default_factory=time.time)

    def to_data(self) -> dict[str, Any]:
        return {"read_at": self.read_at, "manga": manga_to_data(self.manga)}

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "HistoryEntry | None":
        manga = manga_from_data(data.get("manga") or {})
        if manga is None:
            return None
        try:
            read_at = float(data.get("read_at") or time.time())
        except (TypeError, ValueError):
            read_at = time.time()
        return cls(manga=manga, read_at=read_at)


class AppStore:
    """Tiny JSON-backed store for all local user state."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.settings_path = self.data_dir / SETTINGS_FILE
        self.history_path = self.data_dir / HISTORY_FILE
        self._lock = threading.RLock()
        self._settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._history: list[HistoryEntry] = []
        self._favorites: set[str] = set()
        self._excluded: set[str] = set()
        self._load()

    # ------------------------------------------------------------------- load

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = self._read_json(self.settings_path)
        if payload:
            for key, default in DEFAULT_SETTINGS.items():
                value = payload.get(key, default)
                if isinstance(default, bool):
                    value = bool(value)
                self._settings[key] = value
            self._favorites = {
                str(item) for item in payload.get("favorite_tags") or []
            }
            self._excluded = {
                str(item) for item in payload.get("excluded_tags") or []
            }
            if "enabled_sources" in payload:
                self._settings["enabled_sources"] = [
                    str(item) for item in payload.get("enabled_sources") or []
                ]

        history_payload = self._read_json(self.history_path)
        if not history_payload:
            # Create the history file on the first launch as required.
            self._save_history()
        else:
            entries = [
                entry
                for entry in (
                    HistoryEntry.from_data(item)
                    for item in history_payload.get("items") or []
                )
                if entry is not None
            ]
            entries.sort(key=lambda entry: entry.read_at, reverse=True)
            if entries:
                self._history = entries

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".part")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            temp.replace(path)
        except OSError:
            pass

    # --------------------------------------------------------------- settings

    @property
    def interface_language(self) -> str:
        return str(self._settings.get("interface_language") or DEFAULT_LANGUAGE)

    @property
    def hide_read(self) -> bool:
        return bool(self._settings.get("hide_read"))

    @property
    def hide_read_in_search(self) -> bool:
        return bool(self._settings.get("hide_read_in_search"))

    @property
    def block_gay(self) -> bool:
        return bool(self._settings.get("block_gay"))

    @property
    def enabled_sources(self) -> list[str]:
        """Source keys selected in Settings.

        A missing value means "everything"; an explicitly empty list means the
        user unchecked every site, which must stay empty (and produce the
        "select a source" warning).
        """
        if "enabled_sources" not in self._settings:
            from .sources import DEFAULT_SOURCES

            return list(DEFAULT_SOURCES)
        return [str(key) for key in self._settings.get("enabled_sources") or []]

    def set_enabled_sources(self, keys: Iterable[str]) -> None:
        self._settings["enabled_sources"] = list(
            dict.fromkeys(str(key) for key in keys if str(key))
        )
        self._save_settings()

    def set_interface_language(self, code: str) -> None:
        self._settings["interface_language"] = code
        self._save_settings()

    def set_hide_read(self, enabled: bool) -> None:
        self._settings["hide_read"] = bool(enabled)
        self._save_settings()

    def set_hide_read_in_search(self, enabled: bool) -> None:
        self._settings["hide_read_in_search"] = bool(enabled)
        self._save_settings()

    def set_block_gay(self, enabled: bool) -> None:
        self._settings["block_gay"] = bool(enabled)
        self._save_settings()

    def reset_settings(self) -> None:
        self._settings = dict(DEFAULT_SETTINGS)
        self._favorites.clear()
        self._excluded.clear()
        self._save_settings()

    def _save_settings(self) -> None:
        with self._lock:
            payload = dict(self._settings)
            payload["favorite_tags"] = sorted(self._favorites)
            payload["excluded_tags"] = sorted(self._excluded)
            self._write_json(self.settings_path, payload)

    # ---------------------------------------------------------------- history

    def history(self, limit: int | None = None) -> list[HistoryEntry]:
        with self._lock:
            entries = list(self._history)
        return entries[:limit] if limit else entries

    def history_count(self) -> int:
        return len(self._history)

    def is_read(self, manga_or_key: Manga | str) -> bool:
        key = manga_or_key.key if isinstance(manga_or_key, Manga) else manga_or_key
        with self._lock:
            return any(entry.manga.key == key for entry in self._history)

    def add_history(self, manga: Manga) -> None:
        with self._lock:
            now = time.time()
            for entry in self._history:
                if entry.manga.key == manga.key:
                    # Prefer the richer record (details usually have tags and
                    # authors that search cards do not).
                    if len(manga.tags) >= len(entry.manga.tags):
                        entry.manga = manga
                    entry.read_at = now
                    break
            else:
                self._history.append(HistoryEntry(manga=manga, read_at=now))
            self._history.sort(key=lambda entry: entry.read_at, reverse=True)
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[:MAX_HISTORY]
            self._save_history()

    def update_history(self, manga: Manga) -> None:
        """Refresh stored metadata without changing the read timestamp."""
        with self._lock:
            for entry in self._history:
                if entry.manga.key == manga.key:
                    if len(manga.tags) >= len(entry.manga.tags):
                        entry.manga = manga
                    self._save_history()
                    return

    def remove_history(self, manga_or_key: Manga | str) -> None:
        key = manga_or_key.key if isinstance(manga_or_key, Manga) else manga_or_key
        with self._lock:
            self._history = [
                entry for entry in self._history if entry.manga.key != key
            ]
            self._save_history()

    def reset_history(self) -> None:
        with self._lock:
            self._history = []
            self._save_history()

    def _save_history(self) -> None:
        payload = {
            "version": 1,
            "items": [entry.to_data() for entry in self._history],
        }
        self._write_json(self.history_path, payload)

    # ------------------------------------------------------ favourite/excluded

    @property
    def favorite_tags(self) -> set[str]:
        return set(self._favorites)

    @property
    def excluded_tags(self) -> set[str]:
        return set(self._excluded)

    def is_favorite(self, tag_id: str) -> bool:
        return tag_id in self._favorites

    def is_excluded(self, tag_id: str) -> bool:
        return tag_id in self._excluded

    def toggle_favorite(self, tag_id: str) -> bool:
        with self._lock:
            if tag_id in self._favorites:
                self._favorites.discard(tag_id)
                enabled = False
            else:
                self._favorites.add(tag_id)
                enabled = True
            self._save_settings()
            return enabled

    def set_excluded(self, tag_ids: Iterable[str]) -> None:
        with self._lock:
            self._excluded = set(tag_ids)
            self._save_settings()

    def toggle_excluded(self, tag_id: str) -> bool:
        with self._lock:
            if tag_id in self._excluded:
                self._excluded.discard(tag_id)
                enabled = False
            else:
                self._excluded.add(tag_id)
                enabled = True
            self._save_settings()
            return enabled

    def reset_all(self) -> None:
        with self._lock:
            self._settings = dict(DEFAULT_SETTINGS)
            self._favorites.clear()
            self._excluded.clear()
            self._history = []
            self._save_settings()
            self._save_history()

    # ------------------------------------------------------------ diagnostics

    def info(self) -> dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "settings": str(self.settings_path),
            "history": str(self.history_path),
            "history_count": len(self._history),
            "favorites": len(self._favorites),
            "excluded": len(self._excluded),
        }


__all__ = [
    "AppStore",
    "DEFAULT_SETTINGS",
    "HISTORY_FILE",
    "HistoryEntry",
    "MAX_HISTORY",
    "SETTINGS_FILE",
    "manga_from_data",
    "manga_to_data",
]
