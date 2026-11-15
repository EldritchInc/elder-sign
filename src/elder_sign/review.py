"""Show her what Elder Sign left open on one post, and keep each call she makes for good.

Review reads the report `judge` kept for a post: the scan, the verdicts and
the plan. It shows her, one at a time, each comment left **uncertain**, then
each the plan would **hide**, and, when she asks for everything, each it
would **delete**: who wrote it, its words, its media as a model described
it, and what it replies to. She answers friendly or hostile, skips, or
stops.

Her answer is kept as a human verdict, which no model's ever replaces:

- on the comment's **content**, so every copy of it anywhere is decided as
  she decided it, and on its **context**, so the same reply under the same
  parent is too;
- against its **author**, so her one call settles whether they are an ally
  or a troll on every post: see `read_reputation`.

Nothing on the platform changes here. The next `judge` applies her calls.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from elder_sign.policy import TRANSPHOBIC_HARASSMENT
from elder_sign.shared.store import (
    Verdict,
    read_description,
    transaction,
    write_author_verdict,
    write_verdict,
)
from elder_sign.tools import normalize_comments

REASON: Final = "you, in review"
"""The reason her verdicts are kept with."""

ANSWERS: Final = {"f": "friendly", "h": "hostile", "s": "skip", "q": "quit"}

PROMPT: Final = "[f]riendly  [h]ostile  [s]kip  [q]uit > "

KINDS: Final = {"gif": "GIF", "image": "image", "photo": "image", "sticker": "sticker"}

WHY: Final = {"left": "uncertain", "hide": "would be hidden", "delete": "would be deleted"}


@dataclass(frozen=True)
class Item:
    """One comment to review, and why it is shown."""

    comment: dict[str, Any]
    parent: dict[str, Any] | None
    why: str
    keys: dict[str, Any]


def items_of(
    report: dict[str, Any],
    *,
    everything: bool = False,
) -> list[Item]:
    """Return the comments a report leaves for her: uncertain, then hidden, then deleted."""

    scan = report["scan"]
    keyed = {
        comment["id"]: comment
        for comment in normalize_comments.run({"scan": scan})["scan"]["comments"]
    }
    plan = report.get("plan") or {}
    actions = plan.get("actions") or []
    order = [("left", comment_id) for comment_id in plan.get("left") or []]
    order += [("hide", action["comment_id"]) for action in actions if action["action"] == "hide"]

    if everything:
        order += [
            ("delete", action["comment_id"]) for action in actions if action["action"] == "delete"
        ]

    items = []

    for why, comment_id in order:
        comment = keyed.get(comment_id)

        if comment is None:
            continue

        parent = keyed.get(comment.get("parent_id") or "")
        items.append(Item(comment=comment, parent=parent, why=why, keys=comment["keys"]))

    return items


def _written(
    connection: Any,
    comment: dict[str, Any],
) -> str:
    """Return a comment as she is shown it: who wrote it, its words, its media."""

    parts = [f"{comment.get('author_name') or 'Someone'}:"]
    text = str(comment.get("text") or "").strip()

    if text:
        parts.append(f'"{text}"')

    for item, key in zip(comment.get("media") or [], comment["keys"]["media"], strict=False):
        kind = KINDS.get(str(item.get("kind") or ""), "media")
        description = read_description(connection, key) or "not described"
        parts.append(f"[{kind}: {description}]")

    return " ".join(parts)


def _keep(
    connection: Any,
    report: dict[str, Any],
    item: Item,
    verdict: str,
) -> None:
    """Keep her verdict on one comment: by its content and context, and against its author."""

    scan = report["scan"]
    policy = str((report.get("outcome") or {}).get("policy") or TRANSPHOBIC_HARASSMENT)
    hers = Verdict(verdict=verdict, source="human", model=None, reason=REASON)

    with transaction(connection):
        for kind in ("content", "context"):
            key = item.keys.get(kind)

            if key:
                write_verdict(connection, policy, kind, str(key), hers)

        write_author_verdict(
            connection,
            policy,
            str(scan["platform"]),
            str(item.comment["author_id"]),
            str(scan["post_id"]),
            str(item.comment["id"]),
            verdict,
            anywhere=False,
            source="human",
        )


def review(
    report: dict[str, Any],
    connection: Any,
    *,
    ask: Callable[[str], str],
    say: Callable[[str], object],
    everything: bool = False,
) -> dict[str, int]:
    """Ask her about each comment a report leaves open, keep her calls, and count them."""

    items = items_of(report, everything=everything)
    marked = {"friendly": 0, "hostile": 0, "skipped": 0}

    for index, item in enumerate(items, start=1):
        say("")
        say(f"{index} of {len(items)} · {WHY[item.why]}")
        say(f"  {_written(connection, item.comment)}")

        if item.parent is not None:
            say(f"  replying to {_written(connection, item.parent)}")

        while True:
            try:
                answer = ANSWERS.get(ask(PROMPT).strip().lower()[:1])
            except EOFError:
                return marked

            if answer is not None:
                break

        if answer == "quit":
            return marked

        if answer == "skip":
            marked["skipped"] += 1
            continue

        _keep(connection, report, item, answer)
        marked[answer] += 1

    return marked
