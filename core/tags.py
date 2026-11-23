"""Unified tag catalogue: MangaDex + nhentai + a curated extra set.

Genres, themes, formats and labels are all tags. The slug (``id``) is shared
between sources, while ``source_ids`` keeps native identifiers so each source
can receive a query it understands.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .models import TAG_GROUP_ORDER, Manga, Tag

# nhentai tags that sexualise minors never enter the catalogue. Tags supplied
# by MangaDex itself are not removed: they are source data.
NHENTAI_BLOCKLIST = {
    "loli",
    "lolicon",
    "lolita",
    "shotacon",
    "shota",
    "toddlercon",
    "underage",
    "underaged",
    "child",
    "children",
    "childhood",
    "oppai-loli",
    "loli-only",
    "shota-only",
}

# Gay / boys' love tags used by the "block gay content" setting.
GAY_TAGS = {
    "yaoi",
    "boys-love",
    "boy-s-love",
    "bara",
    "shounen-ai",
    "shonen-ai",
    "male-on-male",
    "males-only",
    "gay",
    "homosexual",
    "frotting",
    "omegaverse",
    "mpreg",
}

# Short descriptions shown in tag tooltips. Longer source descriptions win.
TAG_DESCRIPTIONS: dict[str, str] = {
    "action": "Action-focused stories with fights, chases and high stakes.",
    "adventure": "Travel, exploration and quest-driven plots.",
    "comedy": "Humorous stories built around gags and funny situations.",
    "drama": "Emotional stories driven by conflict and relationships.",
    "fantasy": "Magic, imaginary worlds and supernatural settings.",
    "historical": "Stories set in the past or inspired by history.",
    "horror": "Scary stories with monsters, dread or gore.",
    "isekai": "A character is transported to or reborn in another world.",
    "mecha": "Piloted robots and mechanical warfare.",
    "mystery": "Puzzles, investigations and hidden truths.",
    "psychological": "Stories focused on mental states and unreliable reality.",
    "romance": "Love stories and romantic relationships.",
    "sci-fi": "Futuristic technology, space and speculative science.",
    "slice-of-life": "Quiet everyday stories without a large plot.",
    "sports": "Competitive sports and training stories.",
    "supernatural": "Ghosts, spirits, gods and unexplained powers.",
    "thriller": "Suspense, danger and tense pacing.",
    "tragedy": "Stories with painful or downbeat outcomes.",
    "smut": "Explicit sexual content is central to the story.",
    "mature": "Themes intended for adult readers.",
    "school-life": "Stories set in and around school.",
    "villainess": "A noblewoman, often reincarnated, changes her fate.",
    "ahegao": "Exaggerated ecstatic facial expressions during sex.",
    "anal": "Anal intercourse scenes.",
    "bdsm": "Bondage, discipline, dominance and submission.",
    "big-breasts": "Characters with large breasts.",
    "big-penis": "Characters with a large penis.",
    "bikini": "Bikini swimwear focus.",
    "blowjob": "Oral sex performed on a penis.",
    "bondage": "Rope, restraints and restriction play.",
    "bukkake": "Multiple characters ejaculating on one character.",
    "catgirl": "A girl with cat ears and/or a tail.",
    "cheating": "Infidelity and extramarital relationships.",
    "cosplay": "Sex while wearing costume or character outfits.",
    "crossdressing": "Characters wearing clothes of another gender.",
    "cunnilingus": "Oral sex performed on a vulva.",
    "dark-skin": "Characters with dark skin.",
    "defloration": "First sexual experience, often losing virginity.",
    "demon-girl": "A female demon character.",
    "double-penetration": "Two simultaneous penetrations.",
    "elf": "Elf characters, usually with pointed ears.",
    "exhibitionism": "Public or semi-public sexual activity.",
    "facial": "Ejaculation onto the face.",
    "femdom": "A dominant female partner.",
    "fingering": "Manual stimulation with fingers.",
    "footjob": "Sexual stimulation using the feet.",
    "futanari": "Female characters with a penis.",
    "gender-bender": "Characters change gender or live as another gender.",
    "glasses": "Characters wearing glasses.",
    "group-sex": "Three or more participants in a scene.",
    "harem": "One protagonist surrounded by many romantic interests.",
    "handjob": "Manual stimulation of a penis.",
    "incest": "Sexual relationships between relatives.",
    "lactation": "Milk production and breastfeeding themes.",
    "lingerie": "Focus on underwear and lingerie.",
    "maid": "Maid characters and maid outfits.",
    "masturbation": "Solo sexual stimulation.",
    "milf": "Attractive older women, often mothers.",
    "mind-break": "A character's mind is broken by pleasure or abuse.",
    "mind-control": "Characters are controlled against their will.",
    "monster-girl": "A female monster character.",
    "nakadashi": "Ejaculation inside the body.",
    "netorare": "A partner is taken by someone else (NTR).",
    "nun": "Nun characters and religious outfits.",
    "oral": "Oral sex scenes.",
    "orgy": "A large group sex scene.",
    "paizuri": "Sexual stimulation using the breasts.",
    "pregnant": "Pregnancy and impregnation themes.",
    "prostitution": "Paid sexual services.",
    "rape": "Non-consensual sexual content (dark theme).",
    "schoolgirl-uniform": "Sailor-style or school uniforms.",
    "stockings": "Stockings, tights and thigh-highs.",
    "succubus": "A demon that feeds on sexual energy.",
    "swimsuit": "Swimwear focus, often at a pool or beach.",
    "tanlines": "Visible tan lines on the body.",
    "tattoo": "Tattooed characters.",
    "teacher": "Teacher characters and student-teacher dynamics.",
    "tentacles": "Tentacle-based sexual content.",
    "tomboy": "A girl with boyish style or behaviour.",
    "trap": "A feminine-presenting male character.",
    "tsundere": "A character who is prickly then affectionate.",
    "uncensored": "Genitals are shown without censorship.",
    "virginity": "First-time and virginity themes.",
    "x-ray": "Internal anatomy shown through the body.",
    "yandere": "An obsessive and dangerous lover.",
    "yaoi": "Male-male romantic and sexual content.",
    "yuri": "Female-female romantic and sexual content.",
    "anthology": "A collection of separate short works.",
    "artbook": "Illustrations rather than a story.",
    "doujinshi": "Self-published fan or original work.",
    "full-color": "The whole work is in colour.",
    "game-cg": "Computer graphics extracted from a game.",
    "imageset": "A set of unrelated images.",
    "manhwa": "Korean comic format, usually full colour.",
    "oneshot": "A single self-contained chapter.",
    "webtoon": "Vertical-scrolling web comic format.",
    "western": "Western comic art style.",
    "novel": "Text-based work with illustrations.",
    "4-koma": "Four-panel comic strip format.",
    "mosaic-censorship": "Genitals are censored with a mosaic pattern.",
    "decensored": "Censorship removed from the original release.",
    "translated": "Fan-translated from another language.",
    "sample": "A short preview rather than the full work.",
}

# Alternative tag names that resolve to the same canonical tag.
# When a user searches for any alias, the canonical tag is shown,
# but the search uses all aliases across sites.
TAG_ALIASES: dict[str, list[str]] = {
    "big-breasts": [
        "heavy-tits", "enormous-tits", "huge-tits", "large-breasts",
        "big-tits", "massive-tits", "giant-breasts", "huge-breasts",
    ],
    "big-penis": [
        "huge-penis", "large-penis", "massive-penis", "big-dick",
        "huge-dick", "large-dick",
    ],
    "uncensored": [
        "decensored", "no-censor", "no-censorship", "uncensored",
    ],
    "small-breasts": [
        "small-tits", "tiny-tits", "flat-chest", "petite-breasts",
    ],
    "group-sex": [
        "orgy", "group", "threesome", "foursome", "gangbang",
    ],
    "oral": [
        "blowjob", "cunnilingus", "oral-sex", "fellatio",
    ],
    "anal": [
        "anal-sex", "anal-intercourse", "anal-play",
    ],
    "bdsm": [
        "bondage", "s-m", "sadomasochism", "domination",
    ],
    "milf": [
        "mature-woman", "mother", "mom", "mature",
    ],
    "futanari": [
        "futa", "hermaphrodite", "futanari",
    ],
    "incest": [
        "incestuous", "family", "sibling", "brother-sister",
    ],
    "tentacles": [
        "tentacle", "tentacle-sex", "tentacle-rape",
    ],
    "yaoi": [
        "boys-love", "shounen-ai", "male-male", "bl",
    ],
    "yuri": [
        "girls-love", "shoujo-ai", "female-female", "gl",
    ],
    "pregnant": [
        "pregnancy", "impregnation", "knocked-up",
    ],
    "lactation": [
        "breastfeeding", "milk", "lactating",
    ],
    "cosplay": [
        "costume", "cosplaying", "roleplay",
    ],
    "masturbation": [
        "masturbating", "self-pleasure", "solo",
    ],
    "femdom": [
        "female-domination", "dominant-female",
    ],
    "netorare": [
        "ntr", "cuckold", "netori",
    ],
    "gyaru": [
        "gal", "gyaru-oh", "gyaru-girl",
    ],
    "maid": [
        "maids", "maid-outfit", "maid-cafe",
    ],
    "nurse": [
        "nurses", "nurse-outfit", "medical",
    ],
    "teacher": [
        "teachers", "school-teacher", "professor",
    ],
    "schoolgirl": [
        "school-girl", "school-uniform", "student",
    ],
    "swimsuit": [
        "bikini", "swimwear", "beach",
    ],
    "lingerie": [
        "underwear", "bra", "panties",
    ],
    "stockings": [
        "thigh-highs", "pantyhose", "tights",
    ],
    "glasses": [
        "spectacles", "eyewear",
    ],
    "harem": [
        "polygamy", "multiple-partners",
    ],
    "isekai": [
        "another-world", "reincarnation", "transported",
    ],
    "fantasy": [
        "magical", "swords-and-sorcery",
    ],
    "sci-fi": [
        "science-fiction", "scifi", "futuristic",
    ],
    "horror": [
        "scary", "terrifying", "gore",
    ],
    "comedy": [
        "funny", "humorous", "gag",
    ],
    "drama": [
        "emotional", "serious",
    ],
    "romance": [
        "love", "romantic",
    ],
    "action": [
        "fighting", "battle",
    ],
    "adventure": [
        "journey", "quest",
    ],
    "mystery": [
        "detective", "investigation",
    ],
    "psychological": [
        "mind", "mental",
    ],
    "supernatural": [
        "ghost", "spirit", "paranormal",
    ],
    "slice-of-life": [
        "everyday", "daily-life",
    ],
    "ecchi": [
        "suggestive", "fanservice",
    ],
    "yandere": [
        "obsessive", "possessive",
    ],
    "tsundere": [
        "prickly", "cold-then-warm",
    ],
    "trap": [
        "crossdressing", "feminine-male",
    ],
    "tomboy": [
        "boyish-girl", "masculine-female",
    ],
    "succubus": [
        "demon-girl", "sex-demon",
    ],
    "vampire": [
        "vamp", "bloodsucker",
    ],
    "elf": [
        "elves", "pointed-ears",
    ],
    "monster-girl": [
        "monster", "creature-girl",
    ],
    "catgirl": [
        "neko", "cat-ears", "cat-tail",
    ],
    "demon": [
        "devil", "demonic",
    ],
    "angel": [
        "winged", "celestial",
    ],
    "dragon": [
        "wyrm", "drake",
    ],
    "fox-girl": [
        "kitsune", "fox-ears",
    ],
    "wolf-girl": [
        "wolf-ears", "wolf-tail",
    ],
    "bunny-girl": [
        "rabbit-ears", "bunny",
    ],
    "maid": [
        "maids", "maid-outfit",
    ],
    "nurse": [
        "nurses", "medical",
    ],
    "teacher": [
        "teachers", "professor",
    ],
    "schoolgirl": [
        "school-girl", "student",
    ],
    "swimsuit": [
        "bikini", "swimwear",
    ],
    "lingerie": [
        "underwear", "bra",
    ],
    "stockings": [
        "thigh-highs", "pantyhose",
    ],
    "glasses": [
        "spectacles", "eyewear",
    ],
    "harem": [
        "polygamy", "multiple-partners",
    ],
    "isekai": [
        "another-world", "reincarnation",
    ],
    "fantasy": [
        "magical", "swords-and-sorcery",
    ],
    "sci-fi": [
        "science-fiction", "scifi",
    ],
    "horror": [
        "scary", "terrifying",
    ],
    "comedy": [
        "funny", "humorous",
    ],
    "drama": [
        "emotional", "serious",
    ],
    "romance": [
        "love", "romantic",
    ],
    "action": [
        "fighting", "battle",
    ],
    "adventure": [
        "journey", "quest",
    ],
    "mystery": [
        "detective", "investigation",
    ],
    "psychological": [
        "mind", "mental",
    ],
    "supernatural": [
        "ghost", "spirit",
    ],
    "slice-of-life": [
        "everyday", "daily-life",
    ],
    "ecchi": [
        "suggestive", "fanservice",
    ],
    "yandere": [
        "obsessive", "possessive",
    ],
    "tsundere": [
        "prickly", "cold-then-warm",
    ],
    "trap": [
        "crossdressing", "feminine-male",
    ],
    "tomboy": [
        "boyish-girl", "masculine-female",
    ],
    "succubus": [
        "demon-girl", "sex-demon",
    ],
    "vampire": [
        "vamp", "bloodsucker",
    ],
    "elf": [
        "elves", "pointed-ears",
    ],
    "monster-girl": [
        "monster", "creature-girl",
    ],
    "catgirl": [
        "neko", "cat-ears",
    ],
    "demon": [
        "devil", "demonic",
    ],
    "angel": [
        "winged", "celestial",
    ],
    "dragon": [
        "wyrm", "drake",
    ],
}

# Reverse mapping: alias -> canonical
ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in TAG_ALIASES.items():
    ALIAS_TO_CANONICAL[canonical] = canonical
    for alias in aliases:
        ALIAS_TO_CANONICAL[alias] = canonical

# Canonical tag names (human-readable).
TAG_CANONICAL_NAMES: dict[str, str] = {
    "big-breasts": "Big Tits",
    "big-penis": "Big Penis",
    "uncensored": "Uncensored",
    "small-breasts": "Small Tits",
    "group-sex": "Group Sex",
    "oral": "Oral",
    "anal": "Anal",
    "bdsm": "BDSM",
    "milf": "MILF",
    "futanari": "Futanari",
    "incest": "Incest",
    "tentacles": "Tentacles",
    "yaoi": "Yaoi",
    "yuri": "Yuri",
    "pregnant": "Pregnant",
    "lactation": "Lactation",
    "cosplay": "Cosplay",
    "masturbation": "Masturbation",
    "femdom": "Femdom",
    "netorare": "Netorare",
    "gyaru": "Gyaru",
    "maid": "Maid",
    "nurse": "Nurse",
    "teacher": "Teacher",
    "schoolgirl": "Schoolgirl",
    "swimsuit": "Swimsuit",
    "lingerie": "Lingerie",
    "stockings": "Stockings",
    "glasses": "Glasses",
    "harem": "Harem",
    "isekai": "Isekai",
    "fantasy": "Fantasy",
    "sci-fi": "Sci-Fi",
    "horror": "Horror",
    "comedy": "Comedy",
    "drama": "Drama",
    "romance": "Romance",
    "action": "Action",
    "adventure": "Adventure",
    "mystery": "Mystery",
    "psychological": "Psychological",
    "supernatural": "Supernatural",
    "slice-of-life": "Slice of Life",
    "ecchi": "Ecchi",
    "yandere": "Yandere",
    "tsundere": "Tsundere",
    "trap": "Trap",
    "tomboy": "Tomboy",
    "succubus": "Succubus",
    "vampire": "Vampire",
    "elf": "Elf",
    "monster-girl": "Monster Girl",
    "catgirl": "Catgirl",
    "demon": "Demon",
    "angel": "Angel",
    "dragon": "Dragon",
    "fox-girl": "Fox Girl",
    "wolf-girl": "Wolf Girl",
    "bunny-girl": "Bunny Girl",
}


def resolve_tag_alias(name_or_slug: str) -> str:
    """Resolve an alternative tag name to its canonical slug."""
    slug = slugify(name_or_slug)
    return ALIAS_TO_CANONICAL.get(slug, slug)


def get_tag_aliases(slug: str) -> list[str]:
    """Get all aliases (including the canonical) for a tag slug."""
    canonical = ALIAS_TO_CANONICAL.get(slug, slug)
    aliases = [canonical] + TAG_ALIASES.get(canonical, [])
    return list(dict.fromkeys(aliases))  # deduplicate while preserving order


# Additional tags that may be missing from the nhentai popularity pages.
EXTRA_TAGS: list[tuple[str, str]] = [
    # genres
    ("action", "genre"),
    ("adult", "genre"),
    ("adventure", "genre"),
    ("boys-love", "genre"),
    ("comedy", "genre"),
    ("crime", "genre"),
    ("cyberpunk", "genre"),
    ("detective", "genre"),
    ("drama", "genre"),
    ("dungeon", "genre"),
    ("fantasy", "genre"),
    ("game", "genre"),
    ("girls-love", "genre"),
    ("historical", "genre"),
    ("horror", "genre"),
    ("idol", "genre"),
    ("isekai", "genre"),
    ("martial-arts", "genre"),
    ("mature", "genre"),
    ("mecha", "genre"),
    ("medical", "genre"),
    ("military", "genre"),
    ("music", "genre"),
    ("mystery", "genre"),
    ("office-workers", "genre"),
    ("parody", "genre"),
    ("post-apocalyptic", "genre"),
    ("psychological", "genre"),
    ("reincarnation", "genre"),
    ("reverse-harem", "genre"),
    ("romance", "genre"),
    ("samurai", "genre"),
    ("school-life", "genre"),
    ("sci-fi", "genre"),
    ("slice-of-life", "genre"),
    ("smut", "genre"),
    ("space", "genre"),
    ("sports", "genre"),
    ("supernatural", "genre"),
    ("survival", "genre"),
    ("thriller", "genre"),
    ("time-travel", "genre"),
    ("tragedy", "genre"),
    ("vampire", "genre"),
    ("villainess", "genre"),
    ("wuxia", "genre"),
    ("xianxia", "genre"),
    ("zombie", "genre"),

    # themes
    ("ahegao", "theme"),
    ("age-gap", "theme"),
    ("age-progression", "theme"),
    ("alien", "theme"),
    ("all-the-way-through", "theme"),
    ("anal", "theme"),
    ("anal-intercourse", "theme"),
    ("anilingus", "theme"),
    ("apron", "theme"),
    ("armpit", "theme"),
    ("ass-to-mouth", "theme"),
    ("bathing", "theme"),
    ("beach", "theme"),
    ("bdsm", "theme"),
    ("big-breasts", "theme"),
    ("big-penis", "theme"),
    ("bikini", "theme"),
    ("blindfold", "theme"),
    ("blood", "theme"),
    ("blowjob", "theme"),
    ("body-mods", "theme"),
    ("body-swap", "theme"),
    ("bondage", "theme"),
    ("breast-expansion", "theme"),
    ("breast-sucking", "theme"),
    ("brocon", "theme"),
    ("bukkake", "theme"),
    ("catgirl", "theme"),
    ("cervix-penetration", "theme"),
    ("chastity-belt", "theme"),
    ("cheating", "theme"),
    ("chubby", "theme"),
    ("clit-growth", "theme"),
    ("clothed-sex", "theme"),
    ("clothing-damage", "theme"),
    ("condom", "theme"),
    ("corruption", "theme"),
    ("cosplay", "theme"),
    ("cousins", "theme"),
    ("cowgirl", "theme"),
    ("crossdressing", "theme"),
    ("cum-inflation", "theme"),
    ("cum-swapping", "theme"),
    ("cunnilingus", "theme"),
    ("dark-skin", "theme"),
    ("deep-throat", "theme"),
    ("defloration", "theme"),
    ("demon", "theme"),
    ("demon-girl", "theme"),
    ("dick-growth", "theme"),
    ("dilf", "theme"),
    ("double-anal", "theme"),
    ("double-penetration", "theme"),
    ("double-vaginal", "theme"),
    ("drunk", "theme"),
    ("drunk-sex", "theme"),
    ("egg-laying", "theme"),
    ("elf", "theme"),
    ("enema", "theme"),
    ("exhibitionism", "theme"),
    ("facial", "theme"),
    ("facesitting", "theme"),
    ("femdom", "theme"),
    ("feminization", "theme"),
    ("fingering", "theme"),
    ("fisting", "theme"),
    ("foot-licking", "theme"),
    ("footjob", "theme"),
    ("frotting", "theme"),
    ("futanari", "theme"),
    ("gag", "theme"),
    ("garter-belt", "theme"),
    ("gender-bender", "theme"),
    ("glasses", "theme"),
    ("glory-hole", "theme"),
    ("gloves", "theme"),
    ("groping", "theme"),
    ("group-sex", "theme"),
    ("gyaru", "theme"),
    ("hair-job", "theme"),
    ("hairy", "theme"),
    ("hairy-armpits", "theme"),
    ("hairy-pussy", "theme"),
    ("handjob", "theme"),
    ("harem", "theme"),
    ("hidden-sex", "theme"),
    ("hijab", "theme"),
    ("horns", "theme"),
    ("hotpants", "theme"),
    ("human-pet", "theme"),
    ("humiliation", "theme"),
    ("impregnation", "theme"),
    ("incest", "theme"),
    ("inflation", "theme"),
    ("invisible", "theme"),
    ("kemonomimi", "theme"),
    ("kimono", "theme"),
    ("kissing", "theme"),
    ("lab-coat", "theme"),
    ("lactation", "theme"),
    ("large-insertions", "theme"),
    ("latex", "theme"),
    ("legs", "theme"),
    ("lingerie", "theme"),
    ("lotion", "theme"),
    ("machine", "theme"),
    ("magical-girl", "theme"),
    ("maid", "theme"),
    ("male-domination", "theme"),
    ("masturbation", "theme"),
    ("mermaid", "theme"),
    ("mesugaki", "theme"),
    ("milf", "theme"),
    ("mind-break", "theme"),
    ("mind-control", "theme"),
    ("monster", "theme"),
    ("monster-girl", "theme"),
    ("mother", "theme"),
    ("multiple-paizuri", "theme"),
    ("nakadashi", "theme"),
    ("netorare", "theme"),
    ("netori", "theme"),
    ("nun", "theme"),
    ("nurse", "theme"),
    ("office-lady", "theme"),
    ("oil", "theme"),
    ("old-man", "theme"),
    ("omegaverse", "theme"),
    ("oni", "theme"),
    ("oppai", "theme"),
    ("orgy", "theme"),
    ("osananajimi", "theme"),
    ("paizuri", "theme"),
    ("pantyhose", "theme"),
    ("petplay", "theme"),
    ("piercing", "theme"),
    ("pregnant", "theme"),
    ("prostitution", "theme"),
    ("pubic-striptease", "theme"),
    ("rape", "theme"),
    ("rimjob", "theme"),
    ("ryona", "theme"),
    ("school", "theme"),
    ("schoolgirl-uniform", "theme"),
    ("sex-toys", "theme"),
    ("shibari", "theme"),
    ("short-hair", "theme"),
    ("shrinking", "theme"),
    ("sister", "theme"),
    ("sleeping", "theme"),
    ("small-breasts", "theme"),
    ("smoking", "theme"),
    ("sneakers", "theme"),
    ("sole-female", "theme"),
    ("sole-male", "theme"),
    ("solo-action", "theme"),
    ("spanking", "theme"),
    ("spread-pussy", "theme"),
    ("squirting", "theme"),
    ("stockings", "theme"),
    ("stomach-deformation", "theme"),
    ("strategic-censorship", "theme"),
    ("stuck-in-wall", "theme"),
    ("succubus", "theme"),
    ("sundress", "theme"),
    ("swimsuit", "theme"),
    ("tail", "theme"),
    ("tanlines", "theme"),
    ("tattoo", "theme"),
    ("teacher", "theme"),
    ("tentacles", "theme"),
    ("thigh-highs", "theme"),
    ("tickling", "theme"),
    ("tights", "theme"),
    ("tomboy", "theme"),
    ("tracksuit", "theme"),
    ("trap", "theme"),
    ("tribadism", "theme"),
    ("tsundere", "theme"),
    ("ugly-bastard", "theme"),
    ("uncensored", "theme"),
    ("uniform", "theme"),
    ("vampire", "theme"),
    ("virginity", "theme"),
    ("voyeurism", "theme"),
    ("wedding", "theme"),
    ("wet-clothes", "theme"),
    ("witch", "theme"),
    ("wolf-girl", "theme"),
    ("x-ray", "theme"),
    ("yandere", "theme"),
    ("yaoi", "theme"),
    ("yuri", "theme"),
    ("zettai-ryouiki", "theme"),

    # formats
    ("4-koma", "format"),
    ("anthology", "format"),
    ("artbook", "format"),
    ("cg-set", "format"),
    ("comic", "format"),
    ("doujinshi", "format"),
    ("full-color", "format"),
    ("game-cg", "format"),
    ("imageset", "format"),
    ("light-novel", "format"),
    ("magazine", "format"),
    ("manga", "format"),
    ("manhwa", "format"),
    ("novel", "format"),
    ("oneshot", "format"),
    ("short-story", "format"),
    ("webtoon", "format"),
    ("western", "format"),

    # content labels
    ("censored", "content"),
    ("decensored", "content"),
    ("mosaic-censorship", "content"),
    ("sample", "content"),
    ("speechless", "content"),
    ("translated", "content"),
]

_INVALID_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Normalise a tag name into the shared slug."""
    text = value.strip().lower()
    text = text.replace("&", " and ")
    text = text.replace("'", "").replace("’", "")
    text = _INVALID_SLUG.sub("-", text).strip("-")
    return text or "tag"


