"""Read comments, and comment counts, out of what Facebook sends a browser for a reel.

Written from a real capture of a canary reel (`tests/fixtures/facebook/canary.jsonl`).
Facebook's responses are undocumented and nest each comment deep inside
renderers that change, so nothing here follows a path from the top. It finds
what it reads by what it is, wherever it sits:

- a **comment** is any object of `__typename` `Comment` with an `id`, an
  `author` and a `created_time`. A reply names its parent in
  `comment_direct_parent`; its words are `body.text`, the people it mentions
  are `body.ranges`, as UTF-16 offsets into those words, its GIFs,
  stickers and photos are its `attachments`, and `is_hidden_by_viewer` says
  it is hidden from everyone else, by the person or by their keyword filter;
- a **post** is any object with a `post_id`: beside it, one object holds its
  reel's `url` and its `feedback.total_comment_count`, which counts replies
  too, and another its `creation_time`.

A GIF from Giphy reaches the browser wrapped in Facebook's own CDN URL, the
Giphy URL in its `url` parameter; the Giphy URL is unwrapped, so a GIF is the
same GIF wherever it is posted.

This is the module Facebook will break. Everything that knows its shapes is
here, and nothing else in Elder Sign does.
"""

import base64
import binascii
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from elder_sign.platforms.base import Comment, MediaItem
from elder_sign.scrub import payloads_of

COMMENT: Final = "Comment"

MENTIONED: Final = frozenset({"User", "Page"})
"""What a range in a comment's words can mention that is a person or a page."""

PLATFORM: Final = "facebook"


def payloads(
    records: Iterable[dict[str, Any]],
) -> Iterator[Any]:
    """Yield every JSON payload of a capture's records, raw or scrubbed."""

    for record in records:
        if "payloads" in record:
            yield from record["payloads"]
        else:
            yield from payloads_of(record)


def objects(
    value: Any,
) -> Iterator[dict[str, Any]]:
    """Yield every object in a JSON value, outermost first."""

    pending = [value]

    while pending:
        current = pending.pop()

        if isinstance(current, dict):
            yield current
            pending.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))


def is_comment(
    value: dict[str, Any],
) -> bool:
    """Return whether an object is a comment Elder Sign can read."""

    author = value.get("author")

    return (
        value.get("__typename") == COMMENT
        and isinstance(value.get("id"), str)
        and isinstance(author, dict)
        and isinstance(author.get("id"), str)
        and isinstance(value.get("created_time"), int)
    )


def created_at(
    seconds: int,
) -> str:
    """Return a Unix time as the scan's ISO 8601 time, in UTC."""

    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def mentions_of(
    text: str,
    ranges: Any,
) -> list[str]:
    """Return the words of each person or page a comment mentions.

    A range's offset and length count UTF-16 code units, as JavaScript does,
    so an emoji before a mention counts twice.
    """

    if not isinstance(ranges, list):
        return []

    encoded = text.encode("utf-16-le")
    mentions = []

    for entry in ranges:
        if not isinstance(entry, dict):
            continue

        entity = entry.get("entity") or {}
        offset, length = entry.get("offset"), entry.get("length")

        if entity.get("__typename") not in MENTIONED:
            continue

        if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
            continue

        mention = encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")

        if mention.strip():
            mentions.append(mention)

    return mentions


def unwrapped(
    uri: str,
) -> str:
    """Return the URL Facebook's CDN wraps in its `url` parameter, or the URL itself."""

    inner = parse_qs(urlsplit(uri).query).get("url")

    if inner and inner[0].startswith(("http://", "https://")):
        return inner[0]

    return uri


def _uri(
    media: dict[str, Any],
    *names: str,
) -> str | None:
    """Return the URI of the first of a media's images that has one."""

    for name in names:
        image = media.get(name)

        if isinstance(image, dict) and isinstance(image.get("uri"), str):
            return str(image["uri"])

    return None


def media_of(
    attachment: Any,
) -> MediaItem | None:
    """Return the GIF, sticker or photo one attachment of a comment posts, if it posts one."""

    if not isinstance(attachment, dict):
        return None

    styles = attachment.get("style_list") or []
    renderer = attachment.get("style_type_renderer") or {}
    media = (renderer.get("attachment") or {}).get("media") or attachment.get("media") or {}

    if not isinstance(media, dict):
        return None

    typename = media.get("__typename")

    if typename == "Sticker" or "sticker" in styles:
        uri = _uri(media, "image", "sticker_image", "animated_image")

        if uri is None:
            return None

        item: MediaItem = {"kind": "sticker", "url": uri}

        if isinstance(media.get("id"), str):
            item["provider"] = "facebook-sticker"
            item["provider_id"] = str(media["id"])

        return item

    if "animated_image" in media or "animated_image_share" in styles:
        uri = _uri(media, "animated_image", "image", "sticker_image")

        if uri is None:
            return None

        gif: MediaItem = {"kind": "gif", "url": unwrapped(uri)}
        attribution = media.get("animated_image_attribution")

        if isinstance(attribution, str) and attribution.strip():
            gif["provider"] = attribution.strip().lower()

        return gif

    uri = _uri(media, "image", "photo_image", "sticker_image")

    if uri is None:
        return None

    return {"kind": "image", "url": unwrapped(uri)}


