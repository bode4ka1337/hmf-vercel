"""Vercel serverless backend for the HMF web client and Telegram Mini App.

Serves the static UI from ``public/`` and the JSON backend at ``/api/app``.
Uses the shared `core/` package for search, tags and reading so the web
version offers the same features in a browser.

Tabs: Search, Details, Reader, History, Settings (plus Rule34 in Settings).
Actions: search, random, details, pages, open, history_add, history,
history_remove, history_clear, sources, settings, reset_settings, reset_all,
check_sites, tags, rule34_search, rule34_autocomplete, login, logout, me,
admin_status, admin_accounts, admin_create_account, admin_reset_password,
admin_delete_account. Registration is disabled: the admin issues accounts.
"""

from __future__ import annotations

import json
import os
import random as _random
import threading
from typing import Any

from core.library import Library
from core.models import CONTENT_RATINGS, Manga, SearchQuery, Tag
from core.tags import GAY_TAGS
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from urllib.parse import quote_plus, urlencode, urlparse

from core.http import request as http_request

try:  # Vercel imports this module as ``api.app``.
    from .state import (
        add_history,
        clear_history,
        default_settings,
        enrich_history,
        get_history,
        get_settings,
        history_count,
        normalize_user_id,
        remove_history,
        reset_user,
        update_settings,
    )
except ImportError:  # pragma: no cover - direct execution / flat imports
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from state import (  # type: ignore[no-redef]
        add_history,
        clear_history,
        default_settings,
        enrich_history,
        get_history,
        get_settings,
        history_count,
        normalize_user_id,
        remove_history,
        reset_user,
        update_settings,
    )

try:
    from .auth import (
        ADMIN_REQUIRED,
        AUTH_UNAVAILABLE,
        SESSION_COOKIE,
        SESSION_TTL,
        admin_accounts as auth_admin_accounts,
        admin_create_account as auth_admin_create,
        admin_delete_account as auth_admin_delete,
        admin_reset_password as auth_admin_reset_password,
        admin_status as auth_admin_status,
        login as auth_login,
        logout as auth_logout,
        me as auth_me,
        register as auth_register,
        session_user as auth_session_user,
    )
except ImportError:  # pragma: no cover - direct execution / flat imports
    from auth import (  # type: ignore[no-redef]
        ADMIN_REQUIRED,
        AUTH_UNAVAILABLE,
        SESSION_COOKIE,
        SESSION_TTL,
        admin_accounts as auth_admin_accounts,
        admin_create_account as auth_admin_create,
        admin_delete_account as auth_admin_delete,
        admin_reset_password as auth_admin_reset_password,
        admin_status as auth_admin_status,
        login as auth_login,
        logout as auth_logout,
        me as auth_me,
        register as auth_register,
        session_user as auth_session_user,
    )

AUTH_ACTIONS = frozenset(
    (
        "register", "login", "logout", "me",
        "admin_status", "admin_accounts", "admin_create_account",
        "admin_reset_password", "admin_delete_account",
    )
)

_LIBRARY: Library | None = None
_SESSIONS: dict[str, Any] = {}
_SESSION_LOCK = threading.Lock()
_MAX_PAGE_URLS = 1000


def _library() -> Library:
    global _LIBRARY
    if _LIBRARY is None:
        # Serverless: /tmp is writable, use it as the cache dir.
        _LIBRARY = Library(cache_dir="/tmp/hmf")
    return _LIBRARY


def _pretty(slug: str) -> str:
    """Display-friendly tag name used for text search queries."""
    return slug.replace("-", " ").title()


def _user_id(body: dict[str, Any]) -> str:
    """User key from a request body (Telegram id or arbitrary client id)."""
    return normalize_user_id(body.get("user_id"))


def _error_text(exc: BaseException) -> str:
    return (str(exc).strip() or type(exc).__name__)[:240]


