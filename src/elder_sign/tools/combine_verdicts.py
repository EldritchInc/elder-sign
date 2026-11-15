"""Give every comment of a scan its one verdict, and say how each was reached.

The inputs are what the scan's earlier steps returned:

- `resolution`: from `lookup_verdicts`, every comment a layer decided, and
  the media no model has described;
- `judged`: from `classify-comment`, each group a model judged;
- `recorded`: from `record_verdicts`, a verdict for every comment of those
  groups.

Each verdict says whether it holds `anywhere`: decided by its content alone, a
blocked term or its media, or by a model that said it holds wherever it is
posted. A verdict that holds only in its context can be undone by hiding.

An author's standing from `reputations` then settles what is left open:

- an ally's comment that is uncertain, or hostile only in its context, is
  friendly, by `known_ally`: allies answering trolls are misread there;
- a troll's uncertain comment is hostile, by `known_troll`.

A verdict that holds anywhere is never changed by who wrote it, so a troll
who earns an ally's standing still has a slur judged as one.

The outcome lists every comment's verdict, those the layers decided first and
then those the model did, counts how many each layer decided, and counts what
the model was asked: how many groups, how many of its answers could not be
read, and how many a stronger model was asked about again.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-0000000000a0"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-0000000000a3"

NAME: Final = "combine_verdicts"

VERSION: Final = "1.2.0"

DESCRIPTION: Final = (
    "Give every comment of a scan its one verdict, from the layers that decided without a "
    "model and from the model, counting how many each decided and what the model was asked."
)

VERDICT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "comment_id": {"type": "string"},
        "verdict": {"enum": ["friendly", "hostile", "uncertain"]},
        "layer": {"type": "string"},
        "source": {"type": ["string", "null"]},
        "anywhere": {"type": "boolean"},
    },
    "required": ["comment_id", "verdict", "layer", "source", "anywhere"],
}

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "resolution": {"type": "object"},
        "judged": {"type": "array", "items": {"type": "object"}},
        "recorded": {"type": "object"},
    },
    "required": ["resolution", "judged", "recorded"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "outcome": {
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "post_id": {"type": "string"},
                "policy": {"type": "string"},
                "verdicts": {"type": "array", "items": VERDICT_SCHEMA},
                "layers": {"type": "object", "additionalProperties": {"type": "integer"}},
                "model": {
                    "type": "object",
                    "properties": {
                        "groups": {"type": "integer"},
                        "unreadable": {"type": "integer"},
                        "escalated": {"type": "integer"},
                    },
                    "required": ["groups", "unreadable", "escalated"],
                },
                "undescribed_media": {"type": "array"},
            },
            "required": [
                "platform",
                "post_id",
                "policy",
                "verdicts",
                "layers",
                "model",
                "undescribed_media",
            ],
        }
    },
    "required": ["outcome"],
}


ANYWHERE_LAYERS: Final = frozenset({"blocked_author", "blocked_term", "media", "content"})
"""The layers whose verdict holds wherever the comment is posted."""


def settled(
    entry: JsonObject,
    held_anywhere: set[str],
    reputations: JsonObject,
) -> JsonObject:
    """Return one comment's verdict, saying whether it holds anywhere, settled by its author."""

    comment_id = str(entry["comment_id"])
    verdict = str(entry["verdict"])
    layer = str(entry["layer"])
    anywhere = layer in ANYWHERE_LAYERS or (layer == "model" and comment_id in held_anywhere)
    standing = reputations.get(comment_id)
    result: JsonObject = {**entry, "anywhere": anywhere and verdict == "hostile"}

    if standing == "ally" and (verdict == "uncertain" or (verdict == "hostile" and not anywhere)):
        return {**result, "verdict": "friendly", "layer": "known_ally", "anywhere": False}

    if standing == "troll" and verdict == "uncertain":
        return {**result, "verdict": "hostile", "layer": "known_troll", "anywhere": False}

    return result


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return every comment's verdict, and how each was reached."""

    resolution: JsonObject = inputs["resolution"]
    judged: list[JsonObject] = list(inputs["judged"])
    by_model: list[JsonObject] = list(inputs["recorded"].get("resolved") or ())

    held_anywhere = {
        str(comment_id)
        for group in judged
        if group.get("context_independent") is True
        for comment_id in group.get("comment_ids") or ()
    }
    reputations: JsonObject = resolution.get("reputations") or {}
    verdicts = [
        settled(entry, held_anywhere, reputations) for entry in [*resolution["resolved"], *by_model]
    ]
    layers: dict[str, int] = {}

    for entry in verdicts:
        layers[entry["layer"]] = layers.get(entry["layer"], 0) + 1

    return {
        "outcome": {
            "platform": resolution["platform"],
            "post_id": resolution["post_id"],
            "policy": resolution["policy"],
            "verdicts": verdicts,
            "layers": layers,
            "model": {
                "groups": len(judged),
                "unreadable": sum(1 for group in judged if group.get("valid") is False),
                "escalated": sum(1 for group in judged if group.get("escalated") is True),
            },
            "undescribed_media": list(resolution.get("undescribed_media") or ()),
        }
    }
