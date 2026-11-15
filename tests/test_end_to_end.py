"""Tests proving the deterministic core end to end, through the azathoth CLI itself.

A troll swarm hits a reel. The first scan asks a model only about each
distinct comment in its context; a stand-in for the model judges them, and
the verdicts are remembered. The cleaned post's fingerprint is kept, so an
unchanged post is never scanned. When the swarm comes back with disguised
copies of what it already said, the scan asks the model nothing at all.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from azathoth.cli import DATABASE_ENVIRONMENT_VARIABLE as AZATHOTH_DATABASE
from azathoth.cli import main

from elder_sign.build import DOCUMENTS
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    write_standing,
    write_term,
)
from tests.tools import run_tool

WORKFLOWS = DOCUMENTS.parent / "workflows"

RESOLVE_COMMENTS = "e1de7516-0000-4000-8000-000000000100"

NORMALIZE_COMMENTS = "e1de7516-0000-4000-8000-000000000010"

GIF = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"

FRIENDLY = {"slay queen 👑"}
"""The text the stand-in model finds friendly; everything else it is shown is hostile."""

CONTEXT_DEPENDENT = {"lol exactly"}
"""The text whose meaning, for the stand-in model, depends on what it replies to."""


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[sqlite3.Connection]:
    """Install Elder Sign into a fresh Azathoth, as a user would, with no model provider."""

    monkeypatch.setenv(AZATHOTH_DATABASE, str(tmp_path / "azathoth.db"))
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(tmp_path / "elder-sign.db"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    for path in sorted(DOCUMENTS.glob("*.json")):
        cli(capsys, "tool", "import", str(path))

    for path in sorted(WORKFLOWS.glob("*.json")):
        cli(capsys, "workflow", "import", str(path))

    connection = connect(tmp_path / "elder-sign.db")

    try:
        write_standing(connection, "facebook", "bestie", "trusted")
        write_term(connection, "transphobia", "Tr4nny")
        yield connection
    finally:
        connection.close()


def cli(
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> str:
    """Run one azathoth command that must succeed, returning what it printed."""

    status = main(arguments)
    captured = capsys.readouterr()

    assert status == 0, captured.err

    return captured.out


def resolve(
    capsys: pytest.CaptureFixture[str],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run resolve-comments on one scan of the reel, through the CLI."""

    scan = {"platform": "facebook", "post_id": "reel-1", "comments": comments}
    output = cli(
        capsys,
        "workflow",
        "run",
        RESOLVE_COMMENTS,
        "--input",
        json.dumps(scan),
        "--output",
        "result",
    )
    resolution: dict[str, Any] = json.loads(output)

    return resolution


def stand_in_model(
    group: dict[str, Any],
) -> dict[str, Any]:
    """Judge one group as a model would, by the stand-in's two rules."""

    text = group["comment"].get("text") or ""
    friendly = text in FRIENDLY

    return {
        **{key: group[key] for key in ("content_key", "context_key", "media_keys", "comment_ids")},
        "verdict": "friendly" if friendly else "hostile",
        "context_independent": text not in CONTEXT_DEPENDENT,
        "media_hostile": not friendly and not text,
        "model": "stand-in",
        "reason": None,
    }


def fingerprint(
    count: int,
    newest: str | None,
) -> dict[str, Any]:
    """Return the reel's fingerprint."""

    return {
        "post": {
            "platform": "facebook",
            "post_id": "reel-1",
            "comment_count": count,
            "newest_comment_id": newest,
        }
    }