def _build_query(library: Library, body: dict[str, Any]) -> SearchQuery:
    sources = [
        str(key)
        for key in (body.get("sources") or library.keys())
        if str(key) in library.sources
    ] or list(library.sources)
    tags = library.resolve_tags(body.get("tags") or [])
    excluded_ids = {str(t) for t in (body.get("excluded") or []) if str(t)}
    excluded = library.resolve_tags(sorted(excluded_ids))
    if body.get("block_gay"):
        for slug in sorted(GAY_TAGS):
            if slug not in excluded_ids:
                excluded.append(Tag(id=slug, name=_pretty(slug), group="theme"))
    tag_mode = str(body.get("tag_mode") or "AND").upper()
    if tag_mode not in ("AND", "OR"):
        tag_mode = "AND"
    languages = [str(code) for code in (body.get("languages") or []) if str(code)]
    sort = str(body.get("sort") or "relevance")
    return SearchQuery(
        sources=list(sources),
        title=str(body.get("title") or ""),
        author=str(body.get("author") or ""),
        tags=tags,
        excluded_tags=excluded,
        tag_mode=tag_mode,
        ratings=list(body.get("ratings") or CONTENT_RATINGS),
        languages=languages,
        sort=sort,
        limit=int(body.get("limit") or 24),
    )


def _session_key(query: SearchQuery, body: dict[str, Any] | None = None) -> str:
    """Cache key covering every option that can change the result set."""
    body = body or {}
    return json.dumps(
        [
            sorted(str(key) for key in query.sources),
            query.title,
            query.author,
            sorted(tag.id for tag in query.tags),
            sorted(tag.id for tag in query.excluded_tags),
            bool(body.get("block_gay")),
            query.tag_mode,
            sorted(str(code) for code in query.languages),
            sorted(str(rating) for rating in query.ratings),
            query.sort,
            int(body.get("limit") or 24),
            _user_id(body),
        ],
        sort_keys=True,
    )


def _get_session(library: Library, query: SearchQuery, body: dict[str, Any] | None = None):
    key = _session_key(query, body)
    with _SESSION_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = library.start_search(query)
            if len(_SESSIONS) > 40:
                _SESSIONS.clear()
            _SESSIONS[key] = session
        return session


def _manga_to_item(manga: Manga) -> dict[str, Any]:
    return {
        "key": manga.key,
        "id": manga.id,
        "source": manga.source,
        "source_name": manga.source_name,
        "title": manga.title,
        "cover_url": manga.cover_url,
        "url": manga.url,
        "page_count": manga.page_count,
        "tag_names": manga.tag_names,
        "author_label": manga.author_label,
        "authors": list(manga.authors),
        "languages": list(manga.languages),
        "content_rating": manga.content_rating,
        "status": manga.status,
        "year": manga.year,
    }


def _chapter_to_data(chapter) -> dict[str, Any]:
    return {
        "id": chapter.id,
        "label": chapter.label,
        "chapter": chapter.chapter,
        "volume": chapter.volume,
        "title": chapter.title,
        "language": chapter.language,
        "pages": chapter.pages,
        "page_total": chapter.page_total,
        "external_url": chapter.external_url,
        "group": chapter.group,
        "downloadable": chapter.downloadable,
        "readable_at": chapter.readable_at,
        "gallery": bool(chapter.gallery),
    }


