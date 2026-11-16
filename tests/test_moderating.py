"""Tests proving `moderate` judges, plans, acts, confirms and logs, and only when told to act.

A fake platform stands in for Facebook, and a runner answers the workflows
with the verdicts each test names, so every step after judging is real: the
planner, the log, the actions, the confirming scan and the fingerprint kept
once a post is clean.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from elder_sign.audit import entries
from elder_sign.canary import FROZEN, FROZEN_FLAG, FrozenError
from elder_sign.judging import MODERATE_SCAN_ID, RESOLVE_COMMENTS_ID
from elder_sign.moderating import ModerationSettings, moderate
from elder_sign.platforms import ActionResult, Comment, FakePlatform, Post
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    read_fingerprint,
    write_flag,
)

NOW = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)

OLD = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

NEW = (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Give moderating a fresh state file, and the test a connection to it."""

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)

    try:
        yield connection
    finally:
        connection.close()


def comment(
    comment_id: str,
    *,
    parent: str | None = None,
    author: str = "someone",
    created_at: str = OLD,
    hidden: bool = False,
    mentions: list[str] | None = None,
) -> Comment:
    """Return one comment."""

    found: Comment = {
        "id": comment_id,
        "parent_id": parent,
        "author_id": author,
        "author_name": "Eris" if author == "me" else author.title(),
        "text": f"comment {comment_id}",
        "created_at": created_at,
        "hidden": hidden,
        "mentions": mentions or [],
    }

    return found


class Verdicts:
    """Answers the workflows with the verdict each comment is given: (verdict, anywhere)."""

    def __init__(
        self,
        given: dict[str, tuple[str, bool]],
    ) -> None:
        self.given = given
        self.asked: list[str] = []

    def run(self, workflow_id: str, payload: Any = None, **options: Any) -> Any:
        self.asked.append(workflow_id)

        if workflow_id == RESOLVE_COMMENTS_ID:
            return {"undescribed_media": []}

        assert workflow_id == MODERATE_SCAN_ID

        return {
            "policy": "transphobic-harassment",
            "verdicts": [
                {
                    "comment_id": entry["id"],
                    "verdict": self.given.get(entry["id"], ("friendly", False))[0],
                    "layer": "model",
                    "source": "model",
                    "anywhere": self.given.get(entry["id"], ("friendly", False))[1],
                }
                for entry in payload["comments"]
            ],
            "layers": {},
            "model": {"groups": 0, "unreadable": 0, "escalated": 0},
        }


APPLY = ModerationSettings(apply=True)

ONE_HOSTILE = {"c1": ("hostile", True)}
"""One comment, hostile anywhere and, top-level, aimed at her."""

SWARM = [
    comment("h1", author="me"),
    comment("c1"),
    comment("c2", parent="h1"),
    comment("c3", parent="c1", mentions=["Kim"]),
    comment("c4", hidden=True),
    comment("c5"),
]

VERDICTS = {
    "c1": ("hostile", True),
    "c2": ("hostile", False),
    "c3": ("hostile", True),
    "c4": ("friendly", False),
    "c5": ("uncertain", False),
}


def platform_with(
    comments: list[Comment] = SWARM,
) -> tuple[FakePlatform, Post]:
    """Return a platform holding one reel with these comments, signed in as her."""

    platform = FakePlatform(now=NOW, account_id="me")
    post = platform.add_post("reel-1", comments)

    return platform, post


def ran(
    state: sqlite3.Connection,
    platform: FakePlatform,
    post: Post,
    settings: ModerationSettings | None = None,
    verdicts: dict[str, tuple[str, bool]] = VERDICTS,
    *,
    force: bool = False,
) -> Any:
    """Moderate the reel once, now."""

    return moderate(
        state,
        platform,
        post,
        Verdicts(verdicts),
        now=NOW,
        settings=settings or ModerationSettings(),
        force=force,
    )


