"""Keep a post's fingerprint once its comments are clean.

The fingerprint recorded is the one the post has after cleaning: the comments
that remain, and the newest of them. The next scan compares against it with
`check_post_fingerprint`, and skips the post if nothing has changed. Recording
the same fingerprint again changes nothing, so a resumed run may repeat it.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    Fingerprint,
    connect,
    write_fingerprint,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000030"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000031"

NAME: Final = "record_post_fingerprint"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Keep a post's fingerprint, its remaining comment count and newest remaining comment, "
    "once its comments are clean."
)

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "post": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "minLength": 1},
                "post_id": {"type": "string", "minLength": 1},
                "comment_count": {"type": "integer", "minimum": 0},
                "newest_comment_id": {"type": ["string", "null"]},
            },
            "required": ["platform", "post_id", "comment_count"],
        }
    },
    "required": ["post"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"recorded": {"type": "boolean"}},
    "required": ["recorded"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Keep the post's fingerprint, replacing the one before."""

    post: JsonObject = inputs["post"]
    newest = post.get("newest_comment_id")
    connection = connect()

    try:
        write_fingerprint(
            connection,
            str(post["platform"]),
            str(post["post_id"]),
            Fingerprint(
                comment_count=int(post["comment_count"]),
                newest_comment_id=None if newest is None else str(newest),
            ),
        )
    finally:
        connection.close()

    return {"recorded": True}