def comment(
    comment_id: str,
    author: str,
    text: str | None,
    *,
    parent: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Return one comment as an adapter reads it."""

    return {"id": comment_id, "parent_id": parent, "author_id": author, "text": text, **fields}


SWARM = [
    comment("c1", "friend", "slay queen 👑"),
    comment("c2", "bestie", "omg tranny icon"),
    comment("c3", "troll-1", "you will never be a woman"),
    comment("c4", "troll-2", "Y0U WILL NEVER BE A W0MAN!!!"),
    comment("c5", "troll-3", "lol exactly", parent="c3"),
    comment("c6", "troll-4", None, parent="c1", media=[{"kind": "gif", "url": GIF}]),
    comment("c7", "troll-5", "t r a n n y"),
]
"""The first wave: a friend, a bestie, and five trolls."""

RETURN = [
    comment("c1", "friend", "slay queen 👑"),
    comment("c2", "bestie", "omg tranny icon"),
    comment("c8", "troll-6", "Eris you will never be a woman", mentions=["Eris"]),
    comment("c9", "troll-7", "y o u   w i l l never be a woman"),
    comment("c10", "troll-8", "lol exactly", parent="c8"),
    comment("c11", "troll-9", "lmaooo", media=[{"kind": "gif", "url": GIF + "?cid=77"}]),
    comment("c12", "troll-10", "TR@NNYYYY"),
]
"""The swarm comes back, disguising only what it already said."""


def test_a_swarm_is_judged_once_and_never_again(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    #
    # The reel has never been cleaned, so it is scanned.
    #
    assert run_tool("check_post_fingerprint", fingerprint(7, "c7"))["changed"]

    first = resolve(capsys, SWARM)
    decided = {entry["comment_id"]: entry["layer"] for entry in first["resolved"]}

    assert decided == {"c2": "trusted_author", "c7": "blocked_term"}
    assert sorted(group["comment_ids"] for group in first["needs_inference"]) == [
        ["c1"],
        ["c3", "c4"],
        ["c5"],
        ["c6"],
    ]

    #
    # A model is asked about four groups, not five comments, and remembered.
    #
    recorded = run_tool(
        "record_verdicts",
        {
            "judged": [stand_in_model(group) for group in first["needs_inference"]],
            "settings": {"policy": "transphobia"},
        },
    )
    hostile = {
        entry["comment_id"] for entry in recorded["resolved"] if entry["verdict"] == "hostile"
    } | {"c7"}

    assert hostile == {"c3", "c4", "c5", "c6", "c7"}

    #
    # Once the hostile five are gone, the two that remain are fingerprinted,
    # and an unchanged reel is not scanned again.
    #
    run_tool("record_post_fingerprint", fingerprint(2, "c2"))

    assert not run_tool("check_post_fingerprint", fingerprint(2, "c2"))["changed"]

    #
    # The swarm returns in disguise. The reel has changed, so it is scanned,
    # and every comment is decided without a model.
    #
    assert run_tool("check_post_fingerprint", fingerprint(7, "c12"))["changed"]

    second = resolve(capsys, RETURN)
    verdicts = {entry["comment_id"]: entry["verdict"] for entry in second["resolved"]}

    assert second["needs_inference"] == []
    assert verdicts == {
        "c1": "friendly",
        "c2": "friendly",
        "c8": "hostile",
        "c9": "hostile",
        "c10": "hostile",
        "c11": "hostile",
        "c12": "hostile",
    }
    assert second["layers"] == {
        "trusted_author": 1,
        "blocked_term": 1,
        "media": 1,
        "content": 3,
        "context": 1,
    }


def test_production_resolves_as_the_configured_workflow_does(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configured = resolve(capsys, SWARM)

    cli(capsys, "workflow", "promote", RESOLVE_COMMENTS)
    scan = {"platform": "facebook", "post_id": "reel-1", "comments": SWARM}
    output = cli(
        capsys,
        "workflow",
        "invoke",
        RESOLVE_COMMENTS,
        "--input",
        json.dumps(scan),
        "--output",
        "result",
    )

    assert json.loads(output) == {"result": configured}


def test_azathoth_verifies_a_tool_by_its_own_test_cases(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = cli(capsys, "tool", "verify", "--version", "1.0.0", NORMALIZE_COMMENTS)

    assert "Status: passed" in output
    assert "Failed: 0" in output