def is_blocked(slug: str) -> bool:
    if slug in NHENTAI_BLOCKLIST:
        return True
    parts = set(slug.split("-"))
    return bool(parts & {"loli", "lolicon", "shotacon", "shota", "toddlercon"})


def is_gay_tag(*values: str) -> bool:
    for value in values:
        slug = slugify(value)
        if slug in GAY_TAGS:
            return True
        if set(slug.split("-")) & {"yaoi", "bara"}:
            return True
    return False


def tag_matches(card_tag: Tag, wanted: Tag) -> bool:
    """True when a tag parsed from a result card is the requested tag."""
    if card_tag.id and card_tag.id == wanted.id:
        return True
    if card_tag.name and slugify(card_tag.name) == wanted.id:
        return True
    for key, value in wanted.source_ids.items():
        if value and card_tag.source_ids.get(key) == value:
            return True
    return False


def _card_native_tag_ids(manga: Manga) -> set[str] | None:
    """Native tag ids published on a card, or None when the card has none."""
    raw = manga.links.get("tag_ids")
    if raw is None:
        return None
    return {piece.strip() for piece in str(raw).split(",") if piece.strip()}


def card_satisfies_tags(manga: Manga, wanted: Iterable[Tag]) -> bool | None:
    """Check a search card against the selected tags.

    Returns ``True``/``False`` when the card exposes its tags, and ``None``
    when the source does not publish tags in the result list (checking then
    would require opening every title).
    """
    requested = [tag for tag in wanted if tag.id]
    if not requested:
        return True
    if manga.tags:
        return all(
            any(tag_matches(card, tag) for card in manga.tags)
            for tag in requested
        )
    native = _card_native_tag_ids(manga)
    if native is None:
        return None
    for tag in requested:
        value = tag.source_ids.get(manga.source)
        if not value:
            return None
        if str(value) not in native:
            return False
    return True