def comment_of(
    node: dict[str, Any],
    *,
    hidden: bool = False,
) -> Comment:
    """Return one comment node as the scan's comment, hidden if any copy of it said so."""

    author: dict[str, Any] = node["author"]
    given = node.get("body")
    body: dict[str, Any] = given if isinstance(given, dict) else {}
    text = body.get("text")
    parent = node.get("comment_direct_parent")

    comment: Comment = {
        "id": str(node["id"]),
        "parent_id": str(parent["id"])
        if isinstance(parent, dict) and isinstance(parent.get("id"), str)
        else None,
        "author_id": str(author["id"]),
        "author_name": str(author["name"]) if isinstance(author.get("name"), str) else None,
        "text": text if isinstance(text, str) else None,
        "mentions": mentions_of(text, body.get("ranges")) if isinstance(text, str) else [],
        "media": [
            item for item in (media_of(entry) for entry in node.get("attachments") or []) if item
        ],
        "created_at": created_at(int(node["created_time"])),
        "hidden": hidden or node.get("is_hidden_by_viewer") is True,
    }

    return comment


def comments_in(
    records: Iterable[dict[str, Any]],
) -> list[Comment]:
    """Return every comment a capture holds, each once, in the order it first appears.

    A comment Facebook sends twice, as it does when a reply is loaded again
    under its parent, or a thread again under "Hidden by You", is read from
    the fuller of its copies, and is hidden if any copy says it is.
    """

    nodes: dict[str, dict[str, Any]] = {}
    hidden: set[str] = set()

    for payload in payloads(records):
        for value in objects(payload):
            if not is_comment(value):
                continue

            seen = nodes.get(value["id"])

            if value.get("is_hidden_by_viewer") is True:
                hidden.add(value["id"])

            if seen is None or len(value) > len(seen):
                nodes[value["id"]] = value

    return [comment_of(node, hidden=comment_id in hidden) for comment_id, node in nodes.items()]


def filtering_notices(
    records: Iterable[dict[str, Any]],
) -> list[str]:
    """Return every notice Facebook gives that it filtered comments out of what it shows."""

    notices = []

    for payload in payloads(records):
        for value in objects(payload):
            notice = value.get("filtering_footer_string")

            if isinstance(notice, str) and notice.strip() and notice.strip() not in notices:
                notices.append(notice.strip())

    return notices


def post_id_of(
    comment_id: str,
) -> str | None:
    """Return the ID of the post a comment's ID says it is on: `comment:<post>_<comment>`."""

    try:
        decoded = base64.b64decode(comment_id, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None

    kind, _, rest = decoded.partition(":")
    post_id, underscore, _ = rest.partition("_")

    return post_id if kind == "comment" and underscore and post_id.isdigit() else None


@dataclass(frozen=True)
class PostDetails:
    """What Facebook says of one post: its reel, when it was published, how many comments it has."""

    post_id: str
    reel_id: str | None = None
    created: datetime | None = None
    comment_count: int | None = None


REEL_PATH: Final = re.compile(r"/reel/(\d+)")
"""A reel's ID in its URL: `https://www.facebook.com/reel/<id>/`."""


def posts_in(
    records: Iterable[dict[str, Any]],
) -> dict[str, PostDetails]:
    """Return what a capture says of each post, by its post ID.

    Facebook says it piece by piece: one object holds a post's `post_id`, its
    reel's `url` and its `feedback.total_comment_count`, another the same
    `post_id` and its `creation_time`. They are put together here. Replies
    are in the count.
    """

    found: dict[str, dict[str, Any]] = {}

    for payload in payloads(records):
        for value in objects(payload):
            post_id = value.get("post_id")

            if not isinstance(post_id, str):
                continue

            details = found.setdefault(post_id, {})
            feedback = value.get("feedback")
            count = feedback.get("total_comment_count") if isinstance(feedback, dict) else None
            created = value.get("creation_time")
            url = value.get("url")

            if isinstance(count, int) and not isinstance(count, bool):
                details["comment_count"] = count

            if isinstance(created, int) and not isinstance(created, bool):
                details["created"] = datetime.fromtimestamp(created, UTC)

            if isinstance(url, str) and (reel := REEL_PATH.search(url)):
                details["reel_id"] = reel.group(1)

    return {
        post_id: PostDetails(post_id=post_id, **details)
        for post_id, details in found.items()
        if details
    }


def comment_counts(
    records: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """Return each post's comment count, replies included, by its post ID."""

    return {
        post_id: details.comment_count
        for post_id, details in posts_in(records).items()
        if details.comment_count is not None
    }
