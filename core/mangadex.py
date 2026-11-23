"""Compatibility: the MangaDex client moved to :mod:`core.sources.mangadex`.

Old imports such as ``from core.mangadex import MangaDexClient`` still work,
but new code should use :class:`core.library.Library`.
"""

from __future__ import annotations

from .http import HttpError, RateLimiter
from .sources.mangadex import (
    API_BASE,
    UPLOADS_BASE,
    USER_AGENT,
    MangaDexSource,
)

MangaDexClient = MangaDexSource
MangaDexError = HttpError

__all__ = [
    "API_BASE",
    "UPLOADS_BASE",
    "USER_AGENT",
    "HttpError",
    "MangaDexClient",
    "MangaDexError",
    "MangaDexSource",
    "RateLimiter",
]