def _manga_to_detail(
    library: Library, manga: Manga, *, user_id: str | None = None
) -> dict[str, Any]:
    """Build the details payload; optional chapter/stat failures stay soft."""
    errors: list[str] = []
    details = manga
    try:
        details = library.get_details(manga)
    except Exception as exc:  # noqa: BLE001 - show what we have
        errors.append("Details: " + _error_text(exc))

    chapters: list[Any] = []
    try:
        chapters = library.get_chapters(details)
    except Exception as exc:  # noqa: BLE001 - details still useful
        errors.append("Chapters: " + _error_text(exc))

    statistics = None
    try:
        statistics = library.get_statistics(details)
    except Exception as exc:  # noqa: BLE001 - optional metadata
        errors.append("Statistics: " + _error_text(exc))

    try:
        gallery = library.source_for(details).kind == "gallery"
    except Exception:  # noqa: BLE001
        gallery = any(bool(chapter.gallery) for chapter in chapters)
    try:
        data_saver = bool(library.supports_data_saver(details))
    except Exception:  # noqa: BLE001
        data_saver = False

    result = {
        **_manga_to_item(details),
        "cover_url": details.cover_url_for(512),
        "artists": list(details.artists),
        "description": (details.description or "")[:4000],
        "alt_titles": list(details.alt_titles),
        "original_language": details.original_language,
        "demographic": details.demographic,
        "tags": [
            {
                "id": tag.id,
                "name": tag.name,
                "group": tag.group,
                "description": tag.description,
            }
            for tag in details.tags
        ],
        "chapters": [_chapter_to_data(chapter) for chapter in chapters],
        "statistics": (
            {
                "rating": statistics.rating,
                "rating_count": statistics.rating_count,
                "follows": statistics.follows,
                "comments": statistics.comments,
            }
            if statistics is not None
            else None
        ),
        "gallery": bool(gallery),
        "data_saver": data_saver,
    }
    if errors:
        result["error"] = "; ".join(errors)[:500]

    if user_id and user_id != "0":
        enriched = _manga_to_item(details)
        enriched.update(
            {
                "description": result["description"],
                "artists": result["artists"],
                "alt_titles": result["alt_titles"],
                "original_language": result["original_language"],
                "demographic": result["demographic"],
            }
        )
        enrich_history(user_id, enriched)
    return result


def _run_search(library: Library, body: dict[str, Any]) -> dict[str, Any]:
    query = _build_query(library, body)
    session = _get_session(library, query, body)
    result = session.fetch(int(body.get("limit") or 24))
    items = list(result.items)
    user_id = _user_id(body)
    settings = get_settings(user_id)
    if settings.get("hide_read_in_search") and user_id != "0":
        read_keys = {entry.get("key") for entry in get_history(user_id)}
        items = [m for m in items if m.key not in read_keys]
    total = result.total
    if total is None:
        total = len(items) + (1 if result.has_more else 0)
    return {
        "items": [_manga_to_item(m) for m in items],
        "total": int(total),
        "has_more": bool(result.has_more),
        "states": {
            key: {"error": state.error, "note": state.note}
            for key, state in result.states.items()
        },
    }


def _run_history_clear(user_id: str) -> dict[str, Any]:
    clear_history(user_id)
    return {"ok": True, "history_count": 0}


def _run_reset_settings(user_id: str) -> dict[str, Any]:
    state = reset_user(user_id, "settings")
    return {"ok": True, **state["settings"], "history_count": len(state["history"])}


def _run_reset_all(user_id: str) -> dict[str, Any]:
    state = reset_user(user_id, "all")
    return {"ok": True, **state["settings"], "history_count": 0}


def _run_check_sites(library: Library) -> dict[str, Any]:
    results = library.check_sites()
    sites = []
    for key, source in library.sources.items():
        ok, detail = results.get(key, (False, ""))
        sites.append({
            "key": key,
            "name": source.name,
            "url": source.site_url,
            "ok": bool(ok),
            "detail": str(detail),
        })
    return {"sites": sites}


def _seed_manga(body: dict[str, Any]) -> Manga:
    """Minimal Manga from a request, enough to call the source layer."""
    source_key = str(body.get("source") or "doujins")
    return Manga(
        id=str(body.get("id") or ""),
        title=str(body.get("title") or ""),
        source=source_key,
        source_name=str(body.get("source_name") or ""),
        url_text=str(body.get("url") or ""),
        cover_url_text=str(body.get("cover_url") or ""),
    )


def _run_details(library: Library, body: dict[str, Any]) -> dict[str, Any]:
    return _manga_to_detail(library, _seed_manga(body), user_id=_user_id(body))


