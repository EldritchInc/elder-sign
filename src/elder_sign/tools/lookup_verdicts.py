"""Decide every comment Elder Sign can decide without a model, and group the rest.

The input is a scan `normalize_comments` keyed. Each comment goes through
these layers in order, and the first that knows decides it:

1. `trusted_author`: the author is trusted, so the comment is friendly;
2. `blocked_author`: the author is blocked, so the comment is hostile;
3. `nothing_to_judge`: the comment has neither words nor media it can be
   recognized by, so there is nothing to hold against it;
4. `blocked_term`: its normalized text contains a blocked term;
5. `media`: one of its GIFs or images was judged hostile on its own;
6. `content`: its content was judged, in a way that holds in any thread;
7. `context`: its content was judged in reply to this same parent content.

What no layer decides needs a model. Those comments are grouped by their
context, or by their content when their parent is unknown, so that a swarm's
hundred copies of one comment under one parent are asked about once. Each
group carries one comment and its parent, as a model is shown them.

This tool only reads the state file: `record_verdicts` keeps what a model
decides.
"""

import sqlite3
from typing import Any, Final

from elder_sign.shared.keys import term_matches
from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    read_standing,
    read_terms,
    read_verdict,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000040"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000041"

NAME: Final = "lookup_verdicts"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Decide every comment of a keyed scan that trusted and blocked authors, blocked terms and "
    "remembered verdicts can decide, and group the rest so each distinct comment in its "
    "context is asked about once."
)

LAYERS: Final = (
    "trusted_author",
    "blocked_author",
    "nothing_to_judge",
    "blocked_term",
    "media",
    "content",
    "context",
)
"""The layers that decide a comment without a model, in the order they are tried."""

SHOWN_FIELDS: Final = ("id", "author_name", "text", "media", "created_at")
"""The fields of a comment a model is shown: never its keys, nor its author's ID."""

RESOLVED_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "comment_id": {"type": "string"},
        "verdict": {"enum": ["friendly", "hostile", "uncertain"]},
        "layer": {"enum": list(LAYERS)},
        "source": {"type": ["string", "null"]},
    },
    "required": ["comment_id", "verdict", "layer", "source"],
}

GROUP_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "content_key": {"type": "string"},
        "context_key": {"type": ["string", "null"]},
        "media_keys": {"type": "array", "items": {"type": "string"}},
        "comment_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "comment": {"type": "object"},
        "parent": {"type": ["object", "null"]},
    },
    "required": [
        "content_key",
        "context_key",
        "media_keys",
        "comment_ids",
        "comment",
        "parent",
    ],
}

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "scan": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "minLength": 1},
                "post_id": {"type": "string", "minLength": 1},
                "comments": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["platform", "post_id", "comments"],
        },
        "settings": {
            "type": "object",
            "properties": {"policy": {"type": "string", "minLength": 1}},
            "required": ["policy"],
        },
    },
    "required": ["scan", "settings"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "resolution": {
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "post_id": {"type": "string"},
                "policy": {"type": "string"},
                "resolved": {"type": "array", "items": RESOLVED_SCHEMA},
                "needs_inference": {"type": "array", "items": GROUP_SCHEMA},
                "layers": {"type": "object", "additionalProperties": {"type": "integer"}},
            },
            "required": [
                "platform",
                "post_id",
                "policy",
                "resolved",
                "needs_inference",
                "layers",
            ],
        }
    },
    "required": ["resolution"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def _shown(
    comment: JsonObject,
) -> JsonObject:
    """Return the part of a comment a model is shown."""

    return {field: comment[field] for field in SHOWN_FIELDS if field in comment}


def _decide(
    connection: sqlite3.Connection,
    platform: str,
    policy: str,
    terms: tuple[str, ...],
    comment: JsonObject,
) -> tuple[str, str, str | None] | None:
    """Return the verdict, layer and source that decide a comment, or none when a model must."""

    standing = read_standing(connection, platform, str(comment["author_id"]))

    if standing == "trusted":
        return "friendly", "trusted_author", None

    if standing == "blocked":
        return "hostile", "blocked_author", None

    keys: JsonObject = comment["keys"]

    if keys["content"] is None:
        return "friendly", "nothing_to_judge", None

    normalized = str(comment.get("normalized") or "")

    if any(term_matches(normalized, term) for term in terms):
        return "hostile", "blocked_term", None

    for media in keys["media"]:
        found = read_verdict(connection, policy, "content", media)

        if found is not None and found.verdict == "hostile":
            return "hostile", "media", found.source

    found = read_verdict(connection, policy, "content", keys["content"])

    if found is not None:
        return found.verdict, "content", found.source

    if keys["context"] is not None:
        found = read_verdict(connection, policy, "context", keys["context"])

        if found is not None:
            return found.verdict, "context", found.source

    return None


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return what every layer decided, and the groups only a model can decide."""

    scan: JsonObject = inputs["scan"]
    policy = str(inputs["settings"]["policy"])
    platform = str(scan["platform"])
    comments: list[JsonObject] = list(scan["comments"])
    by_id = {str(comment["id"]): comment for comment in comments}

    resolved: list[JsonObject] = []
    groups: dict[tuple[str, str], JsonObject] = {}
    layers: dict[str, int] = {}

    connection = connect()

    try:
        terms = read_terms(connection, policy)

        for comment in comments:
            decided = _decide(connection, platform, policy, terms, comment)

            if decided is not None:
                verdict, layer, source = decided
                layers[layer] = layers.get(layer, 0) + 1
                resolved.append(
                    {
                        "comment_id": str(comment["id"]),
                        "verdict": verdict,
                        "layer": layer,
                        "source": source,
                    }
                )
                continue

            keys: JsonObject = comment["keys"]
            grouping = (
                ("context", str(keys["context"]))
                if keys["context"] is not None
                else ("content", str(keys["content"]))
            )

            if grouping in groups:
                groups[grouping]["comment_ids"].append(str(comment["id"]))
                continue

            parent_id = comment.get("parent_id")
            parent: Any = by_id.get(str(parent_id)) if parent_id is not None else None

            groups[grouping] = {
                "content_key": keys["content"],
                "context_key": keys["context"],
                "media_keys": list(keys["media"]),
                "comment_ids": [str(comment["id"])],
                "comment": _shown(comment),
                "parent": None if parent is None or parent is comment else _shown(parent),
            }
    finally:
        connection.close()

    return {
        "resolution": {
            "platform": platform,
            "post_id": str(scan["post_id"]),
            "policy": policy,
            "resolved": resolved,
            "needs_inference": list(groups.values()),
            "layers": layers,
        }
    }
