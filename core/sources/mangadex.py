"""MangaDex source (official API)."""

from __future__ import annotations

import threading
from typing import Any, Iterable

from ..http import HttpError, RateLimiter, get_bytes, request
from ..models import (
    SORT_OPTIONS,
    Chapter,
    Manga,
    SearchQuery,
    Statistics,
    Tag,
)
from .base import SearchPage, Source, languages_match

API_BASE = "https://api.mangadex.org"
UPLOADS_BASE = "https://uploads.mangadex.org"
USER_AGENT = "MangaFinder/1.0 (web client)"

MangaDexError = HttpError


class MangaDexSource(Source):
    key = "mangadex"
    name = "MangaDex"
    site_url = "https://mangadex.org"
    kind = "manga"
    supports_data_saver = True
    tag_source_key = "mangadex"
    api_limiter = RateLimiter(0.22)
    image_limiter = RateLimiter(0.05)

    def __init__(self) -> None:
        self._author_cache: dict[str, str] = {}
        self._author_lock = threading.Lock()

    # -------------------------------------------------------------------- tags

    def get_tags(self) -> list[Tag]:
        payload = request(
            "GET",
            f"{API_BASE}/manga/tag",
            limiter=self.api_limiter,
            headers={"User-Agent": USER_AGENT},
        ).json()
        tags: list[Tag] = []
        for item in payload.get("data", []):
            attributes = item.get("attributes", {})
            name = (attributes.get("name") or {}).get("en") or item["id"]
            group = attributes.get("group") or "theme"
            description = (attributes.get("description") or {}).get("en", "")
            from ..tags import tag_from_mangadex

            tags.append(tag_from_mangadex(item["id"], name, group, description))
        order = {"genre": 0, "theme": 1, "format": 2, "content": 3}
        tags.sort(key=lambda tag: (order.get(tag.group, 9), tag.name.lower()))
        return tags

    # ------------------------------------------------------------------ search

    def search(
        self,
        query: SearchQuery,
        *,
        page: int = 1,
        offset: int = 0,
        limit: int = 24,
    ) -> SearchPage:
        params: list[tuple[str, Any]] = [
            ("limit", max(1, min(limit, 100))),
            ("offset", max(0, offset)),
            ("includes[]", "cover_art"),
            ("includes[]", "author"),
            ("includes[]", "artist"),
            ("includedTagsMode", query.tag_mode.upper()),
            ("excludedTagsMode", "OR"),
        ]
        params.extend(("contentRating[]", rating) for rating in query.ratings)
        params.extend(
            ("includedTags[]", native)
            for native in self._native_tag_ids(query.tags)
        )
        params.extend(
            ("excludedTags[]", native)
            for native in self._native_tag_ids(query.excluded_tags)
        )
        params.extend(("availableTranslatedLanguage[]", code) for code in query.languages)

        if query.title.strip():
            params.append(("title", query.title.strip()))
        if query.author.strip():
            author_id = self._author_id(query.author.strip())
            if author_id:
                params.append(("authorOrArtist", author_id))

        sort_params = next(
            (item[2] for item in SORT_OPTIONS if item[0] == query.sort), None
        )
        if sort_params:
            params.extend(sort_params.items())

        payload = request(
            "GET",
            f"{API_BASE}/manga",
            params=params,
            limiter=self.api_limiter,
            headers={"User-Agent": USER_AGENT},
        ).json()
        preferred = list(query.languages) + [
            "en",
            "ru",
        ]
        items = []
        for item in payload.get("data", []):
            manga = self._parse_manga(item, preferred)
            if languages_match(query, manga):
                items.append(manga)
        total = int(payload.get("total", 0) or 0)
        shown = offset + len(items)
        return SearchPage(items=items, total=total, has_more=shown < total)

    def _author_id(self, name: str) -> str:
        key = name.strip().lower()
        with self._author_lock:
            cached = self._author_cache.get(key)
            if cached is not None:
                return cached
        try:
            payload = request(
                "GET",
                f"{API_BASE}/author",
                params={"name": name.strip(), "limit": 1},
                limiter=self.api_limiter,
                headers={"User-Agent": USER_AGENT},
            ).json()
        except HttpError:
            return ""
        items = payload.get("data") or []
        author_id = str(items[0].get("id") or "") if items else ""
        with self._author_lock:
            self._author_cache[key] = author_id
        return author_id

    def search_manga(self, query: SearchQuery) -> tuple[list[Manga], int]:
        """Compatibility with the old API."""
        page = self.search(
            query,
            offset=query.offset,
            limit=query.limit,
        )
        return page.items, int(page.total or 0)

    @staticmethod
    def _native_tag_ids(tags: Iterable[Tag]) -> list[str]:
        native: list[str] = []
        for tag in tags:
            value = tag.source_ids.get("mangadex")
            if value:
                native.append(value)
        return native

    # ----------------------------------------------------------------- details

    def get_details(self, manga: Manga) -> Manga:
        return manga

    def get_statistics(self, manga: Manga) -> Statistics | None:
        payload = request(
            "GET",
            f"{API_BASE}/statistics/manga",
            params={"manga[]": [manga.id]},
            limiter=self.api_limiter,
            headers={"User-Agent": USER_AGENT},
        ).json()
        data = (payload.get("statistics") or {}).get(manga.id) or {}
        rating = data.get("rating") or {}
        comments = data.get("comments") or {}
        return Statistics(
            rating=rating.get("bayesian") or rating.get("average"),
            rating_count=sum((rating.get("distribution") or {}).values()),
            follows=int(data.get("follows") or 0),
            comments=int(comments.get("replies") or 0),
        )

    def get_chapters(
        self,
        manga: Manga,
        languages: Iterable[str] | None = None,
        limit: int = 100,
        max_items: int = 5000,
    ) -> list[Chapter]:
        chapters: list[Chapter] = []
        offset = 0
        while offset < max_items:
            params: list[tuple[str, Any]] = [
                ("limit", min(limit, 100)),
                ("offset", offset),
                ("includes[]", "scanlation_group"),
                ("order[volume]", "asc"),
                ("order[chapter]", "asc"),
                ("includeFutureUpdates", "0"),
                ("includeExternalUrl", "1"),
            ]
            params.extend(("translatedLanguage[]", code) for code in (languages or []))

            payload = request(
                "GET",
                f"{API_BASE}/manga/{manga.id}/feed",
                params=params,
                limiter=self.api_limiter,
                headers={"User-Agent": USER_AGENT},
            ).json()
            batch = payload.get("data", [])
            chapters.extend(self._parse_chapter(item) for item in batch)
            total = int(payload.get("total", 0) or 0)
            offset += len(batch)
            if not batch or offset >= total:
                break

        chapters.sort(key=_chapter_sort_key)
        return chapters

    # Compatibility with the old method name.
    def get_feed(
        self,
        manga_id: str,
        languages: Iterable[str] | None = None,
        limit: int = 100,
        max_items: int = 5000,
    ) -> list[Chapter]:
        proxy = Manga(id=manga_id, title="", source=self.key, source_name=self.name)
        return self.get_chapters(
            proxy, languages=languages, limit=limit, max_items=max_items
        )

    # ------------------------------------------------------------------- pages

    def get_page_urls(self, chapter: Chapter, *, data_saver: bool = False) -> list[str]:
        if chapter.page_urls:
            return list(chapter.page_urls)
        payload = self.get_at_home(chapter.id)
        base_url = payload["baseUrl"]
        chapter_hash = payload["chapter"]["hash"]
        mode = "data-saver" if data_saver else "data"
        files = payload["chapter"]["dataSaver" if data_saver else "data"]
        return [f"{base_url}/{mode}/{chapter_hash}/{name}" for name in files]

    def get_at_home(self, chapter_id: str) -> dict[str, Any]:
        try:
            return request(
                "GET",
                f"{API_BASE}/at-home/server/{chapter_id}",
                limiter=self.api_limiter,
                headers={"User-Agent": USER_AGENT},
            ).json()
        except HttpError as exc:
            if "403" not in str(exc):
                raise
            return request(
                "GET",
                f"{API_BASE}/at-home/server/{chapter_id}",
                params={"forcePort443": "true"},
                limiter=self.api_limiter,
                headers={"User-Agent": USER_AGENT},
            ).json()

    def download_page(self, url: str) -> bytes:
        return get_bytes(url, limiter=self.image_limiter)

    def download_cover(self, manga: Manga, size: int = 256) -> bytes:
        url = manga.cover_url_for(size)
        if not url:
            raise HttpError("This manga has no cover")
        return get_bytes(url, limiter=self.image_limiter)

    def check_available(self) -> tuple[bool, str]:
        try:
            response = request(
                "GET",
                f"{API_BASE}/ping",
                limiter=self.api_limiter,
                retries=1,
                timeout=15.0,
                headers={"User-Agent": USER_AGENT},
            )
        except HttpError as exc:
            return False, str(exc)[:160]
        return True, f"HTTP {response.status_code}"

    # ------------------------------------------------------------------- parse

    def _parse_manga(
        self, item: dict[str, Any], preferred: Iterable[str] | None = None
    ) -> Manga:
        attributes = item.get("attributes", {})
        title_map = attributes.get("title") or {}
        alt_titles: list[str] = []
        for alt in attributes.get("altTitles") or []:
            for value in alt.values():
                if value and value not in alt_titles:
                    alt_titles.append(value)

        cover_file = None
        authors: list[str] = []
        artists: list[str] = []
        for relationship in item.get("relationships") or []:
            kind = relationship.get("type")
            rel_attributes = relationship.get("attributes") or {}
            name = rel_attributes.get("name")
            if kind == "cover_art":
                cover_file = rel_attributes.get("fileName")
            elif kind == "author" and name:
                authors.append(str(name))
            elif kind == "artist" and name:
                artists.append(str(name))

        languages = list(attributes.get("availableTranslatedLanguages") or [])
        order = list(preferred or []) + ["en", "ru", attributes.get("originalLanguage") or "ja"]
        if languages:
            order = [code for code in order if code in languages] + order
        description = _pick_description(attributes.get("description") or {}, order)

        from ..tags import tag_from_mangadex

        tags = [
            tag_from_mangadex(
                tag["id"],
                (tag.get("attributes", {}).get("name") or {}).get("en", ""),
                tag.get("attributes", {}).get("group", ""),
                (tag.get("attributes", {}).get("description") or {}).get("en", ""),
            )
            for tag in attributes.get("tags") or []
        ]

        return Manga(
            id=item["id"],
            title=_pick_title(title_map, order),
            alt_titles=alt_titles[:8],
            description=description,
            status=attributes.get("status") or "",
            year=attributes.get("year"),
            content_rating=attributes.get("contentRating") or "",
            demographic=attributes.get("publicationDemographic"),
            tags=tags,
            authors=authors,
            artists=artists,
            cover_file=cover_file,
            original_language=attributes.get("originalLanguage") or "",
            links=attributes.get("links") or {},
            languages=languages,
            source=self.key,
            source_name=self.name,
        )

    @staticmethod
    def _parse_chapter(item: dict[str, Any]) -> Chapter:
        attributes = item.get("attributes", {})
        group = None
        for relationship in item.get("relationships") or []:
            if relationship.get("type") == "scanlation_group":
                group = (relationship.get("attributes") or {}).get("name")
                break
        return Chapter(
            id=item["id"],
            chapter=attributes.get("chapter"),
            volume=attributes.get("volume"),
            title=attributes.get("title"),
            language=attributes.get("translatedLanguage") or "",
            pages=int(attributes.get("pages") or 0),
            external_url=attributes.get("externalUrl"),
            group=group,
            readable_at=attributes.get("readableAt"),
        )


# Old class name kept so older imports keep working.
MangaDexClient = MangaDexSource


def _pick_title(title_map: dict[str, str], preferred: Iterable[str]) -> str:
    for code in preferred:
        if title_map.get(code):
            return title_map[code]
    for value in title_map.values():
        if value:
            return value
    return "Untitled"


def _pick_description(desc_map: dict[str, str], preferred: Iterable[str]) -> str:
    for code in preferred:
        if desc_map.get(code):
            return desc_map[code]
    for value in desc_map.values():
        if value:
            return value
    return ""


def _chapter_sort_key(chapter: Chapter) -> tuple:
    def number(value: str | None) -> float:
        if not value:
            return -1.0
        try:
            return float(value)
        except ValueError:
            digits = "".join(ch for ch in value if ch.isdigit() or ch == ".")
            try:
                return float(digits) if digits else 10_000.0
            except ValueError:
                return 10_000.0

    return (number(chapter.volume), number(chapter.chapter), chapter.language)
