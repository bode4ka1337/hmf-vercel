"""HentaiEra HTML source (https://hentaiera.com/)."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..models import Manga, SearchQuery, Tag
from .base import SearchPage
from .galleries import GallerySource, _clean, _tag

_GALLERY_HREF = re.compile(r"/gallery/(\d+)/?")
_THUMB_PAGE = re.compile(r"/(\d+)t\.(jpg|jpeg|png|webp)$", re.IGNORECASE)


class HentaiEraSource(GallerySource):
    key = "hentaiera"
    name = "HentaiEra"
    site_url = "https://hentaiera.com"
    page_extension = "webp"
    tag_source_key = None
    supports_multi_tag = True
    default_language = None

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
            base_url = f"{self.site_url}/search/" if (text_parts or not use_tag_page) else f"{self.site_url}/"

        # Excluded tags as negative text terms (single-word only).
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

        # No text, no tag page: browse home.
        if not use_tag_page and not text:
            base_url = f"{self.site_url}/"

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
        if text:
            params: dict[str, Any] = {"q": text}
            if page > 1:
                params["page"] = page
            return f"{self.site_url}/search/", params
        if page <= 1:
            return f"{self.site_url}/", None
        return f"{self.site_url}/", {"page": page}

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/gallery/"]'):
            href = (a.get("href") or "").strip()
            m = _GALLERY_HREF.search(href)
            if not m:
                continue
            gid = m.group(1)
            if gid in seen:
                continue
            img = a.select_one("img")
            if img is None:
                continue
            seen.add(gid)
            thumb = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            ).strip()
            if thumb.startswith("//"):
                thumb = "https:" + thumb
            # Skip placeholder/svg data thumbs if real one missing
            if thumb.startswith("data:"):
                thumb = (img.get("data-src") or "").strip()
                if thumb.startswith("//"):
                    thumb = "https:" + thumb

            title = _clean((img.get("alt") or "").strip())
            if not title:
                title = _clean((a.get("title") or "").strip())
            if not title:
                card = a.find_parent(["div", "article", "li"])
                if card:
                    h = card.select_one(".gallery_title a, .caption, h2 a, h2, h3")
                    if h:
                        title = _clean(h.get_text(" ", strip=True))

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
        for a in soup.select(
            'a[href*="/tag/"], a[href*="/tags/"], a[href*="/artist/"], a[href*="/artists/"], '
            'a[href*="/group/"], a[href*="/groups/"], a[href*="/parody/"], a[href*="/parodies/"], '
            'a[href*="/character/"], a[href*="/characters/"], a[href*="/category/"], a[href*="/categories/"], '
            'a[href*="/language/"], a[href*="/languages/"]'
        ):
            href = (a.get("href") or "").lower()
            name = _clean(a.get_text(" ", strip=True))
            if not name:
                continue
            if "/language" in href:
                continue
            if "/artist" in href:
                if name not in authors:
                    authors.append(name)
                continue
            if "/group" in href:
                continue
            if "/category" in href or "/categories" in href:
                tag = _tag(name, "format")
            else:
                tag = _tag(name, "theme")
            if tag and all(t.id != tag.id for t in tags):
                tags.append(tag)

        # Thumbnails reveal base path and count: .../1t.jpg, 2t.jpg, ...
        thumb_urls = []
        for img in soup.select("img"):
            src = (img.get("data-src") or img.get("src") or "").strip()
            if src.startswith("//"):
                src = "https:" + src
            if _THUMB_PAGE.search(src):
                thumb_urls.append(src)

        base = ""
        count = 0
        if thumb_urls:
            pages = []
            for src in thumb_urls:
                m = _THUMB_PAGE.search(src)
                if m:
                    pages.append((int(m.group(1)), src))
            if pages:
                pages.sort()
                sample = pages[0][1]
                base = _THUMB_PAGE.sub("", sample).rstrip("/")
                count = pages[-1][0]

        # Prefer explicit page count from text (thumbs may be partial/lazy-loaded).
        m = re.search(r"Pages:\s*(?:<[^>]+>\s*)*(\d+)", text, re.IGNORECASE)
        if m:
            try:
                count = max(count, int(m.group(1)))
            except Exception:
                pass
        if not m:
            m = re.search(r"(\d[\d,\.]*)\s*pages?\b", text, re.IGNORECASE)
            if m:
                try:
                    count = max(count, int(re.sub(r"\D", "", m.group(1)) or "0"))
                except Exception:
                    pass

        # Full page images are .webp on hentaiera (with fallback on download).
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
