"""Keep a post's fingerprint in the state file: a tool the store tests build and run."""

from typing import Final

from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    Fingerprint,
    connect,
    write_fingerprint,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-00000000f003"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-00000000f004"

NAME: Final = "store_probe"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = "Keep one post's fingerprint in the state file."

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"post_id": {"type": "string"}, "comment_count": {"type": "integer"}},
    "required": ["post_id", "comment_count"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"kept": {"type": "boolean"}},
    "required": ["kept"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Keep the post's fingerprint."""

    connection = connect()

    try:
        write_fingerprint(
            connection,
            "test",
            str(inputs["post_id"]),
            Fingerprint(comment_count=int(inputs["comment_count"]), newest_comment_id=None),
        )
    finally:
        connection.close()

    return {"kept": True}