def ids(
    platform: FakePlatform,
    post: Post,
) -> dict[str, bool]:
    """Return each comment left on the reel, and whether it is hidden."""

    return {c["id"]: bool(c.get("hidden")) for c in platform.scan(post)["comments"]}


# A dry run


def test_by_default_nothing_is_done_and_everything_is_logged_as_a_dry_run(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()
    before = ids(platform, post)

    moderation = ran(state, platform, post)

    assert ids(platform, post) == before
    assert platform.calls["act"] == 0
    assert {entry.dry_run for entry in entries(state, "facebook")} == {True}
    assert moderation.counts() == {
        "deleted": 0,
        "hidden": 0,
        "unhidden": 0,
        "failed": 0,
        "dry_run": 4,
    }
    assert read_fingerprint(state, "facebook", "reel-1") is None


# Acting


def test_applied_it_deletes_what_is_aimed_at_her_hides_the_rest_and_logs_it_done(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()

    moderation = ran(state, platform, post, ModerationSettings(apply=True))

    assert ids(platform, post) == {"h1": False, "c2": True, "c3": True, "c4": True, "c5": False}
    assert moderation.counts() == {
        "deleted": 1,
        "hidden": 2,
        "unhidden": 0,
        "failed": 0,
        "dry_run": 1,
    }
    done = {(entry.comment_id, entry.action) for entry in entries(state, "facebook")}
    assert done == {("c1", "delete"), ("c2", "hide"), ("c3", "hide"), ("c4", "unhide")}
    assert [entry.status for entry in entries(state, "facebook") if entry.comment_id == "c1"] == [
        "done"
    ]


def test_a_reply_to_her_hostile_only_in_context_is_hidden_not_deleted(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()

    ran(state, platform, post, ModerationSettings(apply=True))

    assert ids(platform, post)["c2"] is True
    assert [e.action for e in entries(state, "facebook") if e.comment_id == "c2"] == ["hide"]


def test_unhiding_needs_its_own_word(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()

    moderation = ran(state, platform, post, ModerationSettings(apply=True, apply_unhide=True))

    assert ids(platform, post)["c4"] is False
    assert moderation.counts()["unhidden"] == 1


def test_hiding_only_never_deletes(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()

    ran(state, platform, post, ModerationSettings(apply=True, delete=False))

    assert "c1" in ids(platform, post)


# Remembering it clean


def test_a_clean_post_is_fingerprinted_and_skipped_until_it_changes(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()
    ran(state, platform, post, ModerationSettings(apply=True))
    scans = platform.calls["scan"]

    again = ran(state, platform, post, ModerationSettings(apply=True))

    assert read_fingerprint(state, "facebook", "reel-1") is not None
    assert again.skipped is True
    assert platform.calls["scan"] == scans

    platform.set_comments("reel-1", [*platform.scan(post)["comments"], comment("c9")])
    changed = ran(state, platform, post, ModerationSettings(apply=True))

    assert changed.skipped is False


def test_a_friendly_post_applied_to_is_remembered_clean_with_nothing_done(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with([comment("c1"), comment("c2")])

    moderation = ran(state, platform, post, ModerationSettings(apply=True), {})

    assert moderation.clean is True
    assert platform.calls["act"] == 0
    assert read_fingerprint(state, "facebook", "reel-1") is not None


def test_forcing_looks_again_and_redoes_nothing_done(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with()
    ran(state, platform, post, ModerationSettings(apply=True))

    again = ran(state, platform, post, ModerationSettings(apply=True), force=True)

    assert again.skipped is False
    assert again.counts()["deleted"] == 0


def test_a_post_with_a_comment_too_new_to_act_on_is_not_remembered_clean(
    state: sqlite3.Connection,
) -> None:
    platform, post = platform_with([comment("c1", created_at=NEW)])

    moderation = ran(state, platform, post, APPLY, ONE_HOSTILE)

    assert moderation.plan.waiting == ("c1",)
    assert read_fingerprint(state, "facebook", "reel-1") is None


# When an action does not take


def test_an_action_that_fails_is_logged_failed_and_the_post_is_not_remembered_clean(
    state: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, post = platform_with([comment("c1")])

    def failing(*arguments: Any, **options: Any) -> list[ActionResult]:
        return [ActionResult("c1", "delete", False, "the comment's menu button was not found")]

    monkeypatch.setattr(platform, "act", failing)

    moderation = ran(state, platform, post, APPLY, ONE_HOSTILE)

    (entry,) = entries(state, "facebook")

    assert (entry.status, entry.detail) == ("failed", "the comment's menu button was not found")
    assert moderation.counts()["failed"] == 1
    assert read_fingerprint(state, "facebook", "reel-1") is None


def test_an_action_the_fresh_scan_shows_did_not_take_is_logged_failed(
    state: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, post = platform_with([comment("c1")])

    def claiming(*arguments: Any, **options: Any) -> list[ActionResult]:
        return [ActionResult("c1", "delete", True)]

    monkeypatch.setattr(platform, "act", claiming)

    ran(state, platform, post, APPLY, ONE_HOSTILE)

    (entry,) = entries(state, "facebook")

    assert (entry.status, entry.detail) == ("failed", "it is still there after deleting")


def test_a_delete_facebook_did_not_offer_is_logged_as_the_hide_it_became(
    state: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, post = platform_with([comment("c1")])
    acting = platform.act

    def instead(post: Post, actions: Any, hidden: Any, **options: Any) -> list[ActionResult]:
        acting(post, [("c1", "hide")], hidden)
        return [ActionResult("c1", "hide", True, "Facebook offers no Delete here")]

    monkeypatch.setattr(platform, "act", instead)

    ran(state, platform, post, APPLY, ONE_HOSTILE)

    (entry,) = entries(state, "facebook")

    assert (entry.action, entry.status) == ("delete", "done")
    assert entry.detail == "hidden instead: Facebook offers no Delete here"


# Never while frozen


def test_nothing_is_read_or_done_while_frozen(
    state: sqlite3.Connection,
) -> None:
    write_flag(state, FROZEN_FLAG.format(platform="facebook"), FROZEN, reason="lost replies")
    platform, post = platform_with()

    with pytest.raises(FrozenError):
        ran(state, platform, post, ModerationSettings(apply=True))

    assert platform.calls["scan"] == 0
    assert entries(state, "facebook") == []


# The command


def test_the_moderate_command_dry_runs_a_reel_and_says_what_it_would_do(
    state: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from contextlib import contextmanager

    import elder_sign.cli as command
    import elder_sign.platforms.facebook.reader as reader

    platform = FakePlatform(now=datetime.now(UTC) + timedelta(hours=2), account_id="me")
    platform.add_post("111", SWARM)

    @contextmanager
    def facebook(settings: Any = None, pacer: Any = None, **options: Any) -> Iterator[Any]:
        yield platform

    monkeypatch.setattr(reader, "facebook", facebook)
    monkeypatch.setattr(command, "scans_path", lambda: tmp_path)
    options = command.build_parser().parse_args(
        ["moderate", "facebook", "https://www.facebook.com/reel/111"]
    )

    assert command._moderate(options, runner=Verdicts(VERDICTS)) == 0

    printed = capsys.readouterr().out

    assert "Reel 111: dry run: would delete 1, hide 2, unhide 1" in printed
    assert "1 uncertain left for you" in printed
    assert "Nothing on Facebook was changed: add --apply to act." in printed
    assert (tmp_path / "facebook-111-verdicts.json").exists()


def test_the_moderate_command_does_nothing_while_frozen(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import elder_sign.cli as command

    write_flag(state, FROZEN_FLAG.format(platform="facebook"), FROZEN, reason="lost replies")
    options = command.build_parser().parse_args(
        ["moderate", "facebook", "https://www.facebook.com/reel/111", "--apply"]
    )

    assert command._moderate(options, runner=Verdicts({})) == 3
    assert "frozen on facebook" in capsys.readouterr().err
