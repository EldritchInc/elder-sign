"""Tests every shipped workflow must pass, and the shape of resolve-comments."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import azathoth.standard
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
from elder_sign.policy import TRANSPHOBIC_HARASSMENT

WORKFLOWS = DOCUMENTS.parent / "workflows"

STANDARD_TOOLS = Path(azathoth.standard.__file__).parent / "tools"
"""The standard tools Azathoth ships, which `azathoth init` installs."""

SHIPPED = sorted(WORKFLOWS.glob("*.json"))

RESOLVE_COMMENTS = WORKFLOWS / "resolve-comments.json"

RESOLVE_COMMENTS_ID = UUID("e1de7516-0000-4000-8000-000000000100")

SET_FIELDS = (
    "required_capabilities",
    "required_input_modalities",
    "required_output_modalities",
)
"""Fields Azathoth holds as sets, which it writes in whatever order the process hashes them."""


def sorted_sets(
    value: Any,
) -> Any:
    """Return a JSON value with every set field's members sorted, as the shipped files write them.

    Azathoth 1.4 holds a step's required modalities and capabilities as
    frozensets, and dumps them in the order Python hashes their members, which
    `PYTHONHASHSEED` changes from process to process. The shipped files keep
    them sorted, and are compared with what Azathoth writes after sorting it
    too.
    """

    if isinstance(value, dict):
        return {
            key: sorted(member) if key in SET_FIELDS else sorted_sets(member)
            for key, member in value.items()
        }

    if isinstance(value, list):
        return [sorted_sets(member) for member in value]

    return value


def workflow(
    path: Path,
) -> WorkflowSpecification:
    """Return one shipped workflow."""

    return decode_workflow_document(path.read_text(encoding="utf-8"))


def shipped_tools() -> dict[str, tuple[str, str]]:
    """Return the version and runtime of every tool a workflow may require, by name.

    Those are Elder Sign's own, and the standard tools `azathoth init` installs.
    """

    tools = {}

    for path in (*sorted(STANDARD_TOOLS.glob("*.json")), *sorted(DOCUMENTS.glob("*.json"))):
        document = decode_tool_document(path.read_text(encoding="utf-8"))
        (implementation,) = document.implementations
        tools[document.definition.name] = (document.definition.version, implementation.runtime)

    return tools


def tool_steps(
    specification: WorkflowSpecification,
) -> list[ToolStepSpecification]:
    """Return the tool specifications of a workflow's steps, in order."""

    return [
        step.specification
        for step in specification.steps
        if isinstance(step.specification, ToolStepSpecification)
    ]


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
    written = json.loads(encode_workflow_document(decode_workflow_document(encoded)))

    assert json.dumps(sorted_sets(written), ensure_ascii=False, indent=2) + "\n" == encoded


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


def test_every_workflow_judges_under_the_default_policy() -> None:
    for path in SHIPPED:
        for step in workflow(path).steps:
            for binding in step.inputs:
                if binding.name == "settings" and isinstance(binding.source, WorkflowConstantValue):
                    settings = binding.source.value

                    assert isinstance(settings, dict)

                    if "policy" in settings:
                        assert settings["policy"] == TRANSPHOBIC_HARASSMENT, path.name


def test_resolve_comments_asks_no_model() -> None:
    assert len(tool_steps(workflow(RESOLVE_COMMENTS))) == len(workflow(RESOLVE_COMMENTS).steps)
