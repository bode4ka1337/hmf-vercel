"""Data models shared by the source clients and the user interface."""

from __future__ import annotations

from dataclasses import dataclass, field

MANGA_URL = "https://mangadex.org/title/{manga_id}"
COVER_URL = "https://uploads.mangadex.org/covers/{manga_id}/{file}.{size}.jpg"

# Genres, themes and labels are all tags. The group is only used for
# filtering and sorting in the interface, not for search behaviour.
TAG_GROUPS: list[tuple[str, str]] = [
    ("genre", "Genres"),
    ("theme", "Themes"),
    ("format", "Format"),
    ("content", "Labels"),
]
TAG_GROUP_TITLES = dict(TAG_GROUPS)
TAG_GROUP_ORDER = {key: index for index, (key, _) in enumerate(TAG_GROUPS)}

CONTENT_RATINGS: list[str] = ["safe", "suggestive", "erotica", "pornographic"]

STATUS_CODES: list[str] = ["ongoing", "completed", "hiatus", "cancelled"]
DEMOGRAPHIC_CODES: list[str] = ["shounen", "shoujo", "seinen", "josei"]

SORT_OPTIONS: list[tuple[str, str, dict[str, str]]] = [
    # (key, English label, order[...] parameters for MangaDex)
    ("relevance", "Relevance", {"order[relevance]": "desc"}),
    ("latest", "Updated", {"order[latestUploadedChapter]": "desc"}),
    ("follows", "Popularity", {"order[followedCount]": "desc"}),
    ("rating", "Rating", {"order[rating]": "desc"}),
    ("title", "Title", {"order[title]": "asc"}),
]

