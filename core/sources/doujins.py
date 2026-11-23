"""Doujins.com HTML source: https://doujins.com/."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import HttpError, RateLimiter, request
from ..models import (
    Chapter,
    Manga,
    SearchQuery,
    Statistics,
    Tag,
)
from .base import SearchPage
from .galleries import BROWSER_HEADERS, GallerySource, _clean, _tag

_GALLERY_PATH = re.compile(r"^/[a-z0-9-]+/[^/]+-(\d+)$", re.IGNORECASE)


class DoujinsSource(GallerySource):
    key = "doujins"
    name = "Doujins"
    site_url = "https://doujins.com"
    page_extension = "jpg"
    tag_source_key = None
    supports_multi_tag = True
    search_limiter = RateLimiter(0.35)
    image_limiter = RateLimiter(0.06)
    default_language = "en"

    # ------------------------------------------------------------------ search

    def search(self, query: SearchQuery, *, page: int = 1, offset: int = 0, limit: int = 24):
        page = max(1, page)
        params: dict[str, Any] = {"page": page}

        tags = [tag for tag in query.tags if (tag.id or tag.name).strip()]
        tag_mode = (query.tag_mode or "AND").upper()
        use_tag_page = bool(tags) and (tag_mode == "AND" or len(tags) == 1)

        text_parts: list[str] = []
        if query.title.strip():
            text_parts.append(query.title.strip())
        if query.author.strip():
            text_parts.append(query.author.strip())

        if use_tag_page:
            primary = tags[0]
            tag_slug = (primary.id or "").strip() or _clean(primary.name).lower().replace(" ", "-")
            for tag in tags[1:]:
                name = (tag.name or "").strip()
                if name:
                    text_parts.append(name)
            base_url = f"{self.site_url}/tags/{tag_slug}"
            # doujins tag listing uses 'words' for additional text filtering in some cases; keep in params
        else:
            for tag in tags:
                name = (tag.name or "").strip()
                if name:
                    text_parts.append(name)
            base_url = f"{self.site_url}/searches"

        if text_parts or use_tag_page:
            for tag in query.excluded_tags:
                name = (tag.name or "").strip()
                slug = (tag.id or "").strip()
                term = ""
                if name and not re.search(r"\s", name):
                    term = name
                elif slug and not re.search(r"\s", slug) and slug.replace("-", "").isalnum():
                    term = slug
                if term:
                    text_parts.append(f"-{term}")

        text = " ".join(text_parts).strip()
        if text:
            params["words"] = text

        page_html = self._get_html(base_url, params or None)
        raw_items = self._parse_cards(page_html)
        items = self.filter_results(query, raw_items)

        total = self._parse_total(page_html)
        if total is not None:
            page_size = max(len(raw_items), 20)
            has_more = (page - 1) * page_size + len(raw_items) < total
        else:
            has_more = len(raw_items) >= 5
        return SearchPage(items=items, total=total, has_more=has_more)

    def _search_request(self, text: str, page: int) -> tuple[str, dict[str, Any] | None]:
        params: dict[str, Any] = {"page": page}
        if text:
            params["words"] = text
        return f"{self.site_url}/searches", params

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        for link in soup.select("div.thumbnail-doujin a[href]"):
            href = (link.get("href") or "").rstrip("/")
            if not _GALLERY_PATH.match(href):
                continue
            path = href.lstrip("/")
            if path in seen:
                continue
            title = _clean(link.get_text(" ", strip=True))
            img = link.select_one("img")
            thumb = ""
            if img is not None:
                thumb = (img.get("src") or img.get("data-src") or "").strip()
            seen.add(path)
            items.append(
                self._result(
                    native_id=path,
                    title=title or path.rsplit("-", 1)[0].rsplit("/", 1)[-1],
                    url=f"{self.site_url}/{path}",
                    thumb=thumb,
                    category="",
                    languages=["en"],
                )
            )
        return items

    # ----------------------------------------------------------------- details

    def get_details(self, manga: Manga) -> Manga:
        cached = self._details.get(manga.id)
        if cached is not None:
            return cached
        page_html = self._get_html(self._gallery_url(manga.id))
        parsed = self._parse_gallery(page_html, manga)
        self._details[parsed.id] = parsed
        return parsed

    def _gallery_url(self, manga_id: str) -> str:
        return f"{self.site_url}/{manga_id}"

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        soup = BeautifulSoup(text, "html.parser")

        # Title: last chunk of "Series»Doujins»Title".
        title = manga.title
        folder = soup.select_one("div.folder-title")
        if folder is not None:
            chunks = [c.strip() for c in folder.get_text(" ", strip=True).split("»") if c.strip()]
            if chunks:
                title = _clean(chunks[-1]) or title

        # Authors: "Artist:name1,name2".
        authors: list[str] = []
        artist_el = soup.select_one("div.gallery-artist")
        if artist_el is not None:
            raw = _clean(artist_el.get_text(" ", strip=True))
            raw = re.sub(r"^Artist:\s*", "", raw, flags=re.IGNORECASE)
            authors = [part.strip() for part in raw.split(",") if part.strip()]

        # Tags: links of the form /searches?tag_id=NNN.
        tags: list[Tag] = []
        for a in soup.select("a[href*='tag_id=']"):
            name = _clean(a.get_text(" ", strip=True))
            if not name:
                continue
            tag = _tag(name, "theme")
            if tag is not None and all(tag.id != existing.id for existing in tags):
                tags.append(tag)

        # Reader pages live on the gallery page itself: signed CDN URLs.
        page_urls: list[str] = []
        for img in soup.select("img.swiper-lazy[data-src]"):
            src = (img.get("data-src") or "").strip()
            if "static.doujins.com" in src and src not in page_urls:
                page_urls.append(src)

        if page_urls:
            self._meta[manga.id] = {"page_urls": page_urls}

        return Manga(
            id=manga.id,
            title=title or manga.title,
            alt_titles=manga.alt_titles,
            description="",
            content_rating="pornographic",
            tags=tags,
            authors=authors,
            artists=authors,
            languages=["en"],
            source=self.key,
            source_name=self.name,
            url_text=self._gallery_url(manga.id),
            cover_url_text=manga.cover_url_text,
            page_count=len(page_urls) or manga.page_count,
            links=manga.links,
        )

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        details = self.get_details(manga)
        page_urls = list((self._meta.get(details.id) or {}).get("page_urls") or [])
        return [
            Chapter(
                id=details.id,
                chapter=None,
                volume=None,
                title="Gallery",
                language="en",
                pages=len(page_urls),
                page_urls=page_urls,
                gallery=True,
            )
        ]

    def get_statistics(self, manga: Manga) -> Statistics | None:
        return None

    def check_available(self) -> tuple[bool, str]:
        try:
            response = request(
                "GET",
                self.site_url,
                limiter=self.api_limiter,
                retries=1,
                timeout=15.0,
                headers=BROWSER_HEADERS,
            )
            return True, f"HTTP {response.status_code}"
        except HttpError as exc:
            return False, str(exc)[:160]
