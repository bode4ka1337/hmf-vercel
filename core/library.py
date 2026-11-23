"""Source library: shared search, tags, cache and request delegation.

The interface never talks to a concrete site. It receives a :class:`Library`
and works with :class:`Manga` objects that carry a ``source`` field.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .http import HttpError
from .i18n import tr
from .models import Chapter, Manga, SearchQuery, Statistics, Tag
from .sources import build_sources
from .sources.base import SearchPage, Source
from .tags import (
    card_satisfies_tags,
    catalog_from_data,
    catalog_to_data,
    extra_tags,
    merge_tags,
    resolve_tag_alias,
    slugify,
)

TAG_CACHE_TTL = 14 * 24 * 3600
TAG_CACHE_VERSION = 3
TAG_SOURCE_KEYS = ("nhentai",)

_TITLE_CLEAN = re.compile(r"[^0-9a-zа-яёіїєґ]+")


def _title_key(title: str) -> str:
    return _TITLE_CLEAN.sub("", title.lower())[:90]


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:240]


@dataclass(slots=True)
class SourceState:
    """Pagination state of one source inside a search session."""

    key: str
    name: str
    page: int = 0
    total: int | None = None
    has_more: bool = True
    error: str = ""
    note: str = ""


@dataclass(slots=True)
class MultiSearchResult:
    """A batch of results from several sources at once."""

    items: list[Manga] = field(default_factory=list)
    states: dict[str, SourceState] = field(default_factory=dict)
    has_more: bool = False

    @property
    def total(self) -> int | None:
        known = [state.total for state in self.states.values() if state.total is not None]
        return sum(known) if known else None

    @property
    def errors(self) -> dict[str, str]:
        return {
            state.name: state.error
            for state in self.states.values()
            if state.error
        }

    @property
    def notes(self) -> dict[str, str]:
        """Sources that were intentionally not searched, with the reason."""
        return {
            state.name: state.note
            for state in self.states.values()
            if state.note
        }


class SearchSession:
    """Paginated output with round-robin sources and de-duplication.

    Every site has its own page counter. :meth:`fetch` only requests pages from
    sources whose queue is empty and returns records in a round-robin order, so
    one site cannot dominate the results.

    In AND mode the query is verified again on the client: cards that expose
    tags (MangaDex, nhentai) must contain every selected tag. A site with a tag
    catalogue that does not know one of the tags is not queried at all, because
    the site would otherwise silently drop the unknown tag and return titles
    that do not contain it.
    """

    def __init__(self, library: "Library", query: SearchQuery) -> None:
        self.library = library
        self.query = query
        keys = [key for key in query.sources if key in library.sources]
        if not keys:
            keys = list(library.sources)
        if not keys:
            raise ValueError("No source selected")
        self.states: dict[str, SourceState] = {
            key: SourceState(key=key, name=library.sources[key].name) for key in keys
        }
        self._queues: dict[str, deque[Manga]] = {key: deque() for key in keys}
        self._seen_keys: set[str] = set()
        self._seen_titles: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False

        for key, state in self.states.items():
            reason = self._block_reason(key)
            if reason:
                state.has_more = False
                state.note = reason

    def _block_reason(self, key: str) -> str:
        """Why a site cannot honour the selected AND tags, if it cannot.

        Gallery sites only accept free-text queries, so they are trusted. A
        site with a tag catalogue (MangaDex, nhentai) is skipped when it does
        not know one of the tags: otherwise the server silently drops the
        unknown tag and returns titles that do not actually contain it.
        """
        tags = self.query.tags
        if self.query.tag_mode.upper() != "AND" or not tags:
            return ""
        source = self.library.sources[key]
        if not source.supports_multi_tag and len(tags) > 1:
            return tr("cannot combine several tags")
        native_key = source.tag_source_key
        if native_key and any(tag.source_ids for tag in tags):
            missing = [
                tag.name for tag in tags if not tag.source_ids.get(native_key)
            ]
            if missing:
                return tr("has no tags: {tags}", tags=", ".join(missing))
        return ""

    # ------------------------------------------------------------------ public

    def fetch(self, wanted: int = 24) -> MultiSearchResult:
        wanted = max(1, wanted)
        self._fill(max(wanted, self.query.limit))
        items = self._take(wanted)
        has_more = any(queue for queue in self._queues.values()) or any(
            state.has_more for state in self.states.values()
        )
        with self._lock:
            states = {
                key: SourceState(
                    key=state.key,
                    name=state.name,
                    page=state.page,
                    total=state.total,
                    has_more=state.has_more,
                    error=state.error,
                    note=state.note,
                )
                for key, state in self.states.items()
            }
        return MultiSearchResult(items=items, states=states, has_more=has_more)

    @property
    def exhausted(self) -> bool:
        return not any(self._queues.values()) and not any(
            state.has_more for state in self.states.values()
        )

    # ------------------------------------------------------------------- fill

    def _fill(self, wanted: int) -> None:
        # At most one new request per source per call: an empty page or a
        # failing site must not turn into an infinite loop.
        for _ in range(len(self.states)):
            queued = sum(len(queue) for queue in self._queues.values())
            if queued >= wanted:
                break
            need = [
                key
                for key, queue in self._queues.items()
                if not queue and self.states[key].has_more
            ]
            if not need:
                break
            self._fetch_pages(need)

    def _fetch_pages(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        with ThreadPoolExecutor(max_workers=min(6, len(keys))) as pool:
            futures = {pool.submit(self._fetch_source, key): key for key in keys}
            for future in as_completed(futures):
                key = futures[future]
                state = self.states[key]
                try:
                    page = future.result()
                except Exception as exc:  # noqa: BLE001 - one source may fail
                    state.error = _short_error(exc)
                    state.has_more = False
                    continue
                state.error = ""
                state.page += 1
                state.total = page.total
                raw_items = page.items
                items = raw_items
                if self.query.tag_mode.upper() == "AND" and self.query.tags:
                    items = [
                        manga
                        for manga in raw_items
                        if card_satisfies_tags(manga, self.query.tags) is not False
                    ]
                if not raw_items and not page.has_more:
                    state.has_more = False
                else:
                    state.has_more = bool(page.has_more)
                self._queues[key].extend(items)

    def _fetch_source(self, key: str) -> SearchPage:
        source = self.library.sources[key]
        state = self.states[key]
        page_number = state.page + 1
        offset = (page_number - 1) * max(1, self.query.limit)
        return source.search(
            self.query,
            page=page_number,
            offset=offset,
            limit=max(1, self.query.limit),
        )

    # ------------------------------------------------------------------- take

    def _take(self, wanted: int) -> list[Manga]:
        items: list[Manga] = []
        while len(items) < wanted:
            progressed = False
            for key, queue in self._queues.items():
                while queue:
                    manga = queue.popleft()
                    if self._accept(manga):
                        items.append(manga)
                        progressed = True
                        break
                if len(items) >= wanted:
                    break
            if not progressed:
                break
        return items

    def _accept(self, manga: Manga) -> bool:
        key = manga.key
        if not manga.id or key in self._seen_keys:
            return False
        title_key = _title_key(manga.title)
        if title_key and title_key in self._seen_titles:
            return False
        self._seen_keys.add(key)
        if title_key:
            self._seen_titles.add(title_key)
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for queue in self._queues.values():
                queue.clear()


class Library:
    """Single entry point for sources, tags and the tag cache."""

    def __init__(
        self,
        sources: dict[str, Source] | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.sources = sources or build_sources()
        if cache_dir is None:
            cache_dir = Path.home() / ".mangafinder"
        self.cache_dir = Path(cache_dir)
        self._tags: list[Tag] | None = None
        self._tag_lock = threading.Lock()

    # ----------------------------------------------------------------- sources

    def keys(self) -> list[str]:
        return list(self.sources)

    def source_for(self, manga: Manga | str) -> Source:
        key = manga if isinstance(manga, str) else manga.source
        source = self.sources.get(key)
        if source is None:
            raise KeyError(f"Unknown source: {key}")
        return source

    def check_sites(self) -> dict[str, tuple[bool, str]]:
        """Probe every source and report whether it is reachable."""
        keys = list(self.sources)
        results: dict[str, tuple[bool, str]] = {}
        if not keys:
            return results
        with ThreadPoolExecutor(max_workers=min(12, len(keys))) as pool:
            futures = {
                pool.submit(self.sources[key].check_available): key for key in keys
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as exc:  # noqa: BLE001
                    results[key] = (False, _short_error(exc))
        return results

    # -------------------------------------------------------------------- tags

    def get_tags(self, *, force: bool = False, allow_network: bool = True) -> list[Tag]:
        with self._tag_lock:
            if self._tags is not None and not force:
                return list(self._tags)

            cached, saved_at = self._read_tag_cache()
            fresh = saved_at is not None and time.time() - saved_at < TAG_CACHE_TTL
            if cached and not force and fresh:
                self._tags = cached
                return list(cached)
            if not allow_network:
                if cached:
                    self._tags = cached
                    return list(cached)
                self._tags = merge_tags(extra_tags())
                return list(self._tags)

            try:
                tags = self._collect_tags()
            except Exception:  # noqa: BLE001 - fall back to cache or built-ins
                if cached:
                    self._tags = cached
                    return list(cached)
                raise

            if not tags:
                if cached:
                    tags = cached
                else:
                    tags = merge_tags(extra_tags())
            self._tags = tags
            self._write_tag_cache(tags)
            return list(tags)

    def _collect_tags(self) -> list[Tag]:
        catalogs: list[Iterable[Tag]] = [extra_tags()]
        with ThreadPoolExecutor(max_workers=len(TAG_SOURCE_KEYS)) as pool:
            futures = {
                pool.submit(self.sources[key].get_tags): key
                for key in TAG_SOURCE_KEYS
                if key in self.sources
            }
            for future in as_completed(futures):
                try:
                    batch = future.result()
                except Exception:  # noqa: BLE001 - one site may be down
                    continue
                if batch:
                    catalogs.append(batch)
        return merge_tags(*catalogs)

    def _tag_cache_path(self) -> Path:
        return self.cache_dir / "tags.json"

    def _read_tag_cache(self) -> tuple[list[Tag], float | None]:
        path = self._tag_cache_path()
        if not path.exists():
            return [], None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], None
        if int(payload.get("version") or 0) != TAG_CACHE_VERSION:
            return [], None
        tags = catalog_from_data(payload.get("tags") or [])
        saved_at = payload.get("saved_at")
        return tags, float(saved_at) if saved_at else None

    def _write_tag_cache(self, tags: Iterable[Tag]) -> None:
        path = self._tag_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": TAG_CACHE_VERSION,
                "saved_at": time.time(),
                "tags": catalog_to_data(tags),
            }
            temp = path.with_suffix(".part")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            temp.replace(path)
        except OSError:
            pass

    def tag_catalog(self) -> list[Tag]:
        return self.get_tags(allow_network=False)

    def resolve_tags(self, slugs: Iterable[str]) -> list[Tag]:
        """Build search tags from slugs without triggering a tag refresh.

        Native ``source_ids`` come from the in-memory or cached catalogue so
        sources that understand them can filter precisely and skip tags they
        do not know instead of silently returning unrelated titles.
        """
        wanted = [str(slug) for slug in slugs if str(slug)]
        if not wanted:
            return []
        if self._tags is not None:
            catalog = self._tags
        else:
            cached, _ = self._read_tag_cache()
            catalog = cached or merge_tags(extra_tags())
        by_id: dict[str, Tag] = {}
        for tag in catalog:
            by_id.setdefault(tag.id, tag)
        resolved: list[Tag] = []
        seen: set[str] = set()
        for raw in wanted:
            canonical = resolve_tag_alias(raw)
            if canonical in seen:
                continue
            seen.add(canonical)
            tag = by_id.get(canonical) or by_id.get(slugify(raw))
            if tag is None:
                resolved.append(
                    Tag(
                        id=canonical,
                        name=canonical.replace("-", " ").title(),
                        group="theme",
                    )
                )
                continue
            # Canonical display names can differ from what a site accepts as
            # free text (e.g. "Big Tits" vs nhentai's "big breasts"), so fall
            # back to the slug when the catalogue name does not round-trip.
            name = tag.name
            if not name or slugify(name) != tag.id:
                name = tag.id.replace("-", " ").title()
            resolved.append(
                Tag(
                    id=tag.id,
                    name=name,
                    group=tag.group,
                    description=tag.description,
                    source_ids=dict(tag.source_ids),
                )
            )
        return resolved

    # ------------------------------------------------------------------ search

    def start_search(self, query: SearchQuery) -> SearchSession:
        return SearchSession(self, query)

    def search(self, query: SearchQuery, *, limit: int = 24) -> MultiSearchResult:
        return self.start_search(query).fetch(limit)

    # -------------------------------------------------------------- delegation

    def get_details(self, manga: Manga) -> Manga:
        return self.source_for(manga).get_details(manga)

    def get_chapters(
        self, manga: Manga, languages: Iterable[str] | None = None
    ) -> list[Chapter]:
        source = self.source_for(manga)
        if source.key == "mangadex":
            return source.get_chapters(manga, languages=languages)
        return source.get_chapters(manga)

    def get_statistics(self, manga: Manga) -> Statistics | None:
        return self.source_for(manga).get_statistics(manga)

    def get_page_urls(
        self, manga: Manga, chapter: Chapter, *, data_saver: bool = False
    ) -> list[str]:
        return self.source_for(manga).get_page_urls(chapter, data_saver=data_saver)

    def download_page(self, manga: Manga, url: str) -> bytes:
        return self.source_for(manga).download_page(url)

    def download_cover(self, manga: Manga, size: int = 256) -> bytes:
        return self.source_for(manga).download_cover(manga, size)

    def supports_data_saver(self, manga: Manga) -> bool:
        return bool(self.source_for(manga).supports_data_saver)

    def source_name(self, manga: Manga) -> str:
        try:
            return self.source_for(manga).name
        except KeyError:
            return manga.source_name or manga.source


__all__ = [
    "Library",
    "MultiSearchResult",
    "SearchSession",
    "SourceState",
]