LANGUAGES: list[tuple[str, str]] = [
    ("ru", "Russian"),
    ("en", "English"),
    ("uk", "Українська"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("zh", "中文"),
    ("zh-hk", "中文 (HK)"),
    ("pl", "Polski"),
    ("es", "Español"),
    ("es-la", "Español (Latam)"),
    ("pt-br", "Português (BR)"),
    ("pt", "Português"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("tr", "Türkçe"),
    ("vi", "Tiếng Việt"),
    ("id", "Indonesia"),
    ("th", "ไทย"),
    ("ar", "العربية"),
    ("hi", "हिन्दी"),
    ("cs", "Čeština"),
    ("hu", "Magyar"),
    ("ro", "Română"),
    ("nl", "Nederlands"),
    ("sv", "Svenska"),
    ("fi", "Suomi"),
    ("bg", "Български"),
    ("el", "Ελληνικά"),
    ("fa", "فارسی"),
    ("he", "עברית"),
    ("ca", "Català"),
    ("tl", "Tagalog"),
    ("ms", "Bahasa Melayu"),
    ("bn", "বাংলা"),
    ("ta", "தமிழ்"),
    ("te", "తెలుగు"),
    ("mr", "मराठी"),
    ("ur", "اردو"),
    ("uk", "Українська"),
    ("be", "Беларуская"),
    ("sr", "Српски"),
    ("hr", "Hrvatski"),
    ("sk", "Slovenčina"),
    ("sl", "Slovenščina"),
    ("lt", "Lietuvių"),
    ("lv", "Latviešu"),
    ("et", "Eesti"),
    ("da", "Dansk"),
    ("no", "Norsk"),
    ("is", "Íslenska"),
    ("ga", "Gaeilge"),
    ("cy", "Cymraeg"),
    ("eu", "Euskara"),
    ("gl", "Galego"),
    ("af", "Afrikaans"),
    ("sw", "Kiswahili"),
    ("am", "አማርኛ"),
    ("km", "ខ្មែរ"),
    ("lo", "ລາວ"),
    ("my", "မြန်မာ"),
    ("ne", "नेपाली"),
    ("si", "සිංහල"),
    ("ka", "ქართული"),
    ("hy", "Հայերեն"),
    ("az", "Azərbaycan"),
    ("kk", "Қазақша"),
    ("uz", "O'zbekcha"),
    ("mn", "Монгол"),
]

LANGUAGE_NAMES = dict(LANGUAGES)

# English language names used by nhentai and gallery sites.
LANGUAGE_CODES: dict[str, str] = {
    "english": "en",
    "russian": "ru",
    "ukrainian": "uk",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "cantonese": "zh-hk",
    "polish": "pl",
    "spanish": "es",
    "latino": "es-la",
    "portuguese": "pt",
    "brazilian": "pt-br",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "turkish": "tr",
    "vietnamese": "vi",
    "indonesian": "id",
    "thai": "th",
    "arabic": "ar",
    "hindi": "hi",
    "czech": "cs",
    "hungarian": "hu",
    "romanian": "ro",
    "dutch": "nl",
    "swedish": "sv",
    "finnish": "fi",
    "bulgarian": "bg",
    "greek": "el",
    "persian": "fa",
    "hebrew": "he",
    "catalan": "ca",
    "tagalog": "tl",
}

# Language names in square brackets inside gallery titles.
TITLE_LANGUAGE_NAMES = {
    "english": "en",
    "russian": "ru",
    "russkiy": "ru",
    "ukrainian": "uk",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "polish": "pl",
    "spanish": "es",
    "portuguese": "pt",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "turkish": "tr",
    "vietnamese": "vi",
    "indonesian": "id",
    "thai": "th",
    "arabic": "ar",
    "hindi": "hi",
    "czech": "cs",
    "hungarian": "hu",
    "romanian": "ro",
    "dutch": "nl",
    "swedish": "sv",
    "finnish": "fi",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code.upper() if len(code) <= 3 else code.title())


def language_from_name(name: str) -> str:
    return LANGUAGE_CODES.get(name.strip().lower(), "")


@dataclass(frozen=True, slots=True)
class Tag:
    """A single unified tag. ``id`` is a normalised slug shared by sources."""

    id: str
    name: str
    group: str = "theme"
    description: str = ""
    source_ids: dict[str, str] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.id


@dataclass(slots=True)
class SearchQuery:
    title: str = ""
    author: str = ""
    tags: list[Tag] = field(default_factory=list)
    excluded_tags: list[Tag] = field(default_factory=list)
    tag_mode: str = "AND"
    ratings: list[str] = field(default_factory=lambda: list(CONTENT_RATINGS))
    languages: list[str] = field(default_factory=lambda: ["ru", "en"])
    sort: str = "relevance"
    limit: int = 24
    offset: int = 0
    sources: list[str] = field(default_factory=list)

    @property
    def has_filters(self) -> bool:
        return bool(self.title.strip() or self.author.strip() or self.tags)


@dataclass(slots=True)
class Manga:
    id: str
    title: str
    alt_titles: list[str] = field(default_factory=list)
    description: str = ""
    status: str = ""
    year: int | None = None
    content_rating: str = ""
    demographic: str | None = None
    tags: list[Tag] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    cover_file: str | None = None
    original_language: str = ""
    links: dict[str, str] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    source: str = "mangadex"
    source_name: str = "MangaDex"
    url_text: str = ""
    cover_url_text: str = ""
    page_count: int | None = None

    @property
    def key(self) -> str:
        """Unique key across all sources."""
        return f"{self.source}:{self.id}"

    @property
    def url(self) -> str:
        if self.url_text:
            return self.url_text
        if self.source == "mangadex":
            return MANGA_URL.format(manga_id=self.id)
        return ""

    @property
    def cover_url(self) -> str | None:
        return self.cover_url_for(256)

    def cover_url_for(self, size: int) -> str | None:
        if self.cover_url_text:
            return self.cover_url_text
        if self.source != "mangadex" or not self.cover_file:
            return None
        return COVER_URL.format(manga_id=self.id, file=self.cover_file, size=size)

    @property
    def tag_names(self) -> list[str]:
        return [t.name for t in self.tags]

    @property
    def author_label(self) -> str:
        return ", ".join(self.authors[:3]) or ", ".join(self.artists[:3])


@dataclass(slots=True)
class Chapter:
    id: str
    chapter: str | None
    volume: str | None
    title: str | None
    language: str
    pages: int
    external_url: str | None = None
    group: str | None = None
    readable_at: str | None = None
    page_urls: list[str] = field(default_factory=list)
    gallery: bool = False

    @property
    def page_total(self) -> int:
        return max(len(self.page_urls), self.pages)

    @property
    def downloadable(self) -> bool:
        return bool(self.page_total) and not self.external_url

    @property
    def label(self) -> str:
        if self.gallery:
            return self.title or "gallery"
        parts = []
        if self.volume:
            parts.append(f"vol. {self.volume}")
        parts.append(f"ch. {self.chapter}" if self.chapter else "no number")
        return " ".join(parts)


@dataclass(slots=True)
class Statistics:
    rating: float | None = None
    rating_count: int = 0
    follows: int = 0
    comments: int = 0
