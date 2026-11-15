"""Tests every shipped tool document must pass: canonical, distinct, and true to its test cases."""

import asyncio
from pathlib import Path

import pytest
from azathoth.tools import ProcessToolExecutor, decode_tool_document, encode_tool_document

from elder_sign.build import DOCUMENTS
from elder_sign.tools import TOOL_MODULES

SHIPPED = sorted(DOCUMENTS.glob("*.json"))


def test_every_tool_module_ships_a_document() -> None:
    assert len(SHIPPED) == len(TOOL_MODULES)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.name)
def test_the_document_is_canonical(
    path: Path,
) -> None:
    encoded = path.read_text(encoding="utf-8")

    assert encode_tool_document(decode_tool_document(encoded)) + "\n" == encoded


def test_no_two_documents_share_an_id_or_a_name() -> None:
    documents = [decode_tool_document(path.read_text(encoding="utf-8")) for path in SHIPPED]
    ids = [document.definition.id for document in documents] + [
        implementation.id for document in documents for implementation in document.implementations
    ]
    names = [document.definition.name for document in documents]

    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.name)
def test_the_document_passes_its_own_test_cases(
    path: Path,
) -> None:
    document = decode_tool_document(path.read_text(encoding="utf-8"))
    (implementation,) = document.implementations

    for case in document.test_cases:
        output = asyncio.run(ProcessToolExecutor().execute(implementation, dict(case.inputs)))

        assert output == case.expected_output, case.name
