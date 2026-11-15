"""Make a capture safe to share: every name, word and ID replaced, every shape kept.

A capture holds real people's comments, names and account IDs. A parser
written from it needs none of those, only where they sit and how they relate.
Scrubbing keeps exactly that:

- **words** (a comment's text, a person's name, a caption) are masked letter
  by letter, `x` for a lowercase letter, `X` for an uppercase one and `0` for
  a digit, so they cannot be read back. Their length and spacing are kept, so
  a mention's offset into a comment still lands on the masked name.
- **IDs and other tokens** are replaced through a keyed hash drawn fresh for
  each scrub and then forgotten. The same ID becomes the same replacement
  everywhere, so a reply still names its parent, but no replacement can be
  traced back. A replacement keeps its token's length and its letters' and
  digits' places, so an ID still looks like one.
- **IDs Facebook hides in base64**, such as `feedback:<id>`, are decoded,
  replaced inside, and encoded again, so they match the bare ID elsewhere.
- **URLs** keep their scheme, host and file extension, and well-known path
  words, such as `media` on Giphy; every other path segment and every query
  value is replaced as a token. So is every word of a value under a key that
  addresses someone, such as `uri_token`, `vanity` or `profile_url`, because
  a chosen address is made of a name.
- **Structure** is kept as it is: keys, numbers, booleans, and words that are
  not under a key that holds words and have no digit in them, such as
  enums, and every value under a key Facebook marks as its own with `__`,
  such as `__typename`.

Pages are reduced to the JSON in their `<script>` tags that mentions comments;
their HTML is dropped. A GraphQL body is split into the JSON values it holds,
without the `for (;;);` Facebook may put before them.

Before anything is written, the scrubbed capture is searched for every word
masked and every long ID replaced; if any is still there, nothing is written.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Iterable, Iterator
from typing import Any, Final
from urllib.parse import quote, unquote

TEXT_KEYS: Final = frozenset(
    {
        "accessibility_caption",
        "alt_text",
        "alternate_name",
        "body",
        "caption",
        "description",
        "first_name",
        "label",
        "last_name",
        "message",
        "name",
        "nickname",
        "short_name",
        "snippet",
        "subtitle",
        "text",
        "title",
        "username",
        "vanity",
    }
)
"""Keys whose string values are words a person wrote or is called by."""

PERSON_KEYS: Final = frozenset(
    {"alternate_name", "first_name", "last_name", "nickname", "short_name", "username", "vanity"}
)
"""Keys whose values are part of a person's name even when they are one word."""

FILE_EXTENSIONS: Final = frozenset(
    {
        "avif",
        "gif",
        "heic",
        "html",
        "jpeg",
        "jpg",
        "js",
        "json",
        "mp4",
        "php",
        "png",
        "webm",
        "webp",
    }
)
"""File extensions a URL keeps; anything else after a dot, as in `Kim.Troll`, is a word."""

ADDRESS_KEY_ENDINGS: Final = ("handle", "path", "slug", "token", "uri", "url")
"""Endings of keys whose values address someone, such as `uri_token`: `Eris.Discordia.M`.

Every word of such a value is replaced, not only those with digits, because
a person's chosen address is made of their name.
"""

URL_WORDS: Final = frozenset(
    {
        "api",
        "comment",
        "comments",
        "gif",
        "gifs",
        "giphy",
        "graphql",
        "media",
        "photo",
        "photos",
        "posts",
        "reel",
        "reels",
        "story",
        "tenor",
        "v",
        "v1",
        "video",
        "videos",
        "watch",
        "www",
    }
)
"""Path words a URL keeps, because they say what it is, never whose."""

TOKEN: Final = re.compile(r"[A-Za-z0-9]+")

BASE64: Final = re.compile(r"^[A-Za-z0-9+/]{8,}={0,2}$")

DECODED_ID: Final = re.compile(r"^[A-Za-z_]+:[A-Za-z0-9_:]+$")
"""What an ID Facebook hides in base64 decodes to: `feedback:1234`, `comment:12_34`."""

