"""Write one group of comments as a model is shown it: the comment, and what it replies to.

The input is what `map_workflow` gives each child, one group of
`lookup_verdicts`' `needs_inference` as its item:

    {"item": {"comment": {...}, "parent": {...}, "context_key": "...", ...}, "index": 0}

The output is the text a prompt places, and the group itself, passed on
unchanged for `assemble_judgment`:

- `comment`: who wrote the comment, then its words, quoted as a JSON string
  so nothing in them can pass for the prompt's own words, then each GIF or
  image as its description: `"Kim" wrote: "lol exactly" [GIF: a clown]`.
  Replies often begin with the name of the person they answer, so naming
  who wrote what shows an ally answering a troll for what it is. An author
  known from before is said to be: `"Kim" (has defended her before) wrote:`;
- `thread`: what her post says, quoted, when it is known; then what the
  comment replies to: the post itself, another comment written the same
  way, saying so when she wrote it herself, or a comment that can no longer
  be seen.

Words and captions are cut to `MAXIMUM_TEXT_LENGTH` characters, so a wall of text cannot
crowd the policy out of a model's attention.
"""

import json
from typing import Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000070"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000074"

NAME: Final = "prepare_classification"

VERSION: Final = "1.3.0"

DESCRIPTION: Final = (
    "Write one group of comments as a model is shown it: the comment's words, quoted, with "
    "each GIF or image as its description, and what the comment replies to."
)

MAXIMUM_TEXT_LENGTH: Final = 1000
"""The most characters of a comment's words a model is shown."""

STANDINGS: Final = {"ally": "has defended her before", "troll": "has attacked her before"}
"""How an author's standing is said to a model."""

KINDS: Final = {"gif": "GIF", "image": "image", "photo": "image", "sticker": "sticker"}
"""How each kind of media is named to a model."""

TOP_LEVEL: Final = "It is a top-level comment on her post, replying to the post itself."

REPLY: Final = "It replies to this comment on her post:"

REPLY_TO_HER: Final = "It replies to this comment she wrote herself on her post:"

POST: Final = "Her post says:"

ORPHAN: Final = "It replies to a comment on her post that can no longer be seen."

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "request": {
            "type": "object",
            "properties": {
                "item": {"type": "object"},
                "index": {"type": "integer"},
            },
            "required": ["item"],
        }
    },
    "required": ["request"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {
        "comment": {"type": "string"},
        "thread": {"type": "string"},
        "group": {"type": "object"},
    },
    "required": ["comment", "thread", "group"],
}

TEST_CASES: Final = (
    (
        "writes a reply with a gif",
        "A reply's words are quoted, its GIF described, and its parent written the same way.",
        {
            "request": {
                "item": {
                    "context_key": "x:1",
                    "comment": {
                        "id": "r1",
                        "text": "lol exactly",
                        "media": [{"kind": "gif", "description": "A clown honking a horn."}],
                    },
                    "parent": {"id": "p1", "text": "trans women are men"},
                },
                "index": 0,
            }
        },
        {
            "comment": '"lol exactly" [GIF: A clown honking a horn.]',
            "thread": 'It replies to this comment on her post:\n"trans women are men"',
            "group": {
                "context_key": "x:1",
                "comment": {
                    "id": "r1",
                    "text": "lol exactly",
                    "media": [{"kind": "gif", "description": "A clown honking a horn."}],
                },
                "parent": {"id": "p1", "text": "trans women are men"},
            },
        },
    ),
)


def quoted(
    text: str,
) -> str:
    """Return words quoted as a JSON string, cut to `MAXIMUM_TEXT_LENGTH` characters."""

    if len(text) > MAXIMUM_TEXT_LENGTH:
        text = text[:MAXIMUM_TEXT_LENGTH] + "…"

    return json.dumps(text, ensure_ascii=False)


def written(
    comment: JsonObject,
) -> str:
    """Return a comment as a model reads it: who wrote it, its quoted words, then its media."""

    parts: list[str] = []
    author = str(comment.get("author_name") or "").strip()
    text = str(comment.get("text") or "").strip()

    standing = STANDINGS.get(str(comment.get("standing") or ""))

    if author and not comment.get("by_her"):
        known = f" ({standing})" if standing else ""
        parts.append(f"{quoted(author)}{known} wrote:")

    if text:
        parts.append(quoted(text))

    for item in comment.get("media") or ():
        kind = str(item.get("kind") or "").strip().lower()
        name = KINDS.get(kind, kind or "media")
        description = str(item.get("description") or "").strip()
        parts.append(f"[{name}: {description or 'not described'}]")

    if len(parts) == 1 and parts[0].endswith("wrote:"):
        parts.append("(nothing)")

    return " ".join(parts) if parts else "(nothing)"


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return the comment and its thread as a prompt shows them, and the group unchanged."""

    group: JsonObject = inputs["request"]["item"]
    parent = group.get("parent")

    if parent:
        thread = f"{REPLY_TO_HER if parent.get('by_her') else REPLY}\n{written(parent)}"
    elif group.get("context_key") is not None:
        thread = TOP_LEVEL
    else:
        thread = ORPHAN

    caption = str(group.get("caption") or "").strip()

    if caption:
        thread = f"{POST}\n{quoted(caption)}\n\n{thread}"

    return {"comment": written(group["comment"]), "thread": thread, "group": group}
