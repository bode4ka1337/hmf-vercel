"""Source registry: UI order and source construction."""

from __future__ import annotations

from typing import Iterable

from .base import SearchPage, Source
from .galleries import (
    AsmHentaiSource,
    HentaiFoxSource,
    ThreeHentaiSource,
)
from .doujins import DoujinsSource
from .hentaiera import HentaiEraSource
from .hentaienvy import HentaiEnvySource
from .hentaihere import HentaiHereSource
from .hentaireadmoe import HentaiReadMoeSource
from .hentairox import HentaiRoxSource
from .nhentai import NhentaiSource
from .nhentaixxx import NhentaiXxxSource

SOURCE_CHOICES: list[tuple[str, str]] = [
    ("nhentai", "nhentai"),
    ("nhentaixxx", "nhentai.xxx"),
    ("hentaifox", "HentaiFox"),
    ("hentairox", "HentaiRox"),
    ("hentaiera", "HentaiEra"),
    ("hentaihere", "HentaiHere"),
    ("hentaireadmoe", "HentaiRead.moe"),
    ("hentaienvy", "HentaiEnvy"),
    ("asmhentai", "AsmHentai"),
    ("3hentai", "3Hentai"),
    ("doujins", "Doujins"),
]

SOURCE_ORDER = {key: index for index, (key, _) in enumerate(SOURCE_CHOICES)}

DEFAULT_SOURCES: list[str] = [key for key, _ in SOURCE_CHOICES]

# Sources where almost all content is adult-only.
ADULT_SOURCES: set[str] = {
    "nhentai",
    "nhentaixxx",
    "hentaifox",
    "hentairox",
    "hentaiera",
    "hentaihere",
    "hentaireadmoe",
    "hentaienvy",
    "asmhentai",
    "3hentai",
    "doujins",
}

SOURCE_CLASSES: dict[str, type[Source]] = {
    "nhentai": NhentaiSource,
    "nhentaixxx": NhentaiXxxSource,
    "hentaifox": HentaiFoxSource,
    "hentairox": HentaiRoxSource,
    "hentaiera": HentaiEraSource,
    "hentaihere": HentaiHereSource,
    "hentaireadmoe": HentaiReadMoeSource,
    "hentaienvy": HentaiEnvySource,
    "asmhentai": AsmHentaiSource,
    "3hentai": ThreeHentaiSource,
    "doujins": DoujinsSource,
}


def build_sources(keys: Iterable[str] | None = None) -> dict[str, Source]:
    """Create source instances in the order used by the interface."""
    selected = list(keys) if keys is not None else list(SOURCE_ORDER)
    sources: dict[str, Source] = {}
    for key in sorted(selected, key=lambda item: SOURCE_ORDER.get(item, 99)):
        factory = SOURCE_CLASSES.get(key)
        if factory is not None:
            sources[key] = factory()
    return sources


__all__ = [
    "ADULT_SOURCES",
    "DEFAULT_SOURCES",
    "SOURCE_CHOICES",
    "SOURCE_CLASSES",
    "SOURCE_ORDER",
    "SearchPage",
    "Source",
    "build_sources",
]