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

A model is shown a comment's words and, for each GIF or image, its
description from when `judge-media` looked at it, never the media itself. It
is shown what the post says, its `caption`, and which comments the person
wrote herself, marked `by_her`, so her answers to trolls read as hers, and
the `standing` each other author has earned, `ally` or `troll`, from what
they have been judged to write before. Every comment by an author with a
standing is named in `reputations`, so the scan's verdicts can be settled
by it once a model has spoken: see `combine_verdicts`. A
group whose comment or parent has media no model has described yet names it
in `undescribed_media`, with the URL to fetch it from, so the scan can have
it judged before the words beside it are.

This tool only reads the state file: `record_verdicts` keeps what a model
decides.
"""

import sqlite3
from typing import Any, Final

from elder_sign.shared.keys import media_key, term_matches
from elder_sign.shared.protocol import JsonObject
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    read_description,
    read_reputation,
    read_standing,
    read_terms,
    read_verdict,
)

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000040"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000044"

NAME: Final = "lookup_verdicts"

VERSION: Final = "1.3.0"

DESCRIPTION: Final = (
    "Decide every comment of a keyed scan that trusted and blocked authors, blocked terms and "
    "remembered verdicts can decide, and group the rest so each distinct comment in its "
    "context is asked about once, shown with what its media shows."
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

SHOWN_FIELDS: Final = ("id", "author_name", "text", "created_at")
"""The fields of a comment a model is shown: never its keys, nor its author's ID."""

UNDESCRIBED_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "media_key": {"type": "string"},
        "kind": {"type": ["string", "null"]},
        "url": {"type": ["string", "null"]},
    },
    "required": ["media_key", "kind", "url"],
}

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
        "caption": {"type": ["string", "null"]},
    },
    "required": [
        "content_key",
        "context_key",
        "media_keys",
        "comment_ids",
        "comment",
        "parent",
        "caption",
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
                "caption": {"type": ["string", "null"]},
                "owner_id": {"type": ["string", "null"]},
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
                "undescribed_media": {"type": "array", "items": UNDESCRIBED_SCHEMA},
                "layers": {"type": "object", "additionalProperties": {"type": "integer"}},
                "reputations": {
                    "type": "object",
                    "additionalProperties": {"enum": ["ally", "troll"]},
                },
            },
            "required": [
                "platform",
                "post_id",
                "policy",
                "resolved",
                "needs_inference",
                "undescribed_media",
                "layers",
                "reputations",
            ],
        }
    },
    "required": ["resolution"],
}

ENVIRONMENT: Final = (DATABASE_ENVIRONMENT_VARIABLE,)


def _shown(
    connection: sqlite3.Connection,
    comment: JsonObject,
    undescribed: dict[str, JsonObject],
    owner_id: str | None,
    standings: dict[str, str | None],
) -> JsonObject:
    """Return the part of a comment a model is shown, noting media not yet described.

    Each GIF or image is shown as its kind and description; media no model
    has described is shown with no description, and noted in `undescribed`.
    A comment the person wrote herself is marked `by_her`, and one by an
    author with a standing is marked with it.
    """

    shown: JsonObject = {field: comment[field] for field in SHOWN_FIELDS if field in comment}
    author_id = str(comment.get("author_id"))

    if owner_id is not None and author_id == owner_id:
        shown["by_her"] = True
    elif standings.get(author_id):
        shown["standing"] = standings[author_id]
    media: list[JsonObject] = []

    for item in comment.get("media") or ():
        key = media_key(item)
        description = None if key is None else read_description(connection, key)
        kind = item.get("kind")
        media.append({"kind": kind, "description": description})

        if key is not None and description is None and key not in undescribed:
            undescribed[key] = {"media_key": key, "kind": kind, "url": item.get("url")}

    if media:
        shown["media"] = media

    return shown


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
    caption = scan.get("caption")
    owner = scan.get("owner_id")
    owner_id = str(owner) if owner else None
    comments: list[JsonObject] = list(scan["comments"])
    by_id = {str(comment["id"]): comment for comment in comments}

    resolved: list[JsonObject] = []
    groups: dict[tuple[str, str], JsonObject] = {}
    undescribed: dict[str, JsonObject] = {}
    layers: dict[str, int] = {}
    reputations: dict[str, str] = {}

    connection = connect()

    try:
        terms = read_terms(connection, policy)
        standings: dict[str, str | None] = {
            author_id: None
            if author_id == owner_id
            else read_reputation(connection, policy, platform, author_id)
            for author_id in {str(comment["author_id"]) for comment in comments}
        }

        for comment in comments:
            standing = standings.get(str(comment["author_id"]))

            if standing:
                reputations[str(comment["id"])] = standing

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
                "comment": _shown(connection, comment, undescribed, owner_id, standings),
                "parent": None
                if parent is None or parent is comment
                else _shown(connection, parent, undescribed, owner_id, standings),
                "caption": str(caption) if caption else None,
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
            "undescribed_media": list(undescribed.values()),
            "layers": layers,
            "reputations": reputations,
        }
    }
