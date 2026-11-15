"""Tests proving a post is scanned only when its comments changed since it was cleaned."""

from pathlib import Path
from typing import Any

import pytest
from azathoth.tools import ToolExecutionError

from tests.tools import grant_state_file, run_tool


@pytest.fixture(autouse=True)
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Give every test its own state file."""

    return grant_state_file(monkeypatch, tmp_path)


def post(
    count: int,
    newest: str | None,
    *,
    post_id: str = "reel-1",
    platform: str = "facebook",
) -> dict[str, Any]:
    """Return a post as an adapter describes it."""

    return {
        "platform": platform,
        "post_id": post_id,
        "comment_count": count,
        "newest_comment_id": newest,
    }


def check(
    described: dict[str, Any],
) -> dict[str, Any]:
    """Ask whether the post changed."""

    return run_tool("check_post_fingerprint", {"post": described})


def record(
    described: dict[str, Any],
) -> None:
    """Keep the post's fingerprint."""

    assert run_tool("record_post_fingerprint", {"post": described}) == {"recorded": True}


def test_a_post_never_cleaned_has_changed() -> None:
    assert check(post(3, "c3")) == {"changed": True, "previous": None}


def test_checking_does_not_record() -> None:
    check(post(3, "c3"))

    assert check(post(3, "c3"))["changed"]


def test_a_post_unchanged_since_it_was_cleaned_has_not_changed() -> None:
    record(post(3, "c3"))

    assert check(post(3, "c3")) == {
        "changed": False,
        "previous": {"comment_count": 3, "newest_comment_id": "c3"},
    }


@pytest.mark.parametrize(
    "now",
    [post(4, "c4"), post(4, "c3"), post(2, "c3"), post(3, "c9")],
    ids=["comment added", "count grew", "comment removed", "one added and one removed"],
)
def test_any_change_since_cleaning_is_a_change(
    now: dict[str, Any],
) -> None:
    record(post(3, "c3"))

    assert check(now)["changed"]


def test_a_post_with_no_comments_left_is_fingerprinted_too() -> None:
    record(post(0, None))

    assert not check(post(0, None))["changed"]
    assert check(post(1, "c1"))["changed"]


def test_a_newest_comment_may_be_left_out() -> None:
    record({"platform": "facebook", "post_id": "reel-1", "comment_count": 0})

    assert not check({"platform": "facebook", "post_id": "reel-1", "comment_count": 0})["changed"]


def test_recording_replaces_the_fingerprint_before() -> None:
    record(post(3, "c3"))
    record(post(5, "c5"))
    record(post(5, "c5"))

    assert not check(post(5, "c5"))["changed"]
    assert check(post(3, "c3"))["changed"]


def test_posts_and_platforms_are_fingerprinted_apart() -> None:
    record(post(3, "c3"))

    assert check(post(3, "c3", post_id="reel-2"))["changed"]
    assert check(post(3, "c3", platform="instagram"))["changed"]


def test_a_negative_count_fails_the_tool() -> None:
    with pytest.raises(ToolExecutionError):
        record(post(-1, None))


def test_the_fingerprint_lives_in_the_granted_state_file(
    state: Path,
) -> None:
    record(post(3, "c3"))

    assert state.exists()
