"""Tests proving lookup_verdicts decides what it can without a model, and asks once for the rest."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from elder_sign.policy import TRANSPHOBIC_HARASSMENT
from elder_sign.shared.store import (
    Verdict,
    connect,
    write_description,
    write_standing,
    write_term,
    write_verdict,
)
from tests.tools import grant_state_file, run_tool

POLICY = TRANSPHOBIC_HARASSMENT

GIF = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"

HOSTILE = Verdict(verdict="hostile", source="model", model="m", reason=None)

FRIENDLY = Verdict(verdict="friendly", source="model", model="m", reason=None)

UNCERTAIN = Verdict(verdict="uncertain", source="model", model="m", reason=None)


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


def comment(
    comment_id: str,
    text: str | None = None,
    *,
    parent: str | None = None,
    author: str = "troll",
    **fields: Any,
) -> dict[str, Any]:
    """Return one comment as an adapter reads it."""

    return {"id": comment_id, "parent_id": parent, "author_id": author, "text": text, **fields}


def keyed(
    *comments: dict[str, Any],
    **post: Any,
) -> dict[str, Any]:
    """Return a scan of the comments, keyed by the built normalize_comments."""

    scan = {"platform": "facebook", "post_id": "reel-1", "comments": list(comments), **post}
    output = run_tool("normalize_comments", {"scan": scan})
    result: dict[str, Any] = output["scan"]

    return result


def lookup(
    *comments: dict[str, Any],
    policy: str = POLICY,
    **post: Any,
) -> dict[str, Any]:
    """Key a scan of the comments and look up their verdicts with the built tool."""

    output = run_tool(
        "lookup_verdicts", {"scan": keyed(*comments, **post), "settings": {"policy": policy}}
    )
    resolution: dict[str, Any] = output["resolution"]

    return resolution


def decided(
    resolution: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Return each decided comment's verdict and the layer that decided it."""

    return {
        entry["comment_id"]: (entry["verdict"], entry["layer"]) for entry in resolution["resolved"]
    }


def key_of(
    *comments: dict[str, Any],
    comment_id: str,
    kind: str = "content",
) -> str:
    """Return one key of one comment in a scan."""

    (found,) = (entry for entry in keyed(*comments)["comments"] if entry["id"] == comment_id)
    key: str = found["keys"][kind]

    return key


# Nothing known yet


