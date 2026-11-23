"""Per-user state for the web/Mini App backend.

The serverless backend keeps one record per user id (Telegram id or an
arbitrary client id generated outside Telegram) and mirrors it to two places:

* ``/tmp/hmf/state.json`` -- atomic JSON snapshot, always best effort;
* Vercel KV REST (when ``KV_REST_API_URL`` and ``KV_REST_API_TOKEN`` are set)
  -- durable per-user storage. Any KV failure falls back to the local file.

None of the environment variables are required: without them the module works
exactly like the old in-memory implementation, plus the local snapshot.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

STATE_FILE = Path("/tmp/hmf/state.json")
HISTORY_LIMIT = 800
KV_TIMEOUT = 2.0
KV_PREFIX = "hmf:user:"

try:
    from core.sources import DEFAULT_SOURCES
except Exception:  # pragma: no cover - keep state.py importable on its own
    DEFAULT_SOURCES = [
        "nhentai", "nhentaixxx", "hentaifox", "hentairox", "hentaiera",
        "hentaihere", "hentaireadmoe", "hentaienvy", "asmhentai", "3hentai",
        "doujins",
    ]

_BOOL_KEYS = frozenset(
    (
        "block_gay", "hide_read", "hide_read_in_search",
        "rule34_ai_generated", "rule34_videos", "rule34_images",
    )
)
_LIST_KEYS = frozenset(
    ("sources", "tags", "excluded", "favorite_tags", "languages")
)
_STR_KEYS = frozenset(
    (
        "interface_language", "tag_mode", "sort",
        "rule34_user_id", "rule34_api_key",
    )
)

_LOCK = threading.RLock()
_USERS: dict[str, dict[str, Any]] = {}
_KV_CHECKED: set[str] = set()


def normalize_user_id(user_id: Any) -> str:
    """Accept str/int/None and return a stable storage key."""
    if user_id is None:
        return "0"
    if isinstance(user_id, bool):
        return "1" if user_id else "0"
    value = str(user_id).strip()
    return value or "0"


def default_settings() -> dict[str, Any]:
    """Default settings."""
    return {
        "sources": list(DEFAULT_SOURCES),
        "block_gay": False,
        "hide_read": False,
        "hide_read_in_search": False,
        "interface_language": "en",
        "tags": [],
        "excluded": [],
        "favorite_tags": [],
        "tag_mode": "AND",
        "languages": [],
        "sort": "relevance",
        "rule34_user_id": "",
        "rule34_api_key": "",
        "rule34_ai_generated": True,
        "rule34_videos": True,
        "rule34_images": True,
    }


def _clean_settings(raw: Any) -> dict[str, Any]:
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    for key, value in raw.items():
        if key not in settings:
            continue
        if key in _BOOL_KEYS:
            settings[key] = bool(value)
        elif key in _LIST_KEYS:
            settings[key] = [str(item) for item in (value or []) if str(item)]
        elif key in _STR_KEYS:
            settings[key] = str(value or "").strip()
    tag_mode = str(settings.get("tag_mode") or "AND").upper()
    settings["tag_mode"] = tag_mode if tag_mode in ("AND", "OR") else "AND"
    return settings


def _clean_history(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw[:HISTORY_LIMIT]:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        try:
            entry["read_at"] = float(entry.get("read_at") or time.time())
        except (TypeError, ValueError):
            entry["read_at"] = time.time()
        if not entry.get("key"):
            source = str(entry.get("source") or "")
            manga_id = str(entry.get("id") or "")
            if source and manga_id:
                entry["key"] = f"{source}:{manga_id}"
        if entry.get("key"):
            entry["key"] = str(entry["key"])
            entries.append(entry)
    entries.sort(key=lambda item: float(item.get("read_at") or 0), reverse=True)
    return entries[:HISTORY_LIMIT]


def _normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    settings = raw.get("settings")
    if not isinstance(settings, dict):
        # Legacy shape: settings keys stored on the top level.
        settings = raw
    return {
        "settings": _clean_settings(settings),
        "history": _clean_history(raw.get("history")),
    }


def _copy_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "settings": dict(state.get("settings") or {}),
        "history": [dict(item) for item in (state.get("history") or [])],
    }


# --------------------------------------------------------------- persistence


def _kv_enabled() -> bool:
    return bool(
        os.environ.get("KV_REST_API_URL") and os.environ.get("KV_REST_API_TOKEN")
    )


def _kv_request(method: str, suffix: str, *, data: bytes | None = None) -> Any:
    from core.http import request as http_request

    base = str(os.environ.get("KV_REST_API_URL") or "").strip().rstrip("/")
    token = str(os.environ.get("KV_REST_API_TOKEN") or "").strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    response = http_request(
        method,
        f"{base}/{suffix}",
        headers=headers,
        data=data,
        retries=1,
        timeout=KV_TIMEOUT,
    )
    try:
        return response.json()
    except Exception:
        return None


def _kv_load(uid: str) -> dict[str, Any] | None:
    """Read one user's record from Vercel KV; never raises."""
    if not _kv_enabled():
        return None
    try:
        payload = _kv_request("GET", f"get/{quote(KV_PREFIX + uid, safe='')}")
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return None
    return result if isinstance(result, dict) else None


def _kv_save(uid: str, state: dict[str, Any]) -> bool:
    if not _kv_enabled():
        return False
    payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
    try:
        _kv_request(
            "POST",
            f"set/{quote(KV_PREFIX + uid, safe='')}",
            data=payload,
        )
        return True
    except Exception:
        return False


