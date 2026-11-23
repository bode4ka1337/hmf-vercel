"""HentaiRox HTML source (https://hentairox.com/)."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import request
from ..models import Chapter, Manga, SearchQuery, Tag
from .base import SearchPage
from .galleries import BROWSER_HEADERS, GallerySource, _clean, _tag

_GALLERY_HREF = re.compile(r"/gallery/(\d+)/?")
_THUMB_PAGE = re.compile(r"/(\d+)t\.(jpg|jpeg|png|webp)$", re.IGNORECASE)


class HentaiRoxSource(GallerySource):
    key = "hentairox"
    name = "HentaiRox"
    site_url = "https://hentairox.com"
    page_extension = "webp"
    search_limiter = GallerySource.search_limiter
    image_limiter = GallerySource.image_limiter

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
            base_url = f"{self.site_url}/search/" if text_parts else f"{self.site_url}/"

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
            params["key"] = text

        if not use_tag_page and not text and page <= 1:
            params = None
        elif not use_tag_page and not text and page > 1:
            # home pagination on hentairox is /page/N/ in some forms? earlier probe: /?page=2 not checked,
            # but /page/2/ style used in _search_request; handle via URL below.
            pass

        if not use_tag_page and not text:
            base_url = f"{self.site_url}/" if page == 1 else f"{self.site_url}/page/{page}/"
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
        if not text:
            url = f"{self.site_url}/" if page == 1 else f"{self.site_url}/page/{page}/"
            return url, None
        params: dict[str, Any] = {"key": text}
        if page > 1:
            params["page"] = page
        return f"{self.site_url}/search/", params

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        # Search and home listings use gallery cards with links to /gallery/{id}/
        for link in soup.select('a[href*="/gallery/"]'):
            href = (link.get("href") or "").strip()
            m = _GALLERY_HREF.search(href)
            if not m:
                continue
            gid = m.group(1)
            if gid in seen:
                continue
            # Prefer cover links/cards with an image
            img = link.select_one("img")
            if img is None:
                continue
            seen.add(gid)
            thumb = (img.get("data-src") or img.get("src") or "").strip()
            if thumb.startswith("//"):
                thumb = "https:" + thumb
            title = _clean((link.get("aria-label") or link.get("title") or "").strip())
            if title.lower().startswith("open "):
                title = title[5:].strip()
            if not title:
                # Try caption/title in parent card
                card = link.find_parent(["article", "div"])
                if card:
                    cap = card.select_one(".hr-gallery-card__title a, .hr-home-card__title a, .caption, h2 a, h2")
                    if cap:
                        title = _clean(cap.get_text(" ", strip=True))
            cover = re.sub(r"/thumb\.", "/cover.", thumb) if thumb else ""
            items.append(
                Manga(
                    id=gid,
                    title=title or f"Gallery {gid}",
                    content_rating="pornographic",
                    languages=[],
                    source=self.key,
                    source_name=self.name,
                    url_text=self._gallery_url(gid),
                    cover_url_text=cover or thumb,
                    page_count=None,
                    links={},
                )
            )
        return items

    # ----------------------------------------------------------------- details

    def _gallery_url(self, manga_id: str) -> str:
        return f"{self.site_url}/gallery/{manga_id}/"

    def get_details(self, manga: Manga) -> Manga:
        cached = self._details.get(manga.id)
        if cached is not None:
            return cached
        page_html = self._get_html(self._gallery_url(manga.id))
        parsed = self._parse_gallery(page_html, manga)
        self._details[parsed.id] = parsed
        return parsed

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

        tags: list[Tag] = []
        authors: list[str] = []
        for a in soup.select('a[href*="/tag/"], a[href*="/artist/"], a[href*="/group/"], a[href*="/parody/"], a[href*="/character/"], a[href*="/language/"], a[href*="/category/"]'):
            href = (a.get("href") or "")
            name_el = a.select_one(".hr-gallery-tag__name") or a
            name = _clean(name_el.get_text(" ", strip=True))
            if not name:
                continue
            if "/tag/" in href or "/character/" in href or "/parody/" in href:
                tag = _tag(name, "theme")
                if tag and all(t.id != tag.id for t in tags):
                    tags.append(tag)
            elif "/category/" in href:
                tag = _tag(name, "format")
                if tag and all(t.id != tag.id for t in tags):
                    tags.append(tag)
            elif "/artist/" in href:
                if name not in authors:
                    authors.append(name)
            elif "/group/" in href:
                # keep groups in description context only if needed
                pass
            # language links not added as tags

        # Page count and base from thumbnails .../1t.jpg
        thumb_urls = []
        for img in soup.select("img"):
            src = (img.get("data-src") or img.get("src") or "").strip()
            if src.startswith("//"):
                src = "https:" + src
            if _THUMB_PAGE.search(src):
                thumb_urls.append(src)

        base = ""
        count = 0
        ext = "webp"
        if thumb_urls:
            pages = []
            for src in thumb_urls:
                m = _THUMB_PAGE.search(src)
                if m:
                    pages.append((int(m.group(1)), src))
            if pages:
                pages.sort()
                count = pages[-1][0]
                sample = pages[0][1]
                base = _THUMB_PAGE.sub("", sample).rstrip("/")

        # Detect full image extension (webp/jpg)
        page_ext = "webp"
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
                m = re.search(rf"{re.escape(base)}/1\.(jpg|jpeg|png|webp)", p1, re.IGNORECASE)
                if m:
                    page_ext = m.group(1).lower()
            except Exception:
                page_ext = "webp"

        if base and count:
            self._meta[manga.id] = {"base": base, "pages": count, "extension": page_ext}

        return Manga(
            id=manga.id,
            title=title or manga.title,
            alt_titles=manga.alt_titles,
            description="",
            content_rating="pornographic",
            tags=tags,
            authors=authors,
            artists=authors,
            languages=[],
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

    def download_page(self, url: str) -> bytes:
        try:
            return super().download_page(url)
        except Exception:
            for alt in (".webp", ".jpg", ".jpeg", ".png"):
                alt_url = re.sub(r"\.(jpg|jpeg|png|webp)$", alt, url, flags=re.IGNORECASE)
                if alt_url == url:
                    continue
                try:
                    return super().download_page(alt_url)
                except Exception:
                    pass
            raise
