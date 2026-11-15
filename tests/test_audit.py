"""Tests proving every action Elder Sign plans or takes is logged, and nothing done is redone.

The log keeps no comment's words: only which comment, what was done to it,
why, whether it was a dry run, and how it went. It is what she reads to see
what Elder Sign did in her name.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elder_sign.audit import Entry, entries, pending, planned, settled
from elder_sign.planning import Action, Plan
from elder_sign.shared.store import DATABASE_ENVIRONMENT_VARIABLE, connect


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Give the log a fresh state file, and the test a connection to it."""

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)

    try:
        yield connection
    finally:
        connection.close()


DELETE = Action(comment_id="c1", action="delete", layer="content")

HIDE = Action(comment_id="c2", action="hide", layer="context")

UNHIDE = Action(comment_id="c3", action="unhide", layer="she_answered", dry_run=True)


# Logging


def test_a_planned_action_is_logged_and_then_settled(
    state: sqlite3.Connection,
) -> None:
    logged = planned(state, "facebook", "reel-1", DELETE, verdict="hostile")
    settled(state, logged, "done")

    (entry,) = entries(state, "facebook")

    assert entry == Entry(
        id=logged,
        platform="facebook",
        post_id="reel-1",
        comment_id="c1",
        action="delete",
        verdict="hostile",
        layer="content",
        dry_run=False,
        status="done",
        detail=None,
        recorded_at=entry.recorded_at,
    )


def test_a_failure_is_logged_with_why(
    state: sqlite3.Connection,
) -> None:
    logged = planned(state, "facebook", "reel-1", HIDE, verdict="hostile")
    settled(state, logged, "failed", "the comment's menu had no Hide")

    (entry,) = entries(state, "facebook")

    assert (entry.status, entry.detail) == ("failed", "the comment's menu had no Hide")


def test_a_dry_run_is_logged_as_one(
    state: sqlite3.Connection,
) -> None:
    planned(state, "facebook", "reel-1", UNHIDE, verdict="friendly")

    (entry,) = entries(state, "facebook")

    assert (entry.dry_run, entry.status) == (True, "planned")


def test_an_action_can_only_be_settled_as_done_or_failed(
    state: sqlite3.Connection,
) -> None:
    logged = planned(state, "facebook", "reel-1", DELETE, verdict="hostile")

    with pytest.raises(ValueError):
        settled(state, logged, "planned")


# Reading the log


def test_the_log_is_read_newest_first_by_post_and_since_a_time(
    state: sqlite3.Connection,
) -> None:
    planned(state, "facebook", "reel-1", DELETE, verdict="hostile")
    planned(state, "facebook", "reel-2", HIDE, verdict="hostile")
    planned(state, "instagram", "post-1", HIDE, verdict="hostile")

    assert [entry.post_id for entry in entries(state, "facebook")] == ["reel-2", "reel-1"]
    assert [entry.post_id for entry in entries(state, "facebook", post_id="reel-1")] == ["reel-1"]
    assert entries(state, "facebook", since=datetime.now(UTC) + timedelta(days=1)) == []
    assert len(entries(state, "facebook", limit=1)) == 1


# Never twice


def test_what_was_done_is_never_planned_again_and_what_failed_or_was_dry_run_is(
    state: sqlite3.Connection,
) -> None:
    done = planned(state, "facebook", "reel-1", DELETE, verdict="hostile")
    settled(state, done, "done")
    failed = planned(state, "facebook", "reel-1", HIDE, verdict="hostile")
    settled(state, failed, "failed", "no menu")
    planned(state, "facebook", "reel-1", UNHIDE, verdict="friendly")

    left = pending(state, "facebook", Plan(actions=(DELETE, HIDE, UNHIDE)))

    assert left.actions == (HIDE, UNHIDE)


def test_a_different_action_on_a_comment_done_before_is_still_planned(
    state: sqlite3.Connection,
) -> None:
    hidden = planned(state, "facebook", "reel-1", Action("c1", "hide", "context"), "hostile")
    settled(state, hidden, "done")

    assert pending(state, "facebook", Plan(actions=(DELETE,))).actions == (DELETE,)


def test_what_a_plan_leaves_is_kept_when_its_done_actions_are_dropped(
    state: sqlite3.Connection,
) -> None:
    done = planned(state, "facebook", "reel-1", DELETE, verdict="hostile")
    settled(state, done, "done")

    left = pending(state, "facebook", Plan(actions=(DELETE,), left=("c9",), waiting=("c8",)))

    assert (left.actions, left.left, left.waiting) == ((), ("c9",), ("c8",))


# The command


def test_the_log_command_prints_the_log_without_a_word_of_any_comment(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    done = planned(state, "facebook", "2027199361330354", DELETE, verdict="hostile")
    settled(state, done, "done")
    failed = planned(state, "facebook", "2027199361330354", HIDE, verdict="hostile")
    settled(state, failed, "failed", "no menu")

    assert main(["log", "facebook"]) == 0

    printed = capsys.readouterr().out

    assert "done    delete" in printed
    assert "failed  hide" in printed
    assert "no menu" in printed
    assert "2 actions" in printed


def test_the_log_command_reads_one_reel_by_its_link(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    planned(state, "facebook", "111", DELETE, verdict="hostile")
    planned(state, "facebook", "222", HIDE, verdict="hostile")

    assert main(["log", "facebook", "--reel", "https://www.facebook.com/reel/222"]) == 0
    assert "1 action" in capsys.readouterr().out
