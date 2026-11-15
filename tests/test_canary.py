"""Tests proving the canary: a known reel that tells when a platform changed under Elder Sign.

The canary is run on the in-memory platform, so a change to the platform is
whatever a test makes it: a reply that loses its parent, a GIF that can no
longer be fetched, comments that are no longer read at all.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from elder_sign.canary import (
    Canary,
    CanaryError,
    FrozenError,
    check,
    ensure_unfrozen,
    frozen_reason,
    read_canary,
    set_canary,
)
from elder_sign.platforms import Comment, FakePlatform, Post, ScanError
from elder_sign.shared.store import DATABASE_ENVIRONMENT_VARIABLE, connect

GIF = "https://media.giphy.com/media/abc/giphy.gif"

REEL = "2027199361330354"


def canary_comments() -> list[Comment]:
    """Return a canary's comments: words, a reply, a mention, a GIF and a hidden comment."""

    return [
        {
            "id": "c1",
            "parent_id": None,
            "author_id": "a1",
            "author_name": "One",
            "text": "Canary one",
            "created_at": "2026-11-15T04:30:00Z",
        },
        {
            "id": "c2",
            "parent_id": "c1",
            "author_id": "a2",
            "author_name": "Two",
            "text": "Two replying to One",
            "mentions": ["One"],
            "created_at": "2026-11-15T04:31:00Z",
        },
        {
            "id": "c3",
            "parent_id": None,
            "author_id": "a3",
            "author_name": "Three",
            "media": [{"kind": "gif", "url": GIF, "provider": "giphy"}],
            "created_at": "2026-11-15T04:32:00Z",
        },
        {
            "id": "c4",
            "parent_id": None,
            "author_id": "a4",
            "author_name": "Four",
            "text": "Caught by the filter",
            "created_at": "2026-11-15T04:33:00Z",
            "hidden": True,
        },
    ]


def canary_platform(
    comments: list[Comment] | None = None,
) -> tuple[FakePlatform, Post]:
    """Return a platform holding the canary reel, its GIF fetchable."""

    platform = FakePlatform()
    post = platform.add_post(REEL, canary_comments() if comments is None else comments)
    platform.add_media(GIF, b"png")

    return platform, post


@pytest.fixture
def state(
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Open a fresh state file for one test."""

    connection = connect(tmp_path / "state.db")

    try:
        yield connection
    finally:
        connection.close()


# Setting the canary


def test_setting_the_canary_keeps_what_it_reads_as_now(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()

    canary = set_canary(state, platform, post)

    assert read_canary(state, "facebook") == canary
    assert canary.post_id == REEL
    assert canary.url == post.url
    assert set(canary.comments) == {"c1", "c2", "c3", "c4"}
    assert canary.totals() == {
        "author": 4,
        "hidden": 1,
        "media": 1,
        "mention": 1,
        "reply": 1,
        "still": 1,
        "time": 4,
        "words": 3,
    }
    assert canary.blind_spots() == []


def test_a_canary_says_what_it_cannot_catch() -> None:
    canary = Canary(
        platform="facebook",
        post_id="1",
        url="u",
        comments={"c1": ("author", "time", "words")},
        comment_count=1,
    )

    assert canary.blind_spots() == ["reply", "mention", "media", "still", "hidden"]


def test_a_reel_without_comments_cannot_be_a_canary(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform([])

    with pytest.raises(CanaryError, match="no comments"):
        set_canary(state, platform, post)

    assert read_canary(state, "facebook") is None


def test_there_is_no_canary_until_one_is_set(
    state: sqlite3.Connection,
) -> None:
    platform, _ = canary_platform()

    assert read_canary(state, "facebook") is None

    with pytest.raises(CanaryError, match="elder-sign canary set facebook"):
        check(state, platform)


# Checking it


def test_a_canary_that_reads_as_it_did_passes_and_freezes_nothing(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)

    assert check(state, platform) == []
    assert frozen_reason(state, "facebook") is None


def test_new_comments_on_the_canary_change_nothing(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    newcomer: Comment = {"id": "c5", "parent_id": None, "author_id": "a5", "text": "new"}
    platform.set_comments(REEL, [*canary_comments(), newcomer])

    assert check(state, platform) == []


def test_comments_no_longer_read_freeze_the_platform(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    platform.set_comments(REEL, canary_comments()[:1])

    problems = check(state, platform)

    assert problems[0] == "3 of the canary's 4 comments were not read."
    assert frozen_reason(state, "facebook") == "; ".join(problems)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"parent_id": None}, "replies: 1 expected, 0 read"),
        ({"mentions": []}, "mentions: 1 expected, 0 read"),
        ({"text": None}, "words: 3 expected, 2 read"),
        ({"author_name": None}, "author names: 4 expected, 3 read"),
        ({"created_at": None}, "times: 4 expected, 3 read"),
    ],
)
def test_a_comment_read_without_what_it_had_freezes_the_platform(
    state: sqlite3.Connection,
    change: dict[str, Any],
    problem: str,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    (first, second, third, fourth) = canary_comments()
    changed: Any = {**second, **change}
    platform.set_comments(REEL, [first, changed, third, fourth])

    assert check(state, platform) == [f"Comments with {problem}."]
    assert frozen_reason(state, "facebook") is not None


def test_a_gif_read_but_no_longer_fetched_freezes_the_platform(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    unfetchable = FakePlatform()
    unfetchable.add_post(REEL, canary_comments())

    assert check(state, unfetchable) == [
        "Comments with GIFs, stickers and images fetched: 1 expected, 0 read."
    ]


def test_a_gif_no_longer_read_freezes_the_platform(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    (first, second, third, fourth) = canary_comments()
    platform.set_comments(REEL, [first, second, {**third, "media": []}, fourth])

    assert check(state, platform) == [
        "Comments with GIFs, stickers or images: 1 expected, 0 read.",
        "Comments with GIFs, stickers and images fetched: 1 expected, 0 read.",
    ]


def test_a_hidden_comment_read_as_visible_freezes_the_platform(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    (*rest, fourth) = canary_comments()
    platform.set_comments(REEL, [*rest, {**fourth, "hidden": False}])

    assert check(state, platform) == ["Comments with hidden comments: 1 expected, 0 read."]
    assert frozen_reason(state, "facebook") is not None


def test_a_canary_that_cannot_be_read_at_all_freezes_the_platform(
    state: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)

    def broken(post: Post) -> Any:
        raise ScanError("A scan is not in the shape the workflows take: comments: bad")

    monkeypatch.setattr(platform, "scan", broken)

    (problem,) = check(state, platform)

    assert problem.startswith("The canary could not be read: A scan is not in the shape")
    assert frozen_reason(state, "facebook") == problem


def test_a_count_no_longer_read_freezes_the_platform(
    state: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, post = canary_platform()
    counted = platform.scan

    def with_count(post: Post) -> Any:
        return {**counted(post), "comment_count": 3}

    monkeypatch.setattr(platform, "scan", with_count)
    set_canary(state, platform, post)
    monkeypatch.setattr(platform, "scan", counted)

    assert check(state, platform) == ["The platform's comment count could not be read."]


def test_a_passing_check_unfreezes_the_platform(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    platform.set_comments(REEL, [])
    check(state, platform)
    platform.set_comments(REEL, canary_comments())

    assert frozen_reason(state, "instagram") is None
    assert check(state, platform) == []
    assert frozen_reason(state, "facebook") is None


# Acting only while unfrozen


def test_acting_is_allowed_on_a_platform_that_is_not_frozen(
    state: sqlite3.Connection,
) -> None:
    ensure_unfrozen(state, "facebook")


def test_acting_is_refused_on_a_frozen_platform_with_the_reason(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    platform.set_comments(REEL, canary_comments()[:3])
    check(state, platform)

    with pytest.raises(FrozenError, match="1 of the canary's 4 comments were not read") as raised:
        ensure_unfrozen(state, "facebook")

    assert "Elder Sign is frozen on facebook" in str(raised.value)
    assert "elder-sign canary check facebook" in str(raised.value)
    ensure_unfrozen(state, "instagram")


def test_acting_is_allowed_again_once_a_check_passes(
    state: sqlite3.Connection,
) -> None:
    platform, post = canary_platform()
    set_canary(state, platform, post)
    platform.set_comments(REEL, [])
    check(state, platform)
    platform.set_comments(REEL, canary_comments())
    check(state, platform)

    ensure_unfrozen(state, "facebook")


# The commands


def stand_in(
    monkeypatch: pytest.MonkeyPatch,
    platform: FakePlatform,
) -> None:
    """Stand a platform in for Facebook, for the commands."""

    import elder_sign.platforms.facebook.reader as reader

    @contextmanager
    def facebook(settings: Any = None, pacer: Any = None, **options: Any) -> Iterator[Any]:
        yield platform

    monkeypatch.setattr(reader, "facebook", facebook)


REEL_LINK = f"https://www.facebook.com/reel/{REEL}"


@pytest.fixture
def state_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Point the commands at a fresh state file."""

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))

    return path


