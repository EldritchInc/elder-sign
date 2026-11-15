"""Tests proving the planner turns a scan's verdicts into the actions to take, and nothing more.

The planner is pure: it is given a scan, the verdicts `judge` reached, and
the time, and returns what would be deleted, hidden and unhidden, what is left
for the person, and what is too new to act on yet. Nothing here touches a
platform.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from elder_sign.planning import Action, PlanSettings, plan

NOW = datetime(2026, 11, 15, 17, 0, tzinfo=UTC)

OLD = (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

NEW = (NOW - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(
    comment_id: str,
    *,
    parent: str | None = None,
    author: str = "someone",
    created_at: str | None = OLD,
    hidden: bool = False,
) -> dict[str, Any]:
    """Return one comment of a scan."""

    return {
        "id": comment_id,
        "parent_id": parent,
        "author_id": author,
        "created_at": created_at,
        "hidden": hidden,
    }


def verdict(
    comment_id: str,
    value: str,
    layer: str = "model",
    *,
    anywhere: bool = False,
) -> dict[str, Any]:
    """Return one verdict of an outcome."""

    return {
        "comment_id": comment_id,
        "verdict": value,
        "layer": layer,
        "source": "model",
        "anywhere": anywhere,
    }


def planned(
    comments: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    settings: PlanSettings | None = None,
    owner: str | None = "me",
) -> Any:
    """Plan a scan of these comments under these verdicts, now."""

    scan: Any = {"platform": "facebook", "post_id": "reel-1", "comments": comments}

    if owner is not None:
        scan["owner_id"] = owner

    return plan(scan, {"verdicts": verdicts}, now=NOW, settings=settings or PlanSettings())


def actions(
    result: Any,
) -> list[tuple[str, str]]:
    """Return a plan's actions as (comment, action)."""

    return [(action.comment_id, action.action) for action in result.actions]


# Delete, hide, leave


def test_what_is_hostile_anywhere_is_deleted_and_what_is_hostile_in_context_hidden() -> None:
    result = planned(
        [comment("c1"), comment("c2"), comment("c3")],
        [
            verdict("c1", "hostile", anywhere=True),
            verdict("c2", "hostile"),
            verdict("c3", "hostile", "known_troll"),
        ],
    )

    assert actions(result) == [("c1", "delete"), ("c2", "hide"), ("c3", "hide")]


def test_hiding_only_never_deletes() -> None:
    result = planned(
        [comment("c1")],
        [verdict("c1", "hostile", anywhere=True)],
        PlanSettings(delete=False),
    )

    assert actions(result) == [("c1", "hide")]


def test_what_is_uncertain_is_left_for_the_person_and_what_is_friendly_alone() -> None:
    result = planned(
        [comment("c1"), comment("c2")],
        [verdict("c1", "uncertain"), verdict("c2", "friendly")],
    )

    assert actions(result) == []
    assert result.left == ("c1",)


def test_a_comment_without_a_verdict_is_never_acted_on() -> None:
    assert actions(planned([comment("held")], [])) == []


# Never touched


def test_her_own_comments_and_trusted_authors_are_never_touched() -> None:
    result = planned(
        [comment("c1", author="me"), comment("c2", author="bestie")],
        [
            verdict("c1", "hostile", anywhere=True),
            verdict("c2", "hostile", "trusted_author", anywhere=True),
        ],
    )

    assert actions(result) == []
    assert result.left == ()


# Three minutes


def test_a_hostile_comment_is_acted_on_only_once_it_is_three_minutes_old() -> None:
    result = planned(
        [comment("c1", created_at=NEW), comment("c2", created_at=None), comment("c3")],
        [
            verdict("c1", "hostile", anywhere=True),
            verdict("c2", "hostile", anywhere=True),
            verdict("c3", "hostile", anywhere=True),
        ],
    )

    assert actions(result) == [("c3", "delete")]
    assert result.waiting == ("c1", "c2")


