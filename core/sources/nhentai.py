"""nhentai source (official API v2)."""

from __future__ import annotations

import re
from typing import Any

from ..http import HttpError, RateLimiter, get_bytes, request
from ..models import (
    LANGUAGE_CODES,
    TITLE_LANGUAGE_NAMES,
    Chapter,
    Manga,
    SearchQuery,
    Statistics,
    Tag,
    language_from_name,
)
from ..tags import is_gay_tag, tag_from_nhentai
from .base import SearchPage, Source, languages_match

API_BASE = "https://nhentai.net/api/v2"
SITE_URL = "https://nhentai.net"
IMAGE_BASE = "https://i.nhentai.net"
THUMB_BASE = "https://t1.nhentai.net"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_LANGUAGE_TAG = re.compile(r"language:([a-z-]+)", re.IGNORECASE)
_TITLE_BRACKETS = re.compile(r"\[([^\]]+)\]")

_LANGUAGE_QUERY = {
    "en": "english",
    "ru": "russian",
    "uk": "ukrainian",
    "zh": "chinese",
    "zh-hk": "chinese",
    "ja": "japanese",
    "ko": "korean",
    "fr": "french",
    "de": "german",
    "es": "spanish",
    "es-la": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "pt-br": "portuguese",
    "pl": "polish",
    "tr": "turkish",
    "vi": "vietnamese",
    "id": "indonesian",
    "th": "thai",
    "ar": "arabic",
    "hi": "hindi",
    "cs": "czech",
    "hu": "hungarian",
    "ro": "romanian",
    "nl": "dutch",
    "sv": "swedish",
    "fi": "finnish",
    "bg": "bulgarian",
    "el": "greek",
    "fa": "persian",
    "he": "hebrew",
}