def test_the_canary_commands_set_check_freeze_and_unfreeze(
    state_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    platform, _ = canary_platform()
    stand_in(monkeypatch, platform)

    assert main(["canary", "set", "facebook", REEL_LINK]) == 0
    set_out = capsys.readouterr().out

    assert main(["canary", "check", "facebook"]) == 0
    passed = capsys.readouterr().out

    platform.set_comments(REEL, canary_comments()[:3])
    assert main(["canary", "check", "facebook"]) == 3
    failed = capsys.readouterr().out

    assert main(["canary", "check", "facebook"]) == 3
    still_failed = capsys.readouterr().out

    platform.set_comments(REEL, canary_comments())
    assert main(["canary", "check", "facebook"]) == 0
    recovered = capsys.readouterr().out

    assert f"The canary is reel {REEL}: 4 comments" in set_out
    assert "1 reply" in set_out
    assert "1 GIF, sticker or image (1 fetched)" in set_out
    assert "1 hidden" in set_out
    assert "It has no" not in set_out
    assert "reads as it did" in passed
    assert "1 of the canary's 4 comments were not read." in failed
    assert "Elder Sign is frozen on facebook" in failed
    assert "elder-sign canary set facebook" in failed
    assert "no longer frozen" not in still_failed
    assert "no longer frozen" in recovered
    assert "no longer frozen" not in passed


def test_setting_a_canary_says_what_it_cannot_catch(
    state_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    (first, *_) = canary_comments()
    platform, _ = canary_platform([first])
    stand_in(monkeypatch, platform)

    assert main(["canary", "set", "facebook", REEL_LINK]) == 0
    assert (
        "It has no reply, mention, GIF, sticker or image, or hidden comment"
        in capsys.readouterr().out
    )


def test_checking_without_a_canary_says_how_to_set_one(
    state_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    platform, _ = canary_platform()
    stand_in(monkeypatch, platform)

    assert main(["canary", "check", "facebook"]) == 1

    assert "elder-sign canary set facebook" in capsys.readouterr().err


def test_setting_a_link_that_is_not_a_reel_is_refused(
    state_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    assert main(["canary", "set", "facebook", "https://www.facebook.com/watch/1"]) == 1
    assert "not a link to a reel" in capsys.readouterr().err