SCRIPT_JSON: Final = re.compile(
    r"<script[^>]*type=\"application/json\"[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

GUARD: Final = "for (;;);"
"""What Facebook may put before a JSON body, so it cannot be run as a script."""

LEAK_LENGTH: Final = 6
"""The shortest masked word or replaced ID searched for before a scrub is written."""


ID_DIGITS: Final = 6
"""The fewest digits a number under an ID's key has to be replaced like one."""

IDENTIFIER: Final = re.compile(r"^[A-Za-z]*(?:[A-Z][a-z0-9]+){3,}[A-Za-z0-9]*$")
"""A code name made of three or more capitalized words, such as `CometLogoutHandlerQuery`.

Facebook names its queries and components under `name` keys too. Such a name
is masked like any word there, but is no one's name or words, so a scrub that
keeps it elsewhere, inside a kept code name, has leaked nothing.
"""


def holds_an_id(
    key: str | None,
) -> bool:
    """Return whether a key's value is an ID: `id`, `userId`, `actor_id`, `fbid`, `ownerID`."""

    if key is None:
        return False

    return key.lower() in ("id", "fbid") or key.endswith(("Id", "ID", "_id", "_fbid"))


COUNT_KEY: Final = "total_comment_count"
"""The key a post's comment count is kept under."""

CREATED_KEY: Final = "creation_time"
"""The key a post's time of publishing is kept under, beside its `post_id`."""

COMMENT_MARKERS: Final = ('"__typename": "Comment"', f'"{COUNT_KEY}"', f'"{CREATED_KEY}"')
"""What a payload about comments holds: a comment, or a post's comment count or time."""


class LeakError(ValueError):
    """Raised when a scrubbed capture still holds something it was to replace."""


def json_values(
    text: str,
) -> list[Any]:
    """Return every JSON value a body holds, one after another, without Facebook's guard."""

    text = text.strip()

    if text.startswith(GUARD):
        text = text[len(GUARD) :]

    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0

    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1

        if index >= len(text):
            break

        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break

        values.append(value)

    return values


def page_payloads(
    html: str,
) -> list[Any]:
    """Return the JSON a page holds in its script tags that mentions comments or a post's time."""

    payloads = []

    for match in SCRIPT_JSON.finditer(html):
        source = match.group(1)

        if "comment" not in source.lower() and CREATED_KEY not in source:
            continue

        payloads.extend(json_values(source))

    return payloads


def mask(
    text: str,
) -> str:
    """Return words masked letter by letter, their length and spacing kept."""

    return "".join(
        "X"
        if character.isupper()
        else "x"
        if character.isalpha()
        else "0"
        if character.isdigit()
        else character
        for character in text
    )


class Scrubber:
    """Replace what identifies people in captured JSON, consistently within one scrub."""

    def __init__(
        self,
        key: bytes | None = None,
    ) -> None:
        self._key = key if key is not None else secrets.token_bytes(32)
        self._tokens: dict[str, str] = {}
        self.masked: set[str] = set()
        self.replaced: set[str] = set()

    def token(
        self,
        value: str,
    ) -> str:
        """Return one token's replacement: the same every time, its length and classes kept."""

        if value in self._tokens:
            return self._tokens[value]

        stream = b""
        counter = 0

        while len(stream) < len(value):
            stream += hmac.new(self._key, f"{counter}:{value}".encode(), hashlib.sha256).digest()
            counter += 1

        characters = []

        for position, (character, byte) in enumerate(zip(value, stream, strict=False)):
            if character.isdigit():
                lowest = 1 if position == 0 and character != "0" else 0
                characters.append(str(lowest + byte % (10 - lowest)))
            elif "a" <= character <= "z":
                characters.append(chr(ord("a") + byte % 26))
            elif "A" <= character <= "Z":
                characters.append(chr(ord("A") + byte % 26))
            else:
                characters.append(character)

        replacement = "".join(characters)
        self._tokens[value] = replacement

        if len(value) >= LEAK_LENGTH:
            self.replaced.add(value)

        return replacement

    def _tokens_in(
        self,
        text: str,
        *,
        keep: frozenset[str] = frozenset(),
        words_too: bool = False,
    ) -> str:
        """Replace each token of a string that could identify someone."""

        def replace(match: re.Match[str]) -> str:
            token = match.group()

            if token.lower() in keep:
                return token

            if not words_too and not any(character.isdigit() for character in token):
                return token

            return self.token(token)

        return TOKEN.sub(replace, text)

    def _base64(
        self,
        value: str,
    ) -> str | None:
        """Return an ID hidden in base64 with its insides replaced, or none if it hides none."""

        if not BASE64.match(value) or len(value) % 4:
            return None

        try:
            decoded = base64.b64decode(value, validate=True).decode("ascii")
        except (binascii.Error, UnicodeDecodeError):
            return None

        if not DECODED_ID.match(decoded):
            return None

        return base64.b64encode(self._tokens_in(decoded).encode("ascii")).decode("ascii")

    def _url(
        self,
        value: str,
    ) -> str:
        """Return a URL with its host, extension and well-known words kept, the rest replaced."""

        scheme, _, rest = value.partition("://")
        host, slash, path = rest.partition("/")
        path, question, query = path.partition("?")
        stem, dot, extension = path.rpartition(".")

        if extension.lower() not in FILE_EXTENSIONS:
            stem, dot, extension = path, "", ""

        stem = self._tokens_in(stem, keep=URL_WORDS, words_too=True)
        query = re.sub(r"=([^&]*)", lambda match: "=" + self._query_value(match.group(1)), query)

        return f"{scheme}://{host}{slash}{stem}{dot}{extension}{question}{query}"

    def _query_value(
        self,
        value: str,
    ) -> str:
        """Return one query value replaced, a URL inside it scrubbed as a URL, its escapes kept.

        Facebook wraps the URL of a GIF from Giphy, or of a link, in its own:
        `https://external.xx.fbcdn.net/...?url=https%3A%2F%2Fmedia.giphy.com%2F...`.
        The value is unescaped, scrubbed as what it is, and escaped again, so
        the URL inside keeps its host and its escapes still decode.
        """

        unquoted = unquote(value)

        if unquoted.startswith(("http://", "https://")):
            return quote(self._url(unquoted), safe="")

        return quote(self._tokens_in(unquoted, words_too=True), safe="")

    def string(
        self,
        value: str,
        key: str | None,
    ) -> str:
        """Return one string scrubbed by what it is: words, a URL, an ID, or structure."""

        lowered = (key or "").lower()
        holds_words = not lowered.startswith("__") and (
            lowered in TEXT_KEYS or lowered.endswith("name") or lowered.endswith("_text")
        )

        if holds_words:
            if len(value) >= LEAK_LENGTH and (
                lowered in PERSON_KEYS or any(character.isspace() for character in value.strip())
            ):
                self.masked.add(value)

            return mask(value)

        if value.startswith(("http://", "https://")):
            return self._url(value)

        if lowered.endswith(ADDRESS_KEY_ENDINGS):
            return self._tokens_in(value, keep=URL_WORDS, words_too=True)

        hidden = self._base64(value)

        if hidden is not None:
            return hidden

        if any(character.isspace() for character in value.strip()):
            if len(value) >= LEAK_LENGTH:
                self.masked.add(value)

            return mask(value)

        return self._tokens_in(value)

    def value(
        self,
        value: Any,
        key: str | None = None,
    ) -> Any:
        """Return any JSON value scrubbed, its structure kept."""

        if isinstance(value, dict):
            return {
                self._tokens_in(name): self.value(member, name) for name, member in value.items()
            }

        if isinstance(value, list):
            return [self.value(member, key) for member in value]

        if isinstance(value, str):
            return self.string(value, key)

        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 10 ** (ID_DIGITS - 1)
            and holds_an_id(key)
        ):
            return int(self.token(str(value)))

        return value

    def record(
        self,
        record: dict[str, Any],
        payloads: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Return one captured response scrubbed into a fixture record.

        Its payloads are the JSON its body holds, unless the ones to keep are given.
        """

        if payloads is None:
            payloads = payloads_of(record)

        return {
            "kind": record.get("kind"),
            "url": self.string(str(record.get("url") or ""), "url"),
            "status": record.get("status"),
            "friendly_name": record.get("friendly_name"),
            "doc_id": record.get("doc_id"),
            "variables": self.value(record.get("variables")),
            "payloads": self.value(payloads),
        }


def payloads_of(
    record: dict[str, Any],
) -> list[Any]:
    """Return the JSON one captured response holds: a page's script JSON, or a body's values."""

    body = str(record.get("body") or "")

    return page_payloads(body) if record.get("kind") == "document" else json_values(body)


def about_comments(
    payload: Any,
) -> bool:
    """Return whether a payload holds a comment, or a post's comment count."""

    text = json.dumps(payload, ensure_ascii=False)

    return any(marker in text for marker in COMMENT_MARKERS)


def prune(
    value: Any,
) -> Any:
    """Return only what leads to a comment, or to a post's comment count or time, or none.

    A comment is kept whole. Above it, each object keeps its plain values and
    the members that lead to a comment or a count, and loses the rest: the
    viewer's settings, the next reels' players, the people who reacted. A list
    keeps the members that lead somewhere.
    """

    if isinstance(value, dict):
        if value.get("__typename") == "Comment":
            return value

        kept: dict[str, Any] = {}
        leads = COUNT_KEY in value or (CREATED_KEY in value and "post_id" in value)

        for name, member in value.items():
            if isinstance(member, dict | list):
                pruned = prune(member)

                if pruned is not None:
                    kept[name] = pruned
                    leads = True
            else:
                kept[name] = member

        return kept if leads else None

    if isinstance(value, list):
        members = [pruned for pruned in (prune(member) for member in value) if pruned is not None]

        return members or None

    return None


def string_values(
    value: Any,
) -> Iterator[str]:
    """Yield every string value in a JSON value, never its keys."""

    if isinstance(value, dict):
        for member in value.values():
            yield from string_values(member)
    elif isinstance(value, list):
        for member in value:
            yield from string_values(member)
    elif isinstance(value, str):
        yield value


def leaks(
    scrubber: Scrubber,
    scrubbed: str | Iterable[dict[str, Any]],
) -> list[str]:
    """Return every masked name or comment and replaced ID still in a scrubbed capture's values.

    Only string values are searched: keys and numbers are structure, kept as
    they are, and a word that happens to be a key elsewhere has leaked nothing.
    The words searched for are what people wrote and are called: masked text
    of more than one word, and names. One-word labels, such as a button's
    "Friends", and code names, such as Facebook's query names, are no one's.
    An ID is searched for as a whole token, so a long random replacement that
    happens to hold its digits is not taken for it.
    """

    if isinstance(scrubbed, str):
        values = [scrubbed]
    else:
        values = [
            found
            for record in scrubbed
            for part in (record.get("url"), record.get("variables"), record.get("payloads"))
            for found in string_values(part)
        ]

    tokens = {token for value in values for token in TOKEN.findall(value)}
    words = [found for found in scrubber.masked if not IDENTIFIER.match(found)]
    ids = [
        found
        for found in scrubber.replaced
        if any(character.isdigit() for character in found) and len(set(found)) > 1
    ]

    return sorted(
        {found for found in words if any(found in value for value in values)}
        | {found for found in ids if found in tokens}
    )


def scrub(
    records: Iterable[dict[str, Any]],
    scrubber: Scrubber | None = None,
    *,
    everything: bool = False,
) -> Iterator[str]:
    """Return a capture's records scrubbed, as JSON Lines, refusing if anything leaked.

    Only what is about comments is kept: the payloads that hold a comment or a
    post's comment count, pruned to what leads there, and the responses they
    came in. A page Facebook loads holds megabytes of everything else, as does
    a reel viewer's feed of other people's reels. `everything` keeps it all.

    Raises:
        LeakError: Something masked or replaced is still in the scrubbed capture.
    """

    scrubber = scrubber or Scrubber()
    scrubbed = []

    for record in records:
        payloads = payloads_of(record)

        if not everything:
            payloads = [
                pruned
                for pruned in (prune(payload) for payload in payloads if about_comments(payload))
                if pruned is not None
            ]

            if not payloads:
                continue

        scrubbed.append(scrubber.record(record, payloads))

    lines = [json.dumps(record, ensure_ascii=False) for record in scrubbed]
    found = leaks(scrubber, scrubbed)

    if found:
        raise LeakError(
            f"The scrubbed capture still holds {len(found)} thing(s) it was to replace; "
            "nothing was written."
        )

    yield from (line + "\n" for line in lines)