def _run_pages(library: Library, body: dict[str, Any]) -> dict[str, Any]:
    """Return page image URLs for one chapter (reader support)."""
    reference = body.get("chapter_id")
    if reference is None:
        reference = body.get("chapter")
    chapter_id = str(reference if reference is not None else "")
    manga = _seed_manga(body)
    try:
        details = library.get_details(manga)
        chapters = library.get_chapters(details)
    except Exception as exc:  # noqa: BLE001
        return {"urls": [], "chapter_id": chapter_id, "page_total": 0, "error": _error_text(exc)}

    chosen = None
    for index, chapter in enumerate(chapters):
        if str(chapter.id) == chapter_id or str(index) == chapter_id:
            chosen = chapter
            break
    if chosen is None:
        return {
            "urls": [],
            "chapter_id": chapter_id,
            "page_total": 0,
            "error": "Chapter not found",
        }

    try:
        urls = [
            str(url)
            for url in library.get_page_urls(
                details, chosen, data_saver=bool(body.get("data_saver"))
            )
            if str(url)
        ][:_MAX_PAGE_URLS]
    except Exception as exc:  # noqa: BLE001
        return {
            "urls": [],
            "chapter_id": chapter_id,
            "page_total": chosen.page_total,
            "error": _error_text(exc),
        }
    return {
        "urls": urls,
        "chapter_id": chosen.id,
        "page_total": max(chosen.page_total, len(urls)),
    }


def _run_random(library: Library, body: dict[str, Any]) -> dict[str, Any]:
    """Pick a random source and return one random search card from it."""
    user_id = _user_id(body)
    if "sources" in body:
        raw_sources = body.get("sources") or []
    else:
        raw_sources = get_settings(user_id).get("sources") or []
    allowed = [
        str(key) for key in raw_sources if str(key) in library.sources
    ]
    if not allowed:
        return {"error": "No sources selected"}

    query = _build_query(library, body)
    query.sources = [_random.choice(allowed)]
    query.limit = 24
    try:
        result = library.search(query, limit=24)
    except Exception as exc:  # noqa: BLE001
        return {"error": _error_text(exc)}
    items = list(result.items)
    if get_settings(user_id).get("hide_read_in_search") and user_id != "0":
        read_keys = {entry.get("key") for entry in get_history(user_id)}
        items = [m for m in items if m.key not in read_keys]
    if not items:
        return {"error": "Nothing found"}
    return {"item": _manga_to_item(_random.choice(items))}


def _run_open(library: Library, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Open a title externally: mark it as read and return the card."""
    manga = _seed_manga(body)
    try:
        manga = library.get_details(manga)
    except Exception:  # noqa: BLE001 - keep history even if the site is down
        pass
    entry = _manga_to_item(manga)
    add_history(user_id, entry)
    return entry


def _run_history_add(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Add/enrich a history item from a card DTO without any network call."""
    item = body.get("item")
    if not isinstance(item, dict):
        return {"error": "item must be an object"}
    try:
        add_history(user_id, item)
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}
    return {"ok": True, "history_count": history_count(user_id)}


def _run_history(user_id: str) -> dict[str, Any]:
    return {"history": get_history(user_id)}


