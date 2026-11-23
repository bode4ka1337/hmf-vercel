"""HentaiRead.moe HTML source (https://hentairead.moe/)."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import HttpError
from ..models import Chapter, Manga, SearchQuery, Statistics, Tag
from .base import SearchPage
from .galleries import GallerySource, _clean, _tag

_HENTAI_HREF = re.compile(r"/hentai/([^/?#\"']+)")
_PAGE_IMG = re.compile(
    r"https://readimg\.site/galleries/(\d+)/(\d+)\.(jpg|jpeg|png|webp)", re.IGNORECASE
)


class HentaiReadMoeSource(GallerySource):
    key = "hentaireadmoe"
    name = "HentaiRead.moe"
    site_url = "https://hentairead.moe"
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
            # Keep remaining selected tags as text hints for AND searches.
            for tag in tags[1:]:
                name = (tag.name or "").strip()
                if name:
                    text_parts.append(name)
            base_url = f"{self.site_url}/tag/{tag_slug}"
        else:
            for tag in tags:
                name = (tag.name or "").strip()
                if name:
                    text_parts.append(name)
            base_url = f"{self.site_url}/hentai-list"

        # Excluded tags as negative text terms (single-word only, same as base logic).
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
            params["s"] = text

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
        params: dict[str, Any] = {}
        if text:
            params["s"] = text
        if page > 1:
            params["page"] = page
        return f"{self.site_url}/hentai-list", params or None

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        for a in soup.select('a[href*="/hentai/"]'):
            href = (a.get("href") or "").strip()
            m = _HENTAI_HREF.search(href)
            if not m:
                continue
            slug = m.group(1).strip("/")
            if not slug or slug in seen:
                continue

            img = a.select_one("img")
            # Prefer links that represent a card/cover
            cover = ""
            title = ""
            if img is not None:
                cover = (
                    img.get("data-src")
                    or img.get("data-lazy-src")
                    or img.get("src")
                    or ""
                ).strip()
                if cover.startswith("//"):
                    cover = "https:" + cover
                title = _clean((img.get("alt") or "").strip())
            if not title:
                title = _clean((a.get("title") or a.get("aria-label") or "").strip())
            # If this anchor has no image and no useful title, it's likely nav; skip
            if img is None and not title:
                continue

            seen.add(slug)
            url = href if href.startswith("http") else f"{self.site_url}/hentai/{slug}"
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
        return f"{self.site_url}/hentai/{manga_id}"

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

        tags: list[Tag] = []
        authors: list[str] = []
        languages: list[str] = []
        for a in soup.select(
            'a[href*="/tag/"], a[href*="/genre/"], a[href*="/artist/"], '
            'a[href*="/group/"], a[href*="/parody/"], a[href*="/character/"], '
            'a[href*="/language/"], a[href*="/category/"]'
        ):
            href = (a.get("href") or "").lower()
            name = _clean(a.get_text(" ", strip=True))
            if not name:
                continue
            if "/artist/" in href:
                if name not in authors:
                    authors.append(name)
                continue
            if "/language/" in href:
                # use slug-like name for language code-ish labels when obvious
                lname = name.lower()
                if lname and lname not in languages:
                    languages.append(lname)
                continue
            if "/genre/" in href or "/category/" in href:
                tag = _tag(name, "format")
            else:
                tag = _tag(name, "theme")
            if tag and all(t.id != tag.id for t in tags):
                tags.append(tag)

        # Page images are embedded: https://readimg.site/galleries/<gid>/<n>.<ext>
        page_urls: list[str] = []
        seen_pages: set[int] = set()
        for m in _PAGE_IMG.finditer(text):
            idx = int(m.group(2))
            if idx in seen_pages:
                continue
            seen_pages.add(idx)
            page_urls.append((idx, m.group(0)))
        page_urls.sort(key=lambda x: x[0])
        urls = [u for _, u in page_urls]
        count = len(urls)

        if urls:
            self._meta[manga.id] = {"page_urls": urls, "pages": count}

        return Manga(
            id=manga.id,
            title=title or manga.title,
            alt_titles=manga.alt_titles,
            description="",
            content_rating="pornographic",
            tags=tags,
            authors=authors,
            artists=authors,
            languages=languages,
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
        urls = list(meta.get("page_urls") or [])
        page_count = int(meta.get("pages") or details.page_count or len(urls) or 0)
        return [
            Chapter(
                id=details.id,
                chapter=None,
                volume=None,
                title="Gallery",
                language=(details.languages[0] if details.languages else "en"),
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
