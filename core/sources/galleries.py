"""HTML gallery sources: HentaiFox, IMHentai, AsmHentai and 3Hentai.

One gallery equals one CBZ. The reader does not request every page: the CDN
base and page count are read from the gallery card, then page URLs are built as
``{base}/{index}.{extension}``.
"""

from __future__ import annotations

import html as html_module
import re
from typing import Any

from ..http import HttpError, RateLimiter, request
from ..models import (
    TITLE_LANGUAGE_NAMES,
    Chapter,
    Manga,
    SearchQuery,
    Tag,
    language_from_name,
)
from ..tags import slugify
from .base import SearchPage, Source

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_TITLE_BRACKETS = re.compile(r"\[([^\]]+)\]")
_PAGES = re.compile(r"Pages:\s*(?:<[^>]+>\s*)*(\d+)", re.IGNORECASE)
_RESULT_COUNT = re.compile(r"([\d][\d\s,\.]*)\s+results?", re.IGNORECASE)
_LANGUAGE_HREF = re.compile(
    r'href=["\'](?:https?://[^"\']+)?/language/([^/"\']+)', re.IGNORECASE
)
_COVER = re.compile(
    r'(?:data-src|src)=["\']((?:https?:)?//[^"\']+/cover\.(?:jpg|jpeg|webp|png))',
    re.IGNORECASE,
)


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _first(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else default


_COUNT_SPAN = re.compile(
    r'<span[^>]*class=["\'][^"\']*gallery_count[^"\']*["\'][^>]*>[\s\S]*?</span>',
    re.IGNORECASE,
)
_BADGE_SPAN = re.compile(
    r'<span[^>]*class=["\'][^"\']*t_badge[^"\']*["\'][^>]*>[\s\S]*?</span>',
    re.IGNORECASE,
)
_SMALL_BADGE = re.compile(
    r'<span[^>]*class=["\'][^"\']*badge[^"\']*["\'][^>]*>([\s\S]*?)</span>',
    re.IGNORECASE,
)
_ONLY_COUNT = re.compile(r"^[\s(\[]*[\d\s,.]+[\s)\]]*$")


def _link_text(fragment: str) -> str:
    """Link text without counters such as ``<span class="badge">123</span>``.

    On AsmHentai the tag name itself lives inside ``span.badge``, so keep such
    wrappers and only strip purely numeric counters.
    """
    fragment = _COUNT_SPAN.sub(" ", fragment)
    fragment = _BADGE_SPAN.sub(" ", fragment)

    def small_badge(match: re.Match[str]) -> str:
        inner = match.group(1)
        if _ONLY_COUNT.match(_clean(inner)):
            return " "
        return f" {inner} "

    fragment = _SMALL_BADGE.sub(small_badge, fragment)
    return _clean(fragment)


def _absolute(url: str) -> str:
    url = html_module.unescape(url.strip())
    if url.startswith("//"):
        return "https:" + url
    return url


def _cover_from_thumb(url: str) -> str:
    url = _absolute(url)
    return re.sub(
        r"/thumb\.(?:jpg|jpeg|webp|png)$", "/cover.jpg", url, flags=re.IGNORECASE
    )


def _languages_from_title(title: str) -> list[str]:
    languages: list[str] = []
    for chunk in _TITLE_BRACKETS.findall(title):
        for word in re.split(r"[,/|]+", chunk):
            code = TITLE_LANGUAGE_NAMES.get(word.strip().lower())
            if code and code not in languages:
                languages.append(code)
    return languages


def _languages_from_html(text: str) -> list[str]:
    languages: list[str] = []
    for raw in _LANGUAGE_HREF.findall(text):
        code = language_from_name(raw.replace("-", " "))
        if code and code not in languages:
            languages.append(code)
    return languages


def _tag(name: str, group: str) -> Tag | None:
    name = name.strip()
    if not name or name.lower() in ("translated", "speechless"):
        return None
    slug = slugify(name)
    if not slug or slug == "tag":
        return None
    return Tag(id=slug, name=name, group=group)


class GallerySource(Source):
    """Shared logic for gallery sites."""

    kind = "gallery"
    supports_data_saver = False
    default_language = "en"
    page_extension = "jpg"
    search_limiter = RateLimiter(0.45)
    image_limiter = RateLimiter(0.06)
    api_limiter = search_limiter

    def __init__(self) -> None:
        self._details: dict[str, Manga] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ search

    def search(
        self,
        query: SearchQuery,
        *,
        page: int = 1,
        offset: int = 0,
        limit: int = 24,
    ) -> SearchPage:
        text = self._query_text(query)
        url, params = self._search_request(text, max(1, page))
        page_html = self._get_html(url, params)
        raw_items = self._parse_cards(page_html)
        items = self.filter_results(query, raw_items)

        total = self._parse_total(page_html)
        if total is not None:
            page_size = max(len(raw_items), 20)
            has_more = (page - 1) * page_size + len(raw_items) < total
        else:
            has_more = len(raw_items) >= 5
        return SearchPage(items=items, total=total, has_more=has_more)

    def _query_text(self, query: SearchQuery) -> str:
        parts: list[str] = []
        if query.title.strip():
            parts.append(query.title.strip())
        if query.author.strip():
            parts.append(query.author.strip())
        if query.tag_mode.upper() == "AND":
            parts.extend(tag.name.strip() for tag in query.tags if tag.name.strip())
        else:
            parts.extend(tag.name.strip() for tag in query.tags if tag.name.strip())
        # Excluded tags are injected as "-word" terms. The text search
        # engines on these sites AND every word, so a multi-word name
        # such as "Boys Love" makes the whole query match nothing.
        # Only send single-word exclusions, and only when the query
        # already has positive words to narrow down.
        if parts:
            for tag in query.excluded_tags:
                name = tag.name.strip()
                slug = (tag.id or "").strip()
                term = ""
                if name and not re.search(r"\s", name):
                    term = name
                elif slug and not re.search(r"\s", slug) and slug.replace("-", "").isalnum():
                    term = slug
                if term:
                    parts.append(f"-{term}")
        return " ".join(parts)

    def _get_html(self, url: str, params: dict[str, Any] | None = None) -> str:
        return request(
            "GET",
            url,
            params=params,
            limiter=self.search_limiter,
            headers=BROWSER_HEADERS,
        ).text

    def _parse_total(self, text: str) -> int | None:
        match = _RESULT_COUNT.search(text)
        if not match:
            return None
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else None

    # ----------------------------------------------------------------- details

    def get_details(self, manga: Manga) -> Manga:
        cached = self._details.get(manga.id)
        if cached is not None:
            return cached
        page_html = self._get_html(self._gallery_url(manga.id))
        parsed = self._parse_gallery(page_html, manga)
        self._details[parsed.id] = parsed
        return parsed

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        details = self.get_details(manga)
        meta = self._meta.get(details.id) or {}
        base = meta.get("base") or ""
        page_count = int(meta.get("pages") or details.page_count or 0)
        extension = meta.get("extension") or self.page_extension
        urls = [f"{base}/{index}.{extension}" for index in range(1, page_count + 1)]
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

    def get_statistics(self, manga: Manga) -> Any:
        return None

    def download_page(self, url: str) -> bytes:
        try:
            return super().download_page(url)
        except HttpError:
            alternative = _swap_extension(url)
            if alternative == url:
                raise
            return super().download_page(alternative)

    # ------------------------------------------------------------- per-source

    def _search_request(self, text: str, page: int) -> tuple[str, dict[str, Any] | None]:
        raise NotImplementedError

    def _gallery_url(self, manga_id: str) -> str:
        raise NotImplementedError

    def _parse_cards(self, text: str) -> list[Manga]:
        raise NotImplementedError

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers

    def _result(
        self,
        *,
        native_id: str,
        title: str,
        url: str,
        thumb: str,
        category: str = "",
        languages: list[str] | None = None,
        page_count: int | None = None,
    ) -> Manga:
        title = _clean(title)
        return Manga(
            id=native_id,
            title=title or f"Gallery {native_id}",
            content_rating="pornographic",
            languages=languages or _languages_from_title(title),
            source=self.key,
            source_name=self.name,
            url_text=url,
            cover_url_text=_cover_from_thumb(thumb) if thumb else "",
            page_count=page_count,
            links={"category": category} if category else {},
        )

    def _finish_gallery(
        self,
        *,
        manga: Manga,
        text: str,
        title: str,
        tags: list[Tag],
        description: str = "",
        authors: list[str] | None = None,
    ) -> Manga:
        cover = _absolute(_first(_COVER.pattern, text))
        pages = int(_first(_PAGES.pattern, text, "0") or 0)
        languages = _languages_from_html(text) or _languages_from_title(title)

        base = ""
        if cover:
            base = re.sub(
                r"/cover\.(?:jpg|jpeg|webp|png)$", "", cover, flags=re.IGNORECASE
            )
        if base and pages:
            self._meta[manga.id] = {
                "base": base,
                "pages": pages,
                "extension": self.page_extension,
            }

        return Manga(
            id=manga.id,
            title=title or manga.title,
            alt_titles=manga.alt_titles,
            description=description,
            content_rating="pornographic",
            tags=tags,
            authors=authors or [],
            artists=authors or [],
            languages=languages,
            source=self.key,
            source_name=self.name,
            url_text=self._gallery_url(manga.id),
            cover_url_text=cover or manga.cover_url_text,
            page_count=pages or manga.page_count,
            links=manga.links,
        )


def _swap_extension(url: str) -> str:
    if url.lower().endswith(".webp"):
        return url[:-5] + ".jpg"
    if url.lower().endswith(".jpg"):
        return url[:-4] + ".webp"
    return url


class HentaiFoxSource(GallerySource):
    key = "hentaifox"
    name = "HentaiFox"
    site_url = "https://hentaifox.com"
    page_extension = "webp"

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
            params["q"] = text

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
        return f"{self.site_url}/search/", {"q": text, "page": page}

    def _gallery_url(self, manga_id: str) -> str:
        return f"{self.site_url}/gallery/{manga_id}/"

    def _parse_cards(self, text: str) -> list[Manga]:
        items: list[Manga] = []
        for chunk in text.split('<div class="thumb"')[1:]:
            native_id = _first(r'href="/gallery/(\d+)/"', chunk)
            if not native_id:
                continue
            title = _first(r'<h2 class="g_title">[\s\S]*?<a[^>]*>([\s\S]*?)</a>', chunk)
            thumb = _first(r'data-src="([^"]+)"', chunk)
            category = _first(r'<a class="t_cat"[^>]*>([\s\S]*?)</a>', chunk)
            languages = _languages_from_html(chunk)
            items.append(
                self._result(
                    native_id=native_id,
                    title=title,
                    url=self._gallery_url(native_id),
                    thumb=thumb,
                    category=_clean(category),
                    languages=languages,
                )
            )
        return items

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        title = _clean(_first(r"<h1[^>]*>([\s\S]*?)</h1>", text)) or manga.title

        tags: list[Tag] = []
        tags_block = _first(r'<ul class="tags">([\s\S]*?)</ul>', text)
        for href, name in re.findall(
            r"<a[^>]*href=['\"]/tag/([^'\"]+)['\"][^>]*>([\s\S]*?)</a>",
            tags_block,
            re.IGNORECASE,
        ):
            tag = _tag(_link_text(name), "theme")
            if tag is not None:
                tags.append(tag)

        category_block = _first(r'<ul class="categories">([\s\S]*?)</ul>', text)
        for href, name in re.findall(
            r"<a[^>]*href=['\"]/category/([^'\"]+)['\"][^>]*>([\s\S]*?)</a>",
            category_block,
            re.IGNORECASE,
        ):
            tag = _tag(_link_text(name), "format")
            if tag is not None:
                tags.append(tag)

        description = _credit_lines(
            text,
            [
                ("artists", "Artist"),
                ("groups", "Group"),
                ("parodies", "Parody"),
            ],
        )
        return self._finish_gallery(
            manga=manga,
            text=text,
            title=title,
            tags=tags,
            description=description,
            authors=_credit_names(text, "artists"),
        )

class AsmHentaiSource(GallerySource):
    key = "asmhentai"
    name = "AsmHentai"
    site_url = "https://asmhentai.com"
    page_extension = "jpg"
    # The site searches a single tag (or a title), never several tags.
    supports_multi_tag = False

    def _query_text(self, query: SearchQuery) -> str:
        # AsmHentai searches either by title or by a single tag.
        if query.title.strip():
            return query.title.strip()
        if query.author.strip():
            return query.author.strip()
        return query.tags[0].name.strip() if query.tags else ""

    def search(self, query: SearchQuery, *, page: int = 1, offset: int = 0, limit: int = 24):
        page = max(1, page)
        params: dict[str, Any] = {}
        if page > 1:
            params["page"] = page

        tags = [tag for tag in query.tags if (tag.id or tag.name).strip()]
        title = query.title.strip()
        author = query.author.strip()
        # Prefer tag page for tag-only searches (more accurate than text search).
        if tags and not title and not author:
            primary = tags[0]
            tag_slug = (primary.id or "").strip() or _clean(primary.name).lower().replace(" ", "-")
            base_url = f"{self.site_url}/tag/{tag_slug}/"
            text = ""
        else:
            text = self._query_text(query)
            base_url = f"{self.site_url}/search/"

        # Single-word exclusions as negative text terms when we have positive text.
        if text:
            for tag in query.excluded_tags:
                name = (tag.name or "").strip()
                slug = (tag.id or "").strip()
                term = ""
                if name and not re.search(r"\s", name):
                    term = name
                elif slug and not re.search(r"\s", slug) and slug.replace("-", "").isalnum():
                    term = slug
                if term:
                    text += f" -{term}"

        if text:
            params["q"] = text

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
        return f"{self.site_url}/search/", {"q": text, "page": page}

    def _gallery_url(self, manga_id: str) -> str:
        return f"{self.site_url}/g/{manga_id}/"

    def _parse_cards(self, text: str) -> list[Manga]:
        items: list[Manga] = []
        for chunk in text.split('<div class="preview_item"')[1:]:
            native_id = _first(r'href="/g/(\d+)/"', chunk)
            if not native_id:
                continue
            title = _first(r'<h2 class="caption">([\s\S]*?)</h2>', chunk)
            thumb = _first(r'data-src="([^"]+)"', chunk)
            category = _first(r'href="/category/[^"]*">([\s\S]*?)</a>', chunk)
            items.append(
                self._result(
                    native_id=native_id,
                    title=title,
                    url=self._gallery_url(native_id),
                    thumb=thumb,
                    category=_clean(category),
                    languages=_languages_from_html(chunk),
                )
            )
        return items

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        title = _clean(_first(r"<h1[^>]*>([\s\S]*?)</h1>", text)) or manga.title
        tags: list[Tag] = []
        credits: dict[str, list[str]] = {}

        for block in re.findall(
            r'<div class="tags">([\s\S]*?)</div>', text, re.IGNORECASE
        ):
            label = _clean(_first(r"<h3[^>]*>([\s\S]*?)</h3>", block)).rstrip(":")
            group = "theme" if label.lower() == "tags" else "format"
            for href, name in re.findall(
                r"<a[^>]*href=['\"]/([a-z]+)/[^'\"]+['\"][^>]*>([\s\S]*?)</a>",
                block,
                re.IGNORECASE,
            ):
                kind = href.lower()
                clean_name = _link_text(name)
                if kind in ("tag", "category"):
                    tag = _tag(clean_name, group)
                    if tag is not None:
                        tags.append(tag)
                elif kind in ("artist", "group", "parody", "character"):
                    credits.setdefault(kind, []).append(clean_name)

        description = _credit_description(credits)
        return self._finish_gallery(
            manga=manga,
            text=text,
            title=title,
            tags=tags,
            description=description,
            authors=credits.get("artist") or [],
        )


class ThreeHentaiSource(GallerySource):
    key = "3hentai"
    name = "3Hentai"
    site_url = "https://3hentai.net"
    page_extension = "jpg"

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
            # 3hentai tag listings use /tags/<slug> (plural)
            base_url = f"{self.site_url}/tags/{tag_slug}"
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

        if not use_tag_page and not text:
            base_url = f"{self.site_url}/" if page == 1 else f"{self.site_url}/{page}"
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
            url = f"{self.site_url}/" if page == 1 else f"{self.site_url}/{page}"
            return url, None
        return f"{self.site_url}/search", {"q": text, "page": page}

    def _gallery_url(self, manga_id: str) -> str:
        return f"{self.site_url}/d/{manga_id}"

    def _parse_cards(self, text: str) -> list[Manga]:
        items: list[Manga] = []
        for chunk in text.split('<div class="doujin ')[1:]:
            native_id = _first(r'href="https://3hentai\.net/d/(\d+)"', chunk)
            if not native_id:
                continue
            title = _first(r'<div class="title">([\s\S]*?)</div>', chunk)
            thumb = _first(r'data-src="([^"]+)"', chunk)
            pages = _first(r'data-nb-imgs="(\d+)"', chunk)
            items.append(
                self._result(
                    native_id=native_id,
                    title=title,
                    url=self._gallery_url(native_id),
                    thumb=thumb,
                    page_count=int(pages) if pages else None,
                )
            )
        return items

    def _parse_gallery(self, text: str, manga: Manga) -> Manga:
        title = _clean(_first(r"<h1[^>]*>([\s\S]*?)</h1>", text)) or manga.title
        tags: list[Tag] = []
        credits: dict[str, list[str]] = {}

        for href, name in re.findall(
            r'<span class="filter-elem tag-tags">[\s\S]*?<a class="name" href="([^"]+)"[^>]*>([\s\S]*?)</a>',
            text,
            re.IGNORECASE,
        ):
            clean_name = _tag_name_3h(_link_text(name))
            if "/language/" in href:
                continue
            if "/tags/" in href or "/tag/" in href:
                tag = _tag(clean_name, "theme")
                if tag is not None:
                    tags.append(tag)
            elif "/category/" in href:
                tag = _tag(clean_name, "format")
                if tag is not None:
                    tags.append(tag)
            elif "/search" in href:
                credits.setdefault("artist", []).append(clean_name)

        for href, name in re.findall(
            r'<span class="filter-elem tag-category">[\s\S]*?<a class="name" href="([^"]+)"[^>]*>([\s\S]*?)</a>',
            text,
            re.IGNORECASE,
        ):
            tag = _tag(_link_text(name), "format")
            if tag is not None:
                tags.append(tag)

        description = _credit_description(credits)
        return self._finish_gallery(
            manga=manga,
            text=text,
            title=title,
            tags=tags,
            description=description,
            authors=credits.get("artist") or [],
        )


def _tag_name_3h(name: str) -> str:
    return re.sub(r"\s*\((?:female|male)\)\s*$", "", name, flags=re.IGNORECASE).strip()


def _credit_names(text: str, css_class: str) -> list[str]:
    block = _first(rf'<ul class="{css_class}">([\s\S]*?)</ul>', text)
    names = [
        _link_text(name)
        for name in re.findall(r"<a[^>]*>([\s\S]*?)</a>", block, re.IGNORECASE)
    ]
    return [name for name in names if name]


def _credit_lines(text: str, kinds: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for css_class, label in kinds:
        names = _credit_names(text, css_class)
        if names:
            lines.append(f"**{label}:** {', '.join(names)}")
    return "\n\n".join(lines)


def _credit_description(credits: dict[str, list[str]]) -> str:
    labels = {
        "artist": "Artist",
        "group": "Group",
        "parody": "Parody",
        "character": "Characters",
    }
    lines: list[str] = []
    for kind, label in labels.items():
        names = credits.get(kind) or []
        if names:
            lines.append(f"**{label}:** {', '.join(names)}")
    return "\n\n".join(lines)
