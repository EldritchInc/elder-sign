"""Give every comment of a scan its one verdict, and say how each was reached.

The inputs are what the scan's earlier steps returned:

- `resolution`: from `lookup_verdicts`, every comment a layer decided, and
  the media no model has described;
- `judged`: from `classify-comment`, each group a model judged;
- `recorded`: from `record_verdicts`, a verdict for every comment of those
  groups.

The outcome lists every comment's verdict, those the layers decided first and
then those the model did, counts how many each layer decided, and counts what
the model was asked: how many groups, and how many of its answers could not be
read.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-0000000000a0"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-0000000000a1"

NAME: Final = "combine_verdicts"

VERSION: Final = "1.0.0"

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
    },
    "required": ["comment_id", "verdict", "layer", "source"],
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
                    },
                    "required": ["groups", "unreadable"],
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


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return every comment's verdict, and how each was reached."""

    resolution: JsonObject = inputs["resolution"]
    judged: list[JsonObject] = list(inputs["judged"])
    by_model: list[JsonObject] = list(inputs["recorded"].get("resolved") or ())

    verdicts = [*resolution["resolved"], *by_model]
    layers = dict(resolution.get("layers") or {})

    if by_model:
        layers["model"] = len(by_model)

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
            },
            "undescribed_media": list(resolution.get("undescribed_media") or ()),
        }
    }
