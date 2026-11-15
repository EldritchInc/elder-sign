"""Judge one group as a model would, by two fixed rules: a tool that stands in for classify-comment.

`map_workflow` runs each child in its own process, where no scripted model
can be put in place of a real one. The moderate-scan tests import a child
workflow under classify-comment's ID whose one step is this tool, so the
whole scan runs, map and all, with no model.

The rules: words in `FRIENDLY` are friendly; words in `CONTEXT_DEPENDENT` are
hostile in their context only; anything else is hostile wherever it is
posted, and a comment of media alone is hostile in its media.
"""

from typing import Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-00000000f005"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-00000000f006"

NAME: Final = "stand_in_judge"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = "Judge one group by two fixed rules, standing in for classify-comment."

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"request": {"type": "object"}},
    "required": ["request"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"judged": {"type": "object"}},
    "required": ["judged"],
}

FRIENDLY: Final = ("slay queen 👑", "you look amazing")

CONTEXT_DEPENDENT: Final = ("lol exactly",)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return the group judged by the stand-in's rules."""

    group: JsonObject = inputs["request"]["item"]
    text = str(group["comment"].get("text") or "")
    friendly = text in FRIENDLY

    return {
        "judged": {
            "content_key": group["content_key"],
            "context_key": group["context_key"],
            "media_keys": group["media_keys"],
            "comment_ids": group["comment_ids"],
            "verdict": "friendly" if friendly else "hostile",
            "context_independent": text not in CONTEXT_DEPENDENT,
            "media_hostile": not friendly and not text and bool(group["media_keys"]),
            "model": "stand-in",
            "reason": None,
            "valid": True,
        }
    }
