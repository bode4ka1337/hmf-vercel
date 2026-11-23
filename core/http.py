"""Shared HTTP layer for all sources.

Sessions are per-thread (``requests.Session`` is not thread-safe) while rate
limiters are global so parallel searches stay polite.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class HttpError(RuntimeError):
    """Network error or a non-successful HTTP status."""


class RateLimiter:
    """Ensures requests are not made more often than every ``min_interval``."""

    def __init__(self, min_interval: float) -> None:
        self._interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            if delay > 0:
                time.sleep(delay)
            self._next_at = time.monotonic() + self._interval


_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,image/*;q=0.8,*/*;q=0.7",
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            }
        )
        _local.session = session
    return session


def request(
    method: str,
    url: str,
    *,
    params: Any = None,
    headers: dict[str, str] | None = None,
    referer: str | None = None,
    limiter: RateLimiter | None = None,
    retries: int = 3,
    timeout: float = 30.0,
    **kwargs: Any,
) -> requests.Response:
    """Perform a request with retries, 429/5xx handling and a shared limiter."""

    last_error: Exception | None = None
    merged_headers = dict(headers or {})
    if referer:
        merged_headers.setdefault("Referer", referer)

    for attempt in range(retries):
        if limiter is not None:
            limiter.wait()
        try:
            response = _session().request(
                method,
                url,
                params=params,
                headers=merged_headers or None,
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 10.0) if retry_after else 2.0
            except ValueError:
                delay = 2.0
            time.sleep(delay)
            last_error = HttpError("Too many requests (429)")
            continue
        if 500 <= response.status_code < 600:
            last_error = HttpError(f"Server temporarily unavailable ({response.status_code})")
            time.sleep(0.8 * (attempt + 1))
            continue
        if response.status_code >= 400:
            text = response.text[:200].replace("\n", " ")
            raise HttpError(f"HTTP {response.status_code}: {text}")
        return response

    raise HttpError(str(last_error) if last_error else "Request failed")


def get_bytes(
    url: str,
    *,
    limiter: RateLimiter | None = None,
    referer: str | None = None,
    retries: int = 3,
) -> bytes:
    response = request(
        "GET",
        url,
        limiter=limiter,
        referer=referer,
        retries=retries,
        headers={"Accept": "image/*,*/*;q=0.8"},
    )
    return response.content
