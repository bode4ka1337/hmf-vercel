"""HentaiHere HTML source (https://hentaihere.com/)."""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import request
from ..models import Chapter, Manga, SearchQuery, Tag
from .base import SearchPage
from .galleries import BROWSER_HEADERS, GallerySource, _clean, _tag

_CONTENT_PATH = re.compile(r"/(imageset|original|doujinshi|manga|chapter)/(\d+[^/?#]*)")
_READER_DATA = re.compile(
    r'<script[^>]+data-reader-data[^>]*>(\{.*?\})</script>', re.DOTALL
)


class HentaiHereSource(GallerySource):
    key = "hentaihere"
    name = "HentaiHere"
    site_url = "https://hentaihere.com"
    page_extension = "webp"
    tag_source_key = None
    supports_multi_tag = True
    default_language = "en"

    # ------------------------------------------------------------------ search

    _TAG_PATHS = {
        "uncensored": "/category/47-uncensored",
        "decensored": "/category/47-uncensored",
        "no-censorship": "/category/47-uncensored",
    }

    def search(self, query: SearchQuery, *, page: int = 1, offset: int = 0, limit: int = 24):
        page = max(1, page)
        params: dict[str, Any] = {}
        if page > 1:
            params["page"] = page

        tags = [tag for tag in query.tags if (tag.id or tag.name).strip()]
        tag_mode = (query.tag_mode or "AND").upper()
        title = query.title.strip()
        author = query.author.strip()

        tag_path = ""
        if tags and (tag_mode == "AND" or len(tags) == 1):
            primary_slug = (tags[0].id or "").strip().lower()
            tag_path = self._TAG_PATHS.get(primary_slug, "")

        # Use dedicated tag/category page when known and no text title/author constraints.
        if tag_path and not title and not author:
            base_url = f"{self.site_url}{tag_path}"
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

        text = self._query_text(query)
        url, req_params = self._search_request(text, page)
        page_html = self._get_html(url, req_params)
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
            params: dict[str, Any] = {"q": text}
            if page > 1:
                params["page"] = page
            return f"{self.site_url}/search", params
        if page <= 1:
            return f"{self.site_url}/browse", None
        return f"{self.site_url}/browse", {"page": page}

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/imageset/"], a[href*="/original/"], a[href*="/doujinshi/"], a[href*="/manga/"]'):
            href = (a.get("href") or "").strip()
            if "/reader/" in href or "#preview" in href or "?" in href.split("/")[-1]:
                continue
            m = _CONTENT_PATH.search(href)
            if not m:
                continue
            slug = m.group(2).strip("/")
            if not slug or slug in seen:
                continue

            # Prefer card links that contain a cover image.
            img = a.select_one("img")
            cover = ""
            title = ""
            if img is not None:
                cover = (img.get("src") or img.get("data-src") or "").strip()
                if cover.startswith("//"):
                    cover = "https:" + cover
                title = _clean((img.get("alt") or "").strip())
            if not title:
                title = _clean((a.get("title") or "").strip())
            # Skip non-card links without image/title context.
            if img is None and not title:
                continue

            # Try to get a better title from nearby heading/text.
            if not title or len(title) < 3:
                card = a.find_parent(["article", "div", "li"])
                if card:
                    h = card.select_one("h2, h3, .title, .card-title")
                    if h:
                        title = _clean(h.get_text(" ", strip=True)) or title

            seen.add(slug)
            url = href if href.startswith("http") else f"{self.site_url}{href}"
            # Use full-size cover when a thumb variant is used.
            if "/thumbs/" in cover:
                cover = re.sub(r"/thumbs/([^/]+)-\d+\.(avif|webp|jpg|jpeg|png)$", r"/\1.jpg", cover)
            items.append(
                Manga(
                    id=slug,
                    title=title or slug.replace("-", " ").strip().title(),
                    content_rating="pornographic",
                    languages=[],
                    source=self.key,
                    source_name=self.name,
                    url_text=url,
                    cover_url_text=cover,
                    page_count=None,
                    links={},
                )
            )
        return items

    # ----------------------------------------------------------------- details

    def _gallery_url(self, manga_id: str) -> str:
        if manga_id.startswith("http"):
            return manga_id
        # manga_id is stored as "<id>-<slug>"; type prefix unknown, but URLs we saved
        # in url_text are canonical. Fallback: try to keep as-is not possible, so
        # store only when url_text known; get_details uses manga.url_text if set.
        return f"{self.site_url}/imageset/{manga_id}"

    def get_details(self, manga: Manga) -> Manga:
        cached = self._details.get(manga.id)
        if cached is not None:
            return cached
        url = manga.url_text or self._gallery_url(manga.id)
        page_html = self._get_html(url)
        parsed = self._parse_gallery(page_html, manga, url)
        self._details[parsed.id] = parsed
        return parsed

    def _parse_gallery(self, text: str, manga: Manga, url: str) -> Manga:
        soup = BeautifulSoup(text, "html.parser")

        h1 = soup.select_one("h1")
        title = _clean(h1.get_text(" ", strip=True)) if h1 else manga.title

        cover = manga.cover_url_text
        og = soup.select_one('meta[property="og:image"]')
        if og and (og.get("content") or "").strip():
            cover = (og.get("content") or "").strip()

        tags: list[Tag] = []
        authors: list[str] = []
        for a in soup.select('a[href*="/category/"], a[href*="/content/"], a[href*="/artist/"], a[href*="/tag/"]'):
            href = (a.get("href") or "").lower()
            name = _clean(a.get_text(" ", strip=True))
            if not name:
                continue
            if "/artist/" in href:
                if name not in authors:
                    authors.append(name)
                continue
            # /category/ and /content/ act as tags/genres on this site
            tag = _tag(name, "theme")
            if tag and all(t.id != tag.id for t in tags):
                tags.append(tag)

        # Find reader URL to extract page images.
        reader_url = ""
        m = re.search(r'href="(https://hentaihere\.com/reader/[^"]+/page-1)"', text)
        if m:
            reader_url = html_module.unescape(m.group(1))
        if not reader_url:
            m = re.search(r'href="(/reader/[^"]+/page-1)"', text)
            if m:
                reader_url = self.site_url + html_module.unescape(m.group(1))

        page_urls: list[str] = []
        if reader_url:
            try:
                reader_html = self._get_html(reader_url)
                dm = _READER_DATA.search(reader_html)
                if dm:
                    data = json.loads(html_module.unescape(dm.group(1)))
                    imgs = data.get("images") or []
                    if isinstance(imgs, list):
                        page_urls = [str(u) for u in imgs if u]
            except Exception:
                page_urls = []

        if page_urls:
            self._meta[manga.id] = {"page_urls": page_urls, "pages": len(page_urls)}

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
            url_text=url,
            cover_url_text=cover or manga.cover_url_text,
            page_count=len(page_urls) or manga.page_count,
            links=manga.links,
        )

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        details = self.get_details(manga)
        meta = self._meta.get(details.id) or {}
        urls = list(meta.get("page_urls") or [])
        page_count = int(meta.get("pages") or details.page_count or len(urls) or 0)
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
