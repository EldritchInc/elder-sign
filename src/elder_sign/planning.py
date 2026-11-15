"""Turn the verdicts on one scan into what to do about them: the plan, and nothing more.

The planner is given a scan, the outcome `judge` reached for it, and the
time, and decides, comment by comment:

- what is **hostile anywhere**, decided by its content, a blocked term, its
  media, or a model that said it holds wherever it is posted, is deleted, or
  hidden when deleting is off;
- what is **hostile only in its context**, or by a known troll, is hidden,
  which can be undone, so an ally misread in a heated thread is never deleted;
- what is **uncertain** is left for the person to decide;
- what is **friendly** is left alone, and unhidden when a keyword filter hid it,
  in dry run unless unhiding is turned on;
- her own comments, and trusted authors', are never touched, whatever a
  verdict says, and nothing without a verdict is.

A hostile comment is acted on only once it is `minimum_age` old, so whoever
wrote it has seen it posted. Deletes go first, replies before what they reply
to, so nothing is orphaned; then hides, then unhides. No more than
`maximum_actions` deletes and hides, and `maximum_unhides` unhides, are
planned in one run: the rest is deferred to the next.

Elder Sign is never asked to plan while it is frozen: `judge` refuses first.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from elder_sign.platforms.base import Comment, Scan

ActionName = Literal["delete", "hide", "unhide"]

MINIMUM_AGE: Final = timedelta(minutes=3)
"""How old a hostile comment must be before it is acted on."""

MAXIMUM_ACTIONS: Final = 50
"""The most deletes and hides one run plans for one post."""

MAXIMUM_UNHIDES: Final = 20
"""The most unhides one run plans for one post."""

NEVER_TOUCHED: Final = frozenset({"trusted_author"})
"""The layers whose comments are never acted on, whatever their verdict."""


@dataclass(frozen=True)
class PlanSettings:
    """How a plan is made."""

    delete: bool = True
    """Whether what is hostile anywhere is deleted, or only hidden."""

    unhide: bool = False
    """Whether friendly comments a filter hid are unhidden for real, or in dry run."""

    minimum_age: timedelta = MINIMUM_AGE
    maximum_actions: int = MAXIMUM_ACTIONS
    maximum_unhides: int = MAXIMUM_UNHIDES

    def __post_init__(self) -> None:
        if self.maximum_actions < 1 or self.maximum_unhides < 1:
            raise ValueError("A plan's caps must allow at least one action each.")


@dataclass(frozen=True)
class Action:
    """One thing to do to one comment, and the layer whose verdict asked for it."""

    comment_id: str
    action: ActionName
    layer: str
    dry_run: bool = False


@dataclass(frozen=True)
class Plan:
    """What one scan's verdicts ask to be done, and what they leave."""

    actions: tuple[Action, ...] = ()
    left: tuple[str, ...] = ()
    """Uncertain comments, left for the person to decide."""

    waiting: tuple[str, ...] = ()
    """Hostile comments too new to act on yet."""

    deferred: tuple[str, ...] = field(default=())
    """Comments over this run's caps, for the next run."""

    def counts(self) -> dict[str, int]:
        """Return how many of each action, and of what is left, waiting and deferred."""

        counted = {name: 0 for name in ("delete", "hide", "unhide")}

        for action in self.actions:
            counted[action.action] += 1

        return {
            **counted,
            "left": len(self.left),
            "waiting": len(self.waiting),
            "deferred": len(self.deferred),
        }


def _depth(
    comment_id: str,
    parents: dict[str, str | None],
) -> int:
    """Return how many comments deep a comment is: a top-level comment is 0."""

    depth, seen, current = 0, {comment_id}, parents.get(comment_id)

    while current is not None and current in parents and current not in seen:
        seen.add(current)
        depth += 1
        current = parents[current]

    return depth


def _created(
    comment: Comment,
) -> datetime | None:
    """Return when a comment was created, if the scan says."""

    created = comment.get("created_at")

    if not created:
        return None

    return datetime.fromisoformat(created.replace("Z", "+00:00"))


def plan(
    scan: Scan,
    outcome: dict[str, Any],
    *,
    now: datetime,
    settings: PlanSettings | None = None,
) -> Plan:
    """Return what the verdicts on a scan ask to be done, decided as the module says."""

    settings = settings or PlanSettings()
    comments = {comment["id"]: comment for comment in scan["comments"]}
    parents = {comment["id"]: comment["parent_id"] for comment in scan["comments"]}
    owner = scan.get("owner_id")
    deletes: list[Action] = []
    hides: list[Action] = []
    unhides: list[Action] = []
    left: list[str] = []
    waiting: list[str] = []

    for entry in outcome.get("verdicts", []):
        comment_id = str(entry["comment_id"])
        found = comments.get(comment_id)
        layer = str(entry["layer"])

        if found is None or layer in NEVER_TOUCHED or (owner and found["author_id"] == owner):
            continue

        verdict = entry["verdict"]
        hidden = found.get("hidden") is True

        if verdict == "uncertain":
            left.append(comment_id)
            continue

        if verdict == "friendly":
            if hidden:
                unhides.append(Action(comment_id, "unhide", layer, dry_run=not settings.unhide))
            continue

        created = _created(found)

        if created is None or now - created < settings.minimum_age:
            waiting.append(comment_id)
            continue

        if entry.get("anywhere") is True and settings.delete:
            deletes.append(Action(comment_id, "delete", layer))
        elif not hidden:
            hides.append(Action(comment_id, "hide", layer))

    deletes.sort(key=lambda action: -_depth(action.comment_id, parents))
    acting = [*deletes, *hides]
    kept = acting[: settings.maximum_actions]
    unhidden = unhides[: settings.maximum_unhides]
    deferred = [action.comment_id for action in acting[settings.maximum_actions :]] + [
        action.comment_id for action in unhides[settings.maximum_unhides :]
    ]

    return Plan(
        actions=(*kept, *unhidden),
        left=tuple(left),
        waiting=tuple(waiting),
        deferred=tuple(deferred),
    )
