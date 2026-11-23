"""nhentai.xxx HTML source."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import HttpError, RateLimiter, request
from ..models import Chapter, Manga, SearchQuery, Statistics, Tag
from .base import SearchPage
from .galleries import BROWSER_HEADERS, GallerySource, _clean

_GALLERY_HREF = re.compile(r"/g/(\d+)/?")
_THUMB_PAGE = re.compile(r"/(\d+)t\.(jpg|jpeg|png|webp)$", re.IGNORECASE)


class NhentaiXxxSource(GallerySource):
    key = "nhentaixxx"
    name = "nhentai.xxx"
    site_url = "https://nhentai.xxx"
    page_extension = "jpg"
    tag_source_key = None
    supports_multi_tag = True
    search_limiter = RateLimiter(0.35)
    image_limiter = RateLimiter(0.06)
    default_language = "en"

    # ------------------------------------------------------------------ search

    def search(self, query: SearchQuery, *, page: int = 1, offset: int = 0, limit: int = 24):
        page = max(1, page)
        params: dict[str, Any] = {}
        if page > 1:
            params["page"] = page

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
            base_url = f"{self.site_url}/tag/{tag_slug}/"
        else:
            for tag in tags:
                name = (tag.name or "").strip()
                if name:
                    text_parts.append(name)
            base_url = f"{self.site_url}/search" if text_parts else f"{self.site_url}/"

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
            params["q"] = text

        if not use_tag_page and not text and page <= 1:
            params = None
        page_html = self._get_html(base_url, params)
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
        if text:
            return f"{self.site_url}/search", {"q": text, "page": page}
        # No query: browse latest/home-style listing by page.
        if page <= 1:
            return f"{self.site_url}/", None
        return f"{self.site_url}/", {"page": page}

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        for card in soup.select("div.gallery_item"):
            link = card.select_one('a[href*="/g/"]')
            if link is None:
                continue
            href = (link.get("href") or "").strip()
            match = _GALLERY_HREF.search(href)
            if not match:
                continue
            gid = match.group(1)
            if gid in seen:
                continue
            seen.add(gid)

            img = link.select_one("img")
            thumb = ""
            if img is not None:
                thumb = (img.get("data-src") or img.get("src") or "").strip()
            if thumb.startswith("//"):
                thumb = "https:" + thumb

            caption = link.select_one(".caption")
            title = _clean(caption.get_text(" ", strip=True)) if caption else ""
            if not title:
                title = _clean((link.get("title") or "").strip())

            cover = re.sub(r"/thumb\.", "/cover.", thumb) if thumb else ""

            items.append(
                Manga(
                    id=gid,
                    title=title or f"Gallery {gid}",
                    content_rating="pornographic",
                    languages=["en"],
                    source=self.key,
                    source_name=self.name,
                    url_text=f"{self.site_url}/g/{gid}/",
                    cover_url_text=cover or thumb,
                    page_count=None,
                    links={},
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
        return f"{self.site_url}/g/{manga_id}/"

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        soup = BeautifulSoup(text, "html.parser")

        h1 = soup.select_one("h1")
        title = _clean(h1.get_text(" ", strip=True)) if h1 else manga.title

        cover = manga.cover_url_text
        og = soup.select_one('meta[property="og:image"]')
        if og and (og.get("content") or "").strip():
            cover = (og.get("content") or "").strip()
        if not cover:
            for img in soup.select('img[src*="/cover."], img[data-src*="/cover."]'):
                src = (img.get("data-src") or img.get("src") or "").strip()
                if src:
                    cover = "https:" + src if src.startswith("//") else src
                    break

        # Page thumbnails reveal base path and page count: .../1t.jpg, 2t.jpg, ...
        thumb_urls = []
        for img in soup.select('img[src*="nhentaimg"], img[data-src*="nhentaimg"]'):
            src = (img.get("data-src") or img.get("src") or "").strip()
            if src.startswith("//"):
                src = "https:" + src
            if _THUMB_PAGE.search(src):
                thumb_urls.append(src)

        base = ""
        ext = "webp"
        count = 0
        if thumb_urls:
            # Use highest page number found.
            pages = []
            for src in thumb_urls:
                m = _THUMB_PAGE.search(src)
                if m:
                    pages.append((int(m.group(1)), m.group(2), src))
            if pages:
                pages.sort()
                count = pages[-1][0]
                sample = pages[0][2]
                m = _THUMB_PAGE.search(sample)
                if m:
                    base = _THUMB_PAGE.sub("", sample).rstrip("/")
                    ext = m.group(2) or "webp"

        # Reader images are often .webp even when thumbs are .jpg.
        page_ext = ext
        if base and count:
            try:
                p1 = request(
                    "GET",
                    f"{self.site_url}/g/{manga.id}/1/",
                    limiter=self.search_limiter,
                    headers=BROWSER_HEADERS,
                    retries=2,
                    timeout=20.0,
                ).text
                m = re.search(
                    rf'{re.escape(base)}/1\.(jpg|jpeg|png|webp)',
                    p1,
                    re.IGNORECASE,
                )
                if m:
                    page_ext = m.group(1).lower()
            except Exception:
                page_ext = "webp" if ext == "jpg" else ext

        if base and count:
            self._meta[manga.id] = {
                "base": base,
                "pages": count,
                "extension": page_ext,
            }

        return Manga(
            id=manga.id,
            title=title or manga.title,
            alt_titles=manga.alt_titles,
            description="",
            content_rating="pornographic",
            tags=[],
            authors=[],
            artists=[],
            languages=["en"],
            source=self.key,
            source_name=self.name,
            url_text=self._gallery_url(manga.id),
            cover_url_text=cover or manga.cover_url_text,
            page_count=count or manga.page_count,
            links=manga.links,
        )

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        details = self.get_details(manga)
        meta = self._meta.get(details.id) or {}
        base = meta.get("base") or ""
        page_count = int(meta.get("pages") or details.page_count or 0)
        extension = meta.get("extension") or self.page_extension
        urls = [f"{base}/{index}.{extension}" for index in range(1, page_count + 1)] if base else []
        return [
            Chapter(
                id=details.id,
                chapter=None,
                volume=None,
                title="Gallery",
                language="en",
                pages=page_count or len(urls),
                page_urls=urls,
                gallery=True,
            )
        ]

    def get_statistics(self, manga: Manga) -> Statistics | None:
        return None

    def download_page(self, url: str) -> bytes:
        try:
            return super().download_page(url)
        except HttpError:
            # Try common alternative extensions.
            for alt in (".jpg", ".jpeg", ".png", ".webp"):
                if url.lower().endswith(alt):
                    continue
                alt_url = re.sub(r"\.(jpg|jpeg|png|webp)$", alt, url, flags=re.IGNORECASE)
                if alt_url == url:
                    continue
                try:
                    return super().download_page(alt_url)
                except HttpError:
                    pass
            raise
