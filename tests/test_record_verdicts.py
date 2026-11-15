"""Tests proving record_verdicts remembers a model's verdicts where they hold, and only there."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from azathoth.tools import ToolExecutionError

from elder_sign.shared.store import Verdict, connect, read_verdict, write_verdict
from tests.test_lookup_verdicts import GIF, POLICY, comment, decided, lookup
from tests.tools import grant_state_file, run_tool


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Give the tools a fresh state file, and the test a connection to it."""

    connection = connect(grant_state_file(monkeypatch, tmp_path))

    try:
        yield connection
    finally:
        connection.close()


def judge(
    group: dict[str, Any],
    verdict: str,
    *,
    independent: bool,
    media_hostile: bool = False,
) -> dict[str, Any]:
    """Return one group of lookup_verdicts as a model judged it."""

    return {
        "content_key": group["content_key"],
        "context_key": group["context_key"],
        "media_keys": group["media_keys"],
        "comment_ids": group["comment_ids"],
        "verdict": verdict,
        "context_independent": independent,
        "media_hostile": media_hostile,
        "model": "test-model",
        "reason": "because",
    }


def record(
    *judged: dict[str, Any],
) -> dict[str, Any]:
    """Run the built record_verdicts on some judged groups."""

    return run_tool("record_verdicts", {"judged": list(judged), "settings": {"policy": POLICY}})


def verdict_rows(
    state: sqlite3.Connection,
) -> int:
    """Return how many verdicts the state file keeps."""

    (count,) = state.execute("SELECT count(*) FROM verdicts").fetchone()
    found: int = count

    return found


# Where a verdict is remembered


def test_a_verdict_that_holds_anywhere_decides_every_copy_in_any_thread(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "you will never be a woman"))["needs_inference"]

    output = record(judge(group, "hostile", independent=True))

    again = lookup(
        comment("p1", "love you", author="friend"),
        comment("c2", "YOU WILL NEVER BE A WOMAN", parent="p1"),
    )

    assert output["recorded"] == 2
    assert decided(again)["c2"] == ("hostile", "content")


def test_a_verdict_that_depends_on_its_parent_decides_only_under_that_parent(
    state: sqlite3.Connection,
) -> None:
    thread = (comment("p1", "trans women are men"), comment("r1", "lol exactly", parent="p1"))
    groups = {group["comment"]["id"]: group for group in lookup(*thread)["needs_inference"]}

    record(judge(groups["r1"], "hostile", independent=False))

    again = lookup(
        comment("p1", "trans women are men"),
        comment("r2", "lol exactly", parent="p1", author="t2"),
        comment("p2", "trans women are women", author="friend"),
        comment("r3", "lol exactly", parent="p2", author="friend-2"),
        comment("r4", "lol exactly", author="t3"),
    )

    assert decided(again)["r2"] == ("hostile", "context")
    assert "r3" not in decided(again)
    assert "r4" not in decided(again)


def test_a_context_dependent_verdict_on_an_orphan_is_not_remembered(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("r1", "lol exactly", parent="gone"))["needs_inference"]

    output = record(judge(group, "uncertain", independent=False))

    assert output["recorded"] == 0
    assert verdict_rows(state) == 0
    assert output["resolved"] == [
        {"comment_id": "r1", "verdict": "uncertain", "layer": "model", "source": "model"}
    ]


def test_hostility_in_the_gif_itself_catches_the_gif_with_any_words(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("g1", None, media=[{"kind": "gif", "url": GIF}]))["needs_inference"]

    output = record(judge(group, "hostile", independent=True, media_hostile=True))

    again = lookup(comment("g2", "so true", media=[{"kind": "gif", "url": GIF + "?cid=2"}]))

    assert output["recorded"] == 3
    assert decided(again) == {"g2": ("hostile", "media")}


def test_a_friendly_gif_is_never_remembered_as_media(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("g1", None, media=[{"kind": "gif", "url": GIF}]))["needs_inference"]

    record(judge(group, "friendly", independent=True, media_hostile=True))

    (media_key,) = group["media_keys"]

    assert read_verdict(state, POLICY, "content", media_key) is None


def test_the_model_and_its_reason_are_kept(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "ew"))["needs_inference"]

    record(judge(group, "hostile", independent=True))

    assert read_verdict(state, POLICY, "content", group["content_key"]) == Verdict(
        verdict="hostile", source="model", model="test-model", reason="because"
    )


# Every comment of every group


def test_every_comment_of_a_group_gets_the_groups_verdict(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(
        comment("c1", "groomer", author="t1"),
        comment("c2", "GR00MER!!!", author="t2"),
        comment("c3", "you look amazing", author="friend"),
    )
    groups = {group["comment"]["id"]: group for group in resolution["needs_inference"]}

    output = record(
        judge(groups["c1"], "hostile", independent=True),
        judge(groups["c3"], "friendly", independent=True),
    )

    assert {entry["comment_id"]: entry["verdict"] for entry in output["resolved"]} == {
        "c1": "hostile",
        "c2": "hostile",
        "c3": "friendly",
    }


def test_nothing_judged_records_nothing(
    state: sqlite3.Connection,
) -> None:
    assert record() == {"resolved": [], "recorded": 0}


# Safe to repeat, and never over a human


def test_recording_the_same_verdicts_again_changes_nothing(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "ew"))["needs_inference"]
    judged = judge(group, "hostile", independent=True)

    first = record(judged)
    rows = verdict_rows(state)
    second = record(judged)

    assert first["resolved"] == second["resolved"]
    assert verdict_rows(state) == rows == 2


def test_a_model_never_replaces_a_humans_verdict(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "sir"))["needs_inference"]
    human = Verdict(verdict="friendly", source="human", model=None, reason="inside joke")
    write_verdict(state, POLICY, "content", group["content_key"], human)

    output = record(judge(group, "hostile", independent=True))

    assert output["recorded"] == 1
    assert read_verdict(state, POLICY, "content", group["content_key"]) == human


def test_one_invalid_verdict_records_none_of_them(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(comment("c1", "ew"), comment("c2", "wow"))
    good, bad = resolution["needs_inference"]

    with pytest.raises(ToolExecutionError):
        record(judge(good, "hostile", independent=True), judge(bad, "spicy", independent=True))

    assert verdict_rows(state) == 0
