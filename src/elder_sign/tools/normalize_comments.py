"""Give every comment of a scan the keys Elder Sign recognizes it by.

The input is one scan of one post, as a platform adapter reads it:

    {"platform": "facebook", "post_id": "...", "comments": [
        {"id": "...", "parent_id": null, "author_id": "...", "text": "...",
         "mentions": ["Eris Discordia"], "media": [{"kind": "gif", "url": "..."}]}]}

The output is the same scan, each comment with its normalized text and its
keys added. Nothing is read or written but the scan: this tool needs no state.
"""

from typing import Any, Final

from elder_sign.shared.keys import content_key, context_key, media_key, normalize_text, text_key
from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000010"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000011"

NAME: Final = "normalize_comments"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Give every comment of one scan of a post its normalized text and the keys it is "
    "recognized by: its text, its media, its content, and its content in reply to its parent."
)

MEDIA_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "provider": {"type": ["string", "null"]},
        "provider_id": {"type": ["string", "null"]},
    },
}

COMMENT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "parent_id": {"type": ["string", "null"]},
        "author_id": {"type": "string"},
        "author_name": {"type": ["string", "null"]},
        "text": {"type": ["string", "null"]},
        "mentions": {"type": "array", "items": {"type": "string"}},
        "media": {"type": "array", "items": MEDIA_SCHEMA},
        "created_at": {"type": ["string", "null"]},
    },
    "required": ["id", "author_id"],
}

SCAN_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "platform": {"type": "string", "minLength": 1},
        "post_id": {"type": "string", "minLength": 1},
        "comments": {"type": "array", "items": COMMENT_SCHEMA},
    },
    "required": ["platform", "post_id", "comments"],
}

KEYS_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "text": {"type": ["string", "null"]},
        "media": {"type": "array", "items": {"type": "string"}},
        "content": {"type": ["string", "null"]},
        "context": {"type": ["string", "null"]},
    },
    "required": ["text", "media", "content", "context"],
}

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"scan": SCAN_SCHEMA},
    "required": ["scan"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "scan": {
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "post_id": {"type": "string"},
                "comments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "normalized": {"type": "string"},
                            "keys": KEYS_SCHEMA,
                        },
                        "required": ["id", "normalized", "keys"],
                    },
                },
            },
            "required": ["platform", "post_id", "comments"],
        }
    },
    "required": ["scan"],
}

TEST_CASES: Final = (
    (
        "folds a disguised comment",
        "Leetspeak, repeats, punctuation and a mention fold away.",
        {
            "scan": {
                "platform": "facebook",
                "post_id": "p",
                "comments": [
                    {
                        "id": "c1",
                        "author_id": "a",
                        "text": "@Eris U R A TR4NNYYYY!!!",
                        "mentions": ["@Eris"],
                    }
                ],
            }
        },
        {
            "scan": {
                "platform": "facebook",
                "post_id": "p",
                "comments": [
                    {
                        "id": "c1",
                        "author_id": "a",
                        "text": "@Eris U R A TR4NNYYYY!!!",
                        "mentions": ["@Eris"],
                        "normalized": "ura tranny",
                        "keys": {
                            "text": "t:26b2c115aba0788c9e7162af8bdf5d6b",
                            "media": [],
                            "content": "c:0505dd555b1ea82433da17fa0bcb51dd",
                            "context": "x:8a457f850287dd2b8c79d6ccabb5412a",
                        },
                    }
                ],
            }
        },
    ),
)


def _keyed(
    comment: JsonObject,
) -> tuple[str, str | None, list[str], str | None]:
    """Return a comment's normalized text and its text, media and content keys."""

    normalized = normalize_text(str(comment.get("text") or ""), comment.get("mentions") or ())
    media = sorted(
        {key for item in comment.get("media") or () if (key := media_key(item)) is not None}
    )
    text = text_key(normalized)

    return normalized, text, media, content_key(text, media)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return the scan with every comment's normalized text and keys added.

    Raises:
        ValueError: Two comments share an ID.
    """

    scan: JsonObject = inputs["scan"]
    comments: list[JsonObject] = list(scan["comments"])
    keyed: dict[str, tuple[str, str | None, list[str], str | None]] = {}

    for comment in comments:
        comment_id = str(comment["id"])

        if comment_id in keyed:
            raise ValueError(f"Two comments of post {scan['post_id']} share the ID {comment_id}.")

        keyed[comment_id] = _keyed(comment)

    output: list[Any] = []

    for comment in comments:
        comment_id = str(comment["id"])
        normalized, text, media, content = keyed[comment_id]
        parent_id = comment.get("parent_id")
        top_level = parent_id is None or parent_id == comment_id
        parent = None if top_level else keyed.get(str(parent_id))

        output.append(
            {
                **comment,
                "normalized": normalized,
                "keys": {
                    "text": text,
                    "media": media,
                    "content": content,
                    "context": context_key(
                        content,
                        None if parent is None else parent[3],
                        top_level=top_level,
                    ),
                },
            }
        )

    return {"scan": {**scan, "comments": output}}
