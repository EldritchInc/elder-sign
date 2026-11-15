"""Tests proving allies and trolls are learned from what they write, and settle what is open.

An author's standing is learned from the verdicts on what they wrote before,
shown to the model beside their name, and used once a model has spoken: an
ally's uncertain or in-context hostile comment is friendly, a troll's
uncertain one hostile. A verdict that holds anywhere is never changed by who
wrote it.
"""

import sqlite3
from typing import Any

import pytest

from elder_sign.policy import TRANSPHOBIC_HARASSMENT
from elder_sign.shared.store import read_author_verdicts, read_reputation, write_author_verdict
from tests.test_lookup_verdicts import comment, lookup, state
from tests.test_moderate_scan import moderate, verdicts
from tests.test_moderate_scan import state as installed
from tests.tools import run_tool

__all__ = ["installed", "state"]

POLICY = TRANSPHOBIC_HARASSMENT


def earn(
    connection: sqlite3.Connection,
    author: str,
    verdict: str,
    count: int,
    *,
    anywhere: bool = False,
) -> None:
    """Give an author a record of comments judged one way, on another post."""

    for index in range(count):
        write_author_verdict(
            connection,
            POLICY,
            "facebook",
            author,
            "earlier-reel",
            f"{author}-earlier-{index}",
            verdict,
            anywhere=anywhere,
            source="model",
        )


# What the model is shown


def test_a_known_author_is_named_with_their_standing_and_her_comments_are_not(
    state: sqlite3.Connection,
) -> None:
    earn(state, "jeffy", "hostile", 2, anywhere=True)
    earn(state, "wendy", "friendly", 3)

    resolution = lookup(
        comment("c1", "never get why you'd cut it off", author="jeffy", author_name="Jeffy Jeff"),
        comment("c2", "Jeffy Jeff it gets inverted", parent="c1", author="wendy"),
        comment("c3", "this", parent="c1", author="stranger"),
    )
    (first, second, third) = resolution["needs_inference"]

    assert first["comment"]["standing"] == "troll"
    assert (second["comment"]["standing"], second["parent"]["standing"]) == ("ally", "troll")
    assert "standing" not in third["comment"]
    assert resolution["reputations"] == {"c1": "troll", "c2": "ally"}


def test_the_persons_own_account_is_never_given_a_standing(
    state: sqlite3.Connection,
) -> None:
    earn(state, "me", "friendly", 3)

    resolution = lookup(comment("c1", "hi", author="me"), owner_id="me")

    assert resolution["reputations"] == {}
    assert resolution["needs_inference"][0]["comment"].get("standing") is None


@pytest.mark.parametrize(
    ("standing", "note"),
    [("ally", "has defended her before"), ("troll", "has attacked her before")],
)
def test_a_standing_is_said_beside_the_authors_name(
    standing: str,
    note: str,
) -> None:
    group = {
        "content_key": "c:1",
        "context_key": "x:1",
        "media_keys": [],
        "comment_ids": ["c1"],
        "comment": {"id": "c1", "author_name": "Kim", "standing": standing, "text": "yo"},
        "parent": None,
    }

    prepared = run_tool("prepare_classification", {"request": {"item": group, "index": 0}})

    assert prepared["comment"] == f'"Kim" ({note}) wrote: "yo"'


# Settling what a model left open


def outcome(
    *entries: tuple[str, str, str],
    reputations: dict[str, str],
    anywhere: frozenset[str] = frozenset(),
) -> dict[str, tuple[str, str, bool]]:
    """Combine model verdicts (comment, verdict, layer) under reputations, by comment."""

    resolution = {
        "platform": "facebook",
        "post_id": "reel-1",
        "policy": POLICY,
        "resolved": [
            {"comment_id": cid, "verdict": verdict, "layer": layer, "source": "model"}
            for cid, verdict, layer in entries
            if layer != "model"
        ],
        "needs_inference": [],
        "undescribed_media": [],
        "layers": {},
        "reputations": reputations,
    }
    judged = [
        {"comment_ids": [cid], "context_independent": cid in anywhere, "valid": True}
        for cid, _, layer in entries
        if layer == "model"
    ]
    recorded = {
        "resolved": [
            {"comment_id": cid, "verdict": verdict, "layer": layer, "source": "model"}
            for cid, verdict, layer in entries
            if layer == "model"
        ]
    }
    combined: dict[str, Any] = run_tool(
        "combine_verdicts", {"resolution": resolution, "judged": judged, "recorded": recorded}
    )["outcome"]

    return {
        entry["comment_id"]: (entry["verdict"], entry["layer"], entry["anywhere"])
        for entry in combined["verdicts"]
    }


