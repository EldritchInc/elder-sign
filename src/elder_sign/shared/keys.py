"""The keys that let Elder Sign recognize a comment it has already judged.

A swarm copy-pastes, and dodges filters as it does: `tr4nny`, `t r a n n y`,
`TRANNYYYY`, Cyrillic look-alikes, zero-width spaces, mathematical bold.
Normalization folds all of those into one spelling, so they hash to one key
and are judged once.

Each comment gets four keys:

- `text`: its normalized text, or none when it has no words;
- `media`: one per GIF, sticker or image, from the provider's own ID where
  there is one, so the same GIF is the same key wherever it is posted;
- `content`: its text and media together, for a verdict that holds wherever
  the comment appears;
- `context`: its content in reply to its parent's content, for a verdict that
  holds only in that thread, like "lol exactly".

A key is a short prefix and a SHA-256 digest, so the state file never keeps
what a troll wrote.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

ZERO_WIDTH: Final = frozenset("​‌‍‎‏⁠⁡⁢⁣⁤﻿")
"""Characters that render as nothing, used to break up a word a filter would catch."""

HOMOGLYPHS: Final[Mapping[str, str]] = {
    # Cyrillic
    "а": "a",
    "в": "b",
    "е": "e",
    "ё": "e",
    "к": "k",
    "м": "m",
    "н": "h",
    "о": "o",
    "р": "p",
    "с": "c",
    "т": "t",
    "у": "y",
    "х": "x",
    "і": "i",
    "ї": "i",
    "ј": "j",
    "ѕ": "s",
    "ԁ": "d",
    "ԛ": "q",
    "ԝ": "w",
    "ɡ": "g",
    # Greek
    "α": "a",
    "β": "b",
    "ε": "e",
    "η": "n",
    "ι": "i",
    "κ": "k",
    "ν": "v",
    "ο": "o",
    "ρ": "p",
    "τ": "t",
    "υ": "u",
    "χ": "x",
    "ω": "w",
    # Latin look-alikes
    "ı": "i",
    "ł": "l",
    "ø": "o",
    "đ": "d",
    "ß": "ss",
}
"""Letters from other scripts that look like Latin letters, as the Latin letter they imitate."""

LEET: Final[Mapping[str, str]] = {
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "8": "b",
    "9": "g",
    "@": "a",
    "$": "s",
    "!": "i",
    "|": "l",
    "+": "t",
}
"""Digits and symbols written for letters, folded only inside a word that has a letter."""

REPEATS: Final = re.compile(r"(.)\1{2,}")
"""Three or more of one character, squeezed to one: `yyyy` and `!!!` but not `oo`."""

PHRASE_BREAK: Final = re.compile(r"\s{2,}|\n")
"""A wider gap, or a new line, ends a word spelled out letter by letter: `y o u  w i l l`."""

DIGEST_LENGTH: Final = 32
"""How many hex digits of a SHA-256 digest a key keeps: 128 bits."""

GIPHY_MEDIA: Final = re.compile(r"/media/(?:v1\.[^/]+/)?([A-Za-z0-9]+)/")
"""A Giphy media URL: `media.giphy.com/media/<id>/giphy.gif`."""

GIPHY_PAGE: Final = re.compile(r"/gifs/(?:[^/]*-)?([A-Za-z0-9]+)/?$")
"""A Giphy page URL: `giphy.com/gifs/<words>-<id>`."""

TENOR_MEDIA: Final = re.compile(r"^/([A-Za-z0-9_-]+?)(?:AAAA[A-Za-z])?/")
"""A Tenor media URL: `media.tenor.com/<id><rendition>/<name>.gif`, every rendition one GIF."""


def _digest(
    *parts: str,
) -> str:
    """Return the shortened SHA-256 digest of some parts, kept apart from each other."""

    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def _is_dropped(
    character: str,
) -> bool:
    """Return whether a character is punctuation or a non-emoji symbol, which keys ignore."""

    category = unicodedata.category(character)

    return category.startswith("P") or category in ("Sm", "Sc", "Sk")


LEADING_LEET: Final = frozenset("@$")
"""Symbols that stand for a letter even at the start of a word: `@ss`, `$hit`."""


def _fold_token(
    token: str,
) -> str:
    """Fold one whitespace-separated token: look-alikes, then leetspeak, then punctuation.

    Punctuation at the end of a word is emphasis, not a letter, so `tranny!!!`
    is `tranny`, not `trannyi`; at the start, only `@` and `$` are letters.
    """

    folded = "".join(HOMOGLYPHS.get(character, character) for character in token)
    end = len(folded)

    while end > 0 and _is_dropped(folded[end - 1]):
        end -= 1

    start = 0

    while start < end and _is_dropped(folded[start]) and folded[start] not in LEADING_LEET:
        start += 1

    folded = folded[start:end]

    if any(character.isalpha() for character in folded):
        folded = "".join(LEET.get(character, character) for character in folded)

    return "".join(character for character in folded if not _is_dropped(character))


def _join_spelled_out(
    tokens: Sequence[str],
) -> list[str]:
    """Join runs of three or more single letters, so `t r a n n y` is one word.

    A run is joined within one phrase: a wider gap ends it, so `y o u  w i l l`
    is two words. Letters spelled out with single spaces throughout cannot be
    told apart into words, and are joined into one.
    """

    joined: list[str] = []
    run: list[str] = []

    for token in (*tokens, ""):
        if len(token) == 1 and token.isalpha():
            run.append(token)
            continue

        if len(run) >= 3:
            joined.append("".join(run))
        else:
            joined.extend(run)

        run = []

        if token:
            joined.append(token)

    return joined


def normalize_text(
    text: str,
    mentions: Iterable[str] = (),
) -> str:
    """Fold a comment's text into the one spelling every disguise of it shares.

    The names it mentions are removed first, so the same reply to two people
    is one comment.
    """

    for mention in sorted(set(mentions), key=len, reverse=True):
        if mention.strip():
            text = text.replace(mention, " ")

    text = unicodedata.normalize("NFKC", text)
    text = "".join(character for character in text if character not in ZERO_WIDTH)
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(character for character in text if unicodedata.category(character) != "Mn")

    words: list[str] = []

    for phrase in PHRASE_BREAK.split(text.strip()):
        tokens = [_fold_token(token) for token in phrase.split()]
        words.extend(_join_spelled_out([REPEATS.sub(r"\1", token) for token in tokens if token]))

    return " ".join(words)


def text_key(
    normalized: str,
) -> str | None:
    """Return the key of normalized text, or none when it has no words."""

    return f"t:{_digest(normalized)}" if normalized else None


def media_key(
    media: Mapping[str, Any],
) -> str | None:
    """Return the key of one GIF, sticker or image, or none when nothing identifies it.

    The provider's own ID is used when the platform gives one; otherwise it is
    read from a Giphy or Tenor URL; otherwise the URL's path, without its host
    or query, which on a CDN vary for the same file.
    """

    provider = str(media.get("provider") or "").strip().lower()
    provider_id = str(media.get("provider_id") or "").strip()

    if provider and provider_id:
        return f"m:{provider}:{provider_id}"

    url = str(media.get("url") or "").strip()

    if not url:
        return None

    parts = urlsplit(url)
    host = parts.netloc.lower()

    if host == "giphy.com" or host.endswith(".giphy.com"):
        found = GIPHY_MEDIA.search(parts.path) or GIPHY_PAGE.search(parts.path)

        if found:
            return f"m:giphy:{found.group(1)}"

    if host == "media.tenor.com" or host.endswith(".tenor.com"):
        found = TENOR_MEDIA.search(parts.path)

        if found:
            return f"m:tenor:{found.group(1)}"

    return f"m:url:{_digest(parts.path)}" if parts.path.strip("/") else None


def content_key(
    text: str | None,
    media: Sequence[str],
) -> str | None:
    """Return the key of a comment's text and media together, or none when it has neither."""

    if text is None and not media:
        return None

    return f"c:{_digest(text or '', *sorted(media))}"


