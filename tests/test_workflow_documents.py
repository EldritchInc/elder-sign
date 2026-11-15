"""Tests every shipped workflow must pass, and the shape of resolve-comments."""

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from azathoth.tools import decode_tool_document
from azathoth.workflows import (
    ToolStepSpecification,
    WorkflowConstantValue,
    WorkflowSpecification,
    decode_workflow_document,
    encode_workflow_document,
)

from elder_sign.build import DOCUMENTS

WORKFLOWS = DOCUMENTS.parent / "workflows"

SHIPPED = sorted(WORKFLOWS.glob("*.json"))

RESOLVE_COMMENTS = WORKFLOWS / "resolve-comments.json"

RESOLVE_COMMENTS_ID = UUID("e1de7516-0000-4000-8000-000000000100")


def workflow(
    path: Path,
) -> WorkflowSpecification:
    """Return one shipped workflow."""

    return decode_workflow_document(path.read_text(encoding="utf-8"))


def shipped_tools() -> dict[str, tuple[str, str]]:
    """Return every shipped tool's version and runtime, by name."""

    tools = {}

    for path in sorted(DOCUMENTS.glob("*.json")):
        document = decode_tool_document(path.read_text(encoding="utf-8"))
        (implementation,) = document.implementations
        tools[document.definition.name] = (document.definition.version, implementation.runtime)

    return tools


def tool_steps(
    specification: WorkflowSpecification,
) -> list[ToolStepSpecification]:
    """Return the tool specifications of a workflow's steps, in order."""

    steps = []

    for step in specification.steps:
        assert isinstance(step.specification, ToolStepSpecification)
        steps.append(step.specification)

    return steps


def constant(
    specification: WorkflowSpecification,
    tool: str,
    name: str,
) -> Any:
    """Return the constant a workflow binds to one input of one tool's step."""

    for step in specification.steps:
        assert isinstance(step.specification, ToolStepSpecification)

        if step.specification.requirement.name == tool:
            (binding,) = (binding for binding in step.inputs if binding.name == name)
            assert isinstance(binding.source, WorkflowConstantValue)

            return binding.source.value

    raise AssertionError(f"no {tool} step")


# Every shipped workflow


def test_workflows_are_shipped() -> None:
    assert RESOLVE_COMMENTS in SHIPPED


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.name)
def test_the_workflow_is_canonical(
    path: Path,
) -> None:
    encoded = path.read_text(encoding="utf-8")

    assert encode_workflow_document(decode_workflow_document(encoded)) + "\n" == encoded


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.name)
def test_every_tool_step_requires_a_shipped_tool_exactly(
    path: Path,
) -> None:
    tools = shipped_tools()

    for step in tool_steps(workflow(path)):
        requirement = step.requirement

        assert requirement.name in tools, requirement.name
        assert (requirement.version, requirement.runtime) == tools[requirement.name]


def test_no_two_workflows_share_an_id() -> None:
    ids = [workflow(path).metadata.id for path in SHIPPED]

    assert len(set(ids)) == len(ids)


# resolve-comments


def test_resolve_comments_keys_the_scan_and_then_looks_it_up() -> None:
    specification = workflow(RESOLVE_COMMENTS)
    normalize, lookup = specification.steps

    assert specification.metadata.id == RESOLVE_COMMENTS_ID
    assert [step.requirement.name for step in tool_steps(specification)] == [
        "normalize_comments",
        "lookup_verdicts",
    ]
    assert tuple(lookup.depends_on) == (normalize.id,)
    assert specification.result is not None
    assert specification.result.model_dump(mode="json") == {"name": "resolution"}


def test_resolve_comments_judges_under_the_transphobia_policy() -> None:
    assert constant(workflow(RESOLVE_COMMENTS), "lookup_verdicts", "settings") == {
        "policy": "transphobia"
    }


def test_resolve_comments_asks_no_model() -> None:
    assert len(tool_steps(workflow(RESOLVE_COMMENTS))) == len(workflow(RESOLVE_COMMENTS).steps)
