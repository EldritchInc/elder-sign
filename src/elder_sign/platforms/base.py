"""What Elder Sign needs from a social platform, whichever it is.

A platform adapter reads a person's own recent posts the way they would see
them, and turns each into what the Azathoth workflows already understand: a
fingerprint, to tell whether a post changed since it was cleaned, and a scan
of its comments in the platform-neutral shape `normalize_comments` takes. It
fetches the GIFs and images comments post, for `judge-media` to look at.

Every adapter is held to that shape: `validate_scan` checks a scan against
the very schema `normalize_comments` declares, so a platform that changes
what it returns fails where it is read, not three steps later.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

from azathoth.evaluation import json_schema_validator, json_schema_violations

from elder_sign.shared.store import Fingerprint
from elder_sign.tools import normalize_comments


class MediaItem(TypedDict):
    """A GIF, sticker or image a comment posted."""

    kind: str
    url: NotRequired[str | None]
    provider: NotRequired[str | None]
    provider_id: NotRequired[str | None]


class Comment(TypedDict):
    """One comment, or a reply to one, in the shape `normalize_comments` takes."""

    id: str
    parent_id: str | None
    author_id: str
    author_name: NotRequired[str | None]
    text: NotRequired[str | None]
    mentions: NotRequired[list[str]]
    media: NotRequired[list[MediaItem]]
    created_at: NotRequired[str | None]
    hidden: NotRequired[bool]
    """Whether the comment is hidden from everyone but its author, their friends and the person.

    The person may have hidden it, or the platform did for them, as a keyword
    filter does. A hidden comment is judged like any other: a friendly one can
    be unhidden, and a hostile one is never hidden again.
    """


class Scan(TypedDict):
    """Every comment one post has, as one scan reads them."""

    platform: str
    post_id: str
    comments: list[Comment]
    comment_count: NotRequired[int | None]
    """How many comments the platform counts on the post: replies, hidden and deleted included."""

    caption: NotRequired[str | None]
    """What the post says, so a bare reaction to it can be read for what it reacts to."""

    owner_id: NotRequired[str | None]
    """The person's own account, so a model is told which comments she wrote herself."""


@dataclass(frozen=True)
class Post:
    """One of the person's own posts."""

    platform: str
    post_id: str
    url: str
    published_at: datetime | None = None
    kind: str = "reel"


@dataclass(frozen=True)
class ActionResult:
    """How one action on one comment went, and what its menu offered."""

    comment_id: str
    action: str
    ok: bool
    detail: str | None = None
    menu: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchedMedia:
    """The bytes of one GIF frame or image, in a type Azathoth accepts as an input."""

    content: bytes
    media_type: str


SCAN_VALIDATOR = json_schema_validator(normalize_comments.SCAN_SCHEMA)
"""Checks a scan against the schema `normalize_comments` declares."""

ACCEPTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")
"""The image types Azathoth accepts as a workflow's input (ADR 0112)."""


class ScanError(ValueError):
    """Raised when a scan is not in the shape the workflows take."""


@runtime_checkable
class Platform(Protocol):
    """A social platform Elder Sign reads a person's own posts from."""

    name: str
    """The platform's name, as scans and the state file record it: `facebook`."""

    account_id: str | None
    """The person's own account, as the platform names its authors, when it is known.

    Their own comments are always trusted: never judged, never acted on.
    """

    def recent_posts(
        self,
        days: int,
    ) -> Sequence[Post]:
        """Return the person's own posts from the last `days` days, newest first."""

    def fingerprint(
        self,
        post: Post,
    ) -> Fingerprint:
        """Return how many comments a post has and which is newest, without reading them all."""

    def scan(
        self,
        post: Post,
    ) -> Scan:
        """Return every comment and reply a post has."""

    def fetch_media(
        self,
        item: MediaItem,
    ) -> FetchedMedia | None:
        """Return one GIF's first frame or one image, or none when it cannot be fetched."""

    def act(
        self,
        post: Post,
        actions: Sequence[tuple[str, str]],
        hidden: Collection[str],
        *,
        probe: bool = False,
    ) -> list[ActionResult]:
        """Delete, hide or unhide comments on a post, each `(comment_id, action)`, in order.

        `hidden` names the comments hidden now, which are found where hidden
        comments are shown. With `probe`, each comment is found and its menu
        read, and nothing is done to it.
        """


def validate_scan(
    scan: Any,
) -> Scan:
    """Return a scan once it holds to the schema `normalize_comments` declares.

    Raises:
        ScanError: The scan is not in that shape, or two of its comments share an ID.
    """

    violations = json_schema_violations(SCAN_VALIDATOR, scan)

    if violations:
        (first, *_) = violations

        raise ScanError(
            f"A scan is not in the shape the workflows take: {first.path}: {first.message}"
        )

    ids = [comment["id"] for comment in scan["comments"]]

    if len(set(ids)) != len(ids):
        raise ScanError(f"Two comments of post {scan['post_id']} share an ID.")

    validated: Scan = scan

    return validated


def fingerprint_of(
    scan: Scan,
) -> Fingerprint:
    """Return the fingerprint a whole scan has: its comment count and its newest comment.

    The newest is the one created last; comments without a time are older than
    any with one, and among comments created at once, the last listed is newest.
    """

    comments = scan["comments"]

    if not comments:
        return Fingerprint(comment_count=0, newest_comment_id=None)

    newest = max(
        enumerate(comments),
        key=lambda entry: (entry[1].get("created_at") or "", entry[0]),
    )[1]

    return Fingerprint(comment_count=len(comments), newest_comment_id=newest["id"])


def confirm(
    actions: Sequence[tuple[str, str]],
    rescan: Scan,
) -> dict[str, tuple[bool, str | None]]:
    """Return, by comment, whether each action took, read from a scan made after them all.

    A delete took when the comment is gone; a hide when the comment is there
    and hidden; an unhide when it is there and not hidden.
    """

    found = {comment["id"]: comment for comment in rescan["comments"]}
    confirmed: dict[str, tuple[bool, str | None]] = {}

    for comment_id, action in actions:
        comment = found.get(comment_id)

        if action == "delete":
            confirmed[comment_id] = (
                (True, None) if comment is None else (False, "it is still there after deleting")
            )
        elif comment is None:
            confirmed[comment_id] = (False, f"it is gone after trying to {action} it")
        elif action == "hide":
            confirmed[comment_id] = (
                (True, None) if comment.get("hidden") else (False, "it is not hidden after hiding")
            )
        else:
            confirmed[comment_id] = (
                (True, None) if not comment.get("hidden") else (False, "it is still hidden")
            )

    return confirmed
