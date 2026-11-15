"""Remember, author by author, what each comment of a scan was judged, so standing is learned.

The inputs are the scan `normalize_comments` keyed, for who wrote each
comment, and `combine_verdicts`' outcome, for what each was judged and
whether that holds anywhere. Each verdict a model, a remembered content or
context, a blocked term or a GIF's own verdict decided is kept against its
author; a verdict decided by who wrote it, a trusted, blocked, allied or
trolling author's, is not, so a standing never feeds itself. From these
`read_reputation` learns who is an ally and who is a troll, on this post
and every one after it.

A human's verdict on a comment is never replaced by a model's, and writing
the same verdicts again changes nothing, so a resumed run may repeat this.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    transaction,
    write_author_verdict,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-0000000000b0"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-0000000000b1"

NAME: Final = "record_authors"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Remember against each comment's author what it was judged and whether that holds "
    "anywhere, for every verdict not decided by who wrote it, so allies and trolls are learned."
)

LEARNED_LAYERS: Final = frozenset({"model", "content", "context", "blocked_term", "media"})
"""The layers whose verdicts say something about their author: never one decided by the author."""

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "scan": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "minLength": 1},
                "post_id": {"type": "string", "minLength": 1},
                "comments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "author_id": {"type": "string"},
                        },
                        "required": ["id", "author_id"],
                    },
                },
            },
            "required": ["platform", "post_id", "comments"],
        },
        "outcome": {
            "type": "object",
            "properties": {
                "policy": {"type": "string", "minLength": 1},
                "verdicts": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["policy", "verdicts"],
        },
    },
    "required": ["scan", "outcome"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"learned": {"type": "integer", "minimum": 0}},
    "required": ["learned"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Keep each learned verdict against its comment's author, and return how many."""

    scan: JsonObject = inputs["scan"]
    outcome: JsonObject = inputs["outcome"]
    authors = {str(comment["id"]): str(comment["author_id"]) for comment in scan["comments"]}
    learned = [
        entry
        for entry in outcome["verdicts"]
        if entry.get("layer") in LEARNED_LAYERS and str(entry.get("comment_id")) in authors
    ]

    connection = connect()

    try:
        with transaction(connection):
            for entry in learned:
                comment_id = str(entry["comment_id"])
                write_author_verdict(
                    connection,
                    str(outcome["policy"]),
                    str(scan["platform"]),
                    authors[comment_id],
                    str(scan["post_id"]),
                    comment_id,
                    str(entry["verdict"]),
                    anywhere=entry.get("anywhere") is True,
                    source="human" if entry.get("source") == "human" else "model",
                )
    finally:
        connection.close()

    return {"learned": len(learned)}