def test_an_allys_comment_hostile_only_in_context_or_uncertain_is_friendly() -> None:
    settled = outcome(
        ("c1", "hostile", "model"),
        ("c2", "uncertain", "model"),
        ("c3", "hostile", "context"),
        reputations={"c1": "ally", "c2": "ally", "c3": "ally"},
    )

    assert settled == {
        "c1": ("friendly", "known_ally", False),
        "c2": ("friendly", "known_ally", False),
        "c3": ("friendly", "known_ally", False),
    }


def test_what_holds_anywhere_is_never_changed_by_who_wrote_it() -> None:
    settled = outcome(
        ("c1", "hostile", "model"),
        ("c2", "hostile", "content"),
        ("c3", "hostile", "blocked_term"),
        reputations={"c1": "ally", "c2": "ally", "c3": "ally"},
        anywhere=frozenset({"c1"}),
    )

    assert settled == {
        "c2": ("hostile", "content", True),
        "c3": ("hostile", "blocked_term", True),
        "c1": ("hostile", "model", True),
    }


def test_a_trolls_uncertain_comment_is_hostile_and_their_friendly_one_stays_friendly() -> None:
    settled = outcome(
        ("c1", "uncertain", "model"),
        ("c2", "friendly", "model"),
        reputations={"c1": "troll", "c2": "troll"},
    )

    assert settled == {
        "c1": ("hostile", "known_troll", False),
        "c2": ("friendly", "model", False),
    }


def test_a_settled_verdict_is_counted_by_the_layer_that_settled_it() -> None:
    resolution_layers = outcome(("c1", "uncertain", "model"), reputations={"c1": "ally"})

    assert resolution_layers["c1"][1] == "known_ally"


# Learning who is who


def test_every_verdict_not_decided_by_its_author_is_learned_against_them(
    state: sqlite3.Connection,
) -> None:
    scan = {
        "platform": "facebook",
        "post_id": "reel-1",
        "comments": [
            {"id": "c1", "author_id": "troll"},
            {"id": "c2", "author_id": "friend"},
            {"id": "c3", "author_id": "bestie"},
            {"id": "c4", "author_id": "ally"},
        ],
    }
    decided = {
        "policy": POLICY,
        "verdicts": [
            {"comment_id": "c1", "verdict": "hostile", "layer": "model", "anywhere": True},
            {"comment_id": "c2", "verdict": "friendly", "layer": "content", "source": "human"},
            {"comment_id": "c3", "verdict": "friendly", "layer": "trusted_author"},
            {"comment_id": "c4", "verdict": "friendly", "layer": "known_ally"},
            {"comment_id": "c1", "verdict": "friendly", "layer": "she_answered"},
        ],
    }

    assert run_tool("record_authors", {"scan": scan, "outcome": decided}) == {"learned": 2}
    assert read_author_verdicts(state, POLICY, "facebook", "troll") == [("hostile", True, "model")]
    assert read_author_verdicts(state, POLICY, "facebook", "friend") == [
        ("friendly", False, "human")
    ]
    assert read_author_verdicts(state, POLICY, "facebook", "bestie") == []
    assert read_author_verdicts(state, POLICY, "facebook", "ally") == []


# The whole scan


def test_a_scan_settles_an_allys_reply_and_learns_a_troll(
    installed: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    earn(installed, "wendy", "friendly", 3)
    swarm = [
        comment("c1", "you will never be a woman", author="jeffy"),
        comment("c2", "lol exactly", parent="c1", author="wendy"),
        comment("c3", "lol exactly", parent="c1", author="stranger"),
        comment("c4", "nice try, man", author="jeffy"),
        comment("c5", "you will never be a woman", author="wendy"),
    ]

    first = verdicts(moderate(capsys, swarm))

    assert first["c2"] == ("friendly", "known_ally")
    assert first["c3"] == ("hostile", "model")
    assert first["c5"][0] == "hostile"
    assert read_reputation(installed, POLICY, "facebook", "jeffy") == "troll"
