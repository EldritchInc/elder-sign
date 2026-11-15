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


def assembled(
    judgment: Any,
    group: dict[str, Any] = GROUP,
    **inputs: Any,
) -> dict[str, Any]:
    """Run the built assemble_judgment on one group and one judgment, returning all it returns."""

    return run_tool("assemble_judgment", {"group": group, "judgment": judgment, **inputs})


def assemble(
    judgment: Any,
    group: dict[str, Any] = GROUP,
) -> dict[str, Any]:
    """Run the built assemble_judgment on one group and one judgment."""

    judged: dict[str, Any] = assembled(judgment, group)["judged"]

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
        "escalated": False,
    }


# Escalating to a stronger model


@pytest.mark.parametrize(
    ("judgment", "escalate"),
    [
        ({"verdict": "uncertain"}, True),
        ({"verdict": "hostile", "context_independent": False}, True),
        ({"verdict": "hostile"}, True),
        ("not an answer", True),
        ({"verdict": "hostile", "context_independent": True}, False),
        ({"verdict": "friendly"}, False),
        ({"verdict": "friendly", "context_independent": True}, False),
    ],
    ids=[
        "uncertain",
        "hostile in context",
        "hostile, unsaid where",
        "unreadable",
        "hostile anywhere",
        "friendly",
        "friendly anywhere",
    ],
)
def test_what_is_uncertain_or_hostile_only_in_context_is_escalated(
    judgment: Any,
    escalate: bool,
) -> None:
    assert assembled(judgment)["escalate"] is escalate


def test_a_stronger_models_judgment_is_marked_and_never_escalated_again() -> None:
    output = assembled({"verdict": "uncertain"}, escalated=True, model="openrouter/opus")

    assert output["escalate"] is False
    assert output["judged"]["escalated"] is True
    assert output["judged"]["model"] == "openrouter/opus"


def test_a_verdict_is_read_whatever_its_case_or_spacing() -> None:
    assert assemble({"verdict": "  Friendly "})["verdict"] == "friendly"


def test_the_model_that_judged_is_kept_when_known() -> None:
    judged = run_tool(
        "assemble_judgment",
        {"group": GROUP, "judgment": {"verdict": "friendly"}, "model": "openrouter/x"},
    )["judged"]

    assert judged["model"] == "openrouter/x"


# A reply, as the prompt step gives it


@pytest.mark.parametrize(
    "reply",
    [
        '{"verdict": "hostile", "context_independent": true}',
        '```json\n{"verdict": "hostile", "context_independent": true}\n```',
        '```\n{"verdict": "hostile", "context_independent": true}\n```',
        'Sure! My answer: {"verdict": "hostile", "context_independent": true} Hope it helps.',
        '  \n{"verdict": "hostile", "context_independent": true}\n  ',
    ],
    ids=["json", "fenced", "bare fence", "wrapped in words", "whitespace"],
)
def test_a_judgment_is_read_out_of_the_reply_however_it_was_written(
    reply: str,
) -> None:
    judged = assemble(reply)

    assert (judged["verdict"], judged["context_independent"], judged["valid"]) == (
        "hostile",
        True,
        True,
    )


def test_a_reply_that_holds_no_json_is_unreadable() -> None:
    judged = assemble("I would say this is probably hostile {but who knows}")

    assert (judged["verdict"], judged["valid"]) == ("uncertain", False)


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
