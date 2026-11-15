"""Answer whether a post's comments changed since it was last cleaned.

A post is fingerprinted by how many comments it has and which is newest,
which a platform shows without loading every comment. Any comment added since
the post was last cleaned changes the newest; any comment removed without a
new one changes the count. A post whose fingerprint has not changed needs no
scan at all. This tool only reads: `record_post_fingerprint` keeps the new
fingerprint once the post is clean.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    Fingerprint,
    connect,
    read_fingerprint,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000020"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000021"

NAME: Final = "check_post_fingerprint"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Answer whether a post's comments changed since it was last cleaned, from its comment "
    "count and its newest comment."
)

FINGERPRINT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "comment_count": {"type": "integer", "minimum": 0},
        "newest_comment_id": {"type": ["string", "null"]},
    },
    "required": ["comment_count", "newest_comment_id"],
}

POST_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "platform": {"type": "string", "minLength": 1},
        "post_id": {"type": "string", "minLength": 1},
        "comment_count": {"type": "integer", "minimum": 0},
        "newest_comment_id": {"type": ["string", "null"]},
    },
    "required": ["platform", "post_id", "comment_count"],
}

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"post": POST_SCHEMA},
    "required": ["post"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "changed": {"type": "boolean"},
        "previous": {"anyOf": [FINGERPRINT_SCHEMA, {"type": "null"}]},
    },
    "required": ["changed", "previous"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def fingerprint_of(
    post: JsonObject,
) -> Fingerprint:
    """Return the fingerprint a post's description gives."""

    newest = post.get("newest_comment_id")

    return Fingerprint(
        comment_count=int(post["comment_count"]),
        newest_comment_id=None if newest is None else str(newest),
    )


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return whether the post changed, and its fingerprint from when it was last cleaned."""

    post: JsonObject = inputs["post"]
    connection = connect()

    try:
        previous = read_fingerprint(connection, str(post["platform"]), str(post["post_id"]))
    finally:
        connection.close()

    return {
        "changed": previous != fingerprint_of(post),
        "previous": None
        if previous is None
        else {
            "comment_count": previous.comment_count,
            "newest_comment_id": previous.newest_comment_id,
        },
    }
