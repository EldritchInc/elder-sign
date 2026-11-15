"""A platform that lives in memory, for testing what is built on platforms.

It holds posts and their comments as given, answers as a real adapter does,
and counts every call, so a test can prove an unchanged post was never
scanned, or a known GIF never fetched.
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from elder_sign.platforms.base import (
    ActionResult,
    Comment,
    FetchedMedia,
    MediaItem,
    Post,
    Scan,
    fingerprint_of,
    validate_scan,
)
from elder_sign.shared.store import Fingerprint

FAKE_MENU: tuple[str, ...] = ("Delete", "Hide comment", "Report comment")
"""What the fake's comment menu offers."""


class FakePlatform:
    """A platform whose posts, comments and media are whatever a test gives it."""

    def __init__(
        self,
        name: str = "facebook",
        *,
        now: datetime | None = None,
        account_id: str | None = None,
    ) -> None:
        self.name = name
        self.account_id = account_id
        self.calls: Counter[str] = Counter()
        self._now = now or datetime.now(UTC)
        self._posts: dict[str, Post] = {}
        self._comments: dict[str, list[Comment]] = {}
        self._media: dict[str, FetchedMedia] = {}

    def add_post(
        self,
        post_id: str,
        comments: Sequence[Comment] = (),
        *,
        age: timedelta = timedelta(days=1),
    ) -> Post:
        """Give the platform a post of the person's, published `age` ago, with its comments."""

        post = Post(
            platform=self.name,
            post_id=post_id,
            url=f"https://example.com/{self.name}/{post_id}",
            published_at=self._now - age,
        )
        self._posts[post_id] = post
        self._comments[post_id] = list(comments)

        return post

    def set_comments(
        self,
        post_id: str,
        comments: Sequence[Comment],
    ) -> None:
        """Replace every comment a post has, as trolls and deletions would."""

        self._comments[post_id] = list(comments)

    def add_media(
        self,
        url: str,
        content: bytes,
        media_type: str = "image/png",
    ) -> None:
        """Give the platform the bytes one media URL serves."""

        self._media[url] = FetchedMedia(content=content, media_type=media_type)

    def recent_posts(
        self,
        days: int,
    ) -> Sequence[Post]:
        """Return the posts published in the last `days` days, newest first."""

        self.calls["recent_posts"] += 1
        since = self._now - timedelta(days=days)
        recent = [
            post
            for post in self._posts.values()
            if post.published_at is not None and post.published_at >= since
        ]

        return sorted(recent, key=lambda post: post.published_at or since, reverse=True)

    def fingerprint(
        self,
        post: Post,
    ) -> Fingerprint:
        """Return the post's comment count and newest comment."""

        self.calls["fingerprint"] += 1

        return fingerprint_of(self._scan(post))

    def scan(
        self,
        post: Post,
    ) -> Scan:
        """Return every comment the post has."""

        self.calls["scan"] += 1

        return self._scan(post)

    def fetch_media(
        self,
        item: MediaItem,
    ) -> FetchedMedia | None:
        """Return the bytes the item's URL serves, or none when it serves none."""

        self.calls["fetch_media"] += 1

        return self._media.get(str(item.get("url") or ""))

    def act(
        self,
        post: Post,
        actions: Sequence[tuple[str, str]],
        hidden: Collection[str],
        *,
        probe: bool = False,
    ) -> list[ActionResult]:
        """Delete, hide or unhide the post's comments, as told, unless probing."""

        self.calls["act"] += 1
        comments = self._comments[post.post_id]
        results = []

        for comment_id, action in actions:
            index = next((i for i, c in enumerate(comments) if c["id"] == comment_id), None)

            if index is None:
                missing = ActionResult(comment_id, action, False, "the comment is not on the page")
                results.append(missing)
                continue

            if not probe:
                if action == "delete":
                    comments.pop(index)
                else:
                    comments[index] = {**comments[index], "hidden": action == "hide"}

            results.append(ActionResult(comment_id, action, True, None, FAKE_MENU))

        return results

    def _scan(
        self,
        post: Post,
    ) -> Scan:
        """Return the post's comments as a validated scan."""

        comments: Mapping[str, list[Comment]] = self._comments

        return validate_scan(
            {
                "platform": self.name,
                "post_id": post.post_id,
                "comments": [dict(comment) for comment in comments[post.post_id]],
            }
        )
