"""Remember what a model decided about the groups `lookup_verdicts` could not decide.

Each judged group is one from `lookup_verdicts`, with what the model said:

- `verdict`: friendly, hostile or uncertain;
- `context_independent`: whether the verdict holds wherever the comment
  appears. Then it is remembered by the comment's content, and decides every
  copy of it in any thread. A verdict is always also remembered in its context,
  when the comment has one, so the same reply under the same parent is decided
  without asking;
- `media_hostile`: whether the hostility is in a GIF or image itself. Then
  every one of its media is remembered as hostile, so the GIF is caught with
  any words;
- `model` and `reason`, kept for the audit trail.

A verdict a human made is never replaced. Every write of one call stands
together or not at all, and writing the same verdicts again changes nothing,
so a resumed run may repeat this step.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    Verdict,
    connect,
    transaction,
    write_verdict,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000050"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000051"

NAME: Final = "record_verdicts"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Remember a model's verdicts on the groups lookup_verdicts could not decide, by content "
    "when they hold in any thread, by context always, and by media when the hostility is in "
    "the media, and return a verdict for every comment of every group."
)

JUDGED_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "content_key": {"type": "string", "minLength": 1},
        "context_key": {"type": ["string", "null"]},
        "media_keys": {"type": "array", "items": {"type": "string"}},
        "comment_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "verdict": {"enum": ["friendly", "hostile", "uncertain"]},
        "context_independent": {"type": "boolean"},
        "media_hostile": {"type": "boolean"},
        "model": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["content_key", "context_key", "comment_ids", "verdict", "context_independent"],
}

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "judged": {"type": "array", "items": JUDGED_SCHEMA},
        "settings": {
            "type": "object",
            "properties": {"policy": {"type": "string", "minLength": 1}},
            "required": ["policy"],
        },
    },
    "required": ["judged", "settings"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "resolved": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "comment_id": {"type": "string"},
                    "verdict": {"enum": ["friendly", "hostile", "uncertain"]},
                    "layer": {"const": "model"},
                    "source": {"const": "model"},
                },
                "required": ["comment_id", "verdict", "layer", "source"],
            },
        },
        "recorded": {"type": "integer", "minimum": 0},
    },
    "required": ["resolved", "recorded"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def _keys(
    group: JsonObject,
) -> list[tuple[str, str]]:
    """Return the kind and key of every verdict one judged group is remembered by."""

    keys: list[tuple[str, str]] = []

    if group["context_independent"]:
        keys.append(("content", str(group["content_key"])))

    if group.get("context_key") is not None:
        keys.append(("context", str(group["context_key"])))

    if group.get("media_hostile") and group["verdict"] == "hostile":
        keys.extend(("content", str(media)) for media in group.get("media_keys") or ())

    return keys


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Remember every judged group's verdict, and return a verdict for each of its comments."""

    policy = str(inputs["settings"]["policy"])
    judged: list[JsonObject] = list(inputs["judged"])
    recorded = 0
    connection = connect()

    try:
        with transaction(connection):
            for group in judged:
                verdict = Verdict(
                    verdict=str(group["verdict"]),
                    source="model",
                    model=None if group.get("model") is None else str(group["model"]),
                    reason=None if group.get("reason") is None else str(group["reason"]),
                )

                for kind, key in _keys(group):
                    recorded += write_verdict(connection, policy, kind, key, verdict)
    finally:
        connection.close()

    return {
        "resolved": [
            {
                "comment_id": str(comment_id),
                "verdict": str(group["verdict"]),
                "layer": "model",
                "source": "model",
            }
            for group in judged
            for comment_id in group["comment_ids"]
        ],
        "recorded": recorded,
    }