def test_with_nothing_known_every_distinct_comment_needs_a_model(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(comment("c1", "you are valid"), comment("c2", "ew"))

    assert resolution["resolved"] == []
    assert [group["comment_ids"] for group in resolution["needs_inference"]] == [["c1"], ["c2"]]
    assert resolution["layers"] == {}
    assert (resolution["platform"], resolution["post_id"], resolution["policy"]) == (
        "facebook",
        "reel-1",
        POLICY,
    )


def test_a_swarms_copies_under_one_parent_are_asked_about_once(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(
        comment("c1", "go back to the kitchen sir", author="t1"),
        comment("c2", "GO BACK TO THE KITCHEN SIR!!!", author="t2"),
        comment("c3", "g0 b4ck t0 th3 kitch3n s1r", author="t3"),
    )
    (group,) = resolution["needs_inference"]

    assert group["comment_ids"] == ["c1", "c2", "c3"]
    assert group["comment"]["text"] == "go back to the kitchen sir"
    assert group["parent"] is None


def test_one_reply_under_two_parents_is_asked_about_in_each(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(
        comment("p1", "trans women are men"),
        comment("p2", "trans women are women", author="friend"),
        comment("r1", "lol exactly", parent="p1"),
        comment("r2", "lol exactly", parent="p2"),
        comment("r3", "lol exactly", parent="p1", author="t2"),
    )
    groups = {group["comment"]["id"]: group for group in resolution["needs_inference"]}

    assert groups["r1"]["comment_ids"] == ["r1", "r3"]
    assert groups["r1"]["parent"]["text"] == "trans women are men"
    assert groups["r2"]["comment_ids"] == ["r2"]
    assert groups["r2"]["parent"]["text"] == "trans women are women"


def test_replies_to_unknown_parents_are_grouped_by_their_content(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(
        comment("r1", "lol exactly", parent="gone-1"),
        comment("r2", "lol exactly", parent="gone-2"),
    )
    (group,) = resolution["needs_inference"]

    assert group["comment_ids"] == ["r1", "r2"]
    assert group["context_key"] is None
    assert group["parent"] is None


def test_a_model_is_shown_neither_keys_nor_author_ids(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "hi", author_name="Kim", created_at="t"))["needs_inference"]

    assert group["comment"] == {"id": "c1", "author_name": "Kim", "text": "hi", "created_at": "t"}


def test_a_model_is_shown_what_the_post_says(
    state: sqlite3.Connection,
) -> None:
    (group,) = lookup(comment("c1", "have fun"), caption="Lesbian first date today")[
        "needs_inference"
    ]
    (untold,) = lookup(comment("c1", "have fun"))["needs_inference"]

    assert group["caption"] == "Lesbian first date today"
    assert untold["caption"] is None


def test_a_model_is_told_which_comments_she_wrote_herself(
    state: sqlite3.Connection,
) -> None:
    (hers, answer, piling_on) = lookup(
        comment("c1", "I am a woman", author="me"),
        comment("c2", "no you are not", parent="c1"),
        comment("c3", "lol exactly", parent="c2"),
        owner_id="me",
    )["needs_inference"]

    assert hers["comment"]["by_her"] is True
    assert answer["parent"]["by_her"] is True
    assert "by_her" not in answer["comment"]
    assert "by_her" not in piling_on["parent"]


# Media, as a model is shown it


OTHER_GIF = "https://media.giphy.com/media/xT9IgzoKnwFNmISR8I/giphy.gif"


def test_media_is_shown_by_its_description_never_its_url(
    state: sqlite3.Connection,
) -> None:
    write_description(state, "m:giphy:3o7TKSjRrfIPjeiVyM", "A clown honking a horn.")

    (group,) = lookup(comment("g1", "lol", media=[{"kind": "gif", "url": GIF}]))["needs_inference"]

    assert group["comment"]["media"] == [{"kind": "gif", "description": "A clown honking a horn."}]


def test_media_no_model_has_described_is_named_for_judging_first(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(comment("g1", "lol", media=[{"kind": "gif", "url": GIF + "?cid=1"}]))
    (group,) = resolution["needs_inference"]

    assert group["comment"]["media"] == [{"kind": "gif", "description": None}]
    assert resolution["undescribed_media"] == [
        {"media_key": "m:giphy:3o7TKSjRrfIPjeiVyM", "kind": "gif", "url": GIF + "?cid=1"}
    ]


def test_described_media_is_not_named_again(
    state: sqlite3.Connection,
) -> None:
    write_description(state, "m:giphy:3o7TKSjRrfIPjeiVyM", "A clown.")

    resolution = lookup(comment("g1", "lol", media=[{"kind": "gif", "url": GIF}]))

    assert resolution["undescribed_media"] == []


def test_a_parents_undescribed_media_is_named_too_and_each_once(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(
        comment("p1", None, media=[{"kind": "image", "url": OTHER_GIF}]),
        comment("r1", "so true", parent="p1"),
        comment("r2", "lmao", parent="p1", media=[{"kind": "gif", "url": GIF}]),
        comment("r3", "facts", media=[{"kind": "gif", "url": GIF}]),
    )

    assert [entry["media_key"] for entry in resolution["undescribed_media"]] == [
        "m:giphy:xT9IgzoKnwFNmISR8I",
        "m:giphy:3o7TKSjRrfIPjeiVyM",
    ]


def test_media_of_a_comment_decided_without_a_model_is_not_named(
    state: sqlite3.Connection,
) -> None:
    write_standing(state, "facebook", "bestie", "trusted")

    resolution = lookup(comment("g1", "lol", author="bestie", media=[{"kind": "gif", "url": GIF}]))

    assert resolution["needs_inference"] == []
    assert resolution["undescribed_media"] == []


# Authors


def test_a_trusted_author_is_friendly_whatever_they_write(
    state: sqlite3.Connection,
) -> None:
    write_standing(state, "facebook", "bestie", "trusted")
    write_term(state, POLICY, "tranny")

    resolution = lookup(comment("c1", "proud tranny and you know it", author="bestie"))

    assert decided(resolution) == {"c1": ("friendly", "trusted_author")}


def test_a_blocked_author_is_hostile_whatever_they_write(
    state: sqlite3.Connection,
) -> None:
    write_standing(state, "facebook", "troll", "blocked")

    assert decided(lookup(comment("c1", "nice dress"))) == {"c1": ("hostile", "blocked_author")}


def test_standing_belongs_to_one_platform(
    state: sqlite3.Connection,
) -> None:
    write_standing(state, "instagram", "troll", "blocked")

    assert decided(lookup(comment("c1", "nice dress"))) == {}


# Terms


def test_a_blocked_term_catches_every_disguise_of_it(
    state: sqlite3.Connection,
) -> None:
    write_term(state, POLICY, "Tr4nny")

    resolution = lookup(
        comment("c1", "lol t r a n n y"),
        comment("c2", "TRANNYYYY"),
        comment("c3", "trannies"),
    )

    assert decided(resolution) == {
        "c1": ("hostile", "blocked_term"),
        "c2": ("hostile", "blocked_term"),
    }


def test_a_term_ending_in_a_star_catches_every_word_it_begins(
    state: sqlite3.Connection,
) -> None:
    write_term(state, POLICY, "trann*")

    resolution = lookup(comment("c1", "trannies"), comment("c2", "tranny"), comment("c3", "tran"))

    assert set(decided(resolution)) == {"c1", "c2"}


def test_a_term_of_several_words_matches_them_together(
    state: sqlite3.Connection,
) -> None:
    write_term(state, POLICY, "groomer agenda")

    resolution = lookup(comment("c1", "the GROOMER agenda"), comment("c2", "agenda groomer"))

    assert set(decided(resolution)) == {"c1"}


def test_terms_belong_to_one_policy(
    state: sqlite3.Connection,
) -> None:
    write_term(state, "racism", "tranny")

    assert decided(lookup(comment("c1", "tranny"))) == {}


# Remembered verdicts


def test_remembered_content_decides_it_in_any_thread(
    state: sqlite3.Connection,
) -> None:
    hateful = comment("c1", "you will never be a woman")
    write_verdict(state, POLICY, "content", key_of(hateful, comment_id="c1"), HOSTILE)

    resolution = lookup(
        comment("p1", "so proud of you", author="friend"),
        comment("c2", "YOU WILL NEVER BE A WOMAN", parent="p1"),
        comment("c3", "you will never be a woman"),
    )

    assert decided(resolution) == {"c2": ("hostile", "content"), "c3": ("hostile", "content")}
    assert resolution["layers"] == {"content": 2}


def test_remembered_context_decides_it_only_under_the_same_parent(
    state: sqlite3.Connection,
) -> None:
    thread = (comment("p1", "trans women are men"), comment("r1", "lol exactly", parent="p1"))
    write_verdict(
        state, POLICY, "context", key_of(*thread, comment_id="r1", kind="context"), HOSTILE
    )

    resolution = lookup(
        comment("p9", "trans women are men", author="t2"),
        comment("r9", "lol exactly", parent="p9", author="t3"),
        comment("p8", "trans women are women", author="friend"),
        comment("r8", "lol exactly", parent="p8", author="friend-2"),
    )

    assert decided(resolution)["r9"] == ("hostile", "context")
    assert "r8" not in decided(resolution)


def test_a_gif_judged_hostile_alone_is_hostile_with_any_words(
    state: sqlite3.Connection,
) -> None:
    gif = comment("g1", None, media=[{"kind": "gif", "url": GIF}])
    (media_key,) = keyed(gif)["comments"][0]["keys"]["media"]
    write_verdict(state, POLICY, "content", media_key, HOSTILE)

    resolution = lookup(comment("g2", "lmao", media=[{"kind": "gif", "url": GIF + "?x=1"}]))

    assert decided(resolution) == {"g2": ("hostile", "media")}


def test_a_gif_judged_friendly_alone_does_not_excuse_its_words(
    state: sqlite3.Connection,
) -> None:
    gif = comment("g1", None, media=[{"kind": "gif", "url": GIF}])
    (media_key,) = keyed(gif)["comments"][0]["keys"]["media"]
    write_verdict(state, POLICY, "content", media_key, FRIENDLY)

    assert decided(lookup(comment("g2", "freak", media=[{"kind": "gif", "url": GIF}]))) == {}


def test_a_remembered_uncertain_verdict_is_not_asked_about_again(
    state: sqlite3.Connection,
) -> None:
    write_verdict(
        state, POLICY, "content", key_of(comment("c1", "hmm ok"), comment_id="c1"), UNCERTAIN
    )

    assert decided(lookup(comment("c2", "hmm ok"))) == {"c2": ("uncertain", "content")}


def test_a_human_verdict_is_reported_as_a_humans(
    state: sqlite3.Connection,
) -> None:
    human = Verdict(verdict="friendly", source="human", model=None, reason="inside joke")
    write_verdict(state, POLICY, "content", key_of(comment("c1", "sir"), comment_id="c1"), human)

    (entry,) = lookup(comment("c2", "SIR"))["resolved"]

    assert entry == {
        "comment_id": "c2",
        "verdict": "friendly",
        "layer": "content",
        "source": "human",
    }


def test_verdicts_belong_to_one_policy(
    state: sqlite3.Connection,
) -> None:
    write_verdict(state, POLICY, "content", key_of(comment("c1", "ew"), comment_id="c1"), HOSTILE)

    assert decided(lookup(comment("c2", "ew"), policy="racism")) == {}


def test_a_comment_with_nothing_to_judge_is_left_alone(
    state: sqlite3.Connection,
) -> None:
    resolution = lookup(comment("e1", "...", media=[{"kind": "sticker"}]))

    assert decided(resolution) == {"e1": ("friendly", "nothing_to_judge")}


# The promise


def test_a_scan_of_only_comments_already_judged_needs_no_model(
    state: sqlite3.Connection,
) -> None:
    first = lookup(
        comment("p1", "slay queen", author="friend"),
        comment("c1", "you will never be a woman", parent="p1"),
        comment("c2", "lol exactly", parent="c1", author="t2"),
        comment("g1", None, media=[{"kind": "gif", "url": GIF}], author="t3"),
    )

    for group in first["needs_inference"]:
        verdict = FRIENDLY if group["comment"]["id"] == "p1" else HOSTILE
        write_verdict(state, POLICY, "content", group["content_key"], verdict)

        if group["context_key"] is not None:
            write_verdict(state, POLICY, "context", group["context_key"], verdict)

    second = lookup(
        comment("p1", "slay queen", author="friend"),
        comment("c1", "you will never be a woman", parent="p1"),
        comment("c7", "Y0U W1LL NEVER BE A W0MAN!!!", parent="p1", author="t7"),
        comment("c8", "y o u will never be a woman", author="t8"),
        comment("c9", "lol exactly", parent="c7", author="t9"),
        comment("g2", "", media=[{"kind": "gif", "url": GIF + "?cid=1"}], author="t4"),
    )

    assert len(first["needs_inference"]) == 4
    assert second["needs_inference"] == []
    assert decided(second)["p1"] == ("friendly", "content")
    assert {decided(second)[comment_id][0] for comment_id in ("c1", "c7", "c8", "c9", "g2")} == {
        "hostile"
    }