def _write_snapshot_locked() -> None:
    """Atomic best-effort write of all known users to /tmp/hmf/state.json."""
    temp_name = ""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=".state-", suffix=".json", dir=str(STATE_FILE.parent)
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "version": 1,
                    "saved_at": time.time(),
                    "users": {
                        uid: _copy_state(state) for uid, state in _USERS.items()
                    },
                },
                stream,
                ensure_ascii=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, STATE_FILE)
        temp_name = ""
    except Exception:
        pass
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _load_snapshot() -> None:
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    users = payload.get("users")
    if isinstance(users, dict):
        for uid, raw in users.items():
            _USERS[normalize_user_id(uid)] = _normalize_state(raw)
    elif "settings" in payload or "history" in payload:
        _USERS["0"] = _normalize_state(payload)


_load_snapshot()


def _persist_locked(uid: str) -> None:
    _write_snapshot_locked()
    _kv_save(uid, _copy_state(_USERS[uid]))


def _ensure_locked(uid: str) -> dict[str, Any]:
    state = _USERS.get(uid)
    if state is None:
        remote = None
        if uid not in _KV_CHECKED:
            _KV_CHECKED.add(uid)
            remote = _kv_load(uid)
        state = _normalize_state(remote)
        _USERS[uid] = state
        if remote is not None:
            _write_snapshot_locked()
    return state


# ---------------------------------------------------------------- public API


def get_user(user_id: Any) -> dict[str, Any]:
    """Return a copy of ``{"settings": ..., "history": [...]}``."""
    uid = normalize_user_id(user_id)
    with _LOCK:
        return _copy_state(_ensure_locked(uid))


def get_settings(user_id: Any) -> dict[str, Any]:
    return get_user(user_id)["settings"]


def get_history(user_id: Any) -> list[dict[str, Any]]:
    return get_user(user_id)["history"]


def history_count(user_id: Any) -> int:
    return len(get_history(user_id))


def update_user(
    user_id: Any,
    *,
    settings: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge settings and/or replace history, then persist."""
    uid = normalize_user_id(user_id)
    with _LOCK:
        state = _ensure_locked(uid)
        if settings:
            state["settings"] = _clean_settings({**state["settings"], **settings})
        if history is not None:
            state["history"] = _clean_history(history)
        _persist_locked(uid)
        return _copy_state(state)


def update_settings(user_id: Any, values: dict[str, Any]) -> dict[str, Any]:
    return update_user(user_id, settings=values)["settings"]


def reset_user(user_id: Any, what: str = "all") -> dict[str, Any]:
    """Reset ``history``, ``settings`` or both for one user."""
    uid = normalize_user_id(user_id)
    mode = str(what or "all").lower()
    with _LOCK:
        state = _ensure_locked(uid)
        if mode in ("settings", "all"):
            state["settings"] = default_settings()
        if mode in ("history", "all"):
            state["history"] = []
        _persist_locked(uid)
        return _copy_state(state)


def _merge_entry(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Prefer richer metadata while keeping the original ``read_at``."""
    merged = dict(old)
    read_at = old.get("read_at")
    for key, value in new.items():
        if key == "read_at":
            continue
        if value in (None, "", [], {}):
            continue
        previous = merged.get(key)
        if isinstance(value, list) and isinstance(previous, list):
            if len(value) < len(previous):
                continue
        merged[key] = value
    if read_at is not None:
        merged["read_at"] = read_at
    return merged


def add_history(
    user_id: Any, entry: dict[str, Any], *, read_at: float | None = None
) -> dict[str, Any]:
    """Add or enrich a history item without any network access."""
    uid = normalize_user_id(user_id)
    if not isinstance(entry, dict):
        raise TypeError("history entry must be a dict")
    clean = _clean_history([entry])
    if not clean:
        raise ValueError("history entry needs a key or source+id")
    item = clean[0]
    item["read_at"] = float(read_at if read_at is not None else time.time())
    with _LOCK:
        state = _ensure_locked(uid)
        history = state["history"]
        for index, old in enumerate(history):
            if old.get("key") == item.get("key"):
                history[index] = _merge_entry(old, item)
                break
        else:
            history.insert(0, item)
        history[:] = _clean_history(history)[:HISTORY_LIMIT]
        _persist_locked(uid)
        return dict(history[0] if history[0].get("key") == item["key"] else item)


def enrich_history(user_id: Any, entry: dict[str, Any]) -> dict[str, Any] | None:
    """Refresh an existing history item, keeping its ``read_at`` timestamp."""
    uid = normalize_user_id(user_id)
    clean = _clean_history([entry])
    if not clean:
        return None
    item = clean[0]
    with _LOCK:
        state = _ensure_locked(uid)
        for index, old in enumerate(state["history"]):
            if old.get("key") == item.get("key"):
                state["history"][index] = _merge_entry(old, item)
                _persist_locked(uid)
                return dict(state["history"][index])
    return None


def remove_history(user_id: Any, key: str) -> bool:
    uid = normalize_user_id(user_id)
    key = str(key or "")
    with _LOCK:
        state = _ensure_locked(uid)
        before = len(state["history"])
        state["history"] = [
            item for item in state["history"] if item.get("key") != key
        ]
        changed = len(state["history"]) != before
        if changed:
            _persist_locked(uid)
        return changed


def clear_history(user_id: Any) -> int:
    state = reset_user(user_id, "history")
    return len(state["history"])


__all__ = [
    "HISTORY_LIMIT",
    "STATE_FILE",
    "add_history",
    "clear_history",
    "default_settings",
    "enrich_history",
    "get_history",
    "get_settings",
    "get_user",
    "history_count",
    "normalize_user_id",
    "remove_history",
    "reset_user",
    "update_settings",
    "update_user",
]
