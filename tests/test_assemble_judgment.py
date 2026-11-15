"""Tests proving assemble_judgment takes a model at its word only as far as its word is sound."""

import sqlite3
from typing import Any

import pytest

from elder_sign.policy import TRANSPHOBIC_HARASSMENT
from elder_sign.shared.store import read_verdict
from elder_sign.tools.assemble_judgment import MAXIMUM_REASON_LENGTH, UNREADABLE
from tests.test_lookup_verdicts import GIF, comment, decided, lookup, state
from tests.tools import run_tool

__all__ = ["state"]

GROUP = {
    "content_key": "c:1",
    "context_key": "x:1",
    "media_keys": ["m:giphy:abc"],
    "comment_ids": ["c1", "c2"],
    "comment": {"id": "c1", "text": "lol"},
    "parent": None,
}


def assemble(
    judgment: Any,
    group: dict[str, Any] = GROUP,
) -> dict[str, Any]:
    """Run the built assemble_judgment on one group and one judgment."""

    judged: dict[str, Any] = run_tool("assemble_judgment", {"group": group, "judgment": judgment})[
        "judged"
    ]

    return judged


# A sound judgment


def test_a_sound_judgment_is_taken_as_given_and_joined_to_its_group() -> None:
    judged = assemble(
        {"verdict": "hostile", "context_independent": True, "media_hostile": True, "reason": "slur"}
    )

    assert judged == {
        "content_key": "c:1",
        "context_key": "x:1",
        "media_keys": ["m:giphy:abc"],
        "comment_ids": ["c1", "c2"],
        "verdict": "hostile",
        "context_independent": True,
        "media_hostile": True,
        "model": None,
        "reason": "slur",
        "valid": True,
    }


def test_a_verdict_is_read_whatever_its_case_or_spacing() -> None:
    assert assemble({"verdict": "  Friendly "})["verdict"] == "friendly"


def test_the_model_that_judged_is_kept_when_known() -> None:
    judged = run_tool(
        "assemble_judgment",
        {"group": GROUP, "judgment": {"verdict": "friendly"}, "model": "openrouter/x"},
    )["judged"]

    assert judged["model"] == "openrouter/x"


# An unsound judgment


@pytest.mark.parametrize(
    "judgment",
    [
        "hostile",
        None,
        [],
        {},
        {"verdict": "spicy"},
        {"verdict": True},
        {"verdict": None},
    ],
    ids=["text", "nothing", "list", "empty", "unknown verdict", "boolean", "null verdict"],
)
def test_an_answer_with_no_verdict_is_uncertain_and_remembered_narrowly(
    judgment: Any,
) -> None:
    judged = assemble(judgment)

    assert (judged["verdict"], judged["context_independent"], judged["media_hostile"]) == (
        "uncertain",
        False,
        False,
    )
    assert judged["valid"] is False
    assert judged["reason"] == UNREADABLE


@pytest.mark.parametrize("flag", ["true", 1, "yes", None])
def test_only_exactly_true_holds_a_verdict_in_every_thread(
    flag: Any,
) -> None:
    judged = assemble({"verdict": "hostile", "context_independent": flag})

    assert judged["context_independent"] is False
    assert judged["valid"] is True


def test_media_is_hostile_only_with_a_hostile_verdict_on_a_group_with_media() -> None:
    assert not assemble({"verdict": "friendly", "media_hostile": True})["media_hostile"]
    assert not assemble({"verdict": "uncertain", "media_hostile": True})["media_hostile"]
    assert not assemble({"verdict": "hostile", "media_hostile": "true"})["media_hostile"]
    assert not assemble({"verdict": "hostile", "media_hostile": True}, {**GROUP, "media_keys": []})[
        "media_hostile"
    ]


def test_a_reason_is_kept_only_as_text_and_cut_short() -> None:
    assert assemble({"verdict": "hostile", "reason": 42})["reason"] is None
    assert assemble({"verdict": "hostile", "reason": "  "})["reason"] is None
    assert len(assemble({"verdict": "hostile", "reason": "x" * 999})["reason"]) == (
        MAXIMUM_REASON_LENGTH
    )


# Into record_verdicts


def test_an_assembled_judgment_is_what_record_verdicts_remembers(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(
        comment("c1", "you will never be a woman", media=[{"kind": "gif", "url": GIF}]),
        comment("c2", "YOU WILL NEVER BE A WOMAN", media=[{"kind": "gif", "url": GIF}]),
    )
    (group,) = resolution["needs_inference"]

    judged = assemble(
        {"verdict": "hostile", "context_independent": True, "media_hostile": False}, group
    )
    recorded = run_tool(
        "record_verdicts",
        {"judged": [judged], "settings": {"policy": TRANSPHOBIC_HARASSMENT}},
    )

    assert {entry["comment_id"] for entry in recorded["resolved"]} == {"c1", "c2"}
    assert read_verdict(state, TRANSPHOBIC_HARASSMENT, "content", group["content_key"])
    again = lookup(comment("c3", "You will NEVER be a woman", media=[{"kind": "gif", "url": GIF}]))

    assert decided(again) == {"c3": ("hostile", "content")}


def test_an_unreadable_judgment_is_remembered_only_in_its_context(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "hmm"))["needs_inference"]

    run_tool(
        "record_verdicts",
        {"judged": [assemble("???", group)], "settings": {"policy": TRANSPHOBIC_HARASSMENT}},
    )

    assert read_verdict(state, TRANSPHOBIC_HARASSMENT, "content", group["content_key"]) is None
    assert read_verdict(state, TRANSPHOBIC_HARASSMENT, "context", group["context_key"])
