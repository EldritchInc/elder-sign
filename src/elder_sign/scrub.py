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
  value is replaced as a token.
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

URL_WORDS: Final = frozenset(
    {
        "api",
        "comment",
        "comments",
        "gif",
        "gifs",
        "graphql",
        "media",
        "photo",
        "photos",
        "posts",
        "reel",
        "reels",
        "story",
        "v",
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
    """Return the JSON a page holds in its script tags that mentions comments."""

    payloads = []

    for match in SCRIPT_JSON.finditer(html):
        source = match.group(1)

        if "comment" not in source.lower():
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

        if "/" in extension or not dot or len(extension) > 5:
            stem, dot, extension = path, "", ""

        stem = self._tokens_in(stem, keep=URL_WORDS, words_too=True)
        query = re.sub(
            r"=([^&]*)",
            lambda match: "=" + self._tokens_in(match.group(1), words_too=True),
            query,
        )

        return f"{scheme}://{host}{slash}{stem}{dot}{extension}{question}{query}"

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
            if len(value) >= LEAK_LENGTH:
                self.masked.add(value)

            return mask(value)

        if value.startswith(("http://", "https://")):
            return self._url(value)

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

        return value

    def record(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one captured response scrubbed into a fixture record."""

        body = str(record.get("body") or "")
        payloads = page_payloads(body) if record.get("kind") == "document" else json_values(body)

        return {
            "kind": record.get("kind"),
            "url": self.string(str(record.get("url") or ""), "url"),
            "status": record.get("status"),
            "friendly_name": record.get("friendly_name"),
            "doc_id": record.get("doc_id"),
            "variables": self.value(record.get("variables")),
            "payloads": self.value(payloads),
        }


def leaks(
    scrubber: Scrubber,
    scrubbed: str,
) -> list[str]:
    """Return every masked word and replaced ID still in a scrubbed capture."""

    return sorted(
        found for found in scrubber.masked | scrubber.replaced if found and found in scrubbed
    )


def scrub(
    records: Iterable[dict[str, Any]],
    scrubber: Scrubber | None = None,
) -> Iterator[str]:
    """Return a capture's records scrubbed, as JSON Lines, refusing if anything leaked.

    Raises:
        LeakError: Something masked or replaced is still in the scrubbed capture.
    """

    scrubber = scrubber or Scrubber()
    lines = [json.dumps(scrubber.record(record), ensure_ascii=False) for record in records]
    found = leaks(scrubber, "\n".join(lines))

    if found:
        raise LeakError(
            f"The scrubbed capture still holds {len(found)} thing(s) it was to replace; "
            "nothing was written."
        )

    yield from (line + "\n" for line in lines)
