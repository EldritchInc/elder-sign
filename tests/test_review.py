"""Tests proving `review` shows her what was left open, and keeps her every call for good.

Review reads the report `judge` kept, shows each uncertain comment and each
comment the plan would hide, beside what it replies to, and asks her. Her
answer becomes a human verdict on the comment's content and its context,
which no model's verdict ever replaces, and a human verdict on its author,
which settles their standing on every post.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from elder_sign.policy import TRANSPHOBIC_HARASSMENT
from elder_sign.review import items_of, review
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    Verdict,
    connect,
    read_author_verdicts,
    read_reputation,
    read_verdict,
    write_description,
    write_verdict,
)
from elder_sign.tools import normalize_comments

POLICY = TRANSPHOBIC_HARASSMENT

GIF = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Give review a fresh state file, and the test a connection to it."""

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)

    try:
        yield connection
    finally:
        connection.close()


def report() -> dict[str, Any]:
    """Return a report as `judge` keeps it: a troll, two allies, an open question, her reply."""

    scan = {
        "platform": "facebook",
        "post_id": "reel-1",
        "owner_id": "me",
        "comments": [
            {
                "id": "c1",
                "parent_id": None,
                "author_id": "jeffy",
                "author_name": "Jeffy Jeff",
                "text": "never get why you'd cut it off",
                "media": [{"kind": "gif", "url": GIF, "provider": "giphy"}],
            },
            {
                "id": "c2",
                "parent_id": "c1",
                "author_id": "wendy",
                "author_name": "Wendy",
                "text": "Jeffy Jeff they rarely do",
                "mentions": ["Jeffy Jeff"],
            },
            {
                "id": "c3",
                "parent_id": None,
                "author_id": "roy",
                "author_name": "Roy",
                "text": "Oh dear!",
            },
            {
                "id": "c4",
                "parent_id": "c1",
                "author_id": "me",
                "author_name": "Eris",
                "text": "it's inverted, not cut",
            },
        ],
    }

    return {
        "scan": scan,
        "outcome": {
            "policy": POLICY,
            "verdicts": [
                {"comment_id": "c1", "verdict": "hostile", "layer": "model", "anywhere": True},
                {"comment_id": "c2", "verdict": "hostile", "layer": "model", "anywhere": True},
                {"comment_id": "c3", "verdict": "uncertain", "layer": "model", "anywhere": False},
                {"comment_id": "c4", "verdict": "friendly", "layer": "trusted_author"},
            ],
        },
        "plan": {
            "actions": [
                {"comment_id": "c1", "action": "delete", "layer": "model", "dry_run": False},
                {"comment_id": "c2", "action": "hide", "layer": "model", "dry_run": False},
            ],
            "left": ["c3"],
            "waiting": [],
            "deferred": [],
        },
    }


