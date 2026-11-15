"""Know when a platform changed under Elder Sign, before Elder Sign acts on what it misreads.

Facebook's pages are undocumented and change without notice. When they do,
Elder Sign may read a reply as a top-level comment, or a GIF as nothing at
all, and judge a comment without the context that makes it friendly. So a
platform is checked against a **canary**: one of the person's own reels,
with comments they know, ideally a reply, a mention, a GIF and a comment
they hid among them.

`set_canary` reads the canary as it is now and keeps, for each of its
comments, what Elder Sign read of it: its reply to its parent, its mentions,
its words, its author's name, its time, its media, each GIF, sticker or
image fetched, and whether it is hidden. `check` reads it again. A comment
no longer read, or read without something it had, means the platform
changed: Elder Sign is then **frozen** on that platform, and acts on nothing
there until a check passes; everything that acts asks `ensure_unfrozen`
first. New comments on the canary change nothing.

Both are kept as flags in the state file: `canary:<platform>` and
`frozen:<platform>`.
"""

import json
import sqlite3
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from elder_sign.platforms.base import Comment, Platform, Post, ScanError
from elder_sign.shared.store import forget_flag, read_flag, write_flag

CANARY_FLAG: Final = "canary:{platform}"

FROZEN_FLAG: Final = "frozen:{platform}"

FROZEN: Final = "frozen"
"""The value of a platform's frozen flag."""

FEATURES: Final = {
    "reply": "replies",
    "mention": "mentions",
    "words": "words",
    "author": "author names",
    "time": "times",
    "media": "GIFs, stickers or images",
    "still": "GIFs, stickers and images fetched",
    "hidden": "hidden comments",
}
"""What Elder Sign reads of a comment, in the order it is checked, and what each is called."""

CATCHES: Final = ("reply", "mention", "media", "still", "hidden")
"""What a canary must have to catch Facebook changing how it is read."""

BLIND_SPOTS: Final = {
    "reply": "reply",
    "mention": "mention",
    "media": "GIF, sticker or image",
    "still": "GIF, sticker or image that can be fetched",
    "hidden": "hidden comment",
}
"""What each feature a canary lacks is called, when it is said to lack it."""


class CanaryError(RuntimeError):
    """Raised when a canary cannot be set or checked."""


class FrozenError(RuntimeError):
    """Raised when Elder Sign is asked to act on a platform it is frozen on."""