def _run_history_remove(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    key = str(body.get("key") or "")
    if not key:
        key = f"{body.get('source') or ''}:{body.get('id') or ''}"
    remove_history(user_id, key)
    return {"ok": True}


def _run_sources(library: Library) -> dict[str, Any]:
    return {
        "sources": [
            {
                "key": key,
                "name": source.name,
                "url": source.site_url,
                "kind": source.kind,
            }
            for key, source in library.sources.items()
        ]
    }


_SETTINGS_KEYS = frozenset(default_settings().keys())


def _run_settings(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(user_id)
    # No settings fields in the request -> read-only load.
    incoming = {key: body[key] for key in _SETTINGS_KEYS if key in body}
    if incoming:
        current = update_settings(user_id, incoming)
    return {
        "ok": True,
        **current,
        "history_count": history_count(user_id),
    }


def _run_tags(library: Library) -> dict[str, Any]:
    tags = library.get_tags(allow_network=True)
    return {
        "tags": [
            {
                "id": t.id,
                "name": t.name,
                "group": t.group,
                "description": t.description,
            }
            for t in tags
        ],
    }


# ----------------------------------------------------------------------- rule34

# Values come from the user's Settings or from the deployment environment.
# No credentials are stored in the repository.
def _rule34_creds(user_id: str, body: dict[str, Any]) -> tuple[str, str]:
    settings = get_settings(user_id)
    uid = str(
        body.get("rule34_user_id")
        or settings.get("rule34_user_id")
        or os.environ.get("R34_USER_ID")
        or ""
    ).strip()
    key = str(
        body.get("rule34_api_key")
        or settings.get("rule34_api_key")
        or os.environ.get("R34_API_KEY")
        or ""
    ).strip()
    return uid, key


def _run_rule34_search(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    tags = str(body.get("tags") or body.get("q") or "").strip()
    pid = max(0, int(body.get("pid") or body.get("page") or 0))
    limit = max(1, min(100, int(body.get("limit") or 40)))
    r34_uid, r34_key = _rule34_creds(user_id, body)
    settings = get_settings(user_id)

    include_ai = body.get("rule34_ai_generated")
    if include_ai is None:
        include_ai = settings.get("rule34_ai_generated", True)
    include_videos = body.get("rule34_videos")
    if include_videos is None:
        include_videos = settings.get("rule34_videos", True)
    include_images = body.get("rule34_images")
    if include_images is None:
        include_images = settings.get("rule34_images", True)
    include_ai = bool(include_ai)
    include_videos = bool(include_videos)
    include_images = bool(include_images)

    tag_parts = [t for t in tags.split() if t]
    if not include_ai and "ai_generated" not in tag_parts and "-ai_generated" not in tag_parts:
        tag_parts.append("-ai_generated")
    tags_query = " ".join(tag_parts).strip()

    params: dict[str, Any] = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "json": "1",
        "tags": tags_query,
        "limit": str(limit),
        "pid": str(pid),
    }
    if r34_uid and r34_key:
        params["user_id"] = r34_uid
        params["api_key"] = r34_key

    url = "https://api.rule34.xxx/index.php?" + urlencode(params)
    try:
        resp = http_request(
            "GET",
            url,
            timeout=25.0,
            retries=1,
            headers={"Accept": "application/json,text/plain,*/*"},
        )
    except Exception as exc:
        return {"error": f"Rule34 request failed: {exc}"}

    try:
        data = resp.json()
    except Exception:
        data = None

    if not isinstance(data, list):
        return {"error": "Rule34 request failed."}

    items = []
    for post in data:
        if not isinstance(post, dict):
            continue
        file_url = str(post.get("file_url") or "")
        preview_url = str(post.get("preview_url") or post.get("sample_url") or file_url)
        if file_url and not file_url.startswith("http"):
            file_url = "https:" + file_url if file_url.startswith("//") else file_url
        if preview_url and not preview_url.startswith("http"):
            preview_url = "https:" + preview_url if preview_url.startswith("//") else preview_url
        if not file_url and not preview_url:
            continue
        ext = file_url.rsplit(".", 1)[-1].lower() if "." in file_url else ""
        media_type = "video" if ext in ("mp4", "webm") else ("gif" if ext == "gif" else "image")
        items.append({
            "id": post.get("id"),
            "file_url": file_url or preview_url,
            "preview_url": preview_url or file_url,
            "sample_url": str(post.get("sample_url") or ""),
            "tags": str(post.get("tags") or ""),
            "rating": str(post.get("rating") or ""),
            "score": post.get("score"),
            "width": post.get("width"),
            "height": post.get("height"),
            "media_type": media_type,
            "source": str(post.get("source") or ""),
        })

    if not include_videos:
        items = [it for it in items if it.get("media_type") != "video"]
    if not include_images:
        items = [it for it in items if it.get("media_type") == "video"]

    has_more = len(data) >= limit
    return {"items": items, "has_more": has_more, "pid": pid, "next_pid": pid + 1}


def _run_rule34_autocomplete(body: dict[str, Any]) -> dict[str, Any]:
    q = str(body.get("q") or body.get("term") or "").strip()
    if len(q) < 2:
        return {"items": []}
    url = "https://api.rule34.xxx/autocomplete.php?q=" + quote_plus(q)
    try:
        resp = http_request(
            "GET",
            url,
            timeout=15.0,
            retries=1,
            headers={"Accept": "application/json,text/plain,*/*"},
        )
        data = resp.json()
    except Exception as exc:
        return {"error": f"Autocomplete failed: {exc}"}
    if not isinstance(data, list):
        return {"items": []}
    items = []
    for entry in data[:20]:
        if isinstance(entry, dict):
            value = str(entry.get("value") or entry.get("label") or "").strip()
            label = str(entry.get("label") or value).strip()
            if value:
                items.append({"value": value, "label": label})
        elif isinstance(entry, str) and entry.strip():
            items.append({"value": entry.strip(), "label": entry.strip()})
    return {"items": items}


def run_action(body: dict[str, Any]) -> dict[str, Any]:
    """Pure action dispatcher (kept separate for local testing)."""
    action = str(body.get("action") or "search")
    if action == "register":
        return auth_register(body)
    if action == "login":
        return auth_login(body)
    if action == "logout":
        return auth_logout(body.get("_session_token"))
    if action == "me":
        return auth_me(body.get("_session_token"))
    if action == "admin_status":
        return auth_admin_status(body.get("_session_token"))
    if action == "admin_accounts":
        return auth_admin_accounts(body.get("_session_token"))
    if action == "admin_create_account":
        return auth_admin_create(body.get("_session_token"), body)
    if action == "admin_reset_password":
        return auth_admin_reset_password(body.get("_session_token"), body)
    if action == "admin_delete_account":
        return auth_admin_delete(body.get("_session_token"), body)

    library = _library()
    user_id = _user_id(body)
    if action == "search":
        return _run_search(library, body)
    if action == "random":
        return _run_random(library, body)
    if action == "details":
        return _run_details(library, body)
    if action == "pages":
        return _run_pages(library, body)
    if action == "open":
        return _run_open(library, user_id, body)
    if action == "history_add":
        return _run_history_add(user_id, body)
    if action == "history":
        return _run_history(user_id)
    if action == "history_remove":
        return _run_history_remove(user_id, body)
    if action == "history_clear":
        return _run_history_clear(user_id)
    if action == "sources":
        return _run_sources(library)
    if action == "settings":
        return _run_settings(user_id, body)
    if action == "reset_settings":
        return _run_reset_settings(user_id)
    if action == "reset_all":
        return _run_reset_all(user_id)
    if action == "check_sites":
        return _run_check_sites(library)
    if action == "tags":
        return _run_tags(library)
    if action == "rule34_search":
        return _run_rule34_search(user_id, body)
    if action == "rule34_autocomplete":
        return _run_rule34_autocomplete(body)
    return {"error": f"Unknown action: {action}"}


async def api_index(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid body"}, status_code=400)

    action = str(body.get("action") or "search")
    token = request.cookies.get(SESSION_COOKIE)

    # Auth actions need the real Request so we can set/clear the session cookie.
    if action in AUTH_ACTIONS:
        if action == "register":
            result = auth_register(body)
        elif action == "login":
            result = auth_login(body)
        elif action == "logout":
            result = auth_logout(token)
        elif action == "admin_status":
            result = auth_admin_status(token)
        elif action == "admin_accounts":
            result = auth_admin_accounts(token)
        elif action == "admin_create_account":
            result = auth_admin_create(token, body)
        elif action == "admin_reset_password":
            result = auth_admin_reset_password(token, body)
        elif action == "admin_delete_account":
            result = auth_admin_delete(token, body)
        else:
            result = auth_me(token)

        error = str(result.get("error") or "")
        unavailable = error == AUTH_UNAVAILABLE
        unauthorized = error == ADMIN_REQUIRED or (
            action == "login" and bool(error) and not unavailable
        )
        if result.get("ok") or not error:
            status = 200
        elif unavailable:
            status = 503
        elif unauthorized:
            status = 401
        else:
            status = 400

        safe_result = {key: value for key, value in result.items() if key != "token"}
        response = JSONResponse(
            safe_result, status_code=status, headers={"Cache-Control": "no-store"}
        )
        if result.get("token"):
            response.set_cookie(
                SESSION_COOKIE,
                str(result["token"]),
                max_age=SESSION_TTL,
                path="/",
                httponly=True,
                secure=True,
                samesite="lax",
            )
        if action == "logout":
            response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # A valid session always wins over a client-supplied user_id.
    user = auth_session_user(token)
    if user:
        body["user_id"] = user["user_id"]
        body["_session_token"] = token

    result = run_action(body)
    # Details may carry a soft error for chapters/statistics while still being
    # a valid response, so keep it a 200 for the frontend.
    status = 200 if ("error" not in result or action == "details") else 400
    return JSONResponse(result, status_code=status)


def _proxy_referer_for_url(raw_url: str) -> str | None:
    parsed = urlparse(raw_url)
    host = (parsed.netloc or "").lower()
    referer_overrides = {
        "hermes.hentai.direct": "https://hentaihere.com/",
        "readimg.site": "https://hentairead.moe/",
        "api-cdn.rule34.xxx": "https://rule34.xxx/",
        "wimg.rule34.xxx": "https://rule34.xxx/",
        "ws-cdn-video.rule34.xxx": "https://rule34.xxx/",
        "rule34.xxx": "https://rule34.xxx/",
    }
    if host in referer_overrides:
        return referer_overrides[host]
    if host.endswith(".rule34.xxx"):
        return "https://rule34.xxx/"
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return None


async def api_cover(request: Request) -> Response:
    raw_url = (request.query_params.get("url") or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return Response(status_code=400)
    referer = _proxy_referer_for_url(raw_url)
    try:
        resp = http_request(
            "GET",
            raw_url,
            referer=referer,
            retries=2,
            timeout=20.0,
            headers={"Accept": "image/*,*/*;q=0.8"},
        )
    except Exception:
        # Retry without referer for hosts that block specific referers.
        try:
            resp = http_request(
                "GET",
                raw_url,
                referer=None,
                retries=1,
                timeout=20.0,
                headers={"Accept": "image/*,*/*;q=0.8"},
            )
        except Exception:
            return Response(status_code=502)
    content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def api_media(request: Request) -> Response:
    raw_url = (request.query_params.get("url") or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return Response(status_code=400)
    referer = _proxy_referer_for_url(raw_url)
    try:
        resp = http_request(
            "GET",
            raw_url,
            referer=referer,
            retries=2,
            timeout=30.0,
            headers={"Accept": "*/*"},
        )
    except Exception:
        try:
            resp = http_request(
                "GET",
                raw_url,
                referer=None,
                retries=1,
                timeout=30.0,
                headers={"Accept": "*/*"},
            )
        except Exception:
            return Response(status_code=502)
    content_type = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
    if not (content_type.startswith("image/") or content_type.startswith("video/")):
        # Guess from URL for common media types.
        low = raw_url.lower()
        if low.endswith((".mp4",)):
            content_type = "video/mp4"
        elif low.endswith((".webm",)):
            content_type = "video/webm"
        elif low.endswith((".gif",)):
            content_type = "image/gif"
        elif low.endswith((".png",)):
            content_type = "image/png"
        elif low.endswith((".webp",)):
            content_type = "image/webp"
        elif low.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
    return Response(
        content=resp.content,
        media_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def api_tags(request: Request) -> JSONResponse:
    return JSONResponse(_run_tags(_library()))


app = Starlette(
    routes=[
        Route("/api/app", endpoint=api_index, methods=["POST"]),
        Route("/api/tags", endpoint=api_tags, methods=["GET"]),
        Route("/api/cover", endpoint=api_cover, methods=["GET"]),
        Route("/api/media", endpoint=api_media, methods=["GET"]),
        Mount("/", app=StaticFiles(directory="public", html=True), name="static"),
    ]
)