def context_key(
    content: str | None,
    parent_content: str | None,
    *,
    top_level: bool,
) -> str | None:
    """Return the key of a comment's content in reply to its parent's.

    A top-level comment replies to the post. A reply whose parent's content
    is unknown has no context key: two such replies may answer different
    things.
    """

    if content is None:
        return None

    if top_level:
        return f"x:{_digest(content, 'post')}"

    if parent_content is None:
        return None

    return f"x:{_digest(content, parent_content)}"


PREFIX_WILDCARD: Final = "*"
"""A blocked term ending in `*` matches any word its last word begins: `trann*`."""


def term_matches(
    normalized: str,
    term: str,
) -> bool:
    """Return whether a blocked term appears in normalized text, as whole words.

    The term is normalized as the text was, so a term written `Tr4nny` blocks
    every disguise of it. A term matches only whole words, so blocking one
    word never blocks a longer word that happens to contain it, unless the
    term ends in `*`.
    """

    prefix = term.rstrip().endswith(PREFIX_WILDCARD)
    wanted = normalize_text(term.rstrip().rstrip(PREFIX_WILDCARD)).split()
    words = normalized.split()

    if not wanted:
        return False

    for start in range(len(words) - len(wanted) + 1):
        window = words[start : start + len(wanted)]

        if window[:-1] != wanted[:-1]:
            continue

        last, expected = window[-1], wanted[-1]

        if last == expected or (prefix and last.startswith(expected)):
            return True

    return False