@dataclass(frozen=True)
class Canary:
    """A reel of the person's, and what Elder Sign read of each of its comments."""

    platform: str
    post_id: str
    url: str
    comments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """Each comment's ID, and what was read of it: a feature once, `still` once a fetch."""

    comment_count: int | None = None
    """How many comments the platform counted on the reel, if it counts them."""

    def totals(self) -> dict[str, int]:
        """Return how many of each feature the canary's comments have."""

        return dict(sorted(Counter(f for read in self.comments.values() for f in read).items()))

    def blind_spots(self) -> list[str]:
        """Return what the canary has none of, so a change to it would go unnoticed."""

        totals = self.totals()

        return [feature for feature in CATCHES if not totals.get(feature)]

    def to_json(self) -> str:
        """Return the canary as it is kept."""

        return json.dumps(
            {
                "platform": self.platform,
                "post_id": self.post_id,
                "url": self.url,
                "comments": {key: list(read) for key, read in self.comments.items()},
                "comment_count": self.comment_count,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(
        cls,
        text: str,
    ) -> "Canary":
        """Return a canary as it was kept."""

        kept = json.loads(text)

        return cls(
            platform=str(kept["platform"]),
            post_id=str(kept["post_id"]),
            url=str(kept["url"]),
            comments={str(key): tuple(read) for key, read in kept["comments"].items()},
            comment_count=kept.get("comment_count"),
        )


def features_of(
    comment: Comment,
    platform: Platform,
) -> tuple[str, ...]:
    """Return what was read of one comment, its media fetched to see they still can be."""

    read = []

    if comment.get("parent_id"):
        read.append("reply")

    if comment.get("mentions"):
        read.append("mention")

    if comment.get("text"):
        read.append("words")

    if comment.get("author_name"):
        read.append("author")

    if comment.get("created_at"):
        read.append("time")

    media = comment.get("media") or []

    if media:
        read.append("media")

    read.extend("still" for item in media if platform.fetch_media(item) is not None)

    if comment.get("hidden"):
        read.append("hidden")

    return tuple(read)


def set_canary(
    connection: sqlite3.Connection,
    platform: Platform,
    post: Post,
) -> Canary:
    """Read a reel as it is now, and keep it as the platform's canary.

    Raises:
        CanaryError: The reel has no comments to watch.
    """

    scan = platform.scan(post)

    if not scan["comments"]:
        raise CanaryError(f"Reel {post.post_id} has no comments, so it cannot be a canary.")

    canary = Canary(
        platform=platform.name,
        post_id=post.post_id,
        url=post.url,
        comments={comment["id"]: features_of(comment, platform) for comment in scan["comments"]},
        comment_count=scan.get("comment_count"),
    )
    write_flag(connection, CANARY_FLAG.format(platform=platform.name), canary.to_json())

    return canary


def read_canary(
    connection: sqlite3.Connection,
    platform: str,
) -> Canary | None:
    """Return a platform's canary, if one is set."""

    flag = read_flag(connection, CANARY_FLAG.format(platform=platform))

    return None if flag is None else Canary.from_json(flag.value)


def frozen_reason(
    connection: sqlite3.Connection,
    platform: str,
) -> str | None:
    """Return why Elder Sign is frozen on a platform, or none when it is not."""

    flag = read_flag(connection, FROZEN_FLAG.format(platform=platform))

    return None if flag is None else (flag.reason or "its canary stopped reading as it did")


def ensure_unfrozen(
    connection: sqlite3.Connection,
    platform: str,
) -> None:
    """Refuse to go on when Elder Sign is frozen on a platform; every action asks this first.

    Raises:
        FrozenError: Elder Sign is frozen on the platform, saying why and how to unfreeze it.
    """

    reason = frozen_reason(connection, platform)

    if reason is not None:
        raise FrozenError(
            f"Elder Sign is frozen on {platform}, so it acts on nothing there: {reason}\n"
            f"Once Elder Sign reads {platform} again, run: elder-sign canary check {platform}"
        )


def check(
    connection: sqlite3.Connection,
    platform: Platform,
) -> list[str]:
    """Read the platform's canary again, freeze the platform if it reads less, and say why.

    A check that finds nothing wrong unfreezes the platform.

    Raises:
        CanaryError: No canary is set for the platform.
    """

    canary = read_canary(connection, platform.name)

    if canary is None:
        raise CanaryError(
            f"No canary is set for {platform.name}; "
            f"run: elder-sign canary set {platform.name} <link to a reel of yours>"
        )

    problems = _problems(canary, platform)
    frozen = FROZEN_FLAG.format(platform=platform.name)

    if problems:
        write_flag(connection, frozen, FROZEN, reason="; ".join(problems))
    else:
        forget_flag(connection, frozen)

    return problems


def _problems(
    canary: Canary,
    platform: Platform,
) -> list[str]:
    """Return every way the canary reads less than it did."""

    post = Post(platform=canary.platform, post_id=canary.post_id, url=canary.url)

    try:
        scan = platform.scan(post)
    except ScanError as exc:
        return [f"The canary could not be read: {exc}"]

    read = {comment["id"]: comment for comment in scan["comments"]}
    problems = []
    missing = [key for key in canary.comments if key not in read]

    if missing:
        problems.append(
            f"{len(missing)} of the canary's {len(canary.comments)} comments were not read."
        )

    expected: Counter[str] = Counter()
    now: Counter[str] = Counter()

    for key, features in canary.comments.items():
        if key in read:
            expected.update(features)
            now.update(features_of(read[key], platform))

    for feature, label in FEATURES.items():
        if now[feature] < expected[feature]:
            problems.append(
                f"Comments with {label}: {expected[feature]} expected, {now[feature]} read."
            )

    if canary.comment_count is not None and scan.get("comment_count") is None:
        problems.append("The platform's comment count could not be read.")

    return problems