def test_how_long_to_wait_is_a_setting() -> None:
    result = planned(
        [comment("c1", created_at=NEW)],
        [verdict("c1", "hostile")],
        PlanSettings(minimum_age=timedelta(minutes=1)),
    )

    assert actions(result) == [("c1", "hide")]


# Hidden comments


def test_a_hidden_comment_hostile_in_context_stays_hidden_and_one_hostile_anywhere_goes() -> None:
    result = planned(
        [comment("c1", hidden=True), comment("c2", hidden=True)],
        [verdict("c1", "hostile"), verdict("c2", "hostile", anywhere=True)],
    )

    assert actions(result) == [("c2", "delete")]


def test_a_friendly_comment_the_filter_hid_is_unhidden_in_dry_run_only() -> None:
    result = planned([comment("c1", hidden=True)], [verdict("c1", "friendly")])

    (unhide,) = result.actions

    assert unhide == Action(comment_id="c1", action="unhide", layer="model", dry_run=True)


def test_unhiding_for_real_is_a_setting() -> None:
    result = planned(
        [comment("c1", hidden=True)], [verdict("c1", "friendly")], PlanSettings(unhide=True)
    )

    assert result.actions[0].dry_run is False


def test_an_uncertain_hidden_comment_stays_hidden_and_is_left_for_the_person() -> None:
    result = planned([comment("c1", hidden=True)], [verdict("c1", "uncertain")])

    assert actions(result) == []
    assert result.left == ("c1",)


# Order and caps


def test_replies_are_deleted_before_what_they_reply_to() -> None:
    result = planned(
        [comment("c1"), comment("c2", parent="c1"), comment("c3", parent="c2"), comment("c4")],
        [verdict(cid, "hostile", anywhere=True) for cid in ("c1", "c2", "c3", "c4")],
    )

    deletes = [cid for cid, action in actions(result) if action == "delete"]

    assert deletes.index("c3") < deletes.index("c2") < deletes.index("c1")


def test_deletes_come_first_then_hides_then_unhides() -> None:
    result = planned(
        [comment("c1", hidden=True), comment("c2"), comment("c3")],
        [
            verdict("c1", "friendly"),
            verdict("c2", "hostile"),
            verdict("c3", "hostile", anywhere=True),
        ],
    )

    assert [action for _, action in actions(result)] == ["delete", "hide", "unhide"]


def test_no_more_than_the_cap_is_acted_on_in_one_run_and_the_rest_deferred() -> None:
    comments = [comment(f"c{index}") for index in range(5)]
    verdicts = [verdict(f"c{index}", "hostile", anywhere=True) for index in range(5)]

    result = planned(comments, verdicts, PlanSettings(maximum_actions=3))

    assert len(result.actions) == 3
    assert len(result.deferred) == 2


def test_unhides_have_their_own_cap() -> None:
    comments = [comment(f"c{index}", hidden=True) for index in range(4)]
    verdicts = [verdict(f"c{index}", "friendly") for index in range(4)]

    result = planned(comments, verdicts, PlanSettings(maximum_unhides=1))

    assert actions(result) == [("c0", "unhide")]
    assert result.deferred == ("c1", "c2", "c3")


# Counts


def test_a_plan_says_what_it_would_do_in_numbers() -> None:
    result = planned(
        [
            comment("c1"),
            comment("c2"),
            comment("c3", hidden=True),
            comment("c4"),
            comment("c5", created_at=NEW),
        ],
        [
            verdict("c1", "hostile", anywhere=True),
            verdict("c2", "hostile"),
            verdict("c3", "friendly"),
            verdict("c4", "uncertain"),
            verdict("c5", "hostile"),
        ],
    )

    assert result.counts() == {
        "delete": 1,
        "hide": 1,
        "unhide": 1,
        "left": 1,
        "waiting": 1,
        "deferred": 0,
    }


@pytest.mark.parametrize("maximum", [0, -1])
def test_a_cap_must_allow_something(
    maximum: int,
) -> None:
    with pytest.raises(ValueError):
        PlanSettings(maximum_actions=maximum)