def tag_from_mangadex(
    native_id: str, name: str, group: str, description: str = ""
) -> Tag:
    slug = slugify(name)
    return Tag(
        id=slug,
        name=name or native_id,
        group=group or "theme",
        description=description or TAG_DESCRIPTIONS.get(slug, ""),
        source_ids={"mangadex": native_id},
    )


def tag_from_nhentai(item: dict[str, Any]) -> Tag | None:
    native_type = item.get("type") or "tag"
    if native_type not in ("tag", "category"):
        return None
    name = (item.get("name") or "").strip()
    slug = slugify(item.get("slug") or name)
    if not name or is_blocked(slug):
        return None
    return Tag(
        id=slug,
        name=name,
        group="format" if native_type == "category" else "theme",
        description=item.get("description")
        or TAG_DESCRIPTIONS.get(slug, ""),
        source_ids={"nhentai": str(item.get("id") or slug)},
    )


def extra_tags() -> list[Tag]:
    return [
        Tag(
            id=slug,
            name=slug.replace("-", " "),
            group=group,
            description=TAG_DESCRIPTIONS.get(slug, ""),
        )
        for slug, group in EXTRA_TAGS
        if not is_blocked(slug)
    ]


def merge_tags(*catalogs: Iterable[Tag]) -> list[Tag]:
    """Merge catalogues by slug while keeping native source identifiers."""
    merged: dict[str, Tag] = {}
    for catalog in catalogs:
        for tag in catalog:
            if not tag.id:
                continue
            # Resolve alias to canonical slug.
            canonical_id = resolve_tag_alias(tag.id)
            # Use canonical name if available.
            canonical_name = TAG_CANONICAL_NAMES.get(canonical_id)
            name = canonical_name if canonical_name else tag.name
            current = merged.get(canonical_id)
            if current is None:
                merged[canonical_id] = Tag(
                    id=canonical_id,
                    name=name,
                    group=tag.group,
                    description=tag.description,
                    source_ids=tag.source_ids,
                )
                continue
            source_ids = {**current.source_ids, **tag.source_ids}
            description = (
                current.description
                if len(current.description) >= len(tag.description)
                else tag.description
            )
            group = current.group or tag.group
            merged[canonical_id] = Tag(
                id=canonical_id,
                name=name,
                group=group,
                description=description,
                source_ids=source_ids,
            )
    return sorted(
        merged.values(),
        key=lambda tag: (
            TAG_GROUP_ORDER.get(tag.group, len(TAG_GROUP_ORDER)),
            tag.name.lower(),
        ),
    )


def catalog_to_data(tags: Iterable[Tag]) -> list[dict[str, Any]]:
    return [
        {
            "id": tag.id,
            "name": tag.name,
            "group": tag.group,
            "description": tag.description,
            "source_ids": tag.source_ids,
        }
        for tag in tags
    ]


def catalog_from_data(data: Iterable[dict[str, Any]]) -> list[Tag]:
    tags: list[Tag] = []
    for item in data:
        try:
            tag = Tag(
                id=str(item["id"]),
                name=str(item["name"]),
                group=str(item.get("group") or "theme"),
                description=str(item.get("description") or ""),
                source_ids={
                    str(key): str(value)
                    for key, value in (item.get("source_ids") or {}).items()
                },
            )
        except (KeyError, TypeError, ValueError):
            continue
        tags.append(tag)
    return tags


__all__ = [
    "EXTRA_TAGS",
    "GAY_TAGS",
    "NHENTAI_BLOCKLIST",
    "TAG_DESCRIPTIONS",
    "catalog_from_data",
    "catalog_to_data",
    "extra_tags",
    "is_blocked",
    "is_gay_tag",
    "merge_tags",
    "slugify",
    "tag_from_mangadex",
    "tag_from_nhentai",
]
