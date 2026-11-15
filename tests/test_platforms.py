"""Tests proving the platform boundary: what every adapter returns, and the fake that stands in."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from elder_sign.platforms import (
    Comment,
    FakePlatform,
    Platform,
    ScanError,
    fingerprint_of,
    validate_scan,
)
from elder_sign.shared.store import Fingerprint
from tests.tools import run_tool

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=UTC)

GIF = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"


def comment(
    comment_id: str,
    created_at: str | None,
    *,
    parent: str | None = None,
    text: str = "hi",
) -> Comment:
    """Return one comment as an adapter returns it."""

    return {
        "id": comment_id,
        "parent_id": parent,
        "author_id": f"author-{comment_id}",
        "text": text,
        "created_at": created_at,
    }


# Every adapter's shape


def test_the_fake_is_a_platform() -> None:
    assert isinstance(FakePlatform(), Platform)


def test_the_fake_is_signed_in_as_the_account_it_is_given() -> None:
    assert FakePlatform().account_id is None
    assert FakePlatform(account_id="me").account_id == "me"


def test_a_scan_in_the_workflows_shape_is_valid() -> None:
    scan = {
        "platform": "facebook",
        "post_id": "reel-1",
        "comments": [
            {
                **comment("c1", "2026-11-14T10:00:00Z"),
                "mentions": ["Eris"],
                "media": [{"kind": "gif", "url": GIF}],
            }
        ],
    }

    assert validate_scan(scan) == scan


@pytest.mark.parametrize(
    "scan",
    [
        {"platform": "facebook", "post_id": "reel-1"},
        {"platform": "", "post_id": "reel-1", "comments": []},
        {"platform": "facebook", "post_id": "reel-1", "comments": [{"id": "c1"}]},
        {"platform": "facebook", "post_id": "reel-1", "comments": [{"id": 7, "author_id": "a"}]},
        {
            "platform": "facebook",
            "post_id": "reel-1",
            "comments": [{"id": "c1", "author_id": "a", "media": [{"kind": "gif", "url": 3}]}],
        },
    ],
    ids=["no comments", "blank platform", "no author", "numeric id", "numeric url"],
)
def test_a_scan_in_any_other_shape_is_refused_where_it_is_read(
    scan: dict[str, Any],
) -> None:
    with pytest.raises(ScanError, match="not in the shape"):
        validate_scan(scan)


def test_a_scan_with_two_comments_of_one_id_is_refused() -> None:
    scan = {
        "platform": "facebook",
        "post_id": "reel-1",
        "comments": [comment("c1", None), comment("c1", None)],
    }

    with pytest.raises(ScanError, match="share an ID"):
        validate_scan(scan)


def test_a_valid_scan_is_what_normalize_comments_takes() -> None:
    platform = FakePlatform(now=NOW)
    post = platform.add_post("reel-1", [comment("c1", "2026-11-14T10:00:00Z")])

    keyed = run_tool("normalize_comments", {"scan": platform.scan(post)})

    assert keyed["scan"]["comments"][0]["keys"]["content"] is not None


# Fingerprints


def test_a_scan_with_no_comments_has_an_empty_fingerprint() -> None:
    assert fingerprint_of({"platform": "facebook", "post_id": "p", "comments": []}) == (
        Fingerprint(0, None)
    )


def test_the_newest_comment_is_the_one_created_last_wherever_it_is_listed() -> None:
    scan = validate_scan(
        {
            "platform": "facebook",
            "post_id": "p",
            "comments": [
                comment("c1", "2026-11-14T10:00:00Z"),
                comment("c3", "2026-11-14T12:00:00Z"),
                comment("r1", "2026-11-14T11:00:00Z", parent="c1"),
            ],
        }
    )

    assert fingerprint_of(scan) == Fingerprint(3, "c3")


def test_comments_without_a_time_are_older_and_ties_go_to_the_last_listed() -> None:
    scan = validate_scan(
        {
            "platform": "facebook",
            "post_id": "p",
            "comments": [
                comment("c1", None),
                comment("c2", "2026-11-14T10:00:00Z"),
                comment("c3", "2026-11-14T10:00:00Z"),
                comment("c4", None),
            ],
        }
    )

    assert fingerprint_of(scan) == Fingerprint(4, "c3")


# The fake


def test_recent_posts_are_those_of_the_last_days_newest_first() -> None:
    platform = FakePlatform(now=NOW)
    platform.add_post("old", age=timedelta(days=40))
    platform.add_post("week", age=timedelta(days=7))
    platform.add_post("today", age=timedelta(hours=2))

    assert [post.post_id for post in platform.recent_posts(30)] == ["today", "week"]
    assert [post.post_id for post in platform.recent_posts(1)] == ["today"]


def test_the_fakes_fingerprint_follows_its_comments() -> None:
    platform = FakePlatform(now=NOW)
    post = platform.add_post("reel-1", [comment("c1", "2026-11-14T10:00:00Z")])

    assert platform.fingerprint(post) == Fingerprint(1, "c1")

    platform.set_comments(
        "reel-1",
        [comment("c1", "2026-11-14T10:00:00Z"), comment("c2", "2026-11-14T10:05:00Z")],
    )

    assert platform.fingerprint(post) == Fingerprint(2, "c2")


def test_the_fake_counts_every_call() -> None:
    platform = FakePlatform(now=NOW)
    post = platform.add_post("reel-1")

    platform.recent_posts(30)
    platform.fingerprint(post)
    platform.fingerprint(post)
    platform.fetch_media({"kind": "gif", "url": GIF})

    assert platform.calls == {"recent_posts": 1, "fingerprint": 2, "fetch_media": 1}
    assert platform.calls["scan"] == 0


def test_the_fake_serves_only_the_media_it_was_given() -> None:
    platform = FakePlatform(now=NOW)
    platform.add_media(GIF, b"\x89PNG frame")

    fetched = platform.fetch_media({"kind": "gif", "url": GIF})

    assert fetched is not None
    assert (fetched.content, fetched.media_type) == (b"\x89PNG frame", "image/png")
    assert platform.fetch_media({"kind": "gif", "url": GIF + "?other"}) is None
    assert platform.fetch_media({"kind": "sticker"}) is None
