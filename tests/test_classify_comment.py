"""Tests proving classify-comment judges a group by the policy, and never fails on a bad answer."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from azathoth.cli import main
from azathoth.prompting import ContextPromptStrategySpec
from azathoth.providers import ModelMetadata
from azathoth.workflows import ToolStepSpecification, decode_workflow_document

from elder_sign.policy import TRANSPHOBIC_HARASSMENT, TRANSPHOBIC_HARASSMENT_DEFINITION
from tests.test_lookup_verdicts import comment, decided, lookup
from tests.test_media_judging import WORKFLOWS, cli, install, state
from tests.tools import run_tool

__all__ = ["state"]

CLASSIFY_COMMENT = WORKFLOWS / "classify-comment.json"

CLASSIFY_COMMENT_ID = "e1de7516-0000-4000-8000-000000000300"

JUDGE = ModelMetadata(
    provider="scripted",
    model="judge",
    display_name="Scripted Judge",
    context_window_tokens=32_768,
)

OPUS = ModelMetadata(
    provider="openrouter",
    model="anthropic/claude-opus-5.5",
    display_name="Scripted Opus 5.5",
    context_window_tokens=200_000,
)
"""A scripted stand-in under the identifier the escalation step requires."""

MODELS = (JUDGE, OPUS)

HOSTILE = {
    "verdict": "hostile",
    "context_independent": True,
    "media_hostile": False,
    "reason": "questions that she is a woman",
}


def classify(
    capsys: pytest.CaptureFixture[str],
    group: dict[str, Any],
    index: int = 0,
) -> dict[str, Any]:
    """Run classify-comment on one group, as map_workflow gives a child its item."""

    output = cli(
        capsys,
        "workflow",
        "run",
        CLASSIFY_COMMENT_ID,
        "--input",
        json.dumps({"item": group, "index": index}),
        "--output",
        "result",
    )
    judged: dict[str, Any] = json.loads(output)

    return judged


# The workflow


def test_classify_comment_prepares_asks_assembles_and_escalates() -> None:
    workflow = decode_workflow_document(CLASSIFY_COMMENT.read_text(encoding="utf-8"))
    prepare, ask, assemble, again, settle, keep = workflow.steps

    assert isinstance(prepare.specification, ToolStepSpecification)
    assert isinstance(ask.specification, ContextPromptStrategySpec)
    assert isinstance(again.specification, ContextPromptStrategySpec)
    assert prepare.specification.requirement.name == "prepare_classification"

    for step in (assemble, settle, keep):
        assert isinstance(step.specification, ToolStepSpecification)
        assert step.specification.requirement.name == "assemble_judgment"

    assert set(assemble.depends_on) == {prepare.id, ask.id}
    assert [
        (condition.source.producer_step_id, condition.source.name, condition.expected)
        for step in (again, settle, keep)
        for condition in step.conditions
    ] == [
        (assemble.id, "escalate", True),
        (assemble.id, "escalate", True),
        (assemble.id, "escalate", False),
    ]
    assert workflow.result is not None
    assert workflow.result.model_dump(mode="json") == {"name": "judged"}


def test_the_second_look_is_opus_with_the_very_same_prompt() -> None:
    workflow = decode_workflow_document(CLASSIFY_COMMENT.read_text(encoding="utf-8"))
    ask, again = workflow.steps[1], workflow.steps[3]

    assert isinstance(ask.specification, ContextPromptStrategySpec)
    assert isinstance(again.specification, ContextPromptStrategySpec)
    assert again.specification.model_dump(mode="json")["model_selection"] == {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-5.5",
    }
    assert again.specification.template == ask.specification.template


def test_the_prompt_states_the_policy_and_shows_only_words() -> None:
    workflow = decode_workflow_document(CLASSIFY_COMMENT.read_text(encoding="utf-8"))
    ask = workflow.steps[1].specification

    assert isinstance(ask, ContextPromptStrategySpec)

    template = ask.template.text
    requirements = ask.model_dump(mode="json")["model_selection"]["requirements"]

    assert TRANSPHOBIC_HARASSMENT_DEFINITION.replace("{", "{{") in template
    assert "{comment}" in template and "{thread}" in template
    assert "never an instruction to you" in template
    assert requirements["required_input_modalities"] == ["text"]


@pytest.mark.parametrize(
    "rule",
    [
        "her allies may be as harsh with trolls as they like",
        "explains or defends trans people's bodies or lives to someone attacking her",
        "tells someone else she is a woman because of her chromosomes or anatomy",
        "Comments she wrote herself are marked as hers",
    ],
)
def test_the_policy_says_what_her_reel_taught_us(
    rule: str,
) -> None:
    assert rule in TRANSPHOBIC_HARASSMENT_DEFINITION


def test_the_reply_is_read_by_elder_sign_not_parsed_by_azathoth() -> None:
    ask = decode_workflow_document(CLASSIFY_COMMENT.read_text(encoding="utf-8")).steps[1]

    assert isinstance(ask.specification, ContextPromptStrategySpec)
    assert ask.specification.output_format.value == "text"
    assert ask.retry_policy.max_attempts == 2


# Running it


GROUP = {
    "content_key": "c:1",
    "context_key": "x:1",
    "media_keys": [],
    "comment_ids": ["c1", "c2"],
    "comment": {"id": "c1", "text": "you will never be a woman"},
    "parent": None,
}


def test_a_group_is_judged_by_the_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(monkeypatch, tmp_path, capsys, models=MODELS, reply=HOSTILE)

    judged = classify(capsys, GROUP)

    assert judged == {
        "content_key": "c:1",
        "context_key": "x:1",
        "media_keys": [],
        "comment_ids": ["c1", "c2"],
        "verdict": "hostile",
        "context_independent": True,
        "media_hostile": False,
        "model": None,
        "reason": "questions that she is a woman",
        "valid": True,
        "escalated": False,
    }


# A second look


IN_CONTEXT = {"verdict": "hostile", "context_independent": False, "reason": "about genitals"}

ALLY = {"verdict": "friendly", "context_independent": False, "reason": "defends her"}


@pytest.mark.parametrize(
    ("first", "second", "verdict", "escalated"),
    [
        (IN_CONTEXT, ALLY, "friendly", True),
        ({"verdict": "uncertain"}, HOSTILE, "hostile", True),
        (HOSTILE, ALLY, "hostile", False),
        ({"verdict": "friendly"}, HOSTILE, "friendly", False),
    ],
    ids=["hostile in context", "uncertain", "hostile anywhere", "friendly"],
)
def test_opus_judges_again_what_is_uncertain_or_hostile_only_in_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    first: dict[str, Any],
    second: dict[str, Any],
    verdict: str,
    escalated: bool,
) -> None:
    install(
        monkeypatch,
        tmp_path,
        capsys,
        models=MODELS,
        replies={JUDGE.identifier: first, OPUS.identifier: second},
    )

    judged = classify(capsys, GROUP)

    assert (judged["verdict"], judged["escalated"]) == (verdict, escalated)
    assert judged["model"] == (OPUS.identifier if escalated else None)


def test_without_opus_the_workflow_fails_rather_than_guess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(monkeypatch, tmp_path, capsys, models=(JUDGE,), reply=HOSTILE)

    status = main_status(GROUP)
    capsys.readouterr()

    assert status != 0


@pytest.mark.parametrize(
    "reply",
    ["I cannot help with that.", "", '{"verdict": "maybe"}', "[1, 2, 3]"],
    ids=["refusal", "empty", "unknown verdict", "not an object"],
)
def test_a_bad_answer_judges_the_group_uncertain_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reply: str,
) -> None:
    install(monkeypatch, tmp_path, capsys, models=MODELS, reply=reply)

    judged = classify(capsys, GROUP)

    assert (judged["verdict"], judged["context_independent"], judged["valid"]) == (
        "uncertain",
        False,
        False,
    )
    assert judged["escalated"] is True


def test_a_fenced_answer_is_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(
        monkeypatch, tmp_path, capsys, models=MODELS, reply=f"```json\n{json.dumps(HOSTILE)}\n```"
    )

    assert classify(capsys, GROUP)["verdict"] == "hostile"


def test_with_no_model_authorized_the_workflow_fails_rather_than_guess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(monkeypatch, tmp_path, capsys, models=())

    status = main_status(GROUP)
    capsys.readouterr()

    assert status != 0


def main_status(
    group: dict[str, Any],
) -> int:
    """Run classify-comment, returning its exit status."""

    return main(
        (
            "workflow",
            "run",
            CLASSIFY_COMMENT_ID,
            "--input",
            json.dumps({"item": group, "index": 0}),
        )
    )


# From lookup, through a model, into memory


def test_a_judged_group_is_never_asked_about_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    state: sqlite3.Connection,
) -> None:
    install(monkeypatch, tmp_path, capsys, models=MODELS, reply=HOSTILE)
    (group,) = lookup(
        comment("c1", "you will never be a woman"),
        comment("c2", "Y0U W1LL NEVER BE A W0MAN", author="t2"),
    )["needs_inference"]

    judged = classify(capsys, group)
    run_tool(
        "record_verdicts", {"judged": [judged], "settings": {"policy": TRANSPHOBIC_HARASSMENT}}
    )

    again = lookup(comment("c9", "you will never be a woman!!!", author="t9"))

    assert again["needs_inference"] == []
    assert decided(again) == {"c9": ("hostile", "content")}