def answering(
    *answers: str,
) -> tuple[list[str], Any]:
    """Return what was asked, and an `ask` that answers in turn."""

    asked: list[str] = []
    pending = list(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return pending.pop(0)

    return asked, ask


def keys_of(
    comment_id: str,
) -> dict[str, Any]:
    """Return one comment's keys, as normalize_comments gives them."""

    keyed = normalize_comments.run({"scan": report()["scan"]})["scan"]["comments"]
    (found,) = (entry for entry in keyed if entry["id"] == comment_id)
    keys: dict[str, Any] = found["keys"]

    return keys


# What is shown


def test_the_uncertain_and_the_hidden_are_reviewed_in_that_order() -> None:
    assert [item.comment["id"] for item in items_of(report())] == ["c3", "c2"]


def test_with_everything_the_deleted_are_reviewed_too() -> None:
    assert [item.comment["id"] for item in items_of(report(), everything=True)] == [
        "c3",
        "c2",
        "c1",
    ]


def test_each_comment_is_shown_with_who_wrote_it_and_what_it_replies_to(
    state: sqlite3.Connection,
) -> None:
    write_description(state, keys_of("c1")["media"][0], "A man rolling his eyes.")
    shown: list[str] = []
    _, ask = answering("s", "s")

    review(report(), state, ask=ask, say=shown.append)

    reply = "\n".join(shown)

    assert "1 of 2 · uncertain" in reply
    assert 'Roy: "Oh dear!"' in reply
    assert "2 of 2 · would be hidden" in reply
    assert 'Wendy: "Jeffy Jeff they rarely do"' in reply
    assert (
        'replying to Jeffy Jeff: "never get why you\'d cut it off" [GIF: A man rolling his eyes.]'
        in reply
    )


# What her answers keep


def test_her_answer_is_kept_by_content_and_context_and_against_the_author(
    state: sqlite3.Connection,
) -> None:
    _, ask = answering("h", "f")

    marked = review(report(), state, ask=ask, say=lambda line: None)

    roy, wendy = keys_of("c3"), keys_of("c2")

    assert marked == {"friendly": 1, "hostile": 1, "skipped": 0}
    assert read_verdict(state, POLICY, "content", roy["content"]) == Verdict(
        "hostile", "human", None, "you, in review"
    )
    assert read_verdict(state, POLICY, "context", roy["context"]) is not None
    assert read_verdict(state, POLICY, "context", wendy["context"]) == Verdict(
        "friendly", "human", None, "you, in review"
    )
    assert read_author_verdicts(state, POLICY, "facebook", "wendy") == [
        ("friendly", False, "human")
    ]
    assert read_reputation(state, POLICY, "facebook", "wendy") == "ally"
    assert read_reputation(state, POLICY, "facebook", "roy") == "troll"


def test_her_answer_replaces_what_a_model_decided(
    state: sqlite3.Connection,
) -> None:
    content = keys_of("c2")["content"]
    write_verdict(
        state, POLICY, "content", content, Verdict("hostile", "model", "m", "about genitals")
    )
    _, ask = answering("s", "f")

    review(report(), state, ask=ask, say=lambda line: None)

    found = read_verdict(state, POLICY, "content", content)

    assert found is not None
    assert (found.verdict, found.source) == ("friendly", "human")


def test_skipping_keeps_nothing_and_quitting_stops(
    state: sqlite3.Connection,
) -> None:
    asked, ask = answering("s", "q")

    marked = review(report(), state, ask=ask, say=lambda line: None)

    assert marked == {"friendly": 0, "hostile": 0, "skipped": 1}
    assert len(asked) == 2
    assert state.execute("SELECT COUNT(*) FROM verdicts").fetchone() == (0,)


def test_an_answer_it_does_not_know_is_asked_again(
    state: sqlite3.Connection,
) -> None:
    asked, ask = answering("maybe", "F", "q")

    review(report(), state, ask=ask, say=lambda line: None)

    assert len(asked) == 3


def test_running_out_of_answers_stops_cleanly(
    state: sqlite3.Connection,
) -> None:
    def ask(prompt: str) -> str:
        raise EOFError

    assert review(report(), state, ask=ask, say=lambda line: None) == {
        "friendly": 0,
        "hostile": 0,
        "skipped": 0,
    }


# The command


def test_the_review_command_reviews_the_reels_last_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import elder_sign.cli as command

    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(tmp_path / "state.db"))
    kept = tmp_path / "verdicts.json"
    kept.write_text(json.dumps(report()))
    _, ask = answering("f", "f")

    status = command.main(
        ["review", "facebook", "https://www.facebook.com/reel/1", "--report", str(kept)],
        ask=ask,
    )

    assert status == 0
    assert "Marked 2 friendly and 0 hostile" in capsys.readouterr().out


def test_the_review_command_needs_a_judged_reel(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    missing = tmp_path / "nothing.json"

    arguments = ["review", "facebook", "https://www.facebook.com/reel/1", "--report", str(missing)]

    assert main(arguments) == 1
    assert "elder-sign judge facebook" in capsys.readouterr().err
