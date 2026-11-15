"""Tests proving moderate-scan judges a whole scan, the map running each child in its own process.

The child under classify-comment's ID is a stand-in whose one step judges by
fixed rules, because a map's children run as separate `azathoth` processes,
where no scripted model can be put in place of a real one. The real
classify-comment is proven in-process in its own tests; here, everything
around it is proven for real: the map over the groups, the children, the
unwrapping, the recording and the combining.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from azathoth.cli import DATABASE_ENVIRONMENT_VARIABLE as AZATHOTH_DATABASE
from azathoth.tools import encode_tool_document
from azathoth.workflows import ToolStepSpecification, decode_workflow_document

import tests.fixtures.stand_in_judge_tool as stand_in_judge_tool
from elder_sign.build import DOCUMENTS, build_document
from elder_sign.policy import TRANSPHOBIC_HARASSMENT
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    write_standing,
    write_term,
)
from tests.test_end_to_end import GIF, RETURN, SWARM, comment
from tests.test_media_judging import cli

WORKFLOWS = DOCUMENTS.parent / "workflows"

MODERATE_SCAN = WORKFLOWS / "moderate-scan.json"

MODERATE_SCAN_ID = "e1de7516-0000-4000-8000-000000000400"

CLASSIFY_COMMENT_ID = "e1de7516-0000-4000-8000-000000000300"


def stand_in_child() -> dict[str, Any]:
    """Return a child workflow under classify-comment's ID that judges with the stand-in."""

    return {
        "metadata": {
            "id": CLASSIFY_COMMENT_ID,
            "name": "classify comment",
            "description": "A stand-in for classify-comment that judges by fixed rules.",
            "version": "1.0.0",
        },
        "steps": [
            {
                "id": "e1de7516-0000-4000-8000-00000000f301",
                "specification": {
                    "requirement": {
                        "name": "stand_in_judge",
                        "version": "1.0.0",
                        "runtime": "process",
                    }
                },
                "depends_on": [],
                "inputs": [
                    {
                        "name": "request",
                        "source": {"event_type": "workflow.input.received", "field_name": "input"},
                    }
                ],
                "outputs": [{"name": "judged", "path": ["judged"]}],
                "conditions": [],
                "retry_policy": {
                    "max_attempts": 1,
                    "initial_delay_seconds": 0.0,
                    "backoff_multiplier": 1.0,
                    "maximum_delay_seconds": None,
                },
                "failure_policy": "fail_workflow",
            }
        ],
        "result": {"name": "judged"},
    }


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[sqlite3.Connection]:
    """Install Elder Sign with the stand-in child promoted, and return the state file."""

    monkeypatch.setenv(AZATHOTH_DATABASE, str(tmp_path / "azathoth.db"))
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(tmp_path / "elder-sign.db"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    stand_in_tool = tmp_path / "stand-in-judge.json"
    stand_in_tool.write_text(encode_tool_document(build_document(stand_in_judge_tool)))
    child = tmp_path / "stand-in-classify-comment.json"
    child.write_text(json.dumps(stand_in_child()))

    cli(capsys, "init")

    for path in (*sorted(DOCUMENTS.glob("*.json")), stand_in_tool):
        cli(capsys, "tool", "import", str(path))

    for path in sorted(WORKFLOWS.glob("*.json")):
        if path.name != "classify-comment.json":
            cli(capsys, "workflow", "import", str(path))

    cli(capsys, "workflow", "import", str(child))
    cli(capsys, "workflow", "promote", CLASSIFY_COMMENT_ID)

    connection = connect(tmp_path / "elder-sign.db")

    try:
        write_standing(connection, "facebook", "bestie", "trusted")
        write_term(connection, TRANSPHOBIC_HARASSMENT, "Tr4nny")
        yield connection
    finally:
        connection.close()


def moderate(
    capsys: pytest.CaptureFixture[str],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run moderate-scan on one scan of the reel, through the CLI."""

    scan = {"platform": "facebook", "post_id": "reel-1", "comments": comments}
    output = cli(
        capsys,
        "workflow",
        "run",
        MODERATE_SCAN_ID,
        "--input",
        json.dumps(scan),
        "--output",
        "result",
    )
    outcome: dict[str, Any] = json.loads(output)

    return outcome


def verdicts(
    outcome: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Return each comment's verdict and the layer that decided it."""

    return {
        entry["comment_id"]: (entry["verdict"], entry["layer"]) for entry in outcome["verdicts"]
    }


# The workflow


def test_moderate_scan_maps_classify_comment_over_what_lookup_could_not_decide() -> None:
    workflow = decode_workflow_document(MODERATE_SCAN.read_text(encoding="utf-8"))
    steps = [step.specification for step in workflow.steps]

    assert all(isinstance(step, ToolStepSpecification) for step in steps)
    assert [step.requirement.name for step in steps if isinstance(step, ToolStepSpecification)] == [
        "normalize_comments",
        "lookup_verdicts",
        "map_workflow",
        "unwrap_results",
        "record_verdicts",
        "combine_verdicts",
    ]

    lookup, mapped = workflow.steps[1], workflow.steps[2]
    (settings,) = (binding for binding in mapped.inputs if binding.name == "settings")
    (items,) = (binding for binding in mapped.inputs if binding.name == "items")
    (groups,) = (output for output in lookup.outputs if output.name == "groups")

    assert settings.source.model_dump(mode="json") == {
        "value": {"workflow_id": CLASSIFY_COMMENT_ID, "max_items": 500, "max_concurrency": 4}
    }
    assert items.source.model_dump(mode="json") == {"name": "groups"}
    assert list(groups.path) == ["resolution", "needs_inference"]
    assert workflow.result is not None
    assert workflow.result.model_dump(mode="json") == {"name": "outcome"}


# Running it


def test_a_swarm_is_judged_once_and_its_return_asks_nothing(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = moderate(capsys, SWARM)

    assert verdicts(first) == {
        "c1": ("friendly", "model"),
        "c2": ("friendly", "trusted_author"),
        "c3": ("hostile", "model"),
        "c4": ("hostile", "model"),
        "c5": ("hostile", "model"),
        "c6": ("hostile", "model"),
        "c7": ("hostile", "blocked_term"),
    }
    assert first["model"] == {"groups": 4, "unreadable": 0}
    assert first["layers"] == {"trusted_author": 1, "blocked_term": 1, "model": 5}
    assert (first["platform"], first["post_id"], first["policy"]) == (
        "facebook",
        "reel-1",
        TRANSPHOBIC_HARASSMENT,
    )

    second = moderate(capsys, RETURN)

    assert second["model"] == {"groups": 0, "unreadable": 0}
    assert "model" not in second["layers"]
    assert {verdict for verdict, _ in verdicts(second).values()} == {"friendly", "hostile"}
    assert verdicts(second)["c1"] == ("friendly", "content")
    assert all(
        verdicts(second)[comment_id][0] == "hostile"
        for comment_id in ("c8", "c9", "c10", "c11", "c12")
    )


def test_a_scan_nothing_needs_a_model_for_maps_nothing(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = moderate(
        capsys, [comment("c1", "bestie", "hi babe"), comment("c2", "troll", "tr4nny")]
    )

    assert outcome["model"] == {"groups": 0, "unreadable": 0}
    assert verdicts(outcome) == {
        "c1": ("friendly", "trusted_author"),
        "c2": ("hostile", "blocked_term"),
    }


def test_an_empty_scan_has_no_verdicts(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = moderate(capsys, [])

    assert outcome["verdicts"] == []
    assert outcome["model"] == {"groups": 0, "unreadable": 0}


def test_media_no_model_has_described_is_reported(
    state: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = moderate(
        capsys, [comment("g1", "troll", "lmao", media=[{"kind": "gif", "url": GIF}])]
    )

    assert [entry["media_key"] for entry in outcome["undescribed_media"]] == [
        "m:giphy:3o7TKSjRrfIPjeiVyM"
    ]
