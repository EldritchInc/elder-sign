"""Check a model's judgment of one group, and join it to the group for `record_verdicts`.

The model is asked for one JSON object:

    {"verdict": "hostile", "context_independent": true, "media_hostile": false,
     "reason": "questions that she is a woman"}

The prompt step returns the model's reply as text, and this tool reads the
object out of it: as JSON, from inside a Markdown code fence, or from the
first `{` to the last `}` when the model wrapped it in words. So a model that
answers badly still has its comments judged, as uncertain, rather than failing
the scan.

A model's answer is never trusted to be well formed. Whatever it says is held
to the safest reading that still acts:

- a verdict that is not friendly, hostile or uncertain, or an answer that is
  not an object at all, is `uncertain`: the comments are hidden, which can be
  undone, and nothing is remembered beyond their context;
- `context_independent` is true only when the model said exactly `true`, so a
  confused answer is never remembered for every thread;
- `media_hostile` is true only when the model said exactly `true`, about a
  hostile verdict on a group that has media, so a confused answer never makes
  a GIF hostile everywhere;
- a reason is kept only as text, cut to `MAXIMUM_REASON_LENGTH` characters.

`valid` says whether the model's answer held a verdict it could be taken at.
"""

import json
import re
from typing import Any, Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000080"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000081"

NAME: Final = "assemble_judgment"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Check a model's judgment of one group of comments, reading anything malformed as "
    "uncertain, and join it to the group's keys and comment IDs for record_verdicts."
)

VERDICTS: Final = ("friendly", "hostile", "uncertain")

MAXIMUM_REASON_LENGTH: Final = 200
"""The longest reason kept, in characters."""

FENCE: Final = re.compile(r"^```[A-Za-z0-9_-]*\s*\n(.*)\n\s*```$", re.DOTALL)
"""A reply wrapped in one Markdown code fence, with or without a language tag."""

UNREADABLE: Final = "the model's answer could not be read"
"""The reason given when a model's answer held no verdict it could be taken at."""

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "group": {
            "type": "object",
            "properties": {
                "content_key": {"type": "string"},
                "context_key": {"type": ["string", "null"]},
                "media_keys": {"type": "array", "items": {"type": "string"}},
                "comment_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content_key", "context_key", "comment_ids"],
        },
        "judgment": {},
        "model": {"type": ["string", "null"]},
    },
    "required": ["group", "judgment"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "judged": {
            "type": "object",
            "properties": {
                "content_key": {"type": "string"},
                "context_key": {"type": ["string", "null"]},
                "media_keys": {"type": "array", "items": {"type": "string"}},
                "comment_ids": {"type": "array", "items": {"type": "string"}},
                "verdict": {"enum": list(VERDICTS)},
                "context_independent": {"type": "boolean"},
                "media_hostile": {"type": "boolean"},
                "model": {"type": ["string", "null"]},
                "reason": {"type": ["string", "null"]},
                "valid": {"type": "boolean"},
            },
            "required": [
                "content_key",
                "context_key",
                "media_keys",
                "comment_ids",
                "verdict",
                "context_independent",
                "media_hostile",
                "model",
                "reason",
                "valid",
            ],
        }
    },
    "required": ["judged"],
}

GROUP: Final[JsonObject] = {
    "content_key": "c:1",
    "context_key": "x:1",
    "media_keys": [],
    "comment_ids": ["c1", "c2"],
}

TEST_CASES: Final = (
    (
        "reads a judgment from a fenced reply",
        "A reply a model wrapped in a code fence is read as the JSON inside it.",
        {
            "group": GROUP,
            "judgment": '```json\n{"verdict": "friendly", "context_independent": true}\n```',
        },
        {
            "judged": {
                **GROUP,
                "verdict": "friendly",
                "context_independent": True,
                "media_hostile": False,
                "model": None,
                "reason": None,
                "valid": True,
            }
        },
    ),
    (
        "takes a well formed judgment",
        "A judgment the model gave correctly is joined to its group as it was given.",
        {
            "group": GROUP,
            "judgment": {
                "verdict": "hostile",
                "context_independent": True,
                "media_hostile": False,
                "reason": "questions that she is a woman",
            },
        },
        {
            "judged": {
                **GROUP,
                "verdict": "hostile",
                "context_independent": True,
                "media_hostile": False,
                "model": None,
                "reason": "questions that she is a woman",
                "valid": True,
            }
        },
    ),
    (
        "reads nonsense as uncertain",
        "A judgment with no verdict hides the comments and remembers nothing broadly.",
        {"group": GROUP, "judgment": "I think this is fine?"},
        {
            "judged": {
                **GROUP,
                "verdict": "uncertain",
                "context_independent": False,
                "media_hostile": False,
                "model": None,
                "reason": UNREADABLE,
                "valid": False,
            }
        },
    ),
)


def parsed(
    reply: Any,
) -> Any:
    """Return the JSON a model's reply holds, or the reply itself when it is not text.

    A reply is read as JSON, then as JSON inside one code fence, then as the
    JSON from its first `{` to its last `}`. A reply none of those reads is
    returned unread, and holds no verdict.
    """

    if not isinstance(reply, str):
        return reply

    text = reply.strip()
    fenced = FENCE.match(text)
    candidates = [text, fenced.group(1) if fenced else None]

    if "{" in text and "}" in text:
        candidates.append(text[text.index("{") : text.rindex("}") + 1])

    for candidate in candidates:
        if candidate is None:
            continue

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return reply


def _verdict(
    judgment: Any,
) -> str | None:
    """Return the verdict a judgment gives, or none when it gives none that can be taken."""

    if not isinstance(judgment, dict):
        return None

    verdict = judgment.get("verdict")

    if not isinstance(verdict, str):
        return None

    verdict = verdict.strip().lower()

    return verdict if verdict in VERDICTS else None


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return the group joined to its judgment, read as safely as it can be."""

    group: JsonObject = inputs["group"]
    judgment = parsed(inputs["judgment"])
    model = inputs.get("model")
    media_keys = [str(key) for key in group.get("media_keys") or ()]
    verdict = _verdict(judgment)

    joined: JsonObject = {
        "content_key": str(group["content_key"]),
        "context_key": None if group.get("context_key") is None else str(group["context_key"]),
        "media_keys": media_keys,
        "comment_ids": [str(comment_id) for comment_id in group["comment_ids"]],
        "model": None if model is None else str(model),
    }

    if verdict is None:
        return {
            "judged": {
                **joined,
                "verdict": "uncertain",
                "context_independent": False,
                "media_hostile": False,
                "reason": UNREADABLE,
                "valid": False,
            }
        }

    reason = judgment.get("reason")

    return {
        "judged": {
            **joined,
            "verdict": verdict,
            "context_independent": judgment.get("context_independent") is True,
            "media_hostile": judgment.get("media_hostile") is True
            and verdict == "hostile"
            and bool(media_keys),
            "reason": (reason.strip()[:MAXIMUM_REASON_LENGTH] or None)
            if isinstance(reason, str)
            else None,
            "valid": True,
        }
    }
