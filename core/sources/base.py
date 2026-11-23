"""Base interfaces for content sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..http import HttpError, RateLimiter, get_bytes, request
from ..models import Chapter, Manga, SearchQuery, Statistics, Tag
from ..tags import is_gay_tag


@dataclass(slots=True)
class SearchPage:
    """One page of a source's search result."""

    items: list[Manga] = field(default_factory=list)
    total: int | None = None
    has_more: bool = False


def languages_match(
    query: SearchQuery, manga: Manga, *, fallback: str | None = None
) -> bool:
    """Strict language matching for sources without a server-side filter.

    When a language is selected, a title that has no detected language is
    treated as the source's typical language (``fallback``), never as a match
    for every language. An empty selection means "any language".
    """
    selected = set(query.languages)
    if not selected:
        return True
    languages = set(manga.languages)
    if not languages and fallback:
        languages = {fallback}
    return bool(selected & languages)


def tag_excluded(query: SearchQuery, *names: str) -> bool:
    """Return True when any of the supplied tags is explicitly excluded."""
    excluded = {tag.id for tag in query.excluded_tags}
    if not excluded:
        return False
    from ..tags import slugify

    for name in names:
        if slugify(name) in excluded:
            return True
    return False


def gay_blocked(query: SearchQuery, *names: str) -> bool:
    """Return True when the gay/yaoi block applies to a tag name."""
    exclusions = {tag.id for tag in query.excluded_tags}
    if "yaoi" not in exclusions and "boys-love" not in exclusions:
        return False
    return is_gay_tag(*names)


def _gay_block_active(query: SearchQuery) -> bool:
    exclusions = {tag.id for tag in query.excluded_tags}
    return "yaoi" in exclusions or "boys-love" in exclusions


def _text_has_gay_term(text: str) -> bool:
    haystack = (text or "").lower()
    for term in ("yaoi", "bara", "boys love", "boy's love", "shounen-ai", "shonen-ai", "gay"):
        if term in haystack:
            return True
    return False


def filter_excluded(query: SearchQuery, manga: Manga) -> bool:
    """Post-filter a result by its tags and the global gay block."""
    excluded = {item.id for item in query.excluded_tags}
    for tag in manga.tags:
        if tag.id in excluded:
            return False
        if is_gay_tag(tag.id) and _gay_block_active(query):
            return False
    # Heuristic: when gay block is on and cards have no tags, reject obvious titles.
    if not manga.tags and _gay_block_active(query):
        text = " ".join([manga.title] + manga.authors + manga.artists)
        if _text_has_gay_term(text):
            return False
    return True


class Source:
    """Common site interface.

    Sources are either ``manga`` (chapters, like MangaDex) or ``gallery``
    (one gallery equals one synthetic chapter). Adding a site means
    implementing :meth:`search` and the detail parser.
    """

    key: str = "base"
    name: str = "Source"
    site_url: str = ""
    kind: str = "manga"
    supports_data_saver: bool = False
    # Catalogue key whose native ids appear on this site's search cards.
    # ``None`` means the site does not publish tags on cards.
    tag_source_key: str | None = None
    # Whether a single query can require more than one tag at once.
    supports_multi_tag: bool = True
    api_limiter = RateLimiter(0.25)
    image_limiter = RateLimiter(0.05)
    default_language: str | None = None

    # ------------------------------------------------------------------ search

    def get_tags(self) -> list[Tag]:
        return []

    def search(
        self,
        query: SearchQuery,
        *,
        page: int = 1,
        offset: int = 0,
        limit: int = 24,
    ) -> SearchPage:
        raise NotImplementedError

    def get_details(self, manga: Manga) -> Manga:
        return manga

    def get_chapters(self, manga: Manga) -> list[Chapter]:
        return []

    def get_statistics(self, manga: Manga) -> Statistics | None:
        return None

    def check_available(self) -> tuple[bool, str]:
        """Probe the site and return ``(available, detail)``."""
        url = self.site_url
        if not url:
            return False, "No site URL"
        try:
            response = request(
                "GET",
                url,
                limiter=self.api_limiter,
                retries=1,
                timeout=15.0,
            )
        except HttpError as exc:
            return False, str(exc)[:160]
        return True, f"HTTP {response.status_code}"

    def filter_results(
        self, query: SearchQuery, items: Iterable[Manga]
    ) -> list[Manga]:
        """Apply strict language and exclusion rules to parsed cards."""
        accepted: list[Manga] = []
        for manga in items:
            if not languages_match(query, manga, fallback=self.default_language):
                continue
            if not filter_excluded(query, manga):
                continue
            accepted.append(manga)
        return accepted

    # ------------------------------------------------------------------ images

    def get_page_urls(self, chapter: Chapter, *, data_saver: bool = False) -> list[str]:
        return list(chapter.page_urls)

    def download_page(self, url: str) -> bytes:
        if not url:
            raise HttpError("Empty page URL")
        return get_bytes(url, limiter=self.image_limiter)

    def download_cover(self, manga: Manga, size: int = 256) -> bytes:
        url = manga.cover_url_for(size)
        if not url:
            raise HttpError("This entry has no cover")
        return get_bytes(url, limiter=self.image_limiter)

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return f"<{type(self).__name__} {self.key}>"
