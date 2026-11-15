"""Remember what one GIF or image shows, and whether it is hostile in itself.

The input is the media's key, as `normalize_comments` gave it, and the
judgment `judge-media` returned for it:

    {"media": {"media_key": "m:giphy:...", "model": "...",
               "judgment": {"description": "...", "hostile": false, "reason": "..."}},
     "settings": {"policy": "transphobic-harassment"}}

The description is kept for every policy, so the words beside the media can
be judged with it, and the media never has to be looked at again. A hostile
judgment is also remembered as a verdict on the media under the policy, so
the media is caught with any words.

A model's judgment is checked before anything is kept. A description that is
not a non-blank string is not kept, so the media is described again on a
later scan; a `hostile` that is not exactly `true` is not hostile, so a
confused model can never make media hostile everywhere. A description is cut
to `MAXIMUM_DESCRIPTION_LENGTH` characters.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    Verdict,
    connect,
    transaction,
    write_description,
    write_verdict,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000060"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000061"

NAME: Final = "record_media"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Remember a model's description of one GIF or image for every policy, and, when the model "
    "found it hostile in itself, a hostile verdict on it under the policy."
)

MAXIMUM_DESCRIPTION_LENGTH: Final = 500
"""The longest description kept, in characters."""

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "media": {
            "type": "object",
            "properties": {
                "media_key": {"type": "string", "minLength": 1},
                "judgment": {},
                "model": {"type": ["string", "null"]},
            },
            "required": ["media_key", "judgment"],
        },
        "settings": {
            "type": "object",
            "properties": {"policy": {"type": "string", "minLength": 1}},
            "required": ["policy"],
        },
    },
    "required": ["media", "settings"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "media_key": {"type": "string"},
        "described": {"type": "boolean"},
        "hostile": {"type": "boolean"},
    },
    "required": ["media_key", "described", "hostile"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Keep the media's description, and its hostility, as far as the judgment holds them."""

    media: JsonObject = inputs["media"]
    media_key = str(media["media_key"])
    policy = str(inputs["settings"]["policy"])
    judgment = media["judgment"] if isinstance(media["judgment"], dict) else {}
    model = None if media.get("model") is None else str(media["model"])

    description = judgment.get("description")
    described = isinstance(description, str) and bool(description.strip())
    hostile = judgment.get("hostile") is True
    reason = judgment.get("reason")

    connection = connect()

    try:
        with transaction(connection):
            if described:
                write_description(
                    connection,
                    media_key,
                    str(description).strip()[:MAXIMUM_DESCRIPTION_LENGTH],
                    model,
                )

            if hostile:
                write_verdict(
                    connection,
                    policy,
                    "content",
                    media_key,
                    Verdict(
                        verdict="hostile",
                        source="model",
                        model=model,
                        reason=reason if isinstance(reason, str) else None,
                    ),
                )
    finally:
        connection.close()

    return {"media_key": media_key, "described": described, "hostile": hostile}