class NhentaiSource(Source):
    key = "nhentai"
    name = "nhentai"
    site_url = SITE_URL
    kind = "gallery"
    supports_data_saver = False
    tag_source_key = "nhentai"
    api_limiter = RateLimiter(0.3)
    image_limiter = RateLimiter(0.05)
    default_language = "ja"
    tag_pages = 6

    def __init__(self) -> None:
        self._details: dict[str, Manga] = {}
        self._pages: dict[str, list[str]] = {}
        self._favorites: dict[str, int] = {}

    # -------------------------------------------------------------------- tags

    def get_tags(self) -> list[Tag]:
        tags: list[Tag] = []
        for page in range(1, max(1, self.tag_pages) + 1):
            payload = self._get_json("/tags/tag", params={"page": page})
            batch = payload.get("result") or []
            tags.extend(
                tag
                for tag in (tag_from_nhentai(item) for item in batch)
                if tag is not None
            )
            if not batch or page >= int(payload.get("num_pages") or 0):
                break
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
        text = self._build_query(query)
        params: dict[str, Any] = {"page": max(1, page)}
        if text:
            params["query"] = text
            params["sort"] = "popular" if query.sort in ("follows", "rating") else "date"
            endpoint = "/search"
        else:
            endpoint = "/galleries"

        payload = self._get_json(endpoint, params=params)
        items = self.filter_results(
            query,
            [self._parse_card(item) for item in payload.get("result") or []],
        )
        total = int(payload.get("total") or 0)
        num_pages = int(payload.get("num_pages") or 0)
        has_more = page < num_pages if num_pages else bool(items)
        return SearchPage(items=items, total=total, has_more=has_more)

    def _build_query(self, query: SearchQuery) -> str:
        parts: list[str] = []
        if query.title.strip():
            parts.append(query.title.strip())
        if query.author.strip():
            author = query.author.replace('"', "").strip()
            if author:
                parts.append(f'artist:"{author}"')
        for tag in query.tags:
            name = (tag.name or "").replace('"', "").strip()
            slug = (tag.id or "").replace('"', "").strip()
            term = name or slug.replace("-", " ").strip()
            if term:
                parts.append(f'tag:"{term}"')
        for tag in query.excluded_tags:
            name = (tag.name or "").replace('"', "").strip()
            slug = (tag.id or "").replace('"', "").strip()
            term = name or slug.replace("-", " ").strip()
            if term:
                parts.append(f'-tag:"{term}"')

        # nhentai expects a single language tag. With several selected
        # languages we leave the query open and filter the cards locally.
        if len(query.languages) == 1:
            code = query.languages[0]
            if code in _LANGUAGE_QUERY:
                parts.append(f'language:"{_LANGUAGE_QUERY[code]}"')
        return " ".join(parts)

    # ----------------------------------------------------------------- details

    def get_details(self, manga: Manga) -> Manga:
        cached = self._details.get(manga.id)
        if cached is not None:
            return cached

        payload = self._get_json(f"/galleries/{manga.id}")
        title_map = payload.get("title") or {}
        title = (
            title_map.get("english")
            or title_map.get("pretty")
            or title_map.get("japanese")
            or manga.title
        )
        alt_titles = [
            value
            for key, value in title_map.items()
            if value and key != "english" and value != title
        ]

        tags: list[Tag] = []
        authors: list[str] = []
        artists: list[str] = []
        languages: list[str] = []
        for item in payload.get("tags") or []:
            native_type = item.get("type") or ""
            name = (item.get("name") or "").strip()
            if native_type == "language":
                code = language_from_name(name)
                if code and code not in languages:
                    languages.append(code)
                continue
            if native_type == "artist":
                if name and name not in authors:
                    authors.append(name)
                    artists.append(name)
                continue
            tag = tag_from_nhentai(item)
            if tag is not None:
                tags.append(tag)

        if not languages:
            languages = _languages_from_title(title)

        cover = payload.get("cover") or {}
        cover_path = _fix_double_extension(cover.get("path") or "")
        cover_url = f"{THUMB_BASE}/{cover_path}" if cover_path else manga.cover_url_text

        page_paths = [
            page.get("path") or ""
            for page in payload.get("pages") or []
            if page.get("path")
        ]
        page_urls = [f"{IMAGE_BASE}/{path}" for path in page_paths]

        favorite_count = int(payload.get("num_favorites") or 0)
        self._favorites[manga.id] = favorite_count

        parsed = Manga(
            id=str(payload.get("id") or manga.id),
            title=title,
            alt_titles=alt_titles[:4],
            description=manga.description,
            content_rating="pornographic",
            tags=tags,
            authors=authors,
            artists=artists,
            languages=languages,
            source=self.key,
            source_name=self.name,
            url_text=f"{SITE_URL}/g/{manga.id}/",
            cover_url_text=cover_url,
            page_count=int(payload.get("num_pages") or len(page_urls)),
        )
        self._details[parsed.id] = parsed
        self._pages[parsed.id] = page_urls
        return parsed

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        details = self.get_details(manga)
        pages = self._pages.get(details.id) or []
        return [
            Chapter(
                id=details.id,
                chapter=None,
                volume=None,
                title="Gallery",
                language=details.languages[0] if details.languages else "ja",
                pages=len(pages) or int(details.page_count or 0),
                page_urls=pages,
                gallery=True,
            )
        ]

    def get_statistics(self, manga: Manga) -> Statistics | None:
        self.get_details(manga)
        return Statistics(follows=self._favorites.get(manga.id, 0))

    def get_page_urls(self, chapter: Chapter, *, data_saver: bool = False) -> list[str]:
        return list(chapter.page_urls)

    def download_page(self, url: str) -> bytes:
        # i1..i4 are unreachable from some networks; i.nhentai.net is stable.
        try:
            return get_bytes(url, limiter=self.image_limiter)
        except HttpError:
            fallback = url.replace("https://i1.nhentai.net", IMAGE_BASE)
            fallback = fallback.replace("https://i2.nhentai.net", IMAGE_BASE)
            fallback = fallback.replace("https://i3.nhentai.net", IMAGE_BASE)
            fallback = fallback.replace("https://i4.nhentai.net", IMAGE_BASE)
            if fallback == url:
                raise
            return get_bytes(fallback, limiter=self.image_limiter)

    def download_cover(self, manga: Manga, size: int = 256) -> bytes:
        url = manga.cover_url_text
        if not url:
            raise HttpError("This gallery has no cover")
        return get_bytes(url, limiter=self.image_limiter)

    # ------------------------------------------------------------------- parse

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = request(
            "GET",
            f"{API_BASE}{path}",
            params=params,
            limiter=self.api_limiter,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        return response.json()

    def _parse_card(self, item: dict[str, Any]) -> Manga:
        title = (
            item.get("english_title")
            or item.get("japanese_title")
            or f"Gallery {item.get('id')}"
        )
        thumbnail = _fix_double_extension(item.get("thumbnail") or "")
        cover_url = f"{THUMB_BASE}/{thumbnail}" if thumbnail else ""
        tag_ids = [
            str(value) for value in item.get("tag_ids") or [] if value is not None
        ]
        return Manga(
            id=str(item.get("id")),
            title=str(title),
            content_rating="pornographic",
            languages=_languages_from_title(str(title)),
            source=self.key,
            source_name=self.name,
            url_text=f"{SITE_URL}/g/{item.get('id')}/",
            cover_url_text=cover_url,
            page_count=int(item.get("num_pages") or 0) or None,
            links={"tag_ids": ",".join(tag_ids)} if tag_ids else {},
        )

    def get_available_languages(self) -> list[str]:
        return list(LANGUAGE_CODES.values())


def _fix_double_extension(path: str) -> str:
    """The API sometimes returns ``cover.webp.webp``; keep one extension."""
    for ext in (".webp", ".jpg", ".jpeg", ".png", ".gif"):
        if path.endswith(ext + ext):
            return path[: -len(ext)]
    return path


def _languages_from_title(title: str) -> list[str]:
    languages: list[str] = []
    for chunk in _TITLE_BRACKETS.findall(title):
        for word in re.split(r"[,/|]+", chunk):
            code = TITLE_LANGUAGE_NAMES.get(word.strip().lower())
            if code and code not in languages:
                languages.append(code)
    return languages
