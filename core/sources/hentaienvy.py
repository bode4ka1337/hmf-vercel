"""HentaiEnvy HTML source (https://hentaienvy.com/)."""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from ..models import Chapter, Manga, SearchQuery, Tag, language_from_name
from .base import SearchPage
from .galleries import GallerySource, _clean, _languages_from_html, _tag

_GALLERY_HREF = re.compile(r"/gallery/(\d+)/?")
_TAG_HREF = re.compile(r"/tag/([^/\"']+)/?")
_TOTAL = re.compile(r"\(([\d][\d,\s.]*)\)\s*results", re.IGNORECASE)
_PAGE_JSON = re.compile(
    r'<script[^>]*id="readerPagesJson"[^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_IMAGE_BASE = re.compile(r'data-reader-image-base="([^"]+)"', re.IGNORECASE)
_READER_TOTAL = re.compile(r'data-reader-total="(\d+)"', re.IGNORECASE)
_THUMB_SRC = re.compile(r"/(\d+)t\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)


class HentaiEnvySource(GallerySource):
    key = "hentaienvy"
    name = "HentaiEnvy"
    site_url = "https://hentaienvy.com"
    page_extension = "webp"

    # ------------------------------------------------------------------ search

    def search(
        self, query: SearchQuery, *, page: int = 1, offset: int = 0, limit: int = 24
    ):
        page = max(1, page)
        tags = [tag for tag in query.tags if (tag.id or tag.name).strip()]
        tag_mode = (query.tag_mode or "AND").upper()
        excluded = [tag for tag in query.excluded_tags if (tag.id or tag.name).strip()]

        parts: list[str] = []
        if query.title.strip():
            parts.append(query.title.strip())
        if query.author.strip():
            parts.append(query.author.strip())

        # The site's /tag/ pages ignore any additional key/exclusion, so they
        # are only used for a clean single-tag query. Everything else goes
        # through /search/ where every term is honoured.
        use_tag_page = (
            len(tags) == 1
            and tag_mode == "AND"
            and not parts
            and not excluded
        )

        params: dict[str, Any] = {}
        if use_tag_page:
            primary = tags[0]
            slug = (primary.id or "").strip() or _tag_slug(primary.name)
            base_url = f"{self.site_url}/tag/{slug}/"
        else:
            for tag in tags:
                name = (tag.name or "").strip()
                if name:
                    parts.append(name)
            base_url = f"{self.site_url}/search/"

        for tag in excluded:
            name = (tag.name or "").strip()
            slug = (tag.id or "").strip()
            term = ""
            if name and not re.search(r"\s", name):
                term = name
            elif slug and not re.search(r"\s", slug) and slug.replace("-", "").isalnum():
                term = slug
            if term:
                parts.append(f"-{term}")

        text = " ".join(parts).strip()
        if text:
            params["key"] = text
        if page > 1:
            params["page"] = page
        if not params and page == 1:
            params = None

        page_html = self._get_html(base_url, params)
        raw_items = self._parse_cards(page_html)
        items = self.filter_results(query, raw_items)

        total = self._parse_total(page_html)
        if total is not None:
            page_size = max(len(raw_items), 20)
            has_more = (page - 1) * page_size + len(raw_items) < total
        else:
            has_more = len(raw_items) >= 20
        return SearchPage(items=items, total=total, has_more=has_more)

    def _parse_total(self, text: str) -> int | None:
        match = _TOTAL.search(text)
        if not match:
            return None
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else None

    def _parse_cards(self, text: str) -> list[Manga]:
        soup = BeautifulSoup(text, "html.parser")
        items: list[Manga] = []
        seen: set[str] = set()
        for card in soup.select("article.hnv-gallery-card"):
            link = card.select_one('a.hnv-gallery-card__cover[href*="/gallery/"]')
            if link is None:
                continue
            match = _GALLERY_HREF.search(link.get("href") or "")
            if not match:
                continue
            gid = match.group(1)
            if gid in seen:
                continue
            seen.add(gid)

            img = link.select_one("img")
            thumb = ""
            if img is not None:
                thumb = (img.get("src") or img.get("data-src") or "").strip()
            title_node = card.select_one(".hnv-gallery-card__title a")
            title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
            if not title:
                title = _clean(link.get("aria-label") or "")
                if title.lower().startswith("open "):
                    title = title[5:].strip()

            category_node = card.select_one(".hnv-gallery-card__category")
            category = _clean(category_node.get_text(" ", strip=True)) if category_node else ""
            items.append(
                self._result(
                    native_id=gid,
                    title=title,
                    url=self._gallery_url(gid),
                    thumb=thumb,
                    category=category,
                    languages=_languages_from_html(str(card)),
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
        self._load_page_meta(parsed)
        return parsed

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        soup = BeautifulSoup(text, "html.parser")

        title_node = soup.select_one("#gallery-title") or soup.select_one("h1")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else manga.title

        cover = manga.cover_url_text
        cover_node = soup.select_one(".hnv-gallery-cover img")
        if cover_node is not None:
            src = (cover_node.get("src") or cover_node.get("data-src") or "").strip()
            if src:
                cover = "https:" + src if src.startswith("//") else src

        tags: list[Tag] = []
        authors: list[str] = []
        credit_labels = {
            "Tags": "Tags",
            "Parodies": "Parodies",
            "Characters": "Characters",
            "Groups": "Groups",
            "Artists": "Artists",
            "Languages": "Languages",
            "Category": "Category",
        }
        groups: dict[str, list[str]] = {}
        languages: list[str] = []

        for block in soup.select(".hnv-gallery-entity-group"):
            label_node = block.select_one(".hnv-gallery-entity-label")
            label = _clean(label_node.get_text(" ", strip=True)).rstrip(":") if label_node else ""
            names = [
                _clean(node.get_text(" ", strip=True))
                for node in block.select(".hnv-gallery-tag__name")
            ]
            names = [name for name in names if name]
            if not label:
                continue

            if label == "Languages":
                for raw in _TAG_HREF.findall(str(block)):
                    code = language_from_name(raw.replace("-", " "))
                    if code and code not in languages:
                        languages.append(code)
            if label == "Artists":
                for name in names:
                    if name not in authors:
                        authors.append(name)

            key = credit_labels.get(label)
            if key and names:
                groups[key] = names

            groups_for_tag = {
                "Tags": "theme",
                "Parodies": "content",
                "Characters": "content",
                "Category": "format",
            }
            tag_group = groups_for_tag.get(label)
            if tag_group:
                for name in names:
                    tag = _tag(name, tag_group)
                    if tag is not None and all(item.id != tag.id for item in tags):
                        tags.append(tag)

        pages = 0
        pages_node = soup.select_one(".hnv-gallery-pages .hnv-gallery-entity-items")
        if pages_node is not None:
            digits = re.sub(r"\D", "", pages_node.get_text(" ", strip=True))
            pages = int(digits) if digits else 0

        # Keep the gallery page's page count/base as a fallback: the reader page
        # is only fetched when chapters are actually requested.
        thumbs = soup.select_one("#js-thumbs-grid")
        if thumbs is not None:
            total = int(thumbs.get("data-total-pages") or 0)
            pages = pages or total
            sample = thumbs.select_one("img[src]")
            if sample is not None:
                src = (sample.get("src") or "").strip()
                match = _THUMB_SRC.search(src)
                if match:
                    self._meta.setdefault(manga.id, {})["base"] = src[: match.start()]
            if total:
                self._meta.setdefault(manga.id, {})["pages"] = total

        description_lines = []
        for label in ("Artists", "Groups", "Parodies", "Characters"):
            names = groups.get(label) or []
            if names:
                description_lines.append(f"**{label}:** {', '.join(names)}")
        description = "\n\n".join(description_lines)

        return Manga(
            id=manga.id,
            title=title or manga.title,
            alt_titles=manga.alt_titles,
            description=description,
            content_rating="pornographic",
            tags=tags,
            authors=authors,
            artists=list(authors),
            languages=languages or _languages_from_html(text),
            source=self.key,
            source_name=self.name,
            url_text=self._gallery_url(manga.id),
            cover_url_text=cover,
            page_count=pages or manga.page_count,
            links=manga.links,
        )

    # ------------------------------------------------------------------ images

    def _load_page_meta(self, manga: Manga) -> dict[str, Any]:
        cached = self._meta.get(manga.id) or {}
        if cached.get("urls"):
            return cached

        base = cached.get("base") or ""
        if manga.cover_url_text:
            base = re.sub(
                r"/cover\.(?:jpg|jpeg|png|webp)$",
                "",
                manga.cover_url_text,
                flags=re.IGNORECASE,
            )
        count = int(cached.get("pages") or manga.page_count or 0)
        extensions: dict[int, str] = {}

        try:
            reader_html = self._get_html(f"{self.site_url}/g/{manga.id}/1/")
            base_match = _IMAGE_BASE.search(reader_html)
            if base_match:
                base = html_module.unescape(base_match.group(1))
            total_match = _READER_TOTAL.search(reader_html)
            if total_match:
                count = int(total_match.group(1))
            json_match = _PAGE_JSON.search(reader_html)
            if json_match:
                pages_data = json.loads(json_match.group(1))
                extensions = {
                    int(item["page"]): str(item.get("ext") or self.page_extension).lower()
                    for item in pages_data
                    if isinstance(item, dict) and item.get("page")
                }
                count = len(extensions) or count
        except Exception:
            # The gallery page already gave us base and page count.
            pass

        urls = [
            f"{base}/{index}.{extensions.get(index, self.page_extension)}"
            for index in range(1, count + 1)
        ] if base else []
        meta = {
            "base": base,
            "pages": count,
            "extension": self.page_extension,
            "urls": urls,
        }
        self._meta[manga.id] = meta
        return meta

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        details = self.get_details(manga)
        meta = self._load_page_meta(details)
        urls = list(meta.get("urls") or [])
        page_count = int(meta.get("pages") or details.page_count or len(urls) or 0)
        return [
            Chapter(
                id=details.id,
                chapter=None,
                volume=None,
                title="Gallery",
                language=details.languages[0] if details.languages else "en",
                pages=page_count,
                page_urls=urls,
                gallery=True,
            )
        ]

    def download_page(self, url: str) -> bytes:
        try:
            return super().download_page(url)
        except Exception:
            for ext in ("webp", "jpg", "jpeg", "png"):
                alt = re.sub(r"\.(?:jpg|jpeg|png|webp)$", f".{ext}", url, flags=re.IGNORECASE)
                if alt == url:
                    continue
                try:
                    return super().download_page(alt)
                except Exception:
                    pass
            raise


def _tag_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug
